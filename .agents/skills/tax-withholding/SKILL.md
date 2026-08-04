---
name: tax-withholding
description: Withholding true-up — compare YTD federal withholding against the actual computed liability in result.json, then recommend a W-4 4(c) and/or 1040-ES schedule to close the gap
---

# /tax-withholding — Federal Withholding True-Up

Compares YTD federal withholding + estimated payments (projected to year-end at the
current pace) against the **actual** computed liability in result.json, then recommends
a W-4 line 4(c) per-paycheck adjustment and/or a 1040-ES voucher schedule to hit the
safe-harbor (penalty-avoidance) and zero-balance targets. Read-only: never modifies
result.json or taxpayer_profile.json. Re-runnable.

Distinct from `/tax-project`: this trues up against the real computed number
(result.json line 24), not an annualized estimate. Run it after `/tax-compute` has
produced a current-year result.json.

**State coverage: CA only.** When result.json carries a computed `california_540`
section, the report adds a **California true-up** block: the actual Form 540 tax vs
YTD CA withholding projected at the current pace, the FTB 5805 safe harbor (90% of
current / 100%–110% of prior CA tax — EXCEPT current-year CA AGI ≥ $1M / $500K MFS
forces 90%-of-current only per R&TC §19136.3), a DE 4 per-paycheck
extra-withholding figure, and a 540-ES voucher schedule under CA's **30/40/0/30**
installment weighting (no September payment — R&TC §19136.1). Non-CA returns get
the federal report plus a "CA only" caveat.

## Prerequisites
- `result.json` with a computed `form_1040.line_24_total_tax` (else: run `/tax-compute`).
- `projection_inputs.json` (else: run `/tax-project` first, or create it from
  `agents/templates/projection_inputs_template.json` and fill YTD federal withholding,
  estimated payments, pay frequency, and as-of date).
- `tax_law_{year}.json`.
- Optional: `archive/{year-1}/` — enables the 100%/110%-of-prior safe harbor. Without
  it, runs in degraded mode (90%-of-current test only).

## Steps

1. **Validate prerequisites.** Confirm `result.json`, `projection_inputs.json`, and
   `tax_law_{year}.json` exist. If `result.json` lacks a computed line 24, stop and tell
   the user to run `/tax-compute`.

2. **Resolve prior-year baseline.** If `archive/{year-1}/` exists it is used for the
   prior-year safe harbor; otherwise note degraded mode to the user.

3. **Compute.** Run:
   ```bash
   python3 -m agents.lib.withholding --result result.json --inputs projection_inputs.json \
       --workspace . --year {year} --as-of {MM-DD} --json
   ```
   (Omit `--as-of` to use today.)

4. **Render + save.** Run the same command without `--json` to get the markdown report.
   Save it to `withholding/TY{year}_asof_{MM-DD}.md` and `chmod 600` it. Print the report.

5. **Audit note.** Append one line to `answers.md` (NO dollar PII duplicated):
   ```
   ## Withholding true-up TY{year} as-of {MM-DD} — run {today}. Safe-harbor: {covered/underpaid}.
   ```

## Security & safety
- Any delegated sub-task must have NO network access and must NOT write outside `withholding/`.
- Never extract or store full SSNs.
- This skill never writes `result.json` or `taxpayer_profile.json`.
- All written files (`withholding/*.md`) → `chmod 600`.
