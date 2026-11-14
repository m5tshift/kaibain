"""Windows hard kill during an unsealed export; real web/MCP/VM, scripted HTTP.

Requires an existing current --prepared-state; never prepares/pulls images.
--crash-point first-export proves refusal without any published checkpoint.
--crash-point after-write-export proves explicit recovery of the last sealed
snapshot, discarding a newer unsealed directory. No real LLM or user data.
--crash-point cleanup kills during final clean-VM cleanup before report handoff.
"""
import argparse
import asyncio
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
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
from kaibain.agent_runtime import cleanup_owned,process_alive
from playwright.async_api import async_playwright,expect
from model_fixture_context import completed_steps,model_response
from parallel_crash_fixture import CrashFixture


# Trusted fault injector only: actual development_web / SandboxManager perform
# all work. The barrier is before the real manifest publication, after export
# has written files/diff. It cannot publish a checkpoint or change task data.
ADAPTER=r'''
import hashlib,json,sys,time
from pathlib import Path
FIXED=json.loads(__FIXED_JSON__)
sys.path.insert(0,str(Path(FIXED['runtime'])/'src'))
from sandbox import SandboxManager
from microvm_backend import IMAGE
from development_web import main
original_save=SandboxManager._save
original_stop=SandboxManager.stop
def prepare(self):
 data=Path(FIXED['prepared']).read_bytes();value=json.loads(data)
 assert value['backend']=='microsandbox' and value['image']==IMAGE
 assert value['worker_sha256']==hashlib.sha256((Path(FIXED['runtime'])/'src/sandbox_worker.py').read_bytes()).hexdigest()
 original_save(self,self.state/'image.json',value)
 return {'ok':True,'prepared':True,'receipt_reused':True}
def save(self,path,value):
 path=Path(path)
 if ('--cleanup' not in sys.argv and '--diagnose' not in sys.argv
     and '--recovery-input' not in sys.argv and path.name=='manifest.json'
     and FIXED['sequence'] is not None and path.parent.name==FIXED['sequence'] and path.parent.parent.name=='checkpoints'):
  assert (path.parent/'changes.diff').is_file() and (path.parent/'files/calculator.py').is_file()
  assert not path.exists()
  output=Path(sys.argv[sys.argv.index('--output')+1])
  original_save(self,output.parent/'export-barrier.json',{'path':str(path.parent),'sequence':FIXED['sequence']})
  time.sleep(90)
  raise RuntimeError('Fixture kill barrier expired; export must not continue')
 return original_save(self,path,value)
def stop(self,identifier):
 if FIXED['sequence'] is None and '--output' in sys.argv and '--cleanup' not in sys.argv and '--recovery-input' not in sys.argv:
  output=Path(sys.argv[sys.argv.index('--output')+1])
  clean=output.parent/'development/clean-check.json'
  if clean.is_file() and self.record(identifier)['attempt_id']=='clean-check':
   assert json.loads(clean.read_bytes())['checks']['exit_code']==0
   original_save(self,output.parent/'export-barrier.json',{'path':str(output.parent/'development/result'),'sequence':None,'phase':'cleanup','sandbox_id':identifier})
   time.sleep(90)
   raise RuntimeError('Fixture cleanup kill barrier expired')
 return original_stop(self,identifier)
SandboxManager.prepare=prepare
SandboxManager._save=save
SandboxManager.stop=stop
main()
'''

ORIGINAL=b'def add(a, b):\n    return a - b\n'
FIXED=b'def add(a, b):\n    return a + b\n'


class Models(BaseHTTPRequestHandler):
    def send_json(self,value):
        data=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers()
        try:self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
    def do_GET(self):self.send_json({'models':[{'name':'qwen3:1.7b'},{'name':'qwen3:8b'}]})
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));self.server.chats+=1
        if body['model']=='qwen3:1.7b':
            decision={'action':'proceed'} if 'ask_user' in json.dumps(body['format']) else {'goal':'Correct addition','steps':['Read source','Fix addition','Run checks']}
        else:
            actions=[{'action':'read_file','path':'calculator.py'},
                {'action':'write_file','path':'calculator.py','content':FIXED.decode()},
                {'action':'run_checks'},{'action':'finish','summary':'Correct addition; original tests and clean replay passed.'}]
            if self.server.cleanup and self.server.recovered:actions.pop(1)
            index=completed_steps(body['messages']);assert index<len(actions),(index,body)
            decision=actions[index];self.server.actions.append(decision['action'])
        self.send_json({'done':True,'model':body['model'],'message':{'content':json.dumps(model_response(body,decision))},'prompt_eval_count':1,'eval_count':1})
    def log_message(self,*args):pass


