"""Optional local process bridge. LLM orchestration lives in the configured executor.

No shell commands or model settings are accepted from HTTP. A trusted local JSON
file configures a fixed argv; the browser may also supply a bounded project profile.
"""
from datetime import datetime, timezone, timedelta
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from uuid import uuid4

from .store import ROOT, Store, DomainError
from .process_scope import ProcessScope, ProcessCleanupError


def process_alive(pid):
    if not pid:
        return False
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5  # Access denied is not evidence of exit.
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class AgentRuntime:
    def __init__(self, store, config_path=None):
        self.store = store
        self.config_path = Path(config_path or ROOT/'.local'/'agent-runtime.json')
        self._diagnostic_lock=threading.Lock()
        self._diagnostic_cache={}

    def config(self):
        try:
            value = json.loads(self.config_path.read_text(encoding='utf-8'))
            command = value['command']
            if (value.get('kind')!='development-v1' or not isinstance(command, list) or len(command) < 2 or
                    not all(isinstance(s, str) and s and '\0' not in s for s in command) or
                    not all(Path(p).is_absolute() and Path(p).is_file() for p in command[:2]) or
                    not isinstance(value.get('label'), str) or not value['label'].strip()):
                raise ValueError()
            return value
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def recover(self):
        # The detached supervisor survives HTTP restarts. Never restart its work
        # automatically; after an OS crash explicitly record an interrupted run.
        with self.store.connection() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM agent_runs WHERE status IN ('queued','running','cancelling')")]
            cleanups=[dict(r) for r in db.execute("SELECT * FROM run_cleanups WHERE status='running'")]
        expired = (datetime.now(timezone.utc)-timedelta(seconds=30)).isoformat(timespec='microseconds')
        for row in rows:
            if ((row['status'] in ('running','cancelling') and not process_alive(row['pid'])) or
                    (row['status'] == 'queued' and row['created_at'] < expired)):
                self.store.finish_agent_run(row['id'], 'interrupted', 'Процесс прерван. Автоматического повторного запуска не было.',failure_code='supervisor_lost')
        for action in cleanups:
            if not process_alive(action['pid']):
                self.store.finish_run_cleanup(action['id'],'interrupted','Контроллер очистки прерван. Очистка не подтверждена; повторите её явно.')

    def snapshot(self):
        self.recover()
        config = self.config()
        runs=self.store.agent_runs()
        for run in runs:
            run['progress']=None
            if run['project']:
                try:
                    run['progress']=self.artifact(run['id'],'progress',limit=16000,as_json=True)
                except DomainError:pass
        return {'available': bool(config), 'provider': config['label'] if config else None,
                'diagnostics': bool(config and config.get('diagnostics')=='readiness-v1'),
                'kind': config.get('kind','text') if config else None,
                'message': '' if config else 'Встроенный sandbox-исполнитель не подключён. Подготовьте multi-agent-system по инструкции в настройках.',
                'runs': runs, 'mode': self.store.workspace()['mode'],
                'tasks': self.store.list()['tasks'], 'projects': self.store.projects()['projects']}

    def readiness(self, *, project=None, project_id=None, expected_project_version=None, fresh=False, config=None):
        from .readiness import item, probe
        from .git_project import resolve_commit
        if project is not None and project_id is not None:
            raise DomainError('VALIDATION','Укажите сохранённый проект или разовые параметры.')
        if project_id is None and expected_project_version is not None:
            raise DomainError('VALIDATION','Укажите project_id для версии проекта.')
        config=config if config is not None else self.config()
        checks=[item('runtime','Встроенный исполнитель','passed' if config else 'failed',
                     config['label'] if config else 'Sandbox-исполнитель не подключён.',
                     '' if config else 'Подготовьте multi-agent-system и подключите его по инструкции в настройках.')]
        reference=None;profile=None
        try:
            if project_id is not None:
                resolved=self.store.resolve_project(project_id,expected_project_version)
                profile,reference=resolved['project'],resolved['project_ref']
            elif project is not None:
                profile=self.store.validate_project(project)
                profile['commit']=resolve_commit(profile['repo'],profile['commit'])
            if profile is not None:
                checks.append(item('project','Исходный коммит','passed','Репозиторий и коммит доступны.',commit=profile['commit']))
        except DomainError as error:
            if error.code in ('VALIDATION','PROJECT_CONFLICT','PROJECT_NOT_FOUND'):
                raise
            checks.append(item('project','Исходный коммит','failed',error.message,
                               'Проверьте путь, выбранный коммит и состояние архива проекта.',code=error.code))
            profile=None
        if config:
            key=hashlib.sha256(json.dumps([config,profile],sort_keys=True).encode()).hexdigest()
            if not self._diagnostic_lock.acquire(blocking=False):
                raise DomainError('READINESS_BUSY','Уже выполняется проверка окружения. Повторите через несколько секунд.',409)
            try:
                cached=self._diagnostic_cache.get(key)
                if not fresh and cached and time.monotonic()-cached[0]<15:
                    environment,checked_at=cached[1:]
                else:
                    environment=probe(config,profile)
                    checked_at=datetime.now(timezone.utc).isoformat()
                    self._diagnostic_cache={key:(time.monotonic(),environment,checked_at)}
                checks.extend(environment)
            finally:
                self._diagnostic_lock.release()
        else:
            checked_at=datetime.now(timezone.utc).isoformat()
        return {'ready':all(row['status']=='passed' for row in checks), 'checked_at':checked_at,
                'checks':checks, 'scope':'project' if project_id is not None or project is not None else 'runtime',
                'project_ref':reference, 'source_commit':profile['commit'] if profile else None}

    def artifact(self,run_id,name,*,limit=2097152,as_json=False):
        with self.store.connection() as db:
            run=self.store._run(db,run_id)
        if not run['project']:
            raise DomainError('NOT_FOUND','У этого прогона нет файлов разработки.',404)
        names={'diff':'development/result/changes.diff','manifest':'development/result/manifest.json',
               'report':'development/report.json','log':'executor.log','progress':'progress.json','dialogue':'dialogue.json',
               'independent-review':'development/independent-review.json'}
        if name not in names:
            raise DomainError('NOT_FOUND','Артефакт не найден.',404)
        folder=(self.store.path.parent/'.agent-runs'/run_id).resolve()
        if name=='report' and not (folder/names['report']).is_file():
            names['report']='report.json'
        path=folder/names[name]
        try:
            if not path.resolve().is_relative_to(folder) or path.is_symlink() or path.stat().st_size>limit:
                raise ValueError()
            text=path.read_text(encoding='utf-8')
            return json.loads(text) if as_json else text.encode('utf-8')
        except (OSError,ValueError,UnicodeError):
            raise DomainError('NOT_FOUND','Артефакт ещё не создан или недоступен.',404) from None

    def revise(self, run_id, expected_version, expected_task_version):
        with self.store.connection() as db:
            previous = self.store._run(db, run_id)
        return self.start(previous['task_id'], expected_task_version,
                          revision_of=run_id, expected_review_version=expected_version,
                          project=json.loads(previous['project']) if previous['project'] else None)

    def continue_run(self,run_id,expected_version,expected_task_version):
        with self.store.connection() as db:
            previous=self.store._run(db,run_id)
            self.store._check_version(self.store._public_run(previous),expected_version)
            if previous['status']!='waiting_for_input':
                raise DomainError('CONTINUATION_STALE','Прогон не ожидает продолжения диалога.',409)
            question=dict(db.execute('SELECT * FROM goal_questions WHERE id=?',(previous['question_id'],)).fetchone())
            checkpoint=self.store._continuation_checkpoint(db,previous,question,self.store._row(db,previous['task_id'])) if question['status']=='answered' else None
        if checkpoint:
            from .recovery import load_checkpoint,scope_inactive
            if not scope_inactive(self.store,previous):raise DomainError('RECOVERY_PROCESS_ALIVE','Предыдущая попытка ещё не освободила процессы.',409)
            load_checkpoint(self.store,self.store._public_run(previous),checkpoint)
        return self.start(previous['task_id'],expected_task_version,continuation_of=run_id,
                          expected_pause_version=expected_version,
                          project=json.loads(previous['project']) if previous['project'] else None)

    def answer(self,task_id,*,continue_run=True,**fields):
        if type(continue_run) is not bool:
            raise DomainError('VALIDATION','continue_run должен быть boolean.')
        dialogue=self.store.answer_question(task_id,**fields)
        result={'dialogue':dialogue,'run':None,'continuation_error':None}
        if continue_run and dialogue['continuation']:
            previous=dialogue['continuation']
            try:result['run']=self.continue_run(previous['id'],previous['version'],dialogue['task_version'])
            except DomainError as error:result['continuation_error']=error.result()['error']
        return result

    def recovery_preview(self,run_id):
        from .recovery import load_checkpoint,scope_inactive
        with self.store.connection() as db:
            run=self.store._public_run(self.store._run(db,run_id))
            checkpoint=self.store._checkpoint_for_run(db,run_id)
            root=db.execute('SELECT version FROM tasks WHERE id=?',(run['task_id'],)).fetchone()
            result={'run_id':run_id,'run_version':run['version'],'task_version':root[0] if root else None,
                    'checkpoint':checkpoint,'recoverable':False,'files':[],'error':None}
            try:
                self.store.recovery_context(db,run_id,run['version'],result['task_version'],checkpoint['id'] if checkpoint else None)
            except DomainError as error:
                result['error']=error.result()['error'];return result
        try:
            result['files']=load_checkpoint(self.store,run,checkpoint)['files']
            config=self.config()
            if not config or config.get('recovery')!='checkpoint-v1':
                raise DomainError('RECOVERY_UNSUPPORTED','Обновите возможности подключённого адаптера: run_development_web.py --enable-recovery. Его argv и модели сохранятся.',409)
            with self.store.connection() as db:original=self.store._run(db,run_id)
            if config['command']!=json.loads(original['command']):
                raise DomainError('RECOVERY_RUNTIME_CHANGED','Настройки исполнителя отличаются от исходной попытки. Восстановление не меняет модели и лимиты скрыто.',409)
            if not scope_inactive(self.store,original):
                raise DomainError('RECOVERY_PROCESS_ALIVE','Дерево процессов прошлой попытки ещё не завершилось. Ничего не запускаем.',409)
            result['recoverable']=True
        except DomainError as error:result['error']=error.result()['error']
        return result

    def cleanup_status(self,run_id):
        self.recover()
        return self.store.run_cleanup_status(run_id)

    def cleanup_run(self,run_id,expected_version):
        """Human-only owned cleanup; no snapshot import, inference or new run."""
        from .recovery import scope_inactive
        from .result_review import regular_bytes
        if self.store.source!='user':raise DomainError('FORBIDDEN','Очистку среды запрашивает пользователь.',403)
        self.recover()
        with self.store.connection() as db:
            row=self.store.cleanup_context(db,run_id,expected_version)
            previous=db.execute('SELECT * FROM run_cleanups WHERE run_id=? ORDER BY rowid DESC LIMIT 1',(run_id,)).fetchone()
        folder=self.store.path.parent/'.agent-runs'/run_id
        if process_alive(row['pid']) or not scope_inactive(self.store,row):
            raise DomainError('CLEANUP_PROCESS_ALIVE','Прежнее дерево процессов ещё существует; очистка не завершает его по сохранённому PID.',409)
        if previous and not scope_inactive(self.store,{'id':run_id,'pid':previous['pid']},folder=folder/'operator-cleanups'/previous['id']):
            raise DomainError('CLEANUP_PROCESS_ALIVE','Предыдущий процесс очистки ещё существует. Дождитесь его завершения.',409)
        operator_folder=folder/'operator-cleanups'
        if any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in (operator_folder,*operator_folder.parents)):
            raise DomainError('CLEANUP_UNAVAILABLE','Каталог прогона заменён ссылкой.',409)
        command=json.loads(row['command']);identifier=str(uuid4())
        action_folder=operator_folder/identifier;action_folder.mkdir(parents=True,exist_ok=False)
        # Persist a closed gate before the SQLite lease: every durable action
        # therefore has evidence even if its owner dies before spawning.
        scope_path=action_folder/'scope.json'
        scope_path.write_text(json.dumps({'pid':None,'platform':os.name,'run_id':run_id,'status':'not_started'}),encoding='utf8')
        action=self.store.begin_run_cleanup(run_id,expected_version,os.getpid(),cleanup_id=identifier)
        try:
            # Invalidate an older acknowledgement before invoking the original
            # trusted argv; a zero exit without a fresh receipt is not success.
            ack=folder/'cleanup.json'
            if ack.is_symlink() or getattr(ack,'is_junction',lambda:False)():raise ProcessCleanupError('Квитанция очистки заменена ссылкой.')
            ack.write_text(json.dumps({'ok':False,'operator_cleanup_id':identifier}),encoding='utf8')
            with (action_folder/'cleanup.log').open('ab') as log:
                cleanup_owned([*command,'--cleanup','--output',str(folder/'report.json')],folder,log,
                    receipt=scope_path,scope_id=run_id)
            value=json.loads(regular_bytes(folder,'cleanup.json',4096))
            if (not isinstance(value,dict) or value.get('ok') is not True or
                    type(value.get('records_checked')) is not int or value['records_checked']<0):
                raise ProcessCleanupError('Исполнитель не оставил подтверждение очистки.')
            if not scope_inactive(self.store,{'id':run_id,'pid':os.getpid()},folder=action_folder):
                raise ProcessCleanupError('Не подтверждено закрытие процесса очистки.')
            return self.store.finish_run_cleanup(identifier,'completed',receipt=value)
        except BaseException as error:
            self.store.finish_run_cleanup(identifier,'failed',str(error) or type(error).__name__)
            if isinstance(error,(KeyboardInterrupt,SystemExit)):raise
            raise DomainError('CLEANUP_FAILED','Очистка среды не подтверждена. Сохранённые файлы не изменены; проверьте состояние перед повтором.',409) from error

    def resume(self,run_id,expected_version,expected_task_version,checkpoint_id):
        from .recovery import load_checkpoint,scope_inactive
        self.recover()
        with self.store.connection() as db:
            run,checkpoint=self.store.recovery_context(db,run_id,expected_version,expected_task_version,checkpoint_id)
            ancestors=[];cursor=self.store._run(db,run_id)
            while cursor:
                if len(ancestors)>=100:raise DomainError('RECOVERY_LIMIT','История восстановления превысила лимит.',409)
                ancestors.append(cursor)
                cursor=self.store._run(db,cursor['recovery_of']) if cursor['recovery_of'] else None
        config=self.config()
        if not config or config.get('recovery')!='checkpoint-v1':
            raise DomainError('RECOVERY_UNSUPPORTED','Подключённый исполнитель не объявил checkpoint-v1.',409)
        if config['command']!=json.loads(ancestors[0]['command']):
            raise DomainError('RECOVERY_RUNTIME_CHANGED','Настройки исполнителя изменились после исходного запуска.',409)
        load_checkpoint(self.store,run,checkpoint)
        cleanup_deadline=time.monotonic()+30
        for previous in ancestors:
            if previous['status'] not in ('failed','interrupted','cancelled') or process_alive(previous['pid']):
                raise DomainError('RECOVERY_PROCESS_ALIVE','Предыдущий процесс ещё существует. Восстановление не завершает посторонние процессы.',409)
            folder=self.store.path.parent/'.agent-runs'/previous['id']
            if not scope_inactive(self.store,previous):
                raise DomainError('RECOVERY_PROCESS_ALIVE','Старое дерево процессов ещё существует. Дождитесь его завершения; новая попытка не создана.',409)
            try:
                with (folder/'recovery-cleanup.log').open('ab') as log:
                    remaining=cleanup_deadline-time.monotonic()
                    if remaining<=0:raise ProcessCleanupError('Cleanup deadline')
                    cleanup_owned([*json.loads(previous['command']),'--cleanup','--output',str(folder/'report.json')],folder,log,timeout=remaining)
            except (ProcessCleanupError,OSError):
                raise DomainError('RECOVERY_CLEANUP_FAILED','Не подтверждена очистка принадлежащих прошлой попытке microVM. Новая попытка не создана.',409) from None
        # start repeats readiness and the atomic version/one-run checks after cleanup.
        return self.start(run['task_id'],expected_task_version,project=run['project'],recovery_of=run_id,
                          expected_recovery_version=expected_version,recovery_checkpoint_id=checkpoint_id)

    def start(self, task_id, expected_version, *, revision_of=None, expected_review_version=None, project=None,
              project_id=None, expected_project_version=None,continuation_of=None,expected_pause_version=None,
              recovery_of=None,expected_recovery_version=None,recovery_checkpoint_id=None):
        self.recover()
        config = self.config()
        if not config:
            raise DomainError('RUNTIME_UNAVAILABLE', 'Локальный исполнитель не настроен или его файлы недоступны.', 503)
        project_ref = None
        if project_id is not None:
            if project is not None or revision_of or continuation_of or recovery_of:
                raise DomainError('VALIDATION', 'Выберите сохранённый проект или разовые параметры запуска.')
            resolved = self.store.resolve_project(project_id, expected_project_version)
            project, project_ref = resolved['project'], resolved['project_ref']
        elif expected_project_version is not None:
            raise DomainError('VALIDATION', 'Укажите project_id для версии проекта.')
        if (config.get('kind')=='development-v1') != (project is not None):
            raise DomainError('VALIDATION','Для исполнителя разработки укажите параметры проекта; текстовый исполнитель их не принимает.')
        if config.get('diagnostics')=='readiness-v1':
            readiness=self.readiness(project=project,fresh=True,config=config)
            if not readiness['ready']:
                failed=next(row for row in readiness['checks'] if row['status']!='passed')
                raise DomainError('RUNTIME_NOT_READY',failed['message'],409,readiness=readiness)
        run = self.store.reserve_agent_run(task_id, expected_version, config['label'], config['command'],
                                           revision_of=revision_of, expected_review_version=expected_review_version,
                                           project=project, project_ref=project_ref,
                                           continuation_of=continuation_of,expected_pause_version=expected_pause_version,
                                           recovery_of=recovery_of,expected_recovery_version=expected_recovery_version,recovery_checkpoint_id=recovery_checkpoint_id)
        folder = self.store.path.parent/'.agent-runs'/run['id']
        try:
            folder.mkdir(parents=True, exist_ok=True)
            options = {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS} if os.name == 'nt' else {'start_new_session': True}
            environment = {**os.environ, 'PYTHONUTF8': '1', 'LANGSMITH_TRACING': 'false', 'LANGCHAIN_TRACING_V2': 'false'}
            environment.pop('KAIBAIN_CONTROL_TOKEN', None)
            with (folder/'supervisor.log').open('ab') as log:
                process = subprocess.Popen([sys.executable, str(ROOT/'run.py'), 'agent-job', '--db', str(self.store.path), '--run-id', run['id']],
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=log, cwd=ROOT,
                                 env=environment, **options)
            # Reap while the server lives, without tying the job to its lifetime.
            threading.Thread(target=process.wait, daemon=True).start()
        except Exception:
            self.store.finish_agent_run(run['id'], 'failed', 'Не удалось запустить локальный процесс. Проверьте конфигурацию исполнителя.',failure_code='runtime_start_failed')
            raise DomainError('RUNTIME_START_FAILED', 'Не удалось запустить локального исполнителя.', 503) from None
        return run


