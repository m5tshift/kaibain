"""Recovery UI and real supervisor with disposable unverified adapter fixture."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import threading

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from test_recovery import recovery_fixture
from kaibain.web import make_server
from kaibain.store import Store
from playwright.async_api import async_playwright,expect


async def check():
    artifacts=ROOT/'test-results';artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='recovery-ui-') as temp:
        folder=Path(temp);store,runtime,run,checkpoint,export=recovery_fixture(folder)
        server=make_server(store,0,runtime_config=runtime.config_path);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
        errors=[]
        try:
            async with async_playwright() as p:
                channel=os.environ.get('PLAYWRIGHT_CHANNEL','msedge' if os.name=='nt' else '')
                browser=await p.chromium.launch(headless=True,**({'channel':channel} if channel else {}));page=await browser.new_page(viewport={'width':1440,'height':1100});page.on('pageerror',lambda e:errors.append(str(e)))
                await page.goto(base+'/tasks/'+run['task_id'])
                await expect(page.locator('.ag-run-start')).to_be_disabled()
                await page.get_by_role('button',name='Проверить восстановление',exact=True).click()
                await expect(page.locator('.ag-checkpoint-title')).to_contain_text('Checkpoint 000')
                button=page.get_by_role('button',name='Продолжить из checkpoint',exact=True);await expect(button).to_be_enabled()
                assert len(store.agent_runs())==1
                original=(export/'files'/'app.py').read_bytes();(export/'files'/'app.py').write_bytes(b'tampered')
                await button.click();await expect(page.locator('.ag-recovery-message')).to_contain_text('Checkpoint повреждён')
                assert len(store.agent_runs())==1
                (export/'files'/'app.py').write_bytes(original)
                await page.get_by_role('button',name='Проверить восстановление',exact=True).click();await expect(button).to_be_enabled()
                denied=await page.request.post(base+f'/api/agent-runs/{run["id"]}/resume',data={'expected_version':run['version'],'expected_task_version':checkpoint['task_version'],'checkpoint_id':checkpoint['id'],'project':{}})
                assert denied.status==400
                await page.locator('#theme-toggle').click();await page.evaluate('scrollTo(0,0)');await page.screenshot(path=str(artifacts/'recovery-dark.png'),full_page=True)
                await page.set_viewport_size({'width':390,'height':844});assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                await page.screenshot(path=str(artifacts/'recovery-mobile.png'),full_page=True)
                await button.click()
                await expect(page.locator('.ag-recovery-lineage')).to_contain_text(run['id'][:8],timeout=15000)
                assert len(store.agent_runs())==2
                latest=store.agent_runs()[0];assert latest['recovery_of']==run['id'] and latest['recovery_checkpoint_id']==checkpoint['id']
                assert len(store.list(parent_id=run['task_id'])['tasks'])==1
                await page.reload();await expect(page.locator('.ag-recovery-lineage')).to_be_visible()
                assert not errors,errors;await browser.close()
            print(json.dumps({'ok':True,'fixture':'unverified adapter; no inference or VMs',
                'checks':['checkpoint preview is read-only','tampering blocks resume','no browser host settings','explicit retry','same child','immutable lineage','reload','dark/mobile']}))
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':asyncio.run(check())
