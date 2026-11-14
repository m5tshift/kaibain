"""Agent-first bootstrap and detached lifecycle, using only the standard library."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

from .store import ROOT

LOCAL = ROOT / '.local'
_CHILDREN = {}


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


@contextmanager
def launch_lock():
    LOCAL.mkdir(exist_ok=True)
    with (LOCAL / 'launch.lock').open('a+b') as lock:
        lock.seek(0)
        if not lock.read(1):
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('Another launcher is running. Retry when it finishes.') from None
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == 'nt':
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def health(port):
    try:
        # Loopback must not be sent through an agent's HTTP proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f'http://127.0.0.1:{port}/api/health', timeout=1) as response:
            return json.load(response)
    except (OSError, ValueError, urllib.error.URLError):
        return None


def owned_health(record):
    current = health(record['port'])
    return current if current and current.get('app') == 'kaibain' and Path(current.get('repo', '')).resolve() == ROOT.resolve() and current.get('instance') == record.get('instance') and current.get('database') == record['database'] else None


def prepare_environment():
    if sys.version_info < (3, 11):
        raise RuntimeError('Python 3.11+ is required. Run this command with a supported Python.')
    environment = ROOT / '.venv'
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not environment.exists():
        subprocess.run([sys.executable, '-m', 'venv', str(environment)], check=True)
    if not python.is_file():
        raise RuntimeError('Existing .venv is incomplete or belongs to another OS. Rename it and retry; it was not deleted.')
    digest = hashlib.sha256((ROOT / 'requirements.txt').read_bytes()).hexdigest()
    marker = LOCAL / 'requirements.sha256'
    probe = subprocess.run([str(python), '-c', 'from mcp.server.fastmcp import FastMCP'], capture_output=True)
    if probe.returncode or not marker.exists() or marker.read_text() != digest:
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(ROOT / 'requirements.txt')], check=True, stdout=sys.stderr)
        marker.write_text(digest)
    return python


def configure(python, database):
    args = [str(ROOT / 'run.py'), 'mcp', '--db', str(database)]
    entry = {'type': 'stdio', 'command': str(python), 'args': args}
    target = ROOT / '.mcp.json'
    config = json.loads(target.read_text(encoding='utf-8')) if target.exists() else {}
    if not isinstance(config, dict) or not isinstance(config.get('mcpServers', {}), dict):
        raise RuntimeError('Existing .mcp.json has an invalid structure; it was not changed.')
    servers = config.setdefault('mcpServers', {})
    old = servers.get('kaibain')
    generated = LOCAL / 'claude.mcp.json'
    previous = json.loads(generated.read_text(encoding='utf-8'))['mcpServers']['kaibain'] if generated.exists() else None
    if old is not None and old != entry and old != previous:
        raise RuntimeError('A different kaibain server is already configured in .mcp.json; it was not overwritten. Rename that entry before retrying.')
    servers['kaibain'] = entry
    write_json(target, config)
    write_json(generated, {'mcpServers': {'kaibain': entry}})
    # JSON string escaping also produces valid TOML basic strings/arrays.
    (LOCAL / 'codex.toml').write_text('[mcp_servers.kaibain]\ncommand = ' + json.dumps(str(python)) + '\nargs = ' + json.dumps(args) + '\nstartup_timeout_sec = 20\ntool_timeout_sec = 30\n', encoding='utf-8')
    return {'claude_config': str(target), 'codex_config': str(LOCAL / 'codex.toml'),
            'codex_add_argv': ['codex', 'mcp', 'add', 'kaibain', '--', str(python), *args]}


def manage(mode, port, db):
    try:
        with launch_lock():
            runtime = LOCAL / 'runtime.json'
            record = json.loads(runtime.read_text(encoding='utf-8')) if runtime.exists() else None
            active = owned_health(record) if record else None
            if mode == 'status':
                print(json.dumps({'ok': True, 'running': bool(active), 'url': f'http://localhost:{record["port"]}' if active else None}))
                return
            if mode == 'stop':
                if active:
                    request = urllib.request.Request(f'http://127.0.0.1:{record["port"]}/api/shutdown', method='POST',
                                                     headers={'X-Kaibain-Control': record['token']})
                    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=3) as response:
                        json.load(response)
                    for _ in range(50):
                        if not owned_health(record):
                            break
                        time.sleep(.1)
                    else:
                        raise RuntimeError('Server has not stopped yet; retry status. No process was killed.')
                if runtime.exists():
                    runtime.unlink()
                child = _CHILDREN.pop(record.get('instance'), None) if record else None
                if child:
                    child.wait(timeout=5)
                print(json.dumps({'ok': True, 'running': False}))
                return
            database = Path(db or os.environ.get('KAIBAIN_DB', ROOT / 'data' / 'kaibain.sqlite3')).resolve()
            if not 1 <= port <= 65535:
                raise RuntimeError('Port must be between 1 and 65535.')
            if active and (record['database'] != str(database) or record['port'] != port):
                raise RuntimeError('This repository already runs with another port/database. Use stop before changing them.')
            python = prepare_environment()
            configs = configure(python, database)
            if not active:
                if health(port):
                    raise RuntimeError(f'Port {port} already serves another process. Choose --port; nothing was stopped.')
                token = secrets.token_urlsafe(32)
                environment = {**os.environ, 'KAIBAIN_CONTROL_TOKEN': token, 'PYTHONUNBUFFERED': '1'}
                options = {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS} if os.name == 'nt' else {'start_new_session': True}
                with (LOCAL / 'server.log').open('ab') as log:
                    process = subprocess.Popen([str(python), str(ROOT/'run.py'), 'web', '--port', str(port), '--db', str(database)],
                                               cwd=ROOT, env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=log, **options)
                record = {'pid': process.pid, 'port': port, 'database': str(database), 'token': token,
                          'instance': hashlib.sha256(token.encode()).hexdigest()}
                _CHILDREN[record['instance']] = process
                write_json(runtime, record)
                os.chmod(runtime, 0o600)
                for _ in range(100):
                    if owned_health(record):
                        break
                    if process.poll() is not None:
                        raise RuntimeError(f'Server exited. Inspect {LOCAL / "server.log"}; the port may be occupied.')
                    time.sleep(.1)
                else:
                    raise RuntimeError(f'Server did not become ready. Inspect {LOCAL / "server.log"}; use status/stop to manage it.')
            print(json.dumps({'ok': True, 'running': True, 'reused': bool(active), 'url': f'http://localhost:{port}',
                              'database': str(database), 'log': str(LOCAL/'server.log'), **configs}, ensure_ascii=False, indent=2))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from exc
