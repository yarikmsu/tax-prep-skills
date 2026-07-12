"""Tests for tax_law_fetcher freshness logic — the sdi_wage_limit year-aware
null exception (Codex P1 on PR #29)."""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib.tax_law_fetcher import check_freshness  # noqa: E402


def _write_cache(tmp_path, year, sdi_wage_limit="omit"):
    """A minimal fresh cache: only fields the walker will see. Pass
    sdi_wage_limit="omit" to simulate a legacy cache created before the field
    existed (key absent entirely)."""
    state = {"name": "California", "sdi_rate": 0.012}
    if sdi_wage_limit != "omit":
        state["sdi_wage_limit"] = sdi_wage_limit
    data = {
        "tax_year": year,
        "_meta": {"fetched": datetime.now().isoformat()},
        "federal": {"fbar_threshold": 10000},
        "state": state,
        "exchange_rates": {"EUR": 1.08},
    }
    (tmp_path / f"tax_law_{year}.json").write_text(json.dumps(data))


def test_null_sdi_limit_valid_for_2024_plus(tmp_path):
    """Codex P1: SB 951 removed the ceiling for TY2024+ — a correctly-null
    sdi_wage_limit must NOT flag the cache INCOMPLETE forever."""
    _write_cache(tmp_path, 2025, sdi_wage_limit=None)
    result = check_freshness(2025, str(tmp_path))
    assert not any("sdi_wage_limit" in f for f in result["null_fields"])
    assert result["status"] == "FRESH"


def test_null_sdi_limit_flagged_before_2024(tmp_path):
    """Pre-2024 the ceiling existed — a null limit is a genuinely unfetched
    field and must surface as INCOMPLETE."""
    _write_cache(tmp_path, 2023, sdi_wage_limit=None)
    result = check_freshness(2023, str(tmp_path))
    assert any("sdi_wage_limit" in f for f in result["null_fields"])
    assert result["status"] == "INCOMPLETE"


def test_populated_sdi_limit_never_flagged(tmp_path):
    _write_cache(tmp_path, 2023, sdi_wage_limit=153164)
    result = check_freshness(2023, str(tmp_path))
    assert not any("sdi_wage_limit" in f for f in result["null_fields"])


def test_absent_sdi_key_flagged_for_pre_2024_ca(tmp_path):
    """Codex P2 round 12: a legacy pre-2024 CA cache created before the field
    existed has NO sdi_wage_limit key — the walker only visits present keys,
    so without the explicit check it reported FRESH and nothing ever prompted
    the refetch the excess-SDI credit needs."""
    _write_cache(tmp_path, 2023)  # key absent
    result = check_freshness(2023, str(tmp_path))
    assert any("sdi_wage_limit" in f and "absent" in f
               for f in result["null_fields"])
    assert result["status"] == "INCOMPLETE"


def test_absent_sdi_key_fine_for_2024_plus_ca(tmp_path):
    """TY2024+ has no ceiling — an absent key is as valid as a null one."""
    _write_cache(tmp_path, 2025)  # key absent
    result = check_freshness(2025, str(tmp_path))
    assert not any("sdi_wage_limit" in f for f in result["null_fields"])
    assert result["status"] == "FRESH"
