# RSO Context Alpha roadmap

## Current build: 0.9.0

The September 9 planning pass is in [BUILD-0.8.0-PLAN.md](BUILD-0.8.0-PLAN.md). The 0.8.0 MCP adapter, bounded packets, client setup, and follow-up compatibility fixes are implemented. Live Codex use/resume/query/explain/expand were checked on Windows on September 10; current-branch Windows, Ubuntu, and macOS automated checks passed on September 12; VERIFICATION.md records live Claude Code MCP use on Windows on September 12. That dated check does not establish current acceptance on every host. [VERIFICATION.md](VERIFICATION.md) records the exact scope. The original 30-42-hour schedule is historical planning, not a current commitment or background automation. Repository visibility stays private.

0.9.0 (September 18) adds `rso_check`, typed claim/choice/value questions answered from ledger evidence with no probabilities, and keeps out-of-root projects out of MCP check results. 0.8.1 (September 18) fixed false disagreements and empty compact packets, adds Gemini CLI and Antigravity MCP setup, and packages the post-0.8.0 Linux and launcher fixes. See CHANGELOG.md.

`rso_check` (CLI `check`) ships in 0.9.0. [DESIGN-RSO-CHECK.md](DESIGN-RSO-CHECK.md) has the rules and the v1 implementation notes.

The sections below preserve the earlier 0.4-0.7 roadmap and historical estimates.

This is the build list after reading V4.1 (Google Doc) against the live alpha.
RSO means Recursive Semantic Octree. [RESEARCH-ORIGIN.md](RESEARCH-ORIGIN.md) identifies the paper and explains its proposed approach.
It is RSO-inspired: keep the paper's production discipline, leave speculative geometry off the hot path.

Do not add embeddings, hyperbolic distance, learned axis controllers, Monte Carlo solvers, self-healing, or provider APIs unless a later measured test shows they earn their cost.

## Current development home

RSO development and release builds belong to the private `Kinestrand/RSOContextAlpha` repository. The installed Windows checkout remains `%USERPROFILE%\.local\RSOContextAlpha`. Other projects consume the CLI and are not build dependencies. See `DEVELOPMENT.md` for the build workflow.

The earlier implementation estimates below are historical planning notes, not current commitments.

## Slice A — Solve loop (the CG remainder) — 0.4.0 implemented

The unique leftover from V4.1. The ledger already stores plates. This is the match-move around it.

1. **Checkable leaves.** Keep splitting a request until each leaf has a non-model check (evidence span, hash, test, or named validator). A leaf with no check is not a leaf. Preferred first split of four, hard max twelve. Unknown leaves may take one extra `and`/`;` split while budget remains.
2. **Observable disagreement.** If two spans conflict, return `clarification_needed`. Do not BM25-pick a winner. Trigger on disagreement, not self-reported confidence. Keep both spans.
3. **Match Move matrix.** Audit schema `rso-match-move-audit/v2`: requirement↔evidence and output↔evidence indexes, residuals, `worst_residual` exposed for a later targeted query (not auto-requeried). Lexical mapping is a helper, not proof.
4. **Partition tree in the packet.** Intent root, leaves, `stop_reason`, checkable flag. Not a 3D viewer.

**Status:** shipped in 0.4.0 (checkable leaves, disagreement, partition, match-move matrix).

## Slice B — Run mechanics

1. **Backtracking budget on the run record.** Flat integer in SQLite, not a formula in the prompt, so compaction cannot forget it.
2. **Tiny solver registry.** Symbolic-first: span exists, hash matches, schema holds. Second solver only on conflict or failed check. Agreement is evidence, never proof.
3. **`proposed` claims.** Agents may file a solve without it looking like it was on the plate. Promotion still needs a named person or a deterministic validator.
4. **Make named validation usable.** The CLI exists; the live corpus has almost none. Skill + a one-line `record-validation` habit after the owner actually decides.

**Status: shipped in 0.5.0 (thin: run_budgets integer, three symbolic solvers, propose, pending).**

## Slice C — Hierarchy and freshness

1. **Search order:** active project, then parent domain, then shared core. Session overlay only when labeled provisional.
2. **Takes vs cut on resume.** Show that older source versions exist without dumping them.
3. **Standing observer.** Optional `watch` for an already-registered bounded folder so ingest is not a human chore. Keep roots bounded.

**Status: shipped in 0.6.0 (thin: search_order project/domain/shared, takes counts on resume, watch --path).**

## Out of scope on this roadmap

Hyperbolic embeddings, Poincaré disagreement, GatingMLP path weighting, 0.7 confidence thresholds, weight distillation, JIT model patching, background self-healing, fine-tuning of any kind (Unsloth or otherwise: LoRA/QLoRA adapters, trained routers or gating models, learned residual estimators), merging Graft, indexing chat.

## Roll-up

| Slice | What you get | Calendar if I stay on it |
| --- | --- | --- |
| Now | Correct Studio identity, PATH layout, `use` | ~1 day |
| A | Real match-move around the ledger | 3–5 days |
| B | Run budget, validators, proposed vs verified | 2–3 days |
| C | Shared/project routing, takes, watch | 2–3 days |
| **A+B+C** | **Paper leftovers that are worth shipping** | **about 2 weeks of focused work** |

Deploy on this machine is copy-to-install plus ingest, not a store release. Add a day if we also refresh the Codex skill and wait on a Kinestrand probe. A pip/zip share for other people is extra and not in these numbers.
