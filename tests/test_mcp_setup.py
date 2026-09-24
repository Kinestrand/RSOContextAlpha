from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rso_context.cli import main
from rso_context.mcp_setup import inspect_clients, remove_client, setup_client


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

    def test_remove_preserves_commented_table_headers(self):
        config = Path(self.temp.name) / "codex.toml"
        setup_client("codex", self.root, config=config)
        text = config.read_text(encoding="utf-8")
        config.write_text(
            "[mcp_servers.other] # comment\n"
            'command = "other-server"\n\n' + text,
            encoding="utf-8",
        )
        remove_client("codex", config=config)
        after = config.read_text(encoding="utf-8")
        self.assertIn("[mcp_servers.other] # comment", after)
        self.assertIn("other-server", after)
        self.assertNotIn("rso-context", after)

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

    def test_opencode_setup_keeps_comments_file_parseable_and_other_servers(self):
        """OpenCode keys servers under "mcp" with a command list, and its config may be .jsonc."""
        config = Path(self.temp.name) / "opencode.jsonc"
        config.write_text(
            '{\n  // a comment the parser must tolerate\n'
            '  "theme": "dark",\n'
            '  "mcp": { "kapture": {"type": "local", "command": ["kapture"]} }\n}\n',
            encoding="utf-8",
        )
        setup_client("opencode", self.root, config=config)
        setup_client("opencode", self.root, config=config)
        data = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(data["theme"], "dark")
        self.assertIn("kapture", data["mcp"])
        entry = data["mcp"]["rso-context"]
        self.assertEqual(entry["type"], "local")
        self.assertTrue(entry["enabled"])
        self.assertEqual(entry["command"][-2], "--root")
        self.assertEqual(entry["command"][-1], str(self.root.resolve()))
        remove_client("opencode", config=config)
        after = json.loads(config.read_text(encoding="utf-8"))
        self.assertNotIn("rso-context", after["mcp"])
        self.assertIn("kapture", after["mcp"])
        self.assertEqual(after["theme"], "dark")

    def test_generated_env_carries_what_a_replaced_spawn_env_would_lose(self):
        """Hosts that replace the child env must still get a startable server.

        RSO_CONTEXT_HOME removes the need for a home directory, and on Windows
        SystemRoot is what winsock needs. Without them the server dies before
        the handshake and the host reports a timeout instead of a crash.
        """
        config = Path(self.temp.name) / "claude.json"
        setup_client("claude-code", self.root, config=config)
        env = json.loads(config.read_text(encoding="utf-8"))["mcpServers"]["rso-context"]["env"]
        self.assertIn("PYTHONPATH", env)
        self.assertTrue(env["RSO_CONTEXT_HOME"])
        if os.name == "nt":
            self.assertTrue(env["SystemRoot"])
            self.assertEqual(len(env["PATH"].split(os.pathsep)), 2)

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


    def test_google_clients_preserve_explicit_json_config(self):
        config = Path(self.temp.name) / "gemini" / "settings.json"
        config.parent.mkdir()
        config.write_text(
            json.dumps({"security": {"auth": {"selectedType": "oauth-personal"}}, "mcpServers": {"kapture": {"command": "npx", "args": []}}}),
            encoding="utf-8",
        )
        setup_client("gemini", self.root, config=config)
        setup_client("antigravity", self.root, config=config)
        data = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(data["security"]["auth"]["selectedType"], "oauth-personal")
        self.assertEqual(data["mcpServers"]["kapture"]["command"], "npx")
        self.assertEqual(data["mcpServers"]["rso-context"]["args"][-2], "--root")
        self.assertNotIn(".cmd", data["mcpServers"]["rso-context"]["command"].lower())
        removed = remove_client("gemini", config=config)
        self.assertEqual(removed.get("host"), "gemini")
        after = json.loads(config.read_text(encoding="utf-8"))
        self.assertNotIn("rso-context", after["mcpServers"])
        self.assertIn("kapture", after["mcpServers"])

    def test_google_default_configs_and_removal_are_independent(self):
        home = Path(self.temp.name) / "home"
        with patch("pathlib.Path.home", return_value=home), patch.dict(os.environ, {
            "RSO_MCP_GEMINI_CONFIG": "", "RSO_MCP_ANTIGRAVITY_CONFIG": "",
        }):
            gemini = setup_client("gemini", self.root)
            antigravity = setup_client("antigravity", self.root)
            self.assertEqual(Path(gemini["path"]), home / ".gemini/settings.json")
            self.assertEqual(Path(antigravity["path"]), home / ".gemini/config/mcp_config.json")
            self.assertEqual(antigravity["host"], "antigravity")
            self.assertTrue(inspect_clients()["antigravity"]["rso_context"])
            remove_client("antigravity")
            self.assertFalse(inspect_clients()["antigravity"]["rso_context"])
            self.assertTrue(inspect_clients()["gemini"]["rso_context"])

    def test_antigravity_environment_config_and_explicit_precedence(self):
        configured = Path(self.temp.name) / "custom-antigravity.json"
        explicit = Path(self.temp.name) / "explicit-antigravity.json"
        with patch.dict(os.environ, {"RSO_MCP_ANTIGRAVITY_CONFIG": str(configured)}):
            self.assertEqual(setup_client("antigravity", self.root)["path"], str(configured))
            result = setup_client("antigravity", self.root, config=explicit)
            self.assertEqual(result["path"], str(explicit))
            remove_client("antigravity", config=explicit)
            self.assertIn("rso-context", json.loads(configured.read_text())["mcpServers"])

    def test_doctor_includes_gemini_client(self):
        db = Path(self.temp.name) / "context.sqlite3"
        previous_codex = os.environ.get("RSO_MCP_CODEX_CONFIG")
        previous_claude = os.environ.get("RSO_MCP_CLAUDE_CONFIG")
        previous_gemini = os.environ.get("RSO_MCP_GEMINI_CONFIG")
        os.environ["RSO_MCP_CODEX_CONFIG"] = str(Path(self.temp.name) / "missing-codex.toml")
        os.environ["RSO_MCP_CLAUDE_CONFIG"] = str(Path(self.temp.name) / "missing-claude.json")
        os.environ["RSO_MCP_GEMINI_CONFIG"] = str(Path(self.temp.name) / "missing-gemini.json")
        try:
            self.assertEqual(main(["--db", str(db), "doctor"]), 0)
        finally:
            _restore_env("RSO_MCP_CODEX_CONFIG", previous_codex)
            _restore_env("RSO_MCP_CLAUDE_CONFIG", previous_claude)
            _restore_env("RSO_MCP_GEMINI_CONFIG", previous_gemini)

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
