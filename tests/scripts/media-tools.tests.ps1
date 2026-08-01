[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$workspacePath = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $workspacePath "scripts\media-tools.ps1")

$passed = 0
$failed = 0

function Invoke-Test {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [scriptblock]$Action
    )

    try {
        & $Action
        $script:passed += 1
        Write-Host "  PASS $Name" -ForegroundColor Green
    } catch {
        $script:failed += 1
        Write-Host "  FAIL $Name`n       $($_.Exception.Message)" -ForegroundColor Red
    }
}

function Assert-Equal {
    param($Expected, $Actual)
    if ($Expected -ne $Actual) {
        throw "Expected '$Expected' but received '$Actual'."
    }
}

function Assert-ThrowsLike {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Action,

        [Parameter(Mandatory = $true)]
        [string]$Pattern
    )

    try {
        & $Action
    } catch {
        if ($_.Exception.Message -notlike $Pattern) {
            throw "Expected an error like '$Pattern' but received '$($_.Exception.Message)'."
        }
        return
    }
    throw "Expected an error like '$Pattern', but no error was raised."
}

function New-FakeMediaTool {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [ValidateSet("ffmpeg", "ffprobe")]
        [string]$ToolName,

        [Parameter(Mandatory = $true)]
        [string]$Version,

        [switch]$WithoutLibx264
    )

    $encoderLine = if ($WithoutLibx264) { "echo V..... mpeg4 MPEG-4" } else { "echo V..... libx264 H.264" }
    $content = @(
        "@echo off",
        "if /I ""%~2""==""-encoders"" (",
        "  $encoderLine",
        "  exit /b 0",
        ")",
        "echo $ToolName version $Version-test_build",
        "exit /b 0"
    )
    Set-Content -LiteralPath $Path -Value $content -Encoding Ascii
}

