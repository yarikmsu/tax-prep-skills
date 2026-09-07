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
| `salt_cap` `{amount, mfs_amount}` | OBBBA §70120 schedule (IRS Topic 503) | $40K/$20K-MFS TY2025-2029, reverts $10K in 2030; statutory fallback exists but PIN it |
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

---

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
sh agents/lib/run.sh agents/lib/tax_computations.py   # engine self-test
python3 -m pytest tests/ -q                            # full suite
```

## 4. Freshness gate

`check_freshness(<year>)` must not report any Epic-I block as INCOMPLETE. A
FRESH populated cache is the green light to open the season; an INCOMPLETE one
names the block(s) still to fetch.

## 5. Make the queued definition edits, then re-run the review-subagent acceptance

`tax-law-freshness-auditor` is the only check on the freshness contract this runbook
exists to satisfy, so this runbook LEANS ON it and must not be the thing that first
discovers it has regressed. It also carries the two definition edits that were
deliberately DEFERRED to this moment, and they have to be made and scored in the same
sitting — the acceptance record digest-pins the definition bytes, so an edit without a
replay leaves the record certifying something that is no longer there.

**Do these in order, and do not split them across sittings.** A replay run before the
fetch scores the auditor against last year's figures and proves nothing about this year's;
a definition edited in the session that replays it is not what the replay tested, because
a definition loads at SESSION START.

1. **The fetch and the freshness gate above are already done.** If they are not, stop:
   steps 1-4 come first, and this step is worthless before them.
2. **Make the two queued edits** to `.claude/agents/tax-law-freshness-auditor.md`:
   - the `_provenance` check (V4, `docs/changelog/epic-V.md`): the auditor reads
     `_sources` today and cannot see WHEN a block was retrieved, which is exactly the
     staleness it exists to catch;
   - the item-7 keying note (X1, `docs/changelog/epic-X.md`).
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
