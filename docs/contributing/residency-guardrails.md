# Residency guardrails

Why this toolkit asks a taxpayer to ASSERT where they were resident, instead of
reading it off `address.state`, and where the figures the skills quote come from.

`AGENTS.md` §1 and §2 carry the rules; this file carries the mechanism.

## The problem: one address, two resident states

Every computation here assumes ONE resident state, taken from `address.state`.
That is a mailing address. Residency is a different question, and a taxpayer can
be a full-year resident of two states at once — each taxing all of their income,
with a credit mechanism between them that this toolkit does not model.

The path that gets there is not exotic. A taxpayer moves to Florida, records a
Declaration of Domicile, changes their licence and their voter registration, and
keeps the Manhattan apartment. Whether that changed their DOMICILE is a separate
and contested question — New York says those acts alone do not settle it — but
even if it did, New York has a second, purely mechanical test that does not care
what they intended.

Nothing in the profile recorded the two facts that test turns on — how many days
they were in the state, and how long they kept a home there — so nothing could
ask. The return computed a Florida regime and was silently wrong by the whole New
York resident tax.

## The two questions, and why they are CONTINUOUS

`taxpayer_profile.json` carries a `residency` block:

- `domicile_state` — the one place they intend as their permanent home. There is
  exactly one, and it need not be `address.state`.
- `days_present_by_state` — a count per state, for the tax year.
- `abode_months_by_state` — how many months of the tax year they maintained a
  home there that was available to them year-round.

Counts, not flags, and deliberately so. This repository's standing rule is that
any statutory test with a discontinuity tempts a boolean, and the boolean always
over-reaches in one direction: "did you spend a lot of time in New York?" has no
honest answer at 180 days. The numbers are what the taxpayer can actually answer,
and the threshold is applied where it is written down rather than in their head.

Keys are STATE codes. New York City and Yonkers apply the state's own test at
their own level, and the toolkit tracks those through `flags.nyc_resident` rather
than through a second set of count keys — a gap worth naming, not hiding:
a taxpayer can meet the state test and not the city one, since city days are a
subset of state days. The reverse cannot happen.

`flags.statutory_resident_of_another_state` is what the capability gate reads,
and it is the taxpayer's own conclusion from those numbers — recorded in
`answers.md` beside the counts that produced it, so a reviewer can re-derive it.

## The figures, and the rule that keeps them honest

AGENTS.md §2 forbids a hardcoded rate, bracket, cap or threshold, and these are
thresholds. They are stated in PROSE and computed with by nothing, like
`tax-safe-harbor`'s §6654 restatement — but one of them is NOT statutory and has
already moved once, so the closed-set discipline here is stricter than a note
saying "statutory, unindexed". Every figure any skill states about residency is
in the table below, keyed BY STATE, and in `RESIDENCY_FIGURES` in
`tests/test_capabilities.py`. Adding a state means adding rows, not widening a
flat set — the first version of this registry was flat, which had the effect of
freezing New York's numbers as though they were the general rule.

