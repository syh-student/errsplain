"""clip / watch 命令：让 IDE 里的报错也能被翻译。

- clip  ：即时翻译当前剪贴板里的报错，结果写回剪贴板（适合绑快捷键）。
- watch ：常驻后台监听剪贴板，一复制到「像报错」的文本就弹通知 + 写回结果。
"""

from __future__ import annotations

import argparse
import hashlib
import math
import sys
import time

from . import clipboard


def _handle_clipboard_error(exc: Exception) -> int:
    print(f"剪贴板错误：{exc}", file=sys.stderr)
    return 2


def _diagnose_text(text: str) -> str:
    """跑现场诊断，返回渲染文本；失败返回空串。"""
    try:
        from .diagnose import diagnose, render

        return render(diagnose(text))
    except Exception:
        return ""


def _translate_clipboard(
    *, write_back: bool = True, quiet: bool = False, with_diagnose: bool = False
) -> int:
    """读剪贴板 → 判断是否报错 → 翻译 → 可选写回。返回退出码。"""
    try:
        text = clipboard.get_text()
    except clipboard.ClipboardError as exc:
        return _handle_clipboard_error(exc)

    if not text.strip():
        if not quiet:
            print("剪贴板是空的。")
        return 1
    if not clipboard.looks_like_error(text):
        if not quiet:
            print("剪贴板内容看起来不像报错，未翻译。")
        return 1

    result = clipboard.translate(text)
    diag = _diagnose_text(text) if with_diagnose else ""
    if not result and not diag:
        if not quiet:
            print("这段报错暂时没有匹配的词条。")
        return 1

    combined = result or ""
    if diag:
        combined = f"{combined}\n{diag}" if combined else diag

    if write_back:
        try:
            clipboard.set_text(combined)
        except clipboard.ClipboardError as exc:
            return _handle_clipboard_error(exc)

    if not quiet:
        print(combined)
        if write_back:
            print("\n（结果已写回剪贴板，直接 Ctrl+V 即可粘贴）")
    return 0


def handle_clip_command(args: list[str]) -> int:
    """`errsplain clip`：即时翻译剪贴板。"""
    parser = argparse.ArgumentParser(
        prog="errsplain clip",
        description="翻译剪贴板里的报错，并把结果写回剪贴板（在任意 IDE 复制报错后用）。",
    )
    parser.add_argument(
        "--no-write", action="store_true", help="只打印翻译，不写回剪贴板"
    )
    parser.add_argument(
        "--force", action="store_true", help="即使看起来不像报错也强制翻译"
    )
    parser.add_argument(
        "--diagnose", action="store_true", help="同时做现场诊断（检查模块/端口/路径真实状态）"
    )
    opts = parser.parse_args(args)

    if opts.force:
        # 强制模式跳过「像不像报错」的判断
        try:
            text = clipboard.get_text()
        except clipboard.ClipboardError as exc:
            return _handle_clipboard_error(exc)
        result = clipboard.translate(text)
        diag = _diagnose_text(text) if opts.diagnose else ""
        combined = result or ""
        if diag:
            combined = f"{combined}\n{diag}" if combined else diag
        if not combined:
            print("没有匹配的词条，也没有可检查的对象。")
            return 1
        if not opts.no_write:
            try:
                clipboard.set_text(combined)
            except clipboard.ClipboardError as exc:
                return _handle_clipboard_error(exc)
        print(combined)
        if not opts.no_write:
            print("\n（结果已写回剪贴板）")
        return 0

    return _translate_clipboard(write_back=not opts.no_write, with_diagnose=opts.diagnose)


def handle_watch_command(args: list[str]) -> int:
    """`errsplain watch`：常驻监听剪贴板，报错自动翻译。"""
    parser = argparse.ArgumentParser(
        prog="errsplain watch",
        description="常驻后台监听剪贴板：一复制到报错就弹翻译并写回结果。Ctrl+C 退出。",
    )
    parser.add_argument(
        "--interval", type=float, default=0.8, help="轮询间隔秒数 (默认 0.8)"
    )
    parser.add_argument(
        "--no-notify", action="store_true", help="不弹通知窗口，只在终端打印"
    )
    parser.add_argument(
        "--no-write", action="store_true", help="不写回剪贴板（避免覆盖你复制的原文）"
    )
    parser.add_argument(
        "--diagnose", action="store_true", help="同时做现场诊断（检查模块/端口/路径真实状态）"
    )
    opts = parser.parse_args(args)

    if not math.isfinite(opts.interval) or opts.interval < 0.2:
        print("轮询间隔必须是不少于 0.2 秒的有限数字。", file=sys.stderr)
        return 2

    if not clipboard.IS_WINDOWS:
        print("watch 目前仅支持 Windows。", file=sys.stderr)
        return 2

    print("errsplain watch 已启动：复制报错即自动翻译。Ctrl+C 退出。", flush=True)
    write_state = "否" if opts.no_write else "是"
    print(f"  轮询间隔 {opts.interval}s；写回剪贴板：{write_state}", flush=True)

    last_hash: str | None = None
    try:
        while True:
            try:
                text = clipboard.get_text()
            except clipboard.ClipboardError:
                time.sleep(opts.interval)
                continue

            current = hashlib.md5(text.encode("utf-8", "replace")).hexdigest()
            if not text.strip() or current == last_hash:
                time.sleep(opts.interval)
                continue
            last_hash = current

            if not clipboard.looks_like_error(text):
                time.sleep(opts.interval)
                continue
            result = clipboard.translate(text)
            diag = _diagnose_text(text) if opts.diagnose else ""
            combined = result or ""
            if diag:
                combined = f"{combined}\n{diag}" if combined else diag
            if not combined:
                time.sleep(opts.interval)
                continue

            print(f"\n{combined}\n", flush=True)
            if not opts.no_notify:
                clipboard.notify("报错翻译", combined)
            if not opts.no_write:
                try:
                    clipboard.set_text(combined)
                    # 写回后更新 hash，避免把自己写回的结果再翻译一遍
                    last_hash = hashlib.md5(
                        combined.encode("utf-8", "replace")
                    ).hexdigest()
                except clipboard.ClipboardError:
                    pass
            time.sleep(opts.interval)
    except KeyboardInterrupt:
        print("\nwatch 已停止。")
        return 0
