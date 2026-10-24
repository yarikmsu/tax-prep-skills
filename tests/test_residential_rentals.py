"""Hand-worked TY2025 ScheduleE/4562/8582 and CA3801 examples."""

import copy

import pytest

from agents.lib.tax_computations.federal import rentals as R


def law():
    # Published-form structure with explicit parameters, never engine defaults.
    return {"residential_recovery_years": 27.5, "macrs_first_year": 1987,
        "minimum_active_ownership": .1, "phaseout_rate": .5,
        "special_allowance": {"single": 25000, "mfj": 25000, "mfs": 12500, "hoh": 25000, "qss": 25000},
        "phaseout_ceiling": {"single": 150000, "mfj": 150000, "mfs": 75000, "hoh": 150000, "qss": 150000},
        "gds_percentages": []}


def pal_row(key="house", net=-40000, prior_active=0, prior_other=0, active=True):
    return {"property_id": key, "current_net": net,
            "prior_losses": {"active_origin": prior_active, "other_origin": prior_other},
            "active_eligible": active}


def pal(*, rows=None, magi=125000, status="Single", apart=None):
    return R.compute_rental_passive_losses(tax_year=2025, filing_status=status,
        lived_apart_all_year=apart, properties=rows or [pal_row()], magi=magi, law=law())


@pytest.mark.parametrize("magi,allowed", [(0, 25000), (100000, 25000), (125000, 12500), (150000, 0), (200000, 0)])
def test_published_8582_special_allowance_phaseout(magi, allowed):
    # 8582 lines5–9: min(25k, .5*(150k-MAGI)), bounded by the40k loss.
    out = pal(magi=magi)
    assert out["schedule_e_line26"] == -allowed
    assert out["unallowed_total"] == 40000 - allowed


def test_ineligible_prior_loss_is_not_promoted_by_current_participation():
    # 8582 PartIV instructions require participation in BOTH years.
    # Current active loss5k + eligible prior3k ->8k special;
    # prior nonactive10k remains in PartV, not the25k allowance.
    out = pal(rows=[pal_row(net=-5000, prior_active=3000, prior_other=10000)], magi=0)
    assert out["line_1d"] == -8000 and out["line_2d"] == -10000
    assert out["line_9"] == 8000
    assert out["properties"][0]["carryforward_by_origin"] == {"active_origin": 0, "other_origin": 10000}
    assert out["schedule_e_line26"] == -8000


def test_current_profit_absorbs_prior_losses_without_a_special_allowance():
    out = pal(rows=[pal_row(net=5000, prior_active=10000)], magi=200000)
    assert out["schedule_e_line24"] == 5000
    assert out["schedule_e_line25"] == 5000
    assert out["schedule_e_line26"] == 0
    assert out["properties"][0]["carryforward"] == 5000


def test_active_special_then_proportional_remaining_passive_allocation():
    # PartsVI–VII: active20k/nonactive20k losses, profit10k.
    # MAGI130k gives10k special to active loss. Remaining losses10k/20k;
    # unallowed20k ->6,666.67/13,333.33, deterministic cent remainder.
    out = pal(rows=[pal_row("active", -20000), pal_row("other", -20000, active=False),
                    pal_row("profit", 10000, active=False)], magi=130000)
    assert out["line_9"] == 10000
    assert [r["carryforward"] for r in out["properties"]] == [6666.67, 13333.33, 0]
    assert out["schedule_e_line26"] == -10000


@pytest.mark.parametrize("apart,magi,allowed", [(True, 50000, 12500), (True, 62500, 6250), (True, 75000, 0), (False, 0, 0)])
def test_separate_spouses_eligibility_and_columns(apart, magi, allowed):
    out = pal(status="MFS", apart=apart, magi=magi)
    assert out["schedule_e_line26"] == -allowed
    if not apart:
        assert out["line_1d"] == 0 and out["line_2d"] == -40000
        # Active management in the loss year is retained for future years,
        # despite this year's separate-spouse allowance ineligibility.
        assert out["properties"][0]["carryforward_by_origin"]["active_origin"] == 40000


