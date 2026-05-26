# Withholding True-Up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `/tax-withholding` — a read-only skill + `agents/lib/withholding.py` module that trues up YTD federal withholding against the **actual** computed liability in `result.json` and recommends a W-4 4(c) and/or 1040-ES schedule to close the gap.

**Architecture:** New pure-compute module mirroring `agents/lib/projection.py`: frozen dataclasses + functions + markdown renderer + CLI `_main`. The single structural difference from `/tax-project` is that the liability target comes from `result.json` `form_1040.line_24_total_tax` (actual) instead of annualized YTD income. All safe-harbor / 4(c) / voucher math is imported unchanged from `projection.py`. Never mutates `result.json` or `taxpayer_profile.json`.

**Tech Stack:** Python 3 stdlib (`dataclasses`, `datetime`, `argparse`, `json`, `pathlib`), pytest. Reuses `agents/lib/projection.py` and `agents/lib/rollover.py`.

---

## Context the engineer needs

- **Run tests with:** `source .venv/bin/activate && python3 -m pytest tests/test_withholding.py -v`
- **Functions imported from `agents/lib/projection.py`** (all module-level, already shipped):
  - `compute_safe_harbor(projected_tax, prior_tax, prior_agi, filing_status, withholding, estimates_paid) -> SafeHarbor`
  - `compute_w4_4c(shortfall, paychecks_remaining) -> W4Adjustment`
  - `compute_estimated_vouchers(required_annual, withholding, paid, as_of) -> list[Voucher]`
  - `parse_as_of(as_of_str, year) -> datetime.date`
  - `elapsed_fraction(as_of) -> float`
  - `paychecks_remaining(pay_frequency, frac) -> int`
  - `validate_pay_frequency(pay_frequency) -> str`
  - `load_prior_baseline(archive_dir, prior_year)` (returns a `PriorReturn` or `None`)
  - `_prior_tax_agi(prior) -> tuple[float|None, float|None]`
  - `_read_json(path: Path) -> dict`
  - `_num(value, field="") -> float`, `_money(v) -> str`
  - dataclasses `SafeHarbor`, `Voucher`, `W4Adjustment`
- **From `agents/lib/rollover.py`:** `RolloverError`, `_validate_subpath`, `_validate_year_arg`.
- **`result.json` shape:** `meta.tax_year` (int), `meta.filing_status` (str|None), `form_1040.line_24_total_tax` (number).
- **`projection_inputs.json` shape:** `tax_year`, `as_of_date`, `pay_frequency`, `ytd.federal_withholding`, `estimated_payments_made.federal`.
- **Test fixtures** go under `tests/fixtures/withholding/`. Files named `result.json` / `projection_inputs.json` collide with `.gitignore` unanchored rules, so stage them with `git add -f`.

---

## File Structure

- **Create `agents/lib/withholding.py`** — the whole feature module (one clear responsibility: federal withholding true-up). ~200 lines, matching `projection.py`'s size/shape.
- **Create `agents/skills/tax-withholding.md`** — the skill instructions.
- **Create `tests/test_withholding.py`** — the test suite.
- **Create `tests/fixtures/withholding/result.json`, `tests/fixtures/withholding/projection_inputs.json`, `tests/fixtures/withholding/archive/<prioryear>/result.json`** — fixtures for CLI integration tests.
- **Modify `CLAUDE.md`** — architecture flow note, read-only-planning-skills note, test-count bump.

---

### Task 1: Module scaffold — imports, error class, dataclasses, `read_liability`

**Files:**
- Create: `agents/lib/withholding.py`
- Test: `tests/test_withholding.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_withholding.py`:

```python
"""Tests for agents/lib/withholding.py — federal withholding true-up."""
import datetime
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib import withholding as wh


def test_read_liability_happy_path():
    result = {"meta": {"tax_year": 2026}, "form_1040": {"line_24_total_tax": 42000}}
    assert wh.read_liability(result, 2026) == 42000.0


def test_read_liability_wrong_year_raises():
    result = {"meta": {"tax_year": 2025}, "form_1040": {"line_24_total_tax": 42000}}
    with pytest.raises(wh.WithholdingError):
        wh.read_liability(result, 2026)


def test_read_liability_missing_line_raises():
    result = {"meta": {"tax_year": 2026}, "form_1040": {}}
    with pytest.raises(wh.WithholdingError):
        wh.read_liability(result, 2026)


def test_read_liability_nonpositive_raises():
    result = {"meta": {"tax_year": 2026}, "form_1040": {"line_24_total_tax": 0}}
    with pytest.raises(wh.WithholdingError):
        wh.read_liability(result, 2026)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_withholding.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents.lib.withholding'`

