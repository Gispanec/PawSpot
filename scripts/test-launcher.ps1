$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
foreach ($file in @('start-pawspot.ps1', 'run-pawspot-component.ps1', 'backup-pawspot.ps1')) {
    $tokens = $null
    $parseErrors = $null
    $null = [Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot $file), [ref]$tokens, [ref]$parseErrors)
    if ($parseErrors) { throw "PowerShell syntax failed: $file" }
}
. (Join-Path $PSScriptRoot 'start-pawspot.ps1')
function Assert-True([bool]$Condition, [string]$Message) {
    if (!$Condition) { throw $Message }
}
$fixtureDir = Join-Path $Root ('.local\launcher-test-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $fixtureDir -Force
$LocalDir = $fixtureDir
$fixture = Join-Path $fixtureDir '.env'
$heldLog = Join-Path $fixtureDir 'held.log'
$readyFixture = Join-Path $fixtureDir 'ready.json'
$diagnosticsPath = Join-Path $fixtureDir 'readiness.jsonl'
try {
    # Проверяем реальные функции probe/wait до подмены сервисов ниже.
    function Invoke-WebRequest { return [pscustomobject]@{ StatusCode = 200; Content = '{"status":"ready"}' } }
    Assert-True (Test-PawSpotUrl 'http://localhost/ready' -Ready) 'Backend readiness JSON was rejected.'
    Assert-True (!(Test-PawSpotUrl 'http://localhost/health' -Backend)) 'Wrong backend status was accepted.'
    function Invoke-WebRequest { return [pscustomobject]@{ StatusCode = 200; Content = '<title>Other app</title>' } }
    Assert-True (!(Test-PawSpotUrl 'http://localhost')) 'Unrelated HTML was accepted.'
    Assert-True ($script:ProbeDetail -match 'not PawSpot') 'HTML mismatch diagnostic is missing.'
    function Invoke-WebRequest { return [pscustomobject]@{ StatusCode = 503; Content = 'Unavailable' } }
    Assert-True (!(Test-PawSpotUrl 'https://fresh.trycloudflare.com')) 'HTTP 503 was accepted.'
    Assert-True ($script:ProbeDetail -eq 'HTTP 503') 'HTTP diagnostic is missing.'
    function Invoke-WebRequest { throw [Net.WebException]::new('DNS not ready', [Net.WebExceptionStatus]::NameResolutionFailure) }
    Assert-True (!(Test-PawSpotUrl 'https://fresh.trycloudflare.com')) 'DNS failure was accepted.'
    Assert-True ($script:ProbeDetail -match 'NameResolutionFailure.*DNS not ready') 'DNS diagnostic is missing.'
    $rejected = $false
    try { Wait-PawSpotUrl 'https://fresh.trycloudflare.com' -Component Tunnel -TimeoutSeconds 0 6>$null }
    catch { $rejected = $_.Exception.Message -match 'Tunnel.*fresh.trycloudflare.com.*NameResolutionFailure' }
    Assert-True $rejected 'Bounded readiness failure lost component, URL or reason.'
    $script:probeCalls = 0
    function Invoke-WebRequest {
        $script:probeCalls++
        if ($script:probeCalls -le 5) { throw [Net.WebException]::new('DNS not ready', [Net.WebExceptionStatus]::NameResolutionFailure) }
        return [pscustomobject]@{ StatusCode = 200; Content = '<title>PawSpot</title>' }
    }
    $output = @(Wait-PawSpotUrl 'https://fresh.trycloudflare.com' -Component Tunnel -TimeoutSeconds 20 6>&1)
    Assert-True ($script:probeCalls -eq 6) 'Fresh DNS failure did not recover on retry.'
    Assert-True (($output -join "`n") -match '\[OK\] Mini App \(HTTPS\) ready') 'Recovered Mini App success is missing.'
    Assert-True (($output -join "`n") -notmatch 'NameResolutionFailure|WebException|DNS not ready') 'Transient errors leaked into progress output.'
    $progress = @($output | Where-Object { "$_" -match 'waiting for Cloudflare DNS' })
    Assert-True ($progress.Count -eq 2) 'DNS progress must appear immediately and again after 8 seconds, not on every attempt.'
    $entries = @(Get-Content -LiteralPath $diagnosticsPath | ForEach-Object { $_ | ConvertFrom-Json })
    Assert-True ($entries.Count -eq 8) 'Not every failed/successful probe or final timeout was logged.'
    Assert-True ($entries[0].result -eq 'waiting' -and $entries[1].result -eq 'timeout') 'Final timeout result was not logged.'
    Assert-True ($entries[-1].result -eq 'ready' -and $entries[-1].attempt -eq 6) 'Successful probe result was not logged.'
    Assert-True ($entries[2].detail -match 'NameResolutionFailure.*DNS not ready') 'Detailed DNS cause is missing from log.'
    Assert-True ($entries[2].component -eq 'Tunnel' -and $entries[2].url -eq 'https://fresh.trycloudflare.com') 'Diagnostic component/URL is missing.'
    Assert-True ([DateTimeOffset]::Parse($entries[2].timestamp).Offset -eq [TimeSpan]::Zero) 'Diagnostic timestamp must be UTC.'
    # Быстрый запуск не должен показывать DNS-ожидание.
    $output = @(Wait-PawSpotUrl 'https://fresh.trycloudflare.com' -Component Tunnel 6>&1) -join "`n"
    Assert-True ($output -match '\[OK\]' -and $output -notmatch 'waiting for Cloudflare DNS') 'Fast success output is incorrect.'
    # Проверяем предел попыток без реальных задержек и изменений сети.
    $script:sleepCalls = 0
    function Start-Sleep {
        param([int]$Seconds)
        Assert-True ($Seconds -eq 2) 'Readiness retry interval changed.'
        $script:sleepCalls++
    }
    function Invoke-WebRequest { throw [Net.WebException]::new('DNS not ready', [Net.WebExceptionStatus]::NameResolutionFailure) }
    $output = @(& {
        try { Wait-PawSpotUrl 'https://fresh.trycloudflare.com' -Component Tunnel -TimeoutSeconds 180 }
        catch { $script:timeoutMessage = $_.Exception.Message }
    } 6>&1) -join "`n"
    Assert-True ($script:timeoutMessage -match '90 attempts.*NameResolutionFailure.*readiness.jsonl') 'Attempt limit lost final error or diagnostics path.'
    Assert-True ($script:sleepCalls -eq 90) 'Readiness retry count changed.'
    Assert-True ($output -notmatch '\[OK\]|NameResolutionFailure|WebException') 'Timeout appeared successful or flooded the console.'
    Remove-Item Function:Start-Sleep
    foreach ($failure in @('http', 'html')) {
        function Invoke-WebRequest {
            if ($failure -eq 'http') { return [pscustomobject]@{ StatusCode = 503; Content = 'Unavailable' } }
            return [pscustomobject]@{ StatusCode = 200; Content = '<title>Other app</title>' }
        }
        $rejected = $false
        try { Wait-PawSpotUrl 'https://fresh.trycloudflare.com' -Component Tunnel -TimeoutSeconds 0 6>$null }
        catch { $rejected = $_.Exception.Message -match 'HTTP 503|not PawSpot HTML' }
        Assert-True $rejected 'A real HTTP/content failure was hidden or accepted.'
    }
    # Недоступный лог не превращает успешную проверку в ошибку запуска.
    function Add-Content { throw 'Simulated disk failure' }
    function Invoke-WebRequest { return [pscustomobject]@{ StatusCode = 200; Content = '<title>PawSpot</title>' } }
    $output = @(& {
        Wait-PawSpotUrl 'https://fresh.trycloudflare.com' -Component Tunnel
        Wait-PawSpotUrl 'https://fresh.trycloudflare.com' -Component Tunnel
    } 6>&1)
    Assert-True (@($output | Where-Object { "$_" -match '\[WARN\]' }).Count -eq 1) 'Log failure should warn only once.'
    Assert-True (@($output | Where-Object { "$_" -match '\[OK\]' }).Count -eq 2) 'Log failure changed readiness results.'
    Remove-Item Function:Add-Content
    Remove-Item Function:Invoke-WebRequest
    $heldStream = [IO.FileStream]::new($heldLog, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::ReadWrite)
    $heldWriter = [IO.StreamWriter]::new($heldStream)
    try {
        $heldWriter.WriteLine('https://held.trycloudflare.com')
        $heldWriter.Flush()
        $originalFailed = $false
        try { $null = [IO.File]::ReadAllText($heldLog) } catch [IO.IOException] { $originalFailed = $true }
        Assert-True $originalFailed 'Original sharing violation was not reproduced.'
        Assert-True ((Wait-TunnelUrl $heldLog) -eq 'https://held.trycloudflare.com') 'Shared reader failed while writer was open.'
    } finally { $heldWriter.Dispose() }
    $exclusive = [IO.FileStream]::new($heldLog, [IO.FileMode]::Open, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { Assert-True ((Read-SharedLog $heldLog) -eq '') 'Exclusive lock was not retried safely.' }
    finally { $exclusive.Dispose() }
    Assert-True ((Wait-TunnelUrl $heldLog) -eq 'https://held.trycloudflare.com') 'Read did not recover after lock release.'
    @{ pid = $PID; status = 'ready'; updated_at = [DateTimeOffset]::UtcNow.ToString('o') } | ConvertTo-Json | Set-Content $readyFixture
    Wait-BotReady $readyFixture
    @{ status = 'failed'; error = 'TelegramConflictError' } | ConvertTo-Json | Set-Content $readyFixture
    $rejected = $false
    try { Wait-BotReady $readyFixture } catch { $rejected = $_.Exception.Message -match 'TelegramConflictError' }
    Assert-True $rejected 'Bot failure was silently reported as ready.'
    $original = "# local fixture`r`nPAWSPOT_TELEGRAM_BOT_TOKEN=fixture-token`r`nPAWSPOT_MINI_APP_URL=https://old.trycloudflare.com`r`nPAWSPOT_INTERNAL_SERVICE_TOKEN=fixture-service`r`n"
    [IO.File]::WriteAllText($fixture, $original)
    Write-MiniAppUrl $fixture 'https://new.trycloudflare.com'
    $updated = [IO.File]::ReadAllText($fixture)
    Assert-True ($updated -ceq $original.Replace('https://old.trycloudflare.com', 'https://new.trycloudflare.com')) '.env fields other than Mini App URL changed.'
    Assert-True ((Read-MiniAppUrl $fixture) -eq 'https://new.trycloudflare.com') 'Updated URL cannot be read.'
    $rejected = $false
    try { Write-MiniAppUrl $fixture 'https://untrusted.example' } catch { $rejected = $true }
    Assert-True $rejected 'Untrusted URL was accepted.'
    Assert-True ([IO.File]::ReadAllText($fixture) -ceq $updated) 'Rejected URL changed .env.'
    $CheckOnly = $true
    $EnvPath = Join-Path $fixtureDir 'missing.env'
    $rejected = $false
    try { Start-PawSpot } catch { $rejected = $_.Exception.Message -match '\.env is missing' }
    Assert-True $rejected 'Missing .env did not stop launcher.'
    $EnvPath = $fixture
    $output = @(Start-PawSpot 6>&1) -join "`n"
    Assert-True ($output -notmatch 'fixture-token|fixture-service') 'Launcher printed secrets.'
    # Проверяем повторный запуск без изменений настоящих Docker/Git/процессов.
    $hashPath = Join-Path $fixtureDir 'frontend-lock.sha256'
    Set-Content $hashPath (Get-LockHash (Join-Path $Root 'frontend\package-lock.json'))
    $botStatePath = Join-Path $fixtureDir 'bot.json'
    @{ window_pid = 777; url = 'https://new.trycloudflare.com' } | ConvertTo-Json | Set-Content $botStatePath
    $script:commands = [Collections.Generic.List[string]]::new()
    $script:dirty = $true
    function git {
        switch ($args[0]) {
            'rev-parse' { return $Root }
            'remote' { return 'https://github.com/Gispanec/PawSpot.git' }
            'branch' { return 'main' }
            'status' { if ($script:dirty) { return ' M README.md' } }
        }
    }
    function Get-Command {
        param([string]$Name, [string]$ErrorAction)
        if ($Name -in @('uv', 'docker', 'npm.cmd', 'cloudflared')) { return [pscustomobject]@{ Source = 'cloudflared.exe' } }
        return Microsoft.PowerShell.Core\Get-Command -Name $Name
    }
    function Get-CimInstance {
        return @(
            [pscustomobject]@{ Name = 'python.exe'; CommandLine = '"C:\Python\python.exe" -m pawspot_bot.main' },
            [pscustomobject]@{ ProcessId = 777; Name = 'powershell.exe'; CommandLine = "powershell.exe -File $Root\scripts\run-pawspot-component.ps1 -Component Bot" },
            [pscustomobject]@{ Name = 'cloudflared.exe'; CommandLine = 'cloudflared.exe tunnel --url http://127.0.0.1:5173' }
        )
    }
    function Test-PawSpotUrl { return $true }
    function Invoke-WebRequest { return [pscustomobject]@{ StatusCode = 200 } }
    function Start-Component { throw 'Duplicate process would have been started.' }
    function Wait-BotReady { $script:readyChecks += 1 }
    $script:readyChecks = 0
    function Invoke-Checked {
        param($Program, $Arguments, $Label)
        $script:commands.Add("$Program $($Arguments -join ' ')")
    }
    $CheckOnly = $false
    function Test-Path {
        param([string]$LiteralPath, [string]$Path, [string]$PathType)
        $target = if ($LiteralPath) { $LiteralPath } else { $Path }
        if ($target -like '*frontend\node_modules') { return $true }
        if ($PathType) { return Microsoft.PowerShell.Management\Test-Path -LiteralPath $target -PathType $PathType }
        return Microsoft.PowerShell.Management\Test-Path -LiteralPath $target
    }
    Assert-True (Test-Path (Join-Path $Root 'frontend\node_modules')) 'Simulated dependencies not recognized.'
    Assert-True (((Get-Content $hashPath -Raw).Trim()) -eq (Get-LockHash (Join-Path $Root 'frontend\package-lock.json'))) 'Fixture lock hash mismatch.'
    $output = @(& { Start-PawSpot; Start-PawSpot } 6>&1) -join "`n"
    Assert-True ($output -match '\[OK\] Frontend ready; existing process reused' -and $output -match 'existing Cloudflare Tunnel reused') 'Reused components lack clear status.'
    Assert-True ($output -match '\[OK\] Telegram Bot polling ready' -and $output -match '\[OK\] PawSpot ready') 'Successful launch lacks final ready status.'
    Assert-True ($output -notmatch 'fixture-token|fixture-service') 'Repeated launch printed secrets.'
    Assert-True ($script:commands.Count -eq 8) 'Repeated launch did not reuse all processes.'
    Assert-True ($script:readyChecks -eq 2) 'Reused bot readiness was not checked.'
    Assert-True (!(($script:commands -join "`n") -match 'merge|npm')) 'Dirty Git or installed dependencies were changed.'
    Assert-True ($script:commands[1] -eq 'docker compose --env-file .env -f infra/compose.yaml up -d --wait') 'Existing Compose was not reused.'
    Assert-True ($script:commands[3] -match 'alembic -c backend/alembic.ini upgrade head$') 'Migration command is incorrect.'
    # Новый URL должен попасть в .env до запуска бота.
    $script:dirty = $false
    function Get-CimInstance { return @() }
    $script:started = [Collections.Generic.List[string]]::new()
    function Start-Component {
        param($Component, $Log, $Cloudflared)
        $script:started.Add($Component)
        if ($Component -eq 'Tunnel') {
            Set-Content -LiteralPath $Log 'INFO Your quick Tunnel: https://fresh.trycloudflare.com'
            return [pscustomobject]@{ HasExited = $false }
        }
        if ($Component -eq 'Bot') {
            Assert-True ((Read-MiniAppUrl $EnvPath) -eq 'https://fresh.trycloudflare.com') 'Bot started before .env URL was updated.'
            return [pscustomobject]@{ Id = 888 }
        }
    }
    [IO.File]::WriteAllText($fixture, $original)
    Start-PawSpot
    Assert-True ($script:commands[9] -eq 'git merge --ff-only origin/main') 'Clean main was not updated with fast-forward only.'
    Assert-True (($script:started -join ',') -eq 'Tunnel,Bot') 'Tunnel/Bot startup order is wrong.'
    Assert-True ([IO.File]::ReadAllText($fixture) -ceq $original.Replace('https://old.trycloudflare.com', 'https://fresh.trycloudflare.com')) 'Fresh Tunnel changed other .env fields.'
    # При неоднозначном URL нельзя менять .env или запускать бот.
    function Start-Component {
        param($Component, $Log, $Cloudflared)
        Assert-True ($Component -eq 'Tunnel') 'Bot started with ambiguous Tunnel output.'
        Set-Content -LiteralPath $Log 'https://one.trycloudflare.com https://two.trycloudflare.com'
        return [pscustomobject]@{ HasExited = $false }
    }
    [IO.File]::WriteAllText($fixture, $original)
    $rejected = $false
    try { Start-PawSpot } catch { $rejected = $_.Exception.Message -match 'more than one URL' }
    Assert-True $rejected 'Ambiguous Tunnel URL was accepted.'
    Assert-True ([IO.File]::ReadAllText($fixture) -ceq $original) 'Ambiguous Tunnel changed .env.'
    # После сбоя reader Tunnel уже работает, а Bot ещё не был запущен.
    function Get-CimInstance {
        return @([pscustomobject]@{ Name = 'cloudflared.exe'; CommandLine = 'cloudflared.exe tunnel --url http://127.0.0.1:5173' })
    }
    function Test-PawSpotUrl {
        param($Url)
        return $Url -ne 'https://old.trycloudflare.com'
    }
    Set-Content -LiteralPath (Join-Path $fixtureDir 'tunnel.log') 'https://recovered.trycloudflare.com'
    function Start-Component {
        param($Component)
        Assert-True ($Component -eq 'Bot') 'Recovery created a duplicate service or Tunnel.'
        Assert-True ((Read-MiniAppUrl $EnvPath) -eq 'https://recovered.trycloudflare.com') 'Recovered URL was not saved before Bot started.'
        return [pscustomobject]@{ Id = 999 }
    }
    Start-PawSpot
    Assert-True ((Read-MiniAppUrl $EnvPath) -eq 'https://recovered.trycloudflare.com') 'Failed launch could not resume.'
    # Холодный старт: оба локальных сервиса должны стать готовы до Tunnel, Tunnel до Bot.
    function Get-CimInstance { return @() }
    $script:started.Clear()
    $script:localChecks = @{}
    function Test-PawSpotUrl {
        param($Url, [switch]$Backend, [switch]$Ready)
        if ($Url -in @('http://127.0.0.1:8000/health', 'http://127.0.0.1:5173')) {
            if (!$script:localChecks.ContainsKey($Url)) { $script:localChecks[$Url] = 0 }
            $script:localChecks[$Url]++
            return $script:localChecks[$Url] -gt 1
        }
        return $true
    }
    function Assert-FreePort { }
    function Start-Component {
        param($Component, $Log, $Cloudflared)
        $script:started.Add($Component)
        if ($Component -eq 'Tunnel') {
            Assert-True ($script:localChecks['http://127.0.0.1:5173'] -ge 2) 'Tunnel started before Frontend readiness.'
            Set-Content -LiteralPath $Log 'https://cold.trycloudflare.com'
            return [pscustomobject]@{ HasExited = $false }
        }
        if ($Component -eq 'Bot') {
            Assert-True ((Read-MiniAppUrl $EnvPath) -eq 'https://cold.trycloudflare.com') 'Cold Bot loaded stale URL.'
            return [pscustomobject]@{ Id = 1000 }
        }
    }
    Start-PawSpot
    Assert-True (($script:started -join ',') -eq 'Backend,Frontend,Tunnel,Bot') 'Cold startup order is incorrect.'
    # Даже после открытия Tunnel неуспешная HTTPS-проверка блокирует Bot и запись URL.
    $realWait = (Get-Item Function:Wait-PawSpotUrl).ScriptBlock
    function Wait-PawSpotUrl {
        param($Url, [switch]$Backend, [switch]$Ready, $Component = 'Frontend', $TimeoutSeconds = 60)
        & $realWait $Url -Backend:$Backend -Ready:$Ready -Component $Component -TimeoutSeconds 0
    }
    function Test-PawSpotUrl {
        param($Url)
        if ($Url -match '^https://') { $script:ProbeDetail = 'NameResolutionFailure: WebException: DNS not ready'; return $false }
        return $true
    }
    function Start-Component {
        param($Component, $Log, $Cloudflared)
        $script:started.Add($Component)
        Assert-True ($Component -eq 'Tunnel') 'Bot started before mandatory HTTPS readiness.'
        Set-Content -LiteralPath $Log 'https://unavailable.trycloudflare.com'
        return [pscustomobject]@{ HasExited = $false }
    }
    [IO.File]::WriteAllText($fixture, $original)
    $script:started.Clear()
    $output = @(& {
        try { Start-PawSpot }
        catch { $script:launchError = $_.Exception.Message }
    } 6>&1) -join "`n"
    Assert-True ($script:launchError -match 'Tunnel.*NameResolutionFailure.*Bot was not started') 'Full launch lost final readiness failure.'
    Assert-True (($script:started -join ',') -eq 'Tunnel') 'Failed HTTPS readiness started Bot.'
    Assert-True ($output -notmatch '\[OK\] PawSpot ready|Telegram Bot polling ready') 'Failed launch reported final success.'
    Assert-True ([IO.File]::ReadAllText($fixture) -ceq $original) 'Failed HTTPS readiness changed .env.'
    Assert-True ((Get-Content -LiteralPath $diagnosticsPath -Raw) -notmatch 'fixture-token|fixture-service') 'Readiness log contains secrets.'
    Write-Host 'Launcher validation passed: progress, DNS recovery, timeout, diagnostics, real failures, syntax, env privacy, repeated launch and safe Tunnel URL/Bot order.'
} finally {
    # Удаляем только файлы этого теста, без рекурсивного удаления.
    if (Test-Path -LiteralPath $fixture) { Remove-Item -LiteralPath $fixture }
    if ($hashPath -and (Test-Path -LiteralPath $hashPath)) { Remove-Item -LiteralPath $hashPath }
    if ($botStatePath -and (Test-Path -LiteralPath $botStatePath)) { Remove-Item -LiteralPath $botStatePath }
    $tunnelLog = Join-Path $fixtureDir 'tunnel.log'
    if (Test-Path -LiteralPath $tunnelLog) { Remove-Item -LiteralPath $tunnelLog }
    if (Test-Path -LiteralPath $heldLog) { Remove-Item -LiteralPath $heldLog }
    if (Test-Path -LiteralPath $readyFixture) { Remove-Item -LiteralPath $readyFixture }
    if (Test-Path -LiteralPath $diagnosticsPath) { Remove-Item -LiteralPath $diagnosticsPath }
    Remove-Item -LiteralPath $fixtureDir
}
