param(
    [Parameter(Mandatory = $true)][ValidateSet('Backend', 'Frontend', 'Bot', 'Tunnel')][string]$Component,
    [Parameter(Mandatory = $true)][string]$Root,
    [string]$TunnelLog,
    [string]$Cloudflared
)
$Host.UI.RawUI.WindowTitle = "PawSpot - $Component"
Set-Location -LiteralPath $Root
switch ($Component) {
    'Backend' { & uv run --locked --all-packages --all-extras uvicorn pawspot.main:app --host 127.0.0.1 --port 8000 }
    'Bot' {
        $readyPath = Join-Path $Root '.local\launcher\bot-ready.json'
        if (Test-Path -LiteralPath $readyPath) { Remove-Item -LiteralPath $readyPath }
        & uv run --locked --all-packages --all-extras python -m pawspot_bot.main --ready-file $readyPath
        if ($LASTEXITCODE -ne 0 -and !(Test-Path -LiteralPath $readyPath)) {
            @{ status = 'failed'; error = "BotProcessExit$LASTEXITCODE" } | ConvertTo-Json | Set-Content -LiteralPath $readyPath -Encoding UTF8
        }
    }
    'Frontend' {
        Set-Location -LiteralPath (Join-Path $Root 'frontend')
        & npm.cmd run dev -- --host 127.0.0.1 --port 5173 --strictPort
    }
    'Tunnel' {
        # Cloudflared пишет обычные INFO-сообщения в stderr, это не ошибка запуска.
        $ErrorActionPreference = 'Continue'
        $stream = [IO.FileStream]::new($TunnelLog, [IO.FileMode]::Create, [IO.FileAccess]::Write, ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
        $writer = [IO.StreamWriter]::new($stream, [Text.UTF8Encoding]::new($false))
        $writer.AutoFlush = $true
        try {
            & $Cloudflared tunnel --url http://127.0.0.1:5173 2>&1 | ForEach-Object {
                $line = $_.ToString()
                Write-Host $line
                $writer.WriteLine($line)
            }
        } finally { $writer.Dispose() }
    }
}
Write-Host "PawSpot - $Component stopped. Close this window."
