# tax-setup Step 2 — the taxpayer interview

The question groups for `### 2. Interview the Taxpayer`. Read this when you
reach Step 2. The step's framing and the **non-interactive rule** (read answers
from `answers.md`, hard-stop on a missing REQUIRED answer, never guess) are in
`../SKILL.md` and bind everything below.

Every capability-gate flag in `agents/lib/capabilities.py` must be asked here —
a rule keyed on a flag nothing sets is dead code, and a test enforces it.

## Contents

- Group 1 — Filing basics
- Group 1.5 — Life events THIS tax year (H1)
- Group 2 — Address & state
- Group 3 — Dependents
- Group 4 — Tax situations (yes/no flags)
- Group 5 — Foreign currencies (if applicable)
- Group 6 — Income estimates
- Group 7 — Retirement & savings detail
- Group 8 — Deductions & credits probe
- Group 9 — Prior year carryforwards
- Group 10 — Estimated taxes — **not in this file.** It runs back in
  `../SKILL.md` after Step 9.5, because `/tax-rollover` pre-fills the
  prior-year overpayment it asks about. See the end of this file.

---

**Group 1 — Filing basics:**
- Filing status: Single, Married Filing Jointly (MFJ), Married Filing Separately (MFS), Head of Household (HoH), Qualifying Surviving Spouse (QSS)
- Primary taxpayer: full legal name, SSN (last 4 for display), date of birth, occupation
- If MFJ/MFS: Spouse name, SSN (last 4), DOB, occupation
- **If HoH selected — run the qualifying tests (H2) and record each answer in
  `answers.md` as the substantiation trail** (HoH is among the most-audited
  statuses; the IRS asks exactly these):
  1. **Qualifying person** (Pub 501 Table 4) — one of:
     - a qualifying CHILD who lived with you MORE than half the year (a
       MARRIED child counts only if you can claim them as a dependent —
       generally not if they file a joint return, unless filed solely to
       claim a refund);
     - a dependent PARENT (a parent need NOT live with you if you paid more
       than half the cost of keeping up the parent's main home for the
       whole year);
     - another QUALIFYING RELATIVE you can claim as a dependent who is
       related to you under the §152(d)(2)(A)-(G) list (grandparent,
       sibling, grandchild, aunt/uncle, in-law, step-parent — NOT the
       unrelated member-of-household catch-all) AND lived with you more
       than half the year.
     Name the person and their relationship.
  2. **Cost of keeping up the home:** did you pay MORE than half — rent or
     mortgage interest + property taxes, utilities, repairs, home insurance,
     and food eaten in the home (NOT clothing, education, medical, or the
     rental value of a home you own)?
  3. **If still legally married:** the "considered unmarried" test (§7703(b))
     — you file separately from your spouse, you paid >1/2 the cost of the
     home, the home was the main home of YOUR CHILD whom you can claim as a
     dependent (or could, but for a Form 8332 release to the noncustodial
     parent — HoH still stands), and your spouse did NOT live in the home
     during the LAST 6 months of the year. **Temporary absences do not
     count as not-living-there:** a spouse away for illness, education,
     business, vacation, or MILITARY DEPLOYMENT is considered to live in
     the home — a deployed spouse does NOT make you "considered unmarried".
     ALTERNATIVE: a spouse who was a NONRESIDENT ALIEN at any time in the
     year (no §6013(g) election) also makes you unmarried for HoH.
  If ANY test fails, HoH is not allowed — re-ask filing status (usually
  Single, or MFS if married). Append to `answers.md`:
  ```markdown
  ## HoH qualifying tests — TY{year}
  - Qualifying person: {name}, {relationship}, {months lived with taxpayer}
  - Paid >1/2 cost of keeping up home: {yes + basis}
  - Considered-unmarried (if married): {N/A | spouse absent since {month}}
  ```

**Group 1.5 — Life events THIS tax year (H1):**
For a North Carolina resident, also ask every NC screening question below and
record explicit true/false answers; legacy missing answers block computation.
Read the covered year's D-401 Schedule S and D-400TC with the taxpayer.

- Bonus depreciation or section 179 adjustments? → `nc_has_business_adjustments`.
- Recovery deductions from prior-year NC add-backs (B23/B24/B34-B37)? → `nc_has_recovery_deductions`.
- A federal NOL add-back or NC NOL deduction? → `nc_has_nol`.
- Income or loss as an owner of an NC Taxed PTE? → `nc_has_taxed_pte`.
- Any other Schedule S deduction outside supported B17-B21, including B22/B25-B33? → `nc_has_other_schedule_s_deductions`.
- Historic rehabilitation, carried-over or NC-478 business credits? → `nc_has_other_credits`.

Each yes names an unsupported worksheet at the capability gate. Other Schedule S
ADDITIONS are reviewed manually and disclosed on `meta.limitations`; do not
omit them silently. Ask about a government/military 1099-R, Bailey and
uniformed-services eligibility; retain the taxable amount and the asserted
deduction separately for compute. Ask whether the filer can be claimed as a
dependent, and for MFS whether the spouse itemizes NC, who paid each expense,
and the spouse's mortgage/property deduction. Ask about raw NC Schedule A
expenses even when federal standard deduction was elected. Record these
answers in `answers.md`; a missing answer is never a zero.

Most yes answers set a profile flag the `/tax-compute` capability gate (C1)
turns into a limitations-manifest line — ask ALL of these; a life event the
interview never captures is invisible to every downstream check.
- Did you get married this year? → sets `married_this_year` (Dec-31 rule:
  married at year-end → MFJ or MFS — or HoH ONLY via the considered-unmarried
  or nonresident-alien-spouse tests in Group 1; if MFS in a
  community-property state (incl. CA), Form 8958 income splitting applies
  and is NOT modeled)
- Did you divorce or legally separate this year? → sets `divorced_this_year`
  (unmarried at Dec 31 → Single or HoH only; ask who claims each dependent —
  Form 8332 release — and check any QDRO distributions)
- A child born or adopted this year? → add to Group 3 dependents (a child
  born ANY day of the year counts for the full year)
- ANY adoption expenses this year — including an adoption still IN
  PROGRESS, a failed domestic adoption, prior-year expenses first
  claimable this year (domestic expenses are claimed the year AFTER
  payment until final), or an adoption-credit carryforward? → sets
  `has_adoption_expenses` (Form 8839 credit — manual worksheet). Ask this
  even when no child was adopted THIS year.
