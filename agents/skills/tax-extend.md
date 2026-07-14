---
name: tax-extend
description: File-an-extension flow — Form 4868 with the extension payment computed from the best available estimate, CA FTB 3519 payment guidance, and calendar deadline events
---

# /tax-extend — Extension (Form 4868 + state)

Prepare the April extension decision: how much to pay with Form 4868, how the
resident state handles extensions (CA: automatic, payment-only via FTB 3519),
and the calendar events for the new deadlines.

**The one fact that drives everything: an extension extends the time to FILE,
never the time to PAY.** Unpaid tax accrues interest plus the 0.5%/month
late-payment penalty from April 15 even with a valid extension — so the
payment estimate below is the deliverable, not the form.

## Usage
```
/tax-extend
```

## Prerequisites
- `taxpayer_profile.json` exists (run `/tax-setup` first).
- Best case: a computed `result.json` (run `/tax-compute`). Degrades to a
  `/tax-project` projection, then to a user-supplied estimate — each step
  is LESS reliable and says so.

## Steps

### 1. Establish the balance-due estimate (best available source)

Work down this ladder and TELL the user which rung was used:

1. **Computed return** — `result.json` has a computed
   `form_1040.line_24_total_tax` (and the meta.tax_year matches):
   - Federal extension payment = line 37 amount owed (line 24 total tax −
     line 33 total payments), floored at $0.
   - CA payment (if `california_540` computed) = `ca_total_tax` −
     (`ca_withholding_box17` + CA estimates paid), floored at $0.
2. **Projection** — no computed return: run `/tax-project` (or
   `python3 -m agents.lib.projection ... --json`) and use
   `federal.balance` / `state.balance` (positive = owed). Label the figures
   PROJECTED and recommend a cushion (see step 2).
3. **User estimate** — no YTD data either: ask the user for their best
   estimate of total tax and payments so far. Label clearly.

### 2. Recommend the payment amount

- **Pay the full estimated balance** — the extension is valid even with no
  payment, but interest + 0.5%/month late-payment penalty run on whatever is
  unpaid. **The estimate itself must be made in good faith:** Form 4868 must
  "show the amount properly estimated as tax" (Treas. Reg. §1.6081-4) — a
  lowball estimate to dodge the payment can retroactively VOID the
  extension (Crocker v. Comm'r, 92 T.C. 899), making the return late from
  April 15. This matters most on rung 3 (user estimate) — push back on a
  figure that looks optimistic. Note: if at least 90% of the actual tax is paid by April 15 and
  the rest with the timely-extended return, the late-payment penalty is
  waived (interest still runs) — so a **10% cushion above the projected
  balance** is the standard recommendation when the figure came from rung 2
  or 3.
- If the estimate shows a REFUND: no payment needed — file the extension for
  the filing-deadline protection alone (or consider just filing on time).
- An overpayment comes back with the filed return (or applies to next year's
  estimates); underpaying costs interest + penalty — the asymmetry favors
  rounding UP.

### 3. How to file the federal extension

- **Paying electronically IS filing**: an IRS Direct Pay / EFTPS / card
  payment designated "Form 4868 / extension" files the extension
  automatically — no paper form needed. Otherwise file Form 4868 by mail or
  through software by April 15.
- No signature, no reason required. The extension moves the filing deadline
  to October 15.
- **What it does NOT move:** the payment deadline (above), IRA/HSA
  contribution deadlines (April 15 regardless — but SEP-IRA and solo-401(k)
  EMPLOYER contributions DO follow the extended deadline), and Q1 1040-ES
  (also due April 15 — a taxpayer extending AND owing estimates makes two
  separate payments).

### 4. State extension (per `address.state`)

- **CA:** the extension is **AUTOMATIC — there is no state form**. Any CA
  balance due must be PAID by April 15 via **FTB Form 3519** (Web Pay
  preferred; mandatory e-pay may apply once payments exceed $20,000 or
  total tax exceeds $80,000 — R&TC §19011.5). The extended CA filing
  deadline is October 15.
- **Other states:** rules vary (some piggyback on the federal 4868, some
  need their own form, a few have no extension) — check the state DOR and
  say explicitly that only CA is modeled in depth.

### 5. Record + calendar

1. Set `flags.filed_extension = true` in `taxpayer_profile.json` (this is a
   skill-set flag — the interview never asks it).
2. Regenerate the calendar so the new deadlines are subscribable:
   ```bash
   python3 -m agents.lib.tax_calendar --profile taxpayer_profile.json \
       --workspace . --year {year}
   ```
   This adds two `extension` events: **April 15 {year+1}** (4868 + payment
   due — with the FTB 3519 note for CA residents) and **October 15
   {year+1}** (extended filing deadline; the late-FILING penalty — 5%/month
   up to 25%, offset by any concurrent late-payment penalty per §6651(c)(1)
   — BEGINS after it: with a valid extension it never ran before).
3. Append to `answers.md` (NO dollar figures — the amounts live in the
   conversation and result.json):
   ```markdown
   ## Extension — TY{year}, recorded {today}
   Federal 4868: filed via {method}. Payment made: {yes/no}.
   State ({code}): {automatic — FTB 3519 payment made: yes/no | not modeled}.
   Estimate source: {computed result.json | projection | user estimate}.
   ```

### 6. Print the summary

Show: the balance-due estimate (federal + CA) with its source rung, the
recommended payment (with the 90% waiver rationale when a cushion was
added), the how-to-pay instructions, and the two new calendar dates.

## Caveats
- Statutory dates only — when April 15 / October 15 falls on a weekend or DC
  holiday the IRS rolls the deadline forward; v1 prints the statutory date.
- Late-FILING penalty (5%/month, max 25%) is what the extension prevents;
  the late-PAYMENT penalty (0.5%/month) and interest are what the PAYMENT
  prevents. When BOTH run in the same month they do NOT stack: §6651(c)(1)
  reduces the 5% failure-to-file by the 0.5% failure-to-pay (combined 5%,
  not 5.5%). A return more than 60 days late also faces the minimum FTF
  penalty (lesser of an indexed ~$510 or 100% of the unpaid tax).
- Form 2350 (citizens abroad), combat-zone postponements, and disaster-relief
  postponements are not modeled.
- This skill never modifies `result.json`; it writes only the profile flag,
  the answers.md entry, and the regenerated `.ics`.
