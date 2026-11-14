"""Human answers, coordinator identity and durable pause/continue invariants."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from kaibain.store import Store,DomainError
from kaibain.agent_runtime import AgentRuntime


class DialogueTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)
        self.user=Store(self.folder/'tasks.sqlite3');self.agent=Store(self.user.path,source='agent')
        self.goal=self.user.create('Clarify requirements')
        self.profile={'repo':str(self.folder),'commit':'a'*40,'files':['app.py'],'writable':['app.py'],
            'checks':['python','-m','unittest'],'acceptance':['Preserve API']}
        self.config=self.folder/'runtime.json';self.config.write_text(json.dumps({'kind':'development-v1',
            'label':'Fixture','command':[sys.executable,str(Path(__file__).resolve())]}))
        self.runtime=AgentRuntime(self.user,self.config)
        self.identity='coordinator';self.owner='fixture-owner'

    def tearDown(self):
        self.temp.cleanup()

    def coordinator(self,agent=None):
        agent=agent or self.agent
        agent.register_agent(self.identity,self.owner,'Coordinator','coordinator','fixture','fixture')
        agent.assign_agent(self.identity,self.owner,self.identity,self.goal['id'],1)
        return agent

    def ask(self,agent=None,text='Which public API must be kept?'):
        agent=agent or self.agent
        return agent.ask_question(self.goal['id'],self.user.get(self.goal['id'])['version'],text,
            agent_id=self.identity,owner=self.owner)['question']

    def error(self,code,callback):
        with self.assertRaises(DomainError) as caught:callback()
        self.assertEqual(caught.exception.code,code)

    def answer(self,question,text='Keep add(a, b).'):
        return self.user.answer_question(self.goal['id'],question['id'],question['version'],self.user.get(self.goal['id'])['version'],text)

    def pause(self):
        run=self.user.reserve_agent_run(self.goal['id'],1,'Fixture',[],project=self.profile)
        self.user.claim_agent_run(run['id'],os.getpid())
        with patch.dict(os.environ,{'KAIBAIN_RUN_ID':run['id']}):scoped=Store(self.user.path,source='agent')
        self.coordinator(scoped);question=self.ask(scoped)
        self.user.finish_agent_run(run['id'],'waiting_for_input',question_id=question['id'])
        return self.user.agent_runs()[0],question,scoped

    def test_external_question_answer_and_context_survive_restart(self):
        self.coordinator();question=self.ask()
        current=self.user.get(self.goal['id']);self.assertTrue(current['needs_input'])
        self.assertEqual(current['status'],'planned')
        self.error('INPUT_REQUIRED',lambda:self.agent.create('Premature child',parent_id=current['id']))
        self.error('INPUT_REQUIRED',lambda:self.agent.update(current['id'],current['version'],{'status':'review'}))
        self.error('INPUT_REQUIRED',lambda:self.agent.submit_goal_result(current['id'],current['version'],'Premature',[]))
        self.error('FORBIDDEN',lambda:self.agent.answer_question(current['id'],question['id'],1,current['version'],'Forged answer'))
        answered=self.answer(question)
        self.assertFalse(answered['needs_input']);self.assertEqual(answered['requirements_version'],1)
        self.assertEqual(Store(self.user.path).goal_dialogue(current['id']),answered)
        self.assertEqual(self.agent.goal_dialogue(current['id'])['questions'][0]['answer'],'Keep add(a, b).')
        self.agent.create('Now permitted',parent_id=current['id'])

    def test_only_assigned_coordinator_can_ask_and_versions_protect_answers(self):
        self.error('AGENT_NOT_FOUND',lambda:self.ask())
        self.coordinator();question=self.ask();root=self.user.get(self.goal['id'])
        self.error('INPUT_REQUIRED',lambda:self.ask())
        self.error('CONFLICT',lambda:self.user.answer_question(root['id'],question['id'],1,1,'Stale response'))
        self.answer(question)
        self.error('CONFLICT',lambda:self.answer(question,'Overwrite answer'))
        self.assertEqual(self.user.goal_dialogue(root['id'])['questions'][0]['answer'],'Keep add(a, b).')
        other=self.user.create('Another root')
        self.error('AGENT_FORBIDDEN',lambda:self.agent.ask_question(other['id'],1,'Wrong root?',agent_id=self.identity,owner=self.owner))

    def test_pause_is_not_active_and_continuation_reuses_frozen_profile_once(self):
        run,question,scoped=self.pause()
        self.assertEqual(AgentRuntime(Store(self.user.path),self.config).snapshot()['runs'][0]['status'],'waiting_for_input')
        self.error('RUN_STOPPED',lambda:scoped.create('Old process may not continue'))
        another=self.user.create('Other goal')
        independent=self.user.reserve_agent_run(another['id'],1,'Fixture',[])
        self.user.finish_agent_run(independent['id'],'failed','Fixture done')
        dialogue=self.answer(question);paused=dialogue['continuation']
        self.assertEqual(paused['id'],run['id'])
        with patch('kaibain.agent_runtime.subprocess.Popen') as process:
            process.return_value.wait.return_value=0
            continued=self.runtime.continue_run(run['id'],paused['version'],dialogue['task_version'])
        self.assertEqual(continued['continuation_of'],run['id']);self.assertEqual(continued['project'],run['project'])
        self.assertEqual(continued['requirements_version'],1)
        self.error('RUN_FINISHED',lambda:self.user.stop_agent_run(run['id'],paused['version']))
        self.user.finish_agent_run(continued['id'],'failed','Fixture cleanup')
        self.error('CONTINUATION_STALE',lambda:self.runtime.continue_run(run['id'],paused['version'],dialogue['task_version']))
        self.assertEqual(len(self.user.goal_dialogue(self.goal['id'])['questions']),1)

    def test_answer_before_cleanup_is_rejected_and_stopping_pause_closes_question(self):
        run=self.user.reserve_agent_run(self.goal['id'],1,'Fixture',[],project=self.profile)
        self.user.claim_agent_run(run['id'],os.getpid())
        with patch.dict(os.environ,{'KAIBAIN_RUN_ID':run['id']}):scoped=Store(self.user.path,source='agent')
        self.coordinator(scoped);question=self.ask(scoped)
        self.error('QUESTION_NOT_READY',lambda:self.answer(question))
        self.user.finish_agent_run(run['id'],'waiting_for_input',question_id=question['id'])
        paused=self.user.agent_runs()[0];stopped=self.user.stop_agent_run(run['id'],paused['version'])
        self.assertEqual(stopped['status'],'cancelled')
        dialogue=self.user.goal_dialogue(self.goal['id'])
        self.assertFalse(dialogue['needs_input']);self.assertEqual(dialogue['questions'][0]['status'],'cancelled')

    def test_answer_is_kept_when_resume_readiness_fails(self):
        run,question,_=self.pause()
        with patch.object(self.runtime,'continue_run',side_effect=DomainError('RUNTIME_NOT_READY','Open Ollama',409)):
            result=self.runtime.answer(self.goal['id'],question_id=question['id'],expected_version=1,
                expected_task_version=self.user.get(self.goal['id'])['version'],answer='Use add(a, b).')
        self.assertEqual(result['continuation_error']['code'],'RUNTIME_NOT_READY')
        self.assertEqual(result['dialogue']['questions'][0]['answer'],'Use add(a, b).')
        self.assertEqual(result['dialogue']['continuation']['id'],run['id'])

    def test_stop_wins_race_with_question_pause(self):
        run=self.user.reserve_agent_run(self.goal['id'],1,'Fixture',[],project=self.profile)
        self.user.claim_agent_run(run['id'],os.getpid())
        with patch.dict(os.environ,{'KAIBAIN_RUN_ID':run['id']}):scoped=Store(self.user.path,source='agent')
        self.coordinator(scoped);question=self.ask(scoped)
        current=self.user.agent_runs()[0];self.user.stop_agent_run(run['id'],current['version'])
        self.user.finish_agent_run(run['id'],'waiting_for_input',question_id=question['id'])
        self.assertEqual(self.user.agent_runs()[0]['status'],'cancelled')
        self.assertEqual(self.user.goal_dialogue(self.goal['id'])['questions'][0]['status'],'cancelled')

    def test_clarification_invalidates_old_review_and_limits_are_explicit(self):
        self.coordinator()
        report=self.agent.submit_goal_result(self.goal['id'],1,'Old result',[])
        root=self.agent.get(self.goal['id']);self.agent.update(root['id'],root['version'],{'status':'in_progress'})
        question=self.ask();self.error('VALIDATION',lambda:self.answer(question,'я'*1100))
        self.answer(question)
        root=self.agent.get(self.goal['id']);self.agent.update(root['id'],root['version'],{'status':'review'})
        root=self.user.get(root['id'])
        self.error('REVIEW_STALE',lambda:self.user.review_goal(root['id'],root['version'],report['id'],1,'accepted'))


if __name__=='__main__':unittest.main()
