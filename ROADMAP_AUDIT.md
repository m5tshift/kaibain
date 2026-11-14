# Проверка выполнения roadmap

Срез на 24.10.2026: реализация механизма и завершение приёмки учитываются отдельно.
Источники этапа 7 и ограничения: [EVALUATION.md](../multi-agent-system/EVALUATION.md).

Дополнение 25.10: [память исходников и защита от циклов](docs/evaluations/2026-10-25-source-memory.md)
реализованы и проверены. Runtime 216 unit (183 pass/33 opt-in skip), реальные
context VM 26/26 и scripted graph VM 17/17; terminal-loop cleanup повторён отдельно.
Новая native Ollama попытка остановлена `token_budget`, учтено 17890 токенов,
правок нет. Этап 8 не закрыт; отчёты ниже сохраняют исторические результаты.

Следующий срез 25.10: [пакетные правки и render-only резерв](docs/evaluations/2026-10-25-atomic-edits.md).
267 runtime unit (232 pass/35 skip), 16 batch VM и 18 graph VM прошли; отдельный
scripted web/MCP/VM batch/reviewer/revision/fixture Apply/stop прошёл.
Три actual Ollama native попытки снова без правок. Последняя допустила седьмой
вызов с точным резервом, но агент использовал его на чтение. Автономная разработка
не принята; этап 8 открыт. Windows не закрывает Mac и внешних клиентов.

Срез `decision-note-v1`: [намерения и baseline](docs/evaluations/2026-10-25-decision-notes.md).
279 unit (244 pass/35 skip), 18 graph VM, scripted web batch/reviewer/revision/Apply
и recovery прошли. Найдено и исправлено скрытие baseline до первого developer
запроса. Четыре real-model попытки без правок; инфраструктурный KeyboardInterrupt
исключён из оценки. Готовность автономного native coding не подтверждена.

Срез прерывания: [supervisor и owned cleanup](docs/evaluations/2026-10-25-supervisor-interrupt.md).
147 main unit (145 pass/2 skip), 291 runtime (256 pass/35 skip); реальные VM
с KeyboardInterrupt, SystemExit и неподтверждённым cleanup-ack. Scope/VM закрыты,
контрольный процесс выжил, SQLite terminal до выхода, автоматического продолжения
нет. Дополнительные crash-точки и Mac остаются открытыми.

Срез [вопроса и атомарной публикации](docs/evaluations/2026-10-25-question-publication-crash.md):
157 main unit (155 pass/2 skip), rollback/commit при KeyboardInterrupt/SystemExit,
приоритет Stop; потеря sealed-вопроса исправлена. Windows real microVM/MCP +
scripted HTTP прошёл вопрос, restart и человеческое продолжение с тем же worker.
Первый прогон остановился на старте continuation VM и сохранён как неуспех;
повтор прошёл. Реальные LLM по текущему указанию пользователя не запускаются.
Физический crash/потеря питания, Mac и общий платформенный аудит не закрыты.

Срез [порядка внешних отчётов](docs/evaluations/2026-10-25-report-order.md):
165 main unit (163 pass/2 skip), восемь новых регрессий и браузерный stdio MCP
прошли без LLM/VM. Равные даты используют insertion order, get/list/review
согласованы. Это исправление разработки; историческая модельная задача теперь
регрессия и не доказывает готовность этапа 8.

Срез [жёсткого завершения](docs/evaluations/2026-10-25-hard-crash.md):
Windows owner death, реальные subprocess kill до/после commit, 169 unit
(167 pass/2 skip) и real microVM/MCP + scripted HTTP прошли. Подтверждены
supervisor_lost после restart, отсутствие auto-resume и owned cleanup до нового
supervisor. Исправлен незакрытый bootstrap pipe при ошибке Job assignment.
Отключение питания и Mac/POSIX этим не подтверждены; реальные LLM на паузе.

Срез [scheduler](docs/evaluations/2026-10-25-parallel-scheduler.md): runtime
311 unit (275 pass/36 opt-in skip); raw bytes merge/publication, layout по всему
профилю и удержание резерва при cleanup failure покрыты. Две отдельные VM-проверки
и web/MCP бинарный вопрос/продолжение прошли без LLM; неудачи оснастки сохранены.
Общая приёмка этапа 9, 4-node очередь/расширенные crash-сценарии и Mac остаются открытыми.

| Требование | Текущее свидетельство | Осталось подтвердить/сделать |
|---|---|---|
| 1. Проекты и запуск после рестарта | SQLite-профили, browser_projects_check.py | Повторить итоговую приёмку с окончательным runtime |
| 2. Диагностика и Mac → PC | Readiness API; tags, при optional validator также show; без inference/VM | Реальная связка Mac → PC |
| 3. Вопросы во время работы | Sealed checkpoint, MCP/SQLite/UI, restart/answer/continue; DAG и те же подзадачи, включая native/Git web | Mac |
| 4. Приёмка и новая ветка | Git CAS/private index; native DAG → возврат → доработка → fixture acceptance → отдельный Apply; HEAD/index/tree сохранены | Mac; accept модели не равен решению человека |
| 5. Граф | MCP-фазы, budget, typed links, optional validator и оценка | Платформенная приёмка; очередь относится к этапу 9 |
| 6. Восстановление | Полные периодические DAG checkpoints, ранние stop/model failure, восстановление с теми же задачами; atomic worker transitions + receipt и rollback проверены | Mac; расширенная матрица аварий |
| 7. Качество и независимое ревью | Read-only Gemma3 после tests/cleanVM; family/digest; grounded path/line/evidence; SQLite/review UI/graph; реальные suites и calibration | Этап открыт: качество модели, чистое итоговое сравнение и финальные полные тесты |
| 8. Крупные проекты | Bounded context/replace; pure-Python real coding; native v2/29-package closure + pinned guest Git; полный текущий suite 137 в baseline/replay, по 2 skip; native web question/revision/Apply | Реальная LLM-доработка большого native-проекта; Mac/aarch64 Git и VM |
| 9. Параллельная работа | Реальный web/Ollama/MCP/DAG: 2 worker, 13404 токена, 35.251 с, actual overlap и независимый VM oracle; native scripted stop/question/recovery/revision/Apply | Mac и расширенная проверка Windows; рабочее подключение последовательное |
| Backup/restore | SQLite + artifacts, SHA-256/consistency receipts; CLI, 21 проверка, восстановление рабочей копии в новое место | Реальный Mac roundtrip |
| macOS | Код/CI рассчитаны на обе ОС | Фактический полный запуск на MacBook Air M4 |
| Windows | Настоящие microVM и browser/MCP; reviewer-v3 завершён | Финальные полные наборы; повтор после socket failures |
| Claude Code → Codex | Реальный CLI probe/harness: Claude не найден; Codex 0.164.0 login подтверждён, MCP остановлен native approval gate; 13 model-free safety checks | Обнаруживаемый Claude и native login/trust; интерактивный MCP approval Codex, успешный workflow обоих клиентов |

