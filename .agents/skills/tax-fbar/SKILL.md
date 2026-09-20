---
name: tax-fbar
description: Prepare FBAR (FinCEN 114) filing document for foreign bank accounts. Use when the taxpayer has ANY foreign bank or brokerage account, or signature authority over someone else's foreign account (an employer's, a parent's, a nonprofit's) — the $10,000 aggregate test is what this skill performs, not a precondition for running it — or when the user mentions FBAR, FinCEN 114, or foreign account reporting.
license: Apache-2.0
---

# /tax-fbar

Prepare FBAR (Report of Foreign Bank and Financial Accounts) filing documentation.

## Usage
```
/tax-fbar
```

## Prerequisites
- `result.json` must exist with foreign account data
- `taxpayer_profile.json` must exist. Run this skill when
  `has_foreign_accounts` **OR** `signature_authority_foreign_accounts` is
  true — signature authority alone triggers FinCEN 114 Part IV, even with no
  owned foreign account and nothing on the tax return.
- `summaries/*.md` should have detailed foreign account information

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

### 0.5. Tax Law Freshness Verification

FBAR values depend on exchange rates from `tax_law_{year}.json`. Verify data is current:

1. **Validate** `{year}` is a 4-digit integer (2000–2099) before using in any command.
2. Run: `sh agents/lib/run.sh agents/lib/tax_law_fetcher.py freshness {year}`
3. **Exit 2 means NOT_FOUND — the STOP below.** It is the gates' blocked code,
   spent on the one status whose remedy is a stop; STALE and INCOMPLETE exit 0
   because their remedies are inline and are spelled out here. A 2 with an EMPTY
   report is not this gate at all — it is the launcher refusing (no venv, wrong
   interpreter), so read the JSON before acting on the code. Act on the status,
   which says WHICH figures:
   - **FRESH:** Proceed. Print the exchange rates being used (e.g., `EUR: 1.08`).
   - **STALE:** search the web for updated IRS yearly average exchange rates (live results required — tax figures change annually; if your agent's web access is unavailable or cached-only, STOP and ask the user to paste the current figures with their source URL). Re-fetch if changed.
   - **INCOMPLETE:** Check if `exchange_rates` section is populated. If empty, fetch by searching the web for: `"IRS yearly average exchange rate {year} {currency}"`.
   - **NOT_FOUND:** **STOP.** Tell user to run `/tax-setup` first.
4. Print exchange rates being used so user can verify:
   ```
   Exchange Rates — TY{year}
   {currency}: {rate} per USD (IRS yearly average)
   ```

### 1. Check FBAR Requirement

Read foreign account data from `result.json` sections:
- `form_8938_fatca.accounts` — account details
- `fbar_fincen_114` — FBAR status
- `interest_detail` — foreign interest sources
- `dividend_detail` — foreign dividend sources

Sum maximum balances across ALL foreign financial accounts — **including
signature-authority-only accounts** (31 CFR 1010.350: they count toward the
$10K aggregate and go on Part IV even though they never appear on the tax
return or in any parsed document). Ask the signature-authority question
(step 1.6) BEFORE concluding "not required": a not-required verdict reached
from owned accounts alone is incomplete.
If aggregate maximum value > `fbar_threshold` (from `tax_law JSON`) at any point during the year: FBAR is required.

### 1.5. Account Completeness Probe

Before computing FBAR values, verify the account list is complete. **First check `answers.md` — if foreign accounts were already confirmed complete, skip to Step 1.6.** Otherwise, ask the user, one topic at a time:

> **Non-interactive runs:** if this session cannot prompt the user, read the answers from `answers.md` and hard-stop on any missing REQUIRED answer — never guess. A guessed filing status, residency or account fact silently produces a wrong return (and an omitted foreign account is an unfiled FBAR).

1. Present found foreign accounts:
   ```
   Foreign accounts found in documents:
   1. {institution} — {country} — {account_type}
   2. ...
   ```
   Ask: "Is this the COMPLETE list of foreign financial accounts you had at ANY point during {year}?"
   Record the confirmation as `- **[foreign_account_completeness]** the answer` — the id is
   what `agents.lib.answers --check-required` matches on, and an omitted account is an
   unfiled FBAR.

2. Remind about commonly forgotten accounts:
   - Accounts closed during the year (still reportable for FBAR)
   - Accounts where you have signature authority but aren't the owner (e.g., business accounts, elderly parent's account)
   - Crypto exchanges with foreign domicile (Binance, Kraken non-US entities — conservative: include)
   - Insurance policies with cash value held by foreign companies
   - Foreign pension accounts

3. For each account, confirm:
   - Joint account or sole ownership?
   - Transit/pass-through account? (money passes through briefly — affects max value calculation)
   - Account opened or closed during the year?

4. If user identifies new accounts not in documents:
   - Ask for institution name, country, account type, approximate max balance
   - Add to the account list for processing

