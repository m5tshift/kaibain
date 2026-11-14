"""Readiness must never mutate goals or reserve work on a failed probe."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from kaibain.store import Store,DomainError
from kaibain.agent_runtime import AgentRuntime


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)
        self.store=Store(self.folder/'tasks.sqlite3');self.goal=self.store.create('Untouched goal')
        self.profile={'repo':str(self.folder),'commit':'a'*40,'files':['app.py'],'writable':['app.py'],
                      'checks':['python','-m','unittest'],'acceptance':['API preserved']}
        self.state=self.folder/'state';self.state.write_text('good')
        self.counter=self.folder/'counter'
        self.adapter=self.folder/'probe.py'
        self.adapter.write_text('''import json,os,sys,time
from pathlib import Path
folder=Path(__file__).parent
assert '--diagnose' in sys.argv
assert not any(key in os.environ for key in ('KAIBAIN_CONTROL_TOKEN','KAIBAIN_RUN_ID','KAIBAIN_CANCEL_FILE'))
counter=folder/'counter';counter.write_text(str(int(counter.read_text())+1) if counter.exists() else '1')
state=(folder/'state').read_text()
if state=='hang':time.sleep(60)
if state=='large':print('x'*100000);raise SystemExit()
ids=['ollama','coordinator_model','developer_model','sandbox']
if '--diagnostic-input' in sys.argv:
    data=json.loads(Path(sys.argv[sys.argv.index('--diagnostic-input')+1]).read_text(encoding='utf-8'))
    assert len(data['commit'])==40
    ids.append('project_files')
if state=='incomplete':ids.remove('sandbox')
rows=[{'id':key,'label':key,'status':'failed' if state=='offline' and key=='ollama' else 'passed',
       'message':'Ollama offline' if state=='offline' else 'available','hint':'Open Ollama','details':{}} for key in ids]
print(json.dumps({'protocol':'readiness-v1','ready':True,'checks':rows}))
''',encoding='utf-8')
        self.config=self.folder/'runtime.json';self.config.write_text(json.dumps({'kind':'development-v1',
            'diagnostics':'readiness-v1','label':'Controlled probe','command':[sys.executable,str(self.adapter)]}))
        self.runtime=AgentRuntime(self.store,self.config)
        self.git=patch('kaibain.git_project.resolve_commit',return_value='a'*40);self.git.start()

    def tearDown(self):
        self.git.stop();self.temp.cleanup()

    def assert_untouched(self):
        self.assertEqual(self.store.get(self.goal['id']),self.goal)
        self.assertEqual(self.store.agent_runs(),[])
        self.assertEqual(self.store.list_agents()['agents'],[])

    def test_read_only_probe_does_not_start_agents_and_strips_control_context(self):
        with patch.dict(os.environ,{'KAIBAIN_CONTROL_TOKEN':'private','KAIBAIN_RUN_ID':'not-a-run','KAIBAIN_CANCEL_FILE':'private'}):
            report=self.runtime.readiness(project=self.profile)
        self.assertTrue(report['ready'],report);self.assertEqual(report['source_commit'],'a'*40)
        self.assert_untouched()

    def test_start_rechecks_environment_even_after_cached_positive_result(self):
        self.assertTrue(self.runtime.readiness(project=self.profile)['ready'])
        self.assertTrue(self.runtime.readiness(project=self.profile)['ready'])
        self.assertEqual(self.counter.read_text(),'1')
        self.state.write_text('offline')
        with self.assertRaises(DomainError) as caught:
            self.runtime.start(self.goal['id'],1,project=self.profile)
        self.assertEqual(caught.exception.code,'RUNTIME_NOT_READY')
        self.assertFalse(caught.exception.details['readiness']['ready'])
        self.assertEqual(self.counter.read_text(),'2');self.assert_untouched()

    def test_incomplete_or_oversized_reports_fail_closed(self):
        for state in ('incomplete','large'):
            with self.subTest(state=state):
                self.state.write_text(state)
                report=self.runtime.readiness(project=self.profile,fresh=True)
                self.assertFalse(report['ready']);self.assertEqual(report['checks'][-1]['id'],'diagnostics')
        self.assert_untouched()

    def test_toolchain_configuration_requires_its_diagnostic_check(self):
        value=json.loads(self.config.read_text())
        value['command']+=['--guest-toolchain-bundle',str(self.folder/'bundle'),'--guest-toolchain-sha256','a'*64]
        self.config.write_text(json.dumps(value))
        report=self.runtime.readiness(project=self.profile,fresh=True)
        self.assertFalse(report['ready']);self.assertEqual(report['checks'][-1]['id'],'diagnostics')
        self.assert_untouched()

    def test_diagnostic_timeout_cleans_only_its_own_process(self):
        self.state.write_text('hang')
        with patch('kaibain.readiness.PROBE_TIMEOUT_SECONDS',1):
            report=self.runtime.readiness(project=self.profile)
        self.assertFalse(report['ready']);self.assertIn('12',report['checks'][-1]['message'])
        self.assert_untouched()

    def test_project_failure_and_missing_runtime_are_actionable(self):
        with patch('kaibain.git_project.resolve_commit',side_effect=DomainError('PROJECT_COMMIT_UNAVAILABLE','Missing source',409)):
            report=self.runtime.readiness(project=self.profile)
        self.assertFalse(report['ready']);self.assertEqual(report['checks'][1]['details']['code'],'PROJECT_COMMIT_UNAVAILABLE')
        self.config.unlink()
        report=self.runtime.readiness()
        self.assertFalse(report['ready']);self.assertEqual(report['checks'][0]['id'],'runtime')
        self.assert_untouched()


if __name__=='__main__':
    unittest.main()
