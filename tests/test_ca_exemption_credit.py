"""Form 540 line 32: worked TY2025 AGI Limitation Worksheet cases.

Figures are test inputs from the TY2025 Form 540 booklet, not engine defaults:
https://www.ftb.ca.gov/forms/2025/2025-540-booklet.html
"""

import copy
from datetime import datetime
from pathlib import Path

import pytest

from agents.lib.tax_computations import compute_ca_exemption_credit
from agents.lib.tax_law_fetcher import check_freshness, validate_cache
from agents.lib.secure_fs import write_secure


PHASEOUT_2025 = {
    "threshold": {"single": 252203, "mfj": 504411, "mfs": 252203,
                  "hoh": 378310, "qss": 504411},
    "band": {"single": 2500, "mfj": 2500, "mfs": 1250, "hoh": 2500, "qss": 2500},
    "reduction_per_step": 6,
}


def credit(agi=252203, status="Single", heads=3, dependents=2, **overrides):
    inputs = dict(federal_agi=agi, filing_status=status,
                  personal_blind_senior_count=heads, dependent_count=dependents,
                  personal_exemption=153, dependent_exemption=475,
                  exemption_phaseout=copy.deepcopy(PHASEOUT_2025))
    inputs.update(overrides)
    return compute_ca_exemption_credit(**inputs)


@pytest.mark.parametrize("status", PHASEOUT_2025["threshold"])
@pytest.mark.parametrize("offset,steps,allowed", [(0, 0, 1409), (1, 1, 1379),
                                                 (2500, 1, 1379), (2501, 2, 1349)])
def test_status_threshold_and_ceiling_steps(status, offset, steps, allowed):
    # 3 personal/blind/senior boxes and 2 dependents: 3*153+2*475=1409.
    # Each worksheet step subtracts 6 for each of the five boxes, or 30.
    # MFS has half-width steps, so scale the offset to its 1,250 band.
    if status == "mfs":
        offset = {0: 0, 1: 1, 2500: 1250, 2501: 1251}[offset]
    result = credit(PHASEOUT_2025["threshold"][status] + offset, status)
    assert result["phaseout_steps"] == steps
    assert result["exemption_credit"] == allowed


def test_credit_groups_floor_independently():
    # 30 steps: personal 153-180 floors to 0, dependent 475-180=295.
    result = credit(252203 + 30 * 2500)
    assert result["personal_blind_senior_credit"] == 0
    assert result["dependent_credit"] == 590
    assert result["exemption_credit"] == 590
    assert credit(252203 + 80 * 2500)["exemption_credit"] == 0


def test_joint_seniors_above_amt_screen_are_still_computed():
    # MFJ 600k: ceil((600000-504411)/2500)=39; reduction=234/head.
    # Two personal + two senior boxes floor to 0; 2*(475-234)=482.
    result = credit(600000, "MFJ", heads=4)
    assert result["phaseout_steps"] == 39
    assert result["exemption_credit"] == 482
    # At 520k, seven steps reduce each senior credit to 153-42=111.
    assert credit(520000, "MFJ", heads=4)["exemption_credit"] == 1310
    assert credit(520000, "MFJ", heads=2)["exemption_credit"] == 1088


def test_whole_dollar_agi_uses_the_forms_rounding():
    # Worksheet says whole dollars only: .49 rounds down, .50 up.
    assert credit(252203.49)["phaseout_steps"] == 0
    assert credit(252203.50)["phaseout_steps"] == 1
    assert credit(-1000)["exemption_credit"] == 1409


@pytest.mark.parametrize("field", ["federal_agi", "personal_exemption", "dependent_exemption"])
@pytest.mark.parametrize("bad", [None, True, "153", float("nan"), float("inf")])
def test_malformed_amounts_refuse(field, bad):
    with pytest.raises(ValueError):
        credit(**{field: bad})


