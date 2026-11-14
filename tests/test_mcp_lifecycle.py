"""Real stdio process lifetime and presence, isolated data; no VM or model."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from kaibain.store import Store


class MCPLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def scenario(self,*,register=True,explicit=False,retire_run=False):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);store=Store(folder/'tasks.sqlite3');environment={**os.environ,'PYTHONUTF8':'1'}
            if retire_run:
                goal=store.create('Lifecycle fixture')
                run=store.reserve_agent_run(goal['id'],goal['version'],'Fixture',[sys.executable,str(ROOT/'run.py')])
                store.claim_agent_run(run['id'],os.getpid());environment['KAIBAIN_RUN_ID']=run['id']
            else:environment.pop('KAIBAIN_RUN_ID',None)
            params=StdioServerParameters(command=sys.executable,args=[str(ROOT/'run.py'),'mcp','--db',str(store.path)],env=environment)
            async with stdio_client(params) as (read,write):
                async with ClientSession(read,write) as session:
                    await session.initialize()
                    if register:
                        result=await session.call_tool('register_agent',{'name':'Lifetime fixture','role':'worker','model':'scripted','framework':'stdio'})
                        value=result.structuredContent or json.loads(result.content[0].text)
                        self.assertTrue(value['ok']);identity=value['data']['id']
                        if retire_run:store.finish_agent_run(run['id'],'failed','Fixture ended')
                        if explicit:
                            result=await session.call_tool('disconnect_agent',{})
                            value=result.structuredContent or json.loads(result.content[0].text)
                            self.assertTrue(value['ok']);version=value['data']['version']
            # stdio_client waits for the owned process to exit before this read.
            with store.connection() as db:
                rows=[dict(r) for r in db.execute('SELECT * FROM agents')]
                if register:
                    self.assertEqual(len(rows),1);self.assertEqual(rows[0]['id'],identity)
                    self.assertEqual(rows[0]['disconnected'],1)
                    events=db.execute("SELECT count(*) FROM agent_events WHERE kind='disconnected'").fetchone()[0]
                    self.assertEqual(events,1)
                    if explicit:self.assertEqual(rows[0]['version'],version)
                else:self.assertEqual(rows,[])

    async def test_eof_disconnects_registered_identity(self):await self.scenario()
    async def test_explicit_disconnect_remains_idempotent_at_eof(self):await self.scenario(explicit=True)
    async def test_eof_without_registration_creates_no_presence(self):await self.scenario(register=False)
    async def test_eof_disconnects_after_run_fence(self):await self.scenario(retire_run=True)


if __name__=='__main__':unittest.main()
