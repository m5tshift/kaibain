# Kaibain: instructions for repository agents

Kaibain is agents-native only. Built-in sandbox agents are primary; Claude Code
and Codex are additional MCP clients (Claude first among external clients).
Prefer macOS, then Windows. Application source belongs in `src/`; shared rules
belong in `src/kaibain/store.py`. Orchestration and microVM policies stay in the
separate sibling `multi-agent-system` repository.

## Working context and documentation

Read `docs/STATUS.md` at the start of work for current constraints, next tasks and
the latest verification evidence. `CONTRIBUTING.md` maps the code and checks.
Keep these files current when their facts change: STATUS for the current handoff,
ROADMAP for scope/completion, README for user-facing setup, and local git-ignored docs/evaluations
for reproducible evidence. ROADMAP_AUDIT contains historical snapshots; older
entries do not override newer evidence. Preserve failures and label scripted
checks separately from real inference. Do not turn plans into completed work.
Keep secrets, machine-local configuration and volatile PID/port state out of docs;
query live state instead. Use AGENTS.md as the shared repository instruction file.

## Setup and launch

When asked to launch/set up, do the work yourself. The request authorizes local
setup and server launch. Respect real client permissions and network restrictions.

1. Mac: `python3 run.py start`; Windows: `py run.py start` or the existing
   `.venv/Scripts/python.exe run.py start`. Use installed Python 3.11+.
2. `start` creates the venv if needed, installs requirements, merges project
   `.mcp.json`, writes `.local/codex.toml` and starts a hidden managed server.
   Wait for `ok: true`; a spawned process is not proof of readiness.
3. Check `run.py status` and GET the returned URL's `/api/health`. Open the URL
   when a browser surface is available. Keep the server running for the user.
4. For the built-in executor, read the sibling AGENTS.md and DEVELOPMENT.md,
   prepare its existing venv and Microsandbox, and use
   `run_development_web.py --connect-web`. Replacing another executor needs the
   explicit `--replace-runtime` option and preserves a backup. Never replace
   configuration merely for a demo. Never replace microVMs with containers or
   host execution. Do not silently enable hypervisors/elevate/reboot/download models.
5. External clients are optional. Claude uses the generated project `.mcp.json`.
   Only when asked to connect Codex, execute `codex_add_argv` as correctly quoted
   arguments and check `codex mcp list`. Preserve other MCP entries. Never enable
   bypass modes. If this session cannot reload tools, report the need for a new
   session/native trust prompt; never claim a connection without verification.
6. Return the working URL and any actual remaining client/runtime step. Do not
   request API keys for Kaibain. Ollama or the external client uses its own setup.

Use `run.py status`/`stop` for the managed instance. Do not kill unrelated
processes or a process solely for occupying a port. Retry `start --port 8766`.
Inspect `.local/server.log` on failure; never delete the database or recreate a
venv without inspecting it. Custom storage uses the same absolute `--db` in all
adapters. An instance with different settings must be stopped explicitly first.
Machine-local `.mcp.json`, `.local`, `.venv`, `data` must not be committed/copied
between operating systems.

## Native workflow and ownership

The user sets goals, starts/stops work, and accepts/returns results. Agents own
decomposition, tasks, subtasks and statuses. There is no manual/agent switch.
`get_workspace` returns `workflow: agents_native` and role-specific capabilities;
`mode: agent` is a legacy compatibility value, not an authorization toggle.
Do not restore manual mode, drag-and-drop, human task status editors or checkboxes.

Root tasks are goals. A user can create one and clarify its title/context/deadline/
project selection before work/decomposition starts. Clarifications go through
goal dialogue; feedback on delivered work goes through review. Agents can mark subtasks done but cannot complete a root goal: human
acceptance owns that decision. Do not accept results, launch user goals as demos,
or interpret acceptance as permission to apply exported files, merge or push.

Use MCP for requested task operations once connected. Read before editing and
pass `expected_version`. Reconcile conflicts instead of blindly retrying. Confirm
subtree deletion unless the request already authorizes it. Keep cycles prohibited
and direct-child progress independent of a parent's status.

For live visualization, use one stdio MCP process per agent. Register name/role/
model/framework once; send heartbeat every 5 seconds, including inference. Identity
is immutable and process-scoped. Coordinators assign themselves a goal first,
then assign workers in its subtree using fresh agent versions. Disconnect on exit;
presence expires after 30 seconds. Registration/action text does not prove sandboxing.

