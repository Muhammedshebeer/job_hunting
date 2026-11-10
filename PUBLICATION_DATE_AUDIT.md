# Publication-date audit — 10 November 2026

**Historical snapshot:** counts and decisions below used the earlier 75% / seven-day policy. They are not current-policy results. Phase 4A now uses 50% / 10 days; rerun discovery for current counts. See [PHASE4A.md](PHASE4A.md).

Confirmed from an authorized combined **dry run** against all configured feeds
and the existing Gmail integration. No PostgreSQL records, credentials,
migrations, or applications were changed.

The audit used the actual clock: **2026-11-10 09:57:58.204156 +04:00** in
`Asia/Dubai` (**05:57:58.204156 UTC**). The inclusive rolling window began at
**2026-11-03 09:57:58.204156 +04:00**, exactly 168 hours earlier. It is not a
midnight-to-midnight calendar-week calculation. Dubai conversion preserves each
instant and does not change whether it passes the filter.

## Confirmed cause for all 13 failures

All 13 role-and-UAE matches contained parseable timezone-aware source publication
timestamps. Every timestamp was older than the cutoff. There were no missing,
invalid, naive, or future publication dates among these 13.

All dates below are **UAE time (+04:00)**; source field is shown explicitly.

| Company | Job title | Location | Publication field | Parsed publication time | Date result |
| --- | --- | --- | --- | --- | --- |
| Ruya AI | AI Engineer | Dubai · Bosnia · Serbia | first_published | 2026-08-04 13:41:42 | older_than_seven_days |
| Ruya AI | Backend Data Engineer | Dubai · Bosnia · Serbia | first_published | 2026-08-04 15:18:06 | older_than_seven_days |
| Ruya AI | Senior Full-Stack Frontend Engineer | Dubai · Bosnia · Serbia | first_published | 2026-08-04 15:08:32 | older_than_seven_days |
| Sphere IT Consultants | Fullstack Developer (Springboot and Flutter) | Dubai | first_published | 2025-05-12 17:00:35 | older_than_seven_days |
| Sphere IT Consultants | Python Developer | Dubai, UAE | first_published | 2024-09-10 10:32:28 | older_than_seven_days |
| Ziina | Backend Engineer, Payments Platform | Dubai | publishedAt | 2026-08-12 09:44:50.125 | older_than_seven_days |
| Ziina | Senior Full-Stack Engineer (Web) | Dubai | publishedAt | 2025-10-28 10:56:43 | older_than_seven_days |
| Ziina | Full-Stack Engineer (Internal Tools) | Dubai | publishedAt | 2026-08-11 14:21:10.982 | older_than_seven_days |
| Ziina | Senior Backend Engineer, Payments Experience | Dubai | publishedAt | 2026-08-12 09:28:55.518 | older_than_seven_days |
| Asula Labs | Software Engineer: Intern/New Grad | Dubai | publishedAt | 2025-10-21 02:02:55.209 | older_than_seven_days |
| MultiBank Group | AI Engineer – Generative AI and Automation | Dubai Office | publishedAt | 2026-06-02 16:54:39.281 | older_than_seven_days |
| Lean Technologies | Senior Software Engineer | Dubai, United Arab Emirates | publishedAt | 2026-11-02 13:27:19.212 | older_than_seven_days |
| Field Technologies FZCO | Software Engineer | Dubai | publishedAt | 2025-01-27 15:57:31.666 | older_than_seven_days |

The nearest miss was Lean Technologies at about **7.855 days old**. The other
jobs were about **90–791 days old**. A four-hour timezone conversion cannot make
these valid seven-day matches. No freshness relaxation is justified.

## Publication versus modification semantics

