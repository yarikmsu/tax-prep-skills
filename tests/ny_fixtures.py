#!/usr/bin/env python3
"""New York's published TY2025 figures, in ONE place for the whole suite.

They started beside `compute_ny_tax`'s own tests and stayed there while the
module was the only consumer. `/tax-project` and `/tax-harvest` read the same
cache now, and a second transcription of a nine-band rate schedule is a second
thing to get wrong — the figures are checked against IT-201-I by eye, not by
anything mechanical, so the only defence is having one copy.

TY2025 because that is the instruction set that exists to check against. The
engine holds none of this: every figure is an argument, fetched for whatever
year the return is for. What is pinned here is what the PUBLISHED tables say,
so a test asserting a dollar figure names a page rather than a constant.
"""

# IT-201-I (2025) p.33, "Married filing jointly and qualifying surviving
# spouse". Written as bands so the test reads against the published table.
_NY_2025_SCHEDULE_A = [
    {"rate": 0.04, "min": 0, "max": 17150, "base": 0},
    {"rate": 0.045, "min": 17150, "max": 23600, "base": 686},
    {"rate": 0.0525, "min": 23600, "max": 27900, "base": 976},
    {"rate": 0.055, "min": 27900, "max": 161550, "base": 1202},
    {"rate": 0.06, "min": 161550, "max": 323200, "base": 8553},
    {"rate": 0.0685, "min": 323200, "max": 2155350, "base": 18252},
    {"rate": 0.0965, "min": 2155350, "max": 5000000, "base": 143754},
    {"rate": 0.103, "min": 5000000, "max": 25000000, "base": 418263},
    {"rate": 0.109, "min": 25000000, "max": None, "base": 2478263},]
# IT-201-I (2025) p.33, "Single and married filing separately".
_NY_2025_SCHEDULE_C = [
    {"rate": 0.04, "min": 0, "max": 8500, "base": 0},
    {"rate": 0.045, "min": 8500, "max": 11700, "base": 340},
    {"rate": 0.0525, "min": 11700, "max": 13900, "base": 484},
    {"rate": 0.055, "min": 13900, "max": 80650, "base": 600},
    {"rate": 0.06, "min": 80650, "max": 215400, "base": 4271},
    {"rate": 0.0685, "min": 215400, "max": 1077550, "base": 12356},
    {"rate": 0.0965, "min": 1077550, "max": 5000000, "base": 71413},
    {"rate": 0.103, "min": 5000000, "max": 25000000, "base": 449929},
    {"rate": 0.109, "min": 25000000, "max": None, "base": 2509929},]
# IT-201-I (2025) p.33, "Head of household".
_NY_2025_SCHEDULE_B = [
    {"rate": 0.04, "min": 0, "max": 12800, "base": 0},
    {"rate": 0.045, "min": 12800, "max": 17650, "base": 512},
    {"rate": 0.0525, "min": 17650, "max": 20900, "base": 730},
    {"rate": 0.055, "min": 20900, "max": 107650, "base": 901},
    {"rate": 0.06, "min": 107650, "max": 269300, "base": 5672},
    {"rate": 0.0685, "min": 269300, "max": 1616450, "base": 15371},
    {"rate": 0.0965, "min": 1616450, "max": 5000000, "base": 107651},
    {"rate": 0.103, "min": 5000000, "max": 25000000, "base": 434163},
    {"rate": 0.109, "min": 25000000, "max": None, "base": 2494163},]
_NY_SCHEDULE_BY_STATUS = {"single": "c", "mfs": "c", "mfj": "a", "qss": "a",
                          "hoh": "b"}
