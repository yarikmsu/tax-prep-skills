# Step 3.7 — No-income-tax state completeness

Read when `tax_law state.type == "none"`. Linked from `SKILL.md` step 3.7.

This step covers EVERY `type: "none"` state (FL, AK, NV, SD, TX, WY, and TN
for tax_year >= 2021 — the `create_state_template`/`_NO_INCOME_TAX_STATES`
allowlist), not just FL. **BEFORE branching, run the same cache-vs-taxpayer
cross-check `/tax-compute` Step 10 and `/tax-generate` apply (Codex
round-27 P2 — review lacked the twin):** (a) `tax_law.state.code` MUST
equal the reviewed `taxpayer_profile.json address.state`; (b) the cache's
own declared `tax_year` MUST equal the year under review; (c) the
code-vs-regime allowlist must hold in both directions (`type: "none"` only
for an allowlisted code AND an allowlisted code never `graduated_income`
with real brackets). Any failure is itself a review FINDING and this step
must NOT validate the state content — a completed Florida result beside a
fresh FL cache left over after the profile was corrected to CA would
otherwise have its populated FL statement and sales-tax block validated
here, exactly when the real finding is a MISSING California return. Only
after all three hold, branch on `tax_law_{year}.json state.code`:

**If `state.code == "FL"`** — a dedicated `florida_state` result.json section
exists, so verify ABSENCE was handled affirmatively in full detail:

1. **Affirmative statement present:** `florida_state` exists with
   `state_return_required: false` and the constitutional citation — an
   empty/missing section is a finding, not a pass.
2. **Sales-tax candidate computed — check this BEFORE the itemized-vs-
   standard election, not only when the return already itemizes:**
   `/tax-compute` step 2 always runs the FL sales-tax computation
   regardless of outcome, so `florida_state.sales_tax_deduction.
   total_line_5a_candidate` must be populated (a real computed number, not
   the template's placeholder `0`) on EVERY FL return. Gating this check on
   "the return currently itemizes" is circular (Codex round-9 P2): if the
   candidate was never computed, Schedule A stays below the standard
   deduction, the return elects standard by default, and a check that only
   fires "if itemizing" would never catch the very omission that caused the
   standard-deduction outcome. So: (a) first confirm
   `total_line_5a_candidate` exists and is a real computed value; (b) THEN,
   if it was passed to `compute_schedule_a` as `state_local_sales_taxes`
   and the return still elects the standard deduction, verify that's
   actually the larger of the two (not just assumed) — and if it elects to
   itemize with $0 on line 5a despite a nonzero candidate, that's forgotten
   money. (c) **Method election was OFFERED (Codex round-19 P2):**
   Schedule A line 5a also permits documented ACTUAL general sales taxes
   instead of the optional tables — `sales_tax_deduction.method` must
   record which method was elected (`"actual_receipts"` or
   `"optional_tables"`), and `answers.md` must carry the substantiation
   question's answer (plus both figures when receipts existed). A populated
   candidate with NO recorded method election means the actual-expense
   alternative was never offered — a potential line-5a understatement for
   a receipts-keeping taxpayer, and a finding. When `method` is
   `"actual_receipts"`, the `total_line_5a_candidate` intentionally does
   not equal the table components' sum (they record the losing table
   computation as comparison evidence).
3. **Use-tax note — require ANSWER EVIDENCE, not note presence (Codex
   round-25 P2):** FL self-reports use tax on untaxed out-of-state/online
   purchases — via Form DR-15MO (unregistered), or the dealer's regular
   Form DR-15 if registered as a FL sales-and-use-tax dealer (Codex
   round-16 P2, verified against floridarevenue.com — registered dealers
   must NOT use DR-15MO). **`florida_state.use_tax_note` ships as a
   POPULATED template default, so a present note is NOT evidence the
   questions were ever asked** — the same scaffold-read trap the
   null-by-default `no_income_tax_statement` rule (item 1) exists to
   prevent. In EVERY path, `answers.md` must record BOTH answers: (a) the
   untaxed-purchases question (yes with amounts / affirmative none), and
   (b) the dealer-registration question. Then verify the note reflects
   them: removed (or an affirmative-none rewrite) when no purchases;
   an actual use-tax figure when purchases were identified; DR-15 (not
   DR-15MO) wording for a registered dealer. Findings: a note still
   reading as the generic template text ("Review whether any apply") with
   NO recorded answers = the question was never asked — a taxpayer with a
   real use-tax liability would sail through; a note simply ABSENT with no
   recorded affirmative-none answer = same finding (never asked, not
   cleared); a registered-dealer note that still says DR-15MO = a
   wrong-form finding.