def run_job(store, run_id):
    """Detached supervisor owns the child, timeout and durable completion record."""
    try:
        row = store.claim_agent_run(run_id, os.getpid())
    except Exception as exc:
        # A second supervisor must never mark someone else's running job failed.
        store.finish_agent_run(run_id, 'failed', str(exc), only_status='queued')
        return
    failure_code='executor_error'
    independent_review=None
    execution_entered=False
    adapter_cleanup_finished=False
    try:
        folder = store.path.parent/'.agent-runs'/run_id
        folder.mkdir(parents=True, exist_ok=True)
        # Recorded before any adapter can be released; ProcessScope atomically
        # replaces this with its active ownership receipt before opening the gate.
        initial_scope=folder/'scope.json'
        initial_scope.write_text(json.dumps({'pid':None,'platform':os.name,'run_id':run_id,'status':'not_started'}),encoding='utf-8')
        report_path = folder/'report.json'
        command = json.loads(row['command'])
        argv = [*command, '--db', str(store.path), '--task-id', row['task_id'],
                '--expected-version', str(row['task_version']), '--output', str(report_path)]
        if row['project']:
            project_path=folder/'project.json'
            project_path.write_text(row['project'],encoding='utf-8')
            argv.extend(['--project-input',str(project_path)])
        if row['revision_of']:
            with store.connection() as db:
                previous = store._public_run(store._run(db, row['revision_of']))
            revision = {'previous_run_id': previous['id'], 'feedback': previous['review_comment'],
                        'previous_result': {'summary': previous['result']['summary'], 'checks': previous['result']['checks']},
                        'child_id': previous['result']['child_id'],
                        'expected_child_version': previous['review_versions'][previous['result']['child_id']]}
            if row['project']:
                revision['artifacts']=str(store.path.parent/'.agent-runs'/previous['id']/'development'/'result')
                revision['verification']=previous['result'].get('verification',{})
            revision_path = folder/'revision.json'
            revision_path.write_text(json.dumps(revision, ensure_ascii=False, indent=2), encoding='utf-8')
            argv.extend(['--revision-input', str(revision_path)])
        if row['recovery_of'] or row['continuation_of'] and row['recovery_checkpoint_id']:
            from .recovery import load_checkpoint
            with store.connection() as db:
                previous=store._public_run(store._run(db,row['recovery_of'] or row['continuation_of']))
                checkpoint=dict(db.execute('SELECT * FROM run_checkpoints WHERE id=?',(row['recovery_checkpoint_id'],)).fetchone())
            recovery={**load_checkpoint(store,previous,checkpoint),'previous_run_id':previous['id'],
                      'checkpoint_id':checkpoint['id'],'child_id':checkpoint['child_id'],
                      'expected_child_version':checkpoint['child_version']}
            recovery.pop('files')
            if previous.get('independent_review'):
                recovery['review_feedback']={k:previous['independent_review'][k] for k in ('summary','findings')}
            path=folder/'recovery.json';path.write_text(json.dumps(recovery,ensure_ascii=False),encoding='utf-8')
            argv.extend(['--recovery-input' if row['recovery_of'] else '--continuation-input',str(path)])
        with (folder/'executor.log').open('wb') as log:
            try:
                execution_entered=True
                returncode = execute_owned(store, row, argv, folder, log,grace=15 if row['project'] else 3)
            finally:
                if row['project']:
                    cleanup_owned([*command,'--cleanup','--output',str(report_path)],folder,log)
                    adapter_cleanup_finished=True
        with store.connection() as db:
            current = store._run(db, run_id)
        if current['status'] == 'cancelling':
            store.finish_agent_run(run_id, 'cancelled')
            return
        report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {}
        failure_code=report.get('error_code','executor_error')
        if (folder/'development'/'independent-review.json').exists():
            from .independent_review import load_review
            independent_review=load_review(folder/'development',json.loads(row['project']) if row['project'] else None)
        if returncode or not report.get('ok') or report.get('root_id') != row['task_id']:
            raise RuntimeError(report.get('error') or 'Исполнитель завершился без результата. См. executor.log в .agent-runs рядом с базой.')
        if report.get('status')=='waiting_for_input':
            store.finish_agent_run(run_id,'waiting_for_input',question_id=report.get('question_id'))
            return
        result = {'child_id': report['child_id'], 'summary': report['result']['summary'],
                  'checks': report['result']['checks'], 'inference': report['inference']}
        if row['project']:
            if report.get('verification',{}).get('status')!='passed':
                raise RuntimeError('Исполнитель разработки не подтвердил успешный повтор тестов.')
            result['verification']=report['verification']
            evidence = folder/'development'/'report.json'
            if evidence.is_file() and evidence.stat().st_size <= 2097152:
                result['verification']['report_sha256'] = hashlib.sha256(evidence.read_bytes()).hexdigest()
        # The executor captures versions of the writes it performed, not an
        # arbitrary later read that could already contain somebody else's edits.
        if isinstance(report.get('task_versions'), dict):
            result['task_versions'] = report['task_versions']
        if report.get('handoff')=='atomic-review-v1':result['handoff']='atomic-review-v1'
        result['requirements_version']=report.get('requirements_version',row['requirements_version'])
        store.finish_agent_run(run_id, 'completed', result=result,independent_review=independent_review)
    except ProcessCleanupError as exc:
        store.finish_agent_run(run_id, 'interrupted', str(exc),failure_code='cleanup_failed')
    except (KeyboardInterrupt,SystemExit) as exc:
        host_closed=not execution_entered or closed_owned_scope(store.path.parent/'.agent-runs'/run_id,run_id)
        environment_closed=not execution_entered or not row['project'] or adapter_cleanup_finished
        confirmed=host_closed and environment_closed
        text=('Локальный supervisor прерван ('+type(exc).__name__+'). '
              +('Дочерние ресурсы ещё не запускались. ' if not execution_entered else
                'Очистка собственных ресурсов подтверждена. ' if confirmed else 'Очистка собственных ресурсов не подтверждена. ')
              +'Автоматического повторного запуска не было.')
        store.finish_agent_run(run_id,'interrupted',text,
                               failure_code='supervisor_interrupted' if confirmed else 'cleanup_failed',
                               preserve_pending_question=confirmed and execution_entered and bool(row['project']))
        raise
    except Exception as exc:
        store.finish_agent_run(run_id, 'failed', str(exc) or type(exc).__name__,failure_code=exc.code if isinstance(exc,DomainError) else failure_code,independent_review=independent_review,
                               preserve_pending_question=adapter_cleanup_finished and closed_owned_scope(store.path.parent/'.agent-runs'/run_id,run_id))


