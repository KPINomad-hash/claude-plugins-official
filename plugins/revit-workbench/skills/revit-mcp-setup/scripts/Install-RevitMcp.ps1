#Requires -Version 5.1
<#
.SYNOPSIS
  One-step install of the Horizun and BIMwright Revit MCP servers from the offline kit,
  and registration in Claude Code and Claude Desktop.

.DESCRIPTION
  Put this script (and Install-RevitMcp.cmd) into the offline kit folder, next to
  horizun-mcp-*-setup.exe and RvtMcp.Setup-*-win-x64.zip, close Revit, and run it.
  No administrator rights and no internet access are needed.

  Steps:
    1. checks that Revit is closed and finds the installers;
    2. verifies their SHA-256 (against Revit_MCP_Offline_SHA256SUMS.txt in the kit
       folder when present, otherwise against the table built into this script);
    3. installs Horizun silently, without letting it register clients;
    4. installs BIMwright with its own install.ps1, without letting it register clients;
    5. registers both servers in Claude Code and Claude Desktop with the full
       modeling toolsets (a backup of every edited config is made first);
    6. verifies what was installed and prints what is left for a person to do.

.PARAMETER KitDir
  Folder with the installers. Default: the folder this script is in.

.PARAMETER Servers
  Which servers to install: Horizun, Bimwright. Default: both.

.PARAMETER Clients
  Which clients to register: ClaudeCode, ClaudeDesktop, None. Default: both;
  a client that is not installed is skipped with a note.

.PARAMETER BimwrightToolsets
  Value for BIMwright --toolsets. Default 'all' (the whole catalog).

.PARAMETER EnableSendCode
  Leave BIMwright's revit_send_code_to_revit (arbitrary C# inside Revit) enabled.
  Off by default: the server is registered with --disable-send-code.

.PARAMETER HorizunProfile
  Horizun tool profile. Default 'completo' (every tool); 'esencial' is the small one.

.PARAMETER SkipHashCheck
  Install even when an installer's SHA-256 is unknown. A known but WRONG hash always stops.

.PARAMETER DryRun
  Show every step and change nothing.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\Install-RevitMcp.ps1
.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\Install-RevitMcp.ps1 -Servers Horizun -Clients ClaudeCode -DryRun
#>
[CmdletBinding()]
param(
    [string]$KitDir,
    [ValidateSet('Horizun', 'Bimwright')]
    [string[]]$Servers = @('Horizun', 'Bimwright'),
    [ValidateSet('ClaudeCode', 'ClaudeDesktop', 'None')]
    [string[]]$Clients = @('ClaudeCode', 'ClaudeDesktop'),
    [string]$BimwrightToolsets = 'all',
    [switch]$EnableSendCode,
    [ValidateSet('completo', 'esencial')]
    [string]$HorizunProfile = 'completo',
    [switch]$SkipHashCheck,
    [switch]$DryRun
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

# SHA-256 of the installers this script was written for (from the offline kit list).
$KnownHashes = @{
    'horizun-mcp-4.0.0-setup.exe'     = '71de30e150d49be43647790e36c73370f01aa4f232a93bdd2c1ea898c5d0ab95'
    'RvtMcp.Setup-v1.2.2-win-x64.zip' = '0322b2f7c3028f4d77991f66ba4cf601e4c1aa9caf58079ea017d5738587e535'
}

$HorizunServerName = 'horizun-revit'
$BimwrightServerName = 'rvt-mcp'

function Write-Step([string]$Text) { Write-Host ''; Write-Host "== $Text" -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host "   OK    $Text" -ForegroundColor Green }
function Write-Note([string]$Text) { Write-Host "   NOTE  $Text" -ForegroundColor Yellow }

function Get-HorizunExe { Join-Path $env:LOCALAPPDATA 'Programs\Horizun\MCP\server\horizun-mcp.exe' }
function Get-BimwrightExe { Join-Path $env:LOCALAPPDATA 'Bimwright\rvt-mcp\server\current\rvt-mcp.exe' }

function Find-KitFile([string]$Dir, [string]$Pattern) {
    $found = @(Get-ChildItem -LiteralPath $Dir -Filter $Pattern -File -ErrorAction SilentlyContinue | Sort-Object Name)
    if ($found.Count -eq 0) { throw "Not found in ${Dir}: $Pattern" }
    if ($found.Count -gt 1) { throw "Several files match $Pattern in ${Dir}: $($found.Name -join ', '). Leave one." }
    return $found[0]
}

function Read-HashList([string]$Path) {
    # Lines of "<sha256> <file name>"; Markdown-escaped underscores ("\_") are accepted.
    $table = @{}
    if (-not (Test-Path -LiteralPath $Path)) { return $table }
    foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
        if ($line -match '^\s*([0-9a-fA-F]{64})\s+\*?(.+?)\s*$') {
            $table[$Matches[2].Replace('\_', '_')] = $Matches[1].ToLowerInvariant()
        }
    }
    return $table
}

