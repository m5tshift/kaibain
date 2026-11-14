"""Opt-in supervisor fault injection: trusted adapter fixture + REAL Microsandbox VM.

Run from Kaibain's main venv, ONLY when core edits are ready::

    .venv/Scripts/python.exe tests/supervisor_interrupt_check.py --run-real-vm

The sibling runtime venv runs the generated fixed adapter. No model calls, MCP
sessions, browser, user database, runtime configuration changes, prepare/pull or
global VM cleanup. This is NOT actual-model/MCP or coding/replay proof.
--interrupt system-exit tests SystemExit instead; --withhold-cleanup-ack performs
real cleanup but exits nonzero afterwards to exercise failure_code=cleanup_failed.
All generated files, isolated SQLite/Git, JSON evidence and logs are retained in
.local/supervisor-interrupt/<time>/. Importing this module does not run anything.
"""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]

# This literal is written to a new machine-local directory only after CLI opt-in.
# It deliberately has no development_web.run(), model or MCP entry point.
ADAPTER = r'''
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

FIXED = json.loads(__FIXED_JSON__)
BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(Path(FIXED['runtime']) / 'src'))
from sandbox import SandboxManager
from microvm_backend import IMAGE


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)


def main():
    p = argparse.ArgumentParser(description='Trusted fault injection fixture; no models/MCP')
    for name in ('db', 'task-id', 'expected-version', 'project-input',
                 'revision-input', 'recovery-input', 'continuation-input'):
        p.add_argument('--' + name)
    p.add_argument('--output', required=True)
    modes = p.add_mutually_exclusive_group()
    modes.add_argument('--cleanup', action='store_true')
    modes.add_argument('--inspect-stopped', action='store_true')
    a = p.parse_args()
    output = Path(a.output).resolve()
    folder = output.parent
    run_id = str(uuid.UUID(folder.name))
    if (folder.parent != BASE / '.agent-runs' or folder.name != run_id
            or output.name != 'report.json'):
        raise ValueError('Output is not this fixture run')
    state = folder / 'microvms'
    if a.cleanup:
        # The REAL official owned cleanup; imports define MCP classes but never
        # connect a client or call a model. Never implement a fake cleanup receipt.
        from development_web import cleanup
        cleanup(output)
        receipt = json.loads((folder / 'cleanup.json').read_text(encoding='utf-8'))
        if receipt.get('ok') is not True:
            raise RuntimeError('Official cleanup did not confirm success')
        save(folder / 'fixture-cleanup-success.json', {
            'ok': True, 'run_id': run_id, 'pid': os.getpid(),
            'after_real_development_web_cleanup': True,
        })
        print('REAL owned cleanup succeeded', flush=True)
        if FIXED['withhold_cleanup_ack']:
            print('FAULT INJECTION: withholding adapter exit-code acknowledgement', flush=True)
            return 23
        return 0
    if a.inspect_stopped:
        # Independent SDK lookup after supervisor cleanup, before fallback cleanup.
        if not state.is_dir():
            raise RuntimeError('No VM state to inspect')
        manager = SandboxManager(state)
        values = []
        for path in sorted(state.glob('*.json')):
            if not re.fullmatch('[a-f0-9]{32}', path.stem):
                continue
            record = manager.record(path.stem)
            if record['run_id'] != run_id:
                raise RuntimeError('Foreign run in isolated VM state')
            info = manager.inspect(path.stem)
            values.append({key: info.get(key) for key in (
                'id', 'owner', 'run_id', 'runtime_id', 'status', 'running',
                'missing', 'vm_status', 'backend', 'isolation',
            )})
        save(folder / 'fixture-vm-status.json', {'run_id': run_id, 'vms': values})
        print(json.dumps(values), flush=True)
        return 0
    if (Path(a.db).resolve() != BASE / 'tasks.sqlite3'
            or Path(a.project_input).resolve() != folder / 'project.json'
            or os.environ.get('KAIBAIN_RUN_ID') != run_id
            or not a.task_id or int(a.expected_version) < 1
            or any((a.revision_input, a.recovery_input, a.continuation_input))):
        raise ValueError('Unexpected fixture invocation/context')
    with (folder / 'fixture-starts.jsonl').open('a', encoding='utf-8') as log:
        log.write(json.dumps({'pid': os.getpid(), 'run_id': run_id}) + '\n')
    project = json.loads(Path(a.project_input).read_text(encoding='utf-8'))
    if Path(project['repo']).resolve() != BASE / 'source':
        raise ValueError('Only the isolated source repository is allowed')
    state.mkdir()
    # Reuse only the validated prepared-image receipt, with a fresh owner. NEVER
    # prepare/pull, copy other VM receipts, or touch the configured user adapter.
    prepared = json.loads(Path(FIXED['prepared_receipt']).read_text(encoding='utf-8'))
    worker = Path(FIXED['runtime']) / 'src' / 'sandbox_worker.py'
    if (prepared.get('backend') != 'microsandbox' or prepared.get('image') != IMAGE
            or prepared.get('worker_sha256') != hashlib.sha256(worker.read_bytes()).hexdigest()):
        raise RuntimeError('Prepared image receipt is stale; setup must be done separately')
    save(state / 'image.json', prepared)
    manager = SandboxManager(state)
    info = manager.create(project['repo'], project['commit'], project['files'],
                          run_id=run_id, attempt_id='supervisor-interrupt-fixture')
    result = manager.execute(info['id'], project['checks'], timeout=10)
    if result['exit_code'] != 0 or result.get('stop_reason') is not None:
        raise RuntimeError('Small Python guest check failed: ' + json.dumps(result))
    live = manager.inspect(info['id'])
    if (live['running'] is not True or not live['runtime_id']
            or live['run_id'] != run_id or live['isolation'] != 'microvm'):
        raise RuntimeError('No live run-owned microVM')
    save(folder / 'fixture-ready.json', {
        'run_id': run_id, 'pid': os.getpid(), 'vm_id': live['id'],
        'runtime_id': live['runtime_id'], 'owner': live['owner'],
        'running': live['running'], 'runtime_evidence': live['runtime_evidence'],
        'guest_check': result,
    })
    print('READY: real VM exists and guest Python check passed; host controller waiting', flush=True)
    # Host-controller sleep only. The guest has already finished its tiny check.
    time.sleep(240)
    raise RuntimeError('Supervisor did not inject the interrupt')


if __name__ == '__main__':
    raise SystemExit(main())
'''


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def command_options():
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