- [ ] **Step 3: Write minimal implementation**

Create `agents/lib/withholding.py`:

```python
#!/usr/bin/env python3
"""Federal withholding true-up for /tax-withholding.

Read-only: never writes result.json or taxpayer_profile.json. Reads the ACTUAL
computed liability from result.json (form_1040.line_24_total_tax), compares it to
YTD federal withholding + estimates (from projection_inputs.json) projected at the
current pace, and recommends a W-4 4(c) and/or 1040-ES schedule to close the gap.

Reuses projection.py for the safe-harbor / 4(c) / voucher math and rollover.py for
the prior-year baseline + path/year validation.
"""

import datetime
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agents.lib.projection import (  # noqa: E402
    Voucher,
    W4Adjustment,
    SafeHarbor,
    _money,
    _num,
    _prior_tax_agi,
    _read_json,
    compute_estimated_vouchers,
    compute_safe_harbor,
    compute_w4_4c,
    elapsed_fraction,
    load_prior_baseline,
    paychecks_remaining,
    parse_as_of,
    validate_pay_frequency,
)
from agents.lib.rollover import (  # noqa: E402
    RolloverError,
    _validate_subpath,
    _validate_year_arg,
)


class WithholdingError(Exception):
    """Raised when /tax-withholding cannot safely proceed."""


@dataclass(frozen=True)
class WithholdingInputs:
    ytd_withholding: float
    estimates_paid: float
    elapsed_fraction: float
    paychecks_remaining: int
    projected_full_year_withholding: float
    projected_paid: float
    pay_frequency: str


@dataclass(frozen=True)
class TrueUpTarget:
    name: str  # "safe_harbor" | "zero_balance"
    target_amount: float
    projected_paid: float
    shortfall: float
    covered: bool
    per_paycheck: float


@dataclass(frozen=True)
class TrueUpReport:
    year: int
    as_of: str
    pay_frequency: str
    liability: float
    baseline_source: "str | None"
    degraded: bool
    inputs: WithholdingInputs
    safe_harbor: SafeHarbor
    penalty_target: TrueUpTarget
    zero_target: TrueUpTarget
    vouchers: "list[Voucher]"
    w4_penalty: W4Adjustment
    w4_zero: W4Adjustment


def read_liability(result: dict, year: int) -> float:
    """Return form_1040.line_24_total_tax from result.json, validating tax year."""
    meta = result.get("meta", {}) or {}
    result_year = meta.get("tax_year")
    if result_year != year:
        raise WithholdingError(
            f"result.json is for TY{result_year}, not TY{year}. "
            f"Pass --year {result_year} or recompute for {year}."
        )
    f1040 = result.get("form_1040", {}) or {}
    raw = f1040.get("line_24_total_tax")
    if raw is None:
        raise WithholdingError(
            "result.json has no form_1040.line_24_total_tax — run /tax-compute first."
        )
    liability = _num(raw, "line_24_total_tax")
    if liability <= 0:
        raise WithholdingError(f"line_24_total_tax must be > 0, got {liability}.")
    return round(liability, 2)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_withholding.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add agents/lib/withholding.py tests/test_withholding.py
git commit -m "feat(withholding): module scaffold + read_liability with year/value guards"
```

---

### Task 2: `build_inputs` — project withholding at current pace

**Files:**
- Modify: `agents/lib/withholding.py`
- Test: `tests/test_withholding.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_withholding.py`:

```python
def test_build_inputs_projects_at_current_pace():
    # as_of June 30, 2026 -> day 181 of 365 -> frac ~0.4959
    as_of = datetime.date(2026, 6, 30)
    proj_inputs = {
        "pay_frequency": "biweekly",
        "ytd": {"federal_withholding": 10000},
        "estimated_payments_made": {"federal": 2000},
    }
    inp = wh.build_inputs(proj_inputs, as_of, "biweekly")
    frac = as_of.timetuple().tm_yday / 365
    assert inp.ytd_withholding == 10000.0
    assert inp.estimates_paid == 2000.0
    assert inp.projected_full_year_withholding == round(10000 / frac, 2)
    assert inp.projected_paid == round(round(10000 / frac, 2) + 2000, 2)
    assert inp.paychecks_remaining == max(0, round(26 * (1 - frac)))
    assert inp.pay_frequency == "biweekly"


def test_build_inputs_missing_blocks_default_zero():
    as_of = datetime.date(2026, 6, 30)
    inp = wh.build_inputs({"pay_frequency": "monthly"}, as_of, "monthly")
    assert inp.ytd_withholding == 0.0
    assert inp.estimates_paid == 0.0
    assert inp.projected_full_year_withholding == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_withholding.py -k build_inputs -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'build_inputs'`