function Test-KitFileHash($File, [hashtable]$Expected, [bool]$AllowUnknown) {
    $actual = (Get-FileHash -LiteralPath $File.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    if (-not $Expected.ContainsKey($File.Name)) {
        if ($AllowUnknown) { Write-Note "$($File.Name): no reference SHA-256, not verified ($actual)"; return }
        throw "$($File.Name): no reference SHA-256 for this file. Put Revit_MCP_Offline_SHA256SUMS.txt next to it, or pass -SkipHashCheck if you trust the file. Actual: $actual"
    }
    if ($actual -ne $Expected[$File.Name]) {
        throw "$($File.Name): SHA-256 mismatch. Expected $($Expected[$File.Name]), actual $actual. The file is damaged or is not the one from the kit. Nothing was installed from it."
    }
    Write-Ok "$($File.Name): SHA-256 matches"
}

function Read-KeyValueFile([string]$Path) {
    $result = @{}
    if (Test-Path -LiteralPath $Path) {
        foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
            $i = $line.IndexOf('=')
            if ($i -gt 0) { $result[$line.Substring(0, $i).Trim()] = $line.Substring($i + 1).Trim() }
        }
    }
    return $result
}

function Get-ServerDefinitions([string[]]$Which, [string]$Toolsets, [bool]$SendCode, [string]$HzProfile) {
    $list = @()
    if ($Which -contains 'Horizun') {
        $list += [pscustomobject]@{
            Name    = $HorizunServerName
            Command = (Get-HorizunExe)
            Args    = @()
            Env     = [ordered]@{ HORIZUN_PROFILE = $HzProfile; HORIZUN_SCHEMA_MODE = 'compact' }
        }
    }
    if ($Which -contains 'Bimwright') {
        $serverArgs = @('--toolsets', $Toolsets)
        if (-not $SendCode) { $serverArgs += '--disable-send-code' }
        $list += [pscustomobject]@{
            Name    = $BimwrightServerName
            Command = (Get-BimwrightExe)
            Args    = $serverArgs
            Env     = [ordered]@{}
        }
    }
    return , $list
}

function Get-ClaudeDesktopConfigPath {
    # A Store/MSIX install never reads %APPDATA%\Claude, so its package folder is checked first.
    $packages = Join-Path $env:LOCALAPPDATA 'Packages'
    if (Test-Path -LiteralPath $packages) {
        foreach ($package in Get-ChildItem -LiteralPath $packages -Directory -ErrorAction SilentlyContinue) {
            $dir = Join-Path (Join-Path (Join-Path $package.FullName 'LocalCache') 'Roaming') 'Claude'
            if (Test-Path -LiteralPath $dir) { return (Join-Path $dir 'claude_desktop_config.json') }
        }
    }
    $classic = Join-Path $env:APPDATA 'Claude'
    if (Test-Path -LiteralPath $classic) { return (Join-Path $classic 'claude_desktop_config.json') }
    return $null
}

function Merge-ClaudeDesktopConfig([string]$Json, $Definitions) {
    # Returns the new config text. Other servers and settings are kept as they are.
    $config = $null
    if (-not [string]::IsNullOrWhiteSpace($Json)) { $config = $Json | ConvertFrom-Json }
    if ($null -eq $config) { $config = New-Object psobject }
    if (-not ($config -is [System.Management.Automation.PSCustomObject])) {
        throw 'claude_desktop_config.json is not a JSON object; it was left untouched.'
    }
    $existing = $config.PSObject.Properties['mcpServers']
    if ($null -eq $existing -or -not ($existing.Value -is [System.Management.Automation.PSCustomObject])) {
        $config | Add-Member -NotePropertyName mcpServers -NotePropertyValue (New-Object psobject) -Force
    }
    foreach ($definition in $Definitions) {
        $entry = [ordered]@{ command = $definition.Command }
        if (@($definition.Args).Count -gt 0) { $entry['args'] = @($definition.Args) }
        if ($definition.Env.Count -gt 0) { $entry['env'] = $definition.Env }
        $config.mcpServers | Add-Member -NotePropertyName $definition.Name -NotePropertyValue ([pscustomobject]$entry) -Force
    }
    return ($config | ConvertTo-Json -Depth 32)
}

