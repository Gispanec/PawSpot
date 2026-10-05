param([switch]$CheckOnly, [string]$EnvPath)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$LocalDir = Join-Path $Root '.local\launcher'
if (!$EnvPath) { $EnvPath = Join-Path $Root '.env' }

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments, [string]$Label)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Label failed (exit $LASTEXITCODE)." }
}

function Read-MiniAppUrl {
    param([string]$Path)
    $lines = @(Get-Content -LiteralPath $Path | Where-Object { $_ -match '^\s*PAWSPOT_MINI_APP_URL\s*=' })
    if ($lines.Count -gt 1) { throw 'Duplicate PAWSPOT_MINI_APP_URL in .env. Keep only one entry.' }
    if ($lines.Count -eq 0) { return '' }
    return (($lines[0] -split '=', 2)[1].Trim().Trim('"', "'"))
}

function Write-MiniAppUrl {
    param([string]$Path, [string]$Url)
    if ($Url -notmatch '^https://[a-z0-9-]+\.trycloudflare\.com$') { throw 'Invalid Quick Tunnel URL.' }
    $null = Read-MiniAppUrl $Path
    $content = [IO.File]::ReadAllText($Path)
    if ($content -match '(?m)^\s*PAWSPOT_MINI_APP_URL\s*=') {
        $content = [regex]::Replace($content, '(?m)^[ \t]*PAWSPOT_MINI_APP_URL[ \t]*=[^\r\n]*', "PAWSPOT_MINI_APP_URL=$Url")
    } else {
        $content = $content.TrimEnd("`r", "`n") + "`r`nPAWSPOT_MINI_APP_URL=$Url`r`n"
    }
    [IO.File]::WriteAllText($Path, $content, (New-Object Text.UTF8Encoding $false))
}

function Test-PawSpotUrl {
    param([string]$Url, [switch]$Backend)
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
        if ($Backend) { return ($response.StatusCode -eq 200 -and ($response.Content | ConvertFrom-Json).status -eq 'ok') }
        return ($response.StatusCode -eq 200 -and $response.Content -match '<title>PawSpot</title>')
    } catch { return $false }
}

function Wait-PawSpotUrl {
    param([string]$Url, [switch]$Backend)
    for ($i = 0; $i -lt 30; $i++) {
        if (Test-PawSpotUrl $Url -Backend:$Backend) { return }
        Start-Sleep -Seconds 2
    }
    throw "PawSpot did not become ready at $Url. Check its window."
}

function Start-Component {
    param([string]$Component, [string]$Log = '', [string]$Cloudflared = '')
    $helper = Join-Path $PSScriptRoot 'run-pawspot-component.ps1'
    $arguments = "-NoProfile -NoExit -ExecutionPolicy Bypass -File `"$helper`" -Component $Component -Root `"$Root`""
    if ($Component -eq 'Tunnel') { $arguments += " -TunnelLog `"$Log`" -Cloudflared `"$Cloudflared`"" }
    # Отдельные видимые окна нужны для ежедневного локального запуска.
    $process = Start-Process powershell.exe -ArgumentList $arguments -WorkingDirectory $Root -WindowStyle Normal -PassThru
    Write-Host "Started PawSpot - $Component (window PID $($process.Id))."
    return $process
}

function Assert-FreePort {
    param([int]$Port)
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $Port is occupied by an unrecognized service. Nothing was stopped."
    }
}

