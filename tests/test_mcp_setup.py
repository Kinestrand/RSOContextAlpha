from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from rso_context.cli import main
from rso_context.mcp_setup import remove_client, setup_client


def _restore_env(name: str, previous: str | None) -> None:
    if previous is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = previous


class McpSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        (self.root / "AGENTS.md").write_text("Decision: widgets must stay blue.\n", encoding="utf-8")

    def test_codex_setup_is_repeatable_and_preserves_other_servers(self):
        config = Path(self.temp.name) / "codex" / "config.toml"
        config.parent.mkdir()
        config.write_text(
            'model = "test"\n\n[mcp_servers.graft]\ncommand = "npx"\nargs = ["-y", "@nanonets/graft", "mcp"]\n',
            encoding="utf-8",
        )
        first = setup_client("codex", self.root, config=config)
        second = setup_client("codex", self.root, config=config)
        self.assertEqual(first["action"], "setup")
        self.assertEqual(second["server"], "rso-context")
        text = config.read_text(encoding="utf-8")
        self.assertEqual(text.count("[mcp_servers.rso-context]"), 1)
        self.assertIn("[mcp_servers.graft]", text)
        self.assertIn("model = \"test\"", text)
        self.assertIn("rso_context", text)
        self.assertIn("--root", text)
        removed = remove_client("codex", config=config)
        self.assertEqual(removed["action"], "remove")
        after = config.read_text(encoding="utf-8")
        self.assertNotIn("rso-context", after)
        self.assertIn("[mcp_servers.graft]", after)
        self.assertIn("model = \"test\"", after)

    def test_claude_setup_preserves_unrelated_json_and_removes_only_rso(self):
        config = Path(self.temp.name) / "claude.json"
        config.write_text(
            json.dumps({"autoUpdates": True, "mcpServers": {"kapture": {"command": "kapture", "args": []}}}),
            encoding="utf-8",
        )
        setup_client("claude-code", self.root, config=config)
        setup_client("claude-code", self.root, config=config)
        data = json.loads(config.read_text(encoding="utf-8"))
        self.assertTrue(data["autoUpdates"])
        self.assertEqual(data["mcpServers"]["kapture"]["command"], "kapture")
        self.assertEqual(data["mcpServers"]["rso-context"]["args"][-2], "--root")
        remove_client("claude-code", config=config)
        after = json.loads(config.read_text(encoding="utf-8"))
        self.assertNotIn("rso-context", after["mcpServers"])
        self.assertIn("kapture", after["mcpServers"])
        self.assertTrue(after["autoUpdates"])

    def test_setup_quotes_roots_that_contain_spaces(self):
        root = Path(self.temp.name) / "bounded project"
        root.mkdir()
        (root / "AGENTS.md").write_text("Decision: widgets must stay blue.\n", encoding="utf-8")
        config = Path(self.temp.name) / "codex.toml"
        setup_client("codex", root, config=config)
        quoted = json.dumps(str(root.resolve()), ensure_ascii=True)
        self.assertIn(quoted, config.read_text(encoding="utf-8"))
        claude = Path(self.temp.name) / "claude.json"
        result = setup_client("claude-code", root, config=claude)
        self.assertEqual(result["launch"]["args"][-1], str(root.resolve()))
        self.assertIn(" ", str(root))

    def test_cli_setup_and_remove_use_explicit_config(self):
        config = Path(self.temp.name) / "codex.toml"
        self.assertEqual(
            main(
                [
                    "mcp",
                    "--setup",
                    "--client",
                    "codex",
                    "--root",
                    str(self.root),
                    "--config",
                    str(config),
                ]
            ),
            0,
        )
        self.assertIn("[mcp_servers.rso-context]", config.read_text(encoding="utf-8"))
        self.assertEqual(
            main(["mcp", "--remove", "--client", "codex", "--config", str(config)]),
            0,
        )
        self.assertNotIn("rso-context", config.read_text(encoding="utf-8"))

    def test_doctor_includes_mcp_without_requiring_it_for_ready(self):
        db = Path(self.temp.name) / "context.sqlite3"
        previous_codex = os.environ.get("RSO_MCP_CODEX_CONFIG")
        previous_claude = os.environ.get("RSO_MCP_CLAUDE_CONFIG")
        os.environ["RSO_MCP_CODEX_CONFIG"] = str(Path(self.temp.name) / "missing-codex.toml")
        os.environ["RSO_MCP_CLAUDE_CONFIG"] = str(Path(self.temp.name) / "missing-claude.json")
        try:
            self.assertEqual(main(["--db", str(db), "doctor"]), 0)
        finally:
            _restore_env("RSO_MCP_CODEX_CONFIG", previous_codex)
            _restore_env("RSO_MCP_CLAUDE_CONFIG", previous_claude)


if __name__ == "__main__":
    unittest.main()
