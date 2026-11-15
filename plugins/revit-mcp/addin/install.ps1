<#
.SYNOPSIS
  Installs the Revit MCP add-in and server for the current Windows user.

.DESCRIPTION
  Run from the unpacked release folder (RevitMcp-<version>), with Revit closed.
  No administrator rights and no internet access are needed. The script:
    - copies the add-in to %APPDATA%\Autodesk\Revit\Addins\<year> for each chosen Revit year;
    - copies the MCP server to %LOCALAPPDATA%\RevitMcp\server;
    - prints the lines for registering the server in Claude Code and Claude Desktop.
  It does not edit any client configuration.

.PARAMETER RevitYears
  Revit versions to install for. Default: every one of 2025, 2026, 2027 found on this machine.

.PARAMETER Uninstall
  Remove the add-in and the server. Settings and the log in %LOCALAPPDATA%\RevitMcp are kept.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install.ps1
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -RevitYears 2027
#>
[CmdletBinding()]
param(
    [ValidateSet('2025', '2026', '2027')]
    [string[]]$RevitYears,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$supported = @('2025', '2026', '2027')
$addinsRoot = Join-Path $env:APPDATA 'Autodesk\Revit\Addins'
$serverTarget = Join-Path $env:LOCALAPPDATA 'RevitMcp\server'

if (Get-Process -Name 'Revit' -ErrorAction SilentlyContinue) {
    throw 'Revit is running. Close every Revit window and run the script again.'
}

if ($Uninstall) {
    foreach ($year in $supported) {
        $dir = Join-Path $addinsRoot $year
        Remove-Item (Join-Path $dir 'RevitMcp.addin') -Force -ErrorAction SilentlyContinue
        Remove-Item (Join-Path $dir 'RevitMcp') -Recurse -Force -ErrorAction SilentlyContinue
    }
    Remove-Item $serverTarget -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host 'Revit MCP removed. Remove the "revit" server from your MCP client as well.'
    return
}

if (-not $RevitYears) {
    $RevitYears = @($supported | Where-Object {
        (Test-Path (Join-Path $env:ProgramFiles "Autodesk\Revit $_\Revit.exe")) -or
        (Test-Path (Join-Path $addinsRoot $_))
    })
    if (-not $RevitYears) {
        throw 'No Revit 2025, 2026 or 2027 found. Pass the version explicitly, e.g. -RevitYears 2026.'
    }
}

foreach ($year in $RevitYears) {
    $source = Join-Path $PSScriptRoot "addin\$year\RevitMcp.Addin.dll"
    if (-not (Test-Path $source)) { throw "Missing $source. Run the script from the unpacked release folder." }

    $dir = Join-Path $addinsRoot $year
    $bin = Join-Path $dir 'RevitMcp'
    New-Item -ItemType Directory -Path $bin -Force | Out-Null
    Copy-Item $source $bin -Force
    Copy-Item (Join-Path $PSScriptRoot 'addin\RevitMcp.addin') $dir -Force
    # Files that came from another computer are marked as blocked, and Revit refuses to load them.
    Get-ChildItem $bin, (Join-Path $dir 'RevitMcp.addin') -File | Unblock-File
    Write-Host "Add-in installed for Revit $year -> $dir"
}

New-Item -ItemType Directory -Path $serverTarget -Force | Out-Null
Copy-Item (Join-Path $PSScriptRoot 'server\*') $serverTarget -Recurse -Force
Get-ChildItem $serverTarget -Recurse -File | Unblock-File
$entry = Join-Path $serverTarget 'index.mjs'
Write-Host "MCP server installed -> $entry"

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Warning 'Node.js was not found in PATH. Claude Code needs Node.js 18 or newer to start the server (node-v24 MSI from the offline kit). Claude Desktop extensions (.mcpb) use the Node.js built into Claude Desktop.'
}

$jsonPath = $entry.Replace('\', '\\')
Write-Host ''
Write-Host 'Next steps'
Write-Host '  1. Start Revit. On the first start choose "Always Load" in the unsigned add-in dialog.'
Write-Host '     The "Revit MCP" panel appears on the Add-Ins tab.'
Write-Host '  2. Register the server in your client:'
Write-Host ''
Write-Host '     Claude Code:'
Write-Host "       claude mcp add --scope user revit -- node `"$entry`""
Write-Host ''
Write-Host '     Claude Desktop: install the revit-mcp-*.mcpb file from this folder (Settings > Extensions),'
Write-Host '     or add to claude_desktop_config.json under "mcpServers":'
Write-Host "       `"revit`": { `"command`": `"node`", `"args`": [`"$jsonPath`"] }"
Write-Host ''
Write-Host '  3. Ask the assistant: "Check the Revit connection".'