## Этап 7: результаты и границы

- Real Ollama suite-v1: 3/5 успешных попыток в каждом варианте; по одному
  результату в каждом варианте пропустил дефект, выявленный скрытыми проверками.
- Suite-v2: 5/5 в каждом варианте; без ревью 130.811 с / 25986 токенов,
  с ревью 152.899 с / 28461 токен. Улучшение успешности на этой выборке не показано.
- Seeded calibration-v5: 2 дефектных и 2 исправленных образца; 2 обнаружения,
  1 ложное замечание, 0 ложных допусков, 0 невалидных отчётов, 3627 токенов.
  Calibration-v1: 2 невалидных отчёта; v2: HTTP grammar 400 во всех 4;
  v3: 4 ungrounded. V4 содержал подсказки под suite, сохранён как артефакт
  разработки и исключён из сравнения. Это малая curated-выборка, не широкий benchmark.
- Suite-v3 завершён: **3/5 и 4/5 запланированных** попыток; фактически 4 и 5.
  Без ревью 98.232 с / 21901 известных токенов; с ревью 158.721 с / 28623,
  один прогон с неполным учётом. WinError 10055 в revision round 0 исключил round 1;
  bugfix с ревью получил Ollama HTTP 500 из-за socket buffer failure Windows.
  Возвратов ревью на сгенерированном коде, прошедшем oracle, не было.
  Сбои окружения исключают v3 из сравнения качества; это не итоговый успех этапа.
- Reviewer browser-v2 и v3: `ok: true`, real microVM + scripted HTTP.
  Для v3 проверены `.local/validator-web-check-v3.log` и
  `.local/development-browser/1792774454153056800/check-report.json`.
  Это интеграция, не качество реальной LLM и не приёмка Mac.
- Прежние полные unit-логи не удостоверяют актуальное дерево после новых тестов
  report binding и конфигурации. Финальные полные наборы ожидаются. Здесь тесты
  не запускались; проверялись сохранённые отчёты и тесты по именам.

`atomic-review-v1`: после owned cleanup supervisor одной SQLite-транзакцией Store
сохраняет completed-отчёт и переводит root/child в review. Worker не выставляет
преждевременные финальные статусы задач; stop/crash до commit сохраняют версии
задач sealed checkpoint. Отчёт связан хешами с экспортом и clean checks.
Человеческая приёмка и отдельный Apply остаются за пользователем.

Валидатор не включён в рабочей конфигурации из-за качества модели. Механизм
`--enable-validator --validator-model gemma3:4b` точечно дополняет argv официального
существующего адаптера с backup, сохраняя coordinator/developer, endpoint и recovery;
наличие команды не означает её выполнение. Доступен только Windows; Mac, Mac → PC
и реальные Claude/Codex остаются открытыми. Этап 8 и backup проверены на Windows
в описанных ниже границах; ядро этапа 9 реализовано отдельно, интеграция ещё открыта.

## Проверено 24 октября

- Kaibain: 131 unit-проверка, 129 прошли, 2 пропущены (Windows symlink permission).
- Runtime: 97 unit-проверок, 70 прошли, 27 пропущены как отдельные реальные VM-наборы.
- Диапазоны/поиск/точечная правка: отдельно 23 проверки с настоящей microVM — все прошли.
- Offline окружение: 6 проверок, включая настоящий импорт packaging==24.2 от UID 1000.
- Real Ollama dependency coding: `.local/dependency-coding/1792779548640657300/check-report.json` в sibling; baseline 1, clean replay 0, исходные файлы не изменены. Ранее Ollama была остановлена — тот неудачный запуск сохранён отдельно.
- Профиль Kaibain domain: `.local/kaibain-profile/1792779646905922500/check-report.json` в sibling; настоящий код Kaibain в изолированной Git-копии/VM, только domain baseline.
- Вопрос после правок: `.local/work-dialogue-browser/1792801984183362900/check-report.json`; реальная microVM/MCP, scripted HTTP, не real Ollama.
- Backup/restore: `.local/backups/roadmap-20261023.zip` → новая `.local/restore-check-20261023/tasks.sqlite3`; совпали 34 задачи, 2 тега и история. Рабочая база не перезаписывалась.
- Ядро этапа 9: 7 unit-проверок; `.local/parallel-verification/1792802435030108200/check-report.json` в sibling — два worker в настоящих microVM, общий лимит 2 CPU/1024 MiB, объединение и clean replay, все VM остановлены. Ответы моделей scripted.
- Real Ollama этапа 9: `.local/parallel-real/1792802813788117800/check-report.json` — 2 worker, 31.689 с, 13199 токенов, полная отчётность, actual VM overlap подтверждён по import/stop events. Совместный clean replay и дополнительные acceptance assertions прошли; исходный репозиторий сохранён. Предыдущие неудачные планы/ответы/лимиты сохранены отдельно. Web/MCP-интеграция ещё не включена.
- Отдельная MCP-проекция DAG проверена настоящими stdio-процессами: назначения, зависимости, handoff, версии родительских задач и отказ при внешнем изменении. Общие CPU/RAM/слоты добавлены в Store/MCP/UI; `graph-shared-resources-v2.log` подтверждает граф, restart, тёмную тему и мобильный экран. Единый web-запуск DAG ещё не подключён.
- Native bundle v2 и CLI client harness оставлены субагентами как черновики после лимита сервиса. V1 и fixture-only проверены, но native VM и реальные Claude/Codex ещё не приняты. Границы описаны в sibling `examples/kaibain-native/README.md` и `docs/CLIENT_ACCEPTANCE.md`.

