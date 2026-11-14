# Текущий статус и передача работы

Обновлено 5 ноября 2026 года. Краткий актуальный срез, не журнал всех запусков.
Правила — в [AGENTS.md](../AGENTS.md), карта кода/проверок — в
[CONTRIBUTING.md](../CONTRIBUTING.md), полный объём — в [ROADMAP.md](../ROADMAP.md).

## Текущие ограничения пользователя

- **4 ноября пользователь разрешил возобновить ограниченные реальные LLM-тесты**
  на изолированных проектах. Не скачивать модели и не менять рабочий адаптер ради
  прогона. Scripted проверки по-прежнему отделять от реального инференса.
- **Доступа к Mac нет**, подтверждено пользователем 4 ноября. SSH-хост не
  использовать как предполагаемый Mac; платформенная приёмка остаётся открытой.
- Приоритет: macOS, затем Windows. Пока фактические проверки выполнены на Windows;
  наличие CI или кода для Mac не означает платформенную приёмку.
- Рабочее подключение исполнителя последовательное, validator OFF. Не переключать
  его на DAG/native/validator ради демонстрации. Машинную конфигурацию читать локально.
- Пользовательские цели не запускать, не принимать и не применять ради тестов.
- 5 ноября отдельно разрешены и выполнены загрузка `qwen2.5-coder:7b` и один
  bounded sandbox-прогон. Модель установлена, trial завершился step_budget без
  правки; рабочий adapter не переключался. Это не разрешение скачивать другие модели.

## Условие продолжения

После явного возобновления цели read-only `/api/tags` подтвердил доступность
Ollama и установленных моделей. Предыдущая блокировка сервиса снята внешним
изменением; отклонённый автоматический запуск не повторялся. Реальные bounded
native attempts возобновлены. Mac/Mac→PC и штатная native-клиентская приёмка
остаются открытыми; unit/scripted evidence не заменяет их.

Все **десять возобновлённых native attempts terminal и failed**: 1 context_budget,
4 token_budget, 2 step_budget, 1 model_no_progress, 2 model_output_limit.
Первые шесть — initial/original/compact/empty-search версии; последние четыре —
отдельное stdout-задание. Output 1024/2048/4096, бюджеты 32768/65536 и последний
Qwen3.5 thinking=true различаются и не образуют одинаковый quality comparison.
Первый Qwen записал неуспешную правку; готового model export, full guest suite и
clean native coding replay нет. Все десять VM missing по сохранённым SDK receipts.
[Результаты](evaluations/2026-11-04-native-readiness-resumed.md) и
[новый манифест](evaluations/2026-11-04-native-readiness-resumed.json) закрывают
пробел в evidence, а не этап 8. Production config/validator OFF не менялись.

## Последняя завершённая работа

14 ноября подготовлен публичный релиз на GitHub: добавлены `LICENSE` (MIT),
`SECURITY.md`, `README.en.md`, предупреждение об экспериментальном статусе и ссылка
на sibling-репозиторий в README. Проверено содержимое отслеживаемых и новых файлов
и история Git на секреты, токены, ключи и локальные пути пользователя — находок нет.
Затем по решению пользователя `docs/evaluations/` исключён из Git (`.gitignore`,
файлы сохранены локально; ссылки на них в документации на GitHub не открываются),
шаблон `/Users/yourname` заменён на `/path/to/kaibain`, LAN-адреса в README —
на `PC_LAN_IP`, тестовые адреса — на `remote-ollama`/`kaibain@localhost`.
Полный unit-прогон Windows: 200 tests, OK, 2 skip. VM/LLM-прогоны не запускались.

Ранее подготовлен `.gitignore` для публичного репозитория: локальные настройки/MCP,
SQLite с sidecar-файлами, окружения, секреты, приватные backup и артефакты исключены.
Проверены 73 исключаемых и 23 разрешённых пути; ошибок нет. Уже отслеживаемых
файлов, совпадающих с правилами, нет. Публичные примеры и curated evaluations
сохранены; это проверка правил Git, не аудит содержимого или истории на секреты.

