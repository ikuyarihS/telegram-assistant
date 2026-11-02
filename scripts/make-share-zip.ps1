# Make a zip of TG Kit to give to other people: dist\TGKit-<version>.zip
# It holds only the files tracked by git (the code, installers and docs). It never holds a
# data folder, a login, .venv or anything listed in .gitignore.
# Run:  powershell -ExecutionPolicy Bypass -File scripts\make-share-zip.ps1
$ErrorActionPreference = 'Stop'
$Kit = Split-Path -Parent $PSScriptRoot
Set-Location $Kit

$line = Select-String -Path (Join-Path $Kit 'pyproject.toml') -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $line) { Write-Host 'Could not read the version from pyproject.toml.'; exit 1 }
$Version = $line.Matches[0].Groups[1].Value

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host 'git is needed to make the zip (it decides which files belong in it).'
    exit 1
}
$dirty = git status --porcelain
if ($dirty) {
    Write-Host 'Note: some changes are not committed yet. The zip holds the last commit only:'
    Write-Host ($dirty -join "`n")
}
$Dist = Join-Path $Kit 'dist'
New-Item -ItemType Directory -Force $Dist | Out-Null
$Zip = Join-Path $Dist "TGKit-$Version.zip"
if (Test-Path $Zip) {
    Write-Host "$Zip already exists. Move it away first, or raise the version in pyproject.toml."
    exit 1
}
git archive --format=zip --prefix="TG Kit/" -o "$Zip" HEAD
if ($LASTEXITCODE -ne 0) { Write-Host 'git archive failed.'; exit 1 }
Write-Host "Made $Zip"
Write-Host 'Send it to the other person. They unzip it and double-click Install-Windows.bat'
Write-Host '(on a Mac: Install-Mac.command). For a later version they unzip the new zip over'
Write-Host 'the old folder and double-click Update-Windows.bat.'
