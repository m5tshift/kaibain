# Сверка ROADMAP с кодом и свидетельствами

Срез: 4 ноября 2026 года. Основание — [ROADMAP.md](../ROADMAP.md),
[AGENTS.md](../AGENTS.md), [STATUS.md](STATUS.md) и
[карта кода и проверок](../CONTRIBUTING.md). Прочитан текущий код Kaibain и
соответствующие части соседнего runtime. HEAD Kaibain при сверке:
`91c7d33fff64cbd4347bb2917adeac019e5af04f`.

Документарная сверка дополнена адресными сквозными проверками двух найденных
границ на изолированных данных. Пользовательские цели, конфигурация и production
код не менялись. Новые fixtures/evidence учтены поверх указанного HEAD.

Дополнение после разрешения LLM 4 ноября: [реальный новый срез](evaluations/2026-11-04-real-review.md)
содержит Gemma3 calibration 4/4, Gemma4:e4b 3/4 (invalid на исправном коде),
qwen3:8b comparison 1/5 planned successes в обоих режимах и отдельный малый
qwen3.5:9b pilot (1/1 без review, 0/1 с review из-за developer no-progress).
Все 32 VM отсутствуют. Новая permission отменяет прежнюю LLM-паузу; Mac недоступен.
Runtime evaluation получил явный prepared-state reuse (358 unit, 315 pass/43 skip).
Исходные механизмы приложения и рабочая runtime configuration не менялись;
качество разработки и полная приёмка этапов 7–9 остаются открытыми.

Последующий [coding-loop V3](evaluations/2026-11-04-workflow-feedback.md) завершил
ту же малую выборку 5/5 в обоих режимах, все 30 VM отсутствуют. Runtime 370/43 skip;
актуальная scripted web/MCP integration также прошла. Предыдущие failures не скрыты.
Это обновляет measured comparison, но не закрывает human/platform/real-project gates.

**Статусы:** «выполнено» означает подтверждение конкретного критерия в указанной
границе; «частично» — механизм или часть сценария подтверждены, исходный gate
целиком остаётся открытым; «не проверено» — положительного свидетельства нужного
сценария нет. Наличие теста в коде само по себе не означает его успешный запуск.
Windows scripted HTTP с настоящей microVM подтверждает интеграцию, но не качество
LLM, macOS, Mac → PC или работу настоящего внешнего клиента.

HTTP-owner crash во время operator cleanup подтверждает HTTP/VM/UI retry
на trusted terminal-run fixture, без утверждений о coding/MCP/LLM execution.
Найденный пробел этапа 1 закрыт отдельно: положительный saved-project launch
после завершения и нового запуска HTTP-интерпретатора; настоящий MCP/VM цикл,
scripted HTTP, без реальных LLM и пользовательской приёмки.

## Свежесть и происхождение evidence

| Источник | Что действительно подтверждает | Как используется |
|---|---|---|
| [Saved-project restart, 04.11](evaluations/2026-11-04-saved-project-restart.md), [JSON](evaluations/2026-11-04-saved-project-restart.json) | Windows; профиль создан через UI, HTTP процесс завершён и запущен заново, новая цель запускается по project/version без повторного ввода; настоящий MCP/VM, tests/clean replay | Закрывает выявленный прямой gate этапа 1. Новый HEAD разрешается при запуске, saved profile неизменен, обе VM отсутствуют. Не LLM/Mac proof |
| [Cleanup owner crash, 04.11](evaluations/2026-11-04-cleanup-owner-crash.md), [JSON](evaluations/2026-11-04-cleanup-owner-crash.json) | Windows/x86_64; настоящий HTTP owner kill/Job helper exit, живая SDK VM, durable interrupted fence после restart и явный UI retry до SDK absence | Закрывает именно operator-cleanup owner-loss границу. Terminal-run — trusted fixture, не coding/MCP/LLM proof. Все 12 `sources` совпали по SHA-256; production fixes не потребовались |
| [Web budgets, 04.11, отчёт](evaluations/2026-11-04-web-budgets.md), [JSON](evaluations/2026-11-04-web-budgets.json) | Windows/x86_64; настоящий HTTP/MCP/supervisor/VM, scripted ответы и token metrics; tokens/time → failure → restart → явное recovery → tests/clean replay/oracle | Последнее завершённое сквозное evidence общих бюджетов. Все 7 `sources` Kaibain и 8 `runtime_sources` совпали по SHA-256 с прочитанными файлами |
| [Operator cleanup, 03.11](evaluations/2026-11-03-operator-cleanup.md), [JSON](evaluations/2026-11-03-operator-cleanup.json) | Windows; 24 fixed-adapter unit, настоящий first-export supervisor crash → UI cleanup одной VM, restart, dark/mobile/keyboard | Все 11 записанных `sources` совпали по SHA-256. Отчёт тогда ещё не проверял обрыв HTTP-владельца; новая проверка выше дополняет его, не переписывая историю |
| [Наборы web budgets 04.11](evaluations/2026-11-04-web-budgets.json) | Kaibain: 200, 198 pass / 2 ожидаемых skip; runtime тогда: 354, 312 pass / 42 opt-in skip; exit 0 | Более новый runtime после prepared-state: [358, 315 pass / 43 skip](evaluations/2026-11-04-real-review.md), child exit 0. Приложение не менялось. Opt-in skip не равны исполненным VM-тестам; это не финальный LLM eval |
| [Четыре области](evaluations/2026-10-25-four-worker-dag.md), [fan-in/budgets](evaluations/2026-10-25-fanin-budgets.md), [scheduler](evaluations/2026-10-25-parallel-scheduler.md) | Windows, реальные VM; scripted решения/HTTP, различные отдельные границы scheduler | Более поздний fan-in и web budgets закрывают названные открытыми границы раннего scheduler-среза. Старые failures сохраняются |
| [Real-model suites/calibration, 23.10](evaluations/2026-10-23.json), [runtime EVALUATION](../../multi-agent-system/EVALUATION.md) | Реальная Ollama и microVM на Windows; малая curated-выборка, включая неуспехи | Историческая модельная оценка. После разрешения пользователя 4 ноября [новая calibration](evaluations/2026-11-04-real-review.md): Gemma3 4/4 valid, Gemma4:e4b 3/4. Это не Mac или общая приёмка |
| [Малые реальные coding/DAG, 24.10](evaluations/2026-10-24.json), [native coding](evaluations/2026-10-24-native-coding.md), [последующие попытки](evaluations/2026-10-25-decision-notes.md) | Реальные модели в явно описанных малых задачах; большой native-проект без готового модельного исправления | Успех малой задачи не переносится на большую. Подробные промежуточные результаты остаются историческими |
| [ROADMAP_AUDIT](../ROADMAP_AUDIT.md) | Исторические срезы, конкретные локальные receipts, включая native/Git и real web DAG | Используется с учётом даты раздела. Формулировка «актуальный» внутри старого раздела и старые количества записей/тестов не являются текущим состоянием |

