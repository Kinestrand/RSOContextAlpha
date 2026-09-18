from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from rso_context.audit import record_validation
from rso_context.check import (
    CHECK_SCHEMA,
    MAX_QUESTIONS,
    check_questions,
    explain_check,
    fit_check,
    normalize_questions,
)
from rso_context.cli import main
from rso_context.compact import serialized_bytes
from rso_context.db import Database
from rso_context.ingest import ingest_project
from rso_context.mcp_server import explain_packet
from rso_context.run_budget import get_or_start

from test_mcp_adapter import ROOT, _run_isolated, ensure_isolated_python


def _keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in _keys(item)}
    return set()


class CheckTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.database = Database(Path(self.temp.name) / "context.sqlite3")

    def write(self, relative: str, body: str) -> None:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")

    def ingest(self) -> None:
        if not (self.root / "AGENTS.md").exists():
            self.write("AGENTS.md", "# Fixture\n")
        ingest_project(self.database, self.root, agent="check-test")

    def check(self, *questions: dict, **kwargs) -> dict:
        return check_questions(self.database, list(questions), path=self.root, agent="check-test", **kwargs)

    def answer(self, *questions: dict) -> dict:
        return self.check(*questions)["answers"][0]


class ClaimTests(CheckTestCase):
    def test_supported_contradicted_and_unknown(self):
        self.write("spec.md", "Decision: export the preview at 24 fps.\nDo not upload previews to public buckets.\n")
        self.ingest()
        supported = self.answer({"id": "a", "type": "claim", "text": "Preview export is 24 fps"})
        self.assertEqual((supported["answer"], supported["value"]), ("supported", True))
        self.assertEqual(supported["evidence"][0]["relative_path"], "spec.md")
        self.assertEqual(supported["evidence"][0]["line_start"], 1)
        self.assertEqual(supported["evidence"][0]["expand"]["schema"], "rso-expand-ref/v1")
        contradicted = self.answer({"id": "b", "type": "claim", "text": "Upload previews to public buckets"})
        self.assertEqual((contradicted["answer"], contradicted["value"]), ("contradicted", False))
        self.assertEqual(contradicted["counter_evidence"][0]["polarity"], "negated")
        unknown = self.answer({"id": "c", "type": "claim", "text": "Audio mixes use Dolby Atmos"})
        self.assertEqual((unknown["answer"], unknown["value"]), ("unknown", None))

    def test_numbers_must_match(self):
        self.write("spec.md", "Decision: export the preview at 30 fps.\n")
        self.ingest()
        result = self.answer({"id": "fps", "type": "claim", "text": "Preview export is 24 fps"})
        self.assertEqual(result["answer"], "contradicted")
        self.assertEqual(result["counter_evidence"][0]["reason"], "number_mismatch")
        self.assertEqual(result["evidence"], [])

    def test_comma_grouped_numbers(self):
        self.write("spec.md", "Decision: the upload size limit is 12,000 bytes.\n")
        self.ingest()
        claim = self.answer({"id": "a", "type": "claim", "text": "The upload size limit is 12000 bytes"})
        self.assertEqual(claim["answer"], "supported")
        value = self.answer({"id": "b", "type": "value", "text": "upload size limit", "unit": "bytes"})
        self.assertEqual((value["answer"], value["value"]), ("found", 12000))

    def test_disagreement_across_paths_and_same_path_flag(self):
        self.write("a.md", "Decision: export the preview at 24 fps.\n")
        self.write("b.md", "Do not export the preview at 24 fps.\n")
        self.ingest()
        result = self.answer({"id": "fps", "type": "claim", "text": "Preview export is 24 fps"})
        self.assertEqual(result["answer"], "disagreement")
        self.assertFalse(result["same_path"])
        self.assertEqual({item["relative_path"] for item in result["evidence"]}, {"a.md"})
        self.assertEqual({item["relative_path"] for item in result["counter_evidence"]}, {"b.md"})

    def test_same_file_contradiction_is_flagged(self):
        self.write("spec.md", "Decision: export the preview at 24 fps.\nDo not export the preview at 24 fps.\n")
        self.ingest()
        result = self.answer({"id": "fps", "type": "claim", "text": "Preview export is 24 fps"})
        self.assertEqual(result["answer"], "disagreement")
        self.assertTrue(result["same_path"])


