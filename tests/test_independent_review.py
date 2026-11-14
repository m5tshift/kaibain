"""Only hash-bounded, different-family reports can accompany a configured run."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from kaibain.store import Store,DomainError
from kaibain.independent_review import load_review


def assessment(decision='accept'):
    return {'decision':decision,'summary':'Independent report','findings':[] if decision=='accept' else [
        {'path':'app.py','line':1,'severity':'major','message':'Required behavior is missing.','evidence':'def add'}],
        'identities':{'developer':{'name':'qwen3:8b','family':'qwen3','family_group':'qwen','digest':'a'*64},
                      'validator':{'name':'gemma3:4b','family':'gemma3','family_group':'gemma','digest':'b'*64}}}


class IndependentReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();folder=Path(self.temp.name)
        self.store=Store(folder/'tasks.sqlite3');self.agent=Store(self.store.path,source='agent')
        goal=self.store.create('Fix it');self.goal=goal
        self.project={'repo':str(folder),'commit':'a'*40,'files':['app.py'],'writable':['app.py'],
                      'checks':['python','-m','unittest'],'acceptance':['Preserve API']}
        self.run=self.store.reserve_agent_run(goal['id'],1,'Fixture',[sys.executable,str(folder/'fixture.py'),'--validator-model','gemma3:4b'],project=self.project)
        self.store.claim_agent_run(self.run['id'],os.getpid())
        child=self.agent.create('Work',parent_id=goal['id'],status='review');root=self.agent.get(goal['id'])
        root=self.agent.update(root['id'],root['version'],{'status':'review'})
        self.result={'child_id':child['id'],'summary':'Ready','checks':['Fixture only'],'verification':{'status':'passed'},
                     'task_versions':{root['id']:root['version'],child['id']:child['version']}}
    def tearDown(self):self.temp.cleanup()
    def error(self,code,fn):
        with self.assertRaises(DomainError) as caught:fn()
        self.assertEqual(caught.exception.code,code)

    def test_configured_review_cannot_be_omitted_and_acceptance_still_belongs_to_user(self):
        self.error('REVIEW_MISSING',lambda:self.store.finish_agent_run(self.run['id'],'completed',result=self.result))
        self.store.finish_agent_run(self.run['id'],'completed',result=self.result,independent_review=assessment())
        saved=Store(self.store.path).agent_runs()[0]
        self.assertEqual(saved['independent_review'],assessment());self.assertEqual(self.store.get(self.goal['id'])['status'],'review')
        self.error('FORBIDDEN',lambda:self.agent.review_agent_run(saved['id'],saved['version'],'accepted'))
        self.store.review_agent_run(saved['id'],saved['version'],'accepted')
        self.assertEqual(self.store.get(self.goal['id'])['status'],'done')

    def test_returned_findings_are_saved_without_completing_the_goal(self):
        self.store.finish_agent_run(self.run['id'],'failed','Model requested changes',failure_code='review_rejected',independent_review=assessment('changes_requested'))
        saved=self.store.agent_runs()[0]
        self.assertEqual(saved['failure_kind'],'review');self.assertEqual(saved['independent_review']['findings'][0]['path'],'app.py')
        self.assertNotEqual(self.store.get(self.goal['id'])['status'],'done')

    def test_forged_family_unknown_paths_and_nonfinite_json_are_rejected(self):
        for change in ('same-family','false-group','path','nan'):
            value=copy.deepcopy(assessment('changes_requested'))
            if change=='same-family':value['identities']['validator']=dict(value['identities']['developer'])
            elif change=='false-group':value['identities']['validator'].update(family='qwen35',family_group='gemma')
            elif change=='path':value['findings'][0]['path']='../secrets'
            else:value['inference']={'seconds':float('nan')}
            self.error('INVALID_REVIEW',lambda:Store.validate_independent_review(value,self.project))

    def test_stop_wins_even_if_the_late_result_has_no_review(self):
        current=self.store.agent_runs()[0];self.store.stop_agent_run(current['id'],current['version'])
        self.store.finish_agent_run(current['id'],'completed',result=self.result)
        self.assertEqual(self.store.agent_runs()[0]['status'],'cancelled')

    def test_review_is_bound_to_untampered_export_clean_hashes_and_exact_evidence(self):
        folder=Path(self.temp.name)/'development';files=folder/'result/files';files.mkdir(parents=True)
        data=b'def add(a,b): return a-b\n';(files/'app.py').write_bytes(data)
        sha=lambda body:hashlib.sha256(body).hexdigest()
        patch=b'+def add(a,b): return a-b\n';(folder/'result/changes.diff').write_bytes(patch)
        manifest={'base_commit':'a'*40,'backend':'microsandbox','isolation':'microvm','deleted':[],
                  'patch_sha256':sha(patch),'files':[{'path':'app.py','bytes':len(data),'sha256':sha(data)}]}
        (folder/'result/manifest.json').write_text(json.dumps(manifest))
        clean={'type':'fresh_microvm_test_replay','checks':{'exit_code':0,'stop_reason':None},
               'verified_patch_sha256':sha(patch),'file_hashes':{'app.py':sha(data)}}
        (folder/'clean-check.json').write_text(json.dumps(clean))
        report=assessment('changes_requested');report['manifest_sha256']=sha(json.dumps(manifest,sort_keys=True).encode())
        target=folder/'independent-review.json';target.write_text(json.dumps(report))
        self.assertEqual(load_review(folder,self.project)['decision'],'changes_requested')
        for name in ('result/files/app.py','result/changes.diff','result/manifest.json','clean-check.json'):
            path=folder/name;original=path.read_bytes();path.write_bytes(original+b'!')
            with self.subTest(path=name),self.assertRaises(DomainError):load_review(folder,self.project)
            path.write_bytes(original)
        report['findings'][0]['evidence']='not in source';target.write_text(json.dumps(report))
        self.error('INVALID_REVIEW',lambda:load_review(folder,self.project))


if __name__=='__main__':unittest.main()
