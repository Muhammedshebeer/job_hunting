# Company feed audit and combined discovery — 10 November 2026

**Historical snapshot:** counts and decisions below used the earlier 75% / seven-day policy. They are not current-policy results. Phase 4A now uses 50% / 10 days; rerun discovery for current counts. See [PHASE4A.md](PHASE4A.md).

The audit uses the saved 9 November `LIVE_DISCOVERY_REPORT.md`, the current
authorized public API responses, and public employer listing evidence. There
is no long-term feed archive; do not interpret these observations as a complete
historical record. Relevant below means **approved title plus qualifying UAE
primary location**, before freshness, experience, description, and CV checks.

## Original eight boards

| Employer | Current fetched | UAE target-role matches, all ages | Historical/local evidence and interpretation |
| --- | ---: | ---: | --- |
| Ruya AI | 8 | 3 | Also returned 3 UAE target roles in the saved 9 November snapshot. Useful watch source. |
| Sphere IT Consultants | 14 | 2 | Also returned 2 UAE target roles on 9 November. Its public board lists Dubai Python and Fullstack vacancies. Useful watch source. |
| Ziina | 13 | 4 | Also returned 4 UAE target roles on 9 November. Useful watch source. |
| AlgoDriven | 1 | 0 | No strict matches in either local snapshot. An indexed public Dubai Backend Engineer page is historical evidence of relevance, not evidence of a currently qualifying API record. |
| Sparkland | 2 | 0 | No current strict matches. A public software-engineering listing mentions Dubai in its description, but its generic Remote location does not establish UAE eligibility under the current filter. |
| qlub | 30 | 0 | No current match to both the approved title and UAE location. Public Dubai data-engineering vacancies do not justify expanding the approved role list. |
| Asula Labs | 3 | 1 | Currently returns a Dubai Software Engineer listing; no older local audit for this board. |
| MultiBank Group | 14 | 1 | Currently returns one strict UAE target role; no older local audit for this board. |

The 11 current UAE target-role matches from these eight boards fail freshness;
they are not qualified or review jobs. Neither title/location relevance nor an
old indexed page establishes a verified 1-3 year requirement or a recent date.

Public supporting listing pages:

- [Sphere IT Dubai Python Developer](https://job-boards.greenhouse.io/sphereit/jobs/4122987008)
- [Ziina Dubai Backend Engineer](https://jobs.ashbyhq.com/ziina/b52d240f-2ccb-4aea-800b-623e9ca8ae09/)
- [AlgoDriven Dubai Backend Engineer](https://jobs.ashbyhq.com/algodriven/a1854f09-0488-47bc-b0aa-b5b3d6fa9ea9/)
- [Sparkland software engineer, generic Remote primary location](https://job-boards.greenhouse.io/sparkland/jobs/5435666004)
- [Asula Dubai Software Engineer](https://jobs.ashbyhq.com/asula/ef6a983e-6c8c-4344-a4b4-7b05874128ee)

## Added, actually verified public feeds

Each endpoint was requested directly and returned HTTP 200, valid JSON, a jobs
list, and publication dates. These are documented public Ashby GET endpoints,
not guessed board identifiers. The exact `LeanTech` board capitalization comes
from the employer's public listing URL.

| Employer | Endpoint | Fetched | Current UAE target-role matches |
| --- | --- | ---: | ---: |
| Lean Technologies | https://api.ashbyhq.com/posting-api/job-board/LeanTech | 9 | 1 |
| Field Technologies FZCO | https://api.ashbyhq.com/posting-api/job-board/infinitefield | 2 | 1 |
| Ode with Anthropic | https://api.ashbyhq.com/posting-api/job-board/odewithanthropic | 15 | 0 |

Public employer evidence:
[Lean Technologies Dubai software role](https://jobs.ashbyhq.com/LeanTech/3e9d10c4-fd9c-4cc3-9fee-bfe6c15c76f0),
[Field Technologies Dubai Software Engineer](https://jobs.ashbyhq.com/infinitefield/ca26ce18-d781-41ed-af28-59261a87430d/),
and [Ode UAE Software Engineer](https://jobs.ashbyhq.com/odewithanthropic/a546a133-5856-4372-a960-f3632bec207f).
The Ode page demonstrates UAE hiring relevance but the current API snapshot
has zero matches under the unchanged primary-location/title rules. It is a
lower-priority watch source, not a promised supply of suitable jobs.

Speechify was considered using a real public board identifier. Although its
endpoint returned HTTP 200, the response failed the existing bounded verifier.
It was **not added** or enabled. No response-size/access protections were bypassed.

## Latest combined run with existing profile 1

All 11 company sources and the existing authorized Gmail label ran with
`--dry-run`. No database mutations, application creation, or submissions occurred.

| Filter stage | Cumulative candidates passing |
| --- | ---: |
| Fetched company jobs plus extracted email candidates | 112 (111 + 1) |
| Approved role | 24 |
| Approved role + UAE location | 13 |
| Above + verified last-seven-days publication | 0 |
| Above + verified experience overlap with 1-3 years | 0 |
| Above + CV score >=75 | 0 |
| Fully eligible after all required evidence | 0 |
| Review candidates | 0 |

No record reached experience/CV checks in the cumulative funnel; these zeroes
are not evidence of a CV mismatch. Known older jobs are rejected, not added to
review just to increase results. Unknown evidence still creates review records
when no earlier known disqualifier rejects the candidate.

| Source family/platform | Jobs/candidates fetched | Authenticated messages | Eligible | Review |
| --- | ---: | ---: | ---: | ---: |
| Greenhouse | 24 | N/A | 0 | 0 |
| Ashby | 87 | N/A | 0 | 0 |
| LinkedIn alerts | 0 | 0 | 0 | 0 |
| Indeed alerts | 1 | 1 | 0 | 0 |
| NaukriGulf alerts | 0 | 1 | 0 | 0 |
| Bayt alerts | 0 | 0 | 0 | 0 |

Source errors: **0**. Total eligible: **0**. Total review: **0**.
The existing email credentials, sender arrays, Gmail label, and authentication
code were reused unchanged. LinkedIn, Indeed, and NaukriGulf each have one
configured sender. LinkedIn supplied no message in this bounded run. **Bayt has
zero configured sender addresses and was therefore not searched**; its zero
message count is an inactive-configuration result. Add a genuine authenticated
Bayt alert sender yourself using EMAIL_ALERTS.md before expecting Bayt results;
no sender was invented or added during this change. These counts make no claim
about how many vacancies exist in any platform's job market.

Validation: **153 tests passed**, Django checks passed, migration consistency
passed, and the final combined live dry run returned zero source errors.
