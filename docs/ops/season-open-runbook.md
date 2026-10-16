# Season-Open Runbook — annual TY law refresh (M2)

Run this once per filing season, before the first real return of the new tax
year, to fetch and pin that year's tax law and prove the pipeline is ready.

The engine's fetched cache blocks each have a **declared absence behavior**
chosen by bias direction (2026-07-20 review, Part 1): understatement-risk blocks
HARD-ERROR when unfetched, taxpayer-favorable blocks degrade loudly, and blocks
with a statutory schedule fall back to it. That means a *partly*-fetched cache
either blocks a real return or silently uses a fallback — so the point of this
runbook is to fetch **completely** and **pin the source of every figure**, not
to lean on the fallbacks.

`tests/test_ty2026_readiness.py` is the M1 companion: it proves the ENGINE wiring
and computation for a fully-populated cache and pins each block's absence class.
It does NOT prove fetchability — the live fetch below is the operational
acceptance that a unit test cannot cover.

---

## 1. Fetch the year's law (`/tax-setup`, or by hand into `tax_law_<year>.json`)

Fetch every block, from the authoritative source, and write each figure with
the ingest command — `sh agents/lib/run.sh agents/lib/tax_law_fetcher.py set
<dotted.path> '<value>' --source '<url>' --year <year>` — never by hand into the
JSON. It validates the path and value against the schema seed, refuses a
non-authoritative host, records the URL under `_sources` (K2) and the retrieval
time and applicable period under `_provenance` (V4), so `freshness` can date
each block on its own. The sources are the IRS annual Revenue Procedure(s) and
the enacting statute; state figures come from the state tax authority.

| Block | Source | Notes |
|-------|--------|-------|
| `filing_statuses` (brackets, standard deduction) | Rev. Proc. (annual inflation adjustments) | per status |
| `capital_gains_rates`, `qbi_threshold`, `child_tax_credit`, `social_security`, `additional_medicare_tax`, `niit`, `irmaa`, `hsa_limits`, `ira_limits`, `retirement_401k` | Rev. Proc. + statute | `irmaa` ships empty — fetch it |
| `salt_cap` `{amount, mfs_amount}` | OBBBA §70120 schedule (IRS Topic 503) | the year's amount BEFORE the MAGI phasedown: $40K/$20K-MFS TY2025, $40,400 TY2026, 101%/yr to TY2029, $10K in 2030; `/tax-ptet` falls back to statute, `compute_schedule_a` refuses None — PIN it |
| `amt.phaseout_rate` | §55(d)(3) as amended by OBBBA §70107 | 25% ≤TY2025, 50% TY2026+; statutory fallback exists — fetch only to OVERRIDE |
| `schedule_1a` (tips/overtime/car-loan/senior caps + phase-out starts) | OBBBA §§224/225/70103/70203 + annual indexing | **FAIL-CLOSED**: a nonzero Schedule 1-A input with this unfetched hard-errors |
| `charitable_non_itemizer` `{amount, joint_amount}` | §170(p) (OBBBA §70424) | **FAIL-CLOSED** on nonzero cash gift; $1,000/$2,000-MFJ |
| `qbi_minimum` `{income_threshold, amount}` | §70105 | loud-degrade (taxpayer-favorable) — fetch so the floor applies |
| `retirement_401k.secure_2_0_high_earner_threshold` | IRC §414(v)(7)(A) indexed | **FAIL-CLOSED for every covered year** — X10 removed the annual default; fetch the indexed threshold whenever the TY2026+ catch-up rule is evaluated |
| `underpayment_rates` (§6621 quarterly) | IRS quarterly interest-rate news releases | see the two-window note below |

### The `underpayment_rates` two-window rule (M3)

Two consumers need different spans of the §6621 rate timeline:

- **`compute_form_2210`** (estimated-tax penalty) accrues Apr 15 of TY through
  Apr 15 of TY+1.