## Продолжение 24 октября: web DAG

- `tests/browser_parallel_development_check.py`: `.local/parallel-browser/1792838426547844800/check-report.json` — web start, граф/бюджеты, два worker, совместный clean replay, atomic review и stop во время inference. Все VM закрыты; HEAD/index/worktree фикстуры сохранены.
- `--question`: `1792838794663325700/check-report.json` — worker сначала изменил файл, затем общий checkpoint/вопрос; HTTP restart, человеческий ответ, те же aggregate и worker-задачи, без повторной записи сохранённой правки.
- `--recovery`: `1792839070184670900/check-report.json` — scripted read-only reviewer отклонил экспорт; явное восстановление из полного checkpoint сохранило DAG/задачи/профиль, повторило tests/clean replay, оставило старый отчёт неизменным.
- Это настоящие microVM/MCP/supervisor с scripted HTTP, не real Ollama и не оценка качества ревьюера. Ранние ошибки owner async scopes и селекторов сохранены в `parallel-web-v1/v2.log`, `parallel-recovery-v1.log`; исправлены без обхода MCP или cleanup.
- Для раннего stop/сбоя worker до общего checkpoint восстановление отдельных shard-снимков ещё не реализовано. Доработки после человеческого возврата и real Ollama всего web-пути требуют отдельной приёмки. Этап 9 открыт; рабочий argv не изменён.
- Клиентский gate подтверждён: [CLIENT_ACCEPTANCE.md](docs/CLIENT_ACCEPTANCE.md). Нет успешных реальных MCP-вызовов Codex; exit code CLI не засчитан как успех. Настройки trust/approval не изменялись.
- Native-прогон выявил одинаковые timestamps у задач: сортировка UUID меняла порядок соседей в дереве. Store использует insertion order при равном created_at; существующая проверка дерева теперь воспроизводит одинаковое время для всей партии.
- Финальные логи этого участка: Kaibain `unit-parallel-web-final.log` — 131 проверка, 129 pass/2 skip; runtime `unit-parallel-web.log` — 115 проверок, 86 pass/29 skip (реальные VM отдельно). После последних исправлений фаз/owner cleanup повтор `--recovery`: `1792839466335074300/check-report.json`, успех; `parallel-mcp-final.log` — реальный stdio owner lifecycle.
- Native v2: 25 dependency-проверок без skip, реальные pydantic_core/rpds/CFFI/cryptography операции в двух свежих offline VM; immutable 29-package MCP closure для cp312. x86_64 исполнен, aarch64 только статически проверен. После исправления дерева subset: 115 проверок, 113 pass/2 skip и в baseline, и в replay; полный набор 131 — 16 ошибок отсутствующего Git/2 skip. Это отдельный OS toolchain gate, не полный успех этапа 8. Свидетельства и hash-pinned locks: sibling `examples/kaibain-native/README.md`, `.local/native-completion-report.json`.
- Перед запуском актуального приложения сохранён `.local/backups/parallel-web-20261024.zip`; `run.py start/status` и `/api/health` подтвердили localhost:8765. Рабочий argv остаётся последовательным, validator OFF; тестовые цели в рабочую БД не добавлялись.

## Актуальный срез: периодические снимки, native/Git и реальная Ollama

Эта секция обновляет исторические ограничения выше; старые отчёты не переписаны.

