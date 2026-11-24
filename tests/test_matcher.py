"""匹配引擎测试。"""

import unittest

from errsplain.database import load_entries
from errsplain.matcher import match
from errsplain.models import Entry, normalize


class TestNormalize(unittest.TestCase):
    def test_lowercase_and_whitespace(self):
        self.assertEqual(normalize("  Hello   WORLD\n"), "hello world")

    def test_strips_ansi(self):
        self.assertEqual(normalize("\x1b[31mError\x1b[0m"), "error")


class TestMatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries = load_entries()

    def test_exact_error_matches_top(self):
        results = match("fatal: refusing to merge unrelated histories", self.entries)
        self.assertTrue(results)
        self.assertEqual(results[0].entry.id, "git-unrelated-histories")
        self.assertGreaterEqual(results[0].score, 0.6)

    def test_python_module_not_found(self):
        results = match(
            "ModuleNotFoundError: No module named 'flask'",
            self.entries,
            category="python",
        )
        self.assertTrue(results)
        self.assertEqual(results[0].entry.id, "python-modulenotfound")

    def test_noise_tolerance(self):
        """带路径和数字的报错仍能命中。"""
        results = match(
            "FileNotFoundError: [Errno 2] No such file or directory: '/home/me/data_12345.csv'",
            self.entries,
        )
        self.assertTrue(results)
        self.assertEqual(results[0].entry.id, "python-nosuchfile")

    def test_category_filter(self):
        results = match(
            "Cannot find module 'lodash'", self.entries, category="npm"
        )
        self.assertTrue(results)
        self.assertTrue(all(m.entry.category == "npm" for m in results))

    def test_gibberish_no_match(self):
        results = match("zzzz qqqq xxxx random nonsense", self.entries)
        self.assertEqual(results, [])

    def test_empty_query(self):
        self.assertEqual(match("", self.entries), [])

    def test_top_k(self):
        results = match("Permission denied", self.entries, top_k=1)
        self.assertLessEqual(len(results), 1)


class TestEntryValidation(unittest.TestCase):
    def test_from_bad_dict_reports(self):
        from errsplain.models import validate_entry

        errs = validate_entry({"id": "Bad ID!", "patterns": [], "title": "x"})
        self.assertTrue(any("id" in e for e in errs))
        self.assertTrue(any("patterns" in e for e in errs))
        self.assertTrue(any("meaning" in e for e in errs))

    def test_field_types_are_validated(self):
        from errsplain.models import validate_entry

        base = {
            "id": "valid-id",
            "title": "标题",
            "patterns": ["original error"],
            "meaning": "解释",
        }
        bad_values = (
            ("tags", "不能写成字符串"),
            ("causes", "不能写成字符串"),
            ("fixes", "不能写成字符串"),
            ("note", ["不能写成数组"]),
            ("category", ""),
        )
        for field, value in bad_values:
            with self.subTest(field=field):
                raw = {**base, field: value}
                errors = validate_entry(raw)
                self.assertTrue(
                    any(field in error for error in errors),
                    f"{field} 的错误类型未被拦截：{errors}",
                )


if __name__ == "__main__":
    unittest.main()