def hashes(folder):
    return {p.relative_to(folder).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob('*')) if p.is_file()}


async def check(args):
    runtime=ROOT.parent/'multi-agent-system';python=runtime/'.venv/Scripts/python.exe'
    prepared=args.prepared_state.resolve()/'image.json'
    receipt=json.loads(prepared.read_bytes())
    assert receipt.get('backend')=='microsandbox'
    assert receipt.get('worker_sha256')==hashlib.sha256((runtime/'src/sandbox_worker.py').read_bytes()).hexdigest(),'Stale prepared image receipt'
    # Baseline seals 000; the saved coordinator plan seals 001; first write
    # exports 002. Kill before 002's manifest, keeping 001 authoritative.
    cleanup=args.crash_point=='cleanup'
    sequence=None if cleanup else '000' if args.crash_point=='first-export' else '002'
    folder=ROOT/'.local'/'export-crash'/str(time.time_ns());folder.mkdir(parents=True)
    report={'ok':False,'platform':'Windows/x86_64','real_llm_inference':False,'crash_point':args.crash_point,
        'type':'scripted_http_real_web_mcp_microvm_export_crash','checks':[]}
    repo=folder/'source';repo.mkdir();(repo/'calculator.py').write_bytes(ORIGINAL)
    (repo/'test_calculator.py').write_text('import unittest\nfrom calculator import add\nclass Checks(unittest.TestCase):\n def test_sum(self): self.assertEqual(add(2,3),5)\n',encoding='utf8')
    git=['git','--no-optional-locks','-c','core.hooksPath='+os.devnull,'-c','commit.gpgsign=false',
        '-c','core.fsmonitor=false','-c','core.autocrlf=false','-C',str(repo)]
    for argv in (['init','-q'],['add','.'],['-c','user.name=Fixture','-c','user.email=fixture@localhost','commit','-qm','Initial']):
        subprocess.run([*git,*argv],check=True,capture_output=True)
    commit=subprocess.check_output([*git,'rev-parse','HEAD']).strip()
    index_before=(repo/'.git/index').read_bytes();source_before=hashes(repo)
    adapter=folder/'trusted_export_barrier.py'
    adapter.write_text(ADAPTER.replace('__FIXED_JSON__',repr(json.dumps({'runtime':str(runtime),'prepared':str(prepared),'sequence':sequence}))),encoding='utf8')
    models=ThreadingHTTPServer(('127.0.0.1',0),Models);models.daemon_threads=True;models.chats=0;models.actions=[]
    models.cleanup=cleanup;models.recovered=False
    model_thread=threading.Thread(target=models.serve_forever,daemon=True);model_thread.start()
    config=folder/'runtime.json';config.write_text(json.dumps({'kind':'development-v1','diagnostics':'readiness-v1',
        'recovery':'checkpoint-v1','label':'Export crash fixture','command':[str(python),str(adapter),
        '--kaibain',str(ROOT),'--mcp-python',sys.executable,'--ollama',f'http://127.0.0.1:{models.server_port}']}),encoding='utf8')
    store=Store(folder/'tasks.sqlite3')
    project=store.create_project('Export crash fixture',{'repo':str(repo),'commit':None,'files':['calculator.py','test_calculator.py'],
        'writable':['calculator.py'],'checks':['python','-m','unittest','discover','-s','.','-v'],'acceptance':['Correct addition; original tests unchanged.']})
    goal=store.create('Fix addition across export interruption',project_id=project['id'])
    owned=CrashFixture(store,runtime,python,min_records=1);owned.start()
    server=None;thread=None
    def start_http(port=0):
        nonlocal server,thread
        server=make_server(Store(store.path),port,runtime_config=config)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        return f'http://127.0.0.1:{server.server_port}'
    def stop_http():
        nonlocal server,thread
        if server is not None:server.shutdown();server.server_close();thread.join();server=None;thread=None
    async def terminal(status):
        async with asyncio.timeout(120):
            while True:
                run=store.agent_runs()[0]
                if run['status'] not in ('queued','running','cancelling'):
                    assert run['status']==status,run;return run
                await asyncio.sleep(.1)
    try:
        base=start_http();errors=[]
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True,channel=os.environ.get('PLAYWRIGHT_CHANNEL','msedge'))
            page=await browser.new_page(viewport={'width':1440,'height':1100});page.on('pageerror',lambda e:errors.append(str(e)))
            await page.goto(base+'/tasks/'+goal['id']);await expect(page.locator('.ag-run-start')).to_be_enabled();await page.locator('.ag-run-start').click()
            async with asyncio.timeout(120):
                while True:
                    runs=store.agent_runs()
                    if runs:
                        live=runs[0];assert live['status'] in ('queued','running'),live
                        state=folder/'.agent-runs'/live['id'];barrier=state/'export-barrier.json'
                        if barrier.is_file():break
                    await asyncio.sleep(.1)
            assert live['status']=='running' and live['id'] in owned.owned
            report['original_run_id']=live['id'];partial=Path(json.loads(barrier.read_bytes())['path'])
            assert partial==(state/'development/result' if cleanup else state/'development/checkpoints'/sequence)
            assert (partial/'files/calculator.py').read_bytes()==(ORIGINAL if sequence=='000' else FIXED)
            assert (partial/'manifest.json').exists()==cleanup;partial_before=hashes(partial)
            with store.connection() as db:
                checkpoint=store._checkpoint_for_run(db,live['id'])
            assert (checkpoint is None if sequence=='000' else checkpoint and checkpoint['sequence']==(4 if cleanup else 1))
            root_before=store.get(goal['id']);children_before=store.list(parent_id=goal['id'])['tasks'];assert len(children_before)==1
            assert root_before['status']=='in_progress' and children_before[0]['status']=='in_progress'
            assert root_before['goal_result'] is None and live['result'] is None
            vm_before=await asyncio.to_thread(owned.inspect,live['id'])
            assert len(vm_before)==(2 if cleanup else 1) and sum(v['running'] for v in vm_before)==1
            scope=json.loads((state/'scope.json').read_bytes());assert scope['status']=='active' and process_alive(scope['pid'])
            report['running_vms_before_kill']=vm_before
            await page.goto('about:blank');port=server.server_port;stop_http()
            report['termination']=await owned.kill(live['id'])
            async with asyncio.timeout(10):
                while process_alive(scope['pid']):await asyncio.sleep(.05)
            assert Store(store.path).agent_runs()[0]['status']=='running'
            assert json.loads((state/'scope.json').read_bytes())==scope and not (state/'cleanup.json').exists()
            report['vms_after_kill']=await asyncio.to_thread(owned.inspect,live['id'])
            base=start_http(port);response=await page.request.get(base+'/api/agent-runtime');assert response.status==200
            failed=await terminal('interrupted');assert failed['failure_code']=='supervisor_lost'
            assert store.get(goal['id'])==root_before and store.list(parent_id=goal['id'])['tasks']==children_before
            assert hashes(partial)==partial_before and not (state/'cleanup.json').exists()
            assert len(store.agent_runs())==1 and len(owned.owned)==1 and owned.outsider.poll() is None
            report['checks']+=['verified owned supervisor kill during '+('final VM cleanup' if cleanup else 'partial export'),
                'HTTP restart without auto cleanup/resume','root and child versions unchanged',
                'no premature final report handoff' if cleanup else 'partial export never published','outsider survived']
            await page.goto(base+'/tasks/'+goal['id']);await page.get_by_role('button',name='Проверить восстановление',exact=True).click()
            if checkpoint is None:
                await expect(page.locator('.ag-recovery-preview [role="alert"]')).to_contain_text('checkpoint недоступен')
                await expect(page.get_by_role('button',name='Продолжить из checkpoint',exact=True)).to_have_count(0)
                response=await page.request.post(base+f'/api/agent-runs/{failed["id"]}/resume',data={
                    'expected_version':failed['version'],'expected_task_version':root_before['version'],'checkpoint_id':'unsealed'})
                assert response.status==409 and (await response.json())['error']['code']=='CHECKPOINT_UNAVAILABLE'
                assert len(store.agent_runs())==1 and len(owned.owned)==1 and not (state/'cleanup.json').exists()
                # Exercise the actual separate human cleanup action, never a
                # simulated Resume or fixture-only automatic VM teardown.
                await page.get_by_role('button',name='Очистить среду',exact=True).click()
                await expect(page.locator('.ag-cleanup-state')).to_contain_text('Очистка завершена',timeout=45000)
                await expect(page.locator('.ag-cleanup-state')).to_contain_text('Проверено записей владения: 1')
                action=store.run_cleanup_status(failed['id'])['cleanup'];assert action['status']=='completed'
                report['human_cleanup']=action
                assert len(store.agent_runs())==1 and len(owned.owned)==1
                assert store.get(goal['id'])==root_before and store.list(parent_id=goal['id'])['tasks']==children_before
                assert json.loads((state/'cleanup.json').read_bytes())['ok']
                await page.goto('about:blank');port=server.server_port;stop_http();base=start_http(port)
                await page.goto(base+'/tasks/'+goal['id'])
                await expect(page.locator('.ag-cleanup-state')).to_contain_text('Очистка завершена')
                persisted=await (await page.request.get(base+f'/api/agent-runs/{failed["id"]}/cleanup')).json()
                assert persisted['cleanup']==action and persisted['run_version']==failed['version']
                assert len(store.agent_runs())==1 and len(owned.owned)==1
                await page.set_viewport_size({'width':390,'height':844})
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                await page.screenshot(path=str(folder/'cleanup-mobile.png'),full_page=True)
                await page.set_viewport_size({'width':1440,'height':1100})
                report['checks']+=['no checkpoint: UI and direct resume refuse','explicit human UI cleanup without creating/resuming a run']
                report['checks']+=['cleanup result survives HTTP restart','cleanup UI fits narrow screens']
            else:
                await expect(page.get_by_role('button',name='Продолжить из checkpoint',exact=True)).to_be_enabled()
                preview=await (await page.request.get(base+f'/api/agent-runs/{failed["id"]}/recovery')).json()
                assert preview['checkpoint']['id']==checkpoint['id'] and preview['recoverable']
                sealed=state/'development/checkpoints'/f"{checkpoint['sequence']:03d}";sealed_before=hashes(sealed)
                assert (sealed/'files/calculator.py').read_bytes()==(FIXED if cleanup else ORIGINAL)
                actions_before=len(models.actions)
                models.recovered=True
                await page.get_by_role('button',name='Продолжить из checkpoint',exact=True).click()
                async with asyncio.timeout(20):
                    while len(store.agent_runs())<2:await asyncio.sleep(.1)
                recovered=await terminal('completed')
                assert recovered['recovery_of']==failed['id'] and recovered['recovery_checkpoint_id']==checkpoint['id']
                assert recovered['project']==failed['project'] and recovered['result']['child_id']==children_before[0]['id']
                assert len(store.list(parent_id=goal['id'])['tasks'])==1
                result=folder/'.agent-runs'/recovered['id']/'development'
                checks=json.loads((result/'report.json').read_bytes())
                assert checks['baseline']['exit_code']==(0 if cleanup else 1) and checks['clean_check']['checks']['exit_code']==0
                assert (result/'result/files/calculator.py').read_bytes()==FIXED
                assert models.actions[actions_before:]==(['read_file','run_checks','finish'] if cleanup else ['read_file','write_file','run_checks','finish']),models.actions
                assert hashes(sealed)==sealed_before and owned.previous_cleaned_before_launch
                report['old_vms_absent_before_new_supervisor']=owned.prelaunch_vm_states[failed['id']]
                report['recovered_run_id']=recovered['id'];report['checkpoint_id']=checkpoint['id']
                report['checks']+=['explicit recovery uses last published checkpoint',
                    'fixed checkpoint passes fresh baseline; no repeated write' if cleanup else 'fresh baseline still fails before a new fix; newer partial files ignored',
                    'same child and frozen profile','old VM absent before new supervisor',
                    'new tests and clean replay passed','earlier artifacts immutable']
                await page.goto(base+'/reviews/'+recovered['id']);await expect(page.locator('.review-toolbar')).to_contain_text('Целостность экспорта подтверждена')
            assert hashes(partial)==partial_before
            for identifier in owned.owned:
                run_state=folder/'.agent-runs'/identifier
                assert json.loads((run_state/'cleanup.json').read_bytes())['ok']
                values=await asyncio.to_thread(owned.inspect,identifier)
                assert values and all(not v['running'] and v['missing'] for v in values),values
                report.setdefault('final_vm_states',{})[identifier]=values
            assert (repo/'calculator.py').read_bytes()==ORIGINAL and (repo/'.git/index').read_bytes()==index_before
            assert subprocess.check_output([*git,'rev-parse','HEAD']).strip()==commit and hashes(repo)==source_before
            assert not errors,errors;assert owned.outsider.poll() is None
            await page.screenshot(path=str(folder/'result.png'),full_page=True);await browser.close()
            report['checks']+=['SDK confirms all owned VMs stopped and absent','source HEAD/index/worktree untouched','no browser errors']
            report['ok']=True
    finally:
        try:await owned.close()
        finally:
            stop_http();models.shutdown();models.server_close();model_thread.join()
            (folder/'check-report.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps({'ok':report['ok'],'report':str(folder/'check-report.json')}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--crash-point',choices=('first-export','after-write-export','cleanup'),required=True)
    parser.add_argument('--prepared-state',type=Path,required=True,help='Existing current Microsandbox image.json directory; no prepare/pull')
    args=parser.parse_args()
    if os.name!='nt':parser.error('Windows-only owned handle test; no VM started')
    asyncio.run(check(args))
