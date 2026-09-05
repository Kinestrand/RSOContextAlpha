from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rso_context.audit import agent_inbox, propose_claim
from rso_context.db import Database
from rso_context.ingest import ingest_project


class AgentInboxTests(unittest.TestCase):
    def test_inbox_returns_only_cards_and_filters_topic(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "project"
            workspace.mkdir()
            (workspace / "AGENTS.md").write_text(
                "# Test project\n\nDecision: keep tests bounded.\n",
                encoding="utf-8",
            )
            database = Database(root / "context.sqlite3")
            ingest_project(database, workspace, agent="test")
            propose_claim(database, "ordinary proposal", agent="test", path=workspace)
            card = propose_claim(
                database,
                "RSO-CARD/v1 | kind=request | topic=alpha-test | from=test | "
                "status=open | body=check inbox | reply_to=none",
                agent="test",
                path=workspace,
            )["claim"]
            propose_claim(
                database,
                "RSO-CARD/v1 | kind=request | topic=beta-test | from=test | "
                "status=open | body=other topic | reply_to=none",
                agent="test",
                path=workspace,
            )

            packet = agent_inbox(database, path=workspace, topic="alpha-test")

            self.assertEqual(packet["schema"], "rso-agent-inbox/v1")
            self.assertEqual(packet["topic"], "alpha-test")
            self.assertEqual([item["id"] for item in packet["cards"]], [card["id"]])

    def test_inbox_rejects_invalid_topic_and_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "project"
            workspace.mkdir()
            (workspace / "AGENTS.md").write_text("# Test project\n", encoding="utf-8")
            database = Database(root / "context.sqlite3")
            ingest_project(database, workspace, agent="test")

            with self.assertRaisesRegex(ValueError, "stable lowercase slug"):
                agent_inbox(database, path=workspace, topic="Not Valid")
            with self.assertRaisesRegex(ValueError, "between 1 and 200"):
                agent_inbox(database, path=workspace, limit=0)


if __name__ == "__main__":
    unittest.main()