- Ранний stop: `.local/parallel-browser/1792840195982719100/check-report.json`; ранний model failure: `1792840615921630800`; вопрос после периодических snapshots: `1792840771480472400`; доработка с теми же DAG-задачами: `1792840936985006400`. Реальные VM/MCP/supervisor, scripted HTTP.
- Статусы worker и receipt теперь фиксируются одной SQLite-транзакцией `publish_checkpoint(task_updates=...)`. Проверены rollback на неверном post-transition manifest, запрет root/aggregate/out-of-scope/stale/duplicate updates, stop до commit и конкурирующий stop во время commit. Неполные exports не публикуются; после 64 snapshots фиксированный лимит останавливает попытку.
- Native/Git web с новым bootstrap: `1792856742354782800` — вопрос, HTTP restart, ответ/продолжение без повторной правки; `1792857644468168300` — доработка, fixture acceptance и отдельный Apply. В обеих проверках actual native/Git tests, pinned receipts каждой VM, stop/cleanup, исходный Git сохранён. Ответы моделей scripted.
- Реальный Ollama web: `.local/parallel-real-browser/1792841288084319600/check-report.json`, 35.251 с, 13404 токена, 22.319 с пересечения worker VM, общий peak 2 CPU/1024 MiB, worker/combined clean replay, отдельный behavioral oracle в свежей VM. Результат оставлен pending human review; ничего не принято/применено. Первый actual Ollama DAG `1792841230278277700` некорректен и сохранён как failure. Это два простых сценария, не широкий benchmark.
- Guest Git — отдельный hash-pinned Debian snapshot profile, не host Git: sibling `examples/guest-toolchain/README.md`. `.local/native-git-packed-current/report.json` в sibling: текущие 137 тестов, 135 pass/2 Windows-only skip в каждом из baseline/replay; native/Git smoke и web/MCP subset тоже прошли, source hashes unchanged, owned cleanup подтверждён.
- Транспорт dependency bytes вынесен из повторно читаемого SDK config в bounded bootstrap stdin, чанки 64 KiB. Пакет детерминирован, hash-bound, root-readonly: максимум 20 MiB compressed/48 MiB expanded, 16 MiB/file, 2048 entries. Только фиксированные guest prefixes; нет extractall, hooks или выполнения third-party кода root. ABI/architecture/glibc проверки сохранены. Readiness проверяет combined bounds без VM/инференса.
- Проверки: приложение `unit-periodic-checkpoint-current.log` — 137, 135 pass/2 skip; runtime `unit-packed-runtime-final.log` — 140, 108 pass/32 opt-in skip. Отдельно actual VM: 25 native dependency проверок без skip (`packed-native-dependency-v3.log`), 13 Git проверок без skip (`packed-git-toolchain-real.log`), 3 payload проверки с реальным root refusal (`packed-bootstrap-refusal.log`).
- Сохранены неуспехи: `parallel-early-failure-v1.log` (Windows path separator в provenance); `parallel-native-toolchain-question-v1.log` (10055), v2 (старый bulky config исчерпал время); `packed-native-dependency-v1/v2.log` (обычный stdin для большого пакета timeout); sibling `native-git-full-current-final` (тесту отсутствовал ps); `native-git-full-baseline-v3` (parent source изменился во время прогона). Ни один не засчитан как успех и не удалён.
- Перед последним запуском сохранён `.local/backups/packed-runtime-20261024T161028.zip`; localhost:8765 и health проверены. Рабочие данные остаются 34 задачи/2 тега/1 исторический run, без тестовых записей; активных runs нет. Native/DAG не включались в рабочем argv ради демонстрации.

Не закрыты весь roadmap и платформенная приёмка: качество Gemma/чистое итоговое сравнение, реальная доработка большого native-проекта, MacBook Air M4/Mac→PC/backup, фактический workflow Claude/Codex. Windows-результаты не заменяют эти проверки.

## Реальная задача на текущем Store после удаления тегов

[Отчёт](docs/evaluations/2026-10-24-native-coding.md): воспроизведён дефект
goal_results с одинаковыми датами, но 12 настоящих Ollama попыток не завершили
исправление. Последняя guided-попытка также неуспешна и отдельно отмечена.
Все собственные VM закрыты; исходный Store и рабочая runtime-конфигурация сохранены.
Источник не подменялся искусственно сломанной версией. Baseline выбранного
20-файлового snapshot: 24 теста, 4 ожидаемых регрессионных failure.
Full-suite checker нового экспорта ещё не выполнялся: принятого экспорта нет.

Runtime regression: 148 unit (116 pass/32 opt-in skip), context real VM — 26/26;
scripted web/reviewer/revision/fixture Apply/stop и прежний реальный малый
calculator-сценарий прошли. Это проверка инфраструктуры, не успех большой задачи.
Этап 8 остаётся открыт. Рабочая база после отдельно разрешённого пользователем
сброса пуста; прежние сведения о 34 задачах и тегах выше относятся к истории.

## Аудит этапа 9 — 25 октября 2026

Документарная сверка текущего исходного кода и сохранённых свидетельств. Этот срез
уточняет прежние строки «4-node очередь», «расширенная проверка Windows» и
«hard-kill DAG открыт»; исторические выводы и неуспехи выше не переписаны.
В ходе аудита тесты, модели и VM не запускались; конфигурация и пользовательские
задачи не менялись. Запись ограничена ROADMAP.md и этим дополнением.

Исходный критерий этапа 9 сохраняется: несколько исполнителей соблюдают общий
бюджет, не перезаписывают чужие результаты и корректно освобождают ресурсы после
остановки. Наличие механизма и объём его проверки учитываются отдельно.

| Часть исходного этапа | Текущий код | Проверенное свидетельство и его граница |
|---|---|---|
| Специализации, зависимости, очередь | Sibling [scheduling.py](../multi-agent-system/src/scheduling.py): `SPECIALTIES`, `parse_plan`, `Scheduler`; [parallel_development.py](../multi-agent-system/src/parallel_development.py): scoped request и отдельный worker | Роли backend/frontend/tests/documentation и DAG до четырёх областей реализованы. [test_scheduling_queue.py](../multi-agent-system/tests/test_scheduling_queue.py) проверяет четыре независимых узла при отдельном дефиците слотов, CPU и RAM; diamond, ожидание обоих prerequisite и блокировку потомков при их отказе. Это unit, без VM/инференса; новые VM receipts используют независимые backend-области |
| Общие ресурсы, инференс и время | `Budget.lease/check`, `SharedInference`, `ModelView`; web [parallel_web.py](../multi-agent-system/src/parallel_web.py) передаёт фиксированные общие limits | Queue receipt ниже подтверждает actual peak и резерв до boot/через clean replay. [test_scheduling.py](../multi-agent-system/tests/test_scheduling.py) проверяет deadline и сериализацию inference/учёт токенов. Успешный receipt с полным token accounting не является проверкой исчерпания бюджета при живой очереди |
| Изоляция областей и совместимость | `ParallelRun.work/ancestors`, `merge_export`, [parallel_artifacts.py](../multi-agent-system/src/parallel_artifacts.py); полный layout проверяется до VM | [Scheduler-срез](docs/evaluations/2026-10-25-parallel-scheduler.md): hash-checked binary merge, layout-коллизии и readonly dependency в настоящей VM. Queue receipt: неизменность чужих файлов, worker/combined tests и clean replay, отдельный behavioral oracle. Конкретный четырёхузловой fan-in этими VM fixtures не покрыт |
| Остановка и освобождение | Lease включает весь worker lifecycle; `mark_cleanup_failed` удерживает резерв и запрещает queued worker; supervisor повторяет owned cleanup | [test_parallel_cleanup_budget.py](../multi-agent-system/tests/test_parallel_cleanup_budget.py), реальная VM с отказом stop из scheduler-среза; новый web receipt — Stop и hard-kill при двух живых VM. `test_scheduling_queue.py` отдельно проверяет обе формы отмены с ожиданием двух cleanup и без допуска очереди |
| Сохранение DAG при recovery | [parallel_web.py](../multi-agent-system/src/parallel_web.py): полный task mapping, `publish_checkpoint(task_updates=...)`, восстановление того же DAG; Store/SQLite — атомарные статусы/receipt | Новый web receipt: sealed checkpoint/версии сохранены, HTTP restart не возобновляет работу; явное recovery использует все прежние задачи и очищает старые VM до нового supervisor. Это конкретная аварийная граница этапов 6/9, не вся crash-матрица |

