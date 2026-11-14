"""Windows real HTTP/MCP/microVM shared-budget failure and explicit recovery.

Scripted model responses/metrics only; no real LLM. Existing prepared image
receipt required. Production configuration, database and projects stay untouched.
"""
import argparse
import asyncio
import hashlib
from http.server import ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from kaibain.store import Store
from kaibain.web import make_server
from kaibain.agent_runtime import cleanup_owned
from kaibain.process_scope import ProcessScope
from playwright.async_api import async_playwright,expect
from browser_parallel_development_check import Models as OriginalModels
from model_fixture_context import has_file_write
from parallel_crash_fixture import CrashFixture

NAMES=('alpha','beta','gamma','delta')
ADAPTER=r'''
import asyncio,hashlib,json,sys
from pathlib import Path
FIXED=json.loads(__FIXED_JSON__)
sys.path.insert(0,str(Path(FIXED['runtime'])/'src'))
from sandbox import SandboxManager
from microvm_backend import IMAGE
from parallel_web import ParallelWebProjection
from development_web import main
save=SandboxManager._save
def prepare(self):
 value=json.loads(Path(FIXED['prepared']).read_bytes())
 assert value['backend']=='microsandbox' and value['image']==IMAGE
 assert value['worker_sha256']==hashlib.sha256((Path(FIXED['runtime'])/'src/sandbox_worker.py').read_bytes()).hexdigest()
 save(self,self.state/'image.json',value)
 return {'ok':True,'prepared':True,'receipt_reused':True}
original=ParallelWebProjection.observe
async def observe(self,item,state,**data):
 await original(self,item,state,**data)
 if '--recovery-input' not in sys.argv and state=='checkpoint' and data['sequence']==0 and item.id in ('alpha','beta'):
  if not hasattr(self,'fixture_baselines'):self.fixture_baselines=set();self.fixture_live=asyncio.Event()
  self.fixture_baselines.add(item.id)
  if len(self.fixture_baselines)==2:
   values=[]
   for key in ('alpha','beta'):
    value=await asyncio.to_thread(self.runner.manager.inspect,self.runner.worker_runners[key].worker)
    assert value['running']
    values.append({k:value.get(k) for k in ('id','running','status','policy')})
   save(self.runner.manager,self.output.parent/'budget-live.json',{'vms':values,'budget':self.runner.budget.snapshot()})
   self.fixture_live.set()
  await asyncio.wait_for(self.fixture_live.wait(),30)
SandboxManager.prepare=prepare
ParallelWebProjection.observe=observe
main()
'''

ORACLE=r'''
import hashlib,json,sys
from pathlib import Path
runtime,state,prepared,repo,commit,result,output=sys.argv[1:]
sys.path.insert(0,str(Path(runtime)/'src'))
from sandbox import SandboxManager
from microvm_backend import IMAGE
manager=SandboxManager(Path(state));image=json.loads(Path(prepared).read_bytes())
assert image['image']==IMAGE and image['worker_sha256']==hashlib.sha256((Path(runtime)/'src/sandbox_worker.py').read_bytes()).hexdigest()
manager._save(manager.state/'image.json',image)
names=('alpha','beta','gamma','delta');files=[name+'.py' for name in names]+['test_contract.py','fixture.bin']
vm=manager.create(repo,commit,files,run_id='budget-recovery-oracle',attempt_id='acceptance')
report={'real_llm_inference':False,'sandbox_id':vm['id']}
try:
 for name in names:manager.write(vm['id'],name+'.py',(Path(result)/(name+'.py')).read_bytes())
 code='import sys;sys.path.insert(0,"/workspace")\n'+'\n'.join('from '+name+' import '+name for name in names)+'\n'
 code+='for f in (alpha,beta,gamma,delta):\n assert f(None)==0\n assert f.__doc__\n for x in (-7,0,3,1.5):\n  assert f(x)==(x+1 if f is alpha else x*2)\n'
 report['checks']=manager.execute(vm['id'],['python','-c',code])
 assert report['checks']['exit_code']==0 and report['checks']['stop_reason'] is None
finally:
 manager.stop(vm['id']);actual=manager.inspect(vm['id']);report['absent']=not actual['running'] and actual['missing']
 manager._save(Path(output),report)
assert report['absent']
'''


