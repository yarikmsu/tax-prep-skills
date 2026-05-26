# YoY What-Changed Report — Design Spec

**Status:** Draft, awaiting user approval
**Date:** 2026-05-25
**Author:** Brainstorming session — TY2026 planning backlog
**Roadmap source:** `docs/roadmap/2026-tax-planning-features.md` (item #12 of 12)
**Builds on:** `/tax-rollover` (item #1) — reuses `load_prior` + `PriorReturn` from `agents/lib/rollover.py`

## Purpose

Give the taxpayer the single artifact they most want to see at the end of a return: *what changed since last year, and by how much.* Today `/tax-review` produces a thorough audit-risk + optimization report but says nothing about year-over-year movement. A CPA reviewing a return always opens last year's return alongside this year's and eyeballs the deltas — AGI, bracket, effective rate, and any line that moved materially. This feature automates that eyeball pass into a deterministic, scannable table embedded in the existing `/tax-review` output.

## Scope

### In scope (v1)

- New Python module `agents/lib/yoy_diff.py` — pure diff functions + a markdown renderer + a CLI entry point
- Modification to `/tax-review` skill — adds Step 3.5 "YoY What-Changed" that shells out to `yoy_diff.py` and embeds the result in the Section 4 report
- Test suite: ~15 new tests in `tests/test_yoy_diff.py`
- `CLAUDE.md` test-count reference bump (132 → ~147)

### Out of scope (deferred)

- Narrative roll-up prose ("AGI rose because wages rose") — v1 is pure tabular; no hand-coded or LLM-generated explanation
- Multi-year trend (N-2, N-3 columns) — v1 compares current vs N-1 only
- Embedding the diff in the `/tax-generate` PDF — v1 is markdown-only inside `/tax-review`
- Saving a standalone `summaries/yoy_diff_*.md` deliverable file — v1 prints inline only
- Configurable threshold *unit* (% of AGI) — v1 is a fixed dollar threshold with a `--threshold` override
- Auto-diffing arbitrary `result.json` fields — v1 uses a curated field list (see Field Curation)
- Short-term vs long-term capital-loss carryforward split (inherited limitation from `/tax-rollover` v1)

## User-facing flow

### Returning user (has archived prior year)

```
/tax-review
   ├─ Step 0.5: tax-law freshness check (unchanged)
   ├─ Steps 1–3: load picture, IRS review, EA review (unchanged)
   ├─ Step 3.5 (NEW): YoY What-Changed
   │     ├─ prior_year = year - 1
   │     ├─ archive/{prior_year}/ exists?
   │     │     ├─ no  → emit one-line skip note, continue
   │     │     └─ yes → python3 agents/lib/yoy_diff.py \
   │     │                  --prior archive/{prior_year} --current . \
   │     │                  --year {year} --threshold 1000
   │     └─ embed stdout as a subsection of the Step 4 report
   └─ Step 4: Generate Review Report (now includes YoY subsection)
```

### First-time user (no prior archive)

`archive/{year-1}/` doesn't exist. Step 3.5 emits `_YoY diff skipped: no archive/{prior_year}/ found._` and `/tax-review` proceeds exactly as today. No blocking, no error.

### Power-user / ad-hoc invocation

The CLI is runnable on its own for spot checks or future automation:

```
python3 agents/lib/yoy_diff.py --prior archive/2024 --current . --year 2025 --threshold 500
python3 agents/lib/yoy_diff.py --prior archive/2024 --current . --year 2025 --json
```

## Architecture

### Directory layout

```
Repo root (during TY2026 preparation)
├── archive/
│   └── 2025/
│       ├── result.json            ← prior baseline read by yoy_diff
│       └── taxpayer_profile.json
├── result.json                    ← current TY2026
├── tax_law_2026.json              ← consulted for marginal-bracket lookup
└── agents/
    ├── lib/
    │   ├── rollover.py            ← reused: load_prior, PriorReturn, RolloverError
    │   └── yoy_diff.py            ← NEW
    └── skills/
        └── tax-review.md          ← MODIFIED (+Step 3.5, +Section 4 row)
```

### New artifacts summary

| Artifact | Type | Approx. size |
|---|---|---|
| `agents/lib/yoy_diff.py` | Python module + CLI | ~200 LOC |
| `tests/test_yoy_diff.py` | New test file | ~15 tests |
| `agents/skills/tax-review.md` | Existing skill, modified | +~25 lines (Step 3.5 + report row) |
| `CLAUDE.md` | Test-count reference | +1 line edit |

One new module, one new test file, two edited files. Strictly additive — no template, schema, or profile changes; existing taxpayers and tests are unaffected.

### Read-only contract

`yoy_diff.py` **never writes** to any workspace file. `/tax-review` is a read-side skill; adding write behavior would surprise users. This is the key simplification over `rollover.py` — there is no atomic-write dance, no `.tmp` files, nothing to roll back.

## Components

### `agents/lib/yoy_diff.py`

Pure functions with thin I/O wrappers at the edges. Public API:

```python
compute_headlines(prior: dict, current: dict, tax_law: dict) -> list[HeadlineMetric]
compute_line_movers(prior: dict, current: dict, threshold: float) -> list[LineMover]
compute_section_changes(prior: dict, current: dict) -> list[SectionChange]
compute_yoy_diff(prior: dict, current: dict, tax_law: dict,
                 threshold: float = 1000) -> YoYReport
render_markdown(report: YoYReport) -> str

# CLI entry point
python3 agents/lib/yoy_diff.py --prior archive/2025 --current . --year 2026 \
                               [--threshold 1000] [--json]
```

Dataclasses (all `frozen=True` for hashability + trivial test equality):

```python
@dataclass(frozen=True)
class HeadlineMetric:
    label: str                # "AGI", "Total tax", "Effective rate", ...
    current: float
    prior: float
    delta: float              # current - prior
    delta_pct: float | None   # None when prior == 0 (rendered as "—")
    unit: str                 # "$" | "%" | "pts"

@dataclass(frozen=True)
class LineMover:
    label: str                # "Wages (1040 line 1z)"
    json_path: str            # "form_1040.line_1z_total_wages"
    current: float
    prior: float
    delta: float              # sign preserved

@dataclass(frozen=True)
class SectionChange:
    kind: str                 # "appeared" | "disappeared"
    section: str              # "schedule_c", "form_8889_hsa", ...
    note: str                 # one-line human explanation

@dataclass(frozen=True)
class YoYReport:
    current_year: int
    prior_year: int
    headlines: list[HeadlineMetric]    # always populated, fixed order
    line_movers: list[LineMover]       # filtered by threshold, sorted by abs(delta) desc
    sections_changed: list[SectionChange]
```

Thin I/O wrappers (CLI edge only): `_load_current_result(workspace) -> dict`, `_load_tax_law(workspace, year) -> dict`, `_main(argv) -> int`. The `_get_path(d, dotted, default=0)` helper is imported/shared from `rollover.py` to avoid duplicating nested-dict traversal.

### Reuse of `rollover.py`

`_main` calls `rollover.load_prior(archive_dir, year - 1)` to load and validate the prior return. This gives, for free, the existing guard that the archive's `meta.tax_year` equals the expected prior year — a mislabeled archive raises `RolloverError`, which `_main` converts to a non-zero exit + stderr message.

### `agents/skills/tax-review.md` modifications

**Step 3.5 (new, between EA Review and Generate Review Report):**

```
### 3.5. YoY What-Changed

1. Compute prior_year = {year} - 1.
2. If archive/{prior_year}/ does not exist:
   - Note in the report: "_YoY diff skipped: no archive/{prior_year}/ found._"
   - Proceed to Step 4.
3. Else run:
   python3 agents/lib/yoy_diff.py --prior archive/{prior_year} --current . \
       --year {year} --threshold 1000
4. Exit code 0 → embed stdout as the "YoY What-Changed" subsection in Step 4.
5. Exit code non-zero → note "_YoY diff skipped: {stderr reason}_"; proceed to Step 4.
```

**Section 4 report template (modified):** a new `## 📅 YoY What-Changed` block is added after the EA Perspective, populated by Step 3.5's output (or the skip note).

## Field curation (the core spec)

Rather than recursively diffing all 200+ `result.json` fields (which surfaces rounding noise and double-counts derived fields), v1 hand-picks fields and labels them in tax-human terms. Codified as module-level lists in `yoy_diff.py`.

### Headlines (always shown, fixed order)

| Label | Source | Notes |
|---|---|---|
| AGI | `form_1040.line_11_agi` | |
| Taxable income | `form_1040.line_15_taxable_income` | |
| Marginal bracket | derived from `line_15` + `tax_law.brackets` + `meta.filing_status` | `—` if `tax_law` empty |
| Effective rate | `line_24_total_tax / line_11_agi` | guard divide-by-zero → `delta_pct=None` |
| Total tax | `form_1040.line_24_total_tax` | |
| Total payments | `form_1040.line_33_total_payments` | |
| Refund / (Owed) | `line_35a_refund` − `line_37_amount_owed` | single signed number; owed in parens |

### Line movers (curated, threshold-filtered, federal)

```python
LINE_MOVER_FIELDS: list[tuple[str, str]] = [
    ("Wages (1040 line 1z)",              "form_1040.line_1z_total_wages"),
    ("Taxable interest (1040 line 2b)",   "form_1040.line_2b_taxable_interest"),
    ("Ordinary dividends (1040 line 3b)", "form_1040.line_3b_ordinary_dividends"),
    ("Qualified dividends (1040 line 3a)","form_1040.line_3a_qualified_dividends"),
    ("Taxable IRA (1040 line 4b)",        "form_1040.line_4b_taxable_ira"),
    ("Taxable pensions (1040 line 5b)",   "form_1040.line_5b_taxable_pensions"),
    ("Capital gain (1040 line 7)",        "form_1040.line_7_capital_gain"),
    ("Schedule 1 other income (line 8)",  "form_1040.line_8_schedule1_other"),
    ("Adjustments (1040 line 10)",        "form_1040.line_10_adjustments"),
    ("QBI deduction (1040 line 13a)",     "form_1040.line_13a_qbi_deduction"),
    ("Tax (1040 line 16)",                "form_1040.line_16_tax"),
    ("Child Tax Credit (1040 line 19)",   "form_1040.line_19_ctc"),
    ("Other taxes (1040 line 23)",        "form_1040.line_23_other_taxes"),
    ("Total withholding (1040 line 25d)", "form_1040.line_25d_total_withholding"),
    ("Estimated payments (1040 line 26)", "form_1040.line_26_estimated_payments"),
    ("Schedule C net",                    "schedule_c.line_31_net"),
    ("SE tax",                            "schedule_c.se_tax"),
    ("Net capital gain/loss (Sch D)",     "schedule_d.line_16_combined"),
    ("HSA deduction (Form 8889)",         "form_8889_hsa.line_13_hsa_deduction"),
    ("Foreign tax credit (Form 1116)",    "form_1116_ftc.ftc_allowed"),
    ("Additional Medicare Tax (8959)",    "form_8959_additional_medicare.line_18_total_additional_tax"),
    ("NIIT",                              "federal_tax_computation.niit"),
]
```

### State line movers (only when both years carry the same state section)

| Label | Path |
|---|---|
| State taxable income | `california_540.ca_taxable_income` |
| State total tax | `california_540.ca_total_tax` |
| State refund | `california_540.ca_refund` |

v1 matches the known `california_540` key plus the `*_540` pattern. If the state section key differs between years (taxpayer moved states), it is reported as a section appeared/disappeared rather than diffed line-by-line.

### Section appeared/disappeared

Detected over the set of optional sections (`schedule_c`, `schedule_d`, `form_8889_hsa`, `form_1116_ftc`, `form_8938_fatca`, `fbar_fincen_114`, `form_1099r_401k_rollover`, `form_8959_additional_medicare`, `form_8995_qbi`, `ira_contribution`, and the state section). A section "exists" for a year when its key is present and has any non-zero/non-empty content.

### Three curation decisions baked in

1. **Curated, not recursive.** A new field added to `result_template.json` later will NOT auto-appear in the diff — a maintainer must add it to `LINE_MOVER_FIELDS`. Deliberate trade: explicit + noise-free over automatic + noisy. A code comment flags this for future maintainers.
2. **Missing path = 0.** Reuses `_get_path(..., default=0)`. A section absent last year yields prior=0; a $12K Schedule C net this year shows as a +$12,000 mover.
3. **Redundancy kept.** A brand-new Schedule C appears both as a line mover (quantifies) AND a section-appeared note (explains). Both views are useful; v1 keeps both.

## Output shape

The CLI prints this markdown (embedded by Step 3.5):

```markdown
## YoY What-Changed — TY2025 → TY2026

### Headlines
| Metric            | TY2025   | TY2026   | Δ        | Δ %    |
|-------------------|---------:|---------:|---------:|-------:|
| AGI               | $180,400 | $215,200 | +$34,800 | +19.3% |
| Taxable income    | $152,500 | $186,100 | +$33,600 | +22.0% |
| Marginal bracket  | 24%      | 32%      | +8 pts   | —      |
| Effective rate    | 18.2%    | 21.4%    | +3.2 pts | —      |
| Total tax         | $32,830  | $46,053  | +$13,223 | +40.3% |
| Total payments    | $34,030  | $45,473  | +$11,443 | +33.6% |
| Refund / (Owed)   | $1,200   | ($580)   | −$1,780  | —      |

### Line movers ≥ $1,000 (sorted by |Δ|)
| Line                              | TY2025  | TY2026  | Δ        |
|-----------------------------------|--------:|--------:|---------:|
| Wages (1040 line 1z)              | $160,000| $190,000| +$30,000 |
| Capital gain (1040 line 7)        | $4,000  | $14,000 | +$10,000 |
| HSA deduction (Form 8889)         | $3,850  | $0      | −$3,850  |

### Sections that appeared
- **schedule_c** — Schedule C present this year, not last year

### Sections that disappeared
- (none)
```

When zero line movers clear the threshold: `_No line items moved ≥ $1,000._`

## Error handling

Guiding principle: **the YoY section must never break the rest of `/tax-review`.** Every failure degrades to a one-line note.

| Condition | Detection | Behavior |
|---|---|---|
| No `archive/{N-1}/` dir | Step 3.5 pre-check | Skip note: `_YoY diff skipped: no archive/{N-1}/ found._` |
| Archive missing `result.json` | `load_prior` raises `RolloverError` | CLI exit 1 + stderr; skill prints skip note with reason |
| Archive `meta.tax_year` ≠ N-1 | `load_prior` year guard | `RolloverError` → skip note (guards mislabeled archives) |
| Malformed JSON (either file) | `_read_json` raises `RolloverError` | Skip note: `_YoY diff skipped: {file} is malformed._` |
| Current `result.json` absent | guarded (is a /tax-review prerequisite) | Skip note |
| `tax_law_{N}.json` absent | `_load_tax_law` returns `{}` | Headlines compute; marginal-bracket row shows `—` |
| AGI = 0 (Δ% / effective-rate divide) | guard in `compute_headlines` | `delta_pct = None` → rendered `—` |
| Section present one year only | `_get_path(..., default=0)` | mover + section change |

### CLI exit codes

- `0` — diff computed and rendered (including zero-mover case)
- `1` — could not load prior/current (`RolloverError`); message on stderr

### What we deliberately do NOT do

- No retries, no fallback archive search, no interactive prompts (keeps `/tax-review` non-interactive-friendly)
- No partial-write recovery — `yoy_diff.py` never writes
- No new file output — v1 prints to stdout only

### Input validation (security)

- `--year`: 4-digit integer, 2000–2099 (reuses `rollover._validate_year_arg`)
- `--prior` / `--current` paths: resolved and validated under workspace via `rollover._validate_subpath` (rejects `../` traversal)

## Testing

### `tests/test_yoy_diff.py` (~15 tests)

Inline hand-built dict fixtures (no files under `tests/fixtures/` → avoids PII-scan allowlist friction; matches `test_rollover.py` style).

| Group | Tests |
|---|---|
| Headlines | (1) all seven metrics correct; (2) prior AGI 0 → `delta_pct=None`; (3) empty `tax_law` → marginal bracket `—`; (4) refund→owed sign flip |
| Line movers | (5) only `\|delta\| >= threshold` returned; (6) sorted by `abs(delta)` desc; (7) custom threshold filters more; (8) missing section → prior 0 → mover |
| Section changes | (9) appeared; (10) disappeared; (11) no change when sections identical |
| Orchestrator + render | (12) `compute_yoy_diff` wires three together, default threshold 1000; (13) `render_markdown` emits expected headers + rows (substring asserts) |
| CLI + errors | (14) `--json` emits parseable JSON; (15) malformed/missing archive → exit 1 + stderr |

### CI / bookkeeping

- `CLAUDE.md` test-count reference bumped 132 → ~147 in the same PR
- `.github/workflows/ci.yml` unchanged (runs `pytest tests/ -v` recursively)
- No new fixtures committed (inline dicts only)

## Open follow-ups (post-v1)

- **Narrative roll-ups:** hand-coded "AGI rose because…" prose tying movers to headline metrics
- **Multi-year trend:** N-2 / N-3 columns when more archives exist
- **PDF embedding:** surface the diff in the `/tax-generate` deliverable (requires pipeline ordering decision)
- **Standalone deliverable:** optional `summaries/yoy_diff_{N-1}_to_{N}.md` file
- **Standalone `/tax-diff` skill:** promote the CLI to a first-class skill if ad-hoc demand appears
