"""Latest external report uses chronology and insertion order, never UUID order."""
from pathlib import Path
from contextlib import closing
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from kaibain.store import Store,DomainError

STAMP='2026-10-25T00:00:00.000000+00:00'


class ReportOrderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'tasks.sqlite3'
        self.user=Store(self.path);self.agent=Store(self.path,source='agent')
        self.goal=self.user.create('Report order')

    def reports(self,goal=None,digits='f08',times=None):
        goal=goal or self.goal;reports=[]
        for digit,stamp in zip(digits,times or [STAMP]*len(digits)):
            identifier=f'{digit*8}-{digit*4}-4{digit*3}-8{digit*3}-{digit*12}'
            with patch('kaibain.store.now',return_value=stamp),patch('kaibain.store.uuid4',return_value=identifier):
                current=self.user.get(goal['id'])
                reports.append(self.agent.submit_goal_result(goal['id'],current['version'],'Report '+digit,['Self-reported check']))
        return reports

    def assert_latest(self,store,goal,expected):
        self.assertEqual(store.get(goal['id'])['goal_result'],expected)
        reports=[r for r in store.list()['goal_results'] if r['task_id']==goal['id']]
        self.assertEqual(reports,[expected])

    def snapshot(self):
        with self.user.connection() as db:return list(db.iterdump())

    def test_get_and_list_choose_last_insert_when_time_is_equal(self):
        reports=self.reports()
        self.assertEqual([r['created_at'] for r in reports],[STAMP]*3)
        self.assertNotEqual(max(r['id'] for r in reports),reports[-1]['id'])
        self.assert_latest(self.user,self.goal,reports[-1])

    def test_latest_is_selected_independently_for_each_goal(self):
        other=self.user.create('Other goal')
        first=self.reports();second=self.reports(other,'e17')
        self.assert_latest(self.user,self.goal,first[-1]);self.assert_latest(self.user,other,second[-1])
        self.assertEqual(len(self.user.list()['goal_results']),2)

    def test_latest_can_be_accepted_and_old_review_is_rejected_without_writes(self):
        reports=self.reports();current=self.user.get(self.goal['id']);before=self.snapshot()
        with self.assertRaises(DomainError) as caught:
            self.user.review_goal(current['id'],current['version'],reports[0]['id'],1,'accepted')
        self.assertEqual(caught.exception.code,'REVIEW_STALE');self.assertEqual(self.snapshot(),before)
        accepted=self.user.review_goal(current['id'],current['version'],reports[-1]['id'],1,'accepted')
        self.assertEqual(accepted['decision'],'accepted');self.assertEqual(accepted['version'],2)
        self.assertEqual(self.user.get(current['id'])['status'],'done')
        self.assert_latest(Store(self.path),self.goal,accepted)

    def test_latest_can_be_returned_and_new_report_supersedes_it(self):
        reports=self.reports();current=self.user.get(self.goal['id'])
        returned=self.user.review_goal(current['id'],current['version'],reports[-1]['id'],1,'changes_requested','Add evidence')
        self.assertEqual(returned['comment'],'Add evidence');self.assert_latest(self.user,self.goal,returned)
        new=self.reports(digits='1')[-1]
        self.assert_latest(self.user,self.goal,new)
        before=self.snapshot();current=self.user.get(self.goal['id'])
        with self.assertRaises(DomainError) as caught:
            self.user.review_goal(current['id'],current['version'],returned['id'],2,'accepted')
        self.assertEqual(caught.exception.code,'REVIEW_STALE');self.assertEqual(self.snapshot(),before)

    def test_order_survives_reopen_and_vacuum(self):
        latest=self.reports()[-1];self.assert_latest(Store(self.path),self.goal,latest)
        with closing(sqlite3.connect(self.path)) as db:db.execute('VACUUM')
        self.assert_latest(Store(self.path),self.goal,latest)
        new=self.reports(digits='1')[-1];self.assert_latest(Store(self.path),self.goal,new)

    def test_legacy_foreign_key_rebuild_preserves_insertion_order_and_history(self):
        reports=self.reports();root_before=self.user.get(self.goal['id']);revision_before=self.user.revision()
        with self.user.connection() as db:
            history_before=[dict(r) for r in db.execute('SELECT * FROM goal_results ORDER BY rowid')]
        with self.user.connection(write=True,control=True) as db:
            db.execute('ALTER TABLE goal_results RENAME TO saved_reports')
            db.execute('''CREATE TABLE goal_results (
                id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                task_version INTEGER NOT NULL,summary TEXT NOT NULL,checks TEXT NOT NULL,
                created_at TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,
                decision TEXT NOT NULL DEFAULT 'pending',comment TEXT NOT NULL DEFAULT '')''')
            db.execute('INSERT INTO goal_results SELECT * FROM saved_reports ORDER BY rowid')
            db.execute('DROP TABLE saved_reports')
        migrated=Store(self.path)
        self.assert_latest(migrated,self.goal,reports[-1])
        self.assertEqual(migrated.get(self.goal['id']),root_before);self.assertEqual(migrated.revision(),revision_before)
        with migrated.connection() as db:
            self.assertEqual(db.execute('PRAGMA foreign_key_list(goal_results)').fetchall(),[])
            self.assertEqual([dict(r) for r in db.execute('SELECT * FROM goal_results ORDER BY rowid')],history_before)

    def test_different_timestamps_keep_existing_chronological_priority(self):
        times=[STAMP.replace('-25T','-'+day+'T') for day in ('25','27','26')]
        reports=self.reports(times=times)
        self.assert_latest(self.user,self.goal,reports[1])
        # This report is chronologically newest but its task snapshot is stale.
        current=self.user.get(self.goal['id']);before=self.snapshot()
        with self.assertRaises(DomainError) as caught:
            self.user.review_goal(current['id'],current['version'],reports[1]['id'],1,'accepted')
        self.assertEqual(caught.exception.code,'REVIEW_STALE');self.assertEqual(self.snapshot(),before)

    def test_restart_keeps_review_latest_and_report_payload_unchanged(self):
        reports=self.reports();user=Store(self.path);current=user.get(self.goal['id'])
        accepted=user.review_goal(current['id'],current['version'],reports[-1]['id'],1,'accepted')
        self.assertEqual(set(accepted),set(reports[-1]))
        for key in ('id','task_id','task_version','summary','checks','created_at'):
            self.assertEqual(accepted[key],reports[-1][key])
        self.assert_latest(Store(self.path),self.goal,accepted)


if __name__=='__main__':unittest.main()
