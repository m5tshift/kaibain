"""Real web/supervisor/MCP/microVM DAG; scripted model HTTP in isolated data."""
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

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from kaibain.store import Store
from kaibain.web import make_server
from playwright.async_api import async_playwright,expect
from model_fixture_context import model_response,has_file_write


class Models(BaseHTTPRequestHandler):
    def send_json(self,value):
        raw=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers()
        try:self.wfile.write(raw)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
    def do_GET(self):self.send_json({'models':[{'name':name,'digest':hashlib.sha256(name.encode()).hexdigest()} for name in ('qwen3:1.7b','qwen3:8b','gemma3:4b')]})
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path=='/api/show':
            self.send_json({'details':{'family':'gemma3' if body['model'].startswith('gemma') else 'qwen3'}});return
        schema=body['format']
        if body['model']=='gemma3:4b':
            decision=({'decision':'accept','summary':'Scripted review accepts recovered export.','findings':[]} if self.server.review_accept else
                {'decision':'changes_requested','summary':'Scripted rejection for recovery control check.',
                 'findings':[{'path':'alpha.py','line':1,'severity':'minor','message':'Synthetic reviewer finding, not a quality evaluation.','evidence':'def alpha(x):'}]})
        elif body['model']=='qwen3:1.7b':
            if 'tasks' in schema.get('properties',{}):
                decision={'tasks':[{'id':name,'specialty':'backend','request':'Support None in '+name,
                    'writable':[name+'.py'],'dependencies':[]} for name in self.server.scope_names]}
            elif 'ask_user' in json.dumps(schema):decision={'action':'proceed'}
            else:decision={'goal':'Support None','steps':['Read source and tests','Preserve numeric behavior','Verify None handling']}
        else:
            written=has_file_write(body['messages'])
            if self.server.early_fail and written and not self.server.failed_once:
                self.server.failed_once=True;self.send_json({'error':'Scripted model failure after a sealed worker write.'});return
            if self.server.stall and (not self.server.stall_after_write or written):
                self.server.ready.set();self.server.release.wait(90 if self.server.kill_supervisor else 30)
            task=json.loads(body['messages'][1]['content'])['task'];path=task['writable'][0];name=path[:-3]
            expression='x + 1' if name=='alpha' else 'x * 2'
            revision='Revise the previous exported code' in json.dumps(body['messages'])
            doc='Return zero for None; documented behavior after human feedback.' if revision else 'Return zero for None and preserve numeric behavior.'
            source='def '+name+'(x):\n    """'+doc+'"""\n    return 0 if x is None else '+expression+'\n'
            actions=[{'action':'read_file','path':path},{'action':'read_file','path':'test_contract.py'},
                {'action':'write_file','path':path,'content':source},{'action':'run_checks'},
                {'action':'finish','summary':'None handling implemented and fixed tests passed.'}]
            if self.server.ask and not self.server.answered and name=='alpha':
                actions=actions[:3]+[{'action':'ask_user','question':'Нужно ли сохранить числовое поведение обеих функций?'}]
            elif self.server.answered:
                observed=[];retained=[]
                for message in body['messages']:
                    if message['role']!='user':continue
                    try:value=json.loads(message['content'])
                    except (ValueError,TypeError):continue  # Decision-note context may be plain text.
                    if not isinstance(value,dict):continue
                    observed.append(value.get('tool_result',{}));view=value.get('working_context',{})
                    for evidence in view.get('source_evidence',[]):
                        if evidence.get('path')!=path:continue
                        retained.extend(fragment['text'] for fragment in evidence.get('fragments',[]))
                        columns=view.get('source_evidence_columns',[])
                        if 'text' in columns:retained.extend(row[columns.index('text')] for row in evidence.get('rows',[]))
                if (any(result.get('path')==path and '0 if x is None' in result.get('content','') for result in observed)
                        or any('0 if x is None' in text for text in retained)):
                    actions=actions[:2]+actions[3:]
            from model_fixture_context import completed_steps
            decision=actions[completed_steps(body['messages'])]
            self.server.actions.append((name,decision['action']))
        time.sleep(.35)
        self.send_json({'done':True,'model':body['model'],'message':{'content':json.dumps(model_response(body,decision))},'prompt_eval_count':1,'eval_count':1})
    def log_message(self,*args):pass


