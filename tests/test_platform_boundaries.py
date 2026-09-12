import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from rso_context.identity import source_path_filter
from rso_context.mcp_contract import bind_roots
from rso_context.mcp_runtime import install_runtime


class PlatformBoundaryTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "requires case-sensitive POSIX paths")
    def test_case_distinct_roots_and_sources_stay_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            upper = Path(tmp) / "Project"
            lower = Path(tmp) / "project"
            upper.mkdir()
            lower.mkdir(exist_ok=True)
            if upper.samefile(lower):
                self.skipTest("filesystem is case-insensitive")
            self.assertEqual(bind_roots([str(upper), str(lower)]), [upper, lower])
            with sqlite3.connect(":memory:") as db:
                db.execute("CREATE TABLE sources (path TEXT)")
                paths = [str(upper / "AGENTS.md"), str(lower / "AGENTS.md"), str(upper) + "-other/AGENTS.md"]
                db.executemany("INSERT INTO sources VALUES (?)", [(p,) for p in paths])
                clause, args = source_path_filter("path", upper)
                self.assertEqual(db.execute("SELECT path FROM sources WHERE " + clause, args).fetchall(), [(paths[0],)])

    def test_foreign_runtime_is_not_modified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "pyvenv.cfg"
            content = "home = " + ("/usr/bin" if os.name == "nt" else "C:\\Python311") + "\n"
            config.write_text(content, encoding="utf-8")
            with patch("rso_context.mcp_runtime.venv.EnvBuilder") as builder:
                with self.assertRaisesRegex(RuntimeError, "another operating system"):
                    install_runtime(root)
                builder.assert_not_called()
            self.assertEqual(config.read_text(encoding="utf-8"), content)