def test_nonactive_current_year_preserves_old_active_origin():
    out = pal(rows=[pal_row(net=-10000, prior_active=10000, active=False)], magi=0)
    assert out["line_9"] == 0
    assert out["properties"][0]["carryforward_by_origin"] == {"active_origin": 10000, "other_origin": 10000}


def test_cent_tie_in_loss_origins_does_not_depend_on_json_key_order():
    #One cent of profit absorbs one of two equal prior cents. The remaining
    #cent follows canonical origin order, regardless of input dictionary order.
    row = pal_row(net=.01, prior_active=.01, prior_other=.01, active=False)
    first = pal(rows=[row], magi=200000)
    row['prior_losses'] = dict(reversed(list(row['prior_losses'].items())))
    second = pal(rows=[row], magi=200000)
    assert first == second
    assert first['properties'][0]['carryforward_by_origin'] == dict(active_origin=.01, other_origin=0)


def test_depreciation_first_full_and_final_midmonth_years():
    # Pub527's mid-month formula, synthetic2.5-year life,3,000 basis.
    # July placement: first5.5months=550; full12months=1,200;
    # after550+1,200+1,200, final January half-month=50.
    cfg = law()
    cfg["residential_recovery_years"] = 2.5
    for placed, prior, expected in (("2025-07-01", 0, 550), ("2024-07-31", 550, 1200), ("2022-07-02", 2950, 50)):
        out = R.compute_residential_depreciation(tax_year=2025, placed_in_service=placed,
            basis=3000, method="formula", prior_accumulated=prior, law=cfg)
        assert out["current_depreciation"] == expected
        assert out["closing_accumulated"] == prior + expected
    #Whole-dollar prior schedules may be50c above the cent schedule.
    #Only49.50 basis remains; final scheduled50 cannot exceed it.
    out = R.compute_residential_depreciation(tax_year=2025, placed_in_service='2022-07-02',
        basis=3000, method='formula', prior_accumulated=2950.5, law=cfg)
    assert out['current_depreciation'] == 49.5
    assert out['remaining_basis'] == 0


def test_published_table_method_does_not_silently_switch_to_formula():
    cfg = law()
    # Pub527 Table2-2d's February first-year3.182%; unused age columns
    # are synthetic zeros here. The input must carry the complete row.
    cfg["gds_percentages"] = [{"month": 2, "rates": [.03182] + [0] * 28}]
    args = dict(tax_year=2025, placed_in_service="2025-02-08", basis=160000,
                prior_accumulated=0, law=cfg)
    table = R.compute_residential_depreciation(**args, method="table")
    formula = R.compute_residential_depreciation(**args, method="formula")
    assert table["current_depreciation"] == 5091.2  #160,000*.03182
    assert formula["current_depreciation"] == 5090.91  #160,000/27.5*10.5/12
    cfg["gds_percentages"] = []
    with pytest.raises(ValueError, match="table"):
        R.compute_residential_depreciation(**args, method="table")


def test_conversion_lower_fmv_and_prior_depreciation_reconciliation():
    out = R.compute_residential_depreciation(tax_year=2025, placed_in_service="2025-01-01",
        basis=330000, conversion_fmv=165000, method="formula", prior_accumulated=0, law=law())
    assert out["depreciable_basis"] == 165000
    assert out["current_depreciation"] == 5750  #165k/27.5*11.5/12
    with pytest.raises(ValueError, match="Form3115"):
        R.compute_residential_depreciation(tax_year=2025, placed_in_service="2024-01-01",
            basis=330000, method="formula", prior_accumulated=0, law=law())


