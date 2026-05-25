# `/tax-rollover` — Design Spec

**Status:** Draft, awaiting user approval
**Date:** 2026-05-24
**Author:** Brainstorming session — multi-perspective review of TY2026 planning features
**Roadmap source:** `docs/roadmap/2026-tax-planning-features.md` (item #1 of 12)
**Implements:** Foundation for backlog items #2, #3, #6, #7, #12

## Purpose

Eliminate the friction of typing prior-year carryforward numbers from memory during `/tax-setup`. Today the toolkit asks the user to recall capital loss carryforwards, QBI loss carryforwards, FTC carryforwards, and prior-year overpayments from last year's return — accurate recall depends on the user digging up the prior PDF.

This spec introduces an **archive-then-rollover** workflow that pulls those numbers (plus static identity, dependents with age tick, and a list of recurring payers) directly from the prior year's `result.json`. Rollover is the foundation for every multi-year planning feature in the backlog: scenario modeling (#3), Roth conversion ladders (#6), Form 8606 basis tracking (#7), and the year-over-year diff report (#12) all need a structured prior-year baseline to read from.

## Scope

### In scope (v1)

- New skill `/tax-archive {year}` — moves current-year files into `archive/{year}/`
- New skill `/tax-rollover {year}` — pulls four categories of data from `archive/{year}/` into the freshly initialized current year
- New Python module `agents/lib/archive.py` — validated file moves
- New Python module `agents/lib/rollover.py` — diff + merge engine with deterministic age arithmetic
- Modification to `/tax-setup` — refuses to run if prior-year files exist unarchived; auto-invokes `/tax-rollover` after setup if `archive/{year-1}/` exists
- New section `expected_sources` in `taxpayer_profile.json` (and template) — list of recurring payers seen in prior year
- Test suite: ~35 new tests across `tests/test_rollover.py` and `tests/test_archive.py`

### Out of scope (deferred)

- Importing prior returns from TurboTax / FreeTaxUSA exports (PDF or proprietary `.tax{YYYY}` formats)
- Rolling forward NOL carryforwards (toolkit doesn't compute NOL)
- Rolling forward AMT credit (toolkit doesn't compute AMT / Form 8801)
- Rolling forward 5-year charitable contribution carryover (toolkit doesn't itemize)
- Rolling forward Form 8606 non-deductible IRA basis (covered by backlog item #7)
- Splitting capital-loss carryforward into short-term vs. long-term sub-keys (v1.1 follow-up)
- Adding new dependents during rollover (only existing dependents age-tick; new additions stay in `/tax-setup` interview)

For each deferred carryforward, rollover surfaces a warning: *"If you had {NOL / AMT credit / charitable carryover / Form 8606 basis} on TY{N-1}, the toolkit doesn't track these yet — enter manually in the `prior_year` section."*

## User-facing flow

### Chronology for a returning user (late 2026, prepping TY2025)

```
1.  /tax-archive 2024
      ├─ Validates result.json.meta.tax_year == 2024
      ├─ Moves root tax files into archive/2024/
      ├─ chmod 600 each archived file
      └─ Prints: "Archived 6 items. Run /tax-setup 2025 next."

2.  /tax-setup 2025
      ├─ Step 0 (NEW): refuses if result.json exists at root
      ├─ Steps 1-8: as today (interview, fetch tax_law_2025.json)
      ├─ Step 9: carryforward questions rephrased as "we'll pre-fill these"
      ├─ Step 9.5 (NEW): if archive/2024/ exists → auto-invoke /tax-rollover 2024
      └─ Step 10: prints summary including rollover results

3.  /tax-rollover 2024 (re-runnable any time)
      ├─ rollover.py diff --from archive/2024 --to . --year 2025 --json
      ├─ Skill presents 4 grouped diffs via AskUserQuestion (one per category)
      ├─ User picks: [apply / skip] per category
      ├─ rollover.py apply --categories <selected>
      └─ Appends rollover log block to answers.md
```

### First-time user (no prior workspace)

`/tax-setup {year}` runs the full carryforward interview as today. `archive/{year-1}/` doesn't exist, so step 9.5 is a no-op. Path C from the data-flow walkthrough.

### Re-rollover (returning user runs `/tax-rollover` after setup already filled values)

Diff finds non-zero values already in the new-year files. Per-category AskUserQuestion surfaces conflicts: *"Capital loss c/f: prior=$3,400, current=$3,000 — apply?"* User decides per category whether to overwrite.

## Architecture

### Directory layout

```
Repo root (during TY2025 preparation)
├── archive/
│   └── 2024/                              ← created by /tax-archive 2024
│       ├── result.json
│       ├── taxpayer_profile.json
│       ├── tax_law_2024.json
│       ├── answers.md
│       ├── summaries/
│       └── documents/
├── result.json                             ← fresh TY2025 (post-rollover)
├── taxpayer_profile.json                   ← fresh TY2025 (post-rollover)
├── tax_law_2025.json
├── answers.md
└── agents/
    ├── lib/
    │   ├── archive.py                      ← NEW
    │   ├── rollover.py                     ← NEW
    │   └── tax_computations.py
    └── skills/
        ├── tax-archive.md                  ← NEW
        ├── tax-rollover.md                 ← NEW
        └── tax-setup.md                    ← MODIFIED
```

### New artifacts summary

| Artifact | Type | Approx. size |
|---|---|---|
| `agents/lib/archive.py` | Python module | ~80 LOC |
| `agents/lib/rollover.py` | Python module | ~300 LOC |
| `agents/skills/tax-archive.md` | Markdown skill | ~60 lines |
| `agents/skills/tax-rollover.md` | Markdown skill | ~120 lines |
| `agents/skills/tax-setup.md` | Existing skill, modified | +~30 lines (steps 0 and 9.5) |
| `agents/templates/taxpayer_profile_template.json` | Existing template, modified | +`expected_sources` section |
| `tests/test_rollover.py` | New test file | ~25 tests |
| `tests/test_archive.py` | New test file | ~10 tests |
| `tests/fixtures/rollover/` | New test fixtures dir | 4 fixture workspaces |

Six new files plus a fixtures directory, two modified.

## Components

### `agents/lib/rollover.py`

Pure functions with thin I/O wrappers at the edges. Public API:

```python
load_prior(archive_dir: Path, year: int) -> PriorReturn
compute_diff(prior: PriorReturn, current: CurrentState,
             categories: set[str], tax_law_new: dict
            ) -> dict[str, list[Change]]
apply_changes(changes: dict[str, list[Change]],
              category_decisions: dict[str, Action],
              targets: WritePaths) -> ApplyReport

# CLI entry points
python3 -m agents.lib.rollover diff  --from archive/2024 --to . --year 2025 --json
python3 -m agents.lib.rollover apply --from archive/2024 --to . --year 2025 \
                                     --categories carryforwards,identity,dependents,expected_sources
```

`Change` is a dataclass:

```python
@dataclass(frozen=True)
class Change:
    category: str              # "carryforwards" | "identity" | "dependents" | "expected_sources"
    json_path: str             # dotted path into target file
    target_file: str           # "result.json" | "taxpayer_profile.json"
    prior_value: Any
    current_value: Any
    action_suggested: str      # "apply" | "ask" | "skip"
    reason: str                # human-readable rationale shown to user
```

`action_suggested` semantics:
- `"apply"` — current target value is zero/empty AND prior value is non-zero/non-empty AND no warning-worthy transition (e.g. filing-status change, dependent age cutoff). Safe to apply without per-field confirmation.
- `"ask"` — surfaces individually in the AskUserQuestion. Triggered by: filing-status change, non-zero current value (re-rollover conflict), dependent crossing CTC/qualifying-child age boundary, spouse-section drop when filing status no longer permits MFJ/MFS/QSS.
- `"skip"` — schema drift (unknown source path), malformed source value (e.g. invalid DOB), or category deferred per the *Out of Scope* section.

### `agents/lib/archive.py`

```python
archive_year(year: int, root: Path, dry_run: bool = False) -> ArchiveReport

# CLI
python3 -m agents.lib.archive {year}
```

Moves: `result.json`, `taxpayer_profile.json`, `answers.md`, `tax_law_{year}.json`, `summaries/`, `documents/` → `archive/{year}/`. Refuses if `archive/{year}/` already exists. Refuses if `result.json.meta.tax_year != {year}`. Atomic: any partial-move failure rolls back. Sets `chmod 600` on archived files.

### `agents/skills/tax-archive.md`

Validates `{year}` is a 4-digit integer 2000–2099. Calls `python3 -m agents.lib.archive {year}`. Prints summary. Reminds user: *"Run /tax-setup {year+1} next."*

### `agents/skills/tax-rollover.md`

1. Validates inputs (year format, archive existence, current workspace state)
2. Calls `rollover.py diff --json`, parses output
3. For each of the 4 categories with changes, presents one `AskUserQuestion`:
   *"Apply N changes to {category}? (preview shown above)"* → apply / skip / cancel
4. Calls `rollover.py apply --categories <user-selected>`
5. Appends audit block to `answers.md` (no PII duplicated)
6. Prints final summary including any surfaced warnings (aged-out dependents, deferred carryforward reminders)

### `agents/skills/tax-setup.md` modifications

**Step 0 (new, before today's step 1):** If `result.json` exists at root AND `result.json.meta.tax_year != {requested year}`, refuse:

> *"Prior-year files detected at root (TY{X}). Run `/tax-archive {X}` first, then re-run `/tax-setup {year}`."*

**Step 9 (modified):** Carryforward interview rephrased — if `archive/{year-1}/` exists, the questions become "we'll pre-fill these from your TY{year-1} return — verify after step 9.5" rather than "type these from memory."

**Step 9.5 (new):** If `archive/{year-1}/` exists, auto-invoke `/tax-rollover {year-1}`. Otherwise skip silently.

## Field mapping (the core spec)

Codified as `FIELD_MAP` in `agents/lib/rollover.py`. Stable carryforward keys map to JSON paths so that schema renames in newer toolkit versions don't break rollover of older archived returns.

### Category 1: Carryforwards (dollar amounts)

| Carryforward key | Source (prior `result.json` path) | Target (current `taxpayer_profile.json` path) |
|---|---|---|
| `capital_loss_carryforward` | `schedule_d.line_16_combined` if net < 0, less $3K allowance used | `prior_year.capital_loss_carryforward` |
| `qbi_loss_carryforward` | `form_8995_qbi.line_16_qbi_loss_carryforward` | `prior_year.qbi_loss_carryforward` |
| `unused_ftc_carryforward` | computed from `form_1116_ftc` if Form 1116 filed (not simplified election) | `prior_year.unused_ftc_carryforward` |

For each deferred carryforward (`nol_carryforward`, `amt_credit_carryforward`, `charitable_5yr_carryover`, `form_8606_basis`, `prior_overpayment_applied`), the rollover skill prints a one-line warning telling the user to enter manually if applicable.

**Note on `prior_overpayment_applied`:** rolling this requires knowing how much of the prior year's overpayment the taxpayer elected to apply to the new year's estimated taxes (Form 1040 line 36). The current `result_template.json` captures `line_34_overpayment` and `line_35a_refund` but not `line_36_applied_to_next_year`. Until a follow-up PR adds that schema field and `/tax-compute` populates it, this carryforward cannot be rolled deterministically and is deferred.

**v1 simplification:** Capital loss carryforward rolls the combined net value only. The IRS rule strictly requires splitting short-term vs. long-term carryover. v1.1 follow-up: enhance `prior_year.capital_loss_carryforward` to split into `_short_term` and `_long_term` sub-keys.

### Category 2: Static identity

| Field | Conflict rule |
|---|---|
| `primary.{first_name, middle_name, last_name, ssn_last4, dob, occupation}` | Always apply unless `filing_status` changed |
| `spouse.*` | Apply only if new `filing_status` ∈ {MFJ, MFS, QSS}. Drop if Single/HoH. Surface warning. |
| `address.{street, city, state, zip}` | Always apply; user re-confirms during `/tax-setup` |
| `filing_status` | **Always ask** — even HoH ↔ Single materially affects the return |

### Category 3: Dependents with age tick

For each dependent in prior `taxpayer_profile.dependents`:
1. Copy `first_name`, `last_name`, `ssn_last4`, `dob`, `relationship`
2. Compute age at Dec 31 of new tax year
3. Set `ctc_eligible` based on `tax_law_{new_year}.json` CTC age threshold (typically <17)
4. Default `months_lived_with_you = 12` (user verifies)
5. Surface warning when:
   - Dependent turned 17 (lost CTC; may now qualify for $500 Credit for Other Dependents)
   - Dependent turned 19 or 24 (qualifying-child age cutoffs)
6. New dependents are **not** added — that stays in the `/tax-setup` interview

### Category 4: Recurring entities (NEW SCHEMA)

Adds one new section to `taxpayer_profile.json` and `taxpayer_profile_template.json`:

```json
"expected_sources": {
  "_comment": "Payers/institutions seen in prior year. Informs /tax-parse to flag missing documents. EINs are public business identifiers — full values stored (not last-4-only like individual SSNs).",
  "w2_employers":     [{"name": "...", "ein": "..."}],
  "interest_payers":  [{"name": "...", "country_code": "US"}],
  "dividend_payers":  [{"name": "..."}],
  "brokerages":       [{"name": "..."}],
  "foreign_accounts": [{"institution": "...", "country_code": "...", "account_last4": "..."}]
}
```

**EINs stored in full** — they're public identifiers already printed on every W-2 and are not subject to the SSN minimization policy. Storing full EINs makes `/tax-parse` matching unambiguous when employers have similar names.

Sourced from prior `result.json`:
- `w2_summary.employer` + `w2_summary.employer_ein` → `w2_employers[]`
- `interest_detail.items[].payer` → `interest_payers[]`
- `dividend_detail.items[].payer` → `dividend_payers[]`
- `form_8938_fatca.accounts[]` → `foreign_accounts[]` (deduplicated by institution + country + last4)
- `fbar_fincen_114.accounts[]` → also feeds `foreign_accounts[]`

**Consumed by** `/tax-parse`: prompts *"Last year you had a 1099-INT from {payer}. Did you receive one this year?"* No dollar amounts imported — purely a checklist for completeness.

## Data flow scenarios

### Path A — Happy path (chronology above)

Archive → setup → auto-invoked rollover → user approves all categories → done.

### Path B — Standalone re-rollover

User finished `/tax-setup 2025` weeks ago, then amended TY2024. Re-runs `/tax-rollover 2024`. Diff finds non-zero current values; AskUserQuestion shows per-category conflicts; user picks which categories to overwrite.

### Path C — First-time user, no prior workspace

`/tax-setup {year}` proceeds normally. Step 9.5 detects no `archive/{year-1}/` and skips silently. The interview path is preserved.

### Path D — Schema drift across years

Prior `result.json` was produced by an older toolkit version (e.g., field renamed from `box_f` to `box_c` per `CLAUDE.md` accuracy-audit note). `FIELD_MAP` lookups by stable carryforward key; unknown source paths logged as `"skipped: unknown field {path}"`; missing expected paths default to 0 with `"missing: {key}, defaulting to 0"` log. Diff completes; warnings surface in the skill's final summary.

## Error handling

### `/tax-archive` failure modes

| Scenario | Behavior |
|---|---|
| `archive/{year}/` already exists | Refuse. *"archive/{year}/ exists. Move or delete it first."* No partial moves. |
| `result.json` missing at root | Refuse. *"No result.json at root — nothing to archive."* |
| `result.json.meta.tax_year != {year}` | Refuse. *"Root result.json is for TY{X}, not TY{year}. Pass the correct year."* |
| Some expected files missing (no `summaries/`) | Archive what exists; print log of moved + skipped. Succeeds. |
| Permission error during move | Roll back any partial moves; restore original state; exit non-zero. |

### `/tax-rollover` failure modes

| Scenario | Behavior |
|---|---|
| `archive/{year}/` doesn't exist | Refuse. *"No archive at archive/{year}/. Run /tax-archive {year} first, or supply --from."* |
| `archive/{year}/result.json` malformed JSON | Refuse with parse error line. |
| Prior `result.json.meta.tax_year` missing or != directory year | Refuse. *"Archive at archive/{year}/ is for TY{X}, not TY{year}. Refusing to roll inconsistent data."* |
| Current root `result.json` missing | Refuse. *"No current result.json. Run /tax-setup {new_year} first."* |
| Current root same year as archive | Refuse. *"/tax-rollover rolls forward, not sideways."* |
| Prior file has unknown JSON fields (schema drift forward) | Ignore + log; diff continues |
| Prior file missing expected fields (schema drift backward) | Default to 0/empty + log; diff continues |
| Dependent DOB malformed | Skip that dependent's age tick; log warning; do not skip category |
| User cancels all categories | No-op. No files written. *"No changes applied."* |

### Atomic write guarantee

`apply_changes()` writes to `*.tmp` files first, validates all writes succeed, then `os.replace()`-renames to final paths. If any step fails, all `.tmp` files are unlinked and the workspace is left untouched. There is no intermediate state where some categories applied and others didn't.

### Input validation (security)

- `{year}` argument: 4-digit integer, 2000–2099 (matches existing `/tax-setup` policy)
- `--from` path: must resolve under workspace root via `Path.resolve().is_relative_to(workspace)`; rejects `../` traversal
- Shell metacharacters in any path argument rejected before subprocess call (matches `tax-setup.md:103` policy)

### File permission preservation

Every file written by rollover.py is followed by `os.chmod(path, 0o600)`. archive.py preserves the same on moved files. Matches the PII security policy in `CLAUDE.md`.

### Audit trail in `answers.md`

After successful apply, append:

```markdown
## Rollover from TY2024 — applied 2026-05-24
Categories applied: carryforwards, identity, dependents, expected_sources
Fields updated: 14
Warnings: Dependent Alice (DOB 2009-03-15) turned 17 — no longer CTC-eligible
```

Field values themselves are **not** logged to `answers.md` (avoid duplicating PII into a second file). Field-level detail is shown only on stdout during the conversation.

## Testing

### `tests/test_rollover.py` (~25 tests)

**Fixtures** at `tests/fixtures/rollover/`:
- `prior_2024_clean/` — synthetic MFJ return: $4K capital loss carryforward, one foreign account, two dependents (age 16 and 21), one W-2 employer
- `prior_2024_drift_forward/` — same data plus an unknown extra field (simulates older toolkit version)
- `prior_2024_drift_backward/` — missing `form_8995_qbi` section entirely
- `current_2025_empty/` — freshly initialized TY2025 workspace
- `current_2025_partial/` — TY2025 with some carryforward fields manually filled

**Test groups:**

| Group | Tests |
|---|---|
| `FIELD_MAP` integrity | Every entry resolves to a real JSON path. Every entry has `category` and `action_suggested`. No duplicate source paths. |
| `compute_diff` carryforwards | $4K cap loss in prior → diff shows it. Zero prior → no diff. QBI loss c/f rolls. FTC c/f only rolls when Form 1116 was filed (not simplified). |
| `compute_diff` identity | Names roll. MFJ→MFJ silent; MFJ→Single drops spouse fields + warning. |
| Age tick | 16→17: `ctc_eligible: false` + warning. 21→22: no change. 23→24: qualifying-child cutoff warning. Malformed DOB: skip + log, no crash. |
| Recurring entities | W-2 employer with EIN carried over. Foreign account dedup by institution + country + last4. |
| Schema drift | Forward drift: completes, unknown fields logged. Backward drift: defaults to 0, logged. |
| Conflict handling | Diff against `current_2025_partial` flags `current_value != 0` per field. |
| `apply_changes` atomicity | Inject I/O failure on second-to-last write → all `.tmp` files unlinked, originals unchanged. (`monkeypatch` `os.replace`.) |
| `apply_changes` partial categories | Apply carryforwards only → identity untouched. |
| `apply_changes` no-op | Empty category selection → no files written. |
| Security | `--from /etc/passwd`, `--from ../../outside`, year `'2025; rm -rf /'` all rejected. |
| File permissions | After apply, every written file is mode 0o600. |
| Audit log | `answers.md` gets the rollover block; PII not duplicated. |

### `tests/test_archive.py` (~10 tests)

| Test |
|---|
| Happy path: root files move into `archive/2024/`, root left clean |
| Refuses if `archive/2024/` exists |
| Refuses if `result.json.meta.tax_year != requested year` |
| Refuses if `result.json` missing at root |
| Partial file set: archives what exists, skips what doesn't, succeeds |
| Atomicity: injected failure mid-move rolls back all moves |
| `chmod 600` on every archived file |
| Path validation: rejects `..` traversal in any input |
| Year validation: rejects non-4-digit, out-of-range values |
| Year mismatch error message names both years |

### Integration tests (3 tests in `tests/test_rollover.py`)

| Test |
|---|
| Full pipeline: synthetic `archive/2024/` + `current_2025_empty/` → run `rollover.py diff` via subprocess → parse JSON → run `rollover.py apply` → final files match expected fixture |
| Pipeline with drift forward: succeeds, logs warning |
| `/tax-setup` integration: mock root `result.json` with wrong year → setup exits with helpful message |

### CI updates

- `tests/fixtures/rollover/` committed (no PII; synthetic data only)
- `CLAUDE.md` test-count references bumped from 90 to ~125 in the same PR
- `.github/workflows/ci.yml` unchanged (already runs `pytest tests/ -v` recursively)
- Coverage target: `compute_diff`, `apply_changes`, age-tick math, `FIELD_MAP` → 100% line + branch

## Open follow-ups (post-v1)

- **v1.1:** Split capital-loss carryforward into short-term and long-term sub-keys
- **Schema follow-up:** Add `form_1040.line_36_applied_to_next_year` to `result_template.json`; update `/tax-compute` to populate it; re-enable `prior_overpayment_applied` in `FIELD_MAP`
- **Backlog #7:** Form 8606 non-deductible IRA basis tracking — once landed, add to rollover scope
- **Backlog #3:** Scenario modeling reuses `compute_diff` and `apply_changes` infrastructure
- **Backlog #12:** YoY diff report reuses `load_prior` + a new `summarize_yoy` function
- **TurboTax / FreeTaxUSA import:** standalone follow-up feature for first-time users with prior paper returns
