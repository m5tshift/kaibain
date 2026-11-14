"""Owned-process/VM observation for isolated Windows crash fixtures only."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from kaibain.agent_runtime import cleanup_owned,process_alive
from windows_owned_process import kill_owned_interpreter

INSPECT=r'''
import json,sys
from pathlib import Path
runtime,state,settings=sys.argv[1:4]
sys.path.insert(0,str(Path(runtime)/'src'))
from sandbox import SandboxManager
state=Path(state);assert (state/'owner').is_file()
options=json.loads(settings)
manager=SandboxManager(state,environment=options['environment'],guest_toolchain=options['toolchain'],guest_toolchain_sha256=options['toolchain_sha256'])
values=[]
for path in sorted(state.glob('*.json')):
 if len(path.stem)!=32:continue
 value=manager.inspect(path.stem)
 values.append({key:value.get(key) for key in ('id','owner','run_id','runtime_id','running','missing','status','policy')})
print(json.dumps(values))
'''


class CrashFixture:
    def __init__(self,store,runtime,python,*,environment=None,toolchain=None,toolchain_sha256=None,min_records=2):
        if os.name!='nt':raise ValueError('Windows-only owned supervisor fixture')
        self.store=store;self.root=store.path.parent;self.runtime=runtime;self.python=python
        self.owned={};self.real_popen=subprocess.Popen;self.patcher=None;self.outsider=None
        self.settings={'environment':environment,'toolchain':toolchain,'toolchain_sha256':toolchain_sha256}
        self.min_records=min_records
        self.previous_cleaned_before_launch=False
        self.prelaunch_vm_states={}

    def start(self):
        def launch(argv,*args,**kwargs):
            ours=(isinstance(argv,list) and 'agent-job' in argv and '--run-id' in argv and '--db' in argv
                and Path(argv[argv.index('--db')+1]).resolve()==self.store.path)
            if ours and self.owned:
                for identifier in self.owned:
                    state=self.root/'.agent-runs'/identifier
                    receipt=json.loads((state/'cleanup.json').read_bytes())
                    assert receipt['ok'] is True and receipt['records_checked']>=self.min_records
                    records=[p for p in (state/'microvms').glob('*.json') if len(p.stem)==32]
                    assert records and all(json.loads(p.read_bytes())['status']=='stopped' for p in records)
                self.previous_cleaned_before_launch=True
                if len(self.owned)==1:
                    identifier=next(iter(self.owned));values=self.inspect(identifier)
                    assert values and all(not value['running'] and value.get('missing') for value in values)
                    self.prelaunch_vm_states[identifier]=values
            process=self.real_popen(argv,*args,**kwargs)
            if ours:self.owned[argv[argv.index('--run-id')+1]]=process
            return process
        self.patcher=patch('kaibain.agent_runtime.subprocess.Popen',new=launch);self.patcher.start()
        # No venv launcher is needed for this unrelated, stdlib-only sentinel.
        self.outsider=self.real_popen([sys._base_executable,'-I','-c','import time;time.sleep(600)'],
            creationflags=subprocess.CREATE_NO_WINDOW,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

    def inspect(self,identifier):
        assert identifier in self.owned
        state=self.root/'.agent-runs'/identifier/'microvms'
        env={**os.environ,'PYTHONIOENCODING':'utf-8'};env.pop('KAIBAIN_CONTROL_TOKEN',None)
        value=subprocess.run([str(self.python),'-c',INSPECT,str(self.runtime),str(state),json.dumps(self.settings)],
            stdin=subprocess.DEVNULL,capture_output=True,env=env,timeout=25,creationflags=subprocess.CREATE_NO_WINDOW)
        with (state.parent/'fixture-vm-inspect.log').open('ab') as log:log.write(value.stdout+value.stderr)
        value.check_returncode();return json.loads(value.stdout.decode('utf8').splitlines()[-1])

    async def kill(self,identifier):
        process=self.owned[identifier]
        with self.store.connection() as db:pid=self.store._run(db,identifier)['pid']
        return await asyncio.to_thread(kill_owned_interpreter,process,pid)

    async def close(self):
        try:
            for identifier,process in self.owned.items():
                if process.poll() is None:
                    with self.store.connection() as db:pid=self.store._run(db,identifier)['pid']
                    if pid and process_alive(pid):await asyncio.to_thread(kill_owned_interpreter,process,pid)
                    else:
                        self.store.finish_agent_run(identifier,'interrupted','Fixture ended before supervisor claim',only_status='queued')
                        process.kill()
                await asyncio.to_thread(process.wait,5)
            for identifier in self.owned:
                state=self.root/'.agent-runs'/identifier
                if not (state/'microvms').exists():continue
                receipt=state/'cleanup.json'
                if receipt.exists() and json.loads(receipt.read_bytes()).get('ok'):continue
                with self.store.connection() as db:command=json.loads(self.store._run(db,identifier)['command'])
                with (state/'fixture-emergency-cleanup.log').open('ab') as log:
                    cleanup_owned([*command,'--cleanup','--output',str(state/'report.json')],state,log)
        finally:
            if self.outsider is not None:
                if self.outsider.poll() is None:self.outsider.kill()
                self.outsider.wait(timeout=5)
            if self.patcher:self.patcher.stop()
