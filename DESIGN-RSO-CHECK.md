# Design note: `rso_check`, typed questions over the ledger

Status: proposal, not scheduled. Written 2026-09-17.
Depends on: sentence-level disagreement detection from
[PR #3](https://github.com/Kinestrand/RSOContextAlpha/pull/3).

## Why

Today an agent asks RSO a free-text question and gets back an evidence packet.
It then has to read the excerpts and decide for itself whether the project
says yes, no, or nothing. Most agent questions at a decision point are narrower
than that: "is the preview exported at 24 fps?", "which of these three
renderers did we pick?", "what frame rate does the spec name?". The agent wants
a typed answer it can branch on, plus the spans that justify it.

The idea comes from typed decision models such as TypeSafe AI's Jev, which take
application state plus declared questions and return typed values with
probabilities. RSO keeps the typed interface and drops the model. Answers are
evidence statuses computed from the ledger, and every answer carries its
spans, so the agent (or a person) can check it.

## Non-goals

- No model, embeddings, or provider API. Retrieval stays SQLite FTS5 and the
  lexical rules already in `query.py`.
- No probabilities or confidence scores. The roadmap rules out confidence
  thresholds, and a lexical match count dressed up as a probability would
  invite exactly the `if p >= 0.98: act()` pattern we want to avoid.
- No trust changes. A check result never sets `trust_state`, never counts as a
  named validation, and never promotes a claim.
- No rubric scoring. Jev's third primitive, `Score`, places input on a
  quality or urgency scale. That is a judgment call and lexical rules can't
  make it honestly, so v1 leaves it out (see "Question types").

## Interface

MCP tool, alongside the existing five:

```text
rso_check(path, agent, questions, byte_budget=12000)
```

CLI:

```text
rso-context check --agent <name> --path <folder> --questions <file.json | ->
```

`questions` is a list of 1 to 12 objects. Twelve matches `MAX_LEAVES`, the
existing cap on split requirements. Each question has a caller-chosen `id`
that is echoed back, so the agent can map answers without relying on order.

One call consumes one unit of run budget no matter how many questions it
carries. That is the main practical gain over calling `rso_query` once per
question: fewer round trips, one budget unit, and one shared byte budget.

## Question types

### `claim` (yes/no)

```json
{"id": "fps", "type": "claim", "text": "Preview export is 24 fps"}
```

RSO retrieves spans for `text`, splits them into sentences, and compares each
sentence with the claim using the same polarity and topic rules as
`texts_disagree` (negation and affirmative detection, at least two shared
topic words, overlap of at least half the shorter sentence).

| Answer | Rule |
| --- | --- |
| `supported` | At least one live sentence on the same topic with the same polarity as the claim; none opposing. |
| `contradicted` | At least one live sentence on the same topic with the opposite polarity; none supporting. |
| `disagreement` | Supporting and opposing sentences from different paths. Both sides are returned. |
| `unknown` | No same-topic sentence in live evidence. |

Polarity here is the claim's own: "Preview export is 24 fps" is affirmative,
"Do not export previews at 30 fps" is negated.

Numbers must match. If the claim contains a number, a sentence counts toward
`supported` or `contradicted` only when it states the same number, compared
as a value ("24", "24.0" and "24 fps" match; "30 fps" does not). A same-topic
sentence with a different number is returned under `counter_evidence` with
`reason: number_mismatch` and makes the answer `contradicted` when nothing
states the claimed number. Without this rule, "export at 30 fps" would support
"export at 24 fps", since both share the topic words. Same-path support and
opposition is reported as `disagreement` too, but flagged `same_path: true`,
because one file contradicting itself is a different repair from two files
disagreeing.

### `choice` (one of N)

```json
{"id": "renderer", "type": "choice", "text": "Which renderer do we use?",
 "options": ["Cycles", "Eevee", "Arnold"],
 "aliases": {"Cycles": ["path tracer"], "Eevee": ["EEVEE Next"]}}
```

At most 32 options in v1. Each option, and each of its aliases, is matched as
a literal phrase, case-insensitive, on word boundaries. `aliases` is optional
and maps an option to at most 8 extra phrases. A match on an alias counts for
its option, `value` is always the option name, and each evidence item records
`matched` (the phrase that hit) so the caller can see when an alias did the
work. Two options may not share an alias; the request is rejected if they do. An option counts as supported when it
appears in a live sentence that shares topic words with `text`. An option in
a negated sentence ("do not use Arnold") counts against it.

| Answer | Rule |
| --- | --- |
| `selected` | Exactly one option supported, no opposition to it. `value` is that option. |
| `excluded` | Every supported mention is negated; `value` is null and `excluded` lists the options ruled out. |
| `disagreement` | Two or more options supported from different paths, or one option both supported and opposed. |
| `unknown` | No option appears in a same-topic sentence. |

RSO never breaks a tie by BM25 rank. That rule dates from Slice A ("Do not
BM25-pick a winner") and applies here unchanged.

### `value` (extract a stated value)

```json
{"id": "rate", "type": "value", "text": "preview export frame rate", "unit": "fps"}
```

This replaces `Score`. RSO finds same-topic sentences and pulls the number
immediately before `unit` (or, with `pattern`, the first capture group of a
caller-supplied regex, capped at 200 characters and compiled with a timeout
guard). The answer is `found` with one value, `disagreement` when distinct
values come from different paths, or `unknown`.

This covers what agents most often ask a spec for (rates, sizes, versions,
limits) without asking RSO to judge anything.

## Response

Schema `rso-check/v1`:

```json
{
  "schema": "rso-check/v1",
  "status": "ok",
  "project": {"id": "…", "name": "…", "scope": "project"},
  "corpus_versions": {"…": 9},
  "run": {"initial": 8, "remaining": 6},
  "answers": [
    {
      "id": "fps",
      "type": "claim",
      "answer": "supported",
      "value": true,
      "basis": "lexical",
      "evidence": [
        {"relative_path": "SPEC.md", "line_start": 12, "line_end": 12,
         "text": "Decision: export the preview at 24 fps.",
         "polarity": "affirmative", "expand": {"schema": "rso-expand-ref/v1", "…": "…"}}
      ],
      "counter_evidence": [],
      "validations": [],
      "same_path": false
    }
  ],
  "omitted_count": 0,
  "check_hash": "…",
  "byte_budget": 12000,
  "byte_count": 1480
}
```

Field notes:

- `value` is typed per question: boolean for `claim`, an option string for
  `choice`, a string or number for `value`. It is null unless the answer is
  `supported`, `contradicted`, `selected` or `found`.
- `basis` is `lexical` in v1. If a returned span belongs to a claim with a
  named validation, the validation record is attached under `validations`
  and `basis` becomes `validated`. The answer itself is still computed from
  spans; the validation is shown, not used to override.
- `counter_evidence` holds the opposing side of a `disagreement` or
  `contradicted` answer.
- There is deliberately no `probability`, `confidence`, or `score` field.

## Budget behavior

Status and expand refs are the last things to go. Under pressure the packet
drops, in order: excerpt text beyond the first sentence, whole excerpts
(moved to `omitted` with refs), claim and validation display text, then refs
from the least-supported answers. For a `disagreement` answer it keeps one
supporting and one opposing span together or neither, the same witness-pair
rule PR #3 added to compact packets. If an answer loses all its evidence, its
`answer` stays but `status` for the packet becomes `insufficient_budget`, so
no answer is ever presented as backed by spans the caller can't see.

## Ledger and audit

A check writes a run record the same way `query` does, with its own
`check_hash`. `rso_explain` accepts a `check_hash` and returns the stored
questions, answers and span hashes. A check that ran against sources that
later changed is reported `stale` by explain, exactly like a query packet.

An agent that wants to keep an answer can call `propose` on it. That creates a
`proposed` claim; promotion still needs a named validator.

## Implementation sketch

- New module `src/rso_context/check.py`:
  - `check_questions(database, questions, *, path, agent, byte_budget)`
  - per-type evaluators reusing `_retrieve_requirement`, `_polar_sentences`
    and `_same_topic` from `query.py` (the last two arrive with PR #3)
  - budget trimming reusing `serialized_bytes`, `_make_ref` and the witness
    helper from `compact.py`
- `mcp_server.py`: register `rso_check`, filtered to launch roots like
  `rso_query`.
- `cli.py`: `check` subcommand reading questions from a file or stdin.
- `references/protocol.md` and `SKILL.md`: document when to use `check`
  instead of `query` (a narrow decision with a known answer shape) and that a
  `supported` answer is not verification.

Rough size: one new module of 300 to 400 lines, about 25 tests, and doc
updates. Two to three days.

## Tests

- Each type returns each of its answers on small fixtures, including
  `unknown` on an empty project.
- `choice` never picks between two supported options, whatever their rank.
- An alias hit selects its option and records `matched`; a shared alias is rejected.
- A `claim` with "24 fps" is not supported by a "30 fps" sentence.
- Same-path contradiction sets `same_path: true`.
- Stale sources produce no answer other than `unknown`, and explain reports
  the check as stale.
- Byte budgets: the witness pair survives or the packet reports
  `insufficient_budget`; `byte_count` never exceeds `byte_budget`.
- One call with 12 questions consumes exactly one run-budget unit.
- The response schema contains no probability-like field (a guard test, so a
  later change can't add one quietly).
- Checks never change `trust_state` or write validation rows.

## Decisions

Recorded 2026-09-17 by Paul Griswold:

1. `choice` accepts per-option aliases (see "Question types").
2. Numbers in a `claim` must match, not only the topic words.

## Open questions

1. Is one budget unit per call right, or should very large batches cost more?
   Undecided. v1 can ship with one unit per call, since a call is capped at 12
   questions, and revisit if run budgets start running out.
2. Where should `rso-check/v1` answers appear in the admin viewer? Undecided.
   Not needed for v1; checks are reachable through `rso_explain`.

## Later: an optional judge

The weakest step in every rule above is the same: deciding whether two
sentences are about the same thing and whether they agree. PR #3 does that
with topic-word overlap, which misses conflicts phrased with different words.
A small local classifier (an off-the-shelf NLI cross-encoder, or a Jev-style
model) could fill that slot later, under these limits:

- off by default, runs only on sentence pairs the lexical rules already
  retrieved;
- its output is recorded as its own solver result and can only add a
  `disagreement` or clear one to `unknown`, never produce `supported`;
- it ships only after it beats the lexical rules on a labeled set of real
  conflict and non-conflict pairs from RSO ledgers, stored under
  `benchmarks/`.

Building that labeled set is worthwhile on its own, since it also measures the
current rules.