- [ ] **Step 3: Write minimal implementation**

Append to `agents/lib/withholding.py` (after `read_liability`):

```python
def build_inputs(proj_inputs: dict, as_of: datetime.date,
                 pay_frequency: str) -> WithholdingInputs:
    frac = elapsed_fraction(as_of)
    if frac <= 0:
        raise WithholdingError("elapsed fraction must be > 0")
    ytd = proj_inputs.get("ytd", {}) or {}
    ytd_wh = _num(ytd.get("federal_withholding"), "ytd.federal_withholding")
    est = proj_inputs.get("estimated_payments_made", {}) or {}
    estimates_paid = _num(est.get("federal"), "estimated_payments_made.federal")
    proj_wh = round(ytd_wh / frac, 2)
    projected_paid = round(proj_wh + estimates_paid, 2)
    return WithholdingInputs(
        ytd_withholding=round(ytd_wh, 2),
        estimates_paid=round(estimates_paid, 2),
        elapsed_fraction=round(frac, 4),
        paychecks_remaining=paychecks_remaining(pay_frequency, frac),
        projected_full_year_withholding=proj_wh,
        projected_paid=projected_paid,
        pay_frequency=pay_frequency,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_withholding.py -k build_inputs -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add agents/lib/withholding.py tests/test_withholding.py
git commit -m "feat(withholding): build_inputs projects withholding at current YTD pace"
```

---

### Task 3: `build_report` — two targets, safe harbor, 4(c), vouchers

**Files:**
- Modify: `agents/lib/withholding.py`
- Test: `tests/test_withholding.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_withholding.py`:

```python
class _FakePrior:
    """Stand-in for projection.load_prior_baseline's PriorReturn."""
    def __init__(self, total_tax, agi):
        self.result = {"form_1040": {"line_24_total_tax": total_tax, "line_11_agi": agi}}


def _proj_inputs(ytd_wh, est=0):
    return {
        "pay_frequency": "biweekly",
        "ytd": {"federal_withholding": ytd_wh},
        "estimated_payments_made": {"federal": est},
    }


def test_build_report_underpaid_both_targets():
    # Liability 40000; prior tax 30000, prior AGI 120000 (factor 1.00) -> safe harbor
    # required = min(0.9*40000=36000, 1.0*30000=30000) = 30000.
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    as_of = datetime.date(2026, 6, 30)
    frac = as_of.timetuple().tm_yday / 365
    # YTD withholding 10000 -> projected full-year = 10000/frac (~20165), no estimates.
    prior = _FakePrior(30000, 120000)
    r = wh.build_report(result, _proj_inputs(10000), prior, 2026, as_of)
    proj_wh = round(10000 / frac, 2)
    assert r.liability == 40000.0
    assert r.degraded is False
    assert r.safe_harbor.required_annual == 30000.0
    assert r.penalty_target.name == "safe_harbor"
    assert r.penalty_target.target_amount == 30000.0
    assert r.penalty_target.shortfall == round(max(0.0, 30000 - proj_wh), 2)
    assert r.zero_target.name == "zero_balance"
    assert r.zero_target.target_amount == 40000.0
    assert r.zero_target.shortfall == round(max(0.0, 40000 - proj_wh), 2)
    # 4(c) per paycheck = shortfall / paychecks_remaining
    pr = max(0, round(26 * (1 - frac)))
    assert r.w4_penalty.per_paycheck == round(r.penalty_target.shortfall / pr, 2)
    assert r.zero_target.shortfall > r.penalty_target.shortfall


def test_build_report_covered_when_pace_exceeds_target():
    # High YTD withholding so projected full-year covers even the zero-balance target.
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    as_of = datetime.date(2026, 6, 30)
    prior = _FakePrior(30000, 120000)
    r = wh.build_report(result, _proj_inputs(30000), prior, 2026, as_of)
    assert r.penalty_target.covered is True
    assert r.penalty_target.shortfall == 0.0
    assert r.zero_target.covered is True
    assert r.w4_penalty.per_paycheck == 0.0


def test_build_report_degraded_mode_uses_90pct_only():
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    as_of = datetime.date(2026, 6, 30)
    r = wh.build_report(result, _proj_inputs(5000), None, 2026, as_of)
    assert r.degraded is True
    assert r.baseline_source is None
    # required = 0.90 * 40000 = 36000 (no prior-year test available)
    assert r.safe_harbor.required_annual == 36000.0


def test_build_report_prior_factor_110_above_threshold():
    # prior AGI 200000 (single) > 150000 -> factor 1.10 -> prior_based = 1.10*30000 = 33000
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 100000}}
    as_of = datetime.date(2026, 6, 30)
    prior = _FakePrior(30000, 200000)
    r = wh.build_report(result, _proj_inputs(1000), prior, 2026, as_of)
    # min(0.9*100000=90000, 1.10*30000=33000) = 33000
    assert r.safe_harbor.required_annual == 33000.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_withholding.py -k build_report -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'build_report'`

