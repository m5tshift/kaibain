"""Fault matrix for atomic review publication using isolated SQLite fixtures only."""
import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from kaibain.agent_runtime import AgentRuntime
from kaibain.store import Store
from test_recovery import recovery_fixture


class PublicationCrashTests(unittest.TestCase):
    def fixture(self, folder):
        self.store, self.runtime, self.run, self.checkpoint, self.export = recovery_fixture(
            folder, terminal=False)
        self.tasks = {task_id: self.store.get(task_id) for task_id in
                      (self.run['task_id'], self.checkpoint['child_id'])}
        self.result = {
            'handoff': 'atomic-review-v1',
            'child_id': self.checkpoint['child_id'],
            'summary': 'Sealed fixture result',
            'checks': ['SQLite fixture only; no VM or model execution'],
            'requirements_version': self.checkpoint['requirements_version'],
            'task_versions': {task_id: task['version'] for task_id, task in self.tasks.items()},
        }
        self.original_result = copy.deepcopy(self.result)
        self.assertEqual(self.run['status'], 'running')
        self.assertIsNone(self.run['result'])
        self.assertEqual(self.tasks[self.run['task_id']]['version'], self.checkpoint['task_version'])
        self.assertEqual(self.tasks[self.checkpoint['child_id']]['version'], self.checkpoint['child_version'])
        self.assertTrue(all(task['status'] == 'in_progress' for task in self.tasks.values()))
        with self.store.connection() as db:
            self.assertEqual(Store._latest_run(db, self.run['task_id']), self.run['id'])

    def snapshot(self, store):
        with store.connection() as db:
            return {table: [dict(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY id')]
                    for table in ('tasks', 'agent_runs', 'run_checkpoints', 'goal_results', 'meta')}

    def restart(self):
        self.store = Store(self.store.path)
        self.runtime = AgentRuntime(self.store, self.runtime.config_path)

    def finish(self):
        self.store.finish_agent_run(self.run['id'], 'completed', result=self.result)

    def assert_review_tasks(self, db, *, touched):
        self.assertTrue(db.in_transaction)
        for task_id, before in self.tasks.items():
            task = dict(db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone())
            self.assertEqual(task['status'], 'review')
            self.assertEqual(task['version'], before['version'] + int(touched))

    @contextmanager
    def touch_fault(self, stage, fault):
        original_touch = Store._touch
        calls = []

        def crash(db, ids):
            calls.append(list(ids))
            self.assertEqual(set(ids), set(self.tasks))
            # The real status UPDATE has already executed, inside Store.connection.
            self.assert_review_tasks(db, touched=False)
            if stage == 'after_touch':
                original_touch(db, ids)
                self.assert_review_tasks(db, touched=True)
            row = Store._run(db, self.run['id'])
            self.assertEqual(row['status'], 'running')
            self.assertIsNone(row['result'])
            raise fault

        with patch.object(Store, '_touch', side_effect=crash):
            yield calls

    @contextmanager
    def commit_fault(self, stage, fault):
        original_connect = sqlite3.connect
        owner = self
        calls = []

        class FaultConnection(sqlite3.Connection):
            def commit(db):
                calls.append(stage)
                owner.assert_review_tasks(db, touched=True)
                row = Store._run(db, owner.run['id'])
                owner.assertEqual(row['status'], 'completed')
                owner.assertEqual(row['decision'], 'pending')
                owner.assertEqual(json.loads(row['result'])['task_versions'],
                                  {task_id: task['version'] + 1
                                   for task_id, task in owner.tasks.items()})
                if stage == 'after_commit':
                    super().commit()
                    owner.assertFalse(db.in_transaction)
                raise fault

        def connect(*args, **kwargs):
            return original_connect(*args, **kwargs, factory=FaultConnection)

        # Keep Store.connection's BEGIN/rollback/close behavior and the real COMMIT.
        with patch('kaibain.store.sqlite3.connect', side_effect=connect):
            yield calls

    def assert_tasks_unchanged(self):
        self.assertEqual({task_id: self.store.get(task_id) for task_id in self.tasks}, self.tasks)
        self.assertEqual(self.result, self.original_result)

    def assert_checkpoint_usable(self):
        before = self.snapshot(self.store)
        preview = self.runtime.recovery_preview(self.run['id'])
        self.assertTrue(preview['recoverable'], preview)
        self.assertIsNone(preview['error'])
        self.assertEqual(preview['checkpoint'], self.checkpoint)
        self.assertEqual({entry['path'] for entry in preview['files']}, {'app.py', 'test_app.py'})
        self.assertEqual(self.snapshot(self.store), before)
        self.assertTrue(self.store.agent_runs()[0]['recovery_candidate'])

    def assert_pending_review(self):
        saved = self.store.agent_runs()[0]
        self.assertEqual(saved['id'], self.run['id'])
        self.assertEqual(saved['status'], 'completed')
        self.assertEqual(saved['version'], self.run['version'] + 1)
        self.assertEqual(saved['decision'], 'pending')
        self.assertEqual(saved['error'], '')
        self.assertIsNone(saved['failure_code'])
        self.assertIsNone(saved['reviewed_at'])
        self.assertIsNone(saved['review_versions'])
        self.assertFalse(saved['recovery_candidate'])
        expected = copy.deepcopy(self.original_result)
        expected['task_versions'] = {task_id: task['version'] + 1
                                     for task_id, task in self.tasks.items()}
        self.assertEqual(saved['result'], expected)
        self.assertEqual(self.result, self.original_result)
        for task_id, before in self.tasks.items():
            task = self.store.get(task_id)
            self.assertEqual(task['status'], 'review')
            self.assertEqual(task['version'], before['version'] + 1)
            self.assertFalse(task['needs_input'])
        with self.store.connection() as db:
            self.assertEqual(Store._latest_run(db, self.run['task_id']), self.run['id'])
            self.assertEqual(Store._checkpoint_for_run(db, self.run['id']), self.checkpoint)

    def test_precommit_baseexceptions_roll_back_and_preserve_recovery_after_restart(self):
        for fault_type in (KeyboardInterrupt, SystemExit):
            for stage in ('after_status_update', 'after_touch', 'before_commit'):
                with self.subTest(fault=fault_type.__name__, stage=stage), tempfile.TemporaryDirectory() as folder:
                    self.fixture(Path(folder))
                    before = self.snapshot(self.store)
                    fault = fault_type('publication fault before commit')
                    inject = self.commit_fault if stage == 'before_commit' else self.touch_fault
                    with inject(stage, fault) as calls:
                        with self.assertRaises(fault_type) as caught:
                            self.finish()
                    self.assertIs(caught.exception, fault)
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(self.snapshot(self.store), before)
                    self.assert_tasks_unchanged()
                    self.restart()
                    self.assertEqual(self.snapshot(self.store), before)
                    self.assert_tasks_unchanged()
                    # A running attempt is not recoverable yet. Finalize the lost
                    # supervisor through the real Store API before checking recovery.
                    preview = self.runtime.recovery_preview(self.run['id'])
                    self.assertFalse(preview['recoverable'])
                    self.assertEqual(preview['error']['code'], 'RECOVERY_STALE')
                    self.store.finish_agent_run(self.run['id'], 'interrupted',
                                                'Publication interrupted', failure_code='supervisor_lost')
                    saved = self.store.agent_runs()[0]
                    self.assertEqual(saved['status'], 'interrupted')
                    self.assertIsNone(saved['result'])
                    self.assertEqual(saved['version'], self.run['version'] + 1)
                    self.assert_tasks_unchanged()
                    self.assert_checkpoint_usable()
                    terminal = self.snapshot(self.store)
                    self.restart()
                    self.assertEqual(self.snapshot(self.store), terminal)
                    self.assert_checkpoint_usable()

    def test_postcommit_baseexceptions_cannot_retract_pending_review(self):
        for fault_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(fault=fault_type.__name__), tempfile.TemporaryDirectory() as folder:
                self.fixture(Path(folder))
                fault = fault_type('publication fault after commit')
                with self.commit_fault('after_commit', fault) as calls:
                    with self.assertRaises(fault_type) as caught:
                        self.finish()
                self.assertIs(caught.exception, fault)
                self.assertEqual(calls, ['after_commit'])
                self.assert_pending_review()
                published = self.snapshot(self.store)
                for restarted in (False, True):
                    if restarted:
                        self.restart()
                    self.assertEqual(self.snapshot(self.store), published)
                    self.store.finish_agent_run(self.run['id'], 'interrupted',
                                                'Supervisor unwound after commit', failure_code='supervisor_lost')
                    self.assertEqual(self.snapshot(self.store), published)
                    self.assert_pending_review()
                self.restart()
                self.assertEqual(self.snapshot(self.store), published)

    def test_stop_accepted_first_blocks_review_and_preserves_checkpoint_on_restart(self):
        for reason, status, code in (('user', 'cancelled', 'user_stop'),
                                     ('timeout', 'failed', 'supervisor_timeout')):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as folder:
                self.fixture(Path(folder))
                stopped = self.store.stop_agent_run(self.run['id'], self.run['version'], reason=reason)
                self.assertEqual(stopped['status'], 'cancelling')
                self.assert_tasks_unchanged()
                self.restart()
                self.assertEqual(self.store.agent_runs()[0], {**stopped, 'input_answered': False,
                                                             'recovery_candidate': False,'cleanup': None})
                self.finish()
                saved = self.store.agent_runs()[0]
                self.assertEqual(saved['status'], status)
                self.assertEqual(saved['stop_reason'], reason)
                self.assertEqual(saved['failure_code'], code)
                self.assertEqual(saved['version'], stopped['version'] + 1)
                self.assertIsNone(saved['result'])
                self.assertIsNone(saved['reviewed_at'])
                self.assert_tasks_unchanged()
                self.assert_checkpoint_usable()
                terminal = self.snapshot(self.store)
                self.restart()
                self.finish()
                self.store.finish_agent_run(self.run['id'], 'interrupted', failure_code='supervisor_lost')
                self.assertEqual(self.snapshot(self.store), terminal)
                self.assert_tasks_unchanged()
                self.assert_checkpoint_usable()


if __name__ == '__main__':
    unittest.main()
