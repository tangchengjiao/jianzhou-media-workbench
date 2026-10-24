param(
    [Parameter(Mandatory = $true)]
    [string]$ExePath,
    [int]$Port = 8874
)

$ErrorActionPreference = "Stop"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$dataDir = "D:\JianzhouReleaseAcceptance\$stamp"
New-Item -ItemType Directory -Path $dataDir -Force | Out-Null

$env:JIANZHOU_DATA_DIR = $dataDir
$env:JIANZHOU_DEMO = "1"
$env:JIANZHOU_NO_BROWSER = "1"
$env:JIANZHOU_PORT = [string]$Port

$stdout = Join-Path $dataDir "stdout.log"
$stderr = Join-Path $dataDir "stderr.log"
$baseUrl = "http://127.0.0.1:$Port"
$process = Start-Process -FilePath $ExePath `
    -WorkingDirectory (Split-Path -Parent $ExePath) `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -PassThru

try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        Start-Sleep -Milliseconds 500
        if ($process.HasExited) { break }
        try {
            $health = Invoke-RestMethod -Uri "$baseUrl/healthz" -TimeoutSec 2
            $ready = $true
            break
        } catch {
            # The executable is still starting.
        }
    }
    if (-not $ready) {
        throw "Executable did not become ready. Exited=$($process.HasExited) Code=$($process.ExitCode)"
    }

    $root = Invoke-WebRequest -Uri "$baseUrl/" -UseBasicParsing -TimeoutSec 10
    $settingsResponse = Invoke-WebRequest -Uri "$baseUrl/api/settings" -UseBasicParsing -TimeoutSec 10
    $settingsJson = $settingsResponse.Content

    $vueAsset = Invoke-WebRequest -Uri "$baseUrl/assets/js/vue.global.prod.js" -UseBasicParsing -TimeoutSec 10
    $appAsset = Invoke-WebRequest -Uri "$baseUrl/assets/js/app.js" -UseBasicParsing -TimeoutSec 10

    $result = [ordered]@{
        process_id = $process.Id
        data_dir = $dataDir
        health_ok = [bool]$health.ok
        root_status = $root.StatusCode
        settings_status = $settingsResponse.StatusCode
        settings_contains_known_secret_pattern = [bool]($settingsJson -match 'sk-[A-Za-z0-9_-]{12,}|qbEYC|06b3977')
        settings_returns_nonempty_api_key = [bool]($settingsJson -match '"api_key"\s*:\s*"[^"\s]+"')
        app_db_created = Test-Path -LiteralPath (Join-Path $dataDir "app.db")
        secrets_file_created = Test-Path -LiteralPath (Join-Path $dataDir "secrets.json")
        html_has_mount_point = [bool]($root.Content -match 'id="app"')
        vue_asset_status = $vueAsset.StatusCode
        app_asset_status = $appAsset.StatusCode
        server_listener_running = [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    }
    [pscustomobject]$result | Format-List

    if (-not $result.health_ok -or
        $result.root_status -ne 200 -or
        $result.settings_status -ne 200 -or
        $result.settings_contains_known_secret_pattern -or
        $result.settings_returns_nonempty_api_key -or
        -not $result.app_db_created -or
        $result.secrets_file_created -or
        -not $result.html_has_mount_point -or
        $result.vue_asset_status -ne 200 -or
        $result.app_asset_status -ne 200 -or
        -not $result.server_listener_running) {
        throw "Release acceptance failed."
    }
} finally {
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        Stop-Process -Id $listener.OwningProcess
    }
    Write-Output "--- STDERR TAIL ---"
    if (Test-Path -LiteralPath $stderr) { Get-Content -LiteralPath $stderr -Tail 40 }
    Write-Output "--- STDOUT TAIL ---"
    if (Test-Path -LiteralPath $stdout) { Get-Content -LiteralPath $stdout -Tail 40 }
}
