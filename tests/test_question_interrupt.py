"""Lost question reports after cleanup: durable human pause, never auto-resume."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from kaibain.agent_runtime import AgentRuntime,run_job
from kaibain.process_scope import ProcessCleanupError
from kaibain.store import Store,DomainError
from test_recovery import recovery_fixture


class QuestionInterruptTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name).resolve()
        self.store,self.runtime,self.run,self.checkpoint,self.export=recovery_fixture(self.folder,terminal=False)
        with patch.dict(os.environ,{'KAIBAIN_RUN_ID':self.run['id']}):
            self.agent=Store(self.store.path,source='agent')

    def ask(self):
        root=self.agent.get(self.run['task_id'])
        self.asked=self.agent.ask_question(root['id'],root['version'],'Which API must remain?',agent_id='c',owner='secret')
        return self.asked

    def finish(self,**kwargs):
        self.store.finish_agent_run(self.run['id'],'interrupted','Lost after question',
            failure_code='supervisor_interrupted',preserve_pending_question=True,**kwargs)

    def saved(self):return self.store.agent_runs()[0]

    def assert_parked(self):
        saved=self.saved();self.assertEqual(saved['status'],'waiting_for_input')
        self.assertEqual(saved['question_id'],self.asked['question']['id'])
        self.assertIsNone(saved['result']);self.assertIsNone(saved['failure_code'])
        self.assertEqual(saved['decision'],'pending')
        self.assertEqual(self.store.get(self.run['task_id'])['version'],self.checkpoint['task_version']+1)
        self.assertEqual(self.store.get(self.checkpoint['child_id'])['version'],self.checkpoint['child_version'])
        dialogue=self.store.goal_dialogue(self.run['task_id'])
        self.assertTrue(dialogue['needs_input']);self.assertTrue(dialogue['questions'][0]['can_answer'])
        self.assertEqual(dialogue['questions'][0]['status'],'open')
        self.assertIsNone(dialogue['continuation'])
        with self.assertRaises(DomainError) as caught:self.agent.create('Old run write')
        self.assertEqual(caught.exception.code,'RUN_STOPPED')
        return saved

    def test_confirmed_pause_survives_restart_and_human_continuation_keeps_checkpoint(self):
        self.ask()
        with patch('kaibain.agent_runtime.subprocess.Popen') as spawn:
            self.finish();self.assert_parked()
        spawn.assert_not_called()
        self.store=Store(self.store.path);self.runtime=AgentRuntime(self.store,self.runtime.config_path)
        saved=self.assert_parked()
        dialogue=self.store.answer_question(self.run['task_id'],saved['question_id'],1,
            self.store.get(self.run['task_id'])['version'],'Keep add(a, b).')
        paused=dialogue['continuation']
        with patch('kaibain.agent_runtime.subprocess.Popen') as spawn:
            spawn.return_value.wait.return_value=0
            continued=self.runtime.continue_run(paused['id'],paused['version'],dialogue['task_version'])
        self.assertEqual(continued['continuation_of'],self.run['id'])
        self.assertEqual(continued['recovery_checkpoint_id'],self.checkpoint['id'])
        self.assertEqual(continued['project'],saved['project'])
        self.assertEqual(len(self.store.list(parent_id=self.run['task_id'])['tasks']),1)
        self.store.finish_agent_run(continued['id'],'failed','Fixture teardown')

    def test_supervisor_interrupt_and_lost_report_park_only_after_cleanup(self):
        # Real Store transactions; only process execution/cleanup are scripted.
        for fault in ('keyboard','system_exit','missing_report','invalid_report','after_report'):
            with self.subTest(fault=fault),tempfile.TemporaryDirectory() as temp:
                store,runtime,run,checkpoint,export=recovery_fixture(Path(temp).resolve(),terminal=False)
                with patch.dict(os.environ,{'KAIBAIN_RUN_ID':run['id']}):agent=Store(store.path,source='agent')
                order=[];asked={};folder=store.path.parent/'.agent-runs'/run['id']
                def execute(*args,**kwargs):
                    root=agent.get(run['task_id']);asked.update(agent.ask_question(root['id'],root['version'],
                        'Clarify API?',agent_id='c',owner='secret'))
                    (folder/'scope.json').write_text(json.dumps({'run_id':run['id'],'status':'closed','pid':12345,'platform':os.name}))
                    order.append('execute')
                    if fault=='keyboard':raise KeyboardInterrupt()
                    if fault=='system_exit':raise SystemExit(79)
                    if fault=='invalid_report':(folder/'report.json').write_text('{')
                    if fault=='after_report':
                        (folder/'report.json').write_text(json.dumps({'ok':True,'root_id':run['task_id'],
                            'status':'waiting_for_input','question_id':asked['question']['id']}))
                    return 0
                def cleanup(*args,**kwargs):
                    self.assertFalse(store.goal_dialogue(run['task_id'])['questions'][0]['can_answer'])
                    self.assertEqual(store.agent_runs()[0]['status'],'running');order.append('cleanup')
                original=store.finish_agent_run
                def finish(*args,**kwargs):
                    if fault=='after_report' and args[1]=='waiting_for_input':raise KeyboardInterrupt()
                    return original(*args,**kwargs)
                # The fixture already claimed this run; inject only that boundary.
                with store.connection() as db:claimed=store._run(db,run['id'])
                with patch.object(store,'claim_agent_run',return_value=claimed), \
                        patch('kaibain.agent_runtime.execute_owned',side_effect=execute), \
                        patch('kaibain.agent_runtime.cleanup_owned',side_effect=cleanup) as clean, \
                        patch.object(store,'finish_agent_run',side_effect=finish), \
                        patch('kaibain.agent_runtime.subprocess.Popen') as spawn:
                    if fault in ('keyboard','after_report'):
                        with self.assertRaises(KeyboardInterrupt):run_job(store,run['id'])
                    elif fault=='system_exit':
                        with self.assertRaises(SystemExit):run_job(store,run['id'])
                    else:run_job(store,run['id'])
                self.assertEqual(order,['execute','cleanup']);clean.assert_called_once();spawn.assert_not_called()
                saved=Store(store.path).agent_runs()[0]
                self.assertEqual(saved['status'],'waiting_for_input');self.assertEqual(saved['question_id'],asked['question']['id'])
                self.assertIsNone(saved['result']);self.assertIsNone(saved['failure_code'])

    def test_stop_and_unconfirmed_cleanup_never_park(self):
        for condition in ('stop','cleanup_error','scope_active','cleanup_interrupt'):
            with self.subTest(condition=condition),tempfile.TemporaryDirectory() as temp:
                store,runtime,run,checkpoint,export=recovery_fixture(Path(temp).resolve(),terminal=False)
                with patch.dict(os.environ,{'KAIBAIN_RUN_ID':run['id']}):agent=Store(store.path,source='agent')
                folder=store.path.parent/'.agent-runs'/run['id']
                def execute(*args,**kwargs):
                    root=agent.get(run['task_id']);agent.ask_question(root['id'],root['version'],'Clarify?',agent_id='c',owner='secret')
                    (folder/'scope.json').write_text(json.dumps({'run_id':run['id'],'status':'active' if condition=='scope_active' else 'closed','pid':12345,'platform':os.name}))
                    if condition=='stop':store.stop_agent_run(run['id'],store.agent_runs()[0]['version'])
                    raise KeyboardInterrupt()
                def cleanup(*args,**kwargs):
                    if condition=='cleanup_error':raise ProcessCleanupError('No cleanup receipt')
                    if condition=='cleanup_interrupt':raise KeyboardInterrupt()
                with store.connection() as db:claimed=store._run(db,run['id'])
                with patch.object(store,'claim_agent_run',return_value=claimed), \
                        patch('kaibain.agent_runtime.execute_owned',side_effect=execute), \
                        patch('kaibain.agent_runtime.cleanup_owned',side_effect=cleanup):
                    if condition=='cleanup_error':run_job(store,run['id'])
                    else:
                        with self.assertRaises(KeyboardInterrupt):run_job(store,run['id'])
                saved=store.agent_runs()[0];self.assertEqual(saved['status'],'interrupted')
                self.assertEqual(saved['failure_code'],'supervisor_interrupted' if condition=='stop' else 'cleanup_failed')
                self.assertEqual(store.goal_dialogue(run['task_id'])['questions'][0]['status'],'cancelled')

    def test_stale_or_unsealed_question_is_not_salvaged(self):
        mutations={
            'root':"UPDATE tasks SET version=version+1 WHERE id=(SELECT task_id FROM agent_runs LIMIT 1)",
            'child':"UPDATE tasks SET version=version+1 WHERE id=(SELECT child_id FROM run_checkpoints LIMIT 1)",
            'status':"UPDATE tasks SET status='review' WHERE id=(SELECT child_id FROM run_checkpoints LIMIT 1)",
            'parent':"UPDATE tasks SET parent_id=NULL WHERE id=(SELECT child_id FROM run_checkpoints LIMIT 1)",
            'requirements':"INSERT INTO goal_dialogues(task_id,requirements_version) SELECT task_id,1 FROM agent_runs LIMIT 1 ON CONFLICT(task_id) DO UPDATE SET requirements_version=requirements_version+1",
            'unsealed':"UPDATE goal_questions SET checkpoint_id=NULL",
            'foreign_checkpoint':"UPDATE goal_questions SET checkpoint_id='foreign'",
        }
        for condition,sql in mutations.items():
            with self.subTest(condition=condition),tempfile.TemporaryDirectory() as temp:
                store,runtime,run,checkpoint,export=recovery_fixture(Path(temp),terminal=False)
                with patch.dict(os.environ,{'KAIBAIN_RUN_ID':run['id']}):agent=Store(store.path,source='agent')
                root=agent.get(run['task_id']);agent.ask_question(root['id'],root['version'],'Clarify?',agent_id='c',owner='secret')
                # Corruption fixtures use SQL; no ordinary API permits these writes while waiting.
                with store.connection(write=True,control=True) as db:
                    db.execute(sql)
                store.finish_agent_run(run['id'],'interrupted','Lost',preserve_pending_question=True)
                self.assertEqual(store.agent_runs()[0]['status'],'interrupted')
                self.assertEqual(store.goal_dialogue(run['task_id'])['questions'][0]['status'],'cancelled')

    def test_without_explicit_cleanup_confirmation_question_is_cancelled(self):
        self.ask();self.store.finish_agent_run(self.run['id'],'interrupted','Owner disappeared')
        self.assertEqual(self.saved()['status'],'interrupted')
        self.assertFalse(self.store.goal_dialogue(self.run['task_id'])['needs_input'])

    def test_terminal_pause_is_idempotent(self):
        self.ask();self.finish();saved=self.assert_parked();dialogue=self.store.goal_dialogue(self.run['task_id'])
        self.finish()
        self.assertEqual(self.saved(),saved);self.assertEqual(self.store.goal_dialogue(self.run['task_id']),dialogue)

    def test_parking_commit_fault_is_atomic_and_repeat_does_not_touch_task_versions(self):
        for stage in ('before_commit','after_commit'):
            for fault_type in (KeyboardInterrupt,SystemExit):
                with self.subTest(stage=stage,fault=fault_type.__name__),tempfile.TemporaryDirectory() as temp:
                    store,runtime,run,checkpoint,export=recovery_fixture(Path(temp),terminal=False)
                    with patch.dict(os.environ,{'KAIBAIN_RUN_ID':run['id']}):agent=Store(store.path,source='agent')
                    root=agent.get(run['task_id']);asked=agent.ask_question(root['id'],root['version'],'Clarify?',agent_id='c',owner='secret')
                    connect=sqlite3.connect
                    class FaultConnection(sqlite3.Connection):
                        def commit(db):
                            if stage=='after_commit':super().commit()
                            raise fault_type('Parking commit interrupted')
                    def fault_connect(*args,**kwargs):return connect(*args,**kwargs,factory=FaultConnection)
                    with patch('kaibain.store.sqlite3.connect',side_effect=fault_connect):
                        with self.assertRaises(fault_type):
                            store.finish_agent_run(run['id'],'interrupted','Lost report',preserve_pending_question=True)
                    store=Store(store.path)
                    self.assertEqual(store.agent_runs()[0]['status'],'running' if stage=='before_commit' else 'waiting_for_input')
                    store.finish_agent_run(run['id'],'interrupted','Repeat cleanup-confirmed publication',preserve_pending_question=True)
                    saved=Store(store.path).agent_runs()[0]
                    self.assertEqual(saved['status'],'waiting_for_input');self.assertEqual(saved['question_id'],asked['question']['id'])
                    self.assertEqual(saved['version'],run['version']+1)
                    self.assertEqual(store.get(root['id'])['version'],checkpoint['task_version']+1)
                    self.assertEqual(store.get(checkpoint['child_id'])['version'],checkpoint['child_version'])


if __name__=='__main__':unittest.main()
