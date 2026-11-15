# Demolinator revit-mcp-server — подключение к Claude Code

Плагин регистрирует в Claude Code MCP-сервер **Demolinator revit-mcp-server** (проверенная версия 0.2.0 (коммит a50b09e), лицензия MIT).
Исходный проект: <https://github.com/Demolinator/revit-mcp-server>.

## Установка сервера

Поставьте pyRevit и Python 3.13 x64, затем следуйте разделу «Demolinator без доступа к PyPI» инструкции офлайн-набора: расширение `demolinator.extension`, сервер Routes на порту 48884, окружение `C:\RevitMCP\demolinator-venv`.

Плагин ищет сервер по пути по умолчанию: `C:\RevitMCP\demolinator-venv\Scripts\python.exe` и `C:\RevitMCP\demolinator.extension\main.py`.
Если вы ставили в другое место, задайте до запуска Claude Code переменную окружения `REVIT_DEMOLINATOR_PYTHON` и `REVIT_DEMOLINATOR_MAIN` с полным путём.

## Что важно знать

- Путь к `main.py` зависит от того, куда вы положили расширение pyRevit. Если не `C:\RevitMCP\demolinator.extension`, задайте `REVIT_DEMOLINATOR_MAIN`.
- Сервер pyRevit Routes (порт 48884) **не имеет аутентификации**; к нему может обратиться любой локальный процесс.
- Есть инструмент `execute_revit_code` — произвольный IronPython внутри транзакции, без подтверждений и без отключения. Инструменты не размечены как читающие/изменяющие.
- Поддержка Revit 2027 зависит от сборки pyRevit; в репозитории она только заявлена.

## Перед использованием

- Плагин только подключает уже установленный сервер к Claude Code. Сам сервер и надстройку Revit нужно поставить на этой же машине Windows (см. выше).
- Откройте в Revit **копию** модели и начните с запроса состояния и списка инструментов.
- Это сторонний проект: Anthropic и автор этого репозитория его не проверяют и не сопровождают. Сравнение всех вариантов — в [`plugins/revit-mcp/docs/COMPARISON.md`](../../plugins/revit-mcp/docs/COMPARISON.md).
