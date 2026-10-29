# TG Kit installer for Windows. Double-click Install-Windows.bat rather than running this directly.
# It creates a private Python environment in this folder (.venv), installs TG Kit into it,
# adds the tgkit command and a desktop icon, registers the Claude Code plugin if Claude is
# installed, and starts the first-time setup.
# Set TGKIT_NO_SYSTEM_CHANGES=1 (or pass -NoSystemChanges) to skip PATH, icon and Claude.
param([switch]$NoSystemChanges)

$ErrorActionPreference = 'Stop'
$Kit = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $Kit '.venv'
$VPy = Join-Path $Venv 'Scripts\python.exe'
$Exe = Join-Path $Venv 'Scripts\tgkit.exe'
if ($env:TGKIT_NO_SYSTEM_CHANGES) { $NoSystemChanges = $true }

function Say($text) { Write-Host $text }
function Fail($text) {
    Write-Host ''
    Write-Host $text -ForegroundColor Yellow
    exit 1
}

function Find-Python {
    foreach ($c in @(@('py', '-3'), @('python'), @('python3'))) {
        $exe = $c[0]
        $pre = @($c | Select-Object -Skip 1)
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        try {
            $ok = & $exe @pre -c 'import sys; print(int(sys.version_info >= (3, 10)))' 2>$null
            if ($LASTEXITCODE -eq 0 -and "$ok".Trim() -eq '1') { return ,(@($exe) + $pre) }
        } catch { }
    }
    return $null
}

Say 'Installing TG Kit...'
Say ''

$py = Find-Python
if (-not $py) {
    Say 'TG Kit needs Python (free). It is not installed on this computer yet.'
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        $answer = Read-Host 'Install Python now? Type Y and press Enter (or just press Enter to skip)'
        if ($answer -match '^[Yy]') {
            winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
            $env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine')
            $py = Find-Python
        }
    }
    if (-not $py) {
        Fail "Please install Python from https://www.python.org/downloads/`n(on its first screen, tick 'Add python.exe to PATH'), then double-click Install-Windows.bat again."
    }
}
$pyExe = $py[0]
$pyArgs = @($py | Select-Object -Skip 1)

Say "Step 1 of 3: preparing a private Python for TG Kit (in $Venv)..."
if (-not (Test-Path $VPy)) {
    & $pyExe @pyArgs -m venv "$Venv"
    if ($LASTEXITCODE -ne 0) { Fail "Could not create $Venv. Is the TG Kit folder writable?" }
}

Say 'Step 2 of 3: installing TG Kit (needs internet, about 1 minute)...'
& $VPy -m pip install --quiet --disable-pip-version-check --upgrade pip
& $VPy -m pip install --quiet --disable-pip-version-check "$Kit"
if ($LASTEXITCODE -ne 0) { Fail 'Installing failed. Check your internet connection and double-click Install-Windows.bat again.' }

if (-not $NoSystemChanges) {
    Say 'Step 3 of 3: adding the tgkit command and a desktop icon...'
    try {
        $Bin = Join-Path $Kit 'bin'
        New-Item -ItemType Directory -Force $Bin | Out-Null
        Set-Content -Path (Join-Path $Bin 'tgkit.cmd') -Value '@"%~dp0..\.venv\Scripts\tgkit.exe" %*' -Encoding ASCII
        # Claude Code runs commands in Git Bash, which never runs a .cmd: give it an sh shim too.
        [IO.File]::WriteAllText((Join-Path $Bin 'tgkit'), "#!/bin/sh`nexec `"`$(dirname `"`$0`")/../.venv/Scripts/tgkit.exe`" `"`$@`"`n", (New-Object Text.UTF8Encoding $false))
        $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
        if (-not (($userPath -split ';') -contains $Bin)) {
            if ($userPath) { $newPath = "$userPath;$Bin" } else { $newPath = $Bin }
            [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
        }
        $env:Path = "$env:Path;$Bin"
        $desktop = [Environment]::GetFolderPath('Desktop')
        $link = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop 'TG Kit.lnk'))
        $link.TargetPath = $Exe
        $link.WorkingDirectory = $Kit
        $link.Description = 'TG Kit: search, summarize and track tasks from Telegram'
        $link.Save()
    } catch {
        Say "Could not add the command or the icon ($($_.Exception.Message)). You can still run: $Exe"
    }
    if (Get-Command claude -ErrorAction SilentlyContinue) {
        Say 'Adding TG Kit to Claude Code...'
        try {
            & claude plugin marketplace add "$Kit" | Out-Null
            & claude plugin install tgkit@tgkit | Out-Null
            if ($LASTEXITCODE -ne 0) { throw 'claude returned an error' }
        } catch {
            Say "Could not add it to Claude automatically. In Claude Code type:  /plugin marketplace add $Kit   and then:  /plugin install tgkit@tgkit"
        }
    } else {
        Say '(Claude Code is not installed: skipped. See README, part "Use it with Claude".)'
    }
}

Say ''
Say 'Installed! The first-time setup starts now.'
Say ''
& $Exe setup
Say ''
Say 'Next time, open TG Kit with the "TG Kit" icon on your desktop, or type tgkit in a new terminal window.'
