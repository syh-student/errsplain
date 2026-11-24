"""词库加载、校验与查找。"""

from __future__ import annotations

import json
from pathlib import Path

from .models import Entry, validate_entry

_DEFAULT_DATA = Path(__file__).parent / "data" / "entries.json"

# 用户自定义词库：~/.errsplain/entries.json，可直接往里加词条，无需改源码。
_USER_DATA = Path.home() / ".errsplain" / "entries.json"


class DatasetError(Exception):
    """词库文件损坏或格式不合法。"""


def _load_file(path: Path, *, required: bool) -> list[dict]:
    try:
        if not path.exists():
            if required:
                raise DatasetError(f"词库文件不存在: {path}")
            return []
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise DatasetError(f"词库文件读取失败: {path}\n  {exc}") from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DatasetError(f"词库 JSON 解析失败: {path}\n  {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("entries"), list):
        raise DatasetError(f"词库格式错误（应为含 entries 数组的对象）: {path}")
    return raw["entries"]


def load_entries(
    *, builtin: Path | None = None, include_user: bool = True
) -> list[Entry]:
    """加载内置词条 + 用户词条；用户词条可覆盖同 id 的内置词条。

    同一个文件内部的重复 id 是数据错误，必须在合并前报告；否则先去重再
    校验会让重复 id 静默覆盖，CLI 与测试都看不到问题。
    """
    sources = [("内置词库", _load_file(builtin or _DEFAULT_DATA, required=True))]
    if include_user:
        sources.append(("用户词库", _load_file(_USER_DATA, required=False)))

    problems: list[str] = []
    by_id: dict[str, Entry] = {}
    for source, raw_entries in sources:
        source_ids: set[str] = set()
        for i, raw in enumerate(raw_entries):
            if not isinstance(raw, dict):
                problems.append(f"{source}第 {i + 1} 条不是对象")
                continue
            errs = validate_entry(raw)
            if errs:
                problems.append(
                    f"{source}第 {i + 1} 条 ({raw.get('id', '?')}): "
                    + "; ".join(errs)
                )
                continue
            entry = Entry.from_dict(raw)
            if entry.id in source_ids:
                problems.append(f"{source}第 {i + 1} 条 ({entry.id}): id 重复")
                continue
            source_ids.add(entry.id)
            by_id[entry.id] = entry

    if problems:
        raise DatasetError("词库校验失败:\n  " + "\n  ".join(problems))

    return list(by_id.values())


def categories(entries: list[Entry]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in entries:
        counts[e.category] = counts.get(e.category, 0) + 1
    return dict(sorted(counts.items()))