- **`compute_underpayment_interest`** (§6601/§6622 interest on a late-paid
  balance) accrues from the TY+1 Apr-15 due date FORWARD to the pay date. An
  October extension filer needs the TY+1 Q2/Q3/Q4 quarters — beyond the 2210
  window.

When extensions are in play, extend the fetch forward through the quarter
containing the anticipated pay date. Future quarters aren't published at
season-open; both consumers refuse to guess (2210 degrades; the interest
estimator reports PARTIAL-through the last cached quarter), so re-fetch as the
IRS publishes each quarter.

### Statutory constants to re-verify annually (NOT in the cache)

These are hardcoded because they're bill-fixed, but they DO index or change —
confirm each against the year's guidance and update the constant if it moved:

- §6651(a) >60-day minimum penalty floor (Rev. Proc.; passed into
  `compute_late_filing_penalty`, never hardcoded there).
- SS wage base (SSA announcement) — drives excess-SS and Additional Medicare.
- The safe-harbor $150K/$75K-MFS AGI threshold, ACTC $2,500 floor + 15% rate,
  $500 ODC (statutory-fixed — confirm no legislative change).
- SALT cap reversion year (2030 under current law).

**Record the result here, with the source, every time.** A run that leaves no
trace is indistinguishable next year from a run that never happened, and the
constants that turned out to be RIGHT are most of the value — they are what
lets the next reader spend the time on the ones that moved.

#### Verified 2026-09-13 (X6)

Checked against primary authority, live. Every constant below is in the
engine today and every one of them was found CORRECT:

| Constant | Code | Authority | Result |
|---|---|---|---|
| §24(d)(1)(B)(i) refundability floor + rate | `ACTC_EARNED_INCOME_FLOOR` 2500, `ACTC_EARNED_INCOME_RATE` 0.15 | 26 USC §24(d)(1)(B)(i) | unchanged |
| §24(h)(4)(A) credit for other dependents | `ODC_PER_DEPENDENT` 500 | 26 USC §24(h)(4)(A) | unchanged |
| §6654(d)(1)(C)(i) prior-year AGI threshold | `PRIOR_YEAR_AGI_THRESHOLD` 150_000 / `_MFS` 75_000 | 26 USC §6654(d)(1)(C)(i) | unchanged |
| §6654(e)(1) de-minimis balance | `DE_MINIMIS_BALANCE` 1000 | 26 USC §6654(e)(1) | unchanged |
| §1211(b) capital-loss limit | `CAPITAL_LOSS_LIMIT` 3000 / `_MFS` 1500 | 26 USC §1211(b) | unchanged |
| SALT cap reversion year | `federal/salt.py` `base_cap` falls back at 2030 | 26 USC §164(b)(7) | unchanged |
| SS wage base TY2026 | not hardcoded anywhere; fetched | SSA contribution and benefit base | $184,500, matches the TY2026 eval golden |

Four things were WRONG, none of them a constant — every one was a figure or a
citation in PROSE, where nothing computes against it:

- `/tax-extend` mapped each Revenue Procedure to the wrong year. **A Rev. Proc.
  states the §6651(a) floor for the year AFTER its own tax year**: 2024-40 gives
  returns required to be filed in 2026 ($525), 2025-32 gives 2027 ($535). The
  note shifted both by one, sending a TY2025 return to the wrong document.
- `/tax-extend` quoted the floor as "~$510" — the figure for returns filed in
  2025, two cycles stale, in a skill whose very next sentence says not to reuse
  a prior year's figure. The figure is gone; the fetch instruction stays.
- `/tax-ptet` stated the SALT cap as a flat "$40,000 ($20,000 MFS) for
  TY2025–2029". §164(b)(7) steps it: $40,000 TY2025, **$40,400 TY2026**, then
  101% a year through TY2029. Under-stating the cap makes the PTET election look
  BETTER than it is — the over-statement direction, in the current filing year.
- The state-support roadmap cited §164(b)(6) for the OBBBA cap. (b)(6) is
  the superseded 2018–2025 limitation; the cap lives in (b)(7).

