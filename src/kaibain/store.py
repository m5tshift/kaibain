"""Transactional domain layer. Every process opens the same local SQLite file."""
import os
import re
import sqlite3
import hashlib
import hmac
import json
import math
from contextlib import closing, contextmanager
from datetime import date, datetime, timezone, timedelta
from pathlib import Path, PurePosixPath
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
STATUSES = ('planned', 'in_progress', 'review', 'done')
AGENT_PHASES = ('idle','working','preparing','planning','reading','editing','checking','verifying',
                'waiting','waiting_for_input','blocked','review','done','error')


class DomainError(Exception):
    def __init__(self, code, message, status=400, **details):
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details

    def result(self):
        return {'ok': False, 'error': {'code': self.code, 'message': self.message, **self.details}}


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def valid_date(value):
    if value is None:
        return
    try:
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError()
        date.fromisoformat(value)
    except ValueError:
        raise DomainError('VALIDATION', 'Дедлайн должен быть датой YYYY-MM-DD или null.') from None


class Store:
    def __init__(self, path=None, *, source='user'):
        if source not in ('user', 'agent'):
            raise ValueError('Unknown operation source')
        self.source = source
        self.run_id = os.environ.get('KAIBAIN_RUN_ID') if source == 'agent' else None
        self.path = Path(path or os.environ.get('KAIBAIN_DB', ROOT / 'data' / 'kaibain.sqlite3')).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL CHECK(status IN ('planned','in_progress','review','done')),
                    deadline TEXT,
                    parent_id TEXT REFERENCES tasks(id) ON DELETE RESTRICT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS tasks_parent ON tasks(parent_id);
                CREATE INDEX IF NOT EXISTS tasks_deadline ON tasks(deadline);
                CREATE TABLE IF NOT EXISTS meta (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL);
                INSERT OR IGNORE INTO meta VALUES (1, 0);
            ''')
            # SQLite cannot alter CHECK constraints. Rebuild the legacy table
            # atomically, keeping IDs, links, versions and timestamps unchanged.
            # This initialization connection has foreign_keys disabled; ordinary
            # domain connections always enforce them. Recheck under the write lock
            # so simultaneous web/MCP startups migrate at most once.
            with db:
                db.execute('BEGIN IMMEDIATE')
                schema = db.execute("SELECT sql FROM sqlite_master WHERE name='tasks'").fetchone()[0]
                if "'review'" not in schema:
                    db.execute('''CREATE TABLE tasks_review (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        description TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL CHECK(status IN ('planned','in_progress','review','done')),
                        deadline TEXT,
                        parent_id TEXT REFERENCES tasks(id) ON DELETE RESTRICT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        version INTEGER NOT NULL DEFAULT 1
                    )''')
                    db.execute('INSERT INTO tasks_review SELECT * FROM tasks')
                    db.execute('DROP TABLE tasks')
                    db.execute('ALTER TABLE tasks_review RENAME TO tasks')
                    db.execute('CREATE INDEX tasks_parent ON tasks(parent_id)')
                    db.execute('CREATE INDEX tasks_deadline ON tasks(deadline)')
                    if db.execute('PRAGMA foreign_key_check').fetchone():
                        raise RuntimeError('Migration aborted: invalid task parent references.')
                # Remove the retired taxonomy from existing databases too, including
                # restored backups. Goals are organized through projects now.
                if 'tag_id' in {r[1] for r in db.execute('PRAGMA table_info(tasks)')}:
                    db.execute('ALTER TABLE tasks DROP COLUMN tag_id')
                db.execute('DROP TABLE IF EXISTS tags')
                db.execute("""CREATE TABLE IF NOT EXISTS workspace (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    mode TEXT NOT NULL CHECK(mode IN ('manual','agent')),
                    mode_version INTEGER NOT NULL
                )""")
                db.execute("INSERT OR IGNORE INTO workspace VALUES (1,'manual',1)")
                db.execute('''CREATE TABLE IF NOT EXISTS agents (
                        id TEXT PRIMARY KEY, owner_hash TEXT NOT NULL,
                        name TEXT NOT NULL, role TEXT NOT NULL, model TEXT NOT NULL,
                        framework TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'available',
                        action TEXT NOT NULL DEFAULT '', task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
                        coordinator_id TEXT REFERENCES agents(id),
                        registered_at TEXT NOT NULL, last_seen TEXT NOT NULL,
                        disconnected INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1
                    )''')
                db.execute('''CREATE TABLE IF NOT EXISTS agent_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT NOT NULL REFERENCES agents(id),
                        kind TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL
                    )''')
                db.execute('''CREATE TABLE IF NOT EXISTS agent_runs (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, task_title TEXT NOT NULL,
                    task_version INTEGER NOT NULL, provider TEXT NOT NULL, command TEXT NOT NULL,
                    status TEXT NOT NULL, pid INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '', result TEXT
                )''')
                run_columns = {r[1] for r in db.execute('PRAGMA table_info(agent_runs)')}
                for name, definition in {
                    'version': 'INTEGER NOT NULL DEFAULT 1',
                    'decision': "TEXT NOT NULL DEFAULT 'pending'",
                    'review_comment': "TEXT NOT NULL DEFAULT ''",
                    'reviewed_at': 'TEXT', 'review_versions': 'TEXT', 'revision_of': 'TEXT',
                    'stop_supported': 'INTEGER NOT NULL DEFAULT 0', 'stop_reason': "TEXT NOT NULL DEFAULT ''",
                    'project': 'TEXT',
                }.items():
                    if name not in run_columns:
                        db.execute(f'ALTER TABLE agent_runs ADD COLUMN {name} {definition}')
                db.execute('CREATE INDEX IF NOT EXISTS agent_runs_task ON agent_runs(task_id,created_at)')
                db.execute('''CREATE TABLE IF NOT EXISTS run_cleanups (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, status TEXT NOT NULL,
                    pid INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '', receipt TEXT)''')
                db.execute('CREATE INDEX IF NOT EXISTS run_cleanups_run ON run_cleanups(run_id,created_at)')
                if 'project_ref' not in run_columns:
                    db.execute('ALTER TABLE agent_runs ADD COLUMN project_ref TEXT')
                db.execute('''CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
                    profile TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                )''')
                db.execute('''CREATE TABLE IF NOT EXISTS goal_projects (
                    task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
                    project_id TEXT NOT NULL REFERENCES projects(id)
                )''')
                goal_result_columns='''(
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                    task_version INTEGER NOT NULL, summary TEXT NOT NULL, checks TEXT NOT NULL,
                    created_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
                    decision TEXT NOT NULL DEFAULT 'pending', comment TEXT NOT NULL DEFAULT ''
                )'''
                db.execute('CREATE TABLE IF NOT EXISTS goal_results '+goal_result_columns)
                if db.execute('PRAGMA foreign_key_list(goal_results)').fetchone():
                    # Keep report history even when an explicitly deleted goal is gone.
                    db.execute('CREATE TABLE goal_results_native '+goal_result_columns)
                    db.execute('INSERT INTO goal_results_native SELECT * FROM goal_results ORDER BY rowid')
                    db.execute('DROP TABLE goal_results')
                    db.execute('ALTER TABLE goal_results_native RENAME TO goal_results')
                db.execute('CREATE INDEX IF NOT EXISTS goal_results_task ON goal_results(task_id,created_at)')
                if 'run_id' not in {r[1] for r in db.execute('PRAGMA table_info(agents)')}:
                    db.execute('ALTER TABLE agents ADD COLUMN run_id TEXT')
                for name, definition in {'continuation_of':'TEXT','question_id':'TEXT',
                                         'requirements_version':'INTEGER NOT NULL DEFAULT 0'}.items():
                    if name not in run_columns:
                        db.execute(f'ALTER TABLE agent_runs ADD COLUMN {name} {definition}')
                db.execute('''CREATE TABLE IF NOT EXISTS goal_dialogues (
                    task_id TEXT PRIMARY KEY, requirements_version INTEGER NOT NULL DEFAULT 0)''')
                db.execute('''CREATE TABLE IF NOT EXISTS goal_questions (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, run_id TEXT, agent_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL, question TEXT NOT NULL, answer TEXT,
                    status TEXT NOT NULL DEFAULT 'open', version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL, answered_at TEXT)''')
                db.execute("CREATE UNIQUE INDEX IF NOT EXISTS goal_question_open ON goal_questions(task_id) WHERE status='open'")
                db.execute('CREATE INDEX IF NOT EXISTS goal_question_task ON goal_questions(task_id,created_at,id)')
                if 'checkpoint_id' not in {r[1] for r in db.execute('PRAGMA table_info(goal_questions)')}:
                    db.execute('ALTER TABLE goal_questions ADD COLUMN checkpoint_id TEXT')
                db.execute('''CREATE TABLE IF NOT EXISTS run_applications (
                    run_id TEXT PRIMARY KEY, receipt TEXT NOT NULL)''')
                agent_columns = {r[1] for r in db.execute('PRAGMA table_info(agents)')}
                for name, definition in {'activity_phase':"TEXT NOT NULL DEFAULT 'idle'",
                                         'phase_started_at':'TEXT', 'budget':'TEXT'}.items():
                    if name not in agent_columns:
                        db.execute(f'ALTER TABLE agents ADD COLUMN {name} {definition}')
                if 'metadata' not in {r[1] for r in db.execute('PRAGMA table_info(agent_events)')}:
                    db.execute("ALTER TABLE agent_events ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")
                db.execute('''CREATE TABLE IF NOT EXISTS agent_links (
                    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES agents(id),
                    target_id TEXT NOT NULL REFERENCES agents(id), kind TEXT NOT NULL,
                    source_task_id TEXT NOT NULL, target_task_id TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL,
                    message TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1)''')
                for name in ('recovery_of','recovery_checkpoint_id','failure_code','independent_review'):
                    if name not in run_columns:db.execute(f'ALTER TABLE agent_runs ADD COLUMN {name} TEXT')
                db.execute('''CREATE TABLE IF NOT EXISTS run_checkpoints (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES agent_runs(id),
                    sequence INTEGER NOT NULL, manifest_sha256 TEXT NOT NULL, task_version INTEGER NOT NULL,
                    child_id TEXT NOT NULL, child_version INTEGER NOT NULL, requirements_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL, UNIQUE(run_id,sequence))''')

    @staticmethod
    def _public_run(row):
        value = dict(row)
        value.pop('command', None)
        value.pop('pid', None)
        value['can_stop'] = bool(value.pop('stop_supported')) and value['status'] in ('queued','running','waiting_for_input')
        value['result'] = json.loads(value['result']) if value['result'] else None
        value['review_versions'] = json.loads(value['review_versions']) if value['review_versions'] else None
        value['project'] = json.loads(value['project']) if value.get('project') else None
        value['project_ref'] = json.loads(value['project_ref']) if value.get('project_ref') else None
        value['independent_review']=json.loads(value['independent_review']) if value.get('independent_review') else None
        from .recovery import failure_kind
        value['failure_kind']=failure_kind(value['status'],value.get('failure_code'),value.get('stop_reason'))
        return value

    @staticmethod
    def _public_project(row):
        value = dict(row)
        value['profile'] = json.loads(value['profile'])
        value['archived'] = bool(value['archived'])
        return value

    @staticmethod
    def _project(db, project_id, *, active=False):
        if not isinstance(project_id, str):
            raise DomainError('VALIDATION', 'Укажите идентификатор проекта.')
        row = db.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
        if row is None:
            raise DomainError('PROJECT_NOT_FOUND', 'Проект не найден.', 404)
        project = Store._public_project(row)
        if active and project['archived']:
            raise DomainError('PROJECT_ARCHIVED', 'Проект в архиве. Восстановите его или выберите другой.', 409)
        return project

    @staticmethod
    def _project_version(project, expected):
        if type(expected) is not int or expected < 1:
            raise DomainError('VALIDATION', 'Требуется expected_version проекта.')
        if project['version'] != expected:
            raise DomainError('PROJECT_CONFLICT', 'Настройки проекта изменились. Прочитайте их и проверьте перед сохранением или запуском.',
                              409, current=project)

    @staticmethod
    def _validate_saved_project(name, description, profile):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
            raise DomainError('VALIDATION', 'Название проекта должно содержать от 1 до 120 символов.')
        if not isinstance(description, str) or len(description) > 2000:
            raise DomainError('VALIDATION', 'Описание проекта — до 2000 символов.')
        if not isinstance(profile, dict):
            raise DomainError('VALIDATION', 'Укажите настройки проекта в profile.')
        # A missing hash means HEAD at launch, never a mutable ref in a saved run.
        commit = profile.get('commit')
        value = Store.validate_project({**profile, 'commit': '0'*40 if commit is None or commit == '' else commit})
        if profile.get('commit') not in (None, ''):
            value['commit'] = value['commit'].lower()
        else:
            value['commit'] = None
        return name.strip(), description, value

    def projects(self):
        with self.connection() as db:
            return {'projects': [self._public_project(row) for row in db.execute(
                'SELECT * FROM projects ORDER BY archived,name COLLATE NOCASE,id')], 'revision': self._revision(db)}

    def get_project(self, project_id):
        with self.connection() as db:
            return self._project(db, project_id)

    def create_project(self, name, profile, description=''):
        if self.source != 'user':
            raise DomainError('FORBIDDEN', 'Профили выполнения настраивает пользователь.', 403)
        name, description, profile = self._validate_saved_project(name, description, profile)
        project_id, timestamp = str(uuid4()), now()
        with self.connection(write=True, control=True) as db:
            db.execute('INSERT INTO projects(id,name,description,profile,created_at,updated_at) VALUES (?,?,?,?,?,?)',
                       (project_id, name, description, json.dumps(profile, ensure_ascii=False), timestamp, timestamp))
            self._touch(db, [])
            return self._project(db, project_id)

    def update_project(self, project_id, expected_version, changes):
        if self.source != 'user':
            raise DomainError('FORBIDDEN', 'Профили выполнения настраивает пользователь.', 403)
        if not isinstance(changes, dict) or not changes or set(changes)-{'name','description','profile','archived'}:
            raise DomainError('VALIDATION', 'Разрешены name, description, profile, archived.')
        if 'archived' in changes and type(changes['archived']) is not bool:
            raise DomainError('VALIDATION', 'archived должен быть boolean.')
        with self.connection(write=True, control=True) as db:
            current = self._project(db, project_id)
            self._project_version(current, expected_version)
            name, description, profile = self._validate_saved_project(
                changes.get('name', current['name']), changes.get('description', current['description']),
                changes.get('profile', current['profile']))
            db.execute('UPDATE projects SET name=?,description=?,profile=?,archived=?,version=version+1,updated_at=? WHERE id=?',
                       (name, description, json.dumps(profile, ensure_ascii=False),
                        changes.get('archived', current['archived']), now(), project_id))
            self._touch(db, [])
            return self._project(db, project_id)

    def resolve_project(self, project_id, expected_version):
        if self.source != 'user':
            raise DomainError('FORBIDDEN', 'Исходный коммит проверяется при пользовательском запуске.', 403)
        with self.connection() as db:
            project = self._project(db, project_id, active=True)
            self._project_version(project, expected_version)
        from .git_project import resolve_commit
        profile = {**project['profile'], 'commit': resolve_commit(project['profile']['repo'], project['profile']['commit'])}
        return {'project': self.validate_project(profile),
                'project_ref': {key: project[key] for key in ('id','name','version')}}

    @staticmethod
    def validate_project(value):
        """Freeze user-selected inputs; executable/runtime argv never comes from HTTP."""
        fields={'repo','commit','files','writable','checks','acceptance'}
        if not isinstance(value,dict) or set(value)!=fields:
            raise DomainError('VALIDATION','Укажите репозиторий, коммит, файлы, разрешённые правки, тесты и критерии приёмки.')
        if (not isinstance(value['repo'],str) or len(value['repo'])>4096 or '\0' in value['repo'] or
                not Path(value['repo']).is_absolute() or not isinstance(value['commit'],str) or
                not re.fullmatch(r'[a-fA-F0-9]{40}|[a-fA-F0-9]{64}',value['commit'])):
            raise DomainError('VALIDATION','Нужны абсолютный путь репозитория и полный хеш коммита (40 или 64 hex).')
        for field in ('files','writable','acceptance','checks'):
            items=value[field]
            if not isinstance(items,list) or not 1<=len(items)<=20 or not all(isinstance(s,str) and s.strip() and '\0' not in s for s in items):
                raise DomainError('VALIDATION',f'{field}: укажите от 1 до 20 непустых строк.')
        for field in ('files','writable'):
            if len(set(value[field]))!=len(value[field]):
                raise DomainError('VALIDATION','Список файлов содержит повторы.')
            for name in value[field]:
                path=PurePosixPath(name)
                if (len(name)>256 or path.is_absolute() or any(p in ('','.', '..') for p in name.split('/')) or
                        '\\' in name or ':' in name or any(ord(c)<32 for c in name)):
                    raise DomainError('VALIDATION','Файлы задаются относительными путями с /, без переходов ..')
        if value['checks'][0]!='python' or sum(len(s.encode('utf-8')) for s in value['checks'])>4096:
            raise DomainError('VALIDATION','Команда тестов — массив аргументов, начинающийся с python.')
        if sum(len(s.encode('utf-8')) for s in value['acceptance'])>3000:
            raise DomainError('VALIDATION','Критерии приёмки превышают 3000 байт UTF-8.')
        return json.loads(json.dumps(value))

    @staticmethod
    def _run(db, run_id):
        row = db.execute('SELECT * FROM agent_runs WHERE id=?', (run_id,)).fetchone()
        if row is None:
            raise DomainError('RUN_NOT_FOUND', 'Прогон не найден.', 404)
        return dict(row)

    @staticmethod
    def _latest_run(db, task_id):
        row = db.execute('SELECT id FROM agent_runs WHERE task_id=? ORDER BY created_at DESC,id DESC LIMIT 1', (task_id,)).fetchone()
        return row[0] if row else None

    def agent_runs(self):
        with self.connection() as db:
            # Pending human decisions must not disappear behind a history limit.
            runs = [self._public_run(r) for r in db.execute('''SELECT * FROM agent_runs r WHERE
                status IN ('queued','running','cancelling') OR id IN (SELECT id FROM agent_runs ORDER BY created_at DESC,id DESC LIMIT 20)
                OR (status='completed' AND decision IN ('pending','changes_requested') AND NOT EXISTS (
                    SELECT 1 FROM agent_runs newer WHERE newer.task_id=r.task_id
                    AND (newer.created_at>r.created_at OR (newer.created_at=r.created_at AND newer.id>r.id))))
                OR (status='waiting_for_input' AND NOT EXISTS (
                    SELECT 1 FROM agent_runs newer WHERE newer.task_id=r.task_id
                    AND (newer.created_at>r.created_at OR (newer.created_at=r.created_at AND newer.id>r.id))))
                OR (status IN ('failed','interrupted','cancelled') AND (recovery_checkpoint_id IS NOT NULL OR EXISTS (
                    SELECT 1 FROM run_checkpoints c WHERE c.run_id=r.id)) AND NOT EXISTS (
                    SELECT 1 FROM agent_runs newer WHERE newer.task_id=r.task_id AND
                    (newer.created_at>r.created_at OR (newer.created_at=r.created_at AND newer.id>r.id))))
                ORDER BY created_at DESC,id DESC''')]
            for run in runs:
                run['cleanup']=self._latest_cleanup(db,run['id'])
                question=db.execute('SELECT status FROM goal_questions WHERE id=?',(run['question_id'],)).fetchone() if run['question_id'] else None
                run['input_answered']=bool(question and question[0]=='answered')
                run['recovery_candidate']=run['status'] in ('failed','interrupted','cancelled') and self._latest_run(db,run['task_id'])==run['id'] and self._checkpoint_for_run(db,run['id']) is not None
                if run['status']=='waiting_for_input' and self._latest_run(db,run['task_id'])!=run['id']:
                    run['can_stop']=False
            return runs

    @staticmethod
    def _public_cleanup(row):
        if row is None:return None
        value=dict(row);value.pop('pid',None)
        value['receipt']=json.loads(value['receipt']) if value['receipt'] else None
        return value

    @staticmethod
    def _latest_cleanup(db,run_id):
        return Store._public_cleanup(db.execute('SELECT * FROM run_cleanups WHERE run_id=? ORDER BY rowid DESC LIMIT 1',(run_id,)).fetchone())

    @staticmethod
    def _no_cleanup(db,*,retry_run_id=None):
        row=db.execute('''SELECT c.* FROM run_cleanups c WHERE NOT EXISTS (
            SELECT 1 FROM run_cleanups newer WHERE newer.run_id=c.run_id AND newer.rowid>c.rowid)
            AND (c.status='running' OR (c.status IN ('failed','interrupted') AND c.run_id!=?)) LIMIT 1''',
            (retry_run_id or '',)).fetchone()
        if row:
            raise DomainError('CLEANUP_BUSY' if row['status']=='running' else 'CLEANUP_REQUIRED',
                'Дождитесь очистки среды.' if row['status']=='running' else 'Сначала повторите неподтверждённую очистку среды.',
                409,run_id=row['run_id'])

    def run_cleanup_status(self,run_id):
        with self.connection() as db:
            run=self._run(db,run_id)
            return {'run_id':run_id,'run_version':run['version'],'cleanup':self._latest_cleanup(db,run_id)}

    def cleanup_context(self,db,run_id,expected_version):
        if self.source!='user':raise DomainError('FORBIDDEN','Очистку среды запрашивает пользователь.',403)
        run=self._run(db,run_id);self._check_version(self._public_run(run),expected_version)
        if run['status'] not in ('failed','interrupted','cancelled') or not run['project']:
            raise DomainError('CLEANUP_UNAVAILABLE','Очищается только среда завершённой неудачной попытки разработки.',409)
        self._no_cleanup(db,retry_run_id=run_id)
        if db.execute("SELECT 1 FROM agent_runs WHERE status IN ('queued','running','cancelling')").fetchone():
            raise DomainError('RUN_BUSY','Дождитесь завершения текущего прогона перед очисткой.',409)
        return run

    def begin_run_cleanup(self,run_id,expected_version,pid,*,cleanup_id=None):
        if type(pid) is not int or pid<=1:raise ValueError('Cleanup requires its live owner process')
        with self.connection(write=True,control=True) as db:
            self.cleanup_context(db,run_id,expected_version)
            identifier=cleanup_id or str(uuid4());stamp=now()
            db.execute("INSERT INTO run_cleanups(id,run_id,status,pid,created_at,updated_at) VALUES (?,?,'running',?,?,?)",
                (identifier,run_id,pid,stamp,stamp));self._touch(db,[])
            return self._latest_cleanup(db,run_id)

    def finish_run_cleanup(self,cleanup_id,status,error='',receipt=None):
        if self.source!='user':raise DomainError('FORBIDDEN','Очисткой среды управляет локальный контроллер.',403)
        if status not in ('completed','failed','interrupted'):raise ValueError('Invalid cleanup status')
        with self.connection(write=True,control=True) as db:
            row=db.execute('SELECT * FROM run_cleanups WHERE id=?',(cleanup_id,)).fetchone()
            if row is None:raise DomainError('NOT_FOUND','Попытка очистки не найдена.',404)
            if row['status']=='running':
                db.execute('UPDATE run_cleanups SET status=?,error=?,receipt=?,updated_at=? WHERE id=?',
                    (status,str(error)[:2000],json.dumps(receipt) if receipt is not None else None,now(),cleanup_id))
                self._touch(db,[])
            return self._public_cleanup(db.execute('SELECT * FROM run_cleanups WHERE id=?',(cleanup_id,)).fetchone())

    def reserve_agent_run(self, task_id, expected_version, provider, command, *, revision_of=None, expected_review_version=None, project=None, project_ref=None,
                          continuation_of=None, expected_pause_version=None,recovery_of=None,expected_recovery_version=None,recovery_checkpoint_id=None):
        """User starts a configured executor; this never grants agents write permission."""
        if self.source != 'user':
            raise DomainError('FORBIDDEN', 'Запуск из интерфейса доступен пользователю.', 403)
        if project is not None:
            project=self.validate_project(project)
        with self.connection(write=True, control=True) as db:
            self._no_cleanup(db)
            task = self._row(db, task_id)
            if task['parent_id'] is not None:
                raise DomainError('GOAL_REQUIRED','Запускайте цель верхнего уровня. Подзадачи назначает координатор.',409)
            self._check_version(task, expected_version)
            self._no_question(db,task_id)
            if task['status'] not in ('planned', 'in_progress'):
                raise DomainError('TASK_NOT_RUNNABLE', 'Запускайте задачи в статусе «Запланировано» или «В работе».', 409)
            if db.execute("SELECT 1 FROM agent_runs WHERE status IN ('queued','running','cancelling')").fetchone():
                raise DomainError('RUN_BUSY', 'Дождитесь завершения текущего прогона.', 409)
            if project_ref is not None and not revision_of and not continuation_of and not recovery_of:
                if not isinstance(project_ref, dict) or set(project_ref) != {'id','name','version'} or project is None:
                    raise DomainError('VALIDATION', 'Некорректная ссылка на профиль проекта.')
                saved = self._project(db, project_ref['id'], active=True)
                self._project_version(saved, project_ref['version'])
                expected_profile = {**saved['profile'], 'commit': saved['profile']['commit'] or project['commit']}
                if project != expected_profile or project_ref['name'] != saved['name']:
                    raise DomainError('PROJECT_CONFLICT', 'Настройки запуска не совпадают с сохранённым проектом.', 409)
            latest = self._latest_run(db, task_id)
            if recovery_of:
                previous,checkpoint=self.recovery_context(db,recovery_of,expected_recovery_version,expected_version,recovery_checkpoint_id)
                if command!=json.loads(self._run(db,recovery_of)['command']):
                    raise DomainError('RECOVERY_RUNTIME_CHANGED','Настройки исполнителя изменились во время подготовки восстановления.',409)
                if project!=previous['project']:
                    raise DomainError('RECOVERY_STALE','Профиль восстановления должен совпадать с исходным.',409)
                project_ref=previous['project_ref'];revision_of=previous['revision_of']
            elif continuation_of:
                previous=self._run(db,continuation_of)
                self._check_version(self._public_run(previous),expected_pause_version)
                question=db.execute('SELECT * FROM goal_questions WHERE id=?',(previous['question_id'],)).fetchone()
                if (latest!=continuation_of or previous['task_id']!=task_id or previous['status']!='waiting_for_input' or
                        question is None or question['status']!='answered' or
                        project!=(json.loads(previous['project']) if previous['project'] else None)):
                    raise DomainError('CONTINUATION_STALE','Продолжение доступно для последнего прогона после сохранённого ответа.',409)
                project_ref=json.loads(previous['project_ref']) if previous['project_ref'] else None
                revision_of=previous['revision_of']
                checkpoint=self._continuation_checkpoint(db,previous,question,task)
                if checkpoint:
                    if command!=json.loads(previous['command']):
                        raise DomainError('CONTINUATION_RUNTIME_CHANGED','Настройки исполнителя изменились после вопроса. Снимок не будет запущен в другом окружении.',409)
                    recovery_checkpoint_id=checkpoint['id']
                elif revision_of:
                    reviewed=self._public_run(self._run(db,revision_of))
                    child=self._row(db,reviewed['result']['child_id'])
                    self._check_version(child,reviewed['review_versions'][child['id']])
                    if child['status']!='planned':
                        raise DomainError('CONTINUATION_STALE','Подзадача доработки изменилась после возврата.',409)
            elif latest and self._run(db,latest)['status']=='waiting_for_input':
                raise DomainError('CONTINUATION_REQUIRED','Используйте продолжение диалога, чтобы сохранить исходный профиль и историю.',409)
            elif revision_of:
                previous = self._run(db, revision_of)
                if project != (json.loads(previous['project']) if previous['project'] else None):
                    raise DomainError('REVIEW_STALE','Параметры проекта доработки должны совпадать с исходным прогоном.',409)
                project_ref = json.loads(previous['project_ref']) if previous.get('project_ref') else None
                self._check_version(self._public_run(previous), expected_review_version)
                if previous['task_id'] != task_id or latest != revision_of or previous['decision'] != 'changes_requested':
                    raise DomainError('REVIEW_STALE', 'Доработка доступна для последнего возвращённого результата.', 409)
                versions = json.loads(previous['review_versions'])
                result = json.loads(previous['result'])
                child = self._row(db, result['child_id'])
                for item in (task, child):
                    self._check_version(item, versions[item['id']])
                    if item['status'] != 'planned':
                        raise DomainError('REVIEW_STALE', 'Задачи изменились после возврата. Проверьте их перед новым запуском.', 409)
            elif latest and self._run(db, latest)['decision'] == 'changes_requested':
                raise DomainError('REVISION_REQUIRED', 'Используйте «Запустить доработку» у возвращённого результата, чтобы передать замечания.', 409)
            elif latest and self._checkpoint_for_run(db,latest) is not None:
                raise DomainError('RECOVERY_REQUIRED','У цели сохранён checkpoint. Используйте явное восстановление из истории.',409)
            run_id, timestamp = str(uuid4()), now()
            db.execute('''INSERT INTO agent_runs(id,task_id,task_title,task_version,provider,command,status,created_at,updated_at,revision_of,stop_supported,project,project_ref)
                          VALUES (?,?,?,?,?,?,'queued',?,?,?,1,?,?)''',
                       (run_id, task_id, task['title'], task['version'], provider, json.dumps(command), timestamp, timestamp, revision_of,
                        json.dumps(project,ensure_ascii=False) if project is not None else None,
                        json.dumps(project_ref,ensure_ascii=False) if project_ref is not None else None))
            if project_ref is not None and not revision_of and not recovery_of:
                db.execute('INSERT INTO goal_projects(task_id,project_id) VALUES (?,?) ON CONFLICT(task_id) DO UPDATE SET project_id=excluded.project_id',
                           (task_id, project_ref['id']))
            db.execute('UPDATE agent_runs SET continuation_of=?,requirements_version=? WHERE id=?',
                       (continuation_of,self._requirements_version(db,task_id),run_id))
            db.execute('UPDATE agent_runs SET recovery_of=?,recovery_checkpoint_id=? WHERE id=?',
                       (recovery_of,recovery_checkpoint_id,run_id))
            self._touch(db, [])
            return self._public_run(db.execute('SELECT * FROM agent_runs WHERE id=?', (run_id,)).fetchone())

    def claim_agent_run(self, run_id, pid):
        """Local job supervisor only; not exposed through HTTP/MCP."""
        with self.connection(write=True, control=True) as db:
            self._no_cleanup(db)
            row = db.execute('SELECT * FROM agent_runs WHERE id=?', (run_id,)).fetchone()
            if row is None or row['status'] != 'queued' or row['created_at'] < (
                    datetime.now(timezone.utc)-timedelta(seconds=30)).isoformat(timespec='microseconds'):
                raise DomainError('RUN_EXPIRED', 'Запрос запуска устарел или уже выполняется.', 409)
            self._check_version(self._row(db, row['task_id']), row['task_version'])
            if row['recovery_of']:
                self.recovery_context(db,row['recovery_of'],None,row['task_version'],row['recovery_checkpoint_id'],latest_override=run_id)
            elif row['revision_of']:
                previous = self._public_run(self._run(db, row['revision_of']))
                child = self._row(db, previous['result']['child_id'])
                self._check_version(child, previous['review_versions'][child['id']])
            db.execute("UPDATE agent_runs SET status='running',pid=?,updated_at=?,version=version+1 WHERE id=?", (pid, now(), run_id))
            self._touch(db, [])
            return dict(row)

    def stop_agent_run(self, run_id, expected_version, *, reason='user'):
        """Control operation in either mode. Never edits task statuses or kills by PID."""
        if self.source != 'user':
            raise DomainError('FORBIDDEN', 'Остановку запрашивает пользователь или локальный супервизор.', 403)
        if reason not in ('user','timeout'):
            raise ValueError('Invalid stop reason')
        with self.connection(write=True, control=True) as db:
            row = self._run(db, run_id)
            self._check_version(self._public_run(row), expected_version)
            if not row['stop_supported']:
                raise DomainError('STOP_UNAVAILABLE', 'Этот прогон запущен старой версией без управления остановкой.', 409)
            if row['status'] == 'cancelling':
                return self._public_run(row)
            if row['status']=='waiting_for_input':
                if self._latest_run(db,row['task_id'])!=run_id:
                    raise DomainError('RUN_FINISHED','У этого прогона уже есть продолжение.',409)
                db.execute("UPDATE agent_runs SET status='cancelled',stop_reason=?,updated_at=?,version=version+1 WHERE id=?",(reason,now(),run_id))
                self._cancel_questions(db,run_id)
                self._touch(db,[])
                return self._public_run(self._run(db,run_id))
            if row['status'] not in ('queued','running'):
                raise DomainError('RUN_FINISHED', 'Прогон уже завершён. Обновите историю.', 409)
            status = 'cancelled' if row['status'] == 'queued' else 'cancelling'
            message = 'Остановлено до запуска. Задачи не изменены.' if status == 'cancelled' else ''
            db.execute('UPDATE agent_runs SET status=?,stop_reason=?,error=?,updated_at=?,version=version+1 WHERE id=?',
                       (status, reason, message, now(), run_id))
            self._touch(db, [])
            return self._public_run(self._run(db, run_id))

    def finish_agent_run(self, run_id, status, error='', result=None, *, only_status=None, question_id=None,failure_code=None,independent_review=None,
                         preserve_pending_question=False):
        if status not in ('completed', 'failed', 'interrupted', 'cancelled','waiting_for_input'):
            raise ValueError('Invalid final run status')
        with self.connection(write=True, control=True) as db:
            row = self._run(db, run_id)
            if row['status'] not in ('queued','running','cancelling') or only_status and row['status']!=only_status:
                return
            # Only the owning supervisor may request this after confirmed scope
            # and adapter cleanup. A durable sealed question outlives a lost
            # adapter report; parking it never starts a continuation. Stop wins.
            if (preserve_pending_question and row['status']=='running' and status in ('failed','interrupted')
                    and failure_code!='cleanup_failed'):
                pending=self._pending_checkpoint_question(db,row)
                if pending:
                    status='waiting_for_input';question_id=pending['id']
                    result=None;failure_code=None;independent_review=None
            if independent_review is not None:
                independent_review=self.validate_independent_review(independent_review,json.loads(row['project']) if row['project'] else None)
            requires_review=any(arg=='--validator-model' or arg.startswith('--validator-model=') for arg in json.loads(row['command'])[2:])
            # Completion and stop serialize here: a stop accepted first wins.
            # The supervisor calls this only after closing its process scope.
            if row['status'] == 'cancelling' and status != 'interrupted':
                status = 'failed' if row['stop_reason'] == 'timeout' else 'cancelled'
                error = ('Превышено время выполнения (10 минут). Процессы прогона завершены.'
                         if row['stop_reason'] == 'timeout' else 'Прогон остановлен. Частичные результаты сохранены в задачах.')
                result = None
                failure_code='supervisor_timeout' if row['stop_reason']=='timeout' else 'user_stop'
            if status=='completed' and requires_review and (not independent_review or independent_review['decision']!='accept'):
                raise DomainError('REVIEW_MISSING','Исполнитель не передал допустимое независимое ревью.',409)
            if status=='completed' and result and result.get('handoff')=='atomic-review-v1':
                # Persisting the report and moving both tasks to review is one
                # transaction after owned cleanup. A crash/stop before this point
                # leaves the sealed checkpoint's task versions usable.
                result=json.loads(json.dumps(result,ensure_ascii=False))
                versions=result.get('task_versions',{});child_id=result.get('child_id')
                if child_id==row['task_id'] or set(versions)!={row['task_id'],child_id}:
                    raise DomainError('REVIEW_UNAVAILABLE','Не сохранены версии задач для передачи на ревью.',409)
                root,child=self._row(db,row['task_id']),self._row(db,child_id)
                self._no_question(db,root['id'])
                if (child['parent_id']!=root['id'] or root['status']!='in_progress' or child['status']!='in_progress' or
                        result.get('requirements_version')!=self._requirements_version(db,root['id'])):
                    raise DomainError('REVIEW_STALE','Контекст изменился до сохранения результата.',409)
                for task in (root,child):self._check_version(task,versions[task['id']])
                if row['status'] not in ('queued','running') or only_status and row['status']!=only_status:
                    return
                db.execute("UPDATE tasks SET status='review' WHERE id IN (?,?)",(root['id'],child_id))
                self._touch(db,[root['id'],child_id])
                result['task_versions']={task_id:self._row(db,task_id)['version'] for task_id in versions}
            if status=='waiting_for_input':
                question=db.execute("SELECT id FROM goal_questions WHERE id=? AND task_id=? AND run_id=? AND status='open'",
                                    (question_id,row['task_id'],run_id)).fetchone()
                if not question:
                    raise DomainError('QUESTION_STALE','Исполнитель не сохранил актуальный вопрос.',409)
                result=None
            changed = db.execute("UPDATE agent_runs SET status=?,error=?,result=?,updated_at=?,version=version+1 WHERE id=? AND status IN ('queued','running','cancelling') AND (? IS NULL OR status=?)",
                                (status, error[:2000], json.dumps(result, ensure_ascii=False) if result else None, now(), run_id, only_status, only_status)).rowcount
            if changed:
                from .recovery import valid_code
                db.execute('UPDATE agent_runs SET failure_code=? WHERE id=?',(valid_code(failure_code) if failure_code else None,run_id))
                if independent_review is not None:
                    db.execute('UPDATE agent_runs SET independent_review=? WHERE id=?',(json.dumps(independent_review,ensure_ascii=False),run_id))
                if status=='waiting_for_input':
                    db.execute('UPDATE agent_runs SET question_id=? WHERE id=?',(question_id,run_id))
                elif status in ('failed','interrupted','cancelled'):
                    self._cancel_questions(db,run_id)
                if status == 'cancelled' or (status == 'failed' and row['stop_reason'] == 'timeout'):
                    # The owning supervisor has confirmed scope termination.
                    for agent in db.execute('SELECT id FROM agents WHERE run_id=? AND disconnected=0',(run_id,)).fetchall():
                        db.execute("UPDATE agents SET disconnected=1,status='waiting',action='Прогон остановлен',version=version+1 WHERE id=?",(agent['id'],))
                        self._agent_event(db,agent['id'],'disconnected','Прогон остановлен; соединение закрыто')
                self._touch(db, [])

    def _pending_checkpoint_question(self,db,run):
        if not run['project'] or self._latest_run(db,run['task_id'])!=run['id']:
            return None
        questions=db.execute("SELECT * FROM goal_questions WHERE run_id=? AND status='open'",(run['id'],)).fetchall()
        if len(questions)!=1:return None
        question=dict(questions[0]);checkpoint=self._checkpoint_for_run(db,run['id'])
        if (question['task_id']!=run['task_id'] or not checkpoint or checkpoint['run_id']!=run['id'] or
                question['checkpoint_id']!=checkpoint['id']):return None
        root=self._row(db,run['task_id']);child=self._row(db,checkpoint['child_id'])
        if (root['version']!=checkpoint['task_version']+1 or
                child['version']!=checkpoint['child_version'] or child['parent_id']!=root['id'] or
                self._requirements_version(db,root['id'])!=checkpoint['requirements_version'] or
                root['status']!='in_progress' or child['status']!='in_progress'):
            return None
        return question

    @staticmethod
    def _checkpoint_for_run(db,run_id):
        checkpoint=db.execute('SELECT * FROM run_checkpoints WHERE run_id=? ORDER BY sequence DESC LIMIT 1',(run_id,)).fetchone()
        if not checkpoint:
            previous=Store._run(db,run_id)
            if previous['recovery_checkpoint_id']:
                checkpoint=db.execute('SELECT * FROM run_checkpoints WHERE id=?',(previous['recovery_checkpoint_id'],)).fetchone()
        return dict(checkpoint) if checkpoint else None

    def _continuation_checkpoint(self,db,previous,question,root):
        checkpoint_id=question['checkpoint_id']
        if not checkpoint_id:return None
        checkpoint=db.execute('SELECT * FROM run_checkpoints WHERE id=?',(checkpoint_id,)).fetchone()
        if not checkpoint:raise DomainError('CHECKPOINT_UNAVAILABLE','Снимок перед вопросом недоступен.',409)
        child=self._row(db,checkpoint['child_id'])
        if (checkpoint['run_id']!=previous['id'] or root['version']!=checkpoint['task_version']+2 or
                self._requirements_version(db,root['id'])!=checkpoint['requirements_version']+1 or
                child['version']!=checkpoint['child_version'] or child['parent_id']!=root['id'] or
                root['status']!='in_progress' or child['status']!='in_progress'):
            raise DomainError('CONTINUATION_STALE','Задачи или требования изменились после сохранения снимка и ответа.',409)
        return dict(checkpoint)

    @staticmethod
    def validate_independent_review(value,project):
        allowed={'decision','summary','findings','identities','inference','manifest_sha256','limitations','report_sha256'}
        if (not project or not isinstance(value,dict) or set(value)-allowed or
                not {'decision','summary','findings','identities'}<=set(value) or
                value['decision'] not in ('accept','changes_requested') or not isinstance(value['summary'],str) or
                not 1<=len(value['summary'])<=2000 or not isinstance(value['findings'],list) or len(value['findings'])>8 or
                (value['decision']=='accept')!=(len(value['findings'])==0) or
                not isinstance(value['identities'],dict) or set(value['identities'])!={'developer','validator'}):
            raise DomainError('INVALID_REVIEW','Некорректный отчёт независимого ревью.',409)
        identities=value['identities']
        for role in ('developer','validator'):
            identity=identities[role]
            if (not isinstance(identity,dict) or not all(isinstance(identity.get(k),str) and 1<=len(identity[k])<=150 for k in ('name','digest','family','family_group')) or
                    not re.fullmatch('[a-f0-9]{64}',identity['digest'])):
                raise DomainError('INVALID_REVIEW','Отсутствует идентичность модели ревью.',409)
            canonical=next((prefix for prefix in ('qwen','gemma','llama','mistral','phi','granite','deepseek','gptoss')
                            if re.sub('[^a-z0-9]','',identity['family'].lower()).startswith(prefix)),None)
            if canonical is None or canonical!=identity['family_group']:
                raise DomainError('INVALID_REVIEW','Не подтверждена группа семейства модели.',409)
        if identities['developer']['family_group']==identities['validator']['family_group']:
            raise DomainError('INVALID_REVIEW','Модели одного семейства не считаются независимыми.',409)
        for finding in value['findings']:
            if (not isinstance(finding,dict) or set(finding)!={'path','line','severity','message','evidence'} or
                    not isinstance(finding['path'],str) or finding['path'] not in project['files']+project['writable'] or type(finding['line']) is not int or finding['line']<1 or
                    finding['severity'] not in ('critical','major','minor') or
                    not isinstance(finding['message'],str) or not 1<=len(finding['message'])<=1000 or
                    not isinstance(finding['evidence'],str) or not 1<=len(finding['evidence'])<=300):
                raise DomainError('INVALID_REVIEW','Некорректное замечание ревью.',409)
        try:encoded=json.dumps(value,ensure_ascii=False,allow_nan=False)
        except (ValueError,TypeError):raise DomainError('INVALID_REVIEW','Недопустимые значения в отчёте ревью.',409) from None
        if len(encoded.encode('utf-8'))>20000:raise DomainError('INVALID_REVIEW','Отчёт ревью превышает лимит.',409)
        return json.loads(encoded)

    def publish_checkpoint(self,agent_id,owner,sequence,manifest_sha256,child_id,expected_version,expected_child_version,task_updates=None):
        if not self.run_id or self.source!='agent':raise DomainError('RUN_REQUIRED','Checkpoint публикует только текущий встроенный координатор.',403)
        if type(sequence) is not int or not 0<=sequence<64 or not isinstance(manifest_sha256,str) or not re.fullmatch('[0-9a-f]{64}',manifest_sha256):
            raise DomainError('VALIDATION','Укажите sequence 0..63 и SHA-256 manifest.')
        updates=[] if task_updates is None else task_updates
        if (not isinstance(updates,list) or len(updates)>4 or
                any(not isinstance(item,dict) or set(item)!={'task_id','expected_version','status'} or
                    not isinstance(item['task_id'],str) or type(item['expected_version']) is not int or item['expected_version']<1 or
                    item['status'] not in ('planned','in_progress','done') for item in updates) or
                len({item['task_id'] for item in updates})!=len(updates)):
            raise DomainError('VALIDATION','Checkpoint принимает до четырёх уникальных переходов worker с актуальными версиями.')
        with self.connection(write=True) as db:
            agent=self._agent(db,agent_id);self._owner(agent,owner)
            run=self._public_run(self._run(db,self.run_id))
            if agent['run_id']!=self.run_id or agent['role']!='coordinator' or agent['task_id']!=run['task_id'] or not self._public_agent(agent)['connected']:
                raise DomainError('AGENT_FORBIDDEN','Checkpoint публикует назначенный координатор этого прогона.',403)
            root=self._row(db,run['task_id']);child=self._row(db,child_id)
            self._check_version(root,expected_version);self._check_version(child,expected_child_version)
            self._no_question(db,root['id'])
            if root['status']!='in_progress' or child['parent_id']!=root['id'] or child['status']!='in_progress':
                raise DomainError('CHECKPOINT_SCOPE','Checkpoint относится к выполняемой цели и её подзадаче.',409)
            existing=db.execute('SELECT * FROM run_checkpoints WHERE run_id=? AND sequence=?',(self.run_id,sequence)).fetchone()
            if existing:
                if existing['manifest_sha256']!=manifest_sha256:raise DomainError('CHECKPOINT_CONFLICT','Опубликованный снимок неизменяем.',409)
                if updates:raise DomainError('CHECKPOINT_CONFLICT','Повторная публикация не применяет новые переходы worker.',409)
                return dict(existing)
            changed=[]
            for item in updates:
                task=self._row(db,item['task_id']);self._check_version(task,item['expected_version'])
                if task['parent_id']!=child_id:
                    raise DomainError('CHECKPOINT_SCOPE','Переход относится только к непосредственному worker интегратора.',409)
                if task['status']!=item['status']:
                    db.execute('UPDATE tasks SET status=? WHERE id=?',(item['status'],task['id']));changed.append(task['id'])
            if changed:
                self._touch(db,changed)
                root=self._row(db,root['id']);child=self._row(db,child_id)
            checkpoint={'id':str(uuid4()),'run_id':self.run_id,'sequence':sequence,'manifest_sha256':manifest_sha256,
                        'task_version':root['version'],'child_id':child_id,'child_version':child['version'],
                        'requirements_version':self._requirements_version(db,root['id']),'created_at':now()}
            from .recovery import load_checkpoint
            load_checkpoint(self,run,checkpoint)
            if updates:
                # The manifest binds post-transition versions. Any validation
                # error rolls back node statuses and the new receipt together.
                from .result_review import regular_bytes
                folder=self.path.parent/'.agent-runs'/self.run_id/'development/checkpoints'/f'{sequence:03d}'
                try:
                    parallel=json.loads(regular_bytes(folder,'manifest.json',65536))['parallel'];tasks=parallel['tasks']
                    if (not isinstance(parallel,dict) or type(parallel.get('version')) is not int or parallel['version']!=1 or
                            not isinstance(tasks,dict) or not 1<=len(tasks)<=4):raise ValueError()
                    seen=set()
                    for item in tasks.values():
                        if (not isinstance(item,dict) or set(item)!={'id','version','status'} or not isinstance(item['id'],str) or
                                type(item['version']) is not int or item['version']<1 or
                                item['status'] not in ('planned','in_progress','done') or item['id'] in seen):raise ValueError()
                        task=self._row(db,item['id']);seen.add(item['id'])
                        if task['parent_id']!=child_id or any(item[k]!=task[k] for k in ('version','status')):raise ValueError()
                    if not {item['task_id'] for item in updates}<=seen:raise ValueError()
                except (KeyError,TypeError,ValueError):
                    raise DomainError('CHECKPOINT_CONFLICT','Снимок не связывает итоговые статусы и версии worker.',409) from None
            db.execute('INSERT INTO run_checkpoints VALUES (?,?,?,?,?,?,?,?,?)',tuple(checkpoint.values()))
            self._touch(db,[])
            return checkpoint

    def recovery_context(self,db,run_id,expected_version,expected_task_version,checkpoint_id,*,latest_override=None):
        if self.source!='user':raise DomainError('FORBIDDEN','Восстановление запускает пользователь.',403)
        self._no_cleanup(db)
        run=self._public_run(self._run(db,run_id))
        if expected_version is not None:self._check_version(run,expected_version)
        root=self._row(db,run['task_id']);self._check_version(root,expected_task_version)
        if (run['status'] not in ('failed','interrupted','cancelled') or not run['project'] or
                self._latest_run(db,run['task_id'])!=(latest_override or run_id)):
            raise DomainError('RECOVERY_STALE','Восстанавливается только последняя завершившаяся неудачно попытка.',409)
        self._no_question(db,root['id'])
        if latest_override is None and db.execute("SELECT 1 FROM agent_runs WHERE status IN ('queued','running','cancelling')").fetchone():
            raise DomainError('RUN_BUSY','Дождитесь завершения текущего прогона.',409)
        checkpoint=self._checkpoint_for_run(db,run_id)
        if not checkpoint or checkpoint['id']!=checkpoint_id:
            raise DomainError('CHECKPOINT_UNAVAILABLE','Выбранный подтверждённый checkpoint недоступен.',409)
        origin=self._public_run(self._run(db,checkpoint['run_id']))
        if origin['project']!=run['project']:raise DomainError('CHECKPOINT_CONFLICT','Исходный профиль изменён.',409)
        child=self._row(db,checkpoint['child_id'])
        if (root['version']!=checkpoint['task_version'] or child['version']!=checkpoint['child_version'] or
                root['status']!='in_progress' or child['status']!='in_progress' or child['parent_id']!=root['id'] or
                self._requirements_version(db,root['id'])!=checkpoint['requirements_version']):
            raise DomainError('RECOVERY_STALE','Цель, план или требования изменились после checkpoint. Продолжение не перезапишет эти изменения.',409)
        return run,checkpoint

    def review_agent_run(self, run_id, expected_version, decision, comment=''):
        """Explicit human decision, serialized against saved task/run versions."""
        if self.source != 'user':
            raise DomainError('FORBIDDEN', 'Результат принимает пользователь.', 403)
        if decision not in ('accepted', 'changes_requested') or not isinstance(comment, str) or len(comment) > 2000:
            raise DomainError('VALIDATION', 'Решение: accepted или changes_requested; комментарий — до 2000 символов.')
        comment = comment.strip()
        if decision == 'changes_requested' and not comment:
            raise DomainError('VALIDATION', 'Для возврата опишите, что нужно исправить.')
        with self.connection(write=True,control=True) as db:
            run = self._run(db, run_id)
            self._check_version(self._public_run(run), expected_version)
            if run['status'] != 'completed' or run['decision'] != 'pending' or self._latest_run(db, run['task_id']) != run_id:
                raise DomainError('REVIEW_STALE', 'Можно оценить только последний завершённый результат без решения.', 409)
            result = json.loads(run['result']) if run['result'] else {}
            versions = result.get('task_versions', {})
            child_id = result.get('child_id')
            if child_id == run['task_id'] or set(versions) != {run['task_id'], child_id}:
                raise DomainError('REVIEW_UNAVAILABLE', 'Этот прогон не сохранил версии задач для приёмки. История доступна для просмотра.', 409)
            root, child = self._row(db, run['task_id']), self._row(db, child_id)
            self._no_question(db,root['id'])
            for task in (root, child):
                self._check_version(task, versions[task['id']])
                if task['status'] != 'review':
                    raise DomainError('REVIEW_STALE', 'Задачи уже вышли из ревью. Проверьте их актуальное состояние.', 409)
            if child['parent_id'] != root['id']:
                raise DomainError('REVIEW_STALE', 'Подзадача перемещена в другое дерево.', 409)
            status = 'done' if decision == 'accepted' else 'planned'
            db.execute('UPDATE tasks SET status=? WHERE id IN (?,?)', (status, root['id'], child['id']))
            # _touch also updates all ancestors once, including a non-root task.
            self._touch(db, [root['id'], child['id']])
            review_versions = {t['id']: self._row(db, t['id'])['version'] for t in (root, child)}
            db.execute('''UPDATE agent_runs SET decision=?,review_comment=?,reviewed_at=?,review_versions=?,
                          updated_at=?,version=version+1 WHERE id=?''',
                       (decision, comment, now(), json.dumps(review_versions), now(), run_id))
            return self._public_run(self._run(db, run_id))

    @staticmethod
    def _requirements_version(db,task_id):
        row=db.execute('SELECT requirements_version FROM goal_dialogues WHERE task_id=?',(task_id,)).fetchone()
        return row[0] if row else 0

    def application_context(self, db, run_id, expected_version):
        """Human-only publication of an accepted, still-current review snapshot."""
        if self.source != 'user':
            raise DomainError('FORBIDDEN', 'Изменения применяет только пользователь.', 403)
        run = self._public_run(self._run(db, run_id))
        self._check_version(run, expected_version)
        if run['status'] != 'completed' or run['decision'] != 'accepted' or not run['project']:
            raise DomainError('APPLY_UNAVAILABLE', 'Сначала примите завершённый результат разработки.', 409)
        saved = db.execute('SELECT receipt FROM run_applications WHERE run_id=?', (run_id,)).fetchone()
        if saved:
            return run, json.loads(saved[0])
        if self._latest_run(db, run['task_id']) != run_id:
            raise DomainError('APPLY_STALE', 'У цели появился новый прогон. Откройте его результат.', 409)
        versions = run['review_versions'] or {}
        child_id = (run['result'] or {}).get('child_id')
        if child_id == run['task_id'] or set(versions) != {run['task_id'], child_id}:
            raise DomainError('APPLY_STALE', 'Не сохранены версии принятого результата.', 409)
        for task_id, version in versions.items():
            task = self._row(db, task_id)
            if task['version'] != version or task['status'] != 'done':
                raise DomainError('APPLY_STALE', 'Цель или её план изменились после приёмки.', 409)
        self._no_question(db, run['task_id'])
        return run, None

    @staticmethod
    def _no_question(db,task_id):
        if task_id is None:return
        root=Store._row(db,task_id)
        while root['parent_id'] is not None:root=Store._row(db,root['parent_id'])
        row=db.execute("SELECT id FROM goal_questions WHERE task_id=? AND status='open'",(root['id'],)).fetchone()
        if row:
            raise DomainError('INPUT_REQUIRED','Координатор ожидает ответ пользователя. Работа над целью приостановлена.',409,question_id=row[0])

    @staticmethod
    def _cancel_questions(db,run_id):
        roots=[r[0] for r in db.execute("SELECT task_id FROM goal_questions WHERE run_id=? AND status='open'",(run_id,))]
        db.execute("UPDATE goal_questions SET status='cancelled',version=version+1 WHERE run_id=? AND status='open'",(run_id,))
        if roots:Store._touch(db,roots)

    def goal_dialogue(self,task_id):
        with self.connection() as db:
            task=self._row(db,task_id)
            if task['parent_id'] is not None:
                raise DomainError('GOAL_REQUIRED','Обсуждение относится к цели верхнего уровня.',409)
            questions=[dict(row) for row in db.execute('SELECT * FROM goal_questions WHERE task_id=? ORDER BY created_at,id',(task_id,))]
            latest=self._latest_run(db,task_id)
            for question in questions:
                run=self._run(db,question['run_id']) if question['run_id'] else None
                question['can_answer']=question['status']=='open' and (not run or run['status']=='waiting_for_input' and run['id']==latest)
            continuation=self._public_run(self._run(db,latest)) if latest else None
            if (not continuation or continuation['status']!='waiting_for_input' or
                    not any(q['id']==continuation['question_id'] and q['status']=='answered' for q in questions)):
                continuation=None
            return {'task_id':task_id,'task_version':task['version'],
                    'requirements_version':self._requirements_version(db,task_id),'questions':questions,
                    'needs_input':any(q['status']=='open' for q in questions),'continuation':continuation}

    def ask_question(self,task_id,expected_version,question,*,agent_id,owner):
        if self.source!='agent':raise DomainError('AGENT_ONLY','Вопрос задаёт координатор.',403)
        if not isinstance(question,str) or not question.strip() or len(question.encode('utf-8'))>1000:
            raise DomainError('VALIDATION','Вопрос должен содержать от 1 до 1000 байт UTF-8.')
        with self.connection(write=True) as db:
            root=self._row(db,task_id);self._check_version(root,expected_version)
            agent=self._agent(db,agent_id);self._owner(agent,owner)
            if agent['role']!='coordinator' or agent['task_id']!=task_id or agent['disconnected']:
                raise DomainError('AGENT_FORBIDDEN','Вопрос задаёт координатор, назначенный на эту цель.',403)
            if root['parent_id'] is not None or root['status'] not in ('planned','in_progress'):
                raise DomainError('GOAL_REQUIRED','Уточнение доступно для выполняемой цели верхнего уровня.',409)
            self._no_question(db,task_id)
            active=db.execute("SELECT id FROM agent_runs WHERE task_id=? AND status IN ('queued','running','cancelling')",(task_id,)).fetchone()
            if active and active[0]!=self.run_id:
                raise DomainError('RUN_BUSY','Вопросы этой цели ведёт координатор текущего прогона.',409)
            latest=self._latest_run(db,task_id)
            if latest and self._run(db,latest)['status']=='waiting_for_input':
                raise DomainError('RUN_BUSY','Сначала продолжите или остановите ожидающий встроенный прогон.',409)
            rows=db.execute('SELECT question,answer FROM goal_questions WHERE task_id=?',(task_id,)).fetchall()
            if len(rows)>=20 or sum(len((row[0]+(row[1] or '')).encode('utf-8')) for row in rows)+len(question.encode('utf-8'))>3500:
                raise DomainError('DIALOGUE_LIMIT','Диалог достиг лимита контекста. Новую область работ оформите отдельной целью.',409)
            identifier=str(uuid4())
            checkpoint=self._checkpoint_for_run(db,self.run_id) if self.run_id else None
            if checkpoint:
                child=self._row(db,checkpoint['child_id'])
                if (checkpoint['run_id']!=self.run_id or checkpoint['task_version']!=root['version'] or
                        checkpoint['child_version']!=child['version'] or child['parent_id']!=root['id'] or
                        checkpoint['requirements_version']!=self._requirements_version(db,task_id) or
                        root['status']!='in_progress' or child['status']!='in_progress'):
                    raise DomainError('CHECKPOINT_STALE','Сначала сохраните свежий полный checkpoint перед вопросом.',409)
            db.execute('''INSERT INTO goal_questions(id,task_id,run_id,agent_id,agent_name,question,created_at)
                          VALUES (?,?,?,?,?,?,?)''',(identifier,task_id,self.run_id,agent_id,agent['name'],question.strip(),now()))
            if checkpoint:db.execute('UPDATE goal_questions SET checkpoint_id=? WHERE id=?',(checkpoint['id'],identifier))
            self._touch(db,[task_id])
            db.execute("UPDATE agents SET status='waiting',activity_phase='waiting_for_input',phase_started_at=?,action='Ожидает ответ пользователя',version=version+1 WHERE id=?",(now(),agent_id))
            self._agent_event(db,agent_id,'question','Координатор задал вопрос пользователю')
            return {'question':dict(db.execute('SELECT * FROM goal_questions WHERE id=?',(identifier,)).fetchone()),
                    'task_version':self._row(db,task_id)['version']}

    def answer_question(self,task_id,question_id,expected_version,expected_task_version,answer):
        if self.source!='user':raise DomainError('FORBIDDEN','На вопрос отвечает пользователь.',403)
        if not isinstance(question_id,str):raise DomainError('VALIDATION','question_id должен быть строкой.')
        if not isinstance(answer,str) or not answer.strip() or len(answer.encode('utf-8'))>2000:
            raise DomainError('VALIDATION','Ответ должен содержать от 1 до 2000 байт UTF-8.')
        with self.connection(write=True,control=True) as db:
            root=self._row(db,task_id);self._check_version(root,expected_task_version)
            row=db.execute('SELECT * FROM goal_questions WHERE id=? AND task_id=?',(question_id,task_id)).fetchone()
            if row is None:raise DomainError('QUESTION_NOT_FOUND','Вопрос не найден.',404)
            question=dict(row);self._check_version(question,expected_version)
            if question['status']!='open' or root['status']=='done':
                raise DomainError('QUESTION_STALE','Вопрос уже закрыт или цель завершена.',409)
            if question['run_id']:
                run=self._run(db,question['run_id'])
                if run['status']!='waiting_for_input' or self._latest_run(db,task_id)!=run['id']:
                    raise DomainError('QUESTION_NOT_READY','Исполнитель ещё завершает остановку или вопрос уже неактуален. Обновите диалог.',409)
            rows=db.execute('SELECT question,answer FROM goal_questions WHERE task_id=?',(task_id,)).fetchall()
            if sum(len((row[0]+(row[1] or '')).encode('utf-8')) for row in rows)+len(answer.encode('utf-8'))>4000:
                raise DomainError('DIALOGUE_LIMIT','Сократите ответ: суммарный контекст диалога ограничен 4000 байтами UTF-8.',409)
            db.execute("UPDATE goal_questions SET answer=?,status='answered',answered_at=?,version=version+1 WHERE id=?",(answer.strip(),now(),question_id))
            db.execute('''INSERT INTO goal_dialogues(task_id,requirements_version) VALUES (?,1)
                          ON CONFLICT(task_id) DO UPDATE SET requirements_version=requirements_version+1''',(task_id,))
            if question['run_id']:
                db.execute('UPDATE agent_runs SET version=version+1,updated_at=? WHERE id=?',(now(),question['run_id']))
            self._touch(db,[task_id])
        return self.goal_dialogue(task_id)

    @staticmethod
    def _public_goal_result(row):
        value=dict(row);value['checks']=json.loads(value['checks']);return value

    @staticmethod
    def _latest_goal_result(db,task_id):
        # UUIDs are identities, not chronology. Preserve timestamp priority and
        # break ties by durable insertion order, shared by reads and human review.
        return db.execute('SELECT * FROM goal_results WHERE task_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(task_id,)).fetchone()

    def submit_goal_result(self,task_id,expected_version,summary,checks):
        """External MCP agents hand off a report; only the human can accept it."""
        if self.source!='agent':
            raise DomainError('AGENT_ONLY','Отчёт о выполнении передаёт агент.',403)
        if (not isinstance(summary,str) or not summary.strip() or len(summary)>12000 or
                not isinstance(checks,list) or len(checks)>20 or
                not all(isinstance(s,str) and s.strip() and len(s)<=1000 for s in checks)):
            raise DomainError('VALIDATION','Нужны summary (1–12000 символов) и до 20 текстовых пунктов checks.')
        with self.connection(write=True) as db:
            task=self._row(db,task_id);self._check_version(task,expected_version)
            self._no_question(db,task_id)
            if task['parent_id'] is not None or task['status']=='done':
                raise DomainError('GOAL_REQUIRED','Передайте результат незавершённой цели верхнего уровня.',409)
            if db.execute("SELECT 1 FROM agent_runs WHERE task_id=? AND status IN ('queued','running','cancelling')",(task_id,)).fetchone():
                raise DomainError('RUN_BUSY','Цель выполняется встроенным исполнителем; он передаст свой результат.',409)
            db.execute("UPDATE tasks SET status='review' WHERE id=?",(task_id,));self._touch(db,[task_id])
            identifier=str(uuid4());task=self._row(db,task_id)
            db.execute('''INSERT INTO goal_results(id,task_id,task_version,summary,checks,created_at)
                VALUES (?,?,?,?,?,?)''',(identifier,task_id,task['version'],summary.strip(),json.dumps(checks,ensure_ascii=False),now()))
            return self._public_goal_result(db.execute('SELECT * FROM goal_results WHERE id=?',(identifier,)).fetchone())

    def review_goal(self,task_id,expected_version,result_id,expected_result_version,decision,comment=''):
        if self.source!='user':
            raise DomainError('FORBIDDEN','Результат принимает только пользователь.',403)
        if (not isinstance(result_id,str) or decision not in ('accepted','changes_requested') or not isinstance(comment,str) or len(comment)>2000 or
                decision=='changes_requested' and not comment.strip()):
            raise DomainError('VALIDATION','Выберите решение; для возврата укажите замечания (до 2000 символов).')
        with self.connection(write=True,control=True) as db:
            task=self._row(db,task_id);self._check_version(task,expected_version)
            row=db.execute('SELECT * FROM goal_results WHERE id=? AND task_id=?',(result_id,task_id)).fetchone()
            if row is None:raise DomainError('NOT_FOUND','Отчёт не найден.',404)
            result=self._public_goal_result(row);self._check_version(result,expected_result_version)
            latest=self._latest_goal_result(db,task_id)['id']
            if latest!=result_id or result['decision']!='pending' or task['status']!='review' or task['version']!=result['task_version']:
                raise DomainError('REVIEW_STALE','Цель или её подзадачи изменились после отчёта. Агент должен передать актуальный результат.',409)
            db.execute('UPDATE goal_results SET decision=?,comment=?,version=version+1 WHERE id=?',(decision,comment.strip(),result_id))
            db.execute('UPDATE tasks SET status=? WHERE id=?',('done' if decision=='accepted' else 'planned',task_id))
            self._touch(db,[task_id])
            return self._public_goal_result(db.execute('SELECT * FROM goal_results WHERE id=?',(result_id,)).fetchone())

    @staticmethod
    def _agent_text(value, field, limit=200):
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise DomainError('VALIDATION', f'{field}: требуется текст от 1 до {limit} символов.')
        return value.strip()

    @staticmethod
    def _agent(db, agent_id):
        row = db.execute('SELECT * FROM agents WHERE id=?', (agent_id,)).fetchone()
        if row is None:
            raise DomainError('AGENT_NOT_FOUND', 'Агент не найден.', 404)
        return dict(row)

    @staticmethod
    def _public_agent(row):
        row = dict(row)
        row.pop('owner_hash', None)
        row['connected'] = not row.pop('disconnected') and row['last_seen'] >= (
            datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat(timespec='microseconds')
        row['budget'] = json.loads(row['budget']) if row.get('budget') else None
        return row

    @staticmethod
    def _owner(row, owner):
        if not isinstance(owner, str) or not hmac.compare_digest(row['owner_hash'], hashlib.sha256(owner.encode()).hexdigest()):
            raise DomainError('AGENT_FORBIDDEN', 'Можно изменять только агента своей MCP-сессии.', 403)

    @staticmethod
    def _agent_event(db, agent_id, kind, message, **details):
        agent=Store._agent(db,agent_id)
        root=Store._row(db,agent['task_id']) if agent['task_id'] else None
        while root and root['parent_id']:root=Store._row(db,root['parent_id'])
        metadata={'agent_name':agent['name'],'task_id':agent['task_id'],'run_id':agent['run_id'],
                  'goal_id':root['id'] if root else None,
                  'phase':agent.get('activity_phase','idle'), **details}
        db.execute('INSERT INTO agent_events(agent_id,kind,message,created_at,metadata) VALUES (?,?,?,?,?)',
                   (agent_id, kind, message, now(),json.dumps(metadata)))
        # Keep the dashboard bounded; task data is never removed here.
        db.execute('DELETE FROM agent_events WHERE id <= (SELECT coalesce(max(id),0)-1000 FROM agent_events)')
        Store._touch(db, [])

    def register_agent(self, agent_id, owner, name, role, model, framework):
        name = self._agent_text(name, 'name', 80)
        model = self._agent_text(model, 'model', 120)
        framework = self._agent_text(framework, 'framework', 80)
        if role not in ('coordinator', 'worker', 'validator'):
            raise DomainError('VALIDATION', 'role: coordinator, worker или validator.')
        with self.connection(write=True, control=True) as db:
            existing = db.execute('SELECT * FROM agents WHERE id=?', (agent_id,)).fetchone()
            if existing:
                self._owner(existing, owner)
                if any(existing[k] != v for k, v in dict(name=name, role=role, model=model, framework=framework).items()):
                    raise DomainError('VALIDATION', 'Агент уже зарегистрирован. Роль и идентичность сессии неизменяемы.')
                db.execute('UPDATE agents SET last_seen=?,disconnected=0 WHERE id=?', (now(), agent_id))
            else:
                timestamp = now()
                db.execute('''INSERT INTO agents(id,owner_hash,name,role,model,framework,registered_at,last_seen,run_id)
                              VALUES (?,?,?,?,?,?,?,?,?)''',
                           (agent_id, hashlib.sha256(owner.encode()).hexdigest(), name, role, model, framework, timestamp, timestamp, self.run_id))
                self._agent_event(db, agent_id, 'connected', 'Подключён; ожидает назначения')
            return self._public_agent(self._agent(db, agent_id))

    def report_agent(self, agent_id, owner, status=None, action=None, disconnect=False, phase=None, budget=None):
        if status is not None and status not in ('available', 'waiting', 'working', 'done', 'error'):
            raise DomainError('VALIDATION', 'Неизвестное состояние агента.')
        if action is not None:
            action = self._agent_text(action, 'action', 2000)
        if phase is not None and phase not in AGENT_PHASES:
            raise DomainError('VALIDATION','Неизвестный этап работы агента.')
        if budget is not None:
            base={'scope','seconds_limit','elapsed_seconds','tokens_limit','tokens_used','steps_limit','steps_used'}
            resources={'workers_limit','workers_active','cpus_limit','cpus_reserved','memory_mb_limit','memory_mb_reserved'}
            if (not isinstance(budget,dict) or set(budget) not in (base,base|resources) or
                    budget['scope'] not in ('agent','run')):
                raise DomainError('VALIDATION','Укажите scope и полную ограниченную сводку бюджета.')
            for key, maximum in {'seconds_limit':86400,'elapsed_seconds':864000,'tokens_limit':100000000,
                                 'tokens_used':1000000000,'steps_limit':100000,'steps_used':1000000}.items():
                value=budget[key]
                if (type(value) not in (int,float) or not 0<=value<=maximum or not math.isfinite(value) or
                        key!='elapsed_seconds' and type(value) is not int or key.endswith('_limit') and value<1):
                    raise DomainError('VALIDATION','Некорректное значение бюджета: '+key)
            if resources<=set(budget):
                for prefix,used,maximum in [('workers','active',64),('cpus','reserved',256),('memory_mb','reserved',1048576)]:
                    limit=budget[prefix+'_limit'];amount=budget[prefix+'_'+used]
                    if type(limit) is not int or type(amount) is not int or not 1<=limit<=maximum or not 0<=amount<=limit:
                        raise DomainError('VALIDATION','Некорректная сводка ресурсов: '+prefix)
        with self.connection(write=True, control=True) as db:
            current = self._agent(db, agent_id)
            self._owner(current, owner)
            # Presence stays available after cancellation; a retired run cannot
            # claim fresh work or emit new structured execution telemetry.
            if self.run_id and not disconnect and any(v is not None for v in (status,action,phase,budget)):
                self._check_write(db)
            if status in ('working', 'done') and current['task_id'] is None:
                raise DomainError('ASSIGNMENT_REQUIRED', 'Сначала координатор должен назначить агенту задачу.')
            if phase in ('working','planning','reading','editing','checking','verifying','review','done') and current['task_id'] is None:
                raise DomainError('ASSIGNMENT_REQUIRED','Для рабочего этапа требуется назначение.')
            if phase is None:
                phase=({'available':'idle','waiting':'waiting','working':'working','done':'done','error':'error'}.get(status)
                       if status is not None and status!=current['status'] else None)
            phase=phase or current['activity_phase']
            phase_changed=phase!=current['activity_phase'] or action is not None and action!=current['action']
            next_action=action if action is not None else '' if phase!=current['activity_phase'] else current['action']
            budget_text=json.dumps({**budget,'observed_at':now()},sort_keys=True) if budget is not None else current['budget']
            changed = (status is not None and status != current['status']) or phase_changed or bool(disconnect) != bool(current['disconnected'])
            db.execute('''UPDATE agents SET last_seen=?,disconnected=?,status=?,action=?,activity_phase=?,
                          phase_started_at=?,budget=?,version=version+? WHERE id=?''',
                       (now(), int(disconnect), status or current['status'], next_action,
                        phase,now() if phase_changed else current['phase_started_at'],budget_text,int(changed),agent_id))
            if changed:
                reconnected=not disconnect and bool(current['disconnected'])
                kind='disconnected' if disconnect else 'connected' if reconnected else 'activity'
                message='Отключён' if disconnect else action or ('Снова на связи' if reconnected else 'Изменён этап работы')
                self._agent_event(db,agent_id,kind,message)
            return self._public_agent(self._agent(db, agent_id))

    def assign_agent(self, coordinator_id, owner, agent_id, task_id, expected_version):
        with self.connection(write=True) as db:
            coordinator = self._agent(db, coordinator_id)
            self._owner(coordinator, owner)
            if coordinator['role'] != 'coordinator' or not self._public_agent(coordinator)['connected']:
                raise DomainError('AGENT_FORBIDDEN', 'Назначать задачи может подключённый координатор.', 403)
            target = self._agent(db, agent_id)
            if target['run_id']!=coordinator['run_id']:
                raise DomainError('ASSIGNMENT_SCOPE','Агенты принадлежат разным контекстам прогонов.',409)
            if type(expected_version) is not int or expected_version < 1:
                raise DomainError('VALIDATION', 'Требуется expected_version агента из list_agents.')
            if target['version'] != expected_version:
                raise DomainError('CONFLICT', 'Агент уже изменён. Прочитайте list_agents и проверьте назначение.',
                                  409, current=self._public_agent(target))
            if not self._public_agent(target)['connected']:
                raise DomainError('AGENT_OFFLINE', 'Агент отключён. Дождитесь нового heartbeat.', 409)
            if target['role'] == 'coordinator' and agent_id != coordinator_id:
                raise DomainError('AGENT_FORBIDDEN', 'Координатор не может назначать другого координатора.', 403)
            task = self._row(db, task_id)
            if agent_id != coordinator_id:
                if not coordinator['task_id']:
                    raise DomainError('ASSIGNMENT_REQUIRED', 'Координатор должен сначала выбрать собственную корневую задачу.')
                # A worker belongs to the coordinator's task subtree.
                cursor = task
                while cursor['id'] != coordinator['task_id']:
                    if cursor['parent_id'] is None:
                        raise DomainError('ASSIGNMENT_SCOPE', 'Задача находится вне дерева координатора.')
                    cursor = self._row(db, cursor['parent_id'])
                if target['coordinator_id'] not in (None, coordinator_id):
                    raise DomainError('AGENT_BUSY', 'Агент уже принадлежит другому координатору.', 409)
            db.execute("UPDATE agent_links SET state='obsolete',version=version+1 WHERE state!='obsolete' AND (source_id=? OR target_id=?)",(agent_id,agent_id))
            db.execute("UPDATE agents SET task_id=?,coordinator_id=?,status='waiting',activity_phase='waiting',phase_started_at=?,budget=NULL,action=?,version=version+1 WHERE id=?",
                       (task_id, None if agent_id == coordinator_id else coordinator_id, now(),'Назначено: ' + task['title'], agent_id))
            self._agent_event(db, agent_id, 'assigned', 'Назначено: ' + task['title'])
            self._invalidate_agent_assignments(db)
            return self._public_agent(self._agent(db, agent_id))

    @staticmethod
    def _invalidate_agent_assignments(db):
        """Moving a task/coordinator cannot leave workers assigned outside its tree."""
        for row in db.execute('SELECT * FROM agents WHERE coordinator_id IS NOT NULL AND task_id IS NOT NULL').fetchall():
            coordinator=Store._agent(db,row['coordinator_id'])
            cursor=Store._row(db,row['task_id'])
            while cursor['id']!=coordinator['task_id'] and cursor['parent_id'] is not None:
                cursor=Store._row(db,cursor['parent_id'])
            if cursor['id']==coordinator['task_id']:continue
            db.execute("UPDATE agents SET task_id=NULL,coordinator_id=NULL,status='available',activity_phase='idle',phase_started_at=?,budget=NULL,action='Задача вышла из дерева координатора; ожидает назначения',version=version+1 WHERE id=?",(now(),row['id']))
            db.execute("UPDATE agent_links SET state='obsolete',version=version+1 WHERE state!='obsolete' AND (source_id=? OR target_id=?)",(row['id'],row['id']))
            Store._agent_event(db,row['id'],'unassigned','Назначение снято: задача вышла из дерева координатора')

    def list_agents(self):
        with self.connection() as db:
            agents = [self._public_agent(r) for r in db.execute('SELECT * FROM agents ORDER BY registered_at,id')]
            events = [dict(r) for r in db.execute('SELECT * FROM agent_events ORDER BY id DESC LIMIT 50')]
            for event in events:event['metadata']=json.loads(event['metadata'])
            from .agent_graph import project_graph
            graph=project_graph(db,agents)
            return {'agents': agents, 'events': events, **graph, 'heartbeat_timeout': 30, 'revision': self._revision(db)}

    def connect_agents(self, actor_id, owner, source_id, target_id, kind, expected_source_version, expected_target_version, message=''):
        """Record a declared dependency or actual handoff, never a scheduler command."""
        if kind not in ('dependency','handoff') or source_id==target_id:
            raise DomainError('VALIDATION','Связь dependency/handoff соединяет разных агентов.')
        if not isinstance(message,str) or len(message)>500:
            raise DomainError('VALIDATION','Сообщение связи — до 500 символов.')
        with self.connection(write=True) as db:
            actor=self._agent(db,actor_id);self._owner(actor,owner)
            source=self._agent(db,source_id);target=self._agent(db,target_id)
            for agent, version in ((source,expected_source_version),(target,expected_target_version)):
                self._check_version(self._public_agent(agent),version)
                if not self._public_agent(agent)['connected'] or not agent['task_id']:
                    raise DomainError('ASSIGNMENT_REQUIRED','Оба агента должны быть на связи и назначены.',409)
            coordinator_id=actor_id if actor['role']=='coordinator' else actor['coordinator_id']
            if (not self._public_agent(actor)['connected'] or not coordinator_id or
                    actor['role']!='coordinator' and (kind!='handoff' or source_id!=actor_id) or
                    any(a['id']!=coordinator_id and a['coordinator_id']!=coordinator_id for a in (source,target))):
                raise DomainError('AGENT_FORBIDDEN','Координатор задаёт зависимости своих агентов; результат передаёт его отправитель.',403)
            coordinator=self._agent(db,coordinator_id)
            if not coordinator['task_id']:
                raise DomainError('ASSIGNMENT_REQUIRED','У координатора нет цели.',409)
            for agent in (source,target):
                cursor=self._row(db,agent['task_id'])
                while cursor['id']!=coordinator['task_id']:
                    if cursor['parent_id'] is None:raise DomainError('ASSIGNMENT_SCOPE','Связь выходит за дерево координатора.',409)
                    cursor=self._row(db,cursor['parent_id'])
                if agent['run_id']!=actor['run_id']:
                    raise DomainError('ASSIGNMENT_SCOPE','Нельзя соединять разные контексты прогонов.',409)
            self._no_question(db,coordinator['task_id'])
            if kind=='dependency':
                cycle=db.execute('''WITH RECURSIVE reachable(id) AS (
                    SELECT ? UNION SELECT l.target_id FROM agent_links l JOIN reachable r ON l.source_id=r.id
                    WHERE l.kind='dependency' AND l.state='active') SELECT 1 FROM reachable WHERE id=?''',(target_id,source_id)).fetchone()
                if cycle:raise DomainError('DEPENDENCY_CYCLE','Зависимость создаёт цикл ожидания.',409)
            existing=db.execute("SELECT * FROM agent_links WHERE source_id=? AND target_id=? AND kind=? AND state='active'",(source_id,target_id,kind)).fetchone()
            if existing:return dict(existing)
            if kind=='dependency':
                db.execute("UPDATE agent_links SET state='obsolete',version=version+1 WHERE source_id=? AND target_id=? AND kind='handoff' AND state='active'",(target_id,source_id))
            # A recorded delivery resolves the receiver's corresponding wait.
            if kind=='handoff':
                db.execute("UPDATE agent_links SET state='fulfilled',version=version+1 WHERE source_id=? AND target_id=? AND kind='dependency' AND state='active'",(target_id,source_id))
            identifier=str(uuid4())
            db.execute('INSERT INTO agent_links(id,source_id,target_id,kind,source_task_id,target_task_id,created_at,message) VALUES (?,?,?,?,?,?,?,?)',
                       (identifier,source_id,target_id,kind,source['task_id'],target['task_id'],now(),message.strip()))
            db.execute('UPDATE agents SET version=version+1 WHERE id IN (?,?)',(source_id,target_id))
            self._agent_event(db,actor_id,kind,message.strip() or ('Ожидает результат' if kind=='dependency' else 'Передан результат'),
                              source_id=source_id,target_id=target_id,link_id=identifier)
            return dict(db.execute('SELECT * FROM agent_links WHERE id=?',(identifier,)).fetchone())

    @staticmethod
    def _workspace(db):
        # The legacy row is retained for lossless migration, but is no longer an
        # access switch. Human controls and agent task operations have separate rights.
        return {'mode':'agent','mode_version':1,'workflow':'agents_native',
                'capabilities':{'user':{'create_goals':True,'edit_tasks':False,'review_results':True},
                                'agent':{'edit_tasks':True,'review_results':False,'complete_goals':False}}}

    def workspace(self):
        with self.connection() as db:
            return {**self._workspace(db), 'revision': self._revision(db)}

    def set_mode(self, mode, expected_version):
        raise DomainError('WORKFLOW_FIXED','Kaibain работает только с агентами. Пользователь задаёт цели и оценивает результаты; переключение режимов удалено.',409)

    def _check_write(self, db):
        if self.source!='agent':
            raise DomainError('AGENT_ONLY','Задачами и статусами управляют агенты. Пользователь задаёт цели и принимает результаты.',403)
        if self.run_id:
            run = db.execute('SELECT status FROM agent_runs WHERE id=?', (self.run_id,)).fetchone()
            if run is None or run['status'] != 'running':
                raise DomainError('RUN_STOPPED', 'Прогон остановлен или завершён; новые изменения через его MCP запрещены.', 409)

    @contextmanager
    def connection(self, write=False, *, control=False):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=10000')
        try:
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            if write and not control:
                self._check_write(db)
            yield db
            db.commit()
        except sqlite3.OperationalError as exc:
            db.rollback()
            if 'locked' in str(exc):
                raise DomainError('BUSY', 'Хранилище занято. Повторите запрос.', 503) from exc
            raise
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _row(db, task_id):
        row = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        if row is None:
            raise DomainError('NOT_FOUND', 'Задача не найдена или уже удалена.', 404)
        return dict(row)

    @staticmethod
    def _enrich(db, task):
        total, completed = db.execute(
            "SELECT count(*), coalesce(sum(status='done'),0) FROM tasks WHERE parent_id=?", (task['id'],)
        ).fetchone()
        report=Store._latest_goal_result(db,task['id'])
        project = db.execute('SELECT project_id FROM goal_projects WHERE task_id=?', (task['id'],)).fetchone()
        needs_input=bool(db.execute("SELECT 1 FROM goal_questions WHERE task_id=? AND status='open'",(task['id'],)).fetchone())
        return {**task, 'progress': {'total': total, 'completed': completed},
                'goal_result':Store._public_goal_result(report) if report else None, 'project_id': project[0] if project else None,
                'needs_input':needs_input,'requirements_version':Store._requirements_version(db,task['id'])}

    @staticmethod
    def _revision(db):
        return db.execute('SELECT revision FROM meta WHERE id=1').fetchone()[0]

    def revision(self):
        with self.connection() as db:
            return self._revision(db)

    @staticmethod
    def _check_version(task, expected):
        if type(expected) is not int or expected < 1:
            raise DomainError('VALIDATION', 'Требуется expected_version из последнего чтения задачи.')
        if task['version'] != expected:
            raise DomainError('CONFLICT', 'Задача уже изменена. Прочитайте актуальную версию и согласуйте изменения.',
                              409, current=task)

    @staticmethod
    def _validate(fields):
        allowed = {'title', 'description', 'status', 'deadline', 'parent_id', 'project_id'}
        if not isinstance(fields, dict) or set(fields) - allowed:
            raise DomainError('VALIDATION', 'Неизвестные поля задачи.')
        if 'title' in fields:
            if not isinstance(fields['title'], str) or not fields['title'].strip() or len(fields['title']) > 300:
                raise DomainError('VALIDATION', 'Название должно содержать от 1 до 300 символов.')
            fields['title'] = fields['title'].strip()
        if 'description' in fields and (not isinstance(fields['description'], str) or len(fields['description']) > 100000):
            raise DomainError('VALIDATION', 'Описание должно быть текстом длиной до 100 000 символов.')
        if 'status' in fields and fields['status'] not in STATUSES:
            raise DomainError('VALIDATION', 'Статус: planned, in_progress, review или done.')
        if 'deadline' in fields:
            valid_date(fields['deadline'])
        if 'parent_id' in fields and fields['parent_id'] is not None and not isinstance(fields['parent_id'], str):
            raise DomainError('VALIDATION', 'parent_id должен быть идентификатором или null.')
        if 'project_id' in fields and fields['project_id'] is not None and not isinstance(fields['project_id'], str):
            raise DomainError('VALIDATION', 'project_id должен быть идентификатором или null.')

    def _parent(self, db, task_id, parent_id):
        seen = {task_id}
        while parent_id is not None:
            if parent_id in seen:
                raise DomainError('CYCLE', 'Нельзя назначить родителем саму задачу или её потомка.')
            seen.add(parent_id)
            parent_id = self._row(db, parent_id)['parent_id']

    @staticmethod
    def _touch(db, ids):
        # Descendant changes invalidate ancestors too, protecting subtree deletion
        # against concurrent additions, moves and edits after the user's preview.
        visited = set()
        for task_id in ids:
            while task_id is not None and task_id not in visited:
                visited.add(task_id)
                row = db.execute('SELECT parent_id FROM tasks WHERE id=?', (task_id,)).fetchone()
                if row is None:
                    break
                db.execute('UPDATE tasks SET version=version+1, updated_at=? WHERE id=?', (now(), task_id))
                task_id = row[0]
        db.execute('UPDATE meta SET revision=revision+1 WHERE id=1')

    def get(self, task_id):
        with self.connection() as db:
            return self._enrich(db, self._row(db, task_id))

    def list(self, status=None, deadline_from=None, deadline_to=None, parent_id=None, roots_only=False):
        if status is not None and status not in STATUSES:
            raise DomainError('VALIDATION', 'Неизвестный статус.')
        valid_date(deadline_from)
        valid_date(deadline_to)
        if deadline_from and deadline_to and deadline_from > deadline_to:
            raise DomainError('VALIDATION', 'Начало диапазона позже конца.')
        if roots_only and parent_id is not None:
            raise DomainError('VALIDATION', 'Нельзя одновременно задать roots_only и parent_id.')
        clauses, args = [], []
        for field, op, value in [('status', '=', status), ('deadline', '>=', deadline_from),
                                 ('deadline', '<=', deadline_to), ('parent_id', '=', parent_id)]:
            if value is not None:
                clauses.append(f'{field} {op} ?')
                args.append(value)
        if roots_only:
            clauses.append('parent_id IS NULL')
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with self.connection() as db:
            # Guest clocks may give several inserts the same microsecond. UUIDs
            # would then shuffle siblings; SQLite insertion order keeps the
            # visible tree stable across clients and database reopen/backup.
            rows = db.execute('SELECT * FROM tasks' + where + ' ORDER BY created_at, rowid, id', args).fetchall()
            counts = {r[0]: (r[1], r[2]) for r in db.execute(
                "SELECT parent_id,count(*),sum(status='done') FROM tasks GROUP BY parent_id")}
            tasks = []
            project_index = dict(db.execute('SELECT task_id,project_id FROM goal_projects'))
            waiting={row[0] for row in db.execute("SELECT task_id FROM goal_questions WHERE status='open'")}
            requirements=dict(db.execute('SELECT task_id,requirements_version FROM goal_dialogues'))
            for row in rows:
                total, completed = counts.get(row['id'], (0, 0))
                tasks.append({**dict(row), 'progress': {'total': total, 'completed': completed},
                              'project_id': project_index.get(row['id']), 'needs_input':row['id'] in waiting,
                              'requirements_version':requirements.get(row['id'],0)})
            reports=[self._public_goal_result(r) for r in db.execute('''SELECT r.* FROM goal_results r
                WHERE NOT EXISTS (SELECT 1 FROM goal_results newer WHERE newer.task_id=r.task_id
                AND (newer.created_at>r.created_at OR (newer.created_at=r.created_at AND newer.rowid>r.rowid)))
                ORDER BY r.created_at DESC,r.rowid DESC''')]
            return {'tasks': tasks, 'goal_results':reports, **self._workspace(db), 'revision': self._revision(db)}

    def tree(self, task_id):
        # Flat preorder + depth is a lossless tree without JSON recursion limits.
        snapshot = self.list()
        index = {t['id']: t for t in snapshot['tasks']}
        if task_id not in index:
            raise DomainError('NOT_FOUND', 'Задача не найдена.', 404)
        children = {}
        for task in snapshot['tasks']:
            children.setdefault(task['parent_id'], []).append(task)
        stack, nodes = [(index[task_id], 0)], []
        while stack:
            task, depth = stack.pop()
            nodes.append({**task, 'depth': depth})
            stack.extend((t, depth + 1) for t in reversed(children.get(task['id'], [])))
        return {'root_id': task_id, 'nodes': nodes, 'revision': snapshot['revision']}

    def create(self, title, description='', status='planned', deadline=None, parent_id=None, project_id=None):
        if project_id is not None and (self.source != 'user' or parent_id is not None):
            raise DomainError('FORBIDDEN', 'Проект цели выбирает пользователь.', 403)
        if self.source=='user' and (parent_id is not None or status!='planned'):
            raise DomainError('AGENT_ONLY','Пользователь создаёт только новую цель. Подзадачи и статусы назначают агенты.',403)
        if self.source=='agent' and parent_id is None and status=='done':
            raise DomainError('GOAL_REVIEW_REQUIRED','Завершение цели требует приёмки пользователем. Передайте результат на ревью.',403)
        fields = dict(title=title, description=description, status=status, deadline=deadline, parent_id=parent_id)
        self._validate(fields)
        task_id, timestamp = str(uuid4()), now()
        with self.connection(write=True,control=self.source=='user') as db:
            self._parent(db, task_id, parent_id)
            self._no_question(db,parent_id)
            if project_id is not None:
                self._project(db, project_id, active=True)
            db.execute('INSERT INTO tasks (id,title,description,status,deadline,parent_id,created_at,updated_at,version) VALUES (?,?,?,?,?,?,?,?,1)',
                       (task_id, fields['title'], description, status, deadline, parent_id, timestamp, timestamp))
            if project_id is not None:
                db.execute('INSERT INTO goal_projects VALUES (?,?)', (task_id, project_id))
            self._touch(db, [parent_id])
            return self._enrich(db, self._row(db, task_id))

    def update(self, task_id, expected_version, changes):
        changes = dict(changes) if isinstance(changes, dict) else changes
        self._validate(changes)
        if not changes:
            raise DomainError('VALIDATION', 'Укажите хотя бы одно поле для изменения.')
        with self.connection(write=True,control=self.source=='user') as db:
            current = self._row(db, task_id)
            if self.source=='agent' and 'project_id' in changes:
                raise DomainError('FORBIDDEN', 'Проект цели выбирает пользователь.', 403)
            if self.source=='user' and (current['parent_id'] is not None or set(changes)-{'title','description','deadline','project_id'}):
                raise DomainError('AGENT_ONLY','Пользователь может уточнять только данные ещё не начатой цели. Статусы и подзадачи ведут агенты.',403)
            self._check_version(current, expected_version)
            self._no_question(db,task_id)
            if changes.get('parent_id') is not None:self._no_question(db,changes['parent_id'])
            if (self.source=='agent' and changes.get('parent_id',current['parent_id']) is None and
                    changes.get('status',current['status'])=='done' and
                    (current['parent_id'] is not None or current['status']!='done' or 'status' in changes)):
                raise DomainError('GOAL_REVIEW_REQUIRED','Цель завершает пользователь при приёмке результата.',403)
            if self.source=='agent' and current['parent_id'] is None and current['status']=='done' and changes.get('status','done')!='done':
                raise DomainError('GOAL_ACCEPTED_LOCKED','Цель уже принята. Для новой работы пользователь задаёт новую цель.',409)
            if self.source=='user':
                if (current['status']!='planned' or db.execute('SELECT 1 FROM tasks WHERE parent_id=?',(task_id,)).fetchone() or
                        db.execute("SELECT 1 FROM agent_runs WHERE task_id=? AND status IN ('queued','running','cancelling')",(task_id,)).fetchone()):
                    raise DomainError('GOAL_STARTED','Цель уже передана агентам. Используйте замечания при ревью результата.',409)
            if 'parent_id' in changes:
                self._parent(db, task_id, changes['parent_id'])
                if current['parent_id'] is None and changes['parent_id'] is not None:
                    raise DomainError('GOAL_PARENT_LOCKED','Цель верхнего уровня нельзя превратить в подзадачу.',403)
            if 'project_id' in changes:
                project_id = changes.pop('project_id')
                if project_id is not None:
                    binding = db.execute('SELECT project_id FROM goal_projects WHERE task_id=?', (task_id,)).fetchone()
                    self._project(db, project_id, active=not binding or binding[0] != project_id)
                    db.execute('INSERT INTO goal_projects VALUES (?,?) ON CONFLICT(task_id) DO UPDATE SET project_id=excluded.project_id',
                               (task_id, project_id))
                else:
                    db.execute('DELETE FROM goal_projects WHERE task_id=?', (task_id,))
            assignments = ','.join(f'{key}=?' for key in changes)
            if assignments:
                db.execute(f'UPDATE tasks SET {assignments} WHERE id=?', (*changes.values(), task_id))
            if 'parent_id' in changes:self._invalidate_agent_assignments(db)
            self._touch(db, [task_id, current['parent_id']])
            return self._enrich(db, self._row(db, task_id))

    def delete(self, task_id, expected_version, delete_subtree=False):
        if type(delete_subtree) is not bool:
            raise DomainError('VALIDATION', 'delete_subtree должен быть boolean.')
        with self.connection(write=True) as db:
            task = self._row(db, task_id)
            self._check_version(task, expected_version)
            self._no_question(db,task_id)
            rows = db.execute('''WITH RECURSIVE subtree(id,depth) AS (
                SELECT id,0 FROM tasks WHERE id=? UNION ALL
                SELECT t.id,s.depth+1 FROM tasks t JOIN subtree s ON t.parent_id=s.id
            ) SELECT id FROM subtree ORDER BY depth DESC''', (task_id,)).fetchall()
            if len(rows) > 1 and not delete_subtree:
                raise DomainError('SUBTREE_CONFIRMATION_REQUIRED',
                                  'Будут удалены задача и все её подзадачи. Укажите delete_subtree=true.',
                                  409, descendant_count=len(rows)-1)
            ids = [r[0] for r in rows]
            affected = db.execute('''WITH RECURSIVE subtree(id) AS (
                SELECT id FROM tasks WHERE id=? UNION ALL
                SELECT t.id FROM tasks t JOIN subtree s ON t.parent_id=s.id
            ) SELECT a.id FROM agents a JOIN subtree s ON a.task_id=s.id''', (task_id,)).fetchall()
            for agent in affected:
                db.execute("UPDATE agent_links SET state='obsolete',version=version+1 WHERE state!='obsolete' AND (source_id=? OR target_id=?)",(agent[0],agent[0]))
                db.execute("""UPDATE agents SET task_id=NULL,coordinator_id=NULL,status='available',
                              activity_phase='idle',phase_started_at=?,budget=NULL,
                              action='Задача удалена; ожидает назначения',version=version+1 WHERE id=?""", (now(),agent[0]))
                self._agent_event(db, agent[0], 'unassigned', 'Задача удалена; назначение снято')
            db.executemany('DELETE FROM tasks WHERE id=?', [(i,) for i in ids])
            self._touch(db, [task['parent_id']])
            return {'deleted_ids': ids, 'deleted_count': len(ids)}
