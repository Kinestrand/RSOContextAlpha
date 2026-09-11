# What RSO means

RSO stands for **Recursive Semantic Octree**. The name comes from Paul Griswold's research paper, *The Recursive Semantic Octree Architecture: A Hierarchical Neuro-Symbolic Framework for Deterministic AI Reasoning* (concept manuscript V4.1).

## The proposal in plain language

The paper borrows a technique from 3D graphics: divide a complicated space into smaller regions so each region can be handled at the right level of detail. It proposes applying that idea to the meaning of a request, dividing it into smaller tasks with explicit checks.

Specialist solvers would handle those tasks, assemble the result, and then work backward from the answer to check that it still matches the original request. The paper calls this forward-and-reverse audit **Match Move**, after the visual-effects practice of checking a solved camera against the original footage. A failed check would identify the part that needs further subdivision or repair, within a bounded work budget.

The paper's proposed novelty is the combination of graphics-inspired spatial organization of meaning, adjustable detail, forward-and-reverse auditing, and focused repair with specialist solvers and symbolic checks. This describes the architectural proposal; it does not establish priority over every earlier method. Performance gains and self-healing effectiveness remain research targets.

## What this application implements

RSO Context Alpha is an application inspired by that research. It provides a local SQLite/FTS5 evidence ledger, bounded requirement partitioning, source references and hashes, explicit disagreement, per-run budgets, and a lexical Match Move coverage audit.

It does not implement the full proposed geometric reasoning engine. It has no semantic octree embeddings, learned spatial controller, hyperbolic solver voting, model training, or automatic model repair. A source hash checks file identity; a lexical audit checks coverage. Neither proves that an arbitrary answer is true.

The research manuscript was consulted for this explanation. The manuscript itself, personal evidence ledgers, and indexed project files are not included in the distribution.
