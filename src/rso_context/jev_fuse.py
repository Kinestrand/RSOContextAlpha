"""Optional downstream Jev fuse for dirty rso_check results (rso-jev-fuse/v1).

This runs after `rso-context check`, only when the user turns it on and supplies
their own Experiential Labs key. It asks TypeSafe Jev, through the Experiential
gateway, to route a dirty check: is the evidence about the same claim, is there an
evidence gap, where does the fault lie, and what should happen next.

It is a caller of RSO, not part of RSO's answers. It never changes check statuses,
never writes the ledger, never records a validation, and never sends a key anywhere
except the gateway. Core RSO builds, tests, and runs without it and without network.
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import is_sensitive_path

FUSE_SCHEMA = "rso-jev-fuse/v1"
KEY_ENV = "EXPERIENTIAL_API_KEY"
URL_ENV = "RSO_JEV_URL"
MODEL_ENV = "RSO_JEV_MODEL"
ENABLED_ENV = "RSO_JEV_ENABLED"
DEFAULT_URL = "https://api.experientiallabs.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
DEFAULT_TIMEOUT_S = 10.0

# Decision floors. Starting points to tune on real data, not calibrated guarantees.
ACCEPT_FLOOR = 0.85
GAP_HIGH = 0.70
SAME_CLAIM_LOW = 0.50

MAX_STATE_CHARS = 12_000  # about 3,000 tokens of state
MAX_SPAN_CHARS = 240

DIRTY_ANSWERS = {"unknown", "disagreement", "contradicted"}
NEXT_VALUES = ("accept", "other_solver", "subdivide", "ask_user")


@dataclass
class FuseResult:
    ran: bool
    reason: str
    next: str
    blame: str | None = None
    jev_next: str | None = None
    same_claim: float | None = None
    residual_ok: float | None = None
    evidence_gap: float | None = None
    severity: float | None = None
    answers: dict = field(default_factory=dict)
    input_tokens: int | None = None

    def to_dict(self) -> dict:
        return {"schema": FUSE_SCHEMA, **asdict(self)}


# ---------------------------------------------------------------- configuration


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def is_enabled(explicit: bool | None = None) -> bool:
    return bool(explicit) or _truthy(os.environ.get(ENABLED_ENV))


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        name = name.strip().removeprefix("export ").strip()
        values[name] = value.strip().strip('"').strip("'")
    return values


def get_key(search_dirs: list[Path] | None = None) -> str:
    """EXPERIENTIAL_API_KEY from the environment, else from a gitignored .env. Never logged."""
    key = os.environ.get(KEY_ENV, "").strip()
    if key:
        return key
    dirs = search_dirs if search_dirs is not None else [Path.cwd(), Path(__file__).resolve().parents[2]]
    for directory in dirs:
        key = _read_dotenv(directory / ".env").get(KEY_ENV, "").strip()
        if key:
            return key
    return ""


# ---------------------------------------------------------------- packets


def _answers(check_document: dict) -> list[dict]:
    if "answers" not in check_document and isinstance(check_document.get("packet"), dict):
        check_document = check_document["packet"]  # accept `explain` output too
    return list(check_document.get("answers") or [])


def _status(answer: dict) -> str:
    return str(answer.get("answer") or answer.get("status") or "")


def is_dirty(check_document: dict) -> bool:
    """Dirty when any answer is unknown, a disagreement, or contradicted by live evidence."""
    for answer in _answers(check_document):
        if _status(answer) in DIRTY_ANSWERS:
            return True
        if any(item.get("reason") == "number_mismatch" for item in answer.get("counter_evidence") or []):
            return True
    return False


def _span(item: dict) -> dict | None:
    path = str(item.get("relative_path") or "")
    if not path or is_sensitive_path(Path(path)):
        return None
    return {
        "path": path,
        "lines": f"{item.get('line_start')}-{item.get('line_end')}",
        "text": str(item.get("text") or "")[:MAX_SPAN_CHARS],
    }


def build_packet(q: str, s: str | None, check_document: dict) -> dict:
    """The only data sent to Jev: the question, an optional solution, check statuses, and cited spans."""
    answers = _answers(check_document)
    state: dict = {"q": str(q)}
    if s:
        state["s"] = str(s)
    state["check"] = [{"id": a.get("id"), "status": _status(a), "value": a.get("value")} for a in answers]
    state["evidence"] = []
    state["counter"] = []

    def fits(extra: list[dict]) -> bool:
        return len(json.dumps(state)) + len(json.dumps(extra)) <= MAX_STATE_CHARS

    # First pass: one span per side for each answer. A disagreement goes in with both sides or not at all.
    for answer in answers:
        support = [x for x in map(_span, answer.get("evidence") or []) if x]
        against = [x for x in map(_span, answer.get("counter_evidence") or []) if x]
        pair = support[:1] + against[:1]
        if _status(answer) == "disagreement" and not (support and against):
            continue
        if pair and fits(pair):
            state["evidence"].extend(support[:1])
            state["counter"].extend(against[:1])
    # Second pass: remaining spans while room lasts.
    for answer in answers:
        for key, target in (("evidence", "evidence"), ("counter_evidence", "counter")):
            for span in [x for x in map(_span, (answer.get(key) or [])[1:]) if x]:
                if not fits([span]):
                    break
                state[target].append(span)
    return state


def _option(what: str, not_for: str, examples: list[str]) -> dict:
    return {"what": what, "not_for": not_for, "examples": examples}


def build_questions(has_solution: bool) -> dict:
    questions: dict = {
        "same_claim": {
            "type": "noul",
            "instructions": (
                "Do the spans in `evidence` and `counter` state the same claim that `q` asks about, "
                "rather than a related but different claim (another scope, version, or subject)?"
            ),
        },
        "evidence_gap": {
            "type": "noul",
            "instructions": (
                "Is the evidence in `evidence` and `counter` insufficient to answer `q`, "
                "so that the answer depends on information not shown?"
            ),
        },
        "blame": {
            "type": "choice",
            "instructions": "Where does the problem shown by `check` for `q` most likely lie?",
            "criteria": {
                "intent_split": _option(
                    "The question bundles several separate asks, so no single span can answer it.",
                    "A single, focused question whose evidence is merely weak or conflicting.",
                    ["Which renderer and frame rate do finals use?"],
                ),
                "leaf_solve": _option(
                    "The question is focused and the evidence is present, but the lexical check read it wrongly.",
                    "Cases where the relevant evidence is missing from the spans.",
                    ["A span says 'we use Cycles' but the check returned unknown."],
                ),
                "compose": _option(
                    "Each part is answered, but combining the parts into one answer is where it fails.",
                    "A question with only one part.",
                    ["Two spans each answer half of the question and disagree on how they fit."],
                ),
                "evidence_gap": _option(
                    "The project files do not contain what is needed to answer.",
                    "Evidence that exists but conflicts.",
                    ["No span mentions the frame rate at all."],
                ),
                "other": _option(
                    "None of the other causes fits.",
                    "Any case another option describes.",
                    [],
                ),
            },
        },
        "next": {
            "type": "choice",
            "instructions": "Given `q` and `check`, what should the calling agent do next?",
            "criteria": {
                "accept": _option(
                    "The evidence settles the question as it stands; proceed.",
                    "Any open conflict, contradiction, or missing evidence.",
                    ["Both spans say 24 fps and the conflict flag was a false alarm."],
                ),
                "other_solver": _option(
                    "Try a different way of answering, such as reading the cited file or asking a reasoning model.",
                    "Cases where only the user can decide.",
                    ["The span clearly answers the question but the check missed it."],
                ),
                "subdivide": _option(
                    "Split the question into smaller questions and check each one.",
                    "A question that is already a single ask.",
                    ["Which renderer and frame rate do finals use?"],
                ),
                "ask_user": _option(
                    "A person must decide, because the files genuinely conflict or say nothing.",
                    "Cases a different solver could settle from the files.",
                    ["One file says 24 fps and another says 30 fps."],
                ),
            },
        },
        "severity": {
            "type": "score",
            "instructions": "If the agent acted on the check for `q` as it stands, how bad would the mistake be?",
            "criteria": [
                "Local nit: a small wording or detail issue; the overall answer holds.",
                "Branch wrong: this particular answer is wrong, but the surrounding work is sound.",
                "Parent unusable: the question itself or the work built on it cannot stand.",
            ],
        },
    }
    if has_solution:
        questions["residual_ok"] = {
            "type": "noul",
            "instructions": "Is the candidate solution `s` consistent with every span in `evidence` and `counter`?",
        }
    return questions


# ---------------------------------------------------------------- gateway


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects: following one would resend the Bearer key to another origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def post_systemone(payload: dict, key: str, *, url: str, timeout: float) -> dict:
    """One POST, no retries, nothing written to disk. Raises RuntimeError on failure."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"gateway HTTP {error.code}") from None
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as error:
        reason = getattr(error, "reason", error)
        raise RuntimeError(f"transport error ({reason}); outcome unknown, not retried") from None
    except json.JSONDecodeError:
        raise RuntimeError("gateway returned a non-JSON body") from None