- **Greenhouse:** the documented detail payload has `first_published`, the
  original publication field used by this adapter. `updated_at` is separate
  and is never a fallback. If list data lacks first publication, the existing
  bounded detail request obtains it; if detail also lacks it, it stays unknown.
  [Greenhouse Job Board API](https://docs.greenhouse.io/job-board).
- **Ashby:** `publishedAt` is documented as **last publication**. An explicit
  republished timestamp can therefore qualify; merely modifying a description
  does not create a new posting date. Original creation and modification fields
  are not fallbacks. The API does not establish an independent original-publication
  date when only last publication is exposed.
  [Ashby Job Postings API](https://developers.ashbyhq.com/docs/public-job-posting-api).
- **Mapped public feeds:** previously, an arbitrary mapping could put
  `updated_at` into normalized `posted_at`. This was a real potential provenance
  gap, although the mapped feed is disabled and did not cause these 13 failures.
  Mapping now requires documented `original_publication` or `last_publication`
  semantics. Update/modify/fetch/receipt/creation fields remain unsuitable even
  if relabelled. Missing/ambiguous semantics produce review candidates, not
  automatic old-date rejection, when other known requirements pass.
- **Normalized authorized JSON:** its contract requires actual publication in
  `posted_at`; no updated/modified/fetched field is substituted. A provider that
  uses other field names should use the documented mapper and declare semantics.
- **Gmail:** only the specific job's `JobPosting.datePosted` or an explicit
  posting-date label is considered. Email `Date`, delivery time, IMAP search
  dates, and `INTERNALDATE` do not supply publication. Relative and date-only
  values remain unverified. The Indeed candidate did not match the role filter,
  and the NaukriGulf message had no recognized candidate; neither supplied one
  of the 13 audited role-and-UAE matches.

## Implemented diagnostics and narrow fixes

`--diagnose-dates` is explicitly enabled and requires `--dry-run`. It reports
only whitelisted candidate company/title/location, source field/semantics,
parsed UTC/UAE timestamps, age, and date result. It does not print job URLs,
query parameters, private email bodies, mailbox credentials, or CV content.
Email addresses/URLs/control characters are removed from displayed metadata.
Invalid input values are not echoed. An unverified mapped timestamp may be
shown as a normalized **ignored** timestamp, never as an accepted posting date.

Missing publication, invalid input, date-only ambiguity, and missing timezone
have distinct diagnostics. Qualification still retains them for review unless
another known role/location/experience/CV/closed-status disqualifier rejects the
job. Known older-than-seven-days timestamps stay rejected. New record metadata
also retains exact source publication fields and semantics; existing records
are not rewritten.

Mapped feed configurations with a `posted_at` mapping must set:

```json
{
  "publication_date_semantics": "original_publication",
  "publication_date_reference": "Actual provider documentation explaining this publication field"
}
```

Use `last_publication` only where the provider explicitly documents that meaning.
The disabled example has placeholders that must be replaced with real evidence.
Changing these declarations cannot turn a modification field into a publication
field. None of these safeguards changes the company-feed mappings or the
approved roles, UAE locations, experience requirements, CV scorer, or 75% threshold.

## PowerShell: date details plus combined summary

```powershell
Set-Location C:\Users\Admin\Downloads\uae_job_agent_starter\uae_job_agent
.\.venv\Scripts\python.exe manage.py discover_all_jobs --sources discovery_sources.phase2c.json --profile-id 1 --dry-run --summary --diagnose-dates
```

`DATE_WINDOW` shows the actual clock and cutoff used for that run.
`DATE_CANDIDATE` shows each role-and-UAE match before date qualification.
The remaining output is the ordinary combined summary. Omit `--diagnose-dates`
for aggregate-only reporting. Dates and counts naturally change on later runs.

## Validation

**170 tests passed**, including the previous 153 tests and 17 date/provenance
regressions. Coverage includes invalid/missing/naive dates, UTC/UAE offsets,
exact 168-hour boundaries, future timestamps, source-specific fields, no update
fallback, explicit last-publication timestamps, mapped-feed review handling,
email timestamp exclusion, and no database mutations during diagnostic runs.
Tests use isolated SQLite and synthetic sources; live verification used the
existing PostgreSQL profile read-only. No migration is required.
