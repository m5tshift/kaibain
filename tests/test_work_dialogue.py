"""Saved work before questions, immutable continuation and explicit human answers."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from kaibain.store import Store,DomainError
from test_recovery import recovery_fixture


class WorkDialogueTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store,self.runtime,self.run,self.checkpoint,self.export=recovery_fixture(Path(self.temp.name))
        # Fixture only: simulate the instant after a sealed write, before parking.
        with self.store.connection(write=True,control=True) as db:db.execute("UPDATE agent_runs SET status='running' WHERE id=?",(self.run['id'],))
        with patch.dict(os.environ,{'KAIBAIN_RUN_ID':self.run['id']}):self.agent=Store(self.store.path,source='agent')

    def park(self):
        root=self.agent.get(self.run['task_id'])
        asked=self.agent.ask_question(root['id'],root['version'],'Which edge case is required?',agent_id='c',owner='secret')
        self.assertEqual(asked['question']['checkpoint_id'],self.checkpoint['id'])
        self.store.finish_agent_run(self.run['id'],'waiting_for_input',question_id=asked['question']['id'])
        self.store.answer_question(root['id'],asked['question']['id'],asked['question']['version'],asked['task_version'],'Support negative numbers.')
        return self.store.agent_runs()[0],self.store.get(root['id'])

    def test_answer_continuation_preserves_work_and_old_context_cannot_write(self):
        previous,root=self.park()
        with patch('kaibain.agent_runtime.subprocess.Popen') as process:
            process.return_value.wait.return_value=0
            run=self.runtime.continue_run(previous['id'],previous['version'],root['version'])
        self.assertEqual(run['continuation_of'],previous['id']);self.assertEqual(run['recovery_checkpoint_id'],self.checkpoint['id'])
        self.assertEqual(run['project'],previous['project']);self.assertEqual(len(self.store.list(parent_id=root['id'])['tasks']),1)
        with self.assertRaises(DomainError) as caught:self.agent.create('Late old write',parent_id=root['id'])
        self.assertEqual(caught.exception.code,'RUN_STOPPED')
        self.store.finish_agent_run(run['id'],'failed','Fixture cleanup')

    def test_tampering_context_and_runtime_conflicts_preserve_answer_without_reserving(self):
        previous,root=self.park()
        target=self.export/'files/app.py';original=target.read_bytes();target.write_bytes(b'corrupt')
        with self.assertRaises(DomainError) as caught:self.runtime.continue_run(previous['id'],previous['version'],root['version'])
        self.assertEqual(caught.exception.code,'CHECKPOINT_CONFLICT');target.write_bytes(original)
        config=json.loads(self.runtime.config_path.read_text());config['command'].append('--different-environment');self.runtime.config_path.write_text(json.dumps(config))
        with self.assertRaises(DomainError) as caught:self.runtime.continue_run(previous['id'],previous['version'],root['version'])
        self.assertEqual(caught.exception.code,'CONTINUATION_RUNTIME_CHANGED')
        self.assertEqual(len(self.store.agent_runs()),1)
        self.assertEqual(self.store.goal_dialogue(root['id'])['questions'][0]['answer'],'Support negative numbers.')

    def test_question_requires_current_sealed_task_versions(self):
        child=self.agent.get(self.checkpoint['child_id']);self.agent.update(child['id'],child['version'],{'description':'Changed since seal'})
        root=self.agent.get(self.run['task_id'])
        with self.assertRaises(DomainError) as caught:self.agent.ask_question(root['id'],root['version'],'Question',agent_id='c',owner='secret')
        self.assertEqual(caught.exception.code,'CHECKPOINT_STALE')
        self.assertFalse(self.store.goal_dialogue(root['id'])['questions'])
        self.store.finish_agent_run(self.run['id'],'failed','Fixture cleanup')


if __name__=='__main__':unittest.main()
