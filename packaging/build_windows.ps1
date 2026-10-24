param(
    [string]$ReleaseVersion = "2.1.0-context-20260808"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$DriveRoot = [System.IO.Path]::GetPathRoot($ProjectRoot)
$BuildId = Get-Date -Format "yyyyMMdd-HHmmss"
$BuildRoot = Join-Path $DriveRoot ("JianzhouReleaseBuild\" + $BuildId)
$StageRoot = Join-Path $BuildRoot "source"
$VenvRoot = Join-Path $BuildRoot "venv"
$DistRoot = Join-Path $BuildRoot "dist"
$WorkRoot = Join-Path $BuildRoot "work"
$ReleaseRoot = Join-Path $ProjectRoot ("output\releases\JianzhouWorkbench-" + $ReleaseVersion + "-windows-x64")

New-Item -ItemType Directory -Path $StageRoot,$ReleaseRoot -Force | Out-Null

function Copy-ReleaseTree([string]$RelativeRoot) {
    $SourceRoot = Join-Path $ProjectRoot $RelativeRoot
    Get-ChildItem -LiteralPath $SourceRoot -Recurse -File | Where-Object {
        $_.Extension -notin ".pyc", ".pyo" -and $_.FullName -notmatch "[\\/]__pycache__[\\/]"
    } | ForEach-Object {
        $Relative = $_.FullName.Substring($ProjectRoot.Length).TrimStart("\", "/")
        $Target = Join-Path $StageRoot $Relative
        New-Item -ItemType Directory -Path (Split-Path $Target) -Force | Out-Null
        Copy-Item -LiteralPath $_.FullName -Destination $Target
    }
}

Copy-Item -LiteralPath (Join-Path $ProjectRoot "app.py") -Destination $StageRoot
Copy-Item -LiteralPath (Join-Path $ProjectRoot "requirements.txt") -Destination $StageRoot
Copy-Item -LiteralPath (Join-Path $ProjectRoot "requirements.lock.txt") -Destination $StageRoot
Copy-ReleaseTree "app"
Copy-ReleaseTree "frontend"
Copy-ReleaseTree "packaging"

python -m venv $VenvRoot
$BuildPython = Join-Path $VenvRoot "Scripts\python.exe"
& $BuildPython -m pip install --disable-pip-version-check --index-url https://pypi.org/simple -r (Join-Path $StageRoot "requirements.lock.txt")
$PreviousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = ""
Push-Location $StageRoot
try {
    & $BuildPython -m PyInstaller (Join-Path $StageRoot "packaging\pyinstaller.spec") `
        --noconfirm --clean --distpath $DistRoot --workpath $WorkRoot
} finally {
    Pop-Location
    $env:PYTHONPATH = $PreviousPythonPath
}

$BuiltExe = Get-ChildItem -LiteralPath $DistRoot -Filter "*.exe" -File | Select-Object -First 1 -ExpandProperty FullName
if (-not $BuiltExe -or -not (Test-Path -LiteralPath $BuiltExe)) {
    throw "PyInstaller did not create the expected executable"
}
$ReleaseExe = Join-Path $ReleaseRoot ("JianzhouWorkbench-" + $ReleaseVersion + "-windows-x64.exe")
Copy-Item -LiteralPath $BuiltExe -Destination $ReleaseExe

$SourceFiles = Get-ChildItem -LiteralPath $StageRoot -Recurse -File | Where-Object {
    $_.FullName -notmatch "[\\/]__pycache__[\\/]" -and $_.Extension -notin ".pyc", ".pyo"
}
$SourceManifest = foreach ($File in $SourceFiles) {
    [ordered]@{
        path = $File.FullName.Substring($StageRoot.Length).TrimStart("\", "/").Replace("\", "/")
        sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $File.FullName).Hash.ToLowerInvariant()
        bytes = $File.Length
    }
}
$GitCommit = (git -C $ProjectRoot rev-parse HEAD).Trim()
$Dirty = [bool](git -C $ProjectRoot status --porcelain)
$Manifest = [ordered]@{
    product = "Jianzhou Workbench"
    version = $ReleaseVersion
    platform = "windows-x64"
    built_at = (Get-Date).ToString("o")
    base_commit = $GitCommit
    includes_current_worktree = $Dirty
    build_source_root = $StageRoot
    bundled_runtime_data = $false
    bundled_api_keys = $false
    executable = [ordered]@{
        file = (Split-Path $ReleaseExe -Leaf)
        bytes = (Get-Item -LiteralPath $ReleaseExe).Length
        sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $ReleaseExe).Hash.ToLowerInvariant()
    }
    source_files = $SourceManifest
}
$Manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $ReleaseRoot "release-manifest.json") -Encoding utf8
Get-FileHash -Algorithm SHA256 -LiteralPath $ReleaseExe | ForEach-Object {
    ($_.Hash.ToLowerInvariant() + "  " + (Split-Path $ReleaseExe -Leaf))
} | Set-Content -LiteralPath (Join-Path $ReleaseRoot "SHA256SUMS.txt") -Encoding ascii

Write-Output $ReleaseRoot
