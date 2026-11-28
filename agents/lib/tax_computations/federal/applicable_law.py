"""Readiness of the covered-year law used for a filing package."""

from datetime import datetime
import re

from agents.lib import capabilities, tax_law_fetcher as cache
from agents.lib.projection import _full_status_key, _section_computed
from agents.lib.tax_computations.state.registry import STATE_SECTIONS
from agents.lib.yoy_diff import _section_present


def _positive(value) -> bool:
    return cache._is_number(value) and value > 0


def _state_figures(result: dict, profile: dict, law: dict, year: int, status: str) -> tuple[set[str], list[str]]:
    active = {entry.code for entry in STATE_SECTIONS if _section_computed(result, entry.section, entry.evidence_fields)
              or entry.informational_key and _section_present(result, entry.section)}
    selected = capabilities._filing_state(profile)
    if selected and cache._expected_state_regime(selected, year):
        active.add(selected)
    if not active:
        return set(), []
    state = law.get("state")
    code = cache.declared_state_code(law)
    if code is None or not isinstance(state, dict) or active != {code} or state.get("type") != cache._expected_state_regime(code, year):
        return set(), ["The cache needs the covered-year state law for " + ", ".join(sorted(active)) + "."]

    fields = {"state.type"}
    issues = []
    if code == "CA":
        key = {"mfs": "single", "qss": "mfj"}.get(status, status)
        fields.update({f"state.standard_deduction.{key}", "state.brackets", "state.personal_exemption.amount",
                       f"state.exemption_phaseout.threshold.{status}", f"state.exemption_phaseout.band.{status}",
                       "state.exemption_phaseout.reduction_per_step", f"state.amt_screening.{status}",
                       f"state.underpayment_screen.minimum_balance.{status}", "state.underpayment_screen.tax_fraction"})
        amt_key = "mfj" if status == "qss" else status
        fields.update({f"state.amt.exemption.{amt_key}", f"state.amt.phaseout_start.{amt_key}", "state.amt.tmt_rate",
                       "state.amt.phaseout_rate", "state.schedule_ca.fund_asset_minimum"})
        if profile.get("dependents") or result.get("dependents"):
            fields.add("state.dependent_exemption.amount")
        if (result.get("schedule_ca_itemized") or {}).get("use_itemized") is True:
            fields.add(f"state.itemized_phaseout.{key}")
        if any(_positive(cache._get_path(result, f"{entry.section}.line_{123 if entry.variant == '540NR' else 113}"))
               for entry in STATE_SECTIONS if entry.code == "CA"):
            fields.add("state.underpayment_rates")
        fields.update({"state.tax_table.max_taxable_income", "state.tax_table.rows"})
    elif code == "NY":
        band = (state.get("bracket_schedule_by_status") or {}).get(status)
        if band not in ("a", "b", "c"):
            return fields, ["New York requires its covered-year bracket schedule for the filing status."]
        fields.update({f"state.brackets.{band}", f"state.standard_deduction.{status}",
                       "state.tax_table.max_taxable_income", "state.tax_table.row_segments",
                       "state.tax_table.table_bases"})
    elif code == "NC":
        fields.update({"state.brackets", f"state.standard_deduction.{status}"})
        if state.get("rate_tax_year") != year:
            issues.append("state.brackets: verify the North Carolina rate for the covered year.")
        if profile.get("dependents") or result.get("dependents"):
            key = {"mfj": "mfj_qss", "qss": "mfj_qss", "hoh": "hoh"}.get(status, "single_mfs")
            fields.add(f"state.child_deduction.{key}")
    elif code == "WA":
        fields.update(f"state.wa_capital_gains.{key}" for key in
                      ("tax_year", "standard_deduction", "charitable_minimum", "charitable_cap", "rates"))
    elif code == "FL" and _section_present(result, "florida_state"):
        sales = cache._get_path(result, "florida_state.sales_tax_deduction") or {}
        if sales.get("method") == "optional_tables" and _positive(sales.get("total_line_5a_candidate")):
            fields.update({"state.sales_tax.state_rate", "state.sales_tax.county_surtax_rate"})
    for optional in ("local", "modifications"):
        if optional in state:
            fields.add(f"state.{optional}")
    return fields, issues