Незавершённая работа в sibling: доставка полного свежего `source_observation`
в контекст модели и согласование с guard/cache. Первый полный unit-прогон этой
правки: 471 checks, 2 errors / 44 skip; лог
`.local/source-delivery-first-unit-1793828381656.log`. Новые focused checks:
28 tests, 27 pass, один test failed в девяти subcases некорректных параметров
range/search. Следующий шаг — исправить валидацию и согласовать legacy context
tests, затем повторить проверки. Приведённые ниже 471/427/44 относятся к
предыдущему завершённому состоянию, не к этой незавершённой правке.
Новые VM/LLM-прогоны для изменения `.gitignore` не запускались.

Добавлены доказательства настоящих zero-byte source reads, общий с oversized
receipts лимит 4/768 bytes и lossless subset-sharing enum списков. Пустой файл
не превращается в выдуманную строку; новый SHA/eviction снимает ограничения.
Полный runtime **471, 427 pass / 44 skip**, включая 19 новых checks.
Только redundant contract boilerplate сокращён на 143 bytes; task/source/
outcomes и полная decoding schema не обрезаны. Все 16 старых JSON projections
теперь <=16384 bytes; последний 16359, без VM/инференса в этом replay.

Реальные trials на той же scoped native-задаче пока failed: Qwen3.5:9b с thinking
— token_budget, Qwen3:8b с thinking — неверная правка/failed checks/context_budget,
разрешённый Qwen2.5-Coder:7b без thinking — step_budget, только чтения. Все три
VM missing по SDK, source/production config сохранены. Готового model export и
полного независимого guest suite/clean replay нет.
[Источник, receipt и границы](evaluations/2026-11-05-source-observations.md).

Исправление прежнего вывода: реальный `src/kaibain/__init__.py` не пустой —
48 bytes с docstring. Zero-byte поддержка исправляет общий крайний случай,
а не объясняет повторы этого файла. В свежем Coder-журнале range вернул 18 строк,
но на следующем запросе сохранялись 11 fragments; guard правильно не считал
весь page доступным. Следующий конкретный вопрос — согласование fresh tool
output с доступной source quota/видимостью данных. Не увеличивать caps ради
ложного успеха и не выдавать hypotheses о причине модельного поведения за факт.

## Срез oversized metadata от 4 ноября

Исправлены повторные whole-file запросы после доказанного FILE_TOO_LARGE:
bounded SHA/size receipts, одинаковые wire/schema/guard, ranges доступны,
новый SHA снимает запрет. Metadata не считается source или готовой работой.
Полный runtime **452, 408 pass / 44 skip**; семь новых checks. Offline replay
старого actual журнала исключает три повторных full read без запрета ranges.
Реальные Qwen3.5 attempts всё ещё failed: native — invalid arguments на шаге 4
(16179 tokens), JSON default — context_budget на шаге 16 (47036 tokens), без patch.
Обе VM missing; source/config сохранены. [Receipt и ограничения](evaluations/2026-11-04-oversized-read.md).

Наблюдённый тогда bottleneck: JSON повторяет ranges и чтения `__init__.py`;
после list_files immutable payload при source128 составляет 16701 bytes.
Нужно улучшить обработку подтверждённых empty-source observations/повторов и
lossless представление metadata/schema, сохранив caps и исходные сведения.
Улучшения контроллера не заменяют успешный model export и не закрывают gate
автономной разработки. Пределы и исходный scope сохраняются.

## Предыдущие срезы и их проверки

Документарно сведены десять native failures с точными report/preflight/source и
controller snapshots, before-run digest только у новых четырёх, SDK absence и
логами. Старые failures и evaluation JSON сохранены. Только stdlib чтение/хеши,
без новых тестов, моделей и VM.

