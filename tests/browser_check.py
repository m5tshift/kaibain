"""Native goals/review UI and two real MCP clients, with an isolated database."""
import asyncio
from contextlib import AsyncExitStack,suppress
from datetime import date
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from playwright.async_api import async_playwright,expect

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from kaibain.store import Store
from kaibain.web import make_server


async def call(session,tool_name,**args):
    response=await session.call_tool(tool_name,args)
    value=response.structuredContent or json.loads(response.content[0].text)
    assert value['ok'],value
    return value['data']


async def check():
    artifacts=ROOT/'test-results';artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='native-ui-') as temp:
        folder=Path(temp);store=Store(folder/'tasks.sqlite3');server=make_server(store,0,runtime_config=folder/'not-configured.json')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
        sessions=[];errors=[]
        try:
            async with AsyncExitStack() as stack,async_playwright() as p:
                channel=os.environ.get('PLAYWRIGHT_CHANNEL','msedge' if os.name=='nt' else '')
                browser=await p.chromium.launch(headless=True,**({'channel':channel} if channel else {}))
                page=await browser.new_page(viewport={'width':1440,'height':1050});page.on('pageerror',lambda e:errors.append(str(e)))
                await page.goto(base);await expect(page.get_by_role('heading',name='Задайте цель. Доверьте работу агентам.')).to_be_visible()
                await expect(page.get_by_role('button',name='Ручной',exact=True)).to_have_count(0)
                await expect(page.get_by_role('button',name='Агентный',exact=True)).to_have_count(0)
                await page.locator('main [data-new-goal]').click()
                await page.get_by_label('Название цели',exact=True).fill('Подготовить локальный проект')
                await page.get_by_label('Контекст и ожидаемый результат',exact=True).fill('Working implementation <script>window.injected=1</script>')
                await page.get_by_label('Дедлайн',exact=True).fill(date.today().isoformat())
                await expect(page.locator('#goal-form select[name="status"]')).to_have_count(0)
                await page.get_by_role('button',name='Создать цель',exact=True).click()
                await expect(page.locator('.task-title')).to_have_text('Подготовить локальный проект')
                goal=store.list(roots_only=True)['tasks'][0]
                await expect(page.locator('.ag-run-start')).to_be_disabled()
                assert await page.evaluate('window.injected') is None
                await page.locator('#nav-calendar').click();await expect(page.locator('.native-calendar-event')).to_have_count(1)
                await page.locator('.native-calendar-event').click();await expect(page.locator('.task-title')).to_have_text(goal['title'])
                params=StdioServerParameters(command=sys.executable,args=[str(ROOT/'run.py'),'mcp','--db',str(store.path)])
                identities=[]
                for name,role in [('Coordinator','coordinator'),('Developer','worker')]:
                    read,write=await stack.enter_async_context(stdio_client(params));session=await stack.enter_async_context(ClientSession(read,write))
                    await session.initialize();sessions.append(session)
                    identities.append(await call(session,'register_agent',name=name,role=role,model='MCP fixture',framework='Native protocol fixture'))
                coordinator,worker=sessions
                await page.locator('#nav-agents').click();await expect(page.locator('.ag-pool-list [data-live-node]')).to_have_count(2)
                await expect(page.get_by_role('button',name='Демонстрация',exact=True)).to_have_count(0)
                child=await call(coordinator,'create_task',title='Реализация',parent_id=goal['id'])
                grand=await call(coordinator,'create_task',title='Проверка',parent_id=child['id'])
                await call(coordinator,'assign_agent',target_agent_id=identities[0]['id'],task_id=goal['id'],expected_version=1)
                await call(coordinator,'assign_agent',target_agent_id=identities[1]['id'],task_id=child['id'],expected_version=1)
                await call(worker,'heartbeat_agent',status='working',action='Читает исходники')
                await expect(page.locator('[data-live-edge]')).to_have_count(1)
                await page.screenshot(path=str(artifacts/'native-graph.png'),full_page=True)
                await page.goto(base+'/tasks/'+goal['id']);await expect(page.locator('.native-tree-row')).to_have_count(2)
                await expect(page.locator('.native-tree input,.native-tree select,[data-status-id],[data-action="delete"]')).to_have_count(0)
                await page.locator('#nav-board').click();await expect(page.locator('.task-card[draggable="true"]')).to_have_count(0)
                await expect(page.locator('.task-card')).to_have_count(3)
                status=await page.request.patch(base+'/api/tasks/'+child['id'],data={'expected_version':1,'changes':{'status':'done'}});assert status.status==403
                blocked=await page.request.post(base+'/api/tasks',data={'title':'Human subtask','parent_id':goal['id']});assert blocked.status==403
                mode=await page.request.patch(base+'/api/workspace',data={'mode':'manual','expected_version':1});assert mode.status==409
                spoof=await page.request.post(base+'/api/goals',data={'title':'Spoof','source':'agent'});assert spoof.status==400
                denied=await page.request.post(base+'/api/goals',data={'title':'Cross site'},headers={'Origin':'https://other.example'});assert denied.status==403
                current=await call(coordinator,'get_task',task_id=goal['id'])
                await call(coordinator,'update_task',task_id=goal['id'],expected_version=current['version'],changes={'status':'in_progress'})
                current=await call(worker,'get_task',task_id=child['id'])
                await call(worker,'set_task_status',task_id=child['id'],expected_version=current['version'],status='done')
                assert 'tag_id' not in store.get(grand['id'])
                current=await call(coordinator,'get_task',task_id=goal['id'])
                report=await call(coordinator,'submit_goal_result',task_id=goal['id'],expected_version=current['version'],summary='Result <script>window.injected=2</script>',checks=['Agent-reported check'])
                await page.goto(base+'/tasks/'+goal['id']);await expect(page.locator('.native-external-result')).to_be_visible()
                await expect(page.locator('.native-external-result script')).to_have_count(0)
                review=page.locator('.native-external-result');await review.locator('textarea').fill('Add error handling')
                await review.get_by_role('button',name='Вернуть на доработку',exact=True).click()
                await expect(review.get_by_role('heading',name='Возвращено с замечаниями')).to_be_visible()
                current=await call(coordinator,'get_task',task_id=goal['id']);assert current['goal_result']['comment']=='Add error handling'
                report=await call(coordinator,'submit_goal_result',task_id=goal['id'],expected_version=current['version'],summary='Error handling ready',checks=['Updated check'])
                current=await call(worker,'get_task',task_id=grand['id'])
                await call(worker,'update_task',task_id=grand['id'],expected_version=current['version'],changes={'description':'Changed after report'})
                current=store.get(goal['id'])
                stale=await page.request.post(base+'/api/goals/'+goal['id']+'/review',data={'expected_version':current['version'],'result_id':report['id'],'expected_result_version':1,'decision':'accepted','comment':''});assert stale.status==409
                report=await call(coordinator,'submit_goal_result',task_id=goal['id'],expected_version=current['version'],summary='Final reviewed deliverable',checks=['Original scope preserved'])
                await expect(review.get_by_role('button',name='Принять результат',exact=True)).to_be_visible(timeout=10000)
                await review.get_by_role('button',name='Принять результат',exact=True).click();await expect(review.get_by_role('heading',name='Результат принят')).to_be_visible()
                assert store.get(goal['id'])['status']=='done'
                await page.reload();await expect(page.locator('.native-external-result h2')).to_have_text('Результат принят')
                await page.locator('main').focus();await page.keyboard.press('Space');await expect(page.locator('#spotlight')).to_be_visible()
                await page.get_by_label('Поиск задач',exact=True).fill('Реализация');await page.keyboard.press('Enter');await expect(page.locator('.task-title')).to_have_text('Реализация')
                await page.locator('#theme-toggle').click();await expect(page.locator('html')).to_have_attribute('data-theme','dark')
                await page.locator('#nav-goals').click();await page.get_by_role('button',name='Все',exact=True).click()
                await expect(page.locator('.native-goals .native-card')).to_have_count(1)
                await page.screenshot(path=str(artifacts/'native-goals-dark.png'),full_page=True)
                for route in ('/','/board','/calendar','/agents','/settings','/tasks/'+goal['id']):
                    await page.set_viewport_size({'width':390,'height':844});await page.goto(base+route);await page.wait_for_selector('.page-heading,.task-layout')
                    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth'),route
                await page.screenshot(path=str(artifacts/'native-mobile.png'),full_page=True)
                assert await page.evaluate('window.injected') is None
                assert not errors,errors
                for session in sessions:await call(session,'disconnect_agent')
                await browser.close()
            print('PASS: native goals, no manual mode/task controls, real MCP tree/presence, calendar, source guards, external report/return/accept, conflicts, persistence, Spotlight, dark theme and mobile.')
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__=='__main__':asyncio.run(check())
