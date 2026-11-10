# Discovery history and read-only rejection audit

Current policy and supervised manual workflow: [PHASE4A.md](PHASE4A.md). Minimum score is 50%; publication window is 10 days, inclusive.

The existing dashboard at `/` keeps its design and adds **Discovery history**.
Sign in with an active staff account, select your existing candidate profile,
and click **Run Discovery**. This POST only inserts a queue request. It does not
contact employer endpoints, access Gmail, or execute discovery in the request.
The separate worker uses the existing `run_plan` pipeline and the trusted
`discovery_sources.phase2c.json` manifest, including its existing Gmail source.
No new dependency, mailbox configuration, or credential copy is required.

## Start locally (Windows PowerShell)

Apply the additive migration before starting the updated dashboard:

```powershell
Set-Location C:\Users\Admin\Downloads\uae_job_agent_starter\uae_job_agent
.\.venv\Scripts\python.exe manage.py migrate --plan
.\.venv\Scripts\python.exe manage.py migrate
```

Migration `jobs.0003_discovery_history` creates only discovery-run and rejected-
vacancy tables and their constraints. It does not rewrite existing jobs,
applications, profiles, credentials, migrations, or uploaded CVs. Development
tests apply it only to isolated SQLite; development has not migrated your live
PostgreSQL database.

In one PowerShell window, under the same Windows user who configured the existing
Windows Credential Manager mailbox secret:

```powershell
Set-Location C:\Users\Admin\Downloads\uae_job_agent_starter\uae_job_agent
.\.venv\Scripts\python.exe manage.py discovery_worker --poll-seconds 3
```

In another window:

```powershell
Set-Location C:\Users\Admin\Downloads\uae_job_agent_starter\uae_job_agent
$env:DJANGO_DEBUG = '1'
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Open http://127.0.0.1:8000/, sign in, and click **Run Discovery**. Open the run
to see cumulative filter counts, source results, and the read-only audit.
For a single worker pass after queuing, use:

```powershell
.\.venv\Scripts\python.exe manage.py discovery_worker --once
```

The button starts a **persisting** discovery run: eligible and incomplete review
jobs use the existing insert-only, deduplicated `Job` ingestion; rejected and
expired vacancies become separate `RejectedVacancy` snapshots. No `Application`
records or submission attempts are created. Audit snapshots have no job or
application relation and no preparation, approval, edit, or submission actions.
Existing jobs are not overwritten or deleted when their postings expire.

## States, counts, and privacy

- **Queued:** waiting for the separate worker. The web server cannot execute it.
- **Running:** counts update after each source finishes; the page polls every
  five seconds. A source still fetching may display pending until it finishes.
- **Completed:** all enabled sources finished without source errors.
- **Failed:** a source failed or the worker stopped. Successful-source results,
  partial counts, and rejected snapshots already recorded are retained. Retry
  through a new request once the failed run is terminal; existing job identities
  prevent duplicate saved vacancies.

Fetched, approved roles, role + UAE, and role + UAE + verified 10-day publication
are cumulative stages before deduplication. Eligible/review counts reflect new
candidate decisions after existing-record deduplication. Adapter/platform groups
are alternative breakdowns, so do not sum them again with individual sources.
Rejected audits are observations per run, not deduplicated application records.
Known older/future postings remain rejected; unknown dates require review when
other known checks pass. Experience, approved roles, UAE locations, verified
publication semantics, and the 50% CV threshold remain unchanged.

Old CLI summaries were never persisted and cannot be reconstructed faithfully.
The first queued run captures real history; the historical 112-job summary is not
inserted as a fabricated run. Standard CLI dry runs remain completely read-only
and create neither history nor audit records:

```powershell
.\.venv\Scripts\python.exe manage.py discover_all_jobs --profile-id 1 --sources discovery_sources.phase2c.json --dry-run --summary
```

The audit stores only sanitized public company/title/location, source, verified
posting date when present, and a fixed rejection code. It does not retain URLs,
email bodies, raw source payloads, CV contents, or credentials. Worker error
messages are static and suppress private exception details. All dashboard,
audit, status, and enqueue routes require staff authentication; queue POSTs
require CSRF protection. Audit/status GETs do not change state.

## Managed Windows worker (optional)

Task Scheduler can supervise the worker at sign-in. Run these commands once as
the Windows user who owns the existing Credential Manager mailbox entry. This
registers a local background process and does not store a mailbox password in
the task. Keep the normal PostgreSQL configuration available to that account.

```powershell
$projectPath = 'C:\Users\Admin\Downloads\uae_job_agent_starter\uae_job_agent'
$workerPython = Join-Path $projectPath '.venv\Scripts\python.exe'
$workerCommand = "& '$workerPython' manage.py discovery_worker --poll-seconds 3; exit `$LASTEXITCODE"
$workerAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -Command "{0}"' -f $workerCommand) -WorkingDirectory $projectPath
$workerAccount = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$workerTrigger = New-ScheduledTaskTrigger -AtLogOn -User $workerAccount
$workerPrincipal = New-ScheduledTaskPrincipal -UserId $workerAccount -LogonType Interactive -RunLevel Limited
$workerSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'UAEJobAgentDiscoveryWorker' -Action $workerAction -Trigger $workerTrigger -Principal $workerPrincipal -Settings $workerSettings -Description 'Processes authorized discovery requests; no application submissions'
Start-ScheduledTask -TaskName 'UAEJobAgentDiscoveryWorker'
```

This interactive-account task runs while that Windows user is signed in. Queued
requests survive worker/web-server restarts in PostgreSQL. The task runs Python
without opening an interactive console. Do not start a second manual worker if
the scheduled worker is already active. Use `Get-ScheduledTaskInfo -TaskName
'UAEJobAgentDiscoveryWorker'` to inspect it, and `Stop-ScheduledTask -TaskName
'UAEJobAgentDiscoveryWorker'` to stop it.

## Concurrency and recovery

A partial database unique constraint allows only one queued/running request
globally, across all profiles and web-server processes. A PostgreSQL **session**
advisory lock serializes worker execution and combined `discover_all_jobs` /
`run_plan` calls. Use a direct or session-pooled PostgreSQL connection, not a
transaction-pooling proxy. Legacy standalone discovery commands are separate
entry points: use the combined command/worker for serialized discovery.

A second worker cannot take over an active worker on an elapsed heartbeat.
Only acquisition of the database lock allows recovery of a leftover running
row. On restart, that abandoned run becomes failed and partial results remain;
queue a new request to retry. Losing the owning database session stops further
source fetching/ingestion; it does not silently continue using a new session.
Queued rows remain queued while an unrelated combined CLI run owns the lock.

## Isolated validation

```powershell
.\.venv\Scripts\python.exe manage.py test --settings=config.test_settings
.\.venv\Scripts\python.exe manage.py check --settings=config.test_settings
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.test_settings
```

Tests use synthetic sources and in-memory SQLite, without `.env`, Gmail,
Credential Manager, uploaded files, or the live PostgreSQL database. PostgreSQL
advisory-lock ownership is additionally tested with mocked sessions; real
multi-process PostgreSQL failover is a local integration check, not claimed as
a live verification. To exercise it locally, queue one request, run the worker,
and attempt a second queue request: both must point to the same active run.
Once it finishes, verify `Application` counts remain unchanged and the history
separates eligible/review jobs from rejected snapshots.
