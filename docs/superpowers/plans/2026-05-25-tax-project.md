# /tax-project Mid-Year Projection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `/tax-project`, a read-only mid-year projection that annualizes YTD figures, projects full-year federal (and conditional state) tax, and outputs safe-harbor + W-4 4(c) + 1040-ES guidance.

**Architecture:** A deterministic pure-Python compute module (`agents/lib/projection.py`) — annualize → project tax → safe harbor → W-4/vouchers → markdown renderer + CLI — reusing `tax_computations.py` for tax math and `rollover.py`'s `load_prior`/`_validate_subpath`/`_validate_year_arg` for the prior-year baseline. A thin skill (`agents/skills/tax-project.md`) gathers inputs and calls the CLI. Never mutates the filed return.

**Tech Stack:** Python 3 (stdlib `dataclasses`, `datetime`, `argparse`, `json`), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-25-tax-project-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `agents/lib/projection.py` | All projection compute: dataclasses, validation/date helpers, `annualize`, `project_federal_tax`, `project_state_tax`, `compute_safe_harbor`, `compute_w4_4c`, `compute_estimated_vouchers`, `build_report`, `render_markdown`, `load_prior_baseline`, CLI `_main`. |
| `agents/templates/projection_inputs_template.json` | Stable input schema (YTD + overrides + recurring). |
| `agents/skills/tax-project.md` | Orchestration: gather inputs, call CLI, render, audit-log. |
| `tests/test_projection.py` | Unit + integration tests. |
| `tests/fixtures/projection/` | Synthetic workspace: `tax_law_2026.json`, `taxpayer_profile.json`, `projection_inputs.json`, `archive/2025/{result,taxpayer_profile}.json`. |
| `CLAUDE.md` | Pipeline diagram + test-count bump. |
| `.gitignore` | Ignore `projections/` and `projection_inputs.json`. |

**Status-key mapping** (used throughout): `tax_law` defines `single/mfj/mfs/hoh` for brackets/standard_deduction, but only `single/mfj` for capital-gains limits and CTC phase-out. So two helpers: `_full_status_key` (MFJ/QSS→mfj, MFS→mfs, HOH→hoh, else single) and `_binary_status_key` (MFJ/QSS→mfj, else single).

---

## Task 1: Module scaffolding — errors, dataclasses, status & numeric helpers

**Files:**
- Create: `agents/lib/projection.py`
- Create: `agents/templates/projection_inputs_template.json`
- Modify: `.gitignore`
- Test: `tests/test_projection.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_projection.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib import projection as P  # noqa: E402


def test_status_key_helpers():
    assert P._full_status_key("MFJ") == "mfj"
    assert P._full_status_key("QSS") == "mfj"
    assert P._full_status_key("MFS") == "mfs"
    assert P._full_status_key("HOH") == "hoh"
    assert P._full_status_key("single") == "single"
    assert P._full_status_key(None) == "single"
    assert P._binary_status_key("QSS") == "mfj"
    assert P._binary_status_key("HOH") == "single"


def test_num_coerces_and_rejects():
    assert P._num(None) == 0.0
    assert P._num("$1,250.50") == 1250.50
    assert P._num(42) == 42.0
    import pytest
    with pytest.raises(P.ProjectionError):
        P._num("abc", "wages")
    with pytest.raises(P.ProjectionError):
        P._num(True, "wages")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_projection.py -v`
Expected: FAIL — `ModuleNotFoundError` / `AttributeError: module 'agents.lib.projection' has no attribute ...`

- [ ] **Step 3: Create the module with scaffolding**

```python
# agents/lib/projection.py
#!/usr/bin/env python3
"""Mid-year tax projection for /tax-project.

Read-only: never writes result.json or taxpayer_profile.json. Pure compute
functions + a markdown renderer + a CLI. Reuses tax_computations.py for tax
math and rollover.py for the prior-year baseline + path/year validation.
"""

import calendar
import datetime
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agents.lib.rollover import (  # noqa: E402
    RolloverError,
    _validate_subpath,
    _validate_year_arg,
    load_prior,
)
from agents.lib.tax_computations import (  # noqa: E402
    _coerce_numeric,
    compute_additional_medicare,
    compute_bracket_tax,
    compute_child_tax_credit,
    compute_qbi_deduction,
    compute_qdcg_tax,
    compute_se_tax,
)


class ProjectionError(Exception):
    """Raised when /tax-project cannot safely proceed."""


PAY_PERIODS = {"weekly": 52, "biweekly": 26, "semimonthly": 24, "monthly": 12}
_MFJ_LIKE = {"MFJ", "QSS"}
ES_DUE_DATES = [(1, (4, 15)), (2, (6, 15)), (3, (9, 15)), (4, (1, 15))]  # Q4 due Jan 15 next year


def _full_status_key(filing_status: str | None) -> str:
    fs = (filing_status or "").upper()
    return {"MFJ": "mfj", "QSS": "mfj", "MFS": "mfs", "HOH": "hoh", "SINGLE": "single"}.get(fs, "single")


def _binary_status_key(filing_status: str | None) -> str:
    return "mfj" if (filing_status or "").upper() in _MFJ_LIKE else "single"


def _num(value, field: str = "") -> float:
    """Coerce to float; None→0.0; reject bool and unparseable strings."""
    if value is None:
        return 0.0
    try:
        n = _coerce_numeric(value, default=None)
    except TypeError:
        raise ProjectionError(f"{field or 'value'} must be numeric, got bool")
    if n is None:
        raise ProjectionError(f"{field or 'value'} is not numeric: {value!r}")
    return n


@dataclass(frozen=True)
class ProjectedInputs:
    wages: float
    federal_withholding: float
    state_withholding: float
    medicare_wages: float
    medicare_withheld: float
    ss_wages: float
    capital_gains: float
    qualified_dividends: float
    ordinary_dividends: float
    taxable_interest: float
    self_employment_net: float
    other_income: float
    estimated_fed_paid: float
    estimated_state_paid: float
    elapsed_fraction: float
    paychecks_remaining: int


@dataclass(frozen=True)
class FederalProjection:
    gross_income: float
    adjustments: float
    agi: float
    taxable_income: float
    tax_before_credits: float
    ctc: float
    se_tax: float
    additional_medicare: float
    total_tax: float
    withholding: float
    estimates_paid: float
    balance: float  # >0 owed, <0 refund
    used_qdcg: bool


@dataclass(frozen=True)
class StateProjection:
    code: str
    taxable_income: float
    tax: float
    withholding: float
    estimates_paid: float
    balance: float


@dataclass(frozen=True)
class SafeHarbor:
    pct90_current: float
    prior_tax: float | None
    prior_factor: float | None
    prior_based: float | None
    required_annual: float
    withholding: float
    estimates_paid: float
    shortfall: float
    covered: bool
    degraded: bool


@dataclass(frozen=True)
class W4Adjustment:
    shortfall: float
    paychecks_remaining: int
    per_paycheck: float


@dataclass(frozen=True)
class Voucher:
    quarter: int
    due_date: str
    amount: float


@dataclass(frozen=True)
class ProjectionReport:
    year: int
    as_of: str
    pay_frequency: str
    baseline_source: str | None
    degraded: bool
    proj: ProjectedInputs
    federal: FederalProjection
    state: Any  # StateProjection | None
    safe_harbor: SafeHarbor
    vouchers: list
    w4_penalty: W4Adjustment
    w4_zero: W4Adjustment
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_projection.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Create the input template**

```json
// agents/templates/projection_inputs_template.json
{
  "_comment": "YTD actuals + full-year forecast overrides for /tax-project. Read-only input; never holds computed results.",
  "tax_year": null,
  "as_of_date": null,
  "pay_frequency": "biweekly",
  "ytd": {
    "wages": null,
    "federal_withholding": null,
    "state_withholding": null,
    "ss_wages": null,
    "medicare_wages": null,
    "medicare_withheld": null
  },
  "forecast_overrides": {
    "_comment": "Lumpy/known items entered as FULL-YEAR amounts; NOT annualized.",
    "year_end_bonus": 0,
    "rsu_vesting": 0,
    "capital_gains": 0,
    "self_employment_net": 0,
    "other_income": 0
  },
  "recurring_from_prior": {
    "_comment": "Defaults pulled from archive/{year-1}/ by the skill; user-editable. Treated as full-year.",
    "taxable_interest": null,
    "ordinary_dividends": null,
    "qualified_dividends": null
  },
  "estimated_payments_made": { "federal": 0, "state": 0 },
  "_paystub_parser_hook": "Future: a /tax-parse-paystub feature may populate the ytd block automatically. Keep ytd as the stable interface."
}
```

- [ ] **Step 6: Add a test that the template is valid and complete**

```python
def test_input_template_is_valid_and_complete():
    import json
    from pathlib import Path
    tmpl = json.loads(
        (Path(__file__).resolve().parent.parent
         / "agents/templates/projection_inputs_template.json").read_text()
    )
    for section in ("ytd", "forecast_overrides", "recurring_from_prior", "estimated_payments_made"):
        assert section in tmpl
    assert tmpl["pay_frequency"] in P.PAY_PERIODS
    assert set(tmpl["ytd"]) >= {"wages", "federal_withholding", "state_withholding"}