# ---------------------------------------------------------------- policy


def _noul(answers: dict, qid: str) -> float | None:
    value = (answers.get(qid) or {}).get("noul")
    return float(value) if value is not None else None


def _choice(answers: dict, qid: str) -> str | None:
    value = (answers.get(qid) or {}).get("choice")
    return str(value) if value is not None else None


def decide(packet: dict, answers: dict) -> FuseResult:
    """Code owns the routing policy; Jev supplies judgments."""
    statuses = {str(item.get("status")) for item in packet.get("check") or []}
    same_claim = _noul(answers, "same_claim")
    residual_ok = _noul(answers, "residual_ok")
    evidence_gap = _noul(answers, "evidence_gap")
    blame = _choice(answers, "blame")
    jev_next = _choice(answers, "next")
    severity_value = (answers.get("severity") or {}).get("score")
    severity = float(severity_value) if severity_value is not None else None

    if "contradicted" in statuses:
        # A live contradiction is never accepted, however confident the other judgments are.
        next_step, reason = ("ask_user" if jev_next == "ask_user" else "other_solver"), "contradicted by live evidence"
    elif evidence_gap is not None and evidence_gap >= GAP_HIGH and (same_claim or 0.0) < SAME_CLAIM_LOW:
        next_step, reason = "ask_user", "evidence gap on a different claim"
    elif (
        residual_ok is not None
        and residual_ok >= ACCEPT_FLOOR
        and (same_claim or 0.0) >= ACCEPT_FLOOR
        and jev_next != "ask_user"
    ):
        next_step, reason = "accept", "solution consistent with evidence about the same claim"
    elif blame == "leaf_solve":
        next_step, reason = "other_solver", "blame on the lexical solve"
    elif blame == "intent_split":
        next_step, reason = "subdivide", "question bundles several asks"
    else:
        next_step, reason = "ask_user", "no rule settled it"
    return FuseResult(
        ran=True,
        reason=reason,
        next=next_step,
        blame=blame,
        jev_next=jev_next,
        same_claim=same_claim,
        residual_ok=residual_ok,
        evidence_gap=evidence_gap,
        severity=severity,
        answers=answers,
    )


