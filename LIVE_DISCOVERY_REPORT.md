# Live discovery test — 9 November 2026

**Historical snapshot:** counts and decisions below used the earlier 75% / seven-day policy. They are not current-policy results. Phase 4A now uses 50% / 10 days; rerun discovery for current counts. See [PHASE4A.md](PHASE4A.md).

Executed against existing active CandidateProfile **1** with:

```powershell
.\.venv\Scripts\python.exe manage.py discover_jobs --sources discovery_sources.local.json --profile-id 1 --dry-run
```

Exit code: **0**. Mode: **dry run, no writes**. No application records were
created, no submissions occurred, and no CV text or credentials were logged.
The normal Django settings resolved the existing local database configuration;
the `.env` file was not printed or inspected directly.

## Verified endpoints and counts

Each endpoint returned HTTP **200**, valid JSON, and a jobs list during direct
verification before it was enabled in `discovery_sources.local.json`.

| Employer | Verified public endpoint | Fetched | Last 7 days | UAE location, all ages | Recent + UAE | UAE target roles scoring >=75, all ages |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Ruya AI | https://boards-api.greenhouse.io/v1/boards/ruyaai/jobs?content=true | 8 | 0 | 4 | 0 | 3 |
| Sphere IT Consultants DWC LLC | https://boards-api.greenhouse.io/v1/boards/sphereit/jobs?content=true | 14 | 1 | 7 | 0 | 1 |
| Ziina | https://api.ashbyhq.com/posting-api/job-board/ziina | 13 | 0 | 13 | 0 | 4 |
| AlgoDriven | https://api.ashbyhq.com/posting-api/job-board/algodriven | 1 | 0 | 0 | 0 | 0 |
| **Total** | | **36** | **1** | **24** | **0** | **8** |

UAE locations mean the configured Dubai, Abu Dhabi, Sharjah, or explicitly UAE
remote matching rule. These columns overlap; they are not additive stages.
The one recent job also failed the target-role check. Eight older UAE target
jobs scored at least 75, but none can qualify because none was recent.

## Data quality and qualification

- Older than seven days: **35**.
- Missing publication dates: **0**.
- Invalid or naive publication dates: **0**.
- Future publication dates: **0**.
- Failed configured sources: **0**.
- UAE target roles across all posting ages: **9**.
- UAE target roles whose experience could not be verified by the current
  conservative parser: **9**. This is a parser result, not a claim that every
  employer omitted experience information.
- Recent UAE target roles: **0**.
- Recent UAE target roles passing the CV threshold: **0**.
- Jobs qualifying for creation: **0**.

Public endpoint methods are described in the
[Greenhouse Job Board API documentation](https://docs.greenhouse.io/job-board)
and [Ashby Job Postings API documentation](https://developers.ashbyhq.com/docs/public-job-posting-api).
Greenhouse uses first publication; Ashby reports last publication and may
include reposts. The test evaluates the source-reported dates, not independent
proof of the original posting's age.

## Reliability assessment

This is a successful negative-path live test: API connectivity, existing profile
lookup, public payload normalization, date/location filtering, aggregate local
CV scoring, and the no-write command all worked. It does **not** exercise a
qualifying live insert, PostgreSQL write/concurrency behavior, or submission.

The implementation is suitable for conservative job-only saving with admin
review, while keeping every eligibility gate unchanged; this particular
snapshot would save zero jobs. It is not evidence of comprehensive UAE market
coverage or readiness for automatic applications. Review the nine unverified
experience requirements before improving extraction, with tests for any new
supported wording. Keep ambiguous requirements blocked rather than relax the
filter to obtain results. Different board URLs without shared employer IDs
remain a cross-source deduplication limitation.

To repeat verification without loading Django or CV data:

```powershell
.\.venv\Scripts\python.exe scripts/verify_discovery_sources.py
```

If network access is unavailable in an agent session, run the verification
script and the dry-run command above locally and share only their aggregate
stdout/stderr and exit status. Do not share `.env`, CV text, or database dumps.

After adding diagnostic counters, **38 isolated tests passed**, Django system
checks passed, and `git diff --check` reported no whitespace errors. The tests
use in-memory SQLite and mocked HTTP; they do not alter existing records.
