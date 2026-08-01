[CmdletBinding()]
param(
    [switch]$SkipRust,
    [switch]$SkipPython,
    [switch]$SkipFrontend
)

$workspacePath = Split-Path -Parent $PSScriptRoot
Set-Location $workspacePath
$failures = [System.Collections.Generic.List[string]]::new()

function Invoke-Check([string]$Label, [scriptblock]$Action) {
    Write-Host "`n[$Label]" -ForegroundColor Cyan
    & $Action
    if ($LASTEXITCODE -ne 0) {
        $failures.Add($Label)
    }
}

Write-Host "`n[scripts:media-tools]" -ForegroundColor Cyan
try {
    & (Join-Path $workspacePath "tests\scripts\media-tools.tests.ps1")
} catch {
    $failures.Add("scripts:media-tools")
    Write-Host $_.Exception.Message -ForegroundColor Red
}

if (-not $SkipFrontend) {
    Invoke-Check "frontend:check" { npm.cmd run check }
    Invoke-Check "frontend:test" { npm.cmd run test }
    Invoke-Check "frontend:build" { npm.cmd run build }
}

if (-not $SkipPython) {
    $pythonPath = if (Test-Path ".venv\Scripts\python.exe") {
        ".venv\Scripts\python.exe"
    } else {
        "python"
    }
    $pythonSourcePath = Join-Path $workspacePath "python"
    $env:PYTHONPATH = if ($env:PYTHONPATH) {
        "$pythonSourcePath;$env:PYTHONPATH"
    } else {
        $pythonSourcePath
    }
    Invoke-Check "python:ruff" { & $pythonPath -m ruff check python tests/python }
    Invoke-Check "python:mypy" { & $pythonPath -m mypy python/aistereo }
    Invoke-Check "python:pytest" { & $pythonPath -m pytest tests/python }
}

if (-not $SkipRust) {
    $cargoCommand = Get-Command cargo -ErrorAction SilentlyContinue
    if (-not $cargoCommand) {
        $cargoCandidate = Join-Path ([Environment]::GetFolderPath("UserProfile")) ".cargo\bin\cargo.exe"
        if (Test-Path $cargoCandidate) {
            $env:PATH = "$(Split-Path $cargoCandidate);$env:PATH"
            $cargoCommand = Get-Command cargo -ErrorAction SilentlyContinue
        }
    }
    if ($cargoCommand) {
        Push-Location "src-tauri"
        try {
            Invoke-Check "rust:fmt" { cargo fmt --all -- --check }
            Invoke-Check "rust:check" { cargo check --all-targets --locked }
            Invoke-Check "rust:clippy" { cargo clippy --all-targets --locked -- -D warnings }
            Invoke-Check "rust:test" { cargo test --all-targets --locked }
        } finally {
            Pop-Location
        }
    } else {
        Write-Warning "Rust checks skipped: cargo was not found."
    }
}

if ($failures.Count -gt 0) {
    throw "Checks failed: $($failures -join ', ')"
}

Write-Host "`nAll available checks passed." -ForegroundColor Green
