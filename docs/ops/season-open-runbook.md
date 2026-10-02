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
| `retirement_401k.secure_2_0_high_earner_threshold` | IRC §414(v)(7)(A) indexed | **FAIL-CLOSED for the year AFTER the built-in default's year** — the $150K built-in is TY2026-only; TY2027+ MUST fetch |
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

**DETECTOR — measured, deferred.** `[^.\n]` between the limit word and the
amount cannot cross a LINE BREAK, so any figure that markdown happened to wrap
onto the next line is invisible — which is exactly why "~$510" survived. Adding
floor words (`minimum`, `floor`) alone changes nothing, because distance was
never the problem. Allowing the wrap reveals it, and also makes the detector
read backticked CACHE FIELD NAMES (`min`/`max`) as limit words, firing on a
worked example in `/tax-setup` that is correct. Blanking backticked spans fixes
that and LOSES two detections — one a false positive worth losing (`--max` read
as the word "maximum") and one a real §121 hit that must be kept. Masking only
the LETTERS inside backticks preserves distances and keeps §121, but backtick
MIS-PAIRING across a line leaves both artifacts standing. This is its own slice
with its own cry-wolf validation in both directions, not a regex tweak.

---

### The State and City tax-table bases (AA14)

When the year's New York State and City tax tables publish, compare each table's
rows with its rate schedule and fetch `state.tax_table.table_bases` and
`state.nyc_tax.tax_table.table_bases`. A cache without them gets a note, not a
refusal, and a table row can then be a dollar off in either direction.

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

## 5. Make the queued definition edits, then re-run the review-subagent acceptance

**Point it at an exported copy, never at the workspace** (V7):

```bash
sh agents/lib/run.sh agents/lib/tax_law_export.py <year>
```

**This isolates nothing unless `TAX_WORKSPACE` is set** (Y4). Unset, the workspace IS the
clone, and the auditor's definition tells it four times to read
`agents/lib/tax_law_fetcher.py` for the schema — so handing it the schema hands it the
directory holding `taxpayer_profile.json`. Set the variable, or the export is a tidier
input rather than a boundary.

The command prints a fresh directory holding one file, copied VERBATIM: nothing is
filtered out, because the auditor's items 6 and 8 are findings ABOUT keys the schema does
not define and an export that removed them would be answering for it. It refuses instead
— exit 2, no copy — when the cache holds anything SSN- or credential-shaped, which a
cache fetched from `.gov` sources has no business holding. Exit 1 is "could not run".

Launch the auditor with that directory as its working directory and do not name the
workspace to it. **The Agent tool has no `cwd` parameter**, so this means the PARENT
session's working directory: `cd` there before dispatching, or run the dispatch from a
session started in it. Delete the directory when the audit is done — the command prints
the `rm -rf` for it, and nothing else cleans it up.

**Two things this procedure does not yet have, and both belong in step 5 below.** The
auditor's "What to check" tells it to read `agents/lib/tax_law_fetcher.py` for the schema,
the source-host policy and the fetched-block derivation; with the cwd moved, none of those
resolve, so those instructions need rewording in the same sitting as the other queued
edits. And Replay B in `docs/ops/review-subagent-acceptance.md` builds its own fixture and
hands the auditor a FILE PATH, so it bypasses the export entirely and will keep passing
whether or not this works — the replay should hand it an exported directory, and changing
that changes what the next replay tests, so it is queued too rather than done here.

`tax-law-freshness-auditor` is the only check on the freshness contract this runbook
exists to satisfy, so this runbook LEANS ON it and must not be the thing that first
discovers it has regressed. It also carries the two definition edits that were
deliberately DEFERRED to this moment, and they have to be made and scored in the same
sitting — the acceptance record digest-pins the definition bytes, so an edit without a
replay leaves the record certifying something that is no longer there.

**The whole chain, in one place (Y2):** fetch and gate (steps 1-4 above) →
make the queued definition edits below → a FRESH session → replay both
reviewers → re-record the verdicts and digests. Every link is below; none is a
decision, and the first is gated only on the year's figures publishing.

**Do these in order, and do not split them across sittings.** A replay run before the
fetch scores the auditor against last year's figures and proves nothing about this year's;
a definition edited in the session that replays it is not what the replay tested, because
a definition loads at SESSION START.

1. **The fetch and the freshness gate above are already done.** If they are not, stop:
   steps 1-4 come first, and this step is worthless before them.
2. **Make the queued edits** to `.claude/agents/tax-law-freshness-auditor.md`, and the one to the acceptance record's Replay B fixture:
   - the `_provenance` check (V4, `docs/changelog/epic-V.md`): the auditor reads
     `_sources` today and cannot see WHEN a block was retrieved, which is exactly the
     staleness it exists to catch;
   - the item-7 keying note (X1, #89, `docs/changelog/epic-X.md`): the definition says
     `capital_gains_rates` is keyed ONLY `single`/`mfj`, and #89 keyed both its limits
     by all four statuses; `child_tax_credit.phase_out_start` and `qbi_threshold` are
     still the two-status ones. Left as is, the auditor reports a correct `mfs`/`hoh`
     figure as a keying error;
   - the scope-limits paragraph AND the four schema reads in "What to check" (V7):
     the scope sentence says "read `tax_law_<year>.json` and nothing else in the
     workspace", and there is no workspace any more — the auditor is pointed at an
     exported copy holding nothing else. The four reads of
     `agents/lib/tax_law_fetcher.py` (the schema, `_source_host_authoritative`, the
     fetched-block derivation) do not resolve from that directory either, and with
     `TAX_WORKSPACE` unset they resolve to the clone, which IS the workspace — the
     boundary this epic exists to build, undone by the agent's own instructions. Reword
     both together; a scope sentence describing a directory the agent is not in teaches
     it to go looking for one.
   - **Replay B's fixture** in `docs/ops/review-subagent-acceptance.md`: it hands the
     auditor a file path and so bypasses the export. Hand it an exported directory, or
     the procedure that scores this agent proves nothing about the isolation.
   - **Item 5's `federal.salt_cap`** (AA19): it says the amount "indexes roughly
     1%/yr" and that an absent value degrades quietly. Both are false since AA16 —
     §164(b)(7)(A) NAMES each year's amount ($40,000 TY2025, $40,400 TY2026, 101%
     a year to TY2029), and `compute_schedule_a` refuses a missing cap. Replace
     them with the check that matters: a cached amount that differs from the
     statute's, which is what a filer's already-REDUCED cap fetched as the year's
     amount looks like.
   Do NOT touch `docs/ops/review-subagent-acceptance.md` yet.
3. **Start a FRESH session.** Not a new sub-agent, not a cleared context — a new session,
   because that is when the edited definition is loaded.
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
