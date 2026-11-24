"""词库质量与加载校验：字段、重复数据、覆盖语义和匹配护栏。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from errsplain.database import DatasetError, load_entries
from errsplain.matcher import match
from errsplain.models import normalize


class TestDatasetQuality(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries = load_entries()
        data_path = (
            Path(__file__).resolve().parents[1]
            / "errsplain"
            / "data"
            / "entries.json"
        )
        cls.raw_entries = json.loads(data_path.read_text(encoding="utf-8"))["entries"]

    def test_every_entry_matches_itself(self):
        """每条词条至少有一个 pattern 能命中自己（进 top 3）。"""
        bad = []
        for e in self.entries:
            hit = any(
                any(m.entry.id == e.id for m in match(pat, self.entries, top_k=3))
                for pat in e.patterns
            )
            if not hit:
                bad.append(e.id)
        self.assertEqual(bad, [], f"这些词条无法命中自己: {bad}")

    def test_no_duplicate_ids(self):
        """直接检查原始 JSON，不能检查已被加载器去重后的列表。"""
        ids = [e["id"] for e in self.raw_entries]
        self.assertEqual(len(ids), len(set(ids)), "存在重复的 id")

    def test_no_duplicate_patterns_within_entry(self):
        for e in self.raw_entries:
            with self.subTest(entry=e["id"]):
                patterns = [normalize(p) for p in e["patterns"]]
                self.assertEqual(
                    len(patterns), len(set(patterns)), "条目内存在重复 pattern"
                )

    def test_no_identical_pattern_sets(self):
        """不同词条共用完全相同的一组 pattern，通常意味着重复收录。"""
        by_patterns = {}
        for e in self.raw_entries:
            key = tuple(sorted(normalize(p) for p in e["patterns"]))
            by_patterns.setdefault(key, []).append(e["id"])
        duplicates = [ids for ids in by_patterns.values() if len(ids) > 1]
        self.assertEqual(duplicates, [], f"存在完全重复的词条 patterns: {duplicates}")

    def test_field_schema(self):
        for e in self.raw_entries:
            with self.subTest(entry=e["id"]):
                for key in ("id", "category", "title", "meaning", "note"):
                    self.assertIsInstance(e[key], str, f"{key} 必须是字符串")
                for key in ("patterns", "causes", "fixes", "tags"):
                    self.assertIsInstance(e[key], list, f"{key} 必须是数组")
                    self.assertTrue(
                        all(isinstance(item, str) for item in e[key]),
                        f"{key} 的元素必须是字符串",
                    )

    def test_every_entry_has_meaningful_content(self):
        for e in self.entries:
            with self.subTest(entry=e.id):
                self.assertTrue(e.meaning.strip(), f"{e.id} 缺 meaning")
                self.assertGreaterEqual(len(e.title), 2, f"{e.id} title 过短")
                self.assertTrue(e.patterns, f"{e.id} 无 pattern")

    def test_no_harmful_generic_patterns(self):
        """这些词太常见，放在 pattern 里会让无关报错误命中。"""
        banned = {
            "traceback (most recent call last)",
            "because",
            "is null",
            "expected",
            "found",
            "relation",
            "upstream",
            "no such file or directory",
        }
        violations = []
        for e in self.entries:
            for pat in e.patterns:
                if normalize(pat) in banned:
                    violations.append(f"{e.id}: {pat!r}")
        self.assertEqual(violations, [], f"发现有害泛化 pattern: {violations}")

    def test_dataset_size(self):
        """词条数量只增不减的护栏。"""
        self.assertGreaterEqual(len(self.entries), 283)


class TestDatasetLoading(unittest.TestCase):
    @staticmethod
    def _entry(title="标题", pattern="unique error"):
        return {
            "id": "same-id",
            "category": "python",
            "title": title,
            "patterns": [pattern],
            "meaning": "解释",
            "causes": [],
            "fixes": [],
            "tags": [],
            "note": "",
        }

    @staticmethod
    def _write(path, entries):
        path.write_text(
            json.dumps({"version": "1", "entries": entries}, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_duplicate_builtin_ids_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "entries.json"
            self._write(path, [self._entry(), self._entry("重复")])
            with self.assertRaisesRegex(DatasetError, "id 重复"):
                load_entries(builtin=path, include_user=False)

    def test_duplicate_user_ids_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            builtin = root / "builtin.json"
            user = root / "user.json"
            self._write(builtin, [self._entry()])
            self._write(user, [self._entry(), self._entry("重复")])
            with patch("errsplain.database._USER_DATA", user):
                with self.assertRaisesRegex(DatasetError, "id 重复"):
                    load_entries(builtin=builtin, include_user=True)

    def test_user_entry_overrides_builtin(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            builtin = root / "builtin.json"
            user = root / "user.json"
            self._write(builtin, [self._entry("内置")])
            self._write(user, [self._entry("用户覆盖")])
            with patch("errsplain.database._USER_DATA", user):
                entries = load_entries(builtin=builtin, include_user=True)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].title, "用户覆盖")

    def test_bom_json_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "entries.json"
            payload = json.dumps(
                {"version": "1", "entries": [self._entry()]}, ensure_ascii=False
            ).encode("utf-8")
            path.write_bytes(b"\xef\xbb\xbf" + payload)
            entries = load_entries(builtin=path, include_user=False)
            self.assertEqual(len(entries), 1)

    def test_read_error_is_dataset_error(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "entries.json"
            self._write(path, [self._entry()])
            with patch("pathlib.Path.read_text", side_effect=OSError("模拟读取失败")):
                with self.assertRaisesRegex(DatasetError, "读取失败"):
                    load_entries(builtin=path, include_user=False)


if __name__ == "__main__":
    unittest.main()
