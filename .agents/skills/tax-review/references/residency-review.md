# Residency and multistate guardrails (review)

Read from `SKILL.md` step 3.8, for **every** return. Residency is a question
about the state being LEFT, so the destination's tax regime has nothing to do
with whether it gets asked — the first version of this checklist lived inside the
no-income-tax branch, so a taxpayer leaving New York for any income-tax state
reached none of it.

Return to `SKILL.md` step 4 when done. This file ends at the last section; an
agent walking it in order otherwise hits EOF with no instruction and writes no
review report at all.

---

## 1. Was residency ASSERTED, or read off the address?

`address.state` is a mailing address. `residency.domicile_state` is the place the
taxpayer intends as their permanent home, and there is exactly one — it need not
be the same state. Findings:

- `residency` absent or empty, on a return where anything below could apply, is a
  finding: the questions were never asked, which is NOT a negative answer.
- `residency.domicile_state` differing from `address.state` with nothing in
  `answers.md` explaining it is a finding. That difference is normal and often
  correct; unexplained, it is the shape of a half-finished move.

## 2. New York's mechanical test — and it is NEW YORK's

**Do not transplant these numbers to another state.** Every state writes its own
test and they are not the same shape:

- **Massachusetts** has the day prong and NO abode-duration prong — a six-month
  rental plus the days is enough, so New York's month rule would clear a
  Massachusetts statutory resident. That is the dangerous direction.
- **Connecticut** requires the abode for the ENTIRE taxable year, stricter than
  New York, so New York's rule over-detects there.
- **California — this toolkit's primary state — has no mechanical test at all.**
  Residency is a closest-connection question and the day count is evidence, not
  a threshold.

For New York, and only New York, the engine applies the test itself from the
recorded counts (`agents/lib/capabilities.py`). **BOTH prongs are required:**

- **184 days or more** physically in the state during the tax year. **Any part of
  a calendar day counts**, and the taxpayer need not have been at the home for
  that day to count.
- **AND** a permanent place of abode maintained for "substantially all of the
  taxable year": **more than 10 months from tax year 2022**, more than 11 months
  before it. The Department's published bulletin still says eleven; its own
  Nonresident Audit Guidelines moved the Audit Division's policy to ten, and
  audit policy is what an auditor applies. A year in which the abode is first
  acquired or finally given up is not an exception — the rule is applied to it,
  and the taxpayer usually falls short.

Days in hotels with no abode is not statutory residency. An abode with no days
is not either.

### What does NOT count, and what is NOT an abode

Each of these only ever moves the count DOWN, so leaving one out makes an honest
taxpayer answer "yes" to a test they do not meet — and the capability gate then
blocks a correct return.

- **Travel through the state** — presence solely to board a plane, ship, train or
  bus bound outside New York, or while travelling through to a destination
  outside it, is disregarded.
- **Confinement to a medical institution** in New York, for any reason, is not a
  day spent there. Outpatient care is not covered.
- **Active service in the armed forces** is excluded from the test by statute.
- **An undergraduate's term-time residence** is not a permanent place of abode
  for that student, and neither is a corporate apartment shared first-come or
  where clients have priority.
- An abode must be **suitable for year-round use**, owned or not. Seasonal use by
  itself does not disqualify a place that is; somewhere without cooking or
  bathing facilities is not an abode at all.

**The other state must actually tax income.** A snowbird with a Florida condo
kept year-round and 190 Florida days meets a New-York-shaped test on its face and
owes Florida nothing. Screen against `capabilities._NO_INCOME_TAX_STATES` before
treating a count as exposure.

### The cross-check

Compare `residency.days_present_by_state` and `residency.abode_months_by_state`
against `flags.statutory_resident_of_another_state`. Both directions are
findings:

- Counts that MEET a state's test with the flag False — the return computes one
  resident state and is silently wrong by the whole of the other state's resident
  tax. This is the dangerous direction. For New York the capability gate catches
  it deterministically; for any other state this review is the only check.
