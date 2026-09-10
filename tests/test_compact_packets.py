from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rso_context.compact import (
    COMPACT_SCHEMA,
    compact_packet,
    expand_reference,
    excerpts_for_hit,
    matching_line_groups,
    serialized_bytes,
)
from rso_context.db import Database
from rso_context.ingest import ingest_project
from rso_context.query import query_context


class ExcerptTests(unittest.TestCase):
    def test_matching_groups_are_not_spliced_across_gaps(self):
        text = "alpha widgets here\nignore this middle\nbeta widgets there"
        groups = matching_line_groups(text, 10, "widgets")
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0][0], 10)
        self.assertEqual(groups[1][0], 12)
        self.assertNotIn("middle", groups[0][2] + groups[1][2])

    def test_policy_chunk_stays_whole(self):
        hit = {
            "project_id": "p",
            "relative_path": "policy.md",
            "line_start": 1,
            "line_end": 2,
            "text": "Decision: widgets must be blue except on Tuesday.\nMore notes about widgets.",
            "chunk_hash": "c",
            "source_hash": "h",
            "stale": False,
            "span_kind": "source_lines",
            "media_type": "text/markdown",
            "requirements": [0],
        }
        excerpts = excerpts_for_hit(hit, "widgets")
        self.assertEqual(len(excerpts), 1)
        self.assertIn("except on Tuesday", excerpts[0]["text"])
        self.assertEqual(excerpts[0]["line_start"], 1)
        self.assertEqual(excerpts[0]["line_end"], 2)


class CompactPacketTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        (self.root / "AGENTS.md").write_text("# Fixture\n", encoding="utf-8")
        self.database = Database(Path(self.temp.name) / "context.sqlite3")

    def _ingest(self, relative: str, body: str):
        (self.root / relative).write_text(body, encoding="utf-8")
        ingest_project(self.database, self.root, agent="compact-test")

    def test_cli_full_packet_schema_is_unchanged(self):
        self._ingest("policy.md", "Decision: widgets must stay blue.\n")
        packet = query_context(self.database, "widgets", path=self.root, agent="a")
        self.assertEqual(packet["schema"], "rso-context-packet/v2")
        compact = compact_packet(packet)
        self.assertEqual(compact["schema"], COMPACT_SCHEMA)
        self.assertEqual(compact["source_packet_hash"], packet["packet_hash"])
        self.assertEqual(compact["source_schema"], "rso-context-packet/v2")
        again = query_context(self.database, "widgets", path=self.root, agent="b")
        self.assertEqual(again["packet_hash"], packet["packet_hash"])

    def test_conflict_is_kept_or_insufficient_budget(self):
        self._ingest("must.md", "Decision: widgets must be blue.\n")
        self._ingest("not.md", "Do not paint widgets blue.\n")
        packet = query_context(self.database, "widgets", path=self.root, agent="a")
        self.assertEqual(packet["requirements"][0]["status"], "disagreement")
        compact = compact_packet(packet, byte_budget=20_000)
        self.assertEqual(compact["clarification_needed"], True)
        texts = " ".join(item.get("text") or "" for item in compact["evidence"])
        if compact["status"] == "ok":
            self.assertIn("must", texts.casefold())
            self.assertTrue("do not" in texts.casefold() or "don't" in texts.casefold())
        else:
            self.assertEqual(compact["status"], "insufficient_budget")
        tiny = compact_packet(packet, byte_budget=400)
        self.assertEqual(tiny["status"], "insufficient_budget")
        self.assertFalse(tiny.get("evidence"))
        self.assertLessEqual(tiny["byte_count"], tiny["byte_budget"])
        squeezed = compact_packet(packet, byte_budget=256)
        self.assertLessEqual(squeezed["byte_count"], squeezed["byte_budget"])
        self.assertEqual(squeezed["byte_count"], serialized_bytes(squeezed))

    def test_byte_budget_is_respected_and_omits_with_expand_refs(self):
        body = "Decision: widgets must stay blue.\n" + ("widgets extra line\n" * 80)
        self._ingest("policy.md", body)
        packet = query_context(self.database, "widgets", path=self.root, agent="a")
        full = compact_packet(packet, byte_budget=50_000)
        self.assertGreater(full["byte_count"], 1200)
        compact = compact_packet(packet, byte_budget=1200)
        self.assertLessEqual(compact["byte_count"], compact["byte_budget"])
        self.assertEqual(compact["byte_count"], serialized_bytes(compact))
        self.assertTrue(compact["omitted_count"] or compact["status"] == "insufficient_budget")
        if compact["omitted"]:
            self.assertTrue(all(item.get("expand", {}).get("schema") == "rso-expand-ref/v1" for item in compact["omitted"]))

    def test_expand_recovers_range_and_source_change_is_stale(self):
        self._ingest(
            "policy.md",
            "Decision: widgets must stay blue except on Tuesday.\nWidgets also appear later.\n",
        )
        packet = query_context(self.database, "widgets", path=self.root, agent="a")
        compact = compact_packet(packet, byte_budget=20_000)
        self.assertTrue(compact["evidence"])
        ref = compact["evidence"][0]["expand"]
        recovered = expand_reference(self.database, ref, roots=[self.root])
        self.assertEqual(recovered["status"], "ok")
        self.assertIn("except on Tuesday", recovered["text"])
        (self.root / "policy.md").write_text("Decision: widgets must stay pink.\n", encoding="utf-8")
        stale = expand_reference(self.database, ref, roots=[self.root])
        self.assertEqual(stale["status"], "stale")
        self.assertNotIn("pink", stale.get("text") or "")

    def test_expand_refuses_roots_and_does_not_use_client_paths(self):
        self._ingest("policy.md", "Decision: widgets must stay blue.\n")
        packet = query_context(self.database, "widgets", path=self.root, agent="a")
        compact = compact_packet(packet)
        ref = dict(compact["evidence"][0]["expand"])
        other = Path(self.temp.name) / "other"
        other.mkdir()
        with self.assertRaises(ValueError):
            expand_reference(self.database, ref, roots=[other])
        ref["resolved_path"] = str(Path.home())
        recovered = expand_reference(self.database, ref, roots=[self.root])
        self.assertEqual(recovered["status"], "ok")


if __name__ == "__main__":
    unittest.main()
