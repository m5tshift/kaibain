"""Real microVM recovery after scripted model failure following a sealed write."""
import asyncio
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from kaibain.store import Store
from kaibain.web import make_server
from kaibain.agent_runtime import cleanup_owned,process_alive
from playwright.async_api import async_playwright,expect
from model_fixture_context import model_response


class Models(BaseHTTPRequestHandler):
    def send_json(self,value):
        data=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers()
        try:self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
    def do_GET(self):self.send_json({'models':[{'name':'qwen3:1.7b'},{'name':'qwen3:8b'}]})
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));self.server.chats+=1
        if body['model']=='qwen3:1.7b':
            decision={'action':'proceed'} if 'ask_user' in json.dumps(body['format']) else {'goal':'Correct addition','steps':['Inspect checkpoint files','Run original tests']}
        else:
            from model_fixture_context import completed_steps
            index=completed_steps(body['messages'])
            actions=([{'action':'read_file','path':'calculator.py'},{'action':'write_file','path':'calculator.py','content':'def add(a, b):\n    return a + b\n'}]
                if not self.server.resume else [{'action':'read_file','path':'calculator.py'},{'action':'run_checks'},{'action':'finish','summary':'Recovered the saved addition fix; original tests and clean replay passed.'}])
            if index>=len(actions):
                if self.server.kill_supervisor and not self.server.resume:
                    self.server.blocked.set()
                    self.server.release.wait(90)  # Scripted HTTP only; no LLM.
                self.send_json({'done':True,'model':body['model'],'message':{'content':'invalid JSON after checkpoint'},'prompt_eval_count':1,'eval_count':1});return
            decision=actions[index];self.server.actions.append(decision['action'])
        self.send_json({'done':True,'model':body['model'],'message':{'content':json.dumps(model_response(body,decision))},'prompt_eval_count':1,'eval_count':1})
    def log_message(self,*args):pass


