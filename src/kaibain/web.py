"""Small loopback-only HTTP adapter; no build step or frontend CDN."""
import json
import mimetypes
import sys
import os
import hmac
import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .store import DomainError, ROOT, Store
from .agent_runtime import AgentRuntime

STATIC = ROOT / 'src' / 'static'


def make_server(store, port=8765, *, runtime_config=None):
    # The adapter fixes the source; request JSON can never select it.
    store = Store(store.path, source='user')
    runtime = AgentRuntime(store, runtime_config)
    class Handler(BaseHTTPRequestHandler):
        def send(self, status, data, content_type='application/json; charset=utf-8'):
            body = json.dumps(data, ensure_ascii=False).encode() if isinstance(data, (dict, list)) else data
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)

        def guard(self):
            port = self.server.server_port
            hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
            if self.headers.get('Host') not in hosts:
                raise DomainError('FORBIDDEN', 'Допускаются только локальные запросы.', 403)
            origin = self.headers.get('Origin')
            if origin and origin not in {f'http://{h}' for h in hosts}:
                raise DomainError('FORBIDDEN', 'Запрос с другого сайта запрещён.', 403)
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                raise DomainError('FORBIDDEN', 'Запрос с другого сайта запрещён.', 403)

        def body(self):
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise DomainError('VALIDATION', 'Ожидается application/json.', 415)
            try:
                size = int(self.headers.get('Content-Length', 0))
                if not 0 < size <= 512000:
                    raise DomainError('VALIDATION', 'Недопустимый размер запроса.', 413)
                value = json.loads(self.rfile.read(size))
                if not isinstance(value, dict):
                    raise ValueError()
                return value
            except (ValueError, UnicodeError):
                raise DomainError('VALIDATION', 'Некорректный JSON-объект.') from None

        def dispatch(self):
            try:
                self.guard()
                url = urlsplit(self.path)
                path = url.path
                if self.command == 'GET':
                    if path == '/api/agent-runtime':
                        return self.send(200, {'ok': True, **runtime.snapshot()})
                    if path.startswith('/api/agent-runs/'):
                        parts=path.strip('/').split('/')
                        if len(parts)==4 and parts[3]=='review':
                            from .result_review import review
                            return self.send(200, {'ok':True, **review(store, parts[2])})
                        if len(parts)==4 and parts[3]=='recovery':
                            return self.send(200,{'ok':True,**runtime.recovery_preview(parts[2])})
                        if len(parts)==4 and parts[3]=='cleanup':
                            return self.send(200,{'ok':True,**runtime.cleanup_status(parts[2])})
                        if len(parts)==5 and parts[3]=='artifacts':
                            return self.send(200,runtime.artifact(parts[2],parts[4]),'text/plain; charset=utf-8')
                    if path == '/api/projects':
                        return self.send(200, {'ok': True, **store.projects()})
                    if path.startswith('/api/projects/') and len(path.strip('/').split('/')) == 3:
                        return self.send(200, {'ok': True, 'project': store.get_project(path.rsplit('/', 1)[-1])})
                    if path == '/api/agents':
                        return self.send(200, {'ok': True, **store.list_agents()})
                    if path == '/api/workspace':
                        return self.send(200, {'ok': True, **store.workspace()})
                    if path.startswith('/api/goals/') and path.endswith('/dialogue') and len(path.strip('/').split('/'))==4:
                        return self.send(200,{'ok':True,**store.goal_dialogue(path.strip('/').split('/')[2])})
                    if path == '/api/health':
                        return self.send(200, {'ok': True, 'app': 'kaibain', 'repo': str(ROOT),
                                              'database': str(store.path), 'pid': os.getpid(),
                                              'instance': hashlib.sha256(os.environ.get('KAIBAIN_CONTROL_TOKEN', '').encode()).hexdigest()})
                    if path == '/api/tasks':
                        query = {k: v[-1] for k, v in parse_qs(url.query).items()}
                        if set(query) - {'status','deadline_from','deadline_to','parent_id','roots_only'}:
                            raise DomainError('VALIDATION', 'Неизвестный фильтр.')
                        if 'roots_only' in query:
                            if query['roots_only'] not in ('true', 'false'):
                                raise DomainError('VALIDATION', 'roots_only: true или false.')
                            query['roots_only'] = query['roots_only'] == 'true'
                        return self.send(200, {'ok': True, **store.list(**query)})
                    if path == '/api/revision':
                        return self.send(200, {'ok': True, 'revision': store.revision()})
                    if path.startswith('/api/tasks/'):
                        parts = path.strip('/').split('/')
                        if len(parts) == 4 and parts[3] == 'tree':
                            return self.send(200, {'ok': True, **store.tree(parts[2])})
                        if len(parts) == 3:
                            return self.send(200, {'ok': True, 'task': store.get(parts[2])})
                    assets = {'/': 'index.html', '/index.html': 'index.html',
                              '/style.css': 'style.css', '/favicon.svg': 'favicon.svg', '/theme.js': 'theme.js',
                              '/agents.css': 'agents.css'}
                    assets['/agents-live.js'] = 'agents-live.js'
                    assets['/graph.css'] = 'graph.css'
                    assets['/recovery.js'] = 'recovery.js'
                    assets['/recovery.css'] = 'recovery.css'
                    assets['/agents-run.js'] = 'agents-run.js'
                    assets['/native.js']='native.js'
                    assets['/native.css']='native.css'
                    assets['/projects.js']='projects.js'
                    assets['/projects.css']='projects.css'
                    assets['/readiness.js']='readiness.js'
                    assets['/readiness.css']='readiness.css'
                    assets['/dialogue.js']='dialogue.js'
                    assets['/dialogue.css']='dialogue.css'
                    assets['/result-review.js']='result-review.js'
                    assets['/result-review.css']='result-review.css'
                    if path in assets or path.startswith(('/tasks/','/reviews/')) or path in ('/calendar', '/settings', '/agents','/board','/projects'):
                        file = STATIC / assets.get(path, 'index.html')
                        mime = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
                        return self.send(200, file.read_bytes(), mime + '; charset=utf-8')
                if self.command == 'POST' and path == '/api/shutdown':
                    token = os.environ.get('KAIBAIN_CONTROL_TOKEN', '')
                    supplied = self.headers.get('X-Kaibain-Control', '')
                    if not token or not hmac.compare_digest(token, supplied):
                        raise DomainError('FORBIDDEN', 'Остановка разрешена только локальному launcher.', 403)
                    self.send(200, {'ok': True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                if self.command == 'POST' and path == '/api/agent-runs':
                    body = self.body()
                    if not {'task_id','expected_version'}<=set(body) or set(body)-{'task_id','expected_version','project','project_id','expected_project_version'}:
                        raise DomainError('VALIDATION', 'Укажите task_id, expected_version и сохранённый проект с его версией либо разовый project.')
                    if not isinstance(body['task_id'], str):
                        raise DomainError('VALIDATION', 'task_id должен быть строкой.')
                    return self.send(202, {'ok': True, 'run': runtime.start(body['task_id'], body['expected_version'],
                        project=body.get('project'), project_id=body.get('project_id'), expected_project_version=body.get('expected_project_version'))})
                if self.command == 'POST' and path == '/api/agent-readiness':
                    body=self.body()
                    if set(body)-{'project','project_id','expected_project_version'}:
                        raise DomainError('VALIDATION','Укажите только проект и его версию либо разовые параметры.')
                    return self.send(200,{'ok':True,**runtime.readiness(**body,fresh=True)})
                if self.command == 'POST' and path == '/api/projects':
                    body = self.body()
                    if not {'name','profile'} <= set(body) or set(body)-{'name','description','profile'}:
                        raise DomainError('VALIDATION', 'Укажите name, profile и необязательный description.')
                    return self.send(201, {'ok': True, 'project': store.create_project(**body)})
                if path.startswith('/api/projects/'):
                    parts = path.strip('/').split('/')
                    if self.command == 'PATCH' and len(parts) == 3:
                        body = self.body()
                        if set(body) != {'expected_version','changes'}:
                            raise DomainError('VALIDATION', 'Укажите expected_version и changes проекта.')
                        return self.send(200, {'ok': True, 'project': store.update_project(parts[2], **body)})
                    if self.command == 'POST' and len(parts) == 4 and parts[3] == 'resolve':
                        body = self.body()
                        if set(body) != {'expected_version'}:
                            raise DomainError('VALIDATION', 'Укажите expected_version проекта.')
                        return self.send(200, {'ok': True, **store.resolve_project(parts[2], body['expected_version'])})
                if self.command == 'POST' and path.startswith('/api/agent-runs/'):
                    parts = path.strip('/').split('/')
                    if len(parts)==4 and parts[3]=='cleanup':
                        body=self.body()
                        if set(body)!={'expected_version'}:raise DomainError('VALIDATION','Укажите только expected_version прогона.')
                        return self.send(200,{'ok':True,'cleanup':runtime.cleanup_run(parts[2],**body)})
                    if len(parts)==4 and parts[3]=='resume':
                        body=self.body()
                        if set(body)!={'expected_version','expected_task_version','checkpoint_id'}:
                            raise DomainError('VALIDATION','Укажите версии прогона/цели и checkpoint_id.')
                        return self.send(202,{'ok':True,'run':runtime.resume(parts[2],**body)})
                    if len(parts)==4 and parts[3]=='apply':
                        from .result_review import apply
                        body=self.body()
                        if set(body)!={'expected_version','preview_token'}:
                            raise DomainError('VALIDATION','Укажите версию прогона и preview_token предварительной проверки.')
                        return self.send(200, {'ok':True, 'receipt':apply(store, parts[2], **body)})
                    if len(parts) == 4 and parts[3] in ('review', 'revise', 'stop','continue'):
                        body = self.body()
                        if parts[3] == 'stop':
                            if set(body) != {'expected_version'}:
                                raise DomainError('VALIDATION', 'Укажите только expected_version прогона.')
                            return self.send(200, {'ok': True, 'run': store.stop_agent_run(parts[2], **body)})
                        if parts[3] == 'review':
                            if set(body) != {'expected_version', 'decision', 'comment'}:
                                raise DomainError('VALIDATION', 'Укажите expected_version, decision и comment.')
                            return self.send(200, {'ok': True, 'run': store.review_agent_run(parts[2], **body)})
                        if set(body) != {'expected_version', 'expected_task_version'}:
                            raise DomainError('VALIDATION', 'Укажите expected_version прогона и expected_task_version задачи.')
                        action=runtime.continue_run if parts[3]=='continue' else runtime.revise
                        return self.send(202, {'ok': True, 'run': action(parts[2], **body)})
                if self.command=='POST' and path=='/api/goals':
                    body=self.body()
                    if 'title' not in body or set(body)-{'title','description','deadline','project_id'}:
                        raise DomainError('VALIDATION','Для цели укажите title и необязательные description, deadline, project_id.')
                    return self.send(201,{'ok':True,'goal':store.create(**body)})
                if path.startswith('/api/goals/'):
                    parts=path.strip('/').split('/')
                    if self.command=='POST' and len(parts)==4 and parts[3]=='answers':
                        body=self.body()
                        if not {'question_id','expected_version','expected_task_version','answer'}<=set(body) or set(body)-{'question_id','expected_version','expected_task_version','answer','continue_run'}:
                            raise DomainError('VALIDATION','Укажите вопрос, версии вопроса и цели, answer и необязательный continue_run.')
                        return self.send(200,{'ok':True,**runtime.answer(parts[2],**body)})
                    if self.command=='PATCH' and len(parts)==3:
                        body=self.body()
                        if set(body)!={'expected_version','changes'} or not isinstance(body['changes'],dict) or set(body['changes'])-{'title','description','deadline','project_id'}:
                            raise DomainError('VALIDATION','Укажите expected_version и данные цели в changes.')
                        return self.send(200,{'ok':True,'goal':store.update(parts[2],body['expected_version'],body['changes'])})
                    if self.command=='POST' and len(parts)==4 and parts[3]=='review':
                        body=self.body()
                        if set(body)!={'expected_version','result_id','expected_result_version','decision','comment'}:
                            raise DomainError('VALIDATION','Укажите версии цели и отчёта, result_id, decision и comment.')
                        return self.send(200,{'ok':True,'result':store.review_goal(parts[2],**body)})
                if self.command == 'PATCH' and path == '/api/workspace':
                    body = self.body()
                    if set(body) != {'mode', 'expected_version'}:
                        raise DomainError('VALIDATION', 'Укажите mode и expected_version режима.')
                    return self.send(200, {'ok': True, **store.set_mode(body['mode'], body['expected_version'])})
                if self.command == 'POST' and path == '/api/tasks':
                    body = self.body()
                    Store._validate(body)
                    if 'title' not in body:
                        raise DomainError('VALIDATION', 'Название обязательно.')
                    return self.send(201, {'ok': True, 'task': store.create(**body)})
                if path.startswith('/api/tasks/') and len(path.strip('/').split('/')) == 3:
                    task_id = path.rsplit('/', 1)[-1]
                    body = self.body()
                    if self.command == 'PATCH':
                        if set(body) - {'expected_version', 'changes'}:
                            raise DomainError('VALIDATION', 'Неизвестные поля запроса.')
                        task = store.update(task_id, body.get('expected_version'), body.get('changes'))
                        return self.send(200, {'ok': True, 'task': task})
                    if self.command == 'DELETE':
                        if set(body) - {'expected_version', 'delete_subtree'}:
                            raise DomainError('VALIDATION', 'Неизвестные поля запроса.')
                        result = store.delete(task_id, body.get('expected_version'), body.get('delete_subtree', False))
                        return self.send(200, {'ok': True, **result})
                raise DomainError('NOT_FOUND', 'Маршрут не найден.', 404)
            except DomainError as exc:
                self.send(exc.status, exc.result())
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            except Exception:
                import traceback
                traceback.print_exc(file=sys.stderr)
                self.send(500, {'ok': False, 'error': {'code': 'INTERNAL', 'message': 'Ошибка сервера. См. журнал в терминале.'}})

        do_GET = do_POST = do_PATCH = do_DELETE = dispatch

        def log_message(self, format, *args):
            # Polling should not flood the terminal.
            if not any(path in str(args) for path in ('/api/revision', '/api/agents', '/api/agent-runtime')):
                super().log_message(format, *args)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server


def run(store, port):
    server = make_server(store, port)
    print(f'Kaibain: http://localhost:{server.server_port}', flush=True)
    print(f'SQLite: {store.path}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
