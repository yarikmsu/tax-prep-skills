# North Carolina — maintained D-400 contract

Use with `docs/contributing/state-module-playbook.md` and
`docs/contributing/tax-law-cache.md`. The reviewed form year is TY2025:
`_NC_D401_YEARS_READ` gates Schedule S and D-400 assembly. A separately
year-stamped rate calculation is not support for another year's complete form.
Implementation history is in `docs/changelog/epic-Z.md`.

## Scope and questionnaire

Full-year resident D-400, Schedule S lines 1 and 17–21, raw-input NC Schedule A,
child deduction and D-400TC Part 1/Part 3 arithmetic are computed. Schedule PN
part-year/nonresident and amended-only lines are absent. Required screening
answers and raw keyword inputs live in the ignored taxpayer profile, with the
latter at `north_carolina_inputs`; no contributor document holds taxpayer facts.

| # | Question | Current contract / authority |
|---|---|---|
| 1 | Starting point | Federal AGI, followed by Schedule S additions/subtractions. D-400 lines 6–9; D-401. |
| 2 | Modifications | NC-obligation share of exempt interest, refund in AGI, US-obligation income, taxable Social Security/railroad retirement, attested Bailey/uniformed-services retirement. D-401 Schedule S; G.S. 105-153.5. |
| 3 | Credits | Child deduction uses federal §24 qualifying children; D-400TC Part 1 credit uses verified other-jurisdiction return figures. Other deductions/credits retain gates. |
| 4 | Locals | No local income-tax computation. City/county tax is not eligible for D-400TC Part 1. D-401 p.22. |
| 5 | Surtaxes | Flat tax on positive NC taxable income, with a fetched rate/year stamp. G.S. 105-153.7. |
| 6 | Estimates | General NC-40 equal quarters with fetched current/prior shares. Farmer/fisherman and annualized worksheets remain manual. NC-40 / D-401. |
| 7 | Payroll | No new payroll tax computation; HSA/IRA/deferral conformity does not justify importing federal itemized totals. D-401 conformity rules. |
| 8 | Withholding | NC-4; actual NC withholding reaches D-400 line 20 and the ledger. |
| 9 | Extension | Automatic with federal extension and the D-400 circle; otherwise D-410. Payment is not extended. D-401 pp.7,12. |
| 10 | PTET | Taxed-PTE owner adjustments refuse. NC is not added to shared PTET pricing/windows; that would advertise an unimplemented entity computation. |
| 11 | Community property | NC is absent from the community-property set; separate MFS expense/payment rules still apply. |
| 12 | Itemized | NC Schedule A from raw expenses under the fetched conformity date, with its own caps and explicit eligibility answers. D-401 p.20. |
| 13 | Tax math | One flat-rate band, no NY recapture. Form year and rate year are separate guards. |

## Fetched-law block

`create_state_template("NC")` declares `type: graduated_income` with one
`brackets` band (`min: 0`, `max: null`) and a fetched rate. `rate_tax_year`
must be stamped with the rate update, never copied forward independently.
The cache also holds:

- `standard_deduction` for five statuses and `mfs_spouse_itemizes`;
- `child_deduction` tables keyed `mfj_qss`, `hoh`, `single_mfs`, with inclusive
  `agi_up_to` boundaries and a final open band with zero `per_child`;
- `itemized` real-estate caps, combined mortgage/property cap, medical floor
  and claim-of-right threshold/AGI rate;
- `irc_conformity_date` and `estimated_tax` threshold/shares, including the
  farmer share, all fetched from the covered year's primary authorities.

The child validator is shared by ingest, cache audit and compute: at least
two exact-key bands, positive ascending closed caps, non-increasing deductions
and an open zero top. The conformity date is a substantive rule, not a
cosmetic stamp. Do not seed a use-tax estimate or copy prior-year conformity.

## Raw inputs and computation

`compute_nc_schedule_s` and `compute_nc_itemized` take named keyword inputs;
`compute_nc_d400` passes their input dictionaries as those keywords. Misspellings
refuse rather than silently disappearing. Schedule S amounts have no zero
defaults. Exempt interest needs the NC-obligation share; government-fund
dividends need their US-obligation share. Government/military 1099-R presence
flags are required booleans. Claimed retirement exclusions require eligibility
answers and are bounded by government/uniformed-services taxable 1099-R income,
not all federal IRA/private pension income.

