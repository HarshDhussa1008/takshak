# Install takshak as a Claude Code plugin, then set up a project.
#
#   .\install.ps1 [-ProjectDir DIR] [-Team] [-Local] [-Repo OWNER/REPO]
#
# Re-running is safe. Updates arrive through the marketplace, not this script:
#   claude plugin marketplace update takshak   (or enable auto-update in /plugin)

param(
    [string]$ProjectDir = "",
    [switch]$Team,
    [switch]$Local,
    [string]$Repo = "HarshDhussa1008/takshak"
)

$ErrorActionPreference = "Stop"
$FrameworkDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Error "Claude Code CLI not found on PATH. Install it first: https://code.claude.com/docs/en/setup"
    exit 1
}

$Source = if ($Local) { $FrameworkDir } else { $Repo }

Write-Host "=== takshak ==="
Write-Host "Marketplace: $Source"
& claude plugin marketplace add $Source
if ($LASTEXITCODE -ne 0) { Write-Host "  (marketplace already added)" }
& claude plugin install takshak@takshak --scope user
Write-Host "  Plugin installed. In an open session run /reload-plugins."
Write-Host "  Turn on auto-update once: /plugin -> Marketplaces -> takshak -> Enable auto-update"

if ($ProjectDir) {
    $python = $null
    foreach ($candidate in @("python", "python3", "py")) {
        if (Get-Command $candidate -ErrorAction SilentlyContinue) {
            & $candidate -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
            if ($LASTEXITCODE -eq 0) { $python = $candidate; break }
        }
    }
    if (-not $python) { Write-Error "Python 3.10+ not found (tried python, python3, py)"; exit 1 }

    Write-Host ""
    Write-Host "Initialising $ProjectDir ..."
    $bootstrapArgs = @("$FrameworkDir\tools\bootstrap.py", "init", "--project", $ProjectDir,
                       "--marketplace-repo", $Repo, "--no-statusline")
    if ($Team) { $bootstrapArgs += "--team" }
    & $python @bootstrapArgs
    Write-Host ""
    Write-Host "Open Claude Code in $ProjectDir and run /takshak:init once more -- it wires the"
    Write-Host "budget statusline (needs the plugin's data dir) and asks for anything it couldn't detect."
} else {
    Write-Host ""
    Write-Host "Next: open Claude Code in your project and run /takshak:init"
}
