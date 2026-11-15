# Checks the parts of Install-RevitMcp.ps1 that do not need Windows, Revit or the
# real installers. Run:  pwsh -NoProfile -File Install-RevitMcp.Tests.ps1
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Install-RevitMcp.ps1')

$script:failed = 0
function Check([string]$Name, [bool]$Condition, $Detail = $null) {
    if ($Condition) { Write-Host "ok   $Name" }
    else { $script:failed++; Write-Host "FAIL $Name $Detail" -ForegroundColor Red }
}
function Throws([scriptblock]$Block, [string]$Pattern) {
    try { & $Block | Out-Null; return $false } catch { return ($_.Exception.Message -match $Pattern) }
}

$root = Join-Path ([System.IO.Path]::GetTempPath()) "revit-mcp-install-tests-$PID"
New-Item -ItemType Directory -Path $root -Force | Out-Null
$env:LOCALAPPDATA = Join-Path $root 'Local'
$env:APPDATA = Join-Path $root 'Roaming'
New-Item -ItemType Directory -Path $env:LOCALAPPDATA, $env:APPDATA -Force | Out-Null

try {
    # --- hash list and verification
    $kit = Join-Path $root 'kit'
    New-Item -ItemType Directory -Path $kit | Out-Null
    $file = Join-Path $kit 'horizun-mcp-9.9.9-setup.exe'
    [System.IO.File]::WriteAllText($file, 'payload')
    $hash = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
    $sums = Join-Path $kit 'Revit_MCP_Offline_SHA256SUMS.txt'
    [System.IO.File]::WriteAllLines($sums, @("$hash horizun-mcp-9.9.9-setup.exe  ", (('a' * 64) + ' Revit\_MCP\_other.zip'), 'not a hash line'))
    $table = Read-HashList $sums
    Check 'hash list: plain line' ($table['horizun-mcp-9.9.9-setup.exe'] -eq $hash)
    Check 'hash list: markdown-escaped underscores' ($table.ContainsKey('Revit_MCP_other.zip'))
    Check 'hash list: junk lines ignored' ($table.Count -eq 2)
    Check 'hash list: missing file is empty' ((Read-HashList (Join-Path $kit 'none.txt')).Count -eq 0)

    $item = Find-KitFile $kit 'horizun-mcp-*-setup.exe'
    Check 'find: one match' ($item.Name -eq 'horizun-mcp-9.9.9-setup.exe')
    Check 'find: none is an error' (Throws { Find-KitFile $kit 'RvtMcp.Setup-*.zip' } 'Not found')
    [System.IO.File]::WriteAllText((Join-Path $kit 'horizun-mcp-9.9.8-setup.exe'), 'x')
    Check 'find: two matches is an error' (Throws { Find-KitFile $kit 'horizun-mcp-*-setup.exe' } 'Several files')

    Check 'verify: matching hash passes' (-not (Throws { Test-KitFileHash $item $table $false } '.'))
    Check 'verify: wrong hash stops' (Throws { Test-KitFileHash $item @{ $item.Name = ('b' * 64) } $false } 'mismatch')
    Check 'verify: wrong hash stops even with -SkipHashCheck' (Throws { Test-KitFileHash $item @{ $item.Name = ('b' * 64) } $true } 'mismatch')
    Check 'verify: unknown file stops' (Throws { Test-KitFileHash $item @{} $false } 'no reference')
    Check 'verify: unknown file allowed with -SkipHashCheck' (-not (Throws { Test-KitFileHash $item @{} $true } '.'))

    # --- Horizun result file
    $resultFile = Join-Path $root 'result.txt'
    [System.IO.File]::WriteAllText($resultFile, "version=4.0.0`r`nserver_installed=yes`r`nserver_failure=`r`nsucceeded=2026, 2027`r`nfully_installed=no`r`nnote=a=b`r`n")
    $result = Read-KeyValueFile $resultFile
    Check 'result file: values' ($result['server_installed'] -eq 'yes' -and $result['succeeded'] -eq '2026, 2027' -and $result['server_failure'] -eq '')
    Check 'result file: value may contain =' ($result['note'] -eq 'a=b')
    Check 'result file: missing file is empty' ((Read-KeyValueFile (Join-Path $root 'none.txt')).Count -eq 0)

    # --- server definitions
    $definitions = Get-ServerDefinitions @('Horizun', 'Bimwright') 'all' $false 'completo'
    Check 'definitions: two servers' ($definitions.Count -eq 2)
    Check 'definitions: horizun env' ($definitions[0].Env['HORIZUN_PROFILE'] -eq 'completo' -and $definitions[0].Env['HORIZUN_SCHEMA_MODE'] -eq 'compact')
    Check 'definitions: bimwright args' (($definitions[1].Args -join ' ') -eq '--toolsets all --disable-send-code')
    $withCode = Get-ServerDefinitions @('Bimwright') 'query,create' $true 'completo'
    Check 'definitions: send-code can be left on' ($withCode.Count -eq 1 -and ($withCode[0].Args -join ' ') -eq '--toolsets query,create')

    # --- Claude Desktop config merge
    $fresh = Merge-ClaudeDesktopConfig '' $definitions | ConvertFrom-Json
    Check 'merge: empty config' ($fresh.mcpServers.'rvt-mcp'.args.Count -eq 3 -and $fresh.mcpServers.'horizun-revit'.env.HORIZUN_PROFILE -eq 'completo')
    Check 'merge: no args key when there are none' ($null -eq $fresh.mcpServers.'horizun-revit'.PSObject.Properties['args'])
    Check 'merge: no env key when there is none' ($null -eq $fresh.mcpServers.'rvt-mcp'.PSObject.Properties['env'])

    $existing = '{"globalShortcut":"Ctrl+Space","mcpServers":{"other":{"command":"x","args":["a",{"deep":{"deeper":[1,2]}}]},"rvt-mcp":{"command":"old"}}}'
    $merged = Merge-ClaudeDesktopConfig $existing $definitions | ConvertFrom-Json
    Check 'merge: other settings kept' ($merged.globalShortcut -eq 'Ctrl+Space')
    Check 'merge: other servers kept, nested values intact' ($merged.mcpServers.other.args[1].deep.deeper[1] -eq 2)
    Check 'merge: old entry replaced' ($merged.mcpServers.'rvt-mcp'.command -like '*rvt-mcp.exe')
    $noServers = Merge-ClaudeDesktopConfig '{"a":1}' $definitions | ConvertFrom-Json
    Check 'merge: config without mcpServers' ($noServers.a -eq 1 -and $noServers.mcpServers.'horizun-revit'.command -like '*horizun-mcp.exe')
    Check 'merge: not an object is refused' (Throws { Merge-ClaudeDesktopConfig '[1,2]' $definitions } 'not a JSON object')
    $badServers = Merge-ClaudeDesktopConfig '{"mcpServers":null}' $definitions | ConvertFrom-Json
    Check 'merge: null mcpServers is replaced' ($badServers.mcpServers.'rvt-mcp'.command -like '*rvt-mcp.exe')
    Check 'merge: broken JSON is refused' (Throws { Merge-ClaudeDesktopConfig '{"a":' $definitions } '.')

    # --- Claude Desktop config location and writing
    Check 'desktop: absent client is skipped' ((Register-ClaudeDesktop $definitions $false) -eq 'not installed')
    $classic = Join-Path $env:APPDATA 'Claude'
    New-Item -ItemType Directory -Path $classic | Out-Null
    $classicConfig = Join-Path $classic 'claude_desktop_config.json'
    Check 'desktop: classic path' ((Get-ClaudeDesktopConfigPath) -eq $classicConfig)
    Check 'desktop: dry run writes nothing' ((Register-ClaudeDesktop $definitions $true) -eq 'dry run' -and -not (Test-Path $classicConfig))
    Check 'desktop: first write' ((Register-ClaudeDesktop $definitions $false) -eq 'registered' -and (Test-Path $classicConfig))
    $bytes = [System.IO.File]::ReadAllBytes($classicConfig)
    Check 'desktop: file has no byte-order mark' ($bytes[0] -eq 0x7B)
    Check 'desktop: no backup when there was no file' (@(Get-ChildItem $classic -Filter '*.bak-*').Count -eq 0)
    [System.IO.File]::WriteAllText($classicConfig, $existing)
    Register-ClaudeDesktop $definitions $false | Out-Null
    $backups = @(Get-ChildItem $classic -Filter '*.bak-*')
    Check 'desktop: backup holds the previous text' ($backups.Count -eq 1 -and [System.IO.File]::ReadAllText($backups[0].FullName) -eq $existing)
    Check 'desktop: rewritten file is valid and merged' (([System.IO.File]::ReadAllText($classicConfig) | ConvertFrom-Json).mcpServers.other.command -eq 'x')

    $msix = Join-Path (Join-Path (Join-Path (Join-Path (Join-Path $env:LOCALAPPDATA 'Packages') 'Claude_abc') 'LocalCache') 'Roaming') 'Claude'
    New-Item -ItemType Directory -Path $msix -Force | Out-Null
    Check 'desktop: store install wins over classic folder' ((Get-ClaudeDesktopConfigPath) -eq (Join-Path $msix 'claude_desktop_config.json'))

    # --- Claude Code: without the CLI nothing runs and the commands are printed
    $env:PATH = $root
    $printed = Register-ClaudeCode $definitions $false 6>&1 | Out-String
    Check 'code: reports missing CLI' ($printed -match 'not installed')
    Check 'code: prints horizun command' ($printed -match 'claude mcp add --scope user horizun-revit -e HORIZUN_PROFILE=completo -e HORIZUN_SCHEMA_MODE=compact -- ')
    Check 'code: prints bimwright command' ($printed -match 'rvt-mcp\.exe --toolsets all --disable-send-code')
}
finally {
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}

if ($script:failed -gt 0) { Write-Host "$script:failed check(s) failed" -ForegroundColor Red; exit 1 }
Write-Host 'all checks passed'
