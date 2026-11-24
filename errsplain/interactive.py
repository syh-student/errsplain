"""零依赖的交互式终端结果浏览器。

只在用户显式指定 ``--interactive`` 且 stdin/stdout 都是真实终端时启动。
普通输出、管道与 JSON 输出不受影响。绘制与按键状态更新分离，便于在
没有真实 TTY 的 CI 中测试。
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from typing import Callable, Protocol, Sequence

from .diagnose import Finding
from .layout import disp_width, truncate, wrap
from .matcher import Match
from .models import Entry


class PaletteLike(Protocol):
    reset: str
    bold: str
    dim: str
    reverse: str
    red: str
    green: str
    yellow: str
    blue: str
    cyan: str
    magenta: str

    def paint(self, text: str, color: str) -> str: ...


@dataclass
class BrowserState:
    """交互界面的全部可变状态。"""

    results: Sequence[Match]
    selected: int = 0
    expanded: bool = False
    show_diagnosis: bool = False
    help_visible: bool = False
    scroll: int = 0
    findings: list[Finding] = field(default_factory=list)
    diagnosis_loaded: bool = False
    diagnosis_loading: bool = False

    @property
    def current(self) -> Match:
        if not self.results:
            raise ValueError("交互浏览器至少需要一条匹配结果")
        return self.results[self.selected]


def apply_key(state: BrowserState, key: str) -> str:
    """更新状态；返回 ``quit`` 或空字符串，不接触真实终端。"""
    if len(key) == 1:
        key = key.lower()
    if key in ("q", "esc"):
        return "quit"
    if state.help_visible:
        if key in ("?", "enter"):
            state.help_visible = False
            state.scroll = 0
        return ""
    if key == "?":
        state.help_visible = True
        state.scroll = 0
        return ""
    if key in ("enter", "f"):
        state.expanded = not state.expanded
        state.show_diagnosis = False
        state.scroll = 0
        return ""
    if key in ("d", "tab"):
        state.show_diagnosis = not state.show_diagnosis
        state.scroll = 0
        if state.show_diagnosis and not state.diagnosis_loaded:
            state.diagnosis_loading = True
        return ""

    count = len(state.results)
    if key in ("up", "left", "down", "right") and count > 1:
        step = -1 if key in ("up", "left") else 1
        state.selected = (state.selected + step) % count
        state.scroll = 0
        return ""
    if key == "up":
        state.scroll -= 1
    elif key == "down":
        state.scroll += 1
    elif key == "pgup":
        state.scroll -= 10
    elif key == "pgdn":
        state.scroll += 10
    elif key == "home":
        state.scroll = 0
    elif key == "end":
        state.scroll = 1_000_000
    state.scroll = max(0, state.scroll)
    return ""


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - disp_width(text))


def _columns(left: str, right: str, width: int) -> str:
    """左右对齐的一行；空间不足时优先保留右侧状态。"""
    if disp_width(left) + disp_width(right) + 1 > width:
        left = truncate(left, max(1, width - disp_width(right) - 1))
    gap = max(1, width - disp_width(left) - disp_width(right))
    return left + " " * gap + right


def _row(text: str, width: int, p: PaletteLike | None = None) -> str:
    inner = width - 4
    if disp_width(text) > inner:
        text = truncate(text, inner)
    pipe = p.paint("│", p.dim) if p else "│"
    return pipe + " " + _pad(text, inner) + " " + pipe


def _separator(width: int, p: PaletteLike | None = None) -> str:
    line = "├" + "─" * (width - 2) + "┤"
    return p.paint(line, p.dim) if p else line


def _section(
    lines: list[str], label: str, p: PaletteLike, color: str | None = None
) -> None:
    if lines:
        lines.append("")
    lines.append(p.paint("▍ " + label, p.bold + (color or p.cyan)))


def _wrapped_item(
    lines: list[str], text: str, width: int, first: str, rest: str
) -> None:
    lines.extend(wrap(text, width, first_prefix=first, sub_prefix=rest))


def _entry_body(state: BrowserState, p: PaletteLike, width: int) -> list[str]:
    match = state.current
    entry = match.entry
    lines: list[str] = []

    _section(lines, "这是什么意思", p, p.cyan)
    _wrapped_item(lines, entry.meaning, width, "  ", "  ")

    if state.expanded and entry.causes:
        _section(lines, "常见原因", p, p.magenta)
        for index, cause in enumerate(entry.causes, 1):
            _wrapped_item(lines, cause, width, f"  {index}. ", "     ")

    if entry.fixes:
        _section(lines, "修复步骤" if state.expanded else "先试试这一步", p, p.green)
        fixes = entry.fixes if state.expanded else entry.fixes[:1]
        for index, fix in enumerate(fixes, 1):
            prefix = f"  {index}. " if state.expanded else "  ❯ "
            _wrapped_item(lines, fix, width, prefix, "     ")
        if not state.expanded and len(entry.fixes) > 1:
            lines.append(
                p.paint(
                    f"  还有 {len(entry.fixes) - 1} 条修复 · Enter 展开",
                    p.dim,
                )
            )

    if entry.note:
        _section(lines, "注意", p, p.yellow)
        _wrapped_item(lines, entry.note, width, "  ", "  ")

    return lines


def _diagnosis_body(state: BrowserState, p: PaletteLike, width: int) -> list[str]:
    if state.diagnosis_loading:
        return [
            p.paint("▍ 正在检查本机环境…", p.bold + p.cyan),
            "",
            "  所有检查均为只读；慢 DNS 或外部命令会在超时后跳过。",
        ]
    if not state.findings:
        return [
            p.paint("▍ 现场诊断", p.bold + p.cyan),
            "",
            "  没有从这段报错中识别出可检查的模块、端口、路径或命令。",
        ]

    lines: list[str] = []
    for finding in state.findings:
        if finding.ok is True:
            icon, color = "✓", p.green
        elif finding.ok is False:
            icon, color = "×", p.red
        else:
            icon, color = "•", p.yellow
        if lines:
            lines.append("")
        _wrapped_item(
            lines,
            finding.title,
            width,
            p.paint(f"{icon} ", color),
            "  ",
        )
        _wrapped_item(lines, finding.value, width, "  ", "  ")
        if finding.hint:
            _wrapped_item(
                lines,
                finding.hint,
                width,
                p.paint("  → ", p.green),
                "    ",
            )
    return lines


def _help_body(p: PaletteLike) -> list[str]:
    rows = [
        ("↑ / ↓", "选择上一条 / 下一条结果；只有一条时滚动正文"),
        ("← / →", "切换匹配结果"),
        ("Enter", "展开 / 收起全部原因、修复和注意事项"),
        ("D / Tab", "切换只读现场诊断"),
        ("PgUp / PgDn", "向上 / 向下滚动正文"),
        ("Home / End", "跳到正文开头 / 末尾"),
        ("?", "打开 / 关闭这份帮助"),
        ("Q / Esc", "退出交互界面"),
    ]
    lines = [p.paint("▍ 键盘操作", p.bold + p.cyan), ""]
    for key, description in rows:
        lines.append("  " + p.paint(_pad(key, 14), p.bold + p.green) + description)
    return lines


def _confidence_color(p: PaletteLike, confidence: str) -> str:
    if confidence == "高":
        return p.green
    if confidence == "中":
        return p.yellow
    return p.dim


def _meter(score: float, p: PaletteLike, width: int = 10) -> str:
    filled = max(0, min(width, round(score * width)))
    return p.paint("█" * filled, p.cyan) + p.paint("░" * (width - filled), p.blue)


def _entry_meta(
    entry: Entry, current: Match, p: PaletteLike, expanded: bool
) -> str:
    confidence = current.confidence
    return "".join(
        [
            p.paint(entry.category, p.cyan),
            p.paint("  ·  ", p.dim),
            p.paint(confidence, _confidence_color(p, confidence)),
            p.paint("相关 ", p.dim),
            p.paint(f"{current.score:.0%}", p.dim),
            "  ",
            _meter(current.score, p),
            p.paint("  ·  " + ("完整" if expanded else "摘要"), p.dim),
        ]
    )


def _keycap(label: str, p: PaletteLike) -> str:
    if not p.reverse:
        return f"[{label}]"
    return p.paint(f" {label} ", p.reverse + p.bold)


def _actions(items: list[tuple[str, str]], p: PaletteLike) -> str:
    return "  ".join(_keycap(key, p) + " " + text for key, text in items)


def render_frame(
    state: BrowserState,
    p: PaletteLike,
    *,
    width: int = 100,
    height: int = 30,
) -> str:
    """绘制一帧；不包含清屏、隐藏光标等终端控制序列。"""
    width = max(40, width)
    height = max(10, height)
    inner = width - 4
    current = state.current
    entry = current.entry
    position = (
        p.paint(f"{state.selected + 1:02d}", p.bold + p.red)
        + p.paint(f" / {len(state.results):02d}", p.dim)
    )

    top = p.paint("╭" + "─" * (width - 2) + "╮", p.dim)
    brand = (
        p.paint("▌ ", p.cyan)
        + p.paint("ERRSPLAIN", p.bold + p.cyan)
        + p.paint("  ·  报错导航", p.dim)
    )
    header = [
        top,
        _row(_columns(brand, position, inner), width, p),
        _separator(width, p),
    ]
    if state.help_visible:
        title = "键盘帮助"
        meta = p.paint("随时按 Q 或 Esc 退出", p.dim)
        title_color = p.cyan
    elif state.show_diagnosis:
        title = "现场诊断"
        meta = p.paint("只读检查 · 不修改文件、不安装软件、不更改配置", p.dim)
        title_color = p.cyan
    else:
        title = entry.title
        meta = _entry_meta(entry, current, p, state.expanded)
        title_color = p.red
    header.extend(
        [
            _row(p.paint(truncate("› " + title, inner), p.bold + title_color), width, p),
            _row(meta, width, p),
            _separator(width, p),
        ]
    )

    if state.help_visible:
        body = _help_body(p)
    elif state.show_diagnosis:
        body = _diagnosis_body(state, p, inner)
    else:
        body = _entry_body(state, p, inner)

    body_height = max(1, height - len(header) - 2)
    max_scroll = max(0, len(body) - body_height)
    state.scroll = max(0, min(state.scroll, max_scroll))
    start = state.scroll
    visible = body[start : start + body_height]
    visible += [""] * (body_height - len(visible))
    frame = header + [_row(line, width, p) for line in visible]

    if state.help_visible:
        actions = _actions([("? / Enter", "返回"), ("Q", "退出")], p)
    elif state.show_diagnosis:
        actions = _actions(
            [("D", "返回"), ("PgUp/PgDn", "滚动"), ("?", "帮助"), ("Q", "退出")], p
        )
    else:
        actions = _actions(
            [
                ("↑↓", "选择"),
                ("Enter", "收起" if state.expanded else "展开"),
                ("D", "诊断"),
                ("?", "帮助"),
                ("Q", "退出"),
            ],
            p,
        )
    scroll_text = p.paint(
        f"{start + 1}-{min(start + body_height, len(body))}/{len(body)}",
        p.dim,
    )
    footer = _columns(actions, scroll_text if max_scroll else "", width - 2)
    frame.append(p.paint("╰" + "─" * (width - 2) + "╯", p.dim))
    frame.append(truncate(footer, width - 2))
    return "\n".join(frame)


class Keyboard:
    """Windows / POSIX 非阻塞语义一致的按键读取器。"""

    def __init__(self) -> None:
        self._fd: int | None = None
        self._old_attrs = None

    def __enter__(self) -> "Keyboard":
        if os.name != "nt":
            import termios
            import tty

            self._fd = sys.stdin.fileno()
            self._old_attrs = termios.tcgetattr(self._fd)
            tty.setraw(self._fd)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._fd is not None and self._old_attrs is not None:
            import termios

            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_attrs)

    def read(self) -> str:
        if os.name == "nt":
            return self._read_windows()
        return self._read_posix()

    @staticmethod
    def _read_windows() -> str:
        import msvcrt

        char = msvcrt.getwch()
        if char in ("\x00", "\xe0"):
            code = msvcrt.getwch()
            return {
                "H": "up",
                "P": "down",
                "K": "left",
                "M": "right",
                "I": "pgup",
                "Q": "pgdn",
                "G": "home",
                "O": "end",
            }.get(code, "")
        if char in ("\r", "\n"):
            return "enter"
        if char == "\x1b":
            return "esc"
        if char == "\t":
            return "tab"
        return char

    def _read_posix(self) -> str:
        import select

        assert self._fd is not None
        char = os.read(self._fd, 1).decode("utf-8", errors="ignore")
        if char in ("\r", "\n"):
            return "enter"
        if char == "\t":
            return "tab"
        if char != "\x1b":
            return char
        if not select.select([self._fd], [], [], 0.04)[0]:
            return "esc"
        second = os.read(self._fd, 1).decode("ascii", errors="ignore")
        if second != "[":
            return "esc"
        third = os.read(self._fd, 1).decode("ascii", errors="ignore")
        simple = {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end"}
        if third in simple:
            return simple[third]
        if third in ("1", "4", "5", "6"):
            final = os.read(self._fd, 1).decode("ascii", errors="ignore")
            if final == "~":
                return {"1": "home", "4": "end", "5": "pgup", "6": "pgdn"}[third]
        return "esc"


def run_interactive(
    query: str,
    results: Sequence[Match],
    p: PaletteLike,
    *,
    diagnosis_loader: Callable[[str], list[Finding]],
    show_diagnosis: bool = False,
    expanded: bool = False,
) -> int:
    """运行交互界面；非真实终端明确拒绝，避免污染管道输出。"""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print(
            "交互模式需要真实终端；脚本或管道请使用普通输出或 --json。",
            file=sys.stderr,
        )
        return 2

    state = BrowserState(
        results=results,
        expanded=expanded,
        show_diagnosis=show_diagnosis,
        diagnosis_loading=show_diagnosis,
    )
    size = shutil.get_terminal_size(fallback=(100, 30))
    card_width = max(40, min(size.columns, 110))
    margin = max(0, (size.columns - card_width) // 2)
    prefix = " " * margin

    def draw() -> None:
        current_size = shutil.get_terminal_size(fallback=(size.columns, size.lines))
        nonlocal card_width, margin, prefix
        card_width = max(40, min(current_size.columns, 110))
        margin = max(0, (current_size.columns - card_width) // 2)
        prefix = " " * margin
        frame = render_frame(
            state, p, width=card_width, height=current_size.lines
        )
        body = "\n".join(prefix + line for line in frame.splitlines())
        sys.stdout.write("\033[H\033[2J" + body)
        sys.stdout.flush()

    try:
        with Keyboard() as keyboard:
            sys.stdout.write("\033[?1049h\033[?25l")
            try:
                while True:
                    draw()
                    if state.diagnosis_loading:
                        try:
                            state.findings = list(diagnosis_loader(query))
                        except Exception:
                            state.findings = []
                        state.diagnosis_loading = False
                        state.diagnosis_loaded = True
                        continue
                    if apply_key(state, keyboard.read()) == "quit":
                        break
            finally:
                sys.stdout.write("\033[0m\033[?25h\033[?1049l")
                sys.stdout.flush()
    except KeyboardInterrupt:
        return 130
    return 0
