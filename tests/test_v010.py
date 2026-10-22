"""Regression tests for the 0.10.0 fixes: speed, cache reuse, SQL batching,
run attribution, project cleanup, validation recording and degraded packets."""
from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import subprocess
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from rso_context.audit import record_validation, resolve_claim_id
from rso_context.check import check_questions
from rso_context.cleanup import apply_cleanup, plan_cleanup
from rso_context.cli import main
from rso_context.compact import compact_packet
from rso_context.db import SCHEMA_VERSION, Database
from rso_context.identity import inspect_project, register_project
from rso_context.ingest import ingest_project
from rso_context.mcp_contract import TOOL_NAMES
from rso_context.query import _graph_matches, cache_key_for, query_context


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


def _run_cli(*argv: str) -> dict:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(list(argv))
    return {"code": code, "json": json.loads(buffer.getvalue())}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        (self.workspace / "AGENTS.md").write_text(
            "# Fixture\n\nDecision: widgets must be blue.\n\nThe exporter must write 24 fps previews.\n",
            encoding="utf-8",
        )
        self.db_path = self.root / "context.sqlite3"
        self.database = Database(self.db_path)

    def ingest(self, agent: str = "test-agent"):
        return ingest_project(self.database, self.workspace, agent=agent)

    def rows(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self.database.connect() as connection:
            return connection.execute(sql, params).fetchall()


class MigrationTests(Fixture):
    def test_v6_ledger_migrates_to_v7_with_agent_column_and_fts_map(self):
        self.ingest()
        connection = sqlite3.connect(self.db_path)
        connection.execute("DROP INDEX idx_runs_agent_created")
        connection.execute("ALTER TABLE runs DROP COLUMN agent")
        connection.execute("DROP TABLE chunk_fts_map")
        connection.execute("UPDATE meta SET value='6' WHERE key='schema_version'")
        connection.commit()
        connection.close()
        self.database.initialize()
        columns = {row[1] for row in self.rows("PRAGMA table_info(runs)")}
        self.assertIn("agent", columns)
        fts = self.rows("SELECT COUNT(*) FROM chunk_fts")[0][0]
        mapped = self.rows("SELECT COUNT(*) FROM chunk_fts_map")[0][0]
        self.assertGreater(fts, 0)
        self.assertEqual(fts, mapped)
        self.assertEqual(int(self.rows("SELECT value FROM meta WHERE key='schema_version'")[0][0]), SCHEMA_VERSION)
        indexes = {row[1] for row in self.rows("SELECT type,name FROM sqlite_master WHERE type='index'")}
        self.assertTrue({"idx_nodes_project_name", "idx_claim_evidence_chunk"} <= indexes)


class RunAgentTests(Fixture):
    def test_query_and_check_runs_record_the_agent(self):
        self.ingest()
        query_context(self.database, "widgets blue", path=self.workspace, agent="agent-alpha")
        check_questions(
            self.database,
            [{"id": "q1", "type": "claim", "text": "widgets must be blue"}],
            path=self.workspace,
            agent="agent-beta",
        )
        agents = sorted(row[0] for row in self.rows("SELECT agent FROM runs"))
        self.assertEqual(agents, ["agent-alpha", "agent-beta"])
        usage = _run_cli("--db", str(self.db_path), "usage")["json"]
        self.assertEqual(sorted(row["agent"] for row in usage["rows"]), ["agent-alpha", "agent-beta"])


class SqlBatchingTests(Fixture):
    def test_updating_a_source_with_more_chunks_than_the_variable_limit(self):
        big = self.workspace / "COORDINATION.md"
        sections = [f"## Item {index}\n\nCard {index} must stay open.\n" for index in range(120)]
        big.write_text("\n".join(sections), encoding="utf-8")
        original_connect = Database.connect

        def low_limit(database):
            connection = original_connect(database)
            connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 40)
            return connection

        with patch.object(Database, "connect", low_limit):
            ingest_project(self.database, self.workspace, agent="t", limits=None)
            for revision in range(3):
                big.write_text("\n".join(sections) + f"\nrevision {revision}\n", encoding="utf-8")
                result = ingest_project(self.database, self.workspace, agent="t")
                self.assertEqual(result["counters"]["files_updated"], 1)
        live = self.rows(
            "SELECT COUNT(*) FROM chunks c JOIN source_versions v ON v.id=c.source_version_id "
            "JOIN sources s ON s.id=v.source_id WHERE s.current_hash=v.content_hash"
        )[0][0]
        self.assertEqual(self.rows("SELECT COUNT(*) FROM chunk_fts")[0][0], live)
        self.assertGreater(self.rows("SELECT COUNT(*) FROM chunks")[0][0], 40)


