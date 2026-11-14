"""Real microVM/MCP: save edits, ask, restart HTTP, answer, continue same work."""
import asyncio
import argparse
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
from playwright.async_api import async_playwright,expect
from model_fixture_context import model_response


class Models(BaseHTTPRequestHandler):
    def send_json(self,value):
        raw=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers()
        try:self.wfile.write(raw)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
    def do_GET(self):self.send_json({'models':[{'name':'qwen3:1.7b'},{'name':'qwen3:8b'}]})
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if body['model']=='qwen3:1.7b':
            decision={'action':'proceed'} if 'ask_user' in json.dumps(body['format']) else {'goal':'Correct addition','steps':['Inspect files and original tests','Implement and verify']}
        else:
            from model_fixture_context import completed_steps
            index=completed_steps(body['messages'])
            actions=[{'action':'read_file','path':'calculator.py'},{'action':'read_file','path':'test_calculator.py'}]
            actions+=([{'action':'write_file','path':'calculator.py','content':'def add(a, b):\n    return a + b\n'},
                       {'action':'ask_user','question':'Нужно ли поддержать отрицательные числа?'}] if not self.server.answered else
                      [{'action':'run_checks'},{'action':'finish','summary':'Saved fix preserved; negative numbers and clean replay checked.'}])
            decision=actions[index];self.server.actions.append(decision['action'])
            if self.server.answered:self.server.saw_answer|='Поддержать отрицательные числа' in json.dumps(body['messages'],ensure_ascii=False)
        self.send_json({'done':True,'model':body['model'],'message':{'content':json.dumps(model_response(body,decision))},'prompt_eval_count':1,'eval_count':1})
    def log_message(self,*args):pass