def check(args):
    # Keep imports (and all runtime work) behind the explicit CLI gate.
    sys.path.insert(0, str(ROOT / 'src'))
    from kaibain import agent_runtime
    from kaibain.store import Store

    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError('run_job must run in the main process/main thread')
    if Path(sys.prefix).resolve() != (ROOT / '.venv').resolve():
        raise RuntimeError('Launch this test with the Kaibain main .venv Python')
    runtime = args.runtime_root.resolve(strict=True)
    # Do not resolve the interpreter symlink: that would lose the POSIX venv.
    python = runtime / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    prepared = (args.prepared_state or runtime / '.local' / 'microvms') / 'image.json'
    for path in (python, prepared, runtime / 'src' / 'development_web.py'):
        if not path.is_file():
            raise RuntimeError('Required existing runtime prerequisite missing: ' + str(path))
    import hashlib
    prepared_value=read_json(prepared)
    if (prepared_value.get('backend')!='microsandbox' or
            prepared_value.get('worker_sha256')!=hashlib.sha256((runtime/'src/sandbox_worker.py').read_bytes()).hexdigest()):
        raise RuntimeError('Prepared image receipt is stale; select an existing current --prepared-state before reserving a fixture run')

    base = ROOT / '.local' / 'supervisor-interrupt' / str(time.time_ns())
    base.mkdir(parents=True, exist_ok=False)
    report_path = base / 'check-report.json'
    report = {
        'ok': False, 'label': 'fault injection: trusted adapter fixture + REAL Microsandbox VM',
        'actual_model_proof': False, 'mcp_proof': False,
        'limitations': 'No model/MCP execution, coding replay, browser or cross-platform acceptance.',
        'interrupt': args.interrupt, 'withhold_cleanup_ack': args.withhold_cleanup_ack,
        'host_python': sys.executable, 'adapter_python': str(python),
        'artifacts': str(base), 'checks': {}, 'final_cleanup': {},
    }
    scopes = []
    outsider = None
    output = None
    adapter = base / 'trusted_adapter_fixture.py'
    original_sleep = time.sleep  # time is shared by other modules; save BEFORE patch.
    original_scope = agent_runtime.ProcessScope

    class ObservedScope(original_scope):
        """Retain only our real live handles for emergency finally cleanup."""
        def __init__(self, *positional, **keywords):
            self.fixture_closed = False
            scopes.append(self)
            super().__init__(*positional, **keywords)

        def close(self):
            super().close()
            self.fixture_closed = True

    def require(name, condition):
        report['checks'][name] = bool(condition)
        if not condition:
            raise AssertionError(name)

    def helper(mode, log_name, *, timeout=30):
        # The helper itself is also in a real owned ProcessScope, never a global
        # VM command. Its close is unconditional, including assertion failures.
        environment = {**os.environ, 'PYTHONUTF8': '1'}
        environment.pop('KAIBAIN_CONTROL_TOKEN', None)
        environment.pop('KAIBAIN_RUN_ID', None)
        with (base / log_name).open('ab') as log:
            scope = ObservedScope([str(python), str(adapter), mode, '--output', str(output)],
                                  log=log, cwd=str(base), env=environment)
            try:
                return scope.process.wait(timeout=timeout)
            finally:
                scope.close()

    try:
        fixed = {'runtime': str(runtime), 'prepared_receipt': str(prepared.resolve()),
                 'withhold_cleanup_ack': args.withhold_cleanup_ack}
        source = ADAPTER.replace('__FIXED_JSON__', repr(json.dumps(fixed)))
        compile(source, str(adapter), 'exec')
        adapter.write_text(source, encoding='utf-8')
        repo = base / 'source'
        repo.mkdir()
        guest = ('import json,os,platform\n'
                 'assert os.getuid() == 1000\n'
                 'assert platform.system() == "Linux"\n'
                 'assert sum([2, 3]) == 5\n'
                 'print(json.dumps({"guest_check": "passed", "uid": os.getuid(), '
                 '"kernel": platform.release()}))\n')
        (repo / 'guest_check.py').write_bytes(guest.encode('utf-8'))
        environment = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}
        environment.update({'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
                            'GIT_TERMINAL_PROMPT': '0', 'GIT_ALLOW_PROTOCOL': ''})
        git = ['git', '-c', 'core.hooksPath=' + os.devnull, '-c', 'core.fsmonitor=false',
               '-c', 'commit.gpgsign=false', '-c', 'core.autocrlf=false', '-C', str(repo)]

        def run_git(*argv):
            value = subprocess.run([*git, *argv], env=environment, capture_output=True,
                                   timeout=15, **command_options())
            with (base / 'git.log').open('ab') as log:
                log.write(value.stdout + value.stderr)
            value.check_returncode()
            return value.stdout.decode('utf-8').strip()

        run_git('init', '--quiet', '--template=')
        run_git('add', '--', 'guest_check.py')
        run_git('-c', 'user.name=Interrupt fixture', '-c', 'user.email=fixture@localhost',
                'commit', '--quiet', '-m', 'Isolated guest check')
        commit = run_git('rev-parse', 'HEAD')
        store = Store(base / 'tasks.sqlite3')
        project = store.create_project('Supervisor interrupt fixture', {
            'repo': str(repo), 'commit': commit, 'files': ['guest_check.py'],
            'writable': ['guest_check.py'], 'checks': ['python', 'guest_check.py'],
            'acceptance': ['Guest check passes before supervisor fault injection.'],
        })
        root = store.create('Supervisor interrupt fault injection', project_id=project['id'])
        profile = store.resolve_project(project['id'], project['version'])
        command = [str(python), str(adapter)]
        config = base / 'fixture-runtime.json'
        write_json(config, {'kind': 'development-v1', 'label': report['label'], 'command': command})

        # Our own unrelated host-controller fixture, created before any adapter
        # scope and placed in its own POSIX session. It has no descendants.
        outsider_ready = base / 'outsider-ready.json'
        outsider_code = ('import json,os,sys,time;from pathlib import Path;'
                         'Path(sys.argv[1]).write_text(json.dumps({"pid":os.getpid()}));'
                         'time.sleep(300)')
        options = command_options() if os.name == 'nt' else {'start_new_session': True}
        with (base / 'outsider.log').open('wb') as log:
            outsider = subprocess.Popen([sys.executable, '-I', '-c', outsider_code, str(outsider_ready)],
                                        stdin=subprocess.DEVNULL, stdout=log, stderr=log, **options)
        deadline = time.monotonic() + 10
        while not outsider_ready.exists() and outsider.poll() is None and time.monotonic() < deadline:
            original_sleep(.05)
        require('outsider_ready', outsider_ready.is_file() and outsider.poll() is None)

        run = store.reserve_agent_run(root['id'], root['version'], report['label'], command,
                                      project=profile['project'], project_ref=profile['project_ref'])
        folder = base / '.agent-runs' / run['id']
        output = folder / 'report.json'
        report.update({'run_id': run['id'], 'root_id': root['id'], 'source_commit': commit,
                       'root_before': store.get(root['id']), 'project_before': store.get_project(project['id']),
                       'outsider_pid': outsider.pid})
        ready = folder / 'fixture-ready.json'
        injected = KeyboardInterrupt('fixture supervisor interrupt') if args.interrupt == 'keyboard' else SystemExit(79)
        armed = True
        deadline = time.monotonic() + args.ready_timeout

        def inject_once(seconds):
            nonlocal armed
            # Patching module.time.sleep also patches time.sleep elsewhere. Only
            # inject in execute_owned's polling loop, NEVER in cleanup/finally.
            if armed and sys._getframe(1).f_code is agent_runtime.execute_owned.__code__:
                if ready.is_file():
                    armed = False  # Disarm BEFORE raising or entering any finally.
                    raise injected
                if time.monotonic() >= deadline:
                    armed = False
                    raise TimeoutError('Real VM/guest readiness marker timed out')
            return original_sleep(seconds)

        caught = False
        with (base / 'supervisor.log').open('w', encoding='utf-8') as log:
            with redirect_stdout(log), redirect_stderr(log):
                with patch.object(agent_runtime, 'ProcessScope', ObservedScope):
                    with patch('kaibain.agent_runtime.time.sleep', new=inject_once):
                        try:
                            agent_runtime.run_job(store, run['id'])
                        except (KeyboardInterrupt, SystemExit) as error:
                            if error is not injected:
                                raise
                            caught = True
                            # Snapshot DURABLE state at re-raise, before fallback.
                            report['run_at_reraise'] = Store(store.path).agent_runs()[0]
                            report['scope_at_reraise'] = read_json(folder / 'scope.json')
        report['supervisor_exit'] = 'original_interrupt_reraised' if caught else 'returned'
        report['run_at_exit'] = Store(store.path).agent_runs()[0]
        report['scope_at_exit'] = read_json(folder / 'scope.json')
        require('interrupt_injected_once', not armed and ready.is_file())
        if args.withhold_cleanup_ack:
            # cleanup_owned raises ProcessCleanupError from the finally block;
            # run_job records cleanup_failed and handles that error by returning.
            require('cleanup_failure_handled', not caught)
        else:
            require('original_interrupt_reraised', caught)
        first_scope = report['scope_at_exit']
        saved = report['run_at_exit']
        report['ready'] = read_json(ready)
        report['cleanup_before_fallback'] = read_json(folder / 'cleanup.json')
        report['cleanup_marker_before_fallback'] = read_json(folder / 'fixture-cleanup-success.json')
        require('first_scope_closed_valid_run_id', first_scope['status'] == 'closed'
                and first_scope['run_id'] == run['id'] and first_scope['platform'] == os.name
                and type(first_scope['pid']) is int and first_scope['pid'] > 1)
        require('scope_process_exited', not agent_runtime.process_alive(first_scope['pid']))
        require('sqlite_interrupted_before_exit', saved['status'] == 'interrupted')
        expected = 'cleanup_failed' if args.withhold_cleanup_ack else 'supervisor_interrupted'
        require('sqlite_failure_code', saved['failure_code'] == expected)
        require('no_result_or_review', saved['result'] is None and saved['review_versions'] is None
                and saved['decision'] == 'pending' and saved['independent_review'] is None)
        require('real_cleanup_confirmed', report['cleanup_before_fallback'].get('ok') is True
                and report['cleanup_before_fallback'].get('records_checked') == 1
                and report['cleanup_marker_before_fallback']['run_id'] == run['id']
                and report['cleanup_marker_before_fallback']['after_real_development_web_cleanup'] is True)
        require('independent_real_vm_inspection', helper('--inspect-stopped', 'vm-inspection.log') == 0)
        report['vm_status_before_fallback'] = read_json(folder / 'fixture-vm-status.json')
        vms = report['vm_status_before_fallback']['vms']
        require('real_vm_stopped_and_absent', len(vms) == 1 and vms[0]['id'] == report['ready']['vm_id']
                and vms[0]['runtime_id'] == report['ready']['runtime_id']
                and vms[0]['owner'] == report['ready']['owner'] and vms[0]['run_id'] == run['id']
                and vms[0]['status'] == 'stopped' and vms[0]['running'] is False
                and vms[0]['missing'] is True)
        require('outsider_survived_supervisor_cleanup', outsider.poll() is None)
        require('root_and_requirements_unchanged', store.get(root['id']) == report['root_before'])
        require('project_unchanged', store.get_project(project['id']) == report['project_before'])
        with store.connection() as db:
            counts = {table: db.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                      for table in ('tasks', 'agents', 'goal_results', 'run_checkpoints', 'goal_questions')}
        report['isolated_counts'] = counts
        require('no_agent_mcp_records_or_subtasks', counts == {
            'tasks': 1, 'agents': 0, 'goal_results': 0, 'run_checkpoints': 0, 'goal_questions': 0})
        runtime_view = agent_runtime.AgentRuntime(store, config)
        for _ in range(2):
            runtime_view.recover()
            require('no_auto_resume', runtime_view.snapshot()['runs'] == [{**saved, 'progress': None}])
            original_sleep(.1)
        starts = (folder / 'fixture-starts.jsonl').read_text(encoding='utf-8').splitlines()
        require('adapter_started_exactly_once', len(starts) == 1)
        require('source_git_and_bytes_unchanged', run_git('rev-parse', 'HEAD') == commit
                and run_git('status', '--porcelain') == ''
                and (repo / 'guest_check.py').read_bytes() == guest.encode('utf-8'))
        report['ok'] = True
    except BaseException as error:
        report['error'] = {'type': type(error).__name__, 'message': str(error),
                           'traceback': traceback.format_exc()}
    finally:
        # Always preserve evidence from the supervisor BEFORE this safety net.
        # Retain handles to our actual scopes; NEVER signal a PID from a receipt.
        for scope in reversed(scopes[:]):
            if not scope.fixture_closed and getattr(scope, 'process', None) is not None:
                try:
                    scope.close()
                except BaseException as error:
                    report['final_cleanup'].setdefault('scope_errors', []).append(str(error))
        if output is not None and (output.parent / 'microvms').is_dir():
            try:
                code = helper('--cleanup', 'finally-cleanup.log')
                report['final_cleanup']['adapter_exit_code'] = code
                # In acknowledgement-failure mode nonzero is intentional; the
                # REAL inspection must still establish that all owned VMs exited.
                inspect_code = helper('--inspect-stopped', 'finally-vm-inspection.log')
                status = read_json(output.parent / 'fixture-vm-status.json')
                report['final_cleanup']['vm_status'] = status
                report['final_cleanup']['owned_vms_absent'] = inspect_code == 0 and all(
                    item['status'] == 'stopped' and item['running'] is False and item['missing'] is True
                    for item in status['vms'])
                if not report['final_cleanup']['owned_vms_absent'] or (code and not args.withhold_cleanup_ack):
                    report['ok'] = False
            except BaseException as error:
                report['final_cleanup']['error'] = traceback.format_exc()
                report['ok'] = False
        if outsider is not None:
            report['final_cleanup']['outsider_alive_before_teardown'] = outsider.poll() is None
            if not report['final_cleanup']['outsider_alive_before_teardown']:
                report['ok'] = False
            try:
                if outsider.poll() is None:
                    outsider.terminate()  # Only this exact fixture Popen handle.
                try:
                    outsider.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    outsider.kill()
                    outsider.wait(timeout=5)
                report['final_cleanup']['outsider_reaped'] = True
            except BaseException:
                report['final_cleanup']['outsider_error'] = traceback.format_exc()
                report['ok'] = False
        if report['final_cleanup'].get('scope_errors'):
            report['ok'] = False
        write_json(report_path, report)
    print(json.dumps({'ok': report['ok'], 'label': report['label'], 'report': str(report_path)}, ensure_ascii=False))
    return 0 if report['ok'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-real-vm', action='store_true', help='Explicit opt-in: create a real owned VM')
    parser.add_argument('--runtime-root', type=Path, default=ROOT.parent / 'multi-agent-system')
    parser.add_argument('--prepared-state', type=Path, help='Existing runtime prepared-image state (read only)')
    parser.add_argument('--interrupt', choices=('keyboard', 'system-exit'), default='keyboard')
    parser.add_argument('--withhold-cleanup-ack', action='store_true', help='Real cleanup, then injected exit code 23')
    parser.add_argument('--ready-timeout', type=int, default=120, help='VM readiness deadline, 10..180 seconds')
    args = parser.parse_args()
    if not args.run_real_vm:
        parser.error('Requires --run-real-vm. No VM/integration ran. Wait until core edits are ready.')
    if not 10 <= args.ready_timeout <= 180:
        parser.error('--ready-timeout must be 10..180 seconds')
    return check(args)


if __name__ == '__main__':
    raise SystemExit(main())