def rental_case():
    asset = {"asset_id": "building", "kind": "building", "placed_in_service": "2025-06-01",
        "federal_method": "formula", "ca_method": "formula", "federal_basis": 330000, "ca_basis": 660000,
        "converted_from_personal": False, "conversion_fmv": None}
    prop = {"property_id": "house", "address": {"street": "Example Road", "city": "Sample", "state": "CA", "zip": "90000"},
        "property_type": "single_family", "days_rented": 200, "personal_use_days": 0,
        "new_activity_this_year": True, "active_participation": True, "minimum_ownership_fraction": 1,
        "source_ids": ["rental-book"], "assets": [asset], "basis_and_history_confirmed": True}
    plan = {"method": "residential_rental_v1", "tax_year": 2025,
        "scope": dict.fromkeys(R.RENTAL_SCOPE_FACTS, True), "properties": [prop],
        "lived_apart_all_year": None, "magi_adjustments": {"reviewed": True,
            "included_agi_adjustments": 0, "excluded_agi_adjustments": 0, "excluded_income_addbacks": 0}}
    expenses = dict.fromkeys(R.RENTAL_EXPENSE_LINES, 0)
    expenses["14"] = 16000
    source = {"doc_id": "rental-book", "key_boxes": {R.RENTAL_RENTS: 10000, R.RENTAL_EXPENSES: 16000},
        "rental_source": {"tax_year": 2025, "kind": "book", "reviewed": True, "allocations_confirmed": True,
            "entries": [{"property_id": "house", "rents": 10000, "expenses": expenses}]}}
    income = dict.fromkeys(("line_1z_total_wages", "line_2b_taxable_interest", "line_3b_ordinary_dividends",
        "line_4b_taxable_ira", "line_5b_taxable_pensions", "line_7_capital_gain", "line_10_adjustments"), 0)
    income["line_1z_total_wages"] = 120000
    return dict(tax_year=2025, filing_status="Single", plan=plan, source_records=[source],
                opening=None, federal_law=law(), ca_law=law(), income_components=income, nonrental_schedule1_income=0)


def test_ca_final_allowed_difference_uses_federal_magi():
    # First June year: federal depreciation6,500, CA13,000.
    #10k rent-16k expenses =>loss12,500 federal/19kCA. MAGI120k;
    #special allowance15k BOTH jurisdictions. Allowed-12,500/-15,000,
    #so CA subtraction2,500 (not raw depreciation difference6,500).
    out = R.compute_residential_rental_return(**rental_case())
    assert out["federal_magi"] == 120000
    assert out["federal"]["schedule_e_line26"] == -12500
    assert out["california"]["schedule_e_line26"] == -15000
    assert out["ca_subtraction"] == 2500
    assert out["carryforward"]["properties"][0]["ca_passive_losses"] == {"active_origin": 4000, "other_origin": 0}
    assert out["properties"][0]["form_4562"]["line_19i"] == 6500
    assert out["properties"][0]["ftb_3885a"]["line_8a"] == 6500


def test_high_magi_suspends_both_depreciation_losses_without_an_agi_adjustment():
    args = rental_case()
    args["income_components"]["line_1z_total_wages"] = 200000
    out = R.compute_residential_rental_return(**args)
    assert out["federal"]["schedule_e_line26"] == out["california"]["schedule_e_line26"] == 0
    assert out["ca_subtraction"] == out["ca_addition"] == 0
    assert out["carryforward"]["properties"][0]["federal_passive_losses"]["active_origin"] == 12500
    assert out["carryforward"]["properties"][0]["ca_passive_losses"]["active_origin"] == 19000


def test_magi_uses_income_components_excludes_ss_and_named_adjustments():
    args = rental_case()
    args["income_components"].update(line_1z_total_wages=140000, line_6b_taxable_ss=15000,
                                    line_11_agi=999999, line_10_adjustments=10000)
    args["plan"]["magi_adjustments"].update(included_agi_adjustments=6000,
        excluded_agi_adjustments=4000, excluded_income_addbacks=1000)
    out = R.compute_residential_rental_return(**args)
    assert out["federal_magi"] == 135000  #140k-6k+1k; SS and stale AGI ignored
    assert out["federal"]["schedule_e_line26"] == -7500


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), "private text", -1, None])
def test_invalid_source_amounts_never_default_or_echo(bad):
    args = rental_case()
    args["source_records"][0]["rental_source"]["entries"][0]["rents"] = bad
    with pytest.raises(ValueError) as exc:
        R.compute_residential_rental_return(**args)
    assert "private text" not in str(exc.value)


@pytest.mark.parametrize("fact", R.RENTAL_SCOPE_FACTS)
def test_every_scope_question_is_required_and_cannot_be_waived(fact):
    args = rental_case()
    args["plan"]["scope"][fact] = False
    with pytest.raises(ValueError, match="scope"):
        R.compute_residential_rental_return(**args)
    del args["plan"]["scope"][fact]
    with pytest.raises(ValueError, match="scope"):
        R.compute_residential_rental_return(**args)


