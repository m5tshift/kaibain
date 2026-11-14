"""Real HTTP/browser/Git publication, isolated scripted completed-run fixture."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import threading

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from test_result_review import fixture,git
from kaibain.web import make_server
from kaibain.store import Store
from playwright.async_api import async_playwright,expect


async def check():
    artifacts=ROOT/'test-results';artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='kaibain-review-') as temp:
        store,run,repo,out=fixture(Path(temp));errors=[]
        server=make_server(store,0,runtime_config=Path(temp)/'none.json')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
        try:
            async with async_playwright() as p:
                channel=os.environ.get('PLAYWRIGHT_CHANNEL','msedge' if os.name=='nt' else '')
                browser=await p.chromium.launch(headless=True,**({'channel':channel} if channel else {}))
                page=await browser.new_page(viewport={'width':1440,'height':1100});page.on('pageerror',lambda e:errors.append(str(e)))
                await page.goto(base+'/tasks/'+run['task_id']);await page.locator('.review-open').click()
                await expect(page.locator('.review-toolbar')).to_contain_text('Целостность экспорта подтверждена')
                await expect(page.locator('.review-check .review-pass')).to_have_count(2)
                await page.locator('[data-file="app.py"] summary').click()
                await expect(page.locator('[data-file="app.py"] .review-diff')).to_contain_text('return a + b')
                assert await page.evaluate('window.injected') is None
                # Explicit return requires feedback; draft persists through reload and refresh.
                await page.get_by_role('button',name='Вернуть на доработку',exact=True).click()
                await expect(page.locator('.review-notice')).to_contain_text('опишите')
                await page.get_by_label('Комментарий к результату').fill('Сигнатура сохранена; результат проверен.')
                await page.reload()
                await expect(page.get_by_label('Комментарий к результату')).to_have_value('Сигнатура сохранена; результат проверен.')
                await page.get_by_role('button',name='Обновить проверку').click()
                await expect(page.get_by_label('Комментарий к результату')).to_have_value('Сигнатура сохранена; результат проверен.')
                await page.locator('#theme-toggle').click()
                await expect(page.get_by_role('button',name='Обновить проверку')).to_be_enabled()
                await page.locator('[data-file="app.py"] summary').click()
                await page.screenshot(path=str(artifacts/'result-review-dark.png'),full_page=True)
                await page.set_viewport_size({'width':390,'height':844})
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                await page.screenshot(path=str(artifacts/'result-review-mobile.png'),full_page=True)
                await page.get_by_role('button',name='Принять результат',exact=True).click()
                button=page.get_by_role('button',name='Применить в новую ветку',exact=True)
                await expect(button).to_be_enabled()
                assert not git(repo,'for-each-ref','--format=%(refname)','refs/heads/kaibain')
                # Editing after preview is caught on POST; user content remains intact.
                original=(repo/'app.py').read_bytes();(repo/'app.py').write_bytes(b'user edit')
                await button.click();await expect(page.locator('.review-notice')).to_contain_text('изменился')
                await page.get_by_role('button',name='Обновить проверку').click()
                await expect(page.locator('.review-conflicts')).to_contain_text('локальные правки')
                await expect(button).to_be_disabled();assert (repo/'app.py').read_bytes()==b'user edit'
                (repo/'app.py').write_bytes(original)
                await page.get_by_role('button',name='Обновить проверку').click();await expect(button).to_be_enabled()
                # Browser cannot select executable, actor, destination or branch.
                denied=await page.request.post(base+f'/api/agent-runs/{run["id"]}/apply',data={'expected_version':4,'preview_token':'a'*64,'repo':str(repo)})
                assert denied.status==400
                head=git(repo,'rev-parse','HEAD');index=(repo/'.git'/'index').read_bytes()
                await button.click();await expect(page.get_by_role('heading',name='Ветка готова')).to_be_visible(timeout=15000)
                assert git(repo,'rev-parse','HEAD')==head and (repo/'.git'/'index').read_bytes()==index
                assert (repo/'app.py').read_bytes()==original and b'return a + b' in git(repo,'show','kaibain/'+run['id']+':app.py')
                await page.set_viewport_size({'width':1440,'height':1100})
                await page.screenshot(path=str(artifacts/'result-review-applied.png'),full_page=True)
                # Restart and deep link preserve the receipt. No second publication button.
                port=server.server_port;server.shutdown();server.server_close();thread.join()
                server=make_server(Store(store.path),port,runtime_config=Path(temp)/'none.json')
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                await page.reload();await expect(page.get_by_role('heading',name='Ветка готова')).to_be_visible()
                await expect(button).to_have_count(0)
                await page.goto(base+'/');await expect(page.locator('.result-review')).to_have_count(0)
                assert not errors,errors
                await browser.close()
            print(json.dumps({'ok':True,'checks':['unified review','escaped diff/summary','persistent draft','acceptance separate from apply',
                'stale preview and conflict UI','new branch with real Git','unchanged index/worktree','durable receipt after restart','dark and mobile']}))
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':asyncio.run(check())