Opt-in inference profiles: default **32768/1024**, явные **65536/2048** или
**65536/4096**; input cap **16384 UTF-8 bytes**, num_ctx **16384** сохранены.
Последний сохранённый полный runtime профилей — **396, 353 pass / 43 skip**, OK,
sibling .local/inference-profiles-4096-unit.log. High-profile web/MCP/restart
**1793814164097148200** прошёл с scripted HTTP и настоящими VM; обе missing,
агенты disconnected. Fixture связан с архивом before-native-tools по точному хешу.
Предыдущие 387 unit/empty-search web **1793812893840748800** тоже положительные,
но actual empty-search model run дал model_no_progress. Это engineering evidence,
не LLM quality. [Сводка и источники](evaluations/2026-11-04-native-readiness-resumed.md).

Реализован opt-in `--developer-protocol native-tools`: один вызов, полная observed
schema, прежние broker/CAS/VM и truthful tool history. Render-only overhead теперь
уменьшает source quota до генерации, без роста лимитов или повторения операций.
Полный runtime **445, 401 pass / 44 skip**; включая 31 native tools checks и
ModelView forwarding/probe ownership. `pip check` без конфликтов зависимостей.
Scripted web/MCP/restart с настоящими worker/clean VM прошёл. Реальная Qwen3.5
после исправления render accounting всё ещё не пишет код: 13 steps / 48244 tokens,
token_budget; прежний context_budget сохранён отдельно. Дефект readiness не исправлен.
[Проверки и границы](evaluations/2026-11-04-native-tools.md).

ARM64 guest Git реализован: реальные Debian bytes/ELF/closure проверены статически;
amd64 manifest/wrapper прежние, два x86_64 VM-теста прошли, три VM SDK-missing.
[Свидетельства](evaluations/2026-11-04-arm64-guest-git.md). Mac/aarch64 выполнение
остаётся открытым. Рабочий argv последовательный, validator OFF, `json` default.

Историческая предшествующая версия:
Исправлен бюджет контекста крупных проектов: source cache получает оставшееся
место полного запроса, guard/schema используют ту же квоту; большие read_file
пути возвращают metadata и не вынуждают выбрать посторонний файл. Повторяющиеся
списки путей в signatures передаются общей exact enum_sets таблицей без изменения
decoding schema. Тогдашний полный runtime **381, 338 pass / 43 skip**, exit 0.
[Новые проверки и все неуспехи](evaluations/2026-11-04-context-allocation.md).
Реальная промежуточная попытка уже выбрала нужный readiness.py, но остановилась
на catalogue context до записи; VM отсутствует. Тогда после enum_sets inference
остановился до запуска: Ollama была недоступна, автоматический запуск отклонён
policy review. Позднее внешний запуск позволил десять actual attempts выше;
это больше не текущая блокировка. Рабочая конфигурация сохранена.
Финальная версия прошла отдельный scripted web/MCP/restart цикл с worker/clean VM
checks; обе VM отсутствуют, агенты отключены, source/profile/HEAD/index сохранены.
Screenshot просмотрен. Это integration evidence, не actual inference.
Заключительная объединённая команда hash/SQLite/health verification также отклонена
automatic policy review (`blocked by policy`); повтор не выполнен. Ранее sealed
receipts/hashes, полный unit и terminal web proof проверены отдельно; не заявлять
отклонённую команду как успешную новую проверку.

Тогдашний отдельный read-only probe `/api/tags` также был неуспешен; это
историческое наблюдение до возобновления выше. Запуск сервиса не повторялся.
Уточнён roadmap: LLM-пауза снята,
report-order — историческая уже исправленная задача, а 5/5 comparison относится
к V3 и не подтверждает actual inference после context allocation/enum_sets.

Исправлен контекст coding loop: явные факты операций/проверок, cache-aware schema
и последние две пары assistant/action → user/tool result. Source cache и полный
operator journal сохранены; bounds/guards/clean replay не ослаблены. Полный runtime:
**370, 327 pass / 43 skip**, exit 0. Настоящий исходный малый Ollama suite V3:
**5/5 без review и 5/5 с review**, включая доработки; отдельные hidden oracles
прошли все 10 attempts. Полный token accounting, прежние лимиты, все 30 VM
отсутствуют по SDK. [Отчёт и хеши](evaluations/2026-11-04-workflow-feedback.md).

