"""诊断引擎测试：文本抽取准确性 + 检查函数不崩 + CLI 分派。"""

import io
import json
import socket
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from errsplain import diagnose as dg
from errsplain.cli import main


class TestExtraction(unittest.TestCase):
    def test_extract_module(self):
        cases = [
            ("ModuleNotFoundError: No module named 'flask'", "flask"),
            ('No module named "numpy"', "numpy"),
            ("Cannot find module 'lodash'", "lodash"),
            ("No module named 'a.b.c'", "a.b.c"),
        ]
        for text, expect in cases:
            with self.subTest(text=text[:40]):
                self.assertEqual(dg.extract_module(text), expect)

    def test_extract_port(self):
        cases = [
            ("listen EADDRINUSE :::3000", 3000),
            ("listen EADDRINUSE ::: 51548", 51548),
            ("Failed to connect to localhost port 8080", 8080),
            ("127.0.0.1:5432 refused", 5432),
            ("时间是 12:30 开会", None),  # 不能把时间当端口
            ("10:00-11:00", None),
        ]
        for text, expect in cases:
            with self.subTest(text=text[:34]):
                self.assertEqual(dg.extract_port(text), expect)

    def test_extract_path(self):
        self.assertEqual(dg.extract_path("Permission denied: /var/log/app.log"), "/var/log/app.log")
        self.assertEqual(
            dg.extract_path("open C:\\Windows\\hosts failed"), "C:\\Windows\\hosts"
        )
        self.assertEqual(
            dg.extract_path("No such file or directory: 'config.yaml'"), "config.yaml"
        )
        # URL 不应被当路径
        self.assertIsNone(dg.extract_path("GET http://example.com/a/b failed"))

    def test_extract_command(self):
        self.assertEqual(dg.extract_command("bash: foo: command not found"), "foo")
        self.assertEqual(dg.extract_command("'npm' 不是内部或外部命令"), "npm")

    def test_extract_host(self):
        self.assertEqual(
            dg.extract_host("curl: (6) Could not resolve host: example.com"), "example.com"
        )

    def test_extract_env_vars(self):
        cases = [
            ("KeyError: 'DATABASE_URL'", ["DATABASE_URL"]),
            ("process.env.STRIPE_SECRET_KEY is undefined", ["STRIPE_SECRET_KEY"]),
            ("$JAVA_HOME: unbound variable", ["JAVA_HOME"]),
            ("hello world", []),
        ]
        for text, expect in cases:
            with self.subTest(text=text[:38]):
                self.assertEqual(dg.extract_env_vars(text), expect)

    def test_extract_tool(self):
        self.assertEqual(dg.extract_tool("bash: docker: command not found"), "docker")
        self.assertEqual(dg.extract_tool("kubectl get pods error"), "kubectl")
        self.assertIsNone(dg.extract_tool("just a normal sentence"))


class TestSecretsNeverLeak(unittest.TestCase):
    """环境变量的值绝不能出现在输出里——这是安全底线。"""

    def test_env_value_masked(self):
        import os

        os.environ["ERRSPLAIN_TEST_SECRET"] = "super-secret-value-12345"
        try:
            f = dg.check_env_var("ERRSPLAIN_TEST_SECRET")
            self.assertTrue(f.ok)
            self.assertNotIn("super-secret-value-12345", f.value)
            self.assertIn("***", f.value)
        finally:
            del os.environ["ERRSPLAIN_TEST_SECRET"]

    def test_proxy_credentials_masked(self):
        import os

        os.environ["HTTPS_PROXY"] = "http://user:password@proxy.corp:8080"
        try:
            f = dg.check_proxy_env()
            self.assertNotIn("password", f.value)
            self.assertIn("***@", f.value)
        finally:
            os.environ.pop("HTTPS_PROXY", None)

    def test_short_secret_still_masked(self):
        import os

        os.environ["ERRSPLAIN_TINY"] = "ab"
        try:
            f = dg.check_env_var("ERRSPLAIN_TINY")
            self.assertNotIn("ab", f.value.replace("***", ""))
        finally:
            del os.environ["ERRSPLAIN_TINY"]


