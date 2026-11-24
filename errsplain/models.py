"""词条数据模型与校验。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Entry:
    """一条报错词条。

    id:       唯一标识，小写字母/数字/连字符
    category: 分类，如 git / python / npm / docker / linux / node / rust / java / general
    title:    一句话说明「这是什么问题」
    patterns: 用于匹配的原文片段（保留原始大小写与符号）
    meaning:  人话解释：到底发生了什么
    causes:   「最常见原因」列表
    fixes:    「怎么修」步骤列表（可含命令行）
    tags:     搜索关键词，提高命中率
    note:     可选的补充说明 / 注意事项
    """

    id: str
    category: str
    title: str
    patterns: tuple[str, ...]
    meaning: str
    causes: tuple[str, ...] = field(default_factory=tuple)
    fixes: tuple[str, ...] = field(default_factory=tuple)
    tags: tuple[str, ...] = field(default_factory=tuple)
    note: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Entry":
        return cls(
            id=str(raw["id"]),
            category=str(raw.get("category", "general")),
            title=str(raw["title"]),
            patterns=tuple(str(p) for p in raw.get("patterns", [])),
            meaning=str(raw["meaning"]),
            causes=tuple(str(c) for c in raw.get("causes", [])),
            fixes=tuple(str(f) for f in raw.get("fixes", [])),
            tags=tuple(str(t) for t in raw.get("tags", [])),
            note=str(raw.get("note", "")),
        )

    def searchable_text(self) -> str:
        """把所有可匹配文本拼成一个归一化字符串。"""
        parts = [self.title, *self.patterns, self.meaning, *self.tags, self.id]
        return normalize(" ".join(parts))


_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def validate_entry(raw: dict[str, Any]) -> list[str]:
    """返回该词条的问题列表；空列表表示合法。"""
    errors: list[str] = []
    for key in ("id", "title", "patterns", "meaning"):
        if key not in raw:
            errors.append(f"缺少必填字段: {key}")

    if "id" in raw and (
        not isinstance(raw["id"], str) or not _ID_RE.match(raw["id"])
    ):
        errors.append(f"id 不合法（应小写字母/数字/连字符）: {raw['id']!r}")
    for key in ("title", "meaning"):
        if key in raw and (
            not isinstance(raw[key], str) or not raw[key].strip()
        ):
            errors.append(f"{key} 必须是非空字符串")
    if "category" in raw and (
        not isinstance(raw["category"], str) or not raw["category"].strip()
    ):
        errors.append("category 必须是非空字符串")
    if "note" in raw and not isinstance(raw["note"], str):
        errors.append("note 必须是字符串")

    for key in ("patterns", "causes", "fixes", "tags"):
        if key not in raw:
            continue
        items = raw[key]
        if not isinstance(items, list):
            errors.append(f"{key} 必须是数组")
            continue
        if key == "patterns" and not items:
            errors.append("patterns 必须是非空数组")
        if not all(isinstance(item, str) for item in items):
            errors.append(f"{key} 的元素必须是字符串")
        elif key == "patterns" and not all(item.strip() for item in items):
            errors.append("patterns 中不能有空字符串")
    return errors


def normalize(text: str) -> str:
    """归一化文本，用于匹配：转小写、压空白、去 ANSI 颜色码。"""
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()