Результаты предыдущих версий сохранены отдельно: V1 — 2/5 и 3/5, V2 — 3/5 в
обоих режимах. Прерванный первый V2 с неизвестной причиной исключён из сравнения;
7 VM очищены только после подтверждения отсутствия controller. Отдельный
Qwen3.5 revision pilot не прошёл. Успех V3 на одной малой curated выборке не
доказывает широкое качество или пользу валидатора и не закрывает этапы 7–9.

Тогдашняя V3 также прошла отдельную web/MCP/scripted HTTP integration: saved profile,
настоящий HTTP restart, worker/clean checks, обе VM отсутствуют, агенты отключены,
source/HEAD/index/profile сохранены. Screenshot просмотрен. Это не model proof.
Production runtime не заменён, validator OFF; пользовательские цели не запускались.

Следующая настоящая native readiness-задача проверена двумя ограниченными
Ollama attempts (qwen3:8b и qwen3.5:9b). Baseline в VM воспроизвёл дефект;
оба developer выбрали посторонний full-read путь и остановились на context_budget
до записи. Обе VM отсутствуют, source/config сохранены. Защищённый контракт и
fixed-case checker подготовлены, но clean replay/full application acceptance
этого case не выполнялись. [Свидетельства](evaluations/2026-11-04-native-readiness.md).

Возобновлены реальные LLM-проверки на Windows: seeded Gemma3 calibration 4/4 valid,
2/2 дефектных обнаружены, 0 false positives; Gemma4:e4b — 3/4 valid, один invalid
на исправном коде. Все 16 VM отсутствуют. Сравнение qwen3:8b с/без review завершено:
в каждом режиме 1/5 запланированных успехов (4 попытки, 1 не начата из-за провала
round 0). Шесть failures — model_no_progress/token_budget, не socket failure.
Все 12 SDK VM отсутствуют; учёт токенов полный. Качество разработки не принято.
Отдельный qwen3.5:9b bugfix pilot: без review прошёл hidden oracle, с review
developer застрял на чтениях до валидатора (model_no_progress). Все 4 VM отсутствуют.
[Свидетельства](evaluations/2026-11-04-real-review.md).

Реальный Codex 0.166.0 запущен на isolated fixture: 0 успешных MCP-вызовов,
native trust/approval gate, harness pending/2. Config unchanged, owned cleanup
подтверждён. Claude по-прежнему не обнаружен. Штатный trust требует пользователя.