def test_source_drops_unknown_properties_and_missing_adjustment_components_refuse():
    for field in ("missing-book", "unknown-property", "magi"):
        args = rental_case()
        if field == "missing-book":
            args["plan"]["properties"][0]["source_ids"] = ["missing"]
        elif field == "unknown-property":
            args["source_records"][0]["rental_source"]["entries"][0]["property_id"] = "unknown"
        else:
            args["plan"]["magi_adjustments"]["included_agi_adjustments"] = 1
        with pytest.raises(ValueError):
            R.compute_residential_rental_return(**args)


def test_missing_rate_or_method_does_not_produce_a_plausible_depreciation():
    for key in ("residential_recovery_years", "macrs_first_year", "phaseout_rate", "minimum_active_ownership"):
        cfg = law()
        cfg[key] = None
        with pytest.raises(ValueError):
            R.compute_residential_depreciation(tax_year=2025, placed_in_service="2025-01-01",
                basis=330000, method="formula", prior_accumulated=0, law=cfg)


def test_related_worksheet_review_is_bound_to_actual_inputs_without_a_digest_loop():
    args = rental_case()
    original = copy.deepcopy(args)
    first = R.compute_residential_rental_return(**args)
    assert args == original
    assert not first["related_forms_review_current"]
    args["plan"]["related_forms_review"] = {"input_digest": first["related_forms_input_digest"],
        "qbi_reviewed": True, "niit_reviewed": True, "amt_reviewed": True, "income_dependent_forms_reviewed": True}
    checked = R.compute_residential_rental_return(**args)
    assert checked["related_forms_review_current"]
    assert checked["related_forms_input_digest"] == first["related_forms_input_digest"]
    args["income_components"]["line_11_agi"] = 1  # derived output is not a new input
    assert R.compute_residential_rental_return(**args)["related_forms_review_current"]
    args["income_components"]["line_1z_total_wages"] += 1
    assert not R.compute_residential_rental_return(**args)["related_forms_review_current"]


def test_federal_and_ca_established_depreciation_methods_remain_independent():
    args = rental_case()
    args["plan"]["properties"][0]["assets"][0]["ca_method"] = "table"
    args["ca_law"]["gds_percentages"] = [{"month": 6, "rates": [.02] + [0] * 28}]
    out = R.compute_residential_rental_return(**args)
    dep = out["properties"][0]["assets"][0]
    assert dep["federal"]["current_depreciation"] == 6500
    assert dep["california"]["current_depreciation"] == 13200  # synthetic CA table660k*.02


def existing_case():
    args = rental_case()
    prop = args['plan']['properties'][0]
    prop['new_activity_this_year'] = False
    asset = prop['assets'][0]
    asset['placed_in_service'] = '2024-01-01'
    #2024 first January:330k/27.5*11.5/12=11,500; CA double.
    args['opening'] = dict(tax_year=2024, properties=[dict(property_id='house',
        federal_passive_losses=dict(active_origin=1000, other_origin=2000),
        ca_passive_losses=dict(active_origin=3000, other_origin=4000),
        assets=[dict(asset_id='building', federal_accumulated=11500, ca_accumulated=23000)])],
        asset_inventory=[dict(property_id='house', assets=[copy.deepcopy(asset)])])
    return args


def test_federal_only_path_cannot_erase_known_opening_ca_history():
    args = existing_case()
    args['ca_law'] = None
    with pytest.raises(ValueError, match='known CA rental history'):
        R.compute_residential_rental_return(**args)
    #An activity that never had CA balances still has an explicit federal-only
    #history; its CA balance is unknown, never inferred from federal amounts.
    prior = args['opening']['properties'][0]
    prior['ca_passive_losses'] = None
    prior['assets'][0]['ca_accumulated'] = None
    out = R.compute_residential_rental_return(**args)
    assert out['carryforward']['properties'][0]['ca_passive_losses'] is None
    assert out['carryforward']['properties'][0]['assets'][0]['ca_accumulated'] is None