### Точные новые свидетельства

[Отчёт четырёх областей и DAG recovery](docs/evaluations/2026-10-25-four-worker-dag.md)
связывает следующие локальные receipts. Они игнорируются Git и не копируются;
SHA-256 ниже относится к прочитанным байтам, а не к новому запуску проверки.

- Sibling [queue receipt](../multi-agent-system/.local/parallel-queue-verification/1792926040550215400/check-report.json):
  `ok: true`, `real_llm_inference: false`, `actual_vm_peaks` = 2 VM / 2 CPU /
  1024 MiB, `queued_start_after_cleanup: true`. В итоговом budget пустые
  `active_workers`/`unreleased_workers`, reserved CPU/RAM равны нулю;
  `cleanup_errors: []`, source/HEAD/index/worktree сохранены.
  [test_parallel_queue.py](../multi-agent-system/tests/test_parallel_queue.py)
  связывает 10 интервалов worker/integration VM, включая свежие clean replay VM,
  с квитанциями boot/stop; отдельный одиннадцатый VM oracle проходит с exit 0.
  Для всех 11 VM подтверждено отсутствие после cleanup. Это четыре области при
  двух слотах, а не четыре одновременно работающие VM.
  SHA-256: `04f370b49ece619457a7fb91c3ea5c95c816e68b3fa80279b4b51959a144d6b1`.
- Kaibain [web hard-kill receipt](.local/parallel-browser/1792926096619548600/check-report.json):
  `ok: true`, `real_llm_inference: false`, `scopes: 4`, `workers_limit: 2`.
  Проверены две работающие VM перед kill, неизменные версии root/aggregate/workers,
  полный checkpoint и четыре ID задач, отсутствие автоматического cleanup/resume
  после HTTP restart, явное recovery, tests/clean replay, binary fixture и Stop.
  В `old_vms_absent_before_new_supervisor` у обеих старых VM `running: false`,
  `missing: true`. Сразу после kill было только `running: false` без доказательства
  отсутствия; отсутствие подтверждено после явной cleanup до нового supervisor.
  Сторонний процесс выжил. [Браузерная оснастка](tests/browser_parallel_development_check.py)
  проверяет реальный MCP/supervisor/VM путь, не модельное качество.
  SHA-256: `471ac4b12cd068c8ad9d649db3a198fb947400d4d073c2f1c55bb0b7504be949`.

Сохранённые полные журналы `.local/four-dag-main-unit.log` и sibling
`.local/four-dag-runtime-unit.log` заканчиваются соответственно 172 проверками /
2 skip и 320 проверками / 37 opt-in skip без ошибок. Это исторический результат
данного среза, не повтор тестов в ходе аудита и не подтверждение последующих правок.

Первый web-прогон `.local/parallel-browser/1792925855147013800/` и журнал
`.local/parallel-four-hard-kill.log` сохраняются как неуспех оснастки:
`No blocked scripted request after a worker write`. Четыре области завершились,
но до точки kill проверка не дошла; этот прогон не засчитан как crash/recovery.
Ранние binary/transport/Windows socket failures и некорректный real Ollama DAG
остаются в прежних отчётах. Новые успехи их не заменяют.

### Что конкретно остаётся

1. **Проверка зависимостей в интеграции.** Diamond и отказ prerequisite уже
   реализованы и покрыты unit; readonly consumer проверен отдельно на двух узлах.
   В изученных VM/web receipts нет сочетания четырёхузлового fan-out/fan-in,
   защищённой передачи результатов обоих prerequisite и отказа одного из них.
   Следующая scripted-проверка должна подтвердить отсутствие запуска зависимого
   worker до обоих успешных cleanup, блокировку потомка при отказе и сохранность
   чужих байтов при объединении. Это проверка существующих зависимостей/владения,
   не требование нового scheduler или дополнительных ролей.
2. **Проверка исчерпания общего бюджета в интеграции.** Отдельные admission limits,
   deadline, inference gate и token accounting покрыты кодом/unit; успешная очередь
   и остановка проверены в VM/web. В рассмотренных receipts ещё нет исчерпания
   общего времени/токенов при двух активных и оставшихся queued worker. Открыта
   проверка прекращения допуска/инференса, owned cleanup и достоверного финального
   резерва при cleanup failure в таком сочетании. Нужны scripted ответы, не LLM;
   это граница исходного критерия общего бюджета и освобождения ресурсов.
3. **Оставшиеся аварийные границы относятся к этапу 6.** Новый hard-kill закрывает
   конкретный DAG-сценарий после sealed checkpoint, не crash до первой квитанции
   или во время export/cleanup, не потерю питания. Параллельная разработка
   pre-first-checkpoint fixture этим аудитом не оценивается и не объявляется
   завершённой. Отсутствие checkpoint не требует восстановления неизвестных действий.
