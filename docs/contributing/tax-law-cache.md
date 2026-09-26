# Reading `tax_law_<year>.json`

Relocated out of `AGENTS.md` §8 by Y5 (PR #113), which keeps the rules. The schema
itself is authoritative and §8 names where it lives; everything below is a map, and a
map goes stale in a way the schema cannot.

## Key paths

- `federal.filing_statuses.<single|mfj|mfs|hoh>.{brackets,standard_deduction}`
- `federal.capital_gains_rates.{zero_pct_limit,fifteen_pct_limit}`
- `federal.child_tax_credit.phase_out_start`
- `federal.additional_medicare_tax.threshold`
- `federal.niit.threshold`
- `federal.irmaa.tiers.<single|mfj>` — a list of
  `{magi_threshold, annual_surcharge_per_person}`
- `federal.salt_cap` — `{amount, mfs_amount}`
- `state.{brackets,standard_deduction}`

## The keying is not uniform, and the wrong guess reads a real number

An accessor that assumes four filing statuses everywhere does not fail — it silently
reads a key that exists for a DIFFERENT taxpayer, which is the shape AGENTS.md §4 calls
a hard error when it over-states.

| Block | Statuses it is keyed by |
|---|---|
| `filing_statuses`, `capital_gains_rates` | all four |
| `additional_medicare_tax.threshold`, `niit.threshold` | `single`, `mfj`, `mfs` |
| `child_tax_credit.phase_out_start`, `qbi_threshold` | `single`, `mfj` only |

For the two-key blocks, map MFJ/QSS → `mfj` and everything else → `single`.

`filing_statuses` and `capital_gains_rates` have all four, and that is a fix rather than a fact: X1 found the breakpoints collapsing to `single`, which handed an MFS or HoH filer another taxpayer's 0%/15% limits. A test pins this sentence.

`irmaa.tiers` ships EMPTY, and §8 carries the obligation that follows. The degrade is
in the under-stating direction and banners itself, which is why an unfetched cache is a
loud gap rather than a silent wrong number.

## New York's block is keyed by RATE SCHEDULE, not by filing status

`state.brackets` for New York is a DICT of §601 rate schedules — `a`, `b`, `c` — and
not a flat list of bands the way California's is. Three filing statuses share one
schedule and no status can be read off the bands themselves, so
`state.bracket_schedule_by_status` carries the mapping and is fetched beside them.

**A non-empty dict is TRUTHY**, so every `state.get("brackets") or []` gate passed a
New York cache straight through to code that iterates a list of dicts, where the
schedule KEYS arrive as strings. The one reader that knows both shapes is
`tax_computations.state_brackets_for_status`; a structural sweep forbids binding the
raw read anywhere else, while a boolean `if` stays fine.

Four more New York keys read wrong if they are audited as though they were California's:

- `tax_table_benefit_recapture` is **year-windowed** — §601's (d) subsections are each
  scoped to a range of years — and carries a per-schedule `entry_tier` and `tiers`, so
  a source that does not name the year window proves nothing about the figure.
- `tax_table` is a SEPARATE published method from the rate schedule, selected on BOTH
  NYAGI and taxable income, and its row widths are NOT constant at the bottom.
- `mctmt` has TWO ZONES carrying DIFFERENT rates, with a per-zone CLIFF threshold.
  Neither zone's rate stands in for the other's.
- `estimated_tax_safe_harbor` holds New York's OWN shares and thresholds, keyed to
  **New York** AGI. Verifying them against §6654 verifies the wrong statute.

`personal_exemption.amount` is **0 on purpose** — §616 grants an exemption for each one
allowed under IRC §151(c), the DEPENDENT subsection, and does not reference §151(b) —
so it is an affirmative declaration, not an unfetched field, and reporting it as one
fires on correct data.

## Indexed per year, so a carried-over value goes stale silently

- `salt_cap` — not indexed, but it changes every year all the same, so it goes
  stale the same way. §164(b)(7) steps the cap: $40,000 for TY2025, $40,400 for TY2026,
  101% of the prior year through TY2029, then $10,000. The cache carries the
  amount BEFORE the per-return MAGI phasedown, which `tax_computations/federal/salt.py`
  applies for both the return's Schedule A and `/tax-ptet`. `ptet._salt_cap` prefers the
  cache and falls back to that schedule, which is the STATUTE's own and not an
  inference — the section carries no inflation index, which is why AGENTS.md §2
  lists it as an enumerated exception rather than requiring a fetch. The published
  IRS figure for a stepped year is still authority over arithmetic from an anchor,
  so a populated cache wins. Fetch it.
- `child_tax_credit.refundable_limit` — the indexed per-child ACTC cap that
  `compute_child_tax_credit`'s refundable split requires.

## What is NOT in the cache

The statutory-fixed thresholds are hardcoded constants and live HERE, in one place:
the safe-harbor $150,000/$75,000, the ACTC $2,500 floor and its 15% rate, and the $500
ODC. AGENTS.md §8 used to carry the list and now points at this file — a FIGURE stated
in two files is two files to change when it moves, the failure §2 says has no
downstream check, and the instruction file keeps the ALARM rather than the list (§9).

They are constants on purpose: moving them into the cache would make an unindexed
figure look like one that needs fetching. Each is pinned to its engine constant by
test, which is the arrangement AGENTS.md §2's enumerated exceptions all ride on.