```

- [ ] **Step 7: Update .gitignore**

Add these lines to `.gitignore` (under the runtime/PII section):

```
projections/
projection_inputs.json
```

- [ ] **Step 8: Run tests and commit**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_projection.py -v`
Expected: PASS (3 tests)

```bash
git add agents/lib/projection.py agents/templates/projection_inputs_template.json tests/test_projection.py .gitignore
git commit -m "feat(project): module scaffolding, dataclasses, input template"
```

---

## Task 2: Date & pay-period helpers

**Files:**
- Modify: `agents/lib/projection.py`
- Test: `tests/test_projection.py`

- [ ] **Step 1: Write the failing tests**

```python
import datetime
import pytest


def test_parse_as_of_valid():
    assert P.parse_as_of("06-30", 2026) == datetime.date(2026, 6, 30)


def test_parse_as_of_rejects_jan_1():
    with pytest.raises(P.ProjectionError):
        P.parse_as_of("01-01", 2026)


def test_parse_as_of_rejects_impossible_date():
    with pytest.raises(P.ProjectionError):
        P.parse_as_of("02-30", 2026)
    with pytest.raises(P.ProjectionError):
        P.parse_as_of("6/30", 2026)


def test_elapsed_fraction_midyear_and_leap():
    # 2026 non-leap: Jul 1 is day 182 of 365
    assert P.elapsed_fraction(datetime.date(2026, 7, 1)) == pytest.approx(182 / 365, abs=1e-6)
    # 2028 leap: Dec 31 is day 366 of 366 → 1.0
    assert P.elapsed_fraction(datetime.date(2028, 12, 31)) == pytest.approx(1.0, abs=1e-6)


def test_paychecks_remaining_by_frequency():
    # Half the year elapsed → half the periods remain
    assert P.paychecks_remaining("biweekly", 0.5) == 13
    assert P.paychecks_remaining("weekly", 0.5) == 26
    assert P.paychecks_remaining("monthly", 0.5) == 6
    assert P.paychecks_remaining("semimonthly", 0.5) == 12
    assert P.paychecks_remaining("biweekly", 1.0) == 0


def test_validate_pay_frequency_rejects_unknown():
    with pytest.raises(P.ProjectionError):
        P.validate_pay_frequency("annually")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_projection.py -k "as_of or elapsed or paychecks or pay_frequency" -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'parse_as_of'`

- [ ] **Step 3: Implement the helpers (append to projection.py)**

```python
def validate_pay_frequency(pay_frequency: str) -> str:
    if pay_frequency not in PAY_PERIODS:
        raise ProjectionError(
            f"pay_frequency must be one of {sorted(PAY_PERIODS)}, got {pay_frequency!r}"
        )
    return pay_frequency


def parse_as_of(as_of_str: str, year: int) -> datetime.date:
    """Parse 'MM-DD' into a date in `year`. Rejects Jan 1 and impossible dates."""
    if not isinstance(as_of_str, str):
        raise ProjectionError(f"as_of must be a 'MM-DD' string, got {as_of_str!r}")
    parts = as_of_str.split("-")
    if len(parts) != 2:
        raise ProjectionError(f"as_of must be 'MM-DD', got {as_of_str!r}")
    try:
        month, day = int(parts[0]), int(parts[1])
        d = datetime.date(year, month, day)
    except ValueError:
        raise ProjectionError(f"as_of is not a real calendar date: {as_of_str!r}")
    if month == 1 and day == 1:
        raise ProjectionError("as_of must be after Jan 1 — no YTD data to annualize")
    return d


def _days_in_year(year: int) -> int:
    return 366 if calendar.isleap(year) else 365


def elapsed_fraction(as_of: datetime.date) -> float:
    return as_of.timetuple().tm_yday / _days_in_year(as_of.year)


def paychecks_remaining(pay_frequency: str, fraction: float) -> int:
    total = PAY_PERIODS[validate_pay_frequency(pay_frequency)]
    return max(0, round(total * (1 - fraction)))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_projection.py -k "as_of or elapsed or paychecks or pay_frequency" -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add agents/lib/projection.py tests/test_projection.py
git commit -m "feat(project): date and pay-period helpers"
```

---

## Task 3: annualize()

**Files:**
- Modify: `agents/lib/projection.py`
- Test: `tests/test_projection.py`

- [ ] **Step 1: Write the failing tests**

```python
def _ytd_half_year():
    # as_of that yields exactly 0.5 elapsed fraction in 2026 is awkward; use a known date
    # and assert proportional scaling instead.
    return {
        "wages": 50000, "federal_withholding": 6000, "state_withholding": 2000,
        "ss_wages": 50000, "medicare_wages": 51000, "medicare_withheld": 740,
    }


def test_annualize_scales_steady_items_and_adds_overrides_full_year():
    as_of = datetime.date(2026, 7, 2)  # day 183 of 365
    frac = P.elapsed_fraction(as_of)
    overrides = {"year_end_bonus": 10000, "rsu_vesting": 5000, "capital_gains": 3000,
                 "self_employment_net": 0, "other_income": 1000}
    recurring = {"taxable_interest": 800, "ordinary_dividends": 1200, "qualified_dividends": 900}
    proj = P.annualize(_ytd_half_year(), as_of, "biweekly", overrides, recurring,
                       {"federal": 0, "state": 0})
    # Steady wages annualized, plus full-year bonus + rsu (NOT scaled)
    assert proj.wages == pytest.approx(round(50000 / frac, 2) + 15000, abs=0.01)
    assert proj.federal_withholding == pytest.approx(round(6000 / frac, 2), abs=0.01)
    # Overrides are full-year, untouched by fraction
    assert proj.capital_gains == 3000
    assert proj.other_income == 1000
    # Recurring treated as full-year
    assert proj.taxable_interest == 800
    assert proj.qualified_dividends == 900
    assert proj.paychecks_remaining == P.paychecks_remaining("biweekly", frac)


def test_annualize_carries_estimates_and_defaults_medicare_wages_to_wages():
    as_of = datetime.date(2026, 7, 2)
    proj = P.annualize({"wages": 50000, "federal_withholding": 6000}, as_of, "monthly",
                       {}, {}, {"federal": 1500, "state": 300})
    assert proj.estimated_fed_paid == 1500
    assert proj.estimated_state_paid == 300
    # medicare_wages absent → falls back to projected wages
    assert proj.medicare_wages == proj.wages
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_projection.py -k annualize -v`
Expected: FAIL — `AttributeError: ... 'annualize'`

- [ ] **Step 3: Implement annualize (append to projection.py)**

