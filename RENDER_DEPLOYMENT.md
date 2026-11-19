# Render Free dashboard: prepared, not deployed

The architecture is Windows discovery/report tasks → shared hosted PostgreSQL ←
Render dashboard. Render runs only Gunicorn. It does not run migrations, cron,
workers, discovery, approvals, submissions, retention cleanup or email delivery.
The existing UI and GET filters remain; write controls are hidden and all write
requests are rejected server-side. Login/logout use signed cookies without
database sessions or `last_login` updates. The Render database connection is
read-only by default; use a separate SELECT-only database role as described below.

No database or scheduled task was changed while preparing these files. All
commands below that create/restore/migrate databases, change roles, change local
configuration, disable tasks, push code or deploy require your approval first.
Keep the current local database until the hosted copy has passed verification.

## 1. Choose the free services and prepare credentials

Use **Render Free Web Service** and an external PostgreSQL Free plan, for example
[Neon Free](https://neon.com/docs/introduction/plans). Choose a nearby region for
both. Do not enable a paid plan or paid extras if you require zero cost. Monitor
the provider's usage limits; free-tier availability and limits can change.

[Render Free limits](https://render.com/docs/free): a web service sleeps after
15 idle minutes and may take about a minute to wake up. Its filesystem is
ephemeral; it has no persistent disk, interactive shell or one-off jobs. Render's
Free PostgreSQL expires after 30 days, so it is not the long-term shared database
for this setup. You do not need Render PostgreSQL, a worker or a cron service.

Create the external database only after approval. Save these privately:

* The **direct / unpooled** writer/owner PostgreSQL URL for your Windows PC and
  approved maintenance. Do not use Neon hostnames containing `-pooler` for the PC.
* A separate reader URL for Render, after creating the reader role in step 4.

Both must use SSL: `?sslmode=require` at minimum, or `verify-full` with the
provider's certificate configuration. Preserve `channel_binding=require` if
present. URL-encode special characters in username/password. The application
rejects `sslmode=disable`. Never paste real URLs into Git or chat.

The Windows worker uses PostgreSQL **session-level advisory locks**. Transaction
pooling does not support those locks; use direct connections for this project.
See [Neon pooling limitations](https://neon.com/docs/connect/connection-pooling).

## 2. Back up and rehearse before cutover — approval required

Use PostgreSQL client tools matching the destination's major version and at
least as new as the source. Confirm `pg_dump --version`, `pg_restore --version`,
and the server versions. The commands assume these tools are on PATH.

Open PowerShell in the actual Git root, which contains `manage.py`, `render.yaml`
and `.git`:

```powershell
Set-Location C:\Users\Admin\Downloads\uae_job_agent_starter\uae_job_agent
git rev-parse --show-toplevel
```

After approval, stop the local development server and discovery worker. Disable
the two existing daily tasks, remembering whether each was enabled beforehand:

```powershell
Disable-ScheduledTask -TaskName UAEJobAgent0730Discovery
Disable-ScheduledTask -TaskName UAEJobAgent0800Report
```

Ensure neither task is currently running; wait for any SMTP handoff to finish.
Do not terminate a sending report or blindly retry a `delivery_unknown` report.
Keep all writers stopped until the final dump is verified and cutover finishes.

Choose a private backup folder **outside the repository**. Dumps contain the CV
text, account hashes and application history. Restrict access to your Windows
account and keep an encrypted offline copy. Do not use a public or shared folder.

```powershell
$backupDir = Join-Path $env:LOCALAPPDATA ('UAEJobAgentBackups\' + (Get-Date -Format yyyyMMdd-HHmmss))
New-Item -ItemType Directory -Path $backupDir | Out-Null
.\.venv\Scripts\python.exe manage.py database_inventory > (Join-Path $backupDir 'source-inventory.json')
pg_dump -h localhost -p 5432 -U postgres -W -d uae_job_agent --format=custom --file (Join-Path $backupDir 'source.dump')
if ($LASTEXITCODE -ne 0) { throw 'Backup failed; stop here.' }
pg_restore --list (Join-Path $backupDir 'source.dump') > (Join-Path $backupDir 'dump-contents.txt')
Get-FileHash -Algorithm SHA256 (Join-Path $backupDir 'source.dump')
Copy-Item -LiteralPath .\media -Destination (Join-Path $backupDir 'media') -Recurse
```

Adjust source host, port, role and database to your actual `DB_*` configuration.
`-W` prompts privately for the PostgreSQL password. Do not copy `.env` into Git.
Back up its values privately, along with task XML and the credential service
names in the ignored mailbox/delivery configs. Windows Credential Manager
passwords stay on the PC; they are not included in the PostgreSQL dump.

First test restoring this dump to a **separate empty rehearsal database**. Never
restore over the original database. Use the same steps as the next section and
compare row inventories, migration names, staff login and representative jobs.
Return to the source configuration afterward. If writing resumes between a
rehearsal and final cutover, freeze writers and take a new final dump/inventory.

## 3. Restore the shared database — approval required

The hosted destination must be **empty**, dedicated to this app, and backed up
before any retry. Do not use `--clean`, `DROP DATABASE`, or restore over existing
production data. The new destination's role becomes owner of the restored
objects. Replace only the uppercase placeholders below; the password is prompted:

```powershell
pg_restore --dbname 'host=DIRECT_HOST port=5432 dbname=HOSTED_DB user=HOSTED_OWNER sslmode=require' -W --no-owner --no-acl --single-transaction --exit-on-error (Join-Path $backupDir 'source.dump')
if ($LASTEXITCODE -ne 0) { throw 'Restore failed; do not cut over.' }
```

The custom dump includes auth users, password hashes, profiles, extracted CV
text, jobs, applications, discovery history, report deduplication rows and
sequence values. Actual CV/evidence files stay under local `media`; they are not
uploaded to Render. Database text is sensitive too: keep the repository private,
restrict database access and use HTTPS/staff login for the hosted view.

Verify the destination in this PowerShell process **without editing `.env`**.
If `DATABASE_URL` was already set in this process, record it privately and restore
it afterward instead of removing it. Do not set `DJANGO_SETTINGS_MODULE` to
production on your PC: it must remain unset or `config.settings`.

```powershell
$privateUrl = Read-Host 'Hosted DIRECT writer DATABASE_URL with SSL' -AsSecureString
$urlBuffer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($privateUrl)
try { $env:DATABASE_URL = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($urlBuffer) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($urlBuffer) }
.\.venv\Scripts\python.exe manage.py database_inventory > (Join-Path $backupDir 'hosted-inventory.json')
Compare-Object (Get-Content (Join-Path $backupDir 'source-inventory.json')) (Get-Content (Join-Path $backupDir 'hosted-inventory.json'))
.\.venv\Scripts\python.exe manage.py migrate --check
.\.venv\Scripts\python.exe manage.py migrate --plan
```

Expect **no inventory differences** and no pending migrations when restoring the
same version. All 0001–0004 application migrations are included in the dump.
If migrations are pending, inspect the plan and obtain approval before running:

```powershell
.\.venv\Scripts\python.exe manage.py migrate --noinput
```

Then verify the migration plan, table counts, constraints and sequence values
again. This repository adds no database schema migration for hosting. Do not
create a new profile or re-import jobs to replace a failed restore. The inventory
command checks counts/migration names, not complete content equality: also
inspect representative jobs, application status, references, discovery runs,
and report dates via the local UI while pointed at the destination. Use a
provider backup or a validated dump as the recovery source.

Remove the temporary override when verification ends:

```powershell
Remove-Item Env:DATABASE_URL
```

## 4. Give Render a SELECT-only role — approval required

Run the reviewed SQL template as the hosted owner, not against your local source:

```powershell
psql 'host=DIRECT_HOST port=5432 dbname=HOSTED_DB user=HOSTED_OWNER sslmode=require' -W -f deploy/dashboard_reader.sql
```

It prompts for the owner role name and a new reader password without storing
the password in the SQL file. `dashboard_reader` must be a new role. If it
already exists, inspect its memberships and grants before reusing it. If your
provider restricts SQL role creation, create a non-owner reader through the
provider's console and apply equivalent SELECT grants. Do not put owner
credentials in Render as a workaround.

Verify in a reader `psql` session:

```sql
SHOW ssl;
SHOW default_transaction_read_only;
SELECT has_table_privilege(current_user, 'public.jobs_job', 'SELECT');
SELECT has_table_privilege(current_user, 'public.jobs_job', 'INSERT');
SELECT has_table_privilege(current_user, 'public.applications_application', 'UPDATE');
SELECT has_table_privilege(current_user, 'public.auth_user', 'SELECT');
SELECT has_database_privilege(current_user, current_database(), 'CREATE');
```

Expect `on`, `on`, `true`, `false`, `false`, `true`, `false`. Verify SSL is
**actually in use** with `SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid();`
(expect true); `SHOW ssl` alone only describes server capability. Check DELETE
privileges and role memberships as well. Never test restrictions by changing a
real application record. Render must not have owner/writer-role membership.

## 5. Preserve your Windows automation — approval required for cutover

Only after verification, privately edit the existing local `.env` to add the
**direct writer** `DATABASE_URL` with SSL. Keep `DB_*` as rollback references;
`DATABASE_URL` takes precedence. Leave production-only settings out of `.env` and
out of your Windows user/system environment. Local code defaults to writable
`config.settings`; the hosted flag cannot affect your PC through the database.

Keep the same candidate/staff IDs, project path, virtual environment, `media`
folder, local JSON files and Windows account. Existing task actions therefore
need no changes. The task launcher still loads `.env` from this project.
Existing candidate and file paths from the dump still point to local `media`.

Before enabling the schedule, verify without sending or writing:

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py migrate --check
.\.venv\Scripts\python.exe manage.py run_daily_job_agent --profile-id 1 --staff-id 1 --dry-run
.\.venv\Scripts\python.exe manage.py send_daily_job_report --profile-id 1 --dry-run
Get-ScheduledTask -TaskName 'UAEJobAgent*' | Get-ScheduledTaskInfo
```

Use your existing IDs if they are not 1. Check all task paths still use
`.venv\Scripts\python.exe`, not the system Python 2.7. Dry-run discovery reads
the feeds/mailbox; report dry-run never sends. Do not reset report statuses or
delete report rows: the restored outbox prevents duplicate daily emails.

After approval, re-enable only tasks that were enabled before maintenance:

```powershell
Enable-ScheduledTask -TaskName UAEJobAgent0730Discovery
Enable-ScheduledTask -TaskName UAEJobAgent0800Report
```

The PC must be on, connected to the internet, and running under the same Windows
user for Credential Manager access. Restart the local worker/server after
cutover. Monitor the next 07:30 and 08:00 Dubai task results, the new discovery
run, and the report status. Validate one new inserted job/application ID when
legitimate new data is produced to confirm restored sequences.

Rollback **before new hosted writes**: stop writers, remove the local
`DATABASE_URL` override, restart locally with the preserved `DB_*`, and restore
the prior task-enabled states. Once new writes or emails occur on the hosted
database, do not switch back blindly: take another backup and reconcile jobs,
applications and outbox state first. Never run both databases' writers at once.

## 6. Prepare Git safely — approval required to publish

The Git root is the inner `uae_job_agent` folder. These files do not create a
commit or push anything. Use a private repository. Before committing:

```powershell
git status --short
git ls-files .env '.env.*' media output backups '*.dump' '*.backup' '*.pdf' '*.docx' '*.pem' '*.key'
git diff --check
git diff --cached --stat
```

Only `.env.example` should appear among environment files, with placeholders.
Review the staged diff locally for secrets and personal documents, not only the
filenames. Ignore rules do not remove previously tracked files. If a secret has
ever been committed, remove it from tracking/history as appropriate and rotate
it before publishing; adding `.gitignore` alone does not fix Git history.
CVs, evidence, local config files, backups, `.env`, database/SMTP passwords and
production keys must never be staged. Do not use `git add .` without inspection.

Commit the reviewed source/deployment changes and push only after approval.

## 7. Create the Render Free Web Service — approval required

Follow [Render's Django guide](https://render.com/docs/deploy-django), but use
**this project's commands** (there is deliberately no build-time migration).

1. In Render choose **New → Web Service**, connect the private Git repository,
   and select the deployment branch. Alternatively choose **New → Blueprint**
   for the supplied `render.yaml`; its only resource is a Free Web Service.
2. Name: `uae-job-dashboard` (or another available name). Runtime: **Python 3**.
   Region: near the PostgreSQL region. Instance type: **Free**. Confirm Free
   before saving. No cron/worker/database resource is required.
3. **Root Directory: blank** when the repository root contains `manage.py`.
   If you instead push the parent folder as the repo root, set Root Directory
   to `uae_job_agent` and put the Blueprint at that parent root or use manual
   Web Service creation. Do not set a subdirectory twice.
4. **Build Command:** `bash build.sh`
5. **Start Command:**

   ```text
   gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 1 --threads 2 --timeout 120 --access-logfile - --error-logfile -
   ```

6. **Health Check Path:** `/healthz/`. This is public liveness only: JSON `ok`,
   no database access, personal data, secrets or stack details. It does not
   certify the database connection; verify that separately by staff login.
7. Add the environment values below in Render's environment UI. The Blueprint
   prompts for the values marked `sync: false`. Do not upload a secret `.env`.

| Variable | Value |
| --- | --- |
| `PYTHON_VERSION` | `3.11.18` |
| `DJANGO_SETTINGS_MODULE` | `config.production` |
| `DJANGO_SECRET_KEY` | New random secret, at least 50 characters; different from local |
| `DJANGO_ALLOWED_HOSTS` | Exact assigned hostname, e.g. `uae-job-dashboard.onrender.com` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://` plus that exact hostname |
| `DATABASE_URL` | Reader-role direct hosted PostgreSQL URL with SSL |

Generate a secret locally when ready (do not commit it):

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(64))"
```

Copy it only into Render's secret environment field. Render's Blueprint
`generateValue` produces a 44-character value, so this Blueprint instead asks
for the longer secret required by Django's deployment checks.
The runtime stays in the project's Python 3.11 series, with the current
[3.11.18 security patch](https://blog.python.org/2026/10/python-3152-31410-31318-31216-31118/).
Render supports fully qualified released versions via
[`PYTHON_VERSION`](https://render.com/docs/python-version). The local Windows
interpreter is not replaced by this deployment configuration.

Do not add Gmail/SMTP/IMAP credentials, Windows JSON config files or media files
to Render. `DEBUG=False`, HTTPS redirects, trusted proxy handling, secure
HTTP-only cookies, one-hour sessions, HSTS and frame protection are enforced by
`config.production`. Read-only mode is hard-coded in that settings module.
Only use this module behind Render's trusted HTTPS proxy.

8. After your approval, click **Create Web Service / Deploy**. If Render assigns
   a hostname with a suffix, update both hostname environment fields to the
   actual hostname and redeploy. Never use `*` to fix a host error. Add custom
   HTTPS domains to both lists only after configuring them in Render.
9. Inspect build logs: dependency install, `collectstatic`, and zero-warning
   deployment checks. Startup must show Gunicorn, not `runserver`. There must
   be no `migrate`, `discovery_worker`, job runner or report command in Render's
   build/start configuration. No pre-deploy command is needed.

## 8. Verify the deployed dashboard

* Visit `https://YOUR_HOST/healthz/`: expect `{"status":"ok"}` without login.
* Visit `/`: expect redirect to `/accounts/login/`. Sign in using the existing
  active staff account restored from the source. No public registration exists.
  Use a unique strong password; change it locally with `manage.py changepassword`
  if needed before enabling public access. Render Free has no shell for this.
* Confirm your existing CSS/design, discovered jobs, UAE-first groups, filters,
  eligibility counts, discovery history, review items, application statuses and
  application detail pages. Compare against the Windows UI using the same DB.
* Confirm the hosted banner says read-only and that discovery, report, approval,
  preparation, submission and external-handoff controls are absent. Filter forms
  and sign-out still work. Direct write requests receive 403. Admin and private
  document endpoints receive 404, including for a superuser.
* Confirm login/logout does not update `auth_user.last_login`, create database
  session rows or modify application data. Signed cookies carry auth state, not
  CV or document contents. Logging out clears the browser cookie; a previously
  stolen signed cookie can remain valid until its one-hour expiry. Password
  changes invalidate the user's session authentication hash. Keep HTTPS and
  protect your account and browser. This app does not add a persistent login
  brute-force service; restrict account access and monitor repeated failures.
* Confirm the Render reader cannot write according to step 4. Keep database
  credentials different between the PC (writer) and Render (reader).
* Confirm plain HTTP redirects to HTTPS, cookies are Secure/HttpOnly, and private
  pages use `Cache-Control: no-store`. Never enable public media serving.
* Observe the next successful Windows discovery/report cycle appear on Render
  without redeploying. Hosted eligibility is recalculated from the database's
  stored CV text; approval/file validity is checked locally, while Render shows
  recorded approval statuses without attempting to read private files.

## Validation before deployment

The production tests use isolated SQLite and fake production environment values,
never your local PostgreSQL or a hosted database:

```powershell
.\.venv\Scripts\python.exe manage.py test --settings=config.test_settings --noinput
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.test_settings
```

Once real Render environment values are set, `build.sh` runs `collectstatic` and
`check --deploy --fail-level WARNING`; neither migrates or imports data. The
Gunicorn process runs only on Linux/Render. A passing liveness or deployment
check does not replace the backup/restore and real database login verification.

Production reference: [WhiteNoise Django setup](https://whitenoise.readthedocs.io/en/stable/django.html),
[Render Blueprint reference](https://render.com/docs/blueprint-spec).