- [ ] **Step 3: Write minimal implementation**

Append to `agents/lib/withholding.py` (after `build_inputs`):

```python
def build_report(result: dict, proj_inputs: dict, prior, year: int,
                 as_of: datetime.date) -> TrueUpReport:
    pay_frequency = validate_pay_frequency(proj_inputs.get("pay_frequency", "biweekly"))
    liability = read_liability(result, year)
    inputs = build_inputs(proj_inputs, as_of, pay_frequency)

    filing_status = (result.get("meta", {}) or {}).get("filing_status")
    prior_tax, prior_agi = _prior_tax_agi(prior)
    safe_harbor = compute_safe_harbor(
        projected_tax=liability, prior_tax=prior_tax, prior_agi=prior_agi,
        filing_status=filing_status,
        withholding=inputs.projected_full_year_withholding,
        estimates_paid=inputs.estimates_paid,
    )

    w4_penalty = compute_w4_4c(safe_harbor.shortfall, inputs.paychecks_remaining)
    zero_shortfall = round(max(0.0, liability - inputs.projected_paid), 2)
    w4_zero = compute_w4_4c(zero_shortfall, inputs.paychecks_remaining)

    penalty_target = TrueUpTarget(
        name="safe_harbor", target_amount=safe_harbor.required_annual,
        projected_paid=inputs.projected_paid, shortfall=safe_harbor.shortfall,
        covered=safe_harbor.covered, per_paycheck=w4_penalty.per_paycheck,
    )
    zero_target = TrueUpTarget(
        name="zero_balance", target_amount=liability,
        projected_paid=inputs.projected_paid, shortfall=zero_shortfall,
        covered=zero_shortfall <= 0, per_paycheck=w4_zero.per_paycheck,
    )

    vouchers = compute_estimated_vouchers(
        safe_harbor.required_annual, inputs.projected_full_year_withholding,
        inputs.estimates_paid, as_of,
    )

    return TrueUpReport(
        year=year, as_of=as_of.strftime("%m-%d"), pay_frequency=pay_frequency,
        liability=liability,
        baseline_source=(f"archive/{year - 1}" if prior is not None else None),
        degraded=(prior is None), inputs=inputs, safe_harbor=safe_harbor,
        penalty_target=penalty_target, zero_target=zero_target,
        vouchers=vouchers, w4_penalty=w4_penalty, w4_zero=w4_zero,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_withholding.py -k build_report -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add agents/lib/withholding.py tests/test_withholding.py
git commit -m "feat(withholding): build_report computes safe-harbor + zero-balance targets"
```

---

### Task 4: `render_markdown` + `_report_to_dict`

**Files:**
- Modify: `agents/lib/withholding.py`
- Test: `tests/test_withholding.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_withholding.py`:

```python
def test_render_markdown_contains_key_sections():
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    as_of = datetime.date(2026, 6, 30)
    prior = _FakePrior(30000, 120000)
    r = wh.build_report(result, _proj_inputs(10000), prior, 2026, as_of)
    md = wh.render_markdown(r)
    assert "# Withholding True-Up — TY2026 (as of 06-30)" in md
    assert "## Liability & payments" in md
    assert "## Gap to targets" in md
    assert "## Recommended actions" in md
    assert "W-4 line 4(c)" in md
    assert "1040-ES" in md


def test_render_markdown_degraded_note():
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    as_of = datetime.date(2026, 6, 30)
    r = wh.build_report(result, _proj_inputs(5000), None, 2026, as_of)
    md = wh.render_markdown(r)
    assert "DEGRADED MODE" in md


def test_report_to_dict_roundtrips_json():
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    as_of = datetime.date(2026, 6, 30)
    prior = _FakePrior(30000, 120000)
    r = wh.build_report(result, _proj_inputs(10000), prior, 2026, as_of)
    d = wh._report_to_dict(r)
    s = json.dumps(d, default=str)  # must be JSON-serializable
    back = json.loads(s)
    assert back["liability"] == 40000.0
    assert back["penalty_target"]["name"] == "safe_harbor"
    assert back["zero_target"]["name"] == "zero_balance"
    assert isinstance(back["vouchers"], list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_withholding.py -k "render or report_to_dict" -v`
Expected: FAIL — `AttributeError: ... has no attribute 'render_markdown'`

- [ ] **Step 3: Write minimal implementation**

Append to `agents/lib/withholding.py` (after `build_report`):

```python
def render_markdown(r: TrueUpReport) -> str:
    i = r.inputs
    lines = [f"# Withholding True-Up — TY{r.year} (as of {r.as_of})", ""]

    lines += ["## Assumptions", ""]
    lines.append("- Liability target is the **actual computed** tax from result.json "
                 "(form_1040 line 24), not an annualized estimate.")
    lines.append(f"- Elapsed fraction of year: {i.elapsed_fraction:.0%}")
    lines.append(f"- Pay frequency: {r.pay_frequency} "
                 f"({i.paychecks_remaining} paychecks remaining)")
    lines.append("- Projected full-year withholding assumes the rest of the year "
                 "continues at the YTD withholding rate.")
    lines.append(f"- Prior-year baseline: {r.baseline_source or 'none — DEGRADED MODE'}")
    if r.degraded:
        lines.append("- **Degraded mode:** no prior-year return found. Safe harbor "
                     "uses the 90%-of-current-year test only.")
    if i.ytd_withholding == 0:
        lines.append("- **Note:** YTD federal withholding is $0 — verify it was entered.")
    lines.append("")

    lines += ["## Liability & payments", "",
              "| Item | Amount |", "|---|---:|",
              f"| Total tax (target) | {_money(r.liability)} |",
              f"| YTD federal withholding | {_money(i.ytd_withholding)} |",
              f"| Projected full-year withholding (current pace) | "
              f"{_money(i.projected_full_year_withholding)} |",
              f"| Estimates paid | {_money(i.estimates_paid)} |",
              f"| **Projected total paid** | {_money(i.projected_paid)} |", ""]

    lines += ["## Gap to targets", "",
              "| Target | Required | Projected paid | Shortfall | Status |",
              "|---|---:|---:|---:|---|"]
    for t, label in ((r.penalty_target, "Safe harbor (avoid penalty)"),
                     (r.zero_target, "Zero balance (full liability)")):
        lines.append(
            f"| {label} | {_money(t.target_amount)} | {_money(t.projected_paid)} | "
            f"{_money(t.shortfall)} | {'covered' if t.covered else 'underpaid'} |")
    lines.append("")

    lines += ["## Recommended actions", "",
              "Withholding is treated as paid evenly across the year, so a W-4 line "
              "4(c) bump on remaining paychecks can cure an underpayment retroactively "
              "— prefer it over uneven estimates when paychecks remain.", "",
              "### W-4 line 4(c) per-paycheck extra withholding",
              f"- To avoid penalty (safe harbor): **{_money(r.w4_penalty.per_paycheck)}"
              f"/paycheck** over {r.w4_penalty.paychecks_remaining} remaining paychecks",
              f"- To reach zero balance: **{_money(r.w4_zero.per_paycheck)}/paycheck**",
              "",
              "### 1040-ES voucher schedule (fallback)", "",
              "| Quarter | Due | Amount |", "|---|---|---:|"]
    for v in r.vouchers:
        lines.append(f"| Q{v.quarter} | {v.due_date} | {_money(v.amount)} |")
    lines += ["",
              "## Caveats",
              "- This is a planning projection, not a filed return. Re-run after any "
              "material income or withholding change.",
              "- Federal only; state withholding true-up is not yet covered.", ""]
    return "\n".join(lines) + "\n"


def _report_to_dict(r: TrueUpReport) -> dict:
    return {
        "year": r.year, "as_of": r.as_of, "pay_frequency": r.pay_frequency,
        "liability": r.liability, "baseline_source": r.baseline_source,
        "degraded": r.degraded, "inputs": vars(r.inputs),
        "safe_harbor": vars(r.safe_harbor),
        "penalty_target": vars(r.penalty_target),
        "zero_target": vars(r.zero_target),
        "vouchers": [vars(v) for v in r.vouchers],
        "w4_penalty": vars(r.w4_penalty), "w4_zero": vars(r.w4_zero),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_withholding.py -k "render or report_to_dict" -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add agents/lib/withholding.py tests/test_withholding.py
git commit -m "feat(withholding): markdown renderer + JSON serializer"
```