### 1.6. Ledger Set-Difference (deterministic completeness check)

The completeness probe above relies on the user's memory; this check is
mechanical. When `parse_ledger.json` exists (written by `/tax-parse` step 6.5):

1. Collect foreign payers from the ledger. The block is at column 0, fence and
   all: the `-c` body cannot be indented (Python below 3.14 raises
   IndentationError) and a fenced block indented INSIDE a list item, with a
   flush-left body, ends the item at the first body line and leaves the closing
   fence opening a new one. `tax-parse` has the same shape, also unindented.

```bash
sh agents/lib/run.sh -c "
import json
from agents.lib.tie_out import load_ledger, foreign_payers
ledger = load_ledger(json.load(open('parse_ledger.json')))
print('\n'.join(foreign_payers(ledger)) or '(none)')"
```

2. Diff against the FBAR account table: **every foreign payer in the ledger
   must appear as an institution in the account list** (or be explicitly
   justified in answers.md — e.g. "not a financial account: foreign employer
   issuing a wage statement", or for a crypto exchange "crypto-only all year,
   no fiat balance — not reportable per FinCEN Notice 2020-2"). An
   institution that earned interest in the parsed documents cannot silently
   miss the FBAR table — treat an unmatched payer as a missing account until
   resolved.
3. The reverse direction is fine: FBAR accounts with no ledger record are
   normal (an account that earned nothing generates no tax document).
4. Record the diff outcome in answers.md.

**Signature authority (explicit question, not reminder prose):** ask the user
directly, as its own topic — "Did you have signature authority over (but no
financial interest in) any foreign account in {year} — an employer's account,
a parent's account, a nonprofit's?" Record the yes/no in
`flags.signature_authority_foreign_accounts` in `taxpayer_profile.json` and
in answers.md. A YES adds those accounts to the FBAR (Part IV) even though
they never appear on the tax return or in any ledger document.

After recording the completeness/signature-authority answers, immediately
restrict both files again in case either write replaced the prior file:
```bash
sh agents/lib/run.sh -m agents.lib.secure_fs taxpayer_profile.json answers.md
```

### 2. Gather Account Details

For each foreign account, collect:

| Field | Source |
|-------|--------|
| Institution name (English) | summaries/*.md |
| Institution address | summaries/*.md or a web search |
| Country | result.json |
| Account number | result.json |
| Account type | Checking, Savings, Brokerage, Other |
| Maximum value during year (USD) | Computed from summaries |
| Currency | Original currency |
| Joint account? | taxpayer_profile.json |

**For institution addresses not in the data:**
Search the web to find:
- Search: `"{institution name} headquarters address"`
- Need: full mailing address for FBAR filing

### 3. Compute FBAR Maximum Values (Conservative)

For each account, determine the **FBAR maximum value** during the year. These are FBAR-specific conservative values — Form 8938 uses different methodology (see Step 4).

**Bank accounts:** Use the highest balance from any statement during the year.
Convert to USD using the IRS yearly average exchange rate from `tax_law_{year}.json`.

**Brokerage accounts:** Use the highest portfolio value (securities + cash).
For transit accounts (money passes through briefly): use the conservative maximum
(highest balance at any point, even if temporary).

**FBAR valuation rule:** Use the HIGHEST value at ANY point during the year (conservative). This will typically be ≥ the Form 8938 realistic simultaneous peak because FBAR counts each account's independent maximum, even if they didn't all peak simultaneously.

### 3.5. Value Reconciliation

Present computed maximum values with confidence levels for user review:

```
## FBAR Maximum Value Review (Conservative — for FinCEN 114 only)

| # | Account | Max Value (USD) | Confidence | Source |
|---|---------|-----------------|------------|--------|
| 1 | {name}  | ${amount}       | HIGH       | Year-end statement |
| 2 | {name}  | ${amount}       | MEDIUM     | Estimated from quarterly statements |
| 3 | {name}  | ${amount}       | LOW        | Single data point only |
```

**Confidence levels:**
- **HIGH:** Have monthly/daily statements covering the full year, or a clear year-end max balance
- **MEDIUM:** Have quarterly or semi-annual statements; max is interpolated
- **LOW:** Only have year-end balance or single data point; actual max during year could be higher

For **LOW** confidence accounts:
- Ask user: "For {account}, we only have {data}. Do you have additional statements that would help determine the maximum balance during the year?"
- If no additional data: use conservative (higher) estimate and note it

For **MEDIUM** confidence:
- State the assumption and ask if it's reasonable
- e.g., "We estimated max balance between Q2 and Q3 statements. Does this seem right?"

**Aggregate check:** Sum all FBAR max values. Confirm the aggregate maximum with the user. Note: these are FBAR conservative values — do NOT use them for Form 8938 threshold analysis (Step 4 computes 8938 values separately).

### 4. Also Check Form 8938

While preparing FBAR, separately evaluate Form 8938:
- Use `check_form_8938_required()` from `agents/lib/tax_computations/`, passing `form_8938_thresholds` from `tax_law JSON`
- ⚠️ Do NOT reuse the max values computed in Step 3. Compute separate REALISTIC simultaneous peak values for 8938 analysis.
- FBAR uses conservative maximums; 8938 uses realistic simultaneous peak — these are different numbers.
- Thresholds from `tax_law JSON` `federal.form_8938_thresholds` (e.g., MFJ in US = $100K year-end / $150K any time; unmarried abroad = $200K / $300K)

Report whether Form 8938 is required separately from FBAR.

### 4.5. PFIC / Form 8621 cross-check (G3)

The same foreign brokerage accounts being reported here frequently hold non-US
mutual funds or ETFs, which are almost always **PFICs** (§1291-1298) that **may
require Form 8621 per fund** — an EVENT-DRIVEN obligation (excess distribution,
sale, QEF/MTM election, or the §1298(f) annual reporting, which carries
de-minimis exceptions) entirely separate from FBAR/8938 (and NOT computed by
this toolkit — it's on the `/tax-compute` limitations manifest).

- If `taxpayer_profile.json flags.has_foreign_mutual_funds` is set, or any
  account in the FBAR/8938 list is a brokerage/investment account (vs a plain
  deposit account), surface a cross-note: "Foreign fund holdings in {account}
  likely trigger Form 8621 (PFIC) — punitive §1291 treatment unless a timely
  QEF / mark-to-market election was made; confirm your preparer / specialized
  software is handling each fund. FBAR and 8938 do NOT satisfy the 8621 filing."
- This is a completeness reminder only — do NOT attempt the §1291 / QEF /
  mark-to-market computation. The test is the FUND's domicile (a non-US fund),
  not the underlying holdings (a US fund of foreign stocks is not a PFIC).

### 5. Generate FBAR Preparation Document

⚠️ **SECURITY:** The generated file contains sensitive account numbers. Verify `FBAR_*.md` is in `.gitignore` (install.sh adds it by default). Warn the user: "This file contains full account numbers — securely delete it after FBAR filing is complete."

Create `FBAR_{year}_Preparation.md`:

```markdown
# FBAR (FinCEN 114) — Tax Year {year}

**Filing status:** Required / Not Required
**Filer:** {primary taxpayer name}
**Spouse filing jointly?** Yes/No

## Filing Information
- **Due date:** April 15, {year+1} (automatic extension to October 15, {year+1})
- **Filing method:** BSA E-Filing System (https://bsaefiling.fincen.treas.gov)
- **Cannot be filed with tax return** — separate electronic filing

## Account Summary

| # | Institution | Country | Account Number | Type | Max Value (USD) | Currency |
|---|------------|---------|----------------|------|-----------------|----------|
| 1 | {name}     | {country} | {number}     | {type} | ${max}        | {curr}   |
...

**Aggregate maximum value:** ${total}

## Account Details

### Account 1: {Institution Name}
- **Full institution name:** {name}
- **Institution address:** {full address}
- **Account number:** {number}
- **Account type:** {type}
- **Currency:** {currency}
- **Maximum value:** {amount in original currency} = ${USD amount}
- **Year-end value:** {amount}
- **Exchange rate used:** {rate} (IRS {year} yearly average)
- **Income earned:** ${amount} ({interest/dividends/gains})

### Account 2: ...
...

## Form 8938 (FATCA) — Separate Analysis
- **Required?** {Yes/No}
- **Year-end aggregate:** ${amount} (threshold: ${threshold})
- **Peak aggregate (realistic):** ${amount} (threshold: ${threshold})
- **Note:** {explanation of why 8938 differs from FBAR}

## Filing Checklist
- [ ] Go to https://bsaefiling.fincen.treas.gov
- [ ] Select "File FBAR (FinCEN 114)"
- [ ] Enter filer information
- [ ] Enter each account above
- [ ] Review and submit
- [ ] Save confirmation number
```

Immediately after writing the file, restrict it to the current OS identity:
```bash
sh agents/lib/run.sh -m agents.lib.secure_fs FBAR_{year}_Preparation.md
```

### 6. Update result.json

Ensure `fbar_fincen_114` and `form_8938_fatca` sections in result.json are accurate.
Immediately after writing it, run:
```bash
sh agents/lib/run.sh -m agents.lib.secure_fs result.json
```

### 7. Summary

Print:
- FBAR required: Yes/No
- Number of foreign accounts: {N}
- Aggregate maximum value: ${amount}
- Form 8938 required: Yes/No (separate from FBAR)
- FBAR preparation document saved to: FBAR_{year}_Preparation.md
- Filing deadline: {date}