def _medicare_figures(status: str) -> set[str]:
    # Form 8959: a qualifying surviving spouse uses the single threshold,
    # though NIIT gives that status the joint one.
    key = {"mfj": "mfj", "mfs": "mfs"}.get(status, "single")
    return {"federal.additional_medicare_tax.rate", f"federal.additional_medicare_tax.threshold.{key}"}


def _federal_figures(result: dict, profile: dict, law: dict, year: int, status: str) -> set[str]:
    from agents.lib.tax_computations.charitable import SUBSTANTIATION_LIMITS, substantiated_gifts
    from agents.lib.tax_computations.federal.schedule_c import schedule_c_net

    flags = profile.get("flags") or {}
    form = result.get("form_1040") or {}
    filing_key = "mfj" if status == "qss" else status
    two_status_key = "mfj" if filing_key == "mfj" else "single"
    fields = {f"federal.filing_statuses.{filing_key}.brackets", f"federal.filing_statuses.{filing_key}.standard_deduction"}

    if _positive(form.get("line_3a_qualified_dividends")) or _positive(cache._get_path(result, "schedule_d.part_ii_long_term.line_15_net_long_term")):
        fields.update(f"federal.capital_gains_rates.{key}.{filing_key}" for key in ("zero_pct_limit", "fifteen_pct_limit"))
    if cache._get_path(result, "schedule_d.tax_worksheet_tax") is not None:
        fields.add("federal.tax_table.max_taxable_income")
    if (profile.get("dependents") or result.get("dependents") or _positive(form.get("line_19_ctc"))
            or _positive(form.get("line_28_refundable_actc"))):
        fields.update({"federal.child_tax_credit.amount_per_child", "federal.child_tax_credit.refundable_limit",
                       f"federal.child_tax_credit.phase_out_start.{two_status_key}"})
    itemizes = (result.get("schedule_a") or {}).get("use_itemized") is True
    prior = profile.get("prior_year") or {}
    if (itemizes or flags.get("has_iso_exercise") is True or _section_present(result, "form_6251")
            or _positive(form.get("line_17_schedule2_part1")) or _positive(prior.get("amt_credit_carryforward"))):
        amt_key = "single" if status == "hoh" else filing_key
        fields.update({f"federal.amt.exemption.{amt_key}", f"federal.amt.phaseout_start.{amt_key}", "federal.amt.rate_28_breakpoint"})
        if cache._get_path(law, "federal.amt.phaseout_rate") is not None:
            fields.add("federal.amt.phaseout_rate")
    if itemizes:
        fields.add("federal.salt_cap.mfs_amount" if status == "mfs" else "federal.salt_cap.amount")
    # Federal or California-only itemized gifts: both rest on these limits.
    gifts = substantiated_gifts(result)
    if gifts is None or any(gifts):
        fields.update(f"federal.charitable_substantiation.{key}" for key in SUBSTANTIATION_LIMITS)
    if flags.get("has_hsa") is True or _section_present(result, "form_8889_hsa"):
        fields.update({"federal.hsa_limits.self_only", "federal.hsa_limits.family", "federal.hsa_limits.catch_up_55_plus"})
    if flags.get("has_ira_contributions") is True or _section_present(result, "ira_contribution"):
        fields.add("federal.ira_limits.contribution_limit")
    if flags.get("has_foreign_tax_paid") is True or _section_present(result, "form_1116_ftc"):
        fields.add(f"federal.foreign_tax_credit_simplified.max_{two_status_key}")
    business_income = schedule_c_net(result, profile)
    # Triggered by the INPUTS. Keyed on Form 8959's section, a missing figure
    # left no section, so the requirement never arose: circular.
    if (_section_present(result, "form_8959_additional_medicare") or _positive(form.get("line_1a_wages"))
            or _positive(business_income)):
        fields.update(_medicare_figures(status))
    # NIIT was never required: with its threshold null compute_niit returned 0
    # and a $70,000 investment return reached a final package.
    if (_section_present(result, "form_8960_niit") or _section_present(result, "schedule_e_rentals")
            or any(_positive(form.get(line)) for line in (
                "line_2b_taxable_interest", "line_3b_ordinary_dividends", "line_7_capital_gain"))):
        # §1411(b) gives a surviving spouse the joint threshold, which
        # `two_status_key` already holds for that status.
        niit_key = "mfs" if status == "mfs" else two_status_key
        fields.update({"federal.niit.rate", f"federal.niit.threshold.{niit_key}"})
    if _positive(form.get("schedule_3_line_11_excess_ss")):
        fields.update({"federal.social_security.wage_base", "federal.social_security.employee_rate"})
    business = business_income
    if _positive(cache._get_path(result, "schedule_se.schedule_2_line_4_se_tax")) or (
            _positive(business) and not (result.get("schedule_se") or {}).get("by_owner")):
        fields.update({"federal.social_security.wage_base", "federal.social_security.employee_rate",
                       "federal.se_tax.rate", "federal.se_tax.net_earnings_factor"})
    if business is not None or flags.get("has_k1_income") is True or _section_present(result, "form_8995_qbi"):
        fields.add(f"federal.qbi_threshold.{two_status_key}")
        phase_range = "mfj" if status == "mfj" else "other"
        optional = f"federal.qbi_phase_in_range.{phase_range}"
        if cache._get_path(law, optional) is not None:
            fields.add(optional)
    if _positive(form.get("line_38_estimated_tax_penalty")) and (result.get("form_2210") or {}).get("penalty_computed") is True:
        fields.add("federal.underpayment_rates")
    if _positive(cache._get_path(result, "form_8621_pfic.total_1291_addon")):
        fields.update({"federal.historical_top_rate", "federal.underpayment_rates"})
    if 2025 <= year <= 2028 and status != "mfs":
        if flags.get("has_qualified_tips") is True or _positive(cache._get_path(result, "schedule_1a.tips.allowed")):
            fields.update({"federal.schedule_1a.tips_cap", f"federal.schedule_1a.tips_overtime_phaseout_start.{two_status_key}"})
        if flags.get("has_qualified_overtime") is True or _positive(cache._get_path(result, "schedule_1a.overtime.allowed")):
            fields.update({f"federal.schedule_1a.overtime_cap.{two_status_key}", f"federal.schedule_1a.tips_overtime_phaseout_start.{two_status_key}"})
        if flags.get("has_car_loan_interest") is True or _positive(cache._get_path(result, "schedule_1a.car_loan.allowed")):
            fields.update({"federal.schedule_1a.car_loan_cap", f"federal.schedule_1a.car_loan_phaseout_start.{two_status_key}"})
        if flags.get("age_65_plus_primary") is True or flags.get("age_65_plus_spouse") is True:
            fields.update({"federal.schedule_1a.senior_amount_per_person", f"federal.schedule_1a.senior_phaseout_start.{two_status_key}"})
    if _section_present(result, "charitable_non_itemizer"):
        fields.add("federal.charitable_non_itemizer.joint_amount" if status == "mfj" else "federal.charitable_non_itemizer.amount")
    if _section_present(result, "schedule_e_rentals"):
        fields.add("federal.rental")
        if any(_section_present(result, entry.section) for entry in STATE_SECTIONS if entry.code == "CA"):
            fields.add("state.rental")
    rates = (result.get("meta") or {}).get("exchange_rates") or {}
    declared = profile.get("foreign_currencies") or []
    if not isinstance(declared, list) or not isinstance(rates, dict):
        raise ValueError("Invalid exchange-rate inputs")
    for code in [*declared, *rates]:
        if not isinstance(code, str) or not re.fullmatch(r"[A-Z]{3}", code):
            raise ValueError("Use currency codes in the exchange-rate inputs")
        if code != "USD":
            fields.add(f"exchange_rates.{code}")
    return fields


