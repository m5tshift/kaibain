from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from kaibain.store import Store,DomainError


class NativeOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'tasks.sqlite3'
        self.user=Store(self.path);self.agent=Store(self.path,source='agent');self.goal=self.user.create('User goal')
    def tearDown(self):self.temp.cleanup()
    def error(self,code,fn):
        with self.assertRaises(DomainError) as caught:fn()
        self.assertEqual(caught.exception.code,code)

    def test_agent_cannot_complete_a_goal_by_creation_promotion_or_status_change(self):
        self.error('GOAL_REVIEW_REQUIRED',lambda:self.agent.create('Forged acceptance',status='done'))
        child=self.agent.create('Completed work',parent_id=self.goal['id'],status='done')
        self.error('GOAL_REVIEW_REQUIRED',lambda:self.agent.update(child['id'],1,{'parent_id':None}))
        current=self.agent.get(self.goal['id'])
        self.error('GOAL_REVIEW_REQUIRED',lambda:self.agent.update(current['id'],current['version'],{'status':'done'}))
        other=self.user.create('Another goal')
        self.error('GOAL_PARENT_LOCKED',lambda:self.agent.update(current['id'],current['version'],{'parent_id':other['id'],'status':'done'}))
        self.assertFalse(self.user.workspace()['capabilities']['agent']['review_results'])
        self.assertFalse(self.user.workspace()['capabilities']['user']['edit_tasks'])

    def test_human_reviews_immutable_external_result_and_history_survives_goal_deletion(self):
        report=self.agent.submit_goal_result(self.goal['id'],1,'Frozen summary',['Self-reported evidence'])
        current=self.agent.get(self.goal['id'])
        self.error('FORBIDDEN',lambda:self.agent.review_goal(current['id'],current['version'],report['id'],1,'accepted'))
        self.error('VALIDATION',lambda:self.user.review_goal(current['id'],current['version'],report['id'],1,'changes_requested',' '))
        accepted=self.user.review_goal(current['id'],current['version'],report['id'],1,'accepted')
        self.assertEqual(accepted['summary'],report['summary']);self.assertEqual(accepted['checks'],report['checks'])
        self.error('GOAL_ACCEPTED_LOCKED',lambda:self.agent.update(current['id'],self.agent.get(current['id'])['version'],{'status':'planned'}))
        self.agent.delete(current['id'],self.agent.get(current['id'])['version'])
        with Store(self.path).connection() as db:
            row=dict(db.execute('SELECT * FROM goal_results WHERE id=?',(report['id'],)).fetchone())
        self.assertEqual(row['summary'],'Frozen summary');self.assertEqual(row['decision'],'accepted')

    def test_queued_goal_scope_is_frozen_and_cannot_mix_external_and_builtin_results(self):
        run=self.user.reserve_agent_run(self.goal['id'],1,'Fixture',[])
        self.error('GOAL_STARTED',lambda:self.user.update(self.goal['id'],1,{'description':'Changed while queued'}))
        self.error('RUN_BUSY',lambda:self.agent.submit_goal_result(self.goal['id'],1,'External result',[]))
        self.assertEqual(self.user.get(self.goal['id'])['version'],1)
        self.user.stop_agent_run(run['id'],run['version'])
        updated=self.user.update(self.goal['id'],1,{'description':'Clarified after queue cancellation'})
        self.assertEqual(updated['version'],2)

    def test_early_report_schema_migration_preserves_report_and_task_versions(self):
        report=self.agent.submit_goal_result(self.goal['id'],1,'Preserved report',['Evidence'])
        before=self.user.get(self.goal['id'])
        with self.user.connection(write=True,control=True) as db:
            db.execute('ALTER TABLE goal_results RENAME TO reports_saved')
            db.execute('''CREATE TABLE goal_results (
                id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                task_version INTEGER NOT NULL,summary TEXT NOT NULL,checks TEXT NOT NULL,
                created_at TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,
                decision TEXT NOT NULL DEFAULT 'pending',comment TEXT NOT NULL DEFAULT '')''')
            db.execute('INSERT INTO goal_results SELECT * FROM reports_saved')
            db.execute('DROP TABLE reports_saved')
        migrated=Store(self.path)
        self.assertEqual(migrated.get(self.goal['id']),before)
        with migrated.connection() as db:self.assertEqual(db.execute('PRAGMA foreign_key_list(goal_results)').fetchall(),[])
        self.agent.delete(self.goal['id'],before['version'])
        with migrated.connection() as db:self.assertIsNotNone(db.execute('SELECT id FROM goal_results WHERE id=?',(report['id'],)).fetchone())
