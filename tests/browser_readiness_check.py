"""Readiness UI and launch fencing, using isolated Git/SQLite and a probe fixture."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from playwright.async_api import async_playwright,expect

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from kaibain.store import Store
from kaibain.web import make_server


async def check():
    artifacts=ROOT/'test-results';artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='readiness-ui-') as temp:
        folder=Path(temp);repo=folder/'source';repo.mkdir();(repo/'app.py').write_text('VALUE=1\n')
        git=['git','-c','core.hooksPath='+os.devnull,'-c','commit.gpgsign=false','-C',str(repo)]
        for args in (['init','-q'],['add','.'],['-c','user.name=Fixture','-c','user.email=fixture@localhost','commit','-qm','Source']):
            subprocess.run([*git,*args],check=True,capture_output=True)
        state=folder/'state';state.write_text('offline')
        adapter=folder/'adapter.py';adapter.write_text('''import json,sys
from pathlib import Path
assert '--diagnose' in sys.argv, 'No goal execution in this fixture'
state=(Path(__file__).parent/'state').read_text()
ids=['ollama','coordinator_model','developer_model','sandbox']
if '--diagnostic-input' in sys.argv:ids.append('project_files')
rows=[{'id':key,'label':{'ollama':'Сервер моделей','sandbox':'Sandbox · microVM'}.get(key,key),
       'status':'failed' if key=='ollama' and state=='offline' else 'passed',
       'message':'Сервер недоступен' if state=='offline' and key=='ollama' else 'Компонент доступен',
       'hint':'Откройте Ollama на PC' if state=='offline' and key=='ollama' else '',
       'details':{'model':'model <script>window.injected=1</script>'} if 'model' in key else {}} for key in ids]
print(json.dumps({'protocol':'readiness-v1','checks':rows}))
''',encoding='utf-8')
        config=folder/'runtime.json';config.write_text(json.dumps({'kind':'development-v1','diagnostics':'readiness-v1',
            'label':'Probe fixture','command':[sys.executable,str(adapter)]}))
        store=Store(folder/'tasks.sqlite3')
        project=store.create_project('Рабочий сервис',{'repo':str(repo),'commit':None,'files':['app.py'],
            'writable':['app.py'],'checks':['python','-m','unittest'],'acceptance':['Public API preserved']})
        goal=store.create('Цель для проверки',project_id=project['id'])
        server=make_server(store,0,runtime_config=config);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}';errors=[]
        try:
            async with async_playwright() as p:
                channel=os.environ.get('PLAYWRIGHT_CHANNEL','msedge' if os.name=='nt' else '')
                browser=await p.chromium.launch(headless=True,**({'channel':channel} if channel else {}))
                page=await browser.new_page(viewport={'width':1440,'height':1100});page.on('pageerror',lambda e:errors.append(str(e)))
                await page.goto(base+'/settings');await page.get_by_role('button',name='Проверить окружение',exact=True).click()
                await expect(page.locator('.readiness-result.is-blocked')).to_be_visible(timeout=22000)
                await expect(page.locator('[data-readiness-check="ollama"]')).to_contain_text('Откройте Ollama на PC')
                assert await page.evaluate('window.injected') is None
                state.write_text('good');await page.get_by_role('button',name='Проверить окружение',exact=True).click()
                await expect(page.locator('.readiness-result.is-ready')).to_be_visible(timeout=22000)
                assert store.agent_runs()==[] and store.list_agents()['agents']==[]
                await page.goto(base+'/tasks/'+goal['id']);await expect(page.locator('.ag-run-start')).to_be_enabled()
                await page.get_by_role('button',name='Проверить готовность',exact=True).click()
                await expect(page.locator('.ag-readiness-report .is-ready')).to_be_visible(timeout=22000)
                await expect(page.locator('[data-readiness-check="project_files"]')).to_be_visible()
                # A successful check is not a grant: the environment changes before launch.
                state.write_text('offline');await page.locator('.ag-run-start').click()
                await expect(page.locator('.ag-readiness-report .is-blocked')).to_be_visible(timeout=22000)
                assert store.agent_runs()==[] and store.list_agents()['agents']==[]
                assert store.get(goal['id'])==goal
                denied=await page.request.post(base+'/api/agent-readiness',data={'endpoint':'http://remote-ollama:11434'})
                assert denied.status==400
                denied=await page.request.post(base+'/api/agent-readiness',data={},headers={'Origin':'https://other.example'})
                assert denied.status==403
                await page.locator('#nav-projects').click();await page.get_by_role('button',name='Проверить готовность',exact=True).click()
                await expect(page.locator('.project-diagnostics .is-blocked')).to_be_visible(timeout=22000)
                await page.locator('#theme-toggle').click()
                await page.screenshot(path=str(artifacts/'readiness-dark.png'),full_page=True)
                await page.set_viewport_size({'width':390,'height':844})
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                await page.screenshot(path=str(artifacts/'readiness-mobile.png'),full_page=True)
                await page.locator('#nav-settings').click()
                await expect(page.get_by_role('button',name='Проверить окружение',exact=True)).to_be_visible()
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                assert not errors,errors
                await browser.close()
            print(json.dumps({'ok':True,'checks':['settings readiness','fresh retry','goal readiness','project readiness',
                'failed preflight creates no run','no task or agent mutation','configuration input guard','escaping','dark theme','mobile']}))
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__=='__main__':
    asyncio.run(check())
