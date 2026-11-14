"""Persistent project profiles, immutable sources and launch conflict boundaries."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from kaibain.store import Store, DomainError
from kaibain.agent_runtime import AgentRuntime
from kaibain.web import make_server


class SavedProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='saved-project-')
        self.folder = Path(self.temp.name)
        self.repo = self.folder/'source with spaces'; self.repo.mkdir()
        self.git('init', '-q')
        (self.repo/'app.py').write_text('VALUE = 1\n')
        self.first = self.commit('First')
        self.user = Store(self.folder/'tasks.sqlite3')
        self.agent = Store(self.user.path, source='agent')
        self.profile = {'repo': str(self.repo), 'commit': None, 'files': ['app.py'],
                        'writable': ['app.py'], 'checks': ['python', '-m', 'unittest'],
                        'acceptance': ['Public API preserved']}
        self.project = self.user.create_project('Sample', self.profile, 'Local source')
        self.goal = self.user.create('Fix source', project_id=self.project['id'])
        config = self.folder/'runtime.json'
        config.write_text(json.dumps({'kind': 'development-v1', 'label': 'Test adapter',
                                      'command': [sys.executable, str(Path(__file__).resolve())]}))
        self.runtime = AgentRuntime(self.user, config)

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.check_output(['git', '-c', 'core.hooksPath='+os.devnull,
            '-c', 'commit.gpgsign=false', '-c', 'core.fsmonitor=false', '-c', 'core.autocrlf=false',
            '-C', str(self.repo), *args], stderr=subprocess.DEVNULL).decode().strip()

    def commit(self, message):
        self.git('add', '.')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@localhost', 'commit', '-qm', message)
        return self.git('rev-parse', 'HEAD')

    def error(self, code, operation):
        with self.assertRaises(DomainError) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)

    def reserve(self, resolved):
        return self.user.reserve_agent_run(self.goal['id'], self.user.get(self.goal['id'])['version'],
                                           'Fixture', [], **resolved)

    def test_persistence_goal_selection_and_user_owned_configuration(self):
        restarted = Store(self.user.path)
        self.assertEqual(restarted.projects()['projects'], [self.project])
        self.assertEqual(restarted.get(self.goal['id'])['project_id'], self.project['id'])
        self.assertEqual(restarted.list()['tasks'][0]['project_id'], self.project['id'])
        self.error('FORBIDDEN', lambda: self.agent.create_project('Forged', self.profile))
        self.error('FORBIDDEN', lambda: self.agent.update_project(self.project['id'], 1, {'name': 'Forged'}))
        self.error('FORBIDDEN', lambda: self.agent.update(self.goal['id'], 1, {'project_id': None}))
        self.error('FORBIDDEN', lambda: self.agent.create('Forged', project_id=self.project['id']))
        updated = self.user.update(self.goal['id'], 1, {'project_id': None})
        self.assertIsNone(updated['project_id']); self.assertEqual(updated['version'], 2)
        self.assertEqual(self.user.get_project(self.project['id']), self.project)

    def test_profile_validation_and_archiving_preserve_existing_goals(self):
        for bad in ({'commit': False}, {'commit': 'HEAD'}, {'checks': ['cmd', '/c', 'echo']},
                    {'files': ['../secret']}, {'command': ['arbitrary']}, {'files': []}):
            with self.subTest(bad=bad):
                self.error('VALIDATION', lambda: self.user.create_project('Bad', {**self.profile, **bad}))
        archived = self.user.update_project(self.project['id'], 1, {'archived': True})
        self.assertTrue(archived['archived'])
        self.assertEqual(self.user.get(self.goal['id'])['project_id'], self.project['id'])
        self.error('PROJECT_ARCHIVED', lambda: self.user.create('New', project_id=self.project['id']))
        self.error('PROJECT_ARCHIVED', lambda: self.user.resolve_project(self.project['id'], 2))
        clarified = self.user.update(self.goal['id'], 1, {'title': 'Clarified', 'project_id': self.project['id']})
        self.assertEqual(clarified['title'], 'Clarified')
        self.error('PROJECT_CONFLICT', lambda: self.user.update_project(self.project['id'], 1, {'archived': False}))
        self.assertFalse(self.user.update_project(self.project['id'], 2, {'archived': False})['archived'])

    def test_head_is_resolved_at_each_launch_and_fixed_commit_remains_fixed(self):
        first = self.user.resolve_project(self.project['id'], 1)
        self.assertEqual(first['project']['commit'], self.first)
        (self.repo/'app.py').write_text('VALUE = 2\n'); second = self.commit('Second')
        with patch.dict(os.environ, {'GIT_DIR': str(self.folder/'wrong'), 'GIT_WORK_TREE': str(self.folder/'wrong')}):
            self.assertEqual(self.user.resolve_project(self.project['id'], 1)['project']['commit'], second)
        frozen = self.user.update_project(self.project['id'], 1, {'profile': {**self.profile, 'commit': self.first}})
        self.assertEqual(self.user.resolve_project(frozen['id'], 2)['project']['commit'], self.first)
        self.assertIsNone(self.project['profile']['commit'])
        self.assertEqual((self.repo/'app.py').read_text(), 'VALUE = 2\n')

    def test_unavailable_repository_and_unborn_head_fail_before_reserving(self):
        self.user.update_project(self.project['id'], 1, {'profile': {**self.profile, 'repo': str(self.folder/'absent')}})
        self.error('PROJECT_UNAVAILABLE', lambda: self.runtime.start(self.goal['id'], 1,
            project_id=self.project['id'], expected_project_version=2))
        self.assertEqual(self.user.agent_runs(), [])
        (self.repo/'app.py').write_text('VALUE = 3\n')
        self.user.update_project(self.project['id'], 2, {'profile': {**self.profile, 'commit': 'f'*40}})
        self.error('PROJECT_COMMIT_UNAVAILABLE', lambda: self.user.resolve_project(self.project['id'], 3))
        empty = self.folder/'empty'; empty.mkdir()
        subprocess.run(['git', 'init', '-q', str(empty)], check=True)
        self.user.update_project(self.project['id'], 3, {'profile': {**self.profile, 'repo': str(empty)}})
        self.error('PROJECT_COMMIT_UNAVAILABLE', lambda: self.user.resolve_project(self.project['id'], 4))
        self.assertEqual(self.user.get(self.goal['id'])['version'], 1)

    def test_racing_profile_edit_or_archive_cannot_launch_a_stale_snapshot(self):
        resolved = self.user.resolve_project(self.project['id'], 1)
        self.user.update_project(self.project['id'], 1, {'name': 'Changed'})
        self.error('PROJECT_CONFLICT', lambda: self.reserve(resolved))
        resolved = self.user.resolve_project(self.project['id'], 2)
        self.user.update_project(self.project['id'], 2, {'archived': True})
        self.error('PROJECT_ARCHIVED', lambda: self.reserve(resolved))
        self.assertEqual(self.user.agent_runs(), [])
        self.assertEqual(self.user.get(self.goal['id'])['version'], 1)

    def test_run_and_revision_keep_original_settings_after_project_is_changed(self):
        resolved = self.user.resolve_project(self.project['id'], 1)
        run = self.reserve(resolved)
        self.user.claim_agent_run(run['id'], os.getpid())
        child = self.agent.create('Deliverable', parent_id=self.goal['id'], status='review')
        root = self.user.get(self.goal['id'])
        root = self.agent.update(root['id'], root['version'], {'status': 'review'})
        # This fixture records completion directly; no model or host project code runs.
        self.user.finish_agent_run(run['id'], 'completed', result={'child_id': child['id'], 'summary': 'Done',
            'checks': ['Fixture'], 'task_versions': {root['id']: root['version'], child['id']: child['version']}})
        saved = self.user.agent_runs()[0]
        returned = self.user.review_agent_run(saved['id'], saved['version'], 'changes_requested', 'Improve it')
        self.user.update_project(self.project['id'], 1, {'name': 'Renamed', 'archived': True,
            'profile': {**self.profile, 'acceptance': ['A different goal']}})
        root = self.user.get(root['id'])
        with patch('kaibain.agent_runtime.subprocess.Popen') as process:
            process.return_value.wait.return_value = 0
            revised = self.runtime.revise(run['id'], returned['version'], root['version'])
        self.assertEqual(revised['project'], resolved['project'])
        self.assertEqual(revised['project_ref'], resolved['project_ref'])
        self.assertEqual(self.user.agent_runs()[1]['project'], saved['project'])
        self.assertEqual(revised['project_ref']['name'], 'Sample')
        self.user.finish_agent_run(revised['id'], 'failed', 'Fixture cleanup')

    def test_runtime_accepts_saved_project_and_freezes_profile_before_spawning(self):
        resolved = self.user.resolve_project(self.project['id'], 1)
        with patch.object(self.user, 'resolve_project', return_value=resolved) as resolve, patch('kaibain.agent_runtime.subprocess.Popen') as process:
            process.return_value.wait.return_value = 0
            run = self.runtime.start(self.goal['id'], 1, project_id=self.project['id'], expected_project_version=1)
        resolve.assert_called_once_with(self.project['id'], 1)
        self.assertEqual(run['project']['commit'], self.first)
        self.assertEqual(run['project_ref'], {'id': self.project['id'], 'name': 'Sample', 'version': 1})
        self.user.finish_agent_run(run['id'], 'failed', 'Fixture cleanup')
        self.error('VALIDATION', lambda: self.runtime.start(self.goal['id'], 1, project_id=self.project['id'],
            expected_project_version=1, project=run['project']))

    def test_http_contract_project_conflicts_and_goal_binding(self):
        server = make_server(self.user, 0, runtime_config=self.folder/'absent-runtime.json')
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        def request(path, method='GET', body=None, headers=None):
            req = urllib.request.Request(base+path, method=method, data=json.dumps(body).encode() if body is not None else None,
                headers={'Content-Type': 'application/json', **(headers or {})})
            try:
                with urllib.request.urlopen(req) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as error:
                with error:
                    return error.code, json.load(error)
        try:
            self.assertEqual(request('/api/projects')[1]['projects'], [self.project])
            self.assertEqual(request('/api/projects/'+self.project['id']+'/resolve', 'POST', {'expected_version': 1})[1]['project']['commit'], self.first)
            code, value = request('/api/goals', 'POST', {'title': 'Selected project', 'project_id': self.project['id']})
            self.assertEqual(code, 201); self.assertEqual(value['goal']['project_id'], self.project['id'])
            self.assertEqual(request('/api/projects/'+self.project['id'], 'PATCH', {'expected_version': 1, 'changes': {'description': 'Edited'}})[0], 200)
            self.assertEqual(request('/api/projects/'+self.project['id']+'/resolve', 'POST', {'expected_version': 1})[0], 409)
            self.assertEqual(request('/api/projects', 'POST', {'name': 'Spoof', 'profile': self.profile, 'source': 'agent'})[0], 400)
            self.assertEqual(request('/api/projects', 'POST', {'name': 'Cross site', 'profile': self.profile}, {'Origin': 'https://other.example'})[0], 403)
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