| State | Figure | Test | Authority | Read |
|---|---|---|---|---|
| NY | more than 183 days — i.e. **184 or more** — in the state | statutory residency, day prong | [N.Y. Tax Law §605(b)(1)(B)](https://www.nysenate.gov/legislation/laws/TAX/605), [tax.ny.gov definitions](https://www.tax.ny.gov/pit/file/pit_definitions.htm) | 2026-09-10 |
| NY | any part of a calendar day is a day; presence at the abode is not required | day count | [20 NYCRR 105.20](https://www.law.cornell.edu/regulations/new-york/20-NYCRR-105.20), [tax.ny.gov nonresident FAQs](https://www.tax.ny.gov/pit/file/nonresident-faqs.htm) | 2026-09-10 |
| NY | a permanent place of abode maintained for "substantially all of the taxable year" | statutory residency, abode prong | [20 NYCRR 105.20(a)(2)](https://www.law.cornell.edu/regulations/new-york/20-NYCRR-105.20) | 2026-09-10 |
| NY | "substantially all" = **more than 10 months** from **tax year 2022**; more than 11 months before it | Audit Division POLICY, not statute | [Nonresident Audit Guidelines, Dec 2021, pp. 49 and 51](https://www.tax.ny.gov/pdf/2021/misc/nonresident-audit-guidelines-2021.pdf) | 2026-09-10 |
| NY | the City and Yonkers tests are the State's, applied at that level | NYC / Yonkers residency | [tax.ny.gov definitions](https://www.tax.ny.gov/pit/file/pit_definitions.htm) | 2026-09-10 |
| FL | homestead exemption application by **March 1** | DR-501 deadline | [F.S. 196.011](https://www.flsenate.gov/Laws/Statutes/2024/196.011) | 2026-09-10 |

### The ten-versus-eleven month trap

**This is the one figure in the table that is not law, and it is the one that
moved.** The Department's published bulletin still says "more than eleven
months". Its own Nonresident Audit Guidelines, December 2021, say at pages 49 and
51:

> Prior to tax year 2022, Audit Division policy defined "substantially all of the
> year" to mean a period exceeding 11 months. Beginning with tax year 2022, Audit
> Division policy will define "substantially all of the year" to generally mean a
> period exceeding 10 months.

So for every year this toolkit files, **10 is the operative figure** and the
website is stale. The skills state ten, and say why, because an auditor applies
audit policy.

The direction is what makes this worth a section of its own. Eleven months tells
a taxpayer with a 10½-month New York abode and 190 New York days that New York
does not have them. Under current audit policy New York does — they skip the
resident return and meet an assessment. Worse, the flag stays False, so the
capability gate never fires and `/tax-compute` produces exactly the silently
wrong return the gate exists to prevent, in the whole band between ten and
eleven months.

This is also why the exception AGENTS.md §2 grants here is narrower than the
safe-harbor one: §6654's figures are in the Code, and this one is in an audit
manual that has already been revised. The Read column is what says whether it has
been checked this season, and it is checked WITH the §2 contract, not instead.

### What does NOT count as a day, and what is NOT an abode

Omitting these is not a conservative simplification — each one only ever moves
the count DOWN, so leaving them out makes an honest taxpayer answer "yes" to a
test they do not meet, and the capability gate then BLOCKS a correct return. A
gate that fires on correct data teaches the taxpayer to reason past the next one.

- **Travel through the state.** Presence solely to board a plane, ship, train or
  bus bound outside New York, or while travelling through to a destination
  outside it, is disregarded — 20 NYCRR 105.20(c).
- **Confinement to a medical institution** in New York, for any reason, serious
  or otherwise, is not a day spent there — Audit policy following *Stranahan v
  State Tax Commission*, 68 AD2d 250. OUTPATIENT care is not covered.
- **Active service in the armed forces** is excluded from the statutory-residency
  prong by §605(b)(1)(B) itself.
- **An undergraduate's term-time residence** is not a permanent place of abode
  for that student, and neither is a corporate apartment shared first-come or
  where clients have priority — [permanent place of abode bulletin](https://www.tax.ny.gov/pubs_and_bulls/tg_bulletins/pit/permanent_place_of_abode.htm).
- An abode has to be **suitable for year-round use**, owned or not. Seasonal use
  by itself does not disqualify a place that is; a camp without cooking or
  bathing facilities is not one at all.

A year in which the abode is first acquired or finally given up is NOT an
exception to the month rule — the rule is applied to it, and usually the taxpayer
falls short of the period and is not a statutory resident. Reading it as a waiver
leaves the day count deciding on its own, which over-blocks.

## Other states do not share New York's test

The numbers above are **New York's**, and transplanting them is wrong in both
directions. The skills say so explicitly rather than leaving an agent to
generalise:

- **Massachusetts** has the day prong and NO abode-duration prong at all — a
  six-month rental plus 184 days is enough. New York's month rule would clear a
  Massachusetts statutory resident. That is the dangerous direction.
- **Connecticut** requires the abode for the ENTIRE taxable year, which is
  stricter than New York, so New York's rule over-detects there.
- **California — this toolkit's primary state — has no mechanical test at all.**
  Residency is a closest-connection question; the day count is evidence, not a
  threshold. A California taxpayer told "the test is 184 days plus ten months"
  has been told something false about their own state.

So the questions collect COUNTS, which are state-neutral, and only New York's
conclusion is stated. Any other state is a hand-off, not a computation.

## What the gate does with it

`capabilities.py` carries `statutory_residency_second_state`, keyed on
`flags.statutory_resident_of_another_state`, UNSUPPORTED. It BLOCKS
`/tax-compute` until the taxpayer acknowledges it in `acknowledged_limitations`,
and the acknowledgement is recorded in `answers.md` with the counts behind it.

**The other state has to actually tax income.** A snowbird with a Florida condo
kept year-round and 190 Florida days meets a New-York-shaped test on its face and
owes Florida nothing; blocking there is pure cry-wolf. The interview screens
against the same no-income-tax allowlist the nonresident-source-income question
uses, `capabilities._NO_INCOME_TAX_STATES`.

**BOTH prongs are required.** Days alone, with hotels and no abode, is not
statutory residency; an abode alone without the days is not either.

It is DISTINCT from the three rules next to it, and all four can fire at once:

- `part_year_residency` fires on a mid-year MOVE. A taxpayer who never moved and
  simply kept two homes is not caught by it.
- `nonresident_source_income` fires on income SOURCED elsewhere. Statutory
  residency taxes ALL income, wherever it arose, so the source test misses it
  entirely for a taxpayer whose income is all from their resident state.
- `nyc_resident` fires on the CITY. Its test is the State's applied to the city,
  and Yonkers has the same parity with no flag of its own — an open gap.

**What it does NOT catch** is residency by DOMICILE. A mover whose change of
domicile fails New York's clear-and-convincing burden is a New York resident with
no day or abode count involved, and `moved_states_this_year` only covers the move
year, so a contested change from an earlier year passes clean. The mover
checklist is a review FINDING, not a block, and that asymmetry is deliberate:
domicile is a judgement call, and a gate that blocks on one would fire on every
move ever made.

## The mover checklist

`/tax-review` surfaces it on its MAIN path, not only for a no-income-tax
destination. The first version lived inside the `type: "none"` branch, so a
taxpayer leaving New York for any income-tax state reached none of it. Residency
is a question about the state being LEFT, and the destination's regime has
nothing to do with whether it is asked.

What it covers, and why each item is there rather than as general advice:

- **The two counts above**, asked for the state being left, because that is the
  test that survives a change of domicile.
- **Evidence of intent** — licence, voter registration, vehicle registration,
  where the "near and dear" items went, business ties, family location. New York
  says the paperwork alone does not establish abandonment of domicile, so the
  checklist collects the pattern rather than treating any one item as decisive.
- **Florida's Declaration of Domicile**, F.S. 222.17, recorded with the clerk of
  the circuit court. It is permissive evidence of a domicile already
  established — the statute says a person who HAS established a domicile MAY
  file it — not a step that creates one.
- **The homestead order of operations.** [F.S. 196.031(6)](https://www.flsenate.gov/Laws/Statutes/2024/196.031)
  bars the Florida homestead exemption outright to anyone "receiving or claiming
  the benefit of an ad valorem tax exemption or a tax credit in another state
  where permanent residency is required", so New York's STAR benefit has to be
  given up BEFORE filing DR-501 by March 1 — not merely at some point. Claiming
  both is also disqualifying for STAR and carries its own penalty; the figure is
  not quoted here or in the skills, because it is a dollar amount that changes
  and §2's rule is that one never passes as fresh. It is precisely what an audit
  looks for: two signed, contradictory claims of residence.
- **Contemporaneous day records** for the move year. The day count is the
  taxpayer's burden to prove, and a reconstruction made two years later during an
  audit is the thing the guidelines are written to disbelieve.

## Telecommuting

**The general rule is that wages are sourced where the work is physically
performed.** A handful of states apply a convenience-of-the-employer rule
instead: Connecticut, Delaware, Nebraska, New York and Pennsylvania, plus New
Jersey's later reciprocal version. Everywhere else, a day worked from home is a
day worked at home.

New York's rule has a precondition the skills must state: it applies to a
nonresident **whose primary office is in New York State**, and then a
telecommuting day counts as a New York work day unless the employer has
established a bona fide employer office at the remote location — itself a
multi-factor test, not a plain-language one.

It is an ALLOCATION question, not a residency one, and the two are easy to
conflate: a taxpayer can fail the convenience rule and owe New York on their
wages while being a resident of nowhere near New York. Stating it as a general
rule rather than a minority exception is the expensive mistake — it would feed
`nonresident_source_income`, which BLOCKS, for the ordinary remote worker whose
employer simply happens to be elsewhere.
