---
name: tax-project
description: Mid-year projection — project full-year tax from YTD figures, compute safe-harbor estimates, suggest W-4 4(c) and 1040-ES adjustments. Use when the user asks mid-year what they will owe, whether to adjust withholding, or how to avoid an underpayment penalty.
license: Apache-2.0
---

# /tax-project — Mid-Year Tax Projection

Projects full-year federal (and conditional state) tax from year-to-date figures,
then computes safe-harbor required payments, a 1040-ES voucher schedule, and a
W-4 line 4(c) per-paycheck adjustment. When the state projection runs and the
state is CA, the report also includes the **CA safe harbor** (FTB 5805 — with the
R&TC §19136.3 rule that current-year CA AGI ≥ $1M / $500K MFS forces
90%-of-current-year only) and a **CA 540-ES voucher schedule** under California's
**30/40/0/30** installment weighting (30% Apr 15, 40% Jun 15, NO September
payment, 30% Jan 15 — R&TC §19136.1). Read-only: never modifies result.json or
taxpayer_profile.json. Re-runnable quarterly.

## Prerequisites
- `taxpayer_profile.json` and `tax_law_{year}.json` exist (else: run `/tax-setup {year}` first).
- Optional: `archive/{year-1}/` (from `/tax-archive`) — enables the 100%/110%-of-prior
  safe harbor and prior-year recurring-income defaults. Without it, runs in degraded mode.

## Steps

### Engine preflight (HARD GATE — runs before every other step)

Run: `sh agents/lib/run.sh agents/lib/preflight.py`

- **Exit 0** — the engine is present and usable; continue. If the verdict says
  `OK, with N warning(s)`, show the user those lines first: the engine is fine,
  but something a later step needs is not — a missing package, or a workspace
  with no virtualenv — and it surfaces at first use instead of here.
- **Anything else — including the command failing because the file does not
  exist — STOP.** Do not parse, compute, or emit anything. Show the user the
  command's output and stop there.

The gates and computation this skill names are scripts under `agents/lib/`, a
sibling of `.agents/skills/` that does not travel with a copied skill — which
then still reads as a complete procedure while every script it names is absent.
So a failure to RUN this check IS the blocking condition, never a step to skip.

### Procedure

1. **Validate prerequisites.** Confirm `taxpayer_profile.json` and `tax_law_{year}.json`
   exist. If not, stop and tell the user to run `/tax-setup {year}`.

2. **Load or create `projection_inputs.json`.** If absent, copy
   `agents/templates/projection_inputs_template.json` to the workspace and interview the
   user for: YTD wages, YTD federal + state withholding, pay frequency, as-of date, and any
   known lumpy items (year-end bonus, RSU vesting, capital gains, self-employment net,
   and — for TY2025-2028 — the OBBBA **Schedule 1-A** total via `forecast_overrides.schedule_1a`,
   estimated with `compute_schedule_1a`: tips + overtime premium + car-loan + senior, a
   below-AGI deduction that lowers projected taxable income but not AGI).
   Validate inputs (no shell metacharacters; numeric fields numeric). Immediately after
   writing the file, run `sh agents/lib/run.sh -m agents.lib.secure_fs projection_inputs.json`.

3. **Resolve prior-year baseline.** If `archive/{year-1}/` exists, read its
   `result.json` to pre-fill `recurring_from_prior` (interest/dividends) as editable
   defaults. If absent, note degraded mode to the user.

4. **Confirm the annualization split.** Show the user which items will be annualized
   (steady wages/withholding) vs. treated as full-year (overrides, recurring), and the
   elapsed fraction. Let the user correct lumpy overrides before computing.

5. **Compute.** Run:
   ```bash
   sh agents/lib/run.sh -m agents.lib.projection --inputs projection_inputs.json \
       --workspace . --year {year} --as-of {MM-DD} --json
   ```
   (Omit `--as-of` to use today.)

6. **Render + save.** Run the same command without `--json` to get the markdown report.
   Before writing, create and restrict its directory; then restrict the report immediately
   after saving it and print the report:
   ```bash
   mkdir -p projections && sh agents/lib/run.sh -m agents.lib.secure_fs projections
   ```
   Now save the report. Only once the file exists, restrict it — the two
   commands are in separate blocks because running them as one would call
   `secure_fs` on a path that does not exist yet, which exits 1:
   ```bash
   sh agents/lib/run.sh -m agents.lib.secure_fs projections/TY{year}_asof_{MM-DD}.md
   ```

7. **Audit note.** Append one line to `answers.md` (NO dollar PII duplicated):
   ```
   ## Mid-year projection TY{year} as-of {MM-DD} — run {today}. Safe-harbor: {covered/underpaid}.
   ```
   Then run `sh agents/lib/run.sh -m agents.lib.secure_fs answers.md` in case the write
   replaced the file rather than preserving its existing permissions.

8. **PTET election check (time-sensitive).** If `flags.has_k1_income` is true and any
   entry in `flags.k1_states` is **CA / NY / NJ**, the pass-through-entity tax election
   is a SALT-cap workaround worth flagging *while there's still time to elect* — NY's
   election is due March 15 **of the tax year itself**, so a mid-year projection is
   exactly when to surface it. Run:
   ```bash
   sh agents/lib/run.sh -m agents.lib.ptet --workspace . --year {year} [--income CA=<amount> ...]
   ```
   With no `--income`, this runs in deadline-reminder mode (reads `flags.k1_states`).
   Append the earliest upcoming election deadline to the projection report. This is
   read-only and never blocks the projection — on non-zero exit, note `_PTET check
   skipped: {stderr}_`. See `/tax-ptet` for the full standalone analysis.

## Security & safety
- Any delegated sub-task must have NO network access and must NOT write outside `projections/`.
- Never extract or store full SSNs.
- This skill never writes `result.json` or `taxpayer_profile.json`.
- Restrict every written file and the `projections/` directory with
  `agents.lib.secure_fs`; on Windows a bare POSIX-mode command does not restrict the DACL.