class Models(OriginalModels):
    def do_POST(self):
        raw=self.rfile.read(int(self.headers['Content-Length']));self.body=json.loads(raw)
        self.server.chats+=int(self.path=='/api/chat')
        if (self.server.mode=='time' and not self.server.recovered and self.body.get('model')=='qwen3:8b'
                and has_file_write(self.body.get('messages',[]))):
            self.server.triggered=True;self.server.trigger_chat=self.server.chats
            # Keep a partial JSON response alive to exercise the ORIGINAL run
            # deadline rather than httpx's earlier idle-read timeout. No model
            # output/counts are completed; the caller must cancel this stream.
            self.streaming=True;self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
            until=time.monotonic()+110
            while not self.server.release.wait(5) and time.monotonic()<until:
                try:self.wfile.write(b' ');self.wfile.flush()
                except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):return
        original=self.rfile;self.rfile=io.BytesIO(raw)
        try:super().do_POST()
        finally:self.rfile=original
    def send_json(self,value):
        if getattr(self,'streaming',False):
            try:self.wfile.write(json.dumps(value).encode());self.wfile.flush()
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
            return
        if (getattr(self,'body',{}).get('model')=='qwen3:8b' and value.get('done')
                and self.server.mode=='tokens' and not self.server.recovered and not self.server.triggered
                and has_file_write(self.body['messages'])):
            # Scripted known counts leave less than the normal 1024 output
            # reserve. The REAL Ollama adapter must reject the next call locally.
            value={**value,'prompt_eval_count':32000,'eval_count':1}
            self.server.triggered=True;self.server.trigger_chat=self.server.chats
        super().send_json(value)


def tree_hashes(folder):
    return {p.relative_to(folder).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob('*')) if p.is_file()}


