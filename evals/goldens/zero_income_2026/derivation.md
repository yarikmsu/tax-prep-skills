# `zero_income_2026` — the derivation

Every figure in `golden_result.json`, with the arithmetic that produces it, on
the same terms as `ca_mfj_2026/derivation.md`: a reader can check it with a
pencil, and `tests/test_evals.py` re-derives it from the fixture's own inputs so
it cannot be hand-edited into drift.

The law is the same invented law (`evals/fixture.py`). It never changes, which is
what keeps this golden from going stale every filing season.

## The case

The `ca_mfj_2026` couple in a year with no income at all. Same names, same
address, same filing status, same state. `documents/` is EMPTY, and `answers.md`
says so affirmatively — that claim is the case.

**Why this is a case and not an edge.** `return_builder` refuses to treat an
ABSENT figure as zero, which is the right rule and the one AGENTS.md §4 states.
This is the same rule read backwards, and it fails quietly: an agent that reads a
legitimately zero return as an incomplete one refuses to file, or reports a gap
that is not there. Nothing downstream catches that, because there is no wrong
number to catch — there is a return that never got produced.

## Form 1040

Every income line is zero, so:

| line | figure | why |
|---|---|---|
| 1a wages … 8 Schedule 1 | **0** | no documents, and `answers.md` says there are none to have |
| 9 total income | **0** | the sum of the above |
| 10 adjustments | **0** | none claimed |
| 11 AGI | **0** | 9 − 10 |
| 12 standard deduction | **31,500** | MFJ, from the invented law's `filing_statuses.mfj.standard_deduction` |
| 13a-13c | **0** | no QBI, no Schedule 1-A, no non-itemizer charitable deduction |
| 14 total deductions | **31,500** | 12 + 13 |
| 15 taxable income | **0** | `max(0, 0 − 31,500)`. **Not −31,500** — the floor is the figure this line is about |
| 16 tax | **0** | the bracket table applied to zero |
| 17-23 | **0** | no Schedule 2, no credits, no other taxes |
| 24 total tax | **0** | |
| 25a-25d withholding | **0** | there is no W-2 |
| 26-31 payments | **0** | no estimates, no refundable credits |
| 33 total payments | **0** | |
| 34 overpayment / 35a refund | **0** | 33 − 24 = 0 |
| 37 amount owed | **0** | |
| 38 estimated-tax penalty | **0** | nothing was owed, so §6654 cannot apply |

`digital_asset_question` is **"No"**: it is a question the form asks whatever the
income is, and leaving it null would be an unanswered 1040 rather than a zero one.

## Federal tax computation

All zero. `tax_on_ordinary` is `{}` and `qdcg_detail` is `null` — and an earlier
draft of this file said those were consequences of having no income, which they
are not: `ca_mfj_2026`'s golden carries exactly the same two values on a return
with $155,100 of ordinary income and $9,600 of preferential. Both goldens leave
them at the template's defaults, because nothing in the pipeline populates
either. Stated here so the next reader does not derive a rule from them.

## California Form 540

| figure | value | why |
|---|---|---|
| `ca_standard_deduction` | **11,000** | MFJ, from the invented law's `state.standard_deduction` |
| `federal_agi` / `ca_agi` | **0** | no Schedule CA additions or subtractions to make |
| `ca_taxable_income` | **0** | `max(0, 0 − 11,000)`. Protected by accident today — `compute_bracket_tax` REFUSES a negative taxable income — rather than by a floor anyone wrote |
| `ca_tax_from_table` | **0** | |
| `ca_exemption_credits.personal` | count **2**, per person **150**, total **300** | two filers. The credit exists whether or not there is tax to apply it to — it is a property of the return, not of the income |
| `ca_tax_after_credits` | **0** | `max(0, 0 − 300)`. **Not −300**: the exemption credit is non-refundable, so it cannot make a refund. This floor had no guard and appeared in no instruction — `state-return.md` step 8 said only "CA tax after credits" — so an agent following the docs wrote −300 and `ca_refund` became **+300**, an invented refund. The rule is written there now |
| `ca_mental_health_surcharge` | **0** | the §17043 surcharge starts at $1,000,000 |
| `ca_withholding_box17` / `ca_sdi_box14` | **0** | there is no W-2 |
| `ca_refund` | **0** | |

## The sections this case does not have

`interest_detail` and `dividend_detail` are kept only when their figure is above
zero, and `schedule_d` is keyed on the `has_capital_gains` flag, which this
case's profile sets false. Everything the base case drops, this case drops too —
with one exception noted below.

**`w2_summary` is DROPPED, and an earlier draft of this file kept it on a reason
that does not exist.** It said the template marks the section `(always)`. It does
not: `w2_summary` carries no `_include_if` at all and sits below
`_optional_sections_below`, whose text is "Include only if applicable to this
taxpayer. Remove sections that don't apply." A W-2 summary is not applicable to a
return with no W-2, so a compliant agent prunes it — and a golden that kept it
would have scored that agent's seventeen `MISSING` fields as a failure. The
`(always)` was a label printed by the script used to survey the template, read
back as though it were in the file.

**The exception the base case also makes:** `california_540.schedule_p` carries
an `_include_if` of `has_iso_exercise (CA AMT)`, and neither profile sets that
flag, yet both goldens keep the block at zeros. That is inherited from the first
golden rather than decided here; it is named so the next reader does not take
"everything the base case drops" as exact.

## The one figure that is not zero and not structural

`meta.limitations` carries the same one line the base case does — the
capability gate's state-scope note about California being computed in depth. It
is copied verbatim from the gate's manifest, not written here, for the reason
AGENTS.md §4 gives: the manifest is what reaches page 1 of the generated PDF.
