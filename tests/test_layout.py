"""layout（显示宽度排版）的单元测试。"""

import unittest

from errsplain.layout import disp_width, truncate, wrap


class TestDispWidth(unittest.TestCase):
    def test_cjk_is_double_width(self):
        self.assertEqual(disp_width("中文"), 4)
        self.assertEqual(disp_width("中文ab"), 6)
        self.assertEqual(disp_width("，"), 2)  # 全角标点

    def test_ansi_codes_not_counted(self):
        self.assertEqual(disp_width("\033[1m中文\033[0m"), 4)


class TestWrap(unittest.TestCase):
    def test_hanging_indent_alignment(self):
        lines = wrap("短句 " * 10, 30, first_prefix="  标签  ", sub_prefix="        ")
        self.assertGreater(len(lines), 1)
        for ln in lines[1:]:
            self.assertTrue(ln.startswith("        "), f"续行未对齐: {ln!r}")

    def test_ascii_words_not_broken(self):
        lines = wrap("python -m pip install requests", 30, first_prefix="    1. ", sub_prefix="       ")
        joined = "\n".join(lines)
        self.assertIn("python", joined)
        for word in ("pip", "install", "requests"):
            # 单词必须完整出现在某一行，不能被拆成两半
            self.assertTrue(
                any(word in ln for ln in lines), f"单词 {word} 被拆断了"
            )

    def test_cjk_breaks_anywhere(self):
        lines = wrap("这是一段没有空格的中文长句子需要被折行处理", 20, first_prefix="", sub_prefix="")
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(ln for ln in lines))

    def test_overlong_token_hard_split(self):
        lines = wrap("a" * 100, 30)
        for ln in lines:
            self.assertLessEqual(len(ln), 30)

    def test_closing_punctuation_never_starts_line(self):
        text = "一句话（含括号内容）结尾。再来一句（括号内容更长一些，超出一行的宽度）结束。"
        for width in (20, 30, 40):
            for ln in wrap(text, width):
                self.assertFalse(
                    ln[:1] in "）】」」”，。；：",
                    f"宽度 {width} 行首出现标点: {ln!r}",
                )

    def test_explicit_newline_respected(self):
        lines = wrap("第一行\n第二行", 40)
        self.assertEqual(len(lines), 2)

    def test_extremely_narrow_width(self):
        # 不应抛异常也不应死循环
        lines = wrap("中文内容测试" * 5, 20)
        self.assertTrue(lines)


class TestTruncate(unittest.TestCase):
    def test_short_passthrough(self):
        self.assertEqual(truncate("短文本", 10), "短文本")

    def test_long_truncated_with_ellipsis(self):
        out = truncate("很长的中文内容" * 10, 20)
        self.assertLessEqual(disp_width(out), 20)
        self.assertTrue(out.endswith("…"))
