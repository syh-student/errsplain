"""新增词条与关键排序的回归测试。"""

import unittest

from errsplain.database import load_entries
from errsplain.matcher import match


class TestNewEntries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries = load_entries()

    def top_id(self, query: str):
        results = match(query, self.entries, top_k=1)
        return results[0].entry.id if results else None

    def test_all_new_entries_present(self):
        ids = {e.id for e in self.entries}
        for entry_id in (
            "py-recursion-limit",
            "py-broken-pipe",
            "npm-lifecycle-failed",
            "mysql-syntax-error",
            "git-dubious-ownership",
            "pandas-parser-error",
            "docker-pull-denied",
        ):
            self.assertIn(entry_id, ids)

    def test_recursion_error(self):
        self.assertEqual(
            self.top_id("RecursionError: maximum recursion depth exceeded"),
            "py-recursion-limit",
        )

    def test_broken_pipe(self):
        self.assertEqual(
            self.top_id("BrokenPipeError: [Errno 32] Broken pipe"),
            "py-broken-pipe",
        )

    def test_npm_lifecycle(self):
        self.assertEqual(
            self.top_id("npm ERR! code ELIFECYCLE"),
            "npm-lifecycle-failed",
        )

    def test_mysql_syntax_error(self):
        self.assertEqual(
            self.top_id("ERROR 1064 (42000): You have an error in your SQL syntax"),
            "mysql-syntax-error",
        )

    def test_git_dubious_ownership(self):
        self.assertEqual(
            self.top_id("fatal: detected dubious ownership in repository"),
            "git-dubious-ownership",
        )

    def test_pandas_parser_error(self):
        self.assertEqual(
            self.top_id("pandas.errors.ParserError: Error tokenizing data"),
            "pandas-parser-error",
        )

    def test_docker_pull_denied(self):
        self.assertEqual(
            self.top_id("docker: pull access denied for myimage"),
            "docker-pull-denied",
        )

    def test_psql_prefers_postgresql_over_ssh(self):
        query = (
            "psql: error: connection to server at localhost, port 5432 failed: "
            "Connection refused"
        )
        self.assertEqual(self.top_id(query), "pg-connection-refused")


if __name__ == "__main__":
    unittest.main()
