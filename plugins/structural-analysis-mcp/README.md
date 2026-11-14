# structural-analysis-mcp

Четыре локальных MCP-сервера для расчётных программ на рабочей станции Windows:

| Сервер | Программа | Интерфейс | Префикс инструментов |
| --- | --- | --- | --- |
| `sap2000-mcp` | SAP2000 | CSI OAPI, локальный COM (`comtypes`) | `sap2000_` |
| `staad-mcp` | STAAD.Pro 2025+ | OpenSTAAD через `openstaadpy` | `staad_` |
| `lira-mcp` | ЛИРА-САПР / LIRA-FEM 2024+ | COM ВИЗОР + `LiraResAPI.dll` (`pywin32`) | `lira_` |
| `sapfir-mcp` | САПФИР-3D / LIRA-CAD | COM (этажи и объекты) + файловый маршрут IFC | `sapfir_` |

Серверы работают по stdio на той же машине, где установлены программа и лицензия.
По умолчанию доступ только на чтение. Изменение моделей не реализовано.

## Состояние проверки

Код написан по опубликованным описаниям API и исходникам сторонних проектов
(`openstaadpy`, `openstaad-mcp`, `lira-sapr-mcp`, `sap2000-mcp`, учебное пособие САПФИР, гл. 10).
**На установленных программах он не запускался.** Автотесты (`pytest`, 39 шт.) проверяют
общий слой и разбор ответов API на имитациях, а не сами программы. Перед работой с проектными
моделями пройдите приёмку из раздела «Приёмка на рабочей станции».

## Установка