- Did you SELL your primary home? → sets `sold_primary_home` (§121
  $250K/$500K-MFJ exclusion with the 2-of-5-year ownership AND use tests;
  the exclusion is once-every-2-years (§121(b)(3) — ask about other home
  sales in the prior 2 years); the $500K joint amount additionally needs
  BOTH spouses to meet the use test, either to meet ownership, and neither
  to have used the exclusion in the prior 2 years; **ask whether a Form
  1099-S was received** — if yes, the sale must be reported even when
  fully excluded)
- Did you receive an inheritance? → sets `received_inheritance` (bequests
  themselves are not income, but capture date-of-death FMV basis
  documentation now; EXCEPTION — income in respect of a decedent (§691):
  inherited traditional IRA/401(k) distributions and the decedent's unpaid
  wages ARE taxable and get NO §1014 step-up; inherited IRAs carry
  SECURE-Act 10-year distribution rules)
- Do you pay or receive alimony/spousal support under ANY decree? Branch
  by instrument date:
  - PRE-2019 decree → sets `has_pre2019_alimony` (federally taxable to
    recipient / deductible by payer. CAUTION: a pre-2019 decree MODIFIED
    after 2018 loses grandfathering ONLY if the modification expressly
    applies the TCJA repeal — read the modification, not just the decree
    date)
  - POST-2018 decree → sets `has_post2018_alimony` (federally NEITHER
    income nor deduction — but CA does NOT conform to the TCJA repeal:
    post-2018-decree alimony stays CA-taxable to the recipient /
    CA-deductible by the payer via Schedule CA adjustments, not
    auto-computed; other states' conformity varies)
- Did your spouse pass away this year (or in the prior two years)? Year of
  death → MFJ with the deceased spouse is available ONLY if you did NOT
  remarry by Dec 31 (if you remarried, you file MFJ/MFS with the NEW
  spouse and the deceased spouse's final return is MFS); the TWO FOLLOWING
  years → QSS **only if the tests pass**: a dependent CHILD lived with you ALL year, you paid >1/2
  the cost of the home, you were entitled to file MFJ in the death year,
  and you have not remarried. Record the QSS test answers in `answers.md`
  like the HoH block. Also capture basis step-up on inherited assets
  (FULL step-up on community property incl. CA) → consider
  `received_inheritance`.
- Moved states? asked in Group 2 (`moved_states_this_year`), and moved to or
  from another country (`moved_abroad_this_year`).

**Group 2 — Address & state:**
- Street address, city, state, ZIP
- State of residence (for state return)
- Did you move states during THIS tax year? → sets `moved_states_this_year`
  (part-year returns are NOT modeled — the `/tax-compute` capability gate will
  block; tell the user now, not at compute time)
- Did you move to or from ANOTHER COUNTRY during THIS tax year? → sets
  `moved_abroad_this_year`. Ask it separately: someone who emigrated in June
  truthfully answers "no" to the states question, and without this one gets a
  full-year resident return taxing the months they lived abroad. It blocks at
  the same gate, for the same reason; tell the user now. Living abroad all
  year is `lives_abroad` (Group 4), not this.
- Were you (or your spouse) a bona fide resident of Puerto Rico, American
  Samoa, Guam, the Northern Mariana Islands or the US Virgin Islands at any
  point this year? → sets `bona_fide_territory_resident`. A territory is not a
  foreign country, so `lives_abroad` does not cover it, and the excluded
  territory income changes modified AGI. It blocks at the gate (and so does a
  territory address or `residency.domicile_state`); tell the user now.
- If NY appears anywhere — the address, `residency.domicile_state`, or either
  count above: are you a New York City resident? → sets `nyc_resident`.
  **Not "if state is NY"**: the taxpayer this matters most to has a Florida
  address and a Manhattan apartment, and an address-gated question never
  reaches them. **If NY appears nowhere**, the condition has answered the City
  and both Yonkers questions: set `nyc_resident`, `yonkers_resident` and
  `yonkers_nonresident_earnings` to `false`. They ship `null`, and a null one
  blocks the gate as unanswered.

  This flag used to BLOCK the return and is now the **city-tax switch** — the
  New York City resident tax is computed on it (IT-201 lines 47-54). That
  makes an unanswered one worse, not better: a missing answer once stopped the
  return loudly and would now omit the city tax from a return that owes it, so
  it must still be answered explicitly, true or false.
- **On a JOINT return, ask a second question**: how many of you were New York
  City residents for the WHOLE year — both, or only one? → sets
  `nyc_resident_spouses` (0, 1 or 2). One household boolean cannot carry this:
  `nyc_resident` says whether the household owes city tax at all, and this says
  whether BOTH spouses are residents. **When exactly one was, IT-201 line 47 is
  not used at all** — that return is computed on line 51, which this engine
  does not do, and it REFUSES rather than taxing the whole household. Leaving
  it unanswered refuses too: assuming two over-states the city tax for a
  Manhattan-and-Connecticut couple by the non-resident spouse's entire share.
  NEITHER refuses as well: on a return that owes City tax it means both
  spouses lived in the City for only part of the year.
- **On every OTHER return, ask instead**: were you a New York City resident for
  the WHOLE year? → sets `nyc_resident_full_year` (true or false). A City
  resident for only part of the year figures the City tax on IT-360.1 (IT-201
  line 50), which this engine does not do, so `/tax-compute` REFUSES rather than
  taxing the whole year's income, and an unanswered one refuses too. Tell the
  taxpayer now, not at compute time.
- Under the same "if NY appears anywhere" condition, ask BOTH Yonkers
  questions. They are two DIFFERENT taxes with two different populations, and
  a taxpayer can owe either without the other, so one question cannot stand
  for both:
  - "Did you live in Yonkers?" → sets `yonkers_resident` (the resident income
    tax surcharge, IT-201 line 55)
  - "Did you EARN wages or self-employment income in Yonkers while living
    somewhere else?" → sets `yonkers_nonresident_earnings` (the nonresident
    earnings tax, line 56)
  - **If they lived in Yonkers, on a JOINT return** ask how many of you lived
    in Yonkers for the WHOLE year → sets `yonkers_resident_spouses` (0, 1 or
    2). With exactly one, the surcharge is figured on that spouse's tax as if
    you had filed separately (special condition Y1), which this engine does
    not do, and `/tax-compute` REFUSES; zero refuses too.
  - **On every other return**, ask whether they lived in Yonkers for the
    WHOLE year → sets `yonkers_resident_full_year` (true or false). A
    part-year Yonkers resident files IT-360.1 (line 57), which is not modeled,
    so false or unanswered refuses. Tell the taxpayer now.
  - **If either Yonkers answer is yes**, ask whether the return reconciles a
    STAR credit on Form IT-119, and record its line 3 amount in `answers.md`,
    0 when there is none. The Yonkers worksheet takes it back out of the
    credits, and `/tax-compute` refuses without an answer.
  - **If they earned in Yonkers while living elsewhere**, record in
    `answers.md` the wages and self-employment earnings earned in Yonkers
    (Form Y-203 Schedules A and C allocate a job done partly outside it) and
    any START-UP NY wages. On a JOINT return Form Y-203 is not modeled and
    `/tax-compute` REFUSES, because its limit can be read as each spouse's
    surcharge figured as if they had filed separately; tell the taxpayer now.
    Someone who lived in Yonkers for part of the year and earned there while
    living elsewhere is a part-year resident, which refuses too.

  Both are required-present for the same reason `nyc_resident` is: absence
  reads as False and drops a real liability.
- **Ask EVERY taxpayer, whatever their address:** "What state do you consider
  your permanent home — the one you intend to return to?" → sets
  `residency.domicile_state`. There is exactly ONE, and it need not be the
  mailing address. Then, for any OTHER state they spent real time in this year,
  ask BOTH counts and record them in `residency.days_present_by_state` and
  `residency.abode_months_by_state`, keyed by 2-letter state code:
  - "About how many days were you physically in <state> this year?"
  - "How many months did you keep a home there that was available to you
    year-round — owned or rented, yours to use, not a hotel?"

  **Ask for the NUMBERS, never "were you there a lot?"** A statutory test has a
  line in it and no honest boolean answer near that line; the numbers are what
  the taxpayer can actually answer, they go in `answers.md`, and the engine
  applies the threshold so nobody has to do it in their head.
- **Then state the test — it is NEW YORK's, and only New York's** → sets
  `flags.statutory_resident_of_another_state`. **New York taxes you as a
  resident on ALL of your income, wherever it arose, if you spent 184 days or
  more in the state AND kept a permanent place of abode there for substantially
  all of the taxable year — more than 10 months from tax year 2022 (more than 11
  months before it). Any part of a calendar day counts as a day, and you do not
  have to have been at the home for the day to count. This applies WHATEVER your
  domicile is**, which is how somebody who moved to Florida and kept the
  Manhattan apartment stays a New York resident. Both prongs are required: days
  in hotels is not it, and an empty apartment is not it. Tell them what does NOT
  count — travel through the state, days confined to a medical institution,
  active service in the armed forces — and that an undergraduate's term-time
  residence is not a permanent place of abode. The New York CITY and Yonkers
  tests are the same tests applied at that level, so ask the NYC question below
  whenever NY appears in these counts, not only when the address is NY.
  **Do NOT transplant these numbers to another state** — Massachusetts has no
  abode-duration prong at all, Connecticut requires the whole year, and
  California has no mechanical test whatsoever. For any state other than New
  York, record the counts and hand it off; do not conclude.
  Only set the flag when that other state actually TAXES INCOME — screen against
  `capabilities._NO_INCOME_TAX_STATES`, or a snowbird with a Florida condo gets
  blocked over a tax that does not exist. Two resident states tax the same income
  and the credit between them is NOT modeled here, so this BLOCKS at
  `/tax-compute` — tell the user now, not at compute time. Figures and sources:
  `docs/contributing/residency-guardrails.md`.
- If they work remotely for an employer whose office is in another state → sets
  `residency.telecommutes_for_out_of_state_employer`. This changes the
  NONRESIDENT allocation, not the residency test. **Wages are sourced where the
  work is physically performed**; only Connecticut, Delaware, Nebraska, New York
  and Pennsylvania (plus New Jersey's reciprocal version) apply a
  convenience-of-the-employer rule, and New York's needs the employee's PRIMARY
  OFFICE to be in New York before a home day counts as a New York work day.
  Feed that into the source-income question below only when the employer's state
  is one of those; treating every out-of-state employer as source income would
  block the ordinary remote worker on correct data.
- Do you have ANY income SOURCED to a state other than your resident state
  (even if your resident state has no income tax)? **Ask broadly — do not
  stop at W-2/K-1/rental** (Codex round-15 P1): those three are EXAMPLES,
  not an exhaustive list. Also prompt specifically for (1) self-employment
  / Schedule C work actually PERFORMED in another state (a consultant who
  lives in FL but does client work on-site in another state sources that
  income there) and (2) the SALE of real property LOCATED in another state
  (a nonresident capital-gain filing trigger, separate from rental
  income). **Capture WHICH state the income is sourced to and gate the
  flag on that state (Codex round-16 P2)** — set
  `flags.has_nonresident_source_income` ONLY when the source state is NOT
  on the no-income-tax allowlist
  (`capabilities._NO_INCOME_TAX_STATES`: AK, FL, NV, SD, TX, WY, plus TN
  for `tax_year >= 2021`). A source state with NO individual income tax at
  all cannot impose a nonresident filing obligation on any of these income
  types, regardless of amount — setting the flag anyway would false-block
  `/tax-compute` on an unmodeled return that was never actually owed (e.g.
  a CA resident's NV consulting gig, or an FL resident's TX K-1). Every
  OTHER source state — including WA, which is deliberately NOT on that
  allowlist despite taxing no wages (its capital-gains excise is a
  separate, narrower tax) — keeps the flag set: fail-closed for anything
  not affirmatively no-tax, since an unmodeled real income-tax state's
  filing obligation is the dangerous direction to miss. This is DISTINCT
  from `moved_states_this_year` (which only fires on a mid-year move): a
  full-year resident of a no-tax state like FL can still owe a nonresident
  return on out-of-state income, with no resident-state credit mechanism
  to offset it. Nonresident state returns are NOT prepared — the source
  state's tax on that income is unmodeled (`/tax-compute` capability gate
  will block; tell the user now, not at compute time).
- **Ask EVERY taxpayer, regardless of resident state:** "Do you own tangible
  personal property (equipment, furniture, fixtures, etc.) used in any
  business OR rental activity, and where is that property physically
  LOCATED as of January 1?" — DR-405 tangible personal property tax follows
  property SITUS, not the taxpayer's residence, and covers property used in
  RENTAL activity (furnishings/appliances/equipment in a rental unit) just
  as much as a sole proprietorship's business equipment — narrowing the
  question to "sole proprietorship" would silently miss a landlord's rental
  furnishings. This is NOT a Florida-resident-only question — a Georgia
  resident with Florida business or rental equipment still owes DR-405 and
  would never be asked if this lived in the Florida-resident block below.
  Ask where the assets sit, not just whether they exist. Property located in
  a DIFFERENT state (not Florida) → do NOT set the flag; record that other
  state as a limitations note in `answers.md` (that state's own
  tangible-property tax, if any, is unmodeled — v1's calendar covers FL
  DR-405 only, out of scope for every other state).
  - **If the property is located in Florida, first ask: "Do you have
    property in more than one Florida county (or more than one business
    site/account within a county)?"** (Codex round-16 P2, verified against
    floridarevenue.com's TPP taxpayer guidance: "You must file a single
    return for each site in the county where you transact business," the
    $25,000 exemption is tracked "per return" not per taxpayer statewide,
    and the F.S. 196.183 continuing-filer waiver is granted/tracked "at
    the individual return level" — DR-405 is filed PER COUNTY/PER ACCOUNT
    with that county's property appraiser, so filing history, the January
    1 value test, and appraiser-request status can all differ by county:
    equipment in one county may already be covered by the waiver while a
    second county's rental furnishings still need an initial return).
    **Repeat the three follow-ups below for EACH Florida county/account
    separately** — do not answer them once for "the property" as a whole:
    (1) "Was the INITIAL DR-405 already filed in a prior year for THIS
    county's/account's property?", (2) "What was THIS county's/account's
    property value AS OF JANUARY 1 of {tax_year} — not its value today —
    did it exceed $25,000?" (DR-405's $25,000 exemption, F.S. 196.183, and
    the filing obligation itself are both determined by the property's
    assessed value on the January 1 assessment date, not its value at
    interview time — a taxpayer whose exempt equipment was worth over
    $25,000 on January 1 but was later sold or depreciated below that
    threshold before this interview must still get the filing reminder,
    not a false pass from reporting "current value", Codex round-9 P2),
    and (3) "Has THIS county's property appraiser specifically requested a
    return this year?" — the statute's one override on the waiver, and the
    taxpayer may know this even without prompting. Set
    `flags.fl_business_tangible_property` (the DR-405 Apr 1 calendar
    reminder) if ANY ONE county/account requires a filing THIS year: its
    initial return has NOT yet been filed, OR its January-1 value exceeded
    $25,000, OR its appraiser requested one. **Record WHICH counties/
    accounts require a return (and which are covered by the waiver) in
    `answers.md`** — the calendar reminder itself stays a single generic
    "check whether you owe DR-405 this year" prompt (round-7 finding D),
    but the per-county detail is what tells the taxpayer/preparer which
    specific county property appraiser(s) actually need a return filed.
    Only when EVERY county/account clears the waiver (initial already
    filed, value ≤ $25,000, no appraiser request, for ALL of them) do NOT
    set the flag; instead record a note in `answers.md` that no DR-405
    filing is expected this year under the F.S. 196.183 continuing-filer
    waiver (re-ask next year for every county — value and appraiser
    requests can change independently per county).

