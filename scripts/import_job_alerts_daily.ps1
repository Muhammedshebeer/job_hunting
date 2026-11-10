param([switch]$DryRun)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$importArguments = @('manage.py', 'import_job_alerts', '--config', 'email_alerts.local.json', '--profile-id', '1')
if ($DryRun) { $importArguments += '--dry-run' }
& $pythonPath @importArguments
exit $LASTEXITCODE
