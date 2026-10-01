"""Tests for tax_law_fetcher freshness logic — the sdi_wage_limit year-aware
null exception (Codex P1 on PR #29)."""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib.tax_law_fetcher import (  # noqa: E402
    check_freshness,
    verify_source_pinning,
    _source_host_authoritative,
    create_empty_tax_law,
    create_state_template,
    get_tax_law_path,
    load_tax_law,
    save_tax_law,
)


def _write_cache(tmp_path, year, sdi_wage_limit="omit"):
    """A minimal fresh cache: only fields the walker will see. Pass
    sdi_wage_limit="omit" to simulate a legacy cache created before the field
    existed (key absent entirely)."""
    state = {"name": "California", "type": "graduated_income",
             "sdi_rate": 0.012,
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
        "state": {"name": "California", "type": "graduated_income",
                  "sdi_rate": 0.012,
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


def test_verify_source_pinning_fl_requires_separate_county_rate_pin():
    """FL's two independently-fetched rates need two provenance keys."""
    d = create_empty_tax_law(2025)
    fl = create_state_template("FL")
    fl["sales_tax"]["state_rate"] = 0.06
    fl["sales_tax"]["county_surtax_rate"] = 0.01
    d["state"] = fl
    d["_sources"] = {
        "state.FL":
            "https://floridarevenue.com/taxes/taxesfees/Pages/sales_tax.aspx",
    }

    assert "state.FL.county_surtax_rate" in \
        verify_source_pinning(d)["unpinned"]

    d["_sources"]["state.FL.county_surtax_rate"] = (
        "https://floridarevenue.com/Forms_library/current/dr15dss.pdf")
    state_unpinned = [p for p in verify_source_pinning(d)["unpinned"]
                      if p.startswith("state.FL")]
    assert state_unpinned == []


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
                                                         "max": None}]}},
            "amt": {"exemption": {"single": 88100, "mfj": 137000, "mfs": 68500},
                    "phaseout_start": {"single": 626350, "mfj": 1252700,
                                       "mfs": 626350},
                    "rate_28_breakpoint": 239100},
        },
        "state": {"name": "California", "type": "graduated_income",
                  "sdi_rate": 0.012,
                  "itemized_phaseout": {"single": 244000, "mfj": 488000,
                                        "hoh": 366000}},
        "exchange_rates": {"EUR": 1.08},
    }
    (tmp_path / "tax_law_2025.json").write_text(json.dumps(d))
    result = check_freshness(2025, str(tmp_path))
    assert result["status"] == "FRESH"
    assert "federal.filing_statuses" in result["unpinned_sources"]
    assert "UNPINNED" in result["recommendation"]


class TestStateRegimeType:
    def test_ca_ny_are_graduated_income(self):
        assert create_state_template("CA")["type"] == "graduated_income"
        assert create_state_template("NY")["type"] == "graduated_income"

    def test_generic_fallback_has_no_type_key(self):
        # Deliberate: unknown states keep pre-existing freshness behavior;
        # regimes are typed one modeled state at a time. CO (not on the
        # type-none allowlist) is the "truly unmodeled" example — round-3
        # finding D moved TX (and AK/NV/SD/WY) off the generic fallback onto
        # a minimal none-template; see TestNoTaxStateTemplates.
        assert "type" not in create_state_template("CO")

    def test_fl_template_shape(self):
        t = create_state_template("FL")
        assert t["code"] == "FL"
        assert t["type"] == "none"
        assert t["no_income_tax"] is True
        # No bracket/deduction keys: the freshness walker flags every empty
        # list, so a no-income-tax state structures them OUT (playbook step 1).
        assert "brackets" not in t
        assert "standard_deduction" not in t
        assert t["sales_tax"]["state_rate"] is None
        assert t["sales_tax"]["county_surtax_rate"] is None


class TestNoTaxStateTemplates:
    """Round-3 finding D: assigning type:none onto the generic type-less
    fallback (null standard_deduction + empty brackets) nagged INCOMPLETE
    forever for an allowed no-tax state. create_state_template now takes an
    OPTIONAL `tax_year` kwarg and returns a minimal none-template (mirroring
    FL's shape, minus the FL-only sales_tax block) for AK/NV/SD/TX/WY
    unconditionally, and for TN only when tax_year >= the Hall-tax repeal
    year (2021)."""

    @pytest.mark.parametrize("code", ["AK", "NV", "SD", "TX", "WY"])
    def test_always_none_states_get_minimal_template(self, code):
        t = create_state_template(code)
        assert t["code"] == code
        assert t["type"] == "none"
        assert t["no_income_tax"] is True
        assert "brackets" not in t
        assert "standard_deduction" not in t
        assert "sales_tax" not in t  # FL-only block, not extended

    def test_tx_template_shape(self):
        t = create_state_template("TX")
        assert t["code"] == "TX"
        assert t["name"] == "Texas"
        assert t["type"] == "none"
        assert t["no_income_tax"] is True
        assert "brackets" not in t
        assert "standard_deduction" not in t

    def test_tn_without_tax_year_keeps_generic_fallback(self):
        # No resolvable year — never guess "none" for TN.
        t = create_state_template("TN")
        assert "type" not in t
        assert "brackets" in t

    def test_tn_2020_keeps_generic_fallback(self):
        # Pre-repeal: TN still owed the Hall tax in TY2020.
        t = create_state_template("TN", tax_year=2020)
        assert "type" not in t
        assert "brackets" in t

    def test_tn_2021_gets_minimal_none_template(self):
        # First repeal year — the >= boundary.
        t = create_state_template("TN", tax_year=2021)
        assert t["code"] == "TN"
        assert t["name"] == "Tennessee"
        assert t["type"] == "none"
        assert t["no_income_tax"] is True
        assert "brackets" not in t
        assert "standard_deduction" not in t

    def test_fl_unaffected_by_tax_year_param(self):
        assert create_state_template("FL") == create_state_template("FL", tax_year=2025)

    def test_ca_unaffected_by_tax_year_param(self):
        assert create_state_template("CA") == create_state_template("CA", tax_year=2025)

    def test_generic_co_unaffected_by_tax_year_param(self):
        t = create_state_template("CO", tax_year=2025)
        assert "type" not in t
        assert t == create_state_template("CO")

    def test_tx_none_cache_is_freshness_clean(self, tmp_path):
        data = create_empty_tax_law(2025)
        data["state"] = create_state_template("TX")
        save_tax_law(2025, data, str(tmp_path))
        res = check_freshness(2025, str(tmp_path))
        nulls = [f for f in res["null_fields"]
                 if f == "state" or f.startswith("state.") or f.startswith("state ")]
        assert nulls == []


class TestFlFreshness:
    def _state_nulls(self, res):
        return [f for f in res["null_fields"]
                if f == "state" or f.startswith("state.") or f.startswith("state ")]

    def test_fl_block_clean_once_both_rates_fetched(self, tmp_path):
        data = create_empty_tax_law(2025)
        fl = create_state_template("FL")
        fl["sales_tax"]["state_rate"] = 0.06
        fl["sales_tax"]["county_surtax_rate"] = 0.01
        data["state"] = fl
        save_tax_law(2025, data, str(tmp_path))
        assert self._state_nulls(check_freshness(2025, str(tmp_path))) == []

    def test_unfetched_sales_tax_rates_are_flagged(self, tmp_path):
        data = create_empty_tax_law(2025)
        data["state"] = create_state_template("FL")
        save_tax_law(2025, data, str(tmp_path))
        assert self._state_nulls(check_freshness(2025, str(tmp_path))) == [
            "state.sales_tax.state_rate",
            "state.sales_tax.county_surtax_rate",
        ]

    def test_legacy_single_pin_forces_fl_provenance_migration(self, tmp_path):
        """The old county-table `state.FL` pin must not bless state_rate."""
        fl = create_state_template("FL")
        fl["sales_tax"]["state_rate"] = 0.06
        fl["sales_tax"]["county_surtax_rate"] = 0.01
        data = {
            "tax_year": 2025,
            "federal": {
                "fbar_threshold": 10000,
                "amt": {
                    "exemption": {"single": 88100, "mfj": 137000,
                                  "mfs": 68500},
                    "phaseout_start": {"single": 626350, "mfj": 1252700,
                                       "mfs": 626350},
                    "rate_28_breakpoint": 239100,
                },
            },
            "state": fl,
            "exchange_rates": {"EUR": 1.08},
            "_sources": {
                # Legacy schema: this sole pin named the county table while
                # state_rate was the template's hardcoded 0.06 seed.
                "state.FL":
                    "https://floridarevenue.com/Forms_library/current/dr15dss.pdf",
            },
        }
        save_tax_law(2025, data, str(tmp_path))

        legacy = check_freshness(2025, str(tmp_path))
        assert legacy["status"] == "INCOMPLETE"
        assert any(f.startswith("_sources.state.FL.county_surtax_rate")
                   and "refetch legacy FL caches" in f
                   for f in legacy["null_fields"])

        data["_sources"] = {
            "state.FL":
                "https://floridarevenue.com/taxes/taxesfees/Pages/sales_tax.aspx",
            "state.FL.county_surtax_rate":
                "https://floridarevenue.com/Forms_library/current/dr15dss.pdf",
        }
        save_tax_law(2025, data, str(tmp_path))
        migrated = check_freshness(2025, str(tmp_path))
        assert migrated["status"] == "FRESH"
        assert not any("legacy FL caches" in f
                       for f in migrated["null_fields"])

    def test_empty_state_dict_still_flagged(self, tmp_path):
        # {} keeps meaning "not configured"; only a POSITIVE type:"none"
        # means "no tax". Regression pin on the existing check.
        data = create_empty_tax_law(2025)
        save_tax_law(2025, data, str(tmp_path))
        assert "state (no state tax data configured)" in \
            check_freshness(2025, str(tmp_path))["null_fields"]


class TestScalarByDesignThresholds:
    """`_EXPECT_STRUCTURED` matches on the KEY NAME, and `threshold` is a dict
    keyed by filing status nearly everywhere it appears. CA's mental-health
    services surcharge is the exception — Prop 63 / R&TC §17043 set a flat
    $1,000,000 threshold and never indexed it, so `create_state_template`
    hardcodes a bare int.

    The key-name rule therefore flagged the toolkit's OWN canonical CA template,
    and every California return reported INCOMPLETE at /tax-compute Step 0.5
    forever. That is worse than the sdi_wage_limit / underpayment_rates /
    phaseout_rate nags that preceded it, because Step 0.5 says INCOMPLETE plus
    no live web means STOP: a gate that fires on correct data teaches the agent
    to reason its way past the gate. Observed end to end — an agent hit this,
    correctly diagnosed the false positive, reclassified the cache FRESH, and
    computed a full return on placeholder brackets.

    The exemption is keyed by PATH so it cannot leak: a scalar `niit.threshold`
    is still corruption and must still flag.
    """

    def _nulls(self, tmp_path, mutate=None):
        data = create_empty_tax_law(2025)
        data["state"] = create_state_template("CA")
        if mutate:
            mutate(data)
        save_tax_law(2025, data, str(tmp_path))
        return check_freshness(2025, str(tmp_path))["null_fields"]

    def test_ca_surcharge_threshold_is_not_flagged(self, tmp_path):
        assert not [f for f in self._nulls(tmp_path) if "mental_health" in f], (
            "the canonical CA template must not report itself INCOMPLETE")

    def test_the_exemption_does_not_leak_to_other_thresholds(self, tmp_path):
        def scalarize(d):
            d["federal"]["niit"]["threshold"] = 200000

        flagged = [f for f in self._nulls(tmp_path, scalarize)
                   if f.startswith("federal.niit.threshold")]
        assert flagged and "expected dict/list" in flagged[0], (
            "niit.threshold is per-filing-status; a bare scalar is corruption "
            f"and must still flag. got: {flagged}")

    def test_a_null_surcharge_threshold_is_still_a_gap(self, tmp_path):
        def blank(d):
            d["state"]["mental_health_surcharge"]["threshold"] = None

        flagged = [f for f in self._nulls(tmp_path, blank) if "mental_health" in f]
        assert flagged, "a missing threshold is still a gap, exempt or not"
        assert "expected dict/list" not in flagged[0], (
            "an exempt path must not tell the reader to supply a dict for a "
            f"field that is a scalar by design. got: {flagged[0]}")

    # Every state this repo models a template for.
    STATES = ("CA", "NY", "NJ", "FL", "TX", "WA")

    def test_no_state_template_trips_the_shape_rule_on_its_own_data(self, tmp_path):
        """The class-level invariant, not just the one field that bit us.

        `_EXPECT_STRUCTURED` matches key NAMES, so ANY template hardcoding an
        unindexed statutory scalar at one of those names flags itself — and
        pinning the individual path catches this one while missing the fifth.
        CA's surcharge was the fourth field of this shape after sdi_wage_limit,
        underpayment_rates and phaseout_rate, which is the signal that
        instance-level tests were not holding the line.

        So walk every template and assert none of them provokes a SHAPE
        complaint about its own canonical data. A `got None` complaint is a
        real gap and stays in scope; only `got <scalar>` is the false positive
        this guards. The next one fails HERE, when the template is added.
        """
        offenders = []
        for st in self.STATES:
            data = create_empty_tax_law(2025)
            data["state"] = create_state_template(st)
            save_tax_law(2025, data, str(tmp_path))
            offenders += [
                f"{st}: {f}"
                for f in check_freshness(2025, str(tmp_path))["null_fields"]
                if "expected dict/list, got" in f and "got None" not in f
            ]
        assert not offenders, (
            "a state template reports its OWN correct data as malformed, so "
            "every return for that state stops at /tax-compute Step 0.5 — and "
            "a gate that fires on correct data teaches the agent to override "
            "it. Either give the field its real dict shape, or add its exact "
            f"path to _SCALAR_BY_DESIGN. {offenders}")