4. **Mover check: covered by step 3.8, on the MAIN path, for every return.**
   The checklist and the figures live in `references/residency-review.md`. Do
   not restate them here — this file is the no-income-tax branch, and residency
   is a question about the state being LEFT.

**If `state.code` is any OTHER type-none state** (AK/NV/SD/TX/WY, or TN when
`tax_year >= 2021`) — no dedicated result.json section exists for these yet
(v1 gap; only FL has one), so verify the capability-gate's manifest note
landed correctly instead of the full FL checklist above:

1. **Affirmative no-income-tax statement present (state-NEUTRAL wording):**
   `meta.limitations` carries the state's affirmative "no individual income
   tax — no individual income-tax return exists or is required (this
   state's own property/use-tax filings, if any, are not modeled here)"
   line (the `capabilities._state_note` NON-FL text — the FL form names
   DR-405/DR-15MO are scoped to `code == "FL"` only; Codex round-17 P2,
   validation text aligned round-18 P2) for `state.code`. Two failure
   directions, BOTH findings: (a) a missing or generic "bracket-based
   ESTIMATE" line (the capability gate didn't recognize the state as
   type-none, or the note never made it into result.json); (b) a note that
   names Florida forms (DR-405 / DR-15MO / DR-15) for a NON-FL state — a
   TX/NV/AK taxpayer has no nexus with Florida filings, so that's a
   pre-round-17 capabilities module or a hand-edited manifest. Do NOT
   "correct" a neutral note by substituting Florida-specific wording; the
   neutral caveat IS the expected text here.
2. **Explicit MANUAL Schedule A sales-tax note present (not FL's automated
   wording):** `/tax-compute` step 2 explicitly SKIPS the sales-tax
   computation for every no-tax state other than FL — so the manifest note
   for a non-FL state (`capabilities._state_note`) must say the election
   must be computed MANUALLY from the IRS Optional State Sales Tax Tables,
   distinct from FL's "computed automatically" wording. **AK and TN
   variants (Codex rounds 23/26):** Alaska's note references the IRS
   Optional LOCAL Sales Tax Tables ONLY (AK has no statewide sales tax
   but is on the IRS local-table list), and post-repeal TN's note
   references BOTH the state table AND the local table (TN has a
   statewide sales tax and is on the local-table list too). A generic
   state-table-only note on an AK or TN return omits the taxpayer's
   locality amount and is itself a finding. Accepting FL's
   generic phrase here is itself a finding (Codex round-9 P2): it would let
   review pass a return that has neither a computed line-5a amount nor a
   warning that one is still needed, silently leaving an itemized deduction
   on the table with no evidence it was ever considered. Confirm the note's
   wording actually says MANUAL computation — not merely that a sales-tax
   note of some kind exists.

Missing either item for a non-FL type-none state is a finding — treat it the
same as a missing `florida_state` section: absence is not a pass.

**Nonresident exposure — the big one (state-independent, applies to EVERY
type-none state, not just FL):** no-tax-state residence does NOT remove
nonresident filing on other states' source income (NY IT-203 for NY
wages/K-1s), and with no resident-state return there is NO credit mechanism
— the other state's tax is a pure cost. **The true mechanism:** this is
tracked by `flags.has_nonresident_source_income` (set at `/tax-setup` from
the out-of-state source-income screening — W-2/K-1/rental are EXAMPLES
there, and the interview also asks specifically about Schedule C work
PERFORMED in another state and the SALE of real property LOCATED in
another state, per the round-15 broadening — and confirmed/set by
`/tax-parse` on a Box-15 mismatch) and gated by the
`nonresident_source_income` capability rule, which BLOCKS `/tax-compute`
until acknowledged — `moved_states_this_year`/`part_year_residency` alone
does NOT cover this (it only fires on a mid-year move; a full-year
no-tax-state resident with an NY W-2 or K-1 would otherwise sail through).
Cross-check EVERY screening category against the flag, not just the first
three (Codex round-21 P2): `flags.k1_states`, W-2 Box 15 codes ≠ resident
state, out-of-state rental income, Schedule C work performed in another
state, and an out-of-state real-property sale (`answers.md` records the
interview's answers for the last two) — a flag left False despite ANY of
these signals is itself a review finding, UNLESS the source state is on
the no-income-tax allowlist (`capabilities._NO_INCOME_TAX_STATES` — the
round-16 gate), where False is the CORRECT outcome: do not "fix" it by
setting the flag for an FL resident's TX K-1 or NV consulting gig.
Nonresident returns remain out of scope; flag loudly.

---

**Return to `SKILL.md` step 4 (Generate Review Report).** This file ends here; the review report is written there, so stopping at this line produces no workpaper at all. An agent walking a reference in order hits EOF with no instruction unless the file says where to go — the same early stop `references/interview.md` was fixed for in `tax-setup`.
