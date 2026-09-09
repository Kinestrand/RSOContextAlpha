from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import install as installer


ROOT = Path(__file__).resolve().parents[1]


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "release source"
        self.source.mkdir()
        names = ["LICENSE", "NOTICE", "install.py", "rso-context", "rso-context.ps1"]
        names += [p.relative_to(ROOT).as_posix() for p in (ROOT / "src/rso_context").glob("*.py")]
        for name in names:
            target = self.source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        names.append("RELEASE-FILES.txt")
        self.manifest = self.source / "RELEASE-FILES.txt"
        self.manifest.write_text("\n".join(names) + "\n", encoding="utf-8")
        self.prefix = self.base / "install with spaces"

    def test_native_install_command_and_index_isolation(self):
        index = self.prefix / ".rso-context/context.sqlite3"
        index.parent.mkdir(parents=True)
        index.write_bytes(b"existing index must remain unchanged")
        unrelated = self.prefix / "user-notes.txt"
        unrelated.write_text("keep", encoding="utf-8")
        result = installer.install(self.source, self.prefix)
        command = result["command"]
        def run_command(arguments):
            args = [command, *arguments]
            if os.name == "nt":
                # cmd /s requires an outer quote pair around a command whose
                # executable and arguments both contain quoted paths.
                args = 'cmd /d /s /c "' + subprocess.list2cmdline(args) + '"'
            return subprocess.run(args, capture_output=True, text=True, timeout=30)

        completed = run_command(["--version"])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("0.7.", completed.stdout)
        project = self.base / "bounded project"
        project.mkdir()
        (project / "AGENTS.md").write_text("Decision: widgets must stay blue.\n", encoding="utf-8")
        common = ["--db", str(self.base / "smoke.sqlite3")]
        for operation in (["use"], ["query", "widgets"]):
            run = run_command(common + operation + ["--path", str(project), "--agent", "installer-test"])
            self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)["requirements"][0]["status"], "evidence_found")
        self.assertEqual(index.read_bytes(), b"existing index must remain unchanged")
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")
        self.assertFalse((Path(result["program"]) / ".rso-context").exists())

    def test_same_source_destination_and_repeated_install(self):
        first = installer.install(self.source, self.prefix)
        second = installer.install(Path(first["program"]), self.prefix)
        self.assertEqual(first["command"], second["command"])
        self.assertEqual(second["backups"], [])

    @unittest.skipUnless(os.name == "nt", "Windows execution policy regression")
    def test_windows_command_runs_under_restricted_policy(self):
        result = installer.install(self.source, self.prefix)
        command = result["command"].replace("'", "''")
        script = "& '" + command + "' --version; exit $LASTEXITCODE"
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Restricted", "-Command", script],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("0.7.", completed.stdout)
        pointer = Path(result["command"]).read_text(encoding="utf-8")
        self.assertNotIn("powershell", pointer.casefold())

    def test_update_backs_up_only_changed_program_files(self):
        first = installer.install(self.source, self.prefix)
        target = Path(first["program"]) / "install.py"
        target.write_text("previous installation", encoding="utf-8")
        result = installer.install(self.source, self.prefix)
        self.assertEqual(len(result["backups"]), 1)
        self.assertEqual(Path(result["backups"][0]).read_text(encoding="utf-8"), "previous installation")
        self.assertEqual(target.read_bytes(), (self.source / "install.py").read_bytes())

    def test_bad_manifest_rejected_before_destination_is_created(self):
        original = self.manifest.read_text(encoding="utf-8")
        for name in ("../outside.txt", "/absolute.txt", "C:/outside.txt", "src/*.py",
                     "missing.py", "private/key.txt", "context.sqlite3", ".env"):
            with self.subTest(name=name):
                self.manifest.write_text(original + name + "\n", encoding="utf-8")
                with self.assertRaises(ValueError):
                    installer.install(self.source, self.prefix)
                self.assertFalse(self.prefix.exists())

    def test_missing_required_file_is_rejected(self):
        self.manifest.write_text("install.py\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Required release"):
            installer.install(self.source, self.prefix)

    def test_git_is_required(self):
        with patch("install.subprocess.run", side_effect=FileNotFoundError("git")):
            with self.assertRaisesRegex(ValueError, "Git must"):
                installer.install(self.source, self.prefix)
        self.assertFalse(self.prefix.exists())

    def test_posix_layout_and_quoted_launcher(self):
        prefix = self.base / "friend's tools"
        result = installer.install(self.source, prefix, platform="darwin")
        self.assertEqual(Path(result["program"]), prefix / "share/RSOContextAlpha")
        pointer = Path(result["command"]).read_text(encoding="utf-8")
        self.assertIn('"$@"', pointer)
        self.assertNotIn("eval", pointer)
        self.assertIn("'\"'\"'", pointer)
        if os.name != "nt":
            completed = subprocess.run([result["command"], "--version"], capture_output=True, timeout=30)
            self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_cli_uses_explicit_prefix(self):
        import sys
        completed = subprocess.run(
            [sys.executable, str(self.source / "install.py"), "--prefix", str(self.prefix)],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(Path(json.loads(completed.stdout)["command"]).is_file())


if __name__ == "__main__":
    unittest.main()
