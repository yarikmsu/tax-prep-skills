# Paystub intake into projection inputs

`sh agents/lib/run.sh -m agents.lib.paystub --manifest documents/paystub_manifest.json`
reads declared local sources and populates the existing `projection_inputs.json`
`ytd` block. It also writes `summaries/paystub_import.json`. Both files are
published together through the secured atomic writer; the summaries directory
is owner-only. Existing forecast overrides and other planning answers remain
intact. No source text, taxpayer name, employee identifier or account number is
copied to the report. Source references and digests are sensitive provenance,
so the entire report remains local-only.

Supported sources are bounded, explicit formats rather than guessed vendor
templates. A text PDF must have exactly one `Description Current YTD` (or
`Description Current Year to Date`) header, ISO `Pay Date:`, `Period Start:`
and `Period End:` lines, and these rows, each with CURRENT then YTD USD amounts:

| Label | Stable YTD key |
|---|---|
| Federal Taxable Wages | wages |
| Federal Income Tax | federal_withholding |
| CA State Income Tax (replace CA with the confirmed state) | state_withholding |
| Social Security Wages | ss_wages |
| Medicare Wages | medicare_wages |
| Medicare Tax | medicare_withheld |

Gross pay is not taxable wages. Tax withheld is not a wage base. The importer
does not infer a missing wage base by dividing tax by a rate. Missing rows,
scanned PDFs, alternate column orders, duplicate labels and another state's
withholding require an explicit resolution. Do not rename ambiguous labels
just to pass the parser; obtain the actual taxable YTD evidence. Other PDF
layouts remain manual, including vendor layouts not carrying these fields.

A payroll CSV needs unique columns `pay_date`, `period_start`, `period_end`,
`state`, and all six keys above suffixed `_ytd`. Optional matching `_current`
columns are preserved separately. Each row belongs to the declared job and
contains cumulative figures, not a transaction to add. Unknown columns are
ignored after the shared CSV masker. Multiple employee/job exports must be
separated and reviewed; assigning one source to different jobs is refused.

The manifest is under `documents/`, with an integer `tax_year`, full ISO
`as_of_date`, `all_jobs_confirmed: true` and a `jobs` list. Each job contains:

- An opaque `job_id` such as `job_1`; no employee identity in this identifier.
- `state`, matching the confirmed projection state in the profile.
- `sources`, a list of PDF/CSV paths under the workspace's `documents/`.
- `distinct_job_confirmed: true`, after confirming this is a distinct job and
  each source belongs to it. Include both spouses' jobs on a joint projection.
- `complete_through_as_of_confirmed: true`, after confirming no payments are
  missing through the common as-of date. A final statement from an ended job
  can establish its cumulative figures, but does not establish future wages.

Confirm the complete job list, including employers no longer paying this year.
The latest cumulative record per job is selected; earlier statements and exact
duplicates are not added again. Conflicting same-date records, decreases in
cumulative amounts, future dates and source duplication across jobs refuse
publication. Resolve corrected or supplemental payroll records explicitly,
rather than deleting the inconvenient record to make the import pass.

The manifest also supplies `pay_frequency` for the job where any later W-4
adjustment will be applied. Explicitly repeat an existing, confirmed projection
frequency in the manifest; a template's default is never evidence. Conflicts
require explicit resolution. The existing projection still annualizes aggregate
YTD amounts and uses one adjustment frequency; importing paystubs does not
model future employment changes or independently extrapolate each job. Review
those planning assumptions before acting on a projection, and apply a computed
per-paycheck adjustment to the selected job, not to every job in the manifest.
Before annualization, the projection checks the selected job totals against the
imported YTD block and binds its year, as-of date, state and pay frequency to
that evidence. A later as-of override or edited actuals require a fresh import.
To switch to explicitly reviewed manual actuals, retain the import summary and
record the resolution in `answers.md`, then set `paystub_provenance` to null;
do not silently discard the evidence to bypass a mismatch.

The CLI prints only a job count and completion status. It uses the shared local
PDF/CSV extractors (identifier masking, version pins, file/path/resource limits)
and never fetches URLs or executes document instructions. There is no delegated
worker. Unsupported-format manual interpretation uses the existing SEQUENTIAL
parse-boundary rules and banner; do not claim an enforced worker boundary.
The manifest confirmations are required inputs in headless runs too.