---

### Task 5: CLI `_main` + integration tests

**Files:**
- Modify: `agents/lib/withholding.py`
- Create: `tests/fixtures/withholding/result.json`, `tests/fixtures/withholding/projection_inputs.json`, `tests/fixtures/withholding/archive/2025/result.json`
- Test: `tests/test_withholding.py`

- [ ] **Step 1: Create fixtures**

Create `tests/fixtures/withholding/result.json`:

```json
{
  "meta": {"tax_year": 2026, "filing_status": "single"},
  "form_1040": {"line_24_total_tax": 40000, "line_11_agi": 180000}
}
```

Create `tests/fixtures/withholding/projection_inputs.json`:

```json
{
  "tax_year": 2026,
  "as_of_date": "06-30",
  "pay_frequency": "biweekly",
  "ytd": {"federal_withholding": 10000},
  "estimated_payments_made": {"federal": 0}
}
```

Create `tests/fixtures/withholding/archive/2025/result.json`:

```json
{
  "meta": {"tax_year": 2025, "filing_status": "single"},
  "form_1040": {"line_24_total_tax": 30000, "line_11_agi": 120000}
}
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_withholding.py`:

```python
FIX = Path(__file__).resolve().parent / "fixtures" / "withholding"


def test_main_json_output_ok(capsys):
    rc = wh._main([
        "--result", str(FIX / "result.json"),
        "--inputs", str(FIX / "projection_inputs.json"),
        "--workspace", str(FIX), "--year", "2026",
        "--as-of", "06-30", "--today", "2026-07-01", "--json",
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["year"] == 2026
    assert out["liability"] == 40000.0
    assert out["degraded"] is False  # archive/2025 present
    assert out["baseline_source"] == "archive/2025"


def test_main_markdown_output_ok(capsys):
    rc = wh._main([
        "--result", str(FIX / "result.json"),
        "--inputs", str(FIX / "projection_inputs.json"),
        "--workspace", str(FIX), "--year", "2026",
        "--as-of", "06-30", "--today", "2026-07-01",
    ])
    assert rc == 0
    assert "# Withholding True-Up — TY2026" in capsys.readouterr().out


def test_main_wrong_year_returns_1(capsys):
    rc = wh._main([
        "--result", str(FIX / "result.json"),
        "--inputs", str(FIX / "projection_inputs.json"),
        "--workspace", str(FIX), "--year", "2025",
        "--as-of", "06-30", "--today", "2026-07-01",
    ])
    assert rc == 1
    assert "ERROR" in capsys.readouterr().err


def test_main_future_as_of_returns_1(capsys):
    rc = wh._main([
        "--result", str(FIX / "result.json"),
        "--inputs", str(FIX / "projection_inputs.json"),
        "--workspace", str(FIX), "--year", "2026",
        "--as-of", "12-31", "--today", "2026-07-01",
    ])
    assert rc == 1
    assert "future" in capsys.readouterr().err.lower()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_withholding.py -k main -v`
Expected: FAIL — `AttributeError: ... has no attribute '_main'`

- [ ] **Step 4: Write minimal implementation**

Append to `agents/lib/withholding.py` (after `_report_to_dict`):

