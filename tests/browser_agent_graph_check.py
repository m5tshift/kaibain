"""Real stdio MCP/HTTP/browser graph, with isolated task and agent records."""
import asyncio
from contextlib import AsyncExitStack
from datetime import datetime,timedelta,timezone
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


async def check():
    artifacts=ROOT/'test-results';artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='kaibain-graph-') as temp:
        store=Store(Path(temp)/'tasks.sqlite3');goal=store.create('Сделать локальный калькулятор')
        server=make_server(store,0,runtime_config=Path(temp)/'missing.json')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
        errors=[];budget={'scope':'run','seconds_limit':180,'elapsed_seconds':20,'tokens_limit':32768,'tokens_used':4500,'steps_limit':10,'steps_used':3,
            'workers_limit':2,'workers_active':1,'cpus_limit':2,'cpus_reserved':1,'memory_mb_limit':1024,'memory_mb_reserved':512}
        async def call(session,tool_name,**args):
            reply=await session.call_tool(tool_name,args);value=reply.structuredContent or json.loads(reply.content[0].text)
            assert value['ok'],value;return value['data']
        try:
            async with AsyncExitStack() as stack,async_playwright() as p:
                sessions=[];identities=[]
                params=StdioServerParameters(command=sys.executable,args=[str(ROOT/'run.py'),'mcp','--db',str(store.path)])
                for name,role in [('Координатор','coordinator'),('Разработчик','worker'),('Свободный агент','coordinator')]:
                    read,write=await stack.enter_async_context(stdio_client(params));session=await stack.enter_async_context(ClientSession(read,write));await session.initialize()
                    sessions.append(session);identities.append(await call(session,'register_agent',name=name,role=role,model='MCP fixture',framework='Graph fixture'))
                c,w,spare=sessions;cid,wid,_=[a['id'] for a in identities]
                async def link(kind):
                    agents={a['id']:a for a in (await call(c,'list_agents'))['agents']}
                    source,target=(cid,wid) if kind=='dependency' else (wid,cid)
                    return await call(c if kind=='dependency' else w,'connect_agents',source_id=source,target_id=target,kind=kind,
                        expected_source_version=agents[source]['version'],expected_target_version=agents[target]['version'],message='Ждём тесты' if kind=='dependency' else 'Файлы проверены и переданы')
                channel=os.environ.get('PLAYWRIGHT_CHANNEL','msedge' if os.name=='nt' else '')
                browser=await p.chromium.launch(headless=True,**({'channel':channel} if channel else {}));page=await browser.new_page(viewport={'width':1600,'height':1100});page.on('pageerror',lambda error:errors.append(str(error)))
                await page.goto(base+'/agents');await expect(page.locator('.ag-pool-list [data-live-node]')).to_have_count(3)
                child=await call(c,'create_task',title='Исправить сложение и проверить',parent_id=goal['id'])
                await call(c,'assign_agent',target_agent_id=cid,task_id=goal['id'],expected_version=1)
                await call(c,'assign_agent',target_agent_id=wid,task_id=child['id'],expected_version=1)
                root=await call(c,'get_task',task_id=goal['id']);await call(c,'set_task_status',task_id=goal['id'],expected_version=root['version'],status='in_progress')
                await call(c,'heartbeat_agent',status='working',phase='planning',budget=budget,action='Разбивает цель на шаги')
                await call(w,'heartbeat_agent',status='working',phase='editing',budget=budget,action='Изменяет calculator.py <script>window.injected=1</script>')
                worker=page.locator(f'[data-live-node="{wid}"]');await expect(worker).to_have_attribute('data-state','editing')
                await worker.click();await expect(page.locator('.ag-details h2')).to_have_text('Разработчик')
                await expect(page.locator('.ag-budget')).to_contain_text('28 268'.replace(' ','\u00a0'))
                await expect(page.locator('.ag-budget')).to_contain_text('512 / 1024 MiB свободно')
                await expect(page.get_by_label('Расход бюджета: CPU',exact=True)).to_have_attribute('value','0.5')
                await expect(page.locator('.ag-details')).to_contain_text('Изменяет calculator.py')
                assert await page.evaluate('window.injected') is None
                await call(c,'heartbeat_agent',status='waiting',phase='waiting',action='Ожидает проверенный результат')
                dependency=await link('dependency');edge=page.locator(f'[data-live-edge="{dependency["id"]}"]')
                await expect(edge).to_be_visible();await edge.focus();await page.keyboard.press('Enter')
                await expect(page.locator('.ag-details h2')).to_have_text('Ожидание')
                await expect(page.locator('.ag-details')).to_contain_text('Ждём тесты')
                await call(w,'heartbeat_agent',status='working',phase='checking',budget={**budget,'elapsed_seconds':31,'steps_used':5},action='Проверяет изменения в microVM')
                await expect(worker).to_have_attribute('data-state','checking')
                await worker.click();await expect(page.locator('.ag-budget h3')).to_have_text('Осталось · весь прогон')
                # Keep the fixture presence fresh while exercising UI, like real clients.
                pulse_task=None
                async def pulse():
                    while True:
                        await asyncio.sleep(5)
                        for session in sessions:await call(session,'heartbeat_agent')
                pulse_task=asyncio.create_task(pulse())
                try:
                    await page.locator('#theme-toggle').click();await page.evaluate('scrollTo(0,0)')
                    await page.screenshot(path=str(artifacts/'agent-graph-dark.png'),full_page=True)
                    await page.set_viewport_size({'width':390,'height':844});assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    await page.screenshot(path=str(artifacts/'agent-graph-mobile.png'),full_page=True)
                    handoff=await link('handoff');await expect(edge).to_have_attribute('data-link-state','fulfilled')
                    delivered=page.locator(f'[data-live-edge="{handoff["id"]}"]');await delivered.focus();await page.keyboard.press('Enter')
                    await expect(page.locator('.ag-details h2')).to_have_text('Передача результата')
                    await expect(page.locator('.ag-details')).to_contain_text('Файлы проверены и переданы')
                    other=store.create('Другая цель')
                    spare_id=identities[2]['id']
                    await call(spare,'assign_agent',target_agent_id=spare_id,task_id=other['id'],expected_version=1)
                    await expect(page.locator('.ag-world [data-live-node]')).to_have_count(3)
                    await page.get_by_label('Цель на графе').select_option(other['id'])
                    await expect(page.locator('.ag-world [data-live-node]')).to_have_count(1)
                    await page.locator(f'[data-live-node="{spare_id}"]').click()
                    await expect(page.locator('.ag-details')).to_contain_text('Бюджет не сообщён клиентом')
                    await page.get_by_label('Цель на графе').select_option(goal['id'])
                    await expect(page.locator('.ag-world [data-live-node]')).to_have_count(2)
                    root=await call(c,'get_task',task_id=goal['id'])
                    await call(c,'ask_question',task_id=goal['id'],expected_version=root['version'],question='Какую сигнатуру сохранить?')
                    await expect(worker).to_have_attribute('data-state','waiting_for_input');await worker.click()
                    await expect(page.locator('.ag-details')).to_contain_text('Какую сигнатуру сохранить?')
                    await page.goto(base+'/tasks/'+goal['id']);await page.get_by_label('Ваш ответ',exact=True).fill('Сохранить add(a, b).')
                    await page.get_by_role('button',name='Отправить ответ',exact=True).click();await expect(page.locator('.dialogue-answer')).to_contain_text('add(a, b)')
                    await page.goto(base+'/agents');await expect(worker).to_have_attribute('data-state','checking')
                    await worker.click();await expect(page.locator('.ag-budget')).to_be_visible()
                    # Test timeout deterministically, then restore a fresh real MCP pulse.
                    pulse_task.cancel()
                    try:await pulse_task
                    except asyncio.CancelledError:pass
                    old=(datetime.now(timezone.utc)-timedelta(seconds=31)).isoformat(timespec='microseconds')
                    with store.connection(write=True,control=True) as db:db.execute('UPDATE agents SET last_seen=? WHERE id=?',(old,wid))
                    await expect(worker).to_have_attribute('data-state','offline',timeout=2500)
                    await call(w,'heartbeat_agent');await expect(worker).to_have_attribute('data-state','checking')
                    port=server.server_port;server.shutdown();server.server_close();thread.join()
                    server=make_server(Store(store.path),port,runtime_config=Path(temp)/'missing.json');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                    await page.reload();await expect(page.locator('[data-link-kind="handoff"]')).to_have_count(1)
                    await expect(worker).to_have_attribute('data-state','checking');await worker.click();await expect(page.locator('.ag-budget')).to_be_visible()
                    assert not errors,errors
                finally:
                    pulse_task.cancel()
                    try:await pulse_task
                    except asyncio.CancelledError:pass
                for session in sessions:await call(session,'disconnect_agent')
                await browser.close()
            print(json.dumps({'ok':True,'checks':['real MCP phases and budget','assignment/dependency/handoff','keyboard edge inspection',
                'question overrides activity','answer clears wait','heartbeat expiry','persistent graph after HTTP restart','escaped text','dark theme','mobile']}))
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':asyncio.run(check())