SHA-256 сверены только для перечисленных исходников; это не повтор всех старых
проверок и не аттестация всего дерева. Локальные receipts и журналы не копировались.
Ссылки ниже ведут на код, тесты и сохранённые отчёты, где указаны их пути и хеши.

Свежий [probe внешних CLI без инференса](CLIENT_ACCEPTANCE.md#повторный-probe-без-инференса-4-ноября-2026)
4 ноября: Claude не обнаружен, Codex 0.166.0 поддерживает нужные flags и имеет
subscription login. Новые MCP/model/native trust проверки не выполнялись; старый
approval failure относится к 0.164.0. Gate полной клиентской приёмки остаётся открыт.

Позднее после разрешения выполнена [реальная попытка 0.166.0](CLIENT_ACCEPTANCE.md#реальная-попытка-4-ноября-после-разрешения-пользователя):
два MCP-вызова отказали на native trust/approval, успешных 0. Harness pending/2,
config unchanged, owned cleanup подтверждён. Gate повторно подтверждён уже на новой
сборке; обход или изменение permissions не выполнялись.

## Общая сводка этапов

| Этап | Состояние исходного gate | Подтверждённая граница / остаток |
|---|---|---|
| 1. Сохранённые проекты | **Выполнено на Windows scripted** | Прямой сценарий сохранения профиля → restart HTTP-процесса → новая цель → успешный MCP/VM запуск без повторного ввода теперь подтверждён |
| 2. Диагностика | **Частично** | Windows API/UI и fail-before-reserve выполнены; реальная схема Mac + PC не проверена |
| 3. Диалог | **Выполнено на Windows scripted** | Вопрос после правок, checkpoint, restart, ответ и продолжение той же цели/подзадач подтверждены; общая Mac-приёмка отдельно открыта |
| 4. Приёмка и Apply | **Выполнено на Windows** | Fixture review и отдельная новая Git-ветка с сохранением HEAD/index/worktree; это не приёмка пользовательских результатов или доказательство LLM качества |
| 5. Граф | **Выполнено на Windows** | Реальные stdio MCP события, фазы, связи, бюджеты и UI; сообщения внешних агентов самодекларируемы |
| 6. Восстановление | **Выполнено в проверенных Windows границах; частично в целом** | Включены recovery и новая HTTP-owner crash/VM/UI retry проверка operator cleanup; Mac/POSIX и общая платформенная приёмка открыты |
| 7. Качество и независимое ревью | **Частично** | Механизм выполнен, исторические реальные suites есть; чистое итоговое сравнение и приемлемое качество ревью не получены |
| 8. Более крупные проекты | **Частично** | Контекст, pinned dependencies/native/Git, Windows replay выполнены; успешная автономная большая native-задача не получена, Mac/aarch64 не проверены |
| 9. Специализации и параллельность | **Частично в целом** | Исходный инженерный критерий выполнен на Windows scripted в проверенных сценариях; итоговая платформенная/модельная приёмка открыта. Scoped-команды и переключение рабочего argv не добавлены в gates |

## 1. Сохранённые проекты и простой запуск

Исходный gate: после перезапуска приложения запустить новую цель с сохранённым
профилем без повторного ввода путей, хеша и JSON-команды тестов
([ROADMAP, этап 1](../ROADMAP.md#следующие-этапы)).

| Существенный критерий | Статус | Код и конкретное подтверждение / граница |
|---|---|---|
| Репозиторий, файлы, writable, checks, acceptance сохраняются в SQLite; проекты редактируются, архивируются и выбираются на цели | Выполнено | [Store: `create_project/update_project/resolve_project`](../src/kaibain/store.py#L253), [UI проектов](../src/static/projects.js); [test_projects](../tests/test_projects.py#L62): повторное открытие Store, связь цели, user-owned configuration, archive/conflict; [browser_projects_check](../tests/browser_projects_check.py#L25): CRUD, выбор, reload, stale guard. Историческое подтверждение browser-проверки — [аудит, строка этапа 1](../ROADMAP_AUDIT.md#проверка-выполнения-roadmap) |
| HEAD разрешается при каждом новом запуске либо используется полный pinned commit; run/revision сохраняют неизменяемый профиль и его версию | Выполнено | [git_project](../src/kaibain/git_project.py), [Store](../src/kaibain/store.py#L308), [runtime.start](../src/kaibain/agent_runtime.py#L313); [test_projects](../tests/test_projects.py#L90): HEAD меняется между запусками, fixed commit неизменен, race/archive запрещают stale launch, revision сохраняет исходные настройки |
| Модели и host argv отделены от браузерного профиля | Выполнено | [validate_project](../src/kaibain/store.py#L320) принимает только `repo/commit/files/writable/checks/acceptance`, checks начинаются с `python`; [runtime.config](../src/kaibain/agent_runtime.py#L55) читает доверенный fixed argv. Нет отдельных per-project моделей — явно заявленная граница ROADMAP, а не новый незакрытый gate |
| Успешный запуск после рестарта без повторного ввода | Выполнено на Windows scripted + real MCP/VM | Ранее [test_projects](../tests/test_projects.py#L149) и [dormant UI fixture](../tests/browser_projects_check.py#L25) подтверждали только отдельные части. [Новая сквозная проверка](../tests/browser_saved_project_restart_check.py), [evidence](evaluations/2026-11-04-saved-project-restart.md) прямо проверяют UI save, настоящий restart HTTP-интерпретатора, новую цель, POST с project/version без profile JSON, новый HEAD, completed run/tests/clean VM replay. Saved profile, source HEAD/index/worktree неизменны; оба agents disconnected, обе VM отсутствуют. Это не реальная модельная или Mac-приёмка |

Исходный сценарий запуска после рестарта подтверждён в Windows scripted границе.
Новые поля профиля или отдельные per-project модели для его закрытия не требуются.

## 2. Диагностика готовности

Исходный gate: UI различает подключённый адаптер и готового исполнителя;
неудачная проверка не изменяет цель и не начинает работу.

| Существенный критерий | Статус | Код и конкретное подтверждение / граница |
|---|---|---|
| Ollama, выбранные модели, sandbox/гипервизор, Git и import policy; понятная причина и подсказка | Выполнено на Windows | [AgentRuntime.readiness](../src/kaibain/agent_runtime.py#L100), [bounded probe](../src/kaibain/readiness.py), [runtime readiness](../../multi-agent-system/src/readiness.py): tags, `msb doctor` без fix, files; [test_readiness](../tests/test_readiness.py#L56), [browser_readiness_check](../tests/browser_readiness_check.py) проверяют unavailable/missing/incomplete/timeout и UI; исторический итог механизма — [аудит этапа 2](../ROADMAP_AUDIT.md#проверка-выполнения-roadmap) |
| Диагностика без inference, создания VM/MCP и task writes; fresh recheck перед launch/revision до reserve | Выполнено | [probe](../src/kaibain/readiness.py#L18), [start](../src/kaibain/agent_runtime.py#L313); `test_read_only_probe_does_not_start_agents_and_strips_control_context`, `test_start_rechecks_environment_even_after_cached_positive_result` в [test_readiness](../tests/test_readiness.py#L56). Для configured native/toolchain/validator требуются соответствующие диагностические checks; пропущенный отчёт fail-closed |
| Статус подключения отдельно от реальной readiness | Выполнено | [runtime.snapshot/readiness](../src/kaibain/agent_runtime.py#L83), [UI readiness](../src/static/readiness.js), [browser_readiness_check](../tests/browser_readiness_check.py). Наличие конфигурации и positive metadata probe не доказывают успешный project run |
| Явный доверенный LAN endpoint для схемы Mac sandbox → PC Ollama | Частично | [runtime `local_endpoint`](../../multi-agent-system/src/development_model.py), [models_probe](../../multi-agent-system/src/readiness.py#L23); настройка остаётся в trusted argv. Поддержка кодом есть; [STATUS, платформенные gates](STATUS.md#открыто-и-требует-отдельного-окружения-или-снятия-паузы) не содержит успешного реального Mac → PC прогона |

Осталась фактическая приёмка Mac + PC. Metadata готовность не подменяет её.

## 3. Общение с координатором внутри цели

Исходный gate: запросить уточнение во время работы и продолжить после ответа
без новой цели и потери истории.

| Существенный критерий | Статус | Код и конкретное подтверждение / граница |
|---|---|---|
| Назначенный координатор задаёт MCP-вопрос; история/ответы SQLite, «Нужен ответ», requirements_version | Выполнено | [Store dialogue/ask/answer](../src/kaibain/store.py#L896), [MCP](../src/kaibain/mcp_server.py), [UI](../src/static/dialogue.js); [test_dialogue](../tests/test_dialogue.py#L56) проверяет restart, права координатора, версии, актуальность приёмки и лимиты |
| Ожидание запрещает записи в goal tree, освобождает run slot/процессы; preflight question без VM | Выполнено в проверенных Windows сценариях | [Store](../src/kaibain/store.py#L882), [development_web](../../multi-agent-system/src/development_web.py), [test_dialogue](../tests/test_dialogue.py#L81), [scripted web evidence](evaluations/2026-10-25-supervisor-interrupt.md#проверки-и-запуск). Старый run-context не возобновляет записи после ответа |
| После правок сначала полный sealed checkpoint и cleanup; ответ продолжает ту же цель/worker/profile, файлы не пишутся повторно | Выполнено на Windows scripted + real VM | [continue_run](../src/kaibain/agent_runtime.py#L177), [test_work_dialogue](../tests/test_work_dialogue.py#L31); [question/publication crash](evaluations/2026-10-25-question-publication-crash.md#сквозной-windows-прогон), [binary DAG question](evaluations/2026-10-25-parallel-scheduler.md#проверки-на-windows), [native/Git evidence](../ROADMAP_AUDIT.md#актуальный-срез-периодические-снимки-nativegit-и-реальная-ollama). Это не real-model диалоговая приёмка |
| Ответ сохраняется при readiness failure; явный retry, stale/tampered context и Stop не обходятся | Выполнено | [runtime.answer](../src/kaibain/agent_runtime.py#L193), [test_dialogue](../tests/test_dialogue.py#L112), [test_work_dialogue](../tests/test_work_dialogue.py#L42), [сохранение sealed-вопроса](evaluations/2026-10-25-question-publication-crash.md). Потеря отчёта после подтверждённой cleanup не уничтожает вопрос; первый VM failure и успешный повтор разделены |

Локальный исходный gate подтверждён. Фактический Mac-путь остаётся частью общей
платформенной приёмки, а не новым требованием к диалогу.

## 4. Единый экран приёмки и применение

Исходный gate: принятый результат явно применяется в новую ветку; сама приёмка
не меняет репозиторий и не выполняет merge/push.

| Существенный критерий | Статус | Код и конкретное подтверждение / граница |
|---|---|---|
| Отдельный экран требований, checks/logs, ограничений, diff, решения и доработки | Выполнено на Windows | [result_review](../src/kaibain/result_review.py#L238), [UI](../src/static/result-review.js), [browser_result_review_check](../tests/browser_result_review_check.py); [scripted web reviewer/revision/Apply](evaluations/2026-10-25-atomic-edits.md#проверки), [native revision evidence](../ROADMAP_AUDIT.md#актуальный-срез-периодические-снимки-nativegit-и-реальная-ollama). Checks агента остаются self-reported; независимая смысловая оценка всех критериев не реализована и ROADMAP этого не скрывает |
| Человеческая приёмка отдельно от Apply, версии запрещают stale review | Выполнено | [Store.review_agent_run/review_goal](../src/kaibain/store.py#L815), [test_result_review](../tests/test_result_review.py#L71), [test_native](../tests/test_native.py#L19). [Report-order evidence](evaluations/2026-10-25-report-order.md) подтверждает выбор последнего внешнего отчёта при равных датах и запрет старой приёмки |
| Явный Apply с fresh preview, integrity, HEAD/index/affected worktree checks создаёт отдельную ветку/commit; пользовательские файлы сохраняются | Выполнено на Windows, real Git / изолированные fixture exports | [apply](../src/kaibain/result_review.py#L260): private index, `commit-tree`, create-only CAS `update-ref`; [test_result_review](../tests/test_result_review.py#L71) и [браузерная проверка](../tests/browser_result_review_check.py). Последняя сама по себе не доказывает model/VM execution |
| Конфликты и retry после ref publication не перезаписывают чужую ветку/правки и не дублируют commit; hooks/filters не исполняются | Выполнено | [test_result_review](../tests/test_result_review.py#L92): stale preview, moved HEAD, foreign branch, tampering, user ownership, post-acceptance edits; [crash reconciliation и hooks/filters](../tests/test_result_review.py#L136). Receipt сохраняется в SQLite; retry требует человеческого действия |

Исходный локальный gate выполнен. Удаления, auto-merge/rebase и автоматическая
независимая оценка требований отсутствуют — это заявленные границы, не добавленные
этой сверкой условия закрытия. Ни acceptance, ни fixture Apply не разрешают
применение пользовательского результата агентом.

## 5. Граф состояний и связей

Исходный gate: состояния/связи основаны на реальных событиях; понятно текущее
действие и причина ожидания.

| Существенный критерий | Статус | Код и конкретное подтверждение / граница |
|---|---|---|
| Фазы планирования/изменения/проверки/ожидания/блокировки; сохранённые review/stop/error и причина | Выполнено на Windows | [project_graph](../src/kaibain/agent_graph.py), [Store.report_agent](../src/kaibain/store.py#L1092), [UI](../src/static/agents-live.js); [test_agent_graph](../tests/test_agent_graph.py#L34), [browser_agent_graph_check](../tests/browser_agent_graph_check.py). [Web DAG evidence](evaluations/2026-10-24-parallel-web-native.json) связывает граф с настоящим stdio/runtime, хотя модели scripted |
| Детали задачи/действия/времени и остатка run time/tokens/steps; неизвестный бюджет обозначен явно | Выполнено | [graph projection](../src/kaibain/agent_graph.py), [тесты budget/expiry](../tests/test_agent_graph.py#L34), [runtime progress](../../multi-agent-system/src/development_progress.py), [web budgets](evaluations/2026-11-04-web-budgets.md). Shared CPU/RAM/slots описывают reservation, не ресурсы model host и не sandbox attestation |
| Различимые assignment/dependency/handoff, цель-фильтр, история, keyboard/mobile/dark | Выполнено на Windows | [UI](../src/static/agents-live.js), [browser_agent_graph_check](../tests/browser_agent_graph_check.py); исторические graph/shared-resources receipts в [аудите 24.10](../ROADMAP_AUDIT.md#проверено-24-октября), новые реальные MCP события в [web budgets](evaluations/2026-11-04-web-budgets.md) |
| Версии/роли/область связей, запрет циклов, снятие stale assignment при move; handoff закрывает matching dependency | Выполнено | [Store.assign_agent/connect_agents](../src/kaibain/store.py#L1146), [test_agent_graph](../tests/test_agent_graph.py#L60). Связь графа сама не планирует VM: исполнение DAG относится к этапу 9 |
| Присутствие process-scoped, heartbeat/expiry, реальные runtime stages; optional validator виден | Выполнено в проверенных границах | [test_agents](../tests/test_agents.py#L35), [test_mcp_lifecycle](../tests/test_mcp_lifecycle.py), [web budgets, EOF исправление](evaluations/2026-11-04-web-budgets.md#исправления-найденные-проверкой); [independent review tests](../tests/test_independent_review.py). Normal EOF отключает только свою identity; kill использует expiry. Регистрация и текст phase не доказывают изоляцию или native cadence внешнего CLI |

Локальный gate выполнен; Mac и внешние CLI не засчитываются по этим событиям.

## 6. Восстановление после сбоев

Исходный gate: безопасно продолжить прерванный прогон после рестарта, сохранив
результаты, ограничения и контроль принадлежащих ему ресурсов.

| Существенный критерий | Статус | Код и конкретное подтверждение / граница |
|---|---|---|
| Полный checkpoint с SHA-256 и task/child/requirements versions, immutable receipt; partial не пригоден | Выполнено | [Store.publish_checkpoint](../src/kaibain/store.py#L727), [load_checkpoint](../src/kaibain/recovery.py#L11), [test_recovery](../tests/test_recovery.py#L69); [export-crash first/after-write](evaluations/2026-10-25-export-crash.md#три-проверенные-границы). Worker transitions и receipt атомарны; manifest error/Stop откатывают их |
| Только явный Resume после preview/restart, исходный profile/argv и те же задачи, новая sandbox/tests/clean replay, история/exports сохранены | Выполнено на Windows scripted + real VM | [recovery_preview/resume](../src/kaibain/agent_runtime.py#L204), [test_recovery](../tests/test_recovery.py#L127), [hard crash](evaluations/2026-10-25-hard-crash.md#сквозной-сценарий-с-настоящими-microvmsmcp), [DAG hard-kill](evaluations/2026-10-25-four-worker-dag.md), [web budget recovery](evaluations/2026-11-04-web-budgets.md). HTTP restart не выполняет auto-resume/cleanup |
| Fresh readiness, новые экземпляры исходных budgets, старый расход отдельно; повреждение/stale/runtime edit/cleanup failure/live old scope блокируют запуск | Выполнено в проверенных границах | [runtime.resume/start](../src/kaibain/agent_runtime.py#L279), [test_recovery](../tests/test_recovery.py#L153), [web time 90 s](evaluations/2026-11-04-web-budgets.md#подтверждённый-deadline-сценарий). Scope liveness только читается, старые PID/group не сигналятся; saved write при recovery не повторяется |
| Различать stop/model/tests/environment/budget/conflict; late MCP старого run запрещён | Выполнено | [failure_kind](../src/kaibain/recovery.py#L42), [test_recovery](../tests/test_recovery.py#L187), [web budget классификация](evaluations/2026-11-04-web-budgets.md#исправления-найденные-проверкой). Budget failure — ошибка, а не user cancellation; неизвестные token metrics не заменяются нулём |
| Owned host tree/VM cleanup и outsider survival; KeyboardInterrupt/SystemExit и потеря supervisor | Выполнено на Windows для перечисленных воздействий | [ProcessScope](../src/kaibain/process_scope.py), [run_job/cleanup_owned](../src/kaibain/agent_runtime.py#L358); [supervisor interrupt](evaluations/2026-10-25-supervisor-interrupt.md), [реальный hard-kill](evaluations/2026-10-25-hard-crash.md), [DAG при двух живых VM](evaluations/2026-10-25-four-worker-dag.md). Есть SDK absence до нового supervisor; receipt active сразу после kill не считается closed |
| Crash-точки вопроса/SQLite публикации и отсутствие преждевременного completed/review до cleanup/commit | Выполнено для сохранённой матрицы | [Store.finish_agent_run](../src/kaibain/store.py#L582), [test_publication_crash](../tests/test_publication_crash.py), [process-crash tests](../tests/test_publication_process_crash.py); [question/publication evidence](evaluations/2026-10-25-question-publication-crash.md), [export cleanup crash](evaluations/2026-10-25-export-crash.md). Rollback до commit и сохранение после commit проверены отдельно; Stop не обходится |
| Без первого checkpoint отказать в Resume; не импортировать partial/неизвестные действия | Выполнено | [export crash](evaluations/2026-10-25-export-crash.md): `CHECKPOINT_UNAVAILABLE`, no new attempt; after-write выбирает опубликованный 001, а не неполный 002. Неполученные/неизвестные действия и память VM не восстанавливаются — это исходная граница, не недостающая функция |
| Отдельная человеческая очистка terminal-run без checkpoint/stale context; lease/fresh ack/retry, без изменения задач/exports | Выполнено на Windows для нормального operator cleanup | [cleanup_run](../src/kaibain/agent_runtime.py#L234), [Store cleanup lease](../src/kaibain/store.py#L385), [24 unit](../tests/test_run_cleanup.py), [03.11 UI + real VM](evaluations/2026-11-03-operator-cleanup.md). Замороженный argv, gated receipt, fence launch/recovery/backup, fresh `ok/records_checked`; MCP cleanup tool отсутствует |
| HTTP-owner crash во время самой operator cleanup при живой VM, restart/fence и явный retry | Выполнено на Windows HTTP/VM/UI trusted fixture | [Новый отчёт 04.11](evaluations/2026-11-04-cleanup-owner-crash.md), [JSON](evaluations/2026-11-04-cleanup-owner-crash.json), [browser fixture](../tests/browser_operator_cleanup_crash_check.py). Running lease даёт `CLEANUP_BUSY`; native handle kill HTTP owner закрывает Job helper, та же SDK VM и outsider остаются живы. Restart сохраняет interrupted/`CLEANUP_REQUIRED`, без auto-cleanup. Явный UI retry создаёт новую action, fresh ack `records_checked:1`, SDK missing до holder teardown; исходные строки/версии/exports/Git неизменны. Terminal-run/экспорт подготовлены trusted fixture: это не MCP coding run, LLM или clean coding replay. Два ранних harness failure сохранены |
| Общая платформенная/crash приёмка за пределами этих Windows воздействий | Не проверено | [Hard-crash границы](evaluations/2026-10-25-hard-crash.md#наборы-и-границы) и [export-crash границы](evaluations/2026-10-25-export-crash.md). Нет доказательства Mac/POSIX или потери питания. Это ограничения имеющейся матрицы, а не придуманное требование восстанавливать память VM/инференс или произвольные внешние эффекты |

Локальная Windows реализация продолжения и новая operator-cleanup crash граница
подтверждены в описанных сценариях; исходная общая платформенная приёмка остаётся
открытой. Обрыв финальной cleanup **supervisor** 25.10 и обрыв HTTP-владельца
**operator cleanup** 04.11 — разные подтверждённые сценарии с разным scope proof.

## 7. Реальные модели и независимое ревью

В ROADMAP нет отдельной фразы «Готово, когда» для этапа 7. Его исходные открытые
gates названы прямо: чистое итоговое сравнение, финальные полные тесты и приемлемое
качество ревью; финальная приёмка человеком.

| Существенный критерий | Статус | Код и конкретное подтверждение / граница |
|---|---|---|
| Реальная Ollama: bugfix/feature/multiple_files/revision, сравнение с/без review и seeded calibration | Частично | [23.10 JSON](evaluations/2026-10-23.json), [EVALUATION: методика/результаты](../../multi-agent-system/EVALUATION.md#методика-и-результаты-реальной-ollama). Suite-v1: 3/5 в обоих вариантах, по одному пропущенному oracle defect. V2: 5/5 в обоих; 130.811 с / 25986 токенов без, 152.899 с / 28461 с review. Прирост качества не показан |
| Независимая read-only третья модель после tests и clean replay; family/digest из tags+show, запрет другого Qwen как «независимого» | Выполнено как механизм | [runtime route/family/review](../../multi-agent-system/src/independent_review.py#L23), [development_run](../../multi-agent-system/src/development_run.py), [review tests](../../multi-agent-system/tests/test_independent_review.py), [EVALUATION](../../multi-agent-system/EVALUATION.md#что-проверяет-валидатор). Qwen3/Qwen3.5 → qwen, Gemma3 → gemma; presence отдельной роли не доказывает качество |
| Schema, exact export path/line/evidence, hashes clean checks/export, SQLite/review UI/graph | Выполнено как механизм и Windows scripted integration | [load_review](../src/kaibain/independent_review.py), [Store.validate_independent_review](../src/kaibain/store.py#L694), [test_independent_review](../tests/test_independent_review.py#L41); [reviewer-v3](../ROADMAP_AUDIT.md#этап-7-результаты-и-границы) — реальные VM, **scripted HTTP**. Совпадение цитаты не доказывает истинность finding |
| Атомарная публикация completed/root+child review только после owned cleanup; model accept не принимает цель | Выполнено | [finish_agent_run](../src/kaibain/store.py#L582), [run_job](../src/kaibain/agent_runtime.py#L358), [publication crash evidence](evaluations/2026-10-25-question-publication-crash.md), [test_independent_review](../tests/test_independent_review.py#L41). Человеческий Apply остаётся отдельным |
| Приемлемое качество независимого ревью | Частично, gate открыт | [Calibration-v5](../../multi-agent-system/EVALUATION.md#отдельная-seeded-калибровка): 2 defective + 2 fixed, 2 обнаружения, **1 false positive**, 0 false accepts, 0 invalid, 3627 токенов. Малая выборка не даёт общей гарантии. Validator по STATUS OFF; включение не является доказательством приёмки |
| Чистое итоговое сравнение | Не проверено успешным итоговым прогоном | [Suite-v3](evaluations/2026-10-23.json): 3/5 и 4/5 запланированных, фактически 4/5 попыток; WinError 10055/HTTP 500 и неполный учёт исключают сравнение качества. Calibration-v1 invalid, v2 HTTP 400, v3 ungrounded сохранены; v4 с подсказками исключён как development artifact |
| Финальные полные тесты и человеческая приёмка этапа | Частично | [Полные engineering suites 04.11](evaluations/2026-11-04-web-budgets.json) уже прошли и заменяют старое утверждение «вообще нет свежих полных наборов». Они не превращают неполный LLM eval в итоговый результат; отдельного успешно завершённого сравнения и его финальной приёмки в evidence нет |

Остаются ровно названные ROADMAP quality/final gates. 4 ноября пользователь
разрешил ограниченные реальные LLM-проверки на изолированных проектах; фактический
новый результат ещё нужен. Широкий benchmark или новый численный порог качества
этим документом не вводится.

## 8. Исполнитель более крупных проектов

Исходный gate: реальная задача проекта с зависимостями проходит выполнение,
проверки и повтор в чистой sandbox. ROADMAP отдельно оставляет открытой именно
большую native-задачу и Mac/aarch64.

| Существенный критерий | Статус | Код и конкретное подтверждение / граница |
|---|---|---|
| Выборочное чтение диапазонов, literal search/cursor, SHA-256 replace вместо полного source dump | Выполнено на Windows | [runtime context](../../multi-agent-system/src/development_context.py), [sandbox_worker](../../multi-agent-system/src/sandbox_worker.py), [context tests](../../multi-agent-system/tests/test_context_tools.py); [24.10 JSON](evaluations/2026-10-24.json), [source-memory evidence](evaluations/2026-10-25-source-memory.md). Более поздний context real VM набор — 26/26, не свидетельство успешной большой правки |
| Воспроизводимые pinned dependencies и профиль Kaibain; изоляция/защита tests/resource ceilings сохраняются | Выполнено в bounded Windows профилях | [dependency_environment](../../multi-agent-system/src/dependency_environment.py), [native README/locks](../../multi-agent-system/examples/kaibain-native/README.md), [guest Git profile](../../multi-agent-system/examples/guest-toolchain/README.md); [domain/packaging evidence](evaluations/2026-10-24.json). Без host Git и без выполнения проектного кода на хосте |
| Реальная coding-задача с pure-Python dependency и clean replay | Выполнено исторически для малой задачи | [24.10 JSON, `actual_ollama_offline_dependency_coding`](evaluations/2026-10-24.json): успешный packaging receipt `1792779548640657300`, baseline exit 1 → clean exit 0, source unchanged. Предыдущий model_unavailable сохранён отдельно. Этот успех не закрывает расширенный native scope этапа |
| Native v2 x86_64 closure/Git и полный application baseline/replay | Выполнено исторически для конкретного снимка | [Native README](../../multi-agent-system/examples/kaibain-native/README.md), [аудит native/Git](../ROADMAP_AUDIT.md#актуальный-срез-периодические-снимки-nativegit-и-реальная-ollama): 29 hash-pinned пакетов, pinned guest Git; **137 проверок, 135 pass / 2 ожидаемых skip** в каждой свежей VM. Это старый snapshot, не нынешний suite 200. Более ранние 131/16 Git errors и subset 115 не переписываются в success |
| Полный native web/MCP цикл: вопрос после правок, revision/recovery и fixture Apply | Выполнено на Windows scripted для указанных fixtures | [Аудит native web receipts](../ROADMAP_AUDIT.md#актуальный-срез-периодические-снимки-nativegit-и-реальная-ollama), [native README](../../multi-agent-system/examples/kaibain-native/README.md). Поэтому ранняя фраза «native web открыт» уже не означает отсутствие такой интеграции вообще; реальная автономная большая доработка по-прежнему открыта |
| Расширение контекста по измерениям: exact fragments/cache, no-progress guard, batch edits, baseline/decision note и бюджет | Выполнено как инструменты; качество большой задачи не подтверждено | [source-memory](evaluations/2026-10-25-source-memory.md), [atomic-edits](evaluations/2026-10-25-atomic-edits.md), [decision-notes](evaluations/2026-10-25-decision-notes.md); код [memory](../../multi-agent-system/src/development_memory.py), [decision](../../multi-agent-system/src/development_decision.py). Cache bounded, stale SHA требует нового чтения, заметка verified:false, tests/replay не отключаются |
| Успешное автономное исправление большого native Kaibain и чистая проверка принятого model export | Частично, gate не достигнут | [12 попыток 24.10](evaluations/2026-10-24-native-coding.md), [ещё memory attempt](evaluations/2026-10-25-source-memory.md), [3 atomic attempts](evaluations/2026-10-25-atomic-edits.md), [4 подтверждённые decision-note попытки](evaluations/2026-10-25-decision-notes.md): готовой правки нет. Отдельный KeyboardInterrupt исключён из model quality; full-suite checker нового принятого model export не запускался за отсутствием экспорта |
| Старый дефект report ordering ещё может служить новой реальной задачей | Не подтверждено; историческая задача уже исправлена | [Report-order fix](evaluations/2026-10-25-report-order.md), [Store latest result](../src/kaibain/store.py#L985), [regressions](../tests/test_report_order.py). Это обычная разработка без LLM; старые statements «рабочий Store ещё неисправлен» относятся к своим датам. Для возобновления model eval нужна новая реальная задача согласно [STATUS](STATUS.md#открыто-и-требует-отдельного-окружения-или-снятия-паузы) |
| Native/Git/aarch64 на Apple Silicon | Подготовлено статически; не проверено исполнением | [Native target measurements](../../multi-agent-system/examples/kaibain-native/measurements.json), [ARM64 guest Git](evaluations/2026-11-04-arm64-guest-git.md): actual archives/ELF/closure проверены на Windows, amd64 regression прошла в VM. Mac/aarch64 запуск не выполнен |

Исходный малый dependency gate исторически показан, расширенный gate большого
native-проекта не достигнут. Нельзя засчитать обычное исправление report ordering,
улучшение инструментов или scripted patch как автономный model success.

## 9. Специализации и управляемая параллельность

Исходный gate: несколько исполнителей соблюдают общий бюджет, не перезаписывают
чужие результаты и правильно освобождают ресурсы после остановки.

| Существенный критерий | Статус | Код и конкретное подтверждение / граница |
|---|---|---|
| Роли backend/frontend/tests/documentation, валидированный DAG до четырёх областей, зависимости и очередь | Выполнено как механизм и Windows scripted scheduling | [SPECIALTIES/parse_plan/Scheduler](../../multi-agent-system/src/scheduling.py#L18), [queue unit](../../multi-agent-system/tests/test_scheduling_queue.py), [четыре области](evaluations/2026-10-25-four-worker-dag.md). VM fixtures новых receipts используют backend-области; наличие четырёх ролей не выдаётся за real-model приёмку каждой специальности |
| Общие worker/CPU/RAM ceilings; lease до boot и через clean replay, inference сериализован | Выполнено в проверенных Windows сценариях | [Budget/SharedInference](../../multi-agent-system/src/scheduling.py#L72), [ParallelRun](../../multi-agent-system/src/parallel_development.py); [four-worker queue](evaluations/2026-10-25-four-worker-dag.md): четыре области / два слота, actual peak **2 VM / 2 CPU / 1024 MiB**, admission после cleanup, 11 VM с oracle отсутствуют в конце. Unit отдельно ограничивают slots/CPU/RAM |
| Изоляция workers, исключительное file ownership, readonly prerequisites и hash-checked merge; тесты и clean replay объединения | Выполнено на Windows scripted + real VM | [merge_export](../../multi-agent-system/src/scheduling.py#L216), [parallel artifacts](../../multi-agent-system/src/parallel_artifacts.py); [scheduler binary/layout](evaluations/2026-10-25-parallel-scheduler.md), [fanin diamond](evaluations/2026-10-25-fanin-budgets.md): alpha → beta/gamma → delta, оба prerequisite tests/replay/cleanup до delta, защищённые bytes, отдельный 11-й VM oracle |
| При отказе prerequisite зависимый worker не запускается; другая ветвь завершается самостоятельно | Выполнено на Windows runtime + real VM, scripted | [fan-in evidence](evaluations/2026-10-25-fanin-budgets.md#проверки-настоящих-vm), [test_parallel_fanin](../../multi-agent-system/tests/test_parallel_fanin.py): failing beta, gamma реально жива, delta не допущен; 5 VM очищены, интеграции нет. Это не LLM benchmark |
| Общие token/time ceilings прекращают admission/inference при активных и queued workers; typed failure и явное recovery | Выполнено на Windows через полный web/MCP/VM scripted путь | [web budgets, 04.11](evaluations/2026-11-04-web-budgets.md), [JSON](evaluations/2026-11-04-web-budgets.json), [browser budget test](../tests/browser_parallel_budget_check.py). Две SDK-confirmed живые VM, две queued; token reserve failure / **исходный deadline 90 s**, no new generation/admission; `token_budget/time_budget`, checkpoint, HTTP restart и Resume с теми же task IDs, tests/clean replay/oracle |
| Stop/cancellation дожидаются всех owned stop/report; cleanup failure сохраняет резерв и запрещает очередь | Выполнено на Windows для проверенных воздействий | [Budget.mark_cleanup_failed](../../multi-agent-system/src/scheduling.py#L93), [cancel cleanup tests](../../multi-agent-system/tests/test_development_cancel_cleanup.py); [scheduler cleanup fault](evaluations/2026-10-25-parallel-scheduler.md), [budget live VM failure](evaluations/2026-10-25-fanin-budgets.md), [04.11 исправление await](evaluations/2026-11-04-web-budgets.md#исправления-найденные-проверкой). Живая VM при намеренном отказе stop удерживает 1 CPU/512 MiB до явной cleanup; fallback не переписывает исходный failure в success |
| Атомарные DAG checkpoints/statuses и сохранение всех задач при вопросе/revision/recovery/crash | Выполнено на Windows scripted | [Store.publish_checkpoint](../src/kaibain/store.py#L727), [parallel_web](../../multi-agent-system/src/parallel_web.py), [test_recovery](../tests/test_recovery.py#L69); [native/DAG исторические receipts](../ROADMAP_AUDIT.md#актуальный-срез-периодические-снимки-nativegit-и-реальная-ollama), [four-worker hard-kill](evaluations/2026-10-25-four-worker-dag.md), [web budgets](evaluations/2026-11-04-web-budgets.md). Есть ранний/поздний sealed snapshot; partial не используется |
| Real Ollama parallel coding с actual VM overlap и clean replay | Выполнено исторически для двух простых workers; частично для итогового scope | [24.10 runtime JSON](evaluations/2026-10-24.json): 2 worker, 31.689 с / 13199 токенов; [real web receipt в аудите](../ROADMAP_AUDIT.md#актуальный-срез-периодические-снимки-nativegit-и-реальная-ollama): 35.251 с / 13404 токена, overlap 22.319 с, behavioral oracle свежей VM, pending human review. Первый неверный DAG сохранён. Не подтверждает новую четырёхобластную real-model проверку |
| Итоговая платформенная/модельная приёмка этапа | Частично | Windows scripted исходный инженерный критерий теперь подтверждён перечисленными receipts. [ROADMAP](../ROADMAP.md), [STATUS](STATUS.md) сохраняют отдельные Mac/aarch64 и model gates; последние на паузе. Наличие CLI флага и исторический малый model success не равны финальной приёмке |

Заново открывать четыре области/очередь, fan-in, global budget web/MCP или hard-kill
DAG на основании ранних записей аудита неправильно: последующие отчёты их уже
подтвердили в Windows scripted границе. Отсутствующие scoped test commands,
одновременный inference и переключение последовательного рабочего argv не являются
исходными требованиями закрытия. Каждый worker использует полные fixed checks;
связанные правки требуют одного владельца — заявленное ограничение.

## Сквозные критерии ROADMAP

### Agents-native основа

| Критерий из вступления ROADMAP | Статус | Конкретное подтверждение |
|---|---|---|
| Главная: цели/review queue/counters/actions; доска/календарь/дерево для наблюдения | Выполнено на уровне реализации и Windows UI fixtures | [native.js](../src/static/native.js#L24): dashboard/board/calendar/tree; [browser_check](../tests/browser_check.py) использует настоящий stdio MCP и отдельную БД; [core/native tests](../tests/test_core.py), последние полные сохранённые наборы — [04.11 JSON](evaluations/2026-11-04-web-budgets.json). Настоящий MCP SDK fixture не равен CLI acceptance |
| Пользователь задаёт/уточняет цель, агенты владеют подзадачами/статусами; нет manual switch | Выполнено | [Store.workspace/права](../src/kaibain/store.py#L1258), [create/update](../src/kaibain/store.py#L1438), [test_core](../tests/test_core.py#L203), [test_native](../tests/test_native.py#L19). Legacy mode=agent совместим, human goal done только через review |
| Built-in start с profile, graph/diff/report, stop/owned cleanup, review/revision по версиям | Выполнено на Windows scripted + real VM для сохранённых сценариев | Этапы 1–6 выше; [04.11 web budgets](evaluations/2026-11-04-web-budgets.md), [03.11 cleanup](evaluations/2026-11-03-operator-cleanup.md). [Owner-crash 04.11](evaluations/2026-11-04-cleanup-owner-crash.md) дополнительно подтверждает HTTP/VM/UI lease/retry, но сам не является built-in coding/MCP proof |
| Внешний submit_goal_result и отдельная человеческая приёмка; история и freshness | Выполнено как MCP/Store механизм | [Store.submit/review](../src/kaibain/store.py#L990), [test_native](../tests/test_native.py#L30), [report-order evidence](evaluations/2026-10-25-report-order.md). Checks self-reported; реальные native CLI ещё не приняты |
| Задачи/связи/статусы/проекты/run history сохраняются; отдельная система tags удалена | Выполнено | [Store schema/migrations](../src/kaibain/store.py), [migration/tag removal](../tests/test_core.py#L72), [projects](../tests/test_projects.py#L62), [agents persistence](../tests/test_agents.py#L70). Старые backup counts с тегами описывают старую БД, не текущие записи |
| Права/конфликты/UI/review/dark/mobile покрыты проверками | Выполнено в Windows fixture границе | [browser_check](../tests/browser_check.py), [graph check](../tests/browser_agent_graph_check.py), [result review check](../tests/browser_result_review_check.py), [03.11 visual cleanup evidence](evaluations/2026-11-03-operator-cleanup.md#проверки). Не утверждается, что все браузерные сценарии заново прогнаны на HEAD этой сверкой |

### Платформы, реальные клиенты и сохранность данных

| Исходное требование | Статус | Доказательство и оставшийся gate |
|---|---|---|
| Полный встроенный цикл сначала MacBook Air M4, затем Windows | Частично | Windows web/MCP/VM есть в [04.11](evaluations/2026-11-04-web-budgets.md) и crash-отчётах; исторические реальные Ollama отдельно выше. **MacBook Air M4/POSIX/aarch64 цикл не проверен**. CI/код для Mac не являются исполненным прогоном |
| Kaibain и sandbox на Mac, удалённая Ollama на PC | Не проверено фактически | [STATUS](STATUS.md#открыто-и-требует-отдельного-окружения-или-снятия-паузы), trusted endpoint поддержан этапом 2. Windows loopback и metadata probe не доказывают межмашинную сеть |
| Claude Code первым: MCP connection/current data/presence/assignment/report/human review | Не проверено успешным native workflow | [CLIENT_ACCEPTANCE, 24.10](CLIENT_ACCEPTANCE.md#наблюдения-на-windows-24-октября-2026): Claude не обнаружен тем probe, что не доказывает отсутствие установки сейчас. [Harness](../tests/client_acceptance.py) и 13 model-free checks не заменяют login/trust и полный workflow |
| Codex затем: тот же реальный workflow и native permissions | Не проверено успешным native workflow | [CLIENT_ACCEPTANCE](CLIENT_ACCEPTANCE.md): исторически CLI 0.164.0/login есть, **0 успешных MCP calls**, native approval gate остановил вызовы до adapter; exit 0 не принят как успех. Нужен штатный интерактивный flow без bypass. Данная сессия не повторяла CLI probe/inference |
| Backup SQLite + необходимые artifacts, consistency/version receipts | Выполнено на Windows | [backup.py](../src/kaibain/backup.py#L357), [21 test_backup](../tests/test_backup.py): WAL snapshot, review/checkpoint/Apply receipts, hashes/versions/DB consistency, archive/path attacks, отсутствие config/credentials и overwrite; [исторический CLI roundtrip](../ROADMAP_AUDIT.md#проверено-24-октября), более свежий backup fence — [03.11 cleanup](evaluations/2026-11-03-operator-cleanup.md). Restore в новую папку, без запуска/Apply/включения adapter |
| Восстановление БД/artifacts на обеих ОС | Частично | Windows roundtrip подтверждён; **Mac roundtrip не проверен**. 34 задачи/2 тега старого receipt относятся к 24.10; не выдаются за текущую БД |
| Изолированные новые проверки прав/конфликтов/отмены/cleanup, разделение scripted и actual model | Выполнено для сохранённых отчётов | [04.11 условия/retained failures](evaluations/2026-11-04-web-budgets.md), [03.11](evaluations/2026-11-03-operator-cleanup.md), [test_core права/конфликты](../tests/test_core.py#L139), [test_recovery](../tests/test_recovery.py). Весь roadmap ещё открыт; приёмка пользователя не выводится из test success |

### Заявленные границы, которые нельзя принять за новые gates

| Граница ROADMAP | Сверка с текущей реализацией / evidence |
|---|---|
| Python bounded profile, до 20 import/writable; full read 4096, range до 200 строк/4096 JSON bytes, literal continuation, replace по hash | [validate_project](../src/kaibain/store.py#L320), [context/worker](../../multi-agent-system/src/development_context.py), [context VM evidence](evaluations/2026-10-25-source-memory.md). Не универсальный executor произвольных проектов |
| Запрос цели+диалог+feedback до 8000 UTF-8 bytes; диалог 4000, до 20 вопросов и один открытый | [Store dialogue/reserve](../src/kaibain/store.py), [test_dialogue](../tests/test_dialogue.py#L131), [runtime development inputs](../tests/test_development_inputs.py). Это существующие пределы, не цель автоматического увеличения |
| Один активный built-in run на БД; waiting_for_input освобождает слот | [reserve/claim](../src/kaibain/store.py#L442), [dialogue pause tests](../tests/test_dialogue.py#L81), [cleanup fence](../tests/test_run_cleanup.py). DAG workers внутри одного run не противоречат этому ограничению |
| Checkpoint recovery не восстанавливает память VM, inference или неизвестные внешние действия | [recovery code](../src/kaibain/recovery.py), [export-crash evidence](evaluations/2026-10-25-export-crash.md); нет требования реализовать такое восстановление |
| Kaibain хранит/UI/права, sibling — orchestration и microVM policy; host/container fallback отсутствует | [agent_runtime](../src/kaibain/agent_runtime.py), [runtime microvm backend](../../multi-agent-system/src/microvm_backend.py), [runtime AGENTS](../../multi-agent-system/AGENTS.md). Фазы/регистрация/resource ledger не аттестуют sandbox сами по себе |
| Рабочий runtime последовательный, validator OFF; native/DAG — opt-in | [STATUS](STATUS.md#текущие-ограничения-пользователя), [04.11 границы](evaluations/2026-11-04-web-budgets.md#воспроизведение-и-границы). Сверка не читает/перенастраивает machine-local argv и не объявляет экспериментальный режим рабочим |

## Оставшиеся исходные gates и реальные пробелы

1. **Общая платформенная приёмка, включая этап 6:** полный MacBook Air M4 цикл, POSIX/Apple Silicon
   VM/native/Git, Mac backup roundtrip; отдельно Mac sandbox → PC Ollama.
   Windows scripted evidence остаётся выполненной Windows границей.
2. **Реальные внешние клиенты:** успешный native MCP workflow Claude Code, затем
   Codex с обычными trust/approval, актуальными данными, presence/assignment/report
   и человеческим review. Исторический probe/login/SDK fixture недостаточен.
3. **Этап 7:** малый чистый comparison версии V3 прошёл 5/5 в обоих режимах;
   приемлемое независимое ревью и человеческая приёмка открыты. Исторические
   suite-v3 socket failures исключены; calibration-v5 false positive сохранён.
   Последующая версия с context allocation/enum_sets имеет unit/scripted evidence;
   её actual inference возобновлён: десять failures сохранены отдельно. Новый
   native-tools real run в срезе render fix — token_budget без правки. Итоговый
   comparison не выполнен; V3 не переносится автоматически на новый runtime.
4. **Этап 8:** успешная реальная автономная задача большого native-проекта с
   protected checks и clean replay. Старый report-order defect уже исправлен
   обычной разработкой; для model eval нужен актуальный реальный сценарий.
   [Readiness baseline](evaluations/2026-11-04-native-readiness.md) воспроизведён в VM;
   исходные и [десять возобновлённых failures](evaluations/2026-11-04-native-readiness-resumed.md) сохранены.
   [Native tools/render fix](evaluations/2026-11-04-native-tools.md) прошёл unit
   и scripted web/MCP/VM. Последующее [oversized metadata правило](evaluations/2026-11-04-oversized-read.md)
   прошло 452 checks. [Срез 5 ноября](evaluations/2026-11-05-source-observations.md)
   имеет 471 checks; Qwen3.5/Qwen3 с thinking и разрешённый Coder trial тоже failed,
   готового model export/full guest suite нет. Actual __init__.py содержит 48 bytes;
   прежний вывод о его пустоте исправлен, fresh page visibility требует доработки.
5. **Этап 9:** отдельная итоговая платформенная/модельная приёмка. Windows scripted
   очередь, fan-in, global budget, Stop и sealed DAG recovery уже подтверждены;
   реальный двухworker успех исторический и bounded. Не добавлять scoped checks,
   одновременный inference или переключение production argv в критерий готовности.

Эта сверка не вводит требований универсального native stack, широкого benchmark,
auto-merge, восстановления памяти VM или неограниченной crash-матрицы. Потеря
питания остаётся явно непроверенным воздействием существующих crash-отчётов;
Windows process kill не выдаётся за такую проверку.

Найденный локальный пробел этапа 1 проверен после первоначальной сверки и отражён
выше. Остаток не закрывается повтором scripted проверок: нужны фактическая
платформа и реальная модельная/клиентская приёмка. 4 ноября пользователь разрешил
ограниченные реальные LLM-прогоны; доступа к Mac нет. Новых функциональных
требований сверка не добавляет.
