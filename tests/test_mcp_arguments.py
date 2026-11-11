from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rso_context.audit import record_validation
from rso_context.db import Database

from tests.test_mcp_adapter import ROOT, _run_isolated, ensure_isolated_python

PROBE = r"""
import json, sqlite3, sys
from pathlib import Path
import anyio
from mcp import Client
from rso_context.mcp_server import build_server

db, root, other = sys.argv[1], sys.argv[2], sys.argv[3]
CLAIM = "Decision: widgets must stay blue."

async def main():
    out = {}
    async with Client(build_server(db_path=db, roots=[root])) as client:
        async def call(name, args):
            result = await client.call_tool(name, args)
            text = " ".join(getattr(c, "text", "") for c in result.content)
            return {"is_error": bool(result.is_error), "text": text}

        base = {"path": root, "agent": "Codex"}
        await call("rso_use", base)
        q = {"query": "widgets", **base}
        out["default"] = await call("rso_query", q)
        for name, extra in {
            "neg_budget": {"byte_budget": -5}, "limit0": {"limit": 0}, "limit_neg": {"limit": -1},
            "huge_budget": {"byte_budget": 10**9}, "tokens0": {"token_budget": 0},
        }.items():
            out[name] = await call("rso_query", {**q, **extra})
        out["check_neg"] = await call("rso_check", {"questions": [], "byte_budget": -5, **base})
        out["expand_huge"] = await call("rso_expand", {"ref": "x", "max_bytes": 10**9})
        schema = {t.name: t.input_schema for t in (await client.list_tools()).tools}
        out["schema_byte"] = schema["rso_query"]["properties"]["byte_budget"]

        v = {"claim": CLAIM, "path": root, "result": "verified", "agent": "Codex"}
        out["self"] = await call("rso_validate", {**v, "validator": "  CODEX ", "evidence": "x"})
        out["blank"] = await call("rso_validate", {**v, "validator": "  ", "evidence": "x"})
        out["no_evidence"] = await call("rso_validate", {**v, "validator": "owner review"})
        out["ok"] = await call("rso_validate", {**v, "validator": "owner review", "evidence": "read by owner"})
        for i, name in enumerate(["codex (self)", "Codex-CLI", "cod​ex", "ｃｏｄｅｘ"]):
            out["alike%d" % i] = await call("rso_validate", {**v, "agent": "codex", "validator": name, "evidence": "x"})
        out["paul"] = await call("rso_validate", {**v, "agent": "codex", "validator": "Paul", "evidence": "x"})
        out["forged"] = await call("rso_validate", {**v, "agent": "codex", "validator": "Paul", "evidence": "x",
                                                    "details": {"recorded_by": "Paul"}})
        out["blank_ev"] = await call("rso_validate", {**v, "agent": "codex", "validator": "Paul", "evidence": "real",
                                                      "details": {"evidence": "   "}})
        out["resume_big"] = await call("rso_resume", {**base, "limit": 10**9})
    async with Client(build_server(db_path=db, roots=[other])) as c2:
        await c2.call_tool("rso_use", {"path": other, "agent": "Codex"})
    connection = sqlite3.connect(db)
    foreign = [r[0] for r in connection.execute("SELECT id FROM claims WHERE display_text LIKE '%gadgets%'")][0]
    async with Client(build_server(db_path=db, roots=[root], strict_roots=True)) as c3:
        for key, cid in (("unknown_id", "deadbeefdeadbeef"), ("foreign_id", foreign), ("prefix_id", foreign[:8])):
            r = await c3.call_tool("rso_validate", {"claim": cid, "result": "verified", "agent": "Codex",
                                                    "validator": "owner review", "evidence": "x"})
            out[key] = {"is_error": bool(r.is_error), "text": " ".join(getattr(c, "text", "") for c in r.content)}
    print(json.dumps(out))

anyio.run(main)
"""


class McpArgumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        python = ensure_isolated_python()
        cls.tmp = tempfile.TemporaryDirectory()
        workspace = Path(cls.tmp.name) / "bounded"
        workspace.mkdir()
        (workspace / "AGENTS.md").write_text("Decision: widgets must stay blue.\n", encoding="utf-8")
        db = Path(cls.tmp.name) / "context.sqlite3"
        other = Path(cls.tmp.name) / "other"
        other.mkdir()
        (other / "AGENTS.md").write_text("Decision: gadgets must stay red.\n", encoding="utf-8")
        cls.out = _run_isolated(python, PROBE, str(db), str(workspace), str(other), timeout=180)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def refused(self, key: str, *needles: str):
        item = self.out[key]
        self.assertTrue(item["is_error"], item)
        for needle in needles:
            self.assertIn(needle, item["text"], item)

    def test_defaults_are_accepted(self):
        self.assertFalse(self.out["default"]["is_error"], self.out["default"])

    def test_bad_numbers_are_refused_with_the_range(self):
        self.refused("neg_budget", "512")
        self.refused("huge_budget", "65536")
        self.refused("limit0", "1")
        self.refused("limit_neg", "1")
        self.refused("tokens0", "1")
        self.refused("check_neg", "512")
        self.refused("expand_huge", "65536")

    def test_schema_shows_the_range(self):
        self.assertEqual(
            (self.out["schema_byte"].get("minimum"), self.out["schema_byte"].get("maximum")), (512, 65536)
        )

    def test_agent_cannot_validate_its_own_claim(self):
        self.refused("self", "not the calling agent")
        self.refused("blank", "must name the person")

    def test_look_alike_validators_are_refused_and_real_ones_accepted(self):
        for i in range(4):
            self.refused("alike%d" % i, "not the calling agent")
        self.assertFalse(self.out["paul"]["is_error"], self.out["paul"])

    def test_recorded_by_cannot_be_forged(self):
        item = self.out["forged"]
        self.assertFalse(item["is_error"], item)
        self.assertIn("codex", item["text"])
        self.assertNotIn('"recorded_by": "Paul"', item["text"].replace('":"', '": "'))

    def test_evidence_argument_replaces_a_blank_details_value(self):
        self.assertFalse(self.out["blank_ev"]["is_error"], self.out["blank_ev"])

    def test_unknown_and_invisible_claim_ids_read_the_same(self):
        message = "Claim is not in a project visible to this server"
        for key in ("unknown_id", "foreign_id", "prefix_id"):
            self.refused(key, message)
        self.assertEqual(self.out["unknown_id"]["text"], self.out["foreign_id"]["text"])

    def test_resume_limit_is_bounded(self):
        self.refused("resume_big", "50")

    def test_record_validation_itself_refuses_the_agent_as_validator(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Database(Path(tmp) / "c.sqlite3")
            database.initialize()
            with self.assertRaises(ValueError):
                record_validation(database, "x", validator="Codex-CLI", result="verified", agent="codex")

    def test_verified_needs_evidence(self):
        self.refused("no_evidence", "evidence is required")

    def test_named_validator_with_evidence_records_the_agent(self):
        item = self.out["ok"]
        self.assertFalse(item["is_error"], item)
        self.assertIn("owner review", item["text"])
        self.assertIn('"recorded_by": "Codex"', item["text"].replace('":"', '": "'))


if __name__ == "__main__":
    unittest.main()