def closed_owned_scope(folder,run_id):
    """Read this owner's gated receipt; never inspect or signal a foreign PID."""
    from .result_review import regular_bytes
    try:receipt=json.loads(regular_bytes(folder,'scope.json',4096))
    except (DomainError,ValueError,TypeError):return False
    if not isinstance(receipt,dict) or set(receipt)!={'pid','platform','run_id','status'}:return False
    if receipt['platform']!=os.name or receipt['run_id']!=run_id:return False
    return ((receipt['status']=='not_started' and receipt['pid'] is None) or
            (receipt['status']=='closed' and type(receipt['pid']) is int and receipt['pid']>1))


def cleanup_owned(argv,folder,log,*,timeout=30,receipt=None,scope_id=None):
    """The frozen external adapter cleans its owned resources after any exit/kill."""
    environment={**os.environ,'PYTHONUTF8':'1'}
    environment.pop('KAIBAIN_CONTROL_TOKEN',None)
    if scope_id is not None:environment['KAIBAIN_RUN_ID']=scope_id
    scope=None
    try:
        scope=ProcessScope(argv,log=log,cwd=str(Path(argv[1]).parent),env=environment,
            **({'receipt':receipt} if receipt is not None else {}))
        try:code=scope.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise ProcessCleanupError('Очистка среды исполнителем не завершилась за 30 секунд.') from None
        if code:
            raise ProcessCleanupError('Исполнитель не подтвердил очистку microVM. См. executor.log.')
    except ProcessCleanupError:raise
    except Exception as error:
        raise ProcessCleanupError('Не удалось выполнить очистку среды исполнителя: '+str(error)[:600]) from error
    finally:
        if scope:
            try:scope.close()
            except ProcessCleanupError:raise
            except Exception as error:
                raise ProcessCleanupError('Не подтверждено закрытие процесса очистки: '+str(error)[:600]) from error