```python
def annualize(ytd: dict, as_of: datetime.date, pay_frequency: str,
              overrides: dict, recurring: dict, estimates: dict) -> ProjectedInputs:
    frac = elapsed_fraction(as_of)
    if frac <= 0:
        raise ProjectionError("elapsed fraction must be > 0")
    ytd = ytd or {}
    overrides = overrides or {}
    recurring = recurring or {}
    estimates = estimates or {}

    def ann(key):
        return round(_num(ytd.get(key), key) / frac, 2)

    base_wages = ann("wages")
    wages = round(base_wages + _num(overrides.get("year_end_bonus"), "year_end_bonus")
                  + _num(overrides.get("rsu_vesting"), "rsu_vesting"), 2)
    ytd_medicare = _num(ytd.get("medicare_wages"), "medicare_wages")
    medicare_wages = round(ytd_medicare / frac, 2) if ytd_medicare else wages

    return ProjectedInputs(
        wages=wages,
        federal_withholding=ann("federal_withholding"),
        state_withholding=ann("state_withholding"),
        medicare_wages=medicare_wages,
        medicare_withheld=ann("medicare_withheld"),
        ss_wages=ann("ss_wages"),
        capital_gains=_num(overrides.get("capital_gains"), "capital_gains"),
        qualified_dividends=_num(recurring.get("qualified_dividends"), "qualified_dividends"),
        ordinary_dividends=_num(recurring.get("ordinary_dividends"), "ordinary_dividends"),
        taxable_interest=_num(recurring.get("taxable_interest"), "taxable_interest"),
        self_employment_net=_num(overrides.get("self_employment_net"), "self_employment_net"),
        other_income=_num(overrides.get("other_income"), "other_income"),
        estimated_fed_paid=_num(estimates.get("federal"), "estimated.federal"),
        estimated_state_paid=_num(estimates.get("state"), "estimated.state"),
        elapsed_fraction=round(frac, 4),
        paychecks_remaining=paychecks_remaining(pay_frequency, frac),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_projection.py -k annualize -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add agents/lib/projection.py tests/test_projection.py
git commit -m "feat(project): annualize YTD into full-year projected inputs"
```

---

## Task 4: project_federal_tax()

**Files:**
- Modify: `agents/lib/projection.py`
- Test: `tests/test_projection.py`

- [ ] **Step 1: Write the failing tests**

```python
def _tax_law_2026():
    return {
        "federal": {
            "filing_statuses": {
                "mfj": {"standard_deduction": 30000,
                        "brackets": [{"rate": 0.10, "min": 0, "max": 20000},
                                     {"rate": 0.20, "min": 20000, "max": 200000},
                                     {"rate": 0.30, "min": 200000, "max": None}]},
                "single": {"standard_deduction": 15000,
                           "brackets": [{"rate": 0.10, "min": 0, "max": 10000},
                                        {"rate": 0.20, "min": 10000, "max": None}]},
            },
            "capital_gains_rates": {"zero_pct_limit": {"mfj": 90000, "single": 45000},
                                    "fifteen_pct_limit": {"mfj": 550000, "single": 490000}},
            "child_tax_credit": {"amount_per_child": 2000, "phase_out_start": {"mfj": 400000, "single": 200000}},
            "additional_medicare_tax": {"rate": 0.009, "threshold": {"single": 200000, "mfj": 250000, "mfs": 125000}},
            "social_security": {"wage_base": 176100},
            "qbi_deduction_rate": 0.20,
        },
        "state": {"code": "CA", "standard_deduction": {"mfj": 11000, "single": 5500},
                  "brackets": [{"rate": 0.01, "min": 0, "max": 20000},
                               {"rate": 0.05, "min": 20000, "max": None}]},
    }


def _profile_mfj(deps=None):
    return {"filing_status": "MFJ", "dependents": deps or []}


def test_project_federal_wages_only_matches_bracket_tax():
    proj = P.ProjectedInputs(
        wages=100000, federal_withholding=12000, state_withholding=0, medicare_wages=100000,
        medicare_withheld=1450, ss_wages=100000, capital_gains=0, qualified_dividends=0,
        ordinary_dividends=0, taxable_interest=0, self_employment_net=0, other_income=0,
        estimated_fed_paid=0, estimated_state_paid=0, elapsed_fraction=0.5, paychecks_remaining=13)
    fed = P.project_federal_tax(proj, _profile_mfj(), _tax_law_2026())
    # taxable = 100000 - 30000 std = 70000
    # tax = 10% * 20000 + 20% * (70000-20000) = 2000 + 10000 = 12000
    assert fed.taxable_income == 70000
    assert fed.tax_before_credits == 12000
    assert fed.total_tax == 12000
    assert fed.balance == 0  # 12000 tax - 12000 withholding
    assert fed.used_qdcg is False


def test_project_federal_capital_gains_engages_qdcg():
    proj = P.ProjectedInputs(
        wages=100000, federal_withholding=0, state_withholding=0, medicare_wages=100000,
        medicare_withheld=0, ss_wages=100000, capital_gains=40000, qualified_dividends=0,
        ordinary_dividends=0, taxable_interest=0, self_employment_net=0, other_income=0,
        estimated_fed_paid=0, estimated_state_paid=0, elapsed_fraction=0.5, paychecks_remaining=13)
    fed = P.project_federal_tax(proj, _profile_mfj(), _tax_law_2026())
    assert fed.used_qdcg is True
    assert fed.total_tax > 0


def test_project_federal_applies_ctc():
    proj = P.ProjectedInputs(
        wages=100000, federal_withholding=0, state_withholding=0, medicare_wages=100000,
        medicare_withheld=0, ss_wages=100000, capital_gains=0, qualified_dividends=0,
        ordinary_dividends=0, taxable_interest=0, self_employment_net=0, other_income=0,
        estimated_fed_paid=0, estimated_state_paid=0, elapsed_fraction=0.5, paychecks_remaining=13)
    fed = P.project_federal_tax(proj, _profile_mfj([{"ctc_eligible": True}, {"ctc_eligible": True}]),
                                _tax_law_2026())
    # 2 kids * 2000 = 4000 credit; tax_before_credits 12000 → total 8000
    assert fed.ctc == 4000
    assert fed.total_tax == 8000


def test_project_federal_se_income_adds_se_tax():
    proj = P.ProjectedInputs(
        wages=0, federal_withholding=0, state_withholding=0, medicare_wages=0, medicare_withheld=0,
        ss_wages=0, capital_gains=0, qualified_dividends=0, ordinary_dividends=0, taxable_interest=0,
        self_employment_net=50000, other_income=0, estimated_fed_paid=0, estimated_state_paid=0,
        elapsed_fraction=0.5, paychecks_remaining=13)
    fed = P.project_federal_tax(proj, _profile_mfj(), _tax_law_2026())
    assert fed.se_tax > 0
    # adjustments include half of SE tax
    assert fed.adjustments == pytest.approx(fed.se_tax / 2, abs=0.5)


def test_project_federal_raises_without_brackets():
    proj = P.ProjectedInputs(
        wages=1, federal_withholding=0, state_withholding=0, medicare_wages=1, medicare_withheld=0,
        ss_wages=1, capital_gains=0, qualified_dividends=0, ordinary_dividends=0, taxable_interest=0,
        self_employment_net=0, other_income=0, estimated_fed_paid=0, estimated_state_paid=0,
        elapsed_fraction=0.5, paychecks_remaining=13)
    with pytest.raises(P.ProjectionError):
        P.project_federal_tax(proj, _profile_mfj(), {"federal": {"filing_statuses": {}}})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_projection.py -k project_federal -v`
Expected: FAIL — `AttributeError: ... 'project_federal_tax'`

- [ ] **Step 3: Implement project_federal_tax (append to projection.py)**