4. **Отдельные условия приёмки.** Mac/aarch64 microVM и реальный Mac → PC требуют
   отсутствующего здесь окружения. Расширенная модельная приёмка и автономная
   доработка большого native-проекта ожидают снятия LLM-паузы (этапы 7/8 и общая
   приёмка); прежний малый двухworker real Ollama успех остаётся действительным
   только в своих границах. Windows scripted receipts не заменяют эти проверки,
   но их отсутствие не доказывает дефект очереди. Workflow Claude/Codex — отдельный
   клиентский gate, не недостающий механизм этапа 9.

**Граница реализации:** нового отсутствующего механизма исходного этапа 9 при
этой сверке не найдено. Подтверждённое ограничение — каждый worker обязан пройти
полную фиксированную команду тестов; тесно связанные изменения объединяются у
одного владельца. Scoped-команды пока не реализованы, но исходный критерий их
не требует. Четыре одновременные VM, параллельный inference, отдельные модели для
каждой специальности и переключение рабочего argv также не добавляются к приёмке.
Рабочее подключение остаётся последовательным, validator OFF; этап 9 и весь roadmap
не объявляются завершёнными.

## Исполняемая проверка после документарного аудита — 25 октября 2026

Последующие [crash-проверки](docs/evaluations/2026-10-25-export-crash.md) закрыли
три конкретные Windows web/MCP/VM границы: первый неполный экспорт, экспорт после
записи и kill финальной очистки clean VM. Partial export не становится checkpoint;
при наличии receipt Resume сохраняет подзадачу/профиль и повторяет clean replay.
Без receipt UI/API отказывают; отдельного UI cleanup terminal-run ещё нет.
Фикстурная очистка в этом случае не объявляется готовой продуктовой функцией.

[Новые runtime-проверки](docs/evaluations/2026-10-25-fanin-budgets.md) подтвердили
четырёхузловой fan-in, protected dependency bytes, отказ prerequisite и отсутствие
допуска зависимого worker. Они же воспроизвели дефект общей token-квоты: ошибка
отдельного worker не останавливала очередь и теряла исходную причину. Это уточняет
прежний документарный вывод «новый недостающий механизм не найден»: наличие
механизмов не доказало корректность этой аварийной ветки.

Исправлены общий fence и сохранение причины даже при быстром завершении группы.
Четыре регрессии проходят; три настоящих VM-сценария подтверждают tokens, исходный
deadline и live VM/retained reservation при cleanup failure. Fan-in: 6/6, budget VM:
3/3; приложение: 172/2 skip, runtime: 333/42 skip, без ошибок. Полный web/MCP-путь
глобального исчерпания, безопасное UI cleanup без recovery и платформенная/модельная
приёмка остаются открытыми. Рабочее подключение не переключалось, LLM-пауза соблюдена.

## Пользовательская очистка terminal-run — 3 ноября 2026

Следующий инженерный пробел закрыт отдельным человеческим действием cleanup,
включая отсутствие checkpoint/устаревший контекст. Оно использует frozen argv,
не создаёт новую попытку и не меняет task/run versions или exports. SQLite lease
блокирует launch/recovery/другую очистку; failed/interrupted сохраняют fence до
явного успешного повтора. Old PID/groups не сигналятся, новые helper scopes
имеют собственный gate/receipt. Backup отказывает при активной очистке.

[Свидетельства](docs/evaluations/2026-11-03-operator-cleanup.md): 24 новых теста,
полные наборы 196/2 skip и 333/42 skip, настоящий Windows web/MCP/VM first-export
crash → UI cleanup, HTTP restart, SDK absence и dark/mobile/keyboard view.
Первоначальные fixture/snapshot failures сохранены и не подменены успехом.
Это актуализирует прежний вывод «UI cleanup пока нет», не переписывая старую проверку.

Открыты полный web/MCP путь глобального исчерпания бюджетов и hard kill самого
владельца новой cleanup при живой VM. Unit dead-owner/fixed-adapter не объявляется
последним VM-сценарием. Mac/клиентская и реальная LLM-приёмка остаются открытыми;
пользовательская пауза реальных моделей соблюдена, весь roadmap не закрыт.

## Token/time budget через весь web/MCP путь — 4 ноября 2026

[Новые свидетельства](docs/evaluations/2026-11-04-web-budgets.md) закрывают ранее
названный локальный gate: четыре области / два слота, реальные HTTP/MCP/supervisor/VM,
запрет queued admission и generation, понятная причина `token_budget`/`time_budget`,
полный checkpoint и явное recovery после HTTP restart с прежними задачами/профилем.
После восстановления проходят tests/clean replay и отдельный свежий VM oracle.
Старые VM отсутствуют до нового supervisor; приёмка/Apply не выполнялись.

Интеграция выявила и исправила три дефекта: web progress путал internal cancellation
с user stop; отмена в finally могла потерять worker report/cleanup exception;
координатор с оборванным RPC мог не отметить disconnect. Новый cleanup await
дожидается stop и сохраняет ошибки до release lease; MCP lifespan завершает только
собственную identity при нормальном EOF. Forced kill всё ещё использует expiry.

13 + 8 + 4 новых проверок и полные наборы 200/2 skip и 354/42 skip прошли.
Сохранены первоначальный lost worker report, неизвестный `supervisor_lost`,
зависшая presence и неудачный SDK boot в первом time fixture. Последний не
засчитывается как успешная deadline-проверка; чистый time-повтор прошёл отдельно.
Модельные ответы/метрики scripted; реальные LLM не запускались.

Открыты обрыв владельца operator cleanup при живой VM и итоговая сверка остальных
критериев; Mac/aarch64, Mac → PC и реальные клиентские/модельные gate остаются
неподтверждёнными. Рабочая конфигурация последовательная, validator OFF.

