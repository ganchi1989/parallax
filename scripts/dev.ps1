[CmdletBinding()]
param(
    [ValidateSet("web", "desktop", "worker")]
    [string]$Target = "web",

    [switch]$ProvisionMediaTools
)

$workspacePath = Split-Path -Parent $PSScriptRoot
Set-Location $workspacePath

if ($Target -eq "web") {
    if ($ProvisionMediaTools) {
        throw "-ProvisionMediaTools applies only to the desktop and worker targets. The browser demo does not use FFmpeg."
    }
    npm.cmd run dev
    exit $LASTEXITCODE
}

. (Join-Path $PSScriptRoot "media-tools.ps1")
$mediaTools = if ($ProvisionMediaTools) {
    Install-AIStereoMediaTools
} else {
    Resolve-AIStereoMediaTools
}
$env:AISTEREO_FFMPEG_PATH = $mediaTools.FfmpegPath
$env:AISTEREO_FFPROBE_PATH = $mediaTools.FfprobePath
Write-Host "Using FFmpeg/FFprobe $($mediaTools.Version) from $($mediaTools.Source)." -ForegroundColor Green

if ($Target -eq "worker") {
    if (-not (Test-Path ".venv\Scripts\python.exe")) {
        throw "Run scripts/bootstrap.ps1 first."
    }
    & ".venv\Scripts\python.exe" -m aistereo.worker
    exit $LASTEXITCODE
}

$cargoCommand = Get-Command cargo -ErrorAction SilentlyContinue
if (-not $cargoCommand) {
    $cargoCandidate = Join-Path ([Environment]::GetFolderPath("UserProfile")) ".cargo\bin\cargo.exe"
    if (Test-Path $cargoCandidate) {
        $env:PATH = "$(Split-Path $cargoCandidate);$env:PATH"
        $cargoCommand = Get-Command cargo -ErrorAction SilentlyContinue
    }
}
if (-not $cargoCommand) {
    throw "Rust stable is required for Tauri desktop development."
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    throw "Run scripts/bootstrap.ps1 first."
}

$env:AISTEREO_PYTHON = (Resolve-Path ".venv\Scripts\python.exe").Path
npm.cmd run tauri:dev
exit $LASTEXITCODE