class TestFlSalesTaxAbsentKeyGuard:
    """Codex round-13 P2: the recursive freshness walker only examines
    PRESENT keys, so a FL cache with the WHOLE sales_tax block (or just its
    county_surtax_rate sub-key) deleted contributes no null_fields at all —
    the modeled-state absent-key guards (sdi_wage_limit, itemized_phaseout,
    type) didn't cover this separate required block. /tax-compute must read
    state.sales_tax.county_surtax_rate to produce the mandatory Schedule A
    line 5a candidate."""

    def _state_nulls(self, res):
        return [f for f in res["null_fields"]
                if f == "state" or f.startswith("state.") or f.startswith("state ")]

    def test_fl_missing_whole_sales_tax_block_is_flagged(self, tmp_path):
        data = create_empty_tax_law(2025)
        fl = create_state_template("FL")
        del fl["sales_tax"]
        data["state"] = fl
        save_tax_law(2025, data, str(tmp_path))
        expected = ("state.sales_tax (key absent — required for FL's "
                    "Schedule A line 5a sales-tax election)")
        assert expected in self._state_nulls(check_freshness(2025, str(tmp_path)))

    def test_fl_missing_county_surtax_rate_key_is_flagged(self, tmp_path):
        # Distinct from the EXISTING present-but-null case (test_unfetched_
        # county_rate_is_flagged): here the sub-KEY itself is deleted, not
        # merely left null — a partially-edited cache, not the fresh
        # template's own default shape.
        data = create_empty_tax_law(2025)
        fl = create_state_template("FL")
        del fl["sales_tax"]["county_surtax_rate"]
        data["state"] = fl
        save_tax_law(2025, data, str(tmp_path))
        assert ("state.sales_tax.county_surtax_rate (key absent — required "
                "for FL's Schedule A line 5a sales-tax election)") in \
            self._state_nulls(check_freshness(2025, str(tmp_path)))

    def test_fl_missing_state_rate_key_is_flagged(self, tmp_path):
        data = create_empty_tax_law(2025)
        fl = create_state_template("FL")
        del fl["sales_tax"]["state_rate"]
        data["state"] = fl
        save_tax_law(2025, data, str(tmp_path))
        assert ("state.sales_tax.state_rate (key absent — required for FL's "
                "Schedule A line 5a sales-tax election)") in \
            self._state_nulls(check_freshness(2025, str(tmp_path)))

    def test_tx_missing_sales_tax_key_unaffected(self, tmp_path):
        # The guard is FL-specific — TX (a hand-typed type-none block with
        # no sales_tax modeled at all in v1) must not be flagged for a key
        # this state was never expected to have.
        data = create_empty_tax_law(2025)
        data["state"] = {"code": "TX", "name": "Texas", "type": "none",
                         "no_income_tax": True}
        save_tax_law(2025, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2025, str(tmp_path)))
        assert not any("sales_tax" in f for f in nulls)

    def test_fl_fully_fetched_stays_clean(self, tmp_path):
        # Regression: the genuine, fully-populated FL template (both keys
        # present, county rate fetched) is unaffected.
        data = create_empty_tax_law(2025)
        fl = create_state_template("FL")
        fl["sales_tax"]["state_rate"] = 0.06
        fl["sales_tax"]["county_surtax_rate"] = 0.01
        data["state"] = fl
        save_tax_law(2025, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2025, str(tmp_path)))
        assert not any("sales_tax" in f for f in nulls)

    def test_fl_scalar_sales_tax_is_flagged(self, tmp_path):
        # Codex round-17 P2: the absent-key guard's "sales_tax" not in
        # state_block check only fires when the key is entirely absent,
        # and its isinstance(..., dict) elif only fires when the value IS
        # a dict — a PRESENT scalar ("sales_tax": "oops") slipped through
        # both, and the generic walker did nothing for a bare non-dict/
        # non-list scalar either, so check_freshness reported no defect.
        data = create_empty_tax_law(2025)
        fl = create_state_template("FL")
        fl["sales_tax"] = "oops"
        data["state"] = fl
        save_tax_law(2025, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2025, str(tmp_path)))
        assert any("state.sales_tax" in f and "oops" in f for f in nulls)

    def test_fl_null_sales_tax_is_flagged(self, tmp_path):
        # Present-but-null was already caught by the generic walker's own
        # None-leaf check before this round's fix (a regression pin) —
        # adding "sales_tax" to _EXPECT_STRUCTURED changes the message
        # format (now "(expected dict/list, got None)") but must still flag.
        data = create_empty_tax_law(2025)
        fl = create_state_template("FL")
        fl["sales_tax"] = None
        data["state"] = fl
        save_tax_law(2025, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2025, str(tmp_path)))
        assert any(f.startswith("state.sales_tax") for f in nulls)


class TestStateTypeFreshnessGuard:
    """Codex P2: 'type' was in _SKIP_KEYS, so a deleted/null/misspelled
    regime discriminator passed freshness silently."""

    def _state_nulls(self, res):
        return [f for f in res["null_fields"]
                if f == "state" or f.startswith("state.") or f.startswith("state ")]

    def test_valid_none_passes(self, tmp_path):
        data = create_empty_tax_law(2025)
        fl = create_state_template("FL")
        fl["sales_tax"]["state_rate"] = 0.06
        fl["sales_tax"]["county_surtax_rate"] = 0.01
        data["state"] = fl
        save_tax_law(2025, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2025, str(tmp_path)))
        assert not any("type" in f for f in nulls)

    def test_null_type_flagged(self, tmp_path):
        data = create_empty_tax_law(2025)
        fl = create_state_template("FL")
        fl["sales_tax"]["state_rate"] = 0.06
        fl["sales_tax"]["county_surtax_rate"] = 0.01
        fl["type"] = None
        data["state"] = fl
        save_tax_law(2025, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2025, str(tmp_path)))
        assert any("type" in f for f in nulls)

    def test_misspelled_type_flagged(self, tmp_path):
        data = create_empty_tax_law(2025)
        fl = create_state_template("FL")
        fl["sales_tax"]["state_rate"] = 0.06
        fl["sales_tax"]["county_surtax_rate"] = 0.01
        fl["type"] = "no_tax"  # misspelled/unrecognized regime
        data["state"] = fl
        save_tax_law(2025, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2025, str(tmp_path)))
        assert ("state.type (unrecognized regime — expected "
                "graduated_income|none)") in nulls

    def test_legacy_block_without_type_key_unflagged(self, tmp_path):
        # A generic/legacy state block never carried a "type" key at all
        # (typed one modeled state at a time) — absence must NOT be flagged.
        # CO (not TX — round-5 finding B extended the absent-type guard to
        # cover every code create_state_template now generates a typed
        # "none" cache for, including TX) is genuinely outside the closed
        # expected-regime map, so it's the correct "truly unmodeled" example.
        data = create_empty_tax_law(2025)
        data["state"] = {"code": "CO", "name": "Colorado",
                         "brackets": [{"rate": 0.044, "min": 0, "max": None}]}
        save_tax_law(2025, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2025, str(tmp_path)))
        assert not any("type" in f for f in nulls)

    def test_ca_graduated_income_passes(self, tmp_path):
        data = create_empty_tax_law(2025)
        data["state"] = create_state_template("CA")
        save_tax_law(2025, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2025, str(tmp_path)))
        assert not any("type" in f for f in nulls)


class TestStateTypeAbsentKeyGuard:
    """Codex round-2 P2: round-1 only validated a PRESENT "type" value —
    deleting the key entirely still passed freshness for a modeled state
    (CA/NY/FL). Untyped generic/legacy states must stay unflagged.

    Round-5 finding B: create_state_template now also generates typed
    "none" caches for AK/NV/SD/TX/WY and (year-gated) TN, but the guard
    still only recognized CA/NY/FL — those newer states were silently
    exempt from the absent-key requirement. Extended to reuse
    _none_type_allowed (backed by the single _expected_state_regime table)
    instead of a hardcoded 3-state tuple."""

    def _state_nulls(self, res):
        return [f for f in res["null_fields"]
                if f == "state" or f.startswith("state.") or f.startswith("state ")]

    _EXPECTED_MSG = ("state.type (key absent — required for modeled states "
                     "CA/NY (graduated_income) and FL/AK/NV/SD/TX/WY/TN "
                     "(none, once TN's year-aware regime resolves to none))")

    def test_fl_missing_type_key_flagged(self, tmp_path):
        data = create_empty_tax_law(2025)
        fl = create_state_template("FL")
        fl["sales_tax"]["state_rate"] = 0.06
        fl["sales_tax"]["county_surtax_rate"] = 0.01
        del fl["type"]
        data["state"] = fl
        save_tax_law(2025, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2025, str(tmp_path)))
        assert self._EXPECTED_MSG in nulls

    def test_ca_missing_type_key_flagged(self, tmp_path):
        data = create_empty_tax_law(2025)
        ca = create_state_template("CA")
        del ca["type"]
        data["state"] = ca
        save_tax_law(2025, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2025, str(tmp_path)))
        assert self._EXPECTED_MSG in nulls

    def test_ny_missing_type_key_flagged(self, tmp_path):
        data = create_empty_tax_law(2025)
        ny = create_state_template("NY")
        del ny["type"]
        data["state"] = ny
        save_tax_law(2025, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2025, str(tmp_path)))
        assert self._EXPECTED_MSG in nulls

    def test_tx_missing_type_key_flagged(self, tmp_path):
        # Round-5 finding B: TX is on the closed no-income-tax allowlist
        # (capabilities._NO_INCOME_TAX_STATES) and create_state_template
        # now generates a typed "none" cache for it — a hand-edited or
        # half-migrated TX cache missing "type" must be flagged just like
        # FL, not silently pass as "generic/legacy".
        data = create_empty_tax_law(2025)
        data["state"] = {"code": "TX", "name": "Texas",
                         "brackets": [{"rate": 0.0, "min": 0, "max": None}]}
        save_tax_law(2025, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2025, str(tmp_path)))
        assert self._EXPECTED_MSG in nulls

    def test_tn_2020_missing_type_key_not_flagged(self, tmp_path):
        # Pre-repeal TN (Hall tax still live through TY2020): the
        # year-aware expected regime is "graduated_income", NOT a
        # positively-known "none" — TN is exempt from the absent-key
        # requirement until its regime actually resolves to "none".
        data = create_empty_tax_law(2020)
        data["state"] = {"code": "TN", "name": "Tennessee",
                         "brackets": [{"rate": 0.01, "min": 0, "max": None}]}
        save_tax_law(2020, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2020, str(tmp_path)))
        assert not any("key absent" in f for f in nulls)

    def test_tn_2021_missing_type_key_flagged(self, tmp_path):
        # TY2021 is the first year TN's expected regime resolves to
        # "none" (Hall tax repeal) — "type" is now required.
        data = create_empty_tax_law(2021)
        data["state"] = {"code": "TN", "name": "Tennessee"}
        save_tax_law(2021, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2021, str(tmp_path)))
        assert self._EXPECTED_MSG in nulls

    def test_co_generic_without_type_not_flagged(self, tmp_path):
        # CO is outside the closed expected-regime map entirely — absence
        # of "type" stays legacy behavior (never fetched, never required).
        data = create_empty_tax_law(2025)
        data["state"] = {"code": "CO", "name": "Colorado",
                         "brackets": [{"rate": 0.044, "min": 0, "max": None}]}
        save_tax_law(2025, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2025, str(tmp_path)))
        assert not any("key absent" in f for f in nulls)

    def test_ca_with_type_present_stays_clean(self, tmp_path):
        data = create_empty_tax_law(2025)
        data["state"] = create_state_template("CA")
        save_tax_law(2025, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2025, str(tmp_path)))
        assert not any("key absent" in f for f in nulls)


class TestStateRegimeVsCodeGuard:
    """Codex round-3 P1 finding C: the two guards above only validate
    VOCABULARY ("none"/"graduated_income" are recognized words) — a CA cache
    carrying type "none" passed both of them, because "none" is itself a
    valid word, just wrong for CA. The discriminator must be cross-checked
    against the state's own code via _expected_state_regime."""

    def _state_nulls(self, res):
        return [f for f in res["null_fields"]
                if f == "state" or f.startswith("state.") or f.startswith("state ")]

    def test_ca_typed_none_is_flagged(self, tmp_path):
        data = create_empty_tax_law(2025)
        ca = create_state_template("CA")
        ca["type"] = "none"
        data["state"] = ca
        save_tax_law(2025, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2025, str(tmp_path)))
        assert ('state.type ("none" not allowed for state.code "CA" — '
                'not a recognized no-income-tax state)') in nulls

    def test_fl_typed_none_is_clean(self, tmp_path):
        data = create_empty_tax_law(2025)
        fl = create_state_template("FL")
        fl["sales_tax"]["state_rate"] = 0.06
        fl["sales_tax"]["county_surtax_rate"] = 0.01
        data["state"] = fl
        save_tax_law(2025, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2025, str(tmp_path)))
        assert not any("state.code" in f for f in nulls)

    def test_tx_typed_none_is_clean(self, tmp_path):
        # TX has no pre-built create_state_template entry, but is in the
        # closed _NO_INCOME_TAX_STATES allowlist — a hand-typed TX "none"
        # block must not be flagged.
        data = create_empty_tax_law(2025)
        data["state"] = {"code": "TX", "name": "Texas", "type": "none",
                         "no_income_tax": True}
        save_tax_law(2025, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2025, str(tmp_path)))
        assert not any("state.code" in f for f in nulls)

    def test_tn_typed_none_flagged_for_2020(self, tmp_path):
        # TN's Hall tax was still live in TY2020 — "none" is wrong.
        data = create_empty_tax_law(2020)
        data["state"] = {"code": "TN", "name": "Tennessee", "type": "none",
                         "no_income_tax": True}
        save_tax_law(2020, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2020, str(tmp_path)))
        assert ('state.type ("none" not allowed for state.code "TN" — '
                'not a recognized no-income-tax state)') in nulls

    def test_tn_typed_none_clean_for_2021(self, tmp_path):
        # TY2021 is the first repeal year — "none" is correct.
        data = create_empty_tax_law(2021)
        data["state"] = {"code": "TN", "name": "Tennessee", "type": "none",
                         "no_income_tax": True}
        save_tax_law(2021, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2021, str(tmp_path)))
        assert not any("state.code" in f for f in nulls)

    def test_tn_typed_none_flagged_when_tax_year_missing(self, tmp_path):
        # Without a resolvable tax_year, default to the conservative
        # "not yet repealed" expectation (mirrors capabilities._tn_hall_
        # repealed's own missing-year default) — "none" should still flag.
        data = create_empty_tax_law(2020)
        data["state"] = {"code": "TN", "name": "Tennessee", "type": "none",
                         "no_income_tax": True}
        del data["tax_year"]
        save_tax_law(2020, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2020, str(tmp_path)))
        assert any("not allowed for state.code" in f for f in nulls)

    def test_unknown_code_graduated_income_is_clean(self, tmp_path):
        # CO is outside the closed expected-regime map — vocabulary-only
        # validation applies, so a plausible graduated_income CO cache is
        # unaffected by this guard. graduated_income is the SAFE default
        # for an unmodeled code (round-4 finding A) — unlike "none" below,
        # asserting a graduated income tax exists can't overstate a refund.
        data = create_empty_tax_law(2025)
        data["state"] = {"code": "CO", "name": "Colorado",
                         "type": "graduated_income",
                         "brackets": [{"rate": 0.044, "min": 0, "max": None}]}
        save_tax_law(2025, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2025, str(tmp_path)))
        assert not any("state.code" in f for f in nulls)

    def test_unknown_code_typed_none_is_flagged(self, tmp_path):
        # Round-4 finding A: the OPPOSITE of the graduated_income case above.
        # "none" is an AFFIRMATIVE no-tax claim, not a vocabulary-only word —
        # CO is not on the closed no-income-tax allowlist
        # (capabilities._NO_INCOME_TAX_STATES), so typing it "none" must be
        # flagged even though CO itself is an unmodeled/unmapped code (the
        # round-3 fix only caught a KNOWN-wrong code like CA; this closes
        # the gap for an unknown one). Renamed from
        # test_unknown_code_typed_none_is_also_clean, which codified the
        # bug this finding fixes.
        data = create_empty_tax_law(2025)
        data["state"] = {"code": "CO", "name": "Colorado", "type": "none"}
        save_tax_law(2025, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2025, str(tmp_path)))
        assert ('state.type ("none" not allowed for state.code "CO" — '
                'not a recognized no-income-tax state)') in nulls

    def test_wa_typed_none_is_flagged(self, tmp_path):
        # The sharpest case: Washington has no WAGE income tax (a common
        # taxpayer-facing claim) but IS NOT on the closed no-income-tax
        # allowlist here (it taxes capital gains via a state excise tax,
        # and — more importantly — it is simply not one of the modeled
        # AK/FL/NV/SD/TX/WY(+TN) codes) — asserting "none" for WA is exactly
        # the over-statement round-4 finding A targets, and is far more
        # plausible to hand-type by mistake than CO.
        data = create_empty_tax_law(2025)
        data["state"] = {"code": "WA", "name": "Washington", "type": "none"}
        save_tax_law(2025, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2025, str(tmp_path)))
        assert ('state.type ("none" not allowed for state.code "WA" — '
                'not a recognized no-income-tax state)') in nulls


