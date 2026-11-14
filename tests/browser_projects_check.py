"""Project library UI with a real Git repo and isolated SQLite; no model execution."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from playwright.async_api import async_playwright, expect

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from kaibain.store import Store
from kaibain.web import make_server


async def check():
    artifacts=ROOT/'test-results';artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='projects-ui-') as temp:
        folder=Path(temp);repo=folder/'source with spaces';repo.mkdir()
        (repo/'app.py').write_text('VALUE = 1\n')
        (repo/'test_app.py').write_text('import unittest\n')
        git=['git','-c','core.hooksPath='+os.devnull,'-c','commit.gpgsign=false','-c','core.fsmonitor=false','-C',str(repo)]
        for args in (['init','-q'],['add','.'],['-c','user.name=Fixture','-c','user.email=fixture@localhost','commit','-qm','Source']):
            subprocess.run([*git,*args],check=True,capture_output=True)
        commit=subprocess.check_output([*git,'rev-parse','HEAD'],text=True).strip()
        adapter=folder/'dormant-adapter.py';adapter.write_text('raise SystemExit("No model execution in this UI fixture")\n')
        config=folder/'runtime.json';config.write_text(json.dumps({'kind':'development-v1','label':'Dormant UI fixture',
            'command':[sys.executable,str(adapter)]}))
        store=Store(folder/'tasks.sqlite3');server=make_server(store,0,runtime_config=config)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}';errors=[]
        try:
            async with async_playwright() as p:
                channel=os.environ.get('PLAYWRIGHT_CHANNEL','msedge' if os.name=='nt' else '')
                browser=await p.chromium.launch(headless=True,**({'channel':channel} if channel else {}))
                page=await browser.new_page(viewport={'width':1440,'height':1050});page.on('pageerror',lambda e:errors.append(str(e)))
                await page.goto(base+'/projects');await expect(page.get_by_role('heading',name='Сохраните первый проект')).to_be_visible()
                await page.get_by_role('button',name='+ Новый проект',exact=True).click()
                await page.get_by_label('Название проекта',exact=True).fill('Локальный сервис')
                await page.get_by_label('Описание проекта',exact=True).fill('Python project <script>window.injected=1</script>')
                await page.get_by_label('Путь репозитория',exact=True).fill(str(repo))
                await page.get_by_label('Импортируемые файлы',exact=True).fill('app.py\ntest_app.py')
                await page.get_by_label('Разрешённые файлы',exact=True).fill('app.py')
                await page.get_by_label('Критерии приёмки',exact=True).fill('Public API preserved.')
                await page.get_by_label('Команда проверки',exact=True).fill('not json')
                await page.get_by_role('button',name='Сохранить проект',exact=True).click()
                await expect(page.locator('#project-form .form-error')).to_contain_text('JSON')
                await page.get_by_label('Команда проверки',exact=True).fill('["python", "-m", "unittest"]')
                await page.get_by_role('button',name='Сохранить проект',exact=True).click()
                await expect(page.locator('.project-card')).to_have_count(1)
                assert await page.evaluate('window.injected') is None
                project=store.projects()['projects'][0];assert project['profile']['commit'] is None
                await page.get_by_role('button',name='Проверить Git',exact=True).click()
                await expect(page.locator('.project-check-result')).to_contain_text(commit[:12])
                await page.get_by_role('button',name='Новая цель ↗',exact=True).click()
                await expect(page.get_by_label('Проект цели',exact=True)).to_have_value(project['id'])
                await page.get_by_label('Название цели',exact=True).fill('Улучшить сервис')
                await page.get_by_role('button',name='Создать цель',exact=True).click()
                await expect(page.locator('.task-title')).to_have_text('Улучшить сервис')
                goal=store.list(roots_only=True)['tasks'][0];assert goal['project_id']==project['id']
                await expect(page.get_by_label('Проект для запуска',exact=True)).to_have_value(project['id'])
                await expect(page.locator('.ag-project')).to_be_hidden()
                await expect(page.locator('.ag-run-start')).to_be_enabled()
                await page.reload();await expect(page.get_by_label('Проект для запуска',exact=True)).to_have_value(project['id'])
                store.update_project(project['id'],1,{'description':'Changed in another tab'})
                await expect(page.locator('.ag-profile-stale')).to_be_visible()
                await expect(page.locator('.ag-run-start')).to_be_disabled()
                await page.get_by_role('button',name='Обновить настройки',exact=True).click()
                await expect(page.locator('.ag-run-start')).to_be_enabled()
                await page.locator('#nav-projects').click()
                await page.get_by_role('button',name='Настроить',exact=True).click()
                await page.get_by_label('Описание проекта',exact=True).fill('My unsaved draft')
                store.update_project(project['id'],2,{'description':'Concurrent edit'})
                await page.get_by_role('button',name='Сохранить проект',exact=True).click()
                await expect(page.locator('#project-form .form-error')).to_contain_text('изменились')
                await expect(page.get_by_label('Описание проекта',exact=True)).to_have_value('My unsaved draft')
                await page.get_by_role('button',name='Отмена',exact=True).click()
                await expect(page.locator('.project-description')).to_have_text('Concurrent edit')
                await page.get_by_role('button',name='В архив',exact=True).click()
                await expect(page.locator('.project-card')).to_have_count(0)
                await page.get_by_role('button',name='Показать архив',exact=True).click()
                await expect(page.locator('.project-card.is-archived')).to_have_count(1)
                await expect(page.get_by_role('button',name='Новая цель ↗',exact=True)).to_be_disabled()
                await page.goto(base+'/tasks/'+goal['id'])
                await expect(page.locator('.ag-run-start')).to_be_disabled()
                await expect(page.locator('.ag-profile-stale')).to_contain_text('в архиве')
                await page.locator('#nav-projects').click()
                await page.get_by_role('button',name='Показать архив',exact=True).click()
                await page.get_by_role('button',name='Восстановить',exact=True).click()
                await expect(page.locator('.project-card.is-archived')).to_have_count(0)
                await page.get_by_role('button',name='Настроить',exact=True).click()
                await page.get_by_label('Название проекта',exact=True).fill('Сервис · готов к работе')
                await page.get_by_role('button',name='Сохранить проект',exact=True).click()
                await expect(page.locator('.project-card h2')).to_have_text('Сервис · готов к работе')
                await page.locator('#theme-toggle').click();await expect(page.locator('html')).to_have_attribute('data-theme','dark')
                await page.screenshot(path=str(artifacts/'projects-dark.png'),full_page=True)
                await page.locator('#theme-toggle').click()
                await page.screenshot(path=str(artifacts/'projects-light.png'),full_page=True)
                await page.set_viewport_size({'width':390,'height':844})
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                await page.screenshot(path=str(artifacts/'projects-mobile.png'),full_page=True)
                await page.get_by_role('button',name='Настроить',exact=True).click()
                assert await page.locator('#modal').evaluate('el=>el.scrollWidth<=el.clientWidth+1')
                await page.get_by_role('button',name='Отмена',exact=True).click()
                latest=store.get_project(project['id'])
                store.update_project(project['id'],latest['version'],{'profile':{**latest['profile'],'repo':str(folder/'missing')}})
                await page.goto(base+'/tasks/'+goal['id']);await expect(page.locator('.ag-run-start')).to_be_enabled()
                await page.locator('.ag-run-start').click()
                await expect(page.locator('.ag-run-error')).to_contain_text('недоступен')
                assert store.agent_runs()==[]
                assert Store(store.path).projects()['projects'][0]['name']=='Сервис · готов к работе'
                assert not errors,errors
                await browser.close()
            print(json.dumps({'ok':True,'checks':['project CRUD','Git HEAD','goal selection','reload persistence',
                'stale launch guard','edit conflict keeps draft','archive and restore','unavailable repo no launch',
                'escaping','dark and light themes','mobile']},ensure_ascii=False))
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__=='__main__':
    asyncio.run(check())
