# RSO Context Alpha roadmap

This is the build list after reading V4.1 (Google Doc) against the live alpha.
It is RSO-inspired: keep the paper's production discipline, leave speculative geometry off the hot path.

Do not add embeddings, hyperbolic distance, learned axis controllers, Monte Carlo solvers, self-healing, or provider APIs unless a later measured test shows they earn their cost.

## Now (finish what is already started)

Identity split so `Z:\AI_Pipeline_Tool` is not the Antigravity worktree. Ingest that studio path. Point `rso-context` at `%USERPROFILE%\.local\RSOContextAlpha`. Ship `use` as the agent one-shot.
Ingest tracked files only; never index `private/` or gitignored paths so Studio `use` can be turned back on.

**Estimate:** half a day to a day.

## Slice A — Solve loop (the CG remainder) — 0.4.0 implemented

The unique leftover from V4.1. The ledger already stores plates. This is the match-move around it.

1. **Checkable leaves.** Keep splitting a request until each leaf has a non-model check (evidence span, hash, test, or named validator). A leaf with no check is not a leaf. Preferred first split of four, hard max twelve. Unknown leaves may take one extra `and`/`;` split while budget remains.
2. **Observable disagreement.** If two spans conflict, return `clarification_needed`. Do not BM25-pick a winner. Trigger on disagreement, not self-reported confidence. Keep both spans.
3. **Match Move matrix.** Audit schema `rso-match-move-audit/v2`: requirement↔evidence and output↔evidence indexes, residuals, `worst_residual` exposed for a later targeted query (not auto-requeried). Lexical mapping is a helper, not proof.
4. **Partition tree in the packet.** Intent root, leaves, `stop_reason`, checkable flag. Not a 3D viewer.

**Status:** shipped in 0.4.0 (checkable leaves, disagreement, partition, match-move matrix). Copy onto RYZEN when that host is next updated.

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

## Slice D — Pointer index — 0.7.0 implemented

Chunks store pointers (path, lines, hashes), not file bodies. FTS still indexes text at ingest. Query reconstructs from the live file when the hash matches; otherwise evidence is stale. `compact-pointers` is the explicit clean after a schema 4→5 upgrade (migrate does not wipe). Rename of a file with exactly one matching hash updates the source path. `admin` is a read-only 127.0.0.1 HTML viewer.

**Status: shipped in 0.7.0.** Copy onto RYZEN and run `compact-pointers` once on the live database.

## Out of scope on this roadmap

Hyperbolic embeddings, Poincaré disagreement, GatingMLP path weighting, 0.7 confidence thresholds, weight distillation, JIT model patching, background self-healing, merging Graft, indexing chat.

## Roll-up

| Slice | What you get | Calendar if I stay on it |
| --- | --- | --- |
| Now | Correct Studio identity, PATH layout, `use` | ~1 day |
| A | Real match-move around the ledger | 3–5 days |
| B | Run budget, validators, proposed vs verified | 2–3 days |
| C | Shared/project routing, takes, watch | 2–3 days |
| D | Pointer index, compact-pointers, admin | shipped in 0.7.0 |
| **A+B+C** | **Paper leftovers that are worth shipping** | **about 2 weeks of focused work** |

Deploy on this machine is copy-to-install plus ingest, not a store release. Add a day if we also refresh the Codex skill. A pip/zip share for other people is extra and not in these numbers.
