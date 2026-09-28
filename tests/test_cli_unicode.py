from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rso_context.cli import main


ROOT = Path(__file__).resolve().parents[1]


class UnicodeCliTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "requires Windows PowerShell")
    def test_powershell_pipeline_capture_preserves_all_unicode(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "unicode project"
            workspace.mkdir()
            expected = "Decision: caf\u00e9 \u6771\u4eac \U0001f600 \u0645\u0631\u062d\u0628\u0627 must stay intact."
            source = workspace / "AGENTS.md"
            source.write_text(expected, encoding="utf-8")
            script = Path(temp) / "capture.ps1"
            script.write_text('''param($Launcher, $Database, $Workspace, $Source)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::GetEncoding(1252)
$OutputEncoding = [System.Text.Encoding]::GetEncoding(1252)
$out = & $Launcher --db $Database use --agent unicode-pipeline --path $Workspace
if ($LASTEXITCODE -ne 0) { throw 'Launcher failed' }
$packet = ($out -join "`n") | ConvertFrom-Json
$expected = [System.IO.File]::ReadAllText($Source, [System.Text.Encoding]::UTF8)
$claims = @($packet.resume.claims | ForEach-Object { $_.display_text })
if ($claims -cnotcontains $expected) { throw 'Unicode changed during PowerShell pipeline capture' }
Write-Output 'pipeline Unicode preserved'
''', encoding="ascii")
            environment = dict(os.environ, PYTHONIOENCODING="cp1252", PYTHONUTF8="0", PYTHONDONTWRITEBYTECODE="1")
            result = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                 str(ROOT / "rso-context.ps1"), str(Path(temp) / "pipeline.sqlite3"), str(workspace), str(source)],
                env=environment, capture_output=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode("ascii", errors="replace"))
            self.assertIn(b"pipeline Unicode preserved", result.stdout)

    def test_module_and_windows_launcher_emit_utf8_under_legacy_encoding(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "unicode project"
            workspace.mkdir()
            expected = "Decision: caf\u00e9 \u2192 \u6771\u4eac must stay intact."
            (workspace / "AGENTS.md").write_text(expected, encoding="utf-8")
            environment = dict(os.environ, PYTHONIOENCODING="cp1252", PYTHONUTF8="0", PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT / "src"))
            commands = [[sys.executable, "-B", "-m", "rso_context"]]
            if os.name == "nt":
                commands.append(["powershell", "-NoProfile", "-File", str(ROOT / "rso-context.ps1")])
            for index, command in enumerate(commands):
                with self.subTest(command=command[0]):
                    result = subprocess.run(command + ["--db", str(Path(temp) / f"test-{index}.sqlite3"), "use", "--agent", "unicode-test", "--path", str(workspace)], env=environment, capture_output=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
                    packet = json.loads(result.stdout.decode("utf-8-sig"))
                    self.assertIn(expected, [claim["display_text"] for claim in packet["resume"]["claims"]])

    def test_doctor_is_not_ready_without_git(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()) as output:
            with patch("rso_context.cli.shutil.which", return_value=None), patch(
                "rso_context.config.git_executable", return_value="git"
            ):
                code = main(["--db", str(Path(temp) / "doctor.sqlite3"), "doctor"])
            self.assertEqual(code, 1)
            self.assertFalse(json.loads(output.getvalue())["ready"])


if __name__ == "__main__":
    unittest.main()
