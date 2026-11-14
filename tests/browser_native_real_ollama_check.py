"""Opt-in actual Ollama coding of an unmodified Kaibain snapshot in microVMs.

Only isolated SQLite/Git fixtures are launched. No acceptance, Apply, production
runtime changes, model downloads or host execution of the copied project.
"""
import argparse
import ast
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback

from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT.parent / 'multi-agent-system'
sys.path.insert(0, str(ROOT / 'src'))
from kaibain.store import Store
from kaibain.web import make_server

MODELS = {'coordinator': 'qwen3:1.7b', 'developer': 'qwen3:8b'}
REQUEST = 'Fix Store goal_results ordering when report timestamps are equal.'
DESCRIPTION = ('Store._enrich, Store.list and Store.review_goal in src/kaibain/store.py can select an older '
               'goal_results row when created_at values are equal and UUID order differs from submission order. '
               'Return/review the newest submission; keep older reports stale and distinct-date ordering unchanged. '
               'Read the protected tests/test_report_order_contract.py and the relevant Store methods. '
               'Preserve public APIs and ownership. Only store.py may change.')


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2) + '\n', encoding='utf-8')


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_source(folder):
    repo = folder / 'source'
    repo.mkdir()
    paths = [p.relative_to(ROOT).as_posix() for p in sorted((ROOT / 'src/kaibain').glob('*.py'))]
    paths += ['run.py', 'requirements.txt', 'src/static/index.html', 'tests/test_core.py']
    hashes = {}
    for name in paths:
        source = ROOT / name
        assert not any(p.is_symlink() or getattr(p.lstat(), 'st_file_attributes', 0) & 0x400
                       for p in (source, *source.parents)), 'Linked source refused'
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        hashes[name] = sha(source)
    contract = RUNTIME / 'examples/kaibain-coding/test_report_order_contract.py'
    name = 'tests/test_report_order_contract.py'
    (repo / name).write_bytes(contract.read_bytes())
    paths.append(name)
    assert len(paths) <= 20
    git = ['git', '-c', 'core.hooksPath=' + os.devnull, '-c', 'core.fsmonitor=false',
           '-c', 'core.autocrlf=false', '-c', 'commit.gpgsign=false', '-C', str(repo)]
    for args in (['init', '-q'], ['add', '.'], ['-c', 'user.name=Fixture', '-c',
                 'user.email=fixture@localhost', 'commit', '-qm', 'Unmodified Kaibain with protected regression tests']):
        subprocess.run([*git, *args], check=True, capture_output=True)
    head = subprocess.check_output([*git, 'rev-parse', 'HEAD'], text=True).strip()
    return repo, paths, hashes, git, head, (repo / '.git/index').read_bytes()