Нужны Windows, [uv](https://docs.astral.sh/uv/) и 64-разрядный Python 3.10+ (uv поставит сам).

Как плагин Claude Code — `.mcp.json` в этой папке запускает все четыре сервера.
Вручную, для любого MCP-клиента (stdio):

```powershell
uv run --project C:\path\to\structural-analysis-mcp sap2000-mcp
uv run --project C:\path\to\structural-analysis-mcp lira-mcp
uv run --project C:\path\to\structural-analysis-mcp sapfir-mcp
uv run --project C:\path\to\structural-analysis-mcp --with git+https://github.com/BentleySystems/openstaadpy.git staad-mcp
```

Claude Code: `claude mcp add --transport stdio sap2000 -- uv run --project C:\path\to\structural-analysis-mcp sap2000-mcp`.

### Настройки (переменные окружения)

| Переменная | Назначение | По умолчанию |
| --- | --- | --- |
| `STRUCT_MCP_WORKDIR` | Единственная папка, куда серверы пишут: `copies\` (рабочие копии), `exports\`, `journal\` | `%USERPROFILE%\StructuralMCP` |
| `STRUCT_MCP_MODEL_DIRS` | Папки, из которых разрешено брать модели (через `;`). Только чтение: файл копируется | — |
| `STRUCT_MCP_ALLOW_ANALYSIS` | `1` разрешает запуск расчёта, и только на рабочих копиях | выключено |
| `STRUCT_MCP_TIMEOUT` | Ожидание обычного вызова API, с | 120 |
| `STRUCT_MCP_ANALYSIS_TIMEOUT` | После этого времени расчёт помечается `running_overdue` (решатель не останавливается) | 3600 |
| `STRUCT_MCP_LIRA_PROGID`, `STRUCT_MCP_SAPFIR_PROGID` | Явный ProgID, если автоопределение ошиблось | — |

## Инструменты

Общие для SAP2000, STAAD.Pro и ЛИРА (`<p>` — префикс):

| Инструмент | Что делает |
| --- | --- |
| `<p>_get_capabilities` | Подключение, версия, режим доступа, папки, типы элементов и результатов, проверка станции (регистрация COM, разрядность, пакеты). Работает и без запущенной программы |
| `<p>_connect` | Присоединиться к запущенной программе (`model_path` — выбор экземпляра, `start_if_needed`) |
| `<p>_open_working_copy` | Скопировать модель из разрешённой папки в `copies\` и открыть копию. Исходный файл не открывается |
| `<p>_get_model_info` | Путь, ревизия (хэш сохранённого файла), единицы, число объектов, наличие результатов |
| `<p>_list_elements` | Узлы / стержни / пластины с определением, постранично |
| `<p>_list_load_cases` | Загружения и комбинации с составом |
| `<p>_get_results` | Типизированные строки результатов с единицами, осями и ревизией, постранично |
| `<p>_export_results` | Тот же набор в CSV (`;`, UTF-8 BOM, рядом `.meta.txt`) или XLSX в `exports\`, без перезаписи |
| `<p>_run_analysis`, `<p>_get_job_status` | Расчёт рабочей копии фоновым заданием (если разрешён) |

Дополнительно: `staad_list_instances`; `lira_read_table` (любая таблица ввода), `lira_get_analysis_protocol`;
`sapfir_get_model_structure`, `sapfir_describe_api`, `sapfir_read_property`, `sapfir_load_ifc_export`, `sapfir_get_analytical_model_info`.

Виды результатов:

- **SAP2000**: `joint_displacements`, `support_reactions`, `frame_forces`, `modal_periods`, `modal_mass_ratios` — в текущих единицах модели (сервер их не переключает).
- **STAAD.Pro**: `node_displacements`, `support_reactions`, `member_end_forces` (`options.axes`: `local`/`global`), `member_section_forces` (`options.sections`), `modal` — в базовой системе модели (Metric: кН, м; English: kip, in).
- **ЛИРА**: `node_displacements`, `element_forces`, `fragment_loads` (нагрузки на фрагмент — опорные реакции), `combination_displacements`, `combination_forces` (РСН; `options.limit_state`, `options.table`), `modal` — в фиксированных единицах API: тс, м, тс·м, рад.

Статус строки: `ok` — значение вычислено (ноль — это ноль); `no_result` — у программы нет значения;
`error` — отказ API для этой строки. Отсутствие результатов расчёта — ошибка `no_results`, тайм-аут — `timeout`
(вызов не отменён), занятость — `busy`.

## Что важно знать по каждой программе

**SAP2000.** Через COM доступен один экземпляр. Соглашение `comtypes` — выходные параметры,
затем код возврата — подтверждено официальным примером CSI (Example 7, Python: `[PointName1, PointName2, ret] = ...`);
при ином порядке сервер отказывает, а не сдвигает столбцы. Перед чтением результатов сервер
меняет выбор загружений для вывода (`Results.Setup`) — на модель это не влияет.

**STAAD.Pro.** Нужен STAAD.Pro 2025 (25.0.1)+ с открытой моделью; сервер программу не запускает.
Усилия по длине стержня (`member_section_forces`) запрашиваются по расстоянию от
начала стержня, как в справке openstaadpy; старый справочник OpenSTAAD описывал долю длины 0…1, поэтому при приёмке
проверьте, что последнее сечение совпадает с усилиями на конце B. Модальные массы — в процентах (так подписаны в примере Bentley «Retrieve Dynamic Output»).

**ЛИРА.** ProgID — `LiraSapr.Application` (2024), `LiraSapphire.Application` (2025), `LiraFem.Application`
(2026, так в официальной справке LIRALAND); сервер берёт самый новый из зарегистрированных. Работа с API проверена
автором исходного проекта только на 2024 R2.3. Чтение таблицы создаёт и удаляет временную таблицу в документе, поэтому
ЛИРА ставит флаг «изменён». Результаты ЛИРА хранит по ИМЕНИ модели в рабочем каталоге: расчёт рабочей копии
заменяет результаты одноимённой исходной модели. Опорные реакции читаются как «нагрузки на фрагмент» (`fragment_loads`) —
только для узлов фрагмента, заданного до расчёта. В LIRA-FEM 2026 таблица элементов называется
`Elements_TypeAndNumbersOfNodes` (в 2024 — `Elements_TypeAndNodes`); сервер находит её по номеру. Коэффициенты РСН API не
отдаёт — читайте таблицу `DCL`. Событие «расчёт завершён» приходит и при проблемах — смотрите протокол.

**САПФИР.** ProgID — `Sapfir.Application` (текущая версия) или `Sapfir.Application.<N>.0`. По «Описанию функций
автоматизации САПФИР» и форуму LIRALAND `sapfir_get_model_structure` перебирает здания (`CountProjects`,
`GetProjectByIndex`), этажи (`CountStorey`, `GetStoreyByIndex`) и объекты (`CountModel`, `GetModelByIndex`,
`TypeModel`, `ID`), читает имена (`Parameter("M_NAME")`) и по запросу другие параметры по мнемоникам. Типы объектов
взяты из описания: стена 10, плита 11, колонна 12, балка 13, идеализированные стержень 81 и оболочка 82 и др.;
неизвестные выдаются как `type_<код>`. Мнемоника отметки этажа в описании не названа — её можно посмотреть в окне
свойств САПФИР (режим показа мнемоник) и передать в `parameters`; отметки также есть в IFC-экспорте. `sapfir_describe_api` показывает реальный состав API установленной версии. Полное описание
лежит в папке `Manual` программы («Описание функций автоматизации САПФИР.pdf», «Схема объектов автоматизации
САПФИР.pdf») — `sapfir_get_capabilities` ищет эти файлы. Метод экспорта аналитической модели в ЛИРА через COM в
источниках не найден: передача в ВИЗОР выполняется в интерфейсе, после неё сверяйте с `lira_get_model_info`.

## Приёмка на рабочей станции

1. `<p>_get_capabilities` — версии, разрядность, регистрация COM.
2. Открыть модель с известным числом узлов и элементов, сверить `<p>_get_model_info` и `<p>_list_elements`.
3. Консоль 3 м, сила 10 кН на свободном конце, без собственного веса: реакция 10 кН, момент 30 кН·м;
   прогиб PL³/(3EI). Сверить API, таблицу интерфейса и формулу; повторить в других единицах.
   Здесь же установить знаки и порядок компонент.
4. Два загружения и линейная комбинация — сверить значения и состав.
5. Малая модель с модальным расчётом — периоды и модальные массы.
6. Отказы: программа закрыта, нет результатов, открыт модальный диалог, два запроса одновременно.

## Источники по API

- LIRA-FEM API и RES API: <https://help.liraland.com/en-gb/extensions/lira-fem-api.html>,
  <https://help.liraland.com/en-gb/extensions/lira-fem-res-api.html>,
  <https://help.liraland.com/en-gb/extensions/lira-fem-extensions.html>;
  зеркало для ЛИРА-САПФИР: <https://rflira.ru/kb/89/2669/>
- Сторонний сервер ЛИРА (типы, приёмы работы): <https://github.com/EvgenVer/lira-sapr-mcp>
- САПФИР: <https://rflira.ru/kb/99/554/>, <https://rflira.ru/files/kb/sapfir/SAPFIR_automation_functions.pdf>,
  форум <https://www.liraland.ua/forum/forum16/topic2010/messages/>
- STAAD.Pro: <https://github.com/BentleySystems/openstaadpy>, <https://github.com/BentleySystems/openstaad-mcp>,
  пример динамических результатов
  <https://docs.bentley.com/LiveContent/web/STAAD.Pro%20Help-v20/en/GUID-07692CF5-A7ED-4338-A1AD-638A31BC6572.html>
- SAP2000: `CSI_OAPI_Documentation.chm` в папке установки; <https://github.com/fcocarrascob/Skills_SAP>

## Разработка

```bash
uv run --project . --extra dev pytest
```
