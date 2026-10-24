"""Regression tests for 0.11.0: lean packets, cheap rso_use, per-file dedup,
edited-file freshness, stemming FTS (schema 8), batched ingest, orphan pruning."""
from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rso_context import mcp_server
from rso_context.audit import record_validation
from rso_context.cli import main
from rso_context.config import Limits
from rso_context.db import SCHEMA_REVISION, SCHEMA_VERSION, Database
from rso_context.ingest import chunk_text, ingest_project, use_workspace
from rso_context.prune import plan_orphans, prune_orphans
from rso_context.query import _fts_expressions, query_context


def _run_cli(*argv: str) -> dict:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(list(argv))
    return {"code": code, "json": json.loads(buffer.getvalue())}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "project"
        self.root.mkdir()
        (self.root / "AGENTS.md").write_text("# Fixture\n", encoding="utf-8")
        self.policy = self.root / "policy.md"
        self.policy.write_text("Decision: widgets must be blue.\n", encoding="utf-8")
        self.db_path = base / "context.sqlite3"
        self.database = Database(self.db_path)

    def ingest(self, **kwargs):
        return ingest_project(self.database, self.root, agent="t", **kwargs)

    def query(self, text="widgets", **kwargs):
        return query_context(self.database, text, path=self.root, agent="t", **kwargs)


class DefaultsTests(Fixture):
    def test_lean_defaults(self):
        self.assertEqual(Limits.query_limit, 4)
        self.assertEqual(Limits.compact_byte_budget, 4_000)
        self.assertEqual(Limits.check_limit, 8)

    def test_graph_nodes_off_by_default(self):
        self.ingest()
        self.assertEqual(self.query()["graph_nodes"], [])
        self.assertFalse(self.query()["config"]["graph"])
        self.assertTrue(self.query(include_graph=True, use_cache=False)["config"]["graph"])

    def test_packet_hash_ignores_budget_counters(self):
        self.ingest()
        unbudgeted = self.query(use_cache=False)
        budgeted = self.query(use_cache=False, run_budget=5)
        self.assertNotEqual(unbudgeted["run"], budgeted["run"])
        self.assertEqual(unbudgeted["packet_hash"], budgeted["packet_hash"])

    def test_server_instructions_have_no_startup_ritual(self):
        self.assertNotIn("Call rso_use", mcp_server.INSTRUCTIONS)
        self.assertNotIn("before rso_query", mcp_server.INSTRUCTIONS)

    def test_cli_query_is_compact_by_default_and_full_on_request(self):
        self.ingest()
        compact = _run_cli("--db", str(self.db_path), "query", "widgets", "--path", str(self.root))
        self.assertEqual(compact["json"]["schema"], "rso-mcp-packet/v1")
        self.assertLessEqual(compact["json"]["byte_count"], 4_000)
        full = _run_cli("--db", str(self.db_path), "query", "widgets", "--path", str(self.root), "--full")
        self.assertEqual(full["json"]["schema"], "rso-context-packet/v2")

    def test_compact_items_do_not_repeat_hashes(self):
        self.ingest()
        compact = _run_cli("--db", str(self.db_path), "query", "widgets", "--path", str(self.root))
        item = compact["json"]["evidence"][0]
        self.assertNotIn("chunk_hash", item)
        self.assertNotIn("source_hash", item)
        self.assertNotIn("project_id", item)
        self.assertEqual(item["expand"]["schema"], "rso-expand-ref/v1")


class QueryWritesTests(Fixture):
    def test_one_write_transaction_per_query(self):
        self.ingest()
        original = Database.transaction
        calls = []

        def counting(self_db):
            calls.append(1)
            return original(self_db)

        with patch.object(Database, "transaction", counting):
            self.query(use_cache=False)
            self.assertEqual(len(calls), 1)
            calls.clear()
            self.query()  # cache hit
            self.assertEqual(len(calls), 1)


