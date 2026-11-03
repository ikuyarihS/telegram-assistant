# Publish a GitHub release of Telegram Assistant: tag v<version>, with
# dist\TelegramAssistant-<version>.zip and its .sha256 attached. The app's Check for updates
# reads this release; Update downloads the zip and refuses it unless it matches the .sha256.
#
# Run by the maintainer, by hand, after the commit is pushed (nothing else runs this):
#   powershell -ExecutionPolicy Bypass -File scripts\make-share-zip.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\release.ps1 [-Notes notes.md] [-Draft]
# Needs the GitHub CLI (gh), logged in with: gh auth login
param([string]$Notes = '', [switch]$Draft)
$ErrorActionPreference = 'Stop'
$Kit = Split-Path -Parent $PSScriptRoot
Set-Location $Kit
$Repo = 'ikuyarihS/telegram-assistant'

$line = Select-String -Path (Join-Path $Kit 'pyproject.toml') -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $line) { Write-Host 'Could not read the version from pyproject.toml.'; exit 1 }
$Version = $line.Matches[0].Groups[1].Value
$Tag = "v$Version"
$Zip = Join-Path $Kit "dist\TelegramAssistant-$Version.zip"
$Sum = "$Zip.sha256"

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { Write-Host 'The GitHub CLI (gh) is needed: https://cli.github.com'; exit 1 }
foreach ($f in @($Zip, $Sum)) {
    if (-not (Test-Path $f)) { Write-Host "$f is missing. Run scripts\make-share-zip.ps1 first."; exit 1 }
}
$want = ((Get-Content -LiteralPath $Sum -TotalCount 1) -split '\s+')[0].ToLowerInvariant()
$got = (Get-FileHash -Algorithm SHA256 -LiteralPath $Zip).Hash.ToLowerInvariant()
if ($want -ne $got) { Write-Host "$Sum does not match the zip. Make them again with make-share-zip.ps1."; exit 1 }
if (git status --porcelain) { Write-Host 'Some changes are not committed. Commit them, make the zip again, then release.'; exit 1 }
git fetch --quiet origin
$head = (git rev-parse HEAD).Trim()
if (-not (git branch -r --contains $head)) { Write-Host 'This commit is not pushed yet. Push it first, then release.'; exit 1 }

$ghArgs = @('release', 'create', $Tag, $Zip, $Sum, '--repo', $Repo, '--target', $head, '--title', "Telegram Assistant $Version")
if ($Notes) { $ghArgs += @('--notes-file', $Notes) } else { $ghArgs += @('--generate-notes') }
if ($Draft) { $ghArgs += '--draft' }
& gh @ghArgs
if ($LASTEXITCODE -ne 0) { Write-Host 'gh release create failed (nothing else was changed).'; exit 1 }
Write-Host "Released $Tag on https://github.com/$Repo/releases/tag/$Tag"
