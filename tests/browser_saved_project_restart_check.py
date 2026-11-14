"""Saved project -> real HTTP process restart -> new goal -> MCP/VM coding run.

Windows opt-in, scripted model HTTP only. Uses an existing current image receipt;
never prepare/pull, changes production configuration or accepts/applies a goal.
Project fields are entered once in Projects UI before restart. Afterwards only
the new goal title and launch button are used; the POST must reference its saved
project/version, not contain a manually reconstructed profile.
"""
import argparse
import asyncio
import hashlib
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from kaibain.agent_runtime import AgentRuntime, cleanup_owned
from kaibain.process_scope import ProcessScope
from kaibain.store import Store
from browser_development_check import ModelFixture
from browser_operator_cleanup_crash_check import HTTP_OWNER, read, save, until
from parallel_crash_fixture import CrashFixture
from playwright.async_api import async_playwright, expect


ADAPTER = r'''
import hashlib,json,sys
from pathlib import Path
FIXED=json.loads(__FIXED_JSON__)
sys.path.insert(0,str(Path(FIXED['runtime'])/'src'))
from sandbox import SandboxManager
from microvm_backend import IMAGE
def prepare(self):
    prepared=json.loads(Path(FIXED['prepared']).read_bytes())
    assert prepared['backend']=='microsandbox' and prepared['image']==IMAGE
    assert prepared['worker_sha256']==hashlib.sha256((Path(FIXED['runtime'])/'src/sandbox_worker.py').read_bytes()).hexdigest()
    self._save(self.state/'image.json',prepared)
    return {'ok':True,'prepared':True,'receipt_reused':True}
SandboxManager.prepare=prepare
from development_web import main
main()
'''


