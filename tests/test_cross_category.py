"""跨场景误命中护栏：确保不同工具/场景的报错不会互相串台。

这类测试比「能否命中」更重要——命中率低只是不好用，误命中会给出错误建议。
"""

import unittest

from errsplain.database import load_entries
from errsplain.matcher import match


class TestNoCrossCategoryConfusion(unittest.TestCase):
    """一条报错不能被错认成另一个工具的问题。"""

    @classmethod
    def setUpClass(cls):
        cls.entries = load_entries()

    def _assert_matches_category(self, text, category):
        r = match(text, self.entries, top_k=1, threshold=0.45)
        self.assertTrue(r, f"应当命中 {category}，却完全没匹配：{text!r}")
        self.assertEqual(
            r[0].entry.category, category,
            f"{text!r} 应命中 {category}，实际命中了 {r[0].entry.category}（{r[0].entry.id}）",
        )

    def test_ruby_gem_not_cpp_cmake(self):
        """Ruby 的 'Could not find gem' 不能被认成 CMake 配置失败。"""
        r = match("Could not find gem 'rails'", self.entries, top_k=1, threshold=0.45)
        self.assertTrue(
            not r or r[0].entry.category != "cpp",
            f"Ruby 报错被误判为 C++：{r[0].entry.id if r else '-'}",
        )

    def test_generic_not_satisfied_not_rust(self):
        """泛泛的'条件不满足'不能被认成 Rust trait bound。"""
        r = match("AssertionError: precondition not satisfied", self.entries, top_k=1, threshold=0.45)
        self.assertTrue(
            not r or r[0].entry.category != "rust",
            f"普通断言失败被误判为 Rust：{r[0].entry.id if r else '-'}",
        )

    def test_generic_does_not_exist_not_postgres(self):
        """'配置项不存在'不能被认成 PostgreSQL 表不存在。"""
        r = match("ConfigError: key does not exist in settings", self.entries, top_k=1, threshold=0.45)
        self.assertTrue(
            not r or r[0].entry.category not in ("postgresql", "mysql"),
            f"普通'不存在'被误判为数据库：{r[0].entry.id if r else '-'}",
        )

    def test_python_syntax_not_shell(self):
        """Python 的 SyntaxError 不能被认成 shell 语法错误。"""
        self._assert_matches_category("SyntaxError: invalid syntax", "python")

    def test_port_in_use_goes_to_npm(self):
        self._assert_matches_category("Error: listen EADDRINUSE :::3000", "npm")


class TestBenignInputsStaySilent(unittest.TestCase):
    """普通文本/成功输出不该触发任何解释（hook 模式靠这个保持安静）。"""

    @classmethod
    def setUpClass(cls):
        cls.entries = load_entries()

    def test_benign_silent(self):
        for text in [
            "hello world",
            "Successfully built abc123",
            "added 142 packages in 3s",
            "今天天气不错，出去走走",
            "def foo():\n    return 1\nclass Bar:\n    pass",
        ]:
            with self.subTest(text=text[:25]):
                r = match(text, self.entries, top_k=1, threshold=0.45)
                self.assertFalse(r, f"普通文本不应命中：{text!r} -> {r[0].entry.id if r else ''}")


class TestChineseErrors(unittest.TestCase):
    """中文报错必须能匹配——中文用户会直接看到这类提示。"""

    @classmethod
    def setUpClass(cls):
        cls.entries = load_entries()

    def test_chinese_errors_match(self):
        cases = [
            ("找不到模块", "general"),
            ("模块未找到", "general"),
            ("权限不足", "general"),
            ("拒绝访问", "windows"),
            ("连接超时", "general"),
            ("连接被拒绝", "general"),
            ("磁盘空间不足", "general"),
            ("证书已过期", "general"),
            ("端口被占用", "windows"),
        ]
        for text, expect in cases:
            with self.subTest(text=text):
                r = match(text, self.entries, top_k=1, threshold=0.45)
                self.assertTrue(r, f"中文报错未命中：{text}")
                self.assertEqual(r[0].entry.category, expect, f"{text} 分类错误")


