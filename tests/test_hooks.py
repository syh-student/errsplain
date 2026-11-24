"""自动翻译 hook 的测试：安装幂等、卸载干净、CLI 分派、静默模式。"""

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from errsplain import hooks
from errsplain.cli import main


class TestHookInstall(unittest.TestCase):
    def test_install_then_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / ".bashrc"
            target.write_text("# my original config\nexport FOO=1\n", encoding="utf-8")

            hooks.install("bash", target=target)
            first = target.read_text(encoding="utf-8")
            self.assertIn(hooks.BEGIN, first)
            self.assertIn("export FOO=1", first, "原配置必须保留")

            hooks.install("bash", target=target)  # 再装一次
            second = target.read_text(encoding="utf-8")
            self.assertEqual(second.count(hooks.BEGIN), 1, "重复安装不应产生多个块")

    def test_uninstall_leaves_original(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / ".bashrc"
            target.write_text("# my original config\nexport FOO=1\n", encoding="utf-8")
            hooks.install("bash", target=target)
            hooks.uninstall("bash", target=target)
            after = target.read_text(encoding="utf-8")
            self.assertNotIn(hooks.BEGIN, after)
            self.assertIn("export FOO=1", after)

    def test_script_has_interactive_guard(self):
        """bash/zsh 脚本必须只在交互式 shell 生效，绝不污染 CI/脚本。"""
        self.assertIn("case $- in", hooks.SCRIPTS["bash"])
        self.assertIn("*i*", hooks.SCRIPTS["bash"])
        self.assertIn("interactive", hooks.SCRIPTS["zsh"])

    def test_all_shells_supported(self):
        for shell in ("bash", "zsh"):
            self.assertIn(hooks.BEGIN, hooks.SCRIPTS[shell])
            self.assertIn("errsplain --hook", hooks.SCRIPTS[shell])
            self.assertIn("_ERRSPLAIN_LAST", hooks.SCRIPTS[shell])
        # PowerShell 不改 prompt，只保留 cuowu 一个函数
        script = hooks.SCRIPTS["powershell"]
        self.assertIn(hooks.BEGIN, script)
        self.assertIn("errsplain cuowu", script)
        self.assertNotIn("function global:prompt", script)

    def test_cuowu_available_in_all_shells(self):
        """失败命令的报错必须能被 cuowu 打开为交互导航。"""
        for shell in ("bash", "zsh"):
            script = hooks.SCRIPTS[shell]
            with self.subTest(shell=shell):
                self.assertIn("cuowu", script)
                self.assertIn("上一条命令没有可用的报错", script)
                self.assertIn("--interactive", script)
                self.assertIn("--file", script)
        script = hooks.SCRIPTS["powershell"]
        self.assertIn("cuowu", script)
        self.assertIn("errsplain cuowu", script)

    def test_cuowu_does_not_override_existing_command(self):
        self.assertIn("command -v cuowu", hooks.SCRIPTS["bash"])
        self.assertIn("command -v cuowu", hooks.SCRIPTS["zsh"])
        self.assertIn("Get-Command cuowu", hooks.SCRIPTS["powershell"])

class TestHookCli(unittest.TestCase):
    def test_hook_subcommand_status(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["hook", "status", "bash"])
        self.assertEqual(rc, 0)
        self.assertIn("[bash]", buf.getvalue())

    def test_hook_subcommand_show(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["hook", "show", "bash"])
        self.assertEqual(rc, 0)
        self.assertIn("PROMPT_COMMAND", buf.getvalue())

    def test_hook_mode_compact_and_silent(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["--hook", "fatal: refusing to merge unrelated histories"])
        self.assertEqual(rc, 0)
        self.assertIn("errsplain →", buf.getvalue())
        self.assertIn("试试：", buf.getvalue())

        # 没匹配到必须完全静默
        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            rc2 = main(["--hook", "asdfqwer random nonsense xyz xyz"])
        self.assertEqual(rc2, 0)
        self.assertEqual(buf2.getvalue(), "", "未命中时不应有任何输出")


if __name__ == "__main__":
    unittest.main()
