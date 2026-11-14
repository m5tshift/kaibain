# Приёмка внешних клиентов: Claude Code, затем Codex

`tests/client_acceptance.py` проверяет настоящие установленные CLI. Built-in agents
остаются основным исполнителем Kaibain; среди внешних клиентов первым проверяется
Claude Code, вторым — Codex. SDK, HTTP-фикстуры и успешный запуск процесса сами по
себе не подтверждают приёмку CLI.

## Наблюдения на Windows, 24 октября 2026

| Проверка | Результат |
| --- | --- |
| Claude Code | Не обнаружен в PATH и стандартных местах launcher. Это не доказательство отсутствия установки во всей системе. Флаги, login, trust и workflow не проверены. |
| Codex CLI | Обнаружен `0.164.0`; `login status` подтвердил существующий ChatGPT login. Переменные API key отсутствовали. |
| Реальный Codex MCP | CLI дошёл до inference и попытался вызвать `get_workspace` и `get_task`; оба вызова остановлены native approval до adapter. Успешных MCP-вызовов — 0. |
| Native gate | `MCP tool call requires approval, but approval policy is never`. Exit code CLI был 0; harness оставил результат `pending`. |
| Изоляция фикстуры | Проверены отдельные Git, SQLite и loopback HTTP. Пользовательские цели не запускались. |
| Cleanup и relay | Проверены normal exit, timeout, остановка потомков, сохранность постороннего процесса, закрытый gate при ошибке Job assignment, фильтрация MCP-инструментов и отсутствие записи после отказа. |

Локальные evidence (не включать в Git и не переносить между ОС):

- `.local/client-acceptance/20261024T103731Z-76edebb4/`: probe установленных CLI.
- `.local/client-acceptance/20261024T103747Z-be900ed0/`: fixture-only.
- `.local/client-acceptance/20261024T104010Z-13aa778b/`: реальная попытка Codex;
  `diagnostic/codex-result.json` — повторная диагностика,
  `diagnostic/observed-native-gate.json` — зафиксированная причина отказа. Ранний
  отчёт сохраняет общий код gate; addendum добавлен после наблюдения structured
  `item.error.message` в терминале, без нового model run и перезаписи старого отчёта.
- `.local/client-acceptance/20261024T104557Z-c7b6ff8a/`: финальные 13 model-free
  checks, включая отдельную атрибуцию SDK events.
- `.local/client-acceptance/final-audit-20261024T104710Z/`: повторный probe без
  inference; config-хеши до/после совпали, временных fixture directories не осталось,
  cleanup всех CLI probe подтверждён. Это наблюдение относится к финальному probe,
  а не ретроспективная проверка config во время прежних model attempts.

Ранняя попытка в `20261024T103902Z-9eed5584` остановилась ещё при загрузке config:
установленная сборка со strict parsing отвергла документированный ключ
`tools.view_image`. Этот необязательный override удалён; strict parsing,
read-only sandbox, отключённые command tools и native approvals сохранены.
Ошибочные попытки не засчитаны как успешная приёмка и не удалены.

Полная приёмка обоих клиентов остаётся открытой. Для Claude нужен обнаруживаемый
native executable и его обычный login/trust flow. Для Codex требуется штатное
интерактивное разрешение MCP; headless `exec` в наблюдавшейся конфигурации не может
показать такой prompt. Harness не меняет approval policy, не добавляет auto-allow
и не записывает trust. Авторизация сама по себе не снимает этот gate. Trust,
model access и завершённый workflow проверяются отдельно для каждого клиента.

## Запуск

### Повторный probe без инференса, 4 ноября 2026

`.local/client-acceptance/20261103T224558Z-9cafca92/summary.json` и
`.local/client-probe-current.log` содержат новую read-only проверку:

- Claude снова не обнаружен в PATH/предусмотренных launcher locations. Поиск
  ограничен этими местами, не доказывает отсутствие любой установки.
- Обнаружен Codex **0.166.0**, обязательные flags в `exec --help` поддержаны,
  `login status` подтверждает subscription login. Owned cleanup probe-процессов
  подтверждён. Это не проверка model access, MCP calls или native trust.
- Без `--run`: ни инференса, ни MCP workflow, ни изменения client/server config.
  Native gate сборки 0.166.0 не испытывался. Исторический отказ выше относится
  к 0.164.0; его нельзя автоматически переносить на новую сборку или считать снятым.

На момент этого probe действовала пауза реальных LLM. Позднее 4 ноября пользователь
разрешил ограниченные реальные проверки; их результат описан отдельно ниже.
[Манифест свежего probe](evaluations/2026-11-04-client-probe.json) сохраняет хеши.

### Реальная попытка 4 ноября после разрешения пользователя

`.local/client-acceptance/20261103T230828Z-84e555ab/`: Claude не обнаружен;
Codex 0.166.0 действительно запустил inference и попытался вызвать `get_workspace`
и `get_task`. Оба вызова отказали на native trust/approval до adapter.
Успешных вызовов 0; CLI exit 0 не принят как доказательство, harness exit 2/pending.
Owned process cleanup подтверждён, конфигурационные хеши до/после совпали.
Повторно обходить gate или менять approval/trust настройки не выполнялось.
Нужен штатный пользовательский native trust flow; Claude требует доступной установки.