**For NON-Florida residents, first ask (Codex round-26 P2):** "Do you own
any residential real property located in Florida?" — nothing else in this
interview elicits non-business FL residential ownership from a
non-resident, so without this question the F.S. 196.031
dependent-residence homestead path below is UNREACHABLE (a GA resident
whose otherwise-unreported FL home is a dependent's permanent residence
would never be screened, both homestead flags would stay false, and the
DR-501 reminder would be suppressed). If YES, run the homestead question
below for them (the dependent-residence screening in its note); if NO,
skip the homestead question and this whole FL block.

**For Florida residents additionally ask** (EXCEPTION — the homestead
question below is NOT residents-only: ask it of ANY taxpayer who owns
Florida residential property, per the non-resident elicitation question
above and its F.S. 196.031 dependent-residence
note; the county/surtax and sales-tax questions here remain FL-residents
questions because the Schedule A sales-tax election follows the taxpayer's
own state of residence)**:**
- "Which Florida county do you live in?" → record the county in
  `answers.md` (ZIP codes can span multiple counties — confirm the county
  explicitly with the taxpayer, don't infer it from the ZIP). Feeds the
  county discretionary sales surtax rate fetch in the State section below.
  - **Follow-up:** "Did you live in more than one Florida county this year,
    or did your county's discretionary surtax rate change mid-year?" If yes,
    a single county's rate is wrong for the whole year — per the IRS
    Optional Sales Tax Tables / Schedule A locality worksheet, compute a
    TIME-WEIGHTED local rate instead: `local_rate = Σ(rate_i × days_i) / 365`
    (366 in a leap year), summed over each county/rate period `i` the
    taxpayer lived under during the tax year. Show the arithmetic (each
    period's rate, days, and the weighted sum) in `answers.md` so it's
    auditable, and pass the RESULT as `COUNTY_SURTAX_RATE` — never a single
    county's rate when more than one applied. A same-county, same-rate,
    full-year taxpayer just uses that county's rate directly (no weighting
    needed).
- "Did you buy **or lease** a motor vehicle, buy a boat or aircraft, buy a
  MOBILE or PREFABRICATED home, or buy building MATERIALS to build a home
  (or for a substantial addition / major renovation) this year?" → the IRS
  Optional Sales Tax Tables method (Schedule A
  instructions) allows adding ACTUAL sales tax paid on top of the table
  amount ONLY for these specific purchase classes — motor vehicles
  (**including sales tax paid on a LEASED motor vehicle** — the Schedule A
  instructions include leased vehicles explicitly; a lessee pays FL sales
  tax on each lease payment, so a purchase-only question would miss it and
  understate line 5a; Codex round-24 P2), boats, aircraft, and the home
  class, at the general sales tax rate. **The home class qualifies ONLY
  when GENERAL SALES TAX was actually charged** (Codex round-25 P2): a
  mobile/prefabricated home purchase, or building materials for building
  a home / a substantial addition / a major renovation. A CONVENTIONAL
  existing-home purchase carries documentary stamp tax, NOT sales tax —
  documentary stamps, intangible tax, and other closing taxes NEVER
  qualify, and recording them as big-ticket sales tax would overstate
  line 5a. Record actual sales tax paid on a qualifying
  purchase or vehicle lease in `answers.md` (feeds the Schedule A line 5a
  sales-tax election in /tax-compute). Do **NOT** record ordinary
  purchases here (furniture, jewelry, electronics, appliances, clothing,
  etc.) — those are already inside the table amount, and adding them on
  top would double-count.
- "Did you newly establish a Florida homestead this year — and if so, on
  what date?" **Also ask this of a NON-FL-resident taxpayer who owns
  Florida residential property** (Codex round-25 P2): F.S. 196.031 extends
  the homestead exemption to property maintained as the permanent
  residence of someone legally or naturally DEPENDENT upon the owner — a
  GA-resident owner whose FL home is their dependent parent's or child's
  permanent residence can hold an eligible FL homestead, so this screening
  (and the flags it sets) must not be skipped just because
  `address.state` isn't FL; the calendar emits the DR-501 reminder from
  the asserted flags alone. **Ask for the specific date, don't stop at
  "this year"**
  (Codex round-10 P2): F.S. 196.011 eligibility is determined by ownership
  AND occupancy as of January 1, so a taxpayer who first owned/occupied on
  January 1 of *this* tax year already satisfies THIS year's eligibility
  test — routing them into the forward-looking next-year flag below would
  skip the deadline that's actually due now. Branch on the date:
  - Established EXACTLY on January 1 of this tax year: this is a
    CURRENT-year eligibility case, not a forward-looking one. **Ask a
    follow-up before setting any flag** (Codex round-11 P2): "Have you
    already filed the initial DR-501 for this homestead?" If NOT yet
    filed, set `flags.fl_homestead_unfiled` (the same flag used for a
    prior-year establishment that's still unfiled; a January 1-this-year
    establisher is unfiled for the same current exemption year), which
    sets the CURRENT-year March 1 reminder. If ALREADY filed, do NOT set
    `fl_homestead_unfiled` — no filing remains due, and setting it anyway
    would show a phantom current-year deadline (with a description that
    incorrectly implies the homestead was established before this year).
    Do NOT set `fl_new_homestead` for this date either way.
  - Established on any OTHER date during this tax year (after January 1):
    **ask the same already-filed follow-up before setting the flag**
    (Codex round-16 P2, verified: Fla. Admin. Code Ann. R. 12D-7.001 —
    "Property appraisers are permitted, at their option, to grant
    homestead exemptions upon proper application throughout the year for
    the succeeding year" — pre-filing the initial DR-501 for the UPCOMING
    exemption year is legally authorized county-appraiser practice, not
    hypothetical): "Have you already filed the initial DR-501 for next
    year's exemption?" If NOT yet filed, set `flags.fl_new_homestead`
    (homestead eligibility is determined as of January 1, so a homestead
    newly established after January 1 is first eligible next year — this
    sets a DR-501 reminder due March 1 of *next* year; clear the flag
    after the first year — renewals are automatic in most counties). If
    ALREADY filed (pre-filed), do NOT set `fl_new_homestead` — the filing
    is done and a reminder would be phantom (note: pre-filing is "at
    [the property appraiser's] option" per the rule above, so a "no" answer
    may also mean the taxpayer's county doesn't offer it — the reminder
    stays the safe default in that case).
- "Do you already own and occupy a Florida homestead as of January 1 THIS
  year, but have NOT yet filed the initial DR-501?" → `flags.
  fl_homestead_unfiled` (covers BOTH a homestead established late in a
  PRIOR year that was never claimed, AND a homestead established EXACTLY
  on January 1 of this tax year per the branch above — both share the same
  CURRENT-year March 1 reminder; distinct from `fl_new_homestead`'s
  forward-looking next-year event, which only covers a homestead
  established AFTER January 1 during this year; clear the flag once filed
  — renewals are automatic in most counties).

**Section pruning for no-income-tax states:** remove `california_540`,
`ira_contribution.ca_savings`, and `balance_due_summary.california_refund`
(see their `_note`s); KEEP `florida_state` for FL.

**Group 3 — Dependents:**
- Number of dependents (0 = skip, and set `has_dependent_unearned_income` to
  `false` — no dependent, no Form 8615)
- For each: name, SSN (last 4), DOB, relationship, months lived with taxpayer
- Under 17 at end of tax year? (for CTC eligibility)
- Does any dependent have investment/unearned income over the kiddie-tax
  threshold (INDEXED — take it from the covered year's Form 8615 instructions;
  do not state a figure to the taxpayer)? → sets `has_dependent_unearned_income` (Form 8615 is
  NOT modeled — capability gate)
- **Trump accounts (OBBBA §530A "MAGA"/newborn savings accounts) are NOT
  modeled** (out of persona): the $1,000 seed for children born 2025-2028 and
  the contribution/withdrawal mechanics are not computed — if a dependent has
  one, flag it for manual handling rather than silently omitting it.

**Group 4 — Tax situations (yes/no flags):**
- Do you have foreign bank/investment accounts? → triggers FBAR/8938 workflow (`has_foreign_accounts`)
- Do you have SIGNATURE AUTHORITY over (but no financial interest in) any foreign account — an employer's, a parent's, a nonprofit's? → FBAR Part IV even with no financial interest (`signature_authority_foreign_accounts`)
- Do you hold any NON-US-DOMICILED mutual funds or ETFs in ANY brokerage
  account — foreign OR US (e.g. a UK OEIC/unit trust, an Irish/Luxembourg UCITS
  ETF, a Canadian mutual fund)? → sets `has_foreign_mutual_funds`. **Ask this
  independently of the foreign-account questions:** PFIC status follows the
  FUND's domicile, NOT where it's held, so a foreign-domiciled fund bought
  through a US broker is still a PFIC and must set the flag even when the
  taxpayer has no foreign accounts. These are almost
  always PFICs (§1291-1298); **Form 8621 may be required per fund** — the
  obligation is EVENT-DRIVEN (an excess distribution, a sale, a QEF/MTM
  election, or the §1298(f) annual reporting, which has de-minimis exceptions),
  so determine per fund rather than assuming every fund needs a form. When a
  trigger applies the default §1291 regime is punitive (excess-distribution
  spreading + interest charge) and is NOT modeled here, so this lands on the
  limitations manifest and is flagged in `/tax-review`. US-domiciled funds
  (even ones holding foreign stocks) are NOT PFICs — this is about the FUND's
  domicile, not what it invests in.
  - **If yes — did any of those foreign-domiciled funds/ETFs pay a
    DISTRIBUTION or get SOLD this year?** → sets `has_pfic_taxable_event`. This
    is the load-bearing follow-up: a PFIC with a taxable event owes §1291 tax
    (excess-distribution spreading + top-rate-per-year + a compounding interest
    charge) that is PUNITIVE. `/tax-compute` **DOES compute it** (compute_pfic_1291)
    once you acknowledge the block and supply the facts — but it stays a hard
    stop by default (easy to under-state), and a fund with a QEF/MTM election, or
    one that became a PFIC AFTER acquisition, stays blocked (not modeled). Routing
    a PFIC distribution/gain through the normal qualified-dividend / LTCG path
    would UNDERSTATE tax. Buy-and-hold with no distribution/sale does NOT set
    this — that stays the reporting-only `has_foreign_mutual_funds` note. To
    enable the computation, populate `foreign_holdings_detail.holdings` per fund
    with the §1291 inputs: `fund_id` (a STABLE, UNIQUE per-fund identifier —
    ISIN/CUSIP or a slug — so the tie-out can match each taxable fund to its
    computed result), `acquisition_date`, the event date(s) — `distribution_date`
    AND `disposition_date` (a distribution and a sale in the same year are separate
    §1291 events with different allocation endpoints, so carry BOTH when both
    occur), `pfic_election`, `preceding_holding_years` (REQUIRED for a non-first-year
    distribution — without it the excess-distribution result is INCOMPLETE),
    `distributions_by_year` (the FULL zero-inclusive base-period history — a $0
    entry for every year with no distribution, summed per fund per year),
    `disposition_gain_usd` for a sale — the **SIGNED** proceeds-minus-basis for EVERY
    sale, INCLUDING a negative loss (only a POSITIVE gain is routed through §1291; a
    loss still goes on Schedule D, but the signed amount MUST be recorded so the
    tie-out can tell a real loss from an omitted/unknown amount — a `disposed` sale
    with no finite value is treated as an unverifiable event and BLOCKS the return),
    and `position_fully_closed` (true iff the sale closed the whole position — a
    PARTIAL sale is false so /tax-rollover keeps the remaining shares + history next
    year).
    Absent/short facts → the computation returns `complete=False` and the return
    stays blocked. A fund with a QEF/MTM
    election is hand-prepared (Form 8621 Part I/II/III), not §1291-computed — record
    it (so the event is on file) but expect no computed §1291 result for it.
- Do you hold any foreign securities at a NON-US broker/custodian, or
  directly-registered foreign securities (not in a US brokerage account)? →
  sets `has_foreign_financial_account` (Form 8938 §6038D specified-foreign-
  asset reporting over the threshold; complements FBAR — assets in a US
  brokerage are generally excluded). Record the custody in
  `foreign_holdings_detail`.
- Is any holding under OFAC SANCTIONS — frozen/blocked so you cannot sell it or
  receive distributions (e.g. certain Russian securities post-2022)? → sets
  `has_sanctioned_blocked_asset` (blocked-income + §165(g) worthlessness are
  hand-analyzed — do NOT claim a loss on a merely-frozen asset; and the treaty/
  §901(j) FTC status of the sanctioning country is verified per year).
- Did you HOLD or dispose of any FOREIGN-CURRENCY BOND (non-USD debt) directly
  (not a fund)? → sets `has_foreign_bond` and records it in
  `foreign_holdings_detail.holdings` with kind `bond`, the currency, the
  foreign-currency cost/proceeds, and the acquisition/disposition spot rates. A
  bond is DEBT, NOT a PFIC. Ask about HOLDING (not just selling): coupon
  interest translates to Schedule B and, if bought at a discount, §1272 OID
  accrues into income EVERY year even with no sale (MANUAL — omitting it
  understates income). On a disposition, `/tax-compute` also splits the §988
  ordinary FX gain/loss from the capital market gain/loss via
  `compute_section_988`.
- Did you LIVE abroad (tax home outside the US) during the year? → sets
  `lives_abroad` (raises the Form 8938 thresholds; the FEIE / Form 2555
  election is NOT modeled — only the FTC path — so this lands on the
  limitations manifest)
- Do you have self-employment income (1099-NEC, freelance, business)? → triggers Schedule C (`has_self_employment`)
  - **If yes (or you have K-1 pass-through business income):** Is that income
    from an ACTIVE qualified trade or business you materially participate in —
    NOT a passive/portfolio investment (e.g. a rental, a limited-partner
    interest you don't run, or REIT dividends)? → sets `qbi_active_trade`. This
    is load-bearing for TY2026+: §70105 grants an active qualified business a
    MINIMUM QBI deduction once QBI is at or above an income threshold — both
    figures INDEXED and both fetched into `federal.qbi_minimum`, which is why
    neither is written here — even when 20% of QBI is smaller. It defaults
    False (no floor → no over-claim), so leaving it unset silently forgoes an
    otherwise-applicable floor and OVERSTATES tax for a small active business.
    Ask explicitly whenever `has_self_employment` or `has_k1_income` is set.
- Do you have rental income? → sets `has_rental_income` (Schedule E is NOT
  modeled — the `/tax-compute` capability gate will block; tell the user now)
- Do you have an HSA (Health Savings Account)? **If yes**, for the Form 8889
  return-side excess check (`hsa_detail`): what was your HDHP coverage
  (`self_only`/`family`), and for **how many months** were you an HSA-eligible
  individual (HDHP on the 1st of the month, no disqualifying coverage)? Were you
  eligible on **December 1** (the §223(b)(8) last-month rule → full annual limit,
  but a testing period applies)? If married and BOTH spouses have HSAs with
  family coverage, what share of the family limit is yours? (A partial-year
  filer who contributed the full annual limit has an EXCESS + a 6% excise — the
  months matter.)
- Do you have a 401(k) or employer retirement plan?
- Did you make IRA contributions this year?
- Did you have a 401(k) rollover (Form 1099-R)?
- Did you pay foreign taxes?
- Did you receive a state tax refund for the prior year (Form 1099-G)? → may be taxable if you itemized
- Did you employ anyone in your household (nanny, caregiver)? → sets
  `has_household_employees` (Schedule H is NOT modeled — capability gate)

**Group 5 — Foreign currencies (if applicable):**
- Which foreign currencies? (for exchange rate lookup)

**Group 6 — Income estimates:**
- Approximate total W-2 income? (for threshold screening: AMT, NIIT, Medicare surtax)
- Any gig/freelance income (1099-NEC)? Crypto transactions (buy/sell/exchange)?
- Stock compensation: RSU vesting, ISO exercises, ESPP purchases?
  - **If ISOs were EXERCISED (and any shares held past year-end)** → sets
    `has_iso_exercise`; otherwise set it `false`. This is load-bearing: it triggers the federal AMT
    computation (Form 6251 — the bargain element is the most common AMT
    trigger and can add tens of thousands of tax) and the Form 8801 credit
    carryforward. CA's own AMT (Schedule P) is NOT computed — tell a CA
    exerciser now that the CA side needs software verification. If Form 3921
    is among the documents, /tax-parse extracts the spread per exercise —
    only ask for the figure (FMV − strike per share × shares held) when no
    3921 will be provided.
    (RSU/ESPP alone do NOT set this flag — they are ordinary W-2 wage
    events.)
  - **If RSUs VESTED this year, NQSOs were exercised, OR you SOLD any
    RSU/NQSO employer shares this year (even shares that vested/were exercised
    in a PRIOR year)** → sets `has_rsu_vesting`. **If ESPP shares were
    PURCHASED or SOLD this year (any purchase year)** → sets `has_espp`. The
    SALE trigger is load-bearing: the double-tax happens at SALE, so a taxpayer
    liquidating long-held employer stock with no current-year vesting still
    needs the basis correction — keying only on current-year vesting would
    leave the primary scenario unprotected. These are load-bearing for basis
    correction: brokers report the 1099-B cost basis for employer stock
    EXCLUDING the compensation already in W-2 box 1 (for RSUs, typically $0
    basis), so an unadjusted sale DOUBLE-TAXES the W-2 income — routinely a
    four-to-five-figure overstatement.
    When either flag is set AND any 1099-B is present, the broker's
    *supplemental information* statement (the "adjusted cost basis" pages) —
    or the equity-plan vest confirmations / Form 3922 (ESPP) / Form 3921 (ISO)
    — becomes a REQUIRED intake document; `/tax-parse` will prompt for it and
    `/tax-compute` applies the Form 8949 code-B basis adjustment only from that
    documentation (never inferred).
- K-1 income from partnerships or S-corporations? If yes, ask **which states the entities are based in** (sets `flags.k1_states`, e.g. `["CA", "NY"]`). PTET is an entity-level state tax — the partnership / S-corp files in its operating state, not the partner's resident state — so a TX-resident partner in a CA partnership needs `k1_states: ["CA"]` to get the right PTET reminders in `tax_calendar_{year}.ics`.
- Gambling income (W-2G)? → sets `has_gambling_income` flag
- **OBBBA Schedule 1-A deductions (TY2025-2028 only — BELOW-AGI, line 13b).**
  Ask the unreported/allocated-tips question below in EVERY year:
  `has_unreported_or_allocated_tips` is a required gate question, and Form
  4137 is not a Schedule 1-A item.
  - Any **qualified tip income** in a customarily-tipped occupation? → sets
    `has_qualified_tips` (§224). Source: the employer's designated qualified-
    tip amount (W-2 box 14 code / separate statement) if provided, ELSE **W-2
    box 7 (Social Security tips)** — a single-employer tipped worker usually
    has no separate statement, so box 7 tips still set the flag. The SSN test
    is RECIPIENT-specific: the spouse who RECEIVED the tips must have a valid
    SSN (an ITIN on the OTHER, non-recipient spouse does NOT disqualify the
    return); MFS doesn't qualify — married must file jointly. **Unreported or
    employer-ALLOCATED tips (W-2 box 8)?** → sets
    `has_unreported_or_allocated_tips` (Form 4137 — adds them to wages on 1040
    line 1c and computes SS/Medicare tax on Schedule 2 line 5; NOT modeled by
    the engine, so it lands in the limitations manifest). They ARE §224
    qualified tips (Schedule 1-A Part II has a Form 4137 line), so once the
    paired Form 4137 income + FICA are hand-entered they CAN feed
    `qualified_tips` (set `has_qualified_tips` too) — exclude them only while
    that paired treatment is unhandled; don't permanently drop the deduction.
  - Any **overtime pay**? → sets `has_qualified_overtime`. Capture the **FLSA
    premium portion ONLY** (the "half" of time-and-a-half, box 14 / employer
    statement), NOT gross overtime wages (§225).
  - **Car-loan interest** on a vehicle bought after 2024-12-31? → sets
    `has_car_loan_interest` (§70203). Only qualifies if: NEW vehicle (original
    use), first-lien loan originated after 2024-12-31, US final assembly,
    a qualified passenger vehicle with **GVWR under 14,000 lbs** (car/SUV/van/
    pickup/motorcycle — NOT a heavier commercial vehicle), personal use, the
    VIN is reported on the return, and the filer is NOT married-filing-
    separately (§70203 is unavailable to MFS). Ask for the VIN and the GVWR;
    route an unknown/over-14,000-lb vehicle to manual verification (don't set
    the flag).
- **Age 65 or older** (either spouse) by year end? → sets
  `age_65_plus_primary` / `age_65_plus_spouse`. Drives BOTH the §63(f)
  additional standard deduction AND the separate OBBBA §70103 enhanced senior
  deduction (Schedule 1-A line 13b — they STACK). The spouse's §70103 senior
  deduction counts only on MFJ; the §63(f) additional standard deduction,
  however, may ALSO count the spouse's age/blindness box on an MFS return when
  the spouse had NO income, isn't filing a return, and can't be claimed as a
  dependent by another taxpayer (2025 Form 1040 line 12d). **Valid-SSN split:**
  the §70103 senior deduction (unlike §63(f)) requires the 65+ person to have a
  valid SSN. The `age_65_plus_*` flags drive §63(f), so KEEP them set for a 65+
  ITIN person — but do NOT pass that person's age boolean into
  `compute_schedule_1a` (screen §70103 SSN eligibility separately), or Schedule
  1-A over-claims the senior deduction for a non-SSN filer.
  **Final (decedent) returns:** a taxpayer/spouse who was born before the
  cutoff but DIED before actually reaching age 65 does NOT qualify (per the
  Schedule 1-A instructions) — do NOT set the flag in that case even though
  the birth year would otherwise suggest 65+.

**Group 7 — Retirement & savings detail:**
- IRA type: Traditional, Roth, or both? Any Roth conversion this year?
- Backdoor Roth? (If yes: check pro-rata trap — any pre-tax Traditional IRA balance at year-end?)
- 401(k): Traditional/Roth split? Total deferral amount?
- Age 50-59 or 64+? (catch-up eligibility for 401k/IRA — amounts from `tax_law JSON` `retirement_401k.catch_up_50_plus` and `ira_limits.catch_up_50_plus`)
- Age 60-63? (SECURE 2.0 super catch-up for 401k — amount from `tax_law JSON` `retirement_401k.catch_up_60_63`, which **supersedes** the standard catch-up if both apply)
- **Fetch the SECURE 2.0 §603 high-earner threshold** → `tax_law JSON` `retirement_401k.secure_2_0_high_earner_threshold`: the prior-year FICA-wage threshold above which 401(k) catch-up must be designated Roth (IRC §414(v)(7)(A); $145k indexed from 2023, ≈ $150k for TY2026). It indexes annually — **REQUIRED for TY2027+** (`/tax-retirement`'s built-in $150k default is TY2026-only and hard-errors for later years if this is null). For TY2026 it may be left null (the $150k default applies), but fetching the exact value is preferred.
- Age 55+? (HSA catch-up: amount from `tax_law JSON` `hsa_limits.catch_up_55_plus`)
- Are RMDs (required minimum distributions) currently required from primary or spouse's traditional IRA / 401(k) accounts? (sets `subject_to_rmd_primary` / `subject_to_rmd_spouse` — drives Dec 31 RMD reminders in `tax_calendar_{year}.ics`). RMD start age is birth-year-cohort-dependent under SECURE 2.0 — generally age 73 for those born 1951–1959, age 75 for those born 1960 or later; inherited IRAs trigger RMDs at any age. If unsure, confirm with the taxpayer's preparer.
- Mega backdoor Roth: Does employer plan allow after-tax contributions + in-service conversion?

**Group 8 — Deductions & credits probe:**
- Student loan interest paid? Educator expenses (if teacher)?
- College tuition/education expenses? (AOTC / Lifetime Learning Credit) → sets `has_education_expenses`.
  Form 8863 IS computed by `/tax-compute` (D5) — collect per student NOW:
  **whether the student is claimed as a dependent on THIS return** (only the
  taxpayer, spouse, or a CLAIMED dependent's expenses count — §25A(f)(1);
  a dependent student claims nothing on their own return, and a
  non-claimed relative's tuition earns $0), 1098-T box 1 payments and box 5
  scholarships (expenses are netted per Pub 970), and the AOTC eligibility
  facts (degree program, at least half-time, first 4 years, AOTC claimed
  fewer than 4 prior years, no felony drug conviction, TIN by the due
  date). MFS gets neither credit; QSS phases out on the SINGLE $80K-$90K
  MAGI range.
- Child/dependent care expenses? (Form 2441) → sets `has_dependent_care`.
  **If yes**, collect `dependent_care_detail` for the §21 credit: how many
  QUALIFYING persons (under 13, or a spouse/dependent incapable of self-care who
  lived with you more than half the year)? How much did you pay in work-related
  care? Did BOTH spouses (if married) work, look for work, or attend school
  full-time (the §21(d) work-related rule — confirm)? Was any of it paid through
  an employer dependent-care FSA (W-2 box 10)? For a non-working spouse who was
  a full-time student or disabled, how many months? Do you have the care
  provider's name and TIN (required — due diligence)? If MFS, did you live apart
  from your spouse the last 6 months (the §21(e)(2) exception — otherwise MFS
  gets no credit)? The credit is now COMPUTED from these facts.
- **Earned income this year, and income possibly low enough for the Earned
  Income Credit?** → sets `may_qualify_for_eic`. **Set it TRUE on a MAYBE.**
  A false positive costs one manifest line; a false negative costs the whole
  credit, because NOTHING in this toolkit computes the EIC — Form 1040 line 27,
  IT-201 line 65 (Form IT-215), line 66 (Form IT-209, the noncustodial parent
  credit) and line 70 (the NYC EIC) are four inputs that default to 0, flow
  into the payments totals, and produce no source document for the tie-out to
  miss. Each has its OWN form; never copy one line's figure onto another. The
  flag is what puts the gap on `meta.limitations`. **Leaving it unanswered is
  not a no** — the tie-out says so — but answering it here is what makes the
  manifest line appear at all. **On a New York return, also answer YES for a
  noncustodial parent paying court-ordered child support** — Form IT-209 has
  its own eligibility test, not the federal one, so a federal NO does not
  rule it out. Screen on SHAPE, not on figures: earned income
  from work or self-employment; a qualifying child, or the taxpayer (on MFJ, at
  least one spouse) in the childless age band; SSNs valid for employment;
  filing status other than MFS unless the separated-spouse rules are met;
  investment income under the year's cap. **That list is a prompt, not the
  test** — §32 also requires US citizenship or resident-alien status all year,
  no Form 2555, and that the filer is not another taxpayer's qualifying child.
  The US-home test depends on the case: WITHOUT a qualifying child, the filer's
  own main home must be in the US for more than half the year; WITH one, it is
  the CHILD who must have lived with the filer in the US for more than half the
  year, and a child born or who died during the year still counts. None of this
  is a reason to answer NO — every omission from the prompt biases toward a
  YES, which is the direction this question wants. The amounts
  and the cap index annually and the age band is statutory but has moved — take
  all of them from the covered year's Form 1040 instructions (Pub 596) when the
  credit is actually worked, never from memory and never from here.
- Charitable giving? (approximate amount, cash vs property donations) → sets `has_charitable_giving`
- Energy-efficient home improvements? (heat pump, solar, insulation — 25C/25D) → sets `has_energy_credits`
- EV purchase or lease this year? (30D/25E) → sets `has_ev_purchase`
- Marketplace health insurance? (Form 1095-A) → sets `has_marketplace_insurance`
  (Form 8962 APTC reconciliation is NOT modeled — the `/tax-compute` capability
  gate will block; reconciliation is mandatory when advance PTC was paid, so
  warn the user now)

**Group 9 — Prior year carryforwards:**
- Capital loss carryforward from prior year? Ask for the SHORT-term and
  LONG-term amounts separately (the prior return's Capital Loss Carryover
  Worksheet, lines 8 and 13) → `prior_year.capital_loss_carryforward_short_term`
  and `prior_year.capital_loss_carryforward_long_term`. A figure the user only
  knows combined goes in the legacy `prior_year.capital_loss_carryforward`; do
  not split it by guess — `/tax-compute` stops for the split before Schedule D.
- QBI (qualified business income) loss carryforward?
- Unused foreign tax credit carryforward?
- Net operating loss (NOL) carryforward?

---

## The interview does not end here

Group 9 is the last group in THIS file, not the last group in the interview.
Return to `../SKILL.md` and continue at **Step 9.5**, which auto-invokes
`/tax-rollover` when a prior-year archive exists, and then **Group 10 —
Estimated taxes**, which lives there rather than here on purpose: the rollover
pre-fills the prior-year overpayment Group 10 asks about, so asking it first
would have the taxpayer recall from memory a figure the next step supplies.