```python
def project_federal_tax(proj: ProjectedInputs, profile: dict, tax_law: dict) -> FederalProjection:
    fs = profile.get("filing_status")
    full_key = _full_status_key(fs)
    bin_key = _binary_status_key(fs)
    fed = tax_law.get("federal", {}) or {}
    fstatus = (fed.get("filing_statuses", {}) or {}).get(full_key, {}) or {}
    brackets = fstatus.get("brackets") or []
    if not brackets:
        raise ProjectionError(
            f"No federal brackets for status '{full_key}' in tax_law. Run /tax-setup to fetch."
        )
    std = _num(fstatus.get("standard_deduction"), "standard_deduction")

    # SE tax + QBI (only when self-employed)
    if proj.self_employment_net > 0:
        se = compute_se_tax(
            proj.self_employment_net,
            ss_wage_base=(fed.get("social_security", {}) or {}).get("wage_base"),
            w2_ss_wages=proj.ss_wages,
        )
        qbi = compute_qbi_deduction(
            proj.self_employment_net, 0.0, rate=fed.get("qbi_deduction_rate", 0.20)
        )["total_deduction"]
    else:
        se = {"se_tax": 0.0, "deductible_half": 0.0, "se_earnings": 0.0}
        qbi = 0.0

    gross = (proj.wages + proj.capital_gains + proj.ordinary_dividends
             + proj.taxable_interest + proj.other_income + proj.self_employment_net)
    adjustments = se["deductible_half"]
    agi = max(0.0, gross - adjustments)
    taxable_income = max(0.0, agi - std - qbi)

    cg = fed.get("capital_gains_rates", {}) or {}
    cg_zero = (cg.get("zero_pct_limit") or {}).get(bin_key)
    cg_fifteen = (cg.get("fifteen_pct_limit") or {}).get(bin_key)
    net_ltcg = max(0.0, proj.capital_gains)
    if (proj.qualified_dividends or net_ltcg) and cg_zero is not None and cg_fifteen is not None:
        qd = compute_qdcg_tax(taxable_income, proj.qualified_dividends, net_ltcg,
                              brackets, cg_zero, cg_fifteen)
        tax_before_credits = qd["final_tax"]
        used_qdcg = qd["used_qdcg_worksheet"]
    else:
        tax_before_credits, _ = compute_bracket_tax(taxable_income, brackets)
        used_qdcg = False

    deps = profile.get("dependents") or []
    n_ctc = sum(1 for d in deps if isinstance(d, dict) and d.get("ctc_eligible"))
    ctc_cfg = fed.get("child_tax_credit", {}) or {}
    if n_ctc and ctc_cfg.get("amount_per_child"):
        ctc = compute_child_tax_credit(
            n_ctc, ctc_cfg["amount_per_child"], agi,
            (ctc_cfg.get("phase_out_start") or {}).get(bin_key) or 0,
        )["credit_allowed"]
    else:
        ctc = 0.0

    addl_cfg = fed.get("additional_medicare_tax", {}) or {}
    threshold_map = addl_cfg.get("threshold") or {}
    addl_threshold = threshold_map.get(full_key) or threshold_map.get(bin_key)
    medicare_wages = proj.medicare_wages or proj.wages
    if addl_threshold is not None:
        addl = compute_additional_medicare(
            medicare_wages, se["se_earnings"], addl_threshold,
            proj.medicare_withheld, rate=addl_cfg.get("rate", 0.009),
        )["additional_tax"]
    else:
        addl = 0.0

    total_tax = max(0.0, tax_before_credits - ctc) + se["se_tax"] + addl
    balance = round(total_tax - proj.federal_withholding - proj.estimated_fed_paid, 2)

    return FederalProjection(
        gross_income=round(gross, 2), adjustments=round(adjustments, 2), agi=round(agi, 2),
        taxable_income=round(taxable_income, 2), tax_before_credits=round(tax_before_credits, 2),
        ctc=round(ctc, 2), se_tax=round(se["se_tax"], 2), additional_medicare=round(addl, 2),
        total_tax=round(total_tax, 2), withholding=round(proj.federal_withholding, 2),
        estimates_paid=round(proj.estimated_fed_paid, 2), balance=balance, used_qdcg=used_qdcg,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_projection.py -k project_federal -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add agents/lib/projection.py tests/test_projection.py
git commit -m "feat(project): project full-year federal tax via tax_computations"
```

---

## Task 5: compute_safe_harbor()

**Files:**
- Modify: `agents/lib/projection.py`
- Test: `tests/test_projection.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_safe_harbor_picks_lower_of_90pct_and_prior():
    # projected 20000 → 90% = 18000; prior 15000 *1.0 = 15000 → required 15000
    sh = P.compute_safe_harbor(20000, 15000, 120000, "MFJ", withholding=10000, estimates_paid=0)
    assert sh.pct90_current == 18000
    assert sh.prior_factor == 1.00
    assert sh.prior_based == 15000
    assert sh.required_annual == 15000
    assert sh.shortfall == 5000
    assert sh.covered is False
    assert sh.degraded is False


def test_safe_harbor_110pct_when_high_prior_agi():
    sh = P.compute_safe_harbor(50000, 30000, 200000, "MFJ", withholding=0, estimates_paid=0)
    assert sh.prior_factor == 1.10
    assert sh.prior_based == 33000
    assert sh.required_annual == 33000  # min(45000, 33000)


def test_safe_harbor_mfs_75k_threshold():
    sh = P.compute_safe_harbor(50000, 30000, 80000, "MFS", withholding=0, estimates_paid=0)
    assert sh.prior_factor == 1.10  # 80k > 75k MFS threshold


def test_safe_harbor_degraded_when_no_prior():
    sh = P.compute_safe_harbor(20000, None, None, "single", withholding=21000, estimates_paid=0)
    assert sh.degraded is True
    assert sh.required_annual == 18000  # 90%-only
    assert sh.shortfall == 0
    assert sh.covered is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_projection.py -k safe_harbor -v`
Expected: FAIL — `AttributeError: ... 'compute_safe_harbor'`

- [ ] **Step 3: Implement compute_safe_harbor (append to projection.py)**

```python
def compute_safe_harbor(projected_tax: float, prior_tax: float | None, prior_agi: float | None,
                        filing_status: str | None, withholding: float,
                        estimates_paid: float) -> SafeHarbor:
    pct90 = round(0.90 * projected_tax, 2)
    degraded = prior_tax is None
    if degraded:
        required = pct90
        prior_factor = None
        prior_based = None
    else:
        threshold = 75000 if (filing_status or "").upper() == "MFS" else 150000
        prior_factor = 1.10 if (prior_agi or 0) > threshold else 1.00
        prior_based = round(prior_factor * prior_tax, 2)
        required = round(min(pct90, prior_based), 2)
    paid = round(withholding + estimates_paid, 2)
    shortfall = round(max(0.0, required - paid), 2)
    return SafeHarbor(
        pct90_current=pct90, prior_tax=prior_tax, prior_factor=prior_factor,
        prior_based=prior_based, required_annual=required, withholding=round(withholding, 2),
        estimates_paid=round(estimates_paid, 2), shortfall=shortfall,
        covered=shortfall <= 0, degraded=degraded,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_projection.py -k safe_harbor -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add agents/lib/projection.py tests/test_projection.py
git commit -m "feat(project): safe-harbor analysis (90% current vs 100/110% prior)"
```

---

## Task 6: compute_w4_4c() and compute_estimated_vouchers()

