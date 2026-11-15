# Revit MCP

MCP-сервер для Autodesk Revit 2025–2027: Claude читает открытую модель (элементы, параметры,
спецификации, предупреждения, изображения видов) и, если вы разрешили это в Revit, меняет её
(параметры, уровни, оси, стены, экземпляры семейств, перенос, копирование, удаление).

> **Состояние: 0.1.0, экспериментальная версия.** Код надстройки компилируется против Revit API
> 2025, 2026 и 2027, серверная часть и канал связи покрыты тестами, но **внутри Revit надстройка
> ещё ни разу не запускалась**. Первый запуск делайте на копии модели. Что именно проверено —
> в [docs/COMPARISON.md](docs/COMPARISON.md#что-проверено-а-что-нет).

## Как это устроено

```
Claude Code / Claude Desktop
        │  stdio (MCP)
        ▼
server/index.mjs            Node.js, без зависимостей
        │  именованный канал \\.\pipe\RevitMcp-<pid>
        │  (только текущий пользователь Windows + токен)
        ▼
RevitMcp.Addin.dll          надстройка внутри Revit
        │  ExternalEvent → главный поток Revit
        ▼
Revit API
```

- Сетевые порты не открываются, наружу ничего не отправляется.
- Надстройка при запуске Revit пишет файл `%LOCALAPPDATA%\RevitMcp\instances\<pid>.json` с именем
  канала и случайным токеном; сервер находит по нему работающий Revit. Несколько сеансов Revit
  поддерживаются (`revit_use_instance`).
- **Изменение модели выключено по умолчанию.** Включается кнопкой **Changes OFF/ON** на панели
  **Revit MCP** вкладки «Надстройки». Проверка выполняется внутри Revit, клиент обойти её не может.
- Инструмента для выполнения произвольного кода нет.
- Каждое изменение — одна транзакция «MCP: …», отменяется через Ctrl+Z.

## Установка

Нужны: Windows x64, Revit 2025, 2026 или 2027. Для Claude Code — Node.js 18 или новее
(в офлайн-наборе есть `node-v24.22.3-x64.msi`). Интернет и права администратора не нужны.

### 1. Надстройка и сервер

Возьмите архив `RevitMcp-0.1.0.zip` (собирается скриптом `addin/build.sh`), распакуйте и при
закрытом Revit выполните в PowerShell из распакованной папки:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Скрипт копирует надстройку в `%APPDATA%\Autodesk\Revit\Addins\<год>` для найденных версий Revit
(или для указанных: `-RevitYears 2027`), сервер — в `%LOCALAPPDATA%\RevitMcp\server`, и печатает
строки для подключения клиента. Настройки клиентов он не трогает. Удаление: `.\install.ps1 -Uninstall`.

При первом запуске Revit спросит про неподписанную надстройку — выберите «Всегда загружать».
На вкладке «Надстройки» появится панель **Revit MCP**.

### 2. Подключение клиента

**Claude Code, вариант А — плагин из этого репозитория** (сервер берётся из плагина):

```
/plugin marketplace add KPINomad-hash/claude-plugins-official
/plugin install revit-mcp@claude-plugins-official
```

Этот вариант требует доступа к GitHub на момент установки. Вместе с сервером ставится навык
`revit-modeling` с правилами работы с моделью.

**Claude Code, вариант Б — без сети**, из установленной на шаге 1 копии:

```powershell
claude mcp add --scope user revit -- node "$env:LOCALAPPDATA\RevitMcp\server\index.mjs"
```

**Claude Desktop:** «Настройки → Расширения → Установить расширение» и выберите
`revit-mcp-0.1.0.mcpb` из распакованной папки. Либо вручную в `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "revit": {
      "command": "node",
      "args": ["C:\\Users\\<вы>\\AppData\\Local\\RevitMcp\\server\\index.mjs"]
    }
  }
}
```

### 3. Проверка

Откройте в Revit копию проекта и попросите: «Проверь соединение с Revit и скажи, что в модели».
Ожидаемый порядок вызовов: `revit_status` → `revit_get_document_info` → `revit_list_categories`.

## Инструменты

Все длины и координаты — в миллиметрах, идентификаторы элементов — целые числа.

| Инструмент | Что делает |
| --- | --- |
| `revit_status` | Соединение, версия Revit, открытые документы, активный вид, разрешена ли запись |
| `revit_use_instance` | Выбор сеанса Revit, если их несколько |
| `revit_get_document_info` | Сведения о проекте, единицы, стадии, рабочие наборы, связи |
| `revit_list_categories` | Категории с числом элементов |
| `revit_query_elements` | Поиск по категории, классу, уровню, имени типа, условию на параметр |
| `revit_get_elements` | Полные сведения по элементам: параметры, положение, габариты |
| `revit_get_selection` | Что выделено в Revit |
| `revit_list_levels_and_grids` | Уровни и оси |
| `revit_list_views`, `revit_list_sheets` | Виды и листы |
| `revit_list_family_types` | Типоразмеры семейств (для размещения) |
| `revit_get_schedule_data` | Содержимое спецификации |
| `revit_list_warnings` | Предупреждения модели |
| `revit_export_view_image` | Изображение вида (PNG) |
| `revit_select_elements`, `revit_set_active_view` | Выделить и показать элементы, открыть вид — модель не меняют |
| `revit_set_parameters` ✎ | Значения параметров, до 500 за вызов, есть пробный прогон |
| `revit_create_levels`, `revit_create_grids`, `revit_create_walls` ✎ | Уровни, оси, стены |
| `revit_place_family_instances` ✎ | Колонны, фундаменты, оборудование (по точке), балки и связи (по линии) |
| `revit_transform_elements` ✎ | Перенос, поворот, копирование |
| `revit_delete_elements` ✎ | Удаление; по умолчанию пробный прогон с подсчётом зависимых элементов |

✎ — меняет модель, работает только при включённом «Changes ON».

## Настройки

Переменные окружения сервера:

| Переменная | Значение |
| --- | --- |
| `REVIT_MCP_READ_ONLY=1` | Скрыть все изменяющие инструменты |
| `REVIT_MCP_PID=<pid>` | Предпочитаемый сеанс Revit, если их несколько |
| `REVIT_MCP_TIMEOUT_MS` | Сколько ждать ответа Revit (по умолчанию 150000) |

Надстройка хранит настройки в `%LOCALAPPDATA%\RevitMcp\settings.json`
(`allowWrite`, `revitTimeoutSeconds`), журнал — в `addin.log` там же.

## Если не работает

| Признак | Причина и действие |
| --- | --- |
| Панели Revit MCP нет | Надстройка не установлена для этой версии Revit или заблокирована: повторите `install.ps1`, при запуске выберите «Всегда загружать» |
| «No running Revit session…» | Revit не запущен или запущен под другим пользователем Windows |
| «Revit did not become idle…» | В Revit открыт диалог или идёт команда — завершите её |
| «Model changes are switched off…» | Нажмите **Changes OFF** на панели Revit MCP |
| В клиенте нет инструментов `revit_*` | Не найден `node` в PATH или неверный путь к `index.mjs`; проверьте `node --version` |

## Сборка и тесты

```bash
# тесты сервера (Node.js 18+); сквозной тест с C# требует собранной заглушки
dotnet build addin/tests/BridgeTestHost -c Release
cd server && npm test

# надстройка для 2025/2026/2027 и релизный архив в addin/dist (нужны .NET SDK 8 и 10)
addin/build.sh
```

Revit для сборки не нужен: API подключается через справочные NuGet-пакеты
`Nice3point.Revit.Api.*`. Список команд надстройки
(`addin/src/RevitMcp.Addin/CommandInfrastructure.cs`) и каталог инструментов
(`server/lib/tools.mjs`) сверяются тестом `server/test/catalog.test.mjs`.

## Другие серверы

Сравнение с семью готовыми серверами (Horizun, BIMwright, AEC Model Bridge, RevitCortex, s81,
Demolinator, KenLP) и рекомендация по выбору — в [docs/COMPARISON.md](docs/COMPARISON.md).
Плагины для их подключения к Claude Code лежат в `external_plugins/revit-*`.

## Лицензия

Apache-2.0. Autodesk и Revit — товарные знаки Autodesk, Inc.; проект с Autodesk не связан.
