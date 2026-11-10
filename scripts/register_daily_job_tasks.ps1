[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)][ValidateRange(1, 2147483647)][int]$StaffId,
    [ValidateRange(1, 2147483647)][int]$ProfileId = 1,
    [switch]$Replace,
    [switch]$DisableLegacySchedule
)
$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Existing project virtual environment was not found.'
}
$userSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$exportScript = Join-Path $PSScriptRoot 'export_daily_task_xml.py'
& $pythonPath $exportScript --profile-id $ProfileId --staff-id $StaffId --user-sid $userSid
if ($LASTEXITCODE -ne 0) { throw 'Task definition export failed. No tasks were registered.' }
$taskNames = @('UAEJobAgent0730Discovery', 'UAEJobAgent0800Report')
foreach ($taskName in $taskNames) {
    if ($PSCmdlet.ShouldProcess($taskName, 'Register daily task with an explicit Dubai +04:00 start boundary')) {
        $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($existing -and -not $Replace) {
            throw "Task $taskName already exists. Review it, then use -Replace if replacement is intended."
        }
        $xmlPath = Join-Path $projectPath "output\scheduled_tasks\$taskName.xml"
        $taskXml = Get-Content -LiteralPath $xmlPath -Raw
        Register-ScheduledTask -TaskName $taskName -Xml $taskXml -Force:$Replace | Out-Null
        Write-Output "Registered $taskName."
    }
}
if ($DisableLegacySchedule -and $PSCmdlet.ShouldProcess('UAEJobAgentDailyWorkflow', 'Disable the legacy combined task that can send reports before 08:00')) {
    $legacy = Get-ScheduledTask -TaskName 'UAEJobAgentDailyWorkflow' -ErrorAction SilentlyContinue
    if ($legacy) { Disable-ScheduledTask -TaskName 'UAEJobAgentDailyWorkflow' | Out-Null }
}
