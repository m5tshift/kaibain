"""Published checkpoint integrity, human retry controls and immutable run lineage."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from kaibain.store import Store,DomainError
from kaibain.agent_runtime import AgentRuntime,process_alive
from kaibain.process_scope import ProcessCleanupError
from kaibain.recovery import failure_kind


def recovery_fixture(folder,terminal=True):
    store=Store(folder/'tasks.sqlite3');goal=store.create('Recover addition',description='Preserve add(a, b).')
    script=folder/'adapter.py';script.write_text('import sys\n')
    command=[sys.executable,str(script)]
    config=folder/'runtime.json';config.write_text(json.dumps({'kind':'development-v1','label':'Recovery fixture',
        'recovery':'checkpoint-v1','command':command}))
    project={'repo':str(folder),'commit':'a'*40,'files':['app.py','test_app.py'],'writable':['app.py'],
             'checks':['python','-m','unittest'],'acceptance':['Correct sums']}
    run=store.reserve_agent_run(goal['id'],1,'Recovery fixture',command,project=project)
    store.claim_agent_run(run['id'],999999999)
    with patch.dict(os.environ,{'KAIBAIN_RUN_ID':run['id']}):agent=Store(store.path,source='agent')
    agent.register_agent('c','secret','Coordinator','coordinator','fixture','Fixture')
    agent.assign_agent('c','secret','c',goal['id'],1)
    child=agent.create('Existing work',parent_id=goal['id'],status='in_progress')
    root=agent.get(goal['id']);root=agent.update(root['id'],root['version'],{'status':'in_progress'})
    folder=folder/'.agent-runs'/run['id']/'development'/'checkpoints'/'000';(folder/'files').mkdir(parents=True)
    values={'app.py':b'def add(a,b): return a+b\n','test_app.py':b'# original checks\n'}
    patch_bytes=b'+ return a+b\n';(folder/'changes.diff').write_bytes(patch_bytes)
    entries=[]
    for name,data in values.items():
        (folder/'files'/name).write_bytes(data);entries.append({'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    raw=json.dumps({'base_commit':'a'*40,'backend':'microsandbox','isolation':'microvm','deleted':[],
                    'patch_sha256':hashlib.sha256(patch_bytes).hexdigest(),'files':entries}).encode()
    (folder/'manifest.json').write_bytes(raw)
    checkpoint=agent.publish_checkpoint('c','secret',0,hashlib.sha256(raw).hexdigest(),child['id'],root['version'],child['version'])
    if terminal:store.finish_agent_run(run['id'],'failed','Scripted model error',failure_code='invalid_model_response')
    (folder.parents[2]/'scope.json').write_text(json.dumps({'pid':999999999,'platform':os.name,'run_id':run['id'],'status':'closed'}))
    return store,AgentRuntime(store,config),store.agent_runs()[0],checkpoint,folder


class AtomicCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)
        self.store,self.runtime,self.run,self.previous,self.export=recovery_fixture(self.folder,terminal=False)
        with patch.dict(os.environ,{'KAIBAIN_RUN_ID':self.run['id']}):self.agent=Store(self.store.path,source='agent')
        self.worker=self.agent.create('Worker',parent_id=self.previous['child_id'])
        self.root=self.agent.get(self.run['task_id']);self.child=self.agent.get(self.previous['child_id'])
        self.snapshot=self.export.parent/'001';(self.snapshot/'files').mkdir(parents=True)
        for name in ('app.py','test_app.py'):(self.snapshot/'files'/name).write_bytes((self.export/'files'/name).read_bytes())
        (self.snapshot/'changes.diff').write_bytes((self.export/'changes.diff').read_bytes())
        self.manifest=json.loads((self.export/'manifest.json').read_bytes())
        self.manifest['parallel']={'version':1,'tasks':{'worker':{'id':self.worker['id'],'version':2,'status':'in_progress'}}}
    def tearDown(self):self.temp.cleanup()
    def publish(self,updates=None):
        raw=json.dumps(self.manifest).encode();(self.snapshot/'manifest.json').write_bytes(raw)
        return self.agent.publish_checkpoint('c','secret',1,hashlib.sha256(raw).hexdigest(),self.child['id'],
            self.root['version'],self.child['version'],task_updates=updates if updates is not None else
            [{'task_id':self.worker['id'],'expected_version':1,'status':'in_progress'}])

    def test_transition_scope_versions_and_schema_are_enforced_without_partial_writes(self):
        base={'task_id':self.worker['id'],'expected_version':1,'status':'in_progress'}
        cases=[('CHECKPOINT_SCOPE',[{**base,'task_id':self.root['id'],'expected_version':self.root['version']}]),
            ('CHECKPOINT_SCOPE',[{**base,'task_id':self.child['id'],'expected_version':self.child['version']}]),
            ('VALIDATION',[base,base]),('VALIDATION',[{**base,'status':'review'}]),
            ('VALIDATION',[{**base,'expected_version':True}]),('CONFLICT',[{**base,'expected_version':2}])]
        for code,updates in cases:
            with self.subTest(code=code,updates=updates),self.assertRaises(DomainError) as caught:self.publish(updates)
            self.assertEqual(caught.exception.code,code)
            self.assertEqual(self.store.get(self.worker['id']),self.worker)
            self.assertEqual(self.store.get(self.root['id'])['version'],self.root['version'])

    def test_statuses_and_checkpoint_commit_together_and_manifest_failure_rolls_back(self):
        before={task['id']:task for task in (self.root,self.child,self.worker)};revision=self.store.revision()
        self.manifest['parallel']['tasks']['worker']['version']=1
        with self.assertRaises(DomainError) as caught:self.publish()
        self.assertEqual(caught.exception.code,'CHECKPOINT_CONFLICT')
        self.assertEqual({task_id:self.store.get(task_id) for task_id in before},before);self.assertEqual(self.store.revision(),revision)
        with self.store.connection() as db:self.assertEqual(db.execute('SELECT count(*) FROM run_checkpoints').fetchone()[0],1)
        self.manifest['parallel']['tasks']['worker']['version']=2;checkpoint=self.publish()
        self.assertEqual(self.store.get(self.worker['id'])['status'],'in_progress')
        self.assertEqual(self.store.get(self.worker['id'])['version'],2)
        self.assertEqual(checkpoint['task_version'],self.root['version']+1);self.assertEqual(checkpoint['child_version'],self.child['version']+1)

    def test_stop_waits_for_checkpoint_transaction_and_cannot_split_versions(self):
        from kaibain.recovery import load_checkpoint
        entered=threading.Event();release=threading.Event();stop_entered=threading.Event()
        def hold(*args):
            entered.set();self.assertTrue(release.wait(3));return load_checkpoint(*args)
        def stop():
            stop_entered.set();return self.store.stop_agent_run(self.run['id'],self.run['version'])
        with ThreadPoolExecutor(max_workers=2) as pool,patch('kaibain.recovery.load_checkpoint',side_effect=hold):
            publication=pool.submit(self.publish);self.assertTrue(entered.wait(3))
            stopping=pool.submit(stop);self.assertTrue(stop_entered.wait(3));self.assertFalse(stopping.done())
            release.set();checkpoint=publication.result(timeout=3);stopping.result(timeout=3)
        self.assertEqual(self.store.get(self.root['id'])['version'],checkpoint['task_version'])
        self.assertEqual(self.store.get(self.child['id'])['version'],checkpoint['child_version'])

    def test_stop_before_publication_fences_every_task_update(self):
        self.store.stop_agent_run(self.run['id'],self.run['version'])
        with self.assertRaises(DomainError) as caught:self.publish()
        self.assertEqual(caught.exception.code,'RUN_STOPPED')
        self.assertEqual(self.store.get(self.root['id'])['version'],self.root['version'])
        self.assertEqual(self.store.get(self.child['id'])['version'],self.child['version'])
        self.assertEqual(self.store.get(self.worker['id']),self.worker)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)
        self.store,self.runtime,self.run,self.checkpoint,self.export=recovery_fixture(self.folder)
    def tearDown(self):self.temp.cleanup()
    def error(self,code,fn):
        with self.assertRaises(DomainError) as caught:fn()
        self.assertEqual(caught.exception.code,code)
    def resume(self):
        return self.runtime.resume(self.run['id'],self.run['version'],self.store.get(self.run['task_id'])['version'],self.checkpoint['id'])

    def test_preview_is_read_only_and_partial_unpublished_export_is_ignored(self):
        revision=self.store.revision();before=self.store.agent_runs()
        partial=self.export.parent/'063';partial.mkdir();(partial/'manifest.json').write_text('{}')
        with patch('kaibain.agent_runtime.subprocess.Popen') as popen:
            preview=self.runtime.recovery_preview(self.run['id'])
        self.assertTrue(preview['recoverable']);self.assertEqual(preview['checkpoint']['id'],self.checkpoint['id'])
        self.assertEqual(len(preview['files']),2);popen.assert_not_called()
        self.assertEqual(self.store.revision(),revision);self.assertEqual(self.store.agent_runs(),before)
        self.assertEqual(Store(self.store.path).agent_runs()[0]['failure_kind'],'model')

    def test_resume_freezes_profile_reuses_child_and_claims_once(self):
        original=self.store.agent_runs()[0]
        with patch('kaibain.agent_runtime.cleanup_owned') as cleanup,patch('kaibain.agent_runtime.process_alive',return_value=False),patch('kaibain.agent_runtime.subprocess.Popen') as popen:
            popen.return_value.wait.return_value=0;new=self.resume()
        self.assertEqual(new['recovery_of'],original['id']);self.assertEqual(new['project'],original['project'])
        self.assertEqual(new['recovery_checkpoint_id'],self.checkpoint['id']);cleanup.assert_called_once()
        self.assertEqual(len(self.store.list(parent_id=original['task_id'])['tasks']),1)
        self.assertEqual(next(r for r in self.store.agent_runs() if r['id']==original['id']),{**original,'recovery_candidate':False})
        self.store.claim_agent_run(new['id'],999999998)
        self.error('RECOVERY_STALE',self.resume)
        self.store.finish_agent_run(new['id'],'failed','Fixture ended before task/file writes')
        (self.folder/'.agent-runs'/new['id']/'scope.json').write_text(json.dumps({'pid':999999998,'platform':os.name,'run_id':new['id'],'status':'closed'}))
        # Inherited receipt allows retry if the new attempt failed before changing context.
        preview=self.runtime.recovery_preview(new['id']);self.assertTrue(preview['recoverable'])
        self.assertEqual(preview['checkpoint']['id'],self.checkpoint['id'])

    def test_changed_task_versions_manifest_and_files_block_without_cleanup(self):
        with patch('kaibain.agent_runtime.cleanup_owned') as cleanup:
            for path in (self.export/'manifest.json',self.export/'changes.diff',self.export/'files'/'app.py'):
                before=path.read_bytes();path.write_bytes(before+b'!')
                self.assertFalse(self.runtime.recovery_preview(self.run['id'])['recoverable'])
                self.error('CHECKPOINT_CONFLICT',self.resume);path.write_bytes(before)
            agent=Store(self.store.path,source='agent');child=agent.get(self.checkpoint['child_id'])
            agent.update(child['id'],child['version'],{'description':'Changed since snapshot'})
            self.error('RECOVERY_STALE',self.resume);cleanup.assert_not_called()
        self.assertEqual(len(self.store.agent_runs()),1)

    def test_cleanup_failure_live_process_and_settings_changes_never_reserve(self):
        with patch('kaibain.agent_runtime.process_alive',return_value=True),patch('kaibain.agent_runtime.cleanup_owned') as cleanup:
            self.error('RECOVERY_PROCESS_ALIVE',self.resume);cleanup.assert_not_called()
        with patch('kaibain.agent_runtime.process_alive',return_value=False),patch('kaibain.agent_runtime.cleanup_owned',side_effect=ProcessCleanupError('fixture')):
            self.error('RECOVERY_CLEANUP_FAILED',self.resume)
        value=json.loads(self.runtime.config_path.read_text());value['command'].append('--different-model');self.runtime.config_path.write_text(json.dumps(value))
        self.error('RECOVERY_RUNTIME_CHANGED',self.resume);self.assertEqual(len(self.store.agent_runs()),1)

    def test_readiness_failure_preserves_old_attempt_and_races_reserve_once(self):
        value=json.loads(self.runtime.config_path.read_text());value['diagnostics']='readiness-v1';self.runtime.config_path.write_text(json.dumps(value))
        with patch('kaibain.agent_runtime.process_alive',return_value=False),patch('kaibain.agent_runtime.cleanup_owned'),patch.object(self.runtime,'readiness',return_value={'ready':False,'checks':[{'status':'failed','message':'Ollama stopped'}]}):
            self.error('RUNTIME_NOT_READY',self.resume)
        self.assertEqual(len(self.store.agent_runs()),1)
        value.pop('diagnostics');self.runtime.config_path.write_text(json.dumps(value))
        def attempt(_):
            try:return self.resume()
            except DomainError as error:return error.code
        with patch('kaibain.agent_runtime.process_alive',return_value=False),patch('kaibain.agent_runtime.cleanup_owned'),patch('kaibain.agent_runtime.subprocess.Popen') as popen:
            popen.return_value.wait.return_value=0
            with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,range(2)))
        self.assertEqual(sum(isinstance(r,dict) for r in results),1)
        new=next(r for r in results if isinstance(r,dict));self.store.finish_agent_run(new['id'],'failed','Fixture cleanup')

    def test_external_agents_cannot_publish_or_request_resume_and_old_context_is_fenced(self):
        agent=Store(self.store.path,source='agent')
        self.error('RUN_REQUIRED',lambda:agent.publish_checkpoint('c','secret',0,'a'*64,self.checkpoint['child_id'],1,1))
        self.error('FORBIDDEN',lambda:AgentRuntime(agent,self.runtime.config_path).resume(self.run['id'],self.run['version'],self.checkpoint['task_version'],self.checkpoint['id']))
        with patch.dict(os.environ,{'KAIBAIN_RUN_ID':self.run['id']}):old=Store(self.store.path,source='agent')
        self.error('RUN_STOPPED',lambda:old.create('Late write',parent_id=self.run['task_id']))
        self.error('RECOVERY_REQUIRED',lambda:self.runtime.start(self.run['task_id'],self.checkpoint['task_version'],project=self.run['project']))

    def test_stale_versions_and_checkpoint_selection_are_explicit(self):
        self.error('CONFLICT',lambda:self.runtime.resume(self.run['id'],self.run['version']-1,self.checkpoint['task_version'],self.checkpoint['id']))
        self.error('CHECKPOINT_UNAVAILABLE',lambda:self.runtime.resume(self.run['id'],self.run['version'],self.checkpoint['task_version'],'unknown'))
        with self.store.connection(write=True,control=True) as db:db.execute('DELETE FROM run_checkpoints')
        self.assertFalse(self.runtime.recovery_preview(self.run['id'])['recoverable'])

    def test_missing_or_live_scope_proof_blocks_without_touching_old_processes(self):
        path=self.export.parents[2]/'scope.json';path.unlink()
        self.error('RECOVERY_SCOPE_UNKNOWN',self.resume)
        self.assertFalse(self.runtime.recovery_preview(self.run['id'])['recoverable'])
        with patch('kaibain.recovery.scope_inactive',return_value=False),patch('kaibain.agent_runtime.process_alive',return_value=False),patch('kaibain.agent_runtime.cleanup_owned') as cleanup:
            self.error('RECOVERY_PROCESS_ALIVE',self.resume);cleanup.assert_not_called()

    def test_failed_spawn_remains_recoverable_without_a_released_scope(self):
        with patch('kaibain.agent_runtime.process_alive',return_value=False),patch('kaibain.agent_runtime.cleanup_owned'),patch('kaibain.agent_runtime.subprocess.Popen',side_effect=OSError('Cannot spawn')):
            self.error('RUNTIME_START_FAILED',self.resume)
        latest=self.store.agent_runs()[0]
        self.assertEqual(latest['failure_kind'],'environment');self.assertEqual(latest['recovery_of'],self.run['id'])
        self.assertTrue(self.runtime.recovery_preview(latest['id'])['recoverable'])

    def test_runtime_edit_during_cleanup_is_rejected_atomically(self):
        def changed(*args,**kwargs):
            value=json.loads(self.runtime.config_path.read_text());value['command'].append('--new-model');self.runtime.config_path.write_text(json.dumps(value))
        with patch('kaibain.agent_runtime.process_alive',return_value=False),patch('kaibain.agent_runtime.cleanup_owned',side_effect=changed):
            self.error('RECOVERY_RUNTIME_CHANGED',self.resume)
        self.assertEqual(len(self.store.agent_runs()),1)

    def test_lost_supervisor_is_recorded_without_automatic_resume(self):
        with self.store.connection(write=True,control=True) as db:
            db.execute("UPDATE agent_runs SET status='running',failure_code=NULL WHERE id=?",(self.run['id'],))
        with patch('kaibain.agent_runtime.process_alive',return_value=False),patch('kaibain.agent_runtime.subprocess.Popen') as popen:
            snapshot=self.runtime.snapshot();popen.assert_not_called()
        saved=snapshot['runs'][0]
        self.assertEqual(saved['status'],'interrupted');self.assertEqual(saved['failure_code'],'supervisor_lost')
        self.assertEqual(len(snapshot['runs']),1);self.assertTrue(self.runtime.recovery_preview(saved['id'])['recoverable'])

    def test_failure_categories_do_not_guess_from_messages(self):
        for status,code,kind in [('cancelled','anything','stopped'),('failed','checks_failed','tests'),('failed','model_unavailable','model'),('failed','model_no_progress','model'),
                                 ('interrupted','cleanup_failed','environment'),('failed','step_budget','budget'),('failed','task_conflict','conflict'),('failed','unknown','execution')]:
            self.assertEqual(failure_kind(status,code),kind)


if __name__=='__main__':unittest.main()