- The flag True with no counts recorded, or counts that plainly meet no test — a
  block the taxpayer had to acknowledge for no stated reason. A gate that fires
  on correct data teaches the taxpayer to reason past the next one.

**New York City and Yonkers** apply the State's test at their own level, so city
days are a subset of state days: a taxpayer can meet the State test and not the
City one, never the reverse. `flags.nyc_resident` carries the city answer.
Yonkers has the same parity in New York's own definitions and has no flag here —
record it as a limitation in `answers.md` rather than assuming it away.

**Domicile is not covered by any gate.** A mover whose change of domicile fails
New York's clear-and-convincing burden is a New York resident with no day or
abode count involved, and `moved_states_this_year` only covers the move year. A
contested change from an earlier year passes clean, which is why section 3 is a
review finding rather than a block: domicile is a judgement, and a gate on one
would fire on every move ever made.

The figures above are a closed set, sourced and dated in
`docs/contributing/residency-guardrails.md`. A residency figure stated anywhere
in these skills that is not in that table is itself a finding, and a test
enforces it.

## 3. Mover checklist — the state being LEFT

Surface this whenever `flags.moved_states_this_year` is set, or the prior year
shows a different state, or `residency.domicile_state` differs from
`address.state`. It weighs evidence of intent, which is what a domicile audit
weighs — and New York says the paperwork alone does not establish that a domicile
was abandoned, so collect the pattern rather than treating any one item as
decisive:

- Driver licence, voter registration and vehicle registration in the new state.
- Where the "near and dear" items went — the things a person keeps because they
  matter, not because they are valuable.
- Business ties, time patterns, and the family's location.
- **Contemporaneous day records for the move year.** The day count is the
  taxpayer's burden to prove, and a reconstruction made two years later during an
  audit is exactly what the audit guidelines are written to disbelieve.

**Moving TO Florida** adds two steps that have to happen in this ORDER:

1. **Give up the other state's residency-based benefit FIRST.** F.S. 196.031(6):
   a person "receiving or claiming the benefit of an ad valorem tax exemption or
   a tax credit in another state where permanent residency is required … is not
   entitled to the homestead exemption provided by this section." A mover still
   holding New York's STAR benefit is therefore not entitled to the Florida
   exemption at all. Claiming both is also disqualifying for STAR and carries
   its own penalty — check the current figure on tax.ny.gov rather than quoting
   one here. Two signed, contradictory claims of residence are precisely what an
   audit looks for.
2. **Then file DR-501 by March 1**, and record a **Declaration of Domicile** with
   the clerk of the circuit court. The Declaration is permissive EVIDENCE of a
   domicile already established, not a step that creates one.

## 4. Telecommuting is an ALLOCATION question, not a residency one

**Wages are sourced where the work is physically performed.** That is the rule.
A handful of states apply a convenience-of-the-employer rule instead —
Connecticut, Delaware, Nebraska, New York and Pennsylvania, plus New Jersey's
later reciprocal version — and everywhere else a day worked from home is a day
worked at home.

New York's version has a precondition: it applies to a nonresident **whose
primary office is in New York State**, and then a telecommuting day counts as a
New York work day unless the employer has established a bona fide employer office
at the remote location, which is a multi-factor test rather than a plain-language
one.

Check `residency.telecommutes_for_out_of_state_employer` against
`flags.has_nonresident_source_income`. A remote worker whose employer is in one
of those states AND whose primary office is there, with the source-income flag
False, is a finding. A remote worker for an employer anywhere else is NOT — that
is the over-block this section exists to prevent, and it would land on a large
share of the user base. It is also a DIFFERENT question from section 2: a
taxpayer can owe New York on their wages under the convenience rule while living
and being resident nowhere near New York.

---

**Return to `SKILL.md` step 4 (Generate Review Report).**
