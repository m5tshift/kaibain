# Работа над Kaibain

Обязательные правила — в [AGENTS.md](AGENTS.md), актуальная передача контекста —
в [docs/STATUS.md](docs/STATUS.md). Запуск и подключение MCP описаны в
[README.md](README.md). Этот файл помогает найти место изменения и выбрать проверки.

## Карта кода

| Область | Где менять |
|---|---|
| CLI и управляемый локальный сервер | [run.py](run.py), [launcher.py](src/kaibain/launcher.py) |
| SQLite, права, версии, задачи, проекты, диалог и атомарные переходы | [store.py](src/kaibain/store.py) |
| HTTP API и маршруты | [web.py](src/kaibain/web.py) |
| Инструменты внешних агентов | [mcp_server.py](src/kaibain/mcp_server.py) |
| Supervisor, фиксированный argv и жизненный цикл запуска | [agent_runtime.py](src/kaibain/agent_runtime.py) |
| Владение процессами и завершение дочерней области | [process_scope.py](src/kaibain/process_scope.py), [process_child.py](src/kaibain/process_child.py) |
| Read-only диагностика и проверка Git-профиля | [readiness.py](src/kaibain/readiness.py), [git_project.py](src/kaibain/git_project.py) |
| Checkpoint и проверка сохранённого экспорта | [recovery.py](src/kaibain/recovery.py) |
| Приёмка, целостность экспорта и отдельный Apply | [result_review.py](src/kaibain/result_review.py), [independent_review.py](src/kaibain/independent_review.py) |
| Проекция состояния графа | [agent_graph.py](src/kaibain/agent_graph.py) |
| Резервное копирование и восстановление | [backup.py](src/kaibain/backup.py) |
| Интерфейс без сборщика: HTML, CSS и JavaScript | [src/static](src/static) |
| LLM-оркестрация, DAG, инструменты проектного кода и microVM-политики | Соседний [multi-agent-system](../multi-agent-system) |

Новые общие правила вносить в Store, чтобы HTTP и MCP получали одинаковое поведение.
Не переносить оркестрацию и политики изоляции в веб-приложение. Перед работой над
runtime прочитать его [AGENTS.md](../multi-agent-system/AGENTS.md) и
[DEVELOPMENT.md](../multi-agent-system/DEVELOPMENT.md).

## Рабочий цикл

1. Прочитать STATUS и проверить реальный код затронутого сценария. Сверить состояние
   рабочего дерева; сохранить чужие изменения.
2. Для исправления ошибки воспроизвести её на изолированных данных и добавить
   регрессионную проверку там, где ошибка влияет на корректность или сохранность.
3. Изменить общий слой, затем адаптеры/UI при необходимости. Сохранить проверки
   версий, границы прав, человеческую приёмку и владение ресурсами.
4. Выполнить подходящие проверки из таблицы ниже. При ошибке сохранить свидетельство
   и причину; успешный повтор не заменяет исходный неуспех.
5. Обновить документацию по фактически изменившемуся поведению. В передаче указать
   результат, проверки, ограничения и следующий шаг.

Тестовые SQLite, Git и артефакты должны быть изолированы. В пользовательскую базу
не добавлять демонстрации. Для реального проекта не выполнять Start/Stop/Resume,
acceptance или Apply ради проверки интерфейса; использовать фикстуры.

## Выбор проверок

Команды выполняются из корня Kaibain с его существующим venv. На macOS использовать
`.venv/bin/python`, на Windows — `.venv/Scripts/python.exe`.

```sh
# Подходящий модуль во время правки
python -m unittest discover -s tests -p test_work_dialogue.py -v
# Полный набор после значимых изменений общего слоя
python -m unittest discover -s tests -v
```

В примерах `python` означает Python этого venv. Зависимости — в
[requirements-dev.txt](requirements-dev.txt); подробности браузера и runtime — в README.