async def check(*,lose_question_report=False):
    folder=ROOT/'.local/work-dialogue-browser'/str(time.time_ns());folder.mkdir(parents=True);repo=folder/'source';repo.mkdir()
    original=b'def add(a, b):\n    return a - b\n';(repo/'calculator.py').write_bytes(original)
    (repo/'test_calculator.py').write_bytes(b'import unittest\nfrom calculator import add\nclass Checks(unittest.TestCase):\n def test_sum(self): self.assertEqual(add(2,3),5); self.assertEqual(add(-2,3),1)\n')
    git=['git','-c','core.hooksPath='+os.devnull,'-c','core.autocrlf=false','-C',str(repo)]
    for args in (['init','-q'],['add','.'],['-c','user.name=Fixture','-c','user.email=fixture@localhost','-c','commit.gpgsign=false','commit','-qm','Fixture']):subprocess.run([*git,*args],check=True,capture_output=True)
    models=ThreadingHTTPServer(('127.0.0.1',0),Models);models.answered=False;models.saw_answer=False;models.actions=[]
    model_thread=threading.Thread(target=models.serve_forever,daemon=True);model_thread.start()
    runtime=ROOT.parent/'multi-agent-system';python=runtime/'.venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    adapter=runtime/'run_development_web.py'
    if lose_question_report:
        # Trusted controller fixture, never project code. The real adapter still
        # owns MCP/VM work and cleanup; only its final question report is lost.
        adapter=folder/'lost_question_report.py'
        adapter.write_text('''import json, runpy, sys
from pathlib import Path
ADAPTER = '''+repr(str(runtime/'run_development_web.py'))+'''
sys.path.insert(0,str(Path(ADAPTER).parent))
sys.argv[0]=ADAPTER
code=0
try:runpy.run_path(ADAPTER,run_name='__main__')
except SystemExit as exc:code=exc.code or 0
if code==0 and '--cleanup' not in sys.argv and '--output' in sys.argv:
    output=Path(sys.argv[sys.argv.index('--output')+1])
    if output.is_file():
        report=json.loads(output.read_bytes())
        if report.get('ok') and report.get('status')=='waiting_for_input':
            output.rename(output.with_name('lost-question-report.json'))
            raise SystemExit(17)
raise SystemExit(code)
''',encoding='utf-8')
    config=folder/'runtime.json';config.write_text(json.dumps({'kind':'development-v1','label':'Question fixture','diagnostics':'readiness-v1','recovery':'checkpoint-v1',
        'command':[str(python),str(adapter),'--kaibain',str(ROOT),'--mcp-python',sys.executable,'--ollama',f'http://127.0.0.1:{models.server_port}']}))
    store=Store(folder/'tasks.sqlite3');project=store.create_project('Fixture',{'repo':str(repo),'commit':None,'files':['calculator.py','test_calculator.py'],
        'writable':['calculator.py'],'checks':['python','-m','unittest','discover','-s','.','-v'],'acceptance':['Correct sums; preserve API.']})
    goal=store.create('Fix addition',project_id=project['id']);server=make_server(store,0,runtime_config=config)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
    async def finished(status):
        async with asyncio.timeout(120):
            while True:
                rows=store.agent_runs()
                if rows and rows[0]['status'] not in ('queued','running','cancelling'):
                    assert rows[0]['status']==status,rows[0];return rows[0]
                await asyncio.sleep(.2)
    def cleaned(run):
        state=folder/'.agent-runs'/run['id'];assert json.loads((state/'cleanup.json').read_text())['ok']
        assert all(json.loads(p.read_text())['status']=='stopped' for p in (state/'microvms').glob('*.json') if len(p.stem)==32)
    errors=[]
    try:
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True,**({'channel':'msedge'} if os.name=='nt' else {}));page=await browser.new_page(viewport={'width':1440,'height':1000})
            page.on('pageerror',lambda e:errors.append(str(e)))
            await page.goto(base+'/tasks/'+goal['id']);await expect(page.locator('.ag-run-start')).to_be_enabled();await page.locator('.ag-run-start').click()
            paused=await finished('waiting_for_input');cleaned(paused)
            if lose_question_report:
                state=folder/'.agent-runs'/paused['id']
                assert not (state/'report.json').exists()
                lost=json.loads((state/'lost-question-report.json').read_bytes())
                assert lost['question_id']==paused['question_id'] and lost['status']=='waiting_for_input'
                assert paused['result'] is None and paused['failure_code'] is None
            dialogue=store.goal_dialogue(goal['id']);question=dialogue['questions'][0]
            assert question['checkpoint_id'] and not question['answer']
            assert question['can_answer']
            with store.connection() as db:checkpoint=dict(db.execute('SELECT * FROM run_checkpoints WHERE id=?',(question['checkpoint_id'],)).fetchone())
            child=store.get(checkpoint['child_id']);assert child['status']=='in_progress'
            snapshot=folder/'.agent-runs'/paused['id']/'development/checkpoints'/f"{checkpoint['sequence']:03d}"
            assert (snapshot/'files/calculator.py').read_bytes()==b'def add(a, b):\n    return a + b\n'
            # Idle HTTP restart must preserve question and file receipt.
            port=server.server_port;server.shutdown();server.server_close();thread.join()
            server=make_server(Store(store.path),port,runtime_config=config);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            await page.reload();await expect(page.get_by_label('Ваш ответ',exact=True)).to_be_visible()
            models.answered=True
            await page.get_by_label('Ваш ответ',exact=True).fill('Поддержать отрицательные числа.')
            await page.get_by_role('button',name='Ответить и продолжить',exact=True).click()
            async with asyncio.timeout(20):
                while len(store.agent_runs())<2:await asyncio.sleep(.2)
            completed=await finished('completed');cleaned(completed)
            assert completed['continuation_of']==paused['id'] and completed['recovery_checkpoint_id']==checkpoint['id']
            assert completed['result']['child_id']==child['id'] and completed['project']==paused['project']
            assert len(store.list(parent_id=goal['id'])['tasks'])==1
            report=json.loads((folder/'.agent-runs'/completed['id']/'development/report.json').read_text())
            assert report['baseline']['exit_code']==0 and report['clean_check']['checks']['exit_code']==0
            assert models.actions.count('write_file')==1 and models.saw_answer and not errors,(models.actions,errors)
            assert (repo/'calculator.py').read_bytes()==original
            await page.goto(base+'/reviews/'+completed['id']);await expect(page.locator('.result-review')).to_be_visible()
            await page.screenshot(path=str(folder/'continued-review.png'),full_page=True);await browser.close()
        receipt={'ok':True,'type':'scripted_models_real_microvm_mcp','checks':['sealed edits before question','owned cleanup','HTTP restart','human answer','same worker task and source snapshot','saved answer visible to model','no repeated write','clean replay','original repo unchanged'],'database':str(store.path)}
        receipt['real_llm_inference']=False
        if lose_question_report:
            receipt['fault']='question_report_lost_after_adapter_exit'
            receipt['checks']+=['missing adapter report with nonzero exit','question parked after confirmed cleanup','no automatic continuation']
        (folder/'check-report.json').write_text(json.dumps(receipt,indent=2));print(json.dumps({'ok':True,'report':str(folder/'check-report.json')}))
    finally:
        server.shutdown();server.server_close();thread.join();models.shutdown();models.server_close();model_thread.join()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lose-question-report',action='store_true',help='Lose the sealed question report after real MCP/VM cleanup; scripted model HTTP only')
    args=parser.parse_args();asyncio.run(check(lose_question_report=args.lose_question_report))