class CacheTests(Fixture):
    def setUp(self):
        super().setUp()
        self.ingest()

    def test_budgeted_agent_reuses_the_packet(self):
        first = query_context(self.database, "widgets", path=self.workspace, agent="a", run_budget=5)
        with patch("rso_context.query._retrieve_requirement", side_effect=AssertionError("cache missed")):
            second = query_context(self.database, "widgets", path=self.workspace, agent="a")
        self.assertFalse(first["cache"]["hit"])
        self.assertTrue(second["cache"]["hit"])
        self.assertEqual(second["run"], {"remaining": 3, "initial": 5})
        self.assertEqual(first["evidence"], second["evidence"])

    def test_spacing_and_path_spelling_share_one_cache_entry(self):
        query_context(self.database, "widgets  blue", path=self.workspace, agent="a")
        with patch("rso_context.query._retrieve_requirement", side_effect=AssertionError("cache missed")):
            packet = query_context(
                self.database, "  widgets blue ", path=str(self.workspace) + "/", agent="b"
            )
        self.assertTrue(packet["cache"]["hit"])

    def test_key_ignores_spacing_but_not_case_or_newlines(self):
        base = dict(project_id="p", path=None, versions={"p": 1}, config={})
        self.assertEqual(cache_key_for(query="a  b", **base), cache_key_for(query=" a b ", **base))
        self.assertNotEqual(cache_key_for(query="a b", **base), cache_key_for(query="A b", **base))
        self.assertNotEqual(cache_key_for(query="a; b", **base), cache_key_for(query="a\nb", **base))

    def test_packet_with_a_stale_candidate_is_still_cacheable(self):
        extra = self.workspace / "notes.md"
        extra.write_text("widgets are discussed here too.\n", encoding="utf-8")
        self.ingest()
        extra.write_text("changed without ingest\n", encoding="utf-8")
        first = query_context(self.database, "widgets", path=self.workspace, agent="a")
        self.assertTrue(any(item["stale"] for item in first["evidence"]))
        with patch("rso_context.query._retrieve_requirement", side_effect=AssertionError("cache missed")):
            second = query_context(self.database, "widgets", path=self.workspace, agent="b")
        self.assertTrue(second["cache"]["hit"])
        # Restoring the stale file changes its state, so the entry is rebuilt.
        extra.write_text("widgets are discussed here too.\n", encoding="utf-8")
        third = query_context(self.database, "widgets", path=self.workspace, agent="b")
        self.assertFalse(third["cache"]["hit"])


