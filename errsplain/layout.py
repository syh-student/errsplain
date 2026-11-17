"""终端排版：显示宽度感知的折行、截断（零依赖）。

终端里 East Asian Wide/Fullwidth 字符（中文、全角标点、多数 emoji）
占 2 列，其余占 1 列。直接用 ``len()`` 计算或靠终端自动硬折行，
会让续行顶格、编号列表错位。本模块按显示宽度主动折行，并支持
悬挂缩进（续行与首行正文对齐）。
"""

from __future__ import annotations

import re
import unicodedata

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")

# 行首禁则字符：右括号/引号/标点不应出现在行首，宁可轻微超宽
_NO_LINE_START = set("）】」』〕〉”’！？。，、；：…％·")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def char_width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    if unicodedata.east_asian_width(ch) in ("F", "W"):
        return 2
    return 1


def disp_width(text: str) -> int:
    """文本在终端里的显示列数（忽略 ANSI 颜色码）。"""
    return sum(char_width(c) for c in strip_ansi(text))


def truncate(text: str, width: int, ellipsis: str = "…") -> str:
    """按显示宽度截断，超宽时以省略号结尾。"""
    if disp_width(text) <= width:
        return text
    maxw = max(1, width - char_width(ellipsis))
    out: list[str] = []
    w = 0
    for ch in strip_ansi(text):
        cw = char_width(ch)
        if w + cw > maxw:
            break
        out.append(ch)
        w += cw
    return "".join(out) + ellipsis


def _atoms(text: str) -> list[tuple[str, str]]:
    """把文本切成可断行的最小单元。

    - 空格/换行：单独成单元（可断行点）
    - 宽字符（中文/全角）：单独成单元（CJK 可在任意字符间断行）
    - 连续窄字符（ASCII 单词）：整体成单元（不在单词中间断开）
    """
    out: list[tuple[str, str]] = []
    buf = ""
    for ch in text:
        if ch in " \t\n":
            if buf:
                out.append(("w", buf))
                buf = ""
            out.append(("s", ch))
        elif char_width(ch) >= 2:
            if buf:
                out.append(("w", buf))
                buf = ""
            out.append(("w", ch))
        else:
            buf += ch
    if buf:
        out.append(("w", buf))
    return out


def _split_to_width(token: str, width: int) -> list[str]:
    """把一个超长单元硬切到指定显示宽度。"""
    chunks: list[str] = []
    buf = ""
    w = 0
    for ch in token:
        cw = char_width(ch)
        if buf and w + cw > width:
            chunks.append(buf)
            buf = ""
            w = 0
        buf += ch
        w += cw
    if buf:
        chunks.append(buf)
    return chunks


def wrap(
    text: str,
    width: int,
    first_prefix: str = "",
    sub_prefix: str = "",
) -> list[str]:
    """按显示宽度折行，返回带前缀的行列表。

    width 是**包含前缀**的总宽度。sub_prefix 通常与 first_prefix
    等宽（悬挂缩进）。已有换行符会被保留为独立断行。
    """
    width = max(width, 20)
    # 制表符在终端里会跳到下一个 8 列制表位，按 1 列算会撑破行宽
    text = text.expandtabs(8)
    lines: list[str] = []
    cur = first_prefix
    cur_w = disp_width(first_prefix)
    started = False  # 当前行上是否已有可见内容

    def flush() -> None:
        nonlocal cur, cur_w, started
        lines.append(cur.rstrip())
        cur = sub_prefix
        cur_w = disp_width(sub_prefix)
        started = False

    def place(tok: str, tw: int) -> None:
        nonlocal cur, cur_w, started
        avail = width - cur_w
        if not started:
            chunks = [tok] if tw <= avail else _split_to_width(tok, avail)
            cur += chunks[0]
            cur_w += disp_width(chunks[0])
            started = True
            for c in chunks[1:]:
                flush()
                cur += c
                cur_w += disp_width(c)
        else:
            if cur_w + tw <= width:
                cur += tok
                cur_w += tw
                return
            # 禁则：行首标点宁可让当前行轻微超宽也不换行
            if tok in _NO_LINE_START and cur_w + tw <= width + 2:
                cur += tok
                cur_w += tw
                return
            flush()
            place(tok, tw)

    for kind, tok in _atoms(text):
        if kind == "s":
            if tok == "\n":
                flush()
            elif started:
                cur += tok
                cur_w += 1
            # 行首空格直接跳过
        else:
            place(tok, disp_width(tok))
    lines.append(cur.rstrip())
    return lines