def execute_owned(store, row, argv, folder, log, *, timeout=600, grace=3):
    """Only this live supervisor can terminate its own execution scope."""
    cancel_file = folder/'cancel.request'
    environment = {**os.environ, 'KAIBAIN_RUN_ID': row['id'], 'KAIBAIN_CANCEL_FILE': str(cancel_file)}
    environment.pop('KAIBAIN_CONTROL_TOKEN', None)
    scope = ProcessScope(argv, log=log, cwd=str(Path(argv[1]).parent), env=environment,receipt=folder/'scope.json')
    started, stopping = time.monotonic(), None
    try:
        while True:
            with store.connection() as db:
                current = store._run(db, row['id'])
            if current['status'] == 'cancelling':
                if stopping is None:
                    stopping = time.monotonic()
                    cancel_file.write_text(current['stop_reason'], encoding='ascii')
                if time.monotonic()-stopping >= grace:
                    break
            elif current['status'] == 'running' and time.monotonic()-started >= timeout:
                try:
                    store.stop_agent_run(row['id'], current['version'], reason='timeout')
                except DomainError as error:
                    if error.code != 'CONFLICT': raise
                continue  # Reconcile a concurrent user stop without overwriting it.
            if scope.process.poll() is not None:
                break
            time.sleep(.1)
    finally:
        # Also clean descendants left by an adapter that exited on its own.
        scope.close()
    return scope.process.returncode
