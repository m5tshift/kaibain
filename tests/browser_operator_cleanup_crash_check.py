"""Opt-in Windows HTTP-owner crash during real microVM operator cleanup.

An isolated terminal run is a TRUSTED FIXTURE, not model/MCP/coding evidence.
The SDK creates a real VM in a separately owned holder scope. The actual web
cleanup helper pauses before SDK stop; killing its captured HTTP parent closes
its Job Object while the holder and VM remain live. Only explicit UI retry may
clear the durable lease. Requires a current --prepared-state; no prepare/pull.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from kaibain.agent_runtime import cleanup_owned, process_alive
from kaibain.process_scope import ProcessScope
from kaibain.store import DomainError, Store
from parallel_crash_fixture import CrashFixture
from windows_owned_process import kill_owned_interpreter
from playwright.async_api import async_playwright, expect


DRIVER = r'''
import argparse, hashlib, json, os, sys, time
from pathlib import Path
FIXED = json.loads(__FIXED_JSON__)
BASE = Path(__file__).parent
sys.path.insert(0, str(Path(FIXED['runtime']) / 'src'))
sys.path.insert(0, str(Path(FIXED['kaibain']) / 'src'))
from sandbox import SandboxManager
from microvm_backend import IMAGE

def save(path, value):
    SandboxManager._save(None, path, value)

p = argparse.ArgumentParser()
p.add_argument('--fixture-mode', choices=('terminal', 'holder'), default=None)
p.add_argument('--run-id')
p.add_argument('--cleanup', action='store_true')
p.add_argument('--output')
a = p.parse_args()
if a.cleanup:
    output = Path(a.output).resolve()
    assert output.parent.parent == BASE / '.agent-runs' and output.name == 'report.json'
    assert os.environ['KAIBAIN_RUN_ID'] == output.parent.name
    original = SandboxManager.stop
    def stop(self, identifier):
        if not (BASE / 'allow-cleanup').exists():
            live = self.inspect(identifier)
            assert live['running'] and live['run_id'] == output.parent.name
            save(BASE / 'cleanup-barrier.json', {'pid': os.getpid(), 'vm_id': identifier,
                 'run_id': live['run_id'], 'runtime_id': live['runtime_id'], 'running': True})
            deadline = time.monotonic() + 75
            while time.monotonic() < deadline:
                time.sleep(.1)
            raise RuntimeError('Fault barrier expired; first cleanup must be killed')
        return original(self, identifier)
    SandboxManager.stop = stop
    from development_web import cleanup
    cleanup(output)
elif a.fixture_mode == 'terminal':
    # Genuine short-lived fixture owner; no fabricated PID, no project execution.
    from kaibain.store import Store
    store = Store(BASE / 'tasks.sqlite3')
    store.claim_agent_run(a.run_id, os.getpid())
    store.finish_agent_run(a.run_id, 'failed', 'Trusted terminal-run fixture; no model execution',
                           failure_code='cleanup_failed')
elif a.fixture_mode == 'holder':
    folder = BASE / '.agent-runs' / a.run_id
    project = json.loads((folder / 'project.json').read_bytes())
    state = folder / 'microvms'
    state.mkdir()
    prepared = json.loads(Path(FIXED['prepared']).read_bytes())
    assert prepared['backend'] == 'microsandbox' and prepared['image'] == IMAGE
    assert prepared['worker_sha256'] == hashlib.sha256((Path(FIXED['runtime']) / 'src/sandbox_worker.py').read_bytes()).hexdigest()
    save(state / 'image.json', prepared)  # image metadata ONLY, never owner/VM state
    manager = SandboxManager(state)
    info = manager.create(project['repo'], project['commit'], project['files'],
                          run_id=a.run_id, attempt_id='http-owner-cleanup-fixture')
    try:
        result = manager.execute(info['id'], project['checks'], timeout=10)
        assert result['exit_code'] == 0 and result.get('stop_reason') is None
        live = manager.inspect(info['id'])
        assert live['running'] and live['isolation'] == 'microvm'
        save(BASE / 'holder-ready.json', {'pid': os.getpid(), 'vm': live, 'guest_check': result})
        deadline = time.monotonic() + 240
        while not (BASE / 'release-holder').exists():
            if time.monotonic() >= deadline:
                raise RuntimeError('Fixture holder deadline expired')
            time.sleep(.1)
    finally:
        manager.stop(info['id'])
else:
    raise ValueError('Only explicit fixture modes and owned cleanup are permitted')
'''

HTTP_OWNER = r'''
import json, os, sys, threading, time
from pathlib import Path
FIXED = json.loads(__FIXED_JSON__)
BASE = Path(__file__).parent
sys.path.insert(0, str(Path(FIXED['kaibain']) / 'src'))
from kaibain.store import Store
from kaibain.web import make_server
server = make_server(Store(BASE / 'tasks.sqlite3'), int(sys.argv[1]), runtime_config=BASE / 'runtime.json')
def shutdown():
    while not (BASE / ('stop-http-' + sys.argv[2])).exists(): time.sleep(.1)
    server.shutdown()
threading.Thread(target=shutdown, daemon=True).start()
ready = BASE / ('http-ready-' + sys.argv[2] + '.json')
temporary = ready.with_suffix('.tmp')
temporary.write_text(json.dumps({'pid': os.getpid(), 'port': server.server_port}), encoding='utf8')
temporary.replace(ready)
try: server.serve_forever()
finally: server.server_close()
'''


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')


def read(path):
    return json.loads(path.read_bytes())


async def until(predicate, *, timeout=45):
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise TimeoutError('Fixture observation deadline expired: ' + repr(predicate))
        await asyncio.sleep(.1)


async def check(args):
    if os.name != 'nt' or os.environ.get('KAIBAIN_RUN_ID'):
        raise RuntimeError('Windows-only isolated fixture; no inherited run context')
    if Path(sys.prefix).resolve() != (ROOT / '.venv').resolve():
        raise RuntimeError('Use the existing Kaibain venv')
    runtime = ROOT.parent / 'multi-agent-system'
    python = runtime / '.venv/Scripts/python.exe'
    prepared = args.prepared_state.resolve(strict=True) / 'image.json'
    value = read(prepared)
    assert value['backend'] == 'microsandbox'
    assert value['worker_sha256'] == hashlib.sha256((runtime / 'src/sandbox_worker.py').read_bytes()).hexdigest()
    production_config = ROOT / '.local/agent-runtime.json'
    config_hash = hashlib.sha256(production_config.read_bytes()).hexdigest()
    folder = ROOT / '.local/operator-cleanup-crash' / str(time.time_ns())
    folder.mkdir(parents=True)
    report = {'ok': False, 'platform': 'Windows/x86_64', 'real_llm_inference': False,
              'mcp_proof': False, 'terminal_run': 'trusted fixture, not actual coding execution',
              'type': 'real_http_owner_kill_real_microvm_cleanup', 'checks': [],
              'fixture_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    scopes, http, streams = [], [], []
    outsider = None
    state = None
    command = None
    env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
    env.pop('KAIBAIN_CONTROL_TOKEN', None)
    env.pop('KAIBAIN_RUN_ID', None)

    def log(name):
        stream = (folder / name).open('ab')
        streams.append(stream)
        return stream

    def snapshot():
        with store.connection() as db:
            return {name: [dict(row) for row in db.execute(f'SELECT * FROM {name} ORDER BY rowid')]
                    for name in ('tasks', 'agent_runs', 'run_checkpoints', 'goal_questions',
                                 'goal_results', 'projects')}

    def error(code, call):
        try:
            call()
        except DomainError as caught:
            assert caught.code == code, (caught.code, code)
        else:
            raise AssertionError('Expected ' + code)

    def fenced(code):
        current = store.get(goal['id'])
        error(code, lambda: store.reserve_agent_run(current['id'], current['version'], 'Fixture', command))
        error(code, lambda: store.claim_agent_run(run['id'], os.getpid()))
        with store.connection() as db:
            error(code, lambda: store.recovery_context(db, run['id'], run['version'], current['version'], None))

    async def server(attempt, port=0):
        process = subprocess.Popen([sys.executable, str(owner), str(port), str(attempt)],
            stdin=subprocess.DEVNULL, stdout=log(f'http-{attempt}.log'), stderr=subprocess.STDOUT,
            env=env, creationflags=subprocess.CREATE_NO_WINDOW)
        entry = {'process': process, 'attempt': attempt, 'ready': None}
        http.append(entry)
        path = folder / f'http-ready-{attempt}.json'
        await until(lambda: path.exists() or process.poll() is not None)
        assert process.poll() is None, 'HTTP owner exited before readiness'
        entry['ready'] = read(path)
        return entry

    async def inspect():
        return await asyncio.to_thread(observer.inspect, run['id'])

    try:
        repo = folder / 'source'
        repo.mkdir()
        (repo / 'app.py').write_bytes(b'def value():\n    return 42\n')
        (repo / 'test_app.py').write_bytes(b'import unittest\nfrom app import value\nclass Checks(unittest.TestCase):\n def test_value(self): self.assertEqual(value(),42)\n')
        git = ['git', '--no-optional-locks', '-c', 'core.hooksPath=' + os.devnull,
               '-c', 'commit.gpgsign=false', '-c', 'core.fsmonitor=false', '-c', 'core.autocrlf=false', '-C', str(repo)]
        for argv in (['init', '-q'], ['add', '.'], ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@localhost', 'commit', '-qm', 'Initial']):
            subprocess.run([*git, *argv], check=True, capture_output=True)
        commit = subprocess.check_output([*git, 'rev-parse', 'HEAD']).decode().strip()
        source_before = {p.relative_to(repo).as_posix(): p.read_bytes() for p in repo.rglob('*') if p.is_file()}
        fixed = repr(json.dumps({'runtime': str(runtime), 'kaibain': str(ROOT), 'prepared': str(prepared)}))
        driver = folder / 'trusted_cleanup_fixture.py'
        owner = folder / 'http_owner.py'
        driver.write_text(DRIVER.replace('__FIXED_JSON__', fixed), encoding='utf8')
        owner.write_text(HTTP_OWNER.replace('__FIXED_JSON__', fixed), encoding='utf8')
        command = [str(python), str(driver)]
        config = folder / 'runtime.json'
        save(config, {'kind': 'development-v1', 'label': 'Trusted cleanup crash fixture', 'command': command})
        store = Store(folder / 'tasks.sqlite3')
        goal = store.create('Isolated operator cleanup owner crash')
        child = Store(store.path, source='agent').create('Existing fixture worker', parent_id=goal['id'])
        project = {'repo': str(repo), 'commit': commit, 'files': ['app.py', 'test_app.py'],
                   'writable': ['app.py'], 'checks': ['python', '-m', 'unittest', 'discover', '-s', '.', '-v'],
                   'acceptance': ['Fixture guest returns 42; no changes requested.']}
        run = store.reserve_agent_run(goal['id'], store.get(goal['id'])['version'], 'Fixture', command, project=project)
        state = folder / '.agent-runs' / run['id']
        state.mkdir(parents=True)
        save(state / 'project.json', project)
        scope_env = {**env, 'KAIBAIN_RUN_ID': run['id']}
        terminal = ProcessScope([*command, '--fixture-mode', 'terminal', '--run-id', run['id']],
            log=log('terminal-owner.log'), cwd=str(folder), env=scope_env, receipt=state / 'scope.json')
        scopes.append(terminal)
        assert await asyncio.to_thread(terminal.process.wait, 15) == 0
        terminal.close()
        scopes.remove(terminal)
        with store.connection() as db:
            row = store._run(db, run['id'])
            terminal_pid = row['pid']
            run = store._public_run(row)
        assert not process_alive(terminal_pid) and run['status'] == 'failed'
        save(state / 'report.json', {'fixture': True, 'status': 'failed', 'model_execution': False})
        export = state / 'development/result'
        export.mkdir(parents=True)
        (export / 'app.py').write_bytes(b'# immutable fixture export\x00\xff\n')
        save(export / 'manifest.json', {'fixture': True})
        protected = [state / 'project.json', state / 'scope.json', state / 'report.json', *export.iterdir()]
        artifacts_before = {str(p.relative_to(state)): p.read_bytes() for p in protected}
        rows_before = snapshot()
        holder = ProcessScope([*command, '--fixture-mode', 'holder', '--run-id', run['id']],
            log=log('vm-holder.log'), cwd=str(folder), env=scope_env, receipt=folder / 'holder-scope.json')
        scopes.append(holder)
        outsider = subprocess.Popen([sys._base_executable, '-I', '-c', 'import time;time.sleep(400)'],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        await until(lambda: (folder / 'holder-ready.json').exists() or holder.process.poll() is not None, timeout=80)
        assert holder.process.poll() is None and (folder / 'holder-ready.json').exists()
        observer = CrashFixture(store, runtime, python, min_records=1)
        observer.owned[run['id']] = None  # SDK read-only observation, no supervisor ownership/kill
        first_vm = await inspect()
        assert len(first_vm) == 1 and first_vm[0]['running'] and first_vm[0]['run_id'] == run['id']
        report['vm_before'] = first_vm
        first = await server(1)
        base = f'http://localhost:{first["ready"]["port"]}'
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, channel=os.environ.get('PLAYWRIGHT_CHANNEL', 'msedge'))
            page = await browser.new_page(viewport={'width': 1280, 'height': 900})
            await page.goto(base + '/tasks/' + goal['id'])
            await page.get_by_role('button', name='Очистить среду', exact=True).click()
            await until(lambda: (folder / 'cleanup-barrier.json').exists())
            barrier = read(folder / 'cleanup-barrier.json')
            action = store.run_cleanup_status(run['id'])['cleanup']
            assert action['status'] == 'running'
            action_folder = state / 'operator-cleanups' / action['id']
            scope = read(action_folder / 'scope.json')
            assert scope['status'] == 'active' and process_alive(scope['pid']) and process_alive(barrier['pid'])
            assert not read(state / 'cleanup.json')['ok']
            assert holder.process.poll() is None and outsider.poll() is None
            fenced('CLEANUP_BUSY')
            report['first_action'] = action
            report['kill'] = await asyncio.to_thread(kill_owned_interpreter, first['process'], first['ready']['pid'])
            await until(lambda: not process_alive(scope['pid']) and not process_alive(barrier['pid']))
            with store.connection() as db:
                raw = dict(db.execute('SELECT * FROM run_cleanups WHERE id=?', (action['id'],)).fetchone())
            assert raw['status'] == 'running'  # killing a process cannot rewrite SQLite
            assert read(action_folder / 'scope.json') == scope  # stale active receipt is retained
            after_kill = await inspect()
            assert after_kill[0]['running'] and after_kill[0]['runtime_id'] == first_vm[0]['runtime_id']
            assert holder.process.poll() is None and outsider.poll() is None
            report['vm_after_http_kill'] = after_kill
            second = await server(2, first['ready']['port'])
            await page.goto(base + '/tasks/' + goal['id'])
            await expect(page.locator('.ag-cleanup-state')).to_contain_text('Очистка прервана')
            interrupted = store.run_cleanup_status(run['id'])['cleanup']
            assert interrupted['id'] == action['id'] and interrupted['status'] == 'interrupted'
            fenced('CLEANUP_REQUIRED')
            assert (await inspect())[0]['running'] and not read(state / 'cleanup.json')['ok']
            assert snapshot() == rows_before
            (folder / 'allow-cleanup').write_bytes(b'Explicit fixture human retry only\n')
            await page.get_by_role('button', name='Очистить среду', exact=True).click()
            await expect(page.locator('.ag-cleanup-state')).to_contain_text('Очистка завершена', timeout=45000)
            completed = store.run_cleanup_status(run['id'])['cleanup']
            assert completed['status'] == 'completed' and completed['id'] != action['id']
            assert completed['receipt']['ok'] is True and completed['receipt']['records_checked'] == 1
            final_vm = await inspect()
            assert len(final_vm) == 1 and not final_vm[0]['running'] and final_vm[0]['missing']
            assert holder.process.poll() is None and outsider.poll() is None
            assert snapshot() == rows_before
            assert {str(p.relative_to(state)): p.read_bytes() for p in protected} == artifacts_before
            assert {p.relative_to(repo).as_posix(): p.read_bytes() for p in repo.rglob('*') if p.is_file()} == source_before
            with store.connection() as db:
                actions = [store._public_cleanup(row) for row in db.execute('SELECT * FROM run_cleanups ORDER BY rowid')]
                store._no_cleanup(db)
            assert actions == [interrupted, completed]
            report.update(interrupted_action=interrupted, completed_action=completed, vm_after_retry=final_vm)
            await page.screenshot(path=str(folder / 'cleanup-after-retry.png'), full_page=True)
            await page.reload()
            await expect(page.locator('.ag-cleanup-state')).to_contain_text('Очистка завершена')
            assert store.run_cleanup_status(run['id'])['cleanup'] == completed
            await browser.close()
        report['checks'] = ['real VM guest test passed', 'first cleanup held before SDK stop',
            'running cleanup blocks launch/claim/recovery', 'captured HTTP owner killed with native identity checks',
            'Job child exited; stale receipt and durable running lease retained', 'separate holder/VM and outsider survived',
            'HTTP restart records interrupted without automatically cleaning the live VM',
            'interrupted cleanup blocks launch/claim/recovery', 'explicit UI retry performs real SDK cleanup',
            'successful fresh acknowledgement clears fence; interrupted receipt preserved',
            'task/run rows, versions, artifacts and source/index/HEAD unchanged', 'completed state survives page reload']
        report['ok'] = True
    except BaseException:
        report['error'] = traceback.format_exc()
        raise
    finally:
        # Owned emergency teardown is separately recorded; it never repairs a
        # failed assertion or changes the recorded success of the tested path.
        failures = []
        for entry in http:
            process = entry['process']
            if process.poll() is None:
                (folder / f'stop-http-{entry["attempt"]}').write_bytes(b'stop')
                try:
                    await asyncio.to_thread(process.wait, 8)
                except subprocess.TimeoutExpired:
                    if entry['ready']:
                        await asyncio.to_thread(kill_owned_interpreter, process, entry['ready']['pid'])
                    else:
                        failures.append('HTTP fixture never published identity; inspect captured launcher')
        (folder / 'release-holder').write_bytes(b'release')
        for scope in reversed(scopes):
            try:
                await asyncio.to_thread(scope.process.wait, 15)
            except subprocess.TimeoutExpired:
                pass
            try:
                scope.close()
            except BaseException as exc:
                failures.append(repr(exc))
        if state and (state / 'microvms').exists():
            try:
                (folder / 'allow-cleanup').write_bytes(b'emergency owned teardown')
                with (folder / 'emergency-cleanup.log').open('ab') as stream:
                    await asyncio.to_thread(cleanup_owned, [*command, '--cleanup', '--output', str(state / 'report.json')],
                                            state, stream, scope_id=state.name)
                report['final_vm_observation'] = await inspect()
                assert all(not v['running'] and v['missing'] for v in report['final_vm_observation'])
            except BaseException as exc:
                failures.append(repr(exc))
        if outsider:
            report['outsider_survived_until_teardown'] = outsider.poll() is None
            if outsider.poll() is None:
                outsider.kill()
            outsider.wait(timeout=5)
        for stream in streams:
            stream.close()
        report['production_config_unchanged'] = hashlib.sha256(production_config.read_bytes()).hexdigest() == config_hash
        if not report['production_config_unchanged']:
            failures.append('Production runtime configuration changed during isolated fixture')
        report['teardown_errors'] = failures
        report['ok'] = report['ok'] and not failures and report['production_config_unchanged']
        save(folder / 'check-report.json', report)
        print(json.dumps({'ok': report['ok'], 'report': str(folder / 'check-report.json')}, ensure_ascii=False), flush=True)
        if failures:
            raise RuntimeError('Owned fixture teardown failed: ' + repr(failures))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared-state', type=Path, required=True)
    asyncio.run(check(parser.parse_args()))
