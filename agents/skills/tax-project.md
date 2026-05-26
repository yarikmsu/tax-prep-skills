---
name: tax-project
description: Mid-year projection — project full-year tax from YTD figures, compute safe-harbor estimates, suggest W-4 4(c) and 1040-ES adjustments
---

# /tax-project — Mid-Year Tax Projection

Projects full-year federal (and conditional state) tax from year-to-date figures,
then computes safe-harbor required payments, a 1040-ES voucher schedule, and a
W-4 line 4(c) per-paycheck adjustment. Read-only: never modifies result.json or
taxpayer_profile.json. Re-runnable quarterly.

## Prerequisites
- `taxpayer_profile.json` and `tax_law_{year}.json` exist (else: run `/tax-setup {year}` first).
- Optional: `archive/{year-1}/` (from `/tax-archive`) — enables the 100%/110%-of-prior
  safe harbor and prior-year recurring-income defaults. Without it, runs in degraded mode.

## Steps

1. **Validate prerequisites.** Confirm `taxpayer_profile.json` and `tax_law_{year}.json`
   exist. If not, stop and tell the user to run `/tax-setup {year}`.

2. **Load or create `projection_inputs.json`.** If absent, copy
   `agents/templates/projection_inputs_template.json` to the workspace and interview the
   user for: YTD wages, YTD federal + state withholding, pay frequency, as-of date, and any
   known lumpy items (year-end bonus, RSU vesting, capital gains, self-employment net).
   Validate inputs (no shell metacharacters; numeric fields numeric). `chmod 600` the file.

3. **Resolve prior-year baseline.** If `archive/{year-1}/` exists, read its
   `result.json` to pre-fill `recurring_from_prior` (interest/dividends) as editable
   defaults. If absent, note degraded mode to the user.

4. **Confirm the annualization split.** Show the user which items will be annualized
   (steady wages/withholding) vs. treated as full-year (overrides, recurring), and the
   elapsed fraction. Let the user correct lumpy overrides before computing.

5. **Compute.** Run:
   ```bash
   python3 -m agents.lib.projection --inputs projection_inputs.json \
       --workspace . --year {year} --as-of {MM-DD} --json
   ```
   (Omit `--as-of` to use today.)

6. **Render + save.** Run the same command without `--json` to get the markdown report.
   Save it to `projections/TY{year}_asof_{MM-DD}.md` and `chmod 600` it. Print the report.

7. **Audit note.** Append one line to `answers.md` (NO dollar PII duplicated):
   ```
   ## Mid-year projection TY{year} as-of {MM-DD} — run {today}. Safe-harbor: {covered/underpaid}.
   ```

## Security & safety
- Subagents (if any) must NOT have network access and must NOT write outside `projections/`.
- Never extract or store full SSNs.
- This skill never writes `result.json` or `taxpayer_profile.json`.
- All written files (`projection_inputs.json`, `projections/*.md`) → `chmod 600`.