**The lint did not and could not catch any of them**, for two separate reasons
— one now fixed, one measured and deferred.

**SCOPE — FIXED.** The per-skill figure registry
(`test_the_taxpayer_facing_skills_quote_no_indexed_tax_figure`) named four
skills out of twenty-three by hand, and a skill nobody had added read exactly
like a skill with nothing to declare. Its scope is DERIVED now: every
taxpayer-facing skill must appear either in `_FIGURE_REGISTRY` or in
`_FIGURE_LINT_EXEMPT` with a reason, so a new skill fails until somebody looks
at its figures. Nine skills that quote nothing got an empty entry, each verified
empty rather than assumed — an empty entry is what puts a skill UNDER the lint,
so the first figure it grows has to be defended. `tax-extend` is the case that
proves the point: X6 took a stale §6651(a) floor out of it, and nothing would
have stopped the next one going back in.

`tax-ptet` is EXEMPT pending an owner ruling, named rather than quietly
registered. X6 corrected it from a flat "$40,000 for TY2025–2029" to the
§164(b)(7) schedule, and the skill now also names `federal.salt_cap`, the cache
key those figures are read from — which is exactly the remedy the lint's own
message prescribes. **The open question: may a skill state a figure that is both
statutorily NAMED and FETCHED?** Stating it is what let X6 replace a wrong flat
number; not stating it is what stops TY2027 reading "$40,400 for TY2026" as
current.

**DETECTOR — X9.** Wrapped prose is normalized within each Markdown block;
paragraphs, list entries, headings and table rows remain separate. Backtick
spans pair by delimiter length. Code identifiers and CLI flags cannot supply
limit words, while dollar amounts and their K/M multipliers remain visible.
The bounded sentence window checks every amount in a matched clause, including
both halves of §121 exclusions and joint/separate thresholds. Ordinary skills
and the safe-harbor closed registry share this detector.

The earlier experiments simply removed `\n` from the regex and mis-paired
backticks, trading a schema-field false positive for a lost §121 detection.
Regression tests now cover both directions: the original wrapped penalty
floor refuses, both home-sale exclusions survive, and the CLI `--max` false
alarm disappears. This remains a bounded prose lint, not a tax-law parser;
source freshness and the season's fetched figures still need the gates below.

#### Verified 2026-10-12 (Y2)

Steps 1-4 ran on a TY2026 cache written through `set`, each figure read from the
primary document's own text, never from a search engine's summary of it:

| Block | Source |
|---|---|
| `filing_statuses`, `capital_gains_rates`, `child_tax_credit` amount and refundable limit, `qbi_threshold`, `amt`, `qbi_minimum` | Rev. Proc. 2025-32 |
| `ira_limits`, `retirement_401k`, the §603 threshold included | Notice 2025-67 |
| `hsa_limits` | Rev. Proc. 2025-19; §223(b)(3) for the catch-up |
| `social_security.wage_base` | Publication 15 (2026); ssa.gov refuses automated requests |
| `irmaa.tiers` | CMS, 2026 Medicare Parts A & B premiums and Part D income-related amounts |
| `underpayment_rates` | IRS quarterly interest rates page, all four 2026 quarters |
| `salt_cap`, `child_tax_credit.phase_out_start`, `schedule_1a`, `charitable_non_itemizer` | 26 USC §§164(b)(7), 24(h)(3), 224, 225, 163(h)(4), 151(d)(5)(C), 170(p) |

Every bracket table was checked a second way: the engine's tax at each boundary
equals the base tax the document prints beside it, and moving one boundary by $50
fails that check. The constants in the X6 table above were re-read against their
statutes and are unchanged, and the §6651(a) floor for returns filed in 2027 is
$535 (Rev. Proc. 2025-32 §4.52). The state block follows the taxpayer's state and
filing status, so it is not recorded here.