## Дополнение 4 ноября: обрыв HTTP-владельца самой очистки

[Новые свидетельства](docs/evaluations/2026-11-04-cleanup-owner-crash.md) закрывают
названную выше локальную Windows-границу. Реальная VM создана SDK и держится в
отдельном owned scope; HTTP cleanup helper остановлен fault barrier до SDK stop.
Подтверждённый native handle kill HTTP-интерпретатора завершает его Job helper,
но не VM/holder или посторонний процесс. Running lease и active receipt остаются;
после restart UI показывает interrupted и запуск/recovery остаются заблокированы.
Явный UI retry создаёт новую action, реальный SDK stop подтверждает отсутствие VM
до teardown holder, свежая квитанция снимает fence. Старое interrupted действие,
задачи/run/версии/exports и исходный Git сохранены.

Исходный terminal-run — trusted fixture, не coding/MCP/model цикл. Это дополняет
настоящий first-export/MCP → UI cleanup от 3 ноября, не заменяет его. Две ранние
ошибки harness (непубличный pid и отсутствующий bundled Chromium) сохранены;
единственная VM второй попытки очищена. Финальный повтор и отдельный предыдущий
успех подтверждены, production-код/конфигурация не менялись. Полные unit-наборы
не повторялись; 200/354 выше — прежние результаты. Mac/POSIX, питание и реальные
модели/клиенты по-прежнему не подтверждены.

## Дополнение 4 ноября: итоговая сверка и прямой gate сохранённого проекта

[Чеклист каждого этапа](docs/ROADMAP_CHECKLIST.md) отделил подтверждённые механизмы
от исходных gates. Найден один проверяемый локально пробел: прежние Store/mock
и dormant UI проверки не доказывали успешный новый запуск после настоящего restart.
[Новая сквозная проверка](docs/evaluations/2026-11-04-saved-project-restart.md)
закрыла его: профиль введён через UI один раз, HTTP-интерпретатор завершён,
новый процесс открывает ту же БД, новая цель использует saved project/version.
HEAD изменён между сохранением и запуском и правильно закреплён без ручного ввода.
Настоящие coordinator/worker MCP, реальная VM, failing baseline → исправление →
tests/clean VM replay → review прошли. Обе VM отсутствуют до teardown HTTP Job,
оба агента disconnected, сохранённый профиль/Git и production config неизменны.

Ответы HTTP scripted, acceptance/Apply не выполнялись. Первый harness KeyError
на непубличном поле presence сохранён как неуспех; final retry прошёл.
Production-код не менялся, полного повторного unit-прогона нет. После этой
сверки не найдено ещё одного необходимого функционального изменения исходного
scope; полный roadmap остаётся открыт из-за фактических Mac/Mac → PC,
настоящих внешних CLI и модельной приёмки этапов 7–9. Пауза LLM действует.

## Дополнение 4 ноября: LLM разрешены снова, реальные проверки

Пользователь разрешил ограниченные реальные прогоны на isolated projects и
подтвердил отсутствие доступа к Mac. [Новые reports](docs/evaluations/2026-11-04-real-review.md):
Gemma3 seed calibration 4/4 valid без ложных возвратов; Gemma4:e4b — 3/4 valid,
один противоречивый invalid на исправном коде. Это не устраняет исторический
false positive v5 и не является широкой оценкой. Qwen3:8b comparison завершился
1/5 planned successes в обоих режимах, 4/5 запущено: первый revision round failed,
второй не запускался. Шесть model_no_progress/token_budget, полный token accounting;
WinError/socket failures этого сравнения нет. Отдельный qwen3.5:9b bugfix pilot
успешен без review, с review — developer no-progress до валидатора.

32 VM отсутствуют по SDK. Validator OFF/production argv и пользовательские данные
не менялись. Новый `--prepared-state` evaluation/calibration покрыт 4 unit (3/1skip),
полный runtime 358/43 skip, exit 0; main application code без новых изменений.
Новая реальная клиентская попытка: Claude не найден, Codex 0.166.0 сделал две
попытки MCP, обе отказали native trust/approval, успешных 0; harness pending/2.
Конфигурация сохранена, owned cleanup подтверждён. Дальнейшая работа — общий
feedback агенту о прочитанном/изменённом и tests/finish, не подсказки под fixture,
не silent budget increase или автоматический code fix. Качество ещё не принято.

## Дополнение 4 ноября: coding-loop V3

[Изменения и отдельные результаты V1/V2/V3](docs/evaluations/2026-11-04-workflow-feedback.md):
явные workflow facts, cache-aware schema и восстановленные последние chat pairs.
Полный runtime 370/43 skip, exit 0. V1 дал 2/5 и 3/5, V2 — 3/5 и 3/5;
прерванный partial V2 исключён, причина неизвестна, owned cleanup сохранён.
Текущий V3 — **5/5 в обоих режимах**, 10/10 hidden oracles, включая revision,
все 30 VM отсутствуют. Исходные commits и бюджеты прежние. Последующая scripted
web/MCP/VM проверка с HTTP restart прошла, обе VM отсутствуют, данные сохранены.
Одна малая выборка не доказывает пользу review или широкую успешность. Следующая
реальная native-задача — readiness aggregate false/malformed, metadata-only
counterexample подтверждён, model fix ещё не выполнен. Production argv/validator
OFF не менялись, человеческой приёмки/Apply нет. Mac недоступен.

Дополнительно [native readiness case](docs/evaluations/2026-11-04-native-readiness.md):
семь protected contract tests и новый фиксированный checker profile; четыре unit
профиля и полный runtime 374/43 skip прошли. Два actual Ollama attempts на точных
исходниках Kaibain воспроизвели дефект внутри VM, но остановились context_budget
до правки (qwen3:8b 6701 токен, qwen3.5:9b 3632). Обе VM отсутствуют; нет export,
clean replay или полного independent suite этого case. Этап 8 не закрыт.