class DedupTests(Fixture):
    def test_at_most_two_items_per_file_and_adjacent_spans_merge(self):
        lines = []
        for index in range(40):
            lines.append(f"widgets section {index} " + "filler text " * 20)
            lines.append("")
        (self.root / "many.md").write_text("\n".join(lines), encoding="utf-8")
        self.ingest()
        packet = self.query(limit=8, token_budget=50_000)
        per_file = {}
        for item in packet["evidence"]:
            per_file[item["relative_path"]] = per_file.get(item["relative_path"], 0) + 1
        self.assertLessEqual(max(per_file.values()), 2)
        self.assertLessEqual(per_file.get("many.md", 0), 2)

    def test_duplicate_chunks_in_one_file_are_indexed_once(self):
        block = "Repeated widgets paragraph that is long enough to be its own chunk. " * 10
        (self.root / "dup.md").write_text("\n\n".join([block] * 6), encoding="utf-8")
        result = self.ingest(limits=Limits(chunk_characters=800))
        self.assertGreaterEqual(result["counters"]["chunks_deduplicated"], 1)


class FreshnessTests(Fixture):
    def test_edited_file_returns_current_evidence(self):
        self.ingest()
        self.query()
        self.policy.write_text("Decision: widgets must be pink.\n", encoding="utf-8")
        packet = self.query(use_cache=False)
        self.assertEqual(packet["requirements"][0]["status"], "evidence_found")
        self.assertIn("pink", packet["evidence"][0]["text"])
        self.assertFalse(packet["evidence"][0]["stale"])

    def test_stale_plate_keeps_indexed_text_when_refresh_is_off(self):
        self.ingest()
        self.policy.write_text("Decision: widgets must be pink.\n", encoding="utf-8")
        packet = self.query(use_cache=False, refresh_stale=False)
        item = packet["evidence"][0]
        self.assertTrue(item["stale"])
        self.assertIn("blue", item["text"])
        self.assertNotIn("pink", item["text"])
        self.assertEqual(packet["empty_result"]["kind"], "only_stale_spans")


class UseTests(Fixture):
    def test_use_modes(self):
        first = use_workspace(self.database, self.root, agent="t")
        self.assertEqual(first["mode"], "initial_ingest")
        self.assertEqual(use_workspace(self.database, self.root, agent="t")["mode"], "fresh")
        self.policy.write_text("Decision: widgets must be green.\n", encoding="utf-8")
        refreshed = use_workspace(self.database, self.root, agent="t")
        self.assertEqual(refreshed["mode"], "targeted_refresh")
        self.assertEqual(refreshed["counters"]["files_updated"], 1)
        for index in range(5):
            (self.root / f"n{index}.md").write_text(f"note {index}\n", encoding="utf-8")
        stale = use_workspace(self.database, self.root, agent="t", max_refresh=2)
        self.assertEqual(stale["mode"], "stale")
        self.assertIn("rso-context ingest", stale["advice"])

    def test_use_on_known_project_does_not_ingest_everything(self):
        self.ingest()
        with patch("rso_context.ingest.ingest_project", side_effect=AssertionError("full ingest")):
            self.assertEqual(use_workspace(self.database, self.root, agent="t")["mode"], "fresh")


class IngestTests(Fixture):
    def test_vendored_and_generated_files_are_excluded(self):
        for folder in ("node_modules/pkg", "third_party/lib", "vendor/x", "dist", "build", ".venv/lib"):
            target = self.root / folder
            target.mkdir(parents=True)
            (target / "widgets.md").write_text("widgets vendored\n", encoding="utf-8")
        (self.root / "package-lock.json").write_text('{"widgets": 1}\n', encoding="utf-8")
        (self.root / "app.min.js").write_text("var widgets=1;\n", encoding="utf-8")
        result = self.ingest()
        with self.database.connect() as connection:
            paths = {row[0] for row in connection.execute("SELECT relative_path FROM sources")}
        self.assertEqual(paths, {"AGENTS.md", "policy.md"})
        self.assertGreaterEqual(result["coverage"]["counts"]["generated"], 2)

    def test_unchanged_files_cause_no_source_writes(self):
        self.ingest()
        with self.database.connect() as connection:
            before = [tuple(row) for row in connection.execute("SELECT id,last_seen FROM sources ORDER BY id")]
        second = self.ingest()
        with self.database.connect() as connection:
            after = [tuple(row) for row in connection.execute("SELECT id,last_seen FROM sources ORDER BY id")]
        self.assertEqual(before, after)
        self.assertFalse(second["changed"])
        self.assertEqual(second["counters"]["files_unchanged"], 2)

    def test_changed_files_commit_in_batches(self):
        for index in range(7):
            (self.root / f"f{index}.md").write_text(f"widgets {index}\n", encoding="utf-8")
        result = self.ingest(batch_files=3)
        self.assertEqual(result["counters"]["files_added"], 9)
        self.assertEqual(result["counters"]["write_batches"], 4)  # 3 batches + finish

    def test_long_line_is_clipped_in_packets(self):
        (self.root / "min.txt").write_text("x " * 6000 + " widgets " + "y " * 6000, encoding="utf-8")
        self.ingest()
        packet = self.query(token_budget=50_000)
        item = next(i for i in packet["evidence"] if i["relative_path"] == "min.txt")
        self.assertLessEqual(len(item["text"]), Limits.long_chunk_characters)
        self.assertIn("widgets", item["text"])


