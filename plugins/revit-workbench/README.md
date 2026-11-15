# Revit Workbench

Два навыка для Claude и скрипт установки:

| Что | Где | Для чего |
| --- | --- | --- |
| Установка в один шаг | `skills/revit-mcp-setup/scripts/Install-RevitMcp.cmd` | Ставит серверы Horizun и BIMwright из офлайн-набора и подключает их к Claude Code и Claude Desktop |
| Инструкция для агента по установке | `skills/revit-mcp-setup/SKILL.md` | Claude сам проводит установку, проверку и разбор сбоев |
| Инструкция для агента по переносу | `skills/dwg-to-revit/SKILL.md` | Порядок переноса чертежей АР, КЖ, КМ, ОВ из AutoCAD в Revit |
| Справка по документу правил | `skills/dwg-to-revit/references/requirement-set.md` | Формат правил «слой → элемент» и заготовки по разделам |
| Памятка для человека | `docs/DWG-TO-REVIT.md` | Подготовка чертежей и проекта, решения по ходу работы |

## Состояние

- Скрипт установки проверен 41 автоматической проверкой и пробным прогоном в PowerShell 7 на
  Linux. **На Windows с настоящими установщиками и Revit он не запускался.**
- Порядок переноса составлен по документации и исходникам Horizun 4.0.0. **В Revit он не
  проверялся**; заготовки правил, кроме двух примеров из документации, не прогонялись.

Первый раз запускайте установку с ключом `-DryRun`, а перенос пробуйте на одном этаже в новом
проекте.

## Установка серверов в один клик

1. Скопируйте `Install-RevitMcp.cmd` и `Install-RevitMcp.ps1` в папку офлайн-набора, где лежат
   `horizun-mcp-4.0.0-setup.exe` и `RvtMcp.Setup-v1.2.2-win-x64.zip`.
2. Закройте Revit.
3. Дважды щёлкните `Install-RevitMcp.cmd`.

Скрипт сверит SHA-256 установщиков, поставит оба сервера без прав администратора и без
интернета, зарегистрирует их в Claude Code и Claude Desktop (с резервной копией настроек) и
напишет, что осталось сделать. Пробный прогон без изменений:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Install-RevitMcp.ps1 -DryRun
```

Ключи: `-Servers Horizun`, `-Clients ClaudeCode`, `-Clients None`, `-BimwrightToolsets query,create`,
`-HorizunProfile esencial`, `-EnableSendCode`, `-SkipHashCheck`, `-KitDir <папка>`.

Серверы запускаются так:

- Horizun — полный профиль (`HORIZUN_PROFILE=completo`) с краткими описаниями инструментов;
- BIMwright — весь каталог (`--toolsets all`), выполнение произвольного C# выключено
  (`--disable-send-code`).

После установки запустите Revit и на вопрос о надстройке ответьте «Всегда загружать». BIMwright
рядом с Horizun стартует выключенным: **Надстройки → RvtMcp → MCP: ON**.

Скрипт регистрирует серверы напрямую, поэтому плагины `revit-horizun` и `revit-bimwright` из этого
репозитория после него ставить не нужно — получились бы дублирующие записи.

## Перенос из AutoCAD

Подготовьте чертежи и проект по памятке [docs/DWG-TO-REVIT.md](docs/DWG-TO-REVIT.md), затем
попросите Claude, например: «Перенеси план первого этажа АР из `D:\Проект\АР_этаж1.dwg`».
Навык `dwg-to-revit` включится сам.

## Проверка скрипта

```powershell
pwsh -NoProfile -File skills/revit-mcp-setup/scripts/Install-RevitMcp.Tests.ps1
```

## Лицензия

Apache-2.0. Horizun и BIMwright — сторонние проекты под Apache-2.0; их установщики в этот
репозиторий не входят. Autodesk, Revit и AutoCAD — товарные знаки Autodesk, Inc.
