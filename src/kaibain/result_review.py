"""Read frozen results and publish a human-approved export as a separate Git branch.

No checkout, project commands, filters, hooks, merge, or network. Git plumbing uses
a private index. The deterministic commit makes a retry after DB/ref interruption
recognizable; an existing unrelated ref is never replaced.
"""
from datetime import datetime
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

from .store import DomainError, Store, now

LIMIT = 2 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fail(message):
    raise DomainError('ARTIFACT_CONFLICT', message, 409)


def regular_bytes(root, name, limit=LIMIT):
    """Reject links/junctions at every level, including the configured root."""
    path = root / name
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
            fail('Файл или его каталог заменён ссылкой: ' + name)
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            fail('Файл недоступен или превышает лимит: ' + name)
        with path.open('rb') as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            fail('Файл превышает лимит: ' + name)
        return data
    except OSError:
        fail('Не удалось прочитать файл: ' + name)


class Git:
    def __init__(self, repo):
        self.repo = Path(repo).resolve()
        self.env = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}
        self.env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_NO_LAZY_FETCH='1', GIT_NO_REPLACE_OBJECTS='1',
                        GIT_TERMINAL_PROMPT='0', GIT_ALLOW_PROTOCOL='', GIT_LITERAL_PATHSPECS='1')

    def call(self, *args, data=None, env=None, optional=False, limit=8 * LIMIT):
        argv = ['git', '--no-optional-locks', '-c', 'core.hooksPath='+os.devnull,
                '-c', 'core.fsmonitor=false', '-c', 'protocol.allow=never',
                '-c', 'credential.helper=', '-c', 'commit.gpgsign=false',
                '-c', 'core.splitIndex=false', '-c', 'core.sparseCheckout=false',
                '-c', 'i18n.commitEncoding=utf-8', '-C', str(self.repo), *args]
        try:
            with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as output:
                source.write(data or b''); source.seek(0)
                process = subprocess.Popen(argv, stdin=source, stdout=output, stderr=subprocess.DEVNULL,
                    env={**self.env, **(env or {})},
                    **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
                try:
                    code = process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()
                    raise DomainError('PROJECT_GIT_TIMEOUT', 'Git не ответил за 5 секунд.', 503) from None
                output.seek(0); value = output.read(limit + 1)
        except OSError:
            raise DomainError('PROJECT_UNAVAILABLE', 'Git или репозиторий недоступен.', 409) from None
        if len(value) > limit:
            raise DomainError('PROJECT_LIMIT', 'Ответ Git превышает допустимый размер.', 409)
        if code:
            if optional and code == 1:
                return None
            raise DomainError('PROJECT_GIT_ERROR', 'Git отклонил операцию '+args[0]+'. Проверьте доступ к репозиторию и коммиту.', 409)
        return value

    def tree(self, ref):
        tree = {}
        for entry in self.call('ls-tree', '-r', '-z', ref).split(b'\0'):
            if not entry: continue
            metadata, name = entry.split(b'\t', 1)
            mode, kind, oid = metadata.decode('ascii').split()
            tree[name.decode('utf-8')] = (mode, kind, oid)
        return tree

    def ref(self, branch):
        ref = 'refs/heads/'+branch
        for line in self.call('for-each-ref', '--format=%(refname) %(objectname)', ref).decode('ascii').splitlines():
            name, oid = line.split()
            if name == ref: return oid
        return None


def bundle(store, run):
    profile = Store.validate_project(run['project'])
    verification = (run['result'] or {}).get('verification', {})
    if verification.get('status') != 'passed' or verification.get('tests_run') is not True:
        fail('У результата нет подтверждения обязательных проверок.')
    folder = store.path.parent / '.agent-runs' / run['id'] / 'development'
    raw = regular_bytes(folder, 'result/manifest.json', 65536)
    if digest(raw) != verification.get('manifest_sha256'):
        fail('Manifest изменился после публикации результата или его хеш не сохранён.')
    try:
        manifest = json.loads(raw)
        if (manifest['base_commit'] != profile['commit'] or manifest.get('deleted') or
                manifest.get('backend') != 'microsandbox' or manifest.get('isolation') != 'microvm'):
            fail('Экспорт не соответствует исходному коммиту или поддерживаемому формату microVM.')
        patch = regular_bytes(folder, 'result/changes.diff')
        if digest(patch) != manifest['patch_sha256'] or digest(patch) != verification.get('patch_sha256'):
            fail('Diff изменился после проверки.')
        entries = manifest['files']
        if not isinstance(entries, list) or not 1 <= len(entries) <= 40:
            fail('Некорректный список экспортированных файлов.')
        values = {}; total = 0
        for entry in entries:
            name = entry['path']
            if (name not in set(profile['files'] + profile['writable']) or name in values or
                    any(part.lower() in ('.git', '.hg', '.svn') or part.endswith(('.', ' ')) for part in name.split('/'))):
                fail('Экспорт содержит повтор или неразрешённый путь.')
            data = regular_bytes(folder, 'result/files/'+name)
            total += len(data)
            if total > LIMIT or len(data) != entry['bytes'] or digest(data) != entry['sha256']:
                fail('Изменились байты экспорта: ' + name)
            values[name] = data
        if not set(profile['files']) <= set(values):
            fail('В экспорте отсутствуют исходные файлы.')
        evidence = None
        if verification.get('report_sha256'):
            report = regular_bytes(folder, 'report.json')
            if digest(report) != verification['report_sha256']:
                fail('Протокол проверок изменился после публикации.')
            evidence = json.loads(report)
            clean = evidence['clean_check']
            if (evidence['worker_checks']['exit_code'] != 0 or clean['checks']['exit_code'] != 0 or
                    clean['verified_patch_sha256'] != digest(patch) or
                    clean['file_hashes'] != {name: digest(data) for name, data in values.items()}):
                fail('Протокол повторной проверки не соответствует экспорту.')
        return profile, manifest, values, evidence
    except (KeyError, TypeError, ValueError, UnicodeError):
        fail('Некорректный формат сохранённого результата.')


def inspect_export(store, run):
    profile, manifest, values, evidence = bundle(store, run)
    git = Git(profile['repo'])
    root = git.call('rev-parse', '--show-toplevel').decode('utf-8').strip()
    if Path(root).resolve() != Path(profile['repo']).resolve():
        raise DomainError('PROJECT_ROOT', 'Профиль должен указывать корень рабочего Git-репозитория.', 409)
    base = git.tree(profile['commit']); changed = []; expected = dict(base)
    algorithm = 'sha1' if len(profile['commit']) == 40 else 'sha256'
    for name, data in values.items():
        old = base.get(name)
        if old and old[0] not in ('100644', '100755'):
            fail('Экспорт заменяет ссылку или подмодуль: '+name)
        if name in profile['files'] and not old:
            fail('Исходный файл отсутствует в закреплённом коммите: '+name)
        oid = hashlib.new(algorithm, b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        if old and oid == old[2]: continue
        if name not in profile['writable']:
            fail('Изменён файл за пределами разрешённых правок: '+name)
        # Cross-platform filename collisions must not produce an unusable branch.
        if not old and any(p.casefold() == name.casefold() or p.casefold().startswith(name.casefold()+'/') or
                           name.casefold().startswith(p.casefold()+'/') for p in expected):
            fail('Путь конфликтует с существующим файлом или каталогом: '+name)
        before = git.call('cat-file', 'blob', old[2], limit=LIMIT) if old else b''
        try:
            lines = list(difflib.unified_diff(before.decode('utf-8').splitlines(), data.decode('utf-8').splitlines(),
                fromfile='a/'+name if old else '/dev/null', tofile='b/'+name, lineterm=''))
            patch = '\n'.join(lines)
            if before != data and not patch: patch = 'Изменены переводы строк или завершающий перевод строки.'
            binary = False
        except UnicodeError:
            patch = 'Двоичный файл: текстовое сравнение недоступно.'; binary = True
        changed.append({'path': name, 'kind': 'modified' if old else 'added', 'bytes': len(data),
                        'sha256': digest(data), 'diff': patch[:160000], 'truncated': len(patch)>160000,
                        'binary': binary, 'old': old, 'oid': oid})
        expected[name] = (old[0] if old else '100644', 'blob', oid)
    branch = 'kaibain/'+run['id']
    message = 'Kaibain result '+run['id']+'\n\nManifest-SHA256: '+digest(json.dumps(manifest,sort_keys=True).encode())+'\n'
    head = git.call('rev-parse', '--verify', 'HEAD^{commit}').decode('ascii').strip()
    existing = git.ref(branch)
    matching = False
    if existing:
        commit = git.call('cat-file', 'commit', existing).decode('utf-8')
        headers, body = commit.split('\n\n', 1)
        matching = (body == message and [s for s in headers.splitlines() if s.startswith('parent ')] ==
                    ['parent '+profile['commit']] and git.tree(existing) == expected)
    conflicts = []
    if head != profile['commit']:
        conflicts.append({'code':'HEAD_CHANGED','message':'Текущий коммит отличается от исходного. Автоматического переноса на новую базу нет.'})
    if existing and not matching:
        conflicts.append({'code':'BRANCH_EXISTS','message':'Имя новой ветки уже занято другим результатом: '+branch})
    if not changed:
        conflicts.append({'code':'NO_CHANGES','message':'В экспорте нет изменений для применения.'})
    # Compare the real index and affected working files without invoking clean filters.
    index = {}
    names = [f['path'] for f in changed]
    if names:
        for item in git.call('ls-files', '--stage', '-z', '--', *names).split(b'\0'):
            if not item: continue
            meta, name = item.split(b'\t', 1); mode, oid, stage = meta.decode('ascii').split()
            index.setdefault(name.decode('utf-8'), []).append((mode, oid, stage))
    work = {}
    for item in changed:
        name, old = item['path'], item['old']
        expected_index = [(old[0], old[2], '0')] if old else []
        if index.get(name, []) != expected_index:
            conflicts.append({'code':'INDEX_CHANGED','path':name,'message':'В индексе есть изменения: '+name})
        path = git.repo/name
        try:
            data = regular_bytes(git.repo, name) if path.exists() or path.is_symlink() else None
            work[name] = digest(data) if data is not None else None
            before = git.call('cat-file', 'blob', old[2], limit=LIMIT) if old else None
            # Git's common Windows checkout conversion is the only normalization.
            equivalent = data == before or (data is not None and before is not None and b'\0' not in data and
                                            b'\r' not in before and data.replace(b'\r\n', b'\n') == before)
            if not equivalent:
                conflicts.append({'code':'WORKTREE_CHANGED','path':name,'message':'Есть локальные правки или файл отсутствует: '+name})
            if old and os.name != 'nt' and path.exists() and bool(path.stat().st_mode & 0o111) != (old[0]=='100755'):
                conflicts.append({'code':'MODE_CHANGED','path':name,'message':'Изменены права исполнения: '+name})
        except DomainError as error:
            conflicts.append({'code':error.code,'path':name,'message':error.message})
    token = digest(json.dumps([run['version'], manifest, head, existing, work, index, conflicts], sort_keys=True).encode())
    return {'git':git, 'profile':profile, 'manifest':manifest, 'values':values, 'evidence':evidence,
            'files':changed, 'branch':branch, 'message':message, 'head':head, 'existing':existing,
            'matching':matching, 'conflicts':conflicts, 'token':token}


def review(store, run_id):
    with store.connection() as db:
        run = store._public_run(store._run(db, run_id))
        saved = db.execute('SELECT receipt FROM run_applications WHERE run_id=?',(run_id,)).fetchone()
        receipt = json.loads(saved[0]) if saved else None
        latest = store._latest_run(db, run['task_id']) == run_id
        apply_error = None
        try: store.application_context(db, run_id, run['version'])
        except DomainError as error: apply_error = error.result()['error']
    result = {'run':run, 'latest':latest, 'receipt':receipt, 'files':[], 'integrity':False,
              'conflicts':[], 'can_apply':False, 'apply_error':apply_error, 'evidence':None}
    try:
        export = inspect_export(store, run)
        result.update(integrity=True, files=[{k:v for k,v in f.items() if k not in ('old','oid')} for f in export['files']],
                      conflicts=export['conflicts'], branch=export['branch'], source_commit=export['profile']['commit'],
                      current_commit=export['head'], token=export['token'], evidence=export['evidence'],
                      can_apply=not receipt and not apply_error and (not export['conflicts'] or export['matching']))
    except (DomainError, TypeError, ValueError, UnicodeError) as error:
        result['integrity_error'] = error.message if isinstance(error, DomainError) else 'Результат не содержит поддерживаемого экспорта.'
    return result


def apply(store, run_id, expected_version, preview_token):
    if not isinstance(preview_token, str) or not re.fullmatch('[0-9a-f]{64}', preview_token):
        raise DomainError('VALIDATION', 'Откройте предварительную проверку применения.')
    # SQLite serializes human controls against applying. The Git ref itself uses CAS.
    with store.connection(write=True, control=True) as db:
        run, receipt = store.application_context(db, run_id, expected_version)
        if receipt: return receipt
        export = inspect_export(store, run)
        if export['token'] != preview_token:
            raise DomainError('APPLY_STALE', 'Репозиторий изменился после просмотра. Обновите проверку.', 409)
        if export['conflicts'] and not export['matching']:
            raise DomainError('APPLY_CONFLICT', 'Применение остановлено. Устраните конфликты и обновите проверку.', 409,
                              conflicts=export['conflicts'])
        git = export['git']; profile = export['profile']
        if export['matching']:
            commit = export['existing']  # Recover a published ref after a DB commit interruption.
        else:
            with tempfile.TemporaryDirectory(prefix='kaibain-index-') as temp:
                env = {'GIT_INDEX_FILE':str(Path(temp)/'index')}
                git.call('read-tree', profile['commit'], env=env)
                for item in export['files']:
                    oid = git.call('hash-object', '-w', '--stdin', '--no-filters', data=export['values'][item['path']]).decode().strip()
                    if oid != item['oid']: fail('Git записал другой объект файла.')
                    mode = item['old'][0] if item['old'] else '100644'
                    git.call('update-index', '--add', '--cacheinfo', mode, oid, item['path'], env=env)
                tree = git.call('write-tree', env=env).decode().strip()
                timestamp = str(int(datetime.fromisoformat(run['reviewed_at']).timestamp()))+' +0000'
                env.update({f'GIT_{role}_{key}':value for role in ('AUTHOR','COMMITTER')
                            for key,value in [('NAME','Kaibain'),('EMAIL','kaibain@localhost'),('DATE',timestamp)]})
                commit = git.call('commit-tree', tree, '-p', profile['commit'], data=export['message'].encode(), env=env).decode().strip()
                # Re-read artifacts, HEAD, index and working files immediately before publishing.
                if inspect_export(store, run)['token'] != preview_token:
                    raise DomainError('APPLY_STALE', 'Состояние изменилось во время подготовки. Ветка не создана.', 409)
                git.call('update-ref', '--no-deref', 'refs/heads/'+export['branch'], commit, '0'*len(commit))
        receipt = {'run_id':run_id, 'repo':profile['repo'], 'branch':export['branch'], 'commit':commit,
                   'base_commit':profile['commit'], 'manifest_sha256':run['result']['verification']['manifest_sha256'],
                   'files':[f['path'] for f in export['files']], 'created_at':now()}
        db.execute('INSERT INTO run_applications VALUES (?,?)',(run_id,json.dumps(receipt)))
        store._touch(db, [])
        return receipt
