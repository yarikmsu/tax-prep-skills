# `ca_mfj_2026` — the derivation

Every figure in `golden_result.json`, with the arithmetic that produces it. This
file is the reason the golden is a **known answer** rather than a snapshot of
what some agent once produced: a reader can check it with a pencil, and
`tests/test_evals.py` re-derives it from the fixture's own inputs so it cannot be
hand-edited into drift.

The law is invented (`evals/fixture.py`), with round brackets chosen so the
arithmetic below is checkable by hand. It never changes, which is what keeps this
golden from going stale every filing season.

## The case

MFJ, both spouses, California resident, no dependents, TY2026. One employer,
three information returns, one long-term lot.

| Document | Figure |
|---|---|
| W-2, Acme Widgets LLC | box 1 wages **185,000**; box 2 federal withheld **28,000**; box 17 CA withheld **9,500** |
| 1099-INT, First Meridian Savings | box 1 interest **1,200** |
| 1099-DIV, Northgate Securities | box 1a ordinary **3,000**, box 1b qualified **2,600** |
| 1099-B, Northgate Securities | proceeds **22,000**, basis **15,000**, box D (long-term, covered) |

The W-2's own boxes are derived, not typed: social security wages are capped at
the wage base (**184,500**, so box 3 is the base and not the 185,000 of box 1),
box 4 is 184,500 × 6.2% = **11,439.00**, and box 6 is 185,000 × 1.45% =
**2,682.50**.

## Federal

| Line | Figure | Arithmetic |
|---|---|---|
| 1z wages | 185,000 | the W-2's box 1 |
| 2b taxable interest | 1,200 | |
| 3a qualified dividends | 2,600 | |
| 3b ordinary dividends | 3,000 | |
| 7 capital gain | 7,000 | 22,000 − 15,000 |
| **9 total income** | **196,200** | 185,000 + 1,200 + 3,000 + 7,000 |
| 11 AGI | 196,200 | no adjustments in this case |
| 12 standard deduction | 31,500 | MFJ, from the fixture law |
| **15 taxable income** | **164,700** | 196,200 − 31,500 |

**The Qualified Dividends and Capital Gain Tax Worksheet.** Preferential income
is 2,600 + 7,000 = **9,600**, so ordinary income is 164,700 − 9,600 = **155,100**.

- Ordinary: 12,000 × 10% = 1,200, plus (155,100 − 12,000) × 22% = 143,100 × 22%
  = 31,482 → **32,682**.
- At 0%: the MFJ breakpoint is 98,000 of TAXABLE income, and ordinary income
  (155,100) is already above it, so **none** of the preferential income falls in
  the 0% band. This is the case worth having in a golden: it is the one an agent
  gets wrong by applying the breakpoint to the preferential income instead of to
  taxable income.
- At 15%: min(164,700, 608,000) − max(155,100, 98,000) = 164,700 − 155,100 =
  9,600 → 9,600 × 15% = **1,440**. Nothing reaches 20%.
- Worksheet total **34,122**, against a straight bracket computation of
  1,200 + (164,700 − 12,000) × 22% = **34,794** — so the worksheet is the lower
  and is the one used, which is what `used_qdcg_worksheet: true` records.

