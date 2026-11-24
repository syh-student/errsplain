"""交互式终端浏览器测试；不读写真实终端。"""

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from errsplain.cli import Palette, build_parser, main
from errsplain.diagnose import Finding
from errsplain.interactive import BrowserState, apply_key, render_frame, run_interactive
from errsplain.layout import disp_width, strip_ansi
from errsplain.matcher import Match
from errsplain.models import Entry


def _entry(entry_id: str, title: str) -> Entry:
    return Entry(
        id=entry_id,
        category="python",
        title=title,
        patterns=("demo error",),
        meaning="这是用中文写的一句人话解释。",
        causes=("第一个常见原因", "第二个常见原因"),
        fixes=(
            "python -m pip install demo",
            "python -c 'import sys; print(sys.executable)'",
            "检查虚拟环境是否已经激活",
        ),
        note="这是需要特别注意的一点。",
    )


class TestInteractiveState(unittest.TestCase):
    def setUp(self):
        self.results = [
            Match(_entry("python-demo-one", "第一个错误解释"), 0.90, ()),
            Match(_entry("python-demo-two", "第二个错误解释"), 0.60, ()),
        ]
        self.palette = Palette(False)

    def test_summary_hides_extra_fixes(self):
        frame = render_frame(
            BrowserState(self.results), self.palette, width=90, height=30
        )
        self.assertIn("第一个错误解释", frame)
        self.assertIn("python -m pip install demo", frame)
        self.assertNotIn("检查虚拟环境是否已经激活", frame)
        self.assertIn("Enter 展开", frame)

    def test_enter_expands_and_arrows_select(self):
        state = BrowserState(self.results)
        apply_key(state, "enter")
        self.assertTrue(state.expanded)
        frame = render_frame(state, self.palette, width=90, height=40)
        self.assertIn("检查虚拟环境是否已经激活", frame)
        apply_key(state, "down")
        self.assertEqual(state.selected, 1)
        self.assertEqual(state.scroll, 0)
        apply_key(state, "up")
        self.assertEqual(state.selected, 0)
        apply_key(state, "right")
        self.assertEqual(state.selected, 1)
        apply_key(state, "left")
        self.assertEqual(state.selected, 0)

    def test_diagnosis_toggle_and_render(self):
        state = BrowserState(self.results)
        apply_key(state, "d")
        self.assertTrue(state.show_diagnosis)
        self.assertTrue(state.diagnosis_loading)
        state.findings = [Finding("模块 demo", "没有安装", "安装 demo", ok=False)]
        state.diagnosis_loading = False
        state.diagnosis_loaded = True
        frame = render_frame(state, self.palette, width=90, height=30)
        self.assertIn("模块 demo", frame)
        self.assertIn("没有安装", frame)
        self.assertIn("安装 demo", frame)
        apply_key(state, "tab")
        self.assertFalse(state.show_diagnosis)

    def test_help_and_quit(self):
        state = BrowserState(self.results)
        apply_key(state, "?")
        self.assertTrue(state.help_visible)
        self.assertIn("键盘操作", render_frame(state, self.palette))
        apply_key(state, "enter")
        self.assertFalse(state.help_visible)
        self.assertEqual(apply_key(state, "q"), "quit")
        self.assertEqual(apply_key(state, "esc"), "quit")

    def test_scrolling_is_clamped(self):
        state = BrowserState(self.results[:1], expanded=True)
        apply_key(state, "end")
        render_frame(state, self.palette, width=70, height=12)
        self.assertGreater(state.scroll, 0)
        self.assertLess(state.scroll, 1_000_000)
        apply_key(state, "home")
        self.assertEqual(state.scroll, 0)

    def test_ansi_frame_stays_within_width(self):
        frame = render_frame(
            BrowserState(self.results), Palette(True), width=60, height=20
        )
        for line in frame.splitlines():
            self.assertLessEqual(disp_width(strip_ansi(line)), 60)


class TestInteractiveCli(unittest.TestCase):
    def test_parser_accepts_short_option(self):
        args = build_parser().parse_args(["-i", "demo error"])
        self.assertTrue(args.interactive)

    def test_non_tty_is_rejected(self):
        stderr = io.StringIO()
        with patch("sys.stdin.isatty", return_value=False), redirect_stderr(stderr):
            rc = run_interactive(
                "demo",
                [Match(_entry("python-demo", "Demo"), 1.0, ())],
                Palette(False),
                diagnosis_loader=lambda query: [],
            )
        self.assertEqual(rc, 2)
        self.assertIn("真实终端", stderr.getvalue())

    def test_json_conflict_is_rejected(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            rc = main(["--interactive", "--json", "ModuleNotFoundError"])
        self.assertEqual(rc, 2)
        self.assertIn("不能", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
