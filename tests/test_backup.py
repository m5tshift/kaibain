"""Backup security tests: disposable data only; no project tools/VMs/models."""
import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from kaibain import backup
from kaibain.store import Store
from kaibain.result_review import bundle
from kaibain.recovery import load_checkpoint


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()  # macOS: /var -> /private/var
        self.source = self.folder / 'source'
        self.source.mkdir()
        self.store = Store(self.source / 'tasks.sqlite3')
        self.goal = self.store.create('Backup only')
        self.project = {'repo': str(self.folder / 'NEVER_OPEN_PROJECT'), 'commit': 'a' * 40,
                        'files': ['app.py'], 'writable': ['app.py'],
                        'checks': ['python', '-m', 'unittest'], 'acceptance': ['Fixture']}
        self.run = self.store.reserve_agent_run(self.goal['id'], self.goal['version'], 'fixture', [], project=self.project)
        self.run_id = self.run['id']
        self.development = self.source / '.agent-runs' / self.run_id / 'development'
        self.export = self.development / 'result'
        self.body = b'raise RuntimeError("PROJECT MUST NEVER EXECUTE")\n'
        patch_bytes = b'+fixture bytes\n'
        self.export.mkdir(parents=True)
        (self.export / 'files').mkdir()
        (self.export / 'files' / 'app.py').write_bytes(self.body)
        (self.export / 'changes.diff').write_bytes(patch_bytes)
        raw = json.dumps({'backend': 'microsandbox', 'isolation': 'microvm',
                          'base_commit': 'a' * 40, 'deleted': [], 'patch_sha256': sha(patch_bytes),
                          'files': [{'path': 'app.py', 'bytes': len(self.body), 'sha256': sha(self.body)}]}).encode()
        (self.export / 'manifest.json').write_bytes(raw)
        self.manifest_sha = sha(raw)
        self.verification = {'status': 'passed', 'tests_run': True,
                             'manifest_sha256': sha(raw), 'patch_sha256': sha(patch_bytes)}
        self.store.finish_agent_run(self.run_id, 'completed', result={'verification': self.verification})
        self.archive = self.folder / 'backup.zip'
        self.destination = self.folder / 'restored' / 'renamed.sqlite3'

    def create(self):
        return backup.create_backup(self.store.path, self.archive)

    def invalid(self, function):
        with self.assertRaises((ValueError, OSError)):
            function()

    def rewrite(self, transform):
        with zipfile.ZipFile(self.archive) as source:
            items = [(info.filename, source.read(info)) for info in source.infolist()]
        target = self.folder / 'mutated.zip'
        with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_STORED) as output:
            for name, body in transform(items):
                output.writestr(name, body)
        return target

    def assert_bad_archive(self, archive):
        self.invalid(lambda: backup.inspect_backup(archive))
        self.invalid(lambda: backup.restore_backup(archive, self.destination))
        self.assertFalse(self.destination.parent.exists())
        self.assertFalse(list(self.folder.glob('.kaibain-restore-*')))

    def test_roundtrip_review_checkpoint_apply_receipts_and_original_paths(self):
        checkpoint = self.development / 'checkpoints' / '000'
        import shutil
        shutil.copytree(self.export, checkpoint)
        with self.store.connection(write=True, control=True) as db:
            db.execute('INSERT INTO run_checkpoints VALUES (?,?,?,?,?,?,?,?,?)',
                       ('cp', self.run_id, 0, self.manifest_sha, 1, self.goal['id'], 1, 0, 'fixture'))
            receipt = {'run_id': self.run_id, 'manifest_sha256': self.manifest_sha,
                       'repo': self.project['repo'], 'branch': 'kaibain/' + self.run_id, 'commit': 'b' * 40}
            db.execute('INSERT INTO run_applications VALUES (?,?)', (self.run_id, json.dumps(receipt)))
        before = self.store.revision()
        with patch('subprocess.Popen', side_effect=AssertionError('Execution forbidden')):
            manifest = self.create()
            self.assertEqual(manifest, backup.inspect_backup(self.archive))
            self.assertEqual(manifest, backup.restore_backup(self.archive, self.destination))
        self.assertEqual(self.store.revision(), before)
        self.assertEqual(json.loads(json.dumps(manifest)), manifest)
        # Use the actual review/checkpoint readers on the restored DB. No Git.
        restored = Store(self.destination)
        run = restored.agent_runs()[0]
        self.assertEqual(run['project'], self.project)
        self.assertEqual(run['decision'], 'pending')
        self.assertEqual(bundle(restored, run)[2], {'app.py': self.body})
        with restored.connection() as db:
            saved = dict(db.execute('SELECT * FROM run_checkpoints').fetchone())
            self.assertEqual(json.loads(db.execute('SELECT receipt FROM run_applications').fetchone()[0]), receipt)
        self.assertEqual(load_checkpoint(restored, run, saved)['verification']['manifest_sha256'], self.manifest_sha)
        self.assertFalse((self.folder / 'NEVER_OPEN_PROJECT').exists())
        self.assertFalse(list(self.folder.glob('.kaibain-backup-*')))

    def test_wal_snapshot_includes_committed_rows_and_never_changes_live_db(self):
        with closing(sqlite3.connect(self.store.path)) as live:
            self.assertEqual(live.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            live.execute("UPDATE tasks SET description='committed WAL' WHERE id=?", (self.goal['id'],))
            live.commit()
            before = live.execute('SELECT * FROM tasks').fetchall()
            self.create()
            backup.restore_backup(self.archive, self.destination)
            with closing(sqlite3.connect(self.destination)) as restored:
                self.assertEqual(restored.execute('SELECT * FROM tasks').fetchall(), before)
            self.assertEqual(live.execute('SELECT * FROM tasks').fetchall(), before)

    def test_active_runs_are_rejected_without_archive_or_side_effects(self):
        for status in ('queued', 'running', 'cancelling'):
            with self.subTest(status=status):
                with self.store.connection(write=True, control=True) as db:
                    db.execute('UPDATE agent_runs SET status=?', (status,))
                self.invalid(self.create)
                self.assertFalse(self.archive.exists())
                self.assertFalse(list(self.folder.glob('.kaibain-backup-*')))

    def test_write_lock_covers_copy_and_blocks_launch_and_receipt_edits(self):
        original = backup._inventory
        attempts = []

        def inventory(root):
            with closing(sqlite3.connect(self.store.path, timeout=0.02)) as other:
                with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'):
                    other.execute("UPDATE agent_runs SET status='running',version=version+1")
                attempts.append(True)
            return original(root)
        with patch.object(backup, '_inventory', side_effect=inventory):
            self.create()
        self.assertEqual(len(attempts), 2)
        self.assertEqual(self.store.agent_runs()[0]['status'], 'completed')

    def test_mutating_artifact_set_is_rejected(self):
        original = backup._inventory
        calls = 0

        def inventory(root):
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.development / 'new.log').write_text('raced')
            return original(root)
        with patch.object(backup, '_inventory', side_effect=inventory):
            self.invalid(self.create)
        self.assertFalse(self.archive.exists())

    def test_existing_archive_and_any_existing_restore_directory_are_preserved(self):
        self.archive.write_bytes(b'foreign archive')
        self.invalid(self.create)
        self.assertEqual(self.archive.read_bytes(), b'foreign archive')
        self.archive.unlink()
        self.create()
        self.destination.parent.mkdir()
        self.invalid(lambda: backup.restore_backup(self.archive, self.destination))
        self.destination.write_bytes(b'foreign DB')
        runs = self.destination.parent / '.agent-runs'
        runs.mkdir()
        (runs / 'foreign').write_text('preserved')
        self.invalid(lambda: backup.restore_backup(self.archive, self.destination))
        self.assertEqual(self.destination.read_bytes(), b'foreign DB')
        self.assertEqual((runs / 'foreign').read_text(), 'preserved')
        self.assertEqual(sorted(p.name for p in self.destination.parent.iterdir()), ['.agent-runs', 'renamed.sqlite3'])

    def test_destination_appearing_at_publication_is_never_removed(self):
        self.create()
        original = backup._rename_new

        def race(source, destination):
            destination.mkdir()
            (destination / 'foreign').write_bytes(b'owned by someone else')
            original(source, destination)
        with patch.object(backup, '_rename_new', side_effect=race):
            self.invalid(lambda: backup.restore_backup(self.archive, self.destination))
        self.assertEqual((self.destination.parent / 'foreign').read_bytes(), b'owned by someone else')
        self.assertFalse(self.destination.exists())

    def test_excludes_local_configs_credentials_and_unrelated_files(self):
        for folder in (self.source, self.development):
            for name in ('.mcp.json', '.env', 'id_rsa', 'private.key', 'agent-runtime.json'):
                (folder / name).write_text('DO NOT INCLUDE')
            for name in ('.venv', '.mcp', '.local', 'config'):
                (folder / name).mkdir()
                (folder / name / 'secret').write_text('DO NOT INCLUDE')
        self.create()
        with zipfile.ZipFile(self.archive) as archive:
            self.assertTrue(all(b'DO NOT INCLUDE' not in archive.read(name) for name in archive.namelist()))
        self.assertEqual(backup.inspect_backup(self.archive)['version'], 1)

    def test_missing_db_never_creates_a_database(self):
        missing = self.folder / 'missing.sqlite3'
        self.invalid(lambda: backup.create_backup(missing, self.archive))
        self.assertFalse(missing.exists())

    def test_corrupt_files_and_manifest_hashes_are_rejected(self):
        self.create()
        name = '.agent-runs/' + self.run_id + '/development/result/files/app.py'
        corrupted = self.rewrite(lambda items: [(path, body + b'!' if path == name else body) for path, body in items])
        self.assert_bad_archive(corrupted)

        def mutate(items):
            for path, body in items:
                if path == 'manifest.json':
                    manifest = json.loads(body)
                    manifest['files'][0]['sha256'] = '0' * 64
                    body = json.dumps(manifest).encode()
                yield path, body
        self.assert_bad_archive(self.rewrite(mutate))

    def test_invalid_sqlite_rehashed_by_attacker_still_fails(self):
        self.create()

        def corrupt(items):
            raw = b'not SQLite'
            for path, body in items:
                if path == backup.DATABASE:
                    body = raw
                if path == 'manifest.json':
                    manifest = json.loads(body)
                    entry = next(e for e in manifest['files'] if e['path'] == backup.DATABASE)
                    entry.update(bytes=len(raw), sha256=sha(raw))
                    body = json.dumps(manifest).encode()
                yield path, body
        self.assert_bad_archive(self.rewrite(corrupt))

    def test_version_receipt_and_format_versions_are_checked(self):
        self.create()
        for change in ({'version': 999}, {'version': True}, {'consistency': {}}, {'database': '../outside'}):
            with self.subTest(change=change):
                def mutate(items):
                    for path, body in items:
                        if path == 'manifest.json':
                            manifest = json.loads(body)
                            manifest.update(change)
                            body = json.dumps(manifest).encode()
                        yield path, body
                self.assert_bad_archive(self.rewrite(mutate))

    def test_zip_slip_absolute_ads_reserved_and_separator_attacks(self):
        self.create()
        for name in ('../outside', '/outside', 'C:/outside', '.agent-runs/r/../../outside',
                     '.agent-runs/r/a\\outside', '.agent-runs/r/x:stream', '.agent-runs/r/CON.txt',
                     '.agent-runs/r/end.', '.agent-runs//a', '.agent-runs/r/.env', '.agent-runs/r/a\x00b'):
            with self.subTest(name=name):
                attacked = self.rewrite(lambda items: [*items, (name, b'attack')])
                self.assert_bad_archive(attacked)
        self.assertFalse((self.folder / 'outside').exists())

    def test_duplicates_case_unicode_and_file_directory_conflicts(self):
        self.create()
        for extra in ([('manifest.json', b'{}')],
                      [('.agent-runs/r/Foo', b'1'), ('.agent-runs/r/foo', b'2')],
                      [('.agent-runs/r/é', b'1'), ('.agent-runs/r/e\u0301', b'2')],
                      [('.agent-runs/r/a', b'1'), ('.agent-runs/r/a/b', b'2')]):
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                attacked = self.rewrite(lambda items: [*items, *extra])
            self.assert_bad_archive(attacked)

    def test_zip_symlink_and_bomb_and_bounded_limits(self):
        self.create()
        attacked = self.folder / 'attack.zip'
        info = zipfile.ZipInfo('.agent-runs/r/link')
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(attacked, 'w') as archive:
            archive.writestr(info, '../../outside')
        self.assert_bad_archive(attacked)
        with zipfile.ZipFile(attacked, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('.agent-runs/r/bomb', b'0' * 1000000)
            archive.writestr('manifest.json', '{}')
        self.assert_bad_archive(attacked)
        for limit in ('MAX_FILE_BYTES', 'MAX_FILES', 'MAX_TOTAL_BYTES', 'MAX_MANIFEST_BYTES', 'MAX_ARCHIVE_BYTES', 'MAX_CENTRAL_BYTES'):
            with self.subTest(limit=limit), patch.object(backup, limit, 1):
                self.assert_bad_archive(self.archive)

    def test_rehashed_artifacts_still_must_match_database_receipts(self):
        self.create()
        name = '.agent-runs/' + self.run_id + '/development/result/manifest.json'

        def mutate(items):
            changed = next(body for path, body in items if path == name) + b' '
            for path, body in items:
                if path == name:
                    body = changed
                elif path == 'manifest.json':
                    manifest = json.loads(body)
                    entry = next(e for e in manifest['files'] if e['path'] == name)
                    entry.update(bytes=len(changed), sha256=sha(changed))
                    body = json.dumps(manifest).encode()
                yield path, body
        self.assert_bad_archive(self.rewrite(mutate))

    def test_source_corrupt_artifacts_foreign_keys_and_db_versions_fail(self):
        path = self.export / 'files' / 'app.py'
        original = path.read_bytes()
        path.write_bytes(b'changed')
        self.invalid(self.create)
        path.write_bytes(original)
        with closing(sqlite3.connect(self.store.path)) as db:
            db.execute("UPDATE tasks SET parent_id='missing'")
            db.commit()
        self.invalid(self.create)
        with closing(sqlite3.connect(self.store.path)) as db:
            db.execute('UPDATE tasks SET parent_id=NULL')
            db.execute('PRAGMA user_version=99')
            db.commit()
        self.invalid(self.create)
        self.assertFalse(self.archive.exists())

    def test_symlink_sources_archives_and_destinations_are_forbidden(self):
        link = self.development / 'link'
        try:
            link.symlink_to(self.export / 'files' / 'app.py')
        except OSError:
            self.skipTest('OS does not grant symlink creation')
        self.invalid(self.create)
        link.unlink()
        self.create()
        linked_archive = self.folder / 'link.zip'
        linked_archive.symlink_to(self.archive)
        self.invalid(lambda: backup.inspect_backup(linked_archive))
        linked_parent = self.folder / 'linked'
        linked_parent.symlink_to(self.source, target_is_directory=True)
        self.invalid(lambda: backup.create_backup(linked_parent / 'tasks.sqlite3', self.folder / 'other.zip'))
        self.invalid(lambda: backup.restore_backup(self.archive, linked_parent / 'new' / 'db'))
        self.assertFalse((self.source / 'new').exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows junction protection')
    def test_windows_junctions_are_rejected_including_ancestors(self):
        junction = self.development / 'junction'
        target = self.folder / 'junction-target'
        target.mkdir()
        (target / 'secret').write_text('private')
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(target)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        try:
            self.invalid(self.create)
            self.invalid(lambda: backup.restore_backup(self.folder / 'missing.zip', junction / 'new' / 'db'))
        finally:
            os.rmdir(junction)  # Remove only the junction, never its target tree.
        self.assertEqual((target / 'secret').read_text(), 'private')

    def test_hardlinked_artifacts_and_archive_are_rejected(self):
        link = self.folder / 'hardlink'
        original = self.export / 'files' / 'app.py'
        os.link(original, link)
        self.invalid(self.create)
        link.unlink()
        self.create()
        os.link(self.archive, link)
        self.invalid(lambda: backup.inspect_backup(self.archive))

    def test_stored_output_handles_highly_compressible_regular_artifacts(self):
        (self.development / 'zeros.log').write_bytes(b'0' * 1000000)
        self.create()
        backup.restore_backup(self.archive, self.destination)
        self.assertEqual((self.destination.parent / '.agent-runs' / self.run_id / 'development' / 'zeros.log').stat().st_size, 1000000)


if __name__ == '__main__':
    unittest.main()
