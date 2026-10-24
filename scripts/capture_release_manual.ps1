param(
    [Parameter(Mandatory = $true)]
    [string]$ExePath,
    [string]$OutputDir = "D:\JianzhouManualCapture\shots",
    [int]$Port = 8875
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $OutputDir
$dataDir = Join-Path $root "data"
New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

$browserCandidates = @(
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "C:\Program Files\Microsoft\Edge\Application\msedge.exe"
)
$browser = $browserCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $browser) { throw "Chrome or Edge is required for manual screenshots." }

$env:JIANZHOU_DATA_DIR = $dataDir
$env:JIANZHOU_DEMO = "1"
$env:JIANZHOU_NO_BROWSER = "1"
$env:JIANZHOU_PORT = [string]$Port
$baseUrl = "http://127.0.0.1:$Port"

Start-Process -FilePath $ExePath `
    -WorkingDirectory (Split-Path -Parent $ExePath) `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $dataDir "stdout.log") `
    -RedirectStandardError (Join-Path $dataDir "stderr.log") | Out-Null

try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        Start-Sleep -Milliseconds 500
        try {
            $health = Invoke-RestMethod -Uri "$baseUrl/healthz" -TimeoutSec 2
            if ($health.ok) { $ready = $true; break }
        } catch {
            # Continue polling while the packaged application starts.
        }
    }
    if (-not $ready) { throw "Packaged app did not start for screenshot capture." }

    $pages = [ordered]@{
        "01-workbench.png" = "$baseUrl/"
        "02-profile.png" = "$baseUrl/#/profile"
        "03-accounts.png" = "$baseUrl/#/accounts"
        "04-radar.png" = "$baseUrl/#/radar"
        "05-add-benchmark.png" = "$baseUrl/?radar=add#/radar"
        "06-extraction.png" = "$baseUrl/#/extraction"
        "07-creation.png" = "$baseUrl/#/creation"
        "08-settings-data.png" = "$baseUrl/?settings=source#/settings"
        "09-settings-llm.png" = "$baseUrl/?settings=llm#/settings"
        "10-settings-voice.png" = "$baseUrl/?settings=voice#/settings"
        "11-visuals.png" = "$baseUrl/#/visuals"
        "12-planning.png" = "$baseUrl/#/plan"
        "13-retro.png" = "$baseUrl/#/retro"
        "14-settings-diagnostics.png" = "$baseUrl/?settings=diag#/settings"
    }
    $index = 0
    foreach ($entry in $pages.GetEnumerator()) {
        $index++
        $target = Join-Path $OutputDir $entry.Key
        $profile = Join-Path $root ("browser-profile-" + $index)
        $arguments = @(
            "--headless", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
            "--no-default-browser-check", "--user-data-dir=$profile",
            "--window-size=1440,1000", "--virtual-time-budget=10000",
            "--screenshot=$target", $entry.Value
        )
        Start-Process -FilePath $browser -ArgumentList $arguments -WindowStyle Hidden -Wait | Out-Null
        if (-not (Test-Path -LiteralPath $target)) {
            throw "Screenshot was not created: $($entry.Key)"
        }
        $size = (Get-Item -LiteralPath $target).Length
        if ($size -lt 10000) { throw "Screenshot is unexpectedly small: $($entry.Key)" }
        Write-Output "$($entry.Key) $size bytes"
    }
} finally {
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($listener) { Stop-Process -Id $listener.OwningProcess }
}
