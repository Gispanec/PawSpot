$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
foreach ($file in @('start-pawspot.ps1', 'run-pawspot-component.ps1')) {
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
try {
    $fixture = Join-Path $fixtureDir '.env'
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
    $LocalDir = $fixtureDir
    $hashPath = Join-Path $fixtureDir 'frontend-lock.sha256'
    Set-Content $hashPath (Get-FileHash (Join-Path $Root 'frontend\package-lock.json') -Algorithm SHA256).Hash
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
    Assert-True (((Get-Content $hashPath -Raw).Trim()) -eq (Get-FileHash (Join-Path $Root 'frontend\package-lock.json') -Algorithm SHA256).Hash) 'Fixture lock hash mismatch.'
    Start-PawSpot
    Start-PawSpot
    Assert-True ($script:commands.Count -eq 8) 'Repeated launch did not reuse all processes.'
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
    Write-Host 'Launcher validation passed: syntax, env privacy, missing env, dirty Git, repeated launch, simulated healthy services and safe Tunnel URL/Bot order.'
} finally {
    # Удаляем только файлы этого теста, без рекурсивного удаления.
    if (Test-Path -LiteralPath $fixture) { Remove-Item -LiteralPath $fixture }
    if ($hashPath -and (Test-Path -LiteralPath $hashPath)) { Remove-Item -LiteralPath $hashPath }
    if ($botStatePath -and (Test-Path -LiteralPath $botStatePath)) { Remove-Item -LiteralPath $botStatePath }
    $tunnelLog = Join-Path $fixtureDir 'tunnel.log'
    if (Test-Path -LiteralPath $tunnelLog) { Remove-Item -LiteralPath $tunnelLog }
    Remove-Item -LiteralPath $fixtureDir
}
