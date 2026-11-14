"""Owned child/grandchild cleanup, unrelated process survival and timeout."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from kaibain.store import Store
from kaibain.agent_runtime import execute_owned,process_alive
from kaibain.process_scope import ProcessScope,WindowsJob


def wait_for(fn,seconds=8):
    deadline=time.monotonic()+seconds
    while not fn():
        if time.monotonic()>deadline:raise AssertionError('Condition did not become true')
        time.sleep(.03)


def terminated(pid):
    if not process_alive(pid):return True
    if sys.platform.startswith('linux'):
        # Minimal Linux guests need not contain procps/ps. Read the same kernel
        # state directly; a zombie has exited and cannot execute or spawn work.
        try:state=Path(f'/proc/{pid}/stat').read_text().rpartition(')')[2].split()[0]
        except FileNotFoundError:return not process_alive(pid)
        return state in ('Z','X')
    if os.name!='nt':
        status=subprocess.run(['ps','-p',str(pid),'-o','stat='],capture_output=True,text=True).stdout.strip()
        return not status or status.startswith('Z')
    return False


class ScopeTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='nt','Windows venv launcher child verification')
    def test_owned_interpreter_kill_rejects_other_launchers_child(self):
        from windows_owned_process import kill_owned_interpreter
        processes=[];pids=[]
        try:
            for _ in range(2):
                child=subprocess.Popen([sys.executable,'-c','import os,time;print(os.getpid(),flush=True);time.sleep(90)'],
                    stdout=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
                processes.append(child)
                pids.append(int(child.stdout.readline()))
            with self.assertRaises(ValueError):kill_owned_interpreter(processes[0],pids[1])
            self.assertTrue(all(p.poll() is None for p in processes))
            proof=kill_owned_interpreter(processes[0],pids[0])
            self.assertEqual(proof['pid'],pids[0]);self.assertIsNotNone(processes[0].poll())
            self.assertIsNone(processes[1].poll())
        finally:
            for index,child in enumerate(processes):
                try:
                    if child.poll() is None and index<len(pids):kill_owned_interpreter(child,pids[index])
                    elif child.poll() is None:child.kill();child.wait(timeout=5)
                finally:
                    if child.stdout:child.stdout.close()

    def test_linux_termination_checks_kernel_state_without_external_ps(self):
        with patch('sys.platform','linux'),patch('test_process_scope.process_alive',return_value=True),patch.object(Path,'read_text') as read,patch('subprocess.run') as command:
            read.return_value='123 (name with ) parentheses) Z 1 123 0'
            self.assertTrue(terminated(123))
            read.return_value='123 (active process) S 1 123 0'
            self.assertFalse(terminated(123));command.assert_not_called()

    def test_tree_cleanup_does_not_stop_unrelated_process(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);script=folder/'tree.py';pids=folder/'pids.json'
            script.write_text('''import json,os,subprocess,sys,time
from pathlib import Path
assert json.loads(Path('scope.json').read_text())['status']=='active'
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],**({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
Path(sys.argv[1]).write_text(json.dumps([os.getpid(),child.pid]))
time.sleep(60)
''',encoding='utf-8')
            outsider=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],**({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
            try:
                with (folder/'scope.log').open('wb') as log:
                    scope=ProcessScope([sys.executable,str(script),str(pids)],log=log,cwd=temp,env={**os.environ,'KAIBAIN_RUN_ID':'scope-fixture'},receipt=folder/'scope.json')
                    try:wait_for(pids.exists);owned=json.loads(pids.read_text())
                    finally:scope.close()
                proof=json.loads((folder/'scope.json').read_text());self.assertEqual(proof['status'],'closed');self.assertEqual(proof['run_id'],'scope-fixture')
                for pid in owned:wait_for(lambda:terminated(pid))
                self.assertIsNone(outsider.poll())
            finally:
                outsider.kill();outsider.wait()

    def test_timeout_closes_scope_and_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);script=folder/'hang.py'
            script.write_text('import time; time.sleep(60)',encoding='utf-8')
            store=Store(folder/'tasks.sqlite3');task=store.create('Timeout')
            run=store.reserve_agent_run(task['id'],1,'Fixture',[sys.executable,str(script)])
            row=store.claim_agent_run(run['id'],os.getpid())
            with (folder/'log').open('wb') as log:
                code=execute_owned(store,row,[sys.executable,str(script)],folder,log,timeout=.2,grace=.1)
            self.assertNotEqual(code,0)
            self.assertEqual(store.agent_runs()[0]['stop_reason'],'timeout')
            store.finish_agent_run(run['id'],'cancelled')
            self.assertEqual(store.agent_runs()[0]['status'],'failed')
            self.assertIn('10 минут',store.agent_runs()[0]['error'])

    def test_executor_gets_run_context_but_not_http_control_token(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);script=folder/'environment.py';receipt=folder/'environment.json'
            script.write_text('import os,json;from pathlib import Path;Path("environment.json").write_text(json.dumps({k:os.getenv(k) for k in ["KAIBAIN_CONTROL_TOKEN","KAIBAIN_RUN_ID","KAIBAIN_CANCEL_FILE"]}))',encoding='utf-8')
            store=Store(folder/'tasks.sqlite3');task=store.create('Environment test')
            run=store.reserve_agent_run(task['id'],1,'Fixture',[]);row=store.claim_agent_run(run['id'],os.getpid())
            with (folder/'log').open('wb') as log,patch.dict(os.environ,{'KAIBAIN_CONTROL_TOKEN':'fixture-control-secret'}):
                self.assertEqual(execute_owned(store,row,[sys.executable,str(script)],folder,log),0)
            value=json.loads(receipt.read_text())
            self.assertIsNone(value['KAIBAIN_CONTROL_TOKEN'])
            self.assertEqual(value['KAIBAIN_RUN_ID'],run['id'])
            self.assertEqual(Path(value['KAIBAIN_CANCEL_FILE']),folder/'cancel.request')

    @unittest.skipUnless(os.name=='nt','Windows Job Object gate')
    def test_job_assignment_failure_does_not_execute_adapter(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);marker=folder/'executed';children=[];create=subprocess.Popen
            def capture(*args,**kwargs):
                child=create(*args,**kwargs);children.append(child);return child
            with (folder/'log').open('wb') as log,patch.object(WindowsJob,'assign',side_effect=OSError('Fixture failure')), \
                    patch('kaibain.process_scope.subprocess.Popen',side_effect=capture):
                with self.assertRaises(OSError):
                    ProcessScope([sys.executable,'-c',f'open({str(marker)!r},"w").close()'],log=log,cwd=temp,env=os.environ.copy())
            self.assertFalse(marker.exists())
            self.assertEqual(len(children),1);self.assertTrue(children[0].stdin.closed)
            self.assertIsNotNone(children[0].poll())

    @unittest.skipUnless(os.name=='nt','Windows kill-on-owner-close gate')
    def test_killed_owner_closes_children_without_parent_guard_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);script=folder/'owner.py';ready=folder/'ready.json'
            source=str(Path(__file__).resolve().parents[1]/'src')
            script.write_text('''import json,os,subprocess,sys,time
from pathlib import Path
sys.stdin.buffer.read(1)  # Assign the outer safety job before any descendants.
sys.path.insert(0,sys.argv[1])
from kaibain.process_scope import ProcessScope
folder=Path(sys.argv[2])
(folder/'owner.json').write_text(json.dumps({'pid':os.getpid()}))
adapter=folder/'adapter.py'
adapter.write_text("import json,os,subprocess,sys,time\\nfrom pathlib import Path\\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(90)'],creationflags=subprocess.CREATE_NO_WINDOW)\\nPath('ready.json').write_text(json.dumps([os.getpid(),p.pid]))\\ntime.sleep(90)\\n")
with (folder/'scope.log').open('wb') as log:
 scope=ProcessScope([sys.executable,str(adapter)],log=log,cwd=folder,env={**os.environ,'KAIBAIN_RUN_ID':'hard-crash-fixture'},receipt=folder/'scope.json')
 time.sleep(90)
''',encoding='utf8')
            guard=WindowsJob();owner=None;outsider=None
            try:
                outsider=subprocess.Popen([sys.executable,'-c','import time;time.sleep(90)'],creationflags=subprocess.CREATE_NO_WINDOW)
                owner=subprocess.Popen([sys.executable,str(script),source,str(folder)],stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
                guard.assign(owner);owner.stdin.write(b'1');owner.stdin.close()
                wait_for(ready.exists);children=json.loads(ready.read_bytes())
                scope_before=json.loads((folder/'scope.json').read_bytes())
                self.assertEqual(scope_before['status'],'active')
                self.assertIsNone(owner.poll());self.assertIsNone(outsider.poll())
                from windows_owned_process import kill_owned_interpreter
                kill_owned_interpreter(owner,json.loads((folder/'owner.json').read_bytes())['pid'])
                for pid in [scope_before['pid'],*children]:wait_for(lambda:terminated(pid))
                # The outer safety job remains open and has not been terminated.
                # Thus the inner owner's KILL_ON_JOB_CLOSE performed this cleanup.
                self.assertEqual(json.loads((folder/'scope.json').read_bytes()),scope_before)
                self.assertIsNone(outsider.poll())
            finally:
                try:guard.terminate()
                finally:
                    guard.close()
                    for child in (owner,outsider):
                        if child is None:continue
                        if child.poll() is None:child.kill()
                        child.wait(timeout=5)
                        if child.stdin and not child.stdin.closed:child.stdin.close()


if __name__=='__main__':unittest.main()