**Files:**
- Modify: `agents/lib/projection.py`
- Test: `tests/test_projection.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_w4_4c_divides_shortfall_over_paychecks():
    w4 = P.compute_w4_4c(5200, 13)
    assert w4.per_paycheck == 400.0
    assert w4.paychecks_remaining == 13


def test_w4_4c_zero_shortfall_and_no_paychecks():
    assert P.compute_w4_4c(0, 13).per_paycheck == 0.0
    assert P.compute_w4_4c(5000, 0).per_paycheck == 0.0


def test_vouchers_split_remaining_quarters():
    # as_of May 1 2026 → Q1 (Apr 15) past; Q2/Q3 open + Q4 (Jan 15 2027) → 3 open
    as_of = datetime.date(2026, 5, 1)
    vouchers = P.compute_estimated_vouchers(required_annual=12000, withholding=3000,
                                            paid=0, as_of=as_of)
    assert len(vouchers) == 3
    assert vouchers[0].quarter == 2
    assert vouchers[-1].due_date == "2027-01-15"
    assert sum(v.amount for v in vouchers) == pytest.approx(9000, abs=0.05)


def test_vouchers_zero_when_covered():
    as_of = datetime.date(2026, 5, 1)
    vouchers = P.compute_estimated_vouchers(10000, 12000, 0, as_of)
    assert all(v.amount == 0.0 for v in vouchers)


def test_vouchers_all_past_folds_into_final():
    as_of = datetime.date(2026, 12, 20)  # all of Q1-Q3 past
    vouchers = P.compute_estimated_vouchers(8000, 0, 0, as_of)
    assert len(vouchers) == 1
    assert vouchers[0].due_date == "2027-01-15"
    assert vouchers[0].amount == pytest.approx(8000, abs=0.01)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_projection.py -k "w4 or voucher" -v`
Expected: FAIL — `AttributeError: ... 'compute_w4_4c'`

- [ ] **Step 3: Implement both functions (append to projection.py)**

```python
def compute_w4_4c(shortfall: float, paychecks_remaining: int) -> W4Adjustment:
    if paychecks_remaining <= 0:
        per = 0.0
    else:
        per = round(max(0.0, shortfall) / paychecks_remaining, 2)
    return W4Adjustment(shortfall=round(shortfall, 2),
                        paychecks_remaining=paychecks_remaining, per_paycheck=per)


def compute_estimated_vouchers(required_annual: float, withholding: float, paid: float,
                               as_of: datetime.date) -> list:
    shortfall = max(0.0, required_annual - withholding - paid)
    open_q = []
    for q, (m, d) in ES_DUE_DATES:
        if q == 4:
            open_q.append((4, f"{as_of.year + 1}-01-15"))
            continue
        due = datetime.date(as_of.year, m, d)
        if due >= as_of:
            open_q.append((q, due.isoformat()))
    if not open_q:
        open_q = [(4, f"{as_of.year + 1}-01-15")]
    if shortfall <= 0:
        return [Voucher(q, dd, 0.0) for q, dd in open_q]
    per = round(shortfall / len(open_q), 2)
    return [Voucher(q, dd, per) for q, dd in open_q]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_projection.py -k "w4 or voucher" -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add agents/lib/projection.py tests/test_projection.py
git commit -m "feat(project): W-4 4(c) per-paycheck + 1040-ES voucher schedule"
```

---

## Task 7: project_state_tax()

**Files:**
- Modify: `agents/lib/projection.py`
- Test: `tests/test_projection.py`

- [ ] **Step 1: Write the failing tests**

```python
class _Prior:
    def __init__(self, result):
        self.result = result


def test_state_projection_when_baseline_and_brackets_present():
    proj = P.ProjectedInputs(
        wages=100000, federal_withholding=0, state_withholding=4000, medicare_wages=100000,
        medicare_withheld=0, ss_wages=100000, capital_gains=0, qualified_dividends=0,
        ordinary_dividends=0, taxable_interest=0, self_employment_net=0, other_income=0,
        estimated_fed_paid=0, estimated_state_paid=0, elapsed_fraction=0.5, paychecks_remaining=13)
    prior = _Prior({"california_540": {"ca_total_tax": 5000}})
    st = P.project_state_tax(proj, prior, _tax_law_2026(), filing_status="MFJ")
    # state taxable = 100000 - 11000 std = 89000
    # tax = 1% * 20000 + 5% * (89000-20000) = 200 + 3450 = 3650
    assert st is not None
    assert st.taxable_income == 89000
    assert st.tax == 3650
    assert st.balance == round(3650 - 4000, 2)  # -350 refund


def test_state_projection_none_without_prior():
    proj = P.ProjectedInputs(
        wages=100000, federal_withholding=0, state_withholding=0, medicare_wages=100000,
        medicare_withheld=0, ss_wages=100000, capital_gains=0, qualified_dividends=0,
        ordinary_dividends=0, taxable_interest=0, self_employment_net=0, other_income=0,
        estimated_fed_paid=0, estimated_state_paid=0, elapsed_fraction=0.5, paychecks_remaining=13)
    assert P.project_state_tax(proj, None, _tax_law_2026(), "MFJ") is None


def test_state_projection_none_without_state_brackets():
    proj = P.ProjectedInputs(
        wages=100000, federal_withholding=0, state_withholding=0, medicare_wages=100000,
        medicare_withheld=0, ss_wages=100000, capital_gains=0, qualified_dividends=0,
        ordinary_dividends=0, taxable_interest=0, self_employment_net=0, other_income=0,
        estimated_fed_paid=0, estimated_state_paid=0, elapsed_fraction=0.5, paychecks_remaining=13)
    prior = _Prior({"california_540": {"ca_total_tax": 5000}})
    law = _tax_law_2026()
    law["state"] = {}
    assert P.project_state_tax(proj, prior, law, "MFJ") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_projection.py -k state_projection -v`
Expected: FAIL — `AttributeError: ... 'project_state_tax'`

- [ ] **Step 3: Implement project_state_tax (append to projection.py)**

```python
# State result sections known to the toolkit; extend as more states are added.
STATE_SECTION_KEYS = ["california_540"]


def _detect_prior_state(prior_result: dict) -> str | None:
    for key in STATE_SECTION_KEYS:
        if key in (prior_result or {}):
            return key
    return None


def project_state_tax(proj: ProjectedInputs, prior, tax_law: dict,
                      filing_status: str | None):
    """Project state tax only when a prior-year state section exists AND
    tax_law carries state brackets. Returns StateProjection or None."""
    if prior is None:
        return None
    if _detect_prior_state(getattr(prior, "result", {})) is None:
        return None
    state_cfg = tax_law.get("state") or {}
    brackets = state_cfg.get("brackets") or []
    if not brackets:
        return None
    bin_key = _binary_status_key(filing_status)
    std_map = state_cfg.get("standard_deduction") or {}
    std = _num(std_map.get(bin_key), "state.standard_deduction")
    state_ti = max(0.0, (proj.wages + proj.capital_gains + proj.ordinary_dividends
                         + proj.taxable_interest + proj.other_income
                         + proj.self_employment_net) - std)
    tax, _ = compute_bracket_tax(state_ti, brackets)
    balance = round(tax - proj.state_withholding - proj.estimated_state_paid, 2)
    return StateProjection(
        code=state_cfg.get("code", "STATE"), taxable_income=round(state_ti, 2),
        tax=round(tax, 2), withholding=round(proj.state_withholding, 2),
        estimates_paid=round(proj.estimated_state_paid, 2), balance=balance,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_projection.py -k state_projection -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add agents/lib/projection.py tests/test_projection.py
git commit -m "feat(project): conditional state tax projection"
```

---

## Task 8: build_report() + render_markdown() + _report_to_dict()

**Files:**
- Modify: `agents/lib/projection.py`
- Test: `tests/test_projection.py`

- [ ] **Step 1: Write the failing tests**

