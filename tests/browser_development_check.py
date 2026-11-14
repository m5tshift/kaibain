"""Full web/supervisor/MCP/microVM check; scripted model HTTP, or --real-ollama.

Uses only an isolated fixture database/repository. Requires the sibling runtime's
prepared Microsandbox and Kaibain's Playwright environment.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from kaibain.store import Store
from kaibain.web import make_server
from playwright.async_api import async_playwright,expect
from model_fixture_context import completed_steps,model_response


class ModelFixture(BaseHTTPRequestHandler):
    def send_json(self,value):
        data=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Length',str(len(data)))
        self.end_headers()
        try:self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
    def do_GET(self):self.send_json({'models':[] if self.server.missing else [{'name':name,'digest':hashlib.sha256(name.encode()).hexdigest()} for name in ('qwen3:1.7b','qwen3:8b','gemma3:4b')]})
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path=='/api/show':
            self.send_json({'details':{'family':'gemma3' if body['model'].startswith('gemma') else 'qwen3','parameter_size':'fixture','quantization_level':'fixture'}});return
        if hasattr(self.server,'recorded_options'):
            self.server.recorded_options.append({'model':body.get('model'),
                'num_predict':body.get('options',{}).get('num_predict'),
                'num_ctx':body.get('options',{}).get('num_ctx')})
        if hasattr(self.server,'recorded_protocols'):
            self.server.recorded_protocols.append({'model':body.get('model'),
                'tools':isinstance(body.get('tools'),list),'format':'format' in body})
        self.server.chats+=1
        if self.server.stall and body['model']!='qwen3:1.7b':
            self.server.ready.set();self.server.release.wait(20)
        time.sleep(.6)
        if body['model']=='gemma3:4b':
            decision={'decision':'accept','summary':'Scripted independent review fixture.','findings':[]}
        elif body['model']=='qwen3:1.7b':
            if 'ask_user' in json.dumps(body.get('format',{})):
                decision=({'action':'ask_user','question':'Which function signature should remain unchanged?'}
                    if self.server.ask and 'Saved user clarifications' not in json.dumps(body['messages']) else {'action':'proceed'})
            else:decision={'goal':'Fix addition','steps':['Read source','Fix arithmetic','Run original tests']}
        else:
            revision='Revise the previous exported code' in json.dumps(body['messages'])
            content='def add(a, b):\n'+('    """Return the sum of two numbers."""\n' if revision else '')+'    return a + b\n'
            actions=[{'action':'read_file','path':'calculator.py'},{'action':'write_file','path':'calculator.py','content':content},
                     {'action':'run_checks'},{'action':'finish','summary':'Fixed addition; original tests passed.'}]
            if self.server.batch:
                views=[json.loads(m['content']).get('working_context',{}) for m in body['messages']
                       if m['role']=='user' and m['content'].startswith('{')]
                hashes=next((v['current_writable_hashes'] for v in views if 'current_writable_hashes' in v),{})
                edits=([{'old':'    return a + b\n','new':'    """Return the sum of two numbers."""\n    return a + b\n'}]
                       if revision else [{'old':'return a - b','new':'return a + b'},
                                         {'old':'def add(a, b):','new':'# Arithmetic fixture\ndef add(a, b):'}])
                actions[1]={'action':'replace_text_batch','path':'calculator.py','edits':edits,
                            'expected_sha256':hashes.get('calculator.py','')}
            index=completed_steps(body['messages']);decision=actions[index]
        if 'tools' in body:
            message={'role':'assistant','content':'','tool_calls':[{'function':{
                'name':decision['action'],'arguments':{key:value for key,value in decision.items() if key!='action'}}}]}
        else:message={'content':json.dumps(model_response(body,decision))}
        self.send_json({'done':True,'model':body['model'],'message':message,'prompt_eval_count':1,'eval_count':1})
    def log_message(self,*args):pass


async def check(real=False,reviewer=False,batch=False):
    if real and batch:raise ValueError('--batch selects scripted tool actions; use a separate real-model evaluation')
    folder=ROOT/'.local'/'development-browser'/str(time.time_ns());folder.mkdir(parents=True)
    repo=folder/'source';repo.mkdir()
    original=b'def add(a, b):\n    return a - b\n'
    (repo/'calculator.py').write_bytes(original)
    (repo/'test_calculator.py').write_text('import unittest\nfrom calculator import add\nclass Checks(unittest.TestCase):\n    def test_sum(self):\n        self.assertEqual(add(2, 3), 5)\n        self.assertEqual(add(-2, -3), -5)\n',encoding='utf-8')
    git=['git','-c','core.hooksPath='+os.devnull,'-c','commit.gpgsign=false','-c','core.fsmonitor=false','-c','core.autocrlf=false','-C',str(repo)]
    for args in (['init','-q'],['add','.'],['-c','user.name=Fixture','-c','user.email=fixture@localhost','commit','-qm','Broken addition']):
        subprocess.run([*git,*args],check=True,capture_output=True)
    commit=subprocess.check_output([*git,'rev-parse','HEAD'],text=True).strip()
    runtime=ROOT.parent/'multi-agent-system';python=runtime/'.venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    model_server=ThreadingHTTPServer(('127.0.0.1',0),ModelFixture);model_server.daemon_threads=True
    model_server.stall=False;model_server.missing=False;model_server.ask=True;model_server.chats=0;model_server.ready=threading.Event();model_server.release=threading.Event()
    model_server.batch=batch
    model_thread=threading.Thread(target=model_server.serve_forever,daemon=True);model_thread.start()
    endpoint='http://127.0.0.1:11434' if real else f'http://127.0.0.1:{model_server.server_port}'
    config=folder/'runtime.json'
    config.write_text(json.dumps({'kind':'development-v1','diagnostics':'readiness-v1','label':'Microsandbox integration fixture',
        'command':[str(python),str(runtime/'run_development_web.py'),'--kaibain',str(ROOT),
                   '--mcp-python',sys.executable,'--ollama',endpoint]}),encoding='utf-8')
    if reviewer:
        value=json.loads(config.read_text());value['command'].extend(['--validator-model','gemma3:4b']);config.write_text(json.dumps(value))
    store=Store(folder/'tasks.sqlite3')
    project=store.create_project('Calculator fixture',{'repo':str(repo),'commit':None,
        'files':['calculator.py','test_calculator.py'],'writable':['calculator.py'],
        'checks':['python','-m','unittest','discover','-s','.','-v'],
        'acceptance':['Correct positive and negative sums.']})
    task=store.create('Fix add(a, b)',description='Return the sum. Preserve the public API and existing tests.',project_id=project['id'])
    web=make_server(store,0,runtime_config=config);thread=threading.Thread(target=web.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{web.server_port}'
    errors=[]
    def assert_clean(state):
        receipts=[path for path in state.glob('*.json') if len(path.stem)==32]
        assert receipts,'Expected microVM ownership receipts'
        assert all(json.loads(path.read_text())['status']=='stopped' for path in receipts)
        assert json.loads((state.parent/'cleanup.json').read_text())['ok']
    async def wait_finished(page,status='completed',task_id=None):
        async with asyncio.timeout(120):
            while True:
                runs=[r for r in store.agent_runs() if task_id is None or r['task_id']==task_id]
                if runs and runs[0]['status'] not in ('queued','running','cancelling'):
                    assert runs[0]['status']==status,runs[0];return runs[0]
                await asyncio.sleep(.2)
    async def fill(page):
        await expect(page.get_by_label('Проект для запуска',exact=True)).to_have_value(project['id'])
        await expect(page.locator('.ag-project')).to_be_hidden()
    try:
        async with async_playwright() as p:
            channel=os.environ.get('PLAYWRIGHT_CHANNEL','msedge' if os.name=='nt' else '')
            browser=await p.chromium.launch(headless=True,**({'channel':channel} if channel else {}))
            page=await browser.new_page(viewport={'width':1440,'height':1100});page.on('pageerror',lambda e:errors.append(str(e)))
            await page.goto(base+'/tasks/'+task['id']);await expect(page.locator('.ag-saved-project')).to_be_visible();await fill(page)
            await page.locator('.ag-readiness-check').click()
            await expect(page.locator('.ag-readiness-report .is-ready')).to_be_visible(timeout=22000)
            assert not store.agent_runs() and not store.list_agents()['agents']
            assert model_server.chats==0
            await expect(page.locator('.ag-run-start')).to_be_enabled();await page.locator('.ag-run-start').click()
            if not real:
                paused=await wait_finished(page,'waiting_for_input',task_id=task['id'])
                assert paused['question_id']
                assert not (folder/'.agent-runs'/paused['id']/'microvms').exists()
                assert not store.list(parent_id=task['id'])['tasks']
                await expect(page.locator('.goal-dialogue textarea')).to_be_visible()
                await page.get_by_label('Ваш ответ',exact=True).fill('Keep add(a, b). Correct positive and negative sums.')
                # Even if the profile is edited, continuation keeps the original snapshot.
                store.update_project(project['id'],1,{'name':'Renamed after question'})
                await page.get_by_role('button',name='Ответить и продолжить',exact=True).click()
                async with asyncio.timeout(30):
                    while len(store.agent_runs())<2:await asyncio.sleep(.1)
                await expect(page.locator('.dialogue-answer')).to_contain_text('Keep add(a, b).')
            async with asyncio.timeout(90):
                while not any(a['task_id'] for a in store.list_agents()['agents']):
                    runs=store.agent_runs()
                    assert not runs or runs[0]['status'] in ('queued','running','cancelling'),runs[0]
                    await asyncio.sleep(.1)
            await page.goto(base+'/agents?task='+task['id'])
            await expect(page.locator('[data-link-kind="assignment"]')).to_have_count(2 if reviewer else 1,timeout=20000)
            await page.screenshot(path=str(folder/'working.png'),full_page=True)
            run=await wait_finished(page)
            assert run['project']['commit']==commit and run['project_ref']['id']==project['id']
            if not real:
                assert run['continuation_of']==paused['id'] and run['requirements_version']==1
                assert run['project_ref']['version']==1
                assert 'Keep add(a, b).' in json.loads((folder/'.agent-runs'/run['id']/'development'/'job.json').read_text())['request']
            assert run['result']['verification']['status']=='passed'
            assert len(run['result']['task_versions'])==2
            assert (repo/'calculator.py').read_bytes()==original
            result_path=folder/'.agent-runs'/run['id']/'development'
            detail=json.loads((result_path/'report.json').read_text(encoding='utf-8'))
            assert detail['baseline']['exit_code']==1 and detail['clean_check']['checks']['exit_code']==0
            assert detail['tests']['status']=='passed'
            graph=store.list_agents()
            runtime_agents=[a for a in graph['agents'] if a['run_id']==run['id']]
            assert len(runtime_agents)==(3 if reviewer else 2) and all(a['activity']['phase']=='review' for a in runtime_agents)
            if reviewer:
                assert run['independent_review']['decision']=='accept'
                assert next(a for a in runtime_agents if a['role']=='validator')['model']=='gemma3:4b'
            assert all(a['activity']['budget']['scope']=='run' and a['activity']['budget']['tokens_used']>0 for a in runtime_agents)
            runtime_ids={a['id'] for a in runtime_agents}
            links=[l for l in graph['links'] if l['source_id'] in runtime_ids and l['target_id'] in runtime_ids]
            assert {l['kind'] for l in links}=={'assignment','dependency','handoff'}
            assert next(l for l in links if l['kind']=='dependency')['state']=='fulfilled'
            observed_phases={e['metadata'].get('phase') for e in graph['events'] if e['metadata'].get('run_id')==run['id']}
            assert {'planning','editing','checking','verifying','review'}<=observed_phases,observed_phases
            assert_clean(result_path.parent/'microvms')
            await page.goto(base+'/tasks/'+task['id'])
            await page.locator('.review-open').click()
            await expect(page.locator('.review-toolbar')).to_contain_text('Целостность экспорта подтверждена',timeout=15000)
            await expect(page.locator('.review-check .review-pass')).to_have_count(2)
            if reviewer:await expect(page.locator('.ag-model-assessment')).to_contain_text('gemma3:4b')
            await expect(page.locator('.review-diff')).to_contain_text('return a + b')
            denied=await page.request.get(base+f'/api/agent-runs/{run["id"]}/artifacts/owner');assert denied.status==404
            await page.screenshot(path=str(folder/'review.png'),full_page=True)
            await page.locator('#theme-toggle').click()
            await expect(page.locator('html')).to_have_attribute('data-theme','dark')
            await page.screenshot(path=str(folder/'review-dark.png'),full_page=True)
            immutable=hashlib.sha256((result_path/'report.json').read_bytes()).hexdigest()
            child_id=run['result']['child_id']
            await page.get_by_label('Комментарий к результату',exact=True).fill('Add a docstring explaining the result. Preserve the fixed addition.')
            await page.get_by_role('button',name='Вернуть на доработку',exact=True).click()
            await expect(page.get_by_role('heading',name='На доработке',exact=True)).to_be_visible()
            await expect(page.get_by_role('button',name='Запустить доработку',exact=True)).to_be_enabled()
            before_revision=len(store.agent_runs())
            await page.get_by_role('button',name='Запустить доработку',exact=True).click()
            async with asyncio.timeout(15):
                while len(store.agent_runs())<=before_revision:await asyncio.sleep(.1)
            revised=await wait_finished(page)
            assert revised['result']['child_id']==child_id
            revised_path=folder/'.agent-runs'/revised['id']/'development'
            assert json.loads((revised_path/'report.json').read_text(encoding='utf-8'))['baseline']['exit_code']==0
            assert hashlib.sha256((result_path/'report.json').read_bytes()).hexdigest()==immutable
            await page.goto(base+'/reviews/'+revised['id'])
            await expect(page.get_by_role('button',name='Принять результат',exact=True)).to_be_enabled()
            await page.get_by_role('button',name='Принять результат',exact=True).click()
            await expect(page.get_by_role('heading',name='Принято',exact=True)).to_be_visible()
            assert store.get(task['id'])['status']=='done' and store.get(child_id)['status']=='done'
            assert (repo/'calculator.py').read_bytes()==original
            await expect(page.get_by_role('button',name='Применить в новую ветку',exact=True)).to_be_enabled()
            await page.get_by_role('button',name='Применить в новую ветку',exact=True).click()
            await expect(page.get_by_role('heading',name='Ветка готова')).to_be_visible(timeout=15000)
            branch='kaibain/'+revised['id']
            assert 'Return the sum' in subprocess.check_output([*git,'show',branch+':calculator.py'],text=True)
            assert subprocess.check_output([*git,'rev-parse','HEAD'],text=True).strip()==commit
            assert (repo/'calculator.py').read_bytes()==original
            if not real:
                # Cancel an actual run during a stalled model response with a live VM.
                task2=store.create('Cancellation fixture',description='Fix addition again.')
                model_server.stall=True
                model_server.ask=False
                await page.goto(base+'/tasks/'+task2['id']);await expect(page.locator('.ag-saved-project')).to_be_visible();await fill(page)
                await expect(page.locator('.ag-run-start')).to_be_enabled();await page.locator('.ag-run-start').click()
                async with asyncio.timeout(90):
                    while not model_server.ready.is_set():await asyncio.sleep(.1)
                await expect(page.get_by_role('button',name='Остановить прогон',exact=True)).to_be_enabled()
                await page.get_by_role('button',name='Остановить прогон',exact=True).click()
                stopped=await wait_finished(page,'cancelled',task_id=task2['id']);model_server.release.set()
                state=folder/'.agent-runs'/stopped['id']/'microvms'
                assert_clean(state)
                assert not any(a['connected'] for a in store.list_agents()['agents'])
                # Missing models fail before assigning or changing task data.
                model_server.stall=False;model_server.missing=True
                task3=store.create('Unavailable model fixture')
                await page.goto(base+'/tasks/'+task3['id']);await expect(page.locator('.ag-saved-project')).to_be_visible();await fill(page)
                count=len(store.agent_runs())
                await expect(page.locator('.ag-run-start')).to_be_enabled();await page.locator('.ag-run-start').click()
                await expect(page.locator('.ag-run-error')).to_contain_text('не установлена',timeout=22000)
                await expect(page.locator('.ag-readiness-report .is-blocked')).to_be_visible()
                assert len(store.agent_runs())==count
                assert store.get(task3['id'])['status']=='planned'
                assert store.get(task3['id'])['version']==1
                assert not store.list(parent_id=task3['id'])['tasks']
            await page.set_viewport_size({'width':390,'height':844})
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert not errors,errors
            await page.screenshot(path=str(folder/'mobile.png'),full_page=True)
            await browser.close()
        report={'ok':True,'models':'actual Ollama' if real else 'scripted HTTP fixture','isolation':'real microVMs','batch_edits':batch,
                'checks':['durable question pause without VM','answer and continued context','frozen continuation profile',
                          'read-only readiness','fresh preflight before reservation','saved project and automatic commit','graph assignments','typed dependency and handoff','shared run budget','structured event phases','tests and clean replay','diff','manual review','explicit publication to new branch',
                          'revision overlay','immutable history','original tree unchanged','owned cleanup','missing models','dark theme','mobile'],
                'database':str(store.path)}
        (folder/'check-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'report':str(folder/'check-report.json')},indent=2))
    finally:
        # If an assertion fails, request cancellation only in this isolated fixture.
        for run in store.agent_runs():
            if run['can_stop']:store.stop_agent_run(run['id'],run['version'])
        deadline=time.monotonic()+35
        while any(r['status'] in ('queued','running','cancelling') for r in store.agent_runs()) and time.monotonic()<deadline:
            await asyncio.sleep(.1)
        model_server.release.set();web.shutdown();web.server_close();thread.join()
        model_server.shutdown();model_server.server_close();model_thread.join()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--real-ollama',action='store_true')
    parser.add_argument('--reviewer',action='store_true');parser.add_argument('--batch',action='store_true');args=parser.parse_args()
    asyncio.run(check(args.real_ollama,args.reviewer,args.batch))
