param(
    [int]$Port = 8741,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$dataRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot 'data'))
$url = "http://127.0.0.1:$Port/"

$env:JIANZHOU_DATA_DIR = $dataRoot
$env:JIANZHOU_PORT = [string]$Port
$env:JIANZHOU_BUILD_PROFILE = 'source'
$env:JIANZHOU_BUILD_ID = 'workspace-schema32-channel-permission-evidence'

function Test-CanonicalRuntime {
    try {
        $health = Invoke-RestMethod -Uri ($url + 'healthz') -TimeoutSec 2
        return ($health.ok -and
                $health.build_profile -eq 'source' -and
                [int]$health.schema_version -ge 32 -and
                $health.capabilities.visual_factory -and
                $health.capabilities.unified_assets -and
                $health.capabilities.content_lifecycle -and
                $health.capabilities.channel_operations -and
                $health.capabilities.wechat_official_drafts)
    } catch {
        return $false
    }
}

$listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
if ($listener) {
    if (-not (Test-CanonicalRuntime)) {
        throw "Port $Port is occupied by a non-canonical Jianzhou runtime. Close it first."
    }
} else {
    $python = (Get-Command python -ErrorAction Stop).Source
    Start-Process -FilePath $python `
        -ArgumentList @((Join-Path $projectRoot 'app.py')) `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden | Out-Null

    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 300
        if (Test-CanonicalRuntime) { $ready = $true; break }
    }
    if (-not $ready) {
        throw 'The canonical source runtime did not start. Check data\logs\app.log.'
    }
}

Write-Host "Jianzhou canonical source runtime is ready: $url" -ForegroundColor Green
Write-Host "Data directory: $dataRoot"
Write-Host 'Capabilities: visual factory / unified assets / lifecycle / advisor / channels / WeChat drafts'
if (-not $NoBrowser) { Start-Process $url }
