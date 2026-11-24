"""控制台画面捕获的纯逻辑与 cuowu 入口测试。"""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from errsplain import console_capture as cc
from errsplain.cli import main


class TestExtractRecentText(unittest.TestCase):
    def test_keeps_recent_error_and_drops_prompt_tail(self):
        lines = [
            "PS C:\\repo> python bad.py",
            "Traceback (most recent call last):",
            '  File "bad.py", line 1',
            "ValueError: boom",
            "",
        ]
        text = cc.extract_recent_text(lines)
        self.assertIn("Traceback", text)
        self.assertIn("ValueError: boom", text)
        self.assertFalse(text.endswith("\n"))

    def test_ansi_removed_and_length_limited(self):
        text = cc.extract_recent_text(
            ["\x1b[31m错误\x1b[0m", "x" * 10000], max_chars=20
        )
        self.assertNotIn("\x1b", text)
        self.assertLessEqual(len(text), 20)

    def test_only_last_lines_are_kept(self):
        lines = [f"line-{i}" for i in range(100)]
        text = cc.extract_recent_text(lines, max_lines=5)
        self.assertEqual(text.splitlines(), [f"line-{i}" for i in range(95, 100)])


class TestCuowuCommand(unittest.TestCase):
    def test_empty_screen_returns_one(self):
        buf = io.StringIO()
        with patch.object(cc, "capture_screen_text", return_value=""):
            with redirect_stdout(buf):
                rc = main(["cuowu"])
        self.assertEqual(rc, 1)
        self.assertIn("没有可读取的报错", buf.getvalue())

    def test_screen_text_opens_interactive(self):
        sample = "git push\nfatal: refusing to merge unrelated histories\nPS C:\\> cuowu"
        with patch.object(cc, "capture_screen_text", return_value=sample), patch(
            "errsplain.interactive.run_interactive", return_value=0
        ) as runner:
            rc = main(["cuowu"])
        self.assertEqual(rc, 0)
        self.assertIn("refusing to merge", runner.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
