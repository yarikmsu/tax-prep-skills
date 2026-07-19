"""Tests for tax_law_fetcher freshness logic — the sdi_wage_limit year-aware
null exception (Codex P1 on PR #29)."""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib.tax_law_fetcher import (  # noqa: E402
    check_freshness,
    verify_source_pinning,
    _source_host_authoritative,
    create_empty_tax_law,
)


def _write_cache(tmp_path, year, sdi_wage_limit="omit"):
    """A minimal fresh cache: only fields the walker will see. Pass
    sdi_wage_limit="omit" to simulate a legacy cache created before the field
    existed (key absent entirely)."""
    state = {"name": "California", "sdi_rate": 0.012,
             "itemized_phaseout": {"single": 244000, "mfj": 488000,
                                   "hoh": 366000}}
    if sdi_wage_limit != "omit":
        state["sdi_wage_limit"] = sdi_wage_limit
    data = {
        "tax_year": year,
        "_meta": {"fetched": datetime.now().isoformat()},
        "federal": {"fbar_threshold": 10000, "amt": {"exemption": {"single": 88100, "mfj": 137000, "mfs": 68500}, "phaseout_start": {"single": 626350, "mfj": 1252700, "mfs": 626350}, "rate_28_breakpoint": 239100}},
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

def test_empty_underpayment_rates_not_flagged(tmp_path):
    """Self-review (PR #31): an empty federal.underpayment_rates is
    legitimate — a withholding-only taxpayer never needs the §6621 timeline,
    and compute_form_2210 degrades loudly at the point of need. Flagging it
    would nag INCOMPLETE forever (the sdi_wage_limit precedent)."""
    data = {
        "tax_year": 2025,
        "_meta": {"fetched": datetime.now().isoformat()},
        "federal": {"fbar_threshold": 10000, "underpayment_rates": [], "amt": {"exemption": {"single": 88100, "mfj": 137000, "mfs": 68500}, "phaseout_start": {"single": 626350, "mfj": 1252700, "mfs": 626350}, "rate_28_breakpoint": 239100}},
        "state": {"name": "California", "sdi_rate": 0.012,
                  "itemized_phaseout": {"single": 244000, "mfj": 488000,
                                        "hoh": 366000}},
        "exchange_rates": {"EUR": 1.08},
    }
    (tmp_path / "tax_law_2025.json").write_text(json.dumps(data))
    result = check_freshness(2025, str(tmp_path))
    assert not any("underpayment_rates" in f for f in result["null_fields"])
    assert result["status"] == "FRESH"

def test_absent_itemized_phaseout_flagged_for_ca(tmp_path):
    """Codex P2 (PR #33): a pre-D1 CA cache has no itemized_phaseout key —
    the walker only visits present keys, so without the explicit guard it
    reported FRESH while compute_ca_itemized silently skipped the §17077
    phase-out (overstating high-AGI CA itemized deductions)."""
    data = {
        "tax_year": 2025,
        "_meta": {"fetched": datetime.now().isoformat()},
        "federal": {"fbar_threshold": 10000},
        "state": {"name": "California", "sdi_rate": 0.012},
        "exchange_rates": {"EUR": 1.08},
    }
    (tmp_path / "tax_law_2025.json").write_text(json.dumps(data))
    result = check_freshness(2025, str(tmp_path))
    assert any("itemized_phaseout" in f and "absent" in f
               for f in result["null_fields"])
    assert result["status"] == "INCOMPLETE"

def test_absent_amt_key_flagged(tmp_path):
    """Review P2 (PR #34): a pre-D9 cache has no federal.amt key — without
    the guard it reported FRESH while Form 6251 could only hard-error at
    point of need with no earlier refetch prompt."""
    data = {
        "tax_year": 2025,
        "_meta": {"fetched": datetime.now().isoformat()},
        "federal": {"fbar_threshold": 10000},
        "state": {"name": "California", "sdi_rate": 0.012,
                  "itemized_phaseout": {"single": 244000, "mfj": 488000,
                                        "hoh": 366000}},
        "exchange_rates": {"EUR": 1.08},
    }
    (tmp_path / "tax_law_2025.json").write_text(json.dumps(data))
    result = check_freshness(2025, str(tmp_path))
    assert any("federal.amt" in f and "absent" in f
               for f in result["null_fields"])


# --------------------------------------------------------------------------
# H4 (K2) — tax-law source pinning
# --------------------------------------------------------------------------

def test_source_host_authoritative_accepts_primary_and_rejects_others():
    assert _source_host_authoritative("https://www.irs.gov/pub/irs-drop/rp-24-40.pdf")
    assert _source_host_authoritative("https://ftb.ca.gov/forms/2025/index.html")
    assert _source_host_authoritative("https://www.law.cornell.edu/uscode/text/26/423")
    # subdomain of an authoritative base domain
    assert _source_host_authoritative("https://apps.irs.gov/app/x")
    # Codex P1 (PR #48): ANY .gov is authoritative (registry-restricted to US
    # government) — so state tax authorities work without enumerating 50 states.
    assert _source_host_authoritative("https://www.tax.ny.gov/pit/file/tax_tables.htm")
    assert _source_host_authoritative("https://www.nj.gov/treasury/taxation/")
    assert _source_host_authoritative("https://revenue.wi.gov/x")
    # non-primary / malformed / empty → not authoritative
    assert not _source_host_authoritative("https://smartasset.com/taxes/brackets")
    assert not _source_host_authoritative("https://en.wikipedia.org/wiki/Tax")
    assert not _source_host_authoritative("irs.gov not a url")
    assert not _source_host_authoritative("")
    assert not _source_host_authoritative(None)
    # a .gov lookalike must resolve to a NON-.gov host → rejected
    assert not _source_host_authoritative("https://irs.gov.evil.com/x")
    assert not _source_host_authoritative("https://irs.gov.co/x")


def test_source_requires_http_scheme():
    # Codex P2 (PR #48 round 3): a non-web scheme with a gov hostname is not a
    # retrievable authoritative source — reject javascript:/file:/ftp:/data:.
    assert not _source_host_authoritative("javascript://www.irs.gov/x")
    assert not _source_host_authoritative("file://www.irs.gov/etc/passwd")
    assert not _source_host_authoritative("ftp://www.irs.gov/x")
    assert not _source_host_authoritative("data://www.irs.gov/x")
    assert _source_host_authoritative("https://www.irs.gov/x")
    assert _source_host_authoritative("http://www.irs.gov/x")


def test_verify_source_pinning_state_name_normalizes_to_code():
    # Codex P2 (PR #48 round 3): a legacy state block with only `name`
    # normalizes to the documented state.<CODE> pin key, not state.<Name>.
    d = _populated()
    d["state"] = {"name": "California",
                  "brackets": [{"rate": 0.01, "min": 0, "max": 10000}]}
    u = verify_source_pinning(d)["unpinned"]
    assert "state.CA" in u
    assert not any(x.startswith("state.Cal") for x in u)
    d["_sources"] = {"state.CA": "https://www.ftb.ca.gov/forms/2025/"}
    assert "state.CA" not in verify_source_pinning(d)["unpinned"]


def test_verify_source_pinning_audits_exchange_rates():
    # Codex P2 (PR #48 round 3): fetched exchange rates scale reported USD
    # foreign income / FBAR values — they must be source-pinned too.
    d = create_empty_tax_law(2025)
    d["exchange_rates"] = {"EUR": 1.08, "GBP": 1.28}
    assert "exchange_rates" in verify_source_pinning(d)["unpinned"]
    d["_sources"] = {"exchange_rates":
                     "https://www.irs.gov/individuals/international-taxpayers/"
                     "yearly-average-currency-exchange-rates"}
    assert "exchange_rates" not in verify_source_pinning(d)["unpinned"]


def _populated(year=2025):
    d = create_empty_tax_law(year)
    d["federal"]["filing_statuses"]["single"]["standard_deduction"] = 15000
    d["federal"]["filing_statuses"]["single"]["brackets"] = [
        {"rate": 0.10, "min": 0, "max": 11925}]
    d["federal"]["capital_gains_rates"] = {"zero_pct_limit": 48350,
                                           "fifteen_pct_limit": 533400}
    return d


def test_verify_source_pinning_flags_non_authoritative_and_unpinned():
    d = _populated()
    d["_sources"] = {"federal.filing_statuses": "https://blog.example.com/brackets"}
    r = verify_source_pinning(d)
    assert r["non_authoritative"] == [
        ("federal.filing_statuses", "https://blog.example.com/brackets")]
    # capital_gains_rates is populated but has no _sources pin → unpinned
    assert "federal.capital_gains_rates" in r["unpinned"]


def test_verify_source_pinning_clean_when_all_authoritative():
    d = _populated()
    d["_sources"] = {
        "federal.filing_statuses": "https://www.irs.gov/pub/irs-drop/rp.pdf",
        "federal.capital_gains_rates": "https://www.irs.gov/taxtopics/tc409",
    }
    r = verify_source_pinning(d)
    assert r["non_authoritative"] == []
    assert r["unpinned"] == []


def test_verify_source_pinning_audits_all_fetched_blocks_not_just_two():
    # Codex P2 (PR #48 round 2): the unpinned audit covers EVERY fetched federal
    # block (derived from the schema), not just filing_statuses + capital_gains.
    d = create_empty_tax_law(2025)
    d["federal"]["child_tax_credit"] = {"amount_per_child": 2200,
                                        "refundable_limit": 1700}
    d["federal"]["social_security"]["wage_base"] = 176100
    r = verify_source_pinning(d)
    assert "federal.child_tax_credit" in r["unpinned"]
    assert "federal.social_security" in r["unpinned"]


def test_verify_source_pinning_excludes_structural_constant_blocks():
    # se_tax / niit / additional_medicare_tax / form_8938_thresholds /
    # foreign_tax_credit_simplified ship fully-seeded (bill-fixed rates), so they
    # are NOT fetched figures and must not raise a false unpinned advisory. An
    # empty cache (all blocks == their seed) must produce ZERO advisories.
    d = create_empty_tax_law(2025)
    assert verify_source_pinning(d)["unpinned"] == []
    # even with real data elsewhere, the structural blocks never appear
    d["federal"]["filing_statuses"]["single"]["standard_deduction"] = 15000
    d["federal"]["filing_statuses"]["single"]["brackets"] = [
        {"rate": 0.10, "min": 0, "max": 11925}]
    unpinned = verify_source_pinning(d)["unpinned"]
    for structural in ("federal.se_tax", "federal.niit",
                       "federal.additional_medicare_tax",
                       "federal.form_8938_thresholds",
                       "federal.foreign_tax_credit_simplified"):
        assert structural not in unpinned


def test_verify_source_pinning_state_is_single_block():
    # Codex P2 (PR #48): the cache holds ONE state as a flat block under
    # `state` (state.code/state.brackets/…), so pinning is by the single
    # `state.<code>` key /tax-setup records — NOT per state field.
    d = _populated()
    d["state"] = {"code": "CA", "name": "California",
                  "brackets": [{"rate": 0.01, "min": 0, "max": 10000}]}
    r = verify_source_pinning(d)
    # the single expected state pin is "state.CA", never state.code/state.name
    assert "state.CA" in r["unpinned"]
    assert not any(u.startswith("state.") and u != "state.CA"
                   for u in r["unpinned"])
    # a state.CA pin satisfies it
    d["_sources"] = {"state.CA": "https://www.ftb.ca.gov/forms/2025/"}
    r2 = verify_source_pinning(d)
    assert "state.CA" not in r2["unpinned"]
    assert r2["non_authoritative"] == []


def test_verify_source_pinning_malformed_sources_raises():
    d = _populated()
    d["_sources"] = ["not", "a", "dict"]
    try:
        verify_source_pinning(d)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_freshness_non_authoritative_source_is_incomplete(tmp_path):
    d = _populated()
    d["_meta"] = {"fetched": datetime.now().isoformat()}
    d["state"] = {"name": "California", "sdi_rate": 0.012,
                  "itemized_phaseout": {"single": 244000, "mfj": 488000,
                                        "hoh": 366000}}
    d["federal"]["amt"] = {"exemption": {"single": 88100, "mfj": 137000,
                                         "mfs": 68500},
                           "phaseout_start": {"single": 626350, "mfj": 1252700,
                                              "mfs": 626350},
                           "rate_28_breakpoint": 239100}
    d["exchange_rates"] = {"EUR": 1.08}
    d["_sources"] = {"federal.filing_statuses": "https://smartasset.com/x"}
    (tmp_path / "tax_law_2025.json").write_text(json.dumps(d))
    result = check_freshness(2025, str(tmp_path))
    assert result["status"] == "INCOMPLETE"
    assert any("non-authoritative" in f and "federal.filing_statuses" in f
               for f in result["null_fields"])


def test_freshness_unpinned_is_advisory_not_status_flip(tmp_path):
    # A FRESH, timestamped cache with a populated core block but NO _sources
    # stays FRESH and carries the unpinned advisory (legacy caches predate the
    # field). Built minimally (only present keys are walked) so the sole
    # freshness signal is the unpinned source, not an unrelated null.
    d = {
        "tax_year": 2025,
        "_meta": {"fetched": datetime.now().isoformat()},
        "federal": {
            "fbar_threshold": 10000,
            "filing_statuses": {"single": {"standard_deduction": 15000,
                                           "brackets": [{"rate": 0.10, "min": 0,
                                                         "max": 11925}]}},
            "amt": {"exemption": {"single": 88100, "mfj": 137000, "mfs": 68500},
                    "phaseout_start": {"single": 626350, "mfj": 1252700,
                                       "mfs": 626350},
                    "rate_28_breakpoint": 239100},
        },
        "state": {"name": "California", "sdi_rate": 0.012,
                  "itemized_phaseout": {"single": 244000, "mfj": 488000,
                                        "hoh": 366000}},
        "exchange_rates": {"EUR": 1.08},
    }
    (tmp_path / "tax_law_2025.json").write_text(json.dumps(d))
    result = check_freshness(2025, str(tmp_path))
    assert result["status"] == "FRESH"
    assert "federal.filing_statuses" in result["unpinned_sources"]
    assert "UNPINNED" in result["recommendation"]