class TestChecksDoNotCrash(unittest.TestCase):
    """任何检查都不该抛异常——诊断是增强功能，不能拖垮主流程。"""

    def test_module_check(self):
        f = dg.check_module("json")  # 内置模块，必然存在
        self.assertTrue(f.ok)
        f2 = dg.check_module("definitely_not_a_real_module_xyz")
        self.assertFalse(f2.ok)

    def test_command_check(self):
        f = dg.check_command("definitely_not_a_real_command_xyz")
        self.assertFalse(f.ok)

    def test_path_check_missing(self):
        f = dg.check_path("/definitely/not/a/real/path/xyz")
        self.assertFalse(f.ok)
        self.assertIn("不存在", f.value)

    def test_disk_and_memory(self):
        self.assertTrue(dg.check_disk().value)
        self.assertTrue(dg.check_memory().value)

    def test_port_free(self):
        # 找一个确定空闲的端口
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
        s.close()
        f = dg.check_port(free)
        self.assertIsNotNone(f.value)

    def test_diagnose_empty_returns_empty(self):
        self.assertEqual(dg.diagnose("hello world"), [])

    def test_dns_success(self):
        infos = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 0))]
        with patch.object(dg.socket, "getaddrinfo", return_value=infos):
            finding = dg.check_dns("example.test")
        self.assertTrue(finding.ok)
        self.assertIn("192.0.2.1", finding.value)

    def test_dns_timeout_returns_promptly(self):
        release = threading.Event()

        def slow_resolve(host, port):
            release.wait(5)
            return []

        try:
            with patch.object(dg.socket, "getaddrinfo", side_effect=slow_resolve), \
                    patch.object(dg, "_TIMEOUT", 0.02):
                started = time.perf_counter()
                finding = dg.check_dns("slow.example")
                elapsed = time.perf_counter() - started
        finally:
            release.set()
        self.assertLess(elapsed, 1.0)
        self.assertIn("超时", finding.value)

    def test_diagnose_never_raises(self):
        for text in ["", "??", "a" * 5000, "No module named", ":99999", "C:\\"]:
            with self.subTest(text=text[:20]):
                dg.diagnose(text)  # 不抛异常即通过

    def test_tool_check_missing(self):
        f = dg.check_tool("definitely_not_a_tool_xyz")
        self.assertFalse(f.ok)

    def test_tool_check_present(self):
        # node 在本机存在（由环境确认过）；不存在也不应崩
        f = dg.check_tool("node")
        self.assertIsNotNone(f.value)

    def test_pip_package_present(self):
        f = dg.check_pip_package("pip")
        self.assertTrue(f.ok)

    def test_pip_package_missing(self):
        f = dg.check_pip_package("definitely_not_installed_pkg_xyz")
        self.assertFalse(f.ok)

    def test_proxy_env_no_crash(self):
        f = dg.check_proxy_env()
        self.assertIsNotNone(f.value)

    def test_docker_graceful_when_missing(self):
        """没装 docker 时应明确报告，而不是抛异常。"""
        findings = dg.check_docker()
        self.assertTrue(findings)
        if not dg.shutil.which("docker"):
            self.assertFalse(findings[0].ok)

    def test_inode_check(self):
        f = dg.check_inode()
        self.assertIsNotNone(f.value)


class TestDiagnoseCli(unittest.TestCase):
    def test_diagnose_module(self):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            rc = main(["diagnose", "ModuleNotFoundError: No module named 'definitely_not_real_xyz'"])
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("当前解释器", out)

    def test_diagnose_no_checkable(self):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            rc = main(["diagnose", "hello world"])
        self.assertEqual(rc, 1)

    def test_diagnose_file_strips_utf8_bom(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "error.log"
            path.write_bytes(
                b"\xef\xbb\xbfModuleNotFoundError: No module named 'demo'"
            )
            buf = io.StringIO()
            with patch("errsplain.diagcmd.diagnose", return_value=[]) as mocked:
                with redirect_stdout(buf), redirect_stderr(buf):
                    rc = main(["diagnose", "--file", str(path)])
            self.assertEqual(rc, 1)
            text = mocked.call_args.args[0]
            self.assertTrue(text.startswith("ModuleNotFoundError"))

    def test_diagnose_help(self):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf), self.assertRaises(SystemExit) as cm:
            main(["diagnose", "--help"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("只读", buf.getvalue())

    def test_diagnose_flag_with_query(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["ModuleNotFoundError: No module named 'flask'", "--diagnose", "--no-color"])
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("现场诊断", out)

    def test_json_remains_list_without_diagnose(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["--json", "ModuleNotFoundError: No module named 'flask'"])
        self.assertEqual(rc, 0)
        self.assertIsInstance(json.loads(buf.getvalue()), list)

    def test_json_includes_structured_diagnosis(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main([
                "--json",
                "--diagnose",
                "ModuleNotFoundError: No module named 'definitely_not_real_xyz'",
            ])
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertIsInstance(payload["matches"], list)
        self.assertTrue(payload["matches"])
        self.assertTrue(
            any(
                finding["title"] == "模块 definitely_not_real_xyz"
                for finding in payload["diagnosis"]
            )
        )


if __name__ == "__main__":
    unittest.main()
