from __future__ import annotations

import time
import tempfile
import unittest
from pathlib import Path

from rso_context.compact import compact_packet, serialized_bytes
from rso_context.db import Database
from rso_context.ingest import ingest_project
from rso_context.query import query_context


class ReleaseFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        (self.root / "AGENTS.md").write_text("# Fixture\n", encoding="utf-8")
        self.database = Database(Path(self.temp.name) / "context.sqlite3")

    def test_late_exception_match_and_duplicate_spans(self):
        padding = "\n".join(f"unrelated filler line {i}" for i in range(40))
        (self.root / "policy.md").write_text(
            padding + "\nDecision: widgets must stay blue except on Tuesday.\n",
            encoding="utf-8",
        )
        (self.root / "copy.md").write_text(
            "Decision: widgets must stay blue except on Tuesday.\n",
            encoding="utf-8",
        )
        ingest_project(self.database, self.root, agent="gate")
        started = time.perf_counter()
        packet = query_context(self.database, "widgets", path=self.root, agent="a")
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.assertEqual(packet["schema"], "rso-context-packet/v2")
        self.assertGreaterEqual(len(packet["evidence"]), 2)
        paths = {item["relative_path"] for item in packet["evidence"] if not item.get("stale")}
        self.assertTrue({"policy.md", "copy.md"} <= paths)
        late = next(item for item in packet["evidence"] if item["relative_path"] == "policy.md")
        lines = (self.root / "policy.md").read_text(encoding="utf-8").splitlines()
        decision_line = next(i for i, line in enumerate(lines, start=1) if "except on Tuesday" in line)
        self.assertGreaterEqual(decision_line, 40)
        self.assertLessEqual(int(late["line_start"]), decision_line)
        self.assertGreaterEqual(int(late["line_end"]), decision_line)
        self.assertIn("except on Tuesday", late["text"])
        compact = compact_packet(packet, byte_budget=12_000)
        self.assertLessEqual(compact["byte_count"], compact["byte_budget"])
        self.assertEqual(compact["byte_count"], serialized_bytes(compact))
        self.assertEqual(compact["source_packet_hash"], packet["packet_hash"])
        self.assertTrue(elapsed_ms < 10_000)
        verified = [item for item in packet.get("checks") or [] if item.get("name") == "schema_holds"]
        self.assertTrue(verified)
        self.assertNotEqual(verified[0].get("result"), "verified")

    def test_source_change_after_query_does_not_quote_new_text(self):
        (self.root / "policy.md").write_text("Decision: widgets must stay blue.\n", encoding="utf-8")
        ingest_project(self.database, self.root, agent="gate")
        packet = query_context(self.database, "widgets", path=self.root, agent="a")
        (self.root / "policy.md").write_text("Decision: widgets must stay pink.\n", encoding="utf-8")
        stale = query_context(self.database, "widgets", path=self.root, agent="b", use_cache=False)
        self.assertTrue(all(item.get("stale") for item in stale["evidence"]))
        self.assertNotIn("pink", " ".join(item.get("text") or "" for item in stale["evidence"]))
        self.assertEqual(stale["empty_result"]["kind"], "only_stale_spans")
