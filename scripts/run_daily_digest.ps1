param(
    [int]$TimeoutSeconds = 900,
    [int]$PollSeconds = 3
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$composeStarted = $false

function Wait-Health {
    param([datetime]$Deadline)
    while ((Get-Date) -lt $Deadline) {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health/ready" -TimeoutSec 5
            if ($health.status -eq "ok" -or $health.status -eq "degraded") {
                return
            }
        } catch {
            Start-Sleep -Seconds $PollSeconds
        }
    }
    throw "Agent did not pass /health/ready before the timeout."
}

function Stop-Compose {
    if ($script:composeStarted) {
        try {
            Push-Location $root
            docker compose down | Out-Host
        } finally {
            Pop-Location
            $script:composeStarted = $false
        }
    }
}

try {
    Push-Location $root
    docker info *> $null
    docker compose up -d
    $script:composeStarted = $true
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    Wait-Health -Deadline $deadline

    # 显式触发当天任务，兼容 DIGEST_TRIGGER=manual；已有完成结果时不会重复推送。
    Invoke-RestMethod `
        -Method Post `
        -Uri "http://127.0.0.1:8000/api/v1/digests/run?force=false" `
        -TimeoutSec $TimeoutSeconds | Out-Null

    $digest = $null
    while ((Get-Date) -lt $deadline) {
        try {
            $digest = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/digests/today" -TimeoutSec 10
            if ($digest.status -in @("completed", "failed")) {
                break
            }
        } catch {
            # Digest may still be queued/running or not written yet.
        }
        Start-Sleep -Seconds $PollSeconds
    }

    if ($null -eq $digest -or $digest.status -notin @("completed", "failed")) {
        throw "Digest did not finish before the timeout."
    }

    Write-Host ("Digest {0}: {1}, items={2}" -f $digest.digest_date, $digest.status, $digest.item_count)
    if ($digest.status -eq "failed") {
        throw ("Digest failed: {0}" -f $digest.error_message)
    }
}
catch {
    Write-Error $_
    try {
        Push-Location $root
        docker compose logs --tail=100 | Out-File -FilePath (Join-Path $root "data\daily-digest-error.log") -Encoding utf8
    } catch {
        Write-Warning "Could not save container logs."
    }
    exit 1
}
finally {
    Pop-Location -ErrorAction SilentlyContinue
    Stop-Compose
}