class TestNoneTypeContradictionGuard:
    """Codex round-11 P1: an ALLOWED no-tax code (passes
    TestStateRegimeVsCodeGuard) could still carry leftover income-tax
    computation fields from a copied/partially-replaced graduated_income
    cache — e.g. a CA cache hand-edited to code: "FL", type: "none" without
    stripping CA's real brackets/standard_deduction. project_state_tax and
    loss_harvest._state_marginal_rate only check `if brackets:` before
    using them, so leftover REAL brackets would silently produce invented
    FL tax/harvest savings. A genuinely no-tax state's brackets/
    standard_deduction are never fetched at all — see
    create_state_template's FL/AK/NV/SD/TX/WY/TN templates."""

    def _state_nulls(self, res):
        return [f for f in res["null_fields"]
                if f == "state" or f.startswith("state.") or f.startswith("state ")]

    def test_fl_with_leftover_ca_brackets_is_flagged(self, tmp_path):
        data = create_empty_tax_law(2025)
        fl = create_state_template("FL")
        fl["sales_tax"]["state_rate"] = 0.06
        fl["sales_tax"]["county_surtax_rate"] = 0.01
        fl["brackets"] = [{"rate": 0.01, "min": 0, "max": 10412}]
        data["state"] = fl
        save_tax_law(2025, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2025, str(tmp_path)))
        assert any("state.brackets" in f and "FL" in f for f in nulls)

    def test_fl_with_leftover_standard_deduction_is_flagged(self, tmp_path):
        data = create_empty_tax_law(2025)
        fl = create_state_template("FL")
        fl["sales_tax"]["state_rate"] = 0.06
        fl["sales_tax"]["county_surtax_rate"] = 0.01
        fl["standard_deduction"] = {"single": 5540, "mfj": 11080}
        data["state"] = fl
        save_tax_law(2025, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2025, str(tmp_path)))
        assert any("state.standard_deduction" in f and "FL" in f for f in nulls)

    def test_fl_with_empty_brackets_key_triggers_generic_unfetched_not_contradiction(self, tmp_path):
        # An empty list / all-None dict is scaffold, not REAL data, so the
        # NEW contradiction guard (this class) must not fire — but the
        # PRE-EXISTING generic freshness walker still (correctly, if
        # coincidentally) flags the empty/None leaves as unfetched, since it
        # has no notion of "this key shouldn't exist for a none-type state"
        # either. This pins that the two mechanisms don't double-flag with
        # the SAME message, not that no flag appears at all.
        data = create_empty_tax_law(2025)
        fl = create_state_template("FL")
        fl["sales_tax"]["state_rate"] = 0.06
        fl["sales_tax"]["county_surtax_rate"] = 0.01
        fl["brackets"] = []
        fl["standard_deduction"] = {"single": None, "mfj": None}
        data["state"] = fl
        save_tax_law(2025, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2025, str(tmp_path)))
        assert not any("leftover/contradictory" in f for f in nulls)

    def test_tx_hand_typed_with_leftover_brackets_is_flagged(self, tmp_path):
        # TX has no pre-built create_state_template entry — a hand-typed
        # "none" block with leftover brackets must be flagged just the same.
        data = create_empty_tax_law(2025)
        data["state"] = {"code": "TX", "name": "Texas", "type": "none",
                         "no_income_tax": True,
                         "brackets": [{"rate": 0.05, "min": 0, "max": None}]}
        save_tax_law(2025, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2025, str(tmp_path)))
        assert any("state.brackets" in f and "TX" in f for f in nulls)

    def test_fl_clean_without_contradiction(self, tmp_path):
        # Regression: the genuine FL template (no brackets/standard_
        # deduction keys at all) stays clean.
        data = create_empty_tax_law(2025)
        fl = create_state_template("FL")
        fl["sales_tax"]["state_rate"] = 0.06
        fl["sales_tax"]["county_surtax_rate"] = 0.01
        data["state"] = fl
        save_tax_law(2025, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2025, str(tmp_path)))
        assert not any("state.brackets" in f or "state.standard_deduction" in f
                       for f in nulls)


class TestFloridaDorPinAuthoritative:
    def test_floridarevenue_com_is_authoritative(self):
        from agents.lib.tax_law_fetcher import _source_host_authoritative
        # FL DOR is a .com — without the allowlist entry every state.FL pin
        # would read non-authoritative and flip the cache INCOMPLETE.
        assert _source_host_authoritative(
            "https://floridarevenue.com/taxes/taxesfees/Pages/sales_tax.aspx")


class TestFloridaResultSection:
    def _template(self):
        return json.load(open(Path(__file__).resolve().parent.parent
                              / "agents" / "templates" / "result_template.json"))

    def test_florida_section_shape(self):
        fl = self._template()["florida_state"]
        assert fl["state_return_required"] is False
        assert "total_line_5a_candidate" in fl["sales_tax_deduction"]

    def test_no_income_tax_statement_ships_null(self):
        # Codex round-11 P2: the template used to ship the real affirmative
        # sentence as the DEFAULT value, so an untouched/un-pruned scaffold
        # in a non-FL taxpayer's result.json would already read as "FL
        # section present" to yoy_diff._section_present (which keys
        # presence on this ONE field via INFORMATIONAL_SECTIONS, precisely
        # because the rest of the section is legitimately all zeros/bools
        # even when genuinely computed). It must ship null so only
        # /tax-compute's no-income-tax-states branch (which writes the real
        # sentence for a genuine FL/type-none taxpayer) can make this field
        # non-empty.
        fl = self._template()["florida_state"]
        assert fl["no_income_tax_statement"] is None

    def test_use_tax_note_has_default_disclosure(self):
        # Codex P2: use_tax_note had no producer (shipped null forever). The
        # template now ships a fixed disclosure; /tax-compute confirms with
        # the taxpayer and tailors/removes it per their answer.
        fl = self._template()["florida_state"]
        assert fl["use_tax_note"] is not None
        assert "DR-15MO" in fl["use_tax_note"]
        assert "use tax" in fl["use_tax_note"]

    def test_ca_only_fields_carry_prune_notes(self):
        t = self._template()
        assert "_note_ca_savings" in t["ira_contribution"]
        assert "_note_california_refund" in t["balance_due_summary"]


class TestCacheYearMismatchGuard:
    """Codex round-8 finding C: check_freshness(year) never compared the
    cache's OWN declared tax_year against the year it was loaded for, so a
    mis-saved/misnamed cache (tax_law_2020.json whose body still says
    "tax_year": 2021) silently defeated every year-gated check downstream
    (e.g. the TN Hall-tax repeal boundary) while reporting FRESH."""

    def test_mismatched_tax_year_is_flagged(self, tmp_path):
        _write_cache(tmp_path, 2025)  # body says "tax_year": 2025
        # ...but saved/loaded under the tax_law_2020.json filename.
        (tmp_path / "tax_law_2025.json").rename(tmp_path / "tax_law_2020.json")
        result = check_freshness(2020, str(tmp_path))
        assert (
            "tax_year (cache declares 2025 but was loaded for 2020 — "
            "refetch or fix the filename)"
        ) in result["null_fields"]
        assert result["status"] == "INCOMPLETE"

    def test_matching_tax_year_is_clean(self, tmp_path):
        _write_cache(tmp_path, 2025)
        result = check_freshness(2025, str(tmp_path))
        assert not any(f.startswith("tax_year") for f in result["null_fields"])
        assert result["status"] == "FRESH"

    def test_missing_tax_year_key_is_flagged(self, tmp_path):
        _write_cache(tmp_path, 2025)
        cache_path = tmp_path / "tax_law_2025.json"
        data = json.loads(cache_path.read_text())
        del data["tax_year"]
        cache_path.write_text(json.dumps(data))
        result = check_freshness(2025, str(tmp_path))
        assert any(f.startswith("tax_year (missing or invalid")
                   for f in result["null_fields"])
        assert result["status"] == "INCOMPLETE"


class TestCacheReparsePointGuard:
    """load_tax_law() refuses to read tax law through a link it did not verify.

    The figures in this cache decide every number on the return, so a cache
    file that silently resolves somewhere else is a wrong-return vector, not
    just a containment one.
    """

    def test_refuses_a_symlinked_cache(self, tmp_path):
        real = tmp_path / "elsewhere.json"
        real.write_text(json.dumps(create_empty_tax_law(2025)))
        get_tax_law_path(2025, str(tmp_path)).symlink_to(real)
        with pytest.raises(ValueError, match="symlink or junction"):
            load_tax_law(2025, str(tmp_path))

    def test_guard_uses_the_junction_aware_helper(self):
        """is_symlink() is False for an NTFS junction, so on a Windows clone
        the guard did not fire at all — and on Python 3.14 it converts an
        lstat failure to False. Asserted structurally: a junction cannot be
        created on POSIX CI, and simulating one end-to-end needs os.name ==
        "nt", which makes pathlib construct WindowsPath process-wide.
        `from ... import` binds per module, so identity is checked here."""
        import agents.lib.secure_fs as sfs
        import agents.lib.tax_law_fetcher as tlf

        # `#` comments stripped: the prose names is_symlink() to explain why it
        # is not used, so a raw scan would match the explanation, not a call.
        code = "\n".join(line.split("#", 1)[0]
                         for line in Path(tlf.__file__).read_text().splitlines())
        assert tlf.is_reparse_point is sfs.is_reparse_point
        assert ".is_symlink()" not in code, (
            "tax_law_fetcher still calls Path.is_symlink() — it would read tax "
            "law through an unverified junction")

    def test_fails_closed_when_the_cache_cannot_be_inspected(self, tmp_path, monkeypatch):
        """Python 3.14's is_symlink() converts every OSError to False. Reading
        tax law through a path whose nature could not be established is the
        outcome that guard exists to prevent, so the error propagates."""
        cache = get_tax_law_path(2025, str(tmp_path))
        cache.write_text(json.dumps(create_empty_tax_law(2025)))

        def denied(path):
            raise PermissionError(13, "denied")

        monkeypatch.setattr("agents.lib.secure_fs.os.lstat", denied)
        with pytest.raises(PermissionError):
            load_tax_law(2025, str(tmp_path))

    def test_reads_a_plain_cache_file(self, tmp_path):
        cache = get_tax_law_path(2025, str(tmp_path))
        cache.write_text(json.dumps(create_empty_tax_law(2025)))
        assert load_tax_law(2025, str(tmp_path))["tax_year"] == 2025


# ---------------------------------------------------------------------------
# V4 — the tax-law ingest CLI and per-block provenance.
#
# The agent used to write `tax_law_<year>.json` by hand: a figure typed into the
# wrong key, a bracket list out of order, a `.com` source pasted under
# `_sources`, and `save_tax_law` re-dating the WHOLE cache on every save (E7),
# so editing one figure made every other block look freshly retrieved. `set`
# is the one write path now: schema-validated, year-asserted, source-checked,
# and dated per block in `_provenance` beside the string-valued `_sources`.
# ---------------------------------------------------------------------------

from agents.lib.tax_law_fetcher import (  # noqa: E402
    TaxLawSetError,
    _expected_pins,
    set_figure,
    validate_cache,
)

IRS = "https://www.irs.gov/pub/irs-drop/rp-25-32.pdf"
FTB = "https://www.ftb.ca.gov/forms/2026/2026-540-booklet.html"
NOW = datetime(2026, 9, 5, 12, 0, 0)