heartbeat_agent may include a structured phase and bounded budget (scope agent/run).
Optional shared resources are an all-or-none group: workers_limit/workers_active,
cpus_limit/cpus_reserved, memory_mb_limit/memory_mb_reserved. They describe reserved
execution resources, not the model server's host resources or sandbox attestation.
Do not infer sandbox proof or execution permissions from them. Plain heartbeats
preserve the phase and do not fabricate work. list_agents includes effective activity
and typed links. connect_agents records a dependency (source waits for target) or
handoff (source delivered to target); read both agent versions first. Coordinators
declare dependencies within their assigned subtree; senders may record their own
handoff. Cycles, cross-run links and out-of-scope links are rejected. A handoff closes
the receiver's matching dependency; links do not schedule work or accept a goal.
Task moves outside the coordinator's tree clear the worker assignment and old links.

Additional MCP clients call `submit_goal_result` with the root goal's latest
version, summary and checks. Checks are self-reported, not independently verified.
Only the user can accept/return the saved report. Read `get_task.goal_result` for
feedback. Changes in the goal or descendants invalidate an old review snapshot.

An assigned coordinator may call ask_question(task_id, expected_version, question).
get_goal_dialogue reads persistent questions, answers and requirements_version.
Only the user answers in the UI. On INPUT_REQUIRED stop task writes in that goal
tree; presence and reads remain available. Do not guess an answer. Built-in runs
park as waiting_for_input after cleanup; that old run context cannot write again.
The user's answer-and-continue action starts a new run with the original project
snapshot and saved dialogue. Mid-work questions seal a complete checkpoint before
ask_question; goal_questions.checkpoint_id binds it to the question. Continuation
verifies unchanged child/context and original fixed argv, imports saved writable
files into a new microVM and reuses the same worker task. Only the question and
answer may advance sealed root/requirements versions. Changed artifacts/context/
runtime refuse continuation without discarding the answer.
If continuation fails, the answer remains saved and
requires an explicit retry. Do not resume or stop a waiting run for the user.

## Built-in runs and isolation

`.local/agent-runtime.json` specifies a trusted fixed argv and `development-v1`
protocol. The browser supplies a bounded project profile, never host executables
or model settings. Profiles, versions and reports persist. Tests plus clean
microVM replay are mandatory for web coding runs; the text-only experiment is
not an accepted sandbox executor.

The Projects page stores user-owned profiles in SQLite. An optional full commit
pins the source; otherwise Git HEAD is resolved at each new launch. Saved-project
launches require project_id and expected_project_version. Read-only Git resolution
does not start models or prove sandbox readiness. Project edits/archiving never
rewrite run snapshots; revisions reuse the original snapshot, including its hash.
Do not change project configuration through agent task operations. Keep runtime
executables and model configuration in the trusted local adapter configuration.

The current adapter declares diagnostics: readiness-v1. Read-only diagnostics
are available through POST /api/agent-readiness: {} or a saved project/version.
They never start inference, create a VM or register agents. New launches and
revisions repeat checks before reserving a run; on RUNTIME_NOT_READY report the
component failures instead of bypassing diagnostics. Do not pass endpoint/model
or executable settings from the browser. A passed probe is not proof that a
project run passed tests or that Mac/PC networking was actually exercised.

One run per database at a time. Runs/logs/artifacts live in `.agent-runs/<id>/`
beside that database. The user alone starts, stops, accepts or requests revisions.
Reviews check saved run/task versions without a mode switch. Revisions preserve
the worker subtask, feedback, original profile and immutable earlier artifacts.

Only the user requests `/api/agent-runs/<id>/stop`. A run-scoped MCP process
inherits `KAIBAIN_RUN_ID`; preserve it. On RUN_STOPPED stop work and disconnect;
never remove context or change transports to bypass the fence. The supervisor
owns its child scope and invokes the external adapter's owned-resource cleanup.
Never stop Ollama/unrelated processes as cancellation. Cleanup failure is
interrupted, not successful cancellation. Controller/model calls are outside
the VM; project code and tools are inside it. No automatic code merge/acceptance.

Checkpoint recovery is a human control: GET /api/agent-runs/<id>/recovery previews
the latest sealed export; POST /api/agent-runs/<id>/resume requires run/task versions
and checkpoint_id. Never resume a user's goal yourself or replay unknown actions.
Only a registered coordinator of the active built-in run may publish_checkpoint
after a complete export. Receipts in run_checkpoints are immutable and bind files
to the task/child/requirements versions. Partial/unsealed directories are not usable.
Recovery preserves the original profile, argv, same worker task and earlier artifacts;
the new attempt gets fresh instances of the original budgets with separate accounting.
It requires owned cleanup of the prior chain, fresh readiness and fresh microVM tests
plus clean replay. Changed context/artifacts/runtime or failed cleanup stops recovery;
scope.json is written before releasing the adapter gate and after owned tree cleanup.
Recovery only inspects old scope liveness; it never signals an old/unrelated PID/group.
no silent fallback to older snapshots. Existing official adapters can add capability
via --enable-recovery; this backs up configuration and preserves argv/models/endpoint.

