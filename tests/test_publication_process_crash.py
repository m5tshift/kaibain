"""Real owned process death at SQLite publication boundaries; no models or VM."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from kaibain.agent_runtime import AgentRuntime
from kaibain.store import Store
from test_recovery import recovery_fixture

CHILD=r'''
import json,os,sys,time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
from kaibain.store import Store
store=Store(sys.argv[2]);run_id,stage=sys.argv[3:5];marker=Path(sys.argv[5])
with store.connection() as db:
 run=store._run(db,run_id);cp=store._checkpoint_for_run(db,run_id)
result={'handoff':'atomic-review-v1','child_id':cp['child_id'],'summary':'Process boundary fixture',
 'checks':['SQLite only; not project checks'],'requirements_version':cp['requirements_version'],
 'task_versions':{run['task_id']:cp['task_version'],cp['child_id']:cp['child_version']}}
touch=Store._touch
def paused(db,ids):
 touch(db,ids)
 if set(ids)==set(result['task_versions']):
  marker.write_text(json.dumps({'stage':'transaction-open','pid':os.getpid()}),encoding='ascii')
  time.sleep(90)
if stage=='before_commit':
 with patch.object(Store,'_touch',side_effect=paused):
  store.finish_agent_run(run_id,'completed',result=result)
else:
 store.finish_agent_run(run_id,'completed',result=result)
 marker.write_text(json.dumps({'stage':'committed','pid':os.getpid()}),encoding='ascii')
 time.sleep(90)
'''


class PublicationProcessCrashTests(unittest.TestCase):
    def exercise(self,stage):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);store,runtime,run,cp,export=recovery_fixture(folder,terminal=False)
            tasks={identifier:store.get(identifier) for identifier in (run['task_id'],cp['child_id'])}
            files={p.relative_to(export).as_posix():p.read_bytes() for p in export.rglob('*') if p.is_file()}
            marker=folder/'ready';process=None;outsider=None
            options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
            try:
                outsider=subprocess.Popen([sys.executable,'-c','import time;time.sleep(90)'],**options)
                with (folder/'child.log').open('wb') as log:
                    process=subprocess.Popen([sys.executable,'-c',CHILD,str(Path(__file__).resolve().parents[1]/'src'),
                        str(store.path),run['id'],stage,str(marker)],stdout=log,stderr=log,**options)
                    deadline=time.monotonic()+10
                    while not marker.exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.02)
                    self.assertTrue(marker.exists(),(folder/'child.log').read_text(errors='replace'))
                    self.assertIsNone(process.poll());self.assertIsNone(outsider.poll())
                    actual=json.loads(marker.read_bytes())['pid']
                    if os.name=='nt':
                        from windows_owned_process import kill_owned_interpreter
                        kill_owned_interpreter(process,actual)
                    else:
                        self.assertEqual(actual,process.pid);process.kill();process.wait(timeout=5)
                # Reopen after real process termination; no Python finally ran.
                store=Store(store.path);runtime=AgentRuntime(store,runtime.config_path)
                if stage=='before_commit':
                    self.assertEqual(store.agent_runs()[0]['status'],'running')
                    self.assertEqual({identifier:store.get(identifier) for identifier in tasks},tasks)
                    runtime.recover();saved=store.agent_runs()[0]
                    self.assertEqual(saved['status'],'interrupted');self.assertEqual(saved['failure_code'],'supervisor_lost')
                    self.assertIsNone(saved['result'])
                    self.assertTrue(runtime.recovery_preview(run['id'])['recoverable'])
                else:
                    saved=store.agent_runs()[0];self.assertEqual(saved['status'],'completed')
                    self.assertEqual(saved['decision'],'pending');self.assertIsNotNone(saved['result'])
                    for identifier,before in tasks.items():
                        current=store.get(identifier)
                        self.assertEqual(current['status'],'review');self.assertEqual(current['version'],before['version']+1)
                runtime.recover();self.assertEqual(store.agent_runs()[0],saved)
                self.assertEqual(len(store.agent_runs()),1)
                self.assertEqual({p.relative_to(export).as_posix():p.read_bytes() for p in export.rglob('*') if p.is_file()},files)
                with store.connection() as db:
                    self.assertEqual(store._checkpoint_for_run(db,run['id']),cp)
                    self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                self.assertIsNone(outsider.poll())
            finally:
                for child in (process,outsider):
                    if child is not None:
                        if child.poll() is None:child.kill()
                        child.wait(timeout=5)

    def test_process_death_before_commit_rolls_back_and_keeps_checkpoint(self):self.exercise('before_commit')
    def test_process_death_after_commit_keeps_pending_human_review(self):self.exercise('after_commit')


if __name__=='__main__':unittest.main()
