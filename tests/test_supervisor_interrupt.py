"""Durable interruption state after owned cleanup; isolated Store and adapters."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from kaibain.store import Store
from kaibain.agent_runtime import run_job,cleanup_owned,closed_owned_scope
from kaibain.process_scope import ProcessCleanupError


class SupervisorInterruptTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name).resolve();self.store=Store(self.folder/'tasks.sqlite3')
        self.task=self.store.create('Interruption fixture')
        self.profile={'repo':str(self.folder),'commit':'a'*40,'files':['app.py'],
                      'writable':['app.py'],'checks':['python','-m','unittest'],'acceptance':['Checks pass']}
        self.command=[sys.executable,str(self.folder/'trusted_adapter.py')]

    def reserve(self,project=True):
        run=self.store.reserve_agent_run(self.task['id'],1,'Fixture',self.command,project=self.profile if project else None)
        return run,self.folder/'.agent-runs'/run['id']

    def scope(self,folder,run,**changes):
        receipt={'pid':12345,'platform':os.name,'run_id':run['id'],'status':'closed',**changes}
        (folder/'scope.json').write_text(json.dumps(receipt),encoding='utf-8')

    def fake_completed(self,folder):
        (folder/'report.json').write_text(json.dumps({'ok':True,'root_id':self.task['id'],
            'child_id':self.task['id'],'result':{'summary':'Must not publish','checks':['fixture']},
            'inference':[],'verification':{'status':'passed'}}),encoding='utf-8')

    def saved(self):return self.store.agent_runs()[0]

    def assert_interrupted(self,code):
        current=self.saved();self.assertEqual(current['status'],'interrupted')
        self.assertEqual(current['failure_code'],code);self.assertEqual(current['failure_kind'],'environment')
        self.assertIsNone(current['result']);self.assertEqual(current['decision'],'pending')
        self.assertEqual(self.store.get(self.task['id']),self.task)
        self.assertEqual(Store(self.store.path).agent_runs()[0],current)

    def test_interrupt_after_release_runs_cleanup_once_then_records_terminal_state(self):
        for error in (KeyboardInterrupt(),SystemExit(42)):
            with self.subTest(error=type(error).__name__):
                run,folder=self.reserve();order=[]
                def execute(*args,**kwargs):
                    self.scope(folder,run);self.fake_completed(folder);order.append('execute');raise error
                def clean(argv,actual_folder,log):
                    order.append('cleanup');self.assertEqual(actual_folder,folder)
                    self.assertEqual(argv,[*self.command,'--cleanup','--output',str(folder/'report.json')])
                    self.assertEqual(self.saved()['status'],'running')
                with patch('kaibain.agent_runtime.execute_owned',side_effect=execute), \
                        patch('kaibain.agent_runtime.cleanup_owned',side_effect=clean) as cleanup, \
                        patch('kaibain.agent_runtime.subprocess.Popen') as spawn:
                    with self.assertRaises(type(error)) as caught:run_job(self.store,run['id'])
                self.assertIs(caught.exception,error);cleanup.assert_called_once();spawn.assert_not_called()
                self.assertEqual(order,['execute','cleanup']);self.assert_interrupted('supervisor_interrupted')
                self.assertIn(type(error).__name__,self.saved()['error'])

    def test_interrupt_before_adapter_release_does_not_claim_cleanup_ran(self):
        run,folder=self.reserve();original=Path.write_text
        def write(path,*args,**kwargs):
            if path.name=='project.json':raise KeyboardInterrupt()
            return original(path,*args,**kwargs)
        with patch.object(Path,'write_text',new=write), \
                patch('kaibain.agent_runtime.execute_owned') as execute, \
                patch('kaibain.agent_runtime.cleanup_owned') as cleanup:
            with self.assertRaises(KeyboardInterrupt):run_job(self.store,run['id'])
        execute.assert_not_called();cleanup.assert_not_called();self.assert_interrupted('supervisor_interrupted')
        self.assertIn('ещё не запускались',self.saved()['error'])

    def test_interrupted_cleanup_is_failure_even_with_successful_adapter_report(self):
        run,folder=self.reserve()
        def execute(*args,**kwargs):self.scope(folder,run);self.fake_completed(folder);return 0
        with patch('kaibain.agent_runtime.execute_owned',side_effect=execute), \
                patch('kaibain.agent_runtime.cleanup_owned',side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):run_job(self.store,run['id'])
        self.assert_interrupted('cleanup_failed');self.assertIn('не подтверждена',self.saved()['error'])

    def test_unknown_active_or_foreign_scope_cannot_confirm_cleanup(self):
        cases=[{'status':'active'}, {'run_id':'foreign'}, {'platform':'foreign'}, {'pid':True}]
        for change in cases:
            with self.subTest(change=change):
                run,folder=self.reserve()
                def execute(*args,**kwargs):self.scope(folder,run,**change);raise KeyboardInterrupt()
                with patch('kaibain.agent_runtime.execute_owned',side_effect=execute), \
                        patch('kaibain.agent_runtime.cleanup_owned') as cleanup, \
                        patch('kaibain.agent_runtime.process_alive') as alive:
                    with self.assertRaises(KeyboardInterrupt):run_job(self.store,run['id'])
                cleanup.assert_called_once();alive.assert_not_called();self.assert_interrupted('cleanup_failed')

    def test_scope_receipts_are_bounded_regular_and_gated_to_this_run(self):
        run,folder=self.reserve();folder.mkdir(parents=True)
        self.assertFalse(closed_owned_scope(folder,run['id']))
        for raw in ('[]','{','x'*4097):
            (folder/'scope.json').write_text(raw);self.assertFalse(closed_owned_scope(folder,run['id']))
        self.scope(folder,run,status='not_started',pid=None)
        self.assertTrue(closed_owned_scope(folder,run['id']))
        self.scope(folder,run,status='not_started',pid=12345)
        self.assertFalse(closed_owned_scope(folder,run['id']))

    def test_text_adapter_interrupt_closes_scope_without_external_vm_cleanup(self):
        run,folder=self.reserve(project=False)
        def execute(*args,**kwargs):self.scope(folder,run);raise KeyboardInterrupt()
        with patch('kaibain.agent_runtime.execute_owned',side_effect=execute), \
                patch('kaibain.agent_runtime.cleanup_owned') as cleanup:
            with self.assertRaises(KeyboardInterrupt):run_job(self.store,run['id'])
        cleanup.assert_not_called();self.assert_interrupted('supervisor_interrupted')

    def test_cleanup_start_failure_is_interrupted_and_never_publishes_success_report(self):
        run,folder=self.reserve()
        def execute(*args,**kwargs):self.scope(folder,run);self.fake_completed(folder);return 0
        with patch('kaibain.agent_runtime.execute_owned',side_effect=execute), \
                patch('kaibain.agent_runtime.ProcessScope',side_effect=OSError('Cannot start cleanup')):
            run_job(self.store,run['id'])
        self.assert_interrupted('cleanup_failed')

    def test_duplicate_supervisor_does_not_signal_or_finalize_foreign_owner(self):
        run,folder=self.reserve();self.store.claim_agent_run(run['id'],os.getpid())
        with patch('kaibain.agent_runtime.execute_owned') as execute,patch('kaibain.agent_runtime.cleanup_owned') as cleanup:
            run_job(self.store,run['id'])
        execute.assert_not_called();cleanup.assert_not_called();self.assertEqual(self.saved()['status'],'running')
        self.store.finish_agent_run(run['id'],'failed','Fixture teardown')


class CleanupProcessTests(unittest.TestCase):
    def test_wait_timeout_nonzero_and_close_failure_are_cleanup_errors(self):
        for case in ('timeout','nonzero','close'):
            with self.subTest(case=case),tempfile.TemporaryDirectory() as temp:
                scope=Mock();scope.process.wait.return_value=0
                if case=='timeout':scope.process.wait.side_effect=subprocess.TimeoutExpired('fixture',1)
                if case=='nonzero':scope.process.wait.return_value=7
                if case=='close':scope.close.side_effect=OSError('close failed')
                with patch('kaibain.agent_runtime.ProcessScope',return_value=scope), \
                        (Path(temp)/'log').open('wb') as log:
                    with self.assertRaises(ProcessCleanupError):cleanup_owned([sys.executable,str(Path(temp)/'adapter.py')],Path(temp),log,timeout=1)
                scope.close.assert_called_once()

    def test_cleanup_keeps_frozen_argv_but_removes_http_control_token(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);scope=Mock();scope.process.wait.return_value=0
            argv=[sys.executable,str(folder/'adapter.py'),'--cleanup','--output',str(folder/'report.json')]
            with (folder/'log').open('wb') as log,patch.dict(os.environ,{'KAIBAIN_CONTROL_TOKEN':'fixture-secret'}), \
                    patch('kaibain.agent_runtime.ProcessScope',return_value=scope) as create:
                cleanup_owned(argv,folder,log)
            self.assertEqual(create.call_args.args[0],argv)
            self.assertNotIn('KAIBAIN_CONTROL_TOKEN',create.call_args.kwargs['env'])
            scope.close.assert_called_once()


if __name__=='__main__':unittest.main()
