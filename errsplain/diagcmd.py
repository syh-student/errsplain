"""`errsplain diagnose`：只做现场诊断，不做词库翻译。"""

from __future__ import annotations

import argparse
import os
import sys

from .diagnose import diagnose, has_checkable, render


def handle_diagnose_command(args: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="errsplain diagnose",
        description="现场诊断：去你机器上实际检查模块/端口/路径/命令的真实状态。纯只读。",
        epilog="示例:\n"
        "  errsplain diagnose \"ModuleNotFoundError: No module named 'flask'\"\n"
        "  errsplain diagnose \"listen EADDRINUSE :::3000\"\n"
        "  cat build.log | errsplain diagnose",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("error", nargs="*", help="要诊断的报错文本")
    parser.add_argument("--file", "-f", help="从文件读取报错")
    parser.add_argument("--stdin", action="store_true", help="强制从标准输入读取")
    parser.add_argument("--no-color", action="store_true", help="禁用颜色输出")
    opts = parser.parse_args(args)
    use_color = (
        not getattr(opts, "no_color", False)
        and sys.stdout.isatty()
        and "NO_COLOR" not in os.environ
    )
    from .cli import Palette

    p = Palette(use_color)

    parts: list[str] = []
    if opts.error:
        parts.append(" ".join(opts.error))
    if opts.file:
        from pathlib import Path

        try:
            parts.append(Path(opts.file).read_text(encoding="utf-8-sig", errors="replace"))
        except OSError as exc:
            print(f"读取文件失败：{exc}", file=sys.stderr)
            return 2
    if opts.stdin:
        parts.append(sys.stdin.read())
    if not parts and not sys.stdin.isatty():
        parts.append(sys.stdin.read())

    text = "\n".join(parts).strip()
    if not text:
        print("请提供要诊断的报错文本。示例：errsplain diagnose \"No module named 'flask'\"",
              file=sys.stderr)
        return 1

    findings = diagnose(text)
    if not findings:
        print("这段报错里没有可实地检查的具体对象（模块名 / 端口 / 路径 / 命令 / 域名）。")
        if not has_checkable(text):
            print("可以试试直接翻译：errsplain \"你的报错\"")
        return 1

    print(render(findings, palette=p))
    print()
    return 0
