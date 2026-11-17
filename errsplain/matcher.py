"""匹配引擎：把用户贴进来的报错，匹配到最相关的词条。

设计原则：
1. 纯标准库，零依赖。
2. 分层打分：原文精确包含 > 高信息量模式命中 > 模糊相似度。
3. 可解释：返回每条命中的得分与理由，方便调试词条质量。
4. 快：词条的归一化结果缓存起来，查询时不做重复计算；
   关键词零重合时跳过昂贵的序列比对。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache

from .models import Entry, normalize

# 这些词在报错里几乎人人都有，区分度低，降权处理。
_STOPWORDS = frozenset(
    "error err fail failed failure exception warning warn fatal info the a an to of in on "
    "for from at by is are was were be been being with and or not no it its this that "
    "file line code command process exit status".split()
)

# 报错里常见的「噪声」：路径、时间戳、十六进制地址、数字 ID。
# 匹配前保留它们无妨，但对模糊比对会拉低分数，这里选择性剥离用于相似度计算。
_NOISE_RE = re.compile(
    r"(?:"
    r"/[^\s:'\"()]+|"          # unix 路径
    r"[A-Za-z]:\\[^\s:'\"()]+|"  # windows 路径
    r"\b0x[0-9a-fA-F]+\b|"     # 十六进制地址
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}[^\s]*|"  # 时间戳
    r"\b\d+\b"                 # 纯数字
    r")"
)


@dataclass
class Match:
    """一次匹配结果。"""

    entry: Entry
    score: float
    reasons: tuple[str, ...]

    @property
    def confidence(self) -> str:
        if self.score >= 0.75:
            return "高"
        if self.score >= 0.45:
            return "中"
        return "低"


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9_]+", text)
    return {w for w in words if w and w not in _STOPWORDS and len(w) > 1}


def _strip_noise(text: str) -> str:
    return _NOISE_RE.sub(" ", text)


# ------------------------------------------------------------------ 词条缓存
# Entry 是 frozen dataclass（可哈希），所以可以安全地按词条缓存预处理结果。
# 这样每次查询只需归一化用户输入，不再重复归一化全部词条。


@lru_cache(maxsize=4096)
def _pattern_regex(pn: str) -> re.Pattern[str] | None:
    """把含 `.*` 通配的 pattern 编译成正则；纯字面 pattern 返回 None。

    词库里写 `Swap file .* already exists` 这类带通配的片段更自然，
    若按字面匹配则永远命中不了。这里把 `.*` 当作「任意字符（限长）」处理，
    其余字符全部转义，避免用户写的正则元字符引发意外或 ReDoS。
    """
    if ".*" not in pn:
        return None
    body = ".{0,80}".join(re.escape(part) for part in pn.split(".*"))
    try:
        return re.compile(body)
    except re.error:  # 理论上不会发生（已全转义），保险起见
        return None


@lru_cache(maxsize=4096)
def _norm_patterns(entry: Entry) -> tuple[str, ...]:
    return tuple(pn for pn in (normalize(p) for p in entry.patterns) if pn)


@lru_cache(maxsize=4096)
def _clean_patterns(entry: Entry) -> tuple[str, ...]:
    return tuple(pn for pn in (normalize(_strip_noise(p)) for p in entry.patterns) if pn)


@lru_cache(maxsize=4096)
def _entry_tokens(entry: Entry) -> frozenset[str]:
    return frozenset(_tokens(entry.searchable_text()))


def _score_entry(
    query_norm: str,
    query_compact: str,
    query_clean: str,
    query_tokens: set[str],
    entry: Entry,
) -> Match | None:
    """对单条词条打分。返回 None 表示完全不相关。"""
    reasons: list[str] = []
    score = 0.0

    patterns = _norm_patterns(entry)

    # 1) 原文片段精确命中 —— 最强的信号
    best_pattern = 0.0
    for pn in patterns:
        if pn in query_norm:
            best_pattern = 1.0
            break
        if pn.replace(" ", "") in query_compact:
            best_pattern = max(best_pattern, 0.92)
            continue
        # 带 .* 通配的 pattern（如 "Swap file .* already exists"）
        rx = _pattern_regex(pn)
        if rx is not None and rx.search(query_norm):
            best_pattern = max(best_pattern, 0.95)
    if best_pattern:
        score += 0.62 * best_pattern
        reasons.append("报错原文命中")

    # 2) 关键词重叠（Jaccard），带停用词过滤
    entry_tokens = _entry_tokens(entry)
    overlap = 0
    if entry_tokens and query_tokens:
        overlap = len(query_tokens & entry_tokens)
        if overlap:
            jaccard = overlap / len(query_tokens | entry_tokens)
            score += 0.24 * jaccard
            if jaccard >= 0.12:
                reasons.append(f"{overlap} 个关键词吻合")

    # 3) 模糊相似度：对每个 pattern 与 query 做序列比对，取最高。
    #    这是最贵的一步——没有任何原文命中、且关键词零重合时直接跳过，
    #    这类输入即便算模糊分也基本不可能过阈值，跳过不影响召回。
    if best_pattern or overlap:
        fuzzy = 0.0
        if query_clean:
            for pn in _clean_patterns(entry):
                matcher = SequenceMatcher(None, pn, query_clean)
                ratio = matcher.ratio()
                # 若报错原文里包含 pattern 的大部分，也算强命中
                contained = matcher.find_longest_match(0, len(pn), 0, len(query_clean)).size
                local = contained / max(len(pn), 1)
                fuzzy = max(fuzzy, ratio, local * 0.95)
        if fuzzy >= 0.55:
            score += 0.14 * fuzzy
            reasons.append("措辞高度相似")

    # 完全没理由就不返回
    if not reasons:
        return None

    # 得分上限保护
    return Match(entry=entry, score=min(score, 1.0), reasons=tuple(reasons))


# 超长输入的截断上限。日志动辄几十上百 KB，而真正要看的报错通常在末尾；
# 截断既保住了准确性，也把最坏情况的耗时压在毫秒级。
_MAX_QUERY_CHARS = 20_000


def match(
    query: str,
    entries: list[Entry],
    *,
    category: str | None = None,
    top_k: int = 3,
    threshold: float = 0.22,
) -> list[Match]:
    """返回按相关度排序的匹配结果。

    threshold 过滤掉弱相关；若一字不差命中原文，则强制保留。
    """
    if not query or not query.strip():
        return []

    # 超长输入只保留尾部：报错几乎总在日志最后，而头部往往是噪声
    if len(query) > _MAX_QUERY_CHARS:
        query = query[-_MAX_QUERY_CHARS:]

    query_norm = normalize(query)
    query_compact = query_norm.replace(" ", "")
    query_clean = normalize(_strip_noise(query_norm))
    query_tokens = _tokens(query_norm)
    if category:
        entries = [e for e in entries if e.category == category]

    matches: list[Match] = []
    for entry in entries:
        m = _score_entry(query_norm, query_compact, query_clean, query_tokens, entry)
        if m is None:
            continue
        exact = any(
            pn in query_norm
            or (_pattern_regex(pn) is not None and _pattern_regex(pn).search(query_norm))
            for pn in _norm_patterns(entry)
        )
        if exact or m.score >= threshold:
            matches.append(m)

    matches.sort(key=lambda m: (-m.score, m.entry.id))
    return matches[:top_k]


def clear_cache() -> None:
    """清空词条缓存（词库热重载/测试时用）。"""
    _norm_patterns.cache_clear()
    _clean_patterns.cache_clear()
    _entry_tokens.cache_clear()
    _pattern_regex.cache_clear()