class SentenceAssemblyTests(CheckTestCase):
    def test_sentence_wrapped_across_lines_is_read_as_one(self):
        self.write("spec.md", "# Spec\n\nDecision: export the preview\nat 24 fps for all reels.\n")
        self.ingest()
        result = self.answer({"id": "fps", "type": "claim", "text": "Preview export is 24 fps"})
        self.assertEqual(result["answer"], "supported")
        evidence = result["evidence"][0]
        self.assertIn("export the preview at 24 fps", evidence["text"])
        self.assertEqual((evidence["line_start"], evidence["line_end"]), (3, 4))
        self.assertEqual(evidence["expand"]["line_start"], 3)
        self.assertEqual(evidence["expand"]["line_end"], 4)

    def test_list_items_and_table_rows_are_not_merged(self):
        self.write(
            "spec.md",
            "# Spec\n\n- Preview export uses Cycles\n- Preview export uses Eevee\n\n| a | b |\n",
        )
        self.ingest()
        result = self.answer(
            {
                "id": "r",
                "type": "choice",
                "text": "Which renderer does preview export use?",
                "options": ["Cycles", "Eevee"],
            }
        )
        # Two separate list items, so two competing options, not one merged sentence.
        self.assertEqual(result["answer"], "disagreement")
        self.assertEqual(
            {(item["line_start"], item["line_end"]) for item in result["evidence"]}, {(3, 3), (4, 4)}
        )

    def test_recorded_questions_are_not_evidence(self):
        self.write("spec.md", "# Spec\n\nSomeone asked whether preview export is 24 fps.\n")
        self.ingest()
        claim = self.answer({"id": "fps", "type": "claim", "text": "Preview export is 24 fps"})
        self.assertEqual(claim["answer"], "unknown")
        self.write("spec.md", "# Spec\n\nShould we set the preview export frame rate to 60 fps?\n")
        self.ingest()
        value = self.answer({"id": "v", "type": "value", "text": "preview export frame rate", "unit": "fps"})
        self.assertEqual(value["answer"], "unknown")

    def test_plain_not_is_negation_for_query_and_check_alike(self):
        from rso_context.query import texts_disagree

        self.write("a.md", "Decision: preview export must be 24 fps.\n")
        self.write("b.md", "Preview export is not 24 fps.\n")
        self.ingest()
        result = self.answer({"id": "fps", "type": "claim", "text": "Preview export is 24 fps"})
        self.assertEqual(result["answer"], "disagreement")
        self.assertTrue(
            texts_disagree(
                ["Decision: preview export must be 24 fps.", "Preview export is not 24 fps."],
                ["a.md", "b.md"],
            )
        )


class ChoiceTests(CheckTestCase):
    QUESTION = {
        "id": "renderer",
        "type": "choice",
        "text": "Which renderer do we use for finals?",
        "options": ["Cycles", "Eevee", "Arnold"],
        "aliases": {"Cycles": ["path tracer"]},
    }

    def test_alias_selects_its_option_and_records_match(self):
        self.write("spec.md", "Decision: render finals with the path tracer, not Arnold.\n")
        self.ingest()
        result = self.answer(self.QUESTION)
        self.assertEqual((result["answer"], result["value"]), ("selected", "Cycles"))
        self.assertEqual(result["evidence"][0]["matched"], "path tracer")
        self.assertEqual(result["excluded"], ["Arnold"])

    def test_two_supported_options_are_a_disagreement_not_a_rank_pick(self):
        self.write("a.md", "Decision: render finals with Cycles.\n" * 3)
        self.write("b.md", "Decision: render finals with Eevee.\n")
        self.ingest()
        result = self.answer(self.QUESTION)
        self.assertEqual(result["answer"], "disagreement")
        self.assertIsNone(result["value"])

    def test_only_negated_mentions_are_excluded(self):
        self.write("spec.md", "Never render finals with Arnold.\n")
        self.ingest()
        result = self.answer(self.QUESTION)
        self.assertEqual((result["answer"], result["value"]), ("excluded", None))
        self.assertEqual(result["excluded"], ["Arnold"])

    def test_shared_alias_is_rejected(self):
        bad = dict(self.QUESTION, aliases={"Cycles": ["gpu"], "Eevee": ["GPU"]})
        with self.assertRaisesRegex(ValueError, "used by both"):
            normalize_questions([bad])