```python
def _main(argv: list) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="agents.lib.withholding")
    parser.add_argument("--result", required=True, help="path to result.json")
    parser.add_argument("--inputs", required=True, help="path to projection_inputs.json")
    parser.add_argument("--workspace", required=True, help="current workspace directory")
    parser.add_argument("--year", required=True, help="current tax year")
    parser.add_argument("--as-of", dest="as_of", default=None, help="MM-DD (default: today)")
    parser.add_argument("--today", default=None,
                        help="override today's date as YYYY-MM-DD (testing only)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    args = parser.parse_args(argv)

    try:
        year = _validate_year_arg(args.year)
        workspace = Path(args.workspace).resolve()
        result_path = _validate_subpath(Path(args.result).resolve(), workspace)
        inputs_path = _validate_subpath(Path(args.inputs).resolve(), workspace)
        result = _read_json(result_path)
        proj_inputs = _read_json(inputs_path)

        inputs_year = proj_inputs.get("tax_year")
        if inputs_year is not None and inputs_year != year:
            raise WithholdingError(
                f"projection_inputs.json is for TY{inputs_year}, not TY{year}."
            )

        today = (datetime.date.fromisoformat(args.today) if args.today
                 else datetime.date.today())
        as_of_str = args.as_of or proj_inputs.get("as_of_date") or today.strftime("%m-%d")
        as_of = parse_as_of(as_of_str, year)
        if as_of > today:
            raise WithholdingError(
                f"as_of {as_of.isoformat()} is in the future (today {today.isoformat()})"
            )

        prior = load_prior_baseline(workspace / "archive" / str(year - 1), year - 1)
    except WithholdingError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except RolloverError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    try:
        report = build_report(result, proj_inputs, prior, year, as_of)
    except (WithholdingError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(_report_to_dict(report), indent=2, default=str))
    else:
        print(render_markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
```

Note: `_read_json` raises `ProjectionError` (imported from projection) on a missing/malformed file, which is a subclass of `Exception` but not `WithholdingError`. Import and catch it too — add to the imports from `agents.lib.projection`: `ProjectionError`, and add an `except ProjectionError` arm mirroring the `RolloverError` arm in both try-blocks.

Concretely, update the projection import block to include `ProjectionError`, and in `_main` change both error-handling blocks to also catch it:

```python
    except (WithholdingError, ProjectionError, RolloverError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
```

(Replace the two separate `except` arms in the first try-block with this single combined arm, and use `except (WithholdingError, ProjectionError, ValueError)` for the `build_report` try-block.)

- [ ] **Step 5: Run test to verify it passes**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_withholding.py -k main -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Run the full module test file**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_withholding.py -v`
Expected: PASS (17 passed)

- [ ] **Step 7: Commit (force-add fixtures)**

```bash
git add -f tests/fixtures/withholding/result.json tests/fixtures/withholding/projection_inputs.json tests/fixtures/withholding/archive/2025/result.json
git add agents/lib/withholding.py tests/test_withholding.py
git commit -m "feat(withholding): CLI _main + integration tests with --today override"
```

---

### Task 6: Skill markdown + CLAUDE.md updates

**Files:**
- Create: `agents/skills/tax-withholding.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Create the skill file**

Create `agents/skills/tax-withholding.md`:

