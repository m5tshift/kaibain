"""Actual Ollama -> web supervisor -> stdio DAG workers -> microVMs -> review.

Standalone opt-in check using isolated Git/SQLite/profile/fixed runtime. No model
fixtures, supplied plan, hidden retries, acceptance, Apply or production settings.
Every invocation preserves its inputs, commands, logs and failed evidence under
.local/parallel-real-browser/. The deadline defaults to 180s; --seconds explicitly
selects an existing bounded 180..600s limit without changing the 32768 token cap.
"""
import argparse
import asyncio
from collections import Counter
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback
from urllib.request import build_opener, ProxyHandler, Request

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT.parent / 'multi-agent-system'
sys.path.insert(0, str(ROOT / 'src'))
from kaibain.store import Store
from kaibain.web import make_server
from playwright.async_api import async_playwright, expect

MODELS = {'coordinator': 'qwen3:1.7b', 'developer': 'qwen3:8b'}
REQUEST = ('For None, alpha and beta return zero. Preserve numeric behavior and add '
           'docstrings. The files are independent. Use two independent workers, '
           'one owner for alpha.py and one for beta.py. Do not add a dependency '
           'between these independent modules.')
ORIGINAL = {
    'alpha.py': b'def alpha(x):\n    return x + 1\n',
    'beta.py': b'def beta(x):\n    return x * 2\n',
    'test_contract.py': b'import unittest\nfrom alpha import alpha\nfrom beta import beta\nclass Contract(unittest.TestCase):\n def test_numeric(self): self.assertEqual(alpha(2),3); self.assertEqual(beta(2),4)\n',
}


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


async def review_ui(page, folder, prefix='review'):
    await expect(page.locator('.review-summary').first).to_contain_text('Combined 2 isolated worker exports', timeout=30000)
    await expect(page.locator('.review-check .review-pass')).to_have_count(3)
    await expect(page.locator('.review-files [data-file]')).to_have_count(2)
    await expect(page.locator('[data-review-action="accepted"]')).to_be_enabled()
    await expect(page.locator('[data-review-action="apply"]')).to_have_count(0)
    for name in ('alpha.py', 'beta.py'):
        details = page.locator(f'.review-files [data-file="{name}"]')
        await details.locator('summary').click()
        await expect(details.locator('.review-diff')).to_be_visible()
    await page.screenshot(path=str(folder / (prefix + '.png')), full_page=True)
    (folder / (prefix + '.html')).write_text(await page.content(), encoding='utf-8')


async def inspect_saved(folder):
    """Read-only UI follow-up; never launches models, workers or user controls."""
    folder = Path(folder).resolve()
    assert folder.parent == (ROOT / '.local/parallel-real-browser').resolve()
    report = read(folder / 'check-report.json')
    evidence = folder / 'ui-inspections' / str(time.time_ns())
    evidence.mkdir(parents=True)
    store = Store(folder / 'tasks.sqlite3')
    before = next(row for row in store.agent_runs() if row['id'] == report['run_id'])
    assert before['status'] == 'completed' and before['decision'] == 'pending'
    server = make_server(store, 0, runtime_config=folder / 'runtime.json')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    result = {'ok': False, 'read_only': True, 'run_id': before['id'], 'requests': []}
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, **({'channel': 'msedge'} if os.name == 'nt' else {}))
            page = await browser.new_page(viewport={'width': 1440, 'height': 1100})
            page.on('request', lambda request: result['requests'].append({'method': request.method, 'url': request.url}))
            base = f'http://127.0.0.1:{server.server_port}'
            health = await page.request.get(base + '/api/health')
            assert health.ok
            result['health'] = await health.json()
            await page.goto(base + '/reviews/' + before['id'])
            await review_ui(page, evidence, prefix='review-loaded')
            result['ui_url'] = page.url
            assert all(row['method'] == 'GET' for row in result['requests'])
            after = next(row for row in store.agent_runs() if row['id'] == before['id'])
            assert after['version'] == before['version'] and after['decision'] == 'pending'
            assert store.get(after['task_id'])['status'] == 'review'
            result['ok'] = True
            await browser.close()
    except Exception as error:
        result.update(error=str(error), traceback=traceback.format_exc())
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=5)
        result['server_running'] = False
        save(evidence / 'read-only-ui-report.json', result)
    print(json.dumps({'ok': result['ok'], 'report': str(evidence / 'read-only-ui-report.json')}), flush=True)
    return result['ok']


