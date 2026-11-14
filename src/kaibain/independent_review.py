"""Bind a model report to the exact export checked in a fresh microVM."""
import hashlib
import json

from .result_review import regular_bytes
from .store import Store,DomainError


def load_review(folder,profile):
    def sha(data):return hashlib.sha256(data).hexdigest()
    try:
        raw=regular_bytes(folder,'independent-review.json',20000)
        review=Store.validate_independent_review(json.loads(raw),profile)
        manifest=json.loads(regular_bytes(folder,'result/manifest.json',65536))
        clean=json.loads(regular_bytes(folder,'clean-check.json',65536))
        if (review.get('manifest_sha256')!=sha(json.dumps(manifest,sort_keys=True).encode()) or
                manifest.get('base_commit')!=profile['commit'] or manifest.get('deleted') or
                manifest.get('backend')!='microsandbox' or manifest.get('isolation')!='microvm' or
                clean.get('type')!='fresh_microvm_test_replay' or clean.get('checks',{}).get('exit_code')!=0 or
                clean['checks'].get('stop_reason') or clean.get('verified_patch_sha256')!=manifest.get('patch_sha256') or
                sha(regular_bytes(folder,'result/changes.diff'))!=manifest.get('patch_sha256')):
            raise ValueError('review context')
        entries=manifest['files']
        if not isinstance(entries,list) or not 1<=len(entries)<=40:raise ValueError('files')
        files={};hashes={};total=0
        for entry in entries:
            name=entry['path']
            if name not in profile['files']+profile['writable'] or name in files:raise ValueError('path')
            data=regular_bytes(folder,'result/files/'+name,262144);total+=len(data)
            if total>2097152 or len(data)!=entry['bytes'] or sha(data)!=entry['sha256']:raise ValueError('bytes')
            files[name]=data.decode('utf-8').splitlines();hashes[name]=sha(data)
        if not set(profile['files'])<=set(files) or clean.get('file_hashes')!=hashes:raise ValueError('clean export')
        for finding in review['findings']:
            lines=files[finding['path']]
            if not 1<=finding['line']<=len(lines) or finding['evidence'] not in lines[finding['line']-1]:
                raise ValueError('evidence')
        review['report_sha256']=sha(raw)
        return Store.validate_independent_review(review,profile)
    except (KeyError,ValueError,TypeError,UnicodeError,OSError):
        raise DomainError('INVALID_REVIEW','Отчёт ревью не соответствует экспорту и повторным тестам в чистой microVM.',409) from None
