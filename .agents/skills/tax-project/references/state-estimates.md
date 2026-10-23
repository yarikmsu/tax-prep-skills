# Full-year NY/NC projected payments

Use the `state_projection` object in `projection_inputs.json`, independently of
any prior return. Initial reviewed form year: **TY2025**. Fetching a later rate
does not review that year's forms. `null` leaves NY/NC unmodeled; incomplete
evidence refuses the entire state/local balance and schedule while preserving
the federal forecast. Explain `state_unsupported_note`; never interpret null as
zero or publish a combined payment/refund figure from the federal result alone.

Read the [maintained worksheet contract](../../../../docs/contributing/state-planning.md)
and the NY/NC state contracts linked there. Interview the household and record
its answers in `answers.md`; headless runs require the same explicit answers.
Never copy a prior year's adjustment, deduction or credit without reviewing
the current forecast and current-year law. Secure every runtime write through
`secure_fs`, including `projection_inputs.json` and `answers.md`.

## Household and forecast review

Record `tax_year` at the file root and inside `state_projection`, `state: NY|NC`,
uppercase canonical `filing_status` (`SINGLE`, `MFJ`, `MFS`, `HOH`, `QSS`),
`residency: full_year`, `calendar_year_confirmed: true` and
`farmer_fisherman: false`. The profile's `tax_year` must also match; resolve
missing or wrong-year profile data before computing either portion. Record
the federal `dependents` list explicitly ([] when none are claimed), with a
reviewed covered-year boolean `ctc_eligible` for each entry. Confirm all filing
household members had that residency throughout the year, with no second-state/statutory/source obligation,
move, IT-203, Schedule PN, mixed spouses or local allocation. Such cases need
separate worksheets and remain unavailable here. Qualifying farmers/fishermen
use their special worksheet; do not answer false to clear a refusal.
The reviewed full-year declaration selects the state independently of mailing
address. A mailing address is not evidence of tax residence; retained move or
other-state obligation flags still refuse the full-year forecast.
When a federal qualifying child is present, fetch the covered-year credit
amount and filing-status phaseout before projecting; missing figures cannot
silently become a zero credit and inflate the recommended payment.

Compose the federal forecast first. Record `federal_agi_at_review` and
`self_employment_net_at_review` from its annualized amounts. The engine refuses
if they differ. Record the taxable Social Security already included in forecast
`other_income` as `taxable_social_security`; explicit zero means none. State
engines remove it. Confirm all retirement/basis, business conformity and
other-income classifications; no omitted uncomputed modification, credit,
tax, partnership/PTET or special deduction may hide behind an ordinary-income
total. Then set `scope_confirmed: true`. Rework the worksheets whenever the
forecast changes, even if its total AGI happens to remain the same.

Set `use_tax` to the reviewed full-year sales/use tax, or explicit zero for
none. This affects the balance but never the estimated income-tax requirement.
Keep all payments outside state raw inputs. Enter actual YTD withholding in
`ytd.state_withholding` and all estimated payments/eligible prior overpayments
in `estimated_payments_made.state`, explicit zeroes when none. **For NY the
withholding is State, NYC and Yonkers together** (boxes 17 and applicable 19),
matching all four taxes covered by IT-2105. Do not add the same payments again
as credits. Keep generic `state_modification_amounts` and
`state_local_applicability` absent; this path uses its dedicated worksheets.
NY requires `ny.all_withholding_confirmed: true` after verifying that the
entered amount includes every applicable State/City/Yonkers withholding item.
The optional paystub importer binds State withholding alone. A City/Yonkers
resident must resolve combined manual actuals before using this forecast:
retain the import summary, record the resolution in `answers.md`, and explicitly
set `paystub_provenance` to null per the paystub-intake contract. Do not clear
it silently or call State-only withholding the combined total.

## New York

Under `ny`, collect the current forecast's `additions`, `subtractions`,
`dependent_count`, `claimed_as_dependent` and `itemized` keyword inputs from
the maintained state-planning contract. IT-196 lines 1-45 must be worked for
this forecast's actual AGI/expenses, with completeness confirmed through
`it196_fixed_base_confirmed`; the engine reruns lines 46-49 and the election.
It does not substitute a federal Schedule A total. Pension exclusions need
eligible owners and actual taxable retirement amounts inside `other_income`.
Work the NY dependent count from IT-201 item H, which includes eligible
dependents deliberately not claimed federally; do not blindly copy the
federal claimed-dependent count.

`local` requires independent booleans `nyc_resident`, `yonkers_resident`,
`yonkers_nonresident_earnings`. A local resident needs the matching
`nyc_resident_spouses: 2` / `yonkers_resident_spouses: 2` on MFJ, otherwise
`nyc_resident_full_year: true` / `yonkers_resident_full_year: true`.
NYC also needs `charitable_gifts_trust_deducted`, zero when NY takes the
standard deduction: the line 47 addback applies only to contributions deducted
on IT-196. Yonkers needs its complete
`yonkers_worksheet_credits` and `star_reconciliation` worked at this forecast;
the worksheet's State child-care line is not the combined State/City credit.
Y-203 and IT-360.1 allocations remain unavailable.

