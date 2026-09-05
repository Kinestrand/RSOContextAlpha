from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from rso_context.audit import record_validation
from rso_context.db import Database
from rso_context.identity import inspect_project, register_project, sha256_text
from rso_context.ingest import ingest_project
from rso_context.query import query_context
from rso_context.resume import resume_context


class IdentityStabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = Database(self.root / 'test.sqlite3')
        self.workspace = self.root / 'project'
        self.workspace.mkdir()
        (self.workspace / 'AGENTS.md').write_text('Decision: tests must stay bounded.\n', encoding='utf-8')
        (self.workspace / 'package.json').write_text('{"name":"fixture"}', encoding='utf-8')

    def test_marker_edits_additions_and_removals_keep_identity_across_agents(self):
        original = register_project(self.database, self.workspace, agent='first')['project']['id']
        mutations = [
            lambda: (self.workspace / 'AGENTS.md').write_text('Decision: use changed policy.\n', encoding='utf-8'),
            lambda: (self.workspace / 'README.md').write_text('New shape', encoding='utf-8'),
            lambda: (self.workspace / 'pyproject.toml').write_text('[project]\nname="fixture"', encoding='utf-8'),
            lambda: (self.workspace / 'package.json').unlink(),
            lambda: (self.workspace / 'AGENTS.md').unlink(),
            lambda: (self.workspace / 'pyproject.toml').unlink(),
        ]
        for index, mutate in enumerate(mutations):
            mutate()
            with self.subTest(mutation=index):
                current = register_project(self.database, self.workspace, agent=f'agent-{index}')
                self.assertEqual(current['project']['id'], original)
        with self.database.connect() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM projects').fetchone()[0], 1)

    def test_identical_independent_folders_do_not_merge(self):
        other = self.root / 'independent'
        other.mkdir()
        for child in self.workspace.iterdir():
            (other / child.name).write_bytes(child.read_bytes())
        first = register_project(self.database, self.workspace)['project']['id']
        second = register_project(self.database, other)['project']['id']
        self.assertNotEqual(first, second)
        with self.database.connect() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM possible_project_matches').fetchone()[0], 1)

    def test_legacy_structure_identity_preserves_history_and_validation(self):
        identity = inspect_project(self.workspace)
        legacy_key = 'structure:' + sha256_text(json.dumps(identity.structure_records, ensure_ascii=False, sort_keys=True))
        # Create the old-version fixture through the normal registration and
        # ingestion APIs, without editing database records or any live index.
        with patch('rso_context.identity.inspect_project', return_value=replace(identity, canonical_key=legacy_key)):
            original = ingest_project(self.database, self.workspace, agent='legacy')['project']['id']
        with self.database.connect() as connection:
            claim = connection.execute('SELECT id FROM claims WHERE project_id=?', (original,)).fetchone()[0]
            source_ids = [row[0] for row in connection.execute('SELECT id FROM sources WHERE project_id=? ORDER BY id', (original,))]
        record_validation(self.database, claim, validator='identity-regression-fixture', result='verified')
        (self.workspace / 'package.json').write_text('{"name":"fixture","version":"2"}', encoding='utf-8')
        current = ingest_project(self.database, self.workspace, agent='new-agent')
        self.assertEqual(current['project']['id'], original)
        packet = resume_context(self.database, path=self.workspace, agent='new-agent')
        self.assertEqual(packet['project']['canonical_key'], legacy_key)
        self.assertGreater(packet['takes']['older_versions'], 0)
        with self.database.connect() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM projects').fetchone()[0], 1)
            self.assertEqual(source_ids, [row[0] for row in connection.execute('SELECT id FROM sources WHERE project_id=? ORDER BY id', (original,))])
            saved = connection.execute('SELECT * FROM validations WHERE claim_id=?', (claim,)).fetchone()
            self.assertEqual(saved['validator'], 'identity-regression-fixture')
            self.assertEqual(saved['result'], 'verified')

    def test_legacy_structure_alias_is_used_even_before_any_file_changes(self):
        identity = inspect_project(self.workspace)
        legacy_key = 'structure:' + sha256_text(json.dumps(identity.structure_records, ensure_ascii=False, sort_keys=True))
        with patch('rso_context.identity.inspect_project', return_value=replace(identity, canonical_key=legacy_key)):
            original = register_project(self.database, self.workspace, agent='legacy')['project']['id']
        current = register_project(self.database, self.workspace, agent='another-agent')
        self.assertEqual(current['project']['id'], original)
        self.assertEqual(current['match']['method'], 'resolved_path')
        other = self.root / 'identical-copy'
        other.mkdir()
        for child in self.workspace.iterdir():
            (other / child.name).write_bytes(child.read_bytes())
        self.assertNotEqual(register_project(self.database, other)['project']['id'], original)

    def test_directory_alias_does_not_override_git_identity(self):
        original = register_project(self.database, self.workspace)['project']['id']
        subprocess.run(['git', '-C', str(self.workspace), 'init'], check=True, capture_output=True)
        current = register_project(self.database, self.workspace)['project']
        self.assertEqual(current['kind'], 'git')
        self.assertNotEqual(current['id'], original)

    def test_preexisting_split_resumes_the_project_selected_for_ingestion(self):
        identity = inspect_project(self.workspace)
        with patch('rso_context.identity.inspect_project', return_value=replace(identity, canonical_key='structure:old')):
            old = ingest_project(self.database, self.workspace, agent='old-agent')['project']['id']
        # Reproduce two historical structure identities with aliases at the
        # same folder using public registration, not direct database writes.
        split = replace(identity, canonical_key='structure:new')
        with patch('rso_context.identity.inspect_project', return_value=replace(split, resolved_path=str(self.root / 'old-spelling'))):
            new = register_project(self.database, self.workspace, agent='new-agent')['project']['id']
        with patch('rso_context.identity.inspect_project', return_value=split):
            register_project(self.database, self.workspace, agent='new-agent')
        selected = ingest_project(self.database, self.workspace, agent='repair-agent')['project']['id']
        self.assertEqual(selected, new)
        self.assertNotEqual(selected, old)
        self.assertEqual(resume_context(self.database, path=self.workspace)['project']['id'], selected)
        self.assertEqual(query_context(self.database, 'tests must stay bounded', path=self.workspace, agent='repair-agent')['project']['id'], selected)
        with self.database.connect() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM projects').fetchone()[0], 2)

    def test_git_worktrees_stay_distinct_and_edits_stay_stable(self):
        def git(path, *args):
            result = subprocess.run(['git', '-C', str(path), *args], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        git(self.workspace, 'init')
        git(self.workspace, 'add', 'AGENTS.md', 'package.json')
        git(self.workspace, '-c', 'user.name=Identity Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'fixture')
        other = self.root / 'worktree'
        git(self.workspace, 'worktree', 'add', '--detach', str(other))
        first = register_project(self.database, self.workspace)['project']
        second = register_project(self.database, other)['project']
        self.assertEqual(first['kind'], 'git')
        self.assertNotEqual(first['id'], second['id'])
        (self.workspace / 'AGENTS.md').write_text('Decision: edited Git policy.\n', encoding='utf-8')
        self.assertEqual(register_project(self.database, self.workspace)['project']['id'], first['id'])


if __name__ == '__main__':
    unittest.main()