async def check(args):
    runtime=ROOT.parent/'multi-agent-system';python=runtime/'.venv/Scripts/python.exe'
    prepared=args.prepared_state.resolve()/'image.json';image=json.loads(prepared.read_bytes())
    assert image['worker_sha256']==hashlib.sha256((runtime/'src/sandbox_worker.py').read_bytes()).hexdigest()
    folder=ROOT/'.local/parallel-budget-browser'/str(time.time_ns());folder.mkdir(parents=True)
    repo=folder/'source';repo.mkdir()
    original={name+'.py':('def '+name+'(x):\n    return '+('x + 1' if name=='alpha' else 'x * 2')+'\n').encode() for name in NAMES}
    original['fixture.bin']=b'\x00\xff\x80binary'
    original['test_contract.py']=('import unittest\n'+''.join('from '+n+' import '+n+'\n' for n in NAMES)
        +'class Checks(unittest.TestCase):\n def test_numeric(self):\n'
        +''.join('  self.assertEqual('+n+'(2),'+('3' if n=='alpha' else '4')+')\n' for n in NAMES)
        +' def test_binary(self):\n  from pathlib import Path\n  self.assertEqual(Path("fixture.bin").read_bytes(),'+repr(original['fixture.bin'])+')\n').encode()
    for name,data in original.items():(repo/name).write_bytes(data)
    git=['git','--no-optional-locks','-c','core.hooksPath='+os.devnull,'-c','commit.gpgsign=false','-c','core.fsmonitor=false','-c','core.autocrlf=false','-C',str(repo)]
    for argv in (['init','-q'],['add','.'],['-c','user.name=Fixture','-c','user.email=fixture@localhost','commit','-qm','Budget fixture']):
        subprocess.run([*git,*argv],check=True,capture_output=True)
    head=subprocess.check_output([*git,'rev-parse','HEAD']).decode().strip();source_before=tree_hashes(repo)
    models=ThreadingHTTPServer(('127.0.0.1',0),Models);models.daemon_threads=True
    models.scope_names=NAMES;models.stall=False;models.stall_after_write=False;models.kill_supervisor=False
    models.early_fail=False;models.failed_once=False;models.ask=False;models.answered=False;models.actions=[];models.review_accept=True
    models.ready=threading.Event();models.release=threading.Event();models.mode=args.budget;models.recovered=False
    models.triggered=False;models.chats=0;models.trigger_chat=None
    model_thread=threading.Thread(target=models.serve_forever,daemon=True);model_thread.start()
    adapter=folder/'trusted_budget_barrier.py'
    adapter.write_text(ADAPTER.replace('__FIXED_JSON__',repr(json.dumps({'runtime':str(runtime),'prepared':str(prepared)}))),encoding='utf8')
    config=folder/'runtime.json';seconds=90 if args.budget=='time' else 180
    config.write_text(json.dumps({'kind':'development-v1','diagnostics':'readiness-v1','recovery':'checkpoint-v1','label':'Isolated shared budget fixture',
        'command':[str(python),str(adapter),'--kaibain',str(ROOT),'--mcp-python',sys.executable,'--ollama',
            f'http://127.0.0.1:{models.server_port}','--parallel-workers','2','--seconds',str(seconds)]}),encoding='utf8')
    store=Store(folder/'tasks.sqlite3');project=store.create_project('Budget fixture',{'repo':str(repo),'commit':None,'files':list(original),
        'writable':[n+'.py' for n in NAMES],'checks':['python','-m','unittest','discover','-s','.','-v'],
        'acceptance':['Support None as zero; preserve numeric behavior, docs and protected binary.']})
    goal=store.create('Recover from shared '+args.budget+' budget',project_id=project['id'])
    owned=CrashFixture(store,runtime,python);owned.start();server=None;thread=None;errors=[]
    report={'ok':False,'real_llm_inference':False,'type':'scripted_http_real_web_mcp_microvm_shared_budget','budget':args.budget,'checks':[]}
    def start_http(port=0):
        nonlocal server,thread
        server=make_server(Store(store.path),port,runtime_config=config)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        return f'http://127.0.0.1:{server.server_port}'
    def stop_http():
        nonlocal server,thread
        if server:server.shutdown();server.server_close();thread.join();server=None;thread=None
    async def terminal(status):
        async with asyncio.timeout(210):
            while True:
                run=store.agent_runs()[0]
                if run['status'] not in ('queued','running','cancelling'):
                    assert run['status']==status,run;return run
                await asyncio.sleep(.2)
    try:
        base=start_http()
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True,channel=os.environ.get('PLAYWRIGHT_CHANNEL','msedge'))
            page=await browser.new_page(viewport={'width':1440,'height':1100});page.on('pageerror',lambda e:errors.append(str(e)))
            await page.goto(base+'/tasks/'+goal['id']);await expect(page.locator('.ag-run-start')).to_be_enabled();await page.locator('.ag-run-start').click()
            await page.goto(base+'/agents?task='+goal['id'])
            await expect(page.locator('[data-link-kind="assignment"]')).to_have_count(5,timeout=30000)
            failed=await terminal('failed');state=folder/'.agent-runs'/failed['id']
            expected='time_budget' if args.budget=='time' else 'token_budget'
            assert failed['failure_code']==expected and failed['failure_kind']=='budget',failed
            assert models.triggered and models.chats==models.trigger_chat
            live=json.loads((state/'budget-live.json').read_bytes());assert len(live['vms'])==2
            parallel=json.loads((state/'parallel/report.json').read_bytes())
            assert parallel['error']['code']==expected and parallel['status']=='failed'
            assert parallel['budget']['active_workers']==[] and parallel['budget']['reserved_cpus']==0
            assert parallel['budget']['reserved_memory_mb']==0 and parallel['budget']['peak_cpus']==2
            assert set(parallel['worker_reports'])=={'alpha','beta'}
            assert all(r.get('error',{}).get('code') in ('cancelled',expected) for r in parallel['worker_reports'].values()),parallel['worker_reports']
            assert json.loads((state/'progress.json').read_bytes())['stage']=='failed'
            if args.budget=='tokens':assert parallel['token_accounting']['complete'] and parallel['token_accounting']['tokens_used']>=32001
            else:assert parallel['budget']['elapsed_seconds']>=seconds
            with store.connection() as db:
                cp=store._checkpoint_for_run(db,failed['id']);agents=[dict(r) for r in db.execute('SELECT * FROM agents WHERE run_id=?',(failed['id'],))]
            assert cp and len(agents)==6 and all(a['disconnected'] for a in agents)
            snapshot=state/'development/checkpoints'/f"{cp['sequence']:03d}";manifest=json.loads((snapshot/'manifest.json').read_bytes())
            saved=manifest['parallel']['tasks'];assert len(saved)==4
            assert {row['status'] for key,row in saved.items() if key in ('gamma','delta')}=={'planned'}
            written=[n for n in NAMES if b'0 if x is None' in (snapshot/'files'/(n+'.py')).read_bytes()];assert written
            assert (snapshot/'files/fixture.bin').read_bytes()==original['fixture.bin']
            old_files=tree_hashes(state/'development');root_before=store.get(goal['id']);child_before=store.get(cp['child_id'])
            assert root_before['version']==cp['task_version'] and child_before['version']==cp['child_version']
            assert root_before['status']==child_before['status']=='in_progress'
            states=await asyncio.to_thread(owned.inspect,failed['id']);assert len(states)==2 and all(not v['running'] and v['missing'] for v in states)
            report.update(failed_run_id=failed['id'],failure_code=expected,live=live,failed_budget=parallel['budget'],token_accounting=parallel['token_accounting'],
                checkpoint_id=cp['id'],saved_tasks=saved,saved_written=written,model_chats_before_failure=models.chats)
            report['checks']+=['two SDK-confirmed live VM and two queued workers','real HTTP adapter hits shared '+expected,
                'no further generation or queued VM admission','typed failure and failed progress, not user cancellation','all original MCP sessions disconnected','sealed complete DAG checkpoint retained']
            models.release.set();port=server.server_port;await page.goto('about:blank');stop_http();base=start_http(port)
            assert len(store.agent_runs())==1 and store.get(goal['id'])==root_before and store.get(cp['child_id'])==child_before
            await page.goto(base+'/tasks/'+goal['id'])
            await expect(page.locator('.ag-recovery')).to_contain_text('Исчерпан лимит')
            await page.get_by_role('button',name='Проверить восстановление',exact=True).click()
            await expect(page.get_by_role('button',name='Продолжить из checkpoint',exact=True)).to_be_enabled()
            models.recovered=True;models.answered=True;actions_before=len(models.actions)
            await page.get_by_role('button',name='Продолжить из checkpoint',exact=True).click()
            async with asyncio.timeout(30):
                while len(store.agent_runs())<2:await asyncio.sleep(.1)
            recovered=await terminal('completed');new=folder/'.agent-runs'/recovered['id']
            assert recovered['recovery_of']==failed['id'] and recovered['recovery_checkpoint_id']==cp['id']
            assert recovered['project']==failed['project'] and recovered['result']['child_id']==cp['child_id']
            assert {t['id'] for t in store.list(parent_id=cp['child_id'])['tasks']}=={r['id'] for r in saved.values()}
            assert tree_hashes(state/'development')==old_files
            for name in written:assert (name,'write_file') not in models.actions[actions_before:]
            combined=json.loads((new/'development/report.json').read_bytes());assert combined['clean_check']['checks']['exit_code']==0
            assert recovered['decision']=='pending' and store.get(goal['id'])['status']=='review'
            assert owned.previous_cleaned_before_launch and owned.prelaunch_vm_states[failed['id']]
            for identifier in owned.owned:
                values=await asyncio.to_thread(owned.inspect,identifier)
                assert all(not v['running'] and v['missing'] for v in values)
                report.setdefault('final_vm_states',{})[identifier]=values
            oracle=folder/'oracle-report.json'
            def run_oracle():
                argv=[str(python),'-c',ORACLE,str(runtime),str(folder/'acceptance-oracle/microvms'),str(prepared),
                    str(repo),head,str(new/'development/result/files'),str(oracle)]
                env={**os.environ,'PYTHONIOENCODING':'utf8'};env.pop('KAIBAIN_CONTROL_TOKEN',None)
                with (folder/'oracle.log').open('wb') as log:
                    scope=ProcessScope(argv,log=log,cwd=str(runtime),env=env)
                    try:code=scope.process.wait(timeout=35)
                    finally:scope.close()
                if code:raise subprocess.CalledProcessError(code,argv)
            await asyncio.to_thread(run_oracle)
            report['oracle']=json.loads(oracle.read_bytes());assert report['oracle']['absent']
            assert tree_hashes(repo)==source_before and subprocess.check_output([*git,'rev-parse','HEAD']).decode().strip()==head
            assert not errors,errors;assert owned.outsider.poll() is None
            await page.goto(base+'/reviews/'+recovered['id']);await expect(page.locator('.review-toolbar')).to_contain_text('Целостность экспорта подтверждена')
            await page.screenshot(path=str(folder/'review.png'),full_page=True);await browser.close()
            report.update(recovered_run_id=recovered['id'],old_vms_absent_before_new_supervisor=owned.prelaunch_vm_states[failed['id']],ok=True)
            report['checks']+=['HTTP restart without automatic resume','explicit recovery preserves all task IDs, profile and sealed bytes',
                'saved writes not repeated','fresh tests and combined clean replay','independent fresh-VM behavioral oracle','old artifacts and source Git untouched','pending human review; no acceptance or Apply','outsider survived']
    finally:
        models.release.set()
        try:
            await owned.close()
            oracle_state=folder/'acceptance-oracle'
            if (oracle_state/'microvms').exists():
                with (oracle_state/'fixture-cleanup.log').open('ab') as log:
                    await asyncio.to_thread(cleanup_owned,[str(python),str(runtime/'run_development_web.py'),
                        '--cleanup','--output',str(oracle_state/'report.json')],oracle_state,log)
        finally:
            stop_http();models.shutdown();models.server_close();model_thread.join()
            (folder/'check-report.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps({'ok':report['ok'],'report':str(folder/'check-report.json')}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--budget',choices=('tokens','time'),default='tokens')
    parser.add_argument('--prepared-state',type=Path,required=True)
    args=parser.parse_args()
    if os.name!='nt':parser.error('Windows-only owned process fixture; no VM started')
    if os.environ.get('KAIBAIN_RUN_ID'):parser.error('Run standalone, preserve inherited run context')
    asyncio.run(check(args))
