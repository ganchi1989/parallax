# Development-only FFmpeg/FFprobe discovery and provisioning.
#
# The desktop release pipeline has a separate, reviewed media-tool staging
# process. Do not use this cache as a release-packaging input.

$script:AIStereoMediaToolsVersion = "8.1.2"
$script:AIStereoMediaToolsArchiveUrl = "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-8.1.2-essentials_build.zip"
$script:AIStereoMediaToolsArchiveSha256 = "db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec"
$script:AIStereoMediaToolsProjectRoot = Split-Path -Parent $PSScriptRoot

function Get-AIStereoMediaToolsInstallRoot {
    return Join-Path $script:AIStereoMediaToolsProjectRoot ".dev-tools\ffmpeg\$script:AIStereoMediaToolsVersion"
}

function Get-AIStereoMediaToolVersion {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [ValidateSet("ffmpeg", "ffprobe")]
        [string]$ToolName
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$ToolName executable does not exist: $Path"
    }

    $resolvedPath = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path
    try {
        $versionOutput = @(& $resolvedPath -hide_banner -version 2>&1)
        $exitCode = $LASTEXITCODE
    } catch {
        throw "Could not execute $ToolName at '$resolvedPath': $($_.Exception.Message)"
    }

    if ($exitCode -ne 0) {
        throw "$ToolName at '$resolvedPath' returned exit code $exitCode while reporting its version."
    }

    $versionText = $versionOutput -join "`n"
    $versionMatch = [regex]::Match(
        $versionText,
        "(?im)^\s*$([regex]::Escape($ToolName)) version\s+(?:n-)?(?<version>\d+\.\d+(?:\.\d+)?)"
    )
    if (-not $versionMatch.Success) {
        throw "The executable at '$resolvedPath' did not identify itself as $ToolName."
    }

    return [PSCustomObject]@{
        Path = $resolvedPath
        Version = $versionMatch.Groups["version"].Value
    }
}

function Test-AIStereoMediaToolPair {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$FfmpegPath,

        [Parameter(Mandatory = $true)]
        [string]$FfprobePath,

        [string]$Source = "explicit"
    )

    $ffmpeg = Get-AIStereoMediaToolVersion -Path $FfmpegPath -ToolName "ffmpeg"
    $ffprobe = Get-AIStereoMediaToolVersion -Path $FfprobePath -ToolName "ffprobe"

    $ffmpegDirectory = [IO.Path]::GetDirectoryName($ffmpeg.Path)
    $ffprobeDirectory = [IO.Path]::GetDirectoryName($ffprobe.Path)
    if (-not $ffmpegDirectory.Equals($ffprobeDirectory, [StringComparison]::OrdinalIgnoreCase)) {
        throw "FFmpeg and FFprobe must be colocated in the same directory. Found '$ffmpegDirectory' and '$ffprobeDirectory'."
    }
    if ($ffmpeg.Version -ne $ffprobe.Version) {
        throw "FFmpeg and FFprobe must come from the same version. Found FFmpeg $($ffmpeg.Version) and FFprobe $($ffprobe.Version)."
    }
    if ($ffmpeg.Version -ne $script:AIStereoMediaToolsVersion) {
        throw "Unsupported media-tool version $($ffmpeg.Version). Development requires FFmpeg/FFprobe $script:AIStereoMediaToolsVersion. Run '.\scripts\bootstrap.ps1 -ProvisionMediaTools' to install the verified project-local pair."
    }

    try {
        $encoderOutput = @(& $ffmpeg.Path -hide_banner -encoders 2>&1)
        $encoderExitCode = $LASTEXITCODE
    } catch {
        throw "Could not inspect FFmpeg encoders at '$($ffmpeg.Path)': $($_.Exception.Message)"
    }
    if ($encoderExitCode -ne 0) {
        throw "FFmpeg at '$($ffmpeg.Path)' returned exit code $encoderExitCode while listing encoders."
    }
    if (($encoderOutput -join "`n") -notmatch "(?im)\blibx264\b") {
        throw "FFmpeg $script:AIStereoMediaToolsVersion at '$($ffmpeg.Path)' does not provide the required libx264 encoder. Use '.\scripts\bootstrap.ps1 -ProvisionMediaTools'."
    }

    return [PSCustomObject]@{
        FfmpegPath = $ffmpeg.Path
        FfprobePath = $ffprobe.Path
        Version = $ffmpeg.Version
        Source = $Source
    }
}

