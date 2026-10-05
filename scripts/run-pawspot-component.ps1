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
    'Bot' { & uv run --locked --all-packages --all-extras python -m pawspot_bot.main }
    'Frontend' {
        Set-Location -LiteralPath (Join-Path $Root 'frontend')
        & npm.cmd run dev -- --host 127.0.0.1 --port 5173 --strictPort
    }
    'Tunnel' {
        # Cloudflared пишет обычные INFO-сообщения в stderr, это не ошибка запуска.
        $ErrorActionPreference = 'Continue'
        & $Cloudflared tunnel --url http://127.0.0.1:5173 2>&1 | Tee-Object -FilePath $TunnelLog
    }
}
Write-Host "PawSpot - $Component stopped. Close this window."
