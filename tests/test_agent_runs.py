"""Human launch/review controls and durable native executor lifecycle."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from kaibain.store import Store,DomainError
from kaibain.agent_runtime import AgentRuntime,run_job,process_alive


class RunTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)
        self.store=Store(self.folder/'tasks.sqlite3');self.agent=Store(self.store.path,source='agent')
        self.task=self.store.create('Fix addition',description='Preserve API')
        self.project={'repo':str(self.folder),'commit':'a'*40,'files':['app.py','test_app.py'],
                      'writable':['app.py'],'checks':['python','-m','unittest'],'acceptance':['Tests pass']}
        self.executor=self.folder/'fixture.py'
        self.executor.write_text('''import argparse,json,time
from pathlib import Path
p=argparse.ArgumentParser()
for name in ['db','task-id','expected-version','output','project-input','revision-input']:p.add_argument('--'+name)
p.add_argument('--cleanup',action='store_true');a=p.parse_args()
if a.cleanup:
    Path(a.output).with_name('cleanup.json').write_text('{"ok":true}')
else:
    time.sleep(.3)
    Path(a.output).write_text(json.dumps({'ok':True,'root_id':a.task_id,'child_id':a.task_id,
      'result':{'summary':'Fixture result <script>','checks':['Fixture only']},'inference':[],
      'verification':{'status':'passed','tests_run':True}}),encoding='utf-8')
''',encoding='utf-8')
        self.config=self.folder/'runtime.json';self.config.write_text(json.dumps({'kind':'development-v1',
            'label':'Controlled adapter fixture','command':[sys.executable,str(self.executor)]}),encoding='utf-8')
        self.runtime=AgentRuntime(self.store,self.config)

    def tearDown(self):
        deadline=time.monotonic()+6
        while any(r['status'] in ('queued','running','cancelling') for r in self.store.agent_runs()) and time.monotonic()<deadline:
            time.sleep(.05)
        self.temp.cleanup()

    def error(self,code,fn):
        with self.assertRaises(DomainError) as caught:fn()
        self.assertEqual(caught.exception.code,code)

    def reserve(self):
        return self.store.reserve_agent_run(self.task['id'],self.store.get(self.task['id'])['version'],
            'Fixture',[sys.executable,str(self.executor)],project=self.project)

    def completed(self):
        run=self.reserve();self.store.claim_agent_run(run['id'],os.getpid())
        child=self.agent.create('Deliverable',parent_id=self.task['id'],status='review')
        root=self.agent.get(self.task['id']);root=self.agent.update(root['id'],root['version'],{'status':'review'})
        result={'child_id':child['id'],'summary':'Frozen result','checks':['Original checks'],'inference':[],
            'verification':{'status':'passed'},'task_versions':{root['id']:root['version'],child['id']:child['version']}}
        self.store.finish_agent_run(run['id'],'completed',result=result)
        return self.store.agent_runs()[0],root,child

    def pending_handoff(self):
        run=self.reserve();self.store.claim_agent_run(run['id'],os.getpid())
        child=self.agent.create('Deliverable',parent_id=self.task['id'],status='in_progress')
        root=self.agent.get(self.task['id']);root=self.agent.update(root['id'],root['version'],{'status':'in_progress'})
        result={'child_id':child['id'],'summary':'Frozen result','checks':['Fixed checks'],'handoff':'atomic-review-v1',
                'requirements_version':0,'task_versions':{root['id']:root['version'],child['id']:child['version']}}
        return self.store.agent_runs()[0],root,child,result

    def test_report_and_review_statuses_publish_atomically_and_once(self):
        run,root,child,result=self.pending_handoff()
        self.store.finish_agent_run(run['id'],'completed',result=result)
        saved=self.store.agent_runs()[0]
        self.assertEqual(saved['status'],'completed')
        for task in (root,child):
            current=self.store.get(task['id']);self.assertEqual(current['status'],'review')
            self.assertEqual(saved['result']['task_versions'][task['id']],current['version'])
        self.store.finish_agent_run(run['id'],'completed',result=result)
        self.assertEqual(self.store.agent_runs()[0],saved)
        self.store.review_agent_run(saved['id'],saved['version'],'accepted')

    def test_stop_before_final_publication_leaves_checkpoint_task_versions_intact(self):
        run,root,child,result=self.pending_handoff()
        self.store.stop_agent_run(run['id'],run['version'])
        self.store.finish_agent_run(run['id'],'completed',result=result)
        self.assertEqual(self.store.agent_runs()[0]['status'],'cancelled')
        for task in (root,child):self.assertEqual(self.store.get(task['id']),task)

    def test_conflicting_handoff_rolls_back_statuses_and_report(self):
        run,root,child,result=self.pending_handoff()
        child=self.agent.update(child['id'],child['version'],{'description':'Changed before final publication'})
        self.error('CONFLICT',lambda:self.store.finish_agent_run(run['id'],'completed',result=result))
        self.assertEqual(self.store.get(root['id'])['status'],'in_progress')
        self.assertEqual(self.store.get(child['id']),child)
        self.assertIsNone(self.store.agent_runs()[0]['result'])
        self.store.finish_agent_run(run['id'],'failed','Fixture conflict')

    def test_native_launch_guards_atomic_reservation_and_private_command(self):
        self.error('CONFLICT',lambda:self.runtime.start(self.task['id'],99,project=self.project))
        self.error('FORBIDDEN',lambda:self.agent.reserve_agent_run(self.task['id'],1,'x',[]))
        child=self.agent.create('Child',parent_id=self.task['id'])
        self.error('GOAL_REQUIRED',lambda:self.runtime.start(child['id'],1,project=self.project))
        def reserve(_):
            try:return self.reserve()
            except DomainError as error:return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:values=list(pool.map(reserve,range(2)))
        self.assertEqual(sum(isinstance(v,dict) for v in values),1);self.assertIn('RUN_BUSY',values)
        run=next(v for v in values if isinstance(v,dict));self.assertNotIn('command',run)
        self.store.finish_agent_run(run['id'],'failed','fixture cleanup')

    def test_detached_completion_cleanup_and_persistence(self):
        run=self.runtime.start(self.task['id'],1,project=self.project)
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            saved=AgentRuntime(Store(self.store.path),self.config).snapshot()['runs'][0]
            if saved['status'] not in ('queued','running','cancelling'):break
            time.sleep(.1)
        self.assertEqual(saved['status'],'completed',saved)
        self.assertEqual(saved['result']['summary'],'Fixture result <script>')
        folder=self.folder/'.agent-runs'/run['id']
        self.assertEqual(json.loads((folder/'project.json').read_text()),self.project)
        self.assertTrue(json.loads((folder/'cleanup.json').read_text())['ok'])

    def test_duplicate_claim_and_crash_recovery_do_not_restart_work(self):
        run=self.reserve();self.store.claim_agent_run(run['id'],os.getpid());run_job(self.store,run['id'])
        self.assertEqual(self.store.agent_runs()[0]['status'],'running')
        with patch('kaibain.agent_runtime.process_alive',return_value=False):self.runtime.recover()
        self.assertEqual(self.store.agent_runs()[0]['status'],'interrupted')

    def test_human_review_is_available_without_mode_switch_and_serializes_decisions(self):
        run,root,child=self.completed()
        self.error('FORBIDDEN',lambda:self.agent.review_agent_run(run['id'],run['version'],'accepted'))
        def decide(decision):
            try:return self.store.review_agent_run(run['id'],run['version'],decision,'Address the issue')
            except DomainError as error:return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:values=list(pool.map(decide,['accepted','changes_requested']))
        self.assertEqual(sum(isinstance(v,dict) for v in values),1);self.assertIn('CONFLICT',values)
        saved=self.store.agent_runs()[0];status='done' if saved['decision']=='accepted' else 'planned'
        self.assertEqual(saved['result'],run['result'])
        self.assertTrue(all(self.store.get(t['id'])['status']==status for t in (root,child)))

    def test_review_conflict_preserves_newer_agent_changes(self):
        run,root,child=self.completed();self.agent.update(child['id'],child['version'],{'description':'Newer data'})
        self.error('CONFLICT',lambda:self.store.review_agent_run(run['id'],run['version'],'accepted'))
        self.assertEqual(self.store.agent_runs()[0]['decision'],'pending')
        self.assertEqual(self.store.get(root['id'])['status'],'review')

    def test_revision_reuses_child_feedback_and_frozen_project(self):
        run,root,child=self.completed()
        self.error('VALIDATION',lambda:self.store.review_agent_run(run['id'],run['version'],'changes_requested',' '))
        returned=self.store.review_agent_run(run['id'],run['version'],'changes_requested','Add an example')
        root=self.store.get(root['id'])
        self.error('REVISION_REQUIRED',lambda:self.store.reserve_agent_run(root['id'],root['version'],'x',[]))
        with patch('kaibain.agent_runtime.subprocess.Popen') as popen:
            popen.return_value.wait.return_value=0
            revision=self.runtime.revise(run['id'],returned['version'],root['version'])
        self.assertEqual(revision['revision_of'],run['id']);self.assertEqual(revision['project'],self.project)
        self.assertEqual(self.store.agent_runs()[1]['result'],run['result'])
        self.store.finish_agent_run(revision['id'],'failed','fixture cleanup')

    def test_stop_fences_only_owned_mcp_writes_and_wins_completion_race(self):
        run=self.reserve();self.store.claim_agent_run(run['id'],os.getpid())
        with patch.dict(os.environ,{'KAIBAIN_RUN_ID':run['id']}):owned=Store(self.store.path,source='agent')
        current=self.store.agent_runs()[0];self.store.stop_agent_run(run['id'],current['version'])
        self.error('RUN_STOPPED',lambda:owned.create('Late task'))
        self.agent.create('Independent agent goal')
        self.store.finish_agent_run(run['id'],'completed',result={'summary':'Too late'})
        self.assertEqual(self.store.agent_runs()[0]['status'],'cancelled');self.assertIsNone(self.store.agent_runs()[0]['result'])

    def test_queued_stop_and_legacy_stop_guards(self):
        run=self.reserve();stopped=self.store.stop_agent_run(run['id'],run['version'])
        self.assertEqual(stopped['status'],'cancelled')
        self.error('RUN_EXPIRED',lambda:self.store.claim_agent_run(run['id'],os.getpid()))
        self.error('RUN_FINISHED',lambda:self.store.stop_agent_run(run['id'],stopped['version']))
        run=self.reserve()
        with self.store.connection(write=True,control=True) as db:db.execute('UPDATE agent_runs SET stop_supported=0 WHERE id=?',(run['id'],))
        self.error('STOP_UNAVAILABLE',lambda:self.store.stop_agent_run(run['id'],run['version']))
        self.store.finish_agent_run(run['id'],'failed','fixture cleanup')

    def test_pending_reviews_stay_visible_after_recent_history_limit(self):
        run,_,_=self.completed();other=self.store.create('Other goal')
        for _ in range(22):
            newer=self.store.reserve_agent_run(other['id'],1,'fixture',[])
            self.store.finish_agent_run(newer['id'],'failed','fixture')
        self.assertIn(run['id'],[r['id'] for r in self.store.agent_runs()])

    def test_unsafe_or_text_adapter_is_unavailable(self):
        self.config.write_text(json.dumps({'label':'Old text adapter','command':[sys.executable,str(self.executor)]}))
        self.assertFalse(self.runtime.snapshot()['available'])
        self.error('RUNTIME_UNAVAILABLE',lambda:self.runtime.start(self.task['id'],1,project=self.project))


if __name__=='__main__':unittest.main()