`compute_nc_child_deduction` uses federal AGI and the §24 qualifying-child
count, not all dependents. `compute_nc_itemized` uses raw mortgage interest,
gifts, real-estate tax before federal SALT limits, medical and repayment
inputs. Federal Schedule A totals cannot substitute. Box 5 premiums refuse;
nonzero mortgage/home-equity/charitable amounts require answers about limits
the raw figures cannot settle. True or unanswered unsupported-case answers
block. A nonzero claim-of-right repayment requires explicit eligibility:
prior-year income, no current-AGI double deduction and no federal §1341 credit.
Mixed eligible/ineligible repayments need a manual worksheet.

MFS requires `spouse_itemizes_nc`. Each item needs `paid_by: filer`; spouse
and joint-account expense allocations refuse. The household mortgage/property
cap uses actual amounts paid before the separate real-estate cap, including
the explicit spouse-paid figure for proration. Dependent filers refuse until
the separate deduction eligibility worksheet is supported.

`compute_nc_other_state_credit` normalizes US state/DC codes or full names
and supported foreign national taxes (`country:XX`). California `CA` and
Canada `country:CA` stay distinct; duplicate identities and unlisted
jurisdictions refuse. City/county/federal tax is excluded. The D-400TC ratio
rounds half-up to four decimals; credit is the lesser of its proportional
NC tax and verified net tax paid, then capped in Part 3.

`compute_nc_tax` refuses a mismatched rate stamp. D-400 assembly also refuses
an unread form year. Outputs round to cents under the engine contract;
whole-dollar form transcription can differ by less than a dollar per line.

## Result, registration and planning

`north_carolina_d400` carries literal lines 6–34, `schedule_s`, `schedule_a`
and extension/certification circles. PN/amended-only lines 13, 21c/21d, 22
and 24 stay absent. Registry computation evidence is the tuple of tax,
taxable income, line 12b and deduction; a negative taxable income counts.
Deduction alone is not guaranteed nonzero. Parsed withholding is not evidence.

Use tax is an explicit amount or `use_tax_none_due_circle: true`;
unanswered line 18 and its circle stay `null` with a loud note. A zero amount
without certification refuses. Spouse withholding line 20b is MFJ-only.
D-422 interest above overpayment carries the excess to amount due; other
penalties on an overpaid return remain due rather than reducing the refund.

Registry, parser/ledger `state_withholding_nc`, package attachments, calendar,
extension instructions and capability flags/interview all travel together.
Attachments follow D-401: NC withholding statements, used schedules,
other-state return evidence, required federal copy and retirement/service
proof. Capability depth is advertised only after those registrations exist.

NC projections skip the generic bracket path: missing Schedule S, child and
NC Schedule A inputs cannot safely become zero. A reviewed full-year
`projection_inputs.state_projection` forecast now reuses those raw form
inputs and emits NC-40 equal quarters; its contract and covered-year boundary
are in [state planning](state-planning.md#mid-year-payment-forecasts-aa43).
Completed-return true-up
excludes use tax from the estimated income-tax harbor and includes it in
the balance target. A positive prior-year prong requires a filed twelve-month
return confirmation; the no-liability exception is separately recorded and
rejects contradictory archived tax. Farmer/fisherman status is required before
quarterly true-up. NC-40 general quarters are implemented; legal-holiday
calendars are not inferred from federal holidays, so dates roll weekends only
and tell the reader to verify NCDOR dates.

## Sources, tests and limits

Reviewed structure comes from the
[TY2025 D-401](https://www.ncdor.gov/2025-d-401-individual-income-tax-instructions/open)
and [G.S. 105-153.5](https://www.ncleg.gov/enactedlegislation/statutes/html/bysection/chapter_105/gs_105-153.5.html).
Setup fetches the filing-year rate, deductions, conformity and estimates;
these references do not authorize a later year's form assembly.

Hand worksheets cover child/status boundaries, raw itemized caps and MFS
shares, Schedule S, retirement eligibility, credit ratios and settlement.
Tests also cover nonfinite/bool/domain rejection, required answers, use-tax
certification, negative/zero evidence, cache years, both ledger halves,
launcher integration and incomplete-projection refusal.

Business depreciation/recovery, NOL, Taxed-PTE and other Schedule S
deductions/credits keep named capability gates or explicit acknowledgments.
Uncomputed additions and D-422 interest/penalties remain named limitations;
use tax is asked rather than guessed. Part-year/nonresident and other form
years refuse. PDF/package output does not file a return.
