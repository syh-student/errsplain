"""词库质量测试：保证每一条词条都能命中自己、字段完整、无有害泛化 pattern。"""

import unittest

from errsplain.database import load_entries
from errsplain.matcher import match
from errsplain.models import normalize


class TestDatasetQuality(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries = load_entries()

    def test_every_entry_matches_itself(self):
        """每条词条的每个 pattern 至少有一个能命中自己（进 top 3）。"""
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
        ids = [e.id for e in self.entries]
        self.assertEqual(len(ids), len(set(ids)), "存在重复的 id")

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

    def test_every_entry_has_meaningful_content(self):
        for e in self.entries:
            with self.subTest(entry=e.id):
                self.assertTrue(e.meaning.strip(), f"{e.id} 缺 meaning")
                self.assertGreaterEqual(len(e.title), 2, f"{e.id} title 过短")
                self.assertTrue(e.patterns, f"{e.id} 无 pattern")

    def test_dataset_size(self):
        """词条数量只增不减的护栏。"""
        self.assertGreaterEqual(len(self.entries), 276)


if __name__ == "__main__":
    unittest.main()