```python
def _build_sample_report(degraded=False, with_state=True):
    law = _tax_law_2026()
    inputs = {
        "pay_frequency": "biweekly",
        "ytd": {"wages": 50000, "federal_withholding": 6000, "state_withholding": 2000},
        "forecast_overrides": {"capital_gains": 0},
        "recurring_from_prior": {"taxable_interest": 0},
        "estimated_payments_made": {"federal": 0, "state": 0},
    }
    prior = None if degraded else _Prior(
        {"california_540": {"ca_total_tax": 5000},
         "form_1040": {"line_24_total_tax": 15000, "line_11_agi": 120000}})
    if not with_state:
        law["state"] = {}
    return P.build_report(inputs=inputs, profile=_profile_mfj(), tax_law=law,
                          prior=prior, year=2026, as_of=datetime.date(2026, 7, 2))


def test_build_report_assembles_all_sections():
    r = P.build_report(
        inputs={"pay_frequency": "biweekly",
                "ytd": {"wages": 50000, "federal_withholding": 6000},
                "estimated_payments_made": {"federal": 0, "state": 0}},
        profile=_profile_mfj(),
        tax_law=_tax_law_2026(),
        prior=_Prior({"form_1040": {"line_24_total_tax": 15000, "line_11_agi": 120000}}),
        year=2026, as_of=datetime.date(2026, 7, 2))
    assert r.year == 2026
    assert r.federal.total_tax > 0
    assert r.safe_harbor.prior_tax == 15000
    assert r.w4_penalty.per_paycheck >= 0
    assert r.w4_zero.per_paycheck >= 0


def test_render_markdown_degraded_banner_and_state_skip_note():
    r = _build_sample_report(degraded=True, with_state=False)
    md = P.render_markdown(r)
    assert "Mid-Year Tax Projection" in md
    assert "degraded" in md.lower()
    assert "skipped" in md.lower()


def test_render_markdown_no_crash_on_zeros():
    r = P.build_report(
        inputs={"pay_frequency": "monthly", "ytd": {"wages": 1},
                "estimated_payments_made": {}},
        profile=_profile_mfj(), tax_law=_tax_law_2026(), prior=None,
        year=2026, as_of=datetime.date(2026, 7, 2))
    md = P.render_markdown(r)
    assert isinstance(md, str) and len(md) > 0


def test_report_to_dict_is_json_serializable():
    import json
    r = _build_sample_report()
    json.dumps(P._report_to_dict(r))  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_projection.py -k "build_report or render or report_to_dict" -v`
Expected: FAIL — `AttributeError: ... 'build_report'`

- [ ] **Step 3: Implement build_report, render_markdown, _report_to_dict (append to projection.py)**

```python
def _prior_tax_agi(prior):
    """Extract (prior_total_tax, prior_agi) from a PriorReturn-like object, or (None, None)."""
    if prior is None:
        return None, None
    f1040 = (getattr(prior, "result", {}) or {}).get("form_1040", {}) or {}
    tax = f1040.get("line_24_total_tax")
    agi = f1040.get("line_11_agi")
    return (float(tax) if isinstance(tax, (int, float)) else None,
            float(agi) if isinstance(agi, (int, float)) else None)


def build_report(inputs: dict, profile: dict, tax_law: dict, prior,
                 year: int, as_of: datetime.date) -> ProjectionReport:
    pay_frequency = validate_pay_frequency(inputs.get("pay_frequency", "biweekly"))
    proj = annualize(inputs.get("ytd", {}), as_of, pay_frequency,
                     inputs.get("forecast_overrides", {}),
                     inputs.get("recurring_from_prior", {}),
                     inputs.get("estimated_payments_made", {}))
    fs = profile.get("filing_status")
    federal = project_federal_tax(proj, profile, tax_law)
    state = project_state_tax(proj, prior, tax_law, fs)

    prior_tax, prior_agi = _prior_tax_agi(prior)
    safe_harbor = compute_safe_harbor(
        federal.total_tax, prior_tax, prior_agi, fs,
        withholding=federal.withholding, estimates_paid=federal.estimates_paid)

    vouchers = compute_estimated_vouchers(
        safe_harbor.required_annual, federal.withholding, federal.estimates_paid, as_of)

    # Two W-4 targets: cover safe harbor (avoid penalty) and cover full liability (zero balance)
    full_shortfall = max(0.0, federal.total_tax - federal.withholding - federal.estimates_paid)
    w4_penalty = compute_w4_4c(safe_harbor.shortfall, proj.paychecks_remaining)
    w4_zero = compute_w4_4c(full_shortfall, proj.paychecks_remaining)

    return ProjectionReport(
        year=year, as_of=as_of.strftime("%m-%d"), pay_frequency=pay_frequency,
        baseline_source=(f"archive/{year - 1}" if prior is not None else None),
        degraded=(prior is None), proj=proj, federal=federal, state=state,
        safe_harbor=safe_harbor, vouchers=vouchers, w4_penalty=w4_penalty, w4_zero=w4_zero)


def _money(v) -> str:
    if v is None:
        return "—"
    body = f"${abs(v):,.0f}"
    return f"({body})" if v < 0 else body


def render_markdown(r: ProjectionReport) -> str:
    f = r.federal
    sh = r.safe_harbor
    lines = [f"# Mid-Year Tax Projection — TY{r.year} (as of {r.as_of})", ""]

    lines += ["## Assumptions", ""]
    lines.append(f"- Elapsed fraction of year: {r.proj.elapsed_fraction:.0%}")
    lines.append(f"- Pay frequency: {r.pay_frequency} ({r.proj.paychecks_remaining} paychecks remaining)")
    lines.append(f"- Prior-year baseline: {r.baseline_source or 'none — DEGRADED MODE'}")
    if r.degraded:
        lines.append("- **Degraded mode:** no prior-year return found. Safe harbor uses the "
                     "90%-of-current-year test only; recurring income defaulted to 0.")
    if r.proj.federal_withholding == 0:
        lines.append("- **Note:** projected federal withholding is $0 — verify YTD withholding was entered.")
    lines.append("")

    lines += ["## Projected full-year federal", "",
              "| Item | Amount |", "|---|---:|",
              f"| Gross income | {_money(f.gross_income)} |",
              f"| Adjustments | {_money(f.adjustments)} |",
              f"| AGI | {_money(f.agi)} |",
              f"| Taxable income | {_money(f.taxable_income)} |",
              f"| Tax before credits{' (QDCG worksheet)' if f.used_qdcg else ''} | {_money(f.tax_before_credits)} |",
              f"| Child Tax Credit | {_money(-f.ctc)} |",
              f"| SE tax | {_money(f.se_tax)} |",
              f"| Additional Medicare | {_money(f.additional_medicare)} |",
              f"| **Total tax** | {_money(f.total_tax)} |",
              f"| Withholding | {_money(-f.withholding)} |",
              f"| Estimates paid | {_money(-f.estimates_paid)} |",
              f"| **Projected balance** | {_money(f.balance)} {'(refund)' if f.balance < 0 else '(owed)'} |",
              ""]

    lines += ["## Safe harbor analysis", "",
              "| Test | Amount |", "|---|---:|",
              f"| 90% of projected current-year tax | {_money(sh.pct90_current)} |"]
    if not sh.degraded:
        lines.append(f"| {sh.prior_factor:.0%} of prior-year tax | {_money(sh.prior_based)} |")
    lines += [f"| **Required annual payment** | {_money(sh.required_annual)} |",
              f"| Withholding + estimates paid | {_money(sh.withholding + sh.estimates_paid)} |",
              f"| **Shortfall** | {_money(sh.shortfall)} |",
              f"| Status | {'✅ covered' if sh.covered else '⚠️ underpaid'} |", ""]

    lines += ["## Recommended actions", "",
              "### 1040-ES voucher schedule", "",
              "| Quarter | Due | Amount |", "|---|---|---:|"]
    for v in r.vouchers:
        lines.append(f"| Q{v.quarter} | {v.due_date} | {_money(v.amount)} |")
    lines += ["",
              "### W-4 line 4(c) per-paycheck extra withholding",
              f"- To avoid penalty (safe harbor): **{_money(r.w4_penalty.per_paycheck)}/paycheck** "
              f"over {r.w4_penalty.paychecks_remaining} remaining paychecks",
              f"- To reach zero balance: **{_money(r.w4_zero.per_paycheck)}/paycheck**", ""]

    lines += ["## State projection", ""]
    if r.state is None:
        lines.append("_Skipped — no prior-year state baseline or no state brackets in tax_law._")
    else:
        s = r.state
        lines += ["| Item | Amount |", "|---|---:|",
                  f"| State ({s.code}) taxable income | {_money(s.taxable_income)} |",
                  f"| State tax | {_money(s.tax)} |",
                  f"| State withholding | {_money(-s.withholding)} |",
                  f"| **State balance** | {_money(s.balance)} {'(refund)' if s.balance < 0 else '(owed)'} |"]
    lines += ["",
              "## Caveats",
              "- This is a projection, not a filed return. Re-run after any material income change.",
              "- State estimated-payment scheduling is informational; state rules vary.", ""]
    return "\n".join(lines) + "\n"


def _report_to_dict(r: ProjectionReport) -> dict:
    return {
        "year": r.year, "as_of": r.as_of, "pay_frequency": r.pay_frequency,
        "baseline_source": r.baseline_source, "degraded": r.degraded,
        "proj": vars(r.proj), "federal": vars(r.federal),
        "state": (vars(r.state) if r.state is not None else None),
        "safe_harbor": vars(r.safe_harbor),
        "vouchers": [vars(v) for v in r.vouchers],
        "w4_penalty": vars(r.w4_penalty), "w4_zero": vars(r.w4_zero),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_projection.py -k "build_report or render or report_to_dict" -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add agents/lib/projection.py tests/test_projection.py
git commit -m "feat(project): report assembly, markdown renderer, JSON serializer"
```