async def check(args):
    if os.name != 'nt' or os.environ.get('KAIBAIN_RUN_ID'):
        raise RuntimeError('Windows-only isolated fixture, outside a built-in run')
    if Path(sys.prefix).resolve() != (ROOT / '.venv').resolve():
        raise RuntimeError('Use the existing Kaibain venv')
    runtime = ROOT.parent / 'multi-agent-system'
    python = runtime / '.venv/Scripts/python.exe'
    prepared = args.prepared_state.resolve(strict=True) / 'image.json'
    assert read(prepared)['worker_sha256'] == hashlib.sha256((runtime / 'src/sandbox_worker.py').read_bytes()).hexdigest()
    production_config = ROOT / '.local/agent-runtime.json'
    production_hash = hashlib.sha256(production_config.read_bytes()).hexdigest()
    folder = ROOT / '.local/saved-project-restart' / str(time.time_ns())
    folder.mkdir(parents=True)
    report = {'ok': False, 'platform': 'Windows/x86_64', 'real_llm_inference': False,
              'type': 'saved_project_process_restart_real_mcp_microvm', 'checks': [],
              'fixture_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    scopes, streams = [], []
    models = None
    model_thread = None
    outsider = None
    env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
    env.pop('KAIBAIN_CONTROL_TOKEN', None)
    env.pop('KAIBAIN_RUN_ID', None)
    store = Store(folder / 'tasks.sqlite3')

    def log(name):
        stream = (folder / name).open('ab')
        streams.append(stream)
        return stream

    async def start_http(attempt, port=0):
        scope = ProcessScope([sys.executable, str(owner), str(port), str(attempt)],
            cwd=str(folder), log=log(f'http-{attempt}.log'), env=env,
            receipt=folder / f'http-scope-{attempt}.json')
        entry = {'scope': scope, 'attempt': attempt, 'closed': False}
        scopes.append(entry)
        path = folder / f'http-ready-{attempt}.json'
        await until(lambda: path.exists() or scope.process.poll() is not None)
        assert path.exists() and scope.process.poll() is None
        entry['ready'] = read(path)
        return entry

    async def stop_http(entry):
        (folder / f'stop-http-{entry["attempt"]}').write_bytes(b'explicit fixture server shutdown')
        try:
            await asyncio.to_thread(entry['scope'].process.wait, 8)
        finally:
            entry['scope'].close()  # owned Job, never a stored PID/group
            entry['closed'] = True

    try:
        repo = folder / 'source with spaces'
        repo.mkdir()
        original = b'def add(a, b):\n    return a - b\n'
        (repo / 'calculator.py').write_bytes(original)
        (repo / 'test_calculator.py').write_bytes(b'import unittest\nfrom calculator import add\nclass Checks(unittest.TestCase):\n def test_positive(self): self.assertEqual(add(2,3),5)\n def test_negative(self): self.assertEqual(add(-2,-3),-5)\n')
        git = ['git', '--no-optional-locks', '-c', 'core.hooksPath=' + os.devnull, '-c', 'commit.gpgsign=false',
               '-c', 'core.fsmonitor=false', '-c', 'core.autocrlf=false', '-C', str(repo)]
        def git_run(argv):
            return subprocess.check_output([*git, *argv]).decode().strip()
        git_run(['init', '-q'])
        git_run(['add', '.'])
        identity = ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@localhost']
        git_run([*identity, 'commit', '-qm', 'Initial arithmetic'])
        first_commit = git_run(['rev-parse', 'HEAD'])
        models = ThreadingHTTPServer(('127.0.0.1', 0), ModelFixture)
        models.daemon_threads = True
        models.stall = models.missing = models.ask = models.batch = False
        models.chats = 0
        models.recorded_options = []
        models.recorded_protocols = []
        models.ready, models.release = threading.Event(), threading.Event()
        model_thread = threading.Thread(target=models.serve_forever, daemon=True)
        model_thread.start()
        fixed = repr(json.dumps({'runtime': str(runtime), 'kaibain': str(ROOT), 'prepared': str(prepared)}))
        owner, adapter = folder / 'http_owner.py', folder / 'prepared_adapter.py'
        owner.write_text(HTTP_OWNER.replace('__FIXED_JSON__', fixed), encoding='utf8')
        adapter.write_text(ADAPTER.replace('__FIXED_JSON__', fixed), encoding='utf8')
        command = [str(python), str(adapter), '--kaibain', str(ROOT), '--mcp-python', sys.executable,
                   '--ollama', f'http://127.0.0.1:{models.server_port}']
        token_budget = getattr(args, 'token_budget', 32768)
        output_tokens = getattr(args, 'developer_output_tokens', 1024)
        assert token_budget in (32768, 65536) and output_tokens in (1024, 2048, 4096)
        command += ['--token-budget', str(token_budget), '--developer-output-tokens', str(output_tokens)]
        protocol = getattr(args, 'developer_protocol', 'json')
        assert protocol in ('json', 'native-tools')
        command += ['--developer-protocol', protocol]
        config = folder / 'runtime.json'
        save(config, {'kind': 'development-v1', 'label': 'Saved project restart fixture',
                      'diagnostics': 'readiness-v1', 'command': command})
        outsider = subprocess.Popen([sys._base_executable, '-I', '-c', 'import time;time.sleep(400)'],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        first = await start_http(1)
        base = f'http://localhost:{first["ready"]["port"]}'
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, channel=os.environ.get('PLAYWRIGHT_CHANNEL', 'msedge'))
            page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.goto(base + '/projects')
            await page.get_by_role('button', name='+ Новый проект', exact=True).click()
            await page.get_by_label('Название проекта', exact=True).fill('Saved arithmetic project')
            await page.get_by_label('Путь репозитория', exact=True).fill(str(repo))
            await page.get_by_label('Импортируемые файлы', exact=True).fill('calculator.py\ntest_calculator.py')
            await page.get_by_label('Разрешённые файлы', exact=True).fill('calculator.py')
            checks = ['python', '-m', 'unittest', 'discover', '-s', '.', '-v']
            await page.get_by_label('Команда проверки', exact=True).fill(json.dumps(checks))
            await page.get_by_label('Критерии приёмки', exact=True).fill('Correct positive and negative sums; preserve API and tests.')
            await page.get_by_role('button', name='Сохранить проект', exact=True).click()
            await expect(page.locator('.project-card')).to_have_count(1)
            project = store.projects()['projects'][0]
            assert project['version'] == 1 and project['profile']['commit'] is None
            assert not store.agent_runs() and not store.list()['tasks'] and models.chats == 0
            # Prove HEAD is resolved at launch, not accidentally frozen at save.
            (repo / 'metadata.txt').write_bytes(b'New fixture commit before app restart\n')
            git_run(['add', 'metadata.txt'])
            git_run([*identity, 'commit', '-qm', 'Advance HEAD before restart'])
            launch_commit = git_run(['rev-parse', 'HEAD'])
            assert launch_commit != first_commit
            source_before = {p.relative_to(repo).as_posix(): p.read_bytes() for p in repo.rglob('*') if p.is_file()}
            await stop_http(first)
            second = await start_http(2, first['ready']['port'])
            assert second['ready']['pid'] != first['ready']['pid']
            store = Store(store.path)  # reopen after the actual HTTP interpreter exited
            assert store.get_project(project['id']) == project
            assert not store.agent_runs() and not store.list()['tasks'] and models.chats == 0
            await page.goto(base + '/projects')
            await expect(page.locator('.project-card h2')).to_have_text(project['name'])
            await page.get_by_role('button', name='Новая цель ↗', exact=True).click()
            await expect(page.get_by_label('Проект цели', exact=True)).to_have_value(project['id'])
            await page.get_by_label('Название цели', exact=True).fill('Fix add(a, b) after app restart')
            await page.get_by_role('button', name='Создать цель', exact=True).click()
            await expect(page.locator('.task-title')).to_have_text('Fix add(a, b) after app restart')
            await expect(page.get_by_label('Проект для запуска', exact=True)).to_have_value(project['id'])
            await expect(page.locator('.ag-project')).to_be_hidden()
            await expect(page.locator('.ag-run-start')).to_be_enabled()
            async with page.expect_request(lambda request: request.method == 'POST' and request.url.endswith('/api/agent-runs')) as posted:
                await page.locator('.ag-run-start').click()
            body = (await posted.value).post_data_json
            assert body['project_id'] == project['id'] and body['expected_project_version'] == 1 and 'project' not in body, body
            async with asyncio.timeout(120):
                while True:
                    runs = store.agent_runs()
                    if runs and runs[0]['status'] not in ('queued', 'running', 'cancelling'):
                        run = runs[0]
                        break
                    await asyncio.sleep(.2)
            assert run['status'] == 'completed', run
            assert len(runs) == 1 and run['project_ref'] == {key: project[key] for key in ('id', 'name', 'version')}
            assert run['project'] == {**project['profile'], 'commit': launch_commit}
            state = folder / '.agent-runs' / run['id']
            detail = read(state / 'development/report.json')
            assert detail['baseline']['exit_code'] == 1 and detail['tests']['status'] == 'passed'
            assert detail['clean_check']['checks']['exit_code'] == 0
            developer_calls = [item for item in models.recorded_options if item['model'] == 'qwen3:8b']
            coordinator_calls = [item for item in models.recorded_options if item['model'] == 'qwen3:1.7b']
            assert developer_calls and all(item['num_predict'] == output_tokens for item in developer_calls)
            assert coordinator_calls and all(item['num_predict'] == 1024 for item in coordinator_calls)
            assert all(item['num_ctx'] == 16384 for item in models.recorded_options)
            for item in models.recorded_protocols:
                native = protocol == 'native-tools' and item['model'] == 'qwen3:8b'
                assert item['tools'] == native and item['format'] != native, item
            assert (state / 'development/result/files/calculator.py').read_bytes() == b'def add(a, b):\n    return a + b\n'
            assert store.get(run['task_id'])['status'] == 'review'
            assert store.get_project(project['id']) == project
            agents = [agent for agent in store.list_agents()['agents'] if agent['run_id'] == run['id']]
            assert len(agents) == 2 and {a['role'] for a in agents} == {'coordinator', 'worker'}
            with store.connection() as db:
                presence = [dict(row) for row in db.execute('SELECT id,disconnected FROM agents WHERE run_id=?', (run['id'],))]
            assert len(presence) == 2 and all(a['disconnected'] for a in presence)
            observer = CrashFixture(store, runtime, python)
            observer.owned[run['id']] = None  # read-only SDK lookup; no launch/kill patch
            vms = await asyncio.to_thread(observer.inspect, run['id'])
            assert len(vms) == 2 and all(not v['running'] and v['missing'] for v in vms)
            assert read(state / 'cleanup.json')['ok'] and outsider.poll() is None
            assert source_before == {p.relative_to(repo).as_posix(): p.read_bytes() for p in repo.rglob('*') if p.is_file()}
            await page.goto(base + '/tasks/' + run['task_id'])
            await expect(page.locator('.review-open')).to_be_visible()
            await page.screenshot(path=str(folder / 'saved-project-after-restart.png'), full_page=True)
            assert not errors, errors
            report.update(project=project, first_commit=first_commit, launch_commit=launch_commit,
                          launch_body=body, run=run, agents=agents, presence=presence, owned_vms=vms, model_chats=models.chats,
                          checks=['project entered once through UI', 'HTTP interpreter exited and restarted against same SQLite',
                            'new goal created after restart; saved profile preselected', 'no profile fields reentered',
                            'launch POST carries project/version only', 'new HEAD pinned at launch',
                            'real stdio MCP coordinator/worker complete coding flow', 'baseline fails; edited checks and clean VM replay pass',
                            'same saved profile unchanged; root in review, no acceptance/Apply',
                            'both owned SDK VMs absent; agents disconnected; outsider alive', 'source HEAD/index/worktree unchanged'])
            report['inference_profile'] = {'token_budget': token_budget, 'developer_output_tokens': output_tokens,
                                           'observed_options': models.recorded_options, 'runtime_argv': command}
            report['developer_protocol'] = {'selected': protocol, 'observed_requests': models.recorded_protocols}
            report['ok'] = True
            await browser.close()
    except BaseException:
        report['error'] = traceback.format_exc()
        raise
    finally:
        failures = []
        # Fixture-only human cancellation, followed by closing only our live Job
        # handles; never signal an old supervisor PID/group from SQLite.
        for run in store.agent_runs():
            if run['can_stop']:
                store.stop_agent_run(run['id'], run['version'])
        for entry in reversed(scopes):
            if not entry['closed']:
                try:
                    await stop_http(entry)
                except BaseException as error:
                    failures.append(repr(error))
        AgentRuntime(store, folder / 'runtime.json').recover()
        for run in store.agent_runs():
            state = folder / '.agent-runs' / run['id']
            if not (state / 'microvms').exists():
                continue
            try:
                with (state / 'fixture-final-cleanup.log').open('ab') as stream:
                    await asyncio.to_thread(cleanup_owned, [*command, '--cleanup', '--output', str(state / 'report.json')],
                                            state, stream, scope_id=run['id'])
                observer = CrashFixture(store, runtime, python)
                observer.owned[run['id']] = None
                final_vms = await asyncio.to_thread(observer.inspect, run['id'])
                assert all(not v['running'] and v['missing'] for v in final_vms)
                report.setdefault('final_vm_observations', {})[run['id']] = final_vms
            except BaseException as error:
                failures.append(repr(error))
        if models:
            models.release.set()
            models.shutdown()
            models.server_close()
            model_thread.join()
        if outsider:
            report['outsider_survived_until_teardown'] = outsider.poll() is None
            if outsider.poll() is None:
                outsider.kill()
            outsider.wait(timeout=5)
        for stream in streams:
            stream.close()
        report['production_config_unchanged'] = hashlib.sha256(production_config.read_bytes()).hexdigest() == production_hash
        if not report['production_config_unchanged']:
            failures.append('Production config changed during isolated fixture')
        report['teardown_errors'] = failures
        report['ok'] = report['ok'] and not failures
        save(folder / 'check-report.json', report)
        print(json.dumps({'ok': report['ok'], 'report': str(folder / 'check-report.json')}), flush=True)
        if failures:
            raise RuntimeError('Owned fixture teardown failed: ' + repr(failures))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared-state', type=Path, required=True)
    parser.add_argument('--token-budget', type=int, choices=(32768, 65536), default=32768)
    parser.add_argument('--developer-output-tokens', type=int, choices=(1024, 2048, 4096), default=1024)
    parser.add_argument('--developer-protocol', choices=('json', 'native-tools'), default='json')
    asyncio.run(check(parser.parse_args()))