def _fresh_cache(tmp_path, year=2026):
    """A `create`d scaffold whose `_meta.fetched` is a month old, so a test can
    tell a preserved stamp from a re-dated one."""
    save_tax_law(year, create_empty_tax_law(year), str(tmp_path))
    p = tmp_path / f"tax_law_{year}.json"
    data = json.loads(p.read_text())
    data["_meta"]["fetched"] = datetime(2026, 8, 1).isoformat()
    p.write_text(json.dumps(data))
    return p


def _set(tmp_path, path, value, source=IRS, year=2026, **kw):
    kw.setdefault("now", NOW)
    return set_figure(year, path, value, source, workspace=str(tmp_path), **kw)


def _read(tmp_path, year=2026):
    return json.loads((tmp_path / f"tax_law_{year}.json").read_text())


class TestSetFigureWritesOneValidatedFigure:

    def test_a_leaf_is_written_pinned_and_dated_per_block(self, tmp_path):
        _fresh_cache(tmp_path)
        out = _set(tmp_path, "federal.filing_statuses.mfj.standard_deduction", 32200)
        data = _read(tmp_path)
        assert data["federal"]["filing_statuses"]["mfj"]["standard_deduction"] == 32200
        assert data["_sources"]["federal.filing_statuses"] == IRS
        # Dated per FIGURE: one re-fetched leaf never re-dates its siblings.
        prov = data["_provenance"]["federal.filing_statuses.mfj.standard_deduction"]
        assert prov == {"retrieved": NOW.isoformat(), "applies_to": 2026, "source": IRS}
        assert "federal.filing_statuses" not in data["_provenance"]
        assert out["pin"] == "federal.filing_statuses"
        # E7: the cache-wide stamp is NOT re-dated by a per-block write.
        assert data["_meta"]["fetched"] == datetime(2026, 8, 1).isoformat()
        assert data["tax_year"] == 2026

    def test_the_year_must_match_the_cache(self, tmp_path):
        _fresh_cache(tmp_path)
        (tmp_path / "tax_law_2027.json").write_text(
            json.dumps(dict(_read(tmp_path), tax_year=2026)))
        with pytest.raises(TaxLawSetError, match="tax_year"):
            _set(tmp_path, "federal.filing_statuses.mfj.standard_deduction",
                 32200, year=2027)

    def test_a_missing_cache_is_refused_not_created(self, tmp_path):
        with pytest.raises(TaxLawSetError, match="create"):
            _set(tmp_path, "federal.filing_statuses.mfj.standard_deduction", 32200)
        assert not (tmp_path / "tax_law_2026.json").exists()

    @pytest.mark.parametrize("path", [
        "tax_year", "_meta.fetched", "_sources.federal.amt",
        "_provenance.federal.amt", "federal._note", "nonsense",
        "federal.no_such_block", "federal.filing_statuses.mfj.bonus",
        "federal.filing_statuses.qss.standard_deduction",
    ])
    def test_a_path_outside_the_schema_is_refused(self, tmp_path, path):
        _fresh_cache(tmp_path)
        before = (tmp_path / "tax_law_2026.json").read_text()
        with pytest.raises(TaxLawSetError):
            _set(tmp_path, path, 1)
        assert (tmp_path / "tax_law_2026.json").read_text() == before

    def test_a_non_authoritative_source_is_refused_and_nothing_is_written(self, tmp_path):
        _fresh_cache(tmp_path)
        before = (tmp_path / "tax_law_2026.json").read_text()
        for url in ("https://smartasset.com/taxes", "javascript://www.irs.gov/x",
                    "irs.gov", "", None):
            with pytest.raises(TaxLawSetError, match="authoritative"):
                _set(tmp_path, "federal.filing_statuses.mfj.standard_deduction",
                     32200, source=url)
        assert (tmp_path / "tax_law_2026.json").read_text() == before

    @pytest.mark.parametrize("value", [None, True, "32200", float("nan"),
                                       float("inf"), -1, [32200], {"x": 1}])
    def test_a_number_leaf_takes_only_a_finite_non_negative_number(self, tmp_path, value):
        _fresh_cache(tmp_path)
        with pytest.raises(TaxLawSetError):
            _set(tmp_path, "federal.filing_statuses.mfj.standard_deduction", value)

    def test_a_rate_leaf_is_bounded_to_the_unit_interval(self, tmp_path):
        _fresh_cache(tmp_path)
        _set(tmp_path, "state", create_state_template("CA"), source=FTB)
        _set(tmp_path, "state.sdi_rate", 0.012, source=FTB)
        with pytest.raises(TaxLawSetError, match="rate"):
            _set(tmp_path, "state.sdi_rate", 1.2, source=FTB)
        with pytest.raises(TaxLawSetError, match="rate"):
            _set(tmp_path, "federal.additional_medicare_tax.rate", 9)

    def test_a_structural_constant_keeps_its_type(self, tmp_path):
        _fresh_cache(tmp_path)
        with pytest.raises(TaxLawSetError):
            _set(tmp_path, "federal.capital_gains_rates.twenty_pct_above", 1)
        _set(tmp_path, "federal.capital_gains_rates.twenty_pct_above", False)
        assert _read(tmp_path)["federal"]["capital_gains_rates"]["twenty_pct_above"] is False


class TestSetFigureValidatesBrackets:
    GOOD = [{"rate": 0.10, "min": 0, "max": 24800},
            {"rate": 0.12, "min": 24800, "max": 100800},
            {"rate": 0.22, "min": 100800, "max": None}]

    def test_a_contiguous_ascending_list_is_accepted(self, tmp_path):
        _fresh_cache(tmp_path)
        _set(tmp_path, "federal.filing_statuses.mfj.brackets", self.GOOD)
        assert _read(tmp_path)["federal"]["filing_statuses"]["mfj"]["brackets"] == self.GOOD

    @pytest.mark.parametrize("bad, why", [
        ([], "empty"),
        ([{"rate": 0.10, "min": 0, "max": 100}, {"rate": 0.12, "min": 200, "max": None}], "contiguous"),
        ([{"rate": 0.10, "min": 0, "max": 100}, {"rate": 0.09, "min": 100, "max": None}], "ascending"),
        ([{"rate": 1.5, "min": 0, "max": None}], "rate"),
        ([{"rate": 0.10, "min": 0, "max": 100}, {"rate": 0.12, "min": 100, "max": 50}], "max"),
        ([{"rate": 0.10, "min": 0, "max": None}, {"rate": 0.12, "min": 100, "max": None}], "last"),
        ([{"rate": 0.10, "min": 5, "max": None}], "zero"),
        ([{"rate": 0.10, "max": None}], "min"),
        ("not a list", "list"),
    ])
    def test_a_malformed_list_is_refused(self, tmp_path, bad, why):
        _fresh_cache(tmp_path)
        with pytest.raises(TaxLawSetError, match=why):
            _set(tmp_path, "federal.filing_statuses.mfj.brackets", bad)

    def test_underpayment_rates_need_dated_ascending_entries(self, tmp_path):
        _fresh_cache(tmp_path)
        good = [{"effective_from": "2026-04-01", "annual_rate": 0.07},
                {"effective_from": "2026-07-01", "annual_rate": 0.08}]
        _set(tmp_path, "federal.underpayment_rates", good)
        with pytest.raises(TaxLawSetError, match="ascending"):
            _set(tmp_path, "federal.underpayment_rates", list(reversed(good)))
        with pytest.raises(TaxLawSetError, match="effective_from"):
            _set(tmp_path, "federal.underpayment_rates",
                 [{"effective_from": "April", "annual_rate": 0.07}])

    def test_irmaa_tiers_need_ascending_thresholds(self, tmp_path):
        _fresh_cache(tmp_path)
        good = [{"magi_threshold": 106000, "annual_surcharge_per_person": 888},
                {"magi_threshold": 133000, "annual_surcharge_per_person": 2220}]
        _set(tmp_path, "federal.irmaa.tiers.single", good)
        with pytest.raises(TaxLawSetError, match="ascending"):
            _set(tmp_path, "federal.irmaa.tiers.single", list(reversed(good)))


class TestSetFigureCrossChecksTheStatuses:

    def test_mfj_must_be_at_least_single(self, tmp_path):
        _fresh_cache(tmp_path)
        _set(tmp_path, "federal.filing_statuses.mfj.standard_deduction", 32200)
        with pytest.raises(TaxLawSetError, match="mfj"):
            _set(tmp_path, "federal.filing_statuses.single.standard_deduction", 40000)
        _set(tmp_path, "federal.filing_statuses.single.standard_deduction", 16100)
        _set(tmp_path, "federal.capital_gains_rates.zero_pct_limit",
             {"single": 49450, "mfj": 98900, "mfs": 49450, "hoh": 66200})
        with pytest.raises(TaxLawSetError, match="mfj"):
            _set(tmp_path, "federal.capital_gains_rates.fifteen_pct_limit",
                 {"single": 545500, "mfj": 500000, "mfs": 272750, "hoh": 517450})

    def test_fifteen_must_be_above_zero_limit(self, tmp_path):
        _fresh_cache(tmp_path)
        _set(tmp_path, "federal.capital_gains_rates.zero_pct_limit",
             {"single": 49450, "mfj": 98900, "mfs": 49450, "hoh": 66200})
        with pytest.raises(TaxLawSetError, match="fifteen_pct_limit"):
            _set(tmp_path, "federal.capital_gains_rates.fifteen_pct_limit",
                 {"single": 40000, "mfj": 98900, "mfs": 20000, "hoh": 66200})

    def test_a_block_value_may_not_carry_keys_the_schema_lacks(self, tmp_path):
        _fresh_cache(tmp_path)
        with pytest.raises(TaxLawSetError, match="qss"):
            _set(tmp_path, "federal.capital_gains_rates.zero_pct_limit",
                 {"single": 49450, "mfj": 98900, "qss": 98900})


class TestSetFigureOnStateAndDynamicBlocks:

    def test_the_state_block_is_written_from_its_template_and_pinned_by_code(self, tmp_path):
        _fresh_cache(tmp_path)
        _set(tmp_path, "state", create_state_template("CA"), source=FTB)
        _set(tmp_path, "state.standard_deduction.mfj", 11080, source=FTB)
        data = _read(tmp_path)
        assert data["state"]["code"] == "CA"
        assert data["state"]["standard_deduction"]["mfj"] == 11080
        assert data["_sources"] == {"state.CA": FTB}
        # The whole-block write is dated under the pin; the leaf under its path.
        assert set(data["_provenance"]) == {"state.CA", "state.standard_deduction.mfj"}

    _NYS = "https://www.tax.ny.gov/pit/file/tax-tables/it201i-2025.htm"

    def _ny_cache(self, tmp_path):
        """A NY state block written straight in: `set` cannot take the whole
        NY template, whose brackets are keyed by schedule."""
        cache = _fresh_cache(tmp_path)
        seeded = json.loads(cache.read_text())
        seeded["state"] = create_state_template("NY")
        cache.write_text(json.dumps(seeded))
        return cache

    def test_new_yorks_table_bases_are_written_and_read_fresh(self, tmp_path):
        """AA14. `table_bases` seeds as None, and a None seed is a NUMBER to
        the generic checker, so `set` refused every list — the one write path
        could not record the key the engine reads. And the freshness walker
        flags every empty list, while `[]` is the documented fetch result for
        a table that follows its schedule (the City's TY2025 one does): a
        correct cache would have read INCOMPLETE forever."""
        self._ny_cache(tmp_path)
        _set(tmp_path, "state.tax_table.table_bases",
             [{"schedule": "a", "min": 23600}], source=self._NYS)
        _set(tmp_path, "state.nyc_tax.tax_table.table_bases", [],
             source=self._NYS)
        data = _read(tmp_path)
        assert data["state"]["tax_table"]["table_bases"] == [
            {"schedule": "a", "min": 23600}]
        assert data["state"]["nyc_tax"]["tax_table"]["table_bases"] == []
        nulls = check_freshness(2026, str(tmp_path))["null_fields"]
        assert not any("table_bases" in f for f in nulls), nulls
        assert not any("table_bases" in e for e in validate_cache(data, 2026))

    def test_an_unfetched_table_bases_still_reads_incomplete(self, tmp_path):
        self._ny_cache(tmp_path)
        nulls = check_freshness(2026, str(tmp_path))["null_fields"]
        assert "state.tax_table.table_bases" in nulls

    @pytest.mark.parametrize("bad", [
        # The old shape: the engine sums the figure, so one carrying it
        # refuses rather than lingering.
        [{"schedule": "a", "min": 23600, "base": 976.25}],
        [{"schedule": "", "min": 23600}],
        [{"schedule": "a", "min": -1}],
        [{"schedule": "a", "min": 23600}, {"schedule": "a", "min": 23600}],
        {"a": 23600},
    ])
    def test_a_table_bases_entry_of_the_wrong_shape_is_refused(self, tmp_path,
                                                                bad):
        self._ny_cache(tmp_path)
        with pytest.raises(TaxLawSetError, match="table_bases"):
            _set(tmp_path, "state.tax_table.table_bases", bad,
                 source=self._NYS)

    def test_a_state_field_needs_the_block_first(self, tmp_path):
        _fresh_cache(tmp_path)
        with pytest.raises(TaxLawSetError, match="state"):
            _set(tmp_path, "state.sdi_rate", 0.012, source=FTB)

    def test_a_state_block_with_the_wrong_key_set_is_refused(self, tmp_path):
        _fresh_cache(tmp_path)
        block = dict(create_state_template("CA"))
        block["nyc_tax"] = {}
        with pytest.raises(TaxLawSetError, match="nyc_tax"):
            _set(tmp_path, "state", block, source=FTB)
        block = dict(create_state_template("CA"))
        del block["sdi_rate"]
        with pytest.raises(TaxLawSetError, match="sdi_rate"):
            _set(tmp_path, "state", block, source=FTB)

    def test_a_state_field_outside_the_template_is_refused(self, tmp_path):
        _fresh_cache(tmp_path)
        _set(tmp_path, "state", create_state_template("CA"), source=FTB)
        with pytest.raises(TaxLawSetError, match="sales_tax"):
            _set(tmp_path, "state.sales_tax.state_rate", 0.06, source=FTB)

    def test_florida_county_rate_takes_its_own_pin(self, tmp_path):
        _fresh_cache(tmp_path)
        dor = "https://floridarevenue.com/taxes/taxesfees/Pages/sales_tax.aspx"
        _set(tmp_path, "state", create_state_template("FL"), source=dor)
        _set(tmp_path, "state.sales_tax.state_rate", 0.06, source=dor)
        county = "https://floridarevenue.com/forms_library/current/dr15dss.pdf"
        _set(tmp_path, "state.sales_tax.county_surtax_rate", 0.01, source=county)
        data = _read(tmp_path)
        assert data["_sources"] == {"state.FL": dor,
                                    "state.FL.county_surtax_rate": county}
        assert data["_provenance"]["state.FL.county_surtax_rate"]["retrieved"] == NOW.isoformat()

    def test_exchange_rates_take_an_upper_case_currency_and_a_positive_rate(self, tmp_path):
        _fresh_cache(tmp_path)
        url = "https://www.irs.gov/individuals/international-taxpayers/yearly-average-currency-exchange-rates"
        _set(tmp_path, "exchange_rates.EUR", 0.92, source=url)
        data = _read(tmp_path)
        assert data["exchange_rates"] == {"EUR": 0.92}
        assert data["_sources"]["exchange_rates"] == url
        with pytest.raises(TaxLawSetError, match="currency"):
            _set(tmp_path, "exchange_rates.eur", 0.92, source=url)
        with pytest.raises(TaxLawSetError):
            _set(tmp_path, "exchange_rates.GBP", 0, source=url)

    def test_historical_top_rate_is_keyed_by_year_and_is_a_rate(self, tmp_path):
        _fresh_cache(tmp_path)
        _set(tmp_path, "federal.historical_top_rate.2019", 0.37, applies_to=2019)
        prov = _read(tmp_path)["_provenance"]["federal.historical_top_rate.2019"]
        assert prov["applies_to"] == 2019
        with pytest.raises(TaxLawSetError, match="calendar year"):
            _set(tmp_path, "federal.historical_top_rate.2018", 0.37, applies_to=2019)
        with pytest.raises(TaxLawSetError, match="year"):
            _set(tmp_path, "federal.historical_top_rate.abc", 0.37)
        with pytest.raises(TaxLawSetError, match="rate"):
            _set(tmp_path, "federal.historical_top_rate.2018", 37)

    def test_ptet_takes_a_schedule_per_entity_state(self, tmp_path):
        _fresh_cache(tmp_path)
        url = "https://www.ftb.ca.gov/file/business/credits/pass-through-entity-elective-tax.html"
        _set(tmp_path, "ptet.CA", {"schedule": [[0, 0.093, 0]], "vintage": "AB 150"},
             source=url)
        data = _read(tmp_path)
        assert data["ptet"]["CA"]["schedule"] == [[0, 0.093, 0]]
        assert data["_sources"]["ptet.CA"] == url
        with pytest.raises(TaxLawSetError, match="lower"):
            _set(tmp_path, "ptet.NY", {"schedule": [[1000, 0.0685, 0]], "vintage": "x"},
                 source="https://www.tax.ny.gov/bus/ptet/")
        with pytest.raises(TaxLawSetError, match="state"):
            _set(tmp_path, "ptet.California", {"schedule": [[0, 0.093, 0]], "vintage": "x"},
                 source=url)