async def check(*,kill_supervisor=False):
    folder=ROOT/'.local'/'recovery-browser'/str(time.time_ns());folder.mkdir(parents=True);repo=folder/'source';repo.mkdir()
    original=b'def add(a, b):\n    return a - b\n';(repo/'calculator.py').write_bytes(original)
    (repo/'test_calculator.py').write_text('import unittest\nfrom calculator import add\nclass Checks(unittest.TestCase):\n def test_sum(self): self.assertEqual(add(2,3),5)\n',encoding='utf-8')
    git=['git','-c','core.hooksPath='+os.devnull,'-c','commit.gpgsign=false','-c','core.fsmonitor=false','-c','core.autocrlf=false','-C',str(repo)]
    for args in (['init','-q'],['add','.'],['-c','user.name=Fixture','-c','user.email=fixture@localhost','commit','-qm','Initial']):subprocess.run([*git,*args],check=True,capture_output=True)
    commit=subprocess.check_output([*git,'rev-parse','HEAD'],text=True).strip()
    models=ThreadingHTTPServer(('127.0.0.1',0),Models);models.daemon_threads=True;models.resume=False;models.chats=0;models.actions=[]
    models.kill_supervisor=kill_supervisor;models.blocked=threading.Event();models.release=threading.Event()
    model_thread=threading.Thread(target=models.serve_forever,daemon=True);model_thread.start()
    runtime=ROOT.parent/'multi-agent-system';python=runtime/'.venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    config=folder/'runtime.json';config.write_text(json.dumps({'kind':'development-v1','diagnostics':'readiness-v1','recovery':'checkpoint-v1','label':'Recovery microVM fixture',
        'command':[str(python),str(runtime/'run_development_web.py'),'--kaibain',str(ROOT),'--mcp-python',sys.executable,'--ollama',f'http://127.0.0.1:{models.server_port}']}))
    store=Store(folder/'tasks.sqlite3');project=store.create_project('Recovery fixture',{'repo':str(repo),'commit':None,'files':['calculator.py','test_calculator.py'],
        'writable':['calculator.py'],'checks':['python','-m','unittest','discover','-s','.','-v'],'acceptance':['Correct sums; preserve add(a, b).']})
    goal=store.create('Recover after a model failure',description='Fix addition. Preserve API and tests.',project_id=project['id'])
    owned={};real_popen=subprocess.Popen;outsider=None;crash={}
    def launch(argv,*args,**kwargs):
        ours=(isinstance(argv,list) and 'agent-job' in argv and '--run-id' in argv and '--db' in argv
                and Path(argv[argv.index('--db')+1]).resolve()==store.path)
        if ours and kill_supervisor and owned:
            # Recovery must clean the old attempt BEFORE releasing a new one.
            for identifier in owned:
                state=folder/'.agent-runs'/identifier
                receipt=json.loads((state/'cleanup.json').read_bytes())
                assert receipt['ok'] is True and receipt['records_checked']>=1
                records=[p for p in (state/'microvms').glob('*.json') if len(p.stem)==32]
                assert records and all(json.loads(p.read_bytes())['status']=='stopped' for p in records)
            crash['owned_cleanup_before_new_supervisor']=True
        process=real_popen(argv,*args,**kwargs)
        if ours:
            owned[argv[argv.index('--run-id')+1]]=process
        return process
    # Retain exact handles created by this fixture, never signal a saved PID.
    patcher=patch('kaibain.agent_runtime.subprocess.Popen',new=launch);patcher.start()
    if kill_supervisor:
        outsider=real_popen([sys.executable,'-c','import time;time.sleep(300)'],
            **({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {'start_new_session':True}))
    server=make_server(store,0,runtime_config=config);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
    errors=[]
    async def finished(status):
        async with asyncio.timeout(120):
            while True:
                rows=store.agent_runs()
                if rows and rows[0]['status'] not in ('queued','running','cancelling'):
                    assert rows[0]['status']==status,rows[0];return rows[0]
                await asyncio.sleep(.2)
    def clean(run_id):
        state=folder/'.agent-runs'/run_id
        assert json.loads((state/'cleanup.json').read_text())['ok']
        receipts=[p for p in (state/'microvms').glob('*.json') if len(p.stem)==32]
        assert receipts and all(json.loads(p.read_text())['status']=='stopped' for p in receipts)
    try:
        async with async_playwright() as p:
            channel=os.environ.get('PLAYWRIGHT_CHANNEL','msedge' if os.name=='nt' else '')
            browser=await p.chromium.launch(headless=True,**({'channel':channel} if channel else {}));page=await browser.new_page(viewport={'width':1440,'height':1100});page.on('pageerror',lambda e:errors.append(str(e)))
            await page.goto(base+'/tasks/'+goal['id']);await expect(page.get_by_label('Проект для запуска',exact=True)).to_have_value(project['id'])
            await expect(page.locator('.ag-run-start')).to_be_enabled();await page.locator('.ag-run-start').click()
            if kill_supervisor:
                async with asyncio.timeout(120):
                    while not models.blocked.is_set():await asyncio.sleep(.1)
                live=store.agent_runs()[0];assert live['status']=='running' and live['id'] in owned
                with store.connection() as db:supervisor_pid=store._run(db,live['id'])['pid']
                process=owned[live['id']];assert process.poll() is None
                state=folder/'.agent-runs'/live['id'];scope=json.loads((state/'scope.json').read_bytes())
                assert scope['status']=='active' and scope['run_id']==live['id'] and process_alive(scope['pid'])
                with store.connection() as db:sealed=store._checkpoint_for_run(db,live['id'])
                assert sealed and sealed['sequence']>=2
                root_before=store.get(goal['id']);child_before=store.get(sealed['child_id'])
                # Stop HTTP observation first, so no recovery observer can conceal
                # the durable running state left by an actual supervisor death.
                await page.goto('about:blank')
                port=server.server_port;server.shutdown();server.server_close();thread.join()
                from windows_owned_process import kill_owned_interpreter
                termination=await asyncio.to_thread(kill_owned_interpreter,process,supervisor_pid)
                assert process.returncode is not None
                async with asyncio.timeout(10):
                    while process_alive(scope['pid']):await asyncio.sleep(.05)
                assert json.loads((state/'scope.json').read_bytes())==scope
                assert not (state/'cleanup.json').exists()
                assert Store(store.path).agent_runs()[0]['status']=='running'
                models.release.set()
                server=make_server(Store(store.path),port,runtime_config=config)
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                response=await page.request.get(base+'/api/agent-runtime');assert response.status==200
                failed=await finished('interrupted');assert failed['failure_code']=='supervisor_lost'
                assert store.get(goal['id'])==root_before and store.get(sealed['child_id'])==child_before
                assert len(store.agent_runs())==1 and len(owned)==1 and outsider.poll() is None
                assert not (state/'cleanup.json').exists()  # Restart does not run cleanup/VM automatically.
                crash={'supervisor_termination':termination,'scope_after_kill':scope,
                    'run_after_restart':failed['status'],'failure_code':failed['failure_code'],
                    'no_cleanup_or_resume_on_restart':True,'outsider_survived':True,
                    'checkpoint_id':sealed['id'],'root_and_child_unchanged':True}
            else:
                failed=await finished('failed');assert failed['failure_kind']=='model',failed
            with store.connection() as db:cp=store._checkpoint_for_run(db,failed['id'])
            assert cp and cp['sequence']>=2
            checkpoint_folder=folder/'.agent-runs'/failed['id']/'development'/'checkpoints'/f"{cp['sequence']:03d}"
            assert b'return a + b' in (checkpoint_folder/'files/calculator.py').read_bytes()
            immutable=hashlib.sha256((checkpoint_folder/'manifest.json').read_bytes()).hexdigest()
            if not kill_supervisor:clean(failed['id'])
            # HTTP restart preserves receipt and context. No failed/unknown action is replayed.
            port=server.server_port;server.shutdown();server.server_close();thread.join()
            server=make_server(Store(store.path),port,runtime_config=config);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            await page.goto(base+'/tasks/'+goal['id']);await page.get_by_role('button',name='Проверить восстановление',exact=True).click()
            await expect(page.get_by_role('button',name='Продолжить из checkpoint',exact=True)).to_be_enabled()
            assert len(store.agent_runs())==1
            store.update_project(project['id'],project['version'],{'name':'Edited after failure','archived':True})
            models.resume=True;before_actions=len(models.actions)
            await page.get_by_role('button',name='Продолжить из checkpoint',exact=True).click()
            async with asyncio.timeout(20):
                while len(store.agent_runs())<2:await asyncio.sleep(.1)
            recovered=await finished('completed')
            assert recovered['recovery_of']==failed['id'] and recovered['recovery_checkpoint_id']==cp['id']
            assert recovered['project']==failed['project'] and recovered['project_ref']==failed['project_ref']
            assert recovered['project']['commit']==commit and recovered['result']['child_id']==cp['child_id']
            assert len(store.list(parent_id=goal['id'])['tasks'])==1
            report=json.loads((folder/'.agent-runs'/recovered['id']/'development/report.json').read_text(encoding='utf-8'))
            assert report['baseline']['exit_code']==0 and report['clean_check']['checks']['exit_code']==0
            assert models.actions[before_actions:]==['read_file','run_checks','finish'],models.actions
            assert hashlib.sha256((checkpoint_folder/'manifest.json').read_bytes()).hexdigest()==immutable
            assert (repo/'calculator.py').read_bytes()==original
            assert subprocess.check_output([*git,'rev-parse','HEAD'],text=True).strip()==commit
            clean(recovered['id'])
            if kill_supervisor:
                clean(failed['id']);assert outsider.poll() is None
                assert len(owned)==2 and crash['checkpoint_id']==cp['id']
                assert crash['owned_cleanup_before_new_supervisor'] is True
            await page.goto(base+'/reviews/'+recovered['id']);await expect(page.locator('.review-toolbar')).to_contain_text('Целостность экспорта подтверждена')
            assert not errors,errors;await browser.close()
        result={'ok':True,'models':'scripted HTTP','isolation':'real microVMs','checks':['sealed checkpoint after write','typed model failure','HTTP restart',
            'explicit recovery','original profile after archive','same child','fresh VM baseline uses checkpoint','no tool-history replay','tests and clean replay','immutable old artifacts','owned cleanup','original repo unchanged']}
        result['real_llm_inference']=False
        if kill_supervisor:
            result['crash']=crash
            result['checks'].remove('typed model failure')
            result['checks']+=['real owned supervisor kill','Windows job child exit without Python finally',
                'SQLite running until explicit recovery observation','no automatic cleanup/resume on HTTP restart',
                'old VM cleanup on explicit recovery','outsider survived']
        (folder/'check-report.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'report':str(folder/'check-report.json')}))
    finally:
        # Emergency cleanup is after all success assertions and cannot turn an
        # earlier failure into a pass. It touches only this isolated DB's runs.
        models.release.set()
        try:
            if kill_supervisor:
                from windows_owned_process import kill_owned_interpreter
                for identifier,process in owned.items():
                    if process.poll() is None:
                        with store.connection() as db:pid=store._run(db,identifier)['pid']
                        if pid and process_alive(pid):await asyncio.to_thread(kill_owned_interpreter,process,pid)
                        else:process.kill()
                    await asyncio.to_thread(process.wait,5)
                for run in store.agent_runs():
                    state=folder/'.agent-runs'/run['id']
                    if not (state/'microvms').exists():continue
                    receipt=state/'cleanup.json'
                    if receipt.exists() and json.loads(receipt.read_bytes()).get('ok'):continue
                    with store.connection() as db:command=json.loads(store._run(db,run['id'])['command'])
                    with (state/'fixture-emergency-cleanup.log').open('ab') as log:
                        cleanup_owned([*command,'--cleanup','--output',str(state/'report.json')],state,log)
        finally:
            if outsider is not None:
                if outsider.poll() is None:outsider.kill()
                outsider.wait(timeout=5)
            patcher.stop();server.shutdown();server.server_close();thread.join()
            models.shutdown();models.server_close();model_thread.join()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kill-supervisor',action='store_true',help='Windows: terminate the exact owned supervisor after a sealed write; scripted HTTP, no LLM')
    args=parser.parse_args()
    if args.kill_supervisor and os.name!='nt':parser.error('Hard-kill mode currently verifies Windows Job Objects only; no VM started')
    asyncio.run(check(kill_supervisor=args.kill_supervisor))