Three things were not yet published, tracked as Y8 in `docs/roadmap/BACKLOG.md`. The
gate reads INCOMPLETE on the last two, and each is fetched when it appears:

- the 2027 §6621 quarters, which Form 2210's window and an extension's interest
  window both reach;
- the IRS yearly average exchange rates for 2026, in January;
- state figures that publish with the year's full tables rather than an early
  indexing notice.

One nuance for whoever reads IRMAA next: CMS's lower tiers begin at "greater
than" their threshold and the top tier at "greater than or equal to", as 42
U.S.C. §1395r(i)(3)(C) says "More than" and "At least". `roth_ladder` applied
every tier at `>=`, which charged a MAGI of exactly a lower threshold one tier
early; it now follows the statute tier by tier, the top one included.

---

### The State and City tax-table bases (AA14)

When the year's New York State and City tax tables publish, compare each table's
rows with its rate schedule and fetch `state.tax_table.table_bases` and
`state.nyc_tax.tax_table.table_bases`. A cache without them gets a note, not a
refusal, and a table row can then be a dollar off in either direction.

### The Form 8949 boxes for digital assets (AA48, AA55)

`/tax-parse-crypto` routes a broker-reported disposal to a Form 8949 box only for
a year with its own reviewed entry in `_REPORTING_REGIMES`
(`agents/lib/crypto_importer.py`), and any other year's import with a
filing-year disposal blocks. Every season therefore needs the new year's entry,
read from that year's Form 8949 instructions, and a re-read of the Form 1099-DA
boxes the import maps, against that year's 1099-DA instructions.
`docs/contributing/crypto-accounts.md` holds the box table, the year-pinned
source rule and the 1099-DA map. The two documents publish separately, and
either may still be missing when the season opens: check for each again with
every later fetch until both are out, and add the entry when the Form 8949
instructions appear. TY2026's are AA48 and AA55 in `docs/roadmap/BACKLOG.md`.

## 2. Pin every source (K2)

Every fetched block records its authoritative URL in `tax_law_<year>.json`
`_sources`, and — written through `set` — its retrieval time in `_provenance`.
Then run the pinning verifier:

```bash
sh agents/lib/run.sh -c "from agents.lib.tax_law_fetcher import verify_source_pinning; \
  import json; print(verify_source_pinning(json.load(open('tax_law_<year>.json'))))"
```

Acceptance: no `non_authoritative` source (a non-`.gov` / non-allowlisted host
taints the return → INCOMPLETE) and no core block left `unpinned`. A `.gov`
lookalike (`irs.gov.evil.com`) resolves to a non-`.gov` host and is rejected.

## 3. Prove the wiring

```bash
source .venv/bin/activate
python3 -m pytest tests/test_ty2026_readiness.py -v   # M1 golden path + absence matrix
sh agents/lib/run.sh -m agents.lib.tax_computations  # engine self-test
python3 -m pytest tests/ -q                            # full suite
```

## 4. Freshness gate

`check_freshness(<year>)` must not report any Epic-I block as INCOMPLETE. A
FRESH populated cache is the green light to open the season; an INCOMPLETE one
names the block(s) still to fetch.

## 5. Make any queued definition edits, then re-run the review-subagent acceptance

**Point it at an exported copy, never at the workspace** (V7):

```bash
sh agents/lib/run.sh agents/lib/tax_law_export.py <year>
```

The command prints a fresh directory holding two files: the cache, copied VERBATIM, and
`schema.json`, the seeds, source-host rule and state regimes the auditor checks it
against, generated from the engine. Nothing is filtered out of the copy, because the auditor's items 6 and 8
are findings ABOUT keys the schema does not define and an export that removed them would
be answering for it. It refuses instead — exit 2, no copy — when the cache holds anything
SSN- or credential-shaped, which a cache fetched from `.gov` sources has no business
holding. Exit 1 is "could not run".

