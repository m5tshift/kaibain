"""Opt-in actual Claude Code / Codex CLI acceptance; never touches the user DB.

No model calls by default. See docs/CLIENT_ACCEPTANCE.md for evidence boundaries.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
TOOLS = ["get_workspace", "get_task", "get_task_tree", "register_agent",
         "list_agents", "assign_agent", "heartbeat_agent", "create_task",
         "set_task_status", "submit_goal_result", "disconnect_agent"]


def clean(value):
    """Sanitize synthetic fixture evidence too; never persist raw client output."""
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()
                if not re.search(r"token|secret|credential|email|owner_hash", k, re.I)}
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, str):
        value = value.replace(str(Path.home()), "<HOME>")
        value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "<EMAIL>", value)
        value = re.sub(r"(?i)bearer\s+\S+|\bsk-[\w-]+|\beyJ[\w.-]+", "<SECRET>", value)
    return value


def save(path, value):
    path.write_text(json.dumps(clean(value), indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


class CleanupError(RuntimeError):
    """An owned command tree could not be confirmed stopped."""


def owned_command():
    """Wait for ownership before starting the real CLI, without a shell."""
    payload = json.load(sys.stdin)
    argv = payload["argv"]
    if not isinstance(argv, list) or not argv or not all(isinstance(v, str) for v in argv):
        raise ValueError("Invalid owned argv")
    return subprocess.run(argv, input=payload["input"], text=True, encoding="utf-8",
                          errors="replace", stdin=None if payload["input"] is not None else subprocess.DEVNULL).returncode


def run(argv, *, cwd=None, timeout=30, text=None, env=None):
    """Own all descendants before releasing a stdin gate; clean on every exit.

    Reuse the application's Windows Job Object, not PID/port enumeration or
    taskkill. POSIX uses a newly created session. No security sandbox is claimed.
    Raw client output stays in memory only.
    """
    sys.path.insert(0, str(ROOT / "src"))
    from kaibain.process_scope import WindowsJob
    job = WindowsJob() if os.name == "nt" else None
    process = None
    assigned = False
    try:
        options = ({"creationflags": subprocess.CREATE_NO_WINDOW} if job else {"start_new_session": True})
        process_env = dict(os.environ if env is None else env, PYTHONDONTWRITEBYTECODE="1")
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--owned-command"],
                                   cwd=cwd, env=process_env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", **options)
        if job:
            job.assign(process)
        assigned = True
        output, errors = process.communicate(json.dumps({"argv": argv, "input": text}), timeout=timeout)
        return subprocess.CompletedProcess(argv, process.returncode, output, errors)
    finally:
        try:
            if assigned:
                if job:
                    job.terminate()  # Waits for this job's active process count to become zero.
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            elif process:
                process.kill()  # Gate still closed: no descendants could have started.
            if process:
                process.wait(timeout=5)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream and not stream.closed:
                        stream.close()
        except Exception as error:
            raise CleanupError("Owned process cleanup was not confirmed; stop acceptance") from error
        finally:
            if job:
                job.close()


def locate(client):
    found = shutil.which(client)
    if found:
        return found
    home = Path.home()
    paths = [home / ".local/bin" / (client + ".exe"),
             Path(os.environ.get("APPDATA", str(home))) / "npm" / (client + ".cmd"),
             Path(os.environ.get("LOCALAPPDATA", str(home))) / "Microsoft/WinGet/Links" / (client + ".exe")]
    return next((str(p) for p in paths if p.is_file()), None)


def probe(client, folder):
    executable = locate(client)
    result = {"client": client, "executable": executable, "ready_for_attempt": False}
    if not executable:
        result.update(gate="not_installed_or_not_discoverable",
                      next_step="Install the client separately or put its existing executable on PATH; then use its native login/trust flow.")
        save(folder / (client + "-probe.json"), result)
        return result
    if os.name == "nt" and Path(executable).suffix.lower() != ".exe":
        result["gate"] = "native_executable_required_no_shell_shims"
        save(folder / (client + "-probe.json"), result)
        return result
    try:
        version = run([executable, "--version"])
        match = re.search(r"\d+\.\d+\.\d+", version.stdout + version.stderr)
        result["version"] = match.group() if match else "unknown"
        help_argv = [executable, "exec", "--help"] if client == "codex" else [executable, "--help"]
        help_result = run(help_argv)
        help_text = help_result.stdout + help_result.stderr
        required = (["--json", "--ephemeral", "--ignore-user-config", "--sandbox", "--config", "--strict-config"]
                    if client == "codex" else ["--mcp-config", "--strict-mcp-config", "--max-turns", "--output-format",
                                               "--tools", "--settings", "--setting-sources", "--no-session-persistence"])
        result["supported_required_flags"] = {flag: flag in help_text for flag in required}
        auth_argv = [executable, "login", "status"] if client == "codex" else [executable, "auth", "status"]
        auth = run(auth_argv)
        logged_in = auth.returncode == 0
        auth_mode = "unknown"
        if client == "claude":
            try:
                details = json.loads(auth.stdout)
                logged_in = logged_in and details.get("loggedIn") is True
                auth_mode = "subscription" if details.get("authMethod") in ("claude.ai", "oauth") else "unknown_or_api"
            except ValueError:
                logged_in = False
        elif re.search(r"logged in using chatgpt", auth.stdout + auth.stderr, re.I):
            auth_mode = "subscription"
        result["auth"] = {"command": auth_argv, "exit_code": auth.returncode, "logged_in": logged_in, "mode": auth_mode}
        key_names = (["OPENAI_API_KEY", "CODEX_API_KEY"] if client == "codex" else
                     ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"])
        result["api_key_environment_present"] = any(os.environ.get(name) for name in key_names)
        result["probe_commands_succeeded"] = version.returncode == help_result.returncode == 0
        result["owned_process_cleanup"] = "confirmed"
        result["ready_for_attempt"] = (logged_in and auth_mode == "subscription" and
                                       not result["api_key_environment_present"] and result["probe_commands_succeeded"] and
                                       all(result["supported_required_flags"].values()))
        result["gate"] = ("native_trust_and_model_access_not_yet_exercised" if result["ready_for_attempt"] else
                          "native_login_required" if not logged_in else
                          "existing_subscription_auth_required_no_api_keys" if auth_mode != "subscription" or result["api_key_environment_present"] else
                          "unsupported_installed_cli_options")
        result["help_command"] = help_argv
    except (OSError, subprocess.TimeoutExpired) as error:
        result["gate"] = type(error).__name__
    save(folder / (client + "-probe.json"), result)
    return result


def decode(message):
    result = message.get("result", {})
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    for part in result.get("content", []):
        if part.get("type") == "text":
            try:
                return json.loads(part["text"])
            except ValueError:
                pass
    return {"ok": False, "error": message.get("error", {"code": "NO_STRUCTURED_RESULT"})}


def proxy(db, evidence, caller="actual_cli"):
    """Transparent JSONL stdio relay to the real adapter, with attributed pulses.

    Timer heartbeats maintain the same process identity during client inference.
    They are separately attributed and cannot count as client tool-call proof.
    """
    db = Path(db).resolve()
    fixture_root = (ROOT / ".local/client-acceptance").resolve()
    evidence = Path(evidence).resolve()
    if (not db.is_relative_to(fixture_root) or not evidence.is_relative_to(fixture_root) or
            db.name != "fixture.sqlite3" or not (db.parent / "CLIENT_ACCEPTANCE_FIXTURE").is_file()):
        raise ValueError("Only a marked isolated fixture database is permitted")
    if os.environ.get("KAIBAIN_RUN_ID"):
        raise ValueError("Cannot run fixture proxy inside a built-in goal run")
    child_env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    child = subprocess.Popen([sys.executable, str(ROOT / "run.py"), "mcp", "--db", str(db)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             text=True, encoding="utf-8", bufsize=1, cwd=db.parent, env=child_env)
    write_lock, log_lock, pending_lock = threading.Lock(), threading.Lock(), threading.Lock()
    stdout_lock = threading.Lock()
    active, stop = threading.Event(), threading.Event()
    pending = {}

    def record(value):
        with log_lock, Path(evidence).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(clean({"at": time.time(), **value}), ensure_ascii=True) + "\n")

    def send(value, source):
        method = value.get("method")
        params = value.get("params", {})
        if method == "tools/call" and params.get("name") not in TOOLS:
            body = {"ok": False, "error": {"code": "FIXTURE_TOOL_NOT_ALLOWED"}}
            record({"source": source, "tool": params.get("name"), "arguments": {}, "result": body})
            with stdout_lock:
                sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": value.get("id"),
                                            "result": {"isError": True, "content": [{"type": "text", "text": json.dumps(body)}]}}) + "\n")
                sys.stdout.flush()
            return
        with pending_lock:
            if "id" in value:
                pending[value["id"]] = (source, method, params)
        with write_lock:
            child.stdin.write(json.dumps(value) + "\n")
            child.stdin.flush()

    def responses():
        for line in child.stdout:
            message = json.loads(line)
            with pending_lock:
                request = pending.pop(message.get("id"), None)
            if request:
                source, method, params = request
                if method == "tools/list" and "result" in message:
                    message["result"]["tools"] = [tool for tool in message["result"].get("tools", []) if tool.get("name") in TOOLS]
                    line = json.dumps(message) + "\n"
                if method == "tools/call":
                    body = decode(message)
                    record({"source": source, "tool": params["name"], "arguments": params.get("arguments", {}), "result": body})
                    if body.get("ok") and params["name"] == "register_agent":
                        active.set()
                    if body.get("ok") and params["name"] == "disconnect_agent":
                        active.clear()
                    if source == "harness_timer":
                        continue
            with stdout_lock:
                sys.stdout.write(line)
                sys.stdout.flush()
        stop.set()

    def pulses():
        while not stop.wait(5):
            if active.is_set():
                try:
                    send({"jsonrpc": "2.0", "id": "fixture-heartbeat-" + uuid4().hex,
                          "method": "tools/call", "params": {"name": "heartbeat_agent", "arguments": {}}}, "harness_timer")
                except (OSError, ValueError):
                    return

    reader = threading.Thread(target=responses, daemon=True)
    reader.start()
    threading.Thread(target=pulses, daemon=True).start()
    try:
        for line in sys.stdin:
            value = json.loads(line)
            send(value, caller)
    finally:
        stop.set()
        child.stdin.close()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.terminate()  # This exact child only; never a port/PID search.
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        reader.join(timeout=5)


@contextmanager
def fixture():
    sys.path.insert(0, str(ROOT / "src"))
    from kaibain.store import Store
    from kaibain.web import make_server
    fixture_root = ROOT / ".local/client-acceptance"
    fixture_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fixture-", dir=fixture_root) as temp:
        folder = Path(temp)
        (folder / "CLIENT_ACCEPTANCE_FIXTURE").write_text("synthetic only\n", encoding="utf-8")
        git = shutil.which("git")
        if not git:
            raise RuntimeError("Git must already be installed")
        # No inherited alternate index/worktree/template/signing/hooks configuration.
        git_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        git_env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        template = folder / "empty-template"
        template.mkdir()
        for args in (["init", "--quiet", "--template=" + str(template)],
                     ["-c", "user.name=Fixture", "-c", "user.email=kaibain@localhost", "-c", "commit.gpgsign=false",
                      "-c", "core.hooksPath=" + str(template), "commit", "--allow-empty", "-m", "Synthetic client acceptance"]):
            result = run([git, "-C", str(folder), *args], env=git_env)
            if result.returncode:
                raise RuntimeError("Isolated Git fixture setup failed")
        store = Store(folder / "fixture.sqlite3")
        server = make_server(store, 0, runtime_config=folder / "missing-runtime.json")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield folder, store, f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


def http(base, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
    # Do not route local fixture traffic through a configured HTTP proxy.
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=10) as response:
        return json.load(response)


def prompt(goal_id, client):
    return f"""Perform ONE short synthetic Kaibain MCP acceptance. Use ONLY the
