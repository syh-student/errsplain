"""剪贴板与通知：让 IDE 里的报错也能被翻译。

IDE（VS Code / PyCharm / IDEA / Rider …）点「运行」后的报错走的是 IDE 自己的
控制台，不经过 shell，hook 覆盖不到。但所有 IDE 的报错都能选中复制——本模块
抓住「剪贴板」这个共同出口，用 ctypes 直接调 Windows API，零第三方依赖。

提供：
- get_text / set_text：读写系统剪贴板（当前仅 Windows）。
- looks_like_error：判断剪贴板内容是否像一条报错（避免复制普通文本也弹窗）。
- translate：把报错翻译成人话，返回精简文本。
- notify：弹一个几秒后自动消失的置顶通知窗口。
"""

from __future__ import annotations

import re
import sys
import threading
import time
from pathlib import Path

from .database import DatasetError, load_entries
from .matcher import match

IS_WINDOWS = sys.platform == "win32"

# 一段文本要被当成「报错」才翻译。这些标志出现才值得打扰用户。
_ERROR_MARKERS = re.compile(
    r"(?i)("
    r"\b(error|exception|failed|failure|fatal|panic|traceback|cannot|could not|"
    r"denied|not found|undefined|unresolved|invalid|timeout|refused|"
    r"warning|npm err|module not found|segmentation|core dumped|"
    r"assignable|mismatch|no such|out of range|index out of)\b"
    r"|报错|错误|失败|异常|找不到|拒绝|不存在"  # 中文无单词边界，单独匹配
    r"|"
    r"\b[a-z]{2,8}\d{2,6}\b"        # 错误码：TS2322 / CS1061 / E2025 / PG...
    r"|\b\w*(error|exception)\b"    # ModuleNotFoundError / NullPointerException
    r")"
)

# 代码/文本噪声：一整段源码不是报错，不该翻译。
_CODE_HINT = re.compile(r"(?m)^\s*(def |class |function |import |#include |public class )")


class ClipboardError(Exception):
    """剪贴板不可用。"""


# ------------------------------------------------------------------ 剪贴板

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _CF_UNICODETEXT = 13
    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _u32.OpenClipboard.argtypes = [wintypes.HWND]
    _u32.OpenClipboard.restype = wintypes.BOOL
    _u32.GetClipboardData.argtypes = [wintypes.UINT]
    _u32.GetClipboardData.restype = wintypes.HANDLE
    _u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    _u32.SetClipboardData.restype = wintypes.HANDLE
    _u32.EmptyClipboard.argtypes = []
    _u32.EmptyClipboard.restype = wintypes.BOOL
    _u32.CloseClipboard.argtypes = []
    _u32.CloseClipboard.restype = wintypes.BOOL
    _k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    _k32.GlobalAlloc.restype = wintypes.HGLOBAL
    _k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    _k32.GlobalLock.restype = wintypes.LPVOID
    _k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    _k32.GlobalUnlock.restype = wintypes.BOOL
    _k32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    _k32.GlobalFree.restype = wintypes.HGLOBAL
    _GMEM_MOVEABLE = 0x0002

    def get_text() -> str:
        """读取剪贴板文本；失败抛 ClipboardError。"""
        if not _u32.OpenClipboard(None):
            raise ClipboardError("无法打开剪贴板（可能被其他程序占用）")
        try:
            handle = _u32.GetClipboardData(_CF_UNICODETEXT)
            if not handle:
                raise ClipboardError("剪贴板里没有文本内容")
            ptr = _k32.GlobalLock(handle)
            if not ptr:
                raise ClipboardError("无法锁定剪贴板内存")
            try:
                return ctypes.cast(ptr, ctypes.c_wchar_p).value or ""
            finally:
                _k32.GlobalUnlock(handle)
        finally:
            _u32.CloseClipboard()

    def set_text(text: str) -> None:
        """把文本写入剪贴板；失败路径也释放已分配的全局内存。"""
        if not _u32.OpenClipboard(None):
            raise ClipboardError("无法打开剪贴板（可能被其他程序占用）")
        handle = None
        try:
            _u32.EmptyClipboard()
            data = text.encode("utf-16-le") + b"\x00\x00"
            handle = _k32.GlobalAlloc(_GMEM_MOVEABLE, len(data))
            if not handle:
                raise ClipboardError("分配剪贴板内存失败")
            ptr = _k32.GlobalLock(handle)
            if not ptr:
                raise ClipboardError("无法锁定剪贴板内存")
            try:
                ctypes.memmove(ptr, data, len(data))
            finally:
                _k32.GlobalUnlock(handle)
            if not _u32.SetClipboardData(_CF_UNICODETEXT, handle):
                raise ClipboardError("写入剪贴板失败")
            # 成功后内存所有权交给系统，不能再 GlobalFree。
            handle = None
        finally:
            if handle:
                _k32.GlobalFree(handle)
            _u32.CloseClipboard()

else:  # 非 Windows：给出清晰的降级，不假装能用
    def get_text() -> str:
        raise ClipboardError(
            "剪贴板功能目前仅支持 Windows；其他平台请用管道：命令 2>&1 | errsplain"
        )

    def set_text(text: str) -> None:
        raise ClipboardError("剪贴板写入目前仅支持 Windows")


# ------------------------------------------------------------------ 判断与翻译

def looks_like_error(text: str) -> bool:
    """这段文本值不值得翻译？避免复制普通代码/文字也弹窗打扰。"""
    s = text.strip()
    if not s or len(s) > 4000:
        return False
    if not _ERROR_MARKERS.search(s):
        return False
    if _CODE_HINT.search(s) and len(s.splitlines()) > 3:
        return False  # 更像一整段源码，而非报错
    return True


def translate(text: str) -> str | None:
    """把报错文本翻译成精简人话；没匹配到返回 None。"""
    try:
        entries = load_entries()
    except DatasetError:
        return None
    results = match(text, entries, top_k=1, threshold=0.45)
    if not results:
        return None
    m = results[0]
    e = m.entry
    lines = [f"[{e.category}] {e.title}", f"  {e.meaning}"]
    if e.fixes:
        lines.append(f"  试试：{e.fixes[0]}")
    return "\n".join(lines)


# ------------------------------------------------------------------ 通知弹窗

if IS_WINDOWS:
    _u32.MessageBoxW.argtypes = [
        wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT
    ]
    _u32.MessageBoxW.restype = wintypes.INT
    _u32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    _u32.FindWindowW.restype = wintypes.HWND
    _u32.PostMessageW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    ]
    _MB_ICONINFORMATION = 0x40
    _MB_TOPMOST = 0x00040000
    _WM_CLOSE = 0x0010

    def notify(title: str, body: str, seconds: float = 6.0) -> None:
        """弹一个置顶通知，seconds 秒后自动关闭，不阻塞主线程。"""
        window_title = "errsplain · 报错翻译"
        shown = "errsplain · " + title

        def _popup() -> None:
            _u32.MessageBoxW(None, body, shown, _MB_ICONINFORMATION | _MB_TOPMOST)

        def _auto_close() -> None:
            time.sleep(seconds)
            hwnd = _u32.FindWindowW(None, shown)
            if hwnd:
                _u32.PostMessageW(hwnd, _WM_CLOSE, 0, 0)

        threading.Thread(target=_popup, daemon=True).start()
        threading.Thread(target=_auto_close, daemon=True).start()

else:
    def notify(title: str, body: str, seconds: float = 6.0) -> None:
        print(f"[{title}] {body}")