**The schema travels with the copy, so the definition names nothing in the clone** (Y2).
It used to tell the agent to read `agents/lib/tax_law_fetcher.py`, and with `TAX_WORKSPACE`
unset the clone IS the workspace: the instruction that handed it the schema handed it the
directory holding `taxpayer_profile.json`. What is still not enforced is where the agent
CAN read. `Read` is not directory-confined, and a dispatch from a session started at the
repository root, the only place `.claude/agents/` loads, runs with the clone as its
working directory. The definition's scope sentence is what keeps it out, which is
prompt-level, and the acceptance record's contamination check is what notices a breach.
So the one layer under it is where the taxpayer's files ARE: **dispatch from a clone that
holds none of them**, the return kept in a `TAX_WORKSPACE` outside the checkout or the
session started in a second clone used for nothing else, and the directory the agent
starts in has nothing to find. Name the printed directory to the agent and nothing else,
and delete it when the audit is done: the command prints the `rm -rf` for it, and nothing
else cleans it up.

`tax-law-freshness-auditor` is the only check on the freshness contract this runbook
exists to satisfy, so this runbook LEANS ON it and must not be the thing that first
discovers it has regressed. A definition edit queued for this moment has to be made and
scored in the same sitting: the acceptance record digest-pins the definition bytes, so an
edit without a replay leaves the record certifying something that is no longer there.

**The whole chain, in one place:** fetch and gate (steps 1-4 above) → make any queued
definition edits → a FRESH session → replay both reviewers → re-record the verdicts and
digests. Every link is below; none is a decision, and the first is gated only on the
year's figures publishing.

**Do these in order, and do not split them across sittings.** A replay run before the
fetch scores the auditor against last year's figures and proves nothing about this year's;
a definition edited in the session that replays it is not what the replay tested, because
a definition loads at SESSION START.

1. **The fetch and the freshness gate above are already done.** If they are not, stop:
   steps 1-4 come first, and this step is worthless before them.
2. **Make the queued edits, if any are listed here.** One is queued, with the test change
   it needs, until AA52 lands both with a replay of its own: name `schema.json`'s
   `retirement_conformity_seed`, which the export writes whenever the cache holds that
   block, beside `cache_seed` and `state_seed`, and in the same edit build `SCHEMA_KEYS`
   in `tests/test_claude_agents.py` from a cache holding every optional block. That test
   holds the definition's names and the export's keys to each other in both directions,
   so either half alone turns it red. If AA52 is still open when this sitting comes,
   make both here; its engine fixes stay in the backlog. Y2's sitting (2026-10-12) made
   the five queued before it: the `_provenance` check (V4), the item-7 keying note (X1),
   the scope paragraph and schema reads pointed at the export (V7), Replay B handed an
   exported directory instead of a file path, and item 5's `federal.salt_cap` check
   (AA19).
   AA51's four edits followed with a sitting of their own (2026-10-13), the precedent
   for replaying between seasons. Queue a later edit by listing it here, never by
   editing the definition ahead of its replay.
3. **Start a FRESH session** at the repository root. Not a new sub-agent, not a cleared
   context — a new session, because that is when the edited definition is loaded.
4. **Replay both reviewers** exactly per `docs/ops/review-subagent-acceptance.md`: its
   pre-committed criteria, its known answers, and its traps (a squash merge leaves nothing
   in `<sha>^`, so `--grep=P1` is the wrong search).
5. **Re-record the verdicts and the new digests** in that record, with the date, in the
   same session that ran them.

An acceptance run scored against an old cache proves nothing about the new year's figures,
so this step goes AFTER the fetch and the freshness gate, never before them.

---

## When you add a NEW fetched block in a future epic

Register it in all the places the invariant requires (mirroring Epic I):
`create_empty_tax_law` seed, `/tax-setup` fetch bullet + `_sources` pin,
`check_freshness` treatment (fail-closed / opt-in / statutory-fallback),
`tests/test_ty2026_readiness.py` present + absence cases, and a row in this
runbook's table. A block a user's return can depend on but this runbook doesn't
list is the exact gap M1/M2 exist to close.