# ---------------------------------------------------------------- entry


def run_fuse(
    q: str,
    check_document: dict,
    s: str | None = None,
    *,
    enabled: bool | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    transport=post_systemone,
) -> FuseResult:
    """Skip unless enabled, keyed, and dirty; otherwise make one gateway call and route the result."""
    if not is_enabled(enabled):
        return FuseResult(ran=False, reason=f"off by default; set {ENABLED_ENV}=1 or pass --enabled", next="skipped_disabled")
    key = get_key()
    if not key:
        return FuseResult(ran=False, reason=f"no {KEY_ENV} in the environment or .env", next="skipped_no_key")
    if not is_dirty(check_document):
        return FuseResult(ran=False, reason="check is clean; no call made", next="skipped_clean")
    state = build_packet(q, s, check_document)
    payload = {
        "model": os.environ.get(MODEL_ENV, "").strip() or DEFAULT_MODEL,
        "state": state,
        "questions": build_questions(has_solution=bool(s)),
    }
    try:
        response = transport(payload, key, url=os.environ.get(URL_ENV, "").strip() or DEFAULT_URL, timeout=timeout)
    except RuntimeError as error:
        return FuseResult(ran=True, reason=str(error), next="error")
    result = decide(state, dict(response.get("answers") or {}))
    usage = response.get("usage") or {}
    result.input_tokens = usage.get("input_tokens")
    return result
