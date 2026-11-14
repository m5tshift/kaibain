import concurrent.futures
import json
import sqlite3
from contextlib import closing
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from kaibain.store import DomainError, Store
from kaibain.web import make_server


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'tasks.sqlite3'
        self.store = Store(self.path,source='agent')

    def tearDown(self):
        self.temp.cleanup()

    def assertError(self, code, call):
        with self.assertRaises(DomainError) as raised:
            call()
        self.assertEqual(raised.exception.code, code)
        return raised.exception

    def test_nested_progress_manual_status_and_persistence(self):
        s = self.store
        # A microVM/low-resolution clock may timestamp a whole batch equally.
        with patch('kaibain.store.now',return_value='2026-10-24T00:00:00.000000+00:00'):
            root = s.create('Parent', deadline='2026-10-20')
            child = s.create('Child', parent_id=root['id'])
            s.create('Grandchild', parent_id=child['id'], status='done', deadline='2026-10-21')
            s.create('Second', parent_id=root['id'], status='done')
        self.assertEqual(s.get(root['id'])['progress'], {'total': 2, 'completed': 1})
        self.assertEqual(s.get(root['id'])['status'], 'planned')
        self.assertEqual([n['depth'] for n in s.tree(root['id'])['nodes']], [0, 1, 2, 1])
        reopened = Store(self.path)
        self.assertEqual(s.list(), reopened.list())

    def test_filters_dates_and_clear_values(self):
        s = self.store
        root = s.create('Root')
        task = s.create('Due', parent_id=root['id'], deadline='2026-10-20', status='done')
        self.assertEqual(len(s.list(roots_only=True)['tasks']), 1)
        self.assertEqual(s.list(parent_id=root['id'])['tasks'][0]['id'], task['id'])
        self.assertEqual(len(s.list(deadline_from='2026-10-20', deadline_to='2026-10-20', status='done')['tasks']), 1)
        self.assertEqual(s.list(deadline_to='2026-10-19')['tasks'], [])
        self.assertError('GOAL_REVIEW_REQUIRED',lambda:s.update(task['id'],task['version'],{'parent_id':None}))
        updated = s.update(task['id'], task['version'], {'deadline': None, 'parent_id': None,'status':'planned'})
        self.assertIsNone(updated['deadline'])
        self.assertIsNone(updated['parent_id'])
        for day in ['2026-02-30', '20261020', '2026-1-2', '', 123]:
            self.assertError('VALIDATION', lambda: s.create('bad', deadline=day))

    def test_review_is_active_and_survives_restart(self):
        root = self.store.create('Parent')
        child = self.store.create('Check me', parent_id=root['id'], status='review')
        self.assertEqual(self.store.get(root['id'])['progress'], {'total': 1, 'completed': 0})
        self.assertEqual(self.store.list(status='review')['tasks'][0]['id'], child['id'])
        self.assertEqual(Store(self.path).get(child['id'])['status'], 'review')
        self.store.update(child['id'], child['version'], {'status': 'done'})
        self.assertEqual(self.store.get(root['id'])['progress']['completed'], 1)

    def test_legacy_database_migration_preserves_records_and_constraints(self):
        legacy = Path(self.temp.name) / 'legacy.sqlite3'
        with closing(sqlite3.connect(legacy)) as db:
            db.executescript('''
                CREATE TABLE tasks (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL CHECK(status IN ('planned','in_progress','done')),
                    deadline TEXT, parent_id TEXT REFERENCES tasks(id) ON DELETE RESTRICT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE meta (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL);
                INSERT INTO meta VALUES (1, 12);
                INSERT INTO tasks VALUES ('parent','Parent','Details','in_progress','2026-10-20',NULL,'created','updated',7);
                INSERT INTO tasks VALUES ('child','Child','','done',NULL,'parent','created2','updated2',3);
                INSERT INTO tasks VALUES ('grand','Grand','','planned',NULL,'child','created3','updated3',2);
            ''')
            before = db.execute('SELECT * FROM tasks ORDER BY id').fetchall()
        migrated = Store(legacy,source='agent')
        with migrated.connection() as db:
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM tasks ORDER BY id')], before)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual({r[1] for r in db.execute('PRAGMA index_list(tasks)')} & {'tasks_parent','tasks_deadline'},
                             {'tasks_parent','tasks_deadline'})
        self.assertEqual(migrated.revision(), 12)
        migrated.update('parent', 7, {'status': 'review'})
        self.assertEqual(Store(legacy).get('parent')['status'], 'review')
        self.assertEqual([n['depth'] for n in migrated.tree('parent')['nodes']], [0,1,2])
        with migrated.connection(write=True) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE tasks SET status='invalid' WHERE id='parent'")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE tasks SET parent_id='missing' WHERE id='child'")

    def test_retired_tags_removed_on_database_reopen(self):
        root = self.store.create('Root')
        child = self.store.create('Child', parent_id=root['id'])
        before = self.store.list()
        # Simulate an existing database or a restored older backup.
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript('''CREATE TABLE tags (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, normalized TEXT NOT NULL UNIQUE);
                ALTER TABLE tasks ADD COLUMN tag_id TEXT REFERENCES tags(id);
                INSERT INTO tags VALUES ('old','Legacy','legacy');
                UPDATE tasks SET tag_id='old';''')
        reopened = Store(self.path, source='agent')
        self.assertEqual(reopened.list(), before)
        self.assertEqual(reopened.get(child['id'])['parent_id'], root['id'])
        with reopened.connection() as db:
            self.assertNotIn('tag_id', {r[1] for r in db.execute('PRAGMA table_info(tasks)')})
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='tags'").fetchone())
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(Store(self.path).list(), before)
        self.assertError('VALIDATION', lambda: reopened.update(root['id'], reopened.get(root['id'])['version'], {'tag_id': None}))

    def test_cycles_unknown_parents_and_rollback(self):
        s = self.store
        root = s.create('Root')
        child = s.create('Child', parent_id=root['id'])
        grandchild = s.create('Grandchild', parent_id=child['id'])
        version = s.get(root['id'])['version']
        revision = s.revision()
        self.assertError('CYCLE', lambda: s.update(root['id'], version, {'parent_id': grandchild['id']}))
        self.assertError('CYCLE', lambda: s.update(root['id'], version, {'parent_id': root['id']}))
        self.assertError('NOT_FOUND', lambda: s.create('bad', parent_id='missing'))
        self.assertEqual(s.revision(), revision)
        self.assertIsNone(s.get(root['id'])['parent_id'])

    def test_conflicts_and_two_concurrent_writers(self):
        s = self.store
        task = s.create('Original')
        barrier = threading.Barrier(2)
        def write(title):
            other = Store(self.path)
            barrier.wait()
            try:
                other.update(task['id'], task['version'], {'title': title})
                return 'ok'
            except DomainError as error:
                return error.code
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(write, ['User', 'Agent']))
        self.assertEqual(sorted(results), ['CONFLICT', 'ok'])
        self.assertEqual(s.get(task['id'])['version'], 2)
        self.assertError('VALIDATION', lambda: s.update(task['id'], None, {'title': 'bad'}))
        self.assertError('VALIDATION', lambda: s.delete(task['id'], True))

    def test_delete_confirmation_and_concurrent_descendant_changes(self):
        s = self.store
        root = s.create('Root')
        child = s.create('Child', parent_id=root['id'])
        stale = s.get(root['id'])
        self.assertError('SUBTREE_CONFIRMATION_REQUIRED', lambda: s.delete(root['id'], stale['version']))
        grandchild = s.create('Added after preview', parent_id=child['id'])
        self.assertError('CONFLICT', lambda: s.delete(root['id'], stale['version'], True))
        self.assertEqual(len(s.list()['tasks']), 3)
        fresh = s.get(root['id'])
        result = s.delete(root['id'], fresh['version'], True)
        self.assertEqual(set(result['deleted_ids']), {root['id'], child['id'], grandchild['id']})
        self.assertEqual(s.list()['tasks'], [])

    def test_reparent_invalidates_both_ancestor_chains(self):
        s = self.store
        a, b = s.create('A'), s.create('B')
        child = s.create('Child', parent_id=a['id'])
        av, bv = s.get(a['id'])['version'], s.get(b['id'])['version']
        s.update(child['id'], child['version'], {'parent_id': b['id']})
        self.assertGreater(s.get(a['id'])['version'], av)
        self.assertGreater(s.get(b['id'])['version'], bv)
        self.assertEqual(s.get(a['id'])['progress']['total'], 0)
        self.assertEqual(s.get(b['id'])['progress']['total'], 1)

    def test_deep_tree_without_recursive_serialization(self):
        s = self.store
        root = s.create('0')
        parent = root
        for i in range(1, 140):
            parent = s.create(str(i), parent_id=parent['id'])
        nodes = s.tree(root['id'])['nodes']
        self.assertEqual(nodes[-1]['depth'], 139)
        json.dumps(nodes)
        result = s.delete(root['id'], s.get(root['id'])['version'], True)
        self.assertEqual(result['deleted_count'], 140)

    def test_validation_unknown_fields_and_title(self):
        for value in ['', '  ', None, 'x' * 301]:
            self.assertError('VALIDATION', lambda: self.store.create(value))
        task = self.store.create('  trimmed  ')
        self.assertEqual(task['title'], 'trimmed')
        self.assertError('VALIDATION', lambda: self.store.update(task['id'], 1, {'id': 'changed'}))
        self.assertError('VALIDATION', lambda: self.store.update(task['id'], 1, {'status': 'unknown'}))

    def test_native_rights_separate_user_intent_from_agent_execution(self):
        user,agent=Store(self.path),self.store
        goal=user.create('Human goal')
        self.assertEqual(user.workspace()['workflow'],'agents_native')
        self.assertError('WORKFLOW_FIXED',lambda:user.set_mode('manual',1))
        self.assertError('AGENT_ONLY',lambda:user.create('Child',parent_id=goal['id']))
        self.assertError('AGENT_ONLY',lambda:user.update(goal['id'],1,{'status':'done'}))
        self.assertError('AGENT_ONLY',lambda:user.delete(goal['id'],1))
        changed=user.update(goal['id'],1,{'description':'Intent clarified'})
        child=agent.create('Agent task',parent_id=goal['id'])
        agent.update(child['id'],1,{'status':'done'})
        self.assertEqual(user.get(goal['id'])['progress']['completed'],1)
        self.assertError('GOAL_STARTED',lambda:user.update(goal['id'],user.get(goal['id'])['version'],{'title':'Changed scope'}))
        self.assertError('GOAL_REVIEW_REQUIRED',lambda:agent.update(goal['id'],agent.get(goal['id'])['version'],{'status':'done'}))
        self.assertEqual(Store(self.path).workspace()['workflow'],'agents_native')

    def test_legacy_mode_row_does_not_restrict_agents_or_change_task_data(self):
        user=Store(self.path);goal=user.create('Preserved goal',deadline='2026-10-20')
        with user.connection(write=True,control=True) as db:
            db.execute("UPDATE workspace SET mode='manual',mode_version=7 WHERE id=1")
        before=user.get(goal['id'])
        restarted=Store(self.path,source='agent')
        self.assertEqual(restarted.get(goal['id']),before)
        self.assertEqual(restarted.workspace()['workflow'],'agents_native')
        child=restarted.create('Agent can work',parent_id=goal['id'])
        self.assertEqual(child['parent_id'],goal['id'])
        with restarted.connection() as db:
            self.assertEqual(tuple(db.execute('SELECT mode,mode_version FROM workspace').fetchone()),('manual',7))

    def test_human_review_conflicts_with_concurrent_agent_descendant_edit(self):
        user=Store(self.path);agent=self.store;goal=user.create('Goal')
        child=agent.create('Child',parent_id=goal['id'])
        report=agent.submit_goal_result(goal['id'],agent.get(goal['id'])['version'],'Result',['Self-reported check'])
        agent.update(child['id'],child['version'],{'description':'A newer edit'})
        self.assertError('REVIEW_STALE',lambda:user.review_goal(goal['id'],user.get(goal['id'])['version'],report['id'],1,'accepted'))
        self.assertEqual(user.get(goal['id'])['status'],'review')


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'tasks.sqlite3')
        self.server = make_server(self.store, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, method='GET', body=None, headers=None):
        req = urllib.request.Request(self.base + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type': 'application/json', **(headers or {})})
        try:
            with urllib.request.urlopen(req) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.load(error)

    def test_http_goals_and_agent_only_tasks(self):
        code,created=self.request('/api/goals','POST',{'title':'User intent','deadline':'2026-10-20'})
        self.assertEqual(code,201);goal=created['goal']
        agent=Store(self.store.path,source='agent')
        child=agent.create('Agent task',parent_id=goal['id'])
        code,error=self.request('/api/tasks/'+child['id'],'PATCH',{'expected_version':1,'changes':{'status':'done'}})
        self.assertEqual((code,error['error']['code']),(403,'AGENT_ONLY'))
        agent.update(child['id'],1,{'status':'done'})
        self.assertEqual(self.request('/api/tasks/'+child['id'])[1]['task']['status'],'done')
        self.assertEqual(self.request('/api/tasks/'+goal['id'],'DELETE',{'expected_version':agent.get(goal['id'])['version']})[0],403)
        self.assertEqual(self.request('/api/goals/'+goal['id'],'PATCH',{'expected_version':1,'changes':{'title':'Stale'}})[0],409)

    def test_browser_origin_and_host_guards(self):
        for headers in [{'Origin': 'https://evil.example'}, {'Host': 'evil.example'}, {'Sec-Fetch-Site': 'cross-site'}]:
            code, _ = self.request('/api/tasks', 'POST', {'title': 'blocked'}, headers)
            self.assertEqual(code, 403)
        self.assertEqual(self.store.list()['tasks'], [])
        code, _ = self.request('/api/shutdown', 'POST', {})
        self.assertEqual(code, 403)

    def test_http_cannot_spoof_agent_source_or_restore_manual_workflow(self):
        self.assertEqual(self.request('/api/workspace')[1]['workflow'],'agents_native')
        code,value=self.request('/api/workspace','PATCH',{'mode':'manual','expected_version':1})
        self.assertEqual((code,value['error']['code']),(409,'WORKFLOW_FIXED'))
        self.assertEqual(self.request('/api/goals','POST',{'title':'Spoof','source':'agent'})[0],400)
        goal=self.request('/api/goals','POST',{'title':'Goal'})[1]['goal']
        self.assertEqual(self.request('/api/tasks','POST',{'title':'Child','parent_id':goal['id']})[0],403)
        self.assertEqual(self.request('/api/tasks/'+goal['id'],'PATCH',{'expected_version':1,'changes':{'status':'done'}})[0],403)
        self.assertEqual(self.request('/api/tags','POST',{'name':'Project'})[0],404)
        self.assertEqual(self.request('/api/tags')[0],404)
        self.assertEqual(self.request('/api/goals','POST',{'title':'Old client','tag_id':None})[0],400)
        self.assertEqual(self.request('/api/goals/'+goal['id'],'PATCH',{'expected_version':1,'changes':{'tag_id':None}})[0],400)
        self.assertNotIn('tags',self.request('/api/tasks')[1])
        self.assertNotIn('tag_id',goal)
        self.assertNotIn('tag',goal)

    def test_validation_and_deep_link(self):
        code, _ = self.request('/api/tasks', 'POST', {'title': 'bad', 'extra': 1})
        self.assertEqual(code, 400)
        code, _ = self.request('/api/tasks?roots_only=invalid')
        self.assertEqual(code, 400)
        with urllib.request.urlopen(self.base + '/tasks/any-id') as response:
            self.assertIn(b'<html lang="ru">', response.read())


if __name__ == '__main__':
    unittest.main()
