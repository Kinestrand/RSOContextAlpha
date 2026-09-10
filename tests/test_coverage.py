from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rso_context.config import Limits
from rso_context.db import Database
from rso_context.ingest import ingest_project, iter_project_files
from rso_context.query import query_context
from rso_context.solvers import apply_solvers, packet_body_for_hash
from rso_context.db import json_text
import hashlib


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        (self.root / "AGENTS.md").write_text("Decision: widgets must stay blue.\n", encoding="utf-8")
        self.database = Database(Path(self.temp.name) / "context.sqlite3")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)

    def test_git_ignored_dir_is_a_recorded_gap_without_walking_it(self):
        self.git("init", "-q")
        nested = self.root / "graft" / "secret.md"
        nested.parent.mkdir()
        nested.write_text("Decision: hidden must stay out.\n", encoding="utf-8")
        (self.root / "notes.bin").write_bytes(b"\x00\x01\x02\x03")
        self.git("add", "AGENTS.md", "graft/secret.md", "notes.bin")
        result = ingest_project(self.database, self.root, agent="cov")
        coverage = result["coverage"]
        self.assertEqual(coverage["schema"], "rso-ingest-coverage/v1")
        self.assertEqual(coverage["enumeration"], "git_cached")
        self.assertTrue(coverage["complete"])
        reasons = {item["path"]: item["reason"] for item in coverage["gaps"]}
        self.assertEqual(reasons.get("graft/secret.md"), "ignored_dir")
        self.assertEqual(reasons.get("notes.bin"), "unsupported_type")
        self.assertNotIn("hidden must stay out", str(result))
        self.assertEqual([p.name for p in iter_project_files(self.root, Limits())], ["AGENTS.md"])

    def test_walk_does_not_list_files_inside_forbidden_directories(self):
        secret = self.root / "private" / "inner.md"
        secret.parent.mkdir()
        secret.write_text("Decision: inner secret must stay out.\n", encoding="utf-8")
        result = ingest_project(self.database, self.root, agent="cov")
        coverage = result["coverage"]
        self.assertEqual(coverage["enumeration"], "bounded_walk")
        paths = [item["path"] for item in coverage["gaps"]]
        self.assertTrue(any(path == "private" or path.startswith("private/") for path in paths))
        self.assertFalse(any(path.endswith("inner.md") for path in paths))

    def test_too_large_is_recorded(self):
        (self.root / "big.md").write_text("x" * 400, encoding="utf-8")
        result = ingest_project(
            self.database, self.root, agent="cov", limits=Limits(max_file_bytes=80)
        )
        coverage = result["coverage"]
        self.assertGreaterEqual(coverage["counts"]["too_large"], 1)
        self.assertTrue(any(item["reason"] == "too_large" for item in coverage["gaps"]))

    def test_file_limit_marks_enumeration_incomplete(self):
        (self.root / "other.md").write_text("Decision: other widgets must wait.\n", encoding="utf-8")
        result = ingest_project(
            self.database, self.root, agent="cov", limits=Limits(max_files_per_project=1)
        )
        self.assertFalse(result["coverage"]["complete"])
        self.assertEqual(result["coverage"]["counts"]["eligible"], 1)

    def test_unreadable_extract_is_a_gap(self):
        with patch("rso_context.ingest.extract_text", side_effect=OSError("locked")):
            result = ingest_project(self.database, self.root, agent="cov")
        coverage = result["coverage"]
        self.assertEqual(result["counters"]["files_skipped"], 1)
        self.assertTrue(any(item["reason"] == "unreadable" for item in coverage["gaps"]))

    def test_gap_list_truncates(self):
        self.git("init", "-q")
        names = []
        for index in range(3):
            name = f"skip{index}.bin"
            (self.root / name).write_bytes(b"xxxx")
            names.append(name)
        self.git("add", "AGENTS.md", *names)
        result = ingest_project(
            self.database, self.root, agent="cov", limits=Limits(coverage_gap_limit=1)
        )
        coverage = result["coverage"]
        self.assertTrue(coverage["truncated"])
        self.assertEqual(len(coverage["gaps"]), 1)
        self.assertGreaterEqual(coverage["counts"]["unsupported_type"], 3)

    def test_empty_query_is_labeled_not_proof_of_absence(self):
        ingest_project(self.database, self.root, agent="cov")
        packet = query_context(self.database, "zzzz-no-such-token", path=self.root, agent="a")
        self.assertEqual(packet["requirements"][0]["status"], "unknown")
        self.assertEqual(packet["empty_result"]["kind"], "no_matching_spans")
        self.assertIn("not a proof of absence", packet["empty_result"]["message"])
        expected = hashlib.sha256(json_text(packet_body_for_hash(packet)).encode("utf-8")).hexdigest()
        self.assertEqual(packet["packet_hash"], expected)
        apply_solvers(packet)
        self.assertEqual(packet["packet_hash"], expected)

    def test_matching_query_has_no_empty_result(self):
        ingest_project(self.database, self.root, agent="cov")
        packet = query_context(self.database, "widgets", path=self.root, agent="a")
        self.assertEqual(packet["requirements"][0]["status"], "evidence_found")
        self.assertNotIn("empty_result", packet)


if __name__ == "__main__":
    unittest.main()