`mctmt_by_owner` has `primary` and, on MFJ, `spouse`. Each object explicitly
records `zone_1_net_earnings` and `zone_2_net_earnings`, zero when none. Apply
the SE earnings definition and allocation worksheet; never combine spouses
before the per-person/per-zone threshold. Earnings outside the forecast's
SE income require reconciliation rather than an invented zero mobility tax.

`credit_worksheets` requires explicit completed full-year outputs for
`resident_credit`, `other_nonrefundable_credits`, `net_other_state_taxes`,
`other_nyc_taxes`, `nyc_nonrefundable_credits`, and a `refundable` dictionary.
Use explicit zeroes and `{}` only after reviewing eligibility and all credit
forms; the projection engine assembles these outputs but does not calculate
their source worksheets. Resident-credit allocations remain unavailable.
Refundable keys follow compute_ny_settlement, including school-tax credits;
the college tuition credit and deduction cannot both be claimed. Confirm every
amount belongs to the same income forecast. Nonrefundable caps, household
credits, NY recapture, NYC and Yonkers tax and MCTMT are calculated by their
existing engines. Refundable credits reduce the estimated-tax base and also
the balance, rather than being discarded by a nonrefundable cap.
Named refund/retirement/interest subtractions must fit the corresponding
federal forecast income components. Unknown modification keys refuse instead
of silently dropping a misspelled deduction. Review classifications as well
as totals; a matching AGI alone does not prove the source is included.

## North Carolina

Under `nc`, supply the complete `compute_nc_d400` income/deduction inputs:
`schedule_s`, `ctc_qualifying_children`, `itemized` (raw NC Schedule A, or
reviewed `null`), `spouse_itemizes_nc`, `claimed_as_dependent` and
`eligible_for_federal_standard_deduction`. Schedule S's required amounts and
retirement presence/eligibility answers are explicit, and its taxable Social
Security must match the common forecast amount. The engine reruns child
deduction bands, medical floors and the spouse election. Dependent deduction,
uncomputed provisions and D-400TC allocations retain their existing refusals.
Use-tax/payments/donations/penalties do not belong in this `nc` object.
US-obligation interest and government-fund dividends must be included in the
federal interest/dividend forecasts. Taxable refunds, SS and retirement source
amounts must fit the federal `other_income` forecast; do not deduct an amount
the federal forecast omitted.

## Prior-year prong and schedule

`prior_year.tax_year` must be exactly the preceding year. Choose `method`:

- `filed_return`: enter the completed pre-factor estimated-tax `tax_base`,
  and set `filed_12_month_return_confirmed: true` only after verifying both.
  NY also requires `nyagi` and `mctd_net_earnings` from that same prior return
  (the earnings measure used by that return's IT-2105.9 prior-tax worksheet),
  including explicit zero earnings. The higher fetched share applies if either
  applicable measure exceeds its fetched threshold.
- `no_liability`: enter `tax_base: 0` and
  `no_liability_exception_confirmed: true` only after checking the state's
  exception. NY requires a twelve-month preceding year and NY residency or
  qualifying NY source-income coverage throughout it; a nonresident with no
  NY source income does not qualify. NC's no-prior-liability exception is
  separate from its filed-twelve-month-return prong.
- `ineligible`: use only after confirming the ordinary prior-year prong and
  no-liability exception are both unavailable. Leave `tax_base` absent/null.
  Missing/unknown prior evidence is a refusal, not this answer.

NY's base follows the covered-year IT-2105.9 prior-tax worksheet: exclude
sales/use tax and voluntary contributions, subtract the named refundable
credits and STAR payment, and resolve filing-status changes. Never enter gross
IT-201 line 60 from an archive. The annual safe harbor combines all four taxes,
refundable credits and withholding before comparing current and prior shares.
The separate IT-2105 voucher columns have different minima and thresholds;
their sum cannot replace this combined underpayment requirement. The NY floor
uses current combined tax base less withholding, multiplied by the number of
applicable State/City/Yonkers taxes (MCTMT contributes zero to that count).
Collect ny.star_credit_payment as a reviewed full-year amount, explicit zero
for none. It reduces the underpayment base under line 7a but does not create a
second refund on the return. NC uses income tax after credits, excluding use
tax. Fetch the current-year safe-harbor shares and thresholds under the state
cache schema; do not reuse federal constants or a prior year's cache.

Surface `state_payment_schedule` in JSON and markdown: tested shares, required
annual payment, withholding/estimates, shortfall and vouchers. The schedule
uses regular equal quarters, including September. After an installment closes,
the annual remainder split does not cure its underpayment; reconcile actual
payment dates in IT-2105.9/D-422 and pay catch-up promptly. The date and timing
notes are mandatory. Dates currently roll weekends only; confirm department
legal holidays and disaster relief. An annualization alternative or late first
requirement needs its own worksheet. A below-threshold or no-liability result
zeros the estimate target, while the filing balance can still be payable.