| Line | Figure |
|---|---|
| 16 tax | 34,122 |
| 17 Schedule 2 Part I | 0 (no AMT — no ISO exercise, no preference item) |
| 18 tax + Schedule 2 | 34,122 = 16 + 17 |
| 24 total tax | 34,122 (no SE tax, no AMT, no additional Medicare — MFJ threshold is 250,000 and Medicare wages are 185,000) |
| 25a / 25d / 33 payments | 28,000 (the W-2's box 2; no estimates, no refundable credit) |
| 27 EIC / 28 refundable ACTC / 36 applied forward | 0 |
| 34 overpayment | 0 (payments are below the tax) |
| **37 amount owed** | **6,122** = 34,122 − 28,000 |

## California 540

CA conforms on every item in this case, so CA AGI is the federal 196,200 —
qualified dividends are ordinary income to California, which changes the RATE
they are taxed at and not the AGI.

| | Figure | Arithmetic |
|---|---|---|
| CA standard deduction | 11,000 | MFJ, fixture law |
| CA taxable income | 185,200 | 196,200 − 11,000 |
| CA tax from table | 7,912 | 20,000 × 1% = 200; 30,000 × 2% = 600; 50,000 × 4% = 2,000; 85,200 × 6% = 5,112 |
| Exemption credits | 300 | 2 personal × 150 |
| CA tax after credits | 7,612 | 7,912 − 300 |
| Mental-health surcharge | 0 | 1% over 1,000,000 |
| CA withheld | 9,500 | the W-2's box 17 |
| **CA refund** | **1,888** | 9,500 − 7,612 |

## The net

Federal owes 6,122, California refunds 1,888, so `net_refund` is **−4,234**. A
refund is positive and a balance due is negative in this schema, which is why the
federal figure is recorded as −6,122.

## Schedule D's shape, the per-person summary, and Form 2210

Three things the golden gets from the pipeline's own contract rather than from
convenience:

- **The lot lives in a detail ROW** keyed by its 1099-B box
  (`part_ii_long_term.line_8a_1099b_box_d`), with the part's net on
  `line_15_net_long_term` — the canonical shape in `result_template.json`. A
  golden in any other shape rejects a correctly assembled result, and makes the
  tie-out gate's `capital_proceeds` check unverifiable: the arithmetic chain of
  custody runs through exactly those rows.
- **`income_summary_by_person`** is written by Step 12 for every return, keyed by
  lowercase FIRST names. Omitting it from the golden would score a completed step
  as EXTRA. Every information return here names the PRIMARY as its recipient, so
  the attribution is in the documents rather than in a judgement call.
- **No `form_2210` section**, and that is a computed answer rather than an
  omission. 90% of this year's tax is 30,709.80 against 28,000 withheld, so
  without a prior year there IS an underpayment and Step 9.5 must ask or stop —
  and a case that stops to ask a question is not a known answer. The profile
  carries the prior year (AGI 190,000, tax 25,000), whose 110% safe harbour is
  27,500, under the withholding; `no_penalty` is therefore true and the section's
  own `_include_if` excludes it. `answers.md` in the fixture records the same
  facts, since that file is the headless input path.

## The zeros that are answers, and why the shape comes from the template

**The golden is the initialized `result_template.json`, not a hand-written list
of the lines this case happens to use.** `tax-setup` Step 5 copies that template
and fills it in; it removes inapplicable SECTIONS, and it neither removes
individual lines nor invents them. A golden assembled line by line therefore
fails a run that did everything right, in both directions at once — every
template line it forgot scores as EXTRA, and every line it added that the
template has no key for scores as MISSING. Both are exit 2 for an agent with
nothing to fix, which is a harness measuring itself. `tests/test_evals.py` pins
the shape against the template rather than listing the lines, and pins the
removed sections as an explicit set: a section dropped because filling it in was
awkward would have to be argued for in that tuple.

Three of the retained lines are worth naming, because they are the ones a reader
reaches for when the eval reports an EXTRA: Step 9 writes
`schedule_3_line_11_excess_ss` and `line_31_schedule3_part2`, and Step 9.5 writes
`line_38_estimated_tax_penalty` when no penalty applies. Each is **0** here, and
each is a completed step rather than a skipped one.

The identity fields come across the same way. `taxpayers.primary.dob` and
`.occupation` are in the profile, so Step 5 copies them into the result; a golden
without them scores that copy as EXTRA. They are synthetic, like everything else
in the fixture, and the comparator never prints them.

California SDI is the same shape from the other side: the W-2 carries CASDI in
box 14 (185,000 × 1.1% = **2,035.00**), the state block carries a rate and an
explicitly null wage limit (the correct TY2024+ shape — SB 951 removed the
ceiling), and the excess-SDI credit is **0** because there is one employer.
Without a rate in the cache, `compute_excess_sdi` hard-errors and Step 10.11
stops the whole computation.

## Why the CA itemised worksheet is computed and NOT written down

State withholding makes the federal Schedule A computation mandatory in Step 5,
and Step 10.4 then compares the two California elections — they are independent
of the federal one, so the comparison has to be made before either can be
chosen. Every input is zero here except the state withholding, which California
disallows outright, so the itemised total is **0** against an 11,000 standard
deduction and the standard deduction wins.

The skill writes `schedule_ca_itemized` **when itemizing**, so this return
correctly has no such section, and the golden must not carry one: requiring it
would fail an agent that followed the persistence condition, exactly as omitting
a required section would reward one that skipped a step. The comparison itself
is pinned in `tests/test_evals.py`, which also fails loudly if the case ever
drifts into itemising — at which point the section belongs in the golden.

`answers.md` supplies the inputs that computation asks for — the prior-year
state balance paid this year, and charitable gifts, both zero — because a
headless run stops on an unanswered one rather than assuming, and a cash gift
would also create a §170(p) non-itemizer deduction.

## The manifest, and the two gates the fixture has to pass

`meta.limitations` is not empty and must not be: AGENTS.md has /tax-compute copy
the capability gate's manifest into `result.json` verbatim, and for this profile
the gate emits the California state-scope note. With an empty list in the golden,
an agent doing the right thing would FAIL the comparison and one omitting the
disclosure would pass — backwards, and the false-completion shape this harness
exists to catch. `tests/test_evals.py` asserts the golden's manifest is the one
the gate actually produces, so a reworded note moves both together.

The fixture also has to get PAST both gates before any of this is scored:

- the **capability gate** blocks a profile that predates it, because absence is
  not treated as "no", so the seven screening flags are answered explicitly
  rather than left out;
- the **freshness gate** needs `state.itemized_phaseout` present and the cache
  STAMPED — an unstamped cache reads STALE however complete it is.

Either one stops /tax-compute, at which point the eval measures the gate and not
the agent.

## What this case does NOT cover

Named so that nobody reads a pass here as more than it is: no dependents or CTC,
no Schedule C or SE tax, no itemising, no AMT, no foreign accounts, no
retirement distributions, no state other than California, and no document that
arrives late or wrong. V3's entry, now in `docs/changelog/epic-V.md`, lists the
cases still to come — a
missing and a corrected document, hostile document text, a stale cache, a context
interruption, and a valid zero-income return. Each is its own golden.
