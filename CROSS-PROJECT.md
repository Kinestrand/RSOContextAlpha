# Cross-project retrieval (not a concept registry)

Decision 2026-09-02: keep using plates and `search_order`. Do not add a `concept_id@version` registry or store concept bodies in sqlite.

Cross-project retrieval of vendor quirks, encoding rules, and operational directives is already how query searches the active project, then parent domain, then shared. A span found in another project is `observed` in that source. It is not workstation law and is not inherited as `verified`.

Shared rules live as files (plates). Content hash is the version. A project that needs different behavior copies the plate into its own folder; ingest treats that as a new source. Changing the shared plate does not rewrite other projects until their pointer hash changes.

Runtime formulas and vendor caps execute in code (Graft). RSO points at the file that states the rule.

Studio work and class work must not share one concept space.

MCP launch roots only permit access. Each project must be registered and ingested, normally with `rso_use`, before retrieval. Repeat that bring-up for each allowed folder. Query and resume do not ingest, and RSO never synchronizes source files between checkouts or promotes a copy into an authoritative repository.