class ValueTests(CheckTestCase):
    def test_unit_value_found_and_disagreement(self):
        self.write("a.md", "The preview export frame rate is 24 fps.\n")
        self.ingest()
        question = {"id": "rate", "type": "value", "text": "preview export frame rate", "unit": "fps"}
        found = self.answer(question)
        self.assertEqual((found["answer"], found["value"]), ("found", 24))
        self.write("b.md", "The preview export frame rate is 30 fps.\n")
        self.ingest()
        split = self.answer(question)
        self.assertEqual(split["answer"], "disagreement")
        self.assertEqual(sorted(split["values"]), [24, 30])

    def test_negated_sentences_give_no_value(self):
        self.write("a.md", "Do not set the preview export frame rate to 60 fps.\n")
        self.ingest()
        result = self.answer({"id": "rate", "type": "value", "text": "preview export frame rate", "unit": "fps"})
        self.assertEqual(result["answer"], "unknown")

    def test_pattern_value_and_guard(self):
        self.write("a.md", "The render engine version is 4.2 LTS.\n")
        self.ingest()
        result = self.answer(
            {"id": "v", "type": "value", "text": "render engine version", "pattern": r"version is ([\d.]+ ?LTS)"}
        )
        self.assertEqual((result["answer"], result["value"]), ("found", "4.2 LTS"))
        with self.assertRaisesRegex(ValueError, "nested quantifier"):
            normalize_questions([{"id": "x", "type": "value", "text": "t", "pattern": r"(a+)+b"}])
        with self.assertRaisesRegex(ValueError, "capture group"):
            normalize_questions([{"id": "x", "type": "value", "text": "t", "pattern": r"\d+"}])