## Этап 7: валидатор и финальная передача

Опциональный третий read-only валидатор: `--validator-model gemma3:4b`, только
после tests и clean microVM replay. Family/digest проверять по Ollama `/api/tags`
и `/api/show`: Qwen3/Qwen3.5 → `qwen`, Gemma3 → `gemma`. Другой тег Qwen
не даёт независимости. Диагностика при optional validator читает tags + show;
это metadata без инференса, VM или регистрации MCP.

Замечания должны иметь точный путь экспорта, строку и дословное evidence на ней;
хеши связывают отчёт с экспортом и clean checks. Совпадение цитаты не доказывает
верность замечания. Отчёты сохраняются в SQLite, review UI и графе. Валидный отчёт
и модельное `accept` никогда не заменяют человеческую приёмку и отдельный Apply.

`atomic-review-v1`: supervisor после owned cleanup одной транзакцией Store/SQLite
публикует completed-отчёт и переводит root/child в review. Worker не выставляет
преждевременные финальные статусы задач. Stop/crash до commit оставляют версии
задач sealed checkpoint неизменными; не обходить этот протокол прямыми MCP-записями.

В рабочей конфигурации validator **OFF** из-за качества модели. Наличие команды
не означает включения. Только по запросу настройки из sibling можно выполнить:

```sh
# macOS, из multi-agent-system
.venv/bin/python run_development_web.py --enable-validator --validator-model gemma3:4b
```

```powershell
# Windows, из multi-agent-system
.venv/Scripts/python.exe run_development_web.py --enable-validator --validator-model gemma3:4b
```

Команда точечно дополняет официальный существующий argv, создаёт backup и сохраняет
coordinator/developer, endpoint и recovery; не заменять чужой адаптер/валидатор.
Она не запускает цель. Не выполнять настройку или model run ради правки документации.

Этап 7 открыт: малая curated-выборка, calibration-v5 имеет false positive;
новый повтор 4 ноября с Gemma3 прошёл 4 samples. Исходный qwen3:8b comparison
дал 1/5 в обоих режимах; после workflow/schema/chat-pairs V3 та же малая выборка
прошла 5/5 в обоих режимах. Предыдущие failures сохранены. Свежая реальная
native readiness-задача пока остановилась на context_budget без исправления.
Не превращать малый успех в production readiness; receipts — в docs/STATUS.md.
suite-v3 завершён со сбоями сокетов Windows и исключён из сравнения качества.
Calibration-v4 с подсказками под suite — только development artifact, исключить
из сравнения; ранние invalid/HTTP 400/ungrounded не скрывать. См.
[EVALUATION.md](../multi-agent-system/EVALUATION.md). Scripted HTTP отделять от
real Ollama; Windows не подтверждает Mac/Mac → PC или реальные Claude/Codex.
Диапазоны/поиск/replace_text и backup API проверены на Windows. Помимо pure-Python
v1 есть opt-in native bundle v2: pinned CPython/ABI/Linux/glibc, проверенные ELF,
hash-pinned closure; x86_64 VM проверены, aarch64 только статически. Полный
application suite прошёл baseline/clean replay с pinned Git внутри x86_64 гостя
(137 проверок, 2 ожидаемых skip в каждом). Не заменять его host Git.
Экспериментальный --parallel-workers подключает DAG к web/MCP; Windows scripted
VM проверки покрывают stop, вопрос/ответ, ранний и поздний checkpoint recovery
и доработки с теми же подзадачами. Статусы и checkpoint публикуются атомарно.
Real Ollama web-путь двух worker прошёл отдельный ограниченный fixture-прогон;
большой native-проект и общая платформенная приёмка остаются открытыми.
Рабочий argv последовательный; не переключать его ради демо. Scheduler этапа 9
не завершён. Не переключать runtime ради демо. CLI restore — только
в новое место, без перезаписи, config/ключей, автозапуска, включения адаптера или Apply.