class TestLongInputSafety(unittest.TestCase):
    """超长输入不能拖慢匹配，且尾部报错仍要能命中。"""

    @classmethod
    def setUpClass(cls):
        cls.entries = load_entries()

    def test_huge_input_is_fast(self):
        import time

        blob = "error line\n" * 40000  # ~440KB
        t0 = time.perf_counter()
        match(blob, self.entries, top_k=3)
        elapsed = (time.perf_counter() - t0) * 1000
        self.assertLess(elapsed, 200, f"超长输入耗时 {elapsed:.0f} ms，过慢")

    def test_error_at_tail_still_found(self):
        log = "noise line\n" * 5000 + "fatal: refusing to merge unrelated histories"
        r = match(log, self.entries, top_k=1, threshold=0.45)
        self.assertTrue(r, "长日志尾部的报错应能命中")
        self.assertEqual(r[0].entry.id, "git-unrelated-histories")


class TestCacheBehaviour(unittest.TestCase):
    """缓存必须可清空且清空后结果不变（私有词库热重载依赖它）。"""

    def test_clear_cache_keeps_results(self):
        from errsplain.matcher import clear_cache

        entries = load_entries()
        before = match("fatal: refusing to merge unrelated histories", entries, top_k=1)
        clear_cache()
        after = match("fatal: refusing to merge unrelated histories", entries, top_k=1)
        self.assertEqual(before[0].entry.id, after[0].entry.id)


class TestCoreScenariosStillWork(unittest.TestCase):
    """核心场景的回归护栏：改词库别把主力场景改坏。"""

    @classmethod
    def setUpClass(cls):
        cls.entries = load_entries()

    def test_key_scenarios(self):
        cases = [
            ("fatal: refusing to merge unrelated histories", "git"),
            ("ModuleNotFoundError: No module named 'flask'", "python"),
            ("curl: (6) Could not resolve host: foo.com", "network"),
            ("curl: (60) SSL certificate problem", "network"),
            ("ssh: connect to host 1.2.3.4 port 22: Connection refused", "network"),
            ("TS2322: Type 'string' is not assignable", "typescript"),
            ("declared and not used: x", "go"),
            ("java.lang.NullPointerException", "java"),
            ("Too many open files", "system"),
            ("NU1101: Unable to find package", "csharp"),
            ("ORA-12541: TNS:no listener", "oracle"),
            ("Login failed for user 'sa'", "sqlserver"),
            ("Gradle sync failed: Could not resolve all files", "android"),
            ("Error acquiring the state lock", "terraform"),
            ("Unresolved reference: foo", "kotlin"),
            ("Can't locate Foo/Bar.pm in @INC", "perl"),
            ("NullInjectorError: No provider for HttpClient", "frontend"),
            ("BeanCreationException: Error creating bean", "spring"),
            ("CUDA out of memory. Tried to allocate", "ml"),
            ("E501 line too long", "lint"),
        ]
        for text, expected_cat in cases:
            with self.subTest(text=text[:35]):
                r = match(text, self.entries, top_k=1, threshold=0.45)
                self.assertTrue(r, f"核心场景失效：{text!r}")
                self.assertEqual(r[0].entry.category, expected_cat, f"{text!r} 分类错误")


class TestWildcardPatterns(unittest.TestCase):
    """带 `.*` 通配的 pattern 必须真的能匹配（曾有一批永远命中不了）。"""

    @classmethod
    def setUpClass(cls):
        cls.entries = load_entries()

    def test_wildcard_matches(self):
        cases = [
            ('Swap file ".foo.swp" already exists', "editor"),
            ("Port 8080 was already in use", "spring"),
            ('Key (id)=(42) already exists', "postgresql"),
            ("Timeout 30000ms exceeded", "test"),
        ]
        for text, expected in cases:
            with self.subTest(text=text[:32]):
                r = match(text, self.entries, top_k=1, threshold=0.45)
                self.assertTrue(r, f"通配 pattern 未生效：{text!r}")
                self.assertEqual(r[0].entry.category, expected, f"{text!r} 分类错误")

    def test_wildcard_bounded(self):
        """通配段应限长，避免超长输入拖慢匹配。"""
        r = match("Swap file " + "x" * 500 + " already exists", self.entries, top_k=1, threshold=0.45)
        # 超过 80 字符的通配段不应被单条通配规则命中（可能靠模糊分命中，但不能是精确命中）
        if r:
            self.assertNotIn("报错原文命中", r[0].reasons, "通配段未限长，可能引发性能问题")


if __name__ == "__main__":
    unittest.main()
