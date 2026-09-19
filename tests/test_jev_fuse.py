from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from rso_context import jev_fuse
from rso_context.cli import main
from rso_context.jev_fuse import (
    ACCEPT_FLOOR,
    FUSE_SCHEMA,
    MAX_STATE_CHARS,
    build_packet,
    build_questions,
    decide,
    get_key,
    is_dirty,
    run_fuse,
)

FAKE_KEY = "test-placeholder-not-a-key"


def span(path: str, line: int, text: str, **extra) -> dict:
    return {"relative_path": path, "line_start": line, "line_end": line, "text": text, **extra}


def document(*answers: dict) -> dict:
    return {"schema": "rso-check/v1", "answers": list(answers)}


def answer(qid: str, status: str, evidence=(), counter=(), value=None) -> dict:
    return {
        "id": qid,
        "type": "claim",
        "answer": status,
        "value": value,
        "evidence": list(evidence),
        "counter_evidence": list(counter),
    }


CLEAN = document(answer("fps", "supported", [span("spec.md", 3, "Decision: export at 24 fps.")], value=True))
DISAGREE = document(
    answer(
        "fps",
        "disagreement",
        [span("a.md", 1, "Decision: export at 24 fps.")],
        [span("b.md", 1, "Do not export at 24 fps.", polarity="negated")],
    )
)
CONTRADICTED = document(
    answer("fps", "contradicted", [], [span("b.md", 1, "Export at 30 fps.", reason="number_mismatch")], value=False)
)


def jev_answers(**overrides) -> dict:
    base = {
        "same_claim": {"type": "noul", "noul": 0.95},
        "evidence_gap": {"type": "noul", "noul": 0.10},
        "residual_ok": {"type": "noul", "noul": 0.95},
        "blame": {"type": "choice", "choice": "other"},
        "next": {"type": "choice", "choice": "accept"},
        "severity": {"type": "score", "score": 0.4},
    }
    base.update(overrides)
    return base


class Recorder:
    """Stands in for the HTTP call and records what would have been sent."""

    def __init__(self, response: dict | None = None, error: str | None = None):
        self.calls: list[tuple[dict, str]] = []
        self.response = response or {"answers": jev_answers(), "usage": {"input_tokens": 321}}
        self.error = error

    def __call__(self, payload, key, *, url, timeout):
        self.calls.append((payload, key))
        if self.error:
            raise RuntimeError(self.error)
        return self.response


class DirtyTests(unittest.TestCase):
    def test_dirty_statuses(self):
        self.assertFalse(is_dirty(CLEAN))
        self.assertTrue(is_dirty(DISAGREE))
        self.assertTrue(is_dirty(CONTRADICTED))
        self.assertTrue(is_dirty(document(answer("x", "unknown"))))
        self.assertFalse(is_dirty(document({"id": "c", "answer": "selected", "value": "Cycles"})))
        self.assertFalse(is_dirty(document({"id": "v", "answer": "found", "value": 24})))

    def test_number_mismatch_is_dirty_even_when_supported(self):
        mixed = document(
            answer(
                "fps",
                "supported",
                [span("a.md", 1, "Export at 24 fps.")],
                [span("b.md", 1, "Export at 30 fps.", reason="number_mismatch")],
            )
        )
        self.assertTrue(is_dirty(mixed))

    def test_explain_output_is_accepted(self):
        self.assertTrue(is_dirty({"packet": DISAGREE}))


class SkipTests(unittest.TestCase):
    def test_disabled_by_default_makes_no_call(self):
        recorder = Recorder()
        with patch.dict(os.environ, {jev_fuse.KEY_ENV: FAKE_KEY}, clear=False):
            os.environ.pop(jev_fuse.ENABLED_ENV, None)
            result = run_fuse("q", DISAGREE, transport=recorder)
        self.assertEqual(result.next, "skipped_disabled")
        self.assertEqual(recorder.calls, [])

    def test_missing_key_skips_without_crashing(self):
        recorder = Recorder()
        with patch.object(jev_fuse, "get_key", return_value=""):
            result = run_fuse("q", DISAGREE, enabled=True, transport=recorder)
        self.assertEqual(result.next, "skipped_no_key")
        self.assertEqual(recorder.calls, [])

    def test_clean_check_makes_no_call(self):
        recorder = Recorder()
        with patch.object(jev_fuse, "get_key", return_value=FAKE_KEY):
            result = run_fuse("q", CLEAN, enabled=True, transport=recorder)
        self.assertEqual(result.next, "skipped_clean")
        self.assertEqual(recorder.calls, [])

    def test_transport_failure_is_reported_not_raised(self):
        recorder = Recorder(error="gateway HTTP 502")
        with patch.object(jev_fuse, "get_key", return_value=FAKE_KEY):
            result = run_fuse("q", DISAGREE, enabled=True, transport=recorder)
        self.assertEqual((result.next, result.reason), ("error", "gateway HTTP 502"))
        self.assertEqual(len(recorder.calls), 1)