The result review page is /reviews/<run-id>. Human acceptance and publication are
separate actions. Only the user's explicit Apply action may POST /api/agent-runs/
<id>/apply with expected_version and the fresh preview_token. It creates a local
kaibain/<run-id> branch/commit from the frozen source using a private index; HEAD,
the working tree and the user's index stay untouched. Never interpret acceptance
as authorization to call Apply. No MCP apply tool, checkout, merge, push, project
execution, hooks or filters. Ref publication uses create-only CAS; existing foreign
refs, modified affected files/index, moved HEAD and changed artifacts are conflicts.
SQLite receipts make retries idempotent; a matching ref after a DB interruption is
reconciled only on a human retry. Do not automatically recreate deleted branches.

## Checks

Use isolated databases/repositories; do not add demo records to user data.
Fixtures may exercise full launch/review/revision/stop, including human controls.
Mac: `.venv/bin/python -m unittest discover -s tests -v`; Windows:
`.venv/Scripts/python.exe -m unittest discover -s tests -v`.
`tests/browser_check.py` checks native UI and real external MCP.
`tests/browser_result_review_check.py` checks review/publication with real Git and
isolated completed-run fixtures; it does not claim actual model or VM execution.
`tests/browser_agent_graph_check.py` checks real stdio MCP phases, budgets, links,
questions, expiry, restart persistence, keyboard inspection, dark/mobile UI.
`tests/browser_recovery_check.py` uses isolated receipts and an unverified adapter
fixture to check human controls. `tests/browser_recovery_development_check.py` uses
real microVMs/MCP and scripted model HTTP to check failure, restart and recovery.
The optional `tests/browser_development_check.py` checks real microVMs with scripted model HTTP
responses; `--real-ollama` requests actual inference. See README for dependencies.

`tests/supervisor_interrupt_check.py --run-real-vm` injects KeyboardInterrupt or
SystemExit into an owned supervisor with a real microVM and a trusted adapter
fixture. It proves cleanup/terminal state and outsider survival, not model/MCP
acceptance. Requires a current prepared worker receipt; `--prepared-state` selects
an existing image receipt without copying VM/owner state or pulling images.

`tests/browser_recovery_development_check.py --kill-supervisor` is Windows-only
real process termination after a sealed write, with scripted HTTP and real MCP/VM.
It verifies a live interpreter's parent/creation order before signalling its handle,
never kills a PID merely from SQLite/receipt, and checks explicit recovery plus
owned cleanup before a new supervisor. It is not a power-loss or macOS test.

`tests/browser_export_crash_check.py --crash-point first-export` (or
`after-write-export`, `cleanup`) requires an existing current `--prepared-state`.
Windows-only trusted fault injection, real web/MCP/VM, scripted HTTP; no prepare/pull.
It verifies unsealed export refusal and explicit recovery/cleanup boundaries.
Without a published checkpoint there is no Resume: cleanup is explicit fixture
teardown in the historical October evidence, not recovery of unknown work.
The updated first-export fixture also verifies the separate human cleanup button.

`tests/browser_operator_cleanup_crash_check.py --prepared-state ABSOLUTE_PATH`
checks Windows HTTP-owner loss during cleanup with a separately held real VM,
durable fencing and explicit UI retry. Its initial terminal run is a trusted
fixture, not coding/MCP/model evidence. No prepare/pull or production reconfiguration.

`tests/browser_saved_project_restart_check.py --prepared-state ABSOLUTE_PATH`
checks the saved-profile gate after an actual HTTP process restart: a new goal
launches by project/version, resolves fresh HEAD and completes real MCP/VM checks
and clean replay. Model HTTP is scripted; no user acceptance/Apply or model proof.

Terminal failed/interrupted/cancelled coding runs expose POST /api/agent-runs/<id>/cleanup
with expected_version; GET reports saved state. Only an explicit human action may
request it outside isolated fixtures. It uses the frozen original argv, never
imports files, starts inference, resumes work or signals old PID/groups. A durable
SQLite lease blocks launches/recovery/other cleanup; failure or dead owner retains
the fence until a successful explicit retry. Inspect old and prior-cleanup scopes
without signalling them; the new helper has its own gate/receipt. Preserve tasks,
run versions and exported artifacts. Require a fresh owned cleanup acknowledgement,
not merely zero exit or an older ok. Backup refuses active cleanup. Never add an
MCP cleanup tool or interpret goal acceptance as permission for this control.

`tests/browser_parallel_budget_check.py --budget tokens` (or `time`) requires
an existing current --prepared-state. It uses scripted HTTP/metrics with real
web/MCP/VM and explicit fixture recovery, never real inference. Shared-budget
cancellation is a failure, not a human stop. Await every owned VM stop and retain
its exception/report before releasing a worker lease. Normal stdio EOF retires
only that process's registered identity; forced kills still rely on expiry.