| Изменение | Проверки |
|---|---|
| Store, версии, права, задачи, проекты | Соответствующие `test_*.py`; полный unit-набор при изменении общих правил |
| Порядок внешних отчётов и актуальность ревью | `tests/test_report_order.py`; равные даты, UUID, restart/VACUUM и legacy migration |
| HTTP/MCP и основной интерфейс | `tests/browser_check.py`; для проектов — `tests/browser_projects_check.py` |
| Сохранённый проект после restart → успешный запуск | Windows: `tests/browser_saved_project_restart_check.py --prepared-state ABSOLUTE_PATH`; настоящий HTTP restart/UI/MCP/VM, scripted HTTP без LLM |
| Диагностика | `tests/test_readiness.py`, `tests/browser_readiness_check.py` |
| Вопросы и человеческое продолжение | `tests/test_dialogue.py`, `tests/test_work_dialogue.py`, `tests/browser_dialogue_check.py` |
| Аварии публикации и вопросов | `tests/test_publication_crash.py`, `tests/test_publication_process_crash.py`, `tests/test_question_interrupt.py`, `tests/test_supervisor_interrupt.py` |
| Реальное завершение владельца процесса | `tests/test_process_scope.py`; Windows venv handles — `tests/windows_owned_process.py` |
| Граф и присутствие | `tests/test_agent_graph.py`, `tests/browser_agent_graph_check.py` |
| Приёмка и публикация в Git | `tests/test_result_review.py`, `tests/browser_result_review_check.py` |
| Восстановление | `tests/test_recovery.py`, `tests/browser_recovery_check.py` |
| Реальные microVM и MCP, ответы заранее заданы | `tests/browser_development_check.py`, `tests/browser_recovery_development_check.py`, `tests/browser_work_dialogue_check.py --lose-question-report` |
| Supervisor с реальной VM без моделей | `tests/supervisor_interrupt_check.py --run-real-vm`; нужен актуальный prepared receipt |
| Жёсткий обрыв и явное восстановление | Windows: `tests/browser_recovery_development_check.py --kill-supervisor`; настоящий MCP/VM, scripted HTTP без LLM |
| Обрыв экспорта и финальной очистки | Windows: `tests/browser_export_crash_check.py --crash-point first-export`, `after-write-export` или `cleanup`, обязательно `--prepared-state`; partial export не становится checkpoint |
| Отдельная человеческая очистка среды | `tests/test_run_cleanup.py` (fixed adapter, без VM/LLM); `first-export` выше проходит настоящий UI cleanup и HTTP restart с реальной VM |
| Обрыв HTTP-владельца самой очистки | Windows: `tests/browser_operator_cleanup_crash_check.py --prepared-state ABSOLUTE_PATH`; real SDK VM/UI, terminal-run — trusted fixture, без LLM/MCP coding proof |
| Параллельный scheduler и целостность | Sibling: `test_scheduling.py`, `test_scheduling_layout.py`, `test_parallel_binary.py`, `test_parallel_cleanup_budget.py`; web: `tests/browser_parallel_development_check.py --binary-fixture --question` |
| Fan-in и исчерпание общей квоты | Sibling: `test_shared_budget_exhaustion.py`; opt-in реальные VM — `test_parallel_fanin.py`, `test_parallel_budget.py`, флаги/receipt описаны в `PARALLEL_DEVELOPMENT.md` |
| Общий бюджет через web/MCP и отмена во время cleanup | `tests/browser_parallel_budget_check.py --budget tokens` или `time`, обязательно `--prepared-state`; sibling unit: `test_web_budget_failure.py`, `test_development_cancel_cleanup.py`; stdio EOF: `tests/test_mcp_lifecycle.py` |
| Реальная LLM-калибровка/сравнение без image pull | Sibling: `run_review_calibration.py`/`run_evaluation.py --prepared-state EXISTING_STATE --output NEW_DIRECTORY`; `test_evaluation_prepare.py` проверяет fail-closed metadata reuse. Только с разрешением на реальные LLM |
| Workflow feedback и доступность чтений модели | Sibling: `test_development_workflow_context.py`, `test_development_context.py`, `test_development_memory_run.py`, `test_development_wire.py`; реальные matrices отдельно от unit/scripted |
| Protected native coding cases | Sibling: `test_native_coding_profile.py`; `tools/check_native_coding_export.py --case readiness-aggregate` проверяет полный guest suite, контракт выполнять только внутри VM |
| Полный context bound и видимый source cache | Sibling: `test_context_allocation.py`, reservation/wire/memory checks; actual HTTP shape и guard используют одинаковую квоту, enum_sets не меняет decoding schema |
| Память завершённых пустых поисков | Sibling: `test_empty_search_memory.py`; точные query/scope/cursor, version invalidation, ограничение 768 bytes, отсутствие выдуманных zero-hit/error facts |
| Повтор полного чтения большого файла | Sibling: `test_oversized_read_memory.py`; version-bound size receipts, общие schema/guard/wire, range доступен, ошибка не превращается в успешное чтение; при новом SHA правило снимается |
| Настоящие пустые файлы и компактные enum subsets | Sibling: `test_empty_file_memory.py`, `test_schema_enum_subsets.py`; zero-byte digest/EOF proof, общий receipt budget, отсутствие выдуманных source lines, typed/order-preserving lossless menus и неизменность полного format |
| Явные доверенные inference budgets | Sibling: `test_inference_profiles.py`; default 32768/1024, opt-in 65536/2048/4096, exact reservation, role caps, deadline и отказ неявной замены config. Browser restart fixture поддерживает эти параметры только в своём isolated argv |
| Opt-in Ollama native tools и учёт render overhead | Sibling: `test_native_tool_projection.py`, `test_native_tool_model.py`; один вызов, полная observed schema, truthful history, одинаковый HTTP payload/квота, bounded zero-generation reallocation. Scripted restart fixture: `--developer-protocol native-tools` |
| ARM64 guest Git | Sibling: `test_guest_toolchain_arm64.py`, `test_guest_toolchain.py`; pins/ELF/closure/границы и совместимость amd64. Статическая проверка Windows не заменяет запуск на Mac; [свидетельства](docs/evaluations/2026-11-04-arm64-guest-git.md) |

