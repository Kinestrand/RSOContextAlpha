from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from rso_context.cli import (
    STDIO_STARTUP_ERROR_CODE,
    _emit_stdio_startup_failure,
    build_parser,
    main,
)
from rso_context.db import Database
from rso_context.ingest import ingest_project
from rso_context.mcp_contract import (
    ACCESS_OPEN,
    ACCESS_STRICT,
    resolve_launch_roots,
    TOOL_NAMES,
    assert_bounded_root,
    bind_roots,
    resolve_tool_path,
    resolve_workspace_path,
    tool_names,
)
from rso_context.mcp_runtime import current_sdk_status, install_runtime, runtime_status
from rso_context.mcp_server import bound_packet, explain_packet
from rso_context.query import query_context


ROOT = Path(__file__).resolve().parents[1]


def ensure_isolated_python() -> Path:
    status = runtime_status()
    if status["runtime_ready"] and status["runtime_python"]:
        return Path(status["runtime_python"])
    try:
        installed = install_runtime()
    except Exception as error:
        raise unittest.SkipTest(f"isolated MCP runtime unavailable: {error}") from error
    return Path(installed["runtime_python"])


def _run_isolated(python: Path, script: str, *script_args: str, timeout: int = 90) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["RSO_MCP_IN_RUNTIME"] = "1"
    completed = subprocess.run(
        [str(python), "-c", script, *script_args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        cwd=str(ROOT),
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    return json.loads(lines[-1])


class McpContractTests(unittest.TestCase):
    def test_tool_contract_names(self):
        self.assertEqual(
            tool_names(),
            ("rso_use", "rso_query", "rso_check", "rso_resume", "rso_explain", "rso_expand"),
        )
        self.assertEqual(TOOL_NAMES, tool_names())

    def test_parser_exposes_mcp_without_serving(self):
        self.assertEqual(main(["mcp", "--status"]), 0)

    def test_strict_serve_without_root_fails_from_an_unbounded_directory(self):
        """Under --strict-roots the working directory must still be a bounded folder.

        The default open access starts from any directory instead (see
        McpSharedLedgerTests.test_open_access_serves_folders_outside_every_root).
        """
        original = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "context.sqlite3")
            os.chdir(Path.home())
            try:
                self.assertEqual(main(["--db", db, "mcp", "--strict-roots"]), 2)
                self.assertEqual(main(["mcp", "--db", db, "--strict-roots"]), 2)
            finally:
                os.chdir(original)

    def test_profile_refusal_survives_a_missing_path_home(self):
        """A partial env must keep the refusal, not lose it with Path.home()."""
        with tempfile.TemporaryDirectory() as profile:
            with patch.object(Path, "home", side_effect=RuntimeError("no home")):
                with patch.dict(os.environ, {"USERPROFILE": profile}, clear=False):
                    with self.assertRaises(ValueError):
                        resolve_launch_roots([profile])

    def test_db_default_is_not_resolved_while_building_the_parser(self):
        """default_db_path() needs a home; the parser is built before main() can report that."""
        with patch("rso_context.config.Path.home", side_effect=RuntimeError("no home")):
            with patch.dict(os.environ, {}, clear=True):
                parser = build_parser()
        self.assertIsNone(parser.parse_args(["mcp", "--status"]).db)

    def test_startup_failure_is_announced_on_stdout(self):
        """A stdio client watching stdout must get a reason, not silence it reads as a timeout."""
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            _emit_stdio_startup_failure(ValueError("root is not a directory"))
        frame = json.loads(buffer.getvalue().strip())
        self.assertEqual(frame["jsonrpc"], "2.0")
        self.assertIsNone(frame["id"])
        self.assertEqual(frame["error"]["code"], STDIO_STARTUP_ERROR_CODE)
        self.assertIn("root is not a directory", frame["error"]["message"])
        self.assertEqual(frame["error"]["data"]["type"], "ValueError")

    def test_path_must_stay_inside_launch_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            allowed = base / "project"
            other = base / "other"
            allowed.mkdir()
            other.mkdir()
            roots = bind_roots([str(allowed)])
            self.assertEqual(resolve_tool_path(allowed, roots), allowed.resolve())
            nested = allowed / "src"
            nested.mkdir()
            self.assertEqual(resolve_tool_path(nested, roots), nested.resolve())
            with self.assertRaises(ValueError):
                resolve_tool_path(other, roots)

    def test_launch_roots_default_to_the_working_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "bounded project"
            workspace.mkdir()
            bound = resolve_launch_roots(None, cwd=workspace)
            self.assertEqual([path.name for path in bound], ["bounded project"])

    def test_explicit_roots_win_over_the_working_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            chosen = Path(tmp) / "chosen"
            chosen.mkdir()
            elsewhere = Path(tmp) / "elsewhere"
            elsewhere.mkdir()
            bound = resolve_launch_roots([str(chosen)], cwd=elsewhere)
            self.assertEqual([path.name for path in bound], ["chosen"])

    def test_working_directory_fallback_still_refuses_a_user_profile(self):
        home = Path.home()
        with self.assertRaises(ValueError):
            resolve_launch_roots(None, cwd=home)
        with self.assertRaises(ValueError):
            resolve_launch_roots(None, cwd=home / "Documents")

    def test_open_launch_from_an_unbounded_directory_has_no_roots(self):
        """A desktop host launched from the profile must still get a server."""
        self.assertEqual(resolve_launch_roots(None, cwd=Path.home(), strict=False), [])
        with tempfile.TemporaryDirectory() as tmp:
            bound = resolve_launch_roots(None, cwd=tmp, strict=False)
            self.assertEqual(bound, [Path(tmp).resolve()])

    def test_user_profile_is_not_a_root(self):
        with self.assertRaises(ValueError):
            assert_bounded_root(Path.home())

    def test_broad_and_system_folders_are_not_roots(self):
        home = Path.home().resolve()
        refused = [Path(home.anchor), home.parent]
        for name in ("SystemRoot", "ProgramFiles"):
            if os.environ.get(name):
                refused.append(Path(os.environ[name]))
        if os.name != "nt":
            refused.append(Path("/etc"))
        for folder in refused:
            if folder.is_dir():
                with self.subTest(folder=str(folder)):
                    with self.assertRaises(ValueError):
                        assert_bounded_root(folder)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(assert_bounded_root(tmp), Path(tmp).resolve())

    def test_open_access_accepts_a_bounded_folder_outside_every_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            launched = base / "launched"
            other = base / "other project"
            launched.mkdir()
            other.mkdir()
            roots = bind_roots([str(launched)])
            self.assertEqual(
                resolve_workspace_path(other, roots, access=ACCESS_OPEN),
                other.resolve(),
            )
            self.assertEqual(resolve_workspace_path(other, [], access=ACCESS_OPEN), other.resolve())
            with self.assertRaises(ValueError):
                resolve_workspace_path(other, roots, access=ACCESS_STRICT)
            with self.assertRaises(ValueError):
                resolve_workspace_path(other, roots)

    def test_profile_refusal_survives_an_environment_without_profile_variables(self):
        """A host that strips USERPROFILE/HOME must not turn open access onto the profile."""
        home = Path.home().resolve()
        stripped = {
            key: value
            for key, value in os.environ.items()
            if key.upper() not in {"USERPROFILE", "HOME", "HOMEDRIVE", "HOMEPATH"}
        }
        with patch.dict(os.environ, stripped, clear=True):
            with patch.object(Path, "home", side_effect=RuntimeError("no home")):
                with self.assertRaises(ValueError):
                    resolve_workspace_path(home, [], access=ACCESS_OPEN)

    def test_another_accounts_profile_is_refused(self):
        home = Path.home().resolve()
        siblings = [item for item in home.parent.iterdir() if item.is_dir() and item != home][:3]
        for folder in siblings:
            with self.subTest(folder=str(folder)):
                with self.assertRaises(ValueError):
                    resolve_workspace_path(folder, [], access=ACCESS_OPEN)

    def test_open_access_still_refuses_profiles_and_broad_folders(self):
        home = Path.home().resolve()
        candidates = [home, home.parent, Path(home.anchor), home / "Documents", home / "Downloads"]
        with tempfile.TemporaryDirectory() as tmp:
            for folder in candidates:
                if not folder.is_dir():
                    continue
                with self.subTest(folder=str(folder)):
                    with self.assertRaises(ValueError):
                        resolve_workspace_path(folder, [], access=ACCESS_OPEN)
            ledger_home = Path(tmp) / "ledger"
            ledger_home.mkdir()
            with patch.dict(os.environ, {"RSO_CONTEXT_HOME": str(ledger_home)}):
                with self.assertRaises(ValueError):
                    resolve_workspace_path(ledger_home, [], access=ACCESS_OPEN)

    def test_workspace_path_rejects_files_and_the_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            project = base / "project"
            project.mkdir()
            ledger = base / "context.sqlite3"
            ledger.write_bytes(b"not a sqlite body")
            roots = bind_roots([str(project)])
            with self.assertRaises(ValueError):
                resolve_workspace_path(ledger, bind_roots([str(base)]), database_path=ledger)
            note = project / "policy.md"
            note.write_text("Decision: widgets must stay blue.\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                resolve_workspace_path(note, roots)


class McpExplainScopeTests(unittest.TestCase):
    def test_explain_refuses_packets_from_projects_outside_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            visible = base / "visible"
            hidden = base / "hidden"
            visible.mkdir()
            hidden.mkdir()
            (visible / "AGENTS.md").write_text("Decision: visible widgets must stay blue.\n", encoding="utf-8")
            (hidden / "AGENTS.md").write_text("Decision: hidden widgets must stay red.\n", encoding="utf-8")
            database = Database(base / "context.sqlite3")
            ingest_project(database, visible, agent="scope-test")
            ingest_project(database, hidden, agent="scope-test")
            packet = query_context(database, "hidden widgets", path=hidden, agent="scope-test")
            digest = str(packet["packet_hash"])
            with self.assertRaises(ValueError):
                explain_packet(database, digest, [visible.resolve()])
            explained = explain_packet(database, digest, [hidden.resolve()])
            self.assertEqual(explained["packet_hash"], digest)

    def test_query_and_explain_omit_sources_outside_launch_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            visible = base / "visible"
            shared = base / "shared-rules"
            visible.mkdir()
            shared.mkdir()
            (visible / "AGENTS.md").write_text("Decision: visible widgets must stay blue.\n", encoding="utf-8")
            (shared / "AGENTS.md").write_text("Decision: shared widgets must stay red.\n", encoding="utf-8")
            database = Database(base / "context.sqlite3")
            ingest_project(database, shared, agent="scope-test", scope="shared")
            ingest_project(database, visible, agent="scope-test")
            packet = query_context(database, "widgets", path=visible, agent="scope-test")
            paths = {item.get("relative_path") for item in packet["evidence"]}
            self.assertTrue(paths)
            explained = explain_packet(database, str(packet["packet_hash"]), [visible.resolve()])
            visible_root = visible.resolve()
            for item in explained["packet"]["evidence"]:
                resolved = Path(str(item.get("resolved_path")))
                self.assertTrue(resolved == visible_root or resolved.is_relative_to(visible_root))
            bounded = bound_packet(database, packet, [visible_root])
            for item in bounded["evidence"]:
                self.assertIn("blue", item.get("text") or "")
                self.assertNotIn("red", item.get("text") or "")
            for item in bounded.get("search_order") or []:
                self.assertNotEqual(item.get("scope"), "shared")


class McpHandshakeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.python = ensure_isolated_python()

    def test_isolated_runtime_reports_pinned_sdk(self):
        status = runtime_status()
        self.assertEqual(status["runtime_mcp_version"], "2.2.0")
        self.assertTrue(status["runtime_ready"])
        self.assertEqual(status["sdk_requirement"], "mcp==2.2.0")

    def test_stdio_initialize_lists_contract_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "bounded project"
            workspace.mkdir()
            (workspace / "AGENTS.md").write_text("Decision: widgets must stay blue.\n", encoding="utf-8")
            db = Path(tmp) / "context.sqlite3"
            probe = r"""
import json, os, sys
from pathlib import Path
import anyio
from mcp import Client, StdioServerParameters

db, root, src = sys.argv[1], sys.argv[2], sys.argv[3]
env = os.environ.copy()
env["PYTHONPATH"] = src
env["RSO_MCP_IN_RUNTIME"] = "1"
params = StdioServerParameters(
    command=sys.executable,
    args=["-X", "utf8", "-m", "rso_context", "mcp", "--db", db, "--root", root],
    env=env,
    cwd=str(Path(src).parent),
)

async def main():
    async with Client(params) as client:
        listed = await client.list_tools()
        tools = listed.tools if hasattr(listed, "tools") else listed
        names = [getattr(item, "name", item) for item in tools]
        info = getattr(client, "server_info", None)
        protocol = getattr(client, "protocol_version", None)
        print(json.dumps({
            "protocol_version": str(protocol),
            "server_name": getattr(info, "name", None) if info is not None else None,
            "tools": names,
        }, sort_keys=True))

anyio.run(main)
"""
            payload = _run_isolated(
                self.python, probe, str(db), str(workspace), str(ROOT / "src")
            )
            self.assertEqual(payload["tools"], list(TOOL_NAMES))
            self.assertTrue(payload["protocol_version"])
            self.assertEqual(payload["server_name"], "rso-context")

    def test_stdio_initialize_survives_a_spawn_env_without_a_home(self):
        """A client that replaces rather than augments the child env must still get a handshake.

        OpenCode spawns a stdio server with only the variables in its own config
        block. With no USERPROFILE/HOMEPATH, Path.home() raises RuntimeError on
        Windows; the server died before answering and the client reported it as
        "-32001 Request timed out" rather than as a crash. SystemRoot is kept
        because winsock cannot load without it, which is environmental and not
        something the adapter can fix.
        """
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "bounded project"
            workspace.mkdir()
            (workspace / "AGENTS.md").write_text("Decision: widgets must stay blue.\n", encoding="utf-8")
            env = {
                "PYTHONPATH": str(ROOT / "src"),
                "RSO_MCP_IN_RUNTIME": "1",
                "RSO_CONTEXT_HOME": str(Path(tmp) / "home"),
            }
            for name in ("SystemRoot", "SYSTEMROOT", "PATH"):
                value = os.environ.get(name)
                if value:
                    env[name] = value
            for name in ("HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH"):
                self.assertNotIn(name, env)
            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "homeless-spawn-probe", "version": "1"},
                },
            }
            process = subprocess.Popen(
                [
                    str(self.python),
                    "-X",
                    "utf8",
                    "-m",
                    "rso_context",
                    "mcp",
                    "--db",
                    str(Path(tmp) / "context.sqlite3"),
                    "--root",
                    str(workspace),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=env,
                cwd=str(ROOT),
            )
            try:
                stdout, stderr = process.communicate(json.dumps(request) + "\n", timeout=90)
            except subprocess.TimeoutExpired:
                process.kill()
                self.fail("server never answered initialize under a home-less spawn env")
            lines = [line for line in stdout.splitlines() if line.strip()]
            self.assertTrue(lines, f"no stdout frame; stderr was: {stderr}")
            payload = json.loads(lines[0])
            self.assertNotIn("error", payload, f"initialize failed: {stderr}")
            self.assertEqual(payload["result"]["serverInfo"]["name"], "rso-context")

    def test_query_wire_result_stays_within_byte_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "bounded"
            workspace.mkdir()
            body = "Decision: widgets must stay blue.\n" + ("widgets extra line\n" * 80)
            (workspace / "AGENTS.md").write_text(body, encoding="utf-8")
            db = Path(tmp) / "context.sqlite3"
            probe = r"""
import json, os, sys
from pathlib import Path
import anyio
from mcp import Client, StdioServerParameters

db, root, src = sys.argv[1], sys.argv[2], sys.argv[3]
budget = 4000
env = os.environ.copy()
env["PYTHONPATH"] = src
env["RSO_MCP_IN_RUNTIME"] = "1"
params = StdioServerParameters(
    command=sys.executable,
    args=["-X", "utf8", "-m", "rso_context", "mcp", "--db", db, "--root", root],
    env=env,
    cwd=str(Path(src).parent),
)

async def main():
    async with Client(params) as client:
        await client.call_tool("rso_use", {"path": root, "agent": "wire"})
        result = await client.call_tool(
            "rso_query",
            {"query": "widgets", "path": root, "agent": "wire", "byte_budget": budget},
        )
        dumped = result.model_dump(mode="json", by_alias=True, exclude_none=True)
        message = {"jsonrpc": "2.0", "id": 1, "result": dumped}
        wire = len(json.dumps(message, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))
        print(json.dumps({
            "wire_bytes": wire,
            "budget": budget,
            "has_structured": result.structured_content is not None,
            "is_error": bool(result.is_error),
        }, sort_keys=True))

anyio.run(main)
"""
            payload = _run_isolated(
                self.python, probe, str(db), str(workspace), str(ROOT / "src")
            )
            self.assertFalse(payload["is_error"])
            self.assertFalse(payload["has_structured"])
            self.assertLessEqual(payload["wire_bytes"], payload["budget"])


SHARED_LEDGER_PROBE = r"""
import asyncio, json, os, sys
from pathlib import Path
from mcp import Client, StdioServerParameters

db, root, src, mode = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
env = os.environ.copy()
env["PYTHONPATH"] = src
env["RSO_MCP_IN_RUNTIME"] = "1"
launch = ["-X", "utf8", "-m", "rso_context", "mcp", "--db", db, "--root", root]
launch_cwd = str(Path(src).parent)
if mode == "errors-strict":
    launch.append("--strict-roots")
if mode == "open":
    # A desktop host: no --root, started from the user profile.
    launch = ["-X", "utf8", "-m", "rso_context", "mcp", "--db", db]
    launch_cwd = str(Path.home())
params = StdioServerParameters(
    command=sys.executable,
    args=launch,
    env=env,
    cwd=launch_cwd,
)

def dump(result):
    text = ""
    content = getattr(result, "content", None) or []
    if content:
        text = getattr(content[0], "text", "") or ""
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict) and "schema" not in structured and isinstance(structured.get("result"), dict):
        structured = structured["result"]
    if structured is None and text.strip().startswith("{"):
        try:
            structured = json.loads(text)
        except json.JSONDecodeError:
            structured = None
    return {
        "is_error": bool(getattr(result, "is_error", False)),
        "structured": structured,
        "text": text,
    }

async def session():
    return Client(params, raise_exceptions=False)

async def call(name, arguments):
    async with await session() as client:
        return dump(await client.call_tool(name, arguments))

async def main():
    if mode == "two-client":
        used = await call("rso_use", {"path": root, "agent": "alpha"})
        alpha, beta = await asyncio.gather(
            call("rso_query", {"query": "widgets", "path": root, "agent": "alpha"}),
            call("rso_query", {"query": "widgets", "path": root, "agent": "beta"}),
        )
        resumed = await call("rso_resume", {"path": root, "agent": "beta"})
        print(json.dumps({"use": used, "alpha": alpha, "beta": beta, "resume": resumed}, sort_keys=True))
        return
    if mode == "open":
        used = await call("rso_use", {"path": root, "agent": "alpha"})
        queried = await call("rso_query", {"query": "widgets", "path": root, "agent": "alpha"})
        profile = await call("rso_use", {"path": str(Path.home()), "agent": "alpha"})
        print(json.dumps({"use": used, "query": queried, "profile": profile}, sort_keys=True))
        return
    if mode in ("errors", "errors-strict"):
        outside = str(Path(root).resolve().parent / "outside")
        print(json.dumps({
            "outside": await call("rso_query", {"query": "widgets", "path": outside, "agent": "alpha"}),
            "empty_agent": await call("rso_use", {"path": root, "agent": "  "}),
            "empty_query": await call("rso_query", {"query": " ", "path": root, "agent": "alpha"}),
            "resume_missing": await call("rso_resume", {"path": root, "agent": "alpha"}),
        }, sort_keys=True))
        return
    raise SystemExit("unknown mode")

asyncio.run(main())
"""


class McpSharedLedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.python = ensure_isolated_python()

    def test_two_clients_use_query_resume_on_one_scratch_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "bounded"
            workspace.mkdir()
            (workspace / "AGENTS.md").write_text("Decision: widgets must stay blue.\n", encoding="utf-8")
            db = Path(tmp) / "context.sqlite3"
            payload = _run_isolated(
                self.python,
                SHARED_LEDGER_PROBE,
                str(db),
                str(workspace),
                str(ROOT / "src"),
                "two-client",
                timeout=120,
            )
            for key in ("use", "alpha", "beta", "resume"):
                self.assertFalse(payload[key]["is_error"], payload[key])
            used = payload["use"]["structured"]
            self.assertEqual(used["schema"], "rso-context-use/v1")
            self.assertIn("resume", used)
            alpha = payload["alpha"]["structured"]
            beta = payload["beta"]["structured"]
            self.assertEqual(alpha["schema"], "rso-mcp-packet/v1")
            self.assertEqual(beta["schema"], "rso-mcp-packet/v1")
            self.assertEqual(alpha["source_schema"], "rso-context-packet/v2")
            self.assertEqual(alpha["requirements"][0]["status"], "evidence_found")
            self.assertEqual(beta["requirements"][0]["status"], "evidence_found")
            self.assertIn("blue", alpha["evidence"][0]["text"])
            self.assertEqual(alpha["project"]["id"], beta["project"]["id"])
            self.assertIsNone(alpha["run"]["remaining"])
            self.assertIsNone(beta["run"]["remaining"])
            resumed = payload["resume"]["structured"]
            self.assertEqual(resumed["schema"], "rso-context-resume/v1")
            self.assertEqual(resumed["project"]["id"], alpha["project"]["id"])
            cli = query_context(Database(db), "widgets", path=workspace, agent="gamma")
            self.assertEqual(cli["schema"], "rso-context-packet/v2")
            self.assertEqual(cli["project"]["id"], alpha["project"]["id"])
            self.assertEqual(cli["requirements"][0]["status"], "evidence_found")
            self.assertIsNone(cli["run"]["remaining"])

    def test_tool_errors_do_not_crash_the_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "bounded"
            outside = Path(tmp) / "outside"
            workspace.mkdir()
            outside.mkdir()
            (workspace / "AGENTS.md").write_text("Decision: widgets must stay blue.\n", encoding="utf-8")
            db = Path(tmp) / "context.sqlite3"
            payload = _run_isolated(
                self.python,
                SHARED_LEDGER_PROBE,
                str(db),
                str(workspace),
                str(ROOT / "src"),
                "errors",
            )
            for key in ("outside", "empty_agent", "empty_query", "resume_missing"):
                self.assertTrue(payload[key]["is_error"], payload[key])
            # Open access reaches the outside folder; it is simply not registered yet.
            self.assertIn("No registered project", payload["outside"]["text"])
            self.assertIn("agent is required", payload["empty_agent"]["text"])
            self.assertIn("query is required", payload["empty_query"]["text"])
            self.assertIn("No registered project", payload["resume_missing"]["text"])
            strict = _run_isolated(
                self.python,
                SHARED_LEDGER_PROBE,
                str(db),
                str(workspace),
                str(ROOT / "src"),
                "errors-strict",
            )
            self.assertTrue(strict["outside"]["is_error"], strict["outside"])
            self.assertIn("outside the server's allowed roots", strict["outside"]["text"])

    def test_open_access_serves_folders_outside_every_root(self):
        """A host that starts the server with no --root from the profile still works.

        This is how Claude Desktop and other app hosts launch stdio servers. The
        server must start, serve the project folder the agent names, and still
        refuse the profile itself.
        """
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "any project"
            workspace.mkdir()
            (workspace / "AGENTS.md").write_text("Decision: widgets must stay blue.\n", encoding="utf-8")
            db = Path(tmp) / "context.sqlite3"
            payload = _run_isolated(
                self.python,
                SHARED_LEDGER_PROBE,
                str(db),
                str(workspace),
                str(ROOT / "src"),
                "open",
                timeout=120,
            )
            self.assertFalse(payload["use"]["is_error"], payload["use"])
            self.assertFalse(payload["query"]["is_error"], payload["query"])
            query = payload["query"]["structured"]
            self.assertEqual(query["requirements"][0]["status"], "evidence_found")
            self.assertIn("blue", query["evidence"][0]["text"])
            self.assertTrue(payload["profile"]["is_error"], payload["profile"])
            self.assertIn("user profile", payload["profile"]["text"])


class McpStatusTests(unittest.TestCase):
    def test_status_is_json_and_does_not_require_sdk_2(self):
        status = runtime_status()
        self.assertEqual(status["schema"], "rso-mcp-runtime/v1")
        self.assertEqual(status["sdk_requirement"], "mcp==2.2.0")
        current = current_sdk_status()
        self.assertIn("usable", current)


class WireFitTests(unittest.TestCase):
    """Fitting needs the MCP SDK types, so it runs in the isolated runtime."""

    @classmethod
    def setUpClass(cls):
        cls.python = ensure_isolated_python()

    def test_fit_finds_the_largest_packet_when_sizes_move_in_big_steps(self):
        """A packet a few bytes over used to collapse to an empty 256-byte result."""
        probe = r"""
import json
from rso_context.mcp_server import fit_to_wire_budget, tool_result_wire_bytes

def build(inner):
    # Evidence arrives in whole 1500-byte spans, like real plates.
    spans = max(0, (inner - 300) // 1500)
    return {"schema": "probe", "evidence": ["x" * 1500] * spans, "inner": inner}

base = tool_result_wire_bytes(build(12000))
budget = base - 15
fitted = fit_to_wire_budget(build, budget)
print(json.dumps({
    "full_spans": len(build(12000)["evidence"]),
    "budget": budget,
    "spans": len(fitted["evidence"]),
    "size": tool_result_wire_bytes(fitted),
}))
"""
        payload = _run_isolated(self.python, probe)
        self.assertLessEqual(payload["size"], payload["budget"])
        self.assertEqual(payload["spans"], payload["full_spans"] - 1)


if __name__ == "__main__":
    unittest.main()