class RegistrationTests(Fixture):
    def make_git(self, remote: str = "https://example.invalid/org/one.git"):
        _git(self.workspace, "init", "-q")
        _git(self.workspace, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "add", ".")
        _git(self.workspace, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-qm", "init")
        _git(self.workspace, "remote", "add", "origin", remote)

    def test_degraded_git_detection_reuses_the_git_project(self):
        self.make_git()
        strong = register_project(self.database, self.workspace, agent="a")["project"]
        identity = inspect_project(self.workspace)
        folder_key = identity.canonical_key.rsplit("#tree:", 1)[1]
        for degraded in (
            replace(identity, kind="directory", canonical_key=f"directory-path:{folder_key}",
                    git_remote=None, git_root_commit=None),
            replace(identity, canonical_key=f"git-path:{folder_key}", git_remote=None),
            replace(identity, canonical_key=f"git-common:abc#tree:{folder_key}", git_remote=None),
        ):
            with patch("rso_context.identity.inspect_project", return_value=degraded):
                got = register_project(self.database, self.workspace, agent="b")
            self.assertEqual(got["project"]["id"], strong["id"])
            self.assertEqual(got["match"]["method"], "same_folder")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM projects")[0][0], 1)

    def test_renamed_remote_keeps_one_project(self):
        self.make_git("https://example.invalid/old/name.git")
        first = register_project(self.database, self.workspace, agent="a")["project"]["id"]
        _git(self.workspace, "remote", "set-url", "origin", "https://example.invalid/new/name.git")
        second = register_project(self.database, self.workspace, agent="a")["project"]["id"]
        self.assertEqual(first, second)

    def test_different_root_commit_in_the_same_folder_is_not_joined(self):
        self.make_git()
        first = register_project(self.database, self.workspace, agent="a")["project"]["id"]
        identity = inspect_project(self.workspace)
        other = replace(identity, git_root_commit="f" * 40,
                        canonical_key=identity.canonical_key.replace(identity.git_root_commit, "f" * 40))
        with patch("rso_context.identity.inspect_project", return_value=other):
            second = register_project(self.database, self.workspace, agent="a")["project"]["id"]
        self.assertNotEqual(first, second)


class CleanupTests(Fixture):
    def split_registration(self):
        """Recreate a pre-0.10 split: one folder, two identity keys."""
        self.ingest("agent-one")
        identity = inspect_project(self.workspace)
        split = replace(identity, canonical_key="git:legacy#tree:" + identity.canonical_key.split(":", 1)[1],
                        kind="git")
        with patch("rso_context.identity._same_folder_project", return_value=None), \
                patch("rso_context.identity.inspect_project", return_value=split):
            ingest_project(self.database, self.workspace, agent="agent-two")

    def test_dry_run_reports_and_changes_nothing(self):
        self.split_registration()
        before = self.rows("SELECT COUNT(*) FROM projects")[0][0]
        plan = _run_cli("--db", str(self.db_path), "cleanup-projects")["json"]
        self.assertEqual(plan["mode"], "dry_run")
        self.assertEqual(plan["summary"]["merge_groups"], 1)
        self.assertEqual(plan["summary"]["projects_to_merge"], 1)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM projects")[0][0], before)

    def test_apply_requires_yes_and_then_merges(self):
        self.split_registration()
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            code = main(["--db", str(self.db_path), "cleanup-projects", "--apply"])
        self.assertNotEqual(code, 0)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM projects")[0][0], 2)
        result = apply_cleanup(self.database)
        self.assertTrue(all(item.get("merged", True) for item in result["results"]))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM projects")[0][0], 1)
        survivor = self.rows("SELECT id FROM projects")[0][0]
        self.assertEqual({row[0] for row in self.rows("SELECT DISTINCT project_id FROM project_aliases")}, {survivor})
        self.assertEqual(self.rows("SELECT COUNT(*) FROM chunk_fts")[0][0],
                         self.rows("SELECT COUNT(*) FROM chunk_fts_map")[0][0])
        packet = query_context(self.database, "widgets", path=self.workspace, agent="a")
        self.assertEqual(packet["project"]["id"], survivor)

    def test_agent_worktree_is_a_prune_candidate(self):
        worktree = self.root / "repo" / ".claude" / "worktrees" / "agent-a0123456789abcdef"
        worktree.mkdir(parents=True)
        (worktree / "README.md").write_text("scratch\n", encoding="utf-8")
        ingest_project(self.database, worktree, agent="t")
        plan = plan_cleanup(self.database)
        candidate = next(item for item in plan["prune"] if item["path"] == str(worktree.resolve()))
        self.assertIn("agent_worktree", candidate["reasons"])
        self.assertIn("scratch_name", candidate["reasons"])


class BatchedCleanupTests(CleanupTests):
    def add_worktree(self, name: str) -> Path:
        worktree = self.root / "repo" / ".claude" / "worktrees" / name
        worktree.mkdir(parents=True)
        for index in range(6):
            (worktree / f"note{index}.md").write_text(
                f"# Note {index}\n\nThe exporter must write {index} previews.\n", encoding="utf-8"
            )
        ingest_project(self.database, worktree, agent="t")
        return worktree

    def counts(self) -> dict:
        tables = ("projects", "sources", "chunks", "chunk_fts", "chunk_fts_map", "claims", "nodes", "edges")
        return {table: self.rows(f"SELECT COUNT(*) FROM {table}")[0][0] for table in tables}

    def test_apply_commits_many_small_transactions_and_reports_timing(self):
        self.split_registration()
        self.add_worktree("agent-a0123456789abcdef")
        messages = []
        result = apply_cleanup(self.database, batch_rows=1, pause_seconds=0, progress=messages.append)
        timing = result["timing"]
        self.assertGreater(timing["transactions"], 10)
        self.assertGreaterEqual(timing["longest_lock_ms"], 0)
        self.assertEqual(len(messages), 2)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM projects")[0][0], 1)
        after = self.counts()
        self.assertEqual(after["chunk_fts"], after["chunks"])
        self.assertEqual(after["chunk_fts_map"], after["chunks"])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM meta WHERE key='cleanup_pending'")[0][0], 0)

    def test_interrupted_apply_resumes_and_finishes_cleanly(self):
        from rso_context import cleanup

        self.split_registration()
        self.add_worktree("agent-a0123456789abcdef")
        self.add_worktree("agent-b0123456789abcdef")
        original = cleanup._Batcher.step
        calls = {"n": 0}

        def flaky(batcher, work):
            calls["n"] += 1
            if calls["n"] == 12:
                raise KeyboardInterrupt
            return original(batcher, work)

        with patch.object(cleanup._Batcher, "step", flaky):
            with self.assertRaises(KeyboardInterrupt):
                apply_cleanup(self.database, batch_rows=1, pause_seconds=0)
        pending = plan_cleanup(self.database)["pending"]
        self.assertTrue(pending)
        self.assertGreater(self.rows("SELECT COUNT(*) FROM projects")[0][0], 1)
        result = apply_cleanup(self.database, batch_rows=1, pause_seconds=0)
        self.assertTrue(result["resumed"])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM projects")[0][0], 1)
        after = self.counts()
        self.assertEqual(after["chunk_fts"], after["chunks"])
        self.assertEqual(after["chunk_fts_map"], after["chunks"])
        self.assertEqual(plan_cleanup(self.database)["pending"], [])
        orphans = self.rows(
            "SELECT COUNT(*) FROM chunks c LEFT JOIN source_versions v ON v.id=c.source_version_id WHERE v.id IS NULL"
        )[0][0]
        self.assertEqual(orphans, 0)

    def test_concurrent_writer_is_not_locked_out(self):
        import threading

        self.split_registration()
        for index in range(3):
            self.add_worktree(f"agent-{index}0123456789abcdef")
        errors, writes = [], []
        stop = threading.Event()

        def writer():
            connection = sqlite3.connect(self.db_path, timeout=2)
            try:
                while not stop.is_set():
                    try:
                        connection.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('probe', ?)", (str(time.time()),))
                        connection.commit()
                        connection.execute("SELECT COUNT(*) FROM projects").fetchone()
                        writes.append(1)
                    except sqlite3.OperationalError as error:
                        errors.append(str(error))
                    time.sleep(0.005)
            finally:
                connection.close()

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            apply_cleanup(self.database, batch_rows=2, pause_seconds=0.01)
        finally:
            stop.set()
            thread.join()
        self.assertEqual(errors, [])
        self.assertGreater(len(writes), 0)


class CompactTests(Fixture):
    def test_compact_dry_run_estimates_and_yes_vacuums(self):
        self.ingest()
        dry = _run_cli("--db", str(self.db_path), "compact")["json"]
        self.assertEqual(dry["mode"], "dry_run")
        self.assertIn("locked", dry["warning"])
        with contextlib.redirect_stderr(io.StringIO()):
            done = _run_cli("--db", str(self.db_path), "compact", "--yes")["json"]
        self.assertEqual(done["mode"], "applied")
        self.assertEqual(done["after"]["free_bytes"], 0)


class ValidationTests(Fixture):
    def test_record_validation_by_text_and_prefix_from_cli(self):
        self.ingest()
        claim = self.rows("SELECT id, display_text FROM claims WHERE display_text LIKE '%widgets must be blue%'")[0]
        result = _run_cli(
            "--db", str(self.db_path), "record-validation", "--text", claim["display_text"],
            "--path", str(self.workspace), "--validator", "pytest suite", "--result", "verified",
            "--agent", "agent-beta", "--evidence", "tests/test_widgets.py passed",
        )["json"]
        self.assertEqual(result["claim"]["trust_state"], "verified")
        self.assertEqual(result["validation"]["details"]["recorded_by"], "agent-beta")
        self.assertEqual(result["validation"]["details"]["evidence"], "tests/test_widgets.py passed")
        self.assertEqual(resolve_claim_id(self.database, claim["id"][:10]), claim["id"])

    def test_observed_claim_stays_unverified_without_a_named_validation(self):
        self.ingest()
        states = {row[0] for row in self.rows("SELECT trust_state FROM claims")}
        self.assertNotIn("verified", states)
        claim_id = self.rows("SELECT id FROM claims LIMIT 1")[0][0]
        with self.assertRaises(ValueError):
            record_validation(self.database, claim_id, validator="  ", result="verified")

    def test_mcp_contract_advertises_rso_validate_after_existing_tools(self):
        self.assertEqual(TOOL_NAMES[:6], ("rso_use", "rso_query", "rso_check", "rso_resume", "rso_explain", "rso_expand"))
        self.assertIn("rso_validate", TOOL_NAMES)


class PacketTests(Fixture):
    def test_tiny_budget_explains_that_evidence_exists(self):
        self.ingest()
        packet = query_context(self.database, "widgets must be blue", path=self.workspace, agent="a")
        compact = compact_packet(packet, byte_budget=900)
        self.assertLessEqual(len(json.dumps(compact, ensure_ascii=False, separators=(",", ":")).encode()), 900 + 64)
        if not compact.get("evidence"):
            self.assertIn("degraded", compact)
            self.assertGreater(compact["degraded"]["evidence_found"], 0)
            self.assertEqual(compact["degraded"]["evidence_inline"], 0)

    def test_degraded_note_names_a_budget_that_fits(self):
        self.ingest()
        packet = query_context(self.database, "widgets must be blue", path=self.workspace, agent="a")
        compact = compact_packet(packet, byte_budget=1400)
        if "degraded" in compact and "suggested_byte_budget" in compact["degraded"]:
            roomy = compact_packet(packet, byte_budget=compact["degraded"]["suggested_byte_budget"])
            self.assertTrue(roomy["evidence"])

    def test_graph_skips_project_nodes_and_single_word_padding(self):
        self.ingest()
        with self.database.connect() as connection:
            project_id = connection.execute("SELECT id FROM projects").fetchone()[0]
            nodes = _graph_matches(connection, [project_id], ["exporter", "previews", "zebra"])
            self.assertTrue(all(node["kind"] != "project" for node in nodes))
            for node in nodes:
                self.assertGreaterEqual(node["match_score"], 2)


class DoctorTests(Fixture):
    def test_doctor_skips_full_quick_check_by_default(self):
        self.ingest()
        result = _run_cli("--db", str(self.db_path), "doctor")["json"]
        self.assertEqual(result["quick_check"], "ok")
        self.assertTrue(result["integrity"].startswith("header_only"))
        self.assertNotIn("quick_check", result["timings_ms"])
        full = _run_cli("--db", str(self.db_path), "doctor", "--integrity")["json"]
        self.assertEqual(full["integrity"], "quick_check")
        self.assertIn("quick_check", full["timings_ms"])


if __name__ == "__main__":
    unittest.main()