class SearchTests(Fixture):
    def test_schema_8_uses_porter_stemming(self):
        self.policy.write_text("Decision: the widget must be blue.\n", encoding="utf-8")
        self.ingest()
        self.assertEqual((SCHEMA_VERSION, SCHEMA_REVISION), (7, 8))
        packet = self.query("widgets")
        self.assertEqual(packet["requirements"][0]["status"], "evidence_found")

    def test_and_first_then_or_fill(self):
        expressions = _fts_expressions("blue widgets policy")
        self.assertIn(" AND ", expressions[0])
        self.assertIn(" OR ", expressions[-1])
        (self.root / "other.md").write_text("Only policy words here.\n", encoding="utf-8")
        self.ingest()
        packet = self.query("blue widgets policy", limit=4)
        self.assertEqual(packet["evidence"][0]["relative_path"], "policy.md")

    def test_migration_from_v7_keeps_indexed_rows(self):
        self.ingest()
        with contextlib.closing(sqlite3.connect(self.db_path)) as connection, connection:
            count = connection.execute("SELECT COUNT(*) FROM chunk_fts").fetchone()[0]
            connection.execute("DROP TABLE chunk_fts")
            connection.execute(
                "CREATE VIRTUAL TABLE chunk_fts USING fts5(text, heading, path, project_id, "
                "chunk_id UNINDEXED, tokenize='unicode61 remove_diacritics 2')"
            )
            connection.execute(
                "INSERT INTO chunk_fts(text,heading,path,project_id,chunk_id) VALUES "
                "('Decision: widgets must be blue.','', 'policy.md','x',"
                "(SELECT MIN(id) FROM chunks)), ('orphan','','gone.md','x',999999)"
            )
            connection.execute("DELETE FROM meta WHERE key='schema_revision'")
        self.database.initialize()
        with contextlib.closing(sqlite3.connect(self.db_path)) as connection:
            sql = connection.execute("SELECT sql FROM sqlite_master WHERE name='chunk_fts'").fetchone()[0]
            rows = connection.execute("SELECT COUNT(*) FROM chunk_fts").fetchone()[0]
            version = connection.execute("SELECT value FROM meta WHERE key='schema_revision'").fetchone()[0]
            hard = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
        self.assertIn("porter", sql)
        self.assertEqual(rows, 1)  # orphan row dropped
        self.assertEqual(version, "8")
        self.assertEqual(hard, "7")  # 0.10 servers that are still running keep working
        self.assertGreaterEqual(count, 1)


class PruneTests(Fixture):
    def test_prune_removes_superseded_chunks_but_keeps_validated_evidence(self):
        self.ingest()
        packet = self.query("widgets blue")
        claim = packet["claims"][0]
        record_validation(self.database, claim["id"], validator="human", result="verified")
        (self.root / "notes.md").write_text("Decision: gadgets should be round.\n", encoding="utf-8")
        self.ingest()
        (self.root / "notes.md").write_text("Decision: gadgets should be square.\n", encoding="utf-8")
        self.policy.write_text("Decision: widgets must be pink.\n", encoding="utf-8")
        self.ingest()
        plan = plan_orphans(self.database)
        self.assertEqual(plan["superseded_chunks"], 2)
        self.assertEqual(plan["prunable_chunks"], 1)
        result = prune_orphans(self.database, pause_seconds=0)
        self.assertEqual(result["deleted"]["chunks"], 1)
        self.assertEqual(plan_orphans(self.database)["prunable_chunks"], 0)
        with self.database.connect() as connection:
            self.assertIsNotNone(connection.execute("SELECT 1 FROM claims WHERE id=?", (claim["id"],)).fetchone())
        self.assertEqual(self.query("gadgets square")["requirements"][0]["status"], "evidence_found")


if __name__ == "__main__":
    unittest.main()
