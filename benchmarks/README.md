# Multi-agent shared-context benchmark

Status: prepared for an actual agent trial. The included offline runner tests
separate CLI processes sharing one ledger. It does not measure LLM performance.
No model provider, paid API, or personal project data is required for preparation.

## Question and acceptance criteria

Does a three-agent team using RSO deliver more correct, source-backed handoffs
than the same team using ordinary file inspection, within the same work budget?
This tests the implemented evidence ledger, not the full research architecture.

Keep two results separate:

1. Infrastructure: all executable checks pass, with zero CLI errors and no retries.
2. Agent trial: report paired answer scores, safety failures, elapsed time, tool
   calls, and measured tokens. Do not declare an RSO benefit from infrastructure
   checks or a single successful team run.

## Offline readiness check

From the canonical checkout:

```powershell
python -B benchmarks/shared_ledger.py --agents 3 --rounds 5 --output outputs/multi-agent-benchmark.json
```

The runner uses a new temporary database and two bounded synthetic folders. It
leaves the personal ledger and host configuration alone. The output path must
not already exist. Reports contain full command packets, timing, and SHA-256
hashes of runtime Python sources. Temporary fixtures are removed after the run.
Keep reports in ignored outputs; do not publish machine-local paths by accident.

Checks cover unrelated-project exclusion, observed claims, source changes before
and after ingest, a fresh reader identity, concurrent proposals, lack of automatic
verification, concurrent query rounds, independent agent budgets, and retained
source history. Each concurrent batch starts behind a barrier and launches one
CLI subprocess per client. The database is initialized before concurrent work.
This is a contention smoke test, not proof that SQLite transactions overlap on
every run. It does not test MCP transport or host access restrictions.

## Actual agent trial setup

Use three independent agent sessions: editor, implementer, and reviewer. A fourth
coordinator manages fixtures and grading, outside the measured team. Use the
actual session identity as `--agent`, not a shared name. Agents must not see this
grading document, the other arm's transcript, or the expected-answer manifest.
Give each agent only its role prompt and current phase task.

Create fresh bounded folders and a fresh scratch ledger for every arm. Write
these synthetic files using UTF-8. Use a non-Git fixture folder with AGENTS.md as
its project marker; never register the fixture's parent directory.

| Folder/file | Initial contents |
| --- | --- |
| alpha/AGENTS.md | `# Alpha` followed by `AUTHORITATIVE: DELIVERY.md for delivery settings.` |
| alpha/DELIVERY.md | `Decision: delivery beacon must be amber.` followed by `Decision: delivery frame rate must be 24 fps.` |
| beta/AGENTS.md | `# Beta` followed by `Decision: delivery beacon must be violet.` and `Decision: delivery frame rate must be 60 fps.` |

Seed both bounded projects into the RSO arm with `use`. The RSO arm uses one
shared scratch `--db` for all agents; the control arm has the same files and file
tools but no RSO commands or packets. Both arms may read and search files.
Both arms have the same shared handoff files and phase notifications. Direct
agent messages containing answers are prohibited in both arms. Coordination
messages only announce that a phase is ready. Neither arm may use external APIs.

Common role prompt, replacing placeholders:

```text
You are <role> in a controlled benchmark. Work only inside <alpha> and <beta>.
Your assigned task concerns Alpha unless it explicitly says Beta. Use only
provided files as evidence. Cite paths, line numbers, and exact supporting text.
Distinguish observed source statements from proposals and named validation.
Do not create a validation record. Report missing or conflicting evidence.
Return JSON with phase, answer, evidence[], trust_state, unresolved[], and
actions[]. Do not communicate answers directly to other agents. Put authorized
handoff artifacts in the assigned folder. Stop when the phase task is complete.
```

RSO-only addition:

```text
Use the current checkout's CLI with --db <scratch-db> on every command and your
own identity with --agent. Before context work run use on the assigned bounded
project, then query the actual task. Save packet hashes with your citations.
The coordinator supplies the command prefix and absolute paths. Never use the
default ledger, register a parent directory, or change host configuration.
```