kaibain_acceptance MCP server. Do not use shell, files, other servers or subagents.
Do not bypass any native approval/trust gate; if blocked, stop and state the gate.
All data is a named isolated SQLite fixture, not a real user goal.
1. get_workspace (workflow must be agents_native), then get_task {goal_id}.
2. register_agent ONCE: name='Acceptance {client}', role='coordinator',
   model='client default (self-reported)', framework='{client} CLI acceptance'.
3. list_agents; assign_agent yourself to {goal_id}, using the fresh agent version.
4. heartbeat_agent status='working', action='Synthetic acceptance'. A transparent
   relay maintains plain heartbeats every 5s during inference; do not claim those as your calls.
5. create_task title='Acceptance child' parent_id={goal_id}, then create_task
   title='Acceptance nested' parent_id=<new child id>. Exactly two subtasks.
6. get_task nested; set_task_status nested to done with its freshly read version;
   get_task nested again and verify done. get_task child; set_task_status child to
   done with fresh version; get_task child again. Do not complete the root goal.
7. get_task_tree {goal_id}; get_task {goal_id} for its newest version;
   submit_goal_result with that version, summary='Synthetic CLI acceptance complete',
   checks=['Nested tasks freshly read as done; root left for fixture human review'].
8. get_task {goal_id} to verify goal_result; disconnect_agent, then stop.
Keep reasoning and final response brief. Never accept/apply results yourself.
"""


def read_events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def verify(events, store, goal_id):
    calls = [e for e in events if e["source"] == "actual_cli" and e["result"].get("ok")]
    names = [e["tool"] for e in calls]
    required = set(TOOLS)
    checks = {"actual_cli_tools": required.issubset(names),
              "single_registration": sum(e["source"] == "actual_cli" and e["tool"] == "register_agent" for e in events) == 1,
              "only_fixture_tools": all(e["tool"] in TOOLS for e in events if e["source"] == "actual_cli")}
    workspace = next((e["result"]["data"] for e in calls if e["tool"] == "get_workspace"), {})
    checks["agents_native"] = workspace.get("workflow") == "agents_native"
    tasks = store.list()["tasks"]
    child = next((t for t in tasks if t["parent_id"] == goal_id), None)
    nested = next((t for t in tasks if child and t["parent_id"] == child["id"]), None)
    checks["nested_done_in_database"] = len(tasks) == 3 and bool(child and nested and child["status"] == nested["status"] == "done")
    for label, task in (("child", child), ("nested", nested)):
        task_id = task["id"] if task else None
        changes = [i for i, e in enumerate(calls) if e["tool"] == "set_task_status" and e["arguments"].get("task_id") == task_id]
        def reads(e):
            return e["tool"] == "get_task" and e["arguments"].get("task_id") == task_id
        checks[label + "_fresh_read_write_read"] = any(
            (previous := next((e for e in reversed(calls[:i]) if reads(e)), None)) is not None and
            previous["result"]["data"]["version"] == calls[i]["arguments"].get("expected_version") and
            calls[i]["arguments"].get("status") == "done" and
            any(reads(e) and e["result"]["data"]["status"] == "done" for e in calls[i+1:]) for i in changes)
    goal = store.get(goal_id)
    checks["root_review_pending"] = goal["status"] == "review" and bool(goal["goal_result"] and goal["goal_result"]["decision"] == "pending")
    agents = store.list_agents()["agents"]
    checks["coordinator_assigned_disconnected"] = len(agents) == 1 and agents[0]["role"] == "coordinator" and agents[0]["task_id"] == goal_id and not agents[0]["connected"]
    checks["assignment_used_fresh_agent_read"] = False
    for i, call in enumerate(calls):
        if call["tool"] != "assign_agent" or call["arguments"].get("task_id") != goal_id:
            continue
        listed = next((e for e in reversed(calls[:i]) if e["tool"] == "list_agents"), None)
        if listed:
            target = next((a for a in listed["result"]["data"]["agents"] if a["id"] == call["arguments"].get("target_agent_id")), None)
            checks["assignment_used_fresh_agent_read"] |= bool(target and target["version"] == call["arguments"].get("expected_version"))
    submissions = [i for i, e in enumerate(calls) if e["tool"] == "submit_goal_result" and e["arguments"].get("task_id") == goal_id]
    def root_read(e):
        return e["tool"] == "get_task" and e["arguments"].get("task_id") == goal_id
    checks["client_read_saved_report"] = any(
        (previous := next((e for e in reversed(calls[:i]) if root_read(e)), None)) is not None and
        previous["result"]["data"]["version"] == calls[i]["arguments"].get("expected_version") and
        any(root_read(e) and (e["result"]["data"].get("goal_result") or {}).get("decision") == "pending" for e in calls[i+1:])
        for i in submissions)
    return checks


def classify(output):
    patterns = {"unsupported_invocation_configuration": r"unknown (?:configuration )?(?:field|feature|variant)|unrecognized (?:field|option)|unexpected argument|error parsing|invalid config",
                "native_trust_or_approval_required": r"not trusted|trust.*(?:required|directory)|approval.*required|requires? approval|approval policy is never|native approval gate|permission.*denied|tool.*denied",
                "native_login_required": r"not logged in|authentication.*(?:failed|required)|401 unauthorized|invalid.*(?:token|key)",
                "account_limit_or_billing": r"usage limit|rate limit|quota|insufficient|billing|429",
                "model_or_network_failure": r"connection|timed out|network|model.*not.*(?:found|available)|stream disconnected"}
    return next((gate for gate, pattern in patterns.items() if re.search(pattern, output, re.I)), "client_did_not_complete_required_mcp_calls")


def client_events(output):
    """Retain event/tool names and normalized failures, not model messages."""
    types, attempts = [], []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if re.fullmatch(r"[a-z_.-]{1,80}", str(event.get("type", ""))):
            types.append(event["type"])
        item = event.get("item", {})
        if isinstance(item, dict) and item.get("type") == "mcp_tool_call" and item.get("status") != "in_progress":
            attempts.append({"server": "kaibain_acceptance" if item.get("server") == "kaibain_acceptance" else "other_server",
                             "tool": item.get("tool") if item.get("tool") in TOOLS else "other_tool",
                             "status": "completed" if item.get("status") == "completed" else "failed",
                             "gate": classify(json.dumps(item.get("error"))) if item.get("error") else None})
    return types, attempts


def configuration_fingerprints():
    """Read hashes of config only. Never read/persist credentials or user DBs."""
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    claude_home = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
    paths = {"codex_user_config": codex_home / "config.toml",
             "claude_user_mcp": Path.home() / ".claude.json",
             "claude_user_settings": claude_home / "settings.json",
             "project_mcp": ROOT / ".mcp.json",
             "project_codex": ROOT / ".local/codex.toml"}
    return {name: hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None for name, path in paths.items()}


def self_check(evidence):
    """Model-free checks of ownership and relay boundaries, never CLI proof."""
    import asyncio
    from unittest.mock import patch
    sys.path.insert(0, str(ROOT / "src"))
    from kaibain.agent_runtime import process_alive
    from kaibain.process_scope import WindowsJob
    checks = {}
    approval_error = "MCP tool call requires approval, but approval policy is never"
    checks["native_approval_failure_classified"] = classify(approval_error) == "native_trust_or_approval_required"
    checks["strict_config_failure_classified"] = classify("Error loading config.toml: unknown configuration field `tools.view_image`") == "unsupported_invocation_configuration"
    _, attempts = client_events(json.dumps({"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "kaibain_acceptance", "tool": "get_workspace", "status": "failed", "error": {"message": approval_error}}}))
    checks["denied_client_attempt_is_not_success"] = attempts[0]["status"] == "failed" and attempts[0]["gate"] == "native_trust_or_approval_required"
    result = run([sys.executable, "-c", "import sys; print(sys.stdin.read()); print('fixture stderr',file=sys.stderr); sys.exit(7)"], text="fixture stdin")
    checks["stdin_stdout_stderr_exit_code"] = result.returncode == 7 and "fixture stdin" in result.stdout and "fixture stderr" in result.stderr
    outsider = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
    try:
        for timeout_case in (False, True):
            pids = evidence / ("timeout-pids.json" if timeout_case else "normal-pids.json")
            code = ("import subprocess,sys,os,json,time; from pathlib import Path; "
                    "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],"
                    "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
                    f"Path({str(pids)!r}).write_text(json.dumps([os.getpid(),p.pid])); " +
                    ("time.sleep(60)" if timeout_case else "sys.exit(0)"))
            timed_out = False
            try:
                result = run([sys.executable, "-c", code], timeout=2)
            except subprocess.TimeoutExpired:
                timed_out = True
            owned = json.loads(pids.read_text(encoding="utf-8"))
            checks[("timeout" if timeout_case else "normal_exit") + "_tree_cleanup"] = (
                timed_out == timeout_case and all(not process_alive(pid) for pid in owned))
        checks["unrelated_process_survived"] = outsider.poll() is None
    finally:
        outsider.kill()  # The exact test child we created, never a saved/discovered PID.
        outsider.wait(timeout=5)
    if os.name == "nt":
        marker = evidence / "must-not-execute"
        with patch.object(WindowsJob, "assign", side_effect=OSError("synthetic assignment failure")):
            try:
                run([sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"])
            except OSError:
                checks["ownership_failure_keeps_gate_closed"] = not marker.exists()
            else:
                checks["ownership_failure_keeps_gate_closed"] = False

    async def relay_check(db):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        params = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).resolve()),
            "--stdio-proxy", "--fixture-db", str(db), "--transcript", str(evidence / "relay-self-check.jsonl"), "--caller", "sdk_self_check"])
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                listed = await session.list_tools()
                workspace = await session.call_tool("get_workspace", {})
                denied = await session.call_tool("delete_task", {"task_id": "must-not-be-deleted", "expected_version": 1})
                checks["relay_lists_only_allowed_tools"] = {t.name for t in listed.tools} == set(TOOLS)
                checks["relay_real_adapter_read"] = decode({"result": workspace.model_dump()}).get("ok") is True
                checks["relay_rejects_unlisted_tool"] = denied.isError is True

    with fixture() as (_, store, _):
        before = store.list()
        asyncio.run(relay_check(store.path))
        checks["relay_denial_did_not_write"] = store.list() == before
        checks["sdk_events_not_labeled_actual_cli"] = all(e["source"] == "sdk_self_check" for e in read_events(evidence / "relay-self-check.jsonl"))
    save(evidence / "self-check.json", {"checks": checks, "actual_cli": False})
    return checks


def acceptance(client, info, evidence, timeout, human_review):
    if not info["ready_for_attempt"]:
        report = {"client": client, "status": "pending", "gate": info["gate"]}
        save(evidence / (client + "-result.json"), report)
        return report
    with fixture() as (folder, store, base):
        health = http(base, "/api/health")
        if not health["ok"] or Path(health["database"]).resolve() != store.path.resolve():
            raise RuntimeError("Isolated fixture health/database identity check failed")
        goal = http(base, "/api/goals", {"title": "CLIENT ACCEPTANCE FIXTURE: " + client})["goal"]
        transcript = evidence / (client + "-mcp.jsonl")
        server_args = [str(Path(__file__).resolve()), "--stdio-proxy", "--fixture-db", str(store.path), "--transcript", str(transcript)]
        text = prompt(goal["id"], client)
        if client == "codex":
            argv = [info["executable"], "exec", "--ignore-user-config", "--ephemeral", "--strict-config", "--json", "--color", "never", "--sandbox", "read-only", "-C", str(folder)]
            overrides = {"features.shell_tool": False, "features.multi_agent": False,
                         "features.apps": False, "features.plugins": False, "features.hooks": False,
                         "features.shell_snapshot": False, "web_search": "disabled",
                         "mcp_servers.kaibain_acceptance.command": sys.executable,
                         "mcp_servers.kaibain_acceptance.args": server_args,
                         "mcp_servers.kaibain_acceptance.enabled_tools": TOOLS,
                         "mcp_servers.kaibain_acceptance.required": True,
                         "mcp_servers.kaibain_acceptance.startup_timeout_sec": 30}
            for key, value in overrides.items():
                argv.extend(["-c", key + "=" + json.dumps(value)])
            argv.append("-")
        else:
            config = folder / "client-mcp.json"
            config.write_text(json.dumps({"mcpServers": {"kaibain_acceptance": {"command": sys.executable, "args": server_args}}}), encoding="utf-8")
            argv = [info["executable"], "-p", "--strict-mcp-config", "--mcp-config", str(config),
                    "--tools", "", "--setting-sources", "", "--settings", '{"disableAllHooks":true}',
                    "--no-session-persistence", "--output-format", "json", "--max-turns", "32"]
        save(evidence / (client + "-command.json"), {"argv": argv, "cwd": str(folder), "stdin": text,
              "timeout_seconds": timeout, "native_approvals": "client defaults; no preapproval, approval-mode or trust overrides; native gates may refuse headless calls",
              "configuration": "per invocation only; global MCP entries untouched and not loaded"})
        started = time.monotonic()
        config_before = configuration_fingerprints()
        cleanup = "confirmed"
        try:
            result = run(argv, cwd=folder, timeout=timeout, text=text)
            output, returncode = result.stdout + result.stderr, result.returncode
        except subprocess.TimeoutExpired as error:
            def as_text(value):
                return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
            output = as_text(error.stdout) + as_text(error.stderr)
            returncode = None
        events = read_events(transcript)
        config_after = configuration_fingerprints()
        save(evidence / (client + "-configuration-observation.json"), {"before": config_before, "after": config_after,
             "observed_unchanged": config_before == config_after, "note": "Observation only; another parallel process could change these files"})
        checks = verify(events, store, goal["id"])
        verified = returncode == 0 and all(checks.values())
        report = {"client": client, "version": info["version"], "status": "verified" if verified else "pending",
                  "exit_code": returncode, "elapsed_seconds": round(time.monotonic() - started, 2),
                  "checks": checks, "actual_cli_successful_tools": [e["tool"] for e in events if e["source"] == "actual_cli" and e["result"].get("ok")],
                  "harness_timer_pulses": sum(e["source"] == "harness_timer" for e in events),
                  "owned_process_cleanup": cleanup,
                  "native_five_second_heartbeat": "not_verified; harness timer is separately attributed",
                  "gate": None if verified else "timeout" if returncode is None else classify(output),
                  "human_review": "not_requested", "claims": "actual external CLI only; no VM, sandbox, UI or publication proof"}
        report["client_event_types"], report["client_mcp_attempts"] = client_events(output)
        report["configuration_observed_unchanged"] = config_before == config_after
        if verified and human_review:
            current = store.get(goal["id"])
            saved = current["goal_result"]
            reviewed = http(base, "/api/goals/" + goal["id"] + "/review", {
                "expected_version": current["version"], "result_id": saved["id"],
                "expected_result_version": saved["version"], "decision": "accepted", "comment": "Isolated fixture control only"})
            assert reviewed["ok"] and store.get(goal["id"])["status"] == "done"
            report["human_review"] = "isolated HTTP fixture acceptance verified; not a client or human UI action"
        save(evidence / (client + "-snapshot.json"), {"tasks": store.list(), "agents": store.list_agents()})
        save(evidence / (client + "-result.json"), report)
        return report


def main():
    if sys.argv[1:] == ["--owned-command"]:
        return owned_command()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=["all", "claude", "codex"], default="all")
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--run", action="store_true", help="Opt in to one small logged-in CLI attempt per available client")
    parser.add_argument("--fixture-human-review", action="store_true", help="After verified client proof, accept only the named isolated fixture over HTTP")
    operation.add_argument("--fixture-only", action="store_true", help="Verify isolated Git/SQLite/HTTP setup without a model or MCP client")
    operation.add_argument("--self-check", action="store_true", help="Check owned cleanup and real adapter relay without a model; not CLI acceptance")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--stdio-proxy", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--fixture-db", help=argparse.SUPPRESS)
    parser.add_argument("--transcript", help=argparse.SUPPRESS)
    parser.add_argument("--caller", choices=["actual_cli", "sdk_self_check"], default="actual_cli", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.stdio_proxy:
        proxy(args.fixture_db, args.transcript, args.caller)
        return 0
    if os.environ.get("KAIBAIN_RUN_ID"):
        parser.error("Run outside a built-in goal run; inherited KAIBAIN_RUN_ID must not be cleared or bypassed")
    if args.fixture_human_review and not args.run:
        parser.error("--fixture-human-review requires --run; it operates only on the named isolated fixture")
    if not 10 <= args.timeout <= 600:
        parser.error("--timeout must be 10..600 seconds")
    folder = ROOT / ".local/client-acceptance" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:8])
    folder.mkdir(parents=True)
    if args.self_check:
        checks = self_check(folder)
        print(json.dumps(clean({"ok": all(checks.values()), "actual_cli": False, "checks": checks, "evidence": str(folder)}), indent=2))
        return 0 if all(checks.values()) else 1
    if args.fixture_only:
        with fixture() as (_, store, base):
            assert http(base, "/api/health")["ok"]
            goal = http(base, "/api/goals", {"title": "CLIENT ACCEPTANCE FIXTURE: setup"})["goal"]
            assert store.get(goal["id"])["status"] == "planned"
        save(folder / "fixture-only.json", {"verified": ["isolated Git", "SQLite", "loopback HTTP"], "actual_cli": False})
        print(json.dumps({"ok": True, "actual_cli": False, "evidence": str(folder)}))
        return 0
    clients = ["claude", "codex"] if args.client == "all" else [args.client]
    reports = []
    for client in clients:
        try:
            info = probe(client, folder)
            reports.append(acceptance(client, info, folder, args.timeout, args.fixture_human_review) if args.run else info)
        except CleanupError:
            report = {"client": client, "status": "interrupted", "gate": "owned_process_cleanup_unconfirmed"}
            save(folder / (client + "-result.json"), report)
            reports.append(report)
            break  # Do not start another client after uncertain cleanup.
    save(folder / "summary.json", {"opted_in": args.run, "results": reports})
    print(json.dumps(clean({"evidence": str(folder), "results": reports}), indent=2))
    return 0 if (not args.run and not any(r.get("status") == "interrupted" for r in reports)) or all(r.get("status") == "verified" for r in reports) else 2


if __name__ == "__main__":
    raise SystemExit(main())
