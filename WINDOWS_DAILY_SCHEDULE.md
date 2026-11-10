# Two daily Windows tasks: 07:30 and 08:00 Asia/Dubai

Use these split commands instead of scheduling the older `run_daily_workflow`,
which combines discovery and immediate email delivery. Your existing apps,
PostgreSQL records, candidate, uploaded CV, approvals, evidence, dashboard styling,
mailbox authorization and sender checks remain intact. No new migration is needed
for this change; apply any pending migrations from earlier upgrades normally.

| Dubai time | Command | Task name |
| --- | --- | --- |
| 07:30 every day | `run_daily_job_agent` | `UAEJobAgent0730Discovery` |
| 08:00 every day | `send_daily_job_report` | `UAEJobAgent0800Report` |

The generated daily calendar triggers contain explicit `+04:00` start boundaries.
They do not depend on the PC's current local timezone or daylight-saving setting.
Microsoft documents this offset behavior in
[ITrigger.StartBoundary](https://learn.microsoft.com/en-us/windows/win32/api/taskschd/nf-taskschd-itrigger-get_startboundary).
Task Scheduler may display the corresponding **PC-local** next-run time; the XML
offset defines the intended Dubai instant. The first run is the next occurrence
of each time after installation, not a replay of earlier missed schedules.

## 1. Verify the existing project and identity

Run PowerShell as the same Windows account that owns the existing Gmail/SMTP
Credential Manager entries:

```powershell
Set-Location C:\Users\Admin\Downloads\uae_job_agent_starter\uae_job_agent
.\.venv\Scripts\python.exe manage.py migrate --plan
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py shell -c "from django.contrib.auth import get_user_model; print(list(get_user_model().objects.filter(is_active=True, is_staff=True).values('id', 'username')))"
```

Use your actual existing staff ID below. Examples use `1`; no account is created.
CandidateProfile ID `1` must be active, have its existing CV upload and extracted
`resume_text`, and retain the verified 60-day/visa/transfer facts. The ordinary
project settings must already connect to PostgreSQL. Do not print `.env` or paste
database credentials into task arguments.

## 2. Configure SMTP securely, then test it

If not already configured:

```powershell
if (-not (Test-Path -LiteralPath .\email_delivery.local.json)) {
    Copy-Item -LiteralPath .\email_delivery.example.json -Destination .\email_delivery.local.json
}
notepad .\email_delivery.local.json
```

Verify the username, from address and fixed recipient
`shabeersh122@gmail.com`. Set `authorized` to `true` only for this mailbox. Gmail
uses `smtp.gmail.com:587` with `use_tls: true` and `use_ssl: false`. No password is
stored in JSON; the existing SMTP backend reads it from Windows Credential Manager.
See [DAILY_AUTOMATION.md](DAILY_AUTOMATION.md) for provider/TLS validation and
Google App Password guidance. Existing IMAP credentials are not copied or changed.

```powershell
.\.venv\Scripts\python.exe manage.py configure_report_email
.\.venv\Scripts\python.exe manage.py send_daily_job_report --profile-id 1 --test-email
```

The setup prompt hides the App Password. `--test-email` sends a **real** TEST
email to the configured recipient but does not consume today's daily report.
Each explicit invocation can send another TEST email. It never submits a job
application. Do not put secrets in command lines, source files or task XML.

## 3. Run read-only checks before scheduling

```powershell
.\.venv\Scripts\python.exe manage.py run_daily_job_agent --profile-id 1 --staff-id 1 --dry-run
.\.venv\Scripts\python.exe manage.py send_daily_job_report --profile-id 1 --dry-run
.\.venv\Scripts\python.exe manage.py send_daily_job_report --profile-id 1 --test-email --dry-run
```

The agent dry run reads configured authorized sources, including read-only Gmail
where enabled. It writes no jobs, drafts, approvals, history or reports, sends no
emails, and invokes no submission integration. The report dry run reads local
records and prints only section counts/snapshot metadata. It does not access SMTP
credentials or send an email. **Dry run takes precedence over test-email.**
Known source failures, invalid IDs, missing configuration, lock contention after
its bound, and report delivery failures produce nonzero command exit codes.

## 4. Preview and install both tasks

This PC currently blocks `.ps1` files under its execution policy. The following
uses `RemoteSigned` for this new PowerShell process only; it does not change the
persistent machine/user policy. Preview first:

```powershell
powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy RemoteSigned -File .\scripts\register_daily_job_tasks.ps1 -StaffId 1 -ProfileId 1 -DisableLegacySchedule -WhatIf
```

Preview writes two reviewable XML files in gitignored
`output\scheduled_tasks\`. It does **not** register, start, replace or disable
tasks. Inspect the boundaries, actions and settings:

```powershell
$taskNames = @('UAEJobAgent0730Discovery', 'UAEJobAgent0800Report')
foreach ($taskName in $taskNames) {
    [xml]$taskDefinition = Get-Content -LiteralPath ".\output\scheduled_tasks\$taskName.xml" -Raw
    $taskDefinition.Task.Triggers.CalendarTrigger.StartBoundary
    $taskDefinition.Task.Actions.Exec.WorkingDirectory
}
```

Install after successful dry-run/SMTP checks:

```powershell
powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy RemoteSigned -File .\scripts\register_daily_job_tasks.ps1 -StaffId 1 -ProfileId 1 -DisableLegacySchedule
if ($LASTEXITCODE -ne 0) { throw 'Task installation did not finish; inspect both task definitions.' }
```

The installer refuses to overwrite existing tasks with these names. To replace
them after reviewing the XML, repeat the command with `-Replace`. The optional
`-DisableLegacySchedule` disables **only** the old `UAEJobAgentDailyWorkflow`
combined task, preventing it from sending an earlier report. No unrelated task
or dashboard worker task is deleted or disabled. If you previously scheduled
other report commands under different names, inspect and disable those yourself
before relying on this timing; one-daily-report deduplication means an earlier
email cannot be resent automatically at 08:00.

## 5. Confirm the schedule and account in Task Scheduler

```powershell
Get-ScheduledTask -TaskName 'UAEJobAgent0730Discovery', 'UAEJobAgent0800Report' |
    Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName 'UAEJobAgent0730Discovery' |
    Select-Object NextRunTime, LastRunTime, LastTaskResult
Get-ScheduledTaskInfo -TaskName 'UAEJobAgent0800Report' |
    Select-Object NextRunTime, LastRunTime, LastTaskResult
```

Open **Task Scheduler → Task Scheduler Library** and inspect both tasks:

1. **General:** your current Windows account, least privilege, run while that
   account is signed in. This is needed for its Credential Manager secrets.
2. **Triggers:** daily, with the generated explicit UTC+04:00 boundary. The
   displayed clock may be converted to PC-local time.
3. **Actions:** hidden PowerShell launching the existing virtualenv Python,
   with the project as working directory. The encoded launcher contains paths
   and validated numeric IDs, not credentials. Python's exit code is propagated;
   launcher failures exit `1`.
4. **Conditions/settings:** network required, wake requested, allow battery use,
   start when available, ignore overlapping instances, restart on failure up to
   three times at five-minute intervals. Discovery has a two-hour execution
   limit; reporting has five minutes per attempt. Machine power policies can
   still prevent waking; keep the PC powered/available and signed in.

No Windows password is stored in these interactive-account tasks. They will not
run as your credential-owning account while you are signed out. To run signed
out, configure a separately reviewed Windows logon/service setup and test access
to that account's credentials; changing the task to SYSTEM will not provide your
personal Credential Manager secrets. No account password should be pasted here.

## What the 07:30 command does

`run_daily_job_agent` reuses the same daily run marker and PostgreSQL session
advisory lock as the previous workflow. It discovers from configured authorized
Greenhouse, Ashby, Gmail and manual adapters, preserves source posting dates and
URLs, deduplicates, saves eligible/reviewable vacancies, revalidates the selected
candidate's approvals and prepares only missing eligible application drafts.
Reviewed answers, approvals and existing evidence are not regenerated by the
background task, including races with interactive preparation.

Qualification stays **at least 50%**, **at most 10 days/240 hours old**, with both
boundaries inclusive, approved UAE developer roles/locations and verified 1–3-year
experience. Email receipt, fetch and update timestamps never substitute for
publication. Missing information and unknown work-authorization questions stay
in review; joining dates and visa answers are never invented.

The command does **not** queue or send a report. It also does **not** submit any
applications: no permitted live submission adapter exists. Draft/manual-opening
approval cannot authorize unattended submission. Eligible vacancies follow the
manual queue or need clarification/approval; no success is simulated. The shared
20-slot UAE-day budget and canonical/employer-ID duplicate checks remain enforced
by supervised opening/confirmation. Local draft preparation does not consume a
submission slot.

## What happens if discovery is still running at 08:00

`send_daily_job_report` tries the shared database lock up to seven times, waiting
30 seconds between attempts: **at most three minutes of lock waiting** by default.
It queues no report while the lock is unavailable. If discovery finishes within
that bound, it prepares the current report. Otherwise it exits nonzero without
queuing/sending; Task Scheduler can retry up to three times at five-minute
intervals. Thus deferral is bounded, not an endless loop, and an excessively long
run can leave that day's report unsent with a failed task result.

CLI bounds may be changed explicitly (`--lock-retries 0..10`,
`--retry-seconds 1..60`); installed defaults use six retries and 30 seconds. These
are retries of **lock acquisition**, never retries of an ambiguous SMTP send.

The report selects and records the latest fully completed discovery snapshot,
or clearly labels a failed/partial snapshot when no completed run exists. Older
or unscheduled snapshots and newer failed attempts are identified explicitly;
partial running counts are not presented as completed results. Vacancy/application
statuses and CV scores are recalculated at report preparation time, rather than
claimed to be a historical snapshot of every job.

## Report categories, evidence and recovery

The email separates confirmed submissions, manual applications with current
approval, items pending approval, clarification/review, authorized-integration
approvals (currently none), and failed submissions. Each vacancy includes public
company/title/location, verified source publication date or Unverified, current
score, source, safe direct URL and status. Confirmation timestamps/references
appear only on genuinely confirmed records, never on opened links or unverified
HTTP results. Private CV text, draft answers, cover letters, credentials and
evidence file contents are omitted.

One daily email per recipient/UAE date is enforced in PostgreSQL. Test emails
have a separate allowance. A configuration/render failure before SMTP can be
retried explicitly after repair:

```powershell
.\.venv\Scripts\python.exe manage.py send_daily_job_report --profile-id 1 --retry-failed
```

An interrupted/uncertain SMTP handoff stays `delivery_unknown`; it is not resent
automatically, even by scheduler retries. Use its Message-ID for provider
reconciliation. SMTP cannot provide exactly-once delivery across every crash;
the existing outbox prioritizes avoiding duplicates. A killed discovery releases
its session lock; a later agent invocation marks the abandoned run failed and
preserves partial data. Same-day restarts reuse the scheduled run rather than
silently repeating discovery. Use the dashboard Run Discovery and its worker for
an additional authorized run after repairing a failed source.

Safe command output contains IDs, aggregate counts and fixed error messages.
The dashboard/database retain run/report states and audit timestamps. Task
Scheduler `LastTaskResult = 0` means successful command completion, not proof that
an employer accepted an application or an email arrived in the inbox.

## Manual operation, stopping and isolated tests

The following persist real results and the second may send today's real email:

```powershell
.\.venv\Scripts\python.exe manage.py run_daily_job_agent --profile-id 1 --staff-id 1
.\.venv\Scripts\python.exe manage.py send_daily_job_report --profile-id 1
```

To pause these two schedules without deleting data/tasks:

```powershell
Disable-ScheduledTask -TaskName 'UAEJobAgent0730Discovery'
Disable-ScheduledTask -TaskName 'UAEJobAgent0800Report'
```

To verify code without real emails/applications or existing data:

```powershell
.\.venv\Scripts\python.exe manage.py test applications.tests.test_split_schedule --settings=config.test_settings
.\.venv\Scripts\python.exe manage.py test --settings=config.test_settings
.\.venv\Scripts\python.exe manage.py check --settings=config.test_settings
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.test_settings
```

Tests use synthetic sources, temporary CVs, in-memory SQLite and an in-memory
mail backend. Task XML and PowerShell previews can be validated without
registering any task. Live SMTP delivery and cross-process PostgreSQL scheduling
remain local setup checks. No task has been registered/enabled or real email
sent during implementation; the installer preview only generated task XML.
