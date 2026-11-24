"""命令行参数边界测试。"""

import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from errsplain.cli import _read_query, build_parser


class TestThresholdArgument(unittest.TestCase):
    def test_threshold_rejects_invalid_numbers(self):
        for value in ("nan", "inf", "-inf", "-0.01", "1.01"):
            with self.subTest(value=value):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
                    build_parser().parse_args([f"--threshold={value}", "error"])
                self.assertEqual(cm.exception.code, 2)

    def test_threshold_accepts_bounds(self):
        for raw, expected in (("0", 0.0), ("0.22", 0.22), ("1", 1.0)):
            with self.subTest(raw=raw):
                args = build_parser().parse_args(["--threshold", raw, "error"])
                self.assertEqual(args.threshold, expected)


class TestClipArgument(unittest.TestCase):
    def test_clip_flag_parsed(self):
        args = build_parser().parse_args(["--clip", "--interactive"])
        self.assertTrue(args.clip)

    def test_clip_reads_text_from_clipboard(self):
        args = build_parser().parse_args(["--clip"])
        with patch("errsplain.cli.get_text", return_value="ModuleNotFoundError: boom"):
            self.assertEqual(_read_query(args), "ModuleNotFoundError: boom")


if __name__ == "__main__":
    unittest.main()
