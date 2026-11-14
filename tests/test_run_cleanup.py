"""Terminal cleanup with isolated SQLite and a fixed, non-VM adapter fixture.

These checks exercise ownership bookkeeping, not sandbox/model execution. Only
the controlled concurrency test replaces cleanup_owned; normal receipt tests
run a small trusted Python process through the real ProcessScope.
"""
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from kaibain.agent_runtime import AgentRuntime, cleanup_owned
from kaibain import backup
from kaibain.process_scope import ProcessScope
from kaibain.recovery import scope_inactive
from kaibain.store import DomainError, Store
from kaibain.web import make_server


DEAD_PID = 999999999
ADAPTER = '''"""Test fixture only: no project execution, VM or model access."""
import argparse,json,os,sys
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--fixture-token',required=True)
p.add_argument('--cleanup',action='store_true',required=True)
p.add_argument('--output',required=True)
a=p.parse_args()
base=Path(__file__).parent
folder=Path(a.output).parent
control=json.loads((base/'control.json').read_text())
ack=folder/'cleanup.json'
observation={'argv':sys.argv[1:],'run_id':os.environ.get('KAIBAIN_RUN_ID'),
             'control_token_present':'KAIBAIN_CONTROL_TOKEN' in os.environ,
             'previous_ack':json.loads(ack.read_text())}
with (base/'calls.jsonl').open('a',encoding='utf8') as f:
    f.write(json.dumps(observation)+'\\n')
if control.get('remove'):ack.unlink()
elif 'raw' in control:ack.write_text(control['raw'],encoding='utf8')
elif 'receipt' in control:ack.write_text(json.dumps(control['receipt']),encoding='utf8')
sys.exit(control.get('exit',0))
'''


class CleanupFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='kaibain-cleanup-test-')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.store = Store(self.folder / 'tasks.sqlite3')
        self.adapter = self.folder / 'fixed_adapter.py'
        self.adapter.write_text(ADAPTER, encoding='utf8')
        self.command = [sys.executable, str(self.adapter), '--fixture-token', 'frozen value']
        self.config = self.folder / 'runtime.json'
        self.config.write_text(json.dumps({'kind': 'development-v1', 'label': 'Fixture only',
                                           'command': self.command}), encoding='utf8')
        self.runtime = AgentRuntime(self.store, self.config)
        self.project = {'repo': str(self.folder / 'source-no-longer-present'), 'commit': 'a' * 40,
                        'files': ['app.py'], 'writable': ['app.py'],
                        'checks': ['python', '-m', 'unittest'], 'acceptance': ['Fixture only']}
        self.control(receipt={'ok': True, 'records_checked': 0})
        self.run = self.terminal()
        self.run_folder = self.folder / '.agent-runs' / self.run['id']

    def control(self, **value):
        (self.folder / 'control.json').write_text(json.dumps(value), encoding='utf8')

    def reserve(self, store=None):
        store = store or self.store
        goal = store.create('Isolated cleanup fixture')
        return store.reserve_agent_run(goal['id'], goal['version'], 'Fixture only', self.command,
                                       project=self.project)

    def terminal(self, status='failed', claimed=True):
        run = self.reserve()
        if claimed:
            self.store.claim_agent_run(run['id'], DEAD_PID)
        self.store.finish_agent_run(run['id'], status, 'Original fixture failure',
                                    failure_code='cleanup_failed')
        # Keep a descendant in the immutable-state comparisons as well as root.
        Store(self.store.path, source='agent').create('Existing worker', parent_id=run['task_id'])
        folder = self.folder / '.agent-runs' / run['id']
        (folder / 'development' / 'result').mkdir(parents=True)
        (folder / 'scope.json').write_text(json.dumps({
            'pid': DEAD_PID, 'platform': os.name, 'run_id': run['id'], 'status': 'closed'}))
        for name, data in {'report.json': b'{"original":true}', 'executor.log': b'original log\n',
                           'development/result/manifest.json': b'{"original":"manifest"}',
                           'development/result/changes.diff': b'original diff\x00\xff',
                           'development/result/app.py': b'original exported bytes\n'}.items():
            (folder / name).write_bytes(data)
        with self.store.connection() as db:
            return self.store._public_run(self.store._run(db, run['id']))

    def error(self, code, call):
        with self.assertRaises(DomainError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def cleanup(self):
        return self.runtime.cleanup_run(self.run['id'], self.run['version'])

    def actions(self):
        with self.store.connection() as db:
            return [dict(row) for row in db.execute('SELECT * FROM run_cleanups ORDER BY rowid')]

    def unchanged_state(self):
        # Compare complete durable rows, not just status/version fields.
        with self.store.connection() as db:
            return {table: [dict(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                    for table in ('tasks', 'agent_runs', 'run_checkpoints', 'goal_questions',
                                  'goal_results', 'projects')}

    def artifacts(self):
        return {str(path.relative_to(self.run_folder)): path.read_bytes()
                for path in self.run_folder.rglob('*') if path.is_file()
                and 'operator-cleanups' not in path.parts and path.name != 'cleanup.json'}

    def assert_fenced(self, code, store=None):
        store = store or self.store
        goal = store.get(self.run['task_id'])
        self.error(code, lambda: store.reserve_agent_run(goal['id'], goal['version'],
                                                         'Fixture', self.command))
        self.error(code, lambda: store.claim_agent_run(self.run['id'], os.getpid()))
        with store.connection() as db:
            self.error(code, lambda: store.recovery_context(
                db, self.run['id'], self.run['version'], goal['version'], 'missing-checkpoint'))


class CleanupStoreTests(CleanupFixture):
    def test_versions_rights_and_owner_validation_leave_no_lease(self):
        before = self.unchanged_state()
        for version in (None, True, False, 0, -1, '2', 1.0):
            with self.subTest(version=version):
                self.error('VALIDATION', lambda: self.store.begin_run_cleanup(
                    self.run['id'], version, os.getpid()))
        self.error('CONFLICT', lambda: self.store.begin_run_cleanup(
            self.run['id'], self.run['version'] + 1, os.getpid()))
        self.error('RUN_NOT_FOUND', lambda: self.store.begin_run_cleanup('missing', 1, os.getpid()))
        for pid in (None, True, 0, 1, -1, '123'):
            with self.subTest(pid=pid), self.assertRaises(ValueError):
                self.store.begin_run_cleanup(self.run['id'], self.run['version'], pid)
        agent = Store(self.store.path, source='agent')
        self.error('FORBIDDEN', lambda: agent.begin_run_cleanup(
            self.run['id'], self.run['version'], os.getpid()))
        self.error('FORBIDDEN', lambda: AgentRuntime(agent, self.config).cleanup_run(
            self.run['id'], self.run['version']))
        self.error('FORBIDDEN', lambda: agent.finish_run_cleanup('missing', 'completed'))
        self.assertEqual(self.actions(), [])
        self.assertEqual(self.unchanged_state(), before)

    def test_only_unsuccessful_terminal_project_runs_are_eligible(self):
        for status in ('queued', 'running', 'cancelling', 'completed', 'waiting_for_input'):
            with self.subTest(status=status):
                # Isolated row mutation builds states without launching a supervisor.
                with self.store.connection(write=True, control=True) as db:
                    db.execute('UPDATE agent_runs SET status=? WHERE id=?', (status, self.run['id']))
                self.error('CLEANUP_UNAVAILABLE', lambda: self.store.begin_run_cleanup(
                    self.run['id'], self.run['version'], os.getpid()))
        with self.store.connection(write=True, control=True) as db:
            db.execute("UPDATE agent_runs SET status='failed',project=NULL WHERE id=?", (self.run['id'],))
        self.error('CLEANUP_UNAVAILABLE', lambda: self.store.begin_run_cleanup(
            self.run['id'], self.run['version'], os.getpid()))
        self.assertEqual(self.actions(), [])

    def test_failed_and_interrupted_retry_keep_global_fences_until_success(self):
        peer = Store(self.store.path)
        before = self.unchanged_state()
        for status in ('failed', 'interrupted'):
            with self.subTest(status=status):
                action = self.store.begin_run_cleanup(self.run['id'], self.run['version'], os.getpid())
                self.assertNotIn('pid', action)
                self.assertEqual(action['status'], 'running')
                self.assert_fenced('CLEANUP_BUSY', peer)
                self.error('CLEANUP_BUSY', lambda: peer.begin_run_cleanup(
                    self.run['id'], self.run['version'], os.getpid()))
                finished = self.store.finish_run_cleanup(action['id'], status, 'fixture failure')
                self.assertEqual(finished['error'], 'fixture failure')
                self.assertEqual(peer.run_cleanup_status(self.run['id'])['cleanup'], finished)
                self.assert_fenced('CLEANUP_REQUIRED', peer)
                # A late success from this old owner must not clear the fence.
                late = peer.finish_run_cleanup(action['id'], 'completed', receipt={'ok': True})
                self.assertEqual(late, finished)
                self.assert_fenced('CLEANUP_REQUIRED', peer)
        retry = peer.begin_run_cleanup(self.run['id'], self.run['version'], os.getpid())
        success = peer.finish_run_cleanup(retry['id'], 'completed', receipt={'ok': True, 'records_checked': 0})
        self.assertEqual(self.store.agent_runs()[0]['cleanup'], success)
        self.assertEqual(self.unchanged_state(), before)
        launched = self.reserve(peer)
        peer.claim_agent_run(launched['id'], os.getpid())
        self.assertEqual(peer.agent_runs()[0]['status'], 'running')

    def test_unresolved_cleanup_of_another_run_cannot_be_bypassed(self):
        other = self.terminal()
        action = self.store.begin_run_cleanup(self.run['id'], self.run['version'], os.getpid())
        self.store.finish_run_cleanup(action['id'], 'failed', 'fixture failure')
        self.error('CLEANUP_REQUIRED', lambda: self.store.begin_run_cleanup(
            other['id'], other['version'], os.getpid()))
        retry = self.store.begin_run_cleanup(self.run['id'], self.run['version'], os.getpid())
        self.store.finish_run_cleanup(retry['id'], 'completed', receipt={'ok': True, 'records_checked': 0})
        self.assertEqual(self.store.begin_run_cleanup(other['id'], other['version'], os.getpid())['status'], 'running')

    def test_another_queued_running_or_cancelling_run_blocks_cleanup(self):
        peer = Store(self.store.path)
        other = self.reserve(peer)
        for status in ('queued', 'running', 'cancelling'):
            with self.subTest(status=status):
                if status == 'running':
                    peer.claim_agent_run(other['id'], os.getpid())
                elif status == 'cancelling':
                    with peer.connection() as db:
                        current = peer._run(db, other['id'])
                    peer.stop_agent_run(other['id'], current['version'])
                before, revision = self.unchanged_state(), self.store.revision()
                self.error('RUN_BUSY', lambda: self.store.begin_run_cleanup(
                    self.run['id'], self.run['version'], os.getpid()))
                self.assertEqual(self.actions(), [])
                self.assertEqual(self.unchanged_state(), before)
                self.assertEqual(self.store.revision(), revision)

    def test_finish_is_durable_idempotent_and_validates_input(self):
        action = self.store.begin_run_cleanup(self.run['id'], self.run['version'], os.getpid(),
                                              cleanup_id='explicit-fixture-id')
        self.assertEqual(action['id'], 'explicit-fixture-id')
        self.error('NOT_FOUND', lambda: self.store.finish_run_cleanup('missing', 'failed'))
        with self.assertRaises(ValueError):
            self.store.finish_run_cleanup(action['id'], 'running')
        done = self.store.finish_run_cleanup(action['id'], 'completed', receipt={'ok': True, 'records_checked': 3})
        revision = self.store.revision()
        self.assertEqual(self.store.finish_run_cleanup(action['id'], 'failed', 'late error'), done)
        self.assertEqual(self.store.revision(), revision)
        self.assertEqual(Store(self.store.path).run_cleanup_status(self.run['id'])['cleanup'], done)

    def test_backup_receipt_rejects_running_cleanup_without_mutating_database(self):
        before = self.unchanged_state()
        for status in ('failed', 'interrupted', 'completed'):
            with self.subTest(status=status):
                action = self.store.begin_run_cleanup(self.run['id'], self.run['version'], os.getpid())
                revision, actions = self.store.revision(), self.actions()
                with self.store.connection() as db:
                    # Backup's offline reader uses sqlite tuples, not Store rows.
                    db.row_factory = None
                    with self.assertRaisesRegex(backup.BackupError, 'owned cleanup is running'):
                        backup._receipt(db)
                self.assertEqual(self.store.revision(), revision)
                self.assertEqual(self.actions(), actions)
                self.store.finish_run_cleanup(action['id'], status, 'Fixture terminal cleanup')
                revision, actions = self.store.revision(), self.actions()
                with self.store.connection() as db:
                    db.row_factory = None
                    receipt = backup._receipt(db)
                self.assertEqual(receipt['revision'], revision)
                self.assertEqual(len(receipt['logical_sha256']), 64)
                self.assertEqual(self.store.revision(), revision)
                self.assertEqual(self.actions(), actions)
        self.assertEqual(self.unchanged_state(), before)

    def test_launch_and_cleanup_race_across_store_instances_has_one_winner(self):
        peer = Store(self.store.path)
        goal = peer.create('Racing reservation')
        barrier = threading.Barrier(2)

        def attempt(operation):
            barrier.wait(timeout=5)
            try:
                return ('ok', operation())
            except DomainError as error:
                return ('error', error.code)

        with ThreadPoolExecutor(max_workers=2) as pool:
            cleaning = pool.submit(attempt, lambda: self.store.begin_run_cleanup(
                self.run['id'], self.run['version'], os.getpid()))
            launching = pool.submit(attempt, lambda: peer.reserve_agent_run(
                goal['id'], goal['version'], 'Fixture', self.command))
            clean, launch = cleaning.result(timeout=10), launching.result(timeout=10)
        self.assertEqual(sorted([clean[0], launch[0]]), ['error', 'ok'])
        self.assertEqual(launch[1] if clean[0] == 'ok' else clean[1],
                         'CLEANUP_BUSY' if clean[0] == 'ok' else 'RUN_BUSY')
        if launch[0] == 'ok':
            self.error('RUN_BUSY', lambda: self.store.begin_run_cleanup(
                self.run['id'], self.run['version'], os.getpid()))
            peer.claim_agent_run(launch[1]['id'], os.getpid())
            self.error('RUN_BUSY', lambda: self.store.begin_run_cleanup(
                self.run['id'], self.run['version'], os.getpid()))

    def test_cleanup_cleanup_race_serializes_same_and_different_runs(self):
        other = self.terminal()
        peer = Store(self.store.path)
        for target in (self.run, other):
            with self.subTest(target=target['id']):
                barrier = threading.Barrier(2)

                def begin(store, run):
                    barrier.wait(timeout=5)
                    try:
                        return store.begin_run_cleanup(run['id'], run['version'], os.getpid())
                    except DomainError as error:
                        return error.code

                with ThreadPoolExecutor(max_workers=2) as pool:
                    a = pool.submit(begin, self.store, self.run)
                    b = pool.submit(begin, peer, target)
                    results = [a.result(timeout=10), b.result(timeout=10)]
                winners = [r for r in results if isinstance(r, dict)]
                self.assertEqual(len(winners), 1)
                self.assertIn('CLEANUP_BUSY', results)
                self.store.finish_run_cleanup(winners[0]['id'], 'completed')


class CleanupRuntimeTests(CleanupFixture):
    def test_frozen_command_works_without_checkpoint_config_or_matching_root(self):
        root = self.store.get(self.run['task_id'])
        Store(self.store.path, source='agent').update(
            root['id'], root['version'], {'description': 'Changed after failure'})
        before = self.unchanged_state()
        # Even an absent source tree/config and partial unsealed export are irrelevant.
        self.config.unlink()
        partial = self.run_folder / 'development' / 'checkpoints' / 'partial'
        partial.mkdir(parents=True)
        (partial / 'manifest.json').write_text('not sealed')
        artifacts = self.artifacts()
        with patch.dict(os.environ, {'KAIBAIN_CONTROL_TOKEN': 'fixture-secret'}), \
                patch('kaibain.agent_runtime.ProcessScope', wraps=ProcessScope) as owned:
            action = self.cleanup()
        owned.assert_called_once()
        self.assertEqual(owned.call_args.args[0], self.command + ['--cleanup', '--output', str(self.run_folder / 'report.json')])
        self.assertEqual(owned.call_args.kwargs['receipt'],
                         self.run_folder / 'operator-cleanups' / action['id'] / 'scope.json')
        self.assertEqual(action['status'], 'completed')
        self.assertEqual(action['receipt'], {'ok': True, 'records_checked': 0})
        self.assertEqual(self.unchanged_state(), before)
        self.assertEqual(self.artifacts(), artifacts)
        calls = [json.loads(line) for line in (self.folder / 'calls.jsonl').read_text().splitlines()]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['argv'], self.command[2:] + ['--cleanup', '--output', str(self.run_folder / 'report.json')])
        self.assertEqual(calls[0]['run_id'], self.run['id'])
        self.assertFalse(calls[0]['control_token_present'])
        self.assertIs(calls[0]['previous_ack']['ok'], False)
        self.assertEqual(calls[0]['previous_ack']['operator_cleanup_id'], action['id'])
        scope = json.loads((self.run_folder / 'operator-cleanups' / action['id'] / 'scope.json').read_text())
        self.assertEqual(scope['status'], 'closed')
        self.assertEqual(scope['run_id'], self.run['id'])
        self.assertNotIn(scope['pid'], (None, DEAD_PID, os.getpid()))
        self.assertTrue(scope_inactive(self.store, {'id': self.run['id'], 'pid': os.getpid()},
                                       folder=self.run_folder / 'operator-cleanups' / action['id']))

    def test_changed_current_config_never_replaces_frozen_cleanup_adapter(self):
        wrong = self.folder / 'wrong_adapter.py'
        marker = self.folder / 'wrong-adapter-ran'
        wrong.write_text('from pathlib import Path\nPath(__file__).with_name("wrong-adapter-ran").touch()\n')
        self.config.write_text(json.dumps({'kind': 'development-v1', 'label': 'Wrong adapter',
                                           'command': [sys.executable, str(wrong)]}))
        self.assertEqual(self.cleanup()['status'], 'completed')
        self.assertFalse(marker.exists())

    def test_gate_receipt_exists_before_lease_and_owns_new_helper(self):
        begin = self.store.begin_run_cleanup

        def checked(run_id, version, pid, *, cleanup_id=None):
            gate = self.run_folder / 'operator-cleanups' / cleanup_id / 'scope.json'
            self.assertEqual(json.loads(gate.read_text()), {
                'pid': None, 'platform': os.name, 'run_id': run_id, 'status': 'not_started'})
            self.assertEqual(self.actions(), [])
            return begin(run_id, version, pid, cleanup_id=cleanup_id)

        # The real helper still executes and overwrites its own gated scope.
        with patch.object(self.store, 'begin_run_cleanup', side_effect=checked):
            action = self.cleanup()
        self.assertEqual(action['status'], 'completed')

    def test_all_unsuccessful_terminal_states_and_unclaimed_run_cleanup(self):
        for status, claimed in (('failed', True), ('interrupted', True), ('cancelled', True), ('cancelled', False)):
            with self.subTest(status=status, claimed=claimed):
                run = self.terminal(status, claimed=claimed)
                if not claimed:
                    (self.folder / '.agent-runs' / run['id'] / 'scope.json').unlink()
                before = self.unchanged_state()
                self.assertEqual(self.runtime.cleanup_run(run['id'], run['version'])['status'], 'completed')
                self.assertEqual(self.unchanged_state(), before)

    def test_zero_exit_cannot_reuse_stale_success_and_explicit_retry_clears_fence(self):
        stale = {'ok': True, 'records_checked': 50, 'stale': True}
        (self.run_folder / 'cleanup.json').write_text(json.dumps(stale))
        self.control()  # Adapter exits zero and leaves the invalidated ack untouched.
        before, artifacts = self.unchanged_state(), self.artifacts()
        self.error('CLEANUP_FAILED', self.cleanup)
        failed = self.store.run_cleanup_status(self.run['id'])['cleanup']
        self.assertEqual(failed['status'], 'failed')
        self.assertTrue(failed['error'])
        self.assertIsNone(failed['receipt'])
        self.assertIs(json.loads((self.run_folder / 'cleanup.json').read_text())['ok'], False)
        self.assert_fenced('CLEANUP_REQUIRED')
        self.control(receipt={'ok': True, 'records_checked': 1})
        retry = self.cleanup()
        self.assertNotEqual(retry['id'], failed['id'])
        self.assertEqual(retry['status'], 'completed')
        self.assertEqual(self.actions()[0]['status'], 'failed')
        self.assertEqual(self.unchanged_state(), before)
        self.assertEqual(self.artifacts(), artifacts)
        self.reserve()

    def test_fresh_ack_requires_true_and_nonnegative_integer_records(self):
        cases = [None, [], {'ok': 1, 'records_checked': 0}, {'ok': False, 'records_checked': 0},
                 {'ok': True}, {'ok': True, 'records_checked': True},
                 {'ok': True, 'records_checked': -1}, {'ok': True, 'records_checked': 1.0},
                 {'ok': True, 'records_checked': '0'}]
        before, artifacts = self.unchanged_state(), self.artifacts()
        for receipt in cases:
            with self.subTest(receipt=receipt):
                self.control(receipt=receipt)
                self.error('CLEANUP_FAILED', self.cleanup)
                self.assertEqual(self.actions()[-1]['status'], 'failed')
                self.assert_fenced('CLEANUP_REQUIRED')
        self.assertEqual(self.unchanged_state(), before)
        self.assertEqual(self.artifacts(), artifacts)

    def test_malformed_missing_and_nonzero_exit_ack_fail_durably(self):
        before, artifacts = self.unchanged_state(), self.artifacts()
        for control in ({'raw': '{broken'}, {'remove': True},
                        {'exit': 7, 'receipt': {'ok': True, 'records_checked': 0}}):
            with self.subTest(control=control):
                self.control(**control)
                self.error('CLEANUP_FAILED', self.cleanup)
                self.assertEqual(self.actions()[-1]['status'], 'failed')
                self.assertTrue(self.actions()[-1]['error'])
                self.assert_fenced('CLEANUP_REQUIRED')
        self.assertEqual(self.unchanged_state(), before)
        self.assertEqual(self.artifacts(), artifacts)

    def test_live_old_pid_is_inspected_without_signalling_or_spawning(self):
        with self.store.connection(write=True, control=True) as db:
            db.execute('UPDATE agent_runs SET pid=? WHERE id=?', (os.getpid(), self.run['id']))
        before, artifacts = self.unchanged_state(), self.artifacts()
        real_kill = os.kill

        def inspect_only(pid, sig):
            self.assertEqual(sig, 0, 'Cleanup must not signal the saved run PID')
            return real_kill(pid, sig)

        with patch('kaibain.agent_runtime.subprocess.Popen') as spawn, \
                patch('os.kill', side_effect=inspect_only):
            self.error('CLEANUP_PROCESS_ALIVE', self.cleanup)
        spawn.assert_not_called()
        self.assertEqual(self.actions(), [])
        self.assertEqual(self.unchanged_state(), before)
        self.assertEqual(self.artifacts(), artifacts)

    def test_live_or_unknown_original_scope_never_starts_cleanup(self):
        original = self.run_folder / 'scope.json'
        for receipt, code in (({'pid': os.getpid(), 'platform': os.name, 'run_id': self.run['id'], 'status': 'active'},
                               'CLEANUP_PROCESS_ALIVE'),
                              ({'pid': DEAD_PID, 'platform': os.name, 'run_id': 'another-run', 'status': 'closed'},
                               'RECOVERY_SCOPE_UNKNOWN'), ({}, 'RECOVERY_SCOPE_UNKNOWN')):
            with self.subTest(receipt=receipt):
                original.write_text(json.dumps(receipt))
                with patch('kaibain.agent_runtime.subprocess.Popen') as spawn:
                    # POSIX active scopes are process groups; use our real group.
                    if os.name != 'nt' and receipt.get('status') == 'active':
                        receipt['pid'] = os.getpgrp()
                        original.write_text(json.dumps(receipt))
                    self.error(code, self.cleanup)
                spawn.assert_not_called()
                self.assertEqual(self.actions(), [])

    def test_previous_helper_scope_must_be_inactive_even_after_owner_exit(self):
        action = self.store.begin_run_cleanup(self.run['id'], self.run['version'], DEAD_PID)
        folder = self.run_folder / 'operator-cleanups' / action['id']
        folder.mkdir(parents=True)
        (folder / 'scope.json').write_text(json.dumps({
            'pid': os.getpid() if os.name == 'nt' else os.getpgrp(), 'platform': os.name,
            'run_id': self.run['id'], 'status': 'active'}))
        with patch('kaibain.agent_runtime.subprocess.Popen') as spawn:
            self.error('CLEANUP_PROCESS_ALIVE', self.cleanup)
        spawn.assert_not_called()
        self.assertEqual(self.actions()[0]['status'], 'interrupted')
        self.assert_fenced('CLEANUP_REQUIRED')

    def test_runtime_concurrency_keeps_lease_until_owned_helper_finishes(self):
        peer = Store(self.store.path)
        entered, release = threading.Event(), threading.Event()
        real_cleanup = cleanup_owned

        def held(*args, **kwargs):
            self.assertEqual(kwargs['scope_id'], self.run['id'])
            self.assertEqual(json.loads(Path(kwargs['receipt']).read_text())['status'], 'not_started')
            entered.set()
            if not release.wait(5):
                raise AssertionError('Test did not release the fixture cleanup')
            return real_cleanup(*args, **kwargs)

        with patch('kaibain.agent_runtime.cleanup_owned', side_effect=held) as helper:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self.cleanup)
                try:
                    self.assertTrue(entered.wait(5))
                    self.assert_fenced('CLEANUP_BUSY', peer)
                    self.error('CLEANUP_BUSY', lambda: AgentRuntime(peer, self.config).cleanup_run(
                        self.run['id'], self.run['version']))
                    self.assertEqual(peer.run_cleanup_status(self.run['id'])['cleanup']['status'], 'running')
                finally:
                    release.set()
                self.assertEqual(future.result(timeout=10)['status'], 'completed')
            self.assertEqual(helper.call_count, 1)


class CleanupHTTPTests(CleanupFixture):
    def setUp(self):
        super().setUp()
        self.server = make_server(self.store, 0, runtime_config=self.config)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive())

    def request(self, method='GET', body=None, *, path=None, raw=None, headers=None):
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        try:
            data = raw if raw is not None else json.dumps(body) if body is not None else None
            connection.request(method, path or f'/api/agent-runs/{self.run["id"]}/cleanup', data,
                               headers={'Content-Type': 'application/json', **(headers or {})})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_status_get_is_readonly_and_post_exposes_latest_cleanup(self):
        before, artifacts, revision = self.unchanged_state(), self.artifacts(), self.store.revision()
        for _ in range(2):
            status, value = self.request()
            self.assertEqual(status, 200)
            self.assertEqual(value, {'ok': True, 'run_id': self.run['id'],
                                     'run_version': self.run['version'], 'cleanup': None})
        self.assertEqual(self.store.revision(), revision)
        self.assertFalse((self.folder / 'calls.jsonl').exists())
        status, value = self.request('POST', {'expected_version': self.run['version']})
        self.assertEqual(status, 200)
        self.assertEqual(value['cleanup']['status'], 'completed')
        self.assertNotIn('pid', value['cleanup'])
        self.assertEqual(self.request()[1]['cleanup'], value['cleanup'])
        status, snapshot = self.request(path='/api/agent-runtime')
        self.assertEqual(status, 200)
        self.assertEqual(snapshot['runs'][0]['cleanup'], value['cleanup'])
        self.assertEqual(self.unchanged_state(), before)
        self.assertEqual(self.artifacts(), artifacts)

    def test_http_rejects_extra_fields_invalid_versions_and_foreign_origin(self):
        before, artifacts, revision = self.unchanged_state(), self.artifacts(), self.store.revision()
        payloads = [{}, {'expected_version': self.run['version'], 'command': self.command},
                    {'expected_version': self.run['version'], 'pid': DEAD_PID},
                    {'expected_version': self.run['version'], 'receipt': {'ok': True}},
                    {'expected_version': self.run['version'], 'source': 'agent'},
                    {'expected_version': self.run['version'], 'checkpoint_id': 'fake'}]
        payloads += [{'expected_version': value} for value in (None, True, False, 0, -1, '2', 1.0)]
        for payload in payloads:
            with self.subTest(payload=payload):
                status, value = self.request('POST', payload)
                self.assertEqual(status, 400)
                self.assertEqual(value['error']['code'], 'VALIDATION')
        for raw in ('[]', 'null', '{bad'):
            with self.subTest(raw=raw):
                self.assertEqual(self.request('POST', raw=raw)[0], 400)
        self.assertEqual(self.request('POST', raw='{}', headers={'Content-Type': 'text/plain'})[0], 415)
        status, value = self.request('POST', {'expected_version': self.run['version'] + 1})
        self.assertEqual((status, value['error']['code']), (409, 'CONFLICT'))
        self.assertEqual(self.request('POST', {'expected_version': self.run['version']},
                                      headers={'Origin': 'https://foreign.example'})[0], 403)
        self.assertEqual(self.request(path='/api/agent-runs/missing/cleanup')[0], 404)
        self.assertEqual(self.actions(), [])
        self.assertEqual(self.store.revision(), revision)
        self.assertEqual(self.unchanged_state(), before)
        self.assertEqual(self.artifacts(), artifacts)
        self.assertFalse((self.folder / 'calls.jsonl').exists())

    def test_get_preserves_live_lease_and_failed_post_requires_explicit_retry(self):
        action = self.store.begin_run_cleanup(self.run['id'], self.run['version'], os.getpid())
        gate = self.run_folder / 'operator-cleanups' / action['id']
        gate.mkdir(parents=True)
        (gate / 'scope.json').write_text(json.dumps({
            'pid': None, 'platform': os.name, 'run_id': self.run['id'], 'status': 'not_started'}))
        revision = self.store.revision()
        self.assertEqual(self.request()[1]['cleanup'], action)
        self.assertEqual(self.store.revision(), revision)
        status, value = self.request('POST', {'expected_version': self.run['version']})
        self.assertEqual((status, value['error']['code']), (409, 'CLEANUP_BUSY'))
        self.assertFalse((self.folder / 'calls.jsonl').exists())
        self.store.finish_run_cleanup(action['id'], 'failed', 'Fixture owner released gate')
        before, artifacts = self.unchanged_state(), self.artifacts()
        self.control()  # No fresh acknowledgement, despite a successful exit.
        status, value = self.request('POST', {'expected_version': self.run['version']})
        self.assertEqual((status, value['error']['code']), (409, 'CLEANUP_FAILED'))
        failed = self.store.run_cleanup_status(self.run['id'])['cleanup']
        self.assertEqual(failed['status'], 'failed')
        revision = self.store.revision()
        calls = (self.folder / 'calls.jsonl').read_bytes()
        self.assertEqual(self.request()[1]['cleanup'], failed)
        self.assertEqual(self.request()[1]['cleanup'], failed)
        self.assertEqual(self.store.revision(), revision)
        self.assertEqual((self.folder / 'calls.jsonl').read_bytes(), calls)
        self.assert_fenced('CLEANUP_REQUIRED')
        self.assertEqual(self.unchanged_state(), before)
        self.assertEqual(self.artifacts(), artifacts)
        self.control(receipt={'ok': True, 'records_checked': 0})
        status, value = self.request('POST', {'expected_version': self.run['version']})
        self.assertEqual(status, 200)
        self.assertEqual(value['cleanup']['status'], 'completed')
        self.assertEqual(self.unchanged_state(), before)
        self.assertEqual(self.artifacts(), artifacts)

    def test_dead_owner_restart_reconciles_only_action_and_requires_explicit_retry(self):
        # A short-lived controller publishes its gate/lease, then exits abruptly.
        # It never starts the adapter, any VM, model, or project code.
        script = '''import json,os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from kaibain.store import Store
store=Store(sys.argv[2]);run_id=sys.argv[3];version=int(sys.argv[4])
folder=store.path.parent/'.agent-runs'/run_id/'operator-cleanups'/'crashed-owner'
folder.mkdir(parents=True)
(folder/'scope.json').write_text(json.dumps({'pid':None,'platform':os.name,'run_id':run_id,'status':'not_started'}))
store.begin_run_cleanup(run_id,version,os.getpid(),cleanup_id='crashed-owner')
os._exit(23)
'''
        before, artifacts = self.unchanged_state(), self.artifacts()
        owner = subprocess.run([sys.executable, '-c', script,
                                str(Path(__file__).resolve().parents[1] / 'src'), str(self.store.path),
                                self.run['id'], str(self.run['version'])], capture_output=True, timeout=10)
        self.assertEqual(owner.returncode, 23, owner.stderr.decode(errors='replace'))
        self.assertEqual(self.actions()[0]['status'], 'running')
        self.assert_fenced('CLEANUP_BUSY', Store(self.store.path))
        # New HTTP runtime uses new Store connections to the same isolated DB.
        self.close_server()
        self.server = make_server(Store(self.store.path), 0, runtime_config=self.config)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        status, value = self.request()
        self.assertEqual(status, 200)
        interrupted = value['cleanup']
        self.assertEqual(interrupted['status'], 'interrupted')
        self.assertTrue(interrupted['error'])
        self.assertFalse((self.folder / 'calls.jsonl').exists())
        self.assertEqual(self.unchanged_state(), before)
        self.assertEqual(self.artifacts(), artifacts)
        revision = self.store.revision()
        self.assertEqual(self.request()[1]['cleanup'], interrupted)
        self.assertEqual(self.store.revision(), revision)
        self.assert_fenced('CLEANUP_REQUIRED')
        # Neither an old successful ack nor a late completion clears the lease.
        (self.run_folder / 'cleanup.json').write_text('{"ok":true,"records_checked":0}')
        self.assertEqual(self.store.finish_run_cleanup('crashed-owner', 'completed',
                                                       receipt={'ok': True}), interrupted)
        self.assert_fenced('CLEANUP_REQUIRED')
        status, value = self.request('POST', {'expected_version': self.run['version']})
        self.assertEqual(status, 200)
        self.assertEqual(value['cleanup']['status'], 'completed')
        self.assertNotEqual(value['cleanup']['id'], 'crashed-owner')
        self.assertEqual(self.actions()[0]['status'], 'interrupted')
        self.assertEqual(self.unchanged_state(), before)
        self.assertEqual(self.artifacts(), artifacts)
        self.reserve()


if __name__ == '__main__':
    unittest.main()