---

## Task 9: load_prior_baseline() + CLI + fixtures + integration test

**Files:**
- Modify: `agents/lib/projection.py`
- Create: `tests/fixtures/projection/tax_law_2026.json`
- Create: `tests/fixtures/projection/taxpayer_profile.json`
- Create: `tests/fixtures/projection/projection_inputs.json`
- Create: `tests/fixtures/projection/archive/2025/result.json`
- Create: `tests/fixtures/projection/archive/2025/taxpayer_profile.json`
- Test: `tests/test_projection.py`

- [ ] **Step 1: Create the fixtures**

`tests/fixtures/projection/tax_law_2026.json`:

```json
{
  "tax_year": 2026,
  "federal": {
    "filing_statuses": {
      "mfj": {"standard_deduction": 30000,
              "brackets": [{"rate": 0.10, "min": 0, "max": 20000},
                           {"rate": 0.20, "min": 20000, "max": 200000},
                           {"rate": 0.30, "min": 200000, "max": null}]},
      "single": {"standard_deduction": 15000,
                 "brackets": [{"rate": 0.10, "min": 0, "max": 10000},
                              {"rate": 0.20, "min": 10000, "max": null}]}
    },
    "capital_gains_rates": {"zero_pct_limit": {"mfj": 90000, "single": 45000},
                            "fifteen_pct_limit": {"mfj": 550000, "single": 490000}},
    "child_tax_credit": {"amount_per_child": 2000, "phase_out_start": {"mfj": 400000, "single": 200000}},
    "additional_medicare_tax": {"rate": 0.009, "threshold": {"single": 200000, "mfj": 250000, "mfs": 125000}},
    "social_security": {"wage_base": 176100},
    "qbi_deduction_rate": 0.20
  },
  "state": {"code": "CA", "standard_deduction": {"mfj": 11000, "single": 5500},
            "brackets": [{"rate": 0.01, "min": 0, "max": 20000},
                         {"rate": 0.05, "min": 20000, "max": null}]}
}
```

`tests/fixtures/projection/taxpayer_profile.json`:

```json
{"tax_year": 2026, "filing_status": "MFJ",
 "primary": {"first_name": "Test", "last_name": "User", "ssn_last4": "1234"},
 "dependents": [{"first_name": "Kid", "ctc_eligible": true}]}
```

`tests/fixtures/projection/projection_inputs.json`:

```json
{
  "tax_year": 2026,
  "as_of_date": "06-30",
  "pay_frequency": "biweekly",
  "ytd": {"wages": 60000, "federal_withholding": 7000, "state_withholding": 2500,
          "ss_wages": 60000, "medicare_wages": 61000, "medicare_withheld": 885},
  "forecast_overrides": {"year_end_bonus": 0, "rsu_vesting": 0, "capital_gains": 0,
                         "self_employment_net": 0, "other_income": 0},
  "recurring_from_prior": {"taxable_interest": 500, "ordinary_dividends": 1000, "qualified_dividends": 800},
  "estimated_payments_made": {"federal": 0, "state": 0}
}
```

`tests/fixtures/projection/archive/2025/result.json`:

```json
{"meta": {"tax_year": 2025, "filing_status": "MFJ"},
 "form_1040": {"line_24_total_tax": 14000, "line_11_agi": 130000},
 "california_540": {"ca_total_tax": 4200}}
```

`tests/fixtures/projection/archive/2025/taxpayer_profile.json`:

```json
{"tax_year": 2025, "filing_status": "MFJ", "primary": {"ssn_last4": "1234"}}
```

- [ ] **Step 2: Write the failing tests (load_prior_baseline + CLI integration)**

```python
import subprocess


FIXTURE_WS = Path(__file__).resolve().parent / "fixtures" / "projection"


def test_load_prior_baseline_absent_returns_none(tmp_path):
    assert P.load_prior_baseline(tmp_path / "archive" / "2025", 2025) is None


def test_load_prior_baseline_loads_when_present():
    prior = P.load_prior_baseline(FIXTURE_WS / "archive" / "2025", 2025)
    assert prior is not None
    assert prior.result["form_1040"]["line_24_total_tax"] == 14000


def test_load_prior_baseline_year_mismatch_raises(tmp_path):
    import json
    arch = tmp_path / "archive" / "2025"
    arch.mkdir(parents=True)
    (arch / "result.json").write_text(json.dumps({"meta": {"tax_year": 2099}}))
    (arch / "taxpayer_profile.json").write_text(json.dumps({}))
    with pytest.raises(P.ProjectionError):
        P.load_prior_baseline(arch, 2025)


def test_cli_integration_json_output():
    cmd = [sys.executable, "-m", "agents.lib.projection",
           "--inputs", str(FIXTURE_WS / "projection_inputs.json"),
           "--workspace", str(FIXTURE_WS), "--year", "2026", "--as-of", "06-30", "--json"]
    repo_root = Path(__file__).resolve().parent.parent
    out = subprocess.run(cmd, capture_output=True, text=True, cwd=str(repo_root))
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert data["year"] == 2026
    assert data["degraded"] is False
    assert data["safe_harbor"]["prior_tax"] == 14000
    assert data["federal"]["total_tax"] > 0
    assert data["state"] is not None
    assert data["state"]["code"] == "CA"


def test_cli_rejects_future_as_of():
    cmd = [sys.executable, "-m", "agents.lib.projection",
           "--inputs", str(FIXTURE_WS / "projection_inputs.json"),
           "--workspace", str(FIXTURE_WS), "--year", "2026", "--as-of", "12-31",
           "--today", "2026-06-30", "--json"]
    repo_root = Path(__file__).resolve().parent.parent
    out = subprocess.run(cmd, capture_output=True, text=True, cwd=str(repo_root))
    assert out.returncode == 1
    assert "future" in out.stderr.lower()


def test_cli_rejects_path_traversal():
    cmd = [sys.executable, "-m", "agents.lib.projection",
           "--inputs", "/etc/passwd",
           "--workspace", str(FIXTURE_WS), "--year", "2026", "--as-of", "06-30", "--json"]
    repo_root = Path(__file__).resolve().parent.parent
    out = subprocess.run(cmd, capture_output=True, text=True, cwd=str(repo_root))
    assert out.returncode == 1
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_projection.py -k "baseline or cli" -v`
Expected: FAIL — `AttributeError: ... 'load_prior_baseline'` / module has no `_main`

- [ ] **Step 4: Implement load_prior_baseline + CLI (append to projection.py)**