$temporaryParent = [IO.Path]::GetTempPath()
$temporaryRoot = Join-Path $temporaryParent ("aistereo-media-tools-tests-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $temporaryRoot -ErrorAction Stop | Out-Null

$originalFfmpegPath = $env:AISTEREO_FFMPEG_PATH
$originalFfprobePath = $env:AISTEREO_FFPROBE_PATH
try {
    $validFfmpeg = Join-Path $temporaryRoot "ffmpeg-valid.cmd"
    $validFfprobe = Join-Path $temporaryRoot "ffprobe-valid.cmd"
    $oldFfprobe = Join-Path $temporaryRoot "ffprobe-old.cmd"
    $limitedFfmpeg = Join-Path $temporaryRoot "ffmpeg-limited.cmd"
    New-FakeMediaTool -Path $validFfmpeg -ToolName "ffmpeg" -Version "8.1.2"
    New-FakeMediaTool -Path $validFfprobe -ToolName "ffprobe" -Version "8.1.2"
    New-FakeMediaTool -Path $oldFfprobe -ToolName "ffprobe" -Version "7.1.1"
    New-FakeMediaTool -Path $limitedFfmpeg -ToolName "ffmpeg" -Version "8.1.2" -WithoutLibx264

    Invoke-Test "accepts the pinned matched tool pair" {
        $pair = Test-AIStereoMediaToolPair -FfmpegPath $validFfmpeg -FfprobePath $validFfprobe -Source "test"
        Assert-Equal "8.1.2" $pair.Version
        Assert-Equal "test" $pair.Source
    }

    Invoke-Test "rejects mismatched FFmpeg and FFprobe versions" {
        Assert-ThrowsLike {
            Test-AIStereoMediaToolPair -FfmpegPath $validFfmpeg -FfprobePath $oldFfprobe | Out-Null
        } "*must come from the same version*"
    }

    Invoke-Test "rejects a pair from different directories" {
        $separateDirectory = Join-Path $temporaryRoot "separate"
        New-Item -ItemType Directory -Path $separateDirectory -ErrorAction Stop | Out-Null
        $separateFfprobe = Join-Path $separateDirectory "ffprobe-valid.cmd"
        New-FakeMediaTool -Path $separateFfprobe -ToolName "ffprobe" -Version "8.1.2"
        Assert-ThrowsLike {
            Test-AIStereoMediaToolPair -FfmpegPath $validFfmpeg -FfprobePath $separateFfprobe | Out-Null
        } "*must be colocated in the same directory*"
    }

    Invoke-Test "rejects a build without libx264" {
        Assert-ThrowsLike {
            Test-AIStereoMediaToolPair -FfmpegPath $limitedFfmpeg -FfprobePath $validFfprobe | Out-Null
        } "*does not provide the required libx264 encoder*"
    }

    Invoke-Test "prefers an explicitly configured pair" {
        $env:AISTEREO_FFMPEG_PATH = $validFfmpeg
        $env:AISTEREO_FFPROBE_PATH = $validFfprobe
        $pair = Resolve-AIStereoMediaTools
        Assert-Equal "environment" $pair.Source
        Assert-Equal (Resolve-Path $validFfmpeg).Path $pair.FfmpegPath
    }

    Invoke-Test "rejects a half-configured environment pair" {
        $env:AISTEREO_FFMPEG_PATH = $validFfmpeg
        Remove-Item Env:AISTEREO_FFPROBE_PATH -ErrorAction SilentlyContinue
        Assert-ThrowsLike { Resolve-AIStereoMediaTools | Out-Null } "*must be set together*"
    }

    Invoke-Test "checks archive SHA-256 without downloading" {
        $archiveFixture = Join-Path $temporaryRoot "archive-fixture.bin"
        Set-Content -LiteralPath $archiveFixture -Value "fixture" -Encoding Ascii
        $fixtureHash = (Get-FileHash -LiteralPath $archiveFixture -Algorithm SHA256).Hash
        Assert-Equal $fixtureHash.ToLowerInvariant() (Test-AIStereoMediaToolsArchive -ArchivePath $archiveFixture -ExpectedSha256 $fixtureHash)
        Assert-ThrowsLike {
            Test-AIStereoMediaToolsArchive -ArchivePath $archiveFixture -ExpectedSha256 ("0" * 64) | Out-Null
        } "*integrity check failed*"
    }

    Invoke-Test "rejects unsafe archive paths before extraction" {
        Add-Type -AssemblyName System.IO.Compression -ErrorAction Stop
        Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction Stop
        $unsafeArchivePath = Join-Path $temporaryRoot "unsafe.zip"
        $unsafeArchive = [IO.Compression.ZipFile]::Open($unsafeArchivePath, [IO.Compression.ZipArchiveMode]::Create)
        try {
            $unsafeArchive.CreateEntry("/outside.exe") | Out-Null
        } finally {
            $unsafeArchive.Dispose()
        }
        Assert-ThrowsLike {
            Assert-AIStereoSafeArchiveEntries `
                -ArchivePath $unsafeArchivePath `
                -ExtractionRoot (Join-Path $temporaryRoot "extraction-target")
        } "*unsafe rooted path*"
    }
} finally {
    if ($null -eq $originalFfmpegPath) {
        Remove-Item Env:AISTEREO_FFMPEG_PATH -ErrorAction SilentlyContinue
    } else {
        $env:AISTEREO_FFMPEG_PATH = $originalFfmpegPath
    }
    if ($null -eq $originalFfprobePath) {
        Remove-Item Env:AISTEREO_FFPROBE_PATH -ErrorAction SilentlyContinue
    } else {
        $env:AISTEREO_FFPROBE_PATH = $originalFfprobePath
    }

    $temporaryPath = [IO.Path]::GetFullPath($temporaryRoot)
    $expectedPrefix = [IO.Path]::GetFullPath($temporaryParent).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if ($temporaryPath.StartsWith($expectedPrefix, [StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $temporaryPath)) {
        Remove-Item -LiteralPath $temporaryPath -Recurse -Force
    }
}

if ($failed -gt 0) {
    throw "$failed media-tool script test(s) failed; $passed passed."
}

Write-Host "Media-tool script tests passed: $passed." -ForegroundColor Green
