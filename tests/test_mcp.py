"""Actual stdio MCP: goal -> nested agent work -> report -> human decision."""
import json
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from kaibain.store import Store
from kaibain.web import make_server


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_goal_agent_tree_external_review_and_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'tasks.sqlite3';store=Store(path);server=make_server(store,0)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{server.server_port}'
            def http(route,method='GET',body=None):
                req=urllib.request.Request(base+route,method=method,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
                try:
                    with urllib.request.urlopen(req) as response:return response.status,json.load(response)
                except urllib.error.HTTPError as error:
                    with error:return error.code,json.load(error)
            params=StdioServerParameters(command=sys.executable,args=[str(ROOT/'run.py'),'mcp','--db',str(path)])
            try:
                project=http('/api/projects','POST',{'name':'Release project','profile':{'repo':str(Path(temp).resolve()),
                    'commit':None,'files':['app.py'],'writable':['app.py'], 'checks':['python','-m','unittest'],
                    'acceptance':['API preserved']}})[1]['project']
                goal=http('/api/goals','POST',{'title':'Release','deadline':'2026-10-20','project_id':project['id']})[1]['goal']
                async with stdio_client(params) as (read,write),ClientSession(read,write) as session:
                    await session.initialize()
                    names={t.name for t in (await session.list_tools()).tools}
                    self.assertIn('submit_goal_result',names);self.assertNotIn('review_goal',names);self.assertNotIn('set_mode',names)
                    self.assertTrue({'list_projects','get_project'}<=names)
                    self.assertTrue({'list_tags','create_tag'}.isdisjoint(names))
                    self.assertNotIn('create_project',names);self.assertNotIn('update_project',names)
                    async def call(tool_name,**args):
                        response=await session.call_tool(tool_name,args)
                        self.assertFalse(response.isError,response)
                        return response.structuredContent or json.loads(response.content[0].text)
                    self.assertEqual((await call('get_workspace'))['data']['workflow'],'agents_native')
                    self.assertEqual((await call('list_projects'))['data']['projects'],[project])
                    self.assertEqual((await call('get_project',project_id=goal['project_id']))['data'],project)
                    store.update_project(project['id'],1,{'description':'Updated by user'})
                    self.assertEqual((await call('get_project',project_id=project['id']))['data']['description'],'Updated by user')
                    child=(await call('create_task',title='Implementation',parent_id=goal['id']))['data']
                    grand=(await call('create_task',title='Checks',parent_id=child['id']))['data']
                    self.assertNotIn('tag',grand);self.assertNotIn('tag_id',grand)
                    self.assertEqual([n['depth'] for n in (await call('get_task_tree',task_id=goal['id']))['data']['nodes']],[0,1,2])
                    cycle=await call('update_task',task_id=goal['id'],expected_version=store.get(goal['id'])['version'],changes={'parent_id':grand['id']})
                    self.assertEqual(cycle['error']['code'],'CYCLE')
                    current=(await call('get_task',task_id=child['id']))['data']
                    await call('set_task_status',task_id=child['id'],expected_version=current['version'],status='done')
                    self.assertEqual(http('/api/tasks/'+child['id'])[1]['task']['status'],'done')
                    self.assertEqual(http('/api/tasks/'+child['id'],'PATCH',{'expected_version':store.get(child['id'])['version'],'changes':{'status':'planned'}})[0],403)
                    fresh=(await call('get_task',task_id=goal['id']))['data']
                    denied=await call('set_task_status',task_id=goal['id'],expected_version=fresh['version'],status='done')
                    self.assertEqual(denied['error']['code'],'GOAL_REVIEW_REQUIRED')
                    report=(await call('submit_goal_result',task_id=goal['id'],expected_version=fresh['version'],summary='Ready for review',checks=['Agent-reported check']))['data']
                    goal=store.get(goal['id'])
                    code,value=http('/api/goals/'+goal['id']+'/review','POST',{'expected_version':goal['version'],'result_id':report['id'],'expected_result_version':1,'decision':'changes_requested','comment':'Improve error handling'})
                    self.assertEqual(code,200);self.assertEqual(value['result']['decision'],'changes_requested')
                    feedback=(await call('get_task',task_id=goal['id']))['data'];self.assertEqual(feedback['goal_result']['comment'],'Improve error handling')
                    report=(await call('submit_goal_result',task_id=goal['id'],expected_version=feedback['version'],summary='Errors handled',checks=['Reviewed errors']))['data']
                    goal=store.get(goal['id'])
                    self.assertEqual(http('/api/goals/'+goal['id']+'/review','POST',{'expected_version':goal['version'],'result_id':report['id'],'expected_result_version':1,'decision':'accepted','comment':''})[0],200)
                async with stdio_client(params) as (read,write),ClientSession(read,write) as session:
                    await session.initialize();response=await session.call_tool('get_task',{'task_id':goal['id']})
                    value=response.structuredContent or json.loads(response.content[0].text)
                    self.assertEqual(value['data']['status'],'done')
                    self.assertEqual(value['data']['goal_result']['decision'],'accepted')
            finally:server.shutdown();server.server_close();thread.join()
