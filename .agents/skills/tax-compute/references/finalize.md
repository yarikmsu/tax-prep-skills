# tax-compute Steps 12-13 — write result files and summarize

The last two steps. **Step 12.7 (the parse-to-return tie-out HARD GATE)
runs between them and is written out in full in `../SKILL.md` under
"Gates that run regardless" — run it there before Step 13.**

## Contents

- [Step 12: Update Result Files](#step-12-update-result-files)
- [Step 12.7: Parse-to-return tie-out (HARD GATE)](#step-127-parse-to-return-tie-out-hard-gate)
- [Step 13: Summary](#step-13-summary)

---

### Step 12: Update Result Files

Update `result.json` with ALL computed values:
- form_1040 section (all lines)
- All supplemental forms
- federal_tax_computation detail
- State return
- balance_due_summary
- income_summary_by_person
- **`meta.limitations` — the C2 manifest.** Run
  `sh agents/lib/run.sh agents/lib/capabilities.py --workspace . --json`
  and copy its `limitations` array verbatim into `result.json`
  `meta.limitations`. It is
  never empty (it always carries at least the state-scope note); `/tax-generate`
  prints it on page 1 so the taxpayer sees what this return does NOT cover.

Update `answers.md` with computed results section.

Both files now hold the taxpayer's computed figures. Restrict them immediately
— `secure_fs`, not a bare `chmod 600`: on Windows under a POSIX-compatible
shell, `chmod` only flips the read-only DOS attribute and leaves the file
readable by every other local account.

```bash
sh agents/lib/run.sh -m agents.lib.secure_fs result.json answers.md || exit 1
```

### Step 12.7: Parse-to-return tie-out (HARD GATE)

Run it now, from `../SKILL.md` → "Gates that run regardless". It is written out
there rather than here because a gate the agent can decline to load is a gate
that silently over-states completeness. Do not proceed to Step 13 until it
exits 0.

### Step 13: Summary

Print:
- Filing status and tax year
- Total income, AGI, taxable income
- Federal tax, credits, total tax
- Federal refund or amount owed
- State refund or amount owed
- **TOTAL refund or amount owed**
- Forms that apply to this return
- Suggested next step: `/tax-generate`