def test_existing_history_retains_distinct_basis_accumulation_and_loss_origin():
    args = existing_case()
    args['income_components']['line_1z_total_wages'] = 200000
    out = R.compute_residential_rental_return(**args)
    closing = out['carryforward']['properties'][0]
    #Current federal loss18k +prior1k active/2kother; CA30k+3k/4k.
    assert closing['federal_passive_losses'] == dict(active_origin=19000, other_origin=2000)
    assert closing['ca_passive_losses'] == dict(active_origin=33000, other_origin=4000)
    assert closing['assets'][0] == dict(asset_id='building', federal_accumulated=23500, ca_accumulated=47000)
    assert R.validate_rental_carryforward(out['carryforward'], 2025) == out['carryforward']


@pytest.mark.parametrize('omission', ['inventory', 'asset', 'balance', 'duplicate', 'future', 'nonfinite', 'basis'])
def test_opening_inventory_corruption_is_never_a_zero_balance(omission):
    args = existing_case()
    opening = args['opening']
    if omission == 'inventory':
        opening['asset_inventory'] = []
    elif omission == 'asset':
        opening['asset_inventory'][0]['assets'] = []
    elif omission == 'balance':
        opening['properties'][0]['assets'] = []
    elif omission == 'duplicate':
        opening['asset_inventory'] *= 2
    elif omission == 'future':
        opening['asset_inventory'][0]['assets'][0]['placed_in_service'] = '2026-01-01'
    elif omission == 'nonfinite':
        opening['asset_inventory'][0]['assets'][0]['ca_basis'] = float('nan')
    else:
        args['plan']['properties'][0]['assets'][0]['federal_basis'] += 1
    with pytest.raises(ValueError):
        R.compute_residential_rental_return(**args)


@pytest.mark.parametrize('status', ['Single', 'MFJ', 'MFS', 'HOH', 'QSS'])
def test_zero_rent_explicit_book_remains_an_activity_with_depreciation(status):
    args = rental_case()
    args['filing_status'] = status
    args['plan']['lived_apart_all_year'] = True if status == 'MFS' else None
    args['income_components']['line_1z_total_wages'] = 200000
    source = args['source_records'][0]
    source['key_boxes'][R.RENTAL_RENTS] = 0
    source['rental_source']['entries'][0]['rents'] = 0
    out = R.compute_residential_rental_return(**args)
    assert out['gross_rents'] == 0
    assert out['federal']['unallowed_total'] == 22500  #16k operating+6.5kdep
    assert out['carryforward']['properties'][0]['assets'][0]['federal_accumulated'] == 6500


@pytest.mark.parametrize('bad', [None, True, float('nan'), float('inf'), -1])
def test_nonfinite_or_missing_rental_law_is_refused(bad):
    cfg = law()
    cfg['special_allowance']['single'] = bad
    with pytest.raises(ValueError):
        R.validate_rental_law(cfg)


def test_optional_law_cache_has_annual_sources_and_formula_tables_do_not_nag(tmp_path):
    import json
    from agents.lib.secure_fs import write_secure
    from agents.lib.tax_law_fetcher import create_empty_tax_law, create_state_template, check_freshness, validate_cache, verify_source_pinning
    cache = create_empty_tax_law(2025)
    cache['state'] = create_state_template('CA', 2025)
    path = tmp_path / 'tax_law_2025.json'
    write_secure(path, json.dumps(cache))
    assert not any('.rental' in p for p in check_freshness(2025, str(tmp_path))['null_fields'])
    cache['federal']['rental'] = law()
    cache['state']['rental'] = law()
    assert not any('rental' in p for p in validate_cache(cache, 2025))
    pins = verify_source_pinning(cache)
    assert 'federal.rental' in pins['unpinned'] and 'state.CA' in pins['unpinned']
    cache['_sources'] = {'federal.rental': 'https://www.irs.gov/instructions/i8582',
        'state.CA': 'https://www.ftb.ca.gov/forms/2025/2025-3801-instructions.html'}
    assert 'federal.rental' not in verify_source_pinning(cache)['unpinned']
    write_secure(path, json.dumps(cache))
    assert not any('.rental' in p for p in check_freshness(2025, str(tmp_path))['null_fields'])
    cache['federal']['rental']['minimum_active_ownership'] = None
    assert any('minimum active ownership' in p for p in validate_cache(cache, 2025))