Свежий non-inference probe внешних клиентов: Claude не найден в предусмотренных
местах; Codex 0.166.0, subscription login и нужные CLI flags доступны. MCP/model
access/native trust не проверялись, исторический approval gate 0.164.0 не объявлен
ни подтверждённым, ни снятым в новой сборке. Подключения не менялись.
[Подробности](CLIENT_ACCEPTANCE.md#повторный-probe-без-инференса-4-ноября-2026).

Сверка всех этапов roadmap оформлена в [чеклисте](ROADMAP_CHECKLIST.md). Найденный
пробел этапа 1 закрыт прямым Windows-сценарием: профиль через UI → настоящий restart
HTTP-процесса → новая цель без повторного ввода → настоящий stdio MCP/VM цикл,
tests и clean replay. Новый HEAD закреплён автоматически, saved profile неизменен,
обе VM отсутствуют, агенты отключены. Ответы scripted, приёмка/Apply не выполнялись.
[Свидетельства](evaluations/2026-11-04-saved-project-restart.md); первоначальная ошибка
harness сохранена. Production-код не менялся, полного повторного unit-прогона нет.

Обрыв HTTP-владельца самой operator cleanup проверен с реальной VM: Job helper
завершается, отдельная VM остаётся живой; SQLite fence переживает restart до явного
повтора через UI. Свежая SDK cleanup снимает блокировку; задачи, версии, exports
и исходный Git сохранены, outsider жив. Исходный terminal-run — trusted fixture,
не реальный coding/MCP/model цикл. Два ранних harness failure сохранены;
production-код не менялся, полные unit-наборы не повторялись.
[Свидетельства](evaluations/2026-11-04-cleanup-owner-crash.md).

Общие token/time budgets проверены через настоящий web/HTTP/MCP/microVM путь с четырьмя
областями и двумя слотами. Запрет очереди/новых generation, понятная причина,
полный checkpoint, HTTP restart и явное recovery с теми же задачами прошли вместе
с tests/clean replay и отдельным VM oracle. Проверка нашла и исправила ошибочную
web-классификацию budget failure как user cancellation, потерю worker report при
отмене во время stop и незавершённое присутствие координатора при EOF. Старые
неуспехи сохранены; исправления покрыты 13 + 8 + 4 новыми проверками.
[Свидетельства](evaluations/2026-11-04-web-budgets.md).

Добавлена отдельная человеческая очистка среды terminal-run без checkpoint или
при устаревшем контексте. Она использует исходный fixed argv, не запускает цель,
не меняет задачи/версии/exports. SQLite lease блокирует launch/recovery и другие
cleanup; отказ или потеря контроллера сохраняют fence до явного успешного повтора.
Оригинальные PID/group только проверяются на завершение, не сигналятся. Новый
helper имеет отдельную gated scope receipt; старое `ok: true` не считается успехом.
Проверены 24 новых теста и настоящий web/MCP/microVM путь после first-export crash:
cleanup через UI, HTTP restart, SDK absence, узкий экран и тёмная тема с клавиатурой.
[Свидетельства](evaluations/2026-11-03-operator-cleanup.md).

Проверены три новые crash-границы этапа 6: первый неполный экспорт, экспорт
после записи и финальная очистка clean VM. Partial export не становится checkpoint;
HTTP restart не запускает cleanup/resume. Явное восстановление использует только
опубликованный снимок и повторяет tests/clean replay с той же подзадачей.
[Свидетельства](evaluations/2026-10-25-export-crash.md).

В sibling runtime исправлен общий token fence: исчерпание бюджета теперь запрещает
новый admission/inference, отменяет активную работу и сохраняет `token_budget`,
включая завершение группы до следующего такта watchdog. Подтверждены diamond
fan-in, отказ prerequisite, истечение времени и cleanup failure с живой VM.
[Свидетельства](evaluations/2026-10-25-fanin-budgets.md).

- Последний полный набор приложения: **200 проверок, 198 pass / 2 ожидаемых skip**.
- Исторический runtime до native-tools: **396 проверок, 353 pass / 43 skip**;
  один skip — host symlink rights. 387/344/43 — предыдущая empty-search версия,
  не результат нового незавершённого native-tools кода.
- Предыдущие opt-in VM-наборы: 6/6 fan-in проверок (4 pure + 2 VM), 3/3 shared-budget
  VM-проверок; 3/3 web/MCP crash-сценария. Все owned VM отсутствуют после cleanup.
- Общая token-регрессия: исходные 3 проверки не проходили, итоговые 4 проходят.
- Предыдущие исправления scheduler сохраняются: raw binary merge, layout-коллизии
  и удержание резерва при неподтверждённой очистке.
- Реальные VM со scripted решениями: readonly зависимость с бинарной фикстурой;
  отказ stop при живой VM, удержание резерва и отсутствие запуска queued worker,
  затем явная успешная owned cleanup.
- Web/MCP с `--binary-fixture --question`: два worker, бинарные checkpoints,
  вопрос/ответ, HTTP restart, те же задачи, tests/clean replay, canonical export и
  stop прошли. Оснастка учитывает plain-text decision notes и точные source fragments.
- [Отчёт и границы](evaluations/2026-10-25-parallel-scheduler.md),
  [хеши и свидетельства](evaluations/2026-10-25-parallel-scheduler.json).

Дополнительно проверены очередь четырёх областей при двух слотах и hard-kill
DAG-supervisor с двумя работающими VM: явное восстановление сохраняет checkpoint
и все ID подзадач; старые VM отсутствуют до запуска нового supervisor.
[Свидетельства и ограничения](evaluations/2026-10-25-four-worker-dag.md).

Предыдущее исправление сохранения sealed-вопроса после cleanup и fault matrix
публикации остаётся покрытым полным набором:
[свидетельства](evaluations/2026-10-25-question-publication-crash.md).
Исправление порядка внешних отчётов также сохраняется:
[свидетельства](evaluations/2026-10-25-report-order.md).
Принудительное завершение supervisor и исправление bootstrap pipe:
[свидетельства](evaluations/2026-10-25-hard-crash.md).
По запросу пользователя от 4 ноября выполнен локальный коммит `91c7d33`.
Последующие browser fixtures, сверка и evidence остаются в рабочем дереве.
Соседний runtime — отдельный репозиторий и в этот коммит не входит.
Публикация в remote не выполнялась.

## Ближайшие задачи

Сверка завершена, найденный локальный пробел запуска сохранённого проекта после
restart проверен. [Точный перечень](ROADMAP_CHECKLIST.md#оставшиеся-исходные-gates-и-реальные-пробелы)
содержит оставшиеся условия: Mac/PC и реальные клиентские/модельные прогоны.
Позднее устранён выявленный пробел подготовки ARM64 guest Git; Mac execution
gate этим не закрыт. Разрешены ограниченные реальные прогоны этапов 7–9 на Windows;
рабочий argv не переключать. Mac недоступен.

Следующая актуальная native-задача: `readiness.py` игнорирует optional aggregate
`ready:false` при passed component rows, а также принимает не-bool значение.
Metadata-only counterexample воспроизведён на isolated Store без модели/VM;
готовое исправление не реализовано. После двух исходных attempts и context
allocation выполнены ещё десять terminal failures, включая stdout и thinking.
Context/empty-search/public inference profiles имеют сохранённые unit/scripted
receipts; исторический набор профилей — 396/353/43. Native-tools opt-in и ARM64 Git
имеют новые отдельные checks выше. Далее нужны успешный model
export и полный guest suite/clean replay. Не засчитывать
toy-suite за этот gate и не писать patch за модель.

Это очередь предложенной работы, а не разрешение запускать пользовательские цели,
менять runtime-конфигурацию или выполнять приёмку за пользователя.

<a id="открыто-и-требует-отдельного-окружения-или-снятия-паузы"></a>

## Открыто и требует дополнительных проверок или окружения

| Направление | Что осталось |
|---|---|
| macOS | Фактический запуск на MacBook Air M4, aarch64 microVM/native/Git, backup roundtrip; ARM64 Git подготовлен статически, runtime acceptance нет |
| Mac → PC | Приложение и sandbox на Mac, Ollama на Windows; реальный сетевой сценарий |
| Claude Code, затем Codex | Полный MCP workflow и реальные native trust/approval; не считать наличие конфигурации подключением |
| Этап 7 | Малый V3 comparison прошёл 5/5 в обоих режимах; поздние native attempts дали десять failures, нового итогового comparison нет. Независимое ревью и человеческая приёмка открыты, валидатор выключен |
| Этап 8 | Настоящая автономная доработка большого native-проекта: readiness без успешного export/full guest suite/clean coding replay. Native-tools интеграция проверена, последний real run — token_budget без правки |

Подробная история — в [ROADMAP_AUDIT.md](../ROADMAP_AUDIT.md) и
[CLIENT_ACCEPTANCE.md](CLIENT_ACCEPTANCE.md). Исторические числа тестов, данные БД
и ограничения в старых срезах относятся к датам этих срезов. Для текущего решения
проверять свежие свидетельства и реальное состояние, не переписывая историю.

## Обновление этого файла

После изменения состояния задачи обновить завершённую работу, ближайший шаг и
ссылку на проверку. После новых проверок заменить текущий итог, сохранив старое
свидетельство в evaluations. Удалять временный запрет только после нового указания
пользователя. Не хранить здесь секреты, абсолютные пути машины, PID или порт как
гарантию работающего сервера.