function Resolve-AIStereoMediaTools {
    [CmdletBinding()]
    param()

    $hasFfmpegEnvironmentPath = -not [string]::IsNullOrWhiteSpace($env:AISTEREO_FFMPEG_PATH)
    $hasFfprobeEnvironmentPath = -not [string]::IsNullOrWhiteSpace($env:AISTEREO_FFPROBE_PATH)
    if ($hasFfmpegEnvironmentPath -xor $hasFfprobeEnvironmentPath) {
        throw "AISTEREO_FFMPEG_PATH and AISTEREO_FFPROBE_PATH must be set together to a verified FFmpeg/FFprobe $script:AIStereoMediaToolsVersion pair."
    }
    if ($hasFfmpegEnvironmentPath) {
        return Test-AIStereoMediaToolPair `
            -FfmpegPath $env:AISTEREO_FFMPEG_PATH `
            -FfprobePath $env:AISTEREO_FFPROBE_PATH `
            -Source "environment"
    }

    $installRoot = Get-AIStereoMediaToolsInstallRoot
    $cachedFfmpeg = Join-Path $installRoot "bin\ffmpeg.exe"
    $cachedFfprobe = Join-Path $installRoot "bin\ffprobe.exe"
    $hasCachedFfmpeg = Test-Path -LiteralPath $cachedFfmpeg -PathType Leaf
    $hasCachedFfprobe = Test-Path -LiteralPath $cachedFfprobe -PathType Leaf
    if ($hasCachedFfmpeg -xor $hasCachedFfprobe) {
        throw "The project-local media-tool cache is incomplete at '$installRoot'. Run '.\scripts\bootstrap.ps1 -ProvisionMediaTools' to repair it."
    }
    if ($hasCachedFfmpeg) {
        return Test-AIStereoMediaToolPair `
            -FfmpegPath $cachedFfmpeg `
            -FfprobePath $cachedFfprobe `
            -Source "project-cache"
    }

    $pathFfmpeg = Get-Command "ffmpeg" -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    $pathFfprobe = Get-Command "ffprobe" -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (($null -ne $pathFfmpeg) -xor ($null -ne $pathFfprobe)) {
        throw "PATH contains only one of FFmpeg or FFprobe. Install a matched $script:AIStereoMediaToolsVersion pair, set both AISTEREO_* paths, or run '.\scripts\bootstrap.ps1 -ProvisionMediaTools'."
    }
    if ($null -ne $pathFfmpeg) {
        return Test-AIStereoMediaToolPair `
            -FfmpegPath $pathFfmpeg.Source `
            -FfprobePath $pathFfprobe.Source `
            -Source "PATH"
    }

    throw "FFmpeg and FFprobe $script:AIStereoMediaToolsVersion are required for desktop media work. Run '.\scripts\bootstrap.ps1 -ProvisionMediaTools' for the verified project-local tools, or set AISTEREO_FFMPEG_PATH and AISTEREO_FFPROBE_PATH to a matched pair."
}

function Test-AIStereoMediaToolsArchive {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$ArchivePath,

        [string]$ExpectedSha256 = $script:AIStereoMediaToolsArchiveSha256
    )

    if (-not (Test-Path -LiteralPath $ArchivePath -PathType Leaf)) {
        throw "Downloaded FFmpeg archive was not created: $ArchivePath"
    }
    $archive = Get-Item -LiteralPath $ArchivePath -ErrorAction Stop
    if ($archive.Length -le 0 -or $archive.Length -gt 160MB) {
        throw "Downloaded FFmpeg archive has an invalid size ($($archive.Length) bytes)."
    }

    $actualSha256 = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
    if ($actualSha256 -ne $ExpectedSha256.ToLowerInvariant()) {
        throw "FFmpeg archive integrity check failed. Expected SHA-256 $ExpectedSha256 but received $actualSha256. The archive was not installed."
    }

    return $actualSha256
}

function Assert-AIStereoSafeArchiveEntries {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$ArchivePath,

        [Parameter(Mandatory = $true)]
        [string]$ExtractionRoot
    )

    Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction Stop
    $rootPath = [IO.Path]::GetFullPath($ExtractionRoot).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    $rootPrefix = $rootPath + [IO.Path]::DirectorySeparatorChar
    $uncompressedBytes = [long]0
    $entryCount = 0
    $archive = [IO.Compression.ZipFile]::OpenRead($ArchivePath)
    try {
        foreach ($entry in $archive.Entries) {
            $entryCount += 1
            if ($entryCount -gt 10000) {
                throw "FFmpeg archive contains too many entries."
            }
            $uncompressedBytes += [long]$entry.Length
            if ($uncompressedBytes -gt 1GB) {
                throw "FFmpeg archive expands beyond the allowed size."
            }

            $normalizedEntryName = $entry.FullName.Replace([IO.Path]::AltDirectorySeparatorChar, [IO.Path]::DirectorySeparatorChar)
            if ([IO.Path]::IsPathRooted($normalizedEntryName)) {
                throw "FFmpeg archive contains an unsafe rooted path: $($entry.FullName)"
            }
            $entryPath = [IO.Path]::GetFullPath((Join-Path $rootPath $entry.FullName))
            if (-not $entryPath.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
                throw "FFmpeg archive contains an unsafe path: $($entry.FullName)"
            }
        }
    } finally {
        $archive.Dispose()
    }
}

function Remove-AIStereoGeneratedDirectory {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$AllowedParent
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    $targetPath = [IO.Path]::GetFullPath($Path).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    $parentPath = [IO.Path]::GetFullPath($AllowedParent).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    $parentPrefix = $parentPath + [IO.Path]::DirectorySeparatorChar
    if ($targetPath -eq $parentPath -or -not $targetPath.StartsWith($parentPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove media-tool path outside the expected generated directory: $targetPath"
    }

    $targetItem = Get-Item -LiteralPath $targetPath -Force -ErrorAction Stop
    if (($targetItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to replace reparse-point media-tool path: $targetPath"
    }
    Remove-Item -LiteralPath $targetPath -Recurse -Force -ErrorAction Stop
}

function Install-AIStereoMediaTools {
    [CmdletBinding()]
    param()

    $installRoot = Get-AIStereoMediaToolsInstallRoot
    $cachedFfmpeg = Join-Path $installRoot "bin\ffmpeg.exe"
    $cachedFfprobe = Join-Path $installRoot "bin\ffprobe.exe"
    if ((Test-Path -LiteralPath $cachedFfmpeg -PathType Leaf) -and (Test-Path -LiteralPath $cachedFfprobe -PathType Leaf)) {
        try {
            return Test-AIStereoMediaToolPair `
                -FfmpegPath $cachedFfmpeg `
                -FfprobePath $cachedFfprobe `
                -Source "project-cache"
        } catch {
            Write-Warning "Replacing an invalid project-local media-tool cache: $($_.Exception.Message)"
        }
    }

    $temporaryParent = [IO.Path]::GetTempPath()
    $temporaryRoot = Join-Path $temporaryParent ("aistereo-ffmpeg-" + [guid]::NewGuid().ToString("N"))
    $archivePath = Join-Path $temporaryRoot "ffmpeg.zip"
    $extractionRoot = Join-Path $temporaryRoot "extracted"
    $cacheParent = Split-Path -Parent $installRoot
    $stagingRoot = Join-Path $cacheParent ("$script:AIStereoMediaToolsVersion.stage-" + [guid]::NewGuid().ToString("N"))

    New-Item -ItemType Directory -Path $temporaryRoot -ErrorAction Stop | Out-Null
    try {
        Write-Host "Downloading verified FFmpeg/FFprobe $script:AIStereoMediaToolsVersion development tools..." -ForegroundColor Cyan
        try {
            Invoke-WebRequest `
                -Uri $script:AIStereoMediaToolsArchiveUrl `
                -OutFile $archivePath `
                -UseBasicParsing `
                -ErrorAction Stop
        } catch {
            throw "Could not download the pinned FFmpeg archive. Check the network connection and retry, or configure a verified local pair through AISTEREO_FFMPEG_PATH and AISTEREO_FFPROBE_PATH. $($_.Exception.Message)"
        }

        Test-AIStereoMediaToolsArchive -ArchivePath $archivePath | Out-Null
        New-Item -ItemType Directory -Path $extractionRoot -ErrorAction Stop | Out-Null
        Assert-AIStereoSafeArchiveEntries -ArchivePath $archivePath -ExtractionRoot $extractionRoot
        Expand-Archive -LiteralPath $archivePath -DestinationPath $extractionRoot -Force -ErrorAction Stop

        $ffmpegCandidates = @(Get-ChildItem -LiteralPath $extractionRoot -Filter "ffmpeg.exe" -File -Recurse -ErrorAction Stop)
        $ffprobeCandidates = @(Get-ChildItem -LiteralPath $extractionRoot -Filter "ffprobe.exe" -File -Recurse -ErrorAction Stop)
        if ($ffmpegCandidates.Count -ne 1 -or $ffprobeCandidates.Count -ne 1) {
            throw "Pinned FFmpeg archive did not contain exactly one FFmpeg/FFprobe pair."
        }
        if ($ffmpegCandidates[0].Directory.FullName -ne $ffprobeCandidates[0].Directory.FullName) {
            throw "Pinned FFmpeg archive did not contain FFmpeg and FFprobe in the same bin directory."
        }

        Test-AIStereoMediaToolPair `
            -FfmpegPath $ffmpegCandidates[0].FullName `
            -FfprobePath $ffprobeCandidates[0].FullName `
            -Source "download" | Out-Null

        $archiveProductRoot = Split-Path -Parent $ffmpegCandidates[0].Directory.FullName
        New-Item -ItemType Directory -Path $cacheParent -Force -ErrorAction Stop | Out-Null
        Copy-Item -LiteralPath $archiveProductRoot -Destination $stagingRoot -Recurse -ErrorAction Stop

        Test-AIStereoMediaToolPair `
            -FfmpegPath (Join-Path $stagingRoot "bin\ffmpeg.exe") `
            -FfprobePath (Join-Path $stagingRoot "bin\ffprobe.exe") `
            -Source "staging" | Out-Null

        if (Test-Path -LiteralPath $installRoot) {
            Remove-AIStereoGeneratedDirectory -Path $installRoot -AllowedParent $cacheParent
        }
        Move-Item -LiteralPath $stagingRoot -Destination $installRoot -ErrorAction Stop

        $result = Test-AIStereoMediaToolPair `
            -FfmpegPath (Join-Path $installRoot "bin\ffmpeg.exe") `
            -FfprobePath (Join-Path $installRoot "bin\ffprobe.exe") `
            -Source "project-cache"
        Write-Host "Media tools ready at '$installRoot'." -ForegroundColor Green
        return $result
    } finally {
        if (Test-Path -LiteralPath $stagingRoot) {
            Remove-AIStereoGeneratedDirectory -Path $stagingRoot -AllowedParent $cacheParent
        }
        if (Test-Path -LiteralPath $temporaryRoot) {
            Remove-AIStereoGeneratedDirectory -Path $temporaryRoot -AllowedParent $temporaryParent
        }
    }
}