class TestFreshnessIsPerBlock:
    """E7: `_meta.fetched` stood in for retrieval time, and `save_tax_law`
    rewrote it on every save. Freshness is the OLDEST block now, with the
    cache-wide stamp as the legacy fallback for a block that has no entry."""

    def _populated(self, tmp_path, meta_fetched, provenance, others=None):
        """A cache the walker reports no gaps on — the readiness fixture's
        complete federal side plus a no-tax state — so status turns on age
        alone. Every populated block is pinned. `others` dates every block
        `provenance` does not name; left None, those blocks have no entry and
        fall back to the cache-wide stamp."""
        from tests.test_ty2026_readiness import _complete_federal_ty2026_tax_law
        data = _complete_federal_ty2026_tax_law()
        data["state"] = create_state_template("TX", tax_year=2026)
        data["_sources"] = {pin: IRS for pin, _ in _expected_pins(data)}
        if others is not None and isinstance(provenance, dict):
            provenance = dict({pin: {"retrieved": others.isoformat(), "applies_to": 2026}
                               for pin, _ in _expected_pins(data)}, **provenance)
        data["_provenance"] = provenance
        data["_meta"] = {"tax_year": 2026, "fetched": meta_fetched.isoformat(),
                         "source": "x"}
        (tmp_path / "tax_law_2026.json").write_text(json.dumps(data))
        res = check_freshness(2026, str(tmp_path))
        return res, [f for f in res["null_fields"] if "_provenance" in f]

    def test_the_oldest_block_decides(self, tmp_path):
        res, _ = self._populated(
            tmp_path, datetime.now(),
            {"federal.filing_statuses": {"retrieved": datetime.now().isoformat(),
                                         "applies_to": 2026},
             "federal.capital_gains_rates": {
                 "retrieved": (datetime.now() - timedelta(days=40)).isoformat(),
                 "applies_to": 2026}},
            others=datetime.now())
        assert res["status"] == "STALE"
        assert res["age_days"] == 40
        assert res["oldest_block"] == "federal.capital_gains_rates"
        assert "federal.capital_gains_rates" in res["recommendation"]
        assert res["block_ages"]["federal.filing_statuses"] == 0
        assert res["block_ages"]["federal.capital_gains_rates"] == 40
        assert set(res["block_ages"]) == {p for p, _ in _expected_pins(
            json.loads((tmp_path / "tax_law_2026.json").read_text()))}

    def test_a_stale_cache_wide_stamp_does_not_age_blocks_that_are_dated(self, tmp_path):
        recent = datetime.now().isoformat()
        res, _ = self._populated(
            tmp_path, datetime.now() - timedelta(days=300),
            {"federal.filing_statuses": {"retrieved": recent, "applies_to": 2026},
             "federal.capital_gains_rates": {"retrieved": recent, "applies_to": 2026}},
            others=datetime.now())
        assert res["status"] == "FRESH", res
        assert res["age_days"] == 0

    def test_a_block_without_an_entry_falls_back_to_the_legacy_stamp(self, tmp_path):
        res, _ = self._populated(
            tmp_path, datetime.now() - timedelta(days=300),
            {"federal.filing_statuses": {"retrieved": datetime.now().isoformat(),
                                         "applies_to": 2026}})
        assert res["status"] == "STALE"
        assert res["age_days"] == 300
        assert res["block_ages"]["federal.filing_statuses"] == 0
        assert res["block_ages"]["federal.capital_gains_rates"] == 300
        assert res["block_ages"][res["oldest_block"]] == 300

    def test_a_legacy_cache_with_no_provenance_reads_as_before(self, tmp_path):
        res, _ = self._populated(tmp_path, datetime.now() - timedelta(days=3), {})
        assert res["status"] == "FRESH" and res["age_days"] == 3
        assert res["oldest_block"] is None or res["oldest_block"] in res["block_ages"]

    def test_a_malformed_provenance_block_is_incomplete(self, tmp_path):
        for bad in (["x"], {"federal.filing_statuses": "2026-01-01"},
                    {"federal.filing_statuses": {"retrieved": "yesterday",
                                                 "applies_to": 2026}}):
            res, prov = self._populated(tmp_path, datetime.now(), bad)
            assert res["status"] == "INCOMPLETE", bad
            assert prov, bad

    def test_a_block_that_applies_to_another_year_is_incomplete(self, tmp_path):
        res, prov = self._populated(
            tmp_path, datetime.now(),
            {"federal.filing_statuses": {"retrieved": datetime.now().isoformat(),
                                         "applies_to": 2025}})
        assert res["status"] == "INCOMPLETE"
        assert any("applies_to" in f and "2025" in f for f in prov)

    def test_the_multi_period_blocks_may_name_another_year(self, tmp_path):
        data = create_empty_tax_law(2026)
        data["federal"]["historical_top_rate"] = {"2019": 0.37}
        data["federal"]["underpayment_rates"] = [
            {"effective_from": "2027-01-01", "annual_rate": 0.07}]
        data["_sources"] = {"federal.historical_top_rate": IRS,
                            "federal.underpayment_rates": IRS}
        data["_provenance"] = {
            "federal.historical_top_rate": {"retrieved": datetime.now().isoformat(),
                                            "applies_to": 2019},
            "federal.underpayment_rates": {"retrieved": datetime.now().isoformat(),
                                           "applies_to": "2027-Q1"}}
        data["_meta"] = {"tax_year": 2026, "fetched": datetime.now().isoformat()}
        (tmp_path / "tax_law_2026.json").write_text(json.dumps(data))
        res = check_freshness(2026, str(tmp_path))
        assert not [f for f in res["null_fields"] if "_provenance" in f], res["null_fields"]

    def test_expected_pins_is_the_shared_block_roster(self):
        data = create_empty_tax_law(2026)
        data["federal"]["salt_cap"] = {"amount": 40400, "mfs_amount": 20200}
        data["state"] = create_state_template("FL")
        data["state"]["sales_tax"] = {"state_rate": 0.06, "county_surtax_rate": 0.01}
        data["exchange_rates"] = {"EUR": 0.92}
        assert [k for k, _ in _expected_pins(data)] == [
            "federal.salt_cap", "state.FL", "state.FL.county_surtax_rate",
            "exchange_rates"]


