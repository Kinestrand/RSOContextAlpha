from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from rso_context import query as query_module
from rso_context import replay as replay_module
from rso_context.check import check_questions
from rso_context.db import Database
from rso_context.ingest import ingest_project
from rso_context.query import query_context
from rso_context.replay import REPLAY_SCHEMA, replay_runs


class ReplayTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.database = Database(Path(self.temp.name) / "context.sqlite3")
        self.write(
            "AGENTS.md",
            "# Fixture\n\n"
            "Decision: export the preview at 24 fps.\n\n"
            "We evaluated the Arnold renderer for finals and chose Cycles.\n",
        )
        ingest_project(self.database, self.root, agent="replay-test")

    def write(self, relative: str, body: str) -> None:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")

    def record_query(
        self,
        text: str = "What export frame rate is required; which renderer was chosen?",
    ) -> dict:
        return query_context(self.database, text, path=self.root, agent="replay-test")

    def record_check(self) -> dict:
        return check_questions(
            self.database,
            [{"id": "q1", "type": "claim", "text": "the preview exports at 24 fps"}],
            path=self.root,
            agent="replay-test",
        )

    def ledger_state(self) -> tuple:
        connection = sqlite3.connect(self.database.path)
        try:
            connection.row_factory = sqlite3.Row
            runs = connection.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]
            cache = connection.execute("SELECT COUNT(*) AS n FROM cache").fetchone()["n"]
            budgets = sorted(
                tuple(row)
                for row in connection.execute(
                    "SELECT project_id, agent, remaining FROM run_budgets"
                )
            )
        finally:
            connection.close()
        return runs, cache, budgets

    def replay(self, **kwargs) -> dict:
        return replay_runs(self.database, path=self.root, **kwargs)

    def test_unchanged_code_reports_no_drift(self):
        self.record_query()
        self.record_check()
        report = self.replay()
        self.assertEqual(report["schema"], REPLAY_SCHEMA)
        self.assertEqual(report["counts"]["replayed"], 2)
        self.assertEqual(report["counts"]["matched"], 2)
        self.assertEqual(report["counts"]["drifted"], 0)
        self.assertEqual(report["drift"], [])

    def test_replay_does_not_touch_the_ledger(self):
        self.record_query()
        self.record_check()
        before = self.ledger_state()
        self.replay()
        self.assertEqual(self.ledger_state(), before)

    def test_query_parser_change_is_reported_as_algorithmic_drift(self):
        self.record_query()
        original = query_module.split_requirements

        def fewer_requirements(query, limit=None, **kwargs):
            # Stand-in for a real parser regression: the leaf split collapses.
            return original(query, limit=1)

        query_module.split_requirements = fewer_requirements
        self.addCleanup(setattr, query_module, "split_requirements", original)

        report = self.replay()
        self.assertEqual(report["counts"]["drifted_algorithmic"], 1)
        entry = report["drift"][0]
        self.assertEqual(entry["kind"], "query")
        self.assertEqual(entry["cause"], "algorithmic")
        self.assertIn("requirements", entry["changed_fields"])

    def test_check_semantics_change_is_reported_as_algorithmic_drift(self):
        self.record_check()
        original = replay_module.check_questions

        def altered(database, questions, **kwargs):
            result = original(database, questions, **kwargs)
            result["answers"][0]["status"] = "unknown"
            return result

        replay_module.check_questions = altered
        self.addCleanup(setattr, replay_module, "check_questions", original)

        report = self.replay()
        self.assertEqual(report["counts"]["drifted_algorithmic"], 1)
        entry = report["drift"][0]
        self.assertEqual(entry["kind"], "check")
        self.assertEqual(entry["cause"], "algorithmic")
        self.assertIn("answers", entry["changed_fields"])

    def test_runs_behind_the_corpus_version_are_skipped_by_default(self):
        self.record_query()
        self.write("AGENTS.md", "# Fixture\n\nDecision: export the preview at 30 fps.\n")
        ingest_project(self.database, self.root, agent="replay-test")

        skipped = self.replay()
        self.assertEqual(skipped["counts"]["stale_corpus_skipped"], 1)
        self.assertEqual(skipped["counts"]["replayed"], 0)

        included = self.replay(include_historical=True)
        self.assertEqual(included["counts"]["stale_corpus_skipped"], 0)
        self.assertEqual(included["counts"]["replayed"], 1)

    def test_edited_source_without_ingest_is_not_algorithmic_drift(self):
        # Retrieval reads live files, so a file can change without the corpus
        # version moving. That is not a regression in the code under test.
        self.record_query()
        self.write(
            "AGENTS.md",
            "# Fixture\n\n"
            "Decision: export the preview at 24 fps.\n\n"
            "We evaluated the Arnold renderer for finals and chose Cycles.\n"
            "An extra line that changes the file without an ingest.\n",
        )

        report = self.replay()
        self.assertEqual(report["counts"]["replayed"], 1)
        self.assertEqual(report["counts"]["drifted_algorithmic"], 0)
        entry = report["drift"][0]
        self.assertEqual(entry["cause"], "sources_changed")
        self.assertIn("AGENTS.md", entry["changed_sources"])

    def test_corpus_movement_is_not_counted_as_algorithmic_drift(self):
        self.record_query()
        self.write("AGENTS.md", "# Fixture\n\nDecision: export the preview at 30 fps.\n")
        ingest_project(self.database, self.root, agent="replay-test")

        report = self.replay(include_historical=True)
        self.assertEqual(report["counts"]["drifted"], 1)
        self.assertEqual(report["counts"]["drifted_algorithmic"], 0)
        self.assertEqual(report["drift"][0]["cause"], "corpus_moved")
        self.assertEqual(report["drift"][0]["corpus"], "historical")


if __name__ == "__main__":
    unittest.main()
