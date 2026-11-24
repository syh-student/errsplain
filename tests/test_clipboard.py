"""剪贴板翻译模块测试：判断逻辑、翻译输出、命令分派。"""

import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout

from errsplain import clipboard
from errsplain.clipboard import looks_like_error, translate
from errsplain.cli import main


class TestLooksLikeError(unittest.TestCase):
    def test_error_keywords(self):
        self.assertTrue(looks_like_error("ModuleNotFoundError: No module named 'x'"))
        self.assertTrue(looks_like_error("fatal: refusing to merge unrelated histories"))
        self.assertTrue(looks_like_error("编译失败：找不到符号"))

    def test_error_codes(self):
        """纯错误码（不含 error 字样）也应识别。"""
        self.assertTrue(looks_like_error("TS2322: Type 'string' is not assignable"))
        self.assertTrue(looks_like_error("EACCES: permission denied"))
        self.assertTrue(looks_like_error("CS1061 'foo' does not contain a definition"))

    def test_plain_text_rejected(self):
        self.assertFalse(looks_like_error("hello world"))
        self.assertFalse(looks_like_error(""))
        self.assertFalse(looks_like_error("今天天气不错，出去走走"))

    def test_source_code_rejected(self):
        code = "def foo():\n    import os\n    return 1\nclass Bar:\n    pass"
        self.assertFalse(looks_like_error(code))

    def test_too_long_rejected(self):
        self.assertFalse(looks_like_error("error " + "x" * 5000))


class TestTranslate(unittest.TestCase):
    def test_translates_known_error(self):
        result = translate("fatal: refusing to merge unrelated histories")
        self.assertIsNotNone(result)
        self.assertIn("git", result)
        self.assertIn("试试", result)

    def test_unknown_returns_none(self):
        self.assertIsNone(translate("asdfqwer random nonsense xyz xyz"))


class TestClipCommands(unittest.TestCase):
    def test_clip_help(self):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf), self.assertRaises(SystemExit) as cm:
            main(["clip", "--help"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("剪贴板", buf.getvalue())

    def test_watch_help(self):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf), self.assertRaises(SystemExit) as cm:
            main(["watch", "--help"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("常驻", buf.getvalue())

    def test_watch_rejects_invalid_intervals(self):
        for value in ("nan", "inf", "-inf", "0.1"):
            with self.subTest(value=value):
                buf = io.StringIO()
                with redirect_stderr(buf):
                    rc = main(["watch", f"--interval={value}"])
                self.assertEqual(rc, 2)
                self.assertIn("有限数字", buf.getvalue())

    @unittest.skipUnless(
        clipboard.IS_WINDOWS and os.environ.get("ERRSPLAIN_TEST_REAL_CLIPBOARD") == "1",
        "真实剪贴板集成测试需 Windows 且显式设置 ERRSPLAIN_TEST_REAL_CLIPBOARD=1",
    )
    def test_clipboard_roundtrip(self):
        original = clipboard.get_text()
        try:
            clipboard.set_text("hello clipboard test")
            self.assertEqual(clipboard.get_text(), "hello clipboard test")
        finally:
            clipboard.set_text(original)


if __name__ == "__main__":
    unittest.main()
