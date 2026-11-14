import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from kaibain.agent_runtime import AgentRuntime
from kaibain.store import Store,DomainError


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)
        self.store=Store(self.folder/'tasks.sqlite3');self.task=self.store.create('Fix source')
        self.project={'repo':str(self.folder),'commit':'a'*40,'files':['app.py','test_app.py'],
                      'writable':['app.py'],'checks':['python','-m','unittest'], 'acceptance':['Preserve the API']}
        self.config=self.folder/'runtime.json'
        self.config.write_text(json.dumps({'kind':'development-v1','label':'Development fixture',
                                          'command':[sys.executable,str(Path(__file__).resolve())]}),encoding='utf-8')
        self.runtime=AgentRuntime(self.store,self.config)

    def tearDown(self):self.temp.cleanup()

    def test_profile_validation_cannot_supply_host_commands_or_unsafe_paths(self):
        self.assertEqual(Store.validate_project(self.project),self.project)
        for change in ({'repo':'relative'},{'commit':'HEAD'},{'checks':['cmd','/c','echo']},
                       {'files':['../secret']},{'files':['/etc/passwd']},{'files':['a\\b']},
                       {'files':['a:stream']},{'files':['a','a']},{'writable':[]},{'acceptance':['']},
                       {'command':['python','bad']}):
            with self.subTest(change=change),self.assertRaises(DomainError):Store.validate_project({**self.project,**change})

    def test_project_required_only_for_configured_development_adapter(self):
        with self.assertRaises(DomainError):self.runtime.start(self.task['id'],1)
        self.config.write_text(json.dumps({'label':'Text fixture','command':[sys.executable,str(Path(__file__).resolve())]}))
        with self.assertRaises(DomainError):self.runtime.start(self.task['id'],1,project=self.project)
        self.assertEqual(self.store.agent_runs(),[])

    def test_reservation_freezes_profile_with_mode_and_version_guards(self):
        with self.assertRaises(DomainError):Store(self.store.path,source='agent').reserve_agent_run(self.task['id'],1,'x',[],project=self.project)
        with self.assertRaises(DomainError):self.store.reserve_agent_run(self.task['id'],99,'x',[],project=self.project)
        run=self.store.reserve_agent_run(self.task['id'],1,'x',['private'],project=self.project)
        self.project['files'].append('later.py')
        saved=Store(self.store.path).agent_runs()[0]
        self.assertEqual(saved['project']['files'],['app.py','test_app.py'])
        self.assertNotIn('command',saved)
        self.assertEqual(saved['id'],run['id'])

    def test_artifacts_only_read_fixed_files_inside_the_selected_run(self):
        run=self.store.reserve_agent_run(self.task['id'],1,'x',[],project=self.project)
        folder=self.folder/'.agent-runs'/run['id'];(folder/'development'/'result').mkdir(parents=True)
        (folder/'development'/'result'/'changes.diff').write_text('+<script>not executable</script>',encoding='utf-8')
        self.assertIn(b'<script>',self.runtime.artifact(run['id'],'diff'))
        for name in ('../owner','owner','files/app.py','manifest'):
            with self.subTest(name=name),self.assertRaises(DomainError):self.runtime.artifact(run['id'],name)
        with self.assertRaises(DomainError):self.runtime.artifact(run['id'],'diff',limit=2)
        (folder/'progress.json').write_text('{bad json',encoding='utf-8')
        self.assertIsNone(self.runtime.snapshot()['runs'][0]['progress'])

    def test_revise_reuses_the_frozen_project_instead_of_current_ui_draft(self):
        run=self.store.reserve_agent_run(self.task['id'],1,'x',[],project=self.project)
        with patch.object(self.runtime,'start',return_value={}) as start:
            self.runtime.revise(run['id'],1,1)
        self.assertEqual(start.call_args.kwargs['project'],self.project)