### Команды

Из корня репозитория, с уже подготовленным venv:

```powershell
.venv/Scripts/python.exe tests/client_acceptance.py
.venv/Scripts/python.exe tests/client_acceptance.py --fixture-only
.venv/Scripts/python.exe tests/client_acceptance.py --self-check
```

На macOS использовать `.venv/bin/python`. Запуск без флагов — только version/help/
auth probe, без inference. `--self-check` использует SDK для проверки настоящего
stdio adapter и безопасности relay; события имеют source `sdk_self_check`, и эта
проверка не считается CLI acceptance. Временные фикстуры создаются и удаляются
в `.local/client-acceptance/`, а отдельная папка evidence сохраняется.

Только после явного разрешения на inference:

```powershell
.venv/Scripts/python.exe tests/client_acceptance.py --run --timeout 300
.venv/Scripts/python.exe tests/client_acceptance.py --client claude --run
.venv/Scripts/python.exe tests/client_acceptance.py --client codex --run
```

`--run` делает одну ограниченную попытку на доступный клиент, Claude первым.
Существующий subscription login обязателен; API-key/неизвестный auth mode,
отсутствующие флаги и Windows shell-shim вместо `.exe` оставляют проверку pending.
Harness ничего не устанавливает, не выполняет login/logout и не запрашивает ключи.
На незакрытом gate не следует повторять inference без изменения условий через
нативный пользовательский flow. Код возврата harness: 0 — probe/setup/self-check
успешен либо все запрошенные CLI workflow verified; 2 — CLI pending/interrupted.

`--fixture-human-review` допустим только с `--run` и только после полного verified
workflow. Он проверяет HTTP human control на собственной именованной фикстуре;
в этой сессии не использовался. Это не действие клиента, не нажатие пользователем
кнопки и не разрешение принимать реальные цели или выполнять Apply.

## Ограничения и критерии

Каждый CLI получает собственную SQLite-фикстуру, новый Git repository и один
stdio MCP relay, который запускает реальный `run.py mcp --db <absolute fixture>`.
Relay принимает только помеченную БД под `.local/client-acceptance/`, фильтрует
`tools/list` и отклоняет вызовы вне allow-list. MCP global add/remove не вызывается.
`KAIBAIN_RUN_ID` не снимается: запуск harness/proxy из built-in run отвергается.

Codex использует per-invocation `--ignore-user-config`, `--ephemeral`,
`--strict-config`, `--sandbox read-only` и свой MCP allow-list. Отключены command
tools, subagents, apps, plugins, hooks, shell snapshot и web search. `CODEX_HOME`
сохраняется как есть для существующей авторизации. Claude получает
`--strict-mcp-config`, отдельный config, `--tools ""`, пустой `--setting-sources`,
отключённые hooks и `--no-session-persistence`; эта ветка пока не проверена на
реальной установленной версии. Ни один клиент не получает preapproval или bypass.
Нативные managed restrictions продолжают применяться. Это ограничения вызова,
а не доказательство microVM или полной изоляции доступа к файлам.

Команды запускаются через закрытый stdin gate: Windows Job Object назначается
до запуска CLI, POSIX создаёт новую session/process group. Owned tree очищается
при обычном выходе, timeout и исключении; Windows ожидает нулевое число активных
процессов job. Поиск/остановка процессов по порту или старым PID не используется.
При неподтверждённом cleanup следующий клиент не запускается. Cleanup на macOS
в этой проверке не выполнялся.

`verified` требует успешных вызовов самого CLI: agents_native workspace, одна
регистрация, назначение coordinator с версией из свежего `list_agents`, собственный
heartbeat, две вложенные подзадачи, fresh read/write/read с `expected_version`,
сохранённый и перечитанный goal result, root в review/pending и disconnect.
Ход workflow дополнительно сверяется с БД. Успешный exit code без этих фактов
не даёт `verified`.

Plain heartbeat relay каждые 5 секунд имеет source `harness_timer`; он сохраняет
присутствие того же MCP process во время inference и не считается вызовом CLI.
`native_five_second_heartbeat` всегда явно остаётся `not_verified`: один heartbeat
агента и таймер harness не доказывают native cadence. Бюджеты, связи нескольких
агентов, UI, macOS/Mac → PC, tests/clean microVM replay и публикация этими проверками
не подтверждены.

Evidence содержит очищенные synthetic MCP calls, snapshots, argv, event/tool names
и нормализованные gates. Raw model output, auth status text, account identifiers
и credentials не сохраняются. Для новых live attempts пишутся только хеши config
до/после и наблюдение о совпадении; параллельное изменение другим процессом нельзя
приписывать harness. Пользовательские БД для такого наблюдения не читаются.

Флаги Codex сверены с установленным `exec --help` и
[официальной документацией OpenAI](https://learn.chatgpt.com/docs/developer-commands?surface=cli).
См. также [MCP allow-list](https://learn.chatgpt.com/docs/extend/mcp?surface=cli),
[config reference](https://learn.chatgpt.com/docs/config-file/config-reference)
и [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference).
Поддержку конкретной сборки определяет её фактический probe/strict parsing.