async def check(question=False,recovery=False,early=None,revision=False,environment_bundle=None,guest_toolchain_bundle=None,guest_toolchain_sha256=None,binary_fixture=False,scopes=2,workers=2,kill_supervisor=False):
    if os.environ.get('KAIBAIN_RUN_ID'):raise RuntimeError('Run standalone without bypassing inherited run scope')
    folder=ROOT/'.local/parallel-browser'/str(time.time_ns());folder.mkdir(parents=True);repo=folder/'source';repo.mkdir()
    names=('alpha','beta','gamma','delta')[:scopes]
    original={name+'.py':('def '+name+'(x):\n    return '+('x + 1' if name=='alpha' else 'x * 2')+'\n').encode() for name in names}
    original['test_contract.py']=('import unittest\n'+''.join('from '+name+' import '+name+'\n' for name in names)
        +'class Checks(unittest.TestCase):\n def test_numeric(self): '
        +'; '.join('self.assertEqual('+name+'(2),'+('3' if name=='alpha' else '4')+')' for name in names)+'\n').encode()
    if binary_fixture:
        original['fixture.bin']=b'\x89PNG\r\n\x1a\n\x00\xff\xfe\x80'
        original['test_contract.py']+=(b' def test_binary_fixture(self):\n  from pathlib import Path\n  self.assertEqual(Path("fixture.bin").read_bytes(),'
            +repr(original['fixture.bin']).encode('ascii')+b')\n')
    if environment_bundle:
        original['test_contract.py']+=b' def test_native(self):\n  import mcp, pydantic_core._pydantic_core, cryptography.hazmat.bindings._rust\n  from pydantic import create_model\n  self.assertEqual(create_model("Native",value=(int,...)).model_validate({"value":"7"}).value,7)\n'
    if guest_toolchain_bundle:
        original['test_contract.py']+=b' def test_guest_git(self):\n  import subprocess\n  self.assertEqual(subprocess.check_output(["git","--version"],text=True).strip(),"git version 2.47.3")\n'
    for name,data in original.items():(repo/name).write_bytes(data)
    git=['git','-c','core.hooksPath='+os.devnull,'-c','core.autocrlf=false','-c','commit.gpgsign=false','-C',str(repo)]
    for argv in (['init','-q'],['add','.'],['-c','user.name=Fixture','-c','user.email=fixture@localhost','commit','-qm','Parallel fixture']):subprocess.run([*git,*argv],check=True,capture_output=True)
    head=subprocess.check_output([*git,'rev-parse','HEAD'],text=True).strip();index=(repo/'.git/index').read_bytes()
    models=ThreadingHTTPServer(('127.0.0.1',0),Models);models.daemon_threads=True
    models.scope_names=names
    models.stall=early=='stop' or kill_supervisor;models.stall_after_write=early=='stop' or kill_supervisor
    models.kill_supervisor=kill_supervisor;models.early_fail=early=='failure';models.failed_once=False
    models.ready=threading.Event();models.release=threading.Event()
    models.ask=question;models.answered=False;models.actions=[]
    models.review_accept=False
    model_thread=threading.Thread(target=models.serve_forever,daemon=True);model_thread.start()
    runtime=ROOT.parent/'multi-agent-system';python=runtime/'.venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    config=folder/'runtime.json';config.write_text(json.dumps({'kind':'development-v1','diagnostics':'readiness-v1','recovery':'checkpoint-v1','label':'Isolated DAG fixture',
        'command':[str(python),str(runtime/'run_development_web.py'),'--kaibain',str(ROOT),'--mcp-python',sys.executable,
            '--ollama',f'http://127.0.0.1:{models.server_port}','--seconds','180','--parallel-workers',str(workers)]}))
    if recovery:
        value=json.loads(config.read_text());value['command']+=['--validator-model','gemma3:4b'];config.write_text(json.dumps(value))
    if environment_bundle or guest_toolchain_bundle:
        value=json.loads(config.read_text())
        if environment_bundle:
            bundle=Path(environment_bundle).resolve();digest=hashlib.sha256((bundle/'manifest.json').read_bytes()).hexdigest()
            value['command']+=['--environment-bundle',str(bundle),'--environment-sha256',digest]
        if guest_toolchain_bundle:
            value['command']+=['--guest-toolchain-bundle',str(Path(guest_toolchain_bundle).resolve()),'--guest-toolchain-sha256',guest_toolchain_sha256]
        config.write_text(json.dumps(value))
    store=Store(folder/'tasks.sqlite3');project=store.create_project('Parallel fixture',{'repo':str(repo),'commit':None,'files':list(original),
        'writable':[name+'.py' for name in names],'checks':['python','-m','unittest','discover','-s','.','-v'],
        'acceptance':['For None all scoped functions return zero. Preserve numeric behavior.']})
    goal=store.create('Support None in alpha and beta',project_id=project['id'])
    crash_fixture=None;crash={}
    if kill_supervisor:
        from parallel_crash_fixture import CrashFixture
        crash_fixture=CrashFixture(store,runtime,python,environment=environment_bundle,
            toolchain=guest_toolchain_bundle,toolchain_sha256=guest_toolchain_sha256)
        crash_fixture.start()
    server=make_server(store,0,runtime_config=config);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}';errors=[]
    async def finished(task_id,status):
        async with asyncio.timeout(180):
            while True:
                rows=[run for run in store.agent_runs() if run['task_id']==task_id]
                if rows and rows[0]['status'] not in ('queued','running','cancelling'):
                    assert rows[0]['status']==status,rows[0];return rows[0]
                await asyncio.sleep(.2)
    def cleaned(run):
        state=folder/'.agent-runs'/run['id'];receipts=[p for p in (state/'microvms').glob('*.json') if len(p.stem)==32]
        assert receipts and all(json.loads(p.read_text())['status']=='stopped' for p in receipts)
        assert json.loads((state/'cleanup.json').read_text())['ok']
        for path in receipts:
            receipt=json.loads(path.read_text())
            if environment_bundle:assert receipt['environment']['manifest_sha256']==digest
            if guest_toolchain_bundle:assert receipt['guest_toolchain']['manifest_sha256']==guest_toolchain_sha256
    try:
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True,**({'channel':'msedge'} if os.name=='nt' else {}))
            page=await browser.new_page(viewport={'width':1440,'height':1100});page.on('pageerror',lambda e:errors.append(str(e)))
            await page.goto(base+'/tasks/'+goal['id']);await expect(page.locator('.ag-run-start')).to_be_enabled();await page.locator('.ag-run-start').click()
            async with asyncio.timeout(60):
                while len(store.list_agents()['agents'])<scopes+2:
                    runs=store.agent_runs();assert not runs or runs[0]['status'] in ('queued','running'),runs
                    await asyncio.sleep(.15)
            await page.goto(base+'/agents?task='+goal['id']);await expect(page.locator('[data-link-kind="assignment"]')).to_have_count(scopes+1+bool(recovery),timeout=20000)
            await expect(page.locator('.ag-budget').first).to_be_visible(timeout=10000)
            await page.screenshot(path=str(folder/'working.png'),full_page=True)
            paused=None;failed=None
            if kill_supervisor:
                from kaibain.agent_runtime import process_alive
                assert await asyncio.to_thread(models.ready.wait,60),'No blocked scripted request after a worker write'
                live=store.agent_runs()[0];assert live['status']=='running'
                state=folder/'.agent-runs'/live['id']
                async with asyncio.timeout(60):
                    while True:
                        vms=await asyncio.to_thread(crash_fixture.inspect,live['id'])
                        running=[v for v in vms if v['running']]
                        with store.connection() as db:sealed=store._checkpoint_for_run(db,live['id'])
                        worker_tasks={}
                        if sealed:
                            snapshot=state/'development/checkpoints'/f"{sealed['sequence']:03d}"
                            metadata=json.loads((snapshot/'manifest.json').read_bytes())['parallel']
                            worker_tasks=metadata['tasks']
                        root_now=store.get(goal['id']);child_now=store.get(sealed['child_id']) if sealed else None
                        active=[]
                        for path in (state/'parallel/workers').glob('*/report.json'):
                            report=json.loads(path.read_bytes())
                            if report.get('worker_sandbox') in {v['id'] for v in running}:active.append(path.parent.name)
                        agents=store.list_agents()['agents']
                        ready=(sealed and len(running)==2 and len(active)==2 and len(worker_tasks)==scopes
                            and sealed['task_version']==root_now['version'] and sealed['child_version']==child_now['version']
                            and all(any(agent['task_id']==worker_tasks[name]['id'] and agent['activity']['phase']=='waiting'
                                for agent in agents) for name in active)
                            and any(b'0 if x is None' in (snapshot/'files'/(name+'.py')).read_bytes() for name in names))
                        if ready:break
                        await asyncio.sleep(.15)
                before_tasks={name:store.get(task['id']) for name,task in worker_tasks.items()}
                saved_tasks={task['id'] for task in worker_tasks.values()}
                saved_written=[name for name in names if b'0 if x is None' in (snapshot/'files'/(name+'.py')).read_bytes()]
                assert all(before_tasks[name]['version']==task['version'] and before_tasks[name]['status']==task['status']
                    for name,task in worker_tasks.items())
                scope=json.loads((state/'scope.json').read_bytes());assert scope['status']=='active' and scope['run_id']==live['id']
                manifest_before=(snapshot/'manifest.json').read_bytes()
                await page.goto('about:blank')
                port=server.server_port;server.shutdown();server.server_close();thread.join()
                termination=await crash_fixture.kill(live['id'])
                async with asyncio.timeout(10):
                    while process_alive(scope['pid']):await asyncio.sleep(.05)
                assert json.loads((state/'scope.json').read_bytes())==scope and not (state/'cleanup.json').exists()
                assert Store(store.path).agent_runs()[0]['status']=='running'
                assert store.get(goal['id'])==root_now and store.get(sealed['child_id'])==child_now
                assert all(store.get(task['id'])==before_tasks[name] for name,task in worker_tasks.items())
                with store.connection() as db:assert store._checkpoint_for_run(db,live['id'])==sealed
                after_kill=await asyncio.to_thread(crash_fixture.inspect,live['id'])
                models.release.set();models.stall=False;models.answered=True
                server=make_server(Store(store.path),port,runtime_config=config)
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                response=await page.request.get(base+'/api/agent-runtime');assert response.status==200
                failed=await finished(goal['id'],'interrupted');assert failed['failure_code']=='supervisor_lost'
                assert len(store.agent_runs())==1 and len(crash_fixture.owned)==1
                assert not (state/'cleanup.json').exists() and crash_fixture.outsider.poll() is None
                checkpoint=sealed;failed_state=state;old_report=None
                assert (snapshot/'manifest.json').read_bytes()==manifest_before
                crash={'termination':termination,'running_vms_before_kill':running,'vms_after_kill':after_kill,
                    'scope_after_kill':scope,'checkpoint_id':sealed['id'],'saved_tasks':metadata['tasks'],
                    'root_child_worker_versions_unchanged':True,'no_automatic_cleanup_or_resume':True}
                await page.goto(base+'/tasks/'+goal['id'])
                await page.get_by_role('button',name='Проверить восстановление',exact=True).click()
                await expect(page.get_by_role('button',name='Продолжить из checkpoint',exact=True)).to_be_enabled(timeout=20000)
                await page.get_by_role('button',name='Продолжить из checkpoint',exact=True).click()
                async with asyncio.timeout(30):
                    while len(store.agent_runs())<2:await asyncio.sleep(.1)
            if question:
                paused=await finished(goal['id'],'waiting_for_input');cleaned(paused)
                dialogue=store.goal_dialogue(goal['id']);asked=dialogue['questions'][0]
                assert asked['checkpoint_id']
                with store.connection() as db:checkpoint=dict(db.execute('SELECT * FROM run_checkpoints WHERE id=?',(asked['checkpoint_id'],)).fetchone())
                snapshot=folder/'.agent-runs'/paused['id']/'development/checkpoints'/f"{checkpoint['sequence']:03d}"
                saved=json.loads((snapshot/'manifest.json').read_text())['parallel'];saved_tasks={row['id'] for row in saved['tasks'].values()}
                assert (snapshot/'files/alpha.py').read_bytes().find(b'0 if x is None')>=0
                assert len(saved_tasks)==scopes and store.get(checkpoint['child_id'])['status']=='in_progress'
                # Restart HTTP with persistent tasks/checkpoint before the human answer.
                port=server.server_port;server.shutdown();server.server_close();thread.join()
                server=make_server(Store(store.path),port,runtime_config=config)
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                models.answered=True
                await page.goto(base+'/tasks/'+goal['id']);await expect(page.get_by_label('Ваш ответ',exact=True)).to_be_visible()
                await page.get_by_label('Ваш ответ',exact=True).fill('Да, числовое поведение обеих функций сохранить.')
                await page.get_by_role('button',name='Ответить и продолжить',exact=True).click()
                async with asyncio.timeout(30):
                    while len(store.agent_runs())<2:await asyncio.sleep(.1)
            if recovery or early:
                if early=='stop':
                    assert await asyncio.to_thread(models.ready.wait,60),'No inference after an exported write'
                    await page.goto(base+'/tasks/'+goal['id'])
                    await page.get_by_role('button',name='Остановить прогон',exact=True).click()
                failed=await finished(goal['id'],'cancelled' if early=='stop' else 'failed');cleaned(failed)
                failed_state=folder/'.agent-runs'/failed['id'];old_report=(failed_state/'report.json').read_bytes()
                with store.connection() as db:checkpoint=dict(db.execute('SELECT * FROM run_checkpoints WHERE run_id=? ORDER BY sequence DESC LIMIT 1',(failed['id'],)).fetchone())
                snapshot=failed_state/'development/checkpoints'/f"{checkpoint['sequence']:03d}"
                saved=json.loads((snapshot/'manifest.json').read_text())['parallel'];saved_tasks={row['id'] for row in saved['tasks'].values()}
                saved_written=[name for name in names if b'0 if x is None' in (snapshot/'files'/(name+'.py')).read_bytes()]
                assert saved_written,'Expected at least one saved worker write'
                if early:assert not (failed_state/'parallel/integration').exists(),'Early failure reached integration'
                models.review_accept=True;models.answered=True;models.stall=False;models.early_fail=False;models.release.set()
                await page.goto(base+'/tasks/'+goal['id'])
                await page.get_by_role('button',name='Проверить восстановление',exact=True).click()
                await expect(page.get_by_role('button',name='Продолжить из checkpoint',exact=True)).to_be_enabled(timeout=20000)
                await page.get_by_role('button',name='Продолжить из checkpoint',exact=True).click()
                async with asyncio.timeout(30):
                    while len(store.agent_runs())<2:await asyncio.sleep(.1)
            run=await finished(goal['id'],'completed');cleaned(run)
            if paused:
                assert run['continuation_of']==paused['id'] and run['result']['child_id']==checkpoint['child_id']
                assert {task['id'] for task in store.list(parent_id=checkpoint['child_id'])['tasks']}==saved_tasks
                assert models.actions.count(('alpha','write_file'))==1,models.actions
            if failed:
                assert run['recovery_of']==failed['id'] and run['recovery_checkpoint_id']==checkpoint['id']
                assert run['result']['child_id']==checkpoint['child_id'] and run['project']==failed['project']
                assert {task['id'] for task in store.list(parent_id=checkpoint['child_id'])['tasks']}==saved_tasks
                assert all(models.actions.count((name,'write_file'))==1 for name in saved_written),models.actions
                if old_report is not None:assert (failed_state/'report.json').read_bytes()==old_report
                if kill_supervisor:
                    cleaned(failed);assert crash_fixture.previous_cleaned_before_launch
                    assert crash_fixture.prelaunch_vm_states[failed['id']]
                    assert (snapshot/'manifest.json').read_bytes()==manifest_before
                    assert crash_fixture.outsider.poll() is None and len(crash_fixture.owned)==2
            state=folder/'.agent-runs'/run['id'];report=json.loads((state/'parallel/report.json').read_text())
            assert report['budget']['peak_cpus']==workers and report['budget']['peak_memory_mb']==512*workers
            assert report['token_accounting']['complete'] and report['token_accounting']['tokens_used']>0
            assert not report['budget']['active_workers'] and len(report['nodes'])==scopes
            assert store.get(goal['id'])['status']=='review' and run['result']['verification']['status']=='passed'
            aggregate=store.get(run['result']['child_id']);assert aggregate['status']=='review' and aggregate['progress']['completed']==scopes
            assert run['result']['task_versions']=={goal['id']:store.get(goal['id'])['version'],aggregate['id']:aggregate['version']}
            combined=json.loads((state/'development/report.json').read_text());assert combined['clean_check']['checks']['exit_code']==0
            manifest=json.loads((state/'development/result/manifest.json').read_text())
            for entry in manifest['files']:
                data=(state/'development/result/files'/entry['path']).read_bytes();assert hashlib.sha256(data).hexdigest()==entry['sha256']
                if entry['path'] in tuple(name+'.py' for name in names):assert b'0 if x is None' in data
            if binary_fixture:
                copies=[*state.glob('parallel/workers/*/checkpoints/*/files/fixture.bin'),
                    *state.glob('development/checkpoints/*/files/fixture.bin'),
                    state/'parallel/integration/result/files/fixture.bin',state/'development/result/files/fixture.bin']
                assert len(copies)>=4 and all(p.read_bytes()==original['fixture.bin'] for p in copies)
                expected=hashlib.sha256(original['fixture.bin']).hexdigest()
                assert next(e['sha256'] for e in manifest['files'] if e['path']=='fixture.bin')==expected
            with store.connection() as db:assert db.execute('SELECT count(*) FROM run_checkpoints WHERE run_id=?',(run['id'],)).fetchone()[0]>0
            await page.goto(base+'/reviews/'+run['id']);await expect(page.locator('.result-review')).to_be_visible()
            await page.screenshot(path=str(folder/'review.png'),full_page=True)
            if revision:
                previous=run;immutable=(state/'development/report.json').read_bytes()
                saved_tasks={task['id'] for task in store.list(parent_id=aggregate['id'])['tasks']}
                await page.get_by_label('Комментарий к результату',exact=True).fill('Document None behavior after human feedback. Keep the saved implementation and numeric API.')
                await page.get_by_role('button',name='Вернуть на доработку',exact=True).click()
                await expect(page.get_by_role('button',name='Запустить доработку',exact=True)).to_be_enabled()
                await page.get_by_role('button',name='Запустить доработку',exact=True).click()
                async with asyncio.timeout(30):
                    while len(store.agent_runs())<2:await asyncio.sleep(.1)
                run=await finished(goal['id'],'completed');cleaned(run)
                assert run['revision_of']==previous['id'] and run['project']==previous['project']
                assert run['result']['child_id']==aggregate['id']
                assert {task['id'] for task in store.list(parent_id=aggregate['id'])['tasks']}==saved_tasks
                revised=folder/'.agent-runs'/run['id']/'development'
                assert json.loads((revised/'report.json').read_bytes())['baseline']['exit_code']==0
                for name in names:
                    assert b'documented behavior after human feedback' in (revised/'result/files'/(name+'.py')).read_bytes()
                assert (state/'development/report.json').read_bytes()==immutable
                await page.goto(base+'/reviews/'+run['id'])
                await expect(page.get_by_role('button',name='Принять результат',exact=True)).to_be_enabled()
                await page.get_by_role('button',name='Принять результат',exact=True).click()
                await expect(page.get_by_role('heading',name='Принято',exact=True)).to_be_visible()
                assert store.get(goal['id'])['status']==store.get(aggregate['id'])['status']=='done'
                await expect(page.get_by_role('button',name='Применить в новую ветку',exact=True)).to_be_enabled()
                await page.get_by_role('button',name='Применить в новую ветку',exact=True).click()
                await expect(page.get_by_role('heading',name='Ветка готова')).to_be_visible(timeout=15000)
                for name in names:
                    published=subprocess.check_output([*git,'show','kaibain/'+run['id']+':'+name+'.py'])
                    assert b'documented behavior after human feedback' in published
            # Stop while real microVMs wait for a model response; only fixture controls.
            models.stall=True;models.stall_after_write=False;models.ready.clear();models.release.clear();models.ask=False
            second=store.create('Cancellation fixture',project_id=project['id'])
            await page.goto(base+'/tasks/'+second['id']);await expect(page.locator('.ag-run-start')).to_be_enabled();await page.locator('.ag-run-start').click()
            assert await asyncio.to_thread(models.ready.wait,60),'Model inference did not start'
            await page.get_by_role('button',name='Остановить прогон',exact=True).click()
            cancelled=await finished(second['id'],'cancelled');cleaned(cancelled);models.release.set()
            assert store.get(second['id'])['status']!='review'
            for name,data in original.items():assert (repo/name).read_bytes()==data
            assert subprocess.check_output([*git,'rev-parse','HEAD'],text=True).strip()==head and (repo/'.git/index').read_bytes()==index
            assert not errors,errors
            await browser.close()
        receipt={'ok':True,'type':'scripted_model_http_real_web_mcp_microvms','checks':['web start','DAG agents and dependencies on graph',
            'shared CPU/RAM and complete token accounting','worker tests and combined clean replay','atomic human review handoff',
            'complete checkpoint publication','stop during inference and owned cleanup','source HEAD/index/worktree untouched'],
            'run_id':run['id'],'database':str(store.path)}
        receipt['real_llm_inference']=False
        receipt.update(scopes=scopes,workers_limit=workers)
        if kill_supervisor:
            receipt['crash']=crash;receipt['old_vms_absent_before_new_supervisor']=crash_fixture.prelaunch_vm_states[failed['id']]
            receipt['checks']+=['verified owned supervisor hard kill with two running VM','unchanged complete DAG checkpoint and task versions',
                'HTTP restart without auto cleanup/resume','explicit recovery reused all worker task IDs','SDK confirms old VMs absent before new supervisor','outsider survived']
        if binary_fixture:receipt['checks']+=['protected non-UTF8 binary preserved through worker checkpoints, aggregate and canonical export','binary read assertion in fixed worker and clean tests']
        if question:receipt['checks']+=['mid-work question after aggregate checkpoint','HTTP restart and human answer','same aggregate and worker tasks','saved bytes restored without repeating write']
        if recovery:receipt['checks']+=['scripted independent reviewer rejection','explicit human checkpoint recovery','same DAG tasks and frozen project','fresh worker tests and combined clean replay','no repeated writes; old report unchanged']
        if early:receipt['checks']+=['early '+early+' before integration','complete checkpoint from worker exports','explicit recovery with same DAG/tasks','saved writes not repeated; old report unchanged']
        if revision:receipt['checks']+=['explicit human return and revision launch','same DAG worker tasks and frozen source','feedback reflected in exported code','baseline uses saved files','old report unchanged','fixture human acceptance and separate Apply','new Git branch with both worker outputs; HEAD/index/worktree untouched']
        if environment_bundle:receipt['checks']+=['native MCP/Pydantic/cryptography imports in fixed worker and clean tests','same pinned wheel manifest in every VM']
        if guest_toolchain_bundle:receipt['checks']+=['actual guest Git in fixed worker and clean tests','same pinned toolchain in every VM']
        (folder/'check-report.json').write_text(json.dumps(receipt,indent=2));print(json.dumps({'ok':True,'report':str(folder/'check-report.json')}))
    finally:
        models.release.set()
        for row in store.agent_runs():
            if not kill_supervisor and row['status'] in ('queued','running'):store.stop_agent_run(row['id'],row['version'])
        try:
            if crash_fixture:await crash_fixture.close()
        finally:
            server.shutdown();server.server_close();thread.join();models.shutdown();models.server_close();model_thread.join()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--question',action='store_true');mode.add_argument('--recovery',action='store_true')
    mode.add_argument('--early',choices=('stop','failure'));mode.add_argument('--revision',action='store_true')
    mode.add_argument('--kill-supervisor',action='store_true',help='Windows: kill the verified owned DAG supervisor after a complete checkpoint')
    parser.add_argument('--environment-bundle');parser.add_argument('--guest-toolchain-bundle');parser.add_argument('--guest-toolchain-sha256')
    parser.add_argument('--binary-fixture',action='store_true',help='Include a protected non-UTF8 fixture in fixed tests and exports; validator OFF')
    parser.add_argument('--scopes',type=int,choices=(2,4),default=2)
    parser.add_argument('--workers',type=int,choices=(1,2),default=2)
    args=parser.parse_args()
    if (args.guest_toolchain_bundle is None)!=(args.guest_toolchain_sha256 is None):parser.error('Guest toolchain requires its pinned hash')
    if args.binary_fixture and args.recovery:parser.error('--recovery enables the text-only validator; --binary-fixture requires validator OFF')
    if args.kill_supervisor and (os.name!='nt' or args.workers!=2):parser.error('Hard-kill requires Windows and two worker slots; no VM started')
    asyncio.run(check(question=args.question,recovery=args.recovery,early=args.early,revision=args.revision,
        environment_bundle=args.environment_bundle,guest_toolchain_bundle=args.guest_toolchain_bundle,guest_toolchain_sha256=args.guest_toolchain_sha256,binary_fixture=args.binary_fixture,
        scopes=args.scopes,workers=args.workers,kill_supervisor=args.kill_supervisor))