# IT-201-I (2025) pp.34-38, tax computation worksheets 1-16.
_NY_2025_RECAPTURE = {
    "entry_threshold": 107650,
    "phase_in_band": 50000,
    "entry_tier": {
        # WS1: "Multiply line 2 by 5.5%", taxable income $161,550 or less.
        "a": {"flat_rate": 0.055, "taxable_income_not_over": 161550},
        # WS12: 6%, $269,300 or less.
        "b": {"flat_rate": 0.06, "taxable_income_not_over": 269300},
        # WS7: 6%, $215,400 or less.
        "c": {"flat_rate": 0.06, "taxable_income_not_over": 215400},
    },
    "tiers": {
        "a": [  # WS2-5
            {"taxable_income_over": 161550, "taxable_income_not_over": 323200,
             "recapture_base": 333, "incremental_benefit": 807,
             "nyagi_excess_over": 161550},
            {"taxable_income_over": 323200, "taxable_income_not_over": 2155350,
             "recapture_base": 1140, "incremental_benefit": 2747,
             "nyagi_excess_over": 323200},
            {"taxable_income_over": 2155350,
             "taxable_income_not_over": 5000000,
             "recapture_base": 3887, "incremental_benefit": 60350,
             "nyagi_excess_over": 2155350},
            {"taxable_income_over": 5000000, "taxable_income_not_over": None,
             "recapture_base": 64237, "incremental_benefit": 32500,
             "nyagi_excess_over": 5000000},
        ],
        "b": [  # WS13-15
            {"taxable_income_over": 269300, "taxable_income_not_over": 1616450,
             "recapture_base": 787, "incremental_benefit": 2289,
             "nyagi_excess_over": 269300},
            {"taxable_income_over": 1616450,
             "taxable_income_not_over": 5000000,
             "recapture_base": 3076, "incremental_benefit": 45261,
             "nyagi_excess_over": 1616450},
            {"taxable_income_over": 5000000, "taxable_income_not_over": None,
             "recapture_base": 48337, "incremental_benefit": 32500,
             "nyagi_excess_over": 5000000},
        ],
        "c": [  # WS8-10
            {"taxable_income_over": 215400, "taxable_income_not_over": 1077550,
             "recapture_base": 568, "incremental_benefit": 1831,
             "nyagi_excess_over": 215400},
            {"taxable_income_over": 1077550,
             "taxable_income_not_over": 5000000,
             "recapture_base": 2399, "incremental_benefit": 30172,
             "nyagi_excess_over": 1077550},
            {"taxable_income_over": 5000000, "taxable_income_not_over": None,
             "recapture_base": 32571, "incremental_benefit": 32500,
             "nyagi_excess_over": 5000000},
        ],
    },
    # WS6/WS11/WS16: above this NYAGI the whole taxable income is taxed at one
    # rate, with no schedule at all.
    "full_recapture": {"nyagi_over": 25000000, "rate": 0.109},
}
_NY_BRACKETS = {"a": _NY_2025_SCHEDULE_A, "c": _NY_2025_SCHEDULE_C,
                "b": _NY_2025_SCHEDULE_B}
# IT-201-I (2025) p.32: the TABLE is used when NYAGI is at or below the entry
# threshold AND taxable income is under $65,000; the rate schedule takes over
# above it. The rows are $50 wide over most of the range and NARROWER at the
# very bottom — $0-13, $13-25, $25-50 — which is why the widths are fetched as
# segments rather than assumed to be one figure.
_NY_2025_TAX_TABLE = {
    "max_taxable_income": 65000,
    "row_segments": [
        {"over": 0, "not_over": 13, "row_width": 13},
        {"over": 13, "not_over": 25, "row_width": 12},
        {"over": 25, "not_over": 50, "row_width": 25},
        {"over": 50, "not_over": 65000, "row_width": 50},
    ],
    # The one band where the published table is not the published schedule:
    # its married-joint $23,600-27,900 rows are built on 976.25, the bands
    # beneath summed, where the schedule prints 976 (AA14). Found by comparing
    # every row of the TY2024 and TY2025 tables with the schedule; the other
    # columns, and the City table, follow their published bases throughout.
    "table_bases": [{"schedule": "a", "min": 23600}],
}


# Tax Law §685(c). The SHAPE is statutory; the shares and thresholds are
# fetched, deliberately not shared with the federal constants — they are New
# York's own and may diverge.
_NY_2025_SAFE_HARBOR = {
    "current_year_share": 0.90,
    "prior_year_share": 1.00,
    "prior_year_share_high_income": 1.10,
    "high_income_nyagi_threshold": 150000,
    "high_income_nyagi_threshold_mfs": 75000,
}