function Register-ClaudeDesktop($Definitions, [bool]$IsDryRun) {
    $path = Get-ClaudeDesktopConfigPath
    if (-not $path) {
        Write-Note 'Claude Desktop was not found for this user; skipped.'
        return 'not installed'
    }
    $old = ''
    if (Test-Path -LiteralPath $path) { $old = [System.IO.File]::ReadAllText($path) }
    $new = Merge-ClaudeDesktopConfig $old $Definitions
    if ($IsDryRun) {
        Write-Host "   would write $path (servers: $(@($Definitions | ForEach-Object { $_.Name }) -join ', '))"
        return 'dry run'
    }
    if (Test-Path -LiteralPath $path) {
        $backup = "$path.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        Copy-Item -LiteralPath $path -Destination $backup
        Write-Ok "backup: $backup"
    }
    # No byte-order mark: Claude Desktop reads the file as plain UTF-8 JSON.
    [System.IO.File]::WriteAllText($path, $new, (New-Object System.Text.UTF8Encoding $false))
    Write-Ok "Claude Desktop config updated: $path"
    if (Get-Process -Name 'Claude' -ErrorAction SilentlyContinue) {
        Write-Note 'Claude Desktop is running: quit it completely (tray icon > Quit) and start it again.'
    }
    return 'registered'
}

function Register-ClaudeCode($Definitions, [bool]$IsDryRun) {
    $claude = Get-Command claude -ErrorAction SilentlyContinue
    foreach ($definition in $Definitions) {
        $cli = @('mcp', 'add', '--scope', 'user', $definition.Name)
        foreach ($key in $definition.Env.Keys) { $cli += @('-e', "$key=$($definition.Env[$key])") }
        $cli += @('--', $definition.Command) + @($definition.Args)
        $shown = 'claude ' + (($cli | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' ')
        if (-not $claude -or $IsDryRun) {
            Write-Host "   $shown"
            continue
        }
        # Re-running the script must replace the entry, not fail on "already exists".
        # Windows PowerShell turns a native command's stderr into a terminating error
        # under ErrorActionPreference=Stop, so it is relaxed for these two calls.
        $previous = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            & $claude.Source mcp remove --scope user $definition.Name 2>&1 | Out-Null
            & $claude.Source @cli 2>&1 | Out-Null
            $code = $LASTEXITCODE
        }
        finally { $ErrorActionPreference = $previous }
        if ($code -ne 0) { throw "Claude Code refused: $shown" }
        Write-Ok "Claude Code: $($definition.Name) registered (user scope)"
    }
    if (-not $claude) {
        Write-Note 'The claude command is not in PATH. Run the lines above yourself once Claude Code is installed.'
        return 'not installed'
    }
    if ($IsDryRun) { return 'dry run' }
    return 'registered'
}

function Install-Horizun($Setup, [bool]$IsDryRun) {
    $resultFile = Join-Path ([System.IO.Path]::GetTempPath()) "horizun-install-result-$PID.txt"
    $setupArgs = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/HORIZUNCLIENT=None', "/HORIZUNRESULT=$resultFile")
    if ($IsDryRun) {
        Write-Host "   would run $($Setup.Name) $($setupArgs -join ' ')"
        return
    }
    Remove-Item -LiteralPath $resultFile -Force -ErrorAction SilentlyContinue
    $process = Start-Process -FilePath $Setup.FullName -ArgumentList $setupArgs -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "Horizun setup ended with exit code $($process.ExitCode); nothing may have been installed." }

    # The setup returns 0 even when some Revit years were not deployed: its result file is the truth.
    $result = Read-KeyValueFile $resultFile
    Remove-Item -LiteralPath $resultFile -Force -ErrorAction SilentlyContinue
    if ($result.Count -eq 0) { throw 'Horizun setup left no result file, so the install cannot be confirmed.' }
    if ($result['server_installed'] -ne 'yes') { throw "Horizun server was not installed: $($result['server_failure'])" }
    Write-Ok "Horizun server installed; add-in deployed for Revit: $($result['succeeded'])"
    if ($result['fully_installed'] -ne 'yes') {
        Write-Note "Horizun was not deployed for every Revit year. Failed: $($result['failed']). Those years keep what was installed before."
    }
    if (-not (Test-Path -LiteralPath (Get-HorizunExe))) { throw "Horizun server not found at $(Get-HorizunExe)" }
}

function Install-Bimwright($Zip, [bool]$IsDryRun) {
    if ($IsDryRun) {
        Write-Host "   would unpack $($Zip.Name) and run its install.ps1 -Client none"
        return
    }
    $work = Join-Path ([System.IO.Path]::GetTempPath()) "rvtmcp-setup-$PID"
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    try {
        Expand-Archive -LiteralPath $Zip.FullName -DestinationPath $work
        $installer = @(Get-ChildItem -LiteralPath $work -Recurse -Filter 'install.ps1' -File | Sort-Object { $_.FullName.Length })
        if ($installer.Count -eq 0) { throw 'install.ps1 was not found inside the BIMwright archive.' }
        Get-ChildItem -LiteralPath $work -Recurse -File | Unblock-File
        $shell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        & $shell -NoProfile -ExecutionPolicy Bypass -File $installer[0].FullName -Client none
        if ($LASTEXITCODE -ne 0) { throw "BIMwright install.ps1 ended with exit code $LASTEXITCODE." }
    }
    finally {
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
    $exe = Get-BimwrightExe
    if (-not (Test-Path -LiteralPath $exe)) { throw "BIMwright server not found at $exe" }
    & $exe --help | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'BIMwright server is installed but does not start (rvt-mcp.exe --help failed).' }
    Write-Ok 'BIMwright server installed and starts'
}

