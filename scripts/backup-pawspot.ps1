[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Backup', 'Verify', 'Restore')][string]$Operation,
    [string]$Container = 'infra-postgres-1',
    [string]$Database = 'pawspot',
    [string]$User = 'pawspot',
    [string]$Photos = 'media',
    [string]$Destination = 'backups',
    [string]$Snapshot,
    [switch]$WritersStopped
)
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    if ($Operation -eq 'Restore' -and $Database -notmatch '^pawspot_restore_[a-z0-9_]+$') {
        throw 'Restore requires an explicit NEW pawspot_restore_<suffix> database.'
    }
    $arguments = @('run', '--locked', '--all-packages', '--all-extras', 'python',
        '-m', 'pawspot.backup', $Operation.ToLowerInvariant(), '--container', $Container,
        '--database', $Database, '--user', $User, '--photos', $Photos,
        '--destination', $Destination)
    if ($Snapshot) { $arguments += @('--snapshot', $Snapshot) }
    if ($WritersStopped) { $arguments += '--writers-stopped' }
    & uv @arguments
    if ($LASTEXITCODE -ne 0) { throw "Backup/restore failed (exit $LASTEXITCODE)." }
} finally {
    Pop-Location
}
