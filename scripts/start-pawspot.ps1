param([switch]$CheckOnly, [string]$EnvPath)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$LocalDir = Join-Path $Root '.local\launcher'
if (!$EnvPath) { $EnvPath = Join-Path $Root '.env' }
$script:ProbeLogWarningShown = $false

function Write-ProbeDiagnostic {
    param([string]$Component, [string]$Url, [int]$Attempt, [string]$Result, [string]$Detail)
    try {
        $null = New-Item -ItemType Directory -Path $LocalDir -Force
        # Только диагностика probe: без .env, заголовков запросов и содержимого ответов.
        $entry = [ordered]@{
            timestamp = [DateTimeOffset]::UtcNow.ToString('o')
            component = $Component; url = $Url; attempt = $Attempt
            result = $Result; detail = $Detail
        } | ConvertTo-Json -Compress
        Add-Content -LiteralPath (Join-Path $LocalDir 'readiness.jsonl') -Value $entry -Encoding UTF8
    } catch {
        # Сбой диагностического лога не меняет результат проверки готовности.
        if (!$script:ProbeLogWarningShown) {
            Write-Host '[WARN] Cannot write readiness diagnostics. Check .local/launcher/ permissions and disk space.'
            $script:ProbeLogWarningShown = $true
        }
    }
}

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments, [string]$Label)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Label failed (exit $LASTEXITCODE)." }
}

