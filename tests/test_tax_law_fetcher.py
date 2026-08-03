"""Tests for tax_law_fetcher freshness logic — the sdi_wage_limit year-aware
null exception (Codex P1 on PR #29)."""
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib.tax_law_fetcher import (  # noqa: E402
    check_freshness,
    verify_source_pinning,
    _source_host_authoritative,
    create_empty_tax_law,
    create_state_template,
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
        assert t["sales_tax"]["state_rate"] == 0.06  # F.S. 212.05 structural
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
        fl["sales_tax"]["county_surtax_rate"] = 0.01
        data["state"] = fl
        save_tax_law(2025, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2025, str(tmp_path)))
        assert not any("type" in f for f in nulls)

    def test_null_type_flagged(self, tmp_path):
        data = create_empty_tax_law(2025)
        fl = create_state_template("FL")
        fl["sales_tax"]["county_surtax_rate"] = 0.01
        fl["type"] = None
        data["state"] = fl
        save_tax_law(2025, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2025, str(tmp_path)))
        assert any("type" in f for f in nulls)

    def test_misspelled_type_flagged(self, tmp_path):
        data = create_empty_tax_law(2025)
        fl = create_state_template("FL")
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
        fl["sales_tax"]["county_surtax_rate"] = 0.01
        fl["brackets"] = [{"rate": 0.01, "min": 0, "max": 10412}]
        data["state"] = fl
        save_tax_law(2025, data, str(tmp_path))
        nulls = self._state_nulls(check_freshness(2025, str(tmp_path)))
        assert any("state.brackets" in f and "FL" in f for f in nulls)

    def test_fl_with_leftover_standard_deduction_is_flagged(self, tmp_path):
        data = create_empty_tax_law(2025)
        fl = create_state_template("FL")
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