```markdown
---
name: tax-withholding
description: Withholding true-up — compare YTD federal withholding against the actual computed liability in result.json, then recommend a W-4 4(c) and/or 1040-ES schedule to close the gap
---

# /tax-withholding — Federal Withholding True-Up

Compares YTD federal withholding + estimated payments (projected to year-end at the
current pace) against the **actual** computed liability in result.json, then recommends
a W-4 line 4(c) per-paycheck adjustment and/or a 1040-ES voucher schedule to hit the
safe-harbor (penalty-avoidance) and zero-balance targets. Read-only: never modifies
result.json or taxpayer_profile.json. Re-runnable.

Distinct from `/tax-project`: this trues up against the real computed number
(result.json line 24), not an annualized estimate. Run it after `/tax-compute` has
produced a current-year result.json. Federal only (state not yet covered).

## Prerequisites
- `result.json` with a computed `form_1040.line_24_total_tax` (else: run `/tax-compute`).
- `projection_inputs.json` (else: run `/tax-project` first, or create it from
  `agents/templates/projection_inputs_template.json` and fill YTD federal withholding,
  estimated payments, pay frequency, and as-of date).
- `tax_law_{year}.json`.
- Optional: `archive/{year-1}/` — enables the 100%/110%-of-prior safe harbor. Without
  it, runs in degraded mode (90%-of-current test only).

## Steps

1. **Validate prerequisites.** Confirm `result.json`, `projection_inputs.json`, and
   `tax_law_{year}.json` exist. If `result.json` lacks a computed line 24, stop and tell
   the user to run `/tax-compute`.

2. **Resolve prior-year baseline.** If `archive/{year-1}/` exists it is used for the
   prior-year safe harbor; otherwise note degraded mode to the user.

3. **Compute.** Run:
   ```bash
   python3 -m agents.lib.withholding --result result.json --inputs projection_inputs.json \
       --workspace . --year {year} --as-of {MM-DD} --json
   ```
   (Omit `--as-of` to use today.)

4. **Render + save.** Run the same command without `--json` to get the markdown report.
   Save it to `withholding/TY{year}_asof_{MM-DD}.md` and `chmod 600` it. Print the report.

5. **Audit note.** Append one line to `answers.md` (NO dollar PII duplicated):
   ```
   ## Withholding true-up TY{year} as-of {MM-DD} — run {today}. Safe-harbor: {covered/underpaid}.
   ```

## Security & safety
- Subagents (if any) must NOT have network access and must NOT write outside `withholding/`.
- Never extract or store full SSNs.
- This skill never writes `result.json` or `taxpayer_profile.json`.
- All written files (`withholding/*.md`) → `chmod 600`.
```

- [ ] **Step 2: Commit the skill**

```bash
git add agents/skills/tax-withholding.md
git commit -m "feat(withholding): /tax-withholding skill instructions"
```

- [ ] **Step 3: Update CLAUDE.md — architecture flow note**

In `CLAUDE.md`, find the "Optional planning branch" block (after the linear-flow diagram) and add a bullet for `/tax-withholding` next to `/tax-project`:

```markdown
- **`/tax-withholding`** — withholding true-up: after `/tax-compute`, compares YTD federal
  withholding (projected to year-end at current pace) against the actual computed liability
  in `result.json`, and recommends a W-4 4(c) and/or 1040-ES schedule to hit the
  safe-harbor and zero-balance targets. Read-only; federal only. Uses `archive/{year-1}/`
  for the prior-year safe harbor when present, else degrades to the 90%-of-current test.
```

- [ ] **Step 4: Update CLAUDE.md — read-only planning skills note**

In the "### Read-only planning skills" section, extend the first bullet's file list to include `withholding.py`:

Change `Follow \`agents/lib/projection.py\` / \`yoy_diff.py\` for future planning features (backlog #3/#5/#6).`
to `Follow \`agents/lib/projection.py\` / \`yoy_diff.py\` / \`withholding.py\` for future planning features (backlog #3/#6).`

- [ ] **Step 5: Update CLAUDE.md — test count**

In the "### Testing" section, update the test-count line. Run the full suite first to get the exact number:

```bash
source .venv/bin/activate && python3 -m pytest tests/ -q 2>&1 | tail -3
```

Then change `199 tests covering ...` to `<N> tests covering ... + withholding true-up + ...` reflecting the new total (199 + the count in `test_withholding.py`).

- [ ] **Step 6: Verify full suite passes**

Run: `source .venv/bin/activate && python3 -m pytest tests/ -q 2>&1 | tail -3`
Expected: all tests pass (199 + new withholding tests).

- [ ] **Step 7: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(withholding): wire /tax-withholding into CLAUDE.md flow + bump test count"
```

---

## Self-Review

**1. Spec coverage:**
- Distinct-from-/tax-project (result.json target) → Task 1 `read_liability` + Task 3.
- Reuse projection helpers → Task 1 imports; Tasks 3–5 use them.
- Inputs (result.json, projection_inputs.json, archive, year guards) → Task 1 (result year), Task 5 (inputs year, paths).
- Current-pace withholding projection → Task 2.
- Two targets + 4(c) lead + voucher fallback → Task 3 + Task 4 renderer.
- Federal-only scope, degraded mode → Tasks 3/4 + skill.
- Markdown sections → Task 4.
- Error handling (`WithholdingError`, future as_of, malformed JSON) → Tasks 1/5.
- Skill + CLAUDE.md + test-count → Task 6.
- Tests (~16–18) → Tasks 1–5 (4+2+4+3+4 = 17).
All spec requirements map to a task. No gaps.

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N". All code blocks complete.

**3. Type consistency:** `WithholdingInputs`, `TrueUpTarget`, `TrueUpReport` field names match between definitions (Task 1) and usage (Tasks 3–5). `build_report(result, proj_inputs, prior, year, as_of)` signature consistent across Tasks 3 and 5. `read_liability(result, year)` consistent. `compute_safe_harbor` keyword args match projection's signature. `_report_to_dict` keys match the dataclass fields and the CLI/test usage (`liability`, `penalty_target.name`, `zero_target.name`, `vouchers`).
