from __future__ import annotations

import re
import tempfile
import unittest
import zipfile
from pathlib import Path

import build_release
from install import release_files


ROOT = Path(__file__).resolve().parents[1]


class ReleaseTests(unittest.TestCase):
    def test_package_contains_complete_runtime_and_no_local_material(self):
        files = {p.as_posix() for p in release_files(ROOT)}
        modules = {p.relative_to(ROOT).as_posix() for p in (ROOT / "src/rso_context").glob("*.py")}
        self.assertTrue(modules <= files)
        self.assertTrue({"LICENSE", "NOTICE", "README.md", "QUICKSTART.md", "MANUAL.md", "INSTALL.md", "SKILL.md",
                         "references/protocol.md", "project-truth.html"} <= files)
        self.assertFalse(files & {"AGENTS.md", "ROADMAP.md", "LINKEDIN.md",
                                  "REPAIR-COORDINATION.md", ".mcp.json"})
        for name in files:
            self.assertNotIn(".bak", name)
            self.assertNotIn("coordination", name.casefold())

    def test_shipped_markdown_links_resolve_within_release(self):
        files = {p.as_posix() for p in release_files(ROOT)}
        for name in sorted(files):
            if not name.endswith(".md"):
                continue
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotRegex(text, r"[A-Z]:\\Users\\[^%<\s]+")
            self.assertNotRegex(text, r"[A-Z]:\\AI_Pipeline_Tool")
            for target in re.findall(r"\]\(([^)]+)\)", text):
                if "://" in target or target.startswith("#"):
                    continue
                relative = (Path(name).parent / target.split("#")[0]).as_posix()
                self.assertIn(relative, files, (name, target))

    def test_protocol_copies_are_identical(self):
        self.assertEqual((ROOT / "protocol.md").read_bytes(),
                         (ROOT / "references/protocol.md").read_bytes())

    def test_archive_exact_allowlist_and_reproducible_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first.zip"
            second = Path(tmp) / "second.zip"
            result = build_release.build(ROOT, first)
            other = build_release.build(ROOT, second)
            self.assertEqual(result["sha256"], other["sha256"])
            with zipfile.ZipFile(first) as archive:
                expected = {"RSOContextAlpha-0.9.1/" + p.as_posix() for p in release_files(ROOT)}
                self.assertEqual(set(archive.namelist()), expected)
                self.assertIsNone(archive.testzip())
                for p in release_files(ROOT):
                    self.assertEqual(archive.read("RSOContextAlpha-0.9.1/" + p.as_posix()),
                                     (ROOT / p).read_bytes())
            with self.assertRaises(FileExistsError):
                build_release.build(ROOT, first)


if __name__ == "__main__":
    unittest.main()
