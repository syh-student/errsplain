"""Windows 控制台画面捕获（零依赖）。

PowerShell 5.1 不会把原生命令（python、git、npm 等）的 stderr 存进
``$Error``，但报错仍留在终端画面上。``errsplain cuowu`` 在用户敲下
``cuowu`` 的那一刻读取当前可见画面，交给交互导航。非 Windows 或没有
真实控制台时返回空文本。
"""

from __future__ import annotations

import ctypes
import os

from .layout import strip_ansi

_MAX_LINES = 60
_MAX_CHARS = 8000
_INVALID_HANDLE = ctypes.c_void_p(-1).value


def _read_console_lines_windows() -> list[str]:
    from ctypes import wintypes

    class COORD(ctypes.Structure):
        _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]

    class SMALL_RECT(ctypes.Structure):
        _fields_ = [
            ("Left", wintypes.SHORT),
            ("Top", wintypes.SHORT),
            ("Right", wintypes.SHORT),
            ("Bottom", wintypes.SHORT),
        ]

    class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
        _fields_ = [
            ("dwSize", COORD),
            ("dwCursorPosition", COORD),
            ("wAttributes", wintypes.WORD),
            ("srWindow", SMALL_RECT),
            ("dwMaximumWindowSize", COORD),
        ]

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE

    info = CONSOLE_SCREEN_BUFFER_INFO()

    def console_info(handle) -> bool:
        return bool(kernel32.GetConsoleScreenBufferInfo(handle, ctypes.byref(info)))

    # 直接打开 CONOUT$：即使 stdout 被接到管道，也能读到真实控制台画面
    handle = kernel32.CreateFileW("CONOUT$", 0xC0000000, 3, None, 3, 0, None)
    if handle in (None, _INVALID_HANDLE) or not console_info(handle):
        return []

    left = info.srWindow.Left
    top = info.srWindow.Top
    bottom = info.srWindow.Bottom
    width = max(1, info.srWindow.Right - left + 1)
    lines: list[str] = []
    for y in range(top, bottom + 1):
        buffer = ctypes.create_unicode_buffer(width + 1)
        read = wintypes.DWORD(0)
        coord = COORD(left, y)
        ok = kernel32.ReadConsoleOutputCharacterW(
            handle, buffer, width, coord, ctypes.byref(read)
        )
        if not ok:
            continue
        lines.append(buffer[: read.value].rstrip())
    return lines


def extract_recent_text(
    lines: list[str], *, max_lines: int = _MAX_LINES, max_chars: int = _MAX_CHARS
) -> str:
    """把屏幕行裁成最近一段文本，去掉 ANSI、尾部空行和超长部分。"""
    cleaned = [strip_ansi(line).rstrip() for line in lines]
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()
    text = "\n".join(cleaned[-max_lines:]).strip()
    if len(text) > max_chars:
        text = text[-max_chars:]
    return text


def capture_screen_text() -> str:
    if os.name != "nt":
        return ""
    try:
        return extract_recent_text(_read_console_lines_windows())
    except Exception:
        return ""
