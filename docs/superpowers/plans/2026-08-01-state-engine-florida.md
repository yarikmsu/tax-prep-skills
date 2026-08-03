# Florida State Module (type-none regime) Implementation Plan — RE-BASELINED

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**RE-BASELINE NOTE (2026-08-01):** the first revision of this plan was written against a stale local checkout (PR #23). This revision targets origin/main at `dfe74ae` (PR #64, 1891 tests) and follows `docs/contributing/state-module-playbook.md` (PR #40) — the house enumerate-and-register pattern. The central `state_registry.py` from the first revision is DROPPED per user ruling; the `type: "none"` regime discriminator survives as a playbook amendment for no-income-tax states.

**Goal:** Make "no individual income tax" a first-class, affirmatively-declared state regime and ship Florida support: `type: "none"` tax-law template, correct capability note and harvest banner, the Schedule A optional-tables sales-tax input (feeding the existing `compute_schedule_a` election), FL county property-deadline calendar events, and the FL `/tax-review` section.

**Architecture:** Per the playbook: a fetched-law block (FL template — deliberately NO brackets/standard_deduction keys so freshness is clean by construction), an informational result section (`florida_state` — no computed-evidence pair needed, an explicit non-goal), one new parameterized function in `tax_computations.py` producing the `state_local_sales_taxes` input that `compute_schedule_a` already elects on, and registrations in the modules that enumerate states (projection/yoy literals, capabilities `_state_note`, calendar). No tie-out category (FL W-2s have no Box 17 — both halves or neither, so neither).

**Tech Stack:** Python 3.10+ stdlib, pytest, Claude Code skills (Markdown).

**Spec:** Task 1 writes `docs/superpowers/specs/2026-08-01-fl-state-module-design.md` from `docs/superpowers/specs/TEMPLATE-state-module.md` (playbook definition-of-done). Product context: `docs/roadmap/2026-state-support-ny-fl-wa.md` Epic 1 (whose Epic 0.3 registry is superseded by this re-baseline).
**Branch/worktree:** already active — `feat/state-engine-florida` at `.claude/worktrees/feat+state-engine-florida`, base `dfe74ae`, baseline suite green (1891 passed).

## Global Constraints

- **Venv:** the pip index is unreachable — do NOT `pip install` or create a venv. Activate the main checkout's venv: `source /path/to/tax-prep-skills/.venv/bin/activate`, then run everything from the worktree root.
- Baseline is **1891 passed**; every task leaves the suite green.
- No hardcoded tax law except statutory-structural constants with an inline citation (FL 6% sales rate, F.S. 212.05, qualifies — same class as CA's `mental_health_surcharge` R&TC §17043 precedent). Indexed/annual amounts ship as `None` and are fetched by `/tax-setup`.
- All computation outputs rounded to 2 dp; validators reject bool/NaN/Infinity (`math.isfinite`); filing status via `_normalize_filing_status` where applicable.
- After modifying `tax_computations.py`: `python3 agents/lib/tax_computations.py` self-test must pass.
- `check_freshness` flags every `None` and every empty list — a field the design never fetches must be **structured out**, not shipped empty (playbook step 1).
- Source pinning (H4): fetched state blocks record a `_sources` pin keyed `state.FL`; only `.gov` hosts + `_AUTHORITATIVE_NONGOV_DOMAINS` pass — **Florida DOR is `floridarevenue.com` (a .com)** and must be allowlisted.
- Update the CLAUDE.md test-count line ("1891 tests …") in the final task; per-PR narrative goes to `docs/changelog/` (PR #62 convention), not CLAUDE.md.
- NEVER `git push` — print the command. Commits: plain conventional messages, no attribution trailers.
- Line numbers cited below are from base `dfe74ae` — locate by the quoted code if drifted.

---

## File Structure

| Path | Action | Purpose |
|---|---|---|
| `docs/superpowers/specs/2026-08-01-fl-state-module-design.md` | Create | FL spec from the state-module template (playbook DoD) |
| `agents/lib/tax_law_fetcher.py` | Modify | `type` on CA/NY templates; FL template; `_SKIP_KEYS`; `floridarevenue.com` allowlist |
| `agents/lib/tax_computations.py` | Modify | `compute_sales_tax_deduction` + self-test |
| `agents/lib/loss_harvest.py` | Modify | `state_no_income_tax` helper + type-aware banner |
| `agents/lib/projection.py` | Modify | `STATE_SECTION_KEYS` += `florida_state` |
| `agents/lib/yoy_diff.py` | Modify | `OPTIONAL_SECTIONS` += `florida_state` (movers untouched — FL has none) |
| `agents/lib/capabilities.py` | Modify | `_NO_INCOME_TAX_STATES` + `_state_note` branch |
| `agents/lib/tax_calendar.py` | Modify | `state_property` category + `events_fl_property` (weekend-only `_state_due` rollover) |
| `agents/templates/taxpayer_profile_template.json` | Modify | Flags `has_business_tangible_property`, `fl_new_homestead` |
| `agents/templates/result_template.json` | Modify | `florida_state` section; CA-leak prune notes (lines 528/535) |
| `agents/templates/answers_template.md` | Modify | No-income-tax state note in Tax Result table |
| `agents/skills/tax-setup.md` | Modify | Step-4 `type=none` fetch branch (anchor ~line 563) + FL interview |
| `agents/skills/tax-compute.md` | Modify | Step-10 `type=none` branch (anchor line 1227) |
| `agents/skills/tax-parse.md` | Modify | Box-17 anomaly row (line 241) keys off `state.type` |
| `agents/skills/tax-generate.md` | Modify | State step (lines 57-58) branches on `type` |
| `agents/skills/tax-review.md` | Modify | No-income-tax completeness step + NY→FL mover checklist |
| `agents/skills/tax-calendar.md` | Modify | Document FL property events |
| `docs/contributing/state-module-playbook.md` | Modify | Amendment: the `type: "none"` regime path |
| `tests/test_tax_law_fetcher.py` | Modify | FL template + freshness tests (file exists — append) |
| `tests/test_tax_computations.py`, `tests/test_loss_harvest.py`, `tests/test_tax_calendar.py`, `tests/test_projection.py`, `tests/test_yoy_diff.py`, `tests/test_capabilities.py` | Modify | Per-task tests |
| `CLAUDE.md`, `docs/changelog/`, `docs/roadmap/2026-state-support-ny-fl-wa.md` | Modify | Wrap-up docs |

---

## Task 1: FL state-module spec (playbook step 0 + definition-of-done)

**Files:**
- Create: `docs/superpowers/specs/2026-08-01-fl-state-module-design.md`

**Interfaces:**
- Produces: the spec later tasks' briefs cite; the explicit non-goals list reviewers check against.

- [ ] **Step 1: Write the spec**

Create the file following the section structure of `docs/superpowers/specs/TEMPLATE-state-module.md` (Scope / Questionnaire answers / Fetched-law block / Result section / Compute functions / Registrations / Tests / Explicit non-goals), with this content:

```markdown
# 2026-08-01 — FL state module — design spec

## 1. Scope
Florida for full-year residents: an AFFIRMATIVE no-income-tax pathway. FL levies
no personal income tax (Fla. Const. Art. VII, §5) — there is no return to
compute. The module's product value: (a) a positive "no FL return exists or is
required" declaration in result.json/PDF/capability note (absence-of-tax must
never read as absence-of-data); (b) the federal Schedule A line 5a SALES-tax
election input via the IRS optional-tables method (the one federal computation
FL residents systematically miss); (c) FL county property-deadline reminders
(DR-501 homestead Mar 1, DR-405 tangible personal property Apr 1); (d) /tax-review
guardrails (use tax, nonresident exposure, NY→FL domicile-audit checklist).

## 2. Questionnaire answers (playbook step 0)
1. Starting point: N/A — no income tax, no return (Fla. Const. Art. VII §5).
2. Additions/subtractions: N/A. 3. Credits: N/A. 4. Local income taxes: none
(county PROPERTY deadlines handled via calendar instead). 5. Surtaxes: none.
6. Estimated payments: none exist. 7. Payroll-style items: none (no SDI).
8. Withholding forms: none — FL W-2s carry empty Boxes 15-17.
9. Extension: N/A. 10. PTET: FL has none (not in _PTET_STATES — correct).
11. Community property: FL is not in _COMMUNITY_PROPERTY_STATES — correct.
12. Itemized: no state schedule; the FEDERAL Schedule A sales-tax election is
the FL-relevant piece (compute_schedule_a already elects max(income, sales)).
13. Straight bracket math: N/A — introduce `type: "none"` so consumers can
distinguish "no tax" from "not fetched" (the regime discriminator this repo
lacked; WA's excise will add a third type later).

## 3. Fetched-law block
`create_state_template("FL")`: code/name/authority/`type: "none"`/
`no_income_tax: true`/note + `sales_tax: {state_rate: 0.06 (F.S. 212.05,
structural), county_surtax_rate: None (fetched)}`. Deliberately NO
brackets/standard_deduction keys — the freshness walker flags every empty
list, so a no-income-tax state must structure them out (playbook step 1).
CA/NY templates gain `type: "graduated_income"`; the generic fallback is
UNCHANGED (no type key) so other states' freshness behavior doesn't move.
Source pin: `state.FL` → floridarevenue.com (added to
_AUTHORITATIVE_NONGOV_DOMAINS — FL DOR is a .com, not .gov).

## 4. Result section
`florida_state`: informational — no_income_tax_statement,
state_return_required: false, sales_tax_deduction sub-block (table_amount,
local_addition, big_ticket_tax, total_line_5a_candidate), use_tax_note.
NO computed-evidence pair (playbook step 2): there is no computed state tax
for scaffold-vs-computed gating to protect; stated here explicitly per the
playbook's "if you add none, say so" rule.

## 5. Compute functions
`compute_sales_tax_deduction(table_amount, state_rate, local_rate=0,
big_ticket_tax=0) -> dict` — IRS Schedule A Optional State Sales Tax Tables
method: table + table×(local/state) + actual big-ticket tax. Its `total` is
the INPUT for compute_schedule_a's existing `state_local_sales_taxes`
parameter; the income-vs-sales election and SALT cap already live there.

## 6. Registrations
projection.STATE_SECTION_KEYS += "florida_state"; yoy_diff.OPTIONAL_SECTIONS
+= "florida_state" (appear/disappear tracking on a move year); capabilities
_state_note: type-none branch via _NO_INCOME_TAX_STATES (AK/FL/NV/SD/TX/WY —
WA and NH deliberately EXCLUDED: WA has the capital-gains excise, NH is
transition-year; they keep the estimate note until modeled); tax_calendar
state_property category. NOT registered (explicit non-goals below): tie-out
category, withholding true-up block, /tax-extend, retirement conformity sets
(FL conforms trivially — no tax).

## 7. Tests
Template shape; freshness (clean once county rate fetched; unfetched county
rate flagged; empty state dict still flagged); sales-tax math + validator
rejections; harvest banner both directions; capability note; calendar events
(gating, dates, weekend rollover, UID stability); registration literals.

## 8. Explicit non-goals
No tie_out.CATEGORY_TARGETS entry (no FL withholding exists — the playbook's
both-halves-or-neither rule → neither). No withholding.py state block (no
state withholding to true up). No /tax-extend mechanics (no FL return to
extend). No part-year/nonresident (gated at C1 moved_states_this_year). No
FL corporate/TPP tax computation (calendar reminder only). WA/NH regime
notes unchanged. yoy STATE_MOVER_FIELDS untouched (FL has no movers; the
per-section restructure the playbook prescribes lands with the NY module).
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-08-01-fl-state-module-design.md
git commit -m "docs(spec): FL state module design — type-none regime per playbook"
```

---

## Task 2: FL fetched-law block + `type` discriminator + source-pin allowlist

**Files:**
- Modify: `agents/lib/tax_law_fetcher.py` — `create_state_template` (~line 332), `_SKIP_KEYS` (~line 657), `_AUTHORITATIVE_NONGOV_DOMAINS` (~line 420)
- Test: `tests/test_tax_law_fetcher.py` (exists — append classes)

**Interfaces:**
- Produces: `create_state_template("FL")` dict with `type: "none"`, `sales_tax.state_rate == 0.06`, `sales_tax.county_surtax_rate is None`, NO `brackets`/`standard_deduction` keys; CA/NY templates carry `type: "graduated_income"`. Tasks 4/8 read `tax_law["state"]["type"]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tax_law_fetcher.py`, matching the file's existing import/fixture style (it already imports `create_state_template`, `create_empty_tax_law`, `save_tax_law`, `check_freshness`; reuse its cache-building helper/tmp_path pattern — read the top of the file first):

```python
class TestStateRegimeType:
    def test_ca_ny_are_graduated_income(self):
        assert create_state_template("CA")["type"] == "graduated_income"
        assert create_state_template("NY")["type"] == "graduated_income"

    def test_generic_fallback_has_no_type_key(self):
        # Deliberate: unknown states keep pre-existing freshness behavior;
        # regimes are typed one modeled state at a time.
        assert "type" not in create_state_template("TX")

    def test_fl_template_shape(self):
        t = create_state_template("FL")
        assert t["code"] == "FL"
        assert t["type"] == "none"
        assert t["no_income_tax"] is True
        # No bracket/deduction keys: the freshness walker flags every empty
        # list, so a no-income-tax state structures them OUT (playbook step 1).
        assert "brackets" not in t
        assert "standard_deduction" not in t
        assert t["sales_tax"]["state_rate"] == 0.06  # F.S. 212.05 structural
        assert t["sales_tax"]["county_surtax_rate"] is None


class TestFlFreshness:
    def _state_nulls(self, res):
        return [f for f in res["null_fields"]
                if f == "state" or f.startswith("state.") or f.startswith("state ")]

    def test_fl_block_clean_once_county_rate_fetched(self, tmp_path):
        data = create_empty_tax_law(2025)
        fl = create_state_template("FL")
        fl["sales_tax"]["county_surtax_rate"] = 0.01
        data["state"] = fl
        save_tax_law(2025, data, str(tmp_path))
        assert self._state_nulls(check_freshness(2025, str(tmp_path))) == []

    def test_unfetched_county_rate_is_flagged(self, tmp_path):
        data = create_empty_tax_law(2025)
        data["state"] = create_state_template("FL")
        save_tax_law(2025, data, str(tmp_path))
        assert self._state_nulls(check_freshness(2025, str(tmp_path))) == [
            "state.sales_tax.county_surtax_rate"]

    def test_empty_state_dict_still_flagged(self, tmp_path):
        # {} keeps meaning "not configured"; only a POSITIVE type:"none"
        # means "no tax". Regression pin on the existing check.
        data = create_empty_tax_law(2025)
        save_tax_law(2025, data, str(tmp_path))
        assert "state (no state tax data configured)" in \
            check_freshness(2025, str(tmp_path))["null_fields"]


class TestFloridaDorPinAuthoritative:
    def test_floridarevenue_com_is_authoritative(self):
        from agents.lib.tax_law_fetcher import _source_host_authoritative
        # FL DOR is a .com — without the allowlist entry every state.FL pin
        # would read non-authoritative and flip the cache INCOMPLETE.
        assert _source_host_authoritative(
            "https://floridarevenue.com/taxes/taxesfees/Pages/sales_tax.aspx")
```

Note the FL-freshness tests intentionally IGNORE federal null_fields (`_state_nulls` filter) — `create_empty_tax_law` ships federal fields unfetched and that is not under test here. If the existing file has a helper that builds a fully-populated cache, reusing it instead is also acceptable; the filtered assertion is the portable form. If `check_freshness` emits state-related advisories with other prefixes (e.g. `_sources` pin advisories), keep the filter's three prefixes as written — pin advisories are keyed under `_sources`, not `state`.

- [ ] **Step 2: Run to verify failures**

Run: `source /path/to/tax-prep-skills/.venv/bin/activate && python3 -m pytest tests/test_tax_law_fetcher.py -k "RegimeType or FlFreshness or FloridaDor" -v`
Expected: FAIL — KeyError `'type'` / missing FL entry / `_source_host_authoritative` False for the .com.

- [ ] **Step 3: Implement**

In `agents/lib/tax_law_fetcher.py`:

1. In `create_state_template`, add to the CA entry after `"authority": "Franchise Tax Board (FTB)",`:

```python
            # Regime discriminator: "graduated_income" (bracket income tax) |
            # "none" (no individual income tax — FL). Lets consumers
            # (loss_harvest banner, skills' step-10/generate/parse branches)
            # distinguish "no tax" from "not fetched". Generic fallback
            # carries NO type key — regimes are typed one modeled state at
            # a time (see docs/contributing/state-module-playbook.md).
            "type": "graduated_income",
```

2. Add to the NY entry after `"authority": "Department of Taxation and Finance",`:

```python
            "type": "graduated_income",
```

3. Add the FL entry after the NY entry's closing brace:

```python
        "FL": {
            "code": "FL",
            "name": "Florida",
            "authority": "Florida Department of Revenue",
            "type": "none",
            "no_income_tax": True,
            "note": (
                "Florida imposes no personal income tax (Fla. Const. Art. "
                "VII, §5) — no state individual income tax return exists. "
                "Deliberately NO brackets/standard_deduction keys: the "
                "freshness walker flags every empty list, and a field the "
                "design never fetches would nag INCOMPLETE forever. The only "
                "fetched field is the taxpayer's county discretionary sales "
                "surtax rate, feeding the federal Schedule A line 5a "
                "sales-tax election (compute_sales_tax_deduction → "
                "compute_schedule_a state_local_sales_taxes)."
            ),
            "sales_tax": {
                # F.S. 212.05 general rate — statutory-structural constant
                # with citation, per the CA mental_health_surcharge precedent.
                "state_rate": 0.06,
                "county_surtax_rate": None,  # taxpayer's county rate — /tax-setup fetches
            },
        },
```

4. Extend `_SKIP_KEYS` (~line 657) — per its MAINTENANCE comment, new descriptive keys must be audited in:

```python
    _SKIP_KEYS = {"note", "hsa_note", "name", "authority",
                  "conforms_to_federal", "no_city_income_tax", "twenty_pct_above",
                  "applies", "hsa_conformity", "type", "no_income_tax"}
```

5. Extend `_AUTHORITATIVE_NONGOV_DOMAINS` (~line 420):

```python
_AUTHORITATIVE_NONGOV_DOMAINS = frozenset({
    "law.cornell.edu",    # LII U.S. Code
    # Florida DOR does not use a .gov domain — floridarevenue.com IS the
    # primary state tax authority (verified via myflorida.com directory).
    "floridarevenue.com",
})
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_tax_law_fetcher.py -v`
Expected: new tests PASS, all pre-existing tests in the file still PASS.

- [ ] **Step 5: Commit**

```bash
git add agents/lib/tax_law_fetcher.py tests/test_tax_law_fetcher.py
git commit -m "feat(state): type-none regime discriminator + FL fetched-law block + FL DOR pin allowlist"
```

---

## Task 3: `compute_sales_tax_deduction` (the Schedule A line-5a sales-tax input)

**Files:**
- Modify: `agents/lib/tax_computations.py` (place directly BEFORE `compute_schedule_a` at ~line 2030; self-test block at file end)
- Test: `tests/test_tax_computations.py` (append)

**Interfaces:**
- Consumes: module-level `math` (already imported); mirrors the `_sched_a_amount` validation style.
- Produces: `compute_sales_tax_deduction(table_amount, state_rate, local_rate=0.0, big_ticket_tax=0.0) -> dict` with keys `table_amount, local_addition, big_ticket_tax, total`. Its `total` feeds `compute_schedule_a(state_local_sales_taxes=...)` — Task 8's skill text wires that.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tax_computations.py` (match the file's import style):

```python
class TestComputeSalesTaxDeduction:
    """IRS Schedule A Optional State Sales Tax Tables method (line 5a input).

    deduction = table_amount + table_amount x local_rate/state_rate
              + actual sales tax on specified big-ticket items.
    Output feeds compute_schedule_a(state_local_sales_taxes=...) — the
    income-vs-sales election and SALT cap live THERE, not here.
    """

    def test_table_only(self):
        r = compute_sales_tax_deduction(1000.0, 0.06)
        assert r == {"table_amount": 1000.0, "local_addition": 0.0,
                     "big_ticket_tax": 0.0, "total": 1000.0}

    def test_local_proration_fl_county(self):
        # 1% county surtax on FL's 6% state rate: 1000 x (0.01/0.06) = 166.67
        r = compute_sales_tax_deduction(1000.0, 0.06, local_rate=0.01)
        assert r["local_addition"] == 166.67
        assert r["total"] == 1166.67

    def test_big_ticket_added(self):
        r = compute_sales_tax_deduction(1000.0, 0.06, local_rate=0.01,
                                        big_ticket_tax=450.0)
        assert r["total"] == 1616.67

    def test_zero_state_rate_rejected(self):
        with pytest.raises(ValueError):
            compute_sales_tax_deduction(1000.0, 0.0)

    def test_negative_rejected(self):
        for kwargs in ({"table_amount": -1.0, "state_rate": 0.06},
                       {"table_amount": 1000.0, "state_rate": 0.06, "local_rate": -0.01},
                       {"table_amount": 1000.0, "state_rate": 0.06, "big_ticket_tax": -5.0}):
            with pytest.raises(ValueError):
                compute_sales_tax_deduction(**kwargs)

    def test_bool_and_nonfinite_rejected(self):
        with pytest.raises(ValueError):
            compute_sales_tax_deduction(True, 0.06)
        for bad in (float("nan"), float("inf")):
            with pytest.raises(ValueError):
                compute_sales_tax_deduction(bad, 0.06)
            with pytest.raises(ValueError):
                compute_sales_tax_deduction(1000.0, 0.06, local_rate=bad)
```

(Import `compute_sales_tax_deduction` alongside the file's existing `tax_computations` imports; `pytest` is already imported there.)

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_tax_computations.py::TestComputeSalesTaxDeduction -v`
Expected: ImportError / NameError.

- [ ] **Step 3: Implement**

Insert directly before `def compute_schedule_a(` (~line 2030):

```python
def compute_sales_tax_deduction(
    table_amount: float,
    state_rate: float,
    local_rate: float = 0.0,
    big_ticket_tax: float = 0.0,
) -> dict:
    """Schedule A line 5a sales-tax INPUT — IRS Optional State Sales Tax
    Tables method (Schedule A instructions).

    For no-income-tax states (FL/TX/...) the SALT line-5a election is
    effectively always general sales tax — there is no state income tax to
    elect and W-2 Box 17 is empty. This computes the elective figure:

        total = table_amount                                (IRS table lookup)
              + table_amount * local_rate / state_rate      (local addition)
              + big_ticket_tax    (actual tax on specified items — motor
                                   vehicles, boats, etc., general rate only)

    The result feeds compute_schedule_a(state_local_sales_taxes=total) —
    the income-vs-sales election, the SALT cap (federal.salt_cap), and all
    Schedule A interactions live THERE, deliberately not here.

    Args:
        table_amount: IRS optional-tables amount for the taxpayer's income
            band / exemptions / state (looked up by /tax-compute from the
            current-year Schedule A instructions).
        state_rate: state general sales-tax rate as a decimal (FL 0.06,
            F.S. 212.05). Must be > 0.
        local_rate: county/local general surtax rate as a decimal.
        big_ticket_tax: actual sales tax paid on specified big-ticket items.

    Returns:
        {"table_amount", "local_addition", "big_ticket_tax", "total"} — 2 dp.

    Raises:
        ValueError: bool/NaN/Infinity/negative input, or state_rate <= 0.
    """
    vals = {}
    for name, v in (("table_amount", table_amount), ("state_rate", state_rate),
                    ("local_rate", local_rate), ("big_ticket_tax", big_ticket_tax)):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(
                f"compute_sales_tax_deduction: {name} must be numeric, got {v!r}")
        if not math.isfinite(float(v)):
            raise ValueError(
                f"compute_sales_tax_deduction: {name} must be finite, got {v!r}")
        if v < 0:
            raise ValueError(
                f"compute_sales_tax_deduction: {name} must be >= 0, got {v!r}")
        vals[name] = float(v)
    if vals["state_rate"] <= 0:
        raise ValueError("compute_sales_tax_deduction: state_rate must be > 0")
    local_addition = round(
        vals["table_amount"] * vals["local_rate"] / vals["state_rate"], 2)
    total = round(round(vals["table_amount"], 2) + local_addition
                  + round(vals["big_ticket_tax"], 2), 2)
    return {
        "table_amount": round(vals["table_amount"], 2),
        "local_addition": local_addition,
        "big_ticket_tax": round(vals["big_ticket_tax"], 2),
        "total": total,
    }
```

- [ ] **Step 4: Add self-test entries**

In the `if __name__ == "__main__":` block at the end of the file, append (following the block's existing assert pattern):

```python
    # compute_sales_tax_deduction — FL 6% + 1% county + $450 vehicle tax
    _stx = compute_sales_tax_deduction(1000.0, 0.06, 0.01, 450.0)
    assert _stx["total"] == 1616.67, _stx
    assert _stx["local_addition"] == 166.67, _stx
```

- [ ] **Step 5: Run tests + self-test**

Run: `python3 -m pytest tests/test_tax_computations.py::TestComputeSalesTaxDeduction -v && python3 agents/lib/tax_computations.py`
Expected: 6 PASS; self-test exits clean.

- [ ] **Step 6: Commit**

```bash
git add agents/lib/tax_computations.py tests/test_tax_computations.py
git commit -m "feat(compute): Schedule A optional-tables sales-tax input for no-income-tax states"
```

---

## Task 4: Type-aware harvest banner (no-tax ≠ not-fetched)

**Files:**
- Modify: `agents/lib/loss_harvest.py` — helper near `_state_marginal_rate` (~line 589), `HarvestReport` dataclass (`state_modeled` field ~line 679), report construction (~line 903), banner (~line 966)
- Test: `tests/test_loss_harvest.py`

**Interfaces:**
- Consumes: `tax_law["state"]["type"]` (Task 2).
- Produces: `state_no_income_tax(tax_law) -> bool` module function; `HarvestReport.state_no_income_tax: bool = False` (appended LAST with a default — every existing constructor call stays valid).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_loss_harvest.py`:

```python
class TestStateNoIncomeTaxHelper:
    def test_type_none_is_true(self):
        from agents.lib.loss_harvest import state_no_income_tax
        assert state_no_income_tax({"state": {"type": "none"}}) is True

    def test_missing_type_is_false(self):
        # Absence of the discriminator must NOT read as "no tax" — every
        # pre-existing cache lacks the field.
        from agents.lib.loss_harvest import state_no_income_tax
        assert state_no_income_tax({"state": {"brackets": []}}) is False
        assert state_no_income_tax({"state": {}}) is False
        assert state_no_income_tax({}) is False

    def test_graduated_income_is_false(self):
        from agents.lib.loss_harvest import state_no_income_tax
        assert state_no_income_tax({"state": {"type": "graduated_income"}}) is False
```

Then: `grep -n "State savings not modeled" tests/test_loss_harvest.py` — clone the existing banner test's arrange block into a sibling test in the same class, changing the tax_law's state block to `{"type": "none"}`, asserting:

```python
        assert "no individual income tax" in report_md
        assert "correct and complete" in report_md
        assert "State savings not modeled" not in report_md
```

Leave the existing banner test untouched (it is the missing-type regression guard).

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_loss_harvest.py -k "no_income_tax" -v`
Expected: ImportError on `state_no_income_tax`.

- [ ] **Step 3: Implement**

1. Below `_state_marginal_rate` (~line 596):

```python
def state_no_income_tax(tax_law: dict) -> bool:
    """True only when the fetched state block POSITIVELY declares no income
    tax (state.type == "none" — FL). Absent/unknown type returns False so an
    unfetched cache keeps the "not modeled" warning: absence of data must
    never read as absence of tax (bias-direction rule)."""
    state = tax_law.get("state") or {}
    return state.get("type") == "none"
```

2. In `HarvestReport`, append as the LAST field: `state_no_income_tax: bool = False`

3. In `build_harvest`, where the report is constructed (~line 903, alongside `state_modeled=state_modeled`), add: `state_no_income_tax=state_no_income_tax(tax_law),`

4. In `render_markdown`, replace the `if not r.state_modeled:` banner block (~line 965) with:

```python
    if not r.state_modeled:
        if r.state_no_income_tax:
            lines += ["> **State savings: $0 — correct and complete.** This "
                      "state levies no individual income tax "
                      "(`tax_law.state.type = \"none\"`), so there are no "
                      "state-side savings to model; the fed_savings figure "
                      "is the complete picture.", ""]
        else:
            lines += ["> **State savings not modeled** — `tax_law.state.brackets` is empty "
                      "(no income tax, or `/tax-setup` did not fetch state brackets). The "
                      "fed_savings figure is correct, but a state-tax taxpayer is leaving "
                      "additional savings on the table. Run `/tax-setup` to fetch them.", ""]
```

(Keep the second branch byte-identical to the current banner text at line 966 — only the wrapper `if/else` is new.)

- [ ] **Step 4: Run the full harvest suite**

Run: `python3 -m pytest tests/test_loss_harvest.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add agents/lib/loss_harvest.py tests/test_loss_harvest.py
git commit -m "feat(harvest): distinguish 'no state income tax' from 'state data not fetched'"
```

---

## Task 5: Playbook step-5 registrations (projection, yoy, capabilities)

**Files:**
- Modify: `agents/lib/projection.py:1173` (`STATE_SECTION_KEYS`)
- Modify: `agents/lib/yoy_diff.py` (~line 95-100, `OPTIONAL_SECTIONS`)
- Modify: `agents/lib/capabilities.py` (`_state_note` ~line 450; new frozenset beside `_COMMUNITY_PROPERTY_STATES` ~line 419)
- Test: `tests/test_projection.py`, `tests/test_yoy_diff.py`, `tests/test_capabilities.py`

**Interfaces:**
- Produces: `capabilities._NO_INCOME_TAX_STATES` frozenset; FL-aware `_state_note`. `STATE_MOVER_FIELDS` is deliberately UNTOUCHED (FL has no movers; the per-section restructure lands with NY — spec non-goal).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_projection.py`:

```python
class TestFloridaStateSection:
    def test_florida_in_state_section_keys(self):
        from agents.lib.projection import STATE_SECTION_KEYS
        assert "california_540" in STATE_SECTION_KEYS  # regression pin
        assert "florida_state" in STATE_SECTION_KEYS

    def test_fl_prior_section_never_projects_state_tax(self):
        # FL prior section detected, but tax_law has no brackets → None
        # (silently correct for a no-income-tax state), never an exception.
        from agents.lib.projection import project_state_tax
        class _Prior:
            result = {"florida_state": {"state_return_required": False}}
        out = project_state_tax(
            _make_projected_inputs(), _Prior(),
            {"state": {"type": "none", "code": "FL"}}, "MFJ",
            resident_state="FL")
        assert out is None
```

For `_make_projected_inputs()`: reuse the test file's existing `ProjectedInputs` construction helper if one exists (grep `ProjectedInputs(` in the file); otherwise add a module-level helper constructing it with every field zero — copy the exact field list from the nearest existing construction (the dataclass has NO defaults).

Append to `tests/test_yoy_diff.py`:

```python
class TestFloridaOptionalSection:
    def test_florida_state_in_optional_sections(self):
        from agents.lib.yoy_diff import OPTIONAL_SECTIONS, STATE_MOVER_FIELDS
        assert "florida_state" in OPTIONAL_SECTIONS
        assert "california_540" in OPTIONAL_SECTIONS  # regression pin
        # FL contributes NO movers — the flat CA-only mover list is untouched
        # (per-section restructure is the NY module's job).
        assert all("florida" not in path for _, path in STATE_MOVER_FIELDS)
```

Append to `tests/test_capabilities.py` (match its profile-building fixtures — grep `_state_note` or `address` in the file for the existing pattern):

```python
class TestNoIncomeTaxStateNote:
    def _profile(self, code):
        return {"flags": {}, "address": {"state": code}}

    def test_fl_gets_affirmative_no_tax_note(self):
        from agents.lib.capabilities import _state_note
        note = _state_note(self._profile("FL"))
        assert "no individual income tax" in note
        assert "ESTIMATE" not in note

    def test_wa_keeps_estimate_note(self):
        # WA is deliberately NOT in the no-income-tax set: the capital-gains
        # excise (RCW 82.87) means "no tax at all" would be a false promise.
        from agents.lib.capabilities import _state_note
        assert "ESTIMATE" in _state_note(self._profile("WA"))

    def test_ca_note_unchanged(self):
        from agents.lib.capabilities import _state_note
        assert "Form 540" in _state_note(self._profile("CA"))
```

(Adjust `_profile` to whatever minimal shape `_profile_state` requires — read `_state_note`'s current body first; if `_profile_state` returns None for a missing address the two-key dict suffices.)

- [ ] **Step 2: Run to verify failures**

Run: `python3 -m pytest tests/test_projection.py::TestFloridaStateSection tests/test_yoy_diff.py::TestFloridaOptionalSection tests/test_capabilities.py::TestNoIncomeTaxStateNote -v`
Expected: FAIL on `florida_state` membership and the FL note.

- [ ] **Step 3: Implement**

1. `projection.py:1173`: `STATE_SECTION_KEYS = ["california_540", "florida_state"]`

2. `yoy_diff.py` `OPTIONAL_SECTIONS`: append `"florida_state",` after `"california_540",`.

3. `capabilities.py`, beside `_COMMUNITY_PROPERTY_STATES` (~line 419):

```python
# States with NO individual income tax of any kind — the capability note
# declares this affirmatively instead of the "bracket-based ESTIMATE" text.
# WA and NH are DELIBERATELY excluded: WA levies the capital-gains excise
# (RCW 82.87) and NH is in its interest&dividends transition — a blanket
# "no tax" note would be a false promise there. They keep the estimate note
# until their modules land. tax_law's state.type="none" (FL template) is
# the cache-side twin of this set; both are code-reviewed constants.
_NO_INCOME_TAX_STATES = frozenset({"AK", "FL", "NV", "SD", "TX", "WY"})
```

4. In `_state_note` (~line 450), after the `if code == "CA":` branch, insert:

```python
    if code in _NO_INCOME_TAX_STATES:
        return (f"State ({code}): no individual income tax — no state return "
                "exists or is required. Verified affirmatively (for FL: "
                "Fla. Const. Art. VII, §5); the Schedule A sales-tax "
                "election is handled on the federal side.")
```

- [ ] **Step 4: Run the three suites**

Run: `python3 -m pytest tests/test_projection.py tests/test_yoy_diff.py tests/test_capabilities.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add agents/lib/projection.py agents/lib/yoy_diff.py agents/lib/capabilities.py tests/test_projection.py tests/test_yoy_diff.py tests/test_capabilities.py
git commit -m "feat(state): FL registrations — section keys, optional sections, affirmative capability note"
```

---

## Task 6: FL property-deadline calendar events

**Files:**
- Modify: `agents/lib/tax_calendar.py` — `_VALID_CATEGORIES` (~line 133) + `Category` Literal; new builder after the RMD builder; wiring in `build_events` (~line 834)
- Modify: `agents/templates/taxpayer_profile_template.json` — two flags
- Modify: `agents/skills/tax-calendar.md` — document the events
- Test: `tests/test_tax_calendar.py`

**Interfaces:**
- Consumes: existing `_validate_tax_year`, `_strict_bool`, `_UID_NAMESPACE`, `CalendarEvent`, `_state_due` (weekend-only state rollover) and `_observed_note` (grep their definitions and the CA 540-ES builder for exact signatures/usage before writing).
- Produces: category `"state_property"`; `events_fl_property(tax_year, *, business_tpp: bool, new_homestead: bool) -> list[CalendarEvent]`; UIDs `state-property-fl-homestead-{year}@…`, `state-property-fl-dr405-{year}@…` (UIDs carry the STATUTORY year — stable across regeneration).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tax_calendar.py` (match the module-reference style used by the file's existing tests):

```python
class TestFlPropertyEvents:
    def test_both_flags_emit_two_events(self):
        evs = tax_calendar.events_fl_property(2026, business_tpp=True,
                                              new_homestead=True)
        assert len(evs) == 2
        by_uid = {e.uid: e for e in evs}
        home = by_uid["state-property-fl-homestead-2026@tax-prep-skills"]
        dr405 = by_uid["state-property-fl-dr405-2026@tax-prep-skills"]
        # Mar 1 2026 is a Sunday — state deadlines roll past WEEKENDS only,
        # so the event date is the observed Mon Mar 2 with the statutory
        # date named in the description (the L1 _state_due convention).
        assert home.date == datetime.date(2026, 3, 2)
        assert "March 1" in home.description or "2026-03-01" in home.description
        # Apr 1 2026 is a Wednesday — no roll.
        assert dr405.date == datetime.date(2026, 4, 1)
        assert {e.category for e in evs} == {"state_property"}
        assert all(e.quarter_label == "" for e in evs)

    def test_flags_off_emit_nothing(self):
        assert tax_calendar.events_fl_property(
            2026, business_tpp=False, new_homestead=False) == []

    def test_build_events_gates_on_fl_residence(self):
        profile = {"tax_year": 2026, "address": {"state": "FL"},
                   "flags": {"has_business_tangible_property": True,
                             "fl_new_homestead": True}}
        evs = tax_calendar.build_events(profile, 2026)
        assert sum(1 for e in evs if e.category == "state_property") == 2

    def test_non_fl_resident_emits_none(self):
        profile = {"tax_year": 2026, "address": {"state": "CA"},
                   "flags": {"has_business_tangible_property": True,
                             "fl_new_homestead": True}}
        assert not any(e.category == "state_property"
                       for e in tax_calendar.build_events(profile, 2026))

    def test_fl_resident_without_flags_emits_none(self):
        profile = {"tax_year": 2026, "address": {"state": "FL"}, "flags": {}}
        assert not any(e.category == "state_property"
                       for e in tax_calendar.build_events(profile, 2026))

    def test_malformed_flag_hard_errors(self):
        profile = {"tax_year": 2026, "address": {"state": "FL"},
                   "flags": {"has_business_tangible_property": "yes"}}
        with pytest.raises(tax_calendar.CalendarError):
            tax_calendar.build_events(profile, 2026)
```

Verify the weekend claims before finalizing: `python3 -c "import datetime; print(datetime.date(2026,3,1).weekday(), datetime.date(2026,4,1).weekday())"` (6=Sunday, 2=Wednesday). If `_state_due`'s convention differs (e.g. it takes a date, or `_observed_note` signature differs), mirror the CA 540-ES builder's exact usage and adjust — the invariant to preserve: weekend-rolled event date + statutory date visible in the description.

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_tax_calendar.py::TestFlPropertyEvents -v`
Expected: AttributeError — no `events_fl_property`.

- [ ] **Step 3: Implement**

1. Add `"state_property"` to `_VALID_CATEGORIES` (~line 133) AND the `Category` Literal near it.

2. Add the builder after the RMD builder, mirroring the CA 540-ES builder's `_state_due`/`_observed_note` usage:

```python
def events_fl_property(tax_year: int, *, business_tpp: bool,
                       new_homestead: bool) -> list[CalendarEvent]:
    """Florida COUNTY property-tax reminders (FL has no income tax; these
    are the FL-specific compliance dates an individual typically has).

    - Homestead exemption application (Form DR-501): due March 1 with the
      county property appraiser (F.S. 196.011), first year a new FL
      homestead is claimed (renewals are automatic in most counties).
      Gated on user-asserted flags.fl_new_homestead.
    - Tangible personal property return (Form DR-405): due April 1 with the
      county property appraiser (F.S. 193.062) for business assets owned
      Jan 1; a TIMELY initial filing is required to claim the $25,000
      exemption (F.S. 196.183); late filing 5%/month up to 25%.
      Gated on flags.has_business_tangible_property.

    State/county deadlines roll past WEEKENDS ONLY (the L1 convention —
    county-holiday calendars are not modeled), statutory date named in the
    description. These follow RESIDENCE (address.state == FL), the opposite
    of PTET's entity-state gating.
    """
    _validate_tax_year(tax_year)
    events: list[CalendarEvent] = []
    if new_homestead:
        stat = datetime.date(tax_year, 3, 1)
        obs = _state_due(tax_year, 3, 1)
        events.append(CalendarEvent(
            uid=f"state-property-fl-homestead-{tax_year}@{_UID_NAMESPACE}",
            summary=f"FL homestead exemption application due ({tax_year})",
            date=obs,
            description=(
                f"Form DR-501 homestead exemption application for {tax_year} "
                f"is due March 1 with the county property appraiser "
                f"(F.S. 196.011). First-year filing; renewals are automatic "
                f"in most counties. Note: claiming FL homestead while "
                f"retaining an out-of-state residency-based benefit (e.g. "
                f"NY STAR) is disqualifying and a domicile-audit red flag."
                + _observed_note(stat, obs, state=True)
            ),
            category="state_property",
        ))
    if business_tpp:
        stat = datetime.date(tax_year, 4, 1)
        obs = _state_due(tax_year, 4, 1)
        events.append(CalendarEvent(
            uid=f"state-property-fl-dr405-{tax_year}@{_UID_NAMESPACE}",
            summary=f"FL tangible personal property return due ({tax_year})",
            date=obs,
            description=(
                f"Form DR-405 tangible personal property return for business "
                f"assets owned January 1, {tax_year} is due April 1 with the "
                f"county property appraiser (F.S. 193.062). A timely initial "
                f"filing is required to claim the $25,000 exemption "
                f"(F.S. 196.183). Late filing: 5%/month penalty up to 25%; "
                f"failure to file: 25%."
                + _observed_note(stat, obs, state=True)
            ),
            category="state_property",
        ))
    return events
```

(If `_observed_note` returns "" for un-rolled dates, unconditional concatenation is correct — confirm against the CA builder.)

3. Wire into `build_events` (~line 834), after the RMD block and before the K-1/PTET block:

```python
    # FL county property deadlines follow RESIDENCE (address.state) — the
    # opposite of PTET's entity-state rule below.
    resident_state = address.get("state")
    if isinstance(resident_state, str) and resident_state.strip().upper() == "FL":
        events.extend(events_fl_property(
            tax_year,
            business_tpp=_strict_bool(flags, "has_business_tangible_property"),
            new_homestead=_strict_bool(flags, "fl_new_homestead"),
        ))
```

4. Profile flags — in `agents/templates/taxpayer_profile_template.json`, after `"has_1099g_state_refund": false,` insert:

```json
    "has_business_tangible_property": false,
    "fl_new_homestead": false,
```

Validate: `python3 -c "import json; json.load(open('agents/templates/taxpayer_profile_template.json'))"`

5. `agents/skills/tax-calendar.md` — in the conditional-events list, add:

```markdown
- **FL property deadlines** (resident-state-gated, `address.state == "FL"`):
  homestead DR-501 (Mar 1, `flags.fl_new_homestead` — first year only, clear
  the flag after) and tangible personal property DR-405 (Apr 1,
  `flags.has_business_tangible_property`). County filings, not DOR.
  Weekend-only rollover per the state-deadline convention. Opposite gating
  of PTET events, which follow `flags.k1_states` (entity states).
```

- [ ] **Step 4: Run the calendar suite**

Run: `python3 -m pytest tests/test_tax_calendar.py -q`
Expected: all PASS (UID-uniqueness and sort invariants hold).

- [ ] **Step 5: Commit**

```bash
git add agents/lib/tax_calendar.py agents/templates/taxpayer_profile_template.json agents/skills/tax-calendar.md tests/test_tax_calendar.py
git commit -m "feat(calendar): FL homestead + DR-405 county property deadlines (state_property category)"
```

---

## Task 7: `florida_state` result section + CA-leak prune notes

**Files:**
- Modify: `agents/templates/result_template.json` — new section after `california_540`; notes at `ira_contribution.ca_savings` (line 528) and `balance_due_summary.california_refund` (line 535)
- Test: `tests/test_tax_law_fetcher.py` (append a template cross-check class — the template has no dedicated test file)

**Interfaces:**
- Produces: `florida_state` fields Task 8's skill text writes: `no_income_tax_statement`, `state_return_required`, `sales_tax_deduction.{table_amount, local_addition, big_ticket_tax, total_line_5a_candidate}`, `use_tax_note`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tax_law_fetcher.py`:

```python
class TestFloridaResultSection:
    def _template(self):
        import json
        from pathlib import Path
        return json.load(open(Path(__file__).resolve().parent.parent
                              / "agents" / "templates" / "result_template.json"))

    def test_florida_section_shape(self):
        fl = self._template()["florida_state"]
        assert fl["state_return_required"] is False
        assert "no personal income tax" in fl["no_income_tax_statement"]
        assert "total_line_5a_candidate" in fl["sales_tax_deduction"]

    def test_ca_only_fields_carry_prune_notes(self):
        t = self._template()
        assert "_note_ca_savings" in t["ira_contribution"]
        assert "_note_california_refund" in t["balance_due_summary"]
```

- [ ] **Step 2: Run to verify failure** — `python3 -m pytest tests/test_tax_law_fetcher.py::TestFloridaResultSection -v` → KeyError.

- [ ] **Step 3: Implement**

After the `california_540` section's closing brace, insert:

```json
  "florida_state": {
    "_include_if": "state is FL",
    "_note": "Affirmative absence, not an empty section: a FL client must see that $0 state tax is CORRECT, not a gap. No FL return artifact exists; the only computation here is the federal Schedule A line 5a sales-tax election input (compute_sales_tax_deduction -> compute_schedule_a state_local_sales_taxes). No computed-evidence pair on purpose — there is no computed state tax to protect from scaffold-reads (see docs/superpowers/specs/2026-08-01-fl-state-module-design.md non-goals).",
    "no_income_tax_statement": "Florida imposes no personal income tax (Fla. Const. Art. VII, §5). No Florida individual income tax return exists or is required. Wages, capital gains, retirement income, and interest/dividends are not state-taxed.",
    "state_return_required": false,
    "sales_tax_deduction": {
      "table_amount": 0,
      "local_addition": 0,
      "big_ticket_tax": 0,
      "total_line_5a_candidate": 0
    },
    "use_tax_note": null,
    "note": null
  },
```

In `ira_contribution`, after `"ca_savings": 0,` add:
`"_note_ca_savings": "CA-only field — /tax-setup removes it for non-CA taxpayers.",`

In `balance_due_summary`, after `"california_refund": 0,` add:
`"_note_california_refund": "CA-only legacy field — /tax-setup removes it for non-CA taxpayers; other states' balances live in their own state section.",`

Validate JSON parses, run the test class.

- [ ] **Step 4: Commit**

```bash
git add agents/templates/result_template.json tests/test_tax_law_fetcher.py
git commit -m "feat(template): florida_state affirmative no-return section + CA field prune notes"
```

---

## Task 8: Skill markdown — the type-none pathway

**Files:**
- Modify: `agents/skills/tax-setup.md` (state fetch block, anchor: line 563 `- For NY: Search…` / 564 `- For other states:`; interview flags section)
- Modify: `agents/skills/tax-compute.md` (anchor: line 1227 `**Other states:** Follow state-specific rules…`)
- Modify: `agents/skills/tax-parse.md` (line 241, `Missing state withholding` row)
- Modify: `agents/skills/tax-generate.md` (lines 57-58 state verification bullets)
- Modify: `agents/templates/answers_template.md` (Tax Result table)

Docs-only task; verification = field-name consistency with Task 7 + JSON-path consistency with Task 2.

- [ ] **Step 1: tax-setup.md — state fetch branch**

After the `- For NY: Search "New York {year} income tax brackets"` line and BEFORE `- For other states:`, insert:

```markdown
- **No-income-tax states (template `type: "none"` — FL):** do NOT search for
  income brackets; there are none, and an empty bracket list would keep the
  cache permanently INCOMPLETE. Record the pre-built FL template and fetch
  only its `null` field:
  - FL: Search `"Florida {taxpayer county} county discretionary sales surtax rate {year} site:floridarevenue.com"`
    → `state.sales_tax.county_surtax_rate` (decimal, e.g. 0.01; 0.0 for
    no-surtax counties). Pin the source: `_sources["state.FL"]` →
    the floridarevenue.com page (allowlisted authoritative host).
```

And extend the existing `- For other states:` line to end with: `— and if research shows the state levies NO individual income tax, use the FL pattern (a positive type: "none" declaration), never an empty bracket list.`

- [ ] **Step 2: tax-setup.md — FL interview additions**

In the interview section near the state-specific questions, add:

```markdown
**For Florida residents additionally ask:**
- "Did you buy (or plan to buy) a vehicle, boat, or other big-ticket item
  this year?" → record actual sales tax paid in `answers.md` (feeds the
  Schedule A line 5a sales-tax election in /tax-compute).
- "Do you own business equipment/furniture used in a sole proprietorship
  (as of Jan 1)?" → `flags.has_business_tangible_property` (DR-405 Apr 1
  calendar reminder).
- "Did you newly establish a Florida homestead this year?" →
  `flags.fl_new_homestead` (DR-501 Mar 1 reminder; clear the flag after the
  first year — renewals are automatic in most counties).

**Section pruning for no-income-tax states:** remove `california_540`,
`ira_contribution.ca_savings`, and `balance_due_summary.california_refund`
(see their `_note`s); KEEP `florida_state` for FL.
```

- [ ] **Step 3: tax-compute.md — step-10 branch**

Immediately BEFORE the line `**Other states:** Follow state-specific rules from \`tax_law_{year}.json\`.` (line 1227), insert:

```markdown
**No-income-tax states (`tax_law state.type == "none"` — FL):**
1. Do NOT compute a state return and do NOT leave the section empty.
   Populate `florida_state` from the template: `state_return_required:
   false` + the affirmative `no_income_tax_statement`. A blank section
   reads as "not done"; the statement reads as "done — and the answer is
   zero by law."
2. **Schedule A sales-tax input:** the SALT line-5a election for a
   no-income-tax state is general SALES tax (Box 17 is empty — there is no
   income tax to elect). Look up the IRS Optional State Sales Tax Table
   amount for {income, exemptions, FL} in the current-year Schedule A
   instructions (WebSearch), then:
   ```bash
   python3 -c "from agents.lib.tax_computations import compute_sales_tax_deduction; print(compute_sales_tax_deduction(TABLE_AMOUNT, 0.06, COUNTY_SURTAX_RATE, BIG_TICKET_TAX))"
   ```
   (COUNTY_SURTAX_RATE from `state.sales_tax.county_surtax_rate`;
   BIG_TICKET_TAX from answers.md.) Write the result into
   `florida_state.sales_tax_deduction` (`total` → `total_line_5a_candidate`)
   AND pass it as `state_local_sales_taxes` to the existing
   `compute_schedule_a` call — the income-vs-sales election and the SALT
   cap (`federal.salt_cap`) are computed there.
3. Skip state-refund pieces: no state withholding reconciliation, and a
   1099-G state-refund recovery can never arise from this state.
```

- [ ] **Step 4: tax-parse.md — Box-17 anomaly row**

Replace line 241 with:

```markdown
| Missing state withholding | W-2 has Box 1 income but Box 17 (state tax withheld) is $0 | Flag ONLY if `tax_law state.type != "none"` — FL/TX W-2s normally carry empty Boxes 15-17. If Box 15 names a DIFFERENT income-tax state than residence, flag nonresident-filing exposure instead |
```

- [ ] **Step 5: tax-generate.md — state step branch**

Replace the two bullets at lines 57-58 with:

```markdown
   - Determine the regime from `tax_law_{year}.json` `state.type`:
     - `"none"` (FL): render the affirmative statement block from
       `florida_state` (`no_income_tax_statement` + the sales-tax-deduction
       table when populated) instead of a state return page; PDF
       subject/header is "Federal" only.
     - otherwise: if taxpayer is in an income-tax state, verify the state
       return section is populated; if empty, warn "State return not
       computed — run /tax-compute first?"
```

- [ ] **Step 6: answers_template.md — Tax Result note**

After the `**State refund/(owed)**` row, add:
`_(State rows: for no-income-tax states, replace with one row "State: none — no individual income tax".)_`

- [ ] **Step 7: Consistency check + commit**

Confirm every referenced name exists: `florida_state.no_income_tax_statement`, `state_return_required`, `sales_tax_deduction.total_line_5a_candidate` (Task 7); `state.sales_tax.county_surtax_rate`, `_sources["state.FL"]` (Task 2); `compute_sales_tax_deduction` (Task 3); flags (Task 6).

```bash
git add agents/skills/tax-setup.md agents/skills/tax-compute.md agents/skills/tax-parse.md agents/skills/tax-generate.md agents/templates/answers_template.md
git commit -m "docs(skills): no-income-tax state pathway (setup fetch, compute step 10, parse box-17, generate)"
```

---

## Task 9: `/tax-review` no-income-tax step + playbook amendment

**Files:**
- Modify: `agents/skills/tax-review.md`
- Modify: `docs/contributing/state-module-playbook.md`

- [ ] **Step 1: tax-review.md — locate the anchor**

Run: `grep -n "^### Step 3" agents/skills/tax-review.md | tail -5` — insert the new step after the last 3.x step, numbered accordingly (content below assumes 3.8; renumber to fit).

- [ ] **Step 2: Insert the review step**

```markdown
### Step 3.8: No-income-tax state completeness (tax_law state.type == "none")

For FL residents, verify ABSENCE was handled affirmatively:

1. **Affirmative statement present:** `florida_state` exists with
   `state_return_required: false` and the constitutional citation — an
   empty/missing section is a finding, not a pass.
2. **Sales-tax election taken (if itemizing):**
   `florida_state.sales_tax_deduction.total_line_5a_candidate` populated and
   passed to `compute_schedule_a` as `state_local_sales_taxes`. An itemizer
   with $0 on line 5a is forgotten money.
3. **Use-tax note:** FL self-reports use tax on untaxed out-of-state/online
   purchases (Form DR-15MO). Surface in `florida_state.use_tax_note` —
   commonly ignored; an EA should mention it.
4. **Nonresident exposure — the big one:** no-tax-state residence does NOT
   remove nonresident filing on other states' source income (NY IT-203 for
   NY wages/K-1s), and with no resident-state return there is NO credit
   mechanism — the other state's tax is a pure cost. Cross-check
   `flags.k1_states`, W-2 Box 15 codes ≠ FL, out-of-state rental income.
   Nonresident returns are out of scope (capability gate) — flag loudly.
5. **Mover check (NY→FL corridor):** if the prior year shows an income-tax
   state (esp. NY), surface the domicile checklist: FL Declaration of
   Domicile (F.S. 222.17, recorded with the clerk of the circuit court),
   DR-501 homestead by Mar 1 (**claiming FL homestead while retaining NY
   STAR is disqualifying and audit bait**), driver license, voter + vehicle
   registration. NY's tests: statutory residency = 184+ NY days (any part
   of a day counts) + permanent place of abode substantially all year
   (>10 months per current Nonresident Audit Guidelines). Recommend
   contemporaneous day-count records for the move year.
```

- [ ] **Step 3: Playbook amendment**

In `docs/contributing/state-module-playbook.md`, after the "The pattern in one paragraph" section, add:

```markdown
## Variant: no-income-tax states (`type: "none"`)

FL is the worked example. The state template carries `type: "none"` +
`no_income_tax: true` and deliberately NO `brackets`/`standard_deduction`
keys (the freshness walker flags every empty list — structure out what you
will never fetch). The result section is INFORMATIONAL (an affirmative
no-return statement) with no computed-evidence pair — state that non-goal in
your spec. Register: `STATE_SECTION_KEYS`, `OPTIONAL_SECTIONS`,
`capabilities._NO_INCOME_TAX_STATES` (+ `_state_note`). Do NOT register a
tie-out withholding category (no Box 17 exists — both-halves-or-neither →
neither) or a withholding true-up block. The federal Schedule A sales-tax
election is the state's one computation (`compute_sales_tax_deduction` →
`compute_schedule_a state_local_sales_taxes`). States with a NON-income tax
base (WA's capital-gains excise) are NOT `type: "none"` — they get their own
regime type and module.
```

- [ ] **Step 4: Commit**

```bash
git add agents/skills/tax-review.md docs/contributing/state-module-playbook.md
git commit -m "docs(review+playbook): no-income-tax review step + type-none playbook variant"
```

---

## Task 10: Wrap-up — docs, changelog, full suite

**Files:**
- Modify: `CLAUDE.md` (test-count line; one-line state-regime bullet in Key Design Principles)
- Create: changelog entry per `docs/changelog/` convention (read `ls docs/changelog/` and mirror the existing file pattern)
- Modify: `docs/roadmap/2026-state-support-ny-fl-wa.md` (status for Epic 1 + a re-baseline note)

- [ ] **Step 1: Full suite + self-test**

Run: `source /path/to/tax-prep-skills/.venv/bin/activate && python3 -m pytest tests/ -q && python3 agents/lib/tax_computations.py`
Expected: 0 failures (baseline 1891 + this plan's additions). Note the exact count.

- [ ] **Step 2: CLAUDE.md**

- Replace the test-count number with the actual new total, appending to the coverage enumeration: `+ FL type-none state module (template/freshness, sales-tax input, harvest banner, capability note, property calendar, registrations)`.
- In Key Design Principles add one bullet: ``- **State regimes are typed** — `tax_law state.type` (`graduated_income` | `none`; WA's excise adds a third later): a no-income-tax state is a POSITIVE declaration, never an empty section. See docs/contributing/state-module-playbook.md ("Variant: no-income-tax states").``

- [ ] **Step 3: Changelog + roadmap**

- Add the per-PR narrative entry under `docs/changelog/` following the existing files' naming/format.
- In `docs/roadmap/2026-state-support-ny-fl-wa.md`: mark Epic 1 stories `✅ Done (this PR)`; annotate Epic 0: `0.1/0.2 delivered as the type-none discriminator + FL template (playbook-conformant); 0.3 registry SUPERSEDED by the state-module playbook's enumerate-and-register pattern (re-baseline ruling 2026-08-01); 0.4-0.7 live with the WA/NY modules.`

- [ ] **Step 4: Final commit + hand off push**

```bash
git add CLAUDE.md docs/changelog/ docs/roadmap/2026-state-support-ny-fl-wa.md
git commit -m "docs: FL module wrap-up — test count, changelog, roadmap status"
git log --oneline dfe74ae..HEAD
```

Print (do NOT run): `git push --no-verify -u origin feat/state-engine-florida`
After the user pushes: `gh pr create` (title `feat: Florida state module — type-none regime, sales-tax election input, property deadlines`), then the Codex review loop per convention.

---

## Follow-on plans (after this PR merges)

1. **WA capital-gains excise module** — backlog Epic 2 + a `ltcg_excise` regime type + the state compute function precedent (playbook step 3); revisit `capabilities._state_note` WA exclusion then.
2. **NY IT-201 module** — backlog Epic 3 + yoy per-section mover restructure (playbook step 5 note) + NY stub rework; likely 2-3 PRs.
3. **Residency guardrails** — backlog Epic 4 (partially covered today by the C1 `moved_states_this_year` gate).

## Self-review notes

- Backlog coverage: 1.1 → Tasks 2/7/8; 1.2 → Tasks 4/8 (+ freshness by construction in 2); 1.3 → Tasks 3/8 (election + cap already in `compute_schedule_a`); 1.4 → Task 6; 1.5 → Task 9. Epic 0 re-scoped per re-baseline (type discriminator without registry).
- New-base integrations the stale plan lacked: source-pin allowlist (Task 2), capability note (Task 5), `_state_due` weekend rollover (Task 6), `compute_schedule_a` wiring (Tasks 3/8), spec doc + playbook amendment (Tasks 1/9), changelog convention (Task 10), explicit tie-out/withholding non-goals (spec).
- Name consistency: `type`/`none` (Tasks 2→4→8→9), `florida_state` fields (7→8→9), flags (6→8), `compute_sales_tax_deduction` (3→8→9), `_NO_INCOME_TAX_STATES` (5→9).
