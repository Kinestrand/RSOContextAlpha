from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rso_context.config import Limits
from rso_context.db import Database
from rso_context.ingest import ingest_project, iter_project_files
from rso_context.resume import resume_context


class GitSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        (self.root / "AGENTS.md").write_text("Decision: bounded evidence must stay local.", encoding="utf-8")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)

    def test_git_tracks_only_allowed_files(self):
        self.git("init", "-q")
        (self.root / ".gitignore").write_text("ignored.md\n", encoding="utf-8")
        for name in ("untracked.md", "ignored.md", "private/x.md", "graft/x.md", "outputs/x.md"):
            file = self.root / name
            file.parent.mkdir(exist_ok=True)
            file.write_text("Decision: excluded material must stay out.", encoding="utf-8")
        self.git("add", "AGENTS.md", ".gitignore", "private/x.md", "graft/x.md", "outputs/x.md")
        self.assertEqual([p.name for p in iter_project_files(self.root, Limits())], ["AGENTS.md"])

    def test_plain_directory_remains_supported(self):
        self.assertEqual([p.name for p in iter_project_files(self.root, Limits())], ["AGENTS.md"])

    def test_missing_git_and_timeout_fail_closed(self):
        for error in (FileNotFoundError("git"), subprocess.TimeoutExpired("git", 60), PermissionError("git")):
            with self.subTest(error=type(error).__name__):
                with patch("rso_context.ingest.subprocess.run", side_effect=error):
                    with self.assertRaisesRegex(RuntimeError, "ingestion stopped"):
                        iter_project_files(self.root, Limits())

    def test_git_error_does_not_become_plain_folder(self):
        result = subprocess.CompletedProcess([], 128, b"", b"fatal: detected dubious ownership")
        with patch("rso_context.ingest.subprocess.run", return_value=result):
            with self.assertRaises(RuntimeError):
                iter_project_files(self.root, Limits())

    def test_corrupt_metadata_does_not_become_plain_folder(self):
        (self.root / ".git").mkdir()
        with self.assertRaises(RuntimeError):
            iter_project_files(self.root, Limits())

    def test_failed_ingest_preserves_existing_sources(self):
        database = Database(Path(self.temp.name) / "test.sqlite3")
        ingest_project(database, self.root, agent="test")
        before = resume_context(database, path=self.root)
        with patch("rso_context.ingest._git_ls_files", side_effect=RuntimeError("Git unavailable")):
            with self.assertRaises(RuntimeError):
                ingest_project(database, self.root, agent="test")
        after = resume_context(database, path=self.root)
        self.assertEqual(before["takes"], after["takes"])
        self.assertEqual(before["claims"], after["claims"])
        self.assertEqual(before["project"]["corpus_version"], after["project"]["corpus_version"])


if __name__ == "__main__":
    unittest.main()
