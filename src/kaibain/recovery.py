"""Bounded, read-only validation of explicitly published checkpoint exports."""
import hashlib
import json
import os
import re

from .store import DomainError, Store
from .result_review import regular_bytes


def load_checkpoint(store, run, checkpoint):
    profile=Store.validate_project(run['project'])
    folder=store.path.parent/'.agent-runs'/checkpoint['run_id']/'development'/'checkpoints'/f"{checkpoint['sequence']:03d}"
    try:
        raw=regular_bytes(folder,'manifest.json',65536)
        if hashlib.sha256(raw).hexdigest()!=checkpoint['manifest_sha256']:
            raise ValueError('manifest')
        manifest=json.loads(raw)
        if (manifest['base_commit']!=profile['commit'] or manifest.get('deleted') or
                manifest.get('backend')!='microsandbox' or manifest.get('isolation')!='microvm'):
            raise ValueError('profile')
        patch=regular_bytes(folder,'changes.diff')
        if hashlib.sha256(patch).hexdigest()!=manifest['patch_sha256']:
            raise ValueError('diff')
        entries=manifest['files']
        if not isinstance(entries,list) or not 1<=len(entries)<=40:raise ValueError('files')
        seen=set();total=0
        for entry in entries:
            name=entry['path']
            if name not in set(profile['files']+profile['writable']) or name in seen:raise ValueError('path')
            data=regular_bytes(folder,'files/'+name,262144);total+=len(data)
            if total>2097152 or len(data)!=entry['bytes'] or hashlib.sha256(data).hexdigest()!=entry['sha256']:
                raise ValueError('bytes')
            seen.add(name)
        if not set(profile['files'])<=seen:raise ValueError('incomplete')
        return {'artifacts':str(folder),'verification':{'manifest_sha256':checkpoint['manifest_sha256']},
                'files':[{'path':e['path'],'bytes':e['bytes'],'sha256':e['sha256']} for e in entries]}
    except (DomainError,OSError,KeyError,ValueError,TypeError,UnicodeError):
        raise DomainError('CHECKPOINT_CONFLICT','Checkpoint повреждён, изменён или недоступен. Скрытый откат к более раннему снимку не выполняется.',409) from None


def failure_kind(status, code, stop_reason=''):
    code=code.lower() if isinstance(code,str) else ''
    if status=='cancelled':return 'stopped'
    if status not in ('failed','interrupted'):return None
    if code in ('review_rejected','invalid_review','ungrounded_review','review_missing','validator_same_family','validator_family_unknown'):return 'review'
    if code in ('time_budget','step_budget','token_budget','context_budget','tool_limit','supervisor_timeout') or stop_reason=='timeout':return 'budget'
    if code in ('checks_failed','verification_mutated'):return 'tests'
    if code in ('model_unavailable','model_output_limit','invalid_model_response','model_no_progress'):return 'model'
    if code in ('task_conflict','artifact_conflict','checkpoint_conflict','checkpoint_unavailable','recovery_stale','conflict','input_required','run_stopped'):return 'conflict'
    if status=='interrupted' or code in ('cleanup_failed','sandbox_error','runtime_version','runtime_start_failed','invalid_endpoint','policy_violation','invalid_job'):return 'environment'
    return 'execution'


def valid_code(code):
    return code if isinstance(code,str) and re.fullmatch('[A-Za-z_][A-Za-z_0-9]{0,79}',code) else 'executor_error'


def scope_inactive(store,run,*,folder=None):
    """Read a gated ownership receipt. Never signal a PID/group from a new owner."""
    folder=folder or store.path.parent/'.agent-runs'/run['id']
    # An unclaimed reservation is fenced by its terminal SQLite state; a late
    # supervisor cannot claim it and no execution gate was ever released.
    if run.get('pid') is None:return True
    try:
        receipt=json.loads(regular_bytes(folder,'scope.json',4096))
        if receipt=={'pid':None,'platform':os.name,'run_id':run['id'],'status':'not_started'}:return True
        if (receipt['run_id']!=run['id'] or receipt['platform']!=os.name or type(receipt['pid']) is not int or receipt['pid']<=1 or
                receipt['status'] not in ('active','closed')):raise ValueError()
    except (DomainError,ValueError,KeyError,TypeError,OSError):
        raise DomainError('RECOVERY_SCOPE_UNKNOWN','Нет подтверждённой записи о дереве процессов прошлой попытки. Автоматическое восстановление недоступно.',409) from None
    if receipt['status']=='closed':return True
    from .agent_runtime import process_alive
    if os.name=='nt':return not process_alive(receipt['pid'])
    try:os.killpg(receipt['pid'],0)
    except ProcessLookupError:return True
    except PermissionError:return False
    return False
