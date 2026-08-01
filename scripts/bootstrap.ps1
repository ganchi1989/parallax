[CmdletBinding()]
param(
    [switch]$SkipNode,
    [switch]$SkipPython,
    [switch]$ProvisionMediaTools,
    [switch]$ReleasePackaging
)

$workspacePath = Split-Path -Parent $PSScriptRoot
Set-Location $workspacePath

function Require-Command([string]$CommandName, [string]$InstallHint) {
    if (-not (Get-Command $CommandName -ErrorAction SilentlyContinue)) {
        throw "Missing $CommandName. $InstallHint"
    }
}

if ($ReleasePackaging -and $ProvisionMediaTools) {
    throw "-ProvisionMediaTools installs GPLv3 development tools and cannot be combined with -ReleasePackaging. Release media tools must go through the reviewed staging workflow."
}

$mediaTools = $null
if (-not $ReleasePackaging) {
    . (Join-Path $PSScriptRoot "media-tools.ps1")
    $mediaTools = if ($ProvisionMediaTools) {
        Install-AIStereoMediaTools
    } else {
        Resolve-AIStereoMediaTools
    }
    $env:AISTEREO_FFMPEG_PATH = $mediaTools.FfmpegPath
    $env:AISTEREO_FFPROBE_PATH = $mediaTools.FfprobePath
    Write-Host "Media tools validated: FFmpeg/FFprobe $($mediaTools.Version) ($($mediaTools.Source))." -ForegroundColor Green
}

if (-not $SkipNode) {
    Require-Command "node" "Install Node.js 20 or newer."
    Require-Command "npm.cmd" "Install npm with Node.js."
    npm.cmd install
    if ($LASTEXITCODE -ne 0) { throw "npm install failed." }
}

if (-not $SkipPython) {
    $pythonExecutable = $null
    $pythonPrefixArguments = @()
    $pythonCheck = "import struct,sys; assert sys.version_info >= (3, 11) and struct.calcsize('P') * 8 == 64"

    # `py -3` selects the newest installed Python 3 runtime. Pinning `-3.11`
    # incorrectly rejects newer supported interpreters such as Python 3.12.
    if (Get-Command "py" -ErrorAction SilentlyContinue) {
        & py -3 -c $pythonCheck 2>$null
        if ($LASTEXITCODE -eq 0) {
            $pythonExecutable = "py"
            $pythonPrefixArguments = @("-3")
        }
    }
    if (-not $pythonExecutable -and (Get-Command "python" -ErrorAction SilentlyContinue)) {
        & python -c $pythonCheck 2>$null
        if ($LASTEXITCODE -eq 0) {
            $pythonExecutable = "python"
        }
    }
    if (-not $pythonExecutable) {
        throw "64-bit Python 3.11 or newer is required for supported development."
    }

    $venvPython = ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        & $venvPython -c $pythonCheck 2>$null
        if ($LASTEXITCODE -ne 0) {
            throw "The existing .venv does not use 64-bit Python 3.11+. Remove .venv and run bootstrap again."
        }
    } else {
        & $pythonExecutable @pythonPrefixArguments -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "Could not create .venv." }
    }

    & $venvPython -m pip install --upgrade pip
    # The desktop workflow always needs shot detection/video decoding. Keep the
    # heavyweight depth runtime and release freezer opt-in.
    $pythonExtras = if ($ReleasePackaging) { ".[dev,video,depth,packaging]" } else { ".[dev,video]" }
    & $venvPython -m pip install -e $pythonExtras
    if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed." }

    & $venvPython -c "import sys; print(f'Python environment ready: {sys.version.split()[0]} ({sys.executable})')"
}

if ($ReleasePackaging) {
    Write-Host "Release packaging environment installed. Reviewed FFmpeg/model files are still required."
} else {
    Write-Host "Bootstrap complete. Start the desktop with '.\scripts\dev.ps1 -Target desktop'. Use -ReleasePackaging only on a controlled release workstation."
}