function Get-InstalledRevitYears {
    $years = @()
    foreach ($year in 2022..2027) {
        if (Test-Path -LiteralPath (Join-Path $env:ProgramFiles "Autodesk\Revit $year\Revit.exe")) { $years += $year }
    }
    return , $years
}

function Invoke-Main {
    if (-not $KitDir) { $KitDir = $PSScriptRoot }
    if (-not (Test-Path -LiteralPath $KitDir)) { throw "Kit folder not found: $KitDir" }
    $KitDir = (Resolve-Path -LiteralPath $KitDir).Path
    $registerClients = @($Clients | Where-Object { $_ -ne 'None' })
    if ($Clients -contains 'None') { $registerClients = @() }

    Write-Host 'Revit MCP: Horizun + BIMwright'
    Write-Host "Kit folder: $KitDir"
    if ($DryRun) { Write-Note 'DRY RUN: nothing will be changed.' }

    Write-Step 'Checks'
    if (Get-Process -Name 'Revit' -ErrorAction SilentlyContinue) {
        throw 'Revit is running. Close every Revit window and run the script again.'
    }
    $years = Get-InstalledRevitYears
    if ($years.Count -eq 0) { throw 'No Revit 2022-2027 found on this computer. Install Revit first.' }
    Write-Ok "Revit found: $($years -join ', ')"

    $files = @{}
    if ($Servers -contains 'Horizun') { $files['Horizun'] = Find-KitFile $KitDir 'horizun-mcp-*-setup.exe' }
    if ($Servers -contains 'Bimwright') { $files['Bimwright'] = Find-KitFile $KitDir 'RvtMcp.Setup-*-win-x64.zip' }

    $expected = Read-HashList (Join-Path $KitDir 'Revit_MCP_Offline_SHA256SUMS.txt')
    foreach ($name in $KnownHashes.Keys) { if (-not $expected.ContainsKey($name)) { $expected[$name] = $KnownHashes[$name] } }
    foreach ($file in $files.Values) {
        Test-KitFileHash $file $expected ([bool]$SkipHashCheck)
        if (-not $DryRun) { Unblock-File -LiteralPath $file.FullName }
    }

    if ($Servers -contains 'Horizun') {
        Write-Step 'Horizun'
        Install-Horizun $files['Horizun'] ([bool]$DryRun)
    }
    if ($Servers -contains 'Bimwright') {
        Write-Step 'BIMwright'
        Install-Bimwright $files['Bimwright'] ([bool]$DryRun)
    }

    $definitions = Get-ServerDefinitions $Servers $BimwrightToolsets ([bool]$EnableSendCode) $HorizunProfile
    $status = [ordered]@{}
    if ($registerClients -contains 'ClaudeCode') {
        Write-Step 'Claude Code'
        $status['Claude Code'] = Register-ClaudeCode $definitions ([bool]$DryRun)
    }
    if ($registerClients -contains 'ClaudeDesktop') {
        Write-Step 'Claude Desktop'
        $status['Claude Desktop'] = Register-ClaudeDesktop $definitions ([bool]$DryRun)
    }

    Write-Step 'Result'
    Write-Host "   Servers: $($Servers -join ', ')"
    foreach ($key in $status.Keys) { Write-Host "   ${key}: $($status[$key])" }
    Write-Host ''
    Write-Host 'Left for you to do:'
    Write-Host '  - Start Revit. If it asks about an add-in, choose "Always Load".'
    Write-Host '  - Open a COPY of a project.'
    if ($Servers -contains 'Bimwright') {
        Write-Host '  - BIMwright starts switched OFF when another MCP add-in (Horizun) is installed:'
        Write-Host '    turn it on with Add-Ins > RvtMcp > MCP: ON when you want to use it.'
    }
    Write-Host '  - Restart Claude Code / Claude Desktop and ask: "Check the Revit connection".'
    if (-not $EnableSendCode -and ($Servers -contains 'Bimwright')) {
        Write-Host ''
        Write-Host 'Arbitrary C# execution in BIMwright is switched off (--disable-send-code).'
    }
}

# Dot-sourcing loads the functions without installing anything (used by the tests).
if ($MyInvocation.InvocationName -ne '.') {
    try {
        Invoke-Main
    }
    catch {
        Write-Host ''
        Write-Host "FAILED: $($_.Exception.Message)" -ForegroundColor Red
        exit 1
    }
}