def _provenance_problem(path: str, law: dict, year: int, now: datetime, pin_paths: dict) -> str | None:
    pin = cache._pin_key(path, law)
    sources = law.get("_sources")
    provenance = law.get("_provenance")
    if not isinstance(sources, dict) or not cache._source_host_authoritative(sources.get(pin)):
        return f"{path}: pin an authoritative source at _sources.{pin}."
    if not isinstance(provenance, dict):
        return f"{path}: record covered-year live source provenance."

    value = cache._get_path(law, path)
    leaves = cache._figure_leaves(value, path)
    for leaf in leaves:
        candidates = []
        for key, entry in provenance.items():
            if not isinstance(key, str):
                continue
            parent = pin_paths.get(key, key)
            if leaf == parent or leaf.startswith(parent + "."):
                candidates.append((len(parent.split(".")), key, entry))
        if not candidates:
            return f"{leaf}: record covered-year live source provenance."
        _, key, entry = max(candidates, key=lambda item: (item[0], item[1] != pin))
        try:
            if not isinstance(entry, dict) or not cache._source_host_authoritative(entry.get("source")):
                raise ValueError
            period = entry.get("applies_to")
            if period is None or cache._check_applies_to(pin, leaf, period, year) != period:
                raise ValueError
            stamp = entry.get("retrieved")
            if not isinstance(stamp, str):
                raise ValueError
            retrieved = cache._naive(datetime.fromisoformat(stamp))
        except (TypeError, ValueError, cache.TaxLawSetError):
            return f"{leaf}: verify the authoritative source, covered year and retrieval date in _provenance.{key}."
        # A retrieval stamp says when `set` ran, not that anything was fetched,
        # so a window on it was cleared by re-running `set` on an unchanged
        # value. What a figure can prove is its pin, its covered year and its
        # checksum; freshness against new legislation is the cache-level
        # STALE review and the season-open audit.
        if retrieved > now:
            return f"{leaf}: the retrieval date in _provenance.{key} is in the future."
        digests = entry.get("figure_digests")
        if not isinstance(digests, dict) or digests.get(leaf) != cache._figure_digest(cache._get_path(law, leaf)):
            return f"{leaf}: record the exact verified figure with tax_law_fetcher.py set."
    return None