def model_metadata():
    opener = build_opener(ProxyHandler({}))
    def get(path, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = Request('http://127.0.0.1:11434' + path, data=data,
                          headers={'Content-Type': 'application/json'})
        with opener.open(request, timeout=5) as response:
            return json.load(response)
    tags = get('/api/tags')
    identities = {}
    for role, name in MODELS.items():
        row = next((row for row in tags['models'] if row['name'] == name), None)
        assert row is not None, f'Installed model missing: {name}; no downloads'
        identities[role] = {'tag': row, 'show': get('/api/show', {'model': name})}
    return identities


def execution_evidence(state, seconds):
    report = read(state / 'parallel/report.json')
    plan = read(state / 'parallel/plan.json')
    tasks = plan['tasks']
    assert len(tasks) == 2 and all(not row['dependencies'] for row in tasks), plan
    assert {tuple(row['writable']) for row in tasks} == {('alpha.py',), ('beta.py',)}, plan
    assert report['ok'] and len(report['nodes']) == 2, report
    assert all(row['status'] == 'completed' for row in report['nodes'].values())
    budget = report['budget']
    assert budget['peak_cpus'] == 2 and budget['peak_memory_mb'] == 1024, budget
    assert not budget['active_workers'] and budget['reserved_cpus'] == budget['reserved_memory_mb'] == 0
    assert budget['elapsed_seconds'] <= seconds, budget
    calls = report['model_calls']
    assert calls and report['token_accounting']['complete'] and all(row['tokens_known'] for row in calls)
    tokens = sum(row['prompt_tokens'] + row['output_tokens'] for row in calls)
    assert 0 < tokens <= 32768 and tokens == report['token_accounting']['tokens_used']
    assert [row['total_used_tokens'] for row in calls] == list(itertools.accumulate(
        row['prompt_tokens'] + row['output_tokens'] for row in calls))
    assert {row['model'] for row in calls} == set(MODELS.values())
    for row in calls:
        metrics = row['response_metrics']
        assert metrics['prompt_eval_count'] == row['prompt_tokens'] and metrics['eval_count'] == row['output_tokens']
        assert row['outcome'] == 'returned' and metrics['done']
    intervals = []
    seen = set()
    worker_checks = {}
    for task in tasks:
        node = task['id']
        assert any(row.get('node') == node and row['role'] == 'developer' for row in calls)
        worker = read(state / 'parallel/workers' / node / 'report.json')
        assert worker['ok'] and worker['verification']['status'] == 'passed'
        assert worker['worker_checks']['exit_code'] == worker['clean_check']['checks']['exit_code'] == 0
        assert not worker['worker_checks']['stop_reason'] and not worker['clean_check']['checks']['stop_reason']
        identifier = worker['worker_sandbox']
        clean_id = worker['clean_check']['sandbox_id']
        assert identifier != clean_id and identifier not in seen and clean_id not in seen
        seen.update((identifier, clean_id))
        vm = read(state / 'microvms' / (identifier + '.json'))
        assert vm['policy']['cpus'] == 1 and vm['policy']['memory_mb'] == 512
        events = [json.loads(line) for line in (state / 'microvms' / (identifier + '.events.jsonl')).read_text().splitlines()]
        start = next(row['time'] for row in events if row.get('tool') == 'import' and row['state'] == 'completed')
        end = next(row['time'] for row in events if row['state'] == 'stopped')
        intervals.append({'node': node, 'sandbox_id': identifier, 'runtime_id': vm['runtime_id'],
                          'import_completed': start, 'stopped': end})
        worker_checks[node] = {'worker': identifier, 'clean': clean_id, 'tests': worker['tests'],
                              'verification': worker['verification']}
    overlap = min(row['stopped'] for row in intervals) - max(row['import_completed'] for row in intervals)
    assert overlap > 0, intervals
    combined = read(state / 'development/report.json')
    assert combined['ok'] and combined['verification']['status'] == 'passed'
    assert combined['worker_checks']['exit_code'] == combined['clean_check']['checks']['exit_code'] == 0
    assert not combined['worker_checks']['stop_reason'] and not combined['clean_check']['checks']['stop_reason']
    assert combined['worker_sandbox'] not in seen and combined['clean_check']['sandbox_id'] not in seen
    assert combined['worker_sandbox'] != combined['clean_check']['sandbox_id']
    return {'budget': budget, 'token_accounting': report['token_accounting'],
            'model_call_count': len(calls), 'models': dict(Counter(row['model'] for row in calls)),
            'worker_intervals': intervals, 'worker_vm_overlap_seconds': overlap,
            'worker_checks': worker_checks, 'combined_clean_check': combined['clean_check']}


async def check(seconds):
    if os.environ.get('KAIBAIN_RUN_ID'):
        raise RuntimeError('Run standalone; inherited run scopes must not be bypassed')
    folder = ROOT / '.local/parallel-real-browser' / str(time.time_ns())
    folder.mkdir(parents=True)
    original_stderr = sys.stderr
    http_log = (folder / 'harness.log').open('w', encoding='utf-8')
    sys.stderr = http_log
    print(json.dumps({'attempt': str(folder), 'seconds': seconds, 'tokens': 32768}), flush=True)
    receipt = {'ok': False, 'type': 'actual_ollama_web_stdio_dag_microvms',
               'platform': sys.platform, 'seconds_limit': seconds, 'tokens_limit': 32768,
               'command': [sys.executable, str(Path(__file__).resolve()), '--seconds', str(seconds)],
               'models': MODELS, 'attempt': str(folder), 'cancellation_tested': False,
               'human_accepted': False, 'applied': False}
    server = thread = store = None
    monitor = None
    run = None
    errors = []
    started = time.monotonic()
    try:
        save(folder / 'models.json', await asyncio.to_thread(model_metadata))
        repo = folder / 'source'
        repo.mkdir()
        for name, data in ORIGINAL.items():
            (repo / name).write_bytes(data)
        git = ['git', '-c', 'core.hooksPath=' + os.devnull, '-c', 'core.autocrlf=false',
               '-c', 'commit.gpgsign=false', '-C', str(repo)]
        commands = [['init', '-q'], ['add', '.'], ['-c', 'user.name=Fixture', '-c',
                    'user.email=fixture@localhost', 'commit', '-qm', 'Independent parallel fixture']]
        for command in commands:
            subprocess.run([*git, *command], check=True, capture_output=True)
        head = subprocess.check_output([*git, 'rev-parse', 'HEAD'], text=True).strip()
        index = (repo / '.git/index').read_bytes()
        config = folder / 'runtime.json'
        python = RUNTIME / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        runtime_argv = [str(python), str(RUNTIME / 'run_development_web.py'), '--kaibain', str(ROOT),
                        '--mcp-python', sys.executable, '--ollama', 'http://127.0.0.1:11434',
                        '--coordinator-model', MODELS['coordinator'], '--model', MODELS['developer'],
                        '--seconds', str(seconds), '--parallel-workers', '2']
        save(config, {'kind': 'development-v1', 'diagnostics': 'readiness-v1', 'recovery': 'checkpoint-v1',
                      'label': 'Isolated actual Ollama DAG', 'command': runtime_argv})
        profile = {'repo': str(repo), 'commit': head, 'files': list(ORIGINAL),
                   'writable': ['alpha.py', 'beta.py'],
                   'checks': ['python', '-m', 'unittest', 'discover', '-s', '.', '-v'],
                   'acceptance': ['For None both functions return zero. Preserve the existing numeric API and behavior. Both functions document None in docstrings.']}
        save(folder / 'inputs.json', {'request': REQUEST, 'profile': profile,
                                     'git_commands': [[*git, *command] for command in commands]})
        store = Store(folder / 'tasks.sqlite3')
        project = store.create_project('Actual Ollama parallel fixture', profile)
        goal = store.create(REQUEST, project_id=project['id'])
        receipt.update(database=str(store.path), goal_id=goal['id'], project_id=project['id'])
        server = make_server(store, 0, runtime_config=config)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        receipt['base_url'] = base
        async def observe():
            with (folder / 'graph-samples.jsonl').open('w', encoding='utf-8') as stream:
                while True:
                    value = {'time': time.time(), 'graph': store.list_agents(), 'runs': store.agent_runs()}
                    stream.write(json.dumps(value, ensure_ascii=False) + '\n')
                    stream.flush()
                    await asyncio.sleep(.3)
        monitor = asyncio.create_task(observe())
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, **({'channel': 'msedge'} if os.name == 'nt' else {}))
            page = await browser.new_page(viewport={'width': 1440, 'height': 1100})
            page.on('pageerror', lambda error: errors.append(str(error)))
            health = await page.request.get(base + '/api/health')
            assert health.ok
            receipt['health'] = await health.json()
            await page.goto(base + '/tasks/' + goal['id'])
            await expect(page.locator('.ag-run-start')).to_be_enabled(timeout=20000)
            await page.locator('.ag-run-start').click()
            graph_seen = False
            deadline = time.monotonic() + seconds + 75  # observation/cleanup grace, never a runtime budget extension
            while time.monotonic() < deadline:
                rows = store.agent_runs()
                if rows:
                    run = rows[0]
                    receipt['run_id'] = run['id']
                    if run['status'] not in ('queued', 'running', 'cancelling'):
                        break
                agents = store.list_agents()['agents']
                if len(agents) >= 4 and not graph_seen:
                    await page.goto(base + '/agents?task=' + goal['id'])
                    await expect(page.locator('[data-link-kind="assignment"]')).to_have_count(3, timeout=15000)
                    await expect(page.locator('.ag-budget').first).to_be_visible(timeout=15000)
                    await expect(page.locator('[data-link-kind="dependency"]').first).to_be_visible(timeout=15000)
                    await page.screenshot(path=str(folder / 'working.png'), full_page=True)
                    (folder / 'working.html').write_text(await page.content(), encoding='utf-8')
                    graph_seen = True
                await asyncio.sleep(.2)
            assert run is not None and run['status'] not in ('queued', 'running', 'cancelling'), 'Runtime did not settle within observation bound'
            save(folder / 'run.json', run)
            receipt['run_status'] = run['status']
            assert run['status'] == 'completed', {'status': run['status'], 'error': run.get('error'), 'result': run.get('result')}
            assert graph_seen, 'Two-worker live DAG UI was not observed'
            state = folder / '.agent-runs' / run['id']
            receipt['execution'] = execution_evidence(state, seconds)
            assert store.get(goal['id'])['status'] == 'review'
            aggregate = store.get(run['result']['child_id'])
            assert aggregate['status'] == 'review' and aggregate['progress']['completed'] == 2
            assert run['result']['verification']['status'] == 'passed'
            assert run['result']['task_versions'] == {goal['id']: store.get(goal['id'])['version'], aggregate['id']: aggregate['version']}
            save(folder / 'pending-review.json', {'root': store.get(goal['id']), 'aggregate': aggregate})
            await page.goto(base + '/reviews/' + run['id'])
            await review_ui(page, folder)
            receipt['review_url'] = page.url
            oracle_argv = [str(python), str(RUNTIME / 'tools/check_parallel_export.py'),
                           '--state', str(state), '--output', str(folder / 'independent-oracle')]
            receipt['oracle_command'] = oracle_argv
            with (folder / 'oracle.log').open('w', encoding='utf-8') as log:
                process = await asyncio.create_subprocess_exec(*oracle_argv, stdout=log, stderr=log,
                    **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
                code = await process.wait()
            receipt['oracle'] = read(folder / 'independent-oracle/check-report.json')
            assert code == 0 and receipt['oracle']['ok'], receipt['oracle']
            # No mutation through review, acceptance, return, recovery or Apply controls.
            assert store.get(goal['id'])['status'] == 'review'
            for name, data in ORIGINAL.items():
                assert (repo / name).read_bytes() == data
            assert subprocess.check_output([*git, 'rev-parse', 'HEAD'], text=True).strip() == head
            assert (repo / '.git/index').read_bytes() == index
            assert not subprocess.check_output([*git, 'status', '--porcelain'], text=True).strip()
            assert not errors, errors
            receipt['source_unchanged'] = True
            receipt['ok'] = True
            await browser.close()
    except Exception as error:
        receipt.update(error=str(error), traceback=traceback.format_exc())
    finally:
        if monitor:
            monitor.cancel()
            try:
                await monitor
            except asyncio.CancelledError:
                pass
        # Do not turn a harness failure into a claimed model cancellation. Let the
        # bounded fixture supervisor settle naturally, then inspect owned receipts.
        if store:
            cleanup_deadline = time.monotonic() + seconds + 75
            while any(row['status'] in ('queued', 'running', 'cancelling') for row in store.agent_runs()) and time.monotonic() < cleanup_deadline:
                await asyncio.sleep(.5)
            receipt['final_runs'] = store.agent_runs()
            receipt['runtime_reports'] = {str(path.relative_to(folder)): read(path)
                for path in (folder / '.agent-runs').glob('*/parallel/report.json')}
            vms = []
            for path in (folder / '.agent-runs').glob('*/microvms/*.json'):
                if len(path.stem) == 32:
                    row = read(path)
                    vms.append({key: row.get(key) for key in ('id', 'runtime_id', 'name', 'status', 'owner')})
            receipt['owned_vms'] = vms
            receipt['owned_cleanup_confirmed'] = all(row['status'] == 'stopped' for row in vms)
            receipt['cleanup_receipts'] = {str(path.relative_to(folder)): read(path)
                for path in (folder / '.agent-runs').glob('*/cleanup.json')}
            receipt['owned_cleanup_confirmed'] = receipt['owned_cleanup_confirmed'] and bool(receipt['cleanup_receipts']) and all(value['ok'] for value in receipt['cleanup_receipts'].values())
            if not receipt['owned_cleanup_confirmed']:
                receipt['ok'] = False
        if server:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join(timeout=5)
        receipt.update(harness_seconds=round(time.monotonic() - started, 3), page_errors=errors, server_running=False)
        save(folder / 'check-report.json', receipt)
        sys.stderr = original_stderr
        http_log.close()
        print(json.dumps({'ok': receipt['ok'], 'report': str(folder / 'check-report.json'),
                          'error': receipt.get('error')}), flush=True)
    return receipt['ok']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=180, choices=range(180, 601), metavar='180..600')
    parser.add_argument('--inspect-attempt', type=Path, help='Read-only loaded review UI inspection of a saved isolated attempt; no inference')
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(inspect_saved(args.inspect_attempt) if args.inspect_attempt else check(args.seconds)) else 1)