@pytest.mark.parametrize("field", ["personal_blind_senior_count", "dependent_count"])
@pytest.mark.parametrize("bad", [None, True, -1, 1.5, float("nan"), float("inf")])
def test_malformed_counts_refuse(field, bad):
    with pytest.raises(ValueError, match=field):
        credit(**{field: bad})


def test_unknown_filing_status_refuses():
    with pytest.raises(ValueError, match="filing_status"):
        credit(status="widowed")


@pytest.mark.parametrize("bad", [None, {}, 0, False])
def test_unfetched_phaseout_cannot_keep_full_credit(bad):
    with pytest.raises(ValueError, match="state.exemption_phaseout"):
        credit(agi=0, exemption_phaseout=bad)


@pytest.mark.parametrize("field", ["threshold", "band", "reduction_per_step"])
@pytest.mark.parametrize("bad", [None, True, "6", float("nan"), float("inf"), -1, 0.5])
def test_missing_or_malformed_phaseout_refuses_below_threshold(field, bad):
    phaseout = copy.deepcopy(PHASEOUT_2025)
    if field == "reduction_per_step":
        phaseout[field] = bad
    else:
        phaseout[field]["single"] = bad
    with pytest.raises(ValueError, match="state.exemption_phaseout"):
        credit(agi=0, exemption_phaseout=phaseout)


@pytest.mark.parametrize("field", ["band", "reduction_per_step"])
def test_zero_step_parameters_refuse(field):
    phaseout = copy.deepcopy(PHASEOUT_2025)
    if field == "band":
        phaseout[field]["single"] = 0
    else:
        phaseout[field] = 0
    with pytest.raises(ValueError, match="positive"):
        credit(exemption_phaseout=phaseout)


@pytest.mark.parametrize("missing", ["block", "threshold", "band", "status", "reduction_per_step"])
def test_legacy_or_partial_ca_cache_is_incomplete(tmp_path, missing):
    import json

    phaseout = copy.deepcopy(PHASEOUT_2025)
    state = {"code": "CA", "name": "California", "type": "graduated_income",
             "exemption_phaseout": phaseout}
    if missing == "block":
        del state["exemption_phaseout"]
    elif missing == "status":
        del phaseout["threshold"]["qss"]
    else:
        del phaseout[missing]
    data = {"tax_year": 2025, "state": state,
            "_meta": {"fetched": datetime.now().isoformat()}}
    assert any("state.exemption_phaseout" in p for p in validate_cache(data, 2025))
    write_secure(tmp_path / "tax_law_2025.json", json.dumps(data))
    report = check_freshness(2025, str(tmp_path))
    assert report["status"] == "INCOMPLETE"
    assert any("state.exemption_phaseout" in p for p in report["null_fields"])


def test_complete_phaseout_passes_cache_validation_and_is_not_mutated():
    data = {"tax_year": 2025, "state": {"code": "CA", "name": "California",
            "type": "graduated_income", "exemption_phaseout": copy.deepcopy(PHASEOUT_2025)}}
    original = copy.deepcopy(data)
    assert not any("exemption_phaseout" in p for p in validate_cache(data, 2025))
    assert data == original


def test_compute_and_setup_require_the_worksheet_and_senior_flags():
    repo = Path(__file__).resolve().parents[1]
    compute = (repo / ".agents/skills/tax-compute/references/state-return.md").read_text()
    setup = (repo / ".agents/skills/tax-setup/references/tax-law-fetch.md").read_text()
    assert "compute_ca_exemption_credit()" in compute
    assert "flags.age_65_plus_primary" in compute
    assert "flags.age_65_plus_spouse" in compute
    assert "state.exemption_phaseout" in compute
    assert "ca_exemption_credits.personal.total" in compute
    assert "ca_exemption_credits.dependent.total" in compute
    required = setup.split("For every CA return", 1)[1].split("Optional CA", 1)[0]
    assert "state.exemption_phaseout" in required