class PolicyTests(unittest.TestCase):
    def packet(self, status: str) -> dict:
        return {"check": [{"id": "a", "status": status, "value": None}]}

    def test_contradiction_is_never_accepted(self):
        high = jev_answers()  # every signal says accept
        result = decide(self.packet("contradicted"), high)
        self.assertNotEqual(result.next, "accept")
        self.assertEqual(result.next, "other_solver")
        ask = decide(self.packet("contradicted"), jev_answers(next={"type": "choice", "choice": "ask_user"}))
        self.assertEqual(ask.next, "ask_user")

    def test_accept_needs_both_floors_and_no_ask(self):
        self.assertEqual(decide(self.packet("disagreement"), jev_answers()).next, "accept")
        below = jev_answers(same_claim={"type": "noul", "noul": ACCEPT_FLOOR - 0.01})
        self.assertNotEqual(decide(self.packet("disagreement"), below).next, "accept")
        no_solution = jev_answers()
        del no_solution["residual_ok"]
        self.assertNotEqual(decide(self.packet("disagreement"), no_solution).next, "accept")
        vetoed = jev_answers(next={"type": "choice", "choice": "ask_user"})
        self.assertNotEqual(decide(self.packet("disagreement"), vetoed).next, "accept")

    def test_evidence_gap_on_a_different_claim_asks_the_user(self):
        gap = jev_answers(
            evidence_gap={"type": "noul", "noul": 0.9}, same_claim={"type": "noul", "noul": 0.2}
        )
        self.assertEqual(decide(self.packet("unknown"), gap).next, "ask_user")

    def test_blame_routes(self):
        base = {"residual_ok": {"type": "noul", "noul": 0.1}}
        leaf = jev_answers(blame={"type": "choice", "choice": "leaf_solve"}, **base)
        split = jev_answers(blame={"type": "choice", "choice": "intent_split"}, **base)
        other = jev_answers(blame={"type": "choice", "choice": "compose"}, **base)
        self.assertEqual(decide(self.packet("unknown"), leaf).next, "other_solver")
        self.assertEqual(decide(self.packet("unknown"), split).next, "subdivide")
        self.assertEqual(decide(self.packet("unknown"), other).next, "ask_user")


class PacketTests(unittest.TestCase):
    def test_packet_holds_spans_only_and_skips_sensitive_paths(self):
        leaky = document(
            answer(
                "k",
                "disagreement",
                [span(".env", 1, "EXPERIENTIAL_API_KEY=abc"), span("a.md", 2, "Decision: use Cycles.")],
                [span("secrets.json", 1, "{}"), span("b.md", 3, "Do not use Cycles.")],
            )
        )
        state = build_packet("Which renderer?", None, leaky)
        dumped = json.dumps(state)
        self.assertNotIn(".env", dumped)
        self.assertNotIn("secrets.json", dumped)
        self.assertNotIn("EXPERIENTIAL_API_KEY", dumped)
        self.assertEqual(set(state), {"q", "check", "evidence", "counter"})
        self.assertEqual(state["check"], [{"id": "k", "status": "disagreement", "value": None}])

    def test_long_spans_are_capped_and_state_is_bounded(self):
        many = document(
            *[
                answer(
                    f"q{n}",
                    "disagreement",
                    [span("a.md", n, "x" * 5000)],
                    [span("b.md", n, "y" * 5000)],
                )
                for n in range(40)
            ]
        )
        state = build_packet("q", "s", many)
        self.assertLessEqual(len(json.dumps(state)), MAX_STATE_CHARS)
        self.assertTrue(all(len(item["text"]) <= 240 for item in state["evidence"] + state["counter"]))
        # Disagreements go in with both sides or not at all.
        self.assertEqual(len(state["evidence"]), len(state["counter"]))

    def test_residual_question_only_with_a_solution(self):
        self.assertNotIn("residual_ok", build_questions(has_solution=False))
        self.assertIn("residual_ok", build_questions(has_solution=True))
        questions = build_questions(has_solution=True)
        self.assertEqual(set(questions["next"]["criteria"]), {"accept", "other_solver", "subdivide", "ask_user"})
        self.assertEqual(len(questions["severity"]["criteria"]), 3)

    def test_key_goes_only_to_the_transport_header_argument(self):
        recorder = Recorder()
        with patch.object(jev_fuse, "get_key", return_value=FAKE_KEY):
            result = run_fuse("q", DISAGREE, "use 24 fps", enabled=True, transport=recorder)
        payload, key = recorder.calls[0]
        self.assertEqual(key, FAKE_KEY)
        self.assertNotIn(FAKE_KEY, json.dumps(payload))
        self.assertNotIn(FAKE_KEY, json.dumps(result.to_dict()))
        self.assertEqual(result.input_tokens, 321)
        self.assertEqual(result.to_dict()["schema"], FUSE_SCHEMA)


class KeyTests(unittest.TestCase):
    def test_environment_wins_then_dotenv(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / ".env").write_text(
                f"# comment\nexport {jev_fuse.KEY_ENV}='from-dotenv'\n", encoding="utf-8"
            )
            with patch.dict(os.environ, {jev_fuse.KEY_ENV: "from-env"}):
                self.assertEqual(get_key([folder]), "from-env")
            with patch.dict(os.environ, {jev_fuse.KEY_ENV: ""}):
                self.assertEqual(get_key([folder]), "from-dotenv")
                self.assertEqual(get_key([folder / "missing"]), "")


class CliTests(unittest.TestCase):
    def test_cli_is_off_by_default_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "check.json"
            path.write_text(json.dumps(DISAGREE), encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with patch.dict(os.environ, {jev_fuse.ENABLED_ENV: ""}), redirect_stdout(out), redirect_stderr(err):
                code = main(["jev-fuse", "--question", "q", "--check-file", str(path)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["next"], "skipped_disabled")
        self.assertIn("RSO_JEV_ENABLED", err.getvalue())


if __name__ == "__main__":
    unittest.main()
