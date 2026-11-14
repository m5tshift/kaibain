"""Publication tests use disposable repositories; no project code is executed."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from kaibain.store import Store, DomainError
from kaibain.result_review import review, apply, digest


def git(repo, *args):
    return subprocess.check_output(['git', '-c', 'core.autocrlf=false', '-C', str(repo), *args], stderr=subprocess.DEVNULL)


def fixture(folder):
    repo=folder/'project';repo.mkdir()
    git(repo,'init','-q');git(repo,'config','user.name','Fixture');git(repo,'config','user.email','fixture@localhost')
    for name,body in {'app.py':'def add(a, b):\n    return a - b\n','test_app.py':'# Frozen test fixture\n',
                      'notes.txt':'User notes\n'}.items():(repo/name).write_bytes(body.encode())
    git(repo,'add','.');git(repo,'commit','-qm','Initial')
    base=git(repo,'rev-parse','HEAD').decode().strip()
    store=Store(folder/'tasks.sqlite3');agent=Store(store.path,source='agent')
    goal=store.create('Сложение без ошибок',description='Сохранить публичный API')
    profile={'repo':str(repo),'commit':base,'files':['app.py','test_app.py'], 'writable':['app.py','new.py'],
             'checks':['python','-m','unittest'], 'acceptance':['Верные суммы положительных и отрицательных чисел.','Публичная сигнатура add(a, b) сохранена.']}
    run=store.reserve_agent_run(goal['id'],goal['version'],'Scripted fixture',[],project=profile)
    store.claim_agent_run(run['id'],os.getpid())
    child=agent.create('Исправить вычисление',parent_id=goal['id'],status='review')
    root=agent.get(goal['id']);root=agent.update(root['id'],root['version'],{'status':'review'})
    development=folder/'.agent-runs'/run['id']/'development';out=development/'result';(out/'files').mkdir(parents=True)
    values={'app.py':b'def add(a, b):\n    return a + b\n','test_app.py':(repo/'test_app.py').read_bytes(),'new.py':b'# new module\n'}
    patch_bytes=b'--- a/app.py\n+++ b/app.py\n-return a - b\n+return a + b\n'
    (out/'changes.diff').write_bytes(patch_bytes)
    for name,data in values.items():(out/'files'/name).write_bytes(data)
    manifest={'backend':'microsandbox','isolation':'microvm','base_commit':base,'deleted':[],
              'patch_sha256':digest(patch_bytes),'files':[{'path':name,'bytes':len(data),'sha256':digest(data)} for name,data in values.items()]}
    manifest_bytes=json.dumps(manifest).encode();(out/'manifest.json').write_bytes(manifest_bytes)
    evidence={'baseline':{'exit_code':1,'output':'Expected failing baseline'},'worker_checks':{'exit_code':0,'output':'All checks passed'},
              'clean_check':{'checks':{'exit_code':0,'output':'Fresh VM replay'},'verified_patch_sha256':digest(patch_bytes),
                             'file_hashes':{name:digest(data) for name,data in values.items()}}}
    report_bytes=json.dumps(evidence).encode();(development/'report.json').write_bytes(report_bytes)
    (folder/'.agent-runs'/run['id']/'executor.log').write_text('Fixture only')
    result={'child_id':child['id'],'summary':'Исправлено сложение; сигнатура сохранена. <script>window.injected=1</script>',
            'checks':['Scripted test fixture, no model inference.'],'verification':{'status':'passed','tests_run':True,
            'manifest_sha256':digest(manifest_bytes),'patch_sha256':digest(patch_bytes),'report_sha256':digest(report_bytes)},
            'task_versions':{root['id']:root['version'],child['id']:child['version']}}
    store.finish_agent_run(run['id'],'completed',result=result)
    return store,store.agent_runs()[0],repo,out


class ResultReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)
        self.store,self.run,self.repo,self.out=fixture(self.folder)
    def tearDown(self):self.temp.cleanup()
    def accept(self):
        self.run=self.store.review_agent_run(self.run['id'],self.run['version'],'accepted')
    def apply(self):
        preview=review(self.store,self.run['id'])
        return apply(self.store,self.run['id'],self.run['version'],preview['token'])
    def error(self,code,fn):
        with self.assertRaises(DomainError) as caught:fn()
        self.assertEqual(caught.exception.code,code)

    def test_acceptance_is_separate_and_apply_preserves_tree_index_and_history(self):
        before=(self.repo/'app.py').read_bytes();head=git(self.repo,'rev-parse','HEAD')
        preview=review(self.store,self.run['id'])
        self.assertTrue(preview['integrity']);self.assertFalse(preview['can_apply'])
        self.error('APPLY_UNAVAILABLE',lambda:self.apply())
        self.accept();self.assertEqual(git(self.repo,'rev-parse','HEAD'),head)
        (self.repo/'notes.txt').write_text('User staged content');git(self.repo,'add','notes.txt')
        (self.repo/'notes.txt').write_text('User unstaged content')
        index=(self.repo/'.git'/'index').read_bytes()
        receipt=self.apply()
        self.assertEqual(git(self.repo,'rev-parse','HEAD'),head)
        self.assertEqual((self.repo/'.git'/'index').read_bytes(),index)
        self.assertEqual((self.repo/'app.py').read_bytes(),before)
        self.assertEqual((self.repo/'notes.txt').read_text(),'User unstaged content')
        self.assertEqual(git(self.repo,'show',receipt['branch']+':notes.txt'),b'User notes\n')
        self.assertIn(b'return a + b',git(self.repo,'show',receipt['branch']+':app.py'))
        self.assertEqual(git(self.repo,'show',receipt['branch']+':new.py'),b'# new module\n')
        self.assertEqual(git(self.repo,'rev-parse',receipt['branch']+'^'),head)
        self.assertEqual(self.apply(),receipt)
        self.assertEqual(review(Store(self.store.path),self.run['id'])['receipt'],receipt)

    def test_conflicts_and_stale_preview_never_publish(self):
        self.accept();preview=review(self.store,self.run['id'])
        (self.repo/'app.py').write_text('local work')
        self.error('APPLY_STALE',lambda:apply(self.store,self.run['id'],self.run['version'],preview['token']))
        changed=review(self.store,self.run['id'])
        self.assertIn('WORKTREE_CHANGED',[c['code'] for c in changed['conflicts']])
        self.error('APPLY_CONFLICT',lambda:self.apply())
        git(self.repo,'add','app.py')
        self.assertIn('INDEX_CHANGED',[c['code'] for c in review(self.store,self.run['id'])['conflicts']])
        self.assertEqual(git(self.repo,'for-each-ref','--format=%(refname)','refs/heads/kaibain'),b'')

    def test_changed_head_and_existing_branch_are_not_overwritten(self):
        self.accept();git(self.repo,'branch','kaibain/'+self.run['id'])
        self.assertIn('BRANCH_EXISTS',[c['code'] for c in review(self.store,self.run['id'])['conflicts']])
        self.error('APPLY_CONFLICT',lambda:self.apply())
        git(self.repo,'commit','--allow-empty','-qm','User new commit')
        self.assertIn('HEAD_CHANGED',[c['code'] for c in review(self.store,self.run['id'])['conflicts']])
        self.error('APPLY_CONFLICT',lambda:self.apply())

    def test_tampered_artifacts_report_and_frozen_files_are_rejected(self):
        self.accept()
        for path in (self.out/'manifest.json',self.out/'changes.diff',self.out/'files'/'app.py',self.out.parent/'report.json'):
            before=path.read_bytes();path.write_bytes(before+b' ')
            self.assertFalse(review(self.store,self.run['id'])['integrity'])
            self.error('ARTIFACT_CONFLICT',lambda:apply(self.store,self.run['id'],self.run['version'],'a'*64))
            path.write_bytes(before)
        # Even a self-consistent adapter export cannot expand the project write scope.
        file=self.out/'files'/'test_app.py';file.write_bytes(b'changed tests')
        manifest=json.loads((self.out/'manifest.json').read_bytes())
        entry=next(e for e in manifest['files'] if e['path']=='test_app.py');entry.update(bytes=13,sha256=digest(file.read_bytes()))
        raw=json.dumps(manifest).encode();(self.out/'manifest.json').write_bytes(raw)
        with self.store.connection(write=True,control=True) as db:
            result=json.loads(db.execute('SELECT result FROM agent_runs').fetchone()[0]);result['verification']['manifest_sha256']=digest(raw)
            result['verification'].pop('report_sha256');db.execute('UPDATE agent_runs SET result=?',(json.dumps(result),))
        self.assertIn('разрешённых',review(self.store,self.run['id'])['integrity_error'])

    def test_human_ownership_versions_and_post_acceptance_task_changes(self):
        self.accept();preview=review(self.store,self.run['id'])
        self.error('FORBIDDEN',lambda:apply(Store(self.store.path,source='agent'),self.run['id'],self.run['version'],preview['token']))
        self.error('CONFLICT',lambda:apply(self.store,self.run['id'],self.run['version']-1,preview['token']))
        agent=Store(self.store.path,source='agent');child=agent.get(self.run['result']['child_id'])
        agent.update(child['id'],child['version'],{'description':'Changed after review'})
        self.error('APPLY_STALE',lambda:self.apply())

    def test_crash_after_ref_publication_is_reconciled_without_duplicate_commit(self):
        self.accept();preview=review(self.store,self.run['id'])
        with patch.object(self.store,'_touch',side_effect=RuntimeError('simulated DB failure')):
            with self.assertRaises(RuntimeError):apply(self.store,self.run['id'],self.run['version'],preview['token'])
        commit=git(self.repo,'rev-parse','kaibain/'+self.run['id']).decode().strip()
        self.assertIsNone(review(self.store,self.run['id'])['receipt'])
        self.assertEqual(self.apply()['commit'],commit)
        self.assertEqual(git(self.repo,'rev-list','--count',commit).strip(),b'2')

    def test_repository_hooks_and_filters_never_execute_and_crlf_is_supported(self):
        self.accept();marker=self.repo/'EXECUTED'
        hook=self.repo/'.git'/'hooks'/'reference-transaction';hook.write_text('#!/bin/sh\necho BAD > "'+marker.as_posix()+'"\n');hook.chmod(0o755)
        git(self.repo,'config','filter.unsafe.clean','echo BAD > "'+marker.as_posix()+'"')
        (self.repo/'.git'/'info'/'attributes').write_text('*.py filter=unsafe\n')
        (self.repo/'app.py').write_bytes((self.repo/'app.py').read_bytes().replace(b'\n',b'\r\n'))
        self.apply();self.assertFalse(marker.exists())

    def test_linked_export_is_rejected(self):
        self.accept();file=self.out/'files'/'app.py';data=file.read_bytes();file.unlink()
        target=self.folder/'outside';target.write_bytes(data)
        try:file.symlink_to(target)
        except OSError:self.skipTest('Symlink creation not available on this host')
        self.assertFalse(review(self.store,self.run['id'])['integrity'])


if __name__=='__main__':unittest.main()
