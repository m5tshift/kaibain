"""Presence ownership, assignment boundaries and heartbeat expiry."""
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from kaibain.store import Store, DomainError


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'agents.sqlite3'
        self.user=Store(self.path)
        self.agent=Store(self.path,source='agent')
        self.root=self.user.create('Root')
        self.child=self.agent.create('Child',parent_id=self.root['id'])
        self.other=self.user.create('Other')
        self.coordinator=self.agent.register_agent('coordinator','secret-c','Coordinator','coordinator','local','LangGraph')
        self.worker=self.agent.register_agent('worker','secret-w','Worker','worker','local','LangGraph')

    def tearDown(self):
        self.temp.cleanup()

    def error(self,code,fn):
        with self.assertRaises(DomainError) as caught:fn()
        self.assertEqual(caught.exception.code,code)

    def activate(self):
        self.agent.assign_agent('coordinator','secret-c','coordinator',self.root['id'],1)

    def test_presence_before_assignment_does_not_expose_owner(self):
        data=self.agent.list_agents()
        self.assertEqual(len(data['agents']),2)
        self.assertTrue(all(a['connected'] and a['task_id'] is None for a in data['agents']))
        self.assertNotIn('owner_hash',str(data))
        before=self.agent.revision()
        self.agent.report_agent('worker','secret-w')
        self.assertEqual(before,self.agent.revision())
        self.error('ASSIGNMENT_REQUIRED',lambda:self.agent.assign_agent('coordinator','secret-c','worker',self.child['id'],1))

    def test_registration_idempotence_identity_and_ownership(self):
        self.assertEqual(self.agent.register_agent('worker','secret-w','Worker','worker','local','LangGraph')['id'],'worker')
        self.error('VALIDATION',lambda:self.agent.register_agent('worker','secret-w','Changed','coordinator','local','LangGraph'))
        self.error('AGENT_FORBIDDEN',lambda:self.agent.report_agent('worker','wrong',status='error'))
        self.error('AGENT_FORBIDDEN',lambda:self.agent.register_agent('worker','wrong','Worker','worker','local','LangGraph'))
        self.error('ASSIGNMENT_REQUIRED',lambda:self.agent.report_agent('worker','secret-w',status='working'))

    def test_assignment_activity_conflicts_and_scope(self):
        self.activate()
        self.error('AGENT_FORBIDDEN',lambda:self.agent.assign_agent('worker','secret-w','coordinator',self.root['id'],2))
        self.error('ASSIGNMENT_SCOPE',lambda:self.agent.assign_agent('coordinator','secret-c','worker',self.other['id'],1))
        assigned=self.agent.assign_agent('coordinator','secret-c','worker',self.child['id'],1)
        self.assertEqual((assigned['coordinator_id'],assigned['task_id']),('coordinator',self.child['id']))
        self.error('CONFLICT',lambda:self.agent.assign_agent('coordinator','secret-c','worker',self.child['id'],1))
        try:
            self.agent.assign_agent('coordinator','secret-c','worker',self.child['id'],1)
        except DomainError as error:
            self.assertNotIn('owner_hash',json.dumps(error.result()))
        reported=self.agent.report_agent('worker','secret-w',status='working',action='Inference')
        self.assertEqual(reported['status'],'working')
        self.assertEqual(self.user.get(self.child['id'])['status'],'planned')
        self.assertTrue(self.agent.report_agent('worker','secret-w')['connected'])
        reassigned=self.agent.assign_agent('coordinator','secret-c','worker',self.child['id'],reported['version'])
        self.assertGreater(reassigned['version'],reported['version'])

    def test_expiry_disconnection_persistence_and_deleted_task(self):
        self.activate()
        self.agent.assign_agent('coordinator','secret-c','worker',self.child['id'],1)
        expired=(datetime.now(timezone.utc)-timedelta(seconds=31)).isoformat(timespec='microseconds')
        with self.agent.connection(write=True,control=True) as db:
            db.execute('UPDATE agents SET last_seen=? WHERE id=?',(expired,'worker'))
        self.assertFalse(next(a for a in self.user.list_agents()['agents'] if a['id']=='worker')['connected'])
        self.error('AGENT_OFFLINE',lambda:self.agent.assign_agent('coordinator','secret-c','worker',self.child['id'],2))
        self.agent.report_agent('worker','secret-w')
        self.agent.report_agent('worker','secret-w',disconnect=True)
        reopened=Store(self.path).list_agents()
        self.assertFalse(next(a for a in reopened['agents'] if a['id']=='worker')['connected'])
        self.agent.delete(self.child['id'],self.agent.get(self.child['id'])['version'])
        worker=next(a for a in self.agent.list_agents()['agents'] if a['id']=='worker')
        self.assertIsNone(worker['task_id'])
        self.assertIsNone(worker['coordinator_id'])
        self.assertEqual(worker['status'],'available')


if __name__=='__main__':unittest.main()
