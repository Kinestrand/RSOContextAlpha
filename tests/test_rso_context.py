from __future__ import annotations

import json
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from rso_context.admin import build_admin_html
from rso_context.config import automatic_discovery_roots
from rso_context.db import SCHEMA_VERSION, Database, SchemaVersionError
from rso_context.identity import register_project
from rso_context.ingest import ingest_project
from rso_context.audit import audit_answer, pending_validations, propose_claim, record_validation
from rso_context.pointers import compact_pointers
from rso_context.query import query_context
from rso_context.resume import resume_context
from rso_context.run_budget import set_budget


class RsoContextAlphaTests(unittest.TestCase):
    def _project_dir(self, root: Path) -> Path:
        project = root / "widget-factory"
        project.mkdir()
        (project / "AGENTS.md").write_text(
            "# Agents\n\nDo not register Documents as a discovery root.\n",
            encoding="utf-8",
        )
        (project / "README.md").write_text(
            "# Widget Factory\n\nThe factory ships blue widgets.\n"
            "Do not register Documents as a project root.\n",
            encoding="utf-8",
        )
        return project

    def test_register_ingest_resume_query_and_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "context.sqlite3"
            project_path = self._project_dir(root)
            database = Database(db_path)
            database.initialize()
            database.initialize()  # v3 again must not crash or downgrade

            register_project(database, project_path, agent="tester")
            ingest = ingest_project(database, project_path, agent="tester")
            self.assertTrue(ingest["changed"] or ingest["counters"]["files_seen"] >= 2)

            resume = resume_context(database, path=project_path, agent="tester", limit=8)
            self.assertEqual(resume["schema"], "rso-context-resume/v1")
            self.assertNotIn("query", resume)
            self.assertTrue(any("task" in key.casefold() for key in resume) is False)
            texts = [str(claim["display_text"]) for claim in resume["claims"]]
            self.assertTrue(
                any("Do not register Documents" in text for text in texts),
                texts,
            )
            self.assertTrue(all(claim["trust_state"] == "observed" for claim in resume["claims"]))
            self.assertIn("packet_hash", resume)
            self.assertEqual(resume["db_schema_version"], SCHEMA_VERSION)

            packet = query_context(
                database,
                "what color are the widgets",
                path=project_path,
                agent="tester",
            )
            self.assertTrue(
                any(item["status"] == "evidence_found" for item in packet["requirements"]),
                packet["requirements"],
            )
            self.assertTrue(packet["claims"], "expected claims attached to retrieved evidence")
            claim_texts = [str(claim["display_text"]) for claim in packet["claims"]]
            self.assertTrue(
                any("Do not register Documents" in text for text in claim_texts),
                claim_texts,
            )

            with database.connect() as connection:
                connection.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
            newer = Database(db_path)
            with self.assertRaises(SchemaVersionError):
                newer.initialize()

    def test_automatic_discovery_roots_excludes_downloads_and_onedrive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            roots = [str(path) for path in automatic_discovery_roots(cwd)]
            joined = "\n".join(roots)
            self.assertNotIn("Downloads", joined)
            self.assertNotIn("OneDrive", joined)
            self.assertTrue(any(Path(path) == cwd.resolve() for path in roots), roots)



    def test_migrate_v2_fts_indexes_project_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "context.sqlite3"
            database = Database(db_path)
            database.initialize()
            with database.connect() as connection:
                connection.execute("DROP TABLE chunk_fts")
                connection.execute(
                    "CREATE VIRTUAL TABLE chunk_fts USING fts5("
                    "text, heading, path, project_id UNINDEXED, chunk_id UNINDEXED, "
                    "tokenize='unicode61 remove_diacritics 2')"
                )
                connection.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
            database.initialize()
            with database.connect() as connection:
                sql = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE name='chunk_fts'"
                ).fetchone()[0]
                self.assertNotIn("project_id UNINDEXED", " ".join(sql.split()))
                self.assertIn("chunk_id UNINDEXED", " ".join(sql.split()))
                version = int(
                    connection.execute(
                        "SELECT value FROM meta WHERE key='schema_version'"
                    ).fetchone()[0]
                )
                self.assertEqual(version, SCHEMA_VERSION)



    def test_sibling_worktrees_do_not_share_resume_claims(self) -> None:
        from rso_context.identity import inspect_project

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "context.sqlite3"
            studio = root / "AI_Pipeline_Tool"
            other = root / "test_rso_context_tool"
            studio.mkdir()
            other.mkdir()
            (studio / "AGENTS.md").write_text(
                "# Studio\n\nStudio must use bounded roots.\n", encoding="utf-8"
            )
            (other / "AGENTS.md").write_text(
                "# Nested\n\nNested must not leak into parent resume.\n", encoding="utf-8"
            )
            database = Database(db_path)
            ingest_project(database, studio, agent="tester")
            ingest_project(database, other, agent="tester")
            resume_studio = resume_context(database, path=studio, agent="tester", limit=8)
            texts = [str(claim["display_text"]) for claim in resume_studio["claims"]]
            self.assertTrue(any("bounded roots" in text for text in texts), texts)
            self.assertFalse(any("leak into parent" in text for text in texts), texts)
            self.assertNotEqual(
                inspect_project(studio).canonical_key,
                inspect_project(other).canonical_key,
            )
            packet = query_context(database, "bounded roots", path=studio, agent="tester")
            self.assertTrue(any(item["status"] == "evidence_found" for item in packet["requirements"]))
            for hit in packet["evidence"]:
                self.assertNotIn("test_rso_context_tool", str(hit["resolved_path"]))


    def test_ingest_skips_graft_directory(self) -> None:
        from rso_context.config import IGNORED_DIR_NAMES

        self.assertIn("graft", IGNORED_DIR_NAMES)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "context.sqlite3"
            project = self._project_dir(root)
            graft = project / "graft"
            graft.mkdir()
            (graft / "derived.md").write_text(
                "# Derived\n\nGraft card must not become an RSO claim.\n",
                encoding="utf-8",
            )
            database = Database(db_path)
            ingest_project(database, project, agent="tester")
            with database.connect() as connection:
                paths = [
                    row[0]
                    for row in connection.execute("SELECT relative_path FROM sources")
                ]
            joined = " ".join(paths).replace("\\", "/")
            self.assertNotIn("graft/", joined)
            self.assertFalse(any("derived.md" in path for path in paths), paths)

    def test_git_ingest_indexes_tracked_files_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "tracked-repo"
            project.mkdir()
            (project / "README.md").write_text(
                "# Tracked\n\nPublic docs must remain the only ingested source.\n",
                encoding="utf-8",
            )
            (project / ".gitignore").write_text("ignored.md\nprivate/\n", encoding="utf-8")
            (project / "ignored.md").write_text(
                "Ignored file must not be ingested.\n",
                encoding="utf-8",
            )
            private = project / "private"
            private.mkdir()
            (private / "secret.md").write_text(
                "Secret records must not be ingested.\n",
                encoding="utf-8",
            )
            subprocess.run(
                ["git", "init"],
                cwd=project,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "add", "README.md", ".gitignore"],
                cwd=project,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.email=t@t",
                    "-c",
                    "user.name=t",
                    "commit",
                    "-m",
                    "init",
                ],
                cwd=project,
                check=True,
                capture_output=True,
            )
            database = Database(root / "context.sqlite3")
            ingest_project(database, project, agent="tester")
            with database.connect() as connection:
                paths = [
                    row[0]
                    for row in connection.execute("SELECT relative_path FROM sources")
                ]
            posix = [path.replace("\\", "/") for path in paths]
            self.assertTrue(any(path.endswith("README.md") for path in posix), paths)
            self.assertFalse(any("secret.md" in path for path in posix), paths)
            self.assertFalse(any("ignored.md" in path for path in posix), paths)
            self.assertFalse(any("private/" in path or path.startswith("private") for path in posix), paths)

    def test_nongit_ingest_skips_private_directory(self) -> None:
        from rso_context.config import IGNORED_DIR_NAMES

        for name in (
            "private",
            "backup",
            "backups",
            "output",
            "outputs",
            "renders",
            "tmp",
            "temp",
        ):
            self.assertIn(name, IGNORED_DIR_NAMES)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "plain-folder"
            project.mkdir()
            (project / "keep.md").write_text(
                "# Keep\n\nPublic notes must stay searchable.\n",
                encoding="utf-8",
            )
            private = project / "private"
            private.mkdir()
            (private / "secret.md").write_text(
                "Secret records must not be ingested.\n",
                encoding="utf-8",
            )
            database = Database(root / "context.sqlite3")
            ingest_project(database, project, agent="tester")
            with database.connect() as connection:
                paths = [
                    row[0]
                    for row in connection.execute("SELECT relative_path FROM sources")
                ]
            posix = [path.replace("\\", "/") for path in paths]
            self.assertTrue(any(path.endswith("keep.md") for path in posix), paths)
            self.assertFalse(any("secret.md" in path for path in posix), paths)
            self.assertFalse(any("private/" in path or path.startswith("private") for path in posix), paths)

    def test_compound_query_builds_partition_with_max_leaves_12(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_path = self._project_dir(root)
            database = Database(root / "context.sqlite3")
            ingest_project(database, project_path, agent="tester")
            packet = query_context(
                database,
                "what color are the widgets; do not register Documents as a discovery root",
                path=project_path,
                agent="tester",
            )
            self.assertGreater(len(packet["requirements"]), 1, packet["requirements"])
            self.assertIn("partition", packet)
            partition = packet["partition"]
            self.assertEqual(partition["root"], "what color are the widgets; do not register Documents as a discovery root")
            self.assertEqual(partition["method"], "deterministic-split")
            self.assertEqual(partition["max_leaves"], 12)
            self.assertGreater(len(partition["leaves"]), 1)
            self.assertEqual(len(partition["leaves"]), len(packet["requirements"]))
            for leaf in partition["leaves"]:
                self.assertIn(leaf["stop_reason"], {"evidence_found", "unknown", "disagreement", "max_leaves"})
                self.assertTrue(leaf["checkable"])
                self.assertIn(leaf["status"], {"evidence_found", "unknown", "disagreement", "budget_exhausted"})

    def test_conflicting_spans_yield_disagreement_and_keep_both(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "policy-conflict"
            project.mkdir()
            (project / "AGENTS.md").write_text(
                "# Policy\n\nStudio must use bounded roots.\n",
                encoding="utf-8",
            )
            (project / "README.md").write_text(
                "# Policy\n\nStudio must not use bounded roots.\n",
                encoding="utf-8",
            )
            database = Database(root / "context.sqlite3")
            ingest_project(database, project, agent="tester")
            packet = query_context(database, "bounded roots", path=project, agent="tester")
            statuses = [item["status"] for item in packet["requirements"]]
            self.assertIn("disagreement", statuses, packet["requirements"])
            self.assertTrue(packet["clarification_needed"])
            texts = [str(hit["text"]) for hit in packet["evidence"]]
            has_must = any(
                "must use bounded roots" in text and "must not use bounded roots" not in text
                for text in texts
            )
            has_must_not = any("must not use bounded roots" in text for text in texts)
            self.assertTrue(has_must, texts)
            self.assertTrue(has_must_not, texts)
            disagreed = [item for item in packet["requirements"] if item["status"] == "disagreement"]
            self.assertTrue(all(item["candidate_count"] >= 2 for item in disagreed), disagreed)
            self.assertTrue(
                any(leaf["stop_reason"] == "disagreement" for leaf in packet["partition"]["leaves"])
            )

    def test_large_node_table_query_stays_fast(self) -> None:
        from rso_context.identity import utc_now

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_path = self._project_dir(root)
            database = Database(root / "context.sqlite3")
            ingest_project(database, project_path, agent="tester")
            now = utc_now()
            with database.transaction() as connection:
                project_id = str(connection.execute("SELECT id FROM projects").fetchone()[0])
                chunk_row = connection.execute("SELECT id FROM chunks LIMIT 1").fetchone()
                subject_row = connection.execute("SELECT id FROM nodes LIMIT 1").fetchone()
                self.assertIsNotNone(chunk_row)
                self.assertIsNotNone(subject_row)
                chunk_id = int(chunk_row[0])
                subject_id = str(subject_row[0])
                node_rows = []
                edge_rows = []
                for index in range(2000):
                    name = f"zz_dummy_node_{index:04d}"
                    node_id = str(uuid.uuid5(uuid.NAMESPACE_OID, f"{project_id}:{name}"))
                    node_rows.append(
                        (
                            node_id,
                            project_id,
                            "symbol",
                            name,
                            name,
                            "observed",
                            "{}",
                            now,
                            now,
                        )
                    )
                    edge_rows.append(
                        (
                            project_id,
                            subject_id,
                            "declares",
                            node_id,
                            "observed",
                            chunk_id,
                            "test.dummy-node.v1",
                            now,
                        )
                    )
                connection.executemany(
                    "INSERT INTO nodes(id,project_id,kind,canonical_name,display_name,"
                    "trust_state,properties_json,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    node_rows,
                )
                connection.executemany(
                    "INSERT INTO edges(project_id,subject_id,predicate,object_id,trust_state,"
                    "evidence_chunk_id,rule_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    edge_rows,
                )
            started = time.perf_counter()
            packet = query_context(
                database,
                "qwerty-unmatched-zyxwv no-such-evidence",
                path=project_path,
                agent="tester",
                use_cache=False,
            )
            elapsed = time.perf_counter() - started
            self.assertLess(
                elapsed,
                2.0,
                f"unmatched query over 2000 dummy nodes took {elapsed:.3f}s",
            )
            self.assertEqual(packet["schema"], "rso-context-packet/v2")
            started = time.perf_counter()
            matched = query_context(
                database,
                "what color are the widgets",
                path=project_path,
                agent="tester",
                use_cache=False,
            )
            matched_elapsed = time.perf_counter() - started
            self.assertLess(
                matched_elapsed,
                2.0,
                f"tiny-evidence query over 2000 dummy nodes took {matched_elapsed:.3f}s",
            )
            self.assertTrue(
                any(item["status"] == "evidence_found" for item in matched["requirements"]),
                matched["requirements"],
            )

    def test_audit_matrix_maps_restated_source_and_flags_fabricated_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_path = self._project_dir(root)
            database = Database(root / "context.sqlite3")
            ingest_project(database, project_path, agent="tester")
            supported = audit_answer(
                database,
                "what color are the widgets",
                "The factory ships blue widgets.",
                path=project_path,
                agent="tester",
            )
            self.assertEqual(supported["schema"], "rso-match-move-audit/v2")
            self.assertIn("matrix", supported)
            self.assertIn("requirement_to_evidence", supported["matrix"])
            self.assertIn("output_to_evidence", supported["matrix"])
            self.assertIn("partition", supported)
            self.assertEqual(supported["partition"]["max_leaves"], 12)
            mapped = supported["matrix"]["output_to_evidence"]
            self.assertTrue(mapped and mapped[0], supported)
            self.assertEqual(supported["result"], "match_move_complete")
            self.assertFalse(supported["clarification_needed"])
            self.assertIsNone(supported["worst_residual"])
            self.assertFalse(supported["residuals"]["unsupported_outputs"])
            self.assertIn("not prove", supported["warning"])

            fabricated = audit_answer(
                database,
                "what color are the widgets",
                "The factory ships blue widgets. The factory is located on Mars and employs thousands of robots.",
                path=project_path,
                agent="tester",
            )
            self.assertEqual(fabricated["result"], "residuals_open")
            self.assertTrue(fabricated["residuals"]["unsupported_outputs"], fabricated["residuals"])
            self.assertIsNotNone(fabricated["worst_residual"])
            self.assertEqual(fabricated["worst_residual"]["kind"], "unsupported_output")
            self.assertIn("Mars", str(fabricated["worst_residual"]["text"]))


class SliceBTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / "slice-b"
        self.project.mkdir()
        (self.project / "AGENTS.md").write_text(
            "# Policy\n\nWidgets must ship with a serial number.\n",
            encoding="utf-8",
        )
        self.database = Database(self.root / "context.sqlite3")
        ingest_project(self.database, self.project, agent="tester")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_run_budget_decrements_and_floors_at_zero(self) -> None:
        first = query_context(
            self.database, "serial number", path=self.project, agent="tester"
        )
        self.assertEqual(first["run"]["initial"], 8)
        self.assertEqual(first["run"]["remaining"], 7)
        second = query_context(
            self.database, "serial number", path=self.project, agent="tester"
        )
        self.assertEqual(second["run"]["remaining"], 6)
        with self.database.transaction() as connection:
            set_budget(connection, str(first["project"]["id"]), "tester", 3)
        after_set = query_context(
            self.database, "serial number", path=self.project, agent="tester"
        )
        self.assertEqual(after_set["run"]["remaining"], 2)
        with self.database.transaction() as connection:
            set_budget(connection, str(first["project"]["id"]), "tester", 0)
        empty = query_context(
            self.database, "serial number", path=self.project, agent="tester"
        )
        self.assertEqual(empty["schema"], "rso-context-packet/v2")
        self.assertEqual(empty["run"]["remaining"], 0)
        with self.database.connect() as connection:
            remaining = connection.execute(
                "SELECT remaining_budget FROM runs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()[0]
        self.assertEqual(int(remaining), 0)

    def test_solvers_happy_path_skips_hash_and_disagreement_includes_it(self) -> None:
        packet = query_context(
            self.database, "serial number", path=self.project, agent="tester"
        )
        names = [item["name"] for item in packet["checks"]]
        self.assertIn("schema_holds", names)
        self.assertIn("span_exists", names)
        self.assertNotIn("hash_matches", names)
        schema = next(item for item in packet["checks"] if item["name"] == "schema_holds")
        self.assertEqual(schema["result"], "pass")
        self.assertTrue(all(claim["trust_state"] != "verified" for claim in packet["claims"]))

        conflict = self.root / "policy-conflict"
        conflict.mkdir()
        (conflict / "AGENTS.md").write_text(
            "# Policy\n\nStudio must use bounded roots.\n",
            encoding="utf-8",
        )
        (conflict / "README.md").write_text(
            "# Policy\n\nStudio must not use bounded roots.\n",
            encoding="utf-8",
        )
        ingest_project(self.database, conflict, agent="tester")
        disagreed = query_context(
            self.database, "bounded roots", path=conflict, agent="tester"
        )
        self.assertTrue(disagreed["clarification_needed"])
        check_names = [item["name"] for item in disagreed["checks"]]
        self.assertIn("hash_matches", check_names)
        hash_check = next(item for item in disagreed["checks"] if item["name"] == "hash_matches")
        self.assertEqual(hash_check["result"], "pass")
        with self.database.connect() as connection:
            states = [
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT trust_state FROM claims WHERE project_id=?",
                    (disagreed["project"]["id"],),
                )
            ]
        self.assertNotIn("verified", states)

    def test_propose_does_not_rewrite_observed_and_validation_promotes(self) -> None:
        proposed = propose_claim(
            self.database,
            "Agents may file a solve without a plate.",
            agent="tester",
            path=self.project,
        )
        self.assertEqual(proposed["schema"], "rso-claim-proposal/v1")
        self.assertFalse(proposed["already_existed"])
        self.assertEqual(proposed["claim"]["trust_state"], "proposed")
        claim_id = proposed["claim"]["id"]
        with self.database.connect() as connection:
            evidence = connection.execute(
                "SELECT COUNT(*) FROM claim_evidence WHERE claim_id=?", (claim_id,)
            ).fetchone()[0]
            rows = connection.execute(
                "SELECT COUNT(*) FROM claims WHERE normalized_text=?",
                ("agents may file a solve without a plate.",),
            ).fetchone()[0]
        self.assertEqual(int(evidence), 0)
        self.assertEqual(int(rows), 1)

        again = propose_claim(
            self.database,
            "Agents may file a solve without a plate.",
            agent="tester",
            path=self.project,
        )
        self.assertTrue(again["already_existed"])
        self.assertEqual(again["claim"]["id"], claim_id)
        self.assertEqual(again["claim"]["trust_state"], "proposed")
        with self.database.connect() as connection:
            still = connection.execute(
                "SELECT COUNT(*) FROM claims WHERE id=?", (claim_id,)
            ).fetchone()[0]
        self.assertEqual(int(still), 1)

        observed = propose_claim(
            self.database,
            "Widgets must ship with a serial number.",
            agent="tester",
            path=self.project,
        )
        self.assertTrue(observed["already_existed"])
        self.assertEqual(observed["claim"]["trust_state"], "observed")

        recorded = record_validation(
            self.database,
            claim_id,
            validator="owner",
            result="verified",
        )
        self.assertEqual(recorded["claim"]["trust_state"], "verified")

    def test_pending_lists_proposed_then_drops_after_validation(self) -> None:
        proposed = propose_claim(
            self.database,
            "Pending claims must wait for a named validator.",
            agent="tester",
            path=self.project,
        )
        claim_id = proposed["claim"]["id"]
        pending = pending_validations(self.database, agent="tester", path=self.project)
        self.assertEqual(pending["schema"], "rso-pending-validations/v1")
        ids = [item["id"] for item in pending["claims"]]
        self.assertIn(claim_id, ids)
        record_validation(self.database, claim_id, validator="owner", result="verified")
        after = pending_validations(self.database, agent="tester", path=self.project)
        self.assertNotIn(claim_id, [item["id"] for item in after["claims"]])

    def test_migrate_v3_adds_run_budgets(self) -> None:
        db_path = self.root / "migrate-v3.sqlite3"
        database = Database(db_path)
        database.initialize()
        with database.connect() as connection:
            connection.execute("DROP TABLE run_budgets")
            connection.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
        database.initialize()
        with database.connect() as connection:
            version = int(
                connection.execute(
                    "SELECT value FROM meta WHERE key='schema_version'"
                ).fetchone()[0]
            )
            self.assertEqual(version, SCHEMA_VERSION)
            self.assertEqual(version, 5)
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='run_budgets'"
            ).fetchone()
            self.assertIsNotNone(table)


class SliceCTests(unittest.TestCase):
    def test_search_order_project_domain_shared(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = Database(root / "context.sqlite3")

            core = root / "core"
            core.mkdir()
            (core / "README.md").write_text(
                "shared canon: widgets are blue\n", encoding="utf-8"
            )
            register_project(database, core, agent="tester", scope="shared")
            ingest_project(database, core, agent="tester")

            show = root / "show"
            show.mkdir()
            (show / "README.md").write_text(
                "show bible: widgets ship weekly\n", encoding="utf-8"
            )
            register_project(database, show, agent="tester", scope="domain")
            ingest_project(database, show, agent="tester")

            shot = show / "shot"
            shot.mkdir()
            (shot / "README.md").write_text(
                "shot note: widgets are tracked\n", encoding="utf-8"
            )
            register_project(database, shot, agent="tester", scope="project")
            ingest_project(database, shot, agent="tester")

            other = root / "other"
            other.mkdir()
            (other / "README.md").write_text(
                "unrelated banana widgets\n", encoding="utf-8"
            )
            register_project(database, other, agent="tester", scope="project")
            ingest_project(database, other, agent="tester")

            packet = query_context(database, "widgets", path=shot, agent="tester")
            order = packet["search_order"]
            self.assertEqual(
                [item["scope"] for item in order],
                ["project", "domain", "shared"],
                order,
            )
            names = [item["name"] for item in order]
            self.assertEqual(names, ["shot", "show", "core"], order)
            with database.connect() as connection:
                wanted = {
                    row["display_name"]: row["id"]
                    for row in connection.execute(
                        "SELECT id, display_name FROM projects"
                    )
                }
            self.assertEqual(order[0]["id"], wanted["shot"])
            self.assertEqual(order[1]["id"], wanted["show"])
            self.assertEqual(order[2]["id"], wanted["core"])
            self.assertNotIn("other", names)
            texts = [str(hit["text"]) for hit in packet["evidence"]]
            joined = "\n".join(texts)
            self.assertIn("shot note: widgets are tracked", joined)
            self.assertIn("show bible: widgets ship weekly", joined)
            self.assertIn("shared canon: widgets are blue", joined)
            self.assertNotIn("banana", joined)
            self.assertEqual(packet["evidence"][0]["scope"], "project")
            self.assertEqual(packet["evidence"][0]["project_name"], "shot")

            def first_index(needle: str) -> int:
                for index, text in enumerate(texts):
                    if needle in text:
                        return index
                self.fail(f"missing {needle} in {texts}")
                return -1

            self.assertLess(first_index("shot note"), first_index("show bible"))
            self.assertLess(first_index("show bible"), first_index("shared canon"))
            for hit in packet["evidence"]:
                self.assertIn("scope", hit)
                self.assertIn("project_name", hit)

    def test_takes_counts_older_versions_without_dumping_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "takes-demo"
            project.mkdir()
            note = project / "NOTE.md"
            old_body = "unique-old-take-body-alpha-zyxwv"
            note.write_text(f"# Note\n\n{old_body}\n", encoding="utf-8")
            database = Database(root / "context.sqlite3")
            ingest_project(database, project, agent="tester")
            note.write_text("# Note\n\nunique-new-take-body-beta-qwerty\n", encoding="utf-8")
            ingest_project(database, project, agent="tester")
            resume = resume_context(database, path=project, agent="tester")
            self.assertEqual(resume["schema"], "rso-context-resume/v1")
            takes = resume["takes"]
            self.assertGreaterEqual(takes["current"], 1)
            self.assertGreaterEqual(takes["older_versions"], 1)
            self.assertIn("note", takes)
            self.assertTrue(takes["note"])
            blob = json.dumps(resume)
            self.assertNotIn(old_body, blob)

    def test_watch_path_once_does_not_register_sibling(self) -> None:
        import io
        from contextlib import redirect_stdout
        from rso_context.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            watched = root / "watched"
            watched.mkdir()
            (watched / "AGENTS.md").write_text(
                "# Watched\n\nStay bounded.\n", encoding="utf-8"
            )
            sibling = root / "sibling"
            sibling.mkdir()
            (sibling / "AGENTS.md").write_text(
                "# Sibling\n\nMust not be registered.\n", encoding="utf-8"
            )
            db_path = root / "context.sqlite3"
            database = Database(db_path)
            register_project(database, watched, agent="tester")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(
                    [
                        "--db",
                        str(db_path),
                        "watch",
                        "--path",
                        str(watched),
                        "--once",
                        "--agent",
                        "tester",
                    ]
                )
            self.assertEqual(code, 0, buf.getvalue())
            payload = json.loads(buf.getvalue())
            self.assertIn(payload["changed_projects"], (0, 1), payload)
            with database.connect() as connection:
                names = [
                    row[0]
                    for row in connection.execute("SELECT display_name FROM projects")
                ]
                paths = [
                    row[0]
                    for row in connection.execute(
                        "SELECT resolved_path FROM project_aliases"
                    )
                ]
            self.assertIn("watched", names)
            self.assertNotIn("sibling", names)
            self.assertFalse(any("sibling" in str(path) for path in paths), paths)



class PointerIndexTests(unittest.TestCase):
    def test_ingest_stores_empty_chunk_text_and_query_reconstructs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "pointer-demo"
            project.mkdir()
            sentence = "The xylophone-lemur-context-pointer-7f3a must remain searchable."
            (project / "NOTE.md").write_text(f"# Note\n\n{sentence}\n", encoding="utf-8")
            database = Database(root / "context.sqlite3")
            ingest_project(database, project, agent="tester")
            with database.connect() as connection:
                texts = [row[0] for row in connection.execute("SELECT text FROM chunks")]
                hashes = [row[0] for row in connection.execute("SELECT chunk_hash FROM chunks")]
            self.assertTrue(texts)
            self.assertTrue(all(text == "" for text in texts), texts)
            self.assertTrue(all(item for item in hashes))
            packet = query_context(database, "xylophone-lemur-context-pointer-7f3a", path=project, agent="tester")
            joined = "\n".join(str(hit["text"]) for hit in packet["evidence"])
            self.assertIn(sentence, joined)
            self.assertTrue(packet["evidence"])
            self.assertTrue(all(hit.get("stale") is False for hit in packet["evidence"]), packet["evidence"])

    def test_rename_file_updates_one_source_and_query_works(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "rename-demo"
            project.mkdir()
            sentence = "The rename-plate-quokka-9c21 must stay on one source."
            original = project / "OLD.md"
            original.write_text(f"# Old\n\n{sentence}\n", encoding="utf-8")
            database = Database(root / "context.sqlite3")
            ingest_project(database, project, agent="tester")
            original.rename(project / "NEW.md")
            ingest = ingest_project(database, project, agent="tester")
            self.assertGreaterEqual(int(ingest["counters"]["files_renamed"]), 1, ingest["counters"])
            with database.connect() as connection:
                rows = connection.execute(
                    "SELECT relative_path, active FROM sources ORDER BY id"
                ).fetchall()
            posix = [(row[0].replace("\\", "/"), int(row[1])) for row in rows]
            self.assertEqual(len(posix), 1, posix)
            self.assertTrue(posix[0][0].endswith("NEW.md"), posix)
            self.assertEqual(posix[0][1], 1)
            packet = query_context(database, "rename-plate-quokka-9c21", path=project, agent="tester")
            joined = "\n".join(str(hit["text"]) for hit in packet["evidence"])
            self.assertIn(sentence, joined)
            self.assertTrue(any(str(hit["relative_path"]).endswith("NEW.md") for hit in packet["evidence"]))

    def test_alter_and_save_ingest_returns_new_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "alter-demo"
            project.mkdir()
            note = project / "NOTE.md"
            note.write_text("# Note\n\nold-plate-body-alpha-zyxwv must remain.\n", encoding="utf-8")
            database = Database(root / "context.sqlite3")
            ingest_project(database, project, agent="tester")
            note.write_text("# Note\n\nnew-plate-body-beta-qwerty must replace the prior take.\n", encoding="utf-8")
            ingest = ingest_project(database, project, agent="tester")
            self.assertGreaterEqual(int(ingest["counters"]["files_updated"]), 1, ingest["counters"])
            packet = query_context(database, "new-plate-body-beta-qwerty", path=project, agent="tester")
            joined = "\n".join(str(hit["text"]) for hit in packet["evidence"])
            self.assertIn("new-plate-body-beta-qwerty", joined)
            self.assertNotIn("old-plate-body-alpha-zyxwv", joined)
            self.assertTrue(all(hit.get("stale") is False for hit in packet["evidence"]))

    def test_compact_pointers_clears_live_bodies_and_keeps_missing_file_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "compact-demo"
            project.mkdir()
            live = project / "LIVE.md"
            gone = project / "GONE.md"
            live_sentence = "The compact-live-wombat-44ae must reconstruct after compact."
            gone_sentence = "The compact-legacy-ibis-81df must stay stored when the file is missing."
            live.write_text(f"# Live\n\n{live_sentence}\n", encoding="utf-8")
            gone.write_text(f"# Gone\n\n{gone_sentence}\n", encoding="utf-8")
            database = Database(root / "context.sqlite3")
            ingest_project(database, project, agent="tester")
            with database.transaction() as connection:
                connection.execute(
                    "UPDATE chunks SET text=? WHERE id IN "
                    "(SELECT c.id FROM chunks c JOIN source_versions v ON v.id=c.source_version_id "
                    "JOIN sources s ON s.id=v.source_id WHERE s.relative_path=?)",
                    (live_sentence, "LIVE.md"),
                )
                connection.execute(
                    "UPDATE chunks SET text=? WHERE id IN "
                    "(SELECT c.id FROM chunks c JOIN source_versions v ON v.id=c.source_version_id "
                    "JOIN sources s ON s.id=v.source_id WHERE s.relative_path=?)",
                    (gone_sentence, "GONE.md"),
                )
            gone.unlink()
            result = compact_pointers(database)
            self.assertEqual(result["schema"], SCHEMA_VERSION)
            self.assertGreaterEqual(int(result["cleared"]), 1, result)
            self.assertGreaterEqual(int(result["kept_legacy"]), 1, result)
            self.assertIn("bytes_before", result)
            self.assertIn("bytes_after", result)
            with database.connect() as connection:
                live_texts = [
                    row[0]
                    for row in connection.execute(
                        "SELECT c.text FROM chunks c JOIN source_versions v ON v.id=c.source_version_id "
                        "JOIN sources s ON s.id=v.source_id WHERE s.relative_path=?",
                        ("LIVE.md",),
                    )
                ]
                gone_texts = [
                    row[0]
                    for row in connection.execute(
                        "SELECT c.text FROM chunks c JOIN source_versions v ON v.id=c.source_version_id "
                        "JOIN sources s ON s.id=v.source_id WHERE s.relative_path=?",
                        ("GONE.md",),
                    )
                ]
            self.assertTrue(live_texts)
            self.assertTrue(all(text == "" for text in live_texts), live_texts)
            self.assertTrue(gone_texts)
            self.assertTrue(any(gone_sentence in str(text) for text in gone_texts), gone_texts)
            packet = query_context(database, "compact-live-wombat-44ae", path=project, agent="tester")
            joined = "\n".join(str(hit["text"]) for hit in packet["evidence"])
            self.assertIn(live_sentence, joined)
            self.assertTrue(all(hit.get("stale") is False for hit in packet["evidence"] if "LIVE.md" in str(hit["relative_path"])))

    def test_schema_v4_to_v5_does_not_wipe_chunk_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "migrate-demo"
            project.mkdir()
            (project / "NOTE.md").write_text("# Note\n\nmigrate-keep-body-okapi-12zz\n", encoding="utf-8")
            database = Database(root / "context.sqlite3")
            ingest_project(database, project, agent="tester")
            with database.transaction() as connection:
                connection.execute("UPDATE chunks SET text='migrate-keep-body-okapi-12zz'")
                connection.execute("UPDATE meta SET value='4' WHERE key='schema_version'")
            database.initialize()
            with database.connect() as connection:
                version = int(
                    connection.execute(
                        "SELECT value FROM meta WHERE key='schema_version'"
                    ).fetchone()[0]
                )
                texts = [row[0] for row in connection.execute("SELECT text FROM chunks")]
            self.assertEqual(version, 5)
            self.assertTrue(any(text == "migrate-keep-body-okapi-12zz" for text in texts), texts)

    def test_admin_html_contains_project_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "AdminViewerName"
            project.mkdir()
            (project / "README.md").write_text(
                "# Admin\n\nAdmin viewer must list this project.\n",
                encoding="utf-8",
            )
            database = Database(root / "context.sqlite3")
            ingest_project(database, project, agent="tester")
            page = build_admin_html(database)
            self.assertIn("AdminViewerName", page)
            self.assertIn("<table", page)
            self.assertNotIn("<script>", page.lower())


if __name__ == "__main__":
    unittest.main()
