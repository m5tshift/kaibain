"""Structured activity, coordination edges and read-only graph projections."""
from datetime import datetime,timedelta,timezone
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from kaibain.store import Store,DomainError


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'tasks.sqlite3'
        self.user=Store(self.path);self.agent=Store(self.path,source='agent')
        self.root=self.user.create('Goal');self.child=self.agent.create('Work',parent_id=self.root['id'])
        self.agent.register_agent('c','c-secret','Coordinator','coordinator','local','Fixture')
        self.agent.register_agent('w','w-secret','Developer','worker','local','Fixture')
        self.agent.assign_agent('c','c-secret','c',self.root['id'],1)
        self.agent.assign_agent('c','c-secret','w',self.child['id'],1)
        self.budget={'scope':'run','seconds_limit':180,'elapsed_seconds':10.0,'tokens_limit':32768,
                     'tokens_used':1024,'steps_limit':10,'steps_used':2}
    def tearDown(self):self.temp.cleanup()
    def agents(self):return {a['id']:a for a in self.user.list_agents()['agents']}
    def connect(self,kind,source='c',target='w',actor='c'):
        agents=self.agents()
        return self.agent.connect_agents(actor,actor+'-secret',source,target,kind,agents[source]['version'],agents[target]['version'])
    def error(self,code,fn):
        with self.assertRaises(DomainError) as caught:fn()
        self.assertEqual(caught.exception.code,code)

    def test_activity_phase_budget_and_idle_heartbeat_keep_semantics(self):
        a=self.agent.report_agent('w','w-secret',status='working',phase='editing',action='Write app.py',budget=self.budget)
        revision=self.agent.revision();self.agent.report_agent('w','w-secret')
        saved=self.agents()['w'];self.assertEqual(saved['phase_started_at'],a['phase_started_at'])
        self.assertEqual(saved['version'],a['version']);self.assertEqual(self.agent.revision(),revision)
        self.assertEqual(saved['activity']['phase'],'editing')
        self.assertEqual(saved['activity']['budget']['tokens_remaining'],31744)
        self.assertEqual(saved['activity']['budget']['steps_remaining'],8)
        for budget in ({**self.budget,'tokens_used':-1},{**self.budget,'steps_limit':False},
                       {**self.budget,'elapsed_seconds':float('inf')},{**self.budget,'tokens_used':10**1000},
                       {**self.budget,'endpoint':'http://example.com'}):
            self.error('VALIDATION',lambda:self.agent.report_agent('w','w-secret',budget=budget))
        self.error('VALIDATION',lambda:self.agent.report_agent('w','w-secret',phase='imaginary'))
        self.agent.report_agent('w','w-secret',phase='checking')
        self.assertEqual(self.user.list_agents()['events'][0]['message'],'Изменён этап работы')

    def test_optional_shared_resources_are_complete_bounded_and_survive_graph_projection(self):
        budget={**self.budget,'workers_limit':2,'workers_active':1,'cpus_limit':2,'cpus_reserved':1,'memory_mb_limit':1024,'memory_mb_reserved':512}
        self.agent.report_agent('w','w-secret',phase='checking',budget=budget)
        current=next(row for row in self.user.list_agents()['agents'] if row['id']=='w')
        self.assertEqual(current['activity']['budget']['memory_mb_reserved'],512)
        for changed in ({'workers_active':3},{'memory_mb_reserved':-1},{'cpus_limit':True}):
            self.error('VALIDATION',lambda:self.agent.report_agent('w','w-secret',budget={**budget,**changed}))
        incomplete={**budget};incomplete.pop('cpus_reserved')
        self.error('VALIDATION',lambda:self.agent.report_agent('w','w-secret',budget=incomplete))

    def test_dependencies_handoffs_are_different_persistent_and_idempotent(self):
        wait=self.connect('dependency');self.assertEqual(self.connect('dependency')['id'],wait['id'])
        self.error('DEPENDENCY_CYCLE',lambda:self.connect('dependency','w','c'))
        delivered=self.connect('handoff','w','c','w')
        graph=Store(self.path).list_agents();links={l['kind']:l for l in graph['links']}
        self.assertEqual(links['dependency']['state'],'fulfilled');self.assertEqual(links['handoff']['id'],delivered['id'])
        self.assertEqual(links['assignment']['source_id'],'c')
        event=next(e for e in graph['events'] if e['kind']=='handoff')
        self.assertEqual(event['metadata']['source_id'],'w');self.assertEqual(event['metadata']['target_id'],'c')
        self.assertNotIn('owner_hash',str(graph));self.assertNotIn('secret',str(graph))
        # A later wait must not be satisfied by the earlier delivery.
        new_wait=self.connect('dependency');self.assertNotEqual(new_wait['id'],wait['id'])
        new_handoff=self.connect('handoff','w','c','w');self.assertNotEqual(new_handoff['id'],delivered['id'])

    def test_links_enforce_roles_versions_scopes_and_reassignment(self):
        self.error('AGENT_FORBIDDEN',lambda:self.connect('dependency','w','c','w'))
        self.error('AGENT_FORBIDDEN',lambda:self.connect('handoff','c','w','w'))
        self.error('CONFLICT',lambda:self.agent.connect_agents('c','c-secret','c','w','dependency',1,1))
        self.error('AGENT_ONLY',lambda:self.user.connect_agents('c','c-secret','c','w','dependency',2,2))
        self.connect('dependency')
        w=self.agents()['w'];self.agent.assign_agent('c','c-secret','w',self.child['id'],w['version'])
        self.assertEqual([l['kind'] for l in self.user.list_agents()['links']],['assignment'])
        self.connect('dependency');self.agent.delete(self.child['id'],self.agent.get(self.child['id'])['version'])
        self.assertEqual(self.user.list_agents()['links'],[])

    def test_other_goal_and_other_run_cannot_be_wired(self):
        other=self.user.create('Other');self.agent.register_agent('c2','c2-secret','Other coord','coordinator','local','Fixture')
        self.agent.assign_agent('c2','c2-secret','c2',other['id'],1)
        self.error('AGENT_FORBIDDEN',lambda:self.connect('dependency','c','c2'))
        with self.agent.connection(write=True,control=True) as db:db.execute("UPDATE agents SET run_id='other-run' WHERE id='w'")
        self.error('ASSIGNMENT_SCOPE',lambda:self.connect('handoff','w','c','w'))
        self.error('ASSIGNMENT_SCOPE',lambda:self.agent.assign_agent('c','c-secret','w',self.child['id'],self.agents()['w']['version']))

    def test_reparenting_removes_stale_worker_assignment_and_links(self):
        self.connect('dependency');other=self.user.create('Other')
        child=self.agent.get(self.child['id'])
        self.agent.update(child['id'],child['version'],{'parent_id':other['id']})
        worker=self.agents()['w'];self.assertIsNone(worker['task_id']);self.assertEqual(worker['activity']['phase'],'idle')
        self.assertEqual(self.user.list_agents()['links'],[])

    def test_question_controls_projection_even_when_client_reports_another_phase(self):
        current=self.agent.get(self.root['id'])
        self.agent.ask_question(current['id'],current['version'],'What API?',agent_id='c',owner='c-secret')
        self.agent.report_agent('w','w-secret',phase='editing',status='working')
        self.assertTrue(all(a['activity']['phase']=='waiting_for_input' for a in self.agents().values()))
        self.error('INPUT_REQUIRED',lambda:self.connect('dependency'))

    def test_expired_presence_does_not_tick_elapsed_or_create_fake_work(self):
        self.agent.report_agent('w','w-secret',phase='checking',status='working',budget=self.budget)
        old=(datetime.now(timezone.utc)-timedelta(seconds=40)).isoformat(timespec='microseconds')
        with self.agent.connection(write=True,control=True) as db:db.execute("UPDATE agents SET last_seen=?,phase_started_at=? WHERE id='w'",(old,old))
        saved=self.agents()['w'];self.assertFalse(saved['connected']);self.assertEqual(saved['activity']['phase'],'offline')
        self.assertEqual(saved['activity']['elapsed_seconds'],0)
        self.assertEqual(saved['activity']['budget']['seconds_remaining'],170)

    def test_run_fence_allows_presence_but_not_new_execution_telemetry(self):
        # A real reservation/claim with process-scoped registrations in an isolated DB.
        run=self.user.reserve_agent_run(self.root['id'],self.user.get(self.root['id'])['version'],'Fixture',[])
        self.user.claim_agent_run(run['id'],os.getpid())
        with patch.dict(os.environ,{'KAIBAIN_RUN_ID':run['id']}):scoped=Store(self.path,source='agent')
        scoped.register_agent('run-c','run-secret','Runtime coord','coordinator','fixture','Fixture')
        scoped.assign_agent('run-c','run-secret','run-c',self.root['id'],1)
        self.user.finish_agent_run(run['id'],'failed','Model unavailable')
        scoped.report_agent('run-c','run-secret')
        self.error('RUN_STOPPED',lambda:scoped.report_agent('run-c','run-secret',phase='editing'))
        runtime_agent=next(a for a in self.user.list_agents()['agents'] if a['id']=='run-c')
        self.assertEqual(runtime_agent['activity']['phase'],'blocked')
        self.assertEqual(runtime_agent['activity']['reason'],'Model unavailable')
        scoped.report_agent('run-c','run-secret',disconnect=True)


if __name__=='__main__':unittest.main()