function Get-LockHash {
    param([string]$Path)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($Path)
    try { return ([BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace('-', '') }
    finally { $stream.Dispose(); $algorithm.Dispose() }
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
    param([string]$Url, [switch]$Backend, [switch]$Ready)
    $script:ProbeDetail = ''
    # Quick Tunnel получает свежий DNS: не держим адрес в кэше .NET две минуты.
    [Net.ServicePointManager]::DnsRefreshTimeout = 5000
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
        $script:ProbeDetail = "HTTP $($response.StatusCode)"
        if ($response.StatusCode -ne 200) { return $false }
        if ($Backend -or $Ready) {
            $expected = if ($Ready) { 'ready' } else { 'ok' }
            if (($response.Content | ConvertFrom-Json).status -eq $expected) { return $true }
            $script:ProbeDetail += "; unexpected JSON status (expected $expected)"
        } elseif ($response.Content -match '<title>PawSpot</title>') { return $true }
        else { $script:ProbeDetail += '; response is not PawSpot HTML' }
    } catch {
        $script:ProbeDetail = "$($_.Exception.GetType().Name): $($_.Exception.Message)"
        if ($_.Exception -is [Net.WebException]) { $script:ProbeDetail = "$($_.Exception.Status): $script:ProbeDetail" }
        if ($_.Exception.Response) { $script:ProbeDetail = "HTTP $([int]$_.Exception.Response.StatusCode): $script:ProbeDetail" }
    }
    return $false
}

function Read-SharedLog {
    param([string]$Path)
    for ($attempt = 0; $attempt -lt 3; $attempt++) {
        $stream = $null
        $reader = $null
        try {
            $stream = [IO.FileStream]::new($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
            $reader = [IO.StreamReader]::new($stream)
            return $reader.ReadToEnd()
        } catch [IO.FileNotFoundException] { return '' }
        catch [IO.IOException] {
            if (($_.Exception.HResult -band 65535) -notin @(32, 33)) { throw "Cannot read $([IO.Path]::GetFileName($Path)). Check permissions and disk availability." }
            # Кратковременную блокировку повторяем, остальные ошибки не скрываем.
            Start-Sleep -Milliseconds 100
        } finally {
            if ($reader) { $reader.Dispose() }
            elseif ($stream) { $stream.Dispose() }
        }
    }
    return ''
}

function Wait-TunnelUrl {
    param([string]$Log, $Process)
    Write-Host '[WAIT] Cloudflare Tunnel: waiting for its public URL...'
    $progressTimer = [Diagnostics.Stopwatch]::StartNew()
    $nextProgress = 8
    for ($i = 0; $i -lt 60; $i++) {
        $urls = @([regex]::Matches((Read-SharedLog $Log), 'https://[a-z0-9-]+\.trycloudflare\.com\b') | ForEach-Object { $_.Value } | Select-Object -Unique)
        if ($urls.Count -gt 1) { throw 'Tunnel reported more than one URL; refusing to start bot.' }
        if ($urls.Count -eq 1) { Write-Host '[OK] Cloudflare Tunnel URL received.'; return $urls[0] }
        if ($Process -and $Process.HasExited) { throw 'Tunnel stopped before reporting its URL. Check Tunnel window.' }
        if ($progressTimer.Elapsed.TotalSeconds -ge $nextProgress) {
            Write-Host "[WAIT] Cloudflare Tunnel: waiting for public URL ($([int]$progressTimer.Elapsed.TotalSeconds)s elapsed)."
            $nextProgress = $progressTimer.Elapsed.TotalSeconds + 8
        }
        Start-Sleep -Seconds 1
    }
    throw 'Tunnel URL unavailable after 60 retries (missing output or locked log). Check Tunnel window; bot was not started.'
}

function Wait-BotReady {
    param([string]$Path)
    Write-Host '[WAIT] Telegram Bot: checking polling readiness...'
    $progressTimer = [Diagnostics.Stopwatch]::StartNew()
    $nextProgress = 8
    for ($i = 0; $i -lt 60; $i++) {
        if (Test-Path -LiteralPath $Path) {
            $state = Read-SharedLog $Path
            if ($state) {
                $state = $state | ConvertFrom-Json
                if ($state.status -eq 'failed') { throw "Bot startup/polling failed ($($state.error)). Check Bot window." }
                if ($state.status -eq 'stopped') { throw 'Bot stopped before readiness was confirmed. Check Bot window.' }
                if ($state.status -eq 'ready' -and (Get-Process -Id $state.pid -ErrorAction SilentlyContinue) -and (([DateTimeOffset]::UtcNow - [DateTimeOffset]::Parse($state.updated_at)).TotalSeconds -lt 60)) { return }
            }
        }
        if ($progressTimer.Elapsed.TotalSeconds -ge $nextProgress) {
            Write-Host "[WAIT] Telegram Bot: waiting for successful getUpdates ($([int]$progressTimer.Elapsed.TotalSeconds)s elapsed)."
            $nextProgress = $progressTimer.Elapsed.TotalSeconds + 8
        }
        Start-Sleep -Seconds 1
    }
    throw 'Bot has not completed a successful Telegram getUpdates request. Check Bot window (network, token, webhook or another polling instance).'
}

function Wait-PawSpotUrl {
    param([string]$Url, [switch]$Backend, [switch]$Ready, [string]$Component = 'Frontend', [int]$TimeoutSeconds = 60)
    $stage = if ($Component -eq 'Tunnel') { 'Mini App (HTTPS)' } else { $Component }
    Write-Host "[WAIT] ${stage}: checking availability..."
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $attempt = 0
    $nextProgress = 0
    do {
        $attempt++
        if (Test-PawSpotUrl $Url -Backend:$Backend -Ready:$Ready) {
            Write-ProbeDiagnostic $Component $Url $attempt 'ready' $script:ProbeDetail
            Write-Host "[OK] $stage ready."
            return
        }
        Write-ProbeDiagnostic $Component $Url $attempt 'waiting' $script:ProbeDetail
        if ($timer.Elapsed.TotalSeconds -ge $nextProgress) {
            $reason = if ($Component -eq 'Tunnel' -and $script:ProbeDetail -match '^NameResolutionFailure:') { 'waiting for Cloudflare DNS' } else { 'waiting for readiness' }
            Write-Host "[WAIT] ${stage}: $reason; check $attempt/90 ($([int]$timer.Elapsed.TotalSeconds)/${TimeoutSeconds}s elapsed)."
            $nextProgress = $timer.Elapsed.TotalSeconds + 8
        }
        if ($timer.Elapsed.TotalSeconds -ge $TimeoutSeconds) { break }
        Start-Sleep -Seconds 2
    } while ($attempt -lt 90)
    Write-ProbeDiagnostic $Component $Url $attempt 'timeout' $script:ProbeDetail
    $reason = if ($script:ProbeDetail -match '^NameResolutionFailure:') { 'DNS name could not be resolved. ' } else { '' }
    throw "$Component not ready at $Url after $attempt attempts ($([int]$timer.Elapsed.TotalSeconds)s): $reason$script:ProbeDetail. Bot was not started; inspect the $Component window. Diagnostics: $(Join-Path $LocalDir 'readiness.jsonl')"
}

function Start-Component {
    param([string]$Component, [string]$Log = '', [string]$Cloudflared = '')
    $helper = Join-Path $PSScriptRoot 'run-pawspot-component.ps1'
    $arguments = "-NoProfile -NoExit -ExecutionPolicy Bypass -File `"$helper`" -Component $Component -Root `"$Root`""
    if ($Component -eq 'Tunnel') { $arguments += " -TunnelLog `"$Log`" -Cloudflared `"$Cloudflared`"" }
    # Отдельные видимые окна нужны для ежедневного локального запуска.
    $process = Start-Process powershell.exe -ArgumentList $arguments -WorkingDirectory $Root -WindowStyle Normal -PassThru
    Write-Host "[WAIT] $Component window opened (PID $($process.Id)); checking readiness next."
    return $process
}

function Assert-FreePort {
    param([int]$Port)
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $Port is occupied by an unrecognized service. Nothing was stopped."
    }
}

function Start-PawSpot {
    Write-Host '========================================'
    Write-Host '          PawSpot Launcher'
    Write-Host '========================================'
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
    Write-Host '[WAIT] Checking Git updates and launcher prerequisites...'
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
    $log = Join-Path $LocalDir 'tunnel.log'
    $reuseTunnel = $tunnels.Count -gt 0 -and $oldUrl -match '^https://[a-z0-9-]+\.trycloudflare\.com$' -and (Test-PawSpotUrl $oldUrl)
    $recoveredUrl = ''
    if ($tunnels.Count -eq 1 -and !$reuseTunnel -and (Test-Path -LiteralPath $log)) {
        $recoveredUrl = Wait-TunnelUrl $log
        $reuseTunnel = Test-PawSpotUrl $recoveredUrl
    }
    if ($tunnels.Count -gt 0 -and !$reuseTunnel) { throw 'A cloudflared tunnel is already running, but its .env URL cannot be verified. Close its window and retry; no second tunnel was started.' }
    if ($bots.Count -gt 0 -and !$reuseTunnel) { throw 'A PawSpot bot is already running with an unverified Tunnel URL. Close its window and retry so it can load the new URL.' }
    if ($bots.Count -gt 0 -and $knownBotUrl -ne $oldUrl) { throw 'An existing bot was not started by this launcher with the current URL. Close Bot window and retry; no second polling process was started.' }
    $null = New-Item -ItemType Directory -Path $LocalDir -Force
    Write-Host '[WAIT] Preparing PostgreSQL/PostGIS, Python dependencies and migrations...'
    Invoke-Checked docker @('compose', '--env-file', '.env', '-f', 'infra/compose.yaml', 'up', '-d', '--wait') 'PostgreSQL/PostGIS'
    Invoke-Checked uv @('sync', '--locked', '--all-packages', '--all-extras') 'Python dependency installation'
    Invoke-Checked uv @('run', '--locked', '--all-packages', '--all-extras', 'alembic', '-c', 'backend/alembic.ini', 'upgrade', 'head') 'Alembic migrations'
    if (Test-PawSpotUrl 'http://127.0.0.1:8000/health' -Backend) { Write-Host '[OK] Backend already running; reused. Restart its window to load code changes.' }
    else { Assert-FreePort 8000; $null = Start-Component Backend }
    # Readiness проверяет настоящее подключение к PostgreSQL/PostGIS.
    Wait-PawSpotUrl 'http://127.0.0.1:8000/ready' -Ready -Component Backend
    $lockHash = Get-LockHash (Join-Path $Root 'frontend\package-lock.json')
    $hashPath = Join-Path $LocalDir 'frontend-lock.sha256'
    $savedHash = if (Test-Path $hashPath) { (Get-Content $hashPath -Raw).Trim() } else { '' }
    $needsInstall = !(Test-Path (Join-Path $Root 'frontend\node_modules')) -or $savedHash -ne $lockHash
    if (Test-PawSpotUrl 'http://127.0.0.1:5173') {
        if ($needsInstall) { throw 'Frontend is running but its dependency lockfile needs syncing. Close Frontend window and retry.' }
        Write-Host '[OK] Frontend ready; existing process reused.'
    } else {
        Assert-FreePort 5173
        if ($needsInstall) {
            Write-Host '[WAIT] Installing Frontend dependencies...'
            Push-Location (Join-Path $Root 'frontend')
            try { Invoke-Checked npm.cmd @('ci') 'Frontend dependency installation' } finally { Pop-Location }
            Set-Content -LiteralPath $hashPath -Value $lockHash -Encoding ASCII
        }
        $null = Start-Component Frontend
        Wait-PawSpotUrl 'http://127.0.0.1:5173'
    }
    if ($reuseTunnel) { $url = if ($recoveredUrl) { $recoveredUrl } else { $oldUrl }; Write-Host '[OK] Mini App (HTTPS) ready; existing Cloudflare Tunnel reused.' }
    else {
        Set-Content -LiteralPath $log -Value '' -Encoding UTF8
        $tunnelProcess = Start-Component Tunnel -Log $log -Cloudflared $cloudflaredPath
        $url = Wait-TunnelUrl $log $tunnelProcess
        Wait-PawSpotUrl $url -Component Tunnel -TimeoutSeconds 180
    }
    if ($url -ne $oldUrl) { Write-MiniAppUrl $EnvPath $url; Write-Host 'Updated only PAWSPOT_MINI_APP_URL in local .env.' }
    if ($bots.Count -gt 0) { Write-Host '[WAIT] Existing Telegram Bot found; checking polling. Restart its window to load code changes.' }
    else {
        $readyPath = Join-Path $LocalDir 'bot-ready.json'
        if (Test-Path -LiteralPath $readyPath) { Remove-Item -LiteralPath $readyPath }
        $botProcess = Start-Component Bot
        @{ window_pid = $botProcess.Id; url = $url } | ConvertTo-Json | Set-Content -LiteralPath $botStatePath -Encoding UTF8
    }
    Wait-BotReady (Join-Path $LocalDir 'bot-ready.json')
    Write-Host '[OK] Telegram Bot polling ready.'
    Write-Host '========================================'
    Write-Host "[OK] PawSpot ready. Mini App: $url"
    Write-Host '========================================'
    Write-Host 'Stop components with Ctrl+C in their windows, then close windows. Docker data is preserved.'
}

if ($MyInvocation.InvocationName -ne '.') {
    $launchMutex = [Threading.Mutex]::new($false, 'Local\PawSpotLauncher')
    $ownsMutex = $false
    try {
        try { $ownsMutex = $launchMutex.WaitOne(0) }
        catch [Threading.AbandonedMutexException] { $ownsMutex = $true }
        if (!$ownsMutex) { Write-Host '[WAIT] Another PawSpot launcher is already starting services. Wait for it to finish.' }
        else { Start-PawSpot }
    } catch { Write-Host "[ERROR] PawSpot launch stopped: $($_.Exception.Message)" -ForegroundColor Red; exit 1 }
    finally {
        if ($ownsMutex) { $launchMutex.ReleaseMutex() }
        $launchMutex.Dispose()
    }
}
