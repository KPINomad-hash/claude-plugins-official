@echo off
rem Double-click to install the Horizun and BIMwright Revit MCP servers from this folder.
rem Keep this file next to Install-RevitMcp.ps1 and the installers from the offline kit.
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-RevitMcp.ps1" %*
set "code=%ERRORLEVEL%"
echo.
if "%code%"=="0" (echo Done.) else (echo Installation did not finish. Read the message above.)
pause
exit /b %code%