class TestTheSetCli:

    def _run(self, *args, cwd):
        import subprocess
        return subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent.parent
                                 / "agents" / "lib" / "tax_law_fetcher.py"), *args],
            capture_output=True, text=True, cwd=str(cwd))

    def test_set_round_trips_through_the_command_line(self, tmp_path):
        _fresh_cache(tmp_path)
        proc = self._run("set", "federal.filing_statuses.mfj.standard_deduction",
                         "32200", "--source", IRS, "--year", "2026", cwd=tmp_path)
        assert proc.returncode == 0, proc.stderr
        data = _read(tmp_path)
        assert data["federal"]["filing_statuses"]["mfj"]["standard_deduction"] == 32200
        assert "federal.filing_statuses.mfj.standard_deduction" in data["_provenance"]
        proc = self._run("set", "federal.filing_statuses.mfj.brackets",
                         json.dumps(TestSetFigureValidatesBrackets.GOOD),
                         "--source", IRS, "--year", "2026", cwd=tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert len(_read(tmp_path)["federal"]["filing_statuses"]["mfj"]["brackets"]) == 3

    def test_a_refusal_is_one_error_line_and_exit_1(self, tmp_path):
        _fresh_cache(tmp_path)
        proc = self._run("set", "federal.filing_statuses.mfj.standard_deduction",
                         "32200", "--source", "https://nerdwallet.com/x",
                         "--year", "2026", cwd=tmp_path)
        assert proc.returncode == 1
        assert proc.stderr.startswith("Error: ") and "Traceback" not in proc.stderr
        proc = self._run("set", "federal.filing_statuses.mfj.brackets", "[{bad",
                         "--source", IRS, "--year", "2026", cwd=tmp_path)
        assert proc.returncode == 1 and "JSON" in proc.stderr

    def test_a_bare_word_is_a_string_and_a_numeral_is_a_number(self, tmp_path):
        _fresh_cache(tmp_path)
        proc = self._run("set", "state", json.dumps(create_state_template("CA")),
                         "--source", FTB, "--year", "2026", cwd=tmp_path)
        assert proc.returncode == 0, proc.stderr
        proc = self._run("set", "state.sdi_rate", "0.012", "--source", FTB,
                         "--year", "2026", cwd=tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert _read(tmp_path)["state"]["sdi_rate"] == 0.012
        # The bare word: no quotes on the command line, a string in the file.
        proc = self._run("set", "state.type", "graduated_income", "--source", FTB,
                         "--year", "2026", cwd=tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert _read(tmp_path)["state"]["type"] == "graduated_income"


class TestTheDocumentsNameTheIngestPath:

    def test_the_fetch_reference_writes_through_set_and_never_by_hand(self):
        fetch = (Path(__file__).resolve().parent.parent / ".agents" / "skills"
                 / "tax-setup" / "references" / "tax-law-fetch.md").read_text()
        assert "tax_law_fetcher.py set" in fetch
        assert "--source" in fetch and "--year" in fetch
        assert "by hand" in fetch.lower()
        assert "_provenance" in fetch

    def test_the_runbook_writes_through_set_and_names_the_per_block_dates(self):
        """The auditor definition is deliberately NOT updated here: its bytes
        are digest-pinned by the acceptance record, and an edit demands the
        fresh-session replay — scheduled with the season-open runbook (Y2)."""
        root = Path(__file__).resolve().parent.parent
        runbook = (root / "docs" / "ops" / "season-open-runbook.md").read_text()
        assert "tax_law_fetcher.py set" in runbook
        assert "_provenance" in runbook


class TestTheReviewRoundRules:
    """The rules the pre-merge review round added to `set` and to the
    freshness audit. Each is a door the first cut left open, named in the
    docstring; every synthetic figure is a plausible transcription slip."""

    def test_a_closed_top_bracket_is_refused_and_a_closed_table_cannot_undertax(self, tmp_path):
        """Dropping the "and over" row passed every check, and
        `compute_bracket_tax` returned the tax on the part below it."""
        from agents.lib.tax_computations import compute_bracket_tax
        _fresh_cache(tmp_path)
        closed = [{"rate": 0.10, "min": 0, "max": 11925},
                  {"rate": 0.12, "min": 11925, "max": 48475}]
        with pytest.raises(TaxLawSetError, match="last bracket's max must be null"):
            _set(tmp_path, "federal.filing_statuses.single.brackets", closed)
        with pytest.raises(ValueError, match="closed bracket table"):
            compute_bracket_tax(60000, closed)
        assert compute_bracket_tax(40000, closed)[0] == pytest.approx(1192.5 + 0.12 * 28075)

    def test_the_irs_inclusive_reading_is_normalised_not_refused(self, tmp_path):
        """'Over $11,925 but not over $48,475' transcribes as min 11926; the
        bounds are half-open, so it is normalised to the previous max."""
        _fresh_cache(tmp_path)
        inclusive = [{"rate": 0.10, "min": 0, "max": 11925},
                     {"rate": 0.12, "min": 11926, "max": None}]
        _set(tmp_path, "federal.filing_statuses.single.brackets", inclusive)
        assert _read(tmp_path)["federal"]["filing_statuses"]["single"]["brackets"][1]["min"] == 11925
        with pytest.raises(TaxLawSetError, match="half-open"):
            _set(tmp_path, "federal.filing_statuses.single.brackets",
                 [{"rate": 0.10, "min": 0, "max": 11925},
                  {"rate": 0.12, "min": 12000, "max": None}])

    def test_a_partial_block_is_refused_and_a_leaf_write_seeds_its_parent(self, tmp_path):
        """A block written with fewer keys than the seed replaced a fuller one,
        and a leaf under a missing parent materialised `{}` — both invisible
        to the freshness walker, which only sees keys that are present."""
        _fresh_cache(tmp_path)
        with pytest.raises(TaxLawSetError, match="exactly the schema's keys"):
            _set(tmp_path, "federal.filing_statuses.single", {"standard_deduction": 15000})
        data = _read(tmp_path)
        del data["federal"]["amt"]
        (tmp_path / "tax_law_2026.json").write_text(json.dumps(data))
        _set(tmp_path, "federal.amt.exemption.single", 90400)
        amt = _read(tmp_path)["federal"]["amt"]
        assert amt["exemption"] == {"single": 90400, "mfj": None, "mfs": None}
        assert set(amt) == set(create_empty_tax_law(2026)["federal"]["amt"])
        res = check_freshness(2026, str(tmp_path))
        assert any("federal.amt.exemption.mfj" in f for f in res["null_fields"])

    def test_a_whole_federal_or_ptet_write_is_refused_not_a_traceback(self, tmp_path):
        _fresh_cache(tmp_path)
        for dotted in ("federal", "ptet"):
            with pytest.raises(TaxLawSetError, match="too coarse"):
                _set(tmp_path, dotted, {"x": 1})

    def test_provenance_is_per_figure_and_the_oldest_entry_under_a_pin_dates_the_block(self, tmp_path):
        """One re-fetched leaf used to re-date the whole block (E7 at block
        scope) and overwrite the block's source with a page holding only
        that leaf."""
        _fresh_cache(tmp_path)
        old = NOW.replace(year=NOW.year - 1)
        _set(tmp_path, "federal.filing_statuses.mfj.standard_deduction", 32200, now=old)
        _set(tmp_path, "federal.filing_statuses.single.standard_deduction", 16100)
        data = _read(tmp_path)
        assert set(data["_provenance"]) == {
            "federal.filing_statuses.mfj.standard_deduction",
            "federal.filing_statuses.single.standard_deduction"}
        assert data["_sources"]["federal.filing_statuses"] == IRS
        res = check_freshness(2026, str(tmp_path))
        assert res["block_ages"]["federal.filing_statuses"] >= 364
        assert res["oldest_block"] == "federal.filing_statuses"

    def test_a_cache_with_no_usable_meta_is_not_re_dated_by_a_set(self, tmp_path):
        """`save_tax_law(stamp_fetched=False)` minted `_meta.fetched` when
        `_meta` was missing, dating every un-provenanced block today."""
        _fresh_cache(tmp_path)
        data = _read(tmp_path)
        del data["_meta"]
        (tmp_path / "tax_law_2026.json").write_text(json.dumps(data))
        _set(tmp_path, "federal.fbar_threshold", 10000)
        data = _read(tmp_path)
        assert "fetched" not in data["_meta"]
        assert check_freshness(2026, str(tmp_path))["status"] != "FRESH"

    def test_applies_to_is_checked_at_write_time_whatever_its_spelling(self, tmp_path):
        """Only an `int` period was compared to the year, so "TY2019" pinned a
        prior-year figure into the cache with no complaint."""
        _fresh_cache(tmp_path)
        for period in ("TY2019", "2019", 2019, 2019.0):
            with pytest.raises(TaxLawSetError, match="applies_to"):
                _set(tmp_path, "federal.filing_statuses.single.standard_deduction",
                     15750, applies_to=period)
        _set(tmp_path, "federal.filing_statuses.single.standard_deduction", 15750,
             applies_to=2026)

    def test_a_retrieved_time_in_the_future_is_incomplete(self, tmp_path):
        _fresh_cache(tmp_path)
        data = _read(tmp_path)
        data["_provenance"] = {"federal.filing_statuses": {
            "retrieved": NOW.replace(year=NOW.year + 2).isoformat(), "applies_to": 2026}}
        (tmp_path / "tax_law_2026.json").write_text(json.dumps(data))
        res = check_freshness(2026, str(tmp_path))
        assert any("in the future" in f for f in res["null_fields"])

    def test_the_audit_runs_the_write_rules_over_a_cache_it_did_not_write(self, tmp_path):
        """A hand-edited swap of the single and joint figures, and a zero
        where an indexed amount belongs, are INCOMPLETE — not FRESH."""
        _fresh_cache(tmp_path)
        data = _read(tmp_path)
        data["federal"]["schedule_1a"]["overtime_cap"] = {"single": 25000, "mfj": 12500}
        (tmp_path / "tax_law_2026.json").write_text(json.dumps(data))
        res = check_freshness(2026, str(tmp_path))
        assert res["status"] == "INCOMPLETE"
        assert any("overtime_cap.mfj" in f and "below" in f for f in res["null_fields"])

    def test_the_sibling_rule_is_derived_from_the_schema(self, tmp_path):
        """The hand table covered seven of the fifteen single/mfj pairs; the
        rule now reads the cache, so `schedule_1a` is covered too."""
        _fresh_cache(tmp_path)
        _set(tmp_path, "federal.schedule_1a.overtime_cap.single", 12500)
        with pytest.raises(TaxLawSetError, match="below"):
            _set(tmp_path, "federal.schedule_1a.overtime_cap.mfj", 6000)
        with pytest.raises(TaxLawSetError, match="unfetched state wearing a number"):
            _set(tmp_path, "federal.child_tax_credit.amount_per_child", 0)
        _set(tmp_path, "federal.child_tax_credit.amount_per_child", 2200)
        with pytest.raises(TaxLawSetError, match="below"):
            _set(tmp_path, "federal.child_tax_credit.refundable_limit", 2500)

    def test_a_non_finite_number_anywhere_in_the_cache_refuses_to_load(self, tmp_path):
        """`1e400` parses to inf and passed every `is None` guard; `_is_number`
        guards then SKIPPED it, and the next save wrote the literal Infinity."""
        _fresh_cache(tmp_path)
        raw = (tmp_path / "tax_law_2026.json").read_text()
        assert ": null" in raw
        (tmp_path / "tax_law_2026.json").write_text(raw.replace(": null", ": 1e400", 1))
        with pytest.raises(ValueError, match="non-finite"):
            load_tax_law(2026, str(tmp_path))
        res = check_freshness(2026, str(tmp_path))
        assert res["status"] == "INCOMPLETE" and "non-finite" in res["null_fields"][0]
        with pytest.raises(ValueError, match="non-finite"):   # the loader's, before any write
            _set(tmp_path, "federal.social_security.wage_base", 184500)


class TestTheCapitalGainsBreakpointsAreStatusSpecific:
    """X1: the seed carries four keys, `set` takes all four, and the ordering
    HoH between single and joint, MFS never above single, is a refusal."""

    def test_the_seed_keys_all_four_statuses(self):
        cg = create_empty_tax_law(2026)["federal"]["capital_gains_rates"]
        assert set(cg["zero_pct_limit"]) == {"single", "mfj", "mfs", "hoh"}
        assert set(cg["fifteen_pct_limit"]) == {"single", "mfj", "mfs", "hoh"}

    def test_set_takes_all_four_and_orders_them(self, tmp_path):
        _fresh_cache(tmp_path)
        _set(tmp_path, "federal.capital_gains_rates.zero_pct_limit",
             {"single": 48350, "mfj": 96700, "mfs": 48350, "hoh": 64750})
        _set(tmp_path, "federal.capital_gains_rates.fifteen_pct_limit",
             {"single": 533400, "mfj": 600050, "mfs": 300000, "hoh": 566700})
        with pytest.raises(TaxLawSetError, match="hoh"):
            _set(tmp_path, "federal.capital_gains_rates.zero_pct_limit.hoh", 99000)
        with pytest.raises(TaxLawSetError, match="mfs"):
            _set(tmp_path, "federal.capital_gains_rates.fifteen_pct_limit.mfs", 540000)

    def test_the_fetch_reference_and_the_cache_doc_say_all_four(self):
        """Y5 moved the per-status keying TABLE out of AGENTS.md, so the positive
        claim is pinned where the table now lives. The negative is asserted against
        BOTH files: the pre-X1 text grouped `capital_gains_rates` with the two-key
        blocks, and that grouping must not come back anywhere an accessor is
        written from."""
        root = Path(__file__).resolve().parent.parent
        fetch = (root / ".agents" / "skills" / "tax-setup" / "references"
                 / "tax-law-fetch.md").read_text()
        assert "`mfs`, `hoh`" in fetch and "half of joint" in fetch
        cache_doc = (root / "docs" / "contributing" / "tax-law-cache.md").read_text()
        assert "`filing_statuses` and `capital_gains_rates` have all four" in cache_doc
        for text in (cache_doc, (root / "AGENTS.md").read_text()):
            assert "`capital_gains_rates`, `child_tax_credit.phase_out_start`" not in text
        # The TABLE, not only the sentence under it. An accessor author reads the
        # row; asserting the prose alone let the row be edited to state X1's
        # exact pre-fix error — `capital_gains_rates` moved in beside the two-key
        # blocks — with this test green and the doc contradicting itself.
        rows = [ln for ln in cache_doc.splitlines()
                if ln.startswith("|") and "capital_gains_rates" in ln]
        assert len(rows) == 1, (
            f"`capital_gains_rates` appears in {len(rows)} keying rows; it is "
            "keyed by all four statuses and belongs in exactly one")
        assert "all four" in rows[0], (
            f"the keying table now says {rows[0].strip()!r} — collapsing capital "
            "gains breakpoints to `single` is what X1 fixed, and it handed an MFS "
            "or HoH filer another taxpayer's 0%/15% limits")


class TestTheStatusChainIsDerivedFromTheSchema:
    """joint ≥ head of household ≥ single ≥ separate, for every per-status
    map in the cache — derived by walking it, so the ordering table holds only
    what the chain cannot see (the transposed standard deductions, the 15% ≥
    0% breakpoints, the SALT and CTC pairs)."""

    def test_a_map_the_old_table_never_listed_is_ordered(self, tmp_path):
        _fresh_cache(tmp_path)
        _set(tmp_path, "federal.niit.threshold",
             {"single": 200000, "mfj": 250000, "mfs": 125000})
        with pytest.raises(TaxLawSetError, match="below"):
            _set(tmp_path, "federal.niit.threshold",
                 {"single": 200000, "mfj": 250000, "mfs": 260000})

    def test_the_four_status_breakpoints_follow_the_chain(self, tmp_path):
        _fresh_cache(tmp_path)
        good = {"single": 49450, "mfj": 98900, "mfs": 49450, "hoh": 66200}
        _set(tmp_path, "federal.capital_gains_rates.zero_pct_limit", good)
        for bad in ({**good, "hoh": 99000}, {**good, "mfs": 50000}, {**good, "hoh": 40000}):
            with pytest.raises(TaxLawSetError, match="below"):
                _set(tmp_path, "federal.capital_gains_rates.zero_pct_limit", bad)

    def test_a_populated_cache_satisfies_the_chain(self):
        from agents.lib.tax_law_fetcher import _get_path, _sibling_pairs, _STATUS_CHAIN
        assert _STATUS_CHAIN == ("mfj", "hoh", "single", "mfs")
        law = _populated()
        pairs = _sibling_pairs(law)
        assert pairs, "the walk found no per-status map"
        for hi, lo in pairs:
            assert _get_path(law, hi) >= _get_path(law, lo), (hi, lo)


class TestTheBreakpointRelationsTheStatuteImposes:
    """Not just "not below": head of household is STRICTLY above single, the
    separate 15% breakpoint strictly below single's, and the separate 0%
    breakpoint EQUAL to single's. A copied single column — the usual slip —
    passes every non-strict pair and none of these."""

    GOOD = {"single": 48350, "mfj": 96700, "mfs": 48350, "hoh": 64750}
    GOOD15 = {"single": 533400, "mfj": 600050, "mfs": 300000, "hoh": 566700}

    def test_a_copied_single_column_is_refused(self, tmp_path):
        _fresh_cache(tmp_path)
        _set(tmp_path, "federal.capital_gains_rates.zero_pct_limit", self.GOOD)
        _set(tmp_path, "federal.capital_gains_rates.fifteen_pct_limit", self.GOOD15)
        with pytest.raises(TaxLawSetError, match="not above"):
            _set(tmp_path, "federal.capital_gains_rates.zero_pct_limit.hoh", 48350)
        with pytest.raises(TaxLawSetError, match="not above"):
            _set(tmp_path, "federal.capital_gains_rates.fifteen_pct_limit.mfs", 533400)
        with pytest.raises(TaxLawSetError, match="same class"):
            _set(tmp_path, "federal.capital_gains_rates.zero_pct_limit.mfs", 40000)


class TestAStructuralConstantThatDriftedFromTheSeedIsIncomplete:
    """The Form 8938 `mfj_us` row was wrong in the seed until 2026-09-06;
    every cache created before that still carries the doubled pair, the
    engine prefers the cache, and nothing pinned it — a domestic couple with
    $180K abroad was told no Form 8938 was due."""

    def test_the_old_doubled_pair_reads_incomplete(self, tmp_path):
        _fresh_cache(tmp_path)
        data = _read(tmp_path)
        data["federal"]["form_8938_thresholds"]["mfj_us"] = {"year_end": 200000, "any_time": 300000}
        (tmp_path / "tax_law_2026.json").write_text(json.dumps(data))
        res = check_freshness(2026, str(tmp_path))
        assert res["status"] == "INCOMPLETE"
        assert any("form_8938_thresholds.mfj_us.year_end" in f and "older seed" in f
                   for f in res["null_fields"])

    def test_the_seed_itself_is_clean(self, tmp_path):
        from agents.lib.tax_law_fetcher import _structural_drift
        assert _structural_drift(create_empty_tax_law(2026), 2026) == []


def test_a_two_key_breakpoint_cache_reads_incomplete(tmp_path):
    """The walker sees only present keys, so a pre-X1 cache keyed single/mfj
    was FRESH while the accessor read None for HoH/MFS and refused, naming
    /tax-setup — a loop for the filer."""
    _fresh_cache(tmp_path)
    data = _read(tmp_path)
    data["federal"]["capital_gains_rates"] = {
        "zero_pct_limit": {"single": 48350, "mfj": 96700},
        "fifteen_pct_limit": {"single": 533400, "mfj": 600050}}
    (tmp_path / "tax_law_2026.json").write_text(json.dumps(data))
    res = check_freshness(2026, str(tmp_path))
    assert res["status"] == "INCOMPLETE"
    assert any("zero_pct_limit.hoh (key absent" in f for f in res["null_fields"])
    assert any("fifteen_pct_limit.mfs (key absent" in f for f in res["null_fields"])


class TestTheCodexRoundRules:
    """The local `codex review --base` pass over this branch. Three of its
    four findings are about a figure's AGE — the quantity the whole freshness
    contract is — and the fourth is a figure a state had no way to state."""

    def _legacy_pair(self, tmp_path):
        """A cache carrying an UNPROVENANCED figure beside the one a `set` is
        about to write: the shape every cache written before this command has,
        and the only one in which the legacy stamp still says anything."""
        _fresh_cache(tmp_path)
        data = _read(tmp_path)
        data["federal"]["filing_statuses"]["mfj"]["standard_deduction"] = 32200
        (tmp_path / "tax_law_2026.json").write_text(json.dumps(data))
        return (datetime.now() - datetime(2026, 8, 1)).days

    def test_a_leaf_write_leaves_its_untouched_siblings_as_old_as_they_were(
            self, tmp_path):
        """A block was dated from its entries ALONE once it had any, so one
        `set` against a legacy cache reported every other figure in that block
        as retrieved today — stale law passing the freshness contract, which
        is the direction AGENTS.md §4 makes a hard error. The ingest path
        writes one figure at a time, so this is its ordinary use."""
        legacy = self._legacy_pair(tmp_path)
        _set(tmp_path, "federal.filing_statuses.single.standard_deduction", 15750)
        res = check_freshness(2026, str(tmp_path))
        assert res["block_ages"]["federal.filing_statuses"] == legacy

    def test_the_legacy_stamp_drops_out_once_every_figure_is_covered(self, tmp_path):
        """...and the rule cannot be "a legacy cache is stale for ever", which
        is a gate crying wolf on a block that has been re-read in full."""
        self._legacy_pair(tmp_path)
        _set(tmp_path, "federal.filing_statuses.single.standard_deduction", 15750)
        _set(tmp_path, "federal.filing_statuses.mfj.standard_deduction", 32200)
        res = check_freshness(2026, str(tmp_path))
        assert res["block_ages"]["federal.filing_statuses"] == (
            datetime.now() - NOW).days

    def test_a_block_write_supersedes_the_entries_inside_it(self, tmp_path):
        """An entry for a figure the write has just replaced went on dating the
        block from a retrieval that no longer describes anything in it, so a
        complete re-fetch could not clear STALE."""
        _fresh_cache(tmp_path)
        last_year = NOW.replace(year=NOW.year - 1)
        _set(tmp_path, "federal.salt_cap.amount", 40400, now=last_year)
        _set(tmp_path, "federal.salt_cap", {"amount": 40400, "mfs_amount": 20200})
        data = _read(tmp_path)
        assert [k for k in data["_provenance"] if k.startswith("federal.salt_cap")] \
            == ["federal.salt_cap"]
        res = check_freshness(2026, str(tmp_path))
        assert res["block_ages"]["federal.salt_cap"] == (datetime.now() - NOW).days

    def test_a_state_can_say_it_has_no_standard_deduction(self, tmp_path):
        """New Jersey has none. The schema's null means "not fetched", so
        refusing every zero left the figure unstatable: writing it failed and
        leaving it null read INCOMPLETE for ever on a figure read correctly."""
        _fresh_cache(tmp_path)
        data = _read(tmp_path)
        data["state"] = create_state_template("CA")
        (tmp_path / "tax_law_2026.json").write_text(json.dumps(data))
        _set(tmp_path, "state.standard_deduction.single", 0, source=FTB)
        assert _read(tmp_path)["state"]["standard_deduction"]["single"] == 0

    def test_a_federal_zero_is_still_the_unfetched_state(self, tmp_path):
        """The asymmetry: every federal amount here is indexed, and none of
        them is ever zero."""
        _fresh_cache(tmp_path)
        with pytest.raises(TaxLawSetError, match="zero"):
            _set(tmp_path, "federal.fbar_threshold", 0)

    def test_a_retrieved_time_carrying_an_offset_is_read_not_crashed_on(
            self, tmp_path):
        """`fromisoformat` returns an AWARE datetime for a stamp written with
        an offset or a `Z`, and comparing one against the naive `datetime.now()`
        this module dates everything from raised TypeError — a crash where a
        freshness verdict belongs, reachable through `set_figure` itself."""
        from datetime import timezone
        _fresh_cache(tmp_path)
        out = _set(tmp_path, "federal.filing_statuses.single.standard_deduction",
                   15750, now=NOW.replace(tzinfo=timezone.utc))
        res = check_freshness(2026, str(tmp_path))
        assert isinstance(res["block_ages"][out["pin"]], int)
        assert not [f for f in res["null_fields"] if "_provenance" in f]


class TestEveryGateCliUsesOneExitContract:
    """V5. Four CLIs answer "is it safe to continue" and three of them said so
    the same way: 0 clean, 2 blocked, 1 could not run. The freshness check —
    which AGENTS.md §2 opens by calling the only check there is for a stale
    figure, because "a stale figure silently produces a wrong return that looks
    completely plausible" — printed its verdict and exited 0 for FRESH, STALE,
    INCOMPLETE and NOT_FOUND alike.

    So a workspace with NO CACHE AT ALL, the state every skill's Step 0.5 says to
    STOP on, reported success to anything reading `$?`. The one gate with no
    downstream check was the one that could not fail.

    The JSON is still what a reader acts on — which figures, which block, how old.
    The exit code says only whether it is safe to continue, which is what a script
    can act on, and now every gate says it the same way.
    """

    CLEAN, BLOCKED, COULD_NOT_RUN = 0, 2, 1

    def _run(self, args, cwd):
        return subprocess.run([sys.executable, *args], capture_output=True,
                              text=True, cwd=str(cwd))

    def _fetcher(self, workspace, year=2026):
        return self._run([str(REPO / "agents" / "lib" / "tax_law_fetcher.py"),
                          "freshness", str(year), str(workspace)], REPO)

    def test_a_fresh_cache_is_clean(self, tmp_path):
        """The other half, and the one this class was missing: every assertion
        here was about BLOCKING, so a change that exited 2 unconditionally passed
        the lot — and that failure blocks every run of every skill rather than
        letting a stale one through. A contract needs both ends pinned.

        The cache comes from `evals.fixture`, which exists to build a complete
        one and whose own suite asserts it reports FRESH — the same question, so
        the same builder rather than a second hand-filled schema here."""
        from evals import fixture as F
        F.save_tax_law(F.YEAR, F.tax_law(), str(tmp_path))
        proc = self._fetcher(tmp_path, year=F.YEAR)
        assert json.loads(proc.stdout)["status"] == "FRESH", proc.stdout[:400]
        assert proc.returncode == self.CLEAN

    def test_a_missing_cache_is_blocked_not_clean(self, tmp_path):
        """The case that was exiting 0, and the one every Step 0.5 calls a STOP."""
        proc = self._fetcher(tmp_path)
        assert proc.returncode == self.BLOCKED
        assert json.loads(proc.stdout)["status"] == "NOT_FOUND"

    def test_a_stale_but_otherwise_correct_cache_is_NOT_blocked(self, tmp_path):
        """STALE means "older than the window", not "wrong" — every figure here
        is right and `null_fields` is empty. Step 0.5's own bullet ends "if no
        changes, proceed", so blocking it would contradict the step it serves,
        and the freshness window is far shorter than a filing season.

        The first version of this fix DID block it, and the test that was meant
        to cover the case used an EMPTY cache — 56 nulls, reported INCOMPLETE —
        against an assertion that accepted either status. It never tested STALE
        at all."""
        from evals import fixture as F
        F.save_tax_law(F.YEAR, F.tax_law(), str(tmp_path))
        cache = tmp_path / f"tax_law_{F.YEAR}.json"
        law = json.loads(cache.read_text())
        law["_meta"]["fetched"] = "2000-01-01T00:00:00"
        cache.write_text(json.dumps(law))
        proc = self._fetcher(tmp_path, year=F.YEAR)
        report = json.loads(proc.stdout)
        assert report["status"] == "STALE", report["status"]
        assert report["null_fields"] == [], "not a clean stale cache; fix the setup"
        assert proc.returncode == self.CLEAN

    def test_an_incomplete_cache_is_not_blocked_and_keeps_its_null_fields(
            self, tmp_path):
        """INCOMPLETE fires on CORRECT DATA for the commonest taxpayer there is:
        `exchange_rates` is empty for every domestic-only filer. Blocking on it
        exited 2 on every Step 0.5 of a wholly ordinary return — `gates that cry
        wolf`, which is a gate the agent learns to reason past.

        And the report has to survive, because the step's remedy is to re-fetch
        the fields `null_fields` names."""
        from evals import fixture as F
        law = F.tax_law()
        law["exchange_rates"] = {}          # a domestic-only taxpayer
        F.save_tax_law(F.YEAR, law, str(tmp_path))
        proc = self._fetcher(tmp_path, year=F.YEAR)
        report = json.loads(proc.stdout)
        assert report["status"] == "INCOMPLETE"
        assert report["null_fields"], "the report must still say WHICH figures"
        assert proc.returncode == self.CLEAN

    def test_the_report_is_still_printed_when_it_blocks(self, tmp_path):
        """The exit code replaces nothing: it carries the one bit a script can
        act on without parsing, and the JSON carries everything a reader acts
        on."""
        proc = self._fetcher(tmp_path)
        assert proc.returncode == self.BLOCKED
        report = json.loads(proc.stdout)
        assert "status" in report and "recommendation" in report

    def test_a_cache_this_process_cannot_read_is_could_not_run(self, tmp_path):
        """The contract's third arm, which one revision of this claimed was
        unreachable — in the source, in a test docstring, in test-invariants and
        in the changelog. `load_tax_law` reads with `read_text()`, so a file this
        process cannot open raises PermissionError straight through, and
        AGENTS.md §1 mandates 0600 on that very file: a restored backup or one
        owned by another uid is the ordinary way here.

        "The figures are stale" and "I could not tell" are different
        instructions, and a traceback is neither."""
        if os.name == "nt" or os.geteuid() == 0:  # pragma: no cover
            pytest.skip("POSIX mode bits, and root reads anything")
        cache = tmp_path / "tax_law_2026.json"
        cache.write_text("{}")
        cache.chmod(0o000)
        try:
            proc = self._fetcher(tmp_path)
        finally:
            cache.chmod(0o600)
        assert proc.returncode == self.COULD_NOT_RUN
        assert "Traceback" not in proc.stderr
        assert "could not check freshness" in proc.stderr

    def test_a_corrupt_cache_is_blocked_and_not_a_traceback(self, tmp_path):
        """`check_freshness` handles a corrupt cache itself — it warns, removes
        it so the figures can be re-fetched, and reports NOT_FOUND. What this
        pins is that the wrapper does not turn that into either a clean exit or
        a traceback.

        It does NOT cover the contract's could-not-run arm: no input reaches it,
        which the source says beside the handler rather than leaving a reader to
        assume this test exercises it."""
        (tmp_path / "tax_law_2026.json").write_text("{not json")
        proc = self._fetcher(tmp_path)
        assert proc.returncode == self.BLOCKED
        assert "Traceback" not in proc.stderr
        assert json.loads(proc.stdout)["status"] == "NOT_FOUND"

    def test_every_skill_that_runs_it_no_longer_says_the_exit_code_lies(self):
        """Five skills carried a workaround in prose — "act on the status field,
        NOT the exit code, `freshness` exits 0 even for NOT_FOUND". They were
        compensating for a CLI that lied, which is the clearest possible sign the
        CLI was wrong. A workaround left standing over a fixed defect teaches the
        next reader that the fix did not happen."""
        skills = ("tax-compute", "tax-verify", "tax-fbar", "tax-review", "tax-parse")
        for name in skills:
            text = (REPO / ".agents" / "skills" / name / "SKILL.md").read_text()
            assert "freshness" in text, name
            assert "exits 0 even" not in text, (
                f"{name} still says the freshness command exits 0 on a bad cache")
            assert "Exit 2 means NOT_FOUND" in text, (
                f"{name} does not say what the blocked code means here")
            assert "it is the launcher refusing" in text, (
                f"{name} does not warn that `run.sh` and the engine's own venv "
                "guard ALSO exit 2, with no output — a claim that any non-zero "
                "exit is this gate's verdict is false, and the first version of "
                "this change pinned that false claim in a test")

    def test_this_class_covers_the_fetcher_and_says_who_covers_the_rest(self):
        """The first spelling of this was a source grep — `"2" in source and
        "blocked" in source.lower()` across four modules — and it asserted
        nothing: a 2 appears in any file of that size, so three of the four gates
        were "covered" by containing a digit. That is the same defect a review
        caught one PR earlier in a different test, which is the argument for
        writing down what a class does NOT cover.

        What this class covers is the FETCHER, behaviourally, because it is the
        gate that disagreed. The other three already exit 2 when blocked and
        already have that pinned where their own blocked states are cheap to
        build — `test_capabilities.py`, `test_preflight.py`, `test_tie_out.py`.
        Rebuilding those workspaces here to re-assert it would be a second, and
        weaker, owner of a question they already answer."""
        for module, suite in (("capabilities", "test_capabilities.py"),
                              ("preflight", "test_preflight.py"),
                              ("tie_out", "test_tie_out.py")):
            text = (REPO / "tests" / suite).read_text()
            assert "returncode == 2" in text or "_main(" in text and "== 2" in text, (
                f"{suite} no longer pins {module}'s blocked exit code, and this "
                "class does not cover it either — one of the two has to")


# --- Z1 slice 1: the NY fetched block ---------------------------------------
#
# The block is a SKELETON — every indexed figure null until /tax-setup fetches
# it. What these pin is the SHAPE, because the shape is where a state module
# goes wrong before it computes anything: a status mapped to another status's
# schedule, a regime with no key to put it in, and a "fetched" cache that is
# missing the rules that decide the return.
#
# Every case below exists because a review of the FIRST version of this block
# found the shape inadequate against the statute, in the UNDER-statement
# direction. They are written as the corrections, not as decoration.
#
# Design spec and sources:
# docs/superpowers/specs/2026-09-10-ny-state-module-design.md


class TestTheNewYorkBlock:

    STATUSES = ("single", "mfj", "mfs", "hoh", "qss")
    SCHEDULES = ("a", "b", "c")

    def test_the_brackets_are_keyed_by_schedule_not_by_status(self):
        """§601 has THREE schedules and the status pairings are statutory
        identities — (a) covers MFJ and the surviving spouse, (c) covers single
        and MFS. Five per-status lists would let a fetch populate `mfj` and
        leave `qss` empty, and an empty schedule is a silent ZERO tax rather
        than an error. One list per schedule cannot be half-fetched that way."""
        block = create_state_template("NY")
        assert set(block["brackets"]) == set(self.SCHEDULES), (
            "NY's brackets are keyed by something other than the three §601 "
            "schedules; a per-status keying can go half-fetched")
        assert all(v == [] for v in block["brackets"].values())
        mapping = block["bracket_schedule_by_status"]
        assert set(mapping) == set(self.STATUSES)
        assert mapping["qss"] == mapping["mfj"] == "a", (
            "QSS uses the married-joint schedule under §601(a) — splitting "
            "them invites an empty QSS schedule and a zero-tax return")
        assert mapping["mfs"] == mapping["single"] == "c", (
            "MFS uses the SINGLE schedule under §601(c). This is the pairing "
            "an agent is most likely to guess wrong, because MFS looks married")

    def test_the_standard_deduction_keeps_the_dependent_split(self):
        """The real trap in New York's table, and it is not the one it looks
        like. QSS's amount EQUALS MFJ's and MFS's equals single's — those are
        pairings to get right, not differences to hunt for. What has no
        analogue anywhere else in this cache is a SINGLE filer who can be
        claimed as a dependent on another return: a different, much smaller
        amount, on a fact nothing in the intake asks yet."""
        deduction = create_state_template("NY")["standard_deduction"]
        assert set(deduction) == set(self.STATUSES) | {"single_dependent"}
        assert all(v is None for v in deduction.values())

    def test_the_recapture_can_hold_every_tier(self):
        """The correction that mattered most. Above the entry threshold the
        taxpayer's NY TAXABLE INCOME selects a tier, each with its own
        recapture base and incremental benefit, and both move year to year. A
        shape holding only the entry threshold reproduces the first worksheet
        and nothing else — which UNDER-states every return above the first
        tier, in the direction the bias rule calls a hard error."""
        block = create_state_template("NY")["tax_table_benefit_recapture"]
        assert block["entry_threshold"] is None
        assert not isinstance(block["entry_threshold"], dict), (
            "the entry threshold is ONE figure for every filing status; a "
            "per-status dict here invites four copies of one number")
        assert set(block["tiers"]) == set(self.SCHEDULES), (
            "the recapture tiers are not keyed by §601 schedule, so the "
            "per-schedule base and benefit amounts have nowhere to live")
        assert block["full_recapture"] == {"nyagi_over": None, "rate": None}, (
            "the top regime is not a recapture at all — above a very high "
            "NYAGI the whole taxable income is taxed at one rate with no "
            "schedule, and without its own key the compute falls back to the "
            "tiers and under-states it")

    def test_the_citation_is_a_range_not_one_subsection(self):
        """§601 carries (d) through (d-7), each opening "Notwithstanding the
        provisions of subsection (d), (d-1) …" with its own taxable-year
        window: (d-5) governs 2026, (d-6) governs 2027 onward. Naming one
        subsection in a year-parameterised toolkit dates the comment the
        moment the window rolls — and the first draft named (d-1), which had
        been superseded four times."""
        source = (REPO / "agents" / "lib" / "tax_law_fetcher.py").read_text()
        block = source[source.index('"NY": {'):source.index('"FL": {')]
        assert "(d-1)" in block and "(d-7)" in block, (
            "the recapture comment no longer states the SUBSECTION RANGE, so a "
            "reader takes whichever one it names as current")
        assert "(d-5)" in block, (
            "the comment no longer says which subsection governs which year — "
            "the whole point of naming a range")

    def test_the_itemized_reduction_keeps_four_mechanisms_apart(self):
        """§615(f) reduces TWICE, a flat plateau sits above those, and §615(g)
        REPLACES all of it with a charitable-only limit at very high NYAGI.
        They are mutually exclusive by band: a compute that applies the
        reduction and then the charitable limit double-limits."""
        block = create_state_template("NY")["itemized_reduction"]
        assert set(block["first_threshold"]) == set(self.STATUSES)
        assert block["second_threshold"] is None, (
            "the second reduction lost its own threshold — it applies above a "
            "single figure for every status and is not the first one again")
        assert "second_band" in block, (
            "the second worksheet divides by its OWN band. Reusing the first "
            "is an undeclared coupling: right today, silently wrong if either "
            "figure moves")
        assert "flat_threshold" in block and "flat_rate" in block, (
            "the flat plateau between the second reduction and the charitable "
            "regime has no key, so it is only recoverable as an arithmetic "
            "coincidence of two others")
        assert "charitable_only_threshold" in block, (
            "nothing says where §615(f) STOPS and §615(g) takes over, so the "
            "two can be applied together")

    def test_a_lapsed_charitable_tier_can_be_expressed_as_lapsed(self):
        """Omitting a lapsed tier reads as "not fetched" — the same confusion
        `applies: None` exists to prevent for the city layer."""
        block = create_state_template("NY")
        source = (REPO / "agents" / "lib" / "tax_law_fetcher.py").read_text()
        assert block["charitable_limit"]["tiers"] == []
        assert "first_year" in source and "last_year" in source, (
            "the charitable tiers carry no YEAR WINDOW, so a tier that has "
            "expired cannot be distinguished from one nobody fetched")

    def test_the_tax_table_is_a_method_with_its_own_row_widths(self):
        """The table and the schedule are different METHODS, selected on BOTH
        NYAGI and taxable income, and the table is the schedule at each row's
        MIDPOINT rounded to the dollar. The row width is NOT constant — the
        bottom of the table is narrower than the $50 it settles into — so one
        assumed width would be a guess at exactly the figure this pins. The
        first version of this block held the cutoff as a bare scalar and had
        nowhere to put the widths, and `compute_ny_tax` ignored it entirely."""
        block = create_state_template("NY")["tax_table"]
        assert block["max_taxable_income"] is None
        assert block["row_segments"] == [], (
            "the tax table has no place for its row widths, so a caller has to "
            "assume one — and it is wrong at the bottom of the table")
        # AA14: the table is not always built on the schedule's published
        # base, and only the rows show where. None is "not fetched", which
        # the engine notes; [] is a fetch that found no departure.
        assert block["table_bases"] is None
        assert create_state_template("NY")["nyc_tax"]["tax_table"][
            "table_bases"] is None

    def test_new_york_does_not_conform_to_federal(self):
        """This key read True until the module existed, and that is what makes
        a bracket estimate look like a return: NY applies its own IT-225
        modifications, and IT-196 itemizing runs on federal rules AS THEY STOOD
        BEFORE THE TCJA."""
        assert create_state_template("NY")["conforms_to_federal"] is False

    def test_the_absent_personal_exemption_is_declared_not_missing(self):
        """Tax Law §616 grants an exemption for each one allowed under IRC
        §151(c) — the DEPENDENT subsection. §151(b), the taxpayer and spouse,
        is not referenced. A declared zero says that was researched; an absent
        key says nobody looked, and the two must not read the same."""
        block = create_state_template("NY")
        assert block["personal_exemption"]["amount"] == 0
        assert block["dependent_exemption"]["amount"] is None, (
            "the dependent exemption was seeded from the statute — §616 states "
            "it and does not index it, but a statutory figure may still be "
            "amended, so it is CONFIRMED by fetch like every other one")

    def test_every_credit_has_somewhere_to_land(self):
        """`personal_exemption: 0` exists so an absent key cannot read as "not
        researched". The same argument applies to the credits, and the first
        draft gave them nowhere at all — six credits on the face of IT-201."""
        credits = create_state_template("NY")["credits"]
        for name in ("household", "empire_state_child",
                     "child_and_dependent_care", "earned_income_share",
                     "real_property_tax", "college_tuition"):
            assert name in credits, f"no key for the {name} credit"

    def test_the_pension_exclusion_is_per_spouse_with_an_age_gate(self):
        """A household-level figure cannot express it: each spouse has their
        own exclusion, neither can claim the unused part of the other's, and
        there is an age gate. It sits on the IT-201 FACE, not on IT-225, which
        is why deferring modifications to IT-225 does not cover it."""
        block = create_state_template("NY")["subtractions"]
        assert set(block["pension_annuity_exclusion"]) == {"amount",
                                                           "minimum_age"}

    def test_the_city_layer_distinguishes_absent_from_unfetched(self):
        """`applies: None` is the same distinction `type` draws for the state:
        "no city tax" and "not fetched" are different answers, and a False here
        would let an unfetched cache compute a New York City return as zero."""
        block = create_state_template("NY")
        assert block["nyc_tax"]["applies"] is None
        assert block["yonkers_tax"]["applies"] is None, (
            "Yonkers has the same parity with the state residency test that "
            "the City has — a module that models one and not the other ships "
            "a silent gap for a real population")

    def test_the_school_tax_credit_is_two_credits(self):
        """A fixed amount by filing status under an income ceiling, AND a rate
        reduction that is its own per-schedule bracket table. A scalar holds
        neither, and the first draft had a scalar."""
        block = create_state_template("NY")["nyc_tax"]["school_tax_credit"]
        assert set(block["fixed_amount"]) == set(self.STATUSES)
        assert set(block["rate_reduction"]) == set(self.SCHEDULES), (
            "the rate reduction is a bracket schedule, not a number")

    def test_yonkers_holds_the_exclusion_that_keeps_it_honest(self):
        """The nonresident earnings tax allows an EXCLUSION first. Omitting it
        OVER-states the tax and bills a filer below the no-file floor for a tax
        they do not owe — the cry-wolf direction, and the one that teaches a
        taxpayer to reason past the next gate."""
        block = create_state_template("NY")["yonkers_tax"]
        assert block["nonresident_exclusion"] == {"table": []}
        assert "resident_surcharge_rate" in block
        assert "nonresident_earnings_rate" in block

    def test_the_mctmt_rates_are_labelled_by_zone(self):
        """Split by GEOGRAPHY, not by income tier. An unlabelled rate list read
        positionally mis-assigns the two zones, which nearly doubles or halves
        the tax. The threshold is PER ZONE and it is a CLIFF — once a zone's
        earnings exceed it the WHOLE zone amount is taxed, not the excess — so
        a key named like an exemption invites subtracting it."""
        block = create_state_template("NY")["mctmt"]
        assert set(block) == {"zone_1", "zone_2",
                              "net_earnings_threshold_per_zone"}
        assert "New York" in block["zone_1"]["counties"]
        assert "Westchester" in block["zone_2"]["counties"]
        assert block["zone_1"]["rate"] is None
        assert block["zone_2"]["rate"] is None

    def test_the_safe_harbour_is_new_yorks_and_says_so(self):
        """Equal quarters — April, June, September, the following January —
        against CA's 30/40/0/30 with no September installment. The thresholds
        are keyed to NEW YORK AGI, and the de-minimis figure is NOT the federal
        one: without a key a compute reaches for the federal amount and
        wrongly clears a New York filer who owes."""
        block = create_state_template("NY")
        assert block["estimated_payment_schedule"] == "equal_quarters"
        harbor = block["estimated_tax_safe_harbor"]
        assert "high_income_nyagi_threshold" in harbor, (
            "the threshold no longer names NYAGI, and a consumer holding "
            "FEDERAL AGI misapplies it for anyone with IT-225 modifications")
        assert "high_income_nyagi_threshold_mfs" in harbor, (
            "MFS has its own threshold and it is not the general one")
        assert "farmer_fisherman_share" in harbor, (
            "a farmer or fisherman makes ONE January installment at a lower "
            "share; without a key they are told to make four at the standard "
            "one")
        assert "de_minimis" in harbor

    def test_the_payroll_items_are_year_aware_by_shape(self):
        """The CA scar: the SDI wage ceiling DISAPPEARED in TY2024, and a cap
        read as a constant mis-computed every later return. Both NY items
        carry their cap as a fetched field for the same reason."""
        block = create_state_template("NY")
        assert set(block["paid_family_leave"]) == {"rate", "wage_cap"}
        assert set(block["disability_benefits"]) == {"employee_rate",
                                                     "weekly_cap"}

    def test_an_unfetched_ny_block_is_not_fresh(self):
        """The whole point of a skeleton: it must not pass as law. Every null
        and every empty container has to reach the freshness walker, or a cache
        that has been `create_state_template`'d and never fetched would compute
        a return. The empty-LIST regimes are named explicitly because a walker
        that flags nulls and not empty containers would pass them."""
        import tempfile
        with tempfile.TemporaryDirectory() as workspace:
            data = create_empty_tax_law(2026)
            data["state"] = create_state_template("NY", 2026)
            save_tax_law(2026, data, workspace)
            report = check_freshness(2026, workspace)
            assert report["status"] == "INCOMPLETE", (
                f"an entirely unfetched NY block reported {report['status']}")
            flagged = " ".join(report.get("null_fields", []))
            for expected in ("brackets", "tax_table_benefit_recapture",
                             "itemized_reduction", "standard_deduction",
                             "charitable_limit", "mctmt", "credits",
                             "nyc_tax", "yonkers_tax"):
                assert expected in flagged, (
                    f"{expected} is unfetched and the freshness walker did not "
                    f"name it:\n{flagged}")
