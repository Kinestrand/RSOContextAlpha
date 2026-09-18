"""Typed evidence questions over the ledger (rso-check/v1).

Answers are evidence statuses computed from live source sentences with lexical
rules. There is no model, probability, or confidence score. A check never
changes trust_state and never records a validation. See DESIGN-RSO-CHECK.md.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import time
import uuid
from functools import lru_cache
from pathlib import Path

from .compact import _make_ref, serialized_bytes, token_estimate
from .config import Limits
from .db import Database, json_text
from .identity import resolve_existing_project, utc_now
from .mcp_contract import alias_is_visible
from .pointers import PlateCache, hydrate_chunk_row
from .query import (
    _claims_for_chunks,
    _retrieve_requirement,
    _text_has_negation,
    _topic_tokens,
    _validation_matches,
)
from .run_budget import consume
from .scope import search_scope

CHECK_SCHEMA = "rso-check/v1"
QUESTION_TYPES = ("claim", "choice", "value")
MAX_QUESTIONS = 12
MAX_OPTIONS = 32
MAX_ALIASES = 8
MAX_UNIT_CHARS = 32
MAX_PATTERN_CHARS = 200
EXCERPT_CHARS = 240

_ALLOWED_KEYS = {
    "claim": {"id", "type", "text"},
    "choice": {"id", "type", "text", "options", "aliases"},
    "value": {"id", "type", "text", "unit", "pattern"},
}
_QUESTION_WORDS = frozenset("which what where who whom whose how why does did pick picked choose chosen".split())
_STEM_SUFFIXES = ("ing", "ed", "er")
_SIBILANT_PLURALS = ("ches", "shes", "sses", "xes", "zes")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_ENDS_SENTENCE = re.compile(r"[.!?:;|`]\s*$")
_STRUCTURAL_LINE = re.compile(r"\s*(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|\||```|~~~|>|\t|    \S)")
# A recorded question or suggestion is not a decision, so it is not evidence either way.
_REPORT_CUE = re.compile(
    r"\b(?:asked|asking|whether|unclear|undecided|tbd|to\s+be\s+decided|open\s+question"
    r"|propos(?:e|es|ed|al)|consider(?:ing)?|should\s+we|do\s+we|maybe|might\s+we)\b",
    re.IGNORECASE,
)
_CLAUSE_SPLIT = re.compile(r"\s*[,;:]\s*|\s+but\s+", re.IGNORECASE)
# Plain or comma-grouped numbers: 24, 4.2, 12,000.
_NUM = r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_NUMBER = re.compile(r"(?<![\w.,])" + _NUM + r"(?!\d)")
# Guard for caller regexes: Python's re has no timeout, so reject nested quantifiers outright.
_NESTED_QUANTIFIER = re.compile(r"\([^()]*[+*][^()]*\)\s*[+*{]")


# ---------------------------------------------------------------- questions


def _phrase_regex(phrase: str) -> re.Pattern[str]:
    return re.compile(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", re.IGNORECASE)


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def normalize_questions(raw: object) -> list[dict[str, object]]:
    """Validate and normalize a question list. Raises ValueError on any problem."""
    if isinstance(raw, str):
        raw = json.loads(raw)
    if isinstance(raw, dict) and "questions" in raw:
        raw = raw["questions"]
    if not isinstance(raw, list) or not raw:
        raise ValueError("questions must be a non-empty list")
    if len(raw) > MAX_QUESTIONS:
        raise ValueError(f"at most {MAX_QUESTIONS} questions per check")
    seen_ids: set[str] = set()
    questions: list[dict[str, object]] = []
    for position, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"question {position} must be an object")
        qtype = item.get("type")
        if qtype not in QUESTION_TYPES:
            raise ValueError(f"question {position} type must be one of {', '.join(QUESTION_TYPES)}")
        unknown = set(item) - _ALLOWED_KEYS[str(qtype)]
        if unknown:
            raise ValueError(f"question {position} has unknown keys: {', '.join(sorted(unknown))}")
        qid = _require_text(item.get("id"), f"question {position} id")
        if qid in seen_ids:
            raise ValueError(f"duplicate question id: {qid}")
        seen_ids.add(qid)
        question: dict[str, object] = {
            "id": qid,
            "type": qtype,
            "text": _require_text(item.get("text"), f"question {qid} text"),
        }
        if qtype == "choice":
            question.update(_normalize_choice(qid, item))
        elif qtype == "value":
            question.update(_normalize_value(qid, item))
        questions.append(question)
    return questions


def _normalize_choice(qid: str, item: dict) -> dict[str, object]:
    options = item.get("options")
    if not isinstance(options, list) or not options:
        raise ValueError(f"question {qid} options must be a non-empty list")
    if len(options) > MAX_OPTIONS:
        raise ValueError(f"question {qid} has more than {MAX_OPTIONS} options")
    options = [_require_text(option, f"question {qid} option") for option in options]
    aliases = item.get("aliases") or {}
    if not isinstance(aliases, dict):
        raise ValueError(f"question {qid} aliases must be an object")
    owner: dict[str, str] = {}

    def claim_phrase(phrase: str, option: str) -> None:
        key = phrase.casefold()
        if key in owner and owner[key] != option:
            raise ValueError(f"question {qid}: '{phrase}' is used by both '{owner[key]}' and '{option}'")
        if key in owner:
            raise ValueError(f"question {qid}: duplicate phrase '{phrase}'")
        owner[key] = option

    for option in options:
        claim_phrase(option, option)
    normalized_aliases: dict[str, list[str]] = {}
    for option, phrases in aliases.items():
        if option not in options:
            raise ValueError(f"question {qid} alias key '{option}' is not an option")
        if not isinstance(phrases, list) or len(phrases) > MAX_ALIASES:
            raise ValueError(f"question {qid} aliases for '{option}' must be a list of at most {MAX_ALIASES}")
        cleaned = [_require_text(phrase, f"question {qid} alias") for phrase in phrases]
        for phrase in cleaned:
            claim_phrase(phrase, option)
        normalized_aliases[option] = cleaned
    return {"options": options, "aliases": normalized_aliases}


def _normalize_value(qid: str, item: dict) -> dict[str, object]:
    unit = item.get("unit")
    pattern = item.get("pattern")
    if (unit is None) == (pattern is None):
        raise ValueError(f"question {qid} needs exactly one of unit or pattern")
    if unit is not None:
        unit = _require_text(unit, f"question {qid} unit")
        if len(unit) > MAX_UNIT_CHARS:
            raise ValueError(f"question {qid} unit is longer than {MAX_UNIT_CHARS} characters")
        return {"unit": unit}
    pattern = _require_text(pattern, f"question {qid} pattern")
    if len(pattern) > MAX_PATTERN_CHARS:
        raise ValueError(f"question {qid} pattern is longer than {MAX_PATTERN_CHARS} characters")
    if _NESTED_QUANTIFIER.search(pattern):
        raise ValueError(f"question {qid} pattern has a nested quantifier")
    try:
        compiled = re.compile(pattern)
    except re.error as error:
        raise ValueError(f"question {qid} pattern does not compile: {error}") from error
    if compiled.groups < 1:
        raise ValueError(f"question {qid} pattern needs a capture group")
    return {"pattern": pattern}


# ---------------------------------------------------------------- lexical helpers


def _stem(token: str) -> str:
    """Crude suffix stripping, applied until stable so render/renders/renderer meet."""
    for _ in range(3):
        if token.endswith(_SIBILANT_PLURALS) and len(token) >= 5:
            token = token[:-2]
        elif token.endswith("s") and not token.endswith("ss") and len(token) >= 4:
            token = token[:-1]
        else:
            for suffix in _STEM_SUFFIXES:
                if token.endswith(suffix) and len(token) - len(suffix) >= 4:
                    token = token[: -len(suffix)]
                    break
            else:
                return token
    return token


@lru_cache(maxsize=8192)
def _topics(text: str) -> frozenset[str]:
    return frozenset(_stem(token) for token in _topic_tokens(text) if token not in _QUESTION_WORDS)


def _related(question: frozenset[str], sentence: frozenset[str], *, symmetric: bool) -> bool:
    """Question-driven topic match. Symmetric mode also needs half of the smaller set shared."""
    if not question or not sentence:
        return False
    shared = len(question & sentence)
    if shared < min(2, len(question)):
        return False
    return not symmetric or shared / min(len(question), len(sentence)) >= 0.5


def _negated(text: str) -> bool:
    return _text_has_negation(text)


def _as_number(text: str) -> float | int | None:
    try:
        value = float(text)
    except ValueError:
        return None
    return int(value) if value.is_integer() else value


def _numbers(text: str) -> set[float]:
    return {float(match.replace(",", "")) for match in _NUMBER.findall(text)}


def _joined_blocks(text: str, start: int) -> list[tuple[int, int, str]]:
    """Rejoin hard-wrapped prose: a line continues into the next only when it clearly runs on.

    Documentation is commonly wrapped at 80 columns, and a sentence split across two
    lines used to be invisible to every rule here. Structural lines (headings, list
    items, table rows, fences, indented code) never join, so list items stay separate.
    """
    lines = text.splitlines()
    blocks: list[tuple[int, int, str]] = []
    index = 0
    while index < len(lines):
        first = index
        buffer = lines[index]
        while (
            index + 1 < len(lines)
            and buffer.strip()
            and not _STRUCTURAL_LINE.match(buffer)
            and not _ENDS_SENTENCE.search(buffer)
            and lines[index + 1].strip()
            and not _STRUCTURAL_LINE.match(lines[index + 1])
        ):
            index += 1
            buffer = buffer.rstrip() + " " + lines[index].strip()
        if buffer.strip():
            blocks.append((start + first, start + index, buffer))
        index += 1
    return blocks


def _sentences(row: dict) -> list[tuple[int, int, str]]:
    start = int(row["line_start"])
    result: list[tuple[int, int, str]] = []
    for line_start, line_end, block in _joined_blocks(str(row.get("text") or ""), start):
        for piece in _SENTENCE_END.split(block):
            text = piece.strip(" \t-*#>")
            if not text:
                continue
            # A rejoined block reports the lines it spans; an ordinary line reports itself.
            result.append((line_start, line_end, text))
    return result


def _evidence(row: dict, span: tuple[int, int], sentence: str, **extra: object) -> dict[str, object]:
    line_start, line_end = span
    item: dict[str, object] = {
        "project_id": row.get("project_id"),
        "relative_path": row.get("relative_path"),
        "line_start": line_start,
        "line_end": line_end,
        "text": sentence[:EXCERPT_CHARS],
        "source_hash": row.get("source_hash"),
        "chunk_hash": row.get("chunk_hash"),
        "expand": _make_ref(row, line_start, line_end),
    }
    if len(sentence) > EXCERPT_CHARS:
        item["truncated"] = True
    item.update(extra)
    item["_chunk_id"] = row.get("chunk_id")
    return item


def _is_reported(sentence: str) -> bool:
    """A recorded question or suggestion is not a decision, so it is not evidence either way."""
    return "?" in sentence or _REPORT_CUE.search(sentence) is not None


def _unique_sentences(rows: list[dict]) -> list[tuple[dict, tuple[int, int], str]]:
    seen: set[tuple[str, int, str]] = set()
    result: list[tuple[dict, tuple[int, int], str]] = []
    for row in rows:
        for line_start, line_end, sentence in _sentences(row):
            key = (str(row.get("relative_path")), line_start, sentence)
            if key in seen or _is_reported(sentence):
                continue
            seen.add(key)
            result.append((row, (line_start, line_end), sentence))
    return result


def _paths(items: list[dict]) -> set[str]:
    return {str(item.get("relative_path")) for item in items}


# ---------------------------------------------------------------- evaluators


def _answer(question: dict, answer: str, value: object, evidence: list, counter: list) -> dict[str, object]:
    result: dict[str, object] = {
        "id": question["id"],
        "type": question["type"],
        "answer": answer,
        "value": value,
        "basis": "lexical",
        "evidence": evidence,
        "counter_evidence": counter,
        "validations": [],
        "same_path": answer == "disagreement" and len(_paths(evidence + counter)) == 1,
    }
    return result


def _evaluate_claim(question: dict, sentences: list[tuple[dict, int, str]]) -> dict[str, object]:
    text = str(question["text"])
    topic = _topics(text)
    claim_negated = _negated(text)
    claim_numbers = _numbers(text)
    support: list[dict] = []
    oppose: list[dict] = []
    for row, span, sentence in sentences:
        if not _related(topic, _topics(sentence), symmetric=True):
            continue
        negated = _negated(sentence)
        polarity = "negated" if negated else "affirmative"
        if claim_numbers:
            stated = _numbers(sentence)
            if not stated:
                continue
            if not claim_numbers <= stated:
                # A different stated number contradicts an affirmative claim; negations say nothing.
                if not claim_negated and not negated:
                    oppose.append(_evidence(row, span, sentence, polarity=polarity, reason="number_mismatch"))
                continue
        (support if negated == claim_negated else oppose).append(_evidence(row, span, sentence, polarity=polarity))
    if support and oppose:
        return _answer(question, "disagreement", None, support, oppose)
    if support:
        return _answer(question, "supported", True, support, [])
    if oppose:
        return _answer(question, "contradicted", False, [], oppose)
    return _answer(question, "unknown", None, [], [])


def _evaluate_choice(question: dict, sentences: list[tuple[dict, int, str]]) -> dict[str, object]:
    options = list(question["options"])
    aliases = dict(question.get("aliases") or {})
    phrases = [
        (option, phrase, _phrase_regex(phrase))
        for option in options
        for phrase in [option, *aliases.get(option, [])]
    ]
    phrase_topics = frozenset().union(*(_topics(phrase) for _, phrase, _ in phrases))
    topic = _topics(str(question["text"])) - phrase_topics
    for_option: dict[str, list[dict]] = {option: [] for option in options}
    against: dict[str, list[dict]] = {option: [] for option in options}
    for row, span, sentence in sentences:
        if topic and not _related(topic, _topics(sentence), symmetric=False):
            continue
        hits: dict[tuple[str, bool], str] = {}
        for clause in _CLAUSE_SPLIT.split(sentence):
            negated = _negated(clause)
            for option, phrase, pattern in phrases:
                if pattern.search(clause):
                    hits.setdefault((option, negated), phrase)
        for (option, negated), phrase in hits.items():
            item = _evidence(
                row, span, sentence, polarity="negated" if negated else "affirmative", matched=phrase
            )
            (against if negated else for_option)[option].append(item)
    supported = [option for option in options if for_option[option]]
    ruled_out = [option for option in options if against[option] and not for_option[option]]
    if len(supported) >= 2 or (len(supported) == 1 and against[supported[0]]):
        evidence = [item for option in supported for item in for_option[option]]
        counter = [item for option in supported for item in against[option]]
        result = _answer(question, "disagreement", None, evidence, counter)
    elif supported:
        result = _answer(question, "selected", supported[0], for_option[supported[0]], [])
    elif ruled_out:
        evidence = [item for option in ruled_out for item in against[option]]
        result = _answer(question, "excluded", None, evidence, [])
    else:
        result = _answer(question, "unknown", None, [], [])
    result["excluded"] = ruled_out
    return result


def _evaluate_value(question: dict, sentences: list[tuple[dict, int, str]]) -> dict[str, object]:
    unit = question.get("unit")
    if unit:
        pattern = re.compile(r"(?<![\w.,])(" + _NUM + r")\s*" + re.escape(str(unit)) + r"(?!\w)", re.IGNORECASE)
        topic = _topics(str(question["text"])) - _topics(str(unit))
    else:
        pattern = re.compile(str(question["pattern"]))
        topic = _topics(str(question["text"]))
    found: list[dict] = []
    values: list[object] = []
    for row, span, sentence in sentences:
        if not _related(topic, _topics(sentence), symmetric=False) or _negated(sentence):
            continue
        for match in pattern.finditer(sentence):
            raw = (match.group(1) or "").strip()
            if not raw:
                continue
            number = _as_number(raw.replace(",", "")) if unit else _as_number(raw)
            value = number if number is not None else raw
            if value not in values:
                values.append(value)
            found.append(_evidence(row, span, sentence, value=value))
    if len(values) == 1:
        result = _answer(question, "found", values[0], found, [])
    elif values:
        result = _answer(question, "disagreement", None, found, [])
    else:
        result = _answer(question, "unknown", None, [], [])
    result["values"] = values
    return result


_EVALUATORS = {"claim": _evaluate_claim, "choice": _evaluate_choice, "value": _evaluate_value}


def _retrieval_texts(question: dict) -> list[str]:
    text = str(question["text"])
    if question["type"] == "choice":
        phrases = [phrase for option in question["options"] for phrase in [option, *question["aliases"].get(option, [])]]
        return [text, text + " " + " ".join(phrases)]
    if question["type"] == "value" and question.get("unit"):
        return [text, text + " " + str(question["unit"])]
    return [text]


# ---------------------------------------------------------------- validations


def _attach_validations(connection, project_ids: list[str], answers: list[dict]) -> None:
    """One claims query and one validations query for every answer in the call."""
    chunk_ids = sorted(
        {
            int(item["_chunk_id"])
            for answer in answers
            for item in answer["evidence"] + answer["counter_evidence"]
            if item.get("_chunk_id") is not None
        }
    )
    claims = _claims_for_chunks(connection, project_ids, chunk_ids)
    if not claims:
        return
    records_by_claim: dict[str, list[dict]] = {}
    for record in _validation_matches(connection, sorted({str(claim["id"]) for claim in claims})):
        records_by_claim.setdefault(str(record["claim_id"]), []).append(record)
    if not records_by_claim:
        return
    displays = [
        (str(claim["id"]), re.sub(r"\s+", " ", str(claim.get("display_text") or "")).casefold().strip(), claim)
        for claim in claims
    ]
    for answer in answers:
        items = answer["evidence"] + answer["counter_evidence"]
        sentences = [re.sub(r"\s+", " ", str(item["text"])).casefold() for item in items]
        for claim_id, display, claim in displays:
            if not display or claim_id not in records_by_claim:
                continue
            if not any(display in sentence or sentence in display for sentence in sentences):
                continue
            answer["validations"].extend(
                {
                    "claim_id": record["claim_id"],
                    "claim_text": claim.get("display_text"),
                    "validator": record["validator"],
                    "result": record["result"],
                    "observed_at": record["observed_at"],
                }
                for record in records_by_claim[claim_id]
            )
        if answer["validations"]:
            answer["basis"] = "validated"


# ---------------------------------------------------------------- main entry


def _visible_project_ids(connection, roots: list[Path]) -> set[str]:
    """Projects with at least one alias path under a launch root (same rule as MCP query)."""
    rows = connection.execute(
        "SELECT project_id, resolved_path FROM project_aliases WHERE resolved_path IS NOT NULL"
    ).fetchall()
    return {str(row["project_id"]) for row in rows if alias_is_visible(row["resolved_path"], roots)}


def check_questions(
    database: Database,
    questions: object,
    *,
    path: str | Path | None = None,
    project_id: str | None = None,
    agent: str = "unknown",
    limit: int = Limits.query_limit,
    roots: list[Path] | None = None,
) -> dict[str, object]:
    """Answer typed questions from live ledger evidence. Consumes one run-budget unit per call.

    Returns the full rso-check/v1 result (no byte budget applied); use fit_check to trim it.
    When roots are given, sources outside them are ignored before any answer is computed.
    """
    normalized = normalize_questions(questions)
    database.initialize()
    started = time.perf_counter()
    project = resolve_existing_project(database, path=path, project_id=project_id)
    project_id_value = str(project["id"])
    with database.transaction() as connection:
        budget = consume(connection, project_id_value, agent, n=1)
    config = {"algorithm": "rso-check-lexical-v1", "limit_per_question": int(limit)}
    answers: list[dict[str, object]] = []
    with database.connect() as connection:
        scoped = search_scope(connection, project_id_value, path=path)
        order = [{"id": item["id"], "scope": item["scope"], "name": item["name"]} for item in scoped["order"]]
        shared_ids = [str(item["id"]) for item in order[1:]]
        scoped_ids = [project_id_value, *shared_ids]
        corpus_versions = dict(scoped["corpus_versions"])
        if roots is not None:
            # Out-of-root projects must not surface by name or id, only be skipped.
            visible = _visible_project_ids(connection, roots)
            order = [item for item in order if str(item["id"]) in visible]
            corpus_versions = {key: value for key, value in corpus_versions.items() if str(key) in visible}
        plate_cache = PlateCache()
        for question in normalized:
            rows: list[dict] = []
            seen_chunks: set[object] = set()
            for text in _retrieval_texts(question):
                for row in _retrieve_requirement(
                    connection,
                    text,
                    project_id=project_id_value,
                    shared_ids=shared_ids,
                    limit=limit,
                    path=path,
                    scoped_ids=scoped_ids,
                ):
                    if row["chunk_id"] in seen_chunks:
                        continue
                    if roots is not None and not alias_is_visible(row["resolved_path"], roots):
                        continue
                    seen_chunks.add(row["chunk_id"])
                    hydrated = hydrate_chunk_row(row, plate_cache)
                    if not hydrated.get("stale"):
                        rows.append(hydrated)
            answers.append(_EVALUATORS[str(question["type"])](question, _unique_sentences(rows)))
        _attach_validations(connection, scoped_ids, answers)
    for answer in answers:
        for item in answer["evidence"] + answer["counter_evidence"]:
            item.pop("_chunk_id", None)
    body = {
        "schema": CHECK_SCHEMA,
        "project": {"id": project_id_value, "name": project["display_name"], "scope": project["scope"]},
        "search_order": order,
        "corpus_versions": corpus_versions,
        "questions": normalized,
        "answers": answers,
    }
    check_hash = hashlib.sha256(json_text(body).encode("utf-8")).hexdigest()
    result = {
        **body,
        "status": "ok",
        "run": {"initial": int(budget["initial"]), "remaining": int(budget["remaining"])},
        "check_hash": check_hash,
    }
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO runs(id,project_id,query_text,corpus_version,config_json,packet_json,"
            "packet_hash,elapsed_ms,created_at,remaining_budget) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4()),
                project_id_value,
                json_text({"rso_check": normalized}),
                int(project["corpus_version"]),
                json_text(config),
                json_text(result),
                check_hash,
                (time.perf_counter() - started) * 1000,
                utc_now(),
                int(budget["remaining"]),
            ),
        )
    return result


# ---------------------------------------------------------------- byte budget


def _stamp(packet: dict[str, object]) -> int:
    for _ in range(6):
        size = serialized_bytes(packet)
        packet["byte_count"] = size
        packet["token_estimate"] = token_estimate(size)
        if serialized_bytes(packet) == size:
            break
    return serialized_bytes(packet)


def fit_check(full: dict[str, object], byte_budget: int = Limits.compact_byte_budget) -> dict[str, object]:
    """Trim a check result to byte_budget. Status and expand refs go last.

    Order: extra evidence beyond a one-per-side witness, validation detail, the
    witnesses themselves (status becomes insufficient_budget), then omitted refs.
    """
    budget = max(256, int(byte_budget))
    packet = {key: value for key, value in full.items() if key != "questions"}
    packet["omitted"] = []
    packet["omitted_count"] = 0
    packet["byte_budget"] = budget
    if _stamp(packet) <= budget:
        return packet
    # Trimming mutates answers, so copy only once it is needed.
    packet = copy.deepcopy(packet)
    answers: list[dict] = list(packet.get("answers") or [])

    def over() -> bool:
        return _stamp(packet) > budget

    def omit(answer: dict, item: dict) -> None:
        packet["omitted"].append({"id": answer["id"], "reason": "byte_budget", "expand": item.get("expand")})
        packet["omitted_count"] = int(packet["omitted_count"]) + 1

    for answer in reversed(answers):
        for key in ("evidence", "counter_evidence"):
            while len(answer[key]) > 1 and over():
                omit(answer, answer[key].pop())
    if over():
        for answer in answers:
            answer["validations"] = [
                {"claim_id": item["claim_id"], "result": item["result"]} for item in answer["validations"]
            ]
    for answer in reversed(answers):
        if not over():
            break
        dropped = answer["evidence"] + answer["counter_evidence"]
        if not dropped:
            continue
        for item in dropped:
            omit(answer, item)
        answer["evidence"] = []
        answer["counter_evidence"] = []
        answer["evidence_omitted"] = True
        packet["status"] = "insufficient_budget"
        packet["message"] = "Evidence for some answers cannot fit; use omitted expand refs."
    while packet["omitted"] and over():
        packet["omitted"].pop()
    if over():
        packet = {
            "schema": CHECK_SCHEMA,
            "status": "insufficient_budget",
            "check_hash": full.get("check_hash"),
            "answers": [
                {"id": answer["id"], "answer": answer["answer"], "value": answer["value"]} for answer in answers
            ],
            "omitted": [],
            "omitted_count": packet["omitted_count"],
            "byte_budget": budget,
        }
        for key in ("answers", "omitted", "token_estimate", "omitted_count", "check_hash"):
            if not over():
                break
            packet.pop(key, None)
    return packet


# ---------------------------------------------------------------- explain


def explain_check(connection, packet: dict[str, object], visible_project_ids: set[str] | None = None) -> dict:
    """Mark evidence whose source changed since the check, and drop evidence outside visible projects."""
    result = copy.deepcopy(packet)
    if visible_project_ids is not None:
        result["search_order"] = [
            item for item in result.get("search_order") or [] if str(item.get("id")) in visible_project_ids
        ]
        result["corpus_versions"] = {
            key: value for key, value in (result.get("corpus_versions") or {}).items() if key in visible_project_ids
        }
    stale_count = 0
    for answer in result.get("answers") or []:
        for key in ("evidence", "counter_evidence"):
            kept = []
            for item in answer.get(key) or []:
                if visible_project_ids is not None and str(item.get("project_id")) not in visible_project_ids:
                    answer["restricted"] = True
                    continue
                row = connection.execute(
                    "SELECT current_hash FROM sources WHERE project_id=? AND relative_path=? AND active=1 "
                    "ORDER BY rowid DESC LIMIT 1",
                    (item.get("project_id"), item.get("relative_path")),
                ).fetchone()
                if row is None or row["current_hash"] != item.get("source_hash"):
                    item["stale"] = True
                    stale_count += 1
                kept.append(item)
            answer[key] = kept
    result["stale_evidence"] = stale_count
    return result