class ContractTests(CheckTestCase):
    def test_question_validation(self):
        with self.assertRaisesRegex(ValueError, "at most"):
            normalize_questions([{"id": str(n), "type": "claim", "text": "x"} for n in range(MAX_QUESTIONS + 1)])
        with self.assertRaisesRegex(ValueError, "unknown keys"):
            normalize_questions([{"id": "a", "type": "claim", "text": "x", "confidence": 0.9}])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            normalize_questions([{"id": "a", "type": "claim", "text": "x"}] * 2)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            normalize_questions([{"id": "a", "type": "value", "text": "x"}])

    def test_twelve_questions_cost_one_budget_unit(self):
        self.write("spec.md", "Decision: export the preview at 24 fps.\n")
        self.ingest()
        questions = [{"id": str(n), "type": "claim", "text": "Preview export is 24 fps"} for n in range(12)]
        result = self.check(*questions)
        self.assertEqual(result["run"]["initial"] - result["run"]["remaining"], 1)
        self.assertEqual(len(result["answers"]), 12)

    def test_no_probability_like_fields(self):
        self.write("spec.md", "Decision: export the preview at 24 fps.\n")
        self.ingest()
        result = self.check(
            {"id": "a", "type": "claim", "text": "Preview export is 24 fps"},
            {"id": "b", "type": "value", "text": "preview export", "unit": "fps"},
        )
        forbidden = {"probability", "probabilities", "confidence", "score", "likelihood", "p"}
        self.assertFalse(_keys(result) & forbidden)
        self.assertFalse(_keys(fit_check(result, 600)) & forbidden)

    def test_checks_never_change_trust_or_validations(self):
        self.write("spec.md", "Decision: export the preview at 24 fps.\n")
        self.ingest()
        with self.database.connect() as connection:
            before = connection.execute("SELECT id,trust_state FROM claims ORDER BY id").fetchall()
            validations_before = connection.execute("SELECT COUNT(*) FROM validations").fetchone()[0]
        self.check({"id": "a", "type": "claim", "text": "Preview export is 24 fps"})
        with self.database.connect() as connection:
            after = connection.execute("SELECT id,trust_state FROM claims ORDER BY id").fetchall()
            validations_after = connection.execute("SELECT COUNT(*) FROM validations").fetchone()[0]
        self.assertEqual([tuple(row) for row in before], [tuple(row) for row in after])
        self.assertEqual(validations_before, validations_after)

    def test_named_validation_is_shown_not_used_to_override(self):
        self.write("spec.md", "Decision: export the preview at 24 fps.\n")
        self.ingest()
        with self.database.connect() as connection:
            claim = connection.execute(
                "SELECT id FROM claims WHERE display_text LIKE '%24 fps%' LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(claim, "fixture should produce a policy-like claim")
        record_validation(self.database, claim["id"], validator="owner", result="disputed")
        result = self.answer({"id": "a", "type": "claim", "text": "Preview export is 24 fps"})
        self.assertEqual(result["answer"], "supported")
        self.assertEqual(result["basis"], "validated")
        self.assertEqual(result["validations"][0]["result"], "disputed")

    def test_stale_sources_are_ignored_and_explain_marks_them(self):
        self.write("spec.md", "Decision: export the preview at 24 fps.\n")
        self.ingest()
        result = self.check({"id": "a", "type": "claim", "text": "Preview export is 24 fps"})
        self.assertEqual(result["answers"][0]["answer"], "supported")
        self.write("spec.md", "Decision: export the preview at 25 fps.\n")
        stale = self.answer({"id": "a", "type": "claim", "text": "Preview export is 24 fps"})
        self.assertEqual(stale["answer"], "unknown")
        self.ingest()
        with self.database.connect() as connection:
            explained = explain_check(connection, result)
        self.assertEqual(explained["stale_evidence"], 1)
        self.assertTrue(explained["answers"][0]["evidence"][0]["stale"])

    def test_explain_via_mcp_and_roots(self):
        self.write("spec.md", "Decision: export the preview at 24 fps.\n")
        self.ingest()
        result = self.check({"id": "a", "type": "claim", "text": "Preview export is 24 fps"})
        explained = explain_packet(self.database, result["check_hash"], [self.root.resolve()])
        self.assertEqual(explained["packet"]["schema"], CHECK_SCHEMA)
        self.assertEqual(explained["packet"]["answers"][0]["answer"], "supported")
        outside = Path(self.temp.name) / "elsewhere"
        outside.mkdir()
        with self.assertRaises(ValueError):
            explain_packet(self.database, result["check_hash"], [outside.resolve()])


class RootIsolationTests(unittest.TestCase):
    def test_out_of_root_projects_do_not_surface_in_check_or_explain(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            visible = base / "visible"
            shared = base / "hidden-shared-rules"
            visible.mkdir()
            shared.mkdir()
            (visible / "AGENTS.md").write_text("Decision: export the preview at 24 fps.\n", encoding="utf-8")
            (shared / "AGENTS.md").write_text("Decision: export the preview at 30 fps.\n", encoding="utf-8")
            database = Database(base / "context.sqlite3")
            ingest_project(database, shared, agent="scope-test", scope="shared")
            ingest_project(database, visible, agent="scope-test")
            question = {"id": "fps", "type": "claim", "text": "Preview export is 24 fps"}
            unbounded = check_questions(database, [question], path=visible, agent="scope-test")
            shared_id = next(
                item["id"] for item in unbounded["search_order"] if item["scope"] == "shared"
            )
            self.assertEqual(unbounded["answers"][0]["answer"], "disagreement")

            bounded = check_questions(
                database, [question], path=visible, agent="scope-test", roots=[visible.resolve()]
            )
            dumped = json.dumps(bounded)
            self.assertEqual(bounded["answers"][0]["answer"], "supported")
            self.assertNotIn(shared_id, dumped)
            self.assertNotIn("hidden-shared-rules", dumped)
            self.assertTrue(all(item["scope"] != "shared" for item in bounded["search_order"]))

            explained = explain_packet(database, unbounded["check_hash"], [visible.resolve()])
            self.assertNotIn(shared_id, json.dumps(explained["packet"]))
            self.assertEqual(explained["packet"]["answers"][0]["counter_evidence"], [])
            self.assertTrue(explained["packet"]["answers"][0]["restricted"])


class BudgetTests(CheckTestCase):
    def test_budget_keeps_witness_then_reports_insufficient(self):
        self.write("a.md", "".join(f"Decision: export the preview at 24 fps for reel {n}.\n" for n in range(6)))
        self.write("b.md", "".join(f"Do not export the preview at 24 fps for reel {n}.\n" for n in range(6)))
        self.ingest()
        full = self.check({"id": "fps", "type": "claim", "text": "Preview export is 24 fps"})
        answer = full["answers"][0]
        self.assertEqual(answer["answer"], "disagreement")
        self.assertGreater(len(answer["evidence"]), 1)
        full_size = serialized_bytes(fit_check(full, 1_000_000))
        last_ok = None
        for budget in range(full_size, 256, -100):
            fitted = fit_check(full, budget)
            self.assertLessEqual(fitted["byte_count"], budget)
            if fitted["status"] != "ok":
                break
            kept = fitted["answers"][0]
            # An ok packet always shows both sides of the disagreement.
            self.assertGreaterEqual(len(kept["evidence"]), 1)
            self.assertGreaterEqual(len(kept["counter_evidence"]), 1)
            last_ok = fitted
        self.assertIsNotNone(last_ok)
        kept = last_ok["answers"][0]
        self.assertEqual((len(kept["evidence"]), len(kept["counter_evidence"])), (1, 1))
        self.assertTrue(last_ok["omitted"])
        self.assertTrue(all(item["expand"]["schema"] == "rso-expand-ref/v1" for item in last_ok["omitted"]))
        for budget in (1500, 900, 256):
            tight = fit_check(full, budget)
            self.assertLessEqual(serialized_bytes(tight), budget)
            self.assertEqual(tight["status"], "insufficient_budget")


class CliTests(CheckTestCase):
    def test_cli_check_and_explain(self):
        self.write("spec.md", "Decision: export the preview at 24 fps.\n")
        self.ingest()
        questions = Path(self.temp.name) / "questions.json"
        questions.write_text(
            json.dumps([{"id": "a", "type": "claim", "text": "Preview export is 24 fps"}]), encoding="utf-8"
        )
        base = ["--db", str(self.database.path)]
        out = io.StringIO()
        with redirect_stdout(out):
            code = main([*base, "check", "--questions", str(questions), "--path", str(self.root), "--agent", "cli"])
        self.assertEqual(code, 0)
        result = json.loads(out.getvalue())
        self.assertEqual(result["answers"][0]["answer"], "supported")
        out = io.StringIO()
        with redirect_stdout(out):
            main([*base, "explain", result["check_hash"]])
        explained = json.loads(out.getvalue())
        self.assertEqual(explained["packet"]["stale_evidence"], 0)


CHECK_PROBE = r"""
import json, os, sys
from pathlib import Path
import anyio
from mcp import Client, StdioServerParameters

db, root, src = sys.argv[1], sys.argv[2], sys.argv[3]
budget = 3000
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
        questions = [
            {"id": "fps", "type": "claim", "text": "Preview export is 24 fps"},
            {"id": "rate", "type": "value", "text": "preview export", "unit": "fps"},
        ]
        result = await client.call_tool(
            "rso_check", {"questions": questions, "path": root, "agent": "wire", "byte_budget": budget}
        )
        dumped = result.model_dump(mode="json", by_alias=True, exclude_none=True)
        message = {"jsonrpc": "2.0", "id": 1, "result": dumped}
        wire = len(json.dumps(message, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))
        payload = json.loads(result.content[0].text)
        print(json.dumps({
            "wire_bytes": wire,
            "budget": budget,
            "is_error": bool(result.is_error),
            "answers": {item["id"]: item["answer"] for item in payload["answers"]},
        }, sort_keys=True))

anyio.run(main)
"""


class McpCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.python = ensure_isolated_python()

    def test_rso_check_over_stdio_respects_wire_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "bounded"
            workspace.mkdir()
            (workspace / "AGENTS.md").write_text("Decision: export the preview at 24 fps.\n", encoding="utf-8")
            payload = _run_isolated(
                self.python, CHECK_PROBE, str(Path(tmp) / "context.sqlite3"), str(workspace), str(ROOT / "src")
            )
            self.assertFalse(payload["is_error"])
            self.assertLessEqual(payload["wire_bytes"], payload["budget"])
            self.assertEqual(payload["answers"], {"fps": "supported", "rate": "found"})


if __name__ == "__main__":
    unittest.main()