Не запускать всю таблицу для каждой правки. Выбирать проверки по затронутому
поведению и открытым рискам. Правки только документации требуют проверки ссылок,
путей и команд; запуск моделей и VM для них не нужен.

Unit и обычные браузерные фикстуры не подтверждают качество модели или microVM.
Scripted HTTP + настоящие microVM подтверждает интеграцию, но не качество LLM.
Реальная Ollama, внешние Claude/Codex и Mac — отдельные проверки, выполняемые
с учётом текущих разрешений пользователя из STATUS.

## Документация и передача контекста

| Файл | Что поддерживать |
|---|---|
| [AGENTS.md](AGENTS.md) | Устойчивые обязательные правила; без растущего журнала каждого прогона |
| [docs/STATUS.md](docs/STATUS.md) | Текущие ограничения, ближайшие задачи, последний проверенный результат |
| [docs/ROADMAP_CHECKLIST.md](docs/ROADMAP_CHECKLIST.md) | Требования каждого этапа, точная граница подтверждения и остающиеся исходные gates |
| [ROADMAP.md](ROADMAP.md) | Объём этапов и критерии готовности |
| [ROADMAP_AUDIT.md](ROADMAP_AUDIT.md) | Историю проверок и границы утверждений; новые срезы явно отделять от старых |
| `docs/evaluations/` | Локальные воспроизводимые отчёты, хеши и сохранённые неуспехи; в Git не публикуются (`.gitignore`) |
| [README.md](README.md) | Пользовательские сценарии, запуск и подключение клиентов |

Не копировать длинные логи в STATUS, не фиксировать в нём PID и текущее состояние
серверов. Проверять их командами `run.py status` и `/api/health`. Не переносить между
ОС `.local`, `.venv`, `.mcp.json`, `data` и другие машинные настройки.

## Claude Code и общий AGENTS.md

В репозитории используется один файл инструкций — AGENTS.md. Актуальный Claude Code
умеет читать его напрямую (v2.1.277+; для ряда окружений требуется v2.1.281+).
Автоматическая загрузка зависит от настроек, встроенного плагина и отсутствия
CLAUDE.md/CLAUDE.local.md в текущей или родительских папках. Проверить фактическую
загрузку можно через `/memory`, а режим инструкций — через `/config`.
Старая версия может получить инструкцию явно: «Прочитай AGENTS.md перед работой».
Версии и настройки клиента не заменяют проверку реального MCP-подключения.
[Официальная документация](https://code.claude.com/docs/en/memory#agentsmd).

## Лицензия и сообщения об уязвимостях

Вклад принимается на условиях [MIT License](LICENSE) проекта. Уязвимости не
публикуйте в issues — используйте приватный отчёт GitHub, см. [SECURITY.md](SECURITY.md).
Pull request должен проходить CI (`.github/workflows/checks.yml`); описывайте, какие
проверки выполнены фактически, отдельно от scripted и real-inference evidence.