def filing_issues(result: dict, profile: dict, law: dict, *, now: datetime | None = None,
                  ledger: list | None = None) -> list[str]:
    """Refuse required omissions and unverified provenance, ignoring unused blocks."""
    from agents.lib.tie_out import filing_medicare_wages, has_multiple_ss_wage_forms

    meta = result.get("meta") or {}
    year = meta.get("tax_year") or profile.get("tax_year")
    profile_year = profile.get("tax_year", year)
    law_year = law.get("tax_year")
    if (type(year) is not int or not 2000 <= year <= 2099 or type(profile_year) is not int
            or profile_year != year or type(law_year) is not int or law_year != year):
        return ["The return, profile and tax-law cache need the same covered year."]
    try:
        raw_status = (meta.get("filing_status") or profile.get("filing_status"))
        key = _full_status_key(raw_status, "filing law")
        status = "qss" if isinstance(raw_status, str) and raw_status.strip().upper() == "QSS" else key
        fields = _federal_figures(result, profile, law, year, status)
        if _positive(filing_medicare_wages(ledger or [], None)):
            fields.update(_medicare_figures(status))
        state_fields, issues = _state_figures(result, profile, law, year, status)
    except (ValueError, TypeError, AttributeError, capabilities.CapabilityError):
        return ["Could not determine the applicable law from the return and profile."]
    if has_multiple_ss_wage_forms(ledger or []):
        fields.update({"federal.social_security.wage_base", "federal.social_security.employee_rate"})

    pin_paths = cache.source_pins(law)
    timestamp = cache._naive(now) if now is not None else datetime.now()
    seed = cache.create_empty_tax_law(year)
    for path in sorted(fields | state_fields):
        value = cache._get_path(law, path)
        if value in (None, [], {}):
            issues.append(f"{path}: required covered-year figure is missing.")
            continue
        if value == cache._get_path(seed, path):
            # A statutory constant the template ships complete (the SE factor,
            # NIIT and Additional Medicare rates and thresholds). Nothing
            # fetches it, so nothing can give it provenance, and requiring
            # one blocked every Schedule C return built the documented way.
            continue
        try:
            ref = cache._reference_for(path, law, value, year)
            cache._check_against(path, ref, value, top=True, strict=False, year=year)
        except cache.TaxLawSetError:
            issues.append(f"{path}: the applicable figure is invalid for this return.")
            continue
        provenance_issue = _provenance_problem(path, law, year, timestamp, pin_paths)
        if provenance_issue:
            issues.append(provenance_issue)
    return issues
