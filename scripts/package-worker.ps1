[CmdletBinding()]
param(
    [ValidateSet("x86_64-pc-windows-msvc")]
    [string]$TargetTriple = "x86_64-pc-windows-msvc",
    [Parameter(Mandatory = $true)] [string]$FfmpegPath,
    [Parameter(Mandatory = $true)] [string]$ExpectedFfmpegSha256,
    [Parameter(Mandatory = $true)] [string]$FfprobePath,
    [Parameter(Mandatory = $true)] [string]$ExpectedFfprobeSha256,
    [Parameter(Mandatory = $true)] [string]$FfmpegLicensePath,
    [Parameter(Mandatory = $true)] [string]$FfmpegSourceUrl,
    [Parameter(Mandatory = $true)] [string]$DepthModelPath,
    [Parameter(Mandatory = $true)] [string]$ExpectedDepthModelSha256,
    [Parameter(Mandatory = $true)] [string]$DepthModelLicensePath,
    [Parameter(Mandatory = $true)] [string]$DepthModelSourceUrl
)

$ErrorActionPreference = "Stop"
$workspacePath = Split-Path -Parent $PSScriptRoot
Set-Location $workspacePath

function Resolve-ReviewedFile {
    param(
        [Parameter(Mandatory = $true)] [string]$Path,
        [Parameter(Mandatory = $true)] [string]$ExpectedSha256,
        [Parameter(Mandatory = $true)] [string]$Label
    )

    if ($ExpectedSha256 -notmatch '^[0-9a-fA-F]{64}$') {
        throw "$Label expected SHA-256 must be exactly 64 hexadecimal characters."
    }
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
        throw "$Label must be an existing file."
    }
    $actual = (Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash.ToLowerInvariant()
    $expected = $ExpectedSha256.ToLowerInvariant()
    if ($actual -ne $expected) {
        throw "$Label SHA-256 does not match the independently reviewed value."
    }
    return [PSCustomObject]@{ Path = $resolved; Sha256 = $expected }
}

function Resolve-LicenseFile {
    param(
        [Parameter(Mandatory = $true)] [string]$Path,
        [Parameter(Mandatory = $true)] [string]$Label
    )
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
        throw "$Label license must be an existing file."
    }
    return $resolved
}

function Assert-HttpsSourceUrl {
    param(
        [Parameter(Mandatory = $true)] [string]$Url,
        [Parameter(Mandatory = $true)] [string]$Label
    )
    try {
        $uri = [System.Uri]::new($Url)
    }
    catch {
        throw "$Label source URL is not valid."
    }
    if (-not $uri.IsAbsoluteUri -or $uri.Scheme -ne 'https') {
        throw "$Label source URL must be an absolute HTTPS URL."
    }
}

$pythonPath = ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Run scripts/bootstrap.ps1 first."
}

& $pythonPath -c "import PyInstaller, torch"
if ($LASTEXITCODE -ne 0) {
    throw "Release modules are missing. Run scripts/bootstrap.ps1 -ReleasePackaging on the controlled build machine."
}

$ffmpeg = Resolve-ReviewedFile $FfmpegPath $ExpectedFfmpegSha256 "FFmpeg"
$ffprobe = Resolve-ReviewedFile $FfprobePath $ExpectedFfprobeSha256 "ffprobe"
$depthModel = Resolve-ReviewedFile $DepthModelPath $ExpectedDepthModelSha256 "Depth model"
$ffmpegLicense = Resolve-LicenseFile $FfmpegLicensePath "FFmpeg"
$depthModelLicense = Resolve-LicenseFile $DepthModelLicensePath "Depth model"
Assert-HttpsSourceUrl $FfmpegSourceUrl "FFmpeg"
Assert-HttpsSourceUrl $DepthModelSourceUrl "Depth model"

$ffmpegBuildConfiguration = @(& $ffmpeg.Path -hide_banner -buildconf 2>&1)
if ($LASTEXITCODE -ne 0 -or $ffmpegBuildConfiguration.Count -eq 0) {
    throw "Reviewed FFmpeg binary did not report its build configuration."
}

& $pythonPath -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --name "aistereo-worker" `
    --paths "python" `
    --distpath "build\worker" `
    --workpath "build\pyinstaller" `
    "scripts\worker_entry.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

$binaryDir = "src-tauri\binaries"
$toolResourceDir = "src-tauri\resources\tools"
$modelResourceDir = "src-tauri\resources\models"
New-Item -ItemType Directory -Force -Path $binaryDir, $toolResourceDir, $modelResourceDir | Out-Null

$builtExecutable = "build\worker\aistereo-worker.exe"
if (-not (Test-Path -LiteralPath $builtExecutable -PathType Leaf)) {
    throw "PyInstaller did not produce the expected one-file worker executable."
}
$sidecarExecutable = Join-Path $binaryDir "aistereo-worker-$TargetTriple.exe"
Copy-Item -LiteralPath $builtExecutable -Destination $sidecarExecutable -Force

Copy-Item -LiteralPath $ffmpeg.Path -Destination (Join-Path $toolResourceDir "ffmpeg.exe") -Force
Copy-Item -LiteralPath $ffprobe.Path -Destination (Join-Path $toolResourceDir "ffprobe.exe") -Force
Copy-Item -LiteralPath $ffmpegLicense -Destination (Join-Path $toolResourceDir "FFmpeg-LICENSE.txt") -Force
@(
    "Source and corresponding-source offer: $FfmpegSourceUrl"
    "FFmpeg SHA-256: $($ffmpeg.Sha256)"
    "ffprobe SHA-256: $($ffprobe.Sha256)"
    "ffprobe SHA-256: $($ffprobe.Sha256)"
    ""
    "Reviewed FFmpeg build configuration:"
    $ffmpegBuildConfiguration
) | Set-Content -LiteralPath (Join-Path $toolResourceDir "FFmpeg-SOURCE.txt") -Encoding utf8

$modelDestination = Join-Path $modelResourceDir "video_depth_anything_small.torchscript"
Copy-Item -LiteralPath $depthModel.Path -Destination $modelDestination -Force
Copy-Item -LiteralPath $depthModelLicense -Destination (Join-Path $modelResourceDir "DEPTH-MODEL-LICENSE.txt") -Force
$depthModel.Sha256 | Set-Content -LiteralPath (Join-Path $modelResourceDir "DEPTH-MODEL.sha256") -Encoding ascii -NoNewline
@(
    "Reviewed model/adaptor source: $DepthModelSourceUrl"
    "Packaged artifact SHA-256: $($depthModel.Sha256)"
    "Automatic model download: disabled"
) | Set-Content -LiteralPath (Join-Path $modelResourceDir "DEPTH-MODEL-SOURCE.txt") -Encoding utf8

Write-Host "One-file worker staged at $sidecarExecutable"
Write-Host "Reviewed FFmpeg/ffprobe and depth-model resources staged under src-tauri\resources."
Write-Warning "Code-sign the final installer and retain the reviewed source, license, and hash records."