```python
def load_prior_baseline(archive_dir, prior_year: int):
    """Return PriorReturn for archive_dir, or None if the directory is absent
    (degraded mode). Re-raises a year mismatch as ProjectionError."""
    p = Path(archive_dir)
    if not p.exists():
        return None
    try:
        return load_prior(p, prior_year)
    except RolloverError as e:
        raise ProjectionError(str(e)) from e


def _read_json(path: Path) -> dict:
    if not path.exists():
        raise ProjectionError(f"{path} does not exist")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ProjectionError(f"{path.name} is malformed: {e}")


def _main(argv: list) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="agents.lib.projection")
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
        inputs_path = _validate_subpath(Path(args.inputs).resolve(), workspace)
        inputs = _read_json(inputs_path)

        today = (datetime.date.fromisoformat(args.today) if args.today
                 else datetime.date.today())
        as_of_str = args.as_of or inputs.get("as_of_date") or today.strftime("%m-%d")
        as_of = parse_as_of(as_of_str, year)
        if as_of > today:
            raise ProjectionError(f"as_of {as_of.isoformat()} is in the future (today {today.isoformat()})")

        tax_law_path = workspace / f"tax_law_{year}.json"
        tax_law = _read_json(tax_law_path) if tax_law_path.exists() else {}
        profile = _read_json(workspace / "taxpayer_profile.json")
        prior = load_prior_baseline(workspace / "archive" / str(year - 1), year - 1)
    except ProjectionError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except RolloverError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    try:
        report = build_report(inputs, profile, tax_law, prior, year, as_of)
    except (ProjectionError, ValueError) as e:
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

- [ ] **Step 5: Run tests to verify they pass**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_projection.py -k "baseline or cli" -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Run the full projection test file**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_projection.py -v`
Expected: PASS (all ~30 tests)

- [ ] **Step 7: Commit**

```bash
git add agents/lib/projection.py tests/test_projection.py tests/fixtures/projection/
git commit -m "feat(project): prior-baseline loader, CLI entry point, fixtures, integration tests"
```

---

## Task 10: Skill file + CLAUDE.md updates

**Files:**
- Create: `agents/skills/tax-project.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Write the skill file**

Create `agents/skills/tax-project.md`:

````markdown
---
name: tax-project
description: Mid-year projection — project full-year tax from YTD figures, compute safe-harbor estimates, suggest W-4 4(c) and 1040-ES adjustments
---

# /tax-project — Mid-Year Tax Projection

Projects full-year federal (and conditional state) tax from year-to-date figures,
then computes safe-harbor required payments, a 1040-ES voucher schedule, and a
W-4 line 4(c) per-paycheck adjustment. Read-only: never modifies result.json or
taxpayer_profile.json. Re-runnable quarterly.

## Prerequisites
- `taxpayer_profile.json` and `tax_law_{year}.json` exist (else: run `/tax-setup {year}` first).
- Optional: `archive/{year-1}/` (from `/tax-archive`) — enables the 100%/110%-of-prior
  safe harbor and prior-year recurring-income defaults. Without it, runs in degraded mode.

## Steps

1. **Validate prerequisites.** Confirm `taxpayer_profile.json` and `tax_law_{year}.json`
   exist. If not, stop and tell the user to run `/tax-setup {year}`.

2. **Load or create `projection_inputs.json`.** If absent, copy
   `agents/templates/projection_inputs_template.json` to the workspace and interview the
   user for: YTD wages, YTD federal + state withholding, pay frequency, as-of date, and any
   known lumpy items (year-end bonus, RSU vesting, capital gains, self-employment net).
   Validate inputs (no shell metacharacters; numeric fields numeric). `chmod 600` the file.

3. **Resolve prior-year baseline.** If `archive/{year-1}/` exists, read its
   `result.json` to pre-fill `recurring_from_prior` (interest/dividends) as editable
   defaults. If absent, note degraded mode to the user.

4. **Confirm the annualization split.** Show the user which items will be annualized
   (steady wages/withholding) vs. treated as full-year (overrides, recurring), and the
   elapsed fraction. Let the user correct lumpy overrides before computing.

5. **Compute.** Run:
   ```bash
   python3 -m agents.lib.projection --inputs projection_inputs.json \
       --workspace . --year {year} --as-of {MM-DD} --json
   ```
   (Omit `--as-of` to use today.)

6. **Render + save.** Run the same command without `--json` to get the markdown report.
   Save it to `projections/TY{year}_asof_{MM-DD}.md` and `chmod 600` it. Print the report.

7. **Audit note.** Append one line to `answers.md` (NO dollar PII duplicated):
   ```
   ## Mid-year projection TY{year} as-of {MM-DD} — run {today}. Safe-harbor shortfall: {covered/underpaid}.
   ```

## Security & safety
- Subagents (if any) must NOT have network access and must NOT write outside `projections/`.
- Never extract or store full SSNs.
- This skill never writes `result.json` or `taxpayer_profile.json`.
- All written files (`projection_inputs.json`, `projections/*.md`) → `chmod 600`.
````

- [ ] **Step 2: Update CLAUDE.md pipeline diagram**

In `CLAUDE.md`, find the Architecture pipeline diagram and add `/tax-project` as an
optional planning branch after `/tax-setup`. Insert after the `/tax-parse` line's block,
add this note below the diagram:

```
- **/tax-project** (optional, planning) — mid-year projection: run any time after
  /tax-setup to project full-year tax, safe-harbor estimates, and W-4 4(c)/1040-ES
  guidance from YTD figures. Read-only; re-runnable quarterly.
```

- [ ] **Step 3: Update CLAUDE.md test count**

In `CLAUDE.md`, find the Testing section line that reads "155 tests covering..." and
update the count and description to include projection tests. Run this first to get the
exact number:

Run: `source .venv/bin/activate && python3 -m pytest tests/ -q 2>&1 | tail -1`
Expected: a count like "185 passed"

Then edit the line to match, e.g.:
```
- 185 tests covering all 8 computation functions + archive/rollover modules + YoY diff + mid-year projection + integration pipeline
```

- [ ] **Step 4: Run the full suite + the computations self-test**

Run: `source .venv/bin/activate && python3 -m pytest tests/ -v && python3 agents/lib/tax_computations.py`
Expected: all tests PASS; self-test prints without error.

- [ ] **Step 5: Commit**

```bash
git add agents/skills/tax-project.md CLAUDE.md
git commit -m "feat(project): /tax-project skill + CLAUDE.md pipeline & test count"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| `projection.py` compute module | Tasks 1–9 |
| `projection_inputs.json` template + stable `ytd` interface + parser hook | Task 1 |
| Annualized-vs-full-year split | Task 3 |
| Federal projection via `tax_computations` | Task 4 |
| Safe harbor (90% vs 100/110%, MFS $75k, degraded) | Task 5 |
| W-4 4(c) two targets + 1040-ES vouchers | Task 6, assembled Task 8 |
| Conditional state projection | Task 7 |
| Markdown report w/ degraded banner + state-skip note | Task 8 |
| Read-only; timestamped reports under `projections/` | Task 10 skill + `.gitignore` Task 1 |
| Degraded mode when no archive | Tasks 5, 8, 9 |
| Error handling: future/Jan-1/malformed as-of, path traversal, unknown pay_frequency, non-numeric/bool | Tasks 2, 9 |
| `_validate_subpath` / `_validate_year_arg` reuse | Task 9 |
| `.gitignore` + CLAUDE.md updates | Tasks 1, 10 |
| ~30 tests + fixtures | Tasks 1–9 |

No gaps found.

**Placeholder scan:** No TBD/TODO/"handle edge cases" left; every code step shows complete code.

**Type consistency:** Dataclass field names (`ProjectedInputs.paychecks_remaining`, `FederalProjection.balance`, `SafeHarbor.shortfall`, `Voucher.due_date`, `ProjectionReport.w4_penalty/w4_zero`) are referenced consistently across `build_report`, `render_markdown`, `_report_to_dict`, and the tests. Function signatures match between definition tasks and the integration in Task 8/9 (`compute_safe_harbor(..., withholding=, estimates_paid=)`, `compute_estimated_vouchers(required_annual, withholding, paid, as_of)`, `project_state_tax(proj, prior, tax_law, filing_status)`).