async def check(args):
    if os.environ.get('KAIBAIN_RUN_ID'):
        raise RuntimeError('Run standalone; do not clear or bypass inherited run context')
    folder = ROOT / '.local/native-real-browser' / str(time.time_ns())
    folder.mkdir(parents=True)
    receipt = {'ok': False, 'type': 'actual_ollama_native_kaibain_web_microvm',
               'platform': sys.platform, 'models': MODELS, 'seconds_limit': 180,
               'tokens_limit': 32768, 'human_accepted': False, 'applied': False,
               'production_runtime_changed': False, 'source_defect_seeded': False,
               'guided_implementation':args.guided}
    config_path = ROOT / '.local/agent-runtime.json'
    config_hash = sha(config_path) if config_path.exists() else None
    server = thread = store = run = monitor = None
    errors = []
    log = (folder / 'harness.log').open('w', encoding='utf-8')
    stderr = sys.stderr
    sys.stderr = log
    def stage(name):
        with (folder/'harness-stages.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps({'time':time.time(),'stage':name})+'\n')
    print(json.dumps({'attempt': str(folder), 'actual_models': MODELS, 'seconds': 180}), flush=True)
    try:
        stage('prepare_source')
        repo, paths, hashes, git, head, index = prepare_source(folder)
        receipt.update(source_hashes=hashes, source_commit=head, imported_files=paths)
        profile = {'repo': str(repo), 'commit': head, 'files': paths,
                   'writable': ['src/kaibain/store.py'],
                   'checks': ['python', '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                   'acceptance': ['The protected regression and original tests pass; newest reports are consistent in get/list/review, '
                                  'including equal timestamps, non-monotonic UUIDs and restart. Preserve stale-review guards.']}
        source=(repo/'src/kaibain/store.py').read_text(encoding='utf-8')
        lines=source.splitlines()
        methods=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name=='Store')
        locations={node.name:[n for n in range(node.lineno,node.end_lineno+1) if 'goal_results' in lines[n-1]]
                   for node in methods.body if isinstance(node,ast.FunctionDef) and node.name in ('_enrich','list','review_goal')}
        description=DESCRIPTION+'\nObserved SQL locations (unmodified source): '+json.dumps(locations)+'. Inspect small ranges around these lines.'
        if args.guided:
            description+='\nImplementation specification: keep created_at as the primary order. For equal timestamps use persistent SQLite insertion order (rowid), not UUID order. Apply this consistently to _enrich, list per-goal newer-row selection, and review_goal. Preserve all review/version/ownership guards. Read only small relevant query ranges, then implement the changes; do not repeatedly read entire methods.'
            snippets={name:[lines[n-1] for n in sorted({i for anchor in anchors for i in range(anchor,min(anchor+3,len(lines)+1))})]
                      for name,anchors in locations.items()}
            description+='\nObserved current query excerpts (not the desired patch):\n'+json.dumps(snippets,ensure_ascii=False)
        python = RUNTIME / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        argv = [str(python), str(RUNTIME / 'run_development_web.py'), '--kaibain', str(ROOT),
                '--mcp-python', sys.executable, '--ollama', 'http://127.0.0.1:11434',
                '--coordinator-model', MODELS['coordinator'], '--model', MODELS['developer'], '--seconds', '180',
                '--environment-bundle', str(args.environment_bundle.resolve()), '--environment-sha256', args.environment_sha256,
                '--guest-toolchain-bundle', str(args.guest_toolchain_bundle.resolve()),
                '--guest-toolchain-sha256', args.guest_toolchain_sha256]
        if args.developer_thinking:argv.append('--developer-thinking')
        config = folder / 'runtime.json'
        save(config, {'kind': 'development-v1', 'diagnostics': 'readiness-v1', 'recovery': 'checkpoint-v1',
                      'label': 'Isolated Kaibain native coding / real Ollama', 'command': argv})
        save(folder / 'inputs.json', {'request': REQUEST, 'description': description, 'profile': profile, 'command': argv})
        store = Store(folder / 'tasks.sqlite3')
        project = store.create_project('Kaibain native coding acceptance fixture', profile)
        goal = store.create(REQUEST, description=description, project_id=project['id'])
        receipt.update(database=str(store.path), goal_id=goal['id'])
        server = make_server(store, 0, runtime_config=config)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        async def observe():
            with (folder / 'graph-samples.jsonl').open('w', encoding='utf-8') as stream:
                while True:
                    stream.write(json.dumps({'time': time.time(), 'graph': store.list_agents()}) + '\n')
                    stream.flush()
                    await asyncio.sleep(.5)
        monitor = asyncio.create_task(observe())
        async with async_playwright() as playwright:
            stage('browser_launch')
            browser = await playwright.chromium.launch(headless=True,timeout=30000, **({'channel': 'msedge'} if os.name == 'nt' else {}))
            stage('browser_page')
            page = await asyncio.wait_for(browser.new_page(viewport={'width': 1440, 'height': 1000}),30)
            page.on('pageerror', lambda error: errors.append(str(error)))
            stage('goal_page')
            await page.goto(base + '/tasks/' + goal['id'],timeout=30000)
            await expect(page.locator('.ag-run-start')).to_be_enabled(timeout=20000)
            stage('start_fixture_goal')
            await page.locator('.ag-run-start').click()
            deadline = time.monotonic() + 255  # observation/owned-cleanup grace only
            graph_seen = False
            while time.monotonic() < deadline:
                rows = store.agent_runs()
                if not rows and await page.locator('.ag-run-error').count():
                    failure=await page.locator('.ag-run-error').inner_text()
                    if failure.strip():
                        await page.screenshot(path=str(folder/'launch-error.png'),full_page=True)
                        raise RuntimeError('Fixture launch rejected before reservation: '+failure)
                if rows:
                    run = rows[0]
                    if run['status'] not in ('queued', 'running', 'cancelling'):
                        break
                if len(store.list_agents()['agents']) == 2 and not graph_seen:
                    await page.goto(base + '/agents?task=' + goal['id'])
                    await expect(page.locator('[data-link-kind="assignment"]')).to_have_count(1, timeout=15000)
                    await page.screenshot(path=str(folder / 'working.png'), full_page=True)
                    graph_seen = True
                await asyncio.sleep(.3)
            assert run is not None and run['status'] not in ('queued', 'running', 'cancelling'), 'Run did not settle'
            receipt.update(run_id=run['id'], run_status=run['status'], graph_seen=graph_seen)
            save(folder / 'run.json', run)
            state = folder / '.agent-runs' / run['id']
            assert run['status'] == 'completed', {'status': run['status'], 'error': run.get('error')}
            detail = read(state / 'development/report.json')
            receipt['execution'] = detail
            assert detail['ok'] and detail['baseline']['exit_code'] != 0
            assert detail['worker_checks']['exit_code'] == detail['clean_check']['checks']['exit_code'] == 0
            assert detail['verification']['status'] == 'passed'
            assert detail['token_accounting']['complete'] and 0 < detail['token_accounting']['tokens_used'] <= 32768
            assert detail['worker_sandbox'] != detail['clean_check']['sandbox_id']
            assert graph_seen and store.get(goal['id'])['status'] == 'review'
            await page.goto(base + '/reviews/' + run['id'])
            await expect(page.locator('.review-toolbar')).to_contain_text('Целостность экспорта подтверждена', timeout=15000)
            await expect(page.locator('.review-diff')).to_contain_text('src/kaibain/store.py')
            await page.screenshot(path=str(folder / 'review.png'), full_page=True)
            oracle = [str(python), str(RUNTIME / 'tools/check_native_coding_export.py'), '--state', str(state),
                      '--kaibain', str(ROOT), '--output', str(folder / 'independent-full-suite'),
                      '--environment-bundle', str(args.environment_bundle.resolve()), '--environment-sha256', args.environment_sha256,
                      '--guest-toolchain-bundle', str(args.guest_toolchain_bundle.resolve()),
                      '--guest-toolchain-sha256', args.guest_toolchain_sha256]
            with (folder / 'oracle.log').open('wb') as output:
                process = await asyncio.create_subprocess_exec(*oracle, stdout=output, stderr=output,
                    **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
                code = await process.wait()
            receipt['independent_full_suite'] = read(folder / 'independent-full-suite/check-report.json')
            assert code == 0 and receipt['independent_full_suite']['ok']
            assert all(sha(ROOT / name) == value for name, value in hashes.items())
            assert subprocess.check_output([*git, 'rev-parse', 'HEAD'], text=True).strip() == head
            assert (repo / '.git/index').read_bytes() == index
            assert not subprocess.check_output([*git, 'status', '--porcelain'], text=True).strip()
            assert not errors, errors
            receipt.update(ok=True, source_unchanged=True, pending_human_review=True,
                           diff=str(state / 'development/result/changes.diff'))
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
        if store:
            until = time.monotonic() + 255
            while any(r['status'] in ('queued', 'running', 'cancelling') for r in store.agent_runs()) and time.monotonic() < until:
                await asyncio.sleep(.5)
            receipt['final_runs'] = store.agent_runs()
            receipts = {str(p.relative_to(folder)): read(p) for p in (folder / '.agent-runs').glob('*/cleanup.json')}
            vms = [read(p) for p in (folder / '.agent-runs').glob('*/microvms/*.json') if len(p.stem) == 32]
            receipt['cleanup_receipts'] = receipts
            receipt['owned_vms'] = [{k: v.get(k) for k in ('id', 'runtime_id', 'status')} for v in vms]
            receipt['owned_cleanup_confirmed'] = bool(receipts) and all(x['ok'] for x in receipts.values()) and all(v['status'] == 'stopped' for v in vms)
            if not receipt['owned_cleanup_confirmed']:
                receipt['ok'] = False
        if server:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join(timeout=5)
        receipt['production_runtime_unchanged'] = (sha(config_path) if config_path.exists() else None) == config_hash
        receipt['page_errors'] = errors
        if not receipt['production_runtime_unchanged']:
            receipt['ok'] = False
        save(folder / 'check-report.json', receipt)
        sys.stderr = stderr
        log.close()
        print(json.dumps({'ok': receipt['ok'], 'report': str(folder / 'check-report.json'), 'error': receipt.get('error')}), flush=True)
    return receipt['ok']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment-bundle', required=True, type=Path)
    parser.add_argument('--environment-sha256', required=True)
    parser.add_argument('--guest-toolchain-bundle', required=True, type=Path)
    parser.add_argument('--guest-toolchain-sha256', required=True)
    parser.add_argument('--model', default='qwen3:8b', help='Explicit installed developer model; no downloads or production changes')
    parser.add_argument('--developer-thinking',action='store_true')
    parser.add_argument('--guided',action='store_true',help='Explicit insertion-order implementation specification; not autonomous-discovery acceptance')
    args=parser.parse_args()
    MODELS['developer']=args.model
    raise SystemExit(0 if asyncio.run(check(args)) else 1)