function Start-PawSpot {
    Set-Location -LiteralPath $Root
    if (!(Test-Path -LiteralPath $EnvPath -PathType Leaf)) { throw 'Root .env is missing. Copy .env.example and configure your local secrets first.' }
    $repoRoot = & git rev-parse --show-toplevel
    if ($LASTEXITCODE -ne 0 -or [IO.Path]::GetFullPath($repoRoot) -ne [IO.Path]::GetFullPath($Root)) { throw 'Launcher must be in the PawSpot repository root.' }
    $remote = & git remote get-url origin
    if ($LASTEXITCODE -ne 0 -or $remote -notmatch 'github\.com[:/]Gispanec/PawSpot(?:\.git)?$') { throw 'origin is not the expected PawSpot repository.' }
    $branch = & git branch --show-current
    $dirty = @(& git status --porcelain).Count -gt 0
    if ($dirty -or $branch -ne 'main') { Write-Host 'Git: local changes or non-main branch; automatic update skipped. Your files are preserved.' }
    if ($CheckOnly) {
        Write-Host 'Read-only preflight passed. No fetch, .env writes, migrations or processes were started.'
        return
    }
    Invoke-Checked git @('fetch', 'origin', 'main') 'Git fetch'
    if (!$dirty -and $branch -eq 'main') { Invoke-Checked git @('merge', '--ff-only', 'origin/main') 'Git fast-forward' }
    foreach ($name in @('uv', 'docker', 'npm.cmd', 'cloudflared')) {
        if (!(Get-Command $name -ErrorAction SilentlyContinue)) { throw "Required command '$name' is missing from PATH. Install it and open a new terminal." }
    }
    $cloudflaredPath = (Get-Command cloudflared).Source
    # Проверяем командные строки процессов для защиты от второго polling, без вывода.
    try { $processes = @(Get-CimInstance Win32_Process) }
    catch { throw 'Cannot inspect running processes. Run from your normal Windows account to prevent duplicate bot/tunnel processes.' }
    $bots = @($processes | Where-Object { $_.Name -match '^(?:python|pythonw|uv)\.exe$' -and $_.CommandLine -match '(?:^|\s)-m\s+pawspot_bot\.main(?:\s|$)' })
    $tunnels = @($processes | Where-Object { $_.Name -eq 'cloudflared.exe' -and $_.CommandLine -match 'tunnel.*--url[ =]+"?http://(?:127\.0\.0\.1|localhost):5173(?:"|\s|$)' })
    $oldUrl = Read-MiniAppUrl $EnvPath
    $botStatePath = Join-Path $LocalDir 'bot.json'
    $knownBotUrl = ''
    if ($bots.Count -gt 0 -and (Test-Path -LiteralPath $botStatePath)) {
        try {
            $botState = Get-Content -LiteralPath $botStatePath -Raw | ConvertFrom-Json
            $window = $processes | Where-Object { $_.ProcessId -eq $botState.window_pid -and $_.Name -eq 'powershell.exe' }
            if ($window -and $window.CommandLine.Contains($Root) -and $window.CommandLine -match 'run-pawspot-component\.ps1.*-Component Bot') { $knownBotUrl = $botState.url }
        } catch { throw 'Cannot verify launcher bot state. Close Bot window and retry.' }
    }
    $reuseTunnel = $tunnels.Count -gt 0 -and $oldUrl -match '^https://[a-z0-9-]+\.trycloudflare\.com$' -and (Test-PawSpotUrl $oldUrl)
    if ($tunnels.Count -gt 0 -and !$reuseTunnel) { throw 'A cloudflared tunnel is already running, but its .env URL cannot be verified. Close its window and retry; no second tunnel was started.' }
    if ($bots.Count -gt 0 -and !$reuseTunnel) { throw 'A PawSpot bot is already running with an unverified Tunnel URL. Close its window and retry so it can load the new URL.' }
    if ($bots.Count -gt 0 -and $knownBotUrl -ne $oldUrl) { throw 'An existing bot was not started by this launcher with the current URL. Close Bot window and retry; no second polling process was started.' }
    $null = New-Item -ItemType Directory -Path $LocalDir -Force
    Invoke-Checked docker @('compose', '--env-file', '.env', '-f', 'infra/compose.yaml', 'up', '-d', '--wait') 'PostgreSQL/PostGIS'
    Invoke-Checked uv @('sync', '--locked', '--all-packages', '--all-extras') 'Python dependency installation'
    Invoke-Checked uv @('run', '--locked', '--all-packages', '--all-extras', 'alembic', '-c', 'backend/alembic.ini', 'upgrade', 'head') 'Alembic migrations'
    if (Test-PawSpotUrl 'http://127.0.0.1:8000/health' -Backend) { Write-Host 'Backend already running; reused. Restart its window to load code changes.' }
    else { Assert-FreePort 8000; $null = Start-Component Backend }
    # Readiness проверяет настоящее подключение к PostgreSQL/PostGIS.
    for ($i = 0; $i -lt 30; $i++) {
        try {
            $ready = Invoke-WebRequest http://127.0.0.1:8000/ready -UseBasicParsing -TimeoutSec 5
            if ($ready.StatusCode -eq 200) { break }
        } catch { }
        if ($i -eq 29) { throw 'Backend /ready failed. Check Backend window; bot was not started.' }
        Start-Sleep -Seconds 2
    }
    $lockHash = (Get-FileHash (Join-Path $Root 'frontend\package-lock.json') -Algorithm SHA256).Hash
    $hashPath = Join-Path $LocalDir 'frontend-lock.sha256'
    $savedHash = if (Test-Path $hashPath) { (Get-Content $hashPath -Raw).Trim() } else { '' }
    $needsInstall = !(Test-Path (Join-Path $Root 'frontend\node_modules')) -or $savedHash -ne $lockHash
    if (Test-PawSpotUrl 'http://127.0.0.1:5173') {
        if ($needsInstall) { throw 'Frontend is running but its dependency lockfile needs syncing. Close Frontend window and retry.' }
        Write-Host 'Frontend already running; reused.'
    } else {
        Assert-FreePort 5173
        if ($needsInstall) {
            Push-Location (Join-Path $Root 'frontend')
            try { Invoke-Checked npm.cmd @('ci') 'Frontend dependency installation' } finally { Pop-Location }
            Set-Content -LiteralPath $hashPath -Value $lockHash -Encoding ASCII
        }
        $null = Start-Component Frontend
        Wait-PawSpotUrl 'http://127.0.0.1:5173'
    }
    if ($reuseTunnel) { $url = $oldUrl; Write-Host 'Verified existing Quick Tunnel; reused.' }
    else {
        $log = Join-Path $LocalDir 'tunnel.log'
        Set-Content -LiteralPath $log -Value '' -Encoding UTF8
        $tunnelProcess = Start-Component Tunnel -Log $log -Cloudflared $cloudflaredPath
        $url = ''
        for ($i = 0; $i -lt 60; $i++) {
            $matches = @([regex]::Matches([IO.File]::ReadAllText($log), 'https://[a-z0-9-]+\.trycloudflare\.com\b') | ForEach-Object { $_.Value } | Select-Object -Unique)
            if ($matches.Count -gt 1) { throw 'Tunnel reported more than one URL; refusing to start bot.' }
            if ($matches.Count -eq 1) { $url = $matches[0]; break }
            if ($tunnelProcess.HasExited) { throw 'Tunnel process stopped before reporting its URL. Check Tunnel window.' }
            Start-Sleep -Seconds 1
        }
        if (!$url) { throw 'Quick Tunnel did not report an HTTPS URL. Check Tunnel window; bot was not started.' }
        Wait-PawSpotUrl $url
    }
    if ($url -ne $oldUrl) { Write-MiniAppUrl $EnvPath $url; Write-Host 'Updated only PAWSPOT_MINI_APP_URL in local .env.' }
    if ($bots.Count -gt 0) { Write-Host 'PawSpot bot already running; reused. Restart its window to load code changes.' }
    else {
        $botProcess = Start-Component Bot
        @{ window_pid = $botProcess.Id; url = $url } | ConvertTo-Json | Set-Content -LiteralPath $botStatePath -Encoding UTF8
    }
    Write-Host "PawSpot started. Mini App: $url"
    Write-Host 'Stop components with Ctrl+C in their windows, then close windows. Docker data is preserved.'
}

if ($MyInvocation.InvocationName -ne '.') {
    $launchMutex = [Threading.Mutex]::new($false, 'Local\PawSpotLauncher')
    $ownsMutex = $false
    try {
        try { $ownsMutex = $launchMutex.WaitOne(0) }
        catch [Threading.AbandonedMutexException] { $ownsMutex = $true }
        if (!$ownsMutex) { Write-Host 'Another PawSpot launcher is already starting services. Wait for it to finish.' }
        else { Start-PawSpot }
    } catch { Write-Host "PawSpot launch stopped: $($_.Exception.Message)" -ForegroundColor Red; exit 1 }
    finally {
        if ($ownsMutex) { $launchMutex.ReleaseMutex() }
        $launchMutex.Dispose()
    }
}