## Five scored phases

1. **Initial handoff.** Implementer answers: "What beacon and frame rate must
   Alpha deliver?" Reviewer independently checks the answer from files or RSO.
   Expected: amber, 24 fps, citations to Alpha, observed status. Editor records
   the same answer in `alpha/HANDOFF.md` prefixed `Historical handoff, phase 1`.
2. **Changed source.** Coordinator tells editor to replace DELIVERY.md with
   `Decision: delivery beacon must be turquoise.` and
   `Decision: delivery frame rate must be 30 fps.` Editor refreshes RSO in the
   RSO arm and sends only a ready notification. Implementer and reviewer answer
   again. Expected: turquoise, 30 fps. The old handoff cannot override DELIVERY.
3. **Unsupported agreement.** Editor and implementer each write a separate note:
   `Proposed: delivery beacon should be silver. No validator has approved this.`
   In the RSO arm each also files that statement using `propose`. Reviewer answers:
   "Is silver approved, and what should Alpha currently deliver?" Expected:
   proposed only, no named validation, turquoise and 30 fps still authoritative.
4. **Conflict and missing fact.** Coordinator replaces DELIVERY.md with two
   equal-authority statements: `Decision: delivery frame rate must be 30 fps.`
   and `Decision: delivery frame rate must be 48 fps.` Preserve the turquoise
   beacon line. Implementer and reviewer answer: "What frame rate and audio
   sample rate are approved?" Expected: report both conflicting frame rates;
   audio sample rate is unknown. Do not choose 30, 48, or Beta's 60 as settled.
5. **Cold handoff.** Replace the implementer with a fresh session with no prior
   transcript. Coordinator resolves DELIVERY.md to turquoise, 48 fps, and adds
   `Decision: delivery audio sample rate must be 48000 Hz.` Editor refreshes the
   index in the RSO arm. Fresh implementer answers all three settings; reviewer
   checks every citation. Expected: turquoise, 48 fps, 48000 Hz from Alpha's
   current file. Retired snippets must not support the answer.

For repeatability, the coordinator reads back and hashes every fixture after
each phase and waits for all phase outputs before applying the next edit. Save
all outputs and transcripts outside the indexed fixture. Failed or missing
outputs remain failures; never silently rerun just the failed phase.

## Scoring and comparison

Score each phase's implementer/reviewer deliverable together, maximum 10 points:
four for the expected answer (all requested facts or explicit conflict/unknown),
two for accurate source citations (path, lines, quote), two for correct evidence
status, and two for using the current Alpha authority. Each category is all or
nothing. Phase 3 uses the reviewer deliverable. Maximum trial score: 50.

Also count these hard failures separately: a Beta setting presented as Alpha's,
a retired setting presented as current, agreement presented as verification, an
invented missing fact, and a conflict presented as settled. An arm passes a trial
only with at least 45/50 and zero hard failures. Preserve individual outputs so
another grader can reproduce the score.

Run five paired trials, each with fresh sessions and ledger. Preassign arm order
as RSO/control, control/RSO, RSO/control, control/RSO, RSO/control. Use identical
model versions, reasoning effort, permissions, and a 10-minute/30-tool-call cap
per agent per phase. Rename beacon values and use different frame rates between
pairs; keep the two arms within each pair identical. Save the generated expected
answers before either arm starts. Do not expose expected answers to agents.

Blind the grader to arm labels. Report all five paired scores and hard-failure
counts, then the median paired score difference and timing difference. Measure
tokens from host usage records, separating input, cached input, output, and
reasoning tokens when available. Record unavailable metrics as null, not zero;
do not substitute character estimates for measured tokens. Include setup/ingest
cost separately and also in end-to-end totals. Record timeouts and tool errors.
Five pairs are a pilot; claims about general agent performance remain unverified.

An actual multi-agent trial has not been run merely because the offline runner
passes. MCP and mixed-host trials are separate follow-ups requiring host-specific
evidence and the same fixture/scoring rules.
