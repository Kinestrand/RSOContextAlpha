from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rso_context.audit import record_validation
from rso_context.db import Database
from rso_context.ingest import ingest_project
from rso_context.query import query_context
from rso_context.solvers import apply_solvers


class QueryFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.workspace = root / "project"
        self.workspace.mkdir()
        (self.workspace / "AGENTS.md").write_text("# Fixture project\n", encoding="utf-8")
        self.policy = self.workspace / "policy.md"
        self.original = "Decision: widgets must be blue.\n"
        self.policy.write_text(self.original, encoding="utf-8")
        self.database = Database(root / "context.sqlite3")
        self.ingest()

    def ingest(self):
        return ingest_project(self.database, self.workspace, agent="freshness-test")

    def query(self, agent="a", **kwargs):
        return query_context(self.database, "widgets", path=self.workspace, agent=agent, **kwargs)

    def assert_unknown(self, packet):
        self.assertEqual(packet["requirements"][0]["status"], "unknown")
        self.assertEqual(packet["partition"]["leaves"][0]["evidence_count"], 0)
        self.assertTrue(packet["evidence"])
        self.assertTrue(all(item["stale"] for item in packet["evidence"]))
        self.assertEqual(next(c for c in packet["checks"] if c["name"] == "span_exists")["result"], "fail")
        self.assertFalse(packet["claims"])
        self.assertFalse(packet["graph_nodes"])

    def test_cache_reused_across_agents_at_equal_budget(self):
        first = self.query()
        with patch("rso_context.query._retrieve_requirement", side_effect=AssertionError("cache missed")):
            second = self.query("b")
        self.assertEqual(first, second)

    def test_edit_invalidates_cross_agent_cache_even_with_same_size_and_mtime(self):
        self.query()
        stamp = self.policy.stat()
        self.policy.write_text(self.original.replace("blue", "pink"), encoding="utf-8")
        os.utime(self.policy, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.assert_unknown(self.query("b"))

    def test_missing_source_invalidates_cache(self):
        self.query()
        self.policy.unlink()
        self.assert_unknown(self.query("b"))

    def test_uncached_stale_only_query_is_unknown_then_recovers_after_ingest(self):
        self.policy.write_text("Decision: widgets must be pink.\n", encoding="utf-8")
        self.assert_unknown(self.query(use_cache=False))
        self.ingest()
        current = self.query("b")
        self.assertEqual(current["requirements"][0]["status"], "evidence_found")
        self.assertIn("pink", current["evidence"][0]["text"])
        self.assertFalse(current["evidence"][0]["stale"])

    def test_restoring_source_recovers_without_ingest(self):
        self.query()
        self.policy.unlink()
        self.assert_unknown(self.query("b"))
        self.policy.write_text(self.original, encoding="utf-8")
        restored = self.query("c")
        self.assertEqual(restored["requirements"][0]["status"], "evidence_found")

    def test_stale_verified_boost_is_removed_even_outside_query_hits(self):
        extra = self.workspace / "extra.md"
        extra.write_text("Decision: unrelated components must be amber.\n", encoding="utf-8")
        self.ingest()
        evidence = query_context(self.database, "unrelated", path=self.workspace, agent="fixture")
        claim = next(c for c in evidence["claims"] if "unrelated" in c["display_text"])
        record_validation(self.database, claim["id"], validator="fixture-validator", result="verified")
        first = self.query()
        self.assertIn(claim["id"], [c["id"] for c in first["claims"]])
        extra.unlink()
        second = self.query("b")
        self.assertNotIn(claim["id"], [c["id"] for c in second["claims"]])
        self.assertFalse(second["validations"])
        self.assertEqual(second["requirements"][0]["status"], "evidence_found")

    def test_stale_conflicting_plate_does_not_create_disagreement(self):
        extra = self.workspace / "extra.md"
        extra.write_text("Decision: widgets must not be blue.\n", encoding="utf-8")
        self.ingest()
        self.assertTrue(self.query()["clarification_needed"])
        extra.unlink()
        current = self.query("b")
        self.assertFalse(current["clarification_needed"])
        self.assertEqual(current["requirements"][0]["status"], "evidence_found")
        self.assertEqual(current["partition"]["leaves"][0]["evidence_count"], 1)

    def test_graph_backing_is_checked_when_no_text_hits_are_requested(self):
        first = self.query(limit=0)
        self.assertFalse(first["evidence"])
        self.assertTrue(first["graph_nodes"])
        self.policy.unlink()
        second = self.query("b", limit=0)
        self.assertFalse(second["graph_nodes"])
        self.assertEqual(second["requirements"][0]["status"], "unknown")

    def test_equal_remaining_budget_does_not_reuse_different_initial_budget(self):
        self.query(run_budget=20)
        large_initial = self.query(run_budget=8)
        normal = self.query("b")
        self.assertEqual(large_initial["run"], {"initial": 20, "remaining": 7})
        self.assertEqual(normal["run"], {"initial": 8, "remaining": 7})

    def test_solver_does_not_accept_status_without_current_span(self):
        for evidence in ([], [{"stale": True, "text": "legacy body"}]):
            packet = {"schema": "rso-context-packet/v2", "evidence": evidence,
                      "requirements": [{"status": "evidence_found"}]}
            checks = apply_solvers(packet)
            self.assertEqual(next(c for c in checks if c["name"] == "span_exists")["result"], "fail")


if __name__ == "__main__":
    unittest.main()