def ny_state_cache(**over):
    """A `tax_law["state"]` block shaped the way a fetched New York one is.

    The rate schedules, the recapture and the table are the PUBLISHED TY2025
    figures above. The standard deduction is NOT published — it is a round
    test figure, chosen so the arithmetic in a projection test is readable and
    labelled here so nobody copies it onto a return. Every consumer fetches
    the real table; none of them holds one.

    What this exists for is the SHAPE: `brackets` keyed by §601 rate schedule
    rather than a flat list, which is the difference that made a New York
    cache crash every generic state-bracket consumer in the repository.
    """
    cache = {
        "code": "NY",
        "type": "graduated_income",
        "standard_deduction": {"mfj": 10000, "single": 5000, "mfs": 5000,
                               "hoh": 7500, "qss": 10000},
        "brackets": _NY_BRACKETS,
        "bracket_schedule_by_status": _NY_SCHEDULE_BY_STATUS,
        "tax_table_benefit_recapture": _NY_2025_RECAPTURE,
        "tax_table": _NY_2025_TAX_TABLE,
        "estimated_tax_safe_harbor": _NY_2025_SAFE_HARBOR,
    }
    cache.update(over)
    return cache

# IT-203/IT-360.1 share the published TY2025 local schedules and household bands.
_NY_2025_HOUSEHOLD_TABLE = {
    "single": [
        {"agi_over": None, "agi_not_over": 5000, "amount": 75},
        {"agi_over": 5000, "agi_not_over": 6000, "amount": 60},
        {"agi_over": 6000, "agi_not_over": 7000, "amount": 50},
    ],
    "mfj_hoh_qss": [
        {"agi_over": None, "agi_not_over": 5000,
         "by_exemptions": [90, 105, 120, 135, 150, 165, 180],
         "each_additional": 15},
        {"agi_over": 5000, "agi_not_over": 6000,
         "by_exemptions": [75, 90, 105, 120, 135, 150, 165],
         "each_additional": 15},
    ],
    "mfs": [
        {"agi_over": None, "agi_not_over": 5000,
         "by_exemptions": [45, 53, 60, 68, 75, 83, 90],
         "each_additional": 8},
    ],
}

_NY_2025_MCTMT = {
    "zone_1": {"rate": 0.0060,
               "counties": ["New York", "Bronx", "Kings", "Queens",
                            "Richmond"]},
    "zone_2": {"rate": 0.0034,
               "counties": ["Rockland", "Nassau", "Suffolk", "Orange",
                            "Putnam", "Dutchess", "Westchester"]},
    "net_earnings_threshold_per_zone": 50000,
}

_NYC_2025_SCHEDULES = {
    "a": [{"min": 0, "max": 21600, "rate": 0.03078, "base": 0},
          {"min": 21600, "max": 45000, "rate": 0.03762, "base": 665},
          {"min": 45000, "max": 90000, "rate": 0.03819, "base": 1545},
          {"min": 90000, "max": None, "rate": 0.03876, "base": 3264}],
    "b": [{"min": 0, "max": 14400, "rate": 0.03078, "base": 0},
          {"min": 14400, "max": 30000, "rate": 0.03762, "base": 443},
          {"min": 30000, "max": 60000, "rate": 0.03819, "base": 1030},
          {"min": 60000, "max": None, "rate": 0.03876, "base": 2176}],
    "c": [{"min": 0, "max": 12000, "rate": 0.03078, "base": 0},
          {"min": 12000, "max": 25000, "rate": 0.03762, "base": 369},
          {"min": 25000, "max": 50000, "rate": 0.03819, "base": 858},
          {"min": 50000, "max": None, "rate": 0.03876, "base": 1813}],
}


def ny_it203_cache():
    """Published TY2025 Single fixtures, IT-203-I pp.20, 29–40.

    Other household-table groups retain the partial low bands above; tests of
    this fixture price Single returns. Annual amounts never live in the engine.
    """
    import copy
    table = copy.deepcopy(_NY_2025_HOUSEHOLD_TABLE)
    table["single"] += [
        {"agi_over": 7000, "agi_not_over": 20000, "amount": 45},
        {"agi_over": 20000, "agi_not_over": 25000, "amount": 40},
        {"agi_over": 25000, "agi_not_over": 28000, "amount": 20},
    ]
    return copy.deepcopy(ny_state_cache(
        standard_deduction={"single":8000,"single_dependent":3100,"mfj":16050,
                            "mfs":8000,"hoh":11200,"qss":16050},
        dependent_exemption={"amount":1000},
        credits={"household":{"table":table}},
        nyc_tax={"brackets":_NYC_2025_SCHEDULES},mctmt=_NY_2025_MCTMT,
        subtractions={"pension_annuity_exclusion":{"amount":20000}},
    ))
