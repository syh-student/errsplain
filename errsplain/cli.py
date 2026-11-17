"""命令行界面。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .database import DatasetError, categories, load_entries
from .matcher import Match, match
from .models import Entry


class Palette:
    """ANSI 颜色；在无颜色环境下降级为空串。"""

    def __init__(self, enabled: bool) -> None:
        if enabled:
            self.reset = "\033[0m"
            self.bold = "\033[1m"
            self.dim = "\033[2m"
            self.red = "\033[31m"
            self.green = "\033[32m"
            self.yellow = "\033[33m"
            self.blue = "\033[34m"
            self.cyan = "\033[36m"
        else:
            self.reset = self.bold = self.dim = ""
            self.red = self.green = self.yellow = self.blue = self.cyan = ""

    def paint(self, text: str, color: str) -> str:
        return f"{color}{text}{self.reset}" if color else text


def _render_match(m: Match, p: Palette, *, index: int) -> str:
    e = m.entry
    lines: list[str] = []
    head = f"  [{index}] {e.title}"
    lines.append(p.paint(head, p.bold + p.cyan))
    lines.append(
        "      "
        + p.paint(f"分类 {e.category}", p.dim)
        + "  "
        + p.paint(f"相关度 {m.confidence} ({m.score:.0%})", p.dim)
        + "  "
        + p.paint(" · ".join(m.reasons), p.dim)
    )
    lines.append("")
    lines.append("  " + p.paint("这是什么意思", p.bold) + "  " + e.meaning)
    if e.causes:
        lines.append("")
        lines.append("  " + p.paint("最常见的原因", p.bold))
        lines += [f"    {i}. {c}" for i, c in enumerate(e.causes, 1)]
    if e.fixes:
        lines.append("")
        lines.append("  " + p.paint("怎么修", p.bold + p.green))
        lines += [f"    {i}. {f}" for i, f in enumerate(e.fixes, 1)]
    if e.note:
        lines.append("")
        lines.append("  " + p.paint("注意", p.bold + p.yellow) + "  " + e.note)
    return "\n".join(lines)


def _render_no_match(p: Palette) -> str:
    return "\n".join(
        [
            p.paint("  没找到匹配的词条。", p.yellow),
            "",
            "  可以试试：",
            "    · 只贴报错的关键那一行（去掉路径、时间戳等噪声）",
            "    · 用 --list 看看目前收录了哪些报错",
            "    · 这个报错值得收录？欢迎按 CONTRIBUTING.md 提一个词条 PR",
            "      https://github.com/syh-student/errsplain",
        ]
    )


def _to_dict(m: Match) -> dict:
    e = m.entry
    return {
        "id": e.id,
        "category": e.category,
        "title": e.title,
        "confidence": m.confidence,
        "score": round(m.score, 3),
        "reasons": list(m.reasons),
        "meaning": e.meaning,
        "causes": list(e.causes),
        "fixes": list(e.fixes),
        "tags": list(e.tags),
        "note": e.note,
    }


def _read_query(args: argparse.Namespace) -> str:
    parts: list[str] = []
    if args.error:
        parts.append(" ".join(args.error))
    if args.file:
        parts.append(Path(args.file).read_text(encoding="utf-8", errors="replace"))
    if args.stdin:
        parts.append(sys.stdin.read())
    return "\n".join(parts).strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="errsplain",
        description="把天书般的报错翻译成人话。纯离线，零依赖。",
        epilog="示例:\n  errsplain 'fatal: refusing to merge unrelated histories'\n"
        "  git push 2>&1 | errsplain\n"
        "  errsplain --file build.log --category npm",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("error", nargs="*", help="要翻译的报错文本")
    parser.add_argument("--file", "-f", help="从文件读取报错")
    parser.add_argument("--stdin", action="store_true", help="强制从标准输入读取")
    parser.add_argument("--category", "-c", help="只搜索某个分类，如 git / npm")
    parser.add_argument("--top", "-n", type=int, default=3, help="最多显示几条结果 (默认 3)")
    parser.add_argument("--threshold", type=float, default=0.22, help="相关度阈值 (默认 0.22)")
    parser.add_argument("--no-color", action="store_true", help="禁用颜色输出")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出，方便脚本处理")
    parser.add_argument("--list", action="store_true", help="列出所有词条")
    parser.add_argument("--stats", action="store_true", help="显示词库统计")
    parser.add_argument("--validate", action="store_true", help="校验词库并返回状态码")
    parser.add_argument(
        "--hook",
        action="store_true",
        help="自动模式：只显示最相关的一条精简解释；没匹配到则完全静默（供 shell hook 调用）",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="额外做现场诊断：去你机器上实际检查模块/端口/路径/命令的真实状态",
    )
    parser.add_argument("--version", "-V", action="version", version=f"errsplain {__version__}")
    return parser


def _render_hook(m: Match) -> str:
    """自动模式的精简卡片：一条、几行、能照着做。"""
    e = m.entry
    lines = [f"  errsplain → [{e.category}] {e.title}  ({m.confidence})"]
    lines.append(f"    {e.meaning}")
    if e.fixes:
        lines.append(f"    试试：{e.fixes[0]}")
    return "\n".join(lines)


def _print_diagnosis(query: str) -> None:
    """跑现场诊断并打印。诊断失败绝不能影响主流程。"""
    try:
        from .diagnose import diagnose, render

        findings = diagnose(query)
        if findings:
            print()
            print(render(findings))
    except Exception:  # 诊断是增强功能，任何异常都静默跳过
        pass


def main(argv: list[str] | None = None) -> int:
    # `errsplain hook ...` 子命令：安装/卸载/查看自动翻译 hook
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "hook":
        from .hooks import handle_hook_command

        return handle_hook_command(argv[1:])
    if argv and argv[0] == "clip":
        from .clipcmd import handle_clip_command

        return handle_clip_command(argv[1:])
    if argv and argv[0] == "watch":
        from .clipcmd import handle_watch_command

        return handle_watch_command(argv[1:])
    if argv and argv[0] == "diagnose":
        from .diagcmd import handle_diagnose_command

        return handle_diagnose_command(argv[1:])

    args = build_parser().parse_args(argv)

    use_color = (
        not args.no_color
        and not args.json
        and sys.stdout.isatty()
        and os.environ.get("NO_COLOR") is None
    )
    p = Palette(use_color)

    try:
        entries = load_entries()
    except DatasetError as exc:
        print(p.paint(f"词库错误: {exc}", p.red), file=sys.stderr)
        return 2

    if args.validate:
        print(f"OK: {len(entries)} 条词条校验通过")
        return 0

    if args.stats:
        counts = categories(entries)
        print(f"共 {len(entries)} 条词条，{len(counts)} 个分类：")
        for cat, n in counts.items():
            print(f"  {cat:<12} {n}")
        return 0

    if args.list:
        if args.json:
            print(json.dumps([_to_dict(Match(e, 1.0, ())) for e in entries], ensure_ascii=False, indent=2))
            return 0
        print(f"共 {len(entries)} 条词条。用 --category 过滤，用 --stats 看分类。\n")
        for e in entries:
            print(f"  {p.paint(e.id, p.cyan):<48} {p.paint(e.category, p.dim):<10} {e.title}")
        return 0

    query = _read_query(args)
    if not query:
        if sys.stdin.isatty():
            print("请提供报错文本。示例: errsplain 'Module not found'")
            print("或用 --help 查看全部用法。")
            return 1
        query = sys.stdin.read().strip()
        if not query:
            return 1

    # 自动模式：只用最相关的一条，静默处理未命中（命令失败时不想被"没找到"刷屏）
    if args.hook:
        auto = match(query, entries, category=args.category, top_k=1, threshold=0.45)
        if auto:
            print(_render_hook(auto[0]))
            return 0
        return 0  # 静默成功，不打扰

    results = match(
        query,
        entries,
        category=args.category,
        top_k=max(1, args.top),
        threshold=args.threshold,
    )

    # 若第一名明显领先（≥1.4 倍分差），后面那些只是同名错误的其它语言版本，
    # 全列出来反而干扰判断——只保留最相关的一条。
    if len(results) > 1 and results[0].score >= results[1].score * 1.4:
        results = results[:1]

    if args.json:
        print(json.dumps([_to_dict(m) for m in results], ensure_ascii=False, indent=2))
        return 0 if results else 1

    if not results:
        print(_render_no_match(p))
        if args.diagnose:
            _print_diagnosis(query)
        return 1

    print(p.paint(f"\n  找到 {len(results)} 条相关解释：\n", p.bold))
    for i, m in enumerate(results, 1):
        print(_render_match(m, p, index=i))
        if i != len(results):
            print()
    if args.diagnose:
        _print_diagnosis(query)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
