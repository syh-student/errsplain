"""命令行界面。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from . import __version__
from .database import DatasetError, categories, load_entries
from .layout import disp_width, truncate, wrap
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
            self.magenta = "\033[35m"
            self.cyan = "\033[36m"
        else:
            self.reset = self.bold = self.dim = ""
            self.red = self.green = self.yellow = self.blue = self.cyan = ""
            self.magenta = ""

    def paint(self, text: str, color: str) -> str:
        return f"{color}{text}{self.reset}" if color else text


def _term_width() -> int:
    """终端宽度；过窄时保底，避免排版炸裂。"""
    cols = shutil.get_terminal_size(fallback=(100, 24)).columns
    return max(cols, 46)


def _label_block(label: str, text: str, p: Palette, color: str) -> list[str]:
    """「标签  正文」块：续行与正文对齐（悬挂缩进），按终端宽度折行。"""
    lead = 2 + disp_width(label) + 2  # "  标签  " 的显示宽度
    wrapped = wrap(text, _term_width() - lead)
    lines = ["  " + p.paint(label, color) + "  " + wrapped[0]]
    lines += [" " * lead + ln for ln in wrapped[1:]]
    return lines


def _render_match(
    m: Match, p: Palette, *, index: int, full: bool = False, show_meta: bool = True
) -> str:
    """渲染一条解释。默认精简（意思 + 前 3 条修法 + 注意）；full 显示全部。"""
    e = m.entry
    W = _term_width()
    lines: list[str] = []
    lines.append("  " + p.paint(f"[{index}] {e.title}", p.bold + p.red))
    if show_meta:
        lines.append(
            "      "
            + p.paint(f"分类 {e.category} · 相关度 {m.score:.0%}", p.dim)
        )
    lines.append("")

    lines += _label_block("这是什么意思", e.meaning, p, p.bold + p.blue)
    if full and e.causes:
        lines.append("")
        lines.append("  " + p.paint("最常见的原因", p.bold + p.magenta))
        lines += _numbered(e.causes, W)
    if e.fixes:
        shown = e.fixes if full else e.fixes[:3]
        lines.append("")
        lines.append("  " + p.paint("怎么修", p.bold + p.green))
        lines += _numbered(shown, W)
        if not full and len(e.fixes) > 3:
            lines.append(
                p.paint(f"    … 另有 {len(e.fixes) - 3} 条（--full）", p.dim)
            )
    if e.note:
        lines.append("")
        lines += _label_block("注意", e.note, p, p.bold + p.yellow)
    return "\n".join(lines)


def _numbered(items: list[str], width: int) -> list[str]:
    """编号列表：每条按显示宽度折行，续行与正文对齐。"""
    out: list[str] = []
    for i, item in enumerate(items, 1):
        prefix = f"    {i}. "
        indent = " " * disp_width(prefix)
        wrapped = wrap(item, width - disp_width(prefix))
        out.append(prefix + wrapped[0])
        out += [indent + ln for ln in wrapped[1:]]
    return out


def _render_no_match(p: Palette) -> str:
    return "\n".join(
        [
            p.paint("  没找到匹配的词条。", p.bold + p.red),
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
        parts.append(Path(args.file).read_text(encoding="utf-8-sig", errors="replace"))
    if args.stdin:
        parts.append(sys.stdin.read())
    return "\n".join(parts).strip()


def _positive_int(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("必须是正整数")
    return n


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
    parser.add_argument("--top", "-n", type=_positive_int, default=3, help="最多显示几条结果 (默认 3)")
    parser.add_argument("--threshold", type=float, default=0.22, help="相关度阈值 (默认 0.22)")
    parser.add_argument("--no-color", action="store_true", help="禁用颜色输出")
    parser.add_argument(
        "--full",
        action="store_true",
        help="显示完整内容（含全部原因与全部修法），默认精简",
    )
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


def _render_hook(m: Match, p: Palette) -> str:
    """自动模式的精简卡片：一条、几行、能照着做。"""
    e = m.entry
    W = _term_width()
    head = truncate(f"errsplain → [{e.category}] {e.title}  ({m.confidence})", W - 2)
    lines = ["  " + p.paint(head, p.bold + p.red)]
    for ln in wrap(e.meaning, W - 4):
        lines.append("    " + ln)
    if e.fixes:
        for i, ln in enumerate(wrap(e.fixes[0], W - 10)):
            lines.append(
                (p.paint("    试试：", p.bold + p.green) if i == 0 else "          ")
                + ln
            )
    return "\n".join(lines)


def _print_diagnosis(query: str, p: Palette) -> None:
    """跑现场诊断并打印。诊断失败绝不能影响主流程。"""
    try:
        from .diagnose import diagnose, render

        findings = diagnose(query)
        if findings:
            print()
            print(render(findings, palette=p))
    except Exception:  # 诊断是增强功能，任何异常都静默跳过
        pass


def _safe_output() -> None:
    """输出被重定向到 cp1252 之类的编码时，打印中文不能直接崩溃。

    保留控制台原有编码，只把编不出来的字符替换成 ?；CI 里再用
    PYTHONIOENCODING=utf-8 拿到正常中文。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    _safe_output()
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
    # 分类名统一小写，用户写 -c Git / -c " git " 也能命中
    category = (args.category or "").strip().lower() or None

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
            # 先按显示宽度补空格再上色：直接 format 会把颜色转义码也算进宽度
            id_pad = " " * max(1, 48 - disp_width(e.id))
            cat_pad = " " * max(1, 10 - disp_width(e.category))
            print("  " + p.paint(e.id, p.cyan) + id_pad + p.paint(e.category, p.dim) + cat_pad + e.title)
        return 0

    try:
        query = _read_query(args)
    except OSError as exc:
        print(p.paint(f"读取报错文件失败：{exc}", p.red), file=sys.stderr)
        return 2
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
        auto = match(query, entries, category=category, top_k=1, threshold=0.45)
        if auto:
            print(_render_hook(auto[0], p))
            return 0
        return 0  # 静默成功，不打扰

    results = match(
        query,
        entries,
        category=category,
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
            _print_diagnosis(query, p)
        return 1

    if len(results) == 1:
        # 单条结果不需要「找到 1 条」这种废话，直接上正文
        print()
        print(_render_match(results[0], p, index=1, full=args.full, show_meta=False))
    else:
        print(p.paint(f"\n  找到 {len(results)} 条相关解释：\n", p.bold))
        for i, m in enumerate(results, 1):
            print(_render_match(m, p, index=i, full=args.full))
            if i != len(results):
                print()
    if args.diagnose:
        _print_diagnosis(query, p)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