## Дополнение 4 ноября: полный context bound

[Context allocation](docs/evaluations/2026-11-04-context-allocation.md): общий request_shape,
квота exact cache в пределах 128..4096, согласованные schema/guard, metadata чтения
большого пути и enum_sets в signatures. Точные replay и 7 новых tests; полный runtime
381/43 skip, exit 0, прежние reservation/contract boundaries сохранены. Все ранние
unit failures сохранены. Финальная scripted web/MCP/restart проверка прошла, обе VM
отсутствуют; это не реальное модельное принятие.
Промежуточный real qwen3:8b attempt выбрал нужный путь, затем context_budget на
catalogue outcome, 7412 tokens, единственная VM отсутствует. Это основание enum_sets.
После enum_sets попытка остановилась до inference/VM: Ollama недоступна; её запуск
отклонён automatic policy review. Следующий real run требует запуска пользователем.
Исторический V3 source сохранён отдельно с прежними hashes, failures не подменяются.
Production argv/validator OFF и пользовательские данные сохранены; приёмки/Apply нет.

Последующая документарная сверка исправила текущий roadmap: LLM-пауза уже снята,
report-order больше не воспроизводится как актуальный дефект, а 5/5 comparison
относится к V3, до context allocation/enum_sets. Исторические свидетельства не
переписаны и на новую версию не распространены. Отдельный read-only `/api/tags`
снова недоступен; отклонённый запуск Ollama не повторялся. Для продолжения actual
inference требуется внешний запуск сервиса пользователем.

## 4 ноября: native tools/render quota и ARM64 guest Git

Реализован opt-in native developer transport, строгая observed schema, exact
HTTP/controller payload и bounded zero-generation render reallocation. Полный
runtime **445, 401 pass / 44 skip**, pip check passed; 31 focused check.
Первый полный прогон 423/6 errors сохранён: исправлен scripted mock lookup.
Scripted web/MCP/VM/restart прошёл, обе VM missing; source/profile/Git сохранены.
[Receipt и границы](docs/evaluations/2026-11-04-native-tools.md). Реальные native
Qwen3.5 attempts: context_budget до render fix и token_budget после; service
outage исключён из качества. Готового patch/full guest suite/replay нет.

[ARM64 guest Git](docs/evaluations/2026-11-04-arm64-guest-git.md): реальные pinned
Debian bytes/ELF/closure прошли static verification, amd64 manifest/wrapper
прежние. 31 focused (29 pass/2 skip); дополнительно 2 real x86_64 VM tests
прошли, 3 VM missing по SDK. Mac/aarch64 исполнения нет. Timeout/504 первой
загрузки index сохранены. Рабочий adapter sequential/json, validator OFF;
пользовательские цели, acceptance/Apply и model downloads не выполнялись.

[Консолидация десяти предшествующих failures](docs/evaluations/2026-11-04-native-readiness-resumed.md)
сохранила все outcomes и hashes; 1209 artifacts проверены. Последние native
experiments не переносят исторический V3 5/5 на текущую версию и не закрывают
этапы 7–9, Mac/Mac→PC или штатные native client gates.

## 4 ноября: version-bound FILE_TOO_LARGE receipts

Добавлены bounded metadata факты (4 / 768 bytes) и одинаковые schema/wire/guard
правила: whole read после подтверждённого size/hash не повторяется при том же SHA,
range доступен; смена версии возвращает возможность read. Metadata не считается
прочитанным source или успехом. Полный runtime **452, 408 pass / 44 skip**;
семь новых checks. Первый unit error initial-state fixture сохранён и исправлен
optional history lookup. Offline replay старого actual журнала исключает три
повторных full read, сохраняя ranges, без inference/VM.

[Actual evidence](docs/evaluations/2026-11-04-oversized-read.md): native-tools —
invalid_model_response (4 steps/16179 tokens), JSON default — context_budget
(16 steps/47036 tokens). Во втором whole readiness.py read только один;
продолжились повторы ranges/других файлов, после list_files immutable context
16701 bytes даже с source128. Caps правильно остановили запрос. Правки нет,
независимый full guest suite/clean replay model export не запускался.
Обе VM SDK-missing; source/production config и human controls сохранены.
Сравнение качества по одному разному protocol run не заявляется; этапы открыты.

## Ночь 4–5 ноября: zero-byte proofs, enum subsets и Coder

Поддержаны реальные empty source reads без выдуманных координат; общий pool
empty/oversized 4/768 bytes, current SHA/eviction и прежний repeat stop.
Prompt menu losslessly разделяет enum supersets/exclusions, сохраняя types/order
и полный format; только boilerplate уменьшен на 143 bytes. **471 checks,
427 pass / 44 skip**, 19 новых. Все 16 старых offline projections <=16384 bytes,
последний 16359. До сокращения boilerplate последний 16502 сохранён отдельно.

[Три actual trials и хеши](docs/evaluations/2026-11-05-source-observations.md):
Qwen3.5 thinking — token_budget/48156 tokens; Qwen3 thinking — неверная правка,
failed checks/context_budget/27145; согласованный Qwen2.5-Coder trial —
step_budget/50806, без правки. Пользователь 5 ноября разрешил download 4.7GB
и один trial: pull exit 0, tags/show/digest проверены; дополнительного Coder
инференса не было. Три VM SDK-missing, production config/source сохранены.

Коррекция гипотезы: реальный __init__.py содержит 48-byte docstring. Zero-byte
исправление — общий edge case, не причина наблюдённого цикла этого модуля.
Coder fresh range вернул 18 строк, cache сохранил 11 fragments перед следующими
запросами; guard не считал page полностью доступным. Причинный эффект будущего
изменения доставки source ещё не проверен. Готового model export/full independent
native replay нет; stage 7–9, Mac/Mac→PC и внешние client gates не закрыты.
