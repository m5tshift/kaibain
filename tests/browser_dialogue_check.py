"""Real stdio coordinator -> durable question -> browser answer, on isolated data."""
import asyncio
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
    with tempfile.TemporaryDirectory(prefix='goal-dialogue-') as temp:
        folder=Path(temp);store=Store(folder/'tasks.sqlite3');goal=store.create('Уточнить требования к сервису')
        server=make_server(store,0,runtime_config=folder/'missing-runtime.json')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}';errors=[]
        params=StdioServerParameters(command=sys.executable,args=[str(ROOT/'run.py'),'mcp','--db',str(store.path)])
        try:
            async with stdio_client(params) as (read,write),ClientSession(read,write) as session,async_playwright() as p:
                await session.initialize()
                async def call(tool_name,**args):
                    response=await session.call_tool(tool_name,args);value=response.structuredContent or json.loads(response.content[0].text)
                    assert value['ok'],value
                    return value['data']
                tools={tool.name for tool in (await session.list_tools()).tools}
                assert {'ask_question','get_goal_dialogue'}<=tools and 'answer_question' not in tools
                identity=await call('register_agent',name='Координатор',role='coordinator',model='MCP fixture',framework='Dialogue fixture')
                await call('assign_agent',target_agent_id=identity['id'],task_id=goal['id'],expected_version=1)
                root=await call('set_task_status',task_id=goal['id'],expected_version=1,status='in_progress')
                question=await call('ask_question',task_id=goal['id'],expected_version=root['version'],question='Какую сигнатуру сохранить? <script>window.injected=1</script>')
                channel=os.environ.get('PLAYWRIGHT_CHANNEL','msedge' if os.name=='nt' else '')
                browser=await p.chromium.launch(headless=True,**({'channel':channel} if channel else {}))
                page=await browser.new_page(viewport={'width':1440,'height':1100});page.on('pageerror',lambda e:errors.append(str(e)))
                await page.goto(base)
                await expect(page.get_by_role('heading',name='Агенты ждут ваш ответ')).to_be_visible()
                await page.locator('.dialogue-alert a').click()
                await expect(page.get_by_label('Ваш ответ',exact=True)).to_be_visible()
                assert await page.evaluate('window.injected') is None
                await page.get_by_label('Ваш ответ',exact=True).fill('Сохранить add(a, b).')
                await call('heartbeat_agent',status='waiting',action='Ожидает ответ в обсуждении цели')
                await expect(page.get_by_label('Ваш ответ',exact=True)).to_have_value('Сохранить add(a, b).')
                await page.get_by_role('button',name='Отправить ответ',exact=True).click()
                await expect(page.locator('.dialogue-answer')).to_contain_text('Сохранить add(a, b).')
                dialogue=await call('get_goal_dialogue',task_id=goal['id'])
                assert dialogue['requirements_version']==1 and not dialogue['needs_input']
                root=await call('get_task',task_id=goal['id'])
                await call('ask_question',task_id=goal['id'],expected_version=root['version'],question='Что делать с некорректным аргументом?')
                await expect(page.get_by_label('Ваш ответ',exact=True)).to_be_visible()
                await page.get_by_label('Ваш ответ',exact=True).fill('я'*1100)
                await page.get_by_role('button',name='Отправить ответ',exact=True).click()
                await expect(page.locator('.dialogue-notice')).to_contain_text('2000')
                await expect(page.get_by_label('Ваш ответ',exact=True)).to_have_value('я'*1100)
                await page.get_by_label('Ваш ответ',exact=True).fill('Выбрасывать ValueError с понятным сообщением.')
                await page.reload()
                await expect(page.get_by_label('Ваш ответ',exact=True)).to_have_value('Выбрасывать ValueError с понятным сообщением.')
                # Restart HTTP only; the coordinator keeps its own stdio session.
                port=server.server_port;server.shutdown();server.server_close();thread.join()
                store=Store(store.path);server=make_server(store,port,runtime_config=folder/'missing-runtime.json')
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                await page.reload();await expect(page.get_by_label('Ваш ответ',exact=True)).to_be_visible()
                await expect(page.locator('.dialogue-answer')).to_contain_text('Сохранить add(a, b).')
                await page.locator('#theme-toggle').click()
                await page.screenshot(path=str(artifacts/'dialogue-dark.png'),full_page=True)
                await page.set_viewport_size({'width':390,'height':844})
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                await page.screenshot(path=str(artifacts/'dialogue-mobile.png'),full_page=True)
                await page.get_by_role('button',name='Отправить ответ',exact=True).click()
                await expect(page.locator('.dialogue-answer')).to_have_count(2)
                final=await call('get_goal_dialogue',task_id=goal['id'])
                assert final['requirements_version']==2 and final['questions'][-1]['answer'].startswith('Выбрасывать ValueError')
                assert not store.agent_runs() and not errors,errors
                await call('disconnect_agent');await browser.close()
            print(json.dumps({'ok':True,'checks':['real MCP question','human-only answers','waiting notification',
                'draft survives updates/reload','validation preserves draft','HTTP restart preserves dialogue',
                'fresh answers visible to MCP','requirements versions','escaping','dark theme','mobile']}))
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__=='__main__':asyncio.run(check())
