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

## Indexed per year, so a carried-over value goes stale silently

- `salt_cap` — the OBBBA base indexes roughly 1%/yr for 2026-2029. `ptet._salt_cap`
  prefers the cache and falls back to the unindexed statutory schedule when it is unset.
- `child_tax_credit.refundable_limit` — the indexed per-child ACTC cap that
  `compute_child_tax_credit`'s refundable split requires.

## What is NOT in the cache

AGENTS.md §8 lists the statutory-fixed thresholds that are hardcoded constants, and it
lists them there rather than here because a FIGURE stated in two files is two files to
change when it moves — the failure §2 says has no downstream check. They are constants
on purpose: moving them into the cache would make an unindexed figure look like one that
needs fetching.
