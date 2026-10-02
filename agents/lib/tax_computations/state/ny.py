#!/usr/bin/env python3
"""New York State, New York City and Yonkers — Form IT-201, full-year resident.

W2's `state/` package, first member. Extracted as ONE contiguous block of 1,571
lines depending on exactly one name from the rest of the engine, which is what
made it the move to prove the mechanism on: every sweep, roster and digest that
enumerates `agents/lib/` had to learn to descend in the same change, and a
smaller first move is a cheaper place to find out that one of them did not.

Every figure is an argument. The design and its sources are in
`docs/superpowers/specs/2026-09-10-ny-state-module-design.md`; the cache keys a
reader needs are in `docs/contributing/tax-law-cache.md`.
"""

import math

from agents.lib.tax_computations._shared import (
    _amount,
    _normalize_filing_status,
    _split_remainder_last,
)


def _ny_schedule_tax(taxable_income: float, table: list,
                     schedule: str) -> "tuple[float, list]":
    """New York's rate schedule the way New York publishes it: a cumulative
    BASE plus a rate on the excess over the band's floor.

    Not `compute_bracket_tax`, and the difference is not stylistic. New York
    ROUNDS each published base to the nearest dollar, so summing the bands
    disagrees with the form at most of them — on the TY2025 married-joint
    schedule the $323,200 base sums to 18,251.75 against a published 18,252,
    and the $2,155,350 base sums two and a half cents high. Two of that
    schedule's nine bands do agree exactly, which is why the cross-check below
    tolerates a dollar rather than demanding a difference. `/tax-verify` ties
    line by line
    against software that uses the published schedule, and a systematic
    sub-dollar drift at every band is exactly the kind of difference that reads
    as a defect in whichever side is checked second.

    So `base` is REQUIRED on every band rather than optional. A fallback to
    summation would be a second answer that differs quietly, which is the shape
    this repository keeps paying for.
    """
    if taxable_income < 0:
        raise ValueError(f"taxable_income cannot be negative, got {taxable_income}")
    _ny_check_schedule(table, schedule)
    for band in table:
        upper = band.get("max")
        if taxable_income > band["min"] and (upper is None
                                             or taxable_income <= upper):
            excess = taxable_income - band["min"]
            # UNROUNDED, because every caller rounds to the dollar and the
            # form rounds once. Rounding to cents here as well carried the
            # City schedule's 2,646.49504 to 2,646.50 and then to 2,647, and
            # its table row $3,500-3,550 to 109 against a published 108. The
            # City's rates run to five decimals, so its cents land on .495 to
            # .499 often, and the error is always upward. The recapture
            # worksheets carry the schedule tax in cents, and round it there.
            tax = band["base"] + excess * band["rate"]
            return tax, [{
                "rate": band["rate"],
                "range": f"{band['min']:,.0f} - "
                         f"{upper:,.0f}" if upper is not None
                         else f"{band['min']:,.0f} and over",
                "base": band["base"],
                "excess": round(excess, 2),
                "tax": round(tax, 2),
            }]
    # Only reachable at exactly zero, or from a table whose first band does not
    # start at zero — a transcription gap rather than a taxpayer.
    if taxable_income == 0:
        return 0.0, []
    raise ValueError(
        f"taxable income {taxable_income:,.2f} falls in no band of New York "
        f"schedule {schedule!r}; the first band must start at 0 and the last "
        "must have a null max. Re-run /tax-setup.")


def _ny_check_schedule(table: list, schedule: str) -> None:
    """Every rule a New York rate schedule must meet before a band is read.

    Shared with `tax_law_fetcher set` and its freshness audit, as
    `_ny_table_bases_entries` is, so the cache refuses exactly what the return
    would: a schedule the write path accepts and the engine then refuses is a
    cache that reads fetched and cannot be filed from. The WHOLE schedule is
    checked before any band is read, so a bad band refuses every return on the
    schedule, not only those that land in it. Raises ValueError.
    """
    summed = 0.0
    for i, band in enumerate(table):
        if not isinstance(band, dict):
            raise ValueError(
                f"New York bracket {i} of schedule {schedule!r} is not an object")
        for key in ("rate", "min", "base"):
            if band.get(key) is None:
                raise ValueError(
                    f"New York bracket {i} of schedule {schedule!r} has no "
                    f"{key!r}. Each band carries the PUBLISHED cumulative base; "
                    "summing the bands instead disagrees with the form, because "
                    "New York rounds those bases to the dollar.")
            # Finite, non-negative, not a bool: a NaN base passes the dollar
            # comparison below, every comparison with NaN being False (§8).
            _amount(band[key], f"New York bracket {i} of schedule "
                               f"{schedule!r} {key!r}")
        upper = band.get("max")
        if upper is not None:
            _amount(upper, f"New York bracket {i} of schedule {schedule!r} 'max'")
        # A dropped `max` makes its band TERMINAL, and the bands above it
        # unreachable: a $400,000 return would then be taxed at the 5.5% band's
        # rate all the way up. That is a five-figure under-statement from one
        # missing key, so only the LAST band may be open-ended.
        if upper is None and i != len(table) - 1:
            raise ValueError(
                f"New York bracket {i} of schedule {schedule!r} has no 'max' "
                "and is not the last band; every band above it would be "
                "unreachable")
        # And the last band IS open: a closed top leaves every income above
        # it in no band at all.
        if upper is not None and i == len(table) - 1:
            raise ValueError(
                f"New York schedule {schedule!r} closes its last band at "
                f"{upper:,}; the top band's 'max' must be null")
        # The bands must meet exactly. A gap swallows the incomes inside it and
        # an overlap makes the first match win, and both are transcription
        # errors that no per-band check can see.
        if band["min"] != (0 if i == 0 else table[i - 1].get("max")):
            raise ValueError(
                f"New York schedule {schedule!r} band {i} starts at "
                f"{band['min']}, which does not meet the previous band's top; "
                "the schedule has a gap or an overlap")
        # The PUBLISHED base against the bands beneath it. A base that is
        # MISSING refuses above; a base that is WRONG — a transposed 18,522 for
        # 18,252 — is the likelier transcription error and would shift every
        # affected return by hundreds. The whole argument for requiring the
        # published figure is that it differs from the sum by CENTS, so a
        # difference of more than a dollar refutes the figure, not the rule.
        if abs(band["base"] - summed) > 1.0:
            raise ValueError(
                f"New York schedule {schedule!r} band {i} publishes a base of "
                f"{band['base']:,} where the bands beneath it sum to "
                f"{summed:,.2f}. New York rounds its bases to the dollar, so a "
                "gap this size is a mis-transcribed figure, not rounding.")
        if upper is not None:
            summed += (upper - band["min"]) * band["rate"]


_NY_RECAPTURE_TIER_KEYS = ("taxable_income_over", "recapture_base",
                           "incremental_benefit", "nyagi_excess_over")


def _ny_check_recapture_tiers(tiers: list, schedule: str) -> None:
    """Every rule one schedule's recapture tiers must meet before a tier is
    read, shared with `tax_law_fetcher` as `_ny_check_schedule` is: the four
    figures on every tier, finite and non-negative, tiers that meet, and only
    the top one open. Raises ValueError."""
    where = f"state.tax_table_benefit_recapture.tiers[{schedule!r}]"
    for i, tier in enumerate(tiers):
        if not isinstance(tier, dict):
            raise ValueError(f"{where}[{i}] is not an object")
        for key in _NY_RECAPTURE_TIER_KEYS:
            if tier.get(key) is None:
                raise ValueError(
                    f"{where}[{i}] has no {key!r}. Each tier carries its own "
                    "base and benefit, and they move year to year — there is "
                    "no structural default to use instead.")
            _amount(tier[key], f"{where}[{i}] {key!r}")
        upper = tier.get("taxable_income_not_over")
        last = i == len(tiers) - 1
        if upper is None and not last:
            raise ValueError(
                f"{where}[{i}] has no 'taxable_income_not_over' and is not the "
                "last tier; every tier above it would be unreachable")
        if upper is not None:
            _amount(upper, f"{where}[{i}] 'taxable_income_not_over'")
            if last:
                raise ValueError(
                    f"{where} closes its last tier at {upper:,}; the top "
                    "tier's 'taxable_income_not_over' must be null")
            if upper <= tier["taxable_income_over"]:
                raise ValueError(f"{where}[{i}] ends at or below where it starts")
        if i and tier["taxable_income_over"] != tiers[i - 1].get(
                "taxable_income_not_over"):
            raise ValueError(
                f"{where}[{i}] starts at {tier['taxable_income_over']:,}, which "
                "does not meet the previous tier's top; the tiers have a gap "
                "or an overlap")


def _ny_status(filing_status) -> str:
    """Validate the filing status BY VALUE, and hand back the token New York's
    own tables and the cache blocks are keyed by.

    AGENTS.md §8 requires the value checked at the parse boundary, not just the
    type: a type-valid but out-of-domain string otherwise falls through to a
    wrong-but-plausible default. This module compared lowercase literals and
    never validated at all, so the canonical `"MFJ"` the federal normaliser
    returns took the SINGLE branch at four sites rather than raising — the exact
    failure value-validation exists to stop (Q9).

    Lowercase, because that is how `bracket_schedule_by_status`, the 529 caps
    and the household-credit table are keyed. Callers may now pass either
    spelling; anything outside the canonical set raises.
    """
    return _normalize_filing_status(filing_status).lower()


# New York enters BOTH modification schedules as positive amounts, so a
# negative on one of them is a figure on the wrong schedule. Passed to
# `_amount` where that is what a negative means, and nowhere else.
_NY_WRONG_SCHEDULE = (
    ". New York's additions and subtractions are both entered as POSITIVE "
    "amounts — the SCHEDULE carries the direction, so a negative here means "
    "the figure is on the wrong one.")


_NY_MODIFICATION_PREFIXES = {"addition": ("A-", "EA-"),
                             "subtraction": ("S-", "ES-")}


def compute_ny_modifications(entries: "list[dict] | None", direction: str,
                             invalid_codes: "list[str] | None" = None) -> dict:
    """Total one side of Form IT-225, checking each code against its schedule.

    Form IT-225 puts ADDITIONS on Schedule A and SUBTRACTIONS on Schedule B,
    and the codes carry that: `A-`/`EA-` against `S-`/`ES-`, where the `E`
    forms are modifications flowed through from a partnership, S corporation or
    estate. The caller states which side it is handing over; the prefix is then
    a CROSS-CHECK on that claim rather than an inference from it.

    That check is the point. A subtraction filed as an addition over-states the
    tax; an addition filed as a subtraction under-states it. Netting the two
    sides — or reading a direction off a signed amount — makes both mistakes
    invisible, which is why a negative amount refuses instead of flipping sides.

    `invalid_codes` is the list of codes the form being filed may not carry
    (an IT-201 resident return excludes several). When it is fetched, a code on
    it REFUSES. When it is not, the total is still computed and a LOUD note
    says the validity was not checked — absence of a cross-check may not
    silently become a pass, but neither may it block a correct return.
    """
    if direction not in _NY_MODIFICATION_PREFIXES:
        raise ValueError(
            f"direction must be 'addition' or 'subtraction', got {direction!r}")
    prefixes = _NY_MODIFICATION_PREFIXES[direction]
    notes: "list[str]" = []
    if not invalid_codes:
        notes.append(
            f"IT-225 {direction} codes were NOT checked against the list this "
            "form may not carry — state.it225.codes_not_valid_on_it201 is "
            "unfetched. The amounts are totalled; their eligibility is not "
            "established.")
    total = 0.0
    seen: "set[str]" = set()
    detail = []
    for i, entry in enumerate(entries or []):
        if not isinstance(entry, dict):
            raise ValueError(f"IT-225 {direction} {i} must be an object")
        code = entry.get("code")
        if not isinstance(code, str) or not code.strip():
            raise ValueError(f"IT-225 {direction} {i} has no code")
        code = code.strip().upper()
        if not code.startswith(prefixes):
            raise ValueError(
                f"IT-225 code {code!r} was handed over as a {direction} but "
                f"its prefix is not one of {prefixes}. Schedule A carries "
                "additions and Schedule B subtractions; a modification on the "
                "wrong one moves the tax in the wrong direction.")
        if code in seen:
            raise ValueError(
                f"IT-225 code {code!r} appears twice in the {direction}s. The "
                "same NUMBER may legitimately appear as both an individual "
                "(Part 1) and a pass-through (Part 2) modification, but those "
                "carry different prefixes — a repeated code is a double count.")
        seen.add(code)
        if invalid_codes and code in {c.strip().upper() for c in invalid_codes}:
            raise ValueError(
                f"IT-225 code {code!r} is not valid on the form being filed. "
                "New York disallows the modification rather than adjusting it, "
                "so this is a return that would be corrected, not rounded.")
        amount = _amount(entry.get("amount"), f"IT-225 {code} amount",
                         negative_hint=_NY_WRONG_SCHEDULE)
        total += amount
        detail.append({"code": code, "amount": amount})
    return {"total": round(total, 2), "entries": detail, "notes": notes}


def compute_ny_pension_exclusion(entries: "list[dict] | None",
                                 cap: "float | None",
                                 minimum_age: "int | None" = None) -> dict:
    """IT-201 line 29 — the pension and annuity income exclusion.

    PER TAXPAYER, and that is the whole difficulty. "You and your spouse can
    each subtract up to $20,000 of your OWN pension and annuity income.
    However, neither of you can claim any unused part of your spouse's
    exclusion" — so a household-level cap would let one spouse's unused
    allowance shelter the other's income, which is exactly what the form
    forbids. Each entry is capped on its own and the caps are summed after.

    `entitled` is the caller's, not this function's: the age gate is "59½
    before the start of the tax year", a taxpayer who turned 59½ DURING it
    counts only what they received afterwards, and a BENEFICIARY qualifies on
    the decedent's entitlement regardless of their own age. Those are judgements
    about facts this function is not given. What it will not do is guess one:
    an entry with no `entitled` answer is refused, because granting the
    exclusion unearned under-states the tax.

    Government pensions do NOT belong here. They are fully excluded on their
    own line, and routing one through this cap would silently limit a full
    exclusion to the cap — an over-statement of tax that looks like arithmetic.
    """
    notes: "list[str]" = []
    if not entries:
        notes.append(
            "Pension and annuity income exclusion NOT claimed (IT-201 line 29). "
            "If either taxpayer had qualifying non-government pension or "
            "annuity income and was 59½, this OVER-STATES the tax.")
        return {"total": 0.0, "per_taxpayer": [], "notes": notes}
    if cap is None:
        raise ValueError(
            "state.subtractions.pension_annuity_exclusion.amount is not "
            "fetched, and the exclusion is capped. Claiming it uncapped "
            "under-states the tax; re-run /tax-setup.")
    cap = _amount(cap, "pension exclusion cap")
    per_taxpayer = []
    seen: "set[str]" = set()
    total = 0.0
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"pension exclusion entry {i} must be an object")
        who = entry.get("taxpayer")
        if who not in ("primary", "spouse"):
            raise ValueError(
                f"pension exclusion entry {i} names taxpayer {who!r}; the cap "
                "is PER TAXPAYER, so each entry must say whose it is")
        if who in seen:
            raise ValueError(
                f"two pension exclusion entries for {who!r}. Combine that "
                "taxpayer's qualifying income into one entry — separate "
                "entries would each get the full cap.")
        seen.add(who)
        if entry.get("entitled") is not True:
            raise ValueError(
                f"pension exclusion entry for {who!r} does not state "
                "`entitled: true`. The age gate, the part-year rule for "
                "someone who turned 59½ during the year, and a beneficiary's "
                "claim on a decedent's entitlement are judgements about facts "
                "this function is not given — and granting the exclusion "
                "unearned under-states the tax.")
        if entry.get("government_pension"):
            raise ValueError(
                f"the pension exclusion entry for {who!r} is marked a "
                "GOVERNMENT pension. Those are fully excluded on their own "
                "line; capping one here would silently limit a full exclusion.")
        amount = _amount(entry.get("qualifying_amount"),
                            f"pension exclusion qualifying_amount for {who!r}")
        allowed = min(amount, cap)
        if allowed < amount:
            notes.append(
                f"{who}: qualifying pension and annuity income {amount:,.2f} "
                f"exceeds the exclusion cap; {allowed:,.2f} is excluded and "
                "the remainder stays taxable. The cap applies regardless of "
                "the number of sources.")
        per_taxpayer.append({"taxpayer": who, "qualifying": amount,
                             "excluded": round(allowed, 2)})
        total += allowed
    if minimum_age is not None:
        notes.append(
            f"Exclusion allowed on the caller's assertion that each taxpayer "
            f"met the age test (59-and-a-half in the fetched block: "
            f"{minimum_age}).")
    return {"total": round(total, 2), "per_taxpayer": per_taxpayer,
            "notes": notes}


# IT-201's named addition and subtraction lines, in form order. The NAMES are
# the contract with the caller and the ORDER is what `/tax-verify` ties against,
# so they live in one place rather than being spelled again at each use.
_NY_ADDITION_LINES = (
    ("state_local_bond_interest", 20),
    ("public_employee_414h", 21),
    ("college_savings_withdrawals", 22),
)
_NY_SUBTRACTION_LINES = (
    ("taxable_refunds", 25),
    ("government_pensions", 26),
    ("taxable_social_security", 27),
    ("us_government_bond_interest", 28),
)


def compute_ny_agi(
    federal_agi: float,
    additions: "dict | None" = None,
    subtractions: "dict | None" = None,
    filing_status: str = "single",
    limits: "dict | None" = None,
    it225_invalid_codes: "list[str] | None" = None,
) -> dict:
    """IT-201 lines 19 through 33 — New York adjusted gross income.

    New York starts from federal AGI and then applies its OWN modifications, so
    NYAGI is not federal AGI and the two are not interchangeable anywhere
    downstream: every New York threshold — the recapture's entry, the itemized
    reductions, the estimated-tax safe harbour — is measured against THIS
    figure. Handing the federal one to any of them mis-measures a taxpayer with
    modifications, and the larger their modifications the further off it is.

    Two kinds of modification, and they are kept apart on purpose:

    - The NAMED lines, which have their own place on the form — bond interest
      each way, the public-employee 414(h) add-back, the 529 lines, taxable
      refunds, government pensions at a FULL exclusion, taxable social
      security, and the capped pension-and-annuity exclusion.
    - Everything else, on Form IT-225 under a code, totalled by
      `compute_ny_modifications`, which checks each code against the schedule
      it was handed over as.

    NYAGI MAY BE NEGATIVE and that is an ordinary return — line 33 has no
    floor, and a Schedule C loss, a K-1 loss or an NOL carryforward reaches it
    routinely. Only the individual modification amounts are floored, because
    New York enters both directions as positive figures and lets the SCHEDULE
    carry the sign.

    Args:
        federal_agi: IT-201 line 19, the recomputed federal AGI.
        additions: the named lines above plus `it225`, a list of
            `{"code", "amount"}`.
        subtractions: the named lines above plus `pension_annuity` (a list of
            per-taxpayer entries for `compute_ny_pension_exclusion`),
            `college_savings_contributions`, and `it225`.
        filing_status: selects the 529 deduction cap, which New York keys as
            married-joint against everybody else.
        limits: `state.subtractions` from the fetched block.
        it225_invalid_codes: codes this form may not carry.

    Returns:
        {"nyagi", "total_additions", "total_subtractions", "lines", "notes"}

    Raises:
        ValueError: on a malformed figure, a modification on the wrong
            schedule, or a capped subtraction whose cap was never fetched —
            claiming one uncapped under-states the tax.
    """
    filing_status = _ny_status(filing_status)
    additions = additions or {}
    subtractions = subtractions or {}
    limits = limits or {}
    notes: "list[str]" = []
    lines: "dict[str, float]" = {}

    agi = _amount(federal_agi, "federal_agi", allow_negative=True)
    lines["line_19_federal_agi"] = round(agi, 2)

    total_additions = 0.0
    for name, line_no in _NY_ADDITION_LINES:
        amount = _amount(additions.get(name, 0) or 0, f"additions.{name}",
                         negative_hint=_NY_WRONG_SCHEDULE)
        lines[f"line_{line_no}_{name}"] = round(amount, 2)
        total_additions += amount
    it225_add = compute_ny_modifications(additions.get("it225"), "addition",
                                         it225_invalid_codes)
    notes.extend(it225_add["notes"])
    lines["line_23_other_additions"] = it225_add["total"]
    total_additions += it225_add["total"]
    lines["line_24_total_additions"] = round(agi + total_additions, 2)

    total_subtractions = 0.0
    for name, line_no in _NY_SUBTRACTION_LINES:
        amount = _amount(subtractions.get(name, 0) or 0,
                         f"subtractions.{name}",
                         negative_hint=_NY_WRONG_SCHEDULE)
        lines[f"line_{line_no}_{name}"] = round(amount, 2)
        total_subtractions += amount

    pension_limits = (limits.get("pension_annuity_exclusion") or {})
    pension = compute_ny_pension_exclusion(
        subtractions.get("pension_annuity"),
        pension_limits.get("amount"),
        pension_limits.get("minimum_age"))
    notes.extend(pension["notes"])
    lines["line_29_pension_annuity_exclusion"] = pension["total"]
    total_subtractions += pension["total"]

    college = _amount(subtractions.get("college_savings_contributions", 0)
                         or 0, "subtractions.college_savings_contributions")
    if college:
        # New York keys this cap as married-joint against EVERYBODY ELSE — the
        # same two-key shape the federal child-credit phase-out uses, and the
        # reason `docs/contributing/tax-law-cache.md` warns that the keying is
        # not uniform. Reading it with four status keys finds three of them
        # absent and would claim an uncapped deduction.
        caps = (limits.get("college_savings_deduction") or {})
        cap = caps.get("mfj") if filing_status == "mfj" else caps.get("single")
        if cap is None:
            raise ValueError(
                "state.subtractions.college_savings_deduction is not fetched "
                f"for {filing_status!r}, and the deduction is capped. Claiming "
                "it uncapped under-states the tax; re-run /tax-setup.")
        cap = _amount(cap, "college savings cap")
        if college > cap:
            notes.append(
                f"529 contributions {college:,.2f} exceed the deduction cap; "
                f"{cap:,.2f} is subtracted.")
            college = cap
    lines["line_30_college_savings"] = round(college, 2)
    total_subtractions += college

    it225_sub = compute_ny_modifications(subtractions.get("it225"),
                                         "subtraction", it225_invalid_codes)
    notes.extend(it225_sub["notes"])
    lines["line_31_other_subtractions"] = it225_sub["total"]
    total_subtractions += it225_sub["total"]
    lines["line_32_total_subtractions"] = round(total_subtractions, 2)

    nyagi = round(agi + total_additions - total_subtractions, 2)
    lines["line_33_nyagi"] = nyagi
    return {
        "nyagi": nyagi,
        "total_additions": round(total_additions, 2),
        "total_subtractions": round(total_subtractions, 2),
        "lines": lines,
        "notes": notes,
    }


def _ny_round_half_up(value: float) -> int:
    """New York's own rounding: "Drop amounts that are less than 50 cents" and
    "increase amounts from 50 to 99 cents to the next dollar" (IT-201-I,
    Entering whole dollar amounts). Python's `round` is HALF-EVEN, so it turns
    2,416.50 into 2,416 where the form says 2,417 — a dollar, every time the
    cents land on a tie, in the direction that under-states."""
    return math.floor(value + 0.5) if value >= 0 else -math.floor(-value + 0.5)


def _ny_table_bases(brackets: dict, schedule: str, tax_table: dict,
                    source: str) -> "tuple[list, list]":
    """The schedule the TABLE is built from, which is not always the published
    one.

    New York's table departs from its own rate schedule band by band. Every
    row of the TY2024 and TY2025 State tables was compared with the schedule at
    the row's midpoint: the single, joint-above-$27,900 and head-of-household
    columns agree on every row with the PUBLISHED bases, and the married-joint
    $23,600-27,900 band is a dollar low on 22 rows until its base is the bands
    beneath it summed, unrounded, where the schedule and §601(a) both print the
    rounded figure. Summing every band instead breaks 510 single rows and 17
    head-of-household ones, so no one rule produces the table; WHICH bands
    depart is a per-year fact about the published table. The City table
    follows its published bases on every row.

    So the departures are FETCHED, as `table_bases` — `[{"schedule", "min"}]`,
    `[]` where the table follows the schedule — and each named band's base is
    replaced, in a copy, by the bands beneath it summed. The entry names the
    band and never the figure: the figure differs from the printed one by
    CENTS, so no check can tell a mis-typed one from a real one, and a
    transcribed 977.2 moved a row up a dollar with nothing refusing.

    EVERY entry is checked, whichever schedule this return is on, because one
    for another column would otherwise sit wrong until a return in that column
    reached the table. A schedule letter or floor that meets nothing refuses:
    the entry would read as applied while the table stayed a dollar off.

    A key that was never fetched is a NOTE, not a refusal: the most it moves a
    row is the dollar the table rounds to, inside the one-dollar acceptance
    bar, and every cache written before the key existed would otherwise stop.
    """
    entries = (tax_table or {}).get("table_bases")
    if entries is None:
        return brackets[schedule], [
            f"{source}.table_bases is not fetched. New York's table departs "
            "from its own rate schedule in some bands, so this line can be a "
            "dollar off the published table. Re-run /tax-setup."]
    out = [dict(band) for band in brackets[schedule]]
    for name, floor in _ny_table_bases_entries(entries,
                                               f"{source}.table_bases"):
        bands = brackets.get(name)
        if not bands:
            raise ValueError(
                f"{source}.table_bases names a schedule that the brackets do "
                f"not have; known: {sorted(brackets)}")
        at = next((i for i, band in enumerate(bands)
                   if band.get("min") == floor), None)
        if at is None:
            raise ValueError(
                f"{source}.table_bases names a band of schedule {name!r} "
                f"starting at {floor:,}, and the schedule has no band there")
        if name == schedule:
            out[at]["base"] = sum((band["max"] - band["min"]) * band["rate"]
                                  for band in bands[:at])
    return out, []


def _ny_table_bases_entries(entries, where: str) -> "list[tuple[str, float]]":
    """The SHAPE of a `table_bases` list, as (schedule, min) pairs: the one
    copy, which `tax_law_fetcher set` runs when the key is written and
    `_ny_table_bases` runs again on a cache it did not write. Whether each
    pair meets a band needs the brackets, so that check stays with the engine.
    """
    if not isinstance(entries, list):
        raise ValueError(
            f"{where} must be a list of {{schedule, min}}, got "
            f"{type(entries).__name__}")
    pairs: "list[tuple[str, float]]" = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"schedule", "min"}:
            raise ValueError(
                f"{where} holds a {type(entry).__name__} that is not a "
                "{schedule, min} object; the entry names the band, and the "
                "engine sums its base")
        name = entry["schedule"]
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{where} holds an entry with no schedule key")
        pair = (name, _amount(entry["min"], f"{where}[{name}].min"))
        if pair in pairs:
            raise ValueError(
                f"{where} names schedule {name!r}'s band at {pair[1]:,} twice")
        pairs.append(pair)
    return pairs


def _ny_table_tax(taxable_income: float, brackets: dict, schedule: str,
                  tax_table: dict,
                  source: str = "state.tax_table") -> "tuple[float, list, list]":
    """New York's TAX TABLE, which is a different method from its schedule.

    Below a published taxable income, and at or below the recapture's entry
    threshold, the form says to use the TABLE — and the table is not a
    restatement of the schedule. It is the schedule evaluated at each row's
    MIDPOINT and rounded to the dollar, verified on all three schedules against
    the published TY2025 table: the $50,000-$50,050 row reads 2,587 / 2,419 /
    2,503 for single-and-MFS, married-joint and head-of-household, which is
    exactly the schedule at $50,025. The row FLOOR gives 2,586 / 2,418 / 2,502,
    one dollar low in every case. The schedule it is evaluated on is not
    always the published one, band for band: `_ny_table_bases` says where.

    Using the schedule where the form says table is worth up to about two
    dollars and runs BOTH ways — it over-states whenever taxable income sits in
    the upper half of its row, which is half of all returns — against a stated
    acceptance bar of one dollar a line.

    The ROW WIDTH is not constant. It is fetched as segments, because the
    bottom of the table uses narrower rows than the $50 it settles into, and a
    single width assumed across the range would be a guess at exactly the
    figure this function exists to get right.

    `source` is the cache path the table came from, for the refusals: the City
    table is the same method on its own rows (`state.nyc_tax.tax_table`), and a
    message naming the State's key would send a reader to the wrong block.
    """
    segments = (tax_table or {}).get("row_segments") or []
    if not segments:
        raise ValueError(
            f"{source}.row_segments is not fetched, and this return is "
            "in the TABLE region. The table is the schedule at each row's "
            "midpoint, and its rows are not all the same width — using the "
            "schedule instead is worth up to two dollars in EITHER direction. "
            "Re-run /tax-setup.")
    table, notes = _ny_table_bases(brackets, schedule, tax_table, source)
    for segment in segments:
        upper = segment.get("not_over")
        if taxable_income >= segment["over"] and (upper is None
                                                  or taxable_income < upper):
            width = segment.get("row_width")
            if not width:
                raise ValueError(
                    f"the tax-table segment containing {taxable_income:,.2f} "
                    "has no row_width, so its midpoint cannot be found")
            offset = taxable_income - segment["over"]
            row_floor = segment["over"] + (offset // width) * width
            midpoint = row_floor + width / 2
            at_midpoint, _ = _ny_schedule_tax(midpoint, table, schedule)
            tax = float(_ny_round_half_up(at_midpoint))
            return tax, [{
                "method": "tax table",
                "row": f"{row_floor:,.0f} - {row_floor + width:,.0f}",
                "midpoint": midpoint,
                "tax": tax,
            }], notes
    raise ValueError(
        f"taxable income {taxable_income:,.2f} is in the tax-table region and "
        "no fetched row segment covers it; the segments must span 0 up to "
        f"{source}.max_taxable_income")


def _ny_result(exact: float, schedule: str, regime: str, recaptured: float,
               breakdown: list, notes: list) -> dict:
    """`tax` is IT-201 LINE 39 — a whole dollar, because that is what the form
    carries: "enter whole dollar amounts only … increase amounts from 50 to 99
    cents to the next dollar".

    Returning cents and leaving the caller to round was a trap with no warning
    in the signature. It cost a dollar twice over: Python's `round` is
    half-EVEN, so a tie went down where the form goes up; and rounding to cents
    first and to dollars second carried values across a boundary the form never
    crosses — 19,413.4966 became 19,413.50 and then 19,414, where the form says
    19,413. `tax_exact` keeps the unrounded figure for anything that needs to
    audit the arithmetic.
    """
    return {
        "tax": _ny_round_half_up(exact),
        "tax_exact": round(exact, 4),
        "schedule": schedule,
        "regime": regime,
        "recapture_amount": round(recaptured, 4),
        "breakdown": breakdown,
        "notes": notes,
    }


def compute_ny_tax(
    taxable_income: float,
    nyagi: float,
    filing_status: str,
    brackets: "dict[str, list]",
    schedule_by_status: "dict[str, str]",
    recapture: dict,
    tax_table: "dict | None" = None,
) -> dict:
    """New York State tax: the §601 rate schedule PLUS the benefit recapture.

    The recapture is INSIDE this function and not a caller's option, because a
    caller that can forget it is a caller that will. Above the entry threshold,
    plain bracket math UNDER-states New York tax — not by rounding, but by the
    whole benefit of the graduated schedule — and under-stating what a taxpayer
    owes is the direction AGENTS.md §4 calls a hard error rather than a degrade.

    Three regimes, selected on NYAGI and then on TAXABLE INCOME:

    - **At or below the entry threshold**: the schedule alone.
    - **The entry tier**: the tax phases from the graduated result up to a FLAT
      rate on the whole taxable income, over a band of NYAGI above the
      threshold. The worksheet says "stop" once the band is exhausted; a capped
      fraction reaches the same place without a special case.
    - **The higher tiers**: the schedule result plus a fixed recapture BASE plus
      an incremental BENEFIT phased over the same band. Both figures move year
      to year, which is why they are fetched rather than written here.
    - **Above the top NYAGI**: one flat rate on the whole taxable income, with
      no schedule at all. Not a recapture — a different tax.

    `filing_status` is mapped to a §601 SCHEDULE, never used directly: (a)
    covers married-filing-jointly and the surviving spouse, (c) covers the
    single filer and married-filing-separately. Those are statutory identities,
    and the mapping comes from the cache so this function does not hold a second
    copy of it.

    Args:
        taxable_income: IT-201 line 38.
        nyagi: IT-201 line 33 — New York AGI, NOT federal AGI. The two differ by
            the IT-225 modifications, and the thresholds below are New York's.
        filing_status: Single | MFJ | MFS | HoH | QSS, in either case —
            validated by VALUE and lower-cased by `_ny_status`, so the
            canonical code the federal normaliser returns is accepted
            here rather than silently taking the Single branch (Q9).
        brackets: `state.brackets`, keyed by schedule.
        schedule_by_status: `state.bracket_schedule_by_status`.
        recapture: `state.tax_table_benefit_recapture`.

    NOT the whole of New York State tax. This is IT-201 LINE 39. Line 46 is the
    state total, and two of its components are outside these inputs entirely:
    line 44's separate tax on a lump-sum distribution (Form IT-230) and line
    45's net other New York State taxes (IT-201-ATT). A caller that treats this
    as the state tax under-states a return that has either. FULL-YEAR RESIDENTS
    only — a part-year or nonresident return uses these worksheets against a
    different base, and nothing here refuses one.

    Every figure is an argument, which is what makes this year-agnostic: the
    rates change, and TY2026's schedules are a cut from TY2025's. The recapture
    tables must be re-fetched in LOCKSTEP with the brackets — a mixed pair
    computes a benefit against rates that never coexisted.

    Returns:
        {"tax", "tax_exact", "schedule", "regime", "recapture_amount",
         "breakdown", "notes"} — `tax` is the WHOLE-DOLLAR line 39.

    Raises:
        ValueError: on a malformed input, a missing schedule, or — the one that
            matters — a return above the entry threshold whose recapture data
            was never fetched. Refusing is the only safe answer there: every
            other option under-states the tax.
    """
    filing_status = _ny_status(filing_status)
    for name, value in (("taxable_income", taxable_income), ("nyagi", nyagi)):
        if value is None or isinstance(value, bool) or \
                not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a number, got {value!r}")
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite, got {value!r}")
    # Taxable income is floored by the form — "if line 34 is more than line 33,
    # leave blank" — so a negative one is a defect. NEW YORK AGI IS NOT: line 33
    # is federal AGI plus and minus the IT-225 modifications and has no floor,
    # so a Schedule C loss, a K-1 loss or an NOL carryforward routinely makes it
    # negative. Refusing that refused an ordinary return whose New York tax is
    # zero, and a gate that fires on correct data is the one that gets waved
    # through next time.
    if taxable_income < 0:
        raise ValueError(
            f"taxable_income cannot be negative, got {taxable_income!r}")
    # NYAGI is the base taxable income is computed FROM, so taxable income can
    # never exceed it. When it does, one of the two was mis-sourced — most
    # likely a federal AGI handed in where line 33 belongs — and every
    # threshold below would then be applied to the wrong figure.
    if nyagi >= 0 and taxable_income > nyagi:
        raise ValueError(
            f"taxable income {taxable_income:,.2f} exceeds New York AGI "
            f"{nyagi:,.2f}; line 38 is computed from line 33, so this is a "
            "mis-sourced figure (federal AGI handed in as NYAGI?)")

    schedule = (schedule_by_status or {}).get(filing_status)
    if schedule is None:
        raise ValueError(
            f"no §601 schedule for filing status {filing_status!r} — known: "
            f"{sorted(schedule_by_status or {})}")
    table = (brackets or {}).get(schedule)
    if not table:
        raise ValueError(
            f"New York bracket schedule {schedule!r} is empty; re-run "
            "/tax-setup to fetch state.brackets")

    schedule_tax, breakdown = _ny_schedule_tax(taxable_income, table, schedule)
    notes: "list[str]" = []

    entry_threshold = (recapture or {}).get("entry_threshold")
    if entry_threshold is None:
        raise ValueError(
            "state.tax_table_benefit_recapture.entry_threshold is not fetched, "
            "so nothing here knows whether this return is above the recapture. "
            "Re-run /tax-setup: guessing under-states every affected return.")
    if nyagi <= entry_threshold:
        # THE TABLE, not the schedule, below the published taxable income. Two
        # methods, selected on BOTH figures, and the taxable-income condition is
        # not implied by the NYAGI one — which is why this cannot be folded into
        # a single "at or below the threshold" branch.
        table_max = (tax_table or {}).get("max_taxable_income")
        if table_max is None:
            raise ValueError(
                "state.tax_table.max_taxable_income is not fetched, so nothing "
                "here knows whether this return uses New York's TABLE or its "
                "rate schedule. They give different answers — the table is "
                "midpoint-rounded — and picking the wrong one is worth up to "
                "two dollars in either direction. Re-run /tax-setup.")
        if taxable_income < table_max:
            table_tax, table_breakdown, table_notes = _ny_table_tax(
                taxable_income, brackets, schedule, tax_table)
            return _ny_result(table_tax, schedule, "tax_table", 0.0,
                              table_breakdown, notes + table_notes)
        return _ny_result(schedule_tax, schedule, "graduated", 0.0,
                          breakdown, notes)

    # Above the entry threshold the tax comes off a WORKSHEET, and the
    # worksheet enters the schedule tax as a line in dollars and cents
    # (worksheet 3, line 3: 18,252 + 0.0685 x 1 = 18,252.07). Only the
    # schedule and the table above are rounded once, to the dollar.
    schedule_tax = round(schedule_tax, 2)
    # Above the top NYAGI the schedule stops applying altogether.
    top = (recapture.get("full_recapture") or {})
    if top.get("nyagi_over") is None or top.get("rate") is None:
        raise ValueError(
            "state.tax_table_benefit_recapture.full_recapture is not fetched. "
            "Above a very high NYAGI New York taxes the whole taxable income "
            "at one rate; without it this return falls back to the tiers and "
            "under-states.")
    if nyagi > top["nyagi_over"]:
        tax = taxable_income * top["rate"]
        notes.append(
            "New York AGI is above the top threshold: the entire taxable "
            "income is taxed at one rate, with no rate schedule.")
        return _ny_result(tax, schedule, "full_recapture",
                          tax - schedule_tax, breakdown, notes)

    band = recapture.get("phase_in_band")
    if not band:
        raise ValueError(
            "state.tax_table_benefit_recapture.phase_in_band is not fetched, "
            "and the recapture phases in over it. Re-run /tax-setup.")
    # "Divide by the band and round the result to the FOURTH decimal place" is
    # the worksheet's own instruction, not a convenience — rounding elsewhere
    # moves the answer by cents against a software-prepared return.
    fraction = round(min(nyagi - entry_threshold, band) / band, 4)

    entry = (recapture.get("entry_tier") or {}).get(schedule) or {}
    ceiling = entry.get("taxable_income_not_over")
    flat_rate = entry.get("flat_rate")
    if ceiling is None or flat_rate is None:
        raise ValueError(
            f"state.tax_table_benefit_recapture.entry_tier[{schedule!r}] is "
            "not fetched. The first tier does not use a base and benefit at "
            "all — it phases up to a FLAT rate on the whole taxable income — "
            "so there is nothing to fall back to.")

    if taxable_income <= ceiling:
        benefit = round(taxable_income * flat_rate, 2) - schedule_tax
        if benefit < 0:
            raise ValueError(
                f"the §601 schedule {schedule!r} produces {schedule_tax:,.2f} "
                f"on {taxable_income:,.2f}, MORE than the entry tier's flat "
                f"rate would ({taxable_income * flat_rate:,.2f}). The brackets "
                "and the flat rate disagree, so one of them is mis-fetched; "
                "applying the difference would REDUCE the tax.")
        # The form rounds the FRACTION and nothing else. Rounding the product
        # to cents first can carry a value across the whole-dollar boundary the
        # form would not cross — worth a dollar on the return.
        recaptured = benefit * fraction
        return _ny_result(schedule_tax + recaptured, schedule,
                          "entry_phase_in", recaptured, breakdown, notes)

    tiers = (recapture.get("tiers") or {}).get(schedule) or []
    if not tiers:
        raise ValueError(
            f"state.tax_table_benefit_recapture.tiers[{schedule!r}] is empty "
            "and this return is above the entry tier. Each tier carries its "
            "own base and benefit, and they move year to year — there is no "
            "structural default to use instead.")
    _ny_check_recapture_tiers(tiers, schedule)
    for tier in tiers:
        upper = tier.get("taxable_income_not_over")
        if taxable_income > tier["taxable_income_over"] and \
                (upper is None or taxable_income <= upper):
            excess = max(nyagi - tier["nyagi_excess_over"], 0.0)
            tier_fraction = round(min(excess, band) / band, 4)
            recaptured = (tier["recapture_base"]
                          + tier["incremental_benefit"] * tier_fraction)
            return _ny_result(schedule_tax + recaptured, schedule, "tier",
                              recaptured, breakdown, notes)
    raise ValueError(
        f"taxable income {taxable_income:,.2f} falls in no fetched recapture "
        f"tier for schedule {schedule!r}; the top tier's "
        "taxable_income_not_over must be null. Re-run /tax-setup.")


# IT-201-I household credit tables 1, 2 and 3. New York groups the statuses
# THREE ways here and it is not the grouping it uses anywhere else on the
# return: single alone, married-filing-separately alone, and everybody else
# together. Reading it with the §601 schedules would put MFS with single, which
# is right for the rate schedule and wrong by half for this credit.
_NY_HOUSEHOLD_CREDIT_TABLE = {
    "single": "single",
    "mfj": "mfj_hoh_qss", "hoh": "mfj_hoh_qss", "qss": "mfj_hoh_qss",
    "mfs": "mfs",
}


def compute_ny_household_credit(
    federal_agi: float,
    filing_status: str,
    exemptions: int,
    table: "dict | None" = None,
    can_be_claimed_as_dependent: bool = False,
) -> dict:
    """IT-201 line 40 — the New York State household credit.

    Three things about it are unlike everything else in this module:

    - It is keyed on FEDERAL adjusted gross income, not New York's. Every other
      threshold here reads line 33; this one reads line 19, and handing it
      NYAGI mis-selects the band for any taxpayer with modifications.
    - A taxpayer who CAN BE CLAIMED AS A DEPENDENT on someone else's federal
      return does not qualify at all — item C on the front of the form, the
      same fact the standard deduction splits on. It is a bar, not a
      reduction.
    - The statuses are grouped THREE ways, and it is not the grouping the rate
      schedules use: single alone, married-filing-separately alone, and
      married-joint with head-of-household and the surviving spouse together.
      Married-filing-separately pairs with SINGLE for the rate schedule and
      gets roughly half the credit here.

    `exemptions` is the count on the form's own terms: the dependents listed at
    item H, PLUS one for the taxpayer, plus one more for a spouse on a joint
    return. Past the last column the table gives a per-head increment to add,
    which is why the count is passed rather than clamped by the caller.
    """
    filing_status = _ny_status(filing_status)
    agi = _amount(federal_agi, "federal_agi", allow_negative=True)
    if isinstance(exemptions, bool) or not isinstance(exemptions, int) \
            or exemptions < 1:
        raise ValueError(
            f"exemptions must be a whole number of at least 1 (the taxpayer "
            f"themselves), got {exemptions!r}")
    if can_be_claimed_as_dependent:
        return {"credit": 0.0, "band": None, "notes": [
            "No household credit: the taxpayer can be claimed as a dependent "
            "on another federal return (IT-201 item C). This is a BAR, not a "
            "reduced amount."]}
    key = _NY_HOUSEHOLD_CREDIT_TABLE.get(filing_status)
    if key is None:
        raise ValueError(
            f"no household credit table for filing status {filing_status!r}")
    bands = (table or {}).get(key)
    if not bands:
        raise ValueError(
            f"state.credits.household.table[{key!r}] is not fetched. The "
            "household credit is a TABLE, not a formula — there is nothing to "
            "compute it from, and omitting it over-states the tax.")
    for band in bands:
        lower = band.get("agi_over")
        upper = band.get("agi_not_over")
        # The lowest band has NO lower bound: the instructions note the figure
        # there "could be 0 or a negative amount", so a taxpayer with a loss
        # still lands in it.
        if (lower is None or agi > lower) and (upper is None or agi <= upper):
            if "amount" in band:
                credit = _amount(band["amount"], "household credit amount")
            else:
                by_count = band.get("by_exemptions") or []
                if not by_count:
                    raise ValueError(
                        "a household credit band carries neither a flat "
                        "`amount` nor a `by_exemptions` column list")
                if exemptions <= len(by_count):
                    credit = _amount(by_count[exemptions - 1],
                                        "household credit amount")
                else:
                    # "For each individual over 7, ADD the amount in this
                    # column to the column 7 amount" — an increment on top of
                    # the last column, not a replacement for it.
                    each = band.get("each_additional")
                    if each is None:
                        raise ValueError(
                            f"{exemptions} exemptions is past the last fetched "
                            "column and the band has no `each_additional`; the "
                            "table continues rather than stopping, so "
                            "truncating here under-states the credit")
                    credit = (_amount(by_count[-1], "household credit") +
                              _amount(each, "household credit increment")
                              * (exemptions - len(by_count)))
            return {"credit": round(credit, 2),
                    "band": {"over": lower, "not_over": upper}, "notes": []}
    # The table runs out at an income above which no credit is allowed, and the
    # form simply has no row. Zero is the right answer and it is stated, not
    # fallen into.
    return {"credit": 0.0, "band": None, "notes": [
        "No household credit: federal adjusted gross income is above the last "
        "band of the table."]}


def compute_ny_state_tax_total(
    tax_before_credits: float,
    household_credit: float = 0.0,
    resident_credit: float = 0.0,
    other_nonrefundable_credits: float = 0.0,
    net_other_state_taxes: float = 0.0,
) -> dict:
    """IT-201 lines 39 through 46 — where the nonrefundable credits land.

    Two rules, and the second is the one an implementation gets wrong:

    - The nonrefundable credits are SUMMED and then capped at the tax: line 44
      is "Subtract line 43 from line 39 (if line 43 is more than line 39, enter
      0)". The excess is simply lost, and no ordering among them changes the
      total — which is why this takes three figures rather than a sequence.
    - Line 45, the net other New York State taxes, is added AFTER that cap. A
      nonrefundable credit therefore CANNOT offset it. Netting everything
      first and then applying the credits would let a credit absorb a tax the
      form never lets it reach, and under-state the return.

    Refundable credits are not here at all: they are payments further down the
    form, and treating one as nonrefundable would silently discard the part
    that should have been refunded.
    """
    tax = _amount(tax_before_credits, "tax_before_credits")
    household = _amount(household_credit, "household_credit")
    resident = _amount(resident_credit, "resident_credit")
    other = _amount(other_nonrefundable_credits,
                       "other_nonrefundable_credits")
    net_other = _amount(net_other_state_taxes, "net_other_state_taxes")

    total_credits = round(household + resident + other, 2)
    notes: "list[str]" = []
    after_credits = round(max(tax - total_credits, 0.0), 2)
    if total_credits > tax:
        notes.append(
            f"Nonrefundable credits of {total_credits:,.2f} exceed the tax of "
            f"{tax:,.2f}; {total_credits - tax:,.2f} is lost. They cannot "
            "reduce the tax below zero and they do not carry forward.")
    # Only when the credits were CAPPED. On an ordinary return the credits
    # applied in full and nothing about line 45 is surprising; saying it every
    # time would put a line in `meta.limitations` that means nothing, which is
    # how a reader learns to skim them.
    if net_other and total_credits > tax:
        notes.append(
            "The net other New York State taxes are added AFTER the credits "
            "are applied, so the lost credit above does not offset them "
            "either.")
    return {
        "line_43_total_nonrefundable": total_credits,
        "line_44_after_credits": after_credits,
        "line_45_net_other_taxes": round(net_other, 2),
        "line_46_total_state_tax": round(after_credits + net_other, 2),
        "credits_lost": round(max(total_credits - tax, 0.0), 2),
        "notes": notes,
    }


# IT-201's "Payments and refundable credits" block, in form order. EVERY New
# York credit other than the household and resident credits is here, which is
# the answer to a question the spec carried open: they are not credits against
# the tax at all, they are PAYMENTS. Two of them are easy to miss because they
# sit between the ones people name — the noncustodial parent EIC and the New
# York City EIC.
_NY_REFUNDABLE_CREDIT_LINES = (
    ("empire_state_child", 63),
    ("child_and_dependent_care", 64),
    ("earned_income", 65),
    ("noncustodial_parent_eic", 66),
    ("real_property_tax", 67),
    ("college_tuition", 68),
    ("nyc_school_tax_fixed", 69),
    ("nyc_earned_income", 70),
    ("other_refundable", 71),
)


def compute_ny_settlement(
    total_tax: float,
    refundable_credits: "dict | None" = None,
    state_withholding: float = 0.0,
    city_withholding: float = 0.0,
    yonkers_withholding: float = 0.0,
    estimated_payments: float = 0.0,
    applied_to_next_year: float = 0.0,
    estimated_tax_penalty: float = 0.0,
    other_penalties: float = 0.0,
    college_tuition_deduction_claimed: bool = False,
) -> dict:
    """IT-201 lines 63 through 82 — payments, refund, and the amount owed.

    **Every remaining New York credit is REFUNDABLE**, and that is not a detail
    of presentation: they are added to the PAYMENTS, so they can produce a
    refund with no tax to offset. Treating one as nonrefundable would cap it at
    the tax and silently discard the part that should have been paid out — and
    the capped path is one function away, in `compute_ny_state_tax_total`. The
    names here are a closed set for exactly that reason: handing the household
    or resident credit to this function refuses rather than refunding it.

    **The penalty is INSIDE the amount owed.** Line 80 is "the amount of tax you
    owe PLUS any estimated tax penalty you owe and any other" penalties, and
    against an overpayment it reduces line 77 instead. Reporting it beside the
    amount owed and letting a caller add it double-counts it; reporting it
    nowhere loses it. This repository has paid for that exact shape once
    already, on federal Form 1040 line 38, where a review found the penalty
    missing from BOTH settlement lines.

    Line 79's amount applied forward comes out of the overpayment and cannot
    exceed it — the form computes line 78 as line 77 minus line 79, and a
    larger figure would manufacture a refund out of nothing.
    """
    tax = _amount(total_tax, "total_tax")
    credits = refundable_credits or {}
    known = {name for name, _ in _NY_REFUNDABLE_CREDIT_LINES}
    unknown = sorted(set(credits) - known)
    if unknown:
        raise ValueError(
            f"{unknown} are not New York refundable credits. Every credit on "
            "this block is a PAYMENT; a nonrefundable one belongs in "
            "compute_ny_state_tax_total, where it is capped at the tax. "
            f"Known here: {sorted(known)}")
    if college_tuition_deduction_claimed and credits.get("college_tuition"):
        raise ValueError(
            "both the college tuition CREDIT (line 68) and the college tuition "
            "itemized DEDUCTION (IT-196 line 48) are claimed. The form allows "
            "one or the other — \"you cannot claim both\" — and taking both "
            "under-states the tax twice over.")

    lines: "dict[str, float]" = {}
    total_payments = 0.0
    for name, line_no in _NY_REFUNDABLE_CREDIT_LINES:
        amount = _amount(credits.get(name, 0) or 0,
                            f"refundable_credits.{name}")
        lines[f"line_{line_no}_{name}"] = round(amount, 2)
        total_payments += amount
    for name, line_no, value in (
            ("state_withholding", 72, state_withholding),
            ("city_withholding", 73, city_withholding),
            ("yonkers_withholding", 74, yonkers_withholding),
            ("estimated_payments", 75, estimated_payments)):
        amount = _amount(value, name)
        lines[f"line_{line_no}_{name}"] = round(amount, 2)
        total_payments += amount
    lines["line_76_total_payments"] = round(total_payments, 2)

    penalty = _amount(estimated_tax_penalty, "estimated_tax_penalty")
    other = _amount(other_penalties, "other_penalties")
    applied = _amount(applied_to_next_year, "applied_to_next_year")
    notes: "list[str]" = []

    overpaid = round(max(total_payments - tax, 0.0), 2)
    if overpaid:
        # "…or reduce the overpayment on line 77". The penalty comes out of the
        # refund before anything is applied forward, and what it cannot absorb
        # becomes an amount owed.
        absorbed = min(overpaid, penalty + other)
        overpaid = round(overpaid - absorbed, 2)
        owed = round(penalty + other - absorbed, 2)
        if absorbed:
            notes.append(
                f"The estimated tax penalty and other penalties of "
                f"{penalty + other:,.2f} reduce the overpayment rather than "
                "being billed separately.")
    else:
        owed = round(tax - total_payments + penalty + other, 2)
        if penalty or other:
            notes.append(
                "The amount owed INCLUDES the estimated tax penalty and any "
                "other penalties; they are not a separate bill.")

    if applied > overpaid:
        raise ValueError(
            f"{applied:,.2f} is applied to next year's estimated tax but the "
            f"overpayment is only {overpaid:,.2f}. Line 78 is line 77 less "
            "line 79, so a larger figure manufactures a refund out of nothing.")
    lines["line_77_overpaid"] = overpaid
    lines["line_79_applied_forward"] = round(applied, 2)
    lines["line_78_refund"] = round(overpaid - applied, 2)
    lines["line_80_amount_owed"] = owed
    lines["line_81_estimated_tax_penalty"] = round(penalty, 2)
    lines["line_82_other_penalties"] = round(other, 2)
    return {
        "total_payments": round(total_payments, 2),
        "refund": lines["line_78_refund"],
        "applied_forward": lines["line_79_applied_forward"],
        "amount_owed": owed,
        "lines": lines,
        "notes": notes,
    }


def compute_nyc_taxable_income(
    state_taxable_income: float,
    charitable_gifts_trust_deducted: float = 0.0,
    nyc_resident_spouses: "int | None" = None,
    filing_status: str = "single",
    nyc_resident_full_year: "bool | None" = None,
) -> dict:
    """IT-201 line 47 — New York City taxable income.

    It is the STATE's taxable income in the ordinary case, and the instructions
    say so outright: "If No, enter the line 38 amount on line 47." The
    worksheet exists for one narrow situation — a contribution to the New York
    Charitable Gifts Trust Fund that was ALSO claimed as an IT-196 itemized
    deduction — and it adds that contribution back, because the City does not
    allow it.

    Modelling the worksheet as the general case would be harmless arithmetic
    with a zero add-back; the reason it is written this way round is the
    opposite risk, that a caller who has the contribution never learns it
    matters.

    **A joint return with only ONE New York City resident does not use this
    line at all.** "If you are married and filing a joint New York State
    return and only one of you was a resident of New York City for all of
    2025, do not enter an amount here" — that return goes to line 51 instead,
    on a different computation this function cannot do. It refuses rather than
    taxing the whole household as residents.

    **Nor does a City resident for only PART of the year** (AA11). Line 47 is a
    whole year's income, and a part-year City resident's tax is figured on
    IT-360.1 and entered on line 50, which is not modeled. Reaching this
    function means `nyc_resident` is true, so on a joint return a count of ZERO
    whole-year spouses is a couple who both lived in the City part of the year,
    and it refuses like a count of one. Every other filer answers
    `nyc_resident_full_year`, and an absent answer refuses too: assuming a
    whole year is the answer that over-states the City tax for anyone who
    moved into or out of the City.
    """
    filing_status = _ny_status(filing_status)
    taxable = _amount(state_taxable_income, "state_taxable_income")
    addback = _amount(charitable_gifts_trust_deducted,
                         "charitable_gifts_trust_deducted")
    # REQUIRED on a joint return, and that is the fix for a guard that could
    # not fire. `nyc_resident_spouses` shipped optional, and the workspace has
    # no field carrying it: the profile holds ONE household boolean,
    # `flags.nyc_resident`, and the interview asks one question. So a joint
    # return with a Manhattan spouse and a Connecticut spouse arrived here with
    # the argument omitted, took the ordinary path, and had the CITY tax
    # computed on the whole household — over-stating it by the non-resident
    # spouse's entire share, which is the direction this refusal exists for.
    #
    # A guard whose precondition is a field nobody collects is not a guard. So
    # the ABSENCE is refused too, and `/tax-setup` now asks the second question
    # — one flag cannot stand for two independent facts.
    if filing_status == "mfj":
        if nyc_resident_spouses is None:
            raise ValueError(
                "a joint return must say how many spouses were New York City "
                "residents for the whole year: the answer decides whether "
                "line 47 is used at all. Pass nyc_resident_spouses (0, 1 or "
                "2) from `flags.nyc_resident_spouses`. Assuming two "
                "over-states the city tax for a couple with one resident by "
                "the non-resident spouse's whole share.")
        if nyc_resident_spouses not in (0, 1, 2) \
                or isinstance(nyc_resident_spouses, bool):
            raise ValueError(
                "nyc_resident_spouses must be 0, 1 or 2, got "
                f"{nyc_resident_spouses!r}")
        if nyc_resident_spouses == 1:
            raise ValueError(
                "a joint return with only ONE New York City resident does not "
                "enter a line 47 amount — it is computed on line 51 instead. "
                "Taxing the whole household as residents would over-state the "
                "city tax by the non-resident spouse's whole share.")
        if nyc_resident_spouses == 0:
            raise ValueError(
                "a joint return reached the City tax with NEITHER spouse a New "
                "York City resident for the whole year, so both lived there for "
                "part of it. A part-year City return (IT-360.1, IT-201 line 50) "
                "is not modeled, and taxing the whole year's income as City "
                "taxable income over-states the City tax by the months spent "
                "outside the City.")
    else:
        if nyc_resident_full_year is None:
            raise ValueError(
                "a return with nyc_resident must say whether the taxpayer was a "
                "New York City resident for the WHOLE year: pass "
                "nyc_resident_full_year from `flags.nyc_resident_full_year`. "
                "Assuming the whole year over-states the City tax for anyone "
                "who moved into or out of the City.")
        if not isinstance(nyc_resident_full_year, bool):
            raise ValueError(
                "nyc_resident_full_year must be True or False, got "
                f"{nyc_resident_full_year!r}")
        if not nyc_resident_full_year:
            raise ValueError(
                "a New York City resident for only part of the year files a "
                "part-year City return (IT-360.1, IT-201 line 50), which is not "
                "modeled; taxing the whole year's income over-states the City "
                "tax by the months spent outside the City.")
    notes: "list[str]" = []
    if addback:
        notes.append(
            "New York Charitable Gifts Trust Fund contributions claimed as an "
            "IT-196 itemized deduction are added back for the city.")
    return {"nyc_taxable_income": round(taxable + addback, 2),
            "addback": round(addback, 2), "notes": notes}


def compute_nyc_resident_tax(
    nyc_taxable_income: float,
    filing_status: str,
    brackets: "dict[str, list] | None",
    schedule_by_status: "dict[str, str]",
    tax_table: "dict | None",
) -> dict:
    """IT-201 line 47a — the New York City resident tax on line 47.

    Nothing computed this line before AA13. `state.nyc_tax` was fetched and
    read by nothing, `compute_city_and_yonkers_total` took the tax as an
    argument defaulting to zero, and the compute step never said where it came
    from — so a City resident's return carried a City tax of zero while the
    shipped record said lines 47-58 were done.

    TWO METHODS, chosen on line 47 alone: "Is line 47 ... less than $65,000?
    If Yes, find your New York City resident tax using the 2025 New York City
    Tax Table ... If No, ... using the New York City tax rate schedule"
    (IT-201-I, line 47a). Unlike the State's, the City table carries no New
    York AGI condition, and the City tax has no benefit recapture.

    The table is the State's METHOD — the schedule at each row's midpoint,
    rounded half up — so `_ny_table_tax` computes it. Its ROWS are the City's
    own, fetched into `state.nyc_tax.tax_table`: the bottom rows are $0-18 and
    $18-25 where the State's are $0-13 and $13-25, because each table starts
    its second row where its own first dollar of tax begins. Laid out on the
    State's rows, a $15 City return lands in a row that owes a dollar.

    The schedule letters are the State's (`state.bracket_schedule_by_status`):
    the City groups filers the same three ways, and its schedules carry the
    same PUBLISHED bases that `_ny_schedule_tax` requires.

    Returns:
        {"tax", "tax_exact", "line_47a_nyc_resident_tax", "schedule", "regime",
         "breakdown", "notes"} — `tax` is the WHOLE-DOLLAR line 47a.
    """
    filing_status = _ny_status(filing_status)
    if nyc_taxable_income is None or isinstance(nyc_taxable_income, bool) or \
            not isinstance(nyc_taxable_income, (int, float)):
        raise ValueError(
            f"nyc_taxable_income must be a number, got {nyc_taxable_income!r}")
    if not math.isfinite(nyc_taxable_income) or nyc_taxable_income < 0:
        raise ValueError(
            "nyc_taxable_income must be finite and not negative, got "
            f"{nyc_taxable_income!r}")
    schedule = (schedule_by_status or {}).get(filing_status)
    if schedule is None:
        raise ValueError(
            f"no schedule for filing status {filing_status!r} in "
            "state.bracket_schedule_by_status")
    table = (brackets or {}).get(schedule) or []
    if not table:
        raise ValueError(
            f"state.nyc_tax.brackets has no schedule {schedule!r}: the City's "
            "rate schedules were not fetched, and a City resident's tax must "
            "not default to zero. Re-run /tax-setup.")
    ceiling = (tax_table or {}).get("max_taxable_income")
    if ceiling is None:
        raise ValueError(
            "state.nyc_tax.tax_table.max_taxable_income is not fetched, so "
            "whether line 47 is in the TABLE region is unknown; the table and "
            "the schedule differ by up to a dollar either way. Re-run "
            "/tax-setup.")
    notes: "list[str]" = []
    if nyc_taxable_income < ceiling:
        exact, breakdown, notes = _ny_table_tax(
            nyc_taxable_income, brackets, schedule, tax_table,
            source="state.nyc_tax.tax_table")
        regime = "table"
    else:
        exact, breakdown = _ny_schedule_tax(nyc_taxable_income, table, schedule)
        regime = "schedule"
    line = _ny_round_half_up(exact)
    return {
        "tax": line,
        "tax_exact": round(exact, 4),
        "line_47a_nyc_resident_tax": line,
        "schedule": schedule,
        "regime": regime,
        "breakdown": breakdown,
        "notes": notes,
    }


def compute_mctmt(
    zone_1_net_earnings: float = 0.0,
    zone_2_net_earnings: float = 0.0,
    mctmt: "dict | None" = None,
) -> dict:
    """IT-201 lines 54a-54e — the metropolitan commuter transportation
    mobility tax on self-employment earnings.

    Split by GEOGRAPHY, not by income, and the threshold is a CLIFF applied
    PER ZONE. Once a zone's earnings exceed it the WHOLE zone amount is taxed,
    not the excess — the worked example in the instructions takes a filer with
    $40,000 in one zone and $70,000 in the other and taxes the entire $70,000
    while exempting the $40,000 completely. Subtracting the threshold, which is
    what the name of an ordinary exemption would suggest, under-states the tax
    by the threshold itself.

    The threshold is computed separately for each zone even on a joint return,
    so the two are never added together before the test.
    """
    mctmt = mctmt or {}
    threshold = mctmt.get("net_earnings_threshold_per_zone")
    if threshold is None:
        raise ValueError(
            "state.mctmt.net_earnings_threshold_per_zone is not fetched. It "
            "is a CLIFF and it changed for tax year 2026, so neither a "
            "default nor a stale figure is safe: one taxes a filer who owes "
            "nothing, the other exempts one who owes.")
    threshold = _amount(threshold, "MCTMT threshold")
    lines: "dict[str, float]" = {}
    total = 0.0
    notes: "list[str]" = []
    for zone, earnings, line_no in (("zone_1", zone_1_net_earnings, "54c"),
                                    ("zone_2", zone_2_net_earnings, "54d")):
        amount = _amount(earnings, f"{zone}_net_earnings")
        rate = (mctmt.get(zone) or {}).get("rate")
        lines[f"line_{'54a' if zone == 'zone_1' else '54b'}_{zone}_earnings"] \
            = round(amount, 2)
        if amount <= threshold:
            lines[f"line_{line_no}_{zone}_tax"] = 0.0
            continue
        if rate is None:
            raise ValueError(
                f"state.mctmt.{zone}.rate is not fetched and this return has "
                f"{amount:,.2f} of net earnings there. The two zones carry "
                "DIFFERENT rates, so neither the other zone's nor a default "
                "may stand in.")
        tax = round(amount * _amount(rate, f"{zone} rate"), 2)
        lines[f"line_{line_no}_{zone}_tax"] = tax
        total += tax
        notes.append(
            f"{zone.replace('_', ' ')}: earnings of {amount:,.2f} exceed the "
            f"threshold, so the WHOLE amount is taxed, not the excess.")
    lines["line_54e_total_mctmt"] = round(total, 2)
    return {"mctmt": round(total, 2), "lines": lines, "notes": notes}


# IT-201-I (2025) p.17, the Yonkers worksheet, lines b through i. Each is read
# off its OWN credit form, the one the worksheet names, and not off IT-201's
# credit lines: line 64 carries the City's child care credit beside the
# State's, where IT-216 line 14 is the State's alone. So these are a closed set
# keyed by what the worksheet names, not `_NY_REFUNDABLE_CREDIT_LINES`, and the
# child care key is named for the State's credit so the settlement's line-64
# amount cannot pass under the same name.
_YONKERS_WORKSHEET_CREDITS = (
    ("empire_state_child", "b", "Form IT-213, line 9"),
    ("real_property_tax", "c", "Form IT-214, line 20"),
    ("state_child_and_dependent_care", "d", "Form IT-216, line 14"),
    ("earned_income", "e",
     "Form IT-215, line 16, or line 17 where IT-215 says to use it"),
    ("noncustodial_parent_eic", "f",
     "Form IT-209, line 32, or the larger of lines 32 and 42"),
    ("college_tuition", "g", "Form IT-272, line 5 or 7"),
    ("nyc_school_tax", "h", "Form IT-201, lines 69 and 69a together"),
    ("other_tax_credits", "i", "Form IT-201-ATT, line 13"),
)


def _yonkers_rate(yonkers: "dict | None", key: str) -> float:
    rate = (yonkers or {}).get(key)
    if rate is None:
        raise ValueError(
            f"state.yonkers_tax.{key} is not fetched, and a Yonkers tax must "
            "not default to zero. Re-run /tax-setup.")
    rate = _amount(rate, f"state.yonkers_tax.{key}")
    if not 0 < rate < 1:
        raise ValueError(
            f"state.yonkers_tax.{key} must be a decimal rate between 0 and 1, "
            f"got {rate!r}")
    return rate


def _yonkers_worksheet(line_46: float, worksheet_credits: "dict | None",
                       star_reconciliation: "float | None",
                       yonkers: "dict | None",
                       college_tuition_deduction_claimed: bool) -> dict:
    """The worksheet, lines a to o, rounded once to the dollar as line 55
    enters it, with no residency question asked: Form Y-203 reuses it to cap a
    NONRESIDENT's tax at what the surcharge would have been.

    Line m can fall below zero when the credits exceed line 46, and a
    surcharge on a tax is then nothing rather than a negative tax. Line k, the
    STAR reconciliation amount, is SUBTRACTED from the credits: the worksheet
    keeps the STAR credit it reconciles from reducing the surcharge's base.

    Both inputs are REQUIRED, on the AA11 pattern: an omitted credit leaves
    the base too high and over-states the tax, and an omitted STAR amount
    under-states it with nothing to say so. `{}` and `0` are answers; `None`
    is not.
    """
    rate = _yonkers_rate(yonkers, "resident_surcharge_rate")
    if not isinstance(worksheet_credits, dict):
        raise ValueError(
            "pass worksheet_credits, every credit the Yonkers worksheet lists "
            "that this return claims, {} when it claims none: an omitted "
            "credit leaves the base too high and over-states the tax.")
    if star_reconciliation is None:
        raise ValueError(
            "pass star_reconciliation, Form IT-119 line 3, 0 when the return "
            "has none: the worksheet takes it back out of the credits, and "
            "omitting it under-states the tax.")
    credits = worksheet_credits
    known = {name: source for name, _, source in _YONKERS_WORKSHEET_CREDITS}
    unknown = sorted(set(credits) - set(known))
    if unknown:
        raise ValueError(
            f"{unknown} are not lines of the Yonkers worksheet. Known, each "
            "read off the form line the worksheet names: " + "; ".join(
                f"{name} ({source})" for name, source in known.items()))
    if college_tuition_deduction_claimed and credits.get("college_tuition"):
        raise ValueError(
            "worksheet line g is the college tuition CREDIT, \"if you elected "
            "to claim\" it, and this return took the IT-196 deduction "
            "instead. Subtracting the credit as well under-states the tax.")
    line_a = _amount(line_46, "line_46")
    lines = {"a_line_46": round(line_a, 2)}
    total = 0.0
    for name, letter, _ in _YONKERS_WORKSHEET_CREDITS:
        value = _amount(credits.get(name, 0.0), f"worksheet line {letter}")
        lines[f"{letter}_{name}"] = round(value, 2)
        total += value
    star = _amount(star_reconciliation, "star_reconciliation")
    # Line l is j less k with no floor, so a k above j would push line m past
    # line 46 and charge the surcharge on more than the whole State tax.
    if star > total:
        raise ValueError(
            f"the STAR reconciliation amount ({star:,.2f}, line k) exceeds "
            f"the credits on lines b to i ({total:,.2f}, line j) it is taken "
            "back out of. Check both against the forms they come from.")
    line_m = line_a - (total - star)
    lines.update({"j_credits": round(total, 2), "k_star_reconciliation":
                  round(star, 2), "m_base": round(line_m, 2), "n_rate": rate})
    # "Did you enter an amount that is more than 0 on line 46? If No, go to
    # line 56." With line k at most line j, line m never exceeds line 46, so
    # a return with no State tax already owes nothing here.
    exact = max(line_m, 0.0) * rate
    return {"exact": exact, "line": _ny_round_half_up(exact), "lines": lines}


def _yonkers_residency_guard(filing_status: str,
                             yonkers_resident_spouses: "int | None",
                             yonkers_resident_full_year: "bool | None") -> None:
    """Line 55 is for a WHOLE-year Yonkers resident, on the AA11 pattern.

    A joint return with one Yonkers resident spouse computes the surcharge as
    if the spouses had filed SEPARATE returns (special condition Y1), which is
    a second State tax computation this module does not do. A count of zero
    whole-year spouses on a household with a Yonkers resident is a couple who
    both lived there part of the year, and a part-year resident files
    IT-360.1 onto line 57. Assuming the whole year over-states the surcharge.
    """
    if filing_status == "mfj":
        if isinstance(yonkers_resident_spouses, bool) \
                or yonkers_resident_spouses not in (0, 1, 2):
            raise ValueError(
                "a joint return with a Yonkers resident must say how many "
                "spouses lived in Yonkers for the WHOLE year: pass "
                "yonkers_resident_spouses (0, 1 or 2) from "
                f"`flags.yonkers_resident_spouses`, got "
                f"{yonkers_resident_spouses!r}.")
        if yonkers_resident_spouses == 1:
            raise ValueError(
                "a joint return with only ONE Yonkers resident computes the "
                "surcharge on that spouse's tax as if they had filed "
                "separately (special condition Y1), which is not modeled. "
                "Taxing the joint State tax over-states it.")
        if yonkers_resident_spouses == 0:
            raise ValueError(
                "a joint return reached the Yonkers surcharge with NEITHER "
                "spouse a Yonkers resident for the whole year. A part-year "
                "Yonkers return (IT-360.1, IT-201 line 57) is not modeled.")
        return
    if not isinstance(yonkers_resident_full_year, bool):
        raise ValueError(
            "a return with yonkers_resident must say whether the taxpayer "
            "lived in Yonkers for the WHOLE year: pass yonkers_resident_full_"
            "year (True or False) from `flags.yonkers_resident_full_year`, "
            f"got {yonkers_resident_full_year!r}.")
    if not yonkers_resident_full_year:
        raise ValueError(
            "a Yonkers resident for only part of the year files a part-year "
            "Yonkers return (IT-360.1, IT-201 line 57), which is not modeled; "
            "a whole year's surcharge over-states it.")


def compute_yonkers_resident_surcharge(
    line_46: float,
    filing_status: str,
    yonkers: "dict | None",
    worksheet_credits: "dict | None" = None,
    star_reconciliation: "float | None" = None,
    yonkers_resident_spouses: "int | None" = None,
    yonkers_resident_full_year: "bool | None" = None,
    college_tuition_deduction_claimed: bool = False,
) -> dict:
    """IT-201 line 55 — the Yonkers resident income tax surcharge.

    A rate on the STATE TAX, not on income: the worksheet takes line 46, takes
    away the refundable credits it lists (net of the STAR reconciliation
    amount) and multiplies by the fetched rate, 16.75% for 2025. Nothing read
    `state.yonkers_tax` before this, and a Yonkers resident's return carried a
    surcharge of zero (AA12).

    `worksheet_credits` and `star_reconciliation` are required; see
    `_yonkers_worksheet`. Rounded once, to the dollar, as the form enters it.
    """
    filing_status = _ny_status(filing_status)
    _yonkers_residency_guard(filing_status, yonkers_resident_spouses,
                             yonkers_resident_full_year)
    sheet = _yonkers_worksheet(line_46, worksheet_credits,
                               star_reconciliation, yonkers,
                               college_tuition_deduction_claimed)
    line = sheet["line"]
    notes = []
    if sheet["lines"]["m_base"] < 0 < sheet["lines"]["a_line_46"]:
        notes.append("The worksheet's credits exceed line 46, so the "
                     "surcharge is zero rather than a negative tax.")
    return {"tax": line, "tax_exact": round(sheet["exact"], 4),
            "line_55_yonkers_resident_surcharge": line,
            "worksheet": sheet["lines"], "notes": notes}


def _yonkers_exclusion_rows(table) -> "list[tuple]":
    """Form Y-203's exclusion table, the twelve-month row: a Yonkers
    nonresident for the whole year. Omitting it OVER-states the tax.

    Checked as the fetched VALUES they are (AGENTS.md section 8): the rows
    climb from over 0, each starting where the one below ends, and only the
    TOP row is open. An open row lower down matches every amount above it and
    hands a $50,000 earner the largest exclusion, silently."""
    where = "state.yonkers_tax.nonresident_exclusion.table"
    if not table or not isinstance(table, list):
        raise ValueError(
            f"{where} is not fetched. Form Y-203 allows an exclusion before "
            "the rate, and taxing without it over-states the tax. Re-run "
            "/tax-setup.")
    rows: "list[tuple]" = []
    edge: "float | None" = 0.0
    for i, row in enumerate(table):
        at = f"{where}[{i}]"
        if not isinstance(row, dict):
            raise ValueError(
                f"{at} must be a row of over, not_over and exclusion, got "
                f"{type(row).__name__}")
        over = _amount(row.get("over"), f"{at}.over")
        upper = row.get("not_over")
        if (upper is None) != (i == len(table) - 1):
            raise ValueError(
                f"{at}: only the TOP row is open (not_over null); an open row "
                "lower down matches every amount above it.")
        if upper is not None:
            upper = _amount(upper, f"{at}.not_over")
        if over != edge or (upper is not None and upper <= over):
            raise ValueError(
                f"{at} runs from {over} to {upper}; the rows must climb from "
                "over 0, each starting where the one below ends.")
        rows.append((upper, _amount(row.get("exclusion"), f"{at}.exclusion")))
        edge = upper
    return rows


def _y203_form(earner: dict, rate: float, rows: "list[tuple]") -> dict:
    """Form Y-203, lines 1 to 6 before the limitation.

    In WHOLE DOLLARS, as the form prints every line, and the exclusion is read
    off line 3b AS ENTERED: $10,000.40 of wages is 10,000, inside the $3,000
    row, where the cents would carry it into the $2,000 one."""
    if not isinstance(earner, dict):
        raise ValueError(
            "pass one Form Y-203 as a dict of wages, self_employment and "
            f"startup_ny_wages, got {type(earner).__name__}")
    unknown = sorted(set(earner) - {"wages", "self_employment",
                                    "startup_ny_wages"})
    if unknown:
        raise ValueError(
            f"{unknown} are not Form Y-203 inputs. Pass wages and "
            "self_employment already ALLOCATED to Yonkers (Schedules A and C) "
            "and startup_ny_wages.")
    wages = _ny_round_half_up(_amount(earner.get("wages", 0.0),
                                      "Y-203 line 1 wages"))
    # "If a loss, enter 0 on line 2."
    self_employed = _ny_round_half_up(max(_amount(
        earner.get("self_employment", 0.0), "Y-203 line 2",
        allow_negative=True), 0.0))
    startup = _ny_round_half_up(_amount(earner.get("startup_ny_wages", 0.0),
                                        "Y-203 line 3a"))
    if startup > wages:
        raise ValueError(
            f"START-UP NY wages of {startup:,} exceed the Yonkers wages of "
            f"{wages:,} they are part of.")
    line_3 = wages + self_employed
    line_3b = line_3 - startup
    # Rows climb from over 0 with an open top, so the first whose top reaches
    # line 3b is its row.
    exclusion = 0.0 if line_3b <= 0 else next(
        excl for upper, excl in rows if upper is None or line_3b <= upper)
    line_5 = max(line_3b - exclusion, 0.0)
    return {"line_1": wages, "line_2": self_employed, "line_3": line_3,
            "line_3a": startup, "line_3b": line_3b,
            "line_4": round(exclusion, 2), "line_5": round(line_5, 2),
            "line_6_before_limit": _ny_round_half_up(line_5 * rate)}


def compute_yonkers_nonresident_earnings_tax(
    earner: dict,
    filing_status: str,
    yonkers: "dict | None",
    line_46: float,
    worksheet_credits: "dict | None" = None,
    star_reconciliation: "float | None" = None,
    yonkers_resident: "bool | None" = None,
    college_tuition_deduction_claimed: bool = False,
) -> dict:
    """IT-201 line 56 — the Yonkers nonresident earnings tax, Form Y-203.

    A rate on wages and self-employment earnings ALLOCATED to Yonkers, after
    an exclusion that depends on their size.

    **Limited to the resident surcharge**: "As a Yonkers nonresident, you do
    not have to pay a higher tax than you would if you were a Yonkers
    resident ... Enter the Yonkers resident income tax surcharge on line 6 of
    Form Y-203." The limit is the line-55 worksheet on this return, which is
    why it takes the worksheet's inputs, and line 6 carries the LIMITED tax.

    **Not on a joint return.** Y-203-I figures the limit "as a Yonkers
    resident" under the IT-201 instructions, and those figure a joint return
    with one Yonkers spouse "as if you had filed separate federal returns"
    (special condition Y1). Capping at the joint surcharge can over-state a
    lower-earning spouse's tax under that reading, and the separate
    computation is not modeled, so a joint return refuses.

    Only for a household nobody in which lived in Yonkers: a part-year
    resident prorates the exclusion by month and files IT-360.1 as well.
    """
    filing_status = _ny_status(filing_status)
    if not isinstance(yonkers_resident, bool):
        raise ValueError(
            "pass yonkers_resident from `flags.yonkers_resident`: whether "
            "anyone on this return lived in Yonkers decides whether Form "
            f"Y-203 applies at all, got {yonkers_resident!r}.")
    if yonkers_resident:
        raise ValueError(
            "this return has a Yonkers resident AND Yonkers nonresident "
            "earnings: a part-year resident, or a joint return with one "
            "resident spouse (special condition Y1). Neither is modeled.")
    if filing_status == "mfj":
        raise ValueError(
            "Form Y-203 on a joint return is not modeled: its limitation can "
            "be read as each spouse's surcharge figured as if they had filed "
            "separately (special condition Y1), which is not computed, and "
            "the joint surcharge can over-state it. Name it on "
            "meta.limitations.")
    rate = _yonkers_rate(yonkers, "nonresident_earnings_rate")
    rows = _yonkers_exclusion_rows(
        ((yonkers or {}).get("nonresident_exclusion") or {}).get("table"))
    form = _y203_form(earner, rate, rows)
    limit = _yonkers_worksheet(line_46, worksheet_credits,
                               star_reconciliation, yonkers,
                               college_tuition_deduction_claimed)["line"]
    form["line_6"] = min(form["line_6_before_limit"], limit)
    notes = []
    if form["line_6"] < form["line_6_before_limit"]:
        notes.append(
            f"Form Y-203's tax of {form['line_6_before_limit']:,} is limited "
            f"to the resident surcharge of {limit:,}, which goes on line 6; "
            "submit the calculation with the form as a separate schedule.")
    return {"tax": form["line_6"],
            "line_56_yonkers_nonresident_earnings_tax": form["line_6"],
            "form": form, "resident_limit": limit, "notes": notes}


# Line 58's three household-governed taxes: (profile flag, the argument
# compute_city_and_yonkers_total takes — and `compute_<argument>` produces —,
# the result.json block and field it is written to, the IT-201 line). ONE table,
# read by the function below and by the tie-out gate, which checks that a
# flagged line reached result.json as a computed figure (AA29).
NY_HOUSEHOLD_LINES = (
    ("nyc_resident", "nyc_resident_tax", "nyc",
     "line_47a_nyc_resident_tax", "47a"),
    ("yonkers_resident", "yonkers_resident_surcharge", "yonkers",
     "line_55_yonkers_resident_surcharge", "55"),
    ("yonkers_nonresident_earnings", "yonkers_nonresident_earnings_tax",
     "yonkers", "line_56_yonkers_nonresident_earnings_tax", "56"),
)


def ny_household_refusals(filing_status, flags: dict) -> "list[tuple[str, str]]":
    """The household lines this module REFUSES, from ANSWERED flags (AA30).

    `(flag, why)` for each: a part-year City or Yonkers resident (IT-360.1,
    IT-201 lines 50 and 57), a joint return with one or no whole-year resident
    spouse (line 51, special condition Y1), and Form Y-203 on a joint return or
    beside a Yonkers residence. The capability gate reads it to block at Step
    0.7 what `compute_nyc_taxable_income`, `_yonkers_residency_guard` and
    `compute_yonkers_nonresident_earnings_tax` refuse at Step 10, and the
    tie-out to exempt an acknowledged one. An UNANSWERED count or full-year
    answer is not here: each function refuses it by name.
    """
    joint = _ny_status(filing_status) == "mfj"
    out = []
    for flag, spouses, full_year, place, line in (
            ("nyc_resident", "nyc_resident_spouses", "nyc_resident_full_year",
             "New York City", "IT-201 line 50 or 51"),
            ("yonkers_resident", "yonkers_resident_spouses",
             "yonkers_resident_full_year", "Yonkers", "IT-201 line 57")):
        if flags.get(flag) is not True:
            continue
        count = flags.get(spouses)
        if joint and not isinstance(count, bool) and count in (0, 1):
            out.append((flag, f"{place}: a joint return with {count} "
                              f"whole-year resident spouse(s) ({line}, "
                              "IT-360.1)"))
        elif not joint and flags.get(full_year) is False:
            out.append((flag, f"{place}: a resident for only part of the "
                              f"year ({line}, IT-360.1)"))
    if flags.get("yonkers_nonresident_earnings") is True and (
            joint or flags.get("yonkers_resident") is True):
        out.append(("yonkers_nonresident_earnings",
                    "Yonkers nonresident earnings: Form Y-203 on a joint "
                    "return, or beside a Yonkers residence (special condition "
                    "Y1)"))
    return out


def _flagged_tax(amount, name: str, flag, flag_name: str, line: str) -> float:
    """One of line 58's flag-governed taxes, refused where the household flag
    and the amount disagree. See compute_city_and_yonkers_total."""
    if not isinstance(flag, bool):
        raise ValueError(f"{flag_name} must be True or False, got {flag!r}")
    if amount is None:
        if flag:
            raise ValueError(
                f"flags.{flag_name} is set and {name} was not passed: line "
                f"{line} comes from compute_{name}, and a line nobody computed "
                "is not "
                "a zero. Run it and pass its tax, or correct the flag.")
        return 0.0
    value = _amount(amount, name)
    if value and not flag:
        raise ValueError(
            f"{name} of {value:,.2f} was passed, but flags.{flag_name} is not "
            f"set: either the household owes line {line} and the flag is "
            "wrong, or the amount belongs to someone else.")
    return value


def compute_city_and_yonkers_total(
    nyc_resident_tax: "float | None" = None,
    nyc_household_credit: float = 0.0,
    other_nyc_taxes: float = 0.0,
    nyc_nonrefundable_credits: float = 0.0,
    mctmt: float = 0.0,
    yonkers_resident_surcharge: "float | None" = None,
    yonkers_nonresident_earnings_tax: "float | None" = None,
    *,
    nyc_resident: bool,
    yonkers_resident: bool,
    yonkers_nonresident_earnings: bool,
) -> dict:
    """IT-201 lines 47a through 58 — the city block's own two credit caps.

    The same shape as the state side, twice over, and with the same trap at the
    end. The household credit is capped at the city tax (line 49: "if line 48
    is more than line 47a, enter 0"), then the other city taxes are added, then
    the city's nonrefundable credits are capped against THAT total (line 54).

    And line 58 adds lines 54 and **54e through 57** — so the MCTMT, the
    Yonkers resident surcharge and the Yonkers nonresident earnings tax all
    come AFTER both caps. No New York City credit offsets any of them. This is
    the second appearance of the pattern line 45 sets on the state side, and it
    is the same mistake to net everything first: it would let a city credit
    absorb a tax the form never lets it reach.

    Part-year city and Yonkers lines (50 and 57) are not here. This module is
    full-year residents, and a part-year figure belongs to IT-360.1.

    **The three household flags are required, and the three taxes they govern
    default to None, not zero (AA15).** Each of lines 47a, 55 and 56 comes from
    its own function, and a default of zero made a line nobody computed read
    the same as a computed zero: an under-statement with nothing to say so. It
    hid line 47a until AA13 and lines 55-56 until AA12. So a flagged tax that
    was not passed is refused, and so is a tax passed for a household the
    profile says does not owe it. An unflagged tax left out is a zero, which is
    what the flag asserts.
    """
    passed = {"nyc_resident_tax": nyc_resident_tax,
              "yonkers_resident_surcharge": yonkers_resident_surcharge,
              "yonkers_nonresident_earnings_tax":
                  yonkers_nonresident_earnings_tax}
    flags = {"nyc_resident": nyc_resident,
             "yonkers_resident": yonkers_resident,
             "yonkers_nonresident_earnings": yonkers_nonresident_earnings}
    city_tax, yonkers_res, yonkers_non = (
        _flagged_tax(passed[arg], arg, flags[flag], flag, line)
        for flag, arg, _block, _field, line in NY_HOUSEHOLD_LINES)
    household = _amount(nyc_household_credit, "nyc_household_credit")
    other_city = _amount(other_nyc_taxes, "other_nyc_taxes")
    city_credits = _amount(nyc_nonrefundable_credits,
                              "nyc_nonrefundable_credits")
    mobility = _amount(mctmt, "mctmt")

    notes: "list[str]" = []
    line_49 = round(max(city_tax - household, 0.0), 2)
    line_52 = round(line_49 + other_city, 2)
    line_54 = round(max(line_52 - city_credits, 0.0), 2)
    if household > city_tax:
        notes.append(
            f"The New York City household credit of {household:,.2f} exceeds "
            f"the city tax of {city_tax:,.2f}; the excess is lost.")
    if city_credits > line_52:
        notes.append(
            f"New York City nonrefundable credits of {city_credits:,.2f} "
            f"exceed the city tax of {line_52:,.2f}; the excess is lost.")
    after = mobility + yonkers_res + yonkers_non
    if after and (household > city_tax or city_credits > line_52):
        notes.append(
            "The MCTMT and the Yonkers taxes are added AFTER both city credit "
            "caps, so the lost credit above does not offset them either.")
    total = round(line_54 + after, 2)
    return {
        "line_49_after_household_credit": line_49,
        "line_52_city_tax_before_credits": line_52,
        "line_54_after_city_credits": line_54,
        # The SAME spelling `compute_mctmt` uses for its own total. Two names
        # for one line means a reader tying out line 54e has to know which
        # function produced the dict in front of them.
        "line_54e_total_mctmt": round(mobility, 2),
        "line_55_yonkers_resident_surcharge": round(yonkers_res, 2),
        "line_56_yonkers_nonresident_earnings_tax": round(yonkers_non, 2),
        "line_58_total_city_and_yonkers": total,
        "notes": notes,
    }


# The two PTET credits an owner may claim on Form IT-653 and the IT-225
# addition code each one REQUIRES. They are pairs, and the pairing is an exact
# EQUALITY rather than a related adjustment: A-219 is "the amount of the credit
# claimed on Form IT-653, LINE 1" and A-222 is the amount on LINE 2.
_NY_PTET_CREDIT_ADDBACKS = (
    ("nys", "A-219", 1),
    ("nyc", "A-222", 2),
)


def compute_ny_ptet_owner(
    nys_ptet_credit: float = 0.0,
    nyc_ptet_credit: float = 0.0,
    it225_additions: "list[dict] | None" = None,
) -> dict:
    """Form IT-653 — the OWNER side of the pass-through entity tax.

    The entity side is already modelled elsewhere; this is what the partner or
    shareholder does on their own return, and it is two things that must happen
    together:

    - The credit is claimed on IT-653 and is REFUNDABLE — "if the amount of the
      PTET credit allowable for any taxable year exceeds the tax due for the
      year, the excess is treated as an overpayment, to be credited or refunded"
      — so it belongs with the payments, not with the capped credits.
    - An IT-225 ADDITION of exactly the same amount is required, because the
      entity deducted the tax federally and New York does not allow the owner
      the benefit twice.

    **Claiming the credit without the addition under-states New York tax by the
    whole of the credit**, and nothing else on the return would notice: the two
    live on different forms, and a caller assembling IT-225 by hand has no
    reason to connect them. So the addbacks are DERIVED here from the credits
    and checked against whatever the caller already assembled.

    There are TWO of each, and they do not combine. The State credit on IT-653
    line 1 pairs with A-219 and the City credit on line 2 with A-222; adding
    the two credits back under one code is a different return from the one the
    form describes, and it is the mistake a single "PTET addback" invites.
    """
    claimed = {"nys": _amount(nys_ptet_credit, "nys_ptet_credit"),
               "nyc": _amount(nyc_ptet_credit, "nyc_ptet_credit")}
    # NORMALISED by `compute_ny_modifications`, not by hand. The loop that
    # stood here did the strip and upper itself and SILENTLY DROPPED an entry
    # whose shape it did not recognise: a `{"code": None}` addition was
    # skipped, the A-219 pairing check never ran against it, and the caller was
    # told to add an add-back that was already there. Adding it twice
    # over-states the tax by the whole of the credit.
    #
    # The shared function also refuses what this loop accepted — a duplicate
    # code, and a SUBTRACTION-prefixed code filed on the addition side — and it
    # is the same question: "given this IT-225 addition side, what amount sits
    # under each code?"
    existing = {entry["code"]: entry["amount"]
                for entry in compute_ny_modifications(
                    it225_additions, "addition")["entries"]}

    required = []
    notes: "list[str]" = []
    for which, code, line_no in _NY_PTET_CREDIT_ADDBACKS:
        amount = claimed[which]
        if not amount:
            # A code present with no credit behind it adds tax the taxpayer
            # does not owe, which is the other direction and just as wrong.
            if existing.get(code):
                raise ValueError(
                    f"IT-225 code {code} adds back a PTET deduction, but no "
                    f"{which.upper()} PTET credit is claimed on IT-653 line "
                    f"{line_no}. The addback exists only to offset the credit.")
            continue
        required.append({"code": code, "amount": round(amount, 2)})
        if code in existing:
            stated = existing[code]
            if stated is None or abs(_amount(
                    stated, f"IT-225 {code} amount") - amount) > 0.005:
                raise ValueError(
                    f"IT-225 code {code} is {stated!r} but the {which.upper()} "
                    f"PTET credit claimed on IT-653 line {line_no} is "
                    f"{amount:,.2f}. The addback is that amount EXACTLY — not "
                    "the entity's tax, not a share of it.")
        else:
            notes.append(
                f"IT-225 addition {code} of {amount:,.2f} is REQUIRED against "
                f"the {which.upper()} PTET credit on IT-653 line {line_no}. "
                "Claiming the credit without it under-states the tax by the "
                "whole of the credit.")
    return {
        "refundable_credit": round(claimed["nys"] + claimed["nyc"], 2),
        "required_it225_additions": required,
        "notes": notes,
    }


def compute_ny_required_annual_payment(
    current_year_tax: float,
    prior_year_tax: "float | None" = None,
    prior_year_nyagi: "float | None" = None,
    filing_status: str = "single",
    safe_harbor: "dict | None" = None,
) -> dict:
    """Form IT-2105 — the required annual payment, and what each instalment is.

    New York's §685(c) safe harbour has the same SHAPE as federal §6654 and
    deliberately none of its constants: the thresholds are keyed to NEW YORK
    adjusted gross income, not federal, so a taxpayer with IT-225 modifications
    is measured against the wrong figure if the federal one is handed in.

    The instalments are EQUAL QUARTERS — April, June, September and the
    following January — which is not California's 30/40/0/30, and one voucher
    covers the State, the City, Yonkers and the MCTMT together. A schedule
    borrowed from the California branch would tell a New York filer to skip
    September.

    Without a prior year the current-year share is the only available floor,
    and that is stated rather than silently assumed: a first-year filer has no
    prior-year safe harbour, and a taxpayer who simply has not supplied one is
    in a different position that the caller has to resolve.
    """
    filing_status = _ny_status(filing_status)
    safe_harbor = safe_harbor or {}
    current_share = safe_harbor.get("current_year_share")
    prior_share = safe_harbor.get("prior_year_share")
    high_share = safe_harbor.get("prior_year_share_high_income")
    threshold = safe_harbor.get("high_income_nyagi_threshold")
    threshold_mfs = safe_harbor.get("high_income_nyagi_threshold_mfs")
    missing = [n for n, v in (("current_year_share", current_share),
                              ("prior_year_share", prior_share),
                              ("prior_year_share_high_income", high_share),
                              ("high_income_nyagi_threshold", threshold),
                              ("high_income_nyagi_threshold_mfs",
                               threshold_mfs)) if v is None]
    if missing:
        raise ValueError(
            "state.estimated_tax_safe_harbor is missing " + ", ".join(missing) +
            ". Falling back to the FEDERAL figures would measure a New York "
            "safe harbour against the wrong thresholds — they are keyed to NY "
            "adjusted gross income and may diverge.")

    tax = _amount(current_year_tax, "current_year_tax")
    notes: "list[str]" = []
    current_floor = round(tax * _amount(current_share, "current_year_share"),
                          2)
    options = {"current_year": current_floor}

    if prior_year_tax is None:
        notes.append(
            "No prior-year tax supplied, so the only floor available is the "
            "current-year share. A first-year New York filer genuinely has no "
            "prior-year safe harbour; a taxpayer who simply has not supplied "
            "one may have a lower requirement.")
    else:
        prior = _amount(prior_year_tax, "prior_year_tax")
        share = _amount(prior_share, "prior_year_share")
        applicable = (threshold_mfs if filing_status == "mfs" else threshold)
        if prior_year_nyagi is None:
            raise ValueError(
                "a prior-year tax was supplied without the prior-year NEW YORK "
                "adjusted gross income, and the higher share applies above a "
                "NYAGI threshold. Assuming the lower one under-states the "
                "required payment for exactly the taxpayers it is aimed at.")
        nyagi = _amount(prior_year_nyagi, "prior_year_nyagi",
                           allow_negative=True)
        if nyagi > _amount(applicable, "high income threshold"):
            share = _amount(high_share, "prior_year_share_high_income")
            notes.append(
                "Prior-year New York AGI is above the threshold, so the higher "
                "prior-year share applies.")
        options["prior_year"] = round(prior * share, 2)

    required = round(min(options.values()), 2)
    # "Each required installment is 25% of the required annual payment" — but a
    # quarter of an odd number of cents does not divide, and four figures that
    # do not sum to the total are a small lie in a schedule somebody pays from.
    #
    # `_split_remainder_last` and NOT a hand-rolled quarter. The first version
    # here WAS hand-rolled and reproduced the exact defect that helper's
    # docstring records: a required payment of $0.02 allocated three one-cent
    # shares against two cents and printed a fourth instalment of -$0.01. A
    # negative voucher is not a rounding artefact to a person reading a payment
    # schedule. The cap inside the helper is what makes that impossible.
    instalments = _split_remainder_last(required, [1.0] * 4)
    return {
        "required_annual_payment": required,
        "options": options,
        "instalments": instalments,
        "schedule": "equal_quarters",
        "notes": notes,
    }


def compute_ny_itemized(
    itemized_before_adjustment: float,
    charitable_contributions: float,
    nyagi: float,
    filing_status: str,
    reduction: "dict | None" = None,
    charitable_limit: "dict | None" = None,
    college_tuition_deduction: float = 0.0,
    *,
    standard_deduction: float,
    mfs_spouse_itemizes: bool | None = None,
) -> dict:
    """Form IT-196 lines 46 through 49 — the §615 adjustment and the election.

    This is the part of New York's itemized deduction with NO federal analogue.
    Four regimes by NYAGI, mutually exclusive, and the top two do not reduce the
    deduction at all — they REPLACE it with a share of the charitable
    contributions:

    - at or below the first threshold, no adjustment;
    - up to the second threshold, a quarter of the deduction phased in over a
      band above a PER-STATUS threshold;
    - up to the flat threshold, that whole quarter PLUS another quarter phased
      in over its own band above the second threshold;
    - up to the charitable-only threshold, a flat half;
    - above it, the deduction BECOMES a share of the charitable contributions,
      and the adjustment line is left blank.

    Applying a reduction AND the charitable regime double-limits, which
    over-states the tax; skipping the reduction under-states it. Neither is a
    rounding error — the middle bands take a quarter and then a half of the
    whole deduction — so an unfetched figure REFUSES rather than defaulting.

    `itemized_before_adjustment` is IT-196 line 45 and is the CALLER's: building
    it from source figures is a separate piece of work, because New York
    computes it on federal rules AS THEY STOOD BEFORE THE TCJA — the 2%
    miscellaneous floor is live again, the mortgage limit is the pre-TCJA one,
    and state and local INCOME taxes are adjusted back out. What this function
    will not do is pretend a federal Schedule A total is that figure.

    `charitable_contributions` is line 19, and it is load-bearing only in the
    top two regimes — where it is the ENTIRE deduction. A caller that passes
    zero there because it was not tracking the split silently zeroes the
    deduction for a taxpayer above the threshold, so it is required, not
    defaulted, when the return reaches that band.

    Returns:
        {"line_46_adjustment", "line_47_after_adjustment", "line_48_college",
         "line_49_total", "deduction", "use_itemized", "regime", "notes"}
    """
    filing_status = _ny_status(filing_status)
    reduction = reduction or {}
    charitable_limit = charitable_limit or {}
    notes: "list[str]" = []

    line_45 = _amount(itemized_before_adjustment,
                         "itemized_before_adjustment")
    charitable = _amount(charitable_contributions,
                            "charitable_contributions")
    college = _amount(college_tuition_deduction,
                         "college_tuition_deduction")
    agi = _amount(nyagi, "nyagi", allow_negative=True)

    thresholds = reduction.get("first_threshold") or {}
    first_threshold = thresholds.get(filing_status)
    band = reduction.get("first_band")
    rate = reduction.get("first_rate")
    second_threshold = reduction.get("second_threshold")
    second_band = reduction.get("second_band")
    second_rate = reduction.get("second_rate")
    flat_threshold = reduction.get("flat_threshold")
    flat_rate = reduction.get("flat_rate")
    charitable_only = reduction.get("charitable_only_threshold")

    missing = [name for name, value in (
        ("first_threshold[%s]" % filing_status, first_threshold),
        ("first_band", band), ("first_rate", rate),
        ("second_threshold", second_threshold),
        ("second_band", second_band), ("second_rate", second_rate),
        ("flat_threshold", flat_threshold), ("flat_rate", flat_rate),
        ("charitable_only_threshold", charitable_only),
    ) if value is None]
    if missing:
        raise ValueError(
            "state.itemized_reduction is missing " + ", ".join(missing) +
            ". The middle bands take a QUARTER and then a HALF of the whole "
            "deduction, so a default here is not a rounding error — skipping "
            "the reduction under-states the tax. Re-run /tax-setup.")

    tiers = sorted(charitable_limit.get("tiers") or [],
                   key=lambda t: t["nyagi_over"])
    if not tiers:
        raise ValueError(
            "state.charitable_limit.tiers is not fetched. Above the "
            "charitable-only threshold the deduction BECOMES a share of the "
            "charitable contributions, and there is no structural default for "
            "that share.")
    # The two figures describe ONE boundary — where §615(f)'s reduction stops
    # and §615(g)'s replacement begins. A half-fetch that moves one and not the
    # other opens a band where both apply, or neither.
    if tiers[0]["nyagi_over"] != charitable_only:
        raise ValueError(
            f"charitable_only_threshold is {charitable_only:,} but the lowest "
            f"charitable tier starts at {tiers[0]['nyagi_over']:,}. They are "
            "the same boundary; a gap between them leaves a band where the "
            "reduction and the replacement both apply, or neither does.")

    if agi > charitable_only:
        share = tiers[0]["share"]
        for tier in tiers:
            if agi > tier["nyagi_over"]:
                share = tier["share"]
        if not charitable and line_45:
            raise ValueError(
                "New York AGI is above the charitable-only threshold, where "
                "the itemized deduction BECOMES a share of the charitable "
                "contributions — but charitable_contributions is zero against "
                "a non-zero line 45. A caller that was not tracking the split "
                "would silently zero the deduction.")
        line_47 = round(charitable * share, 2)
        line_46 = 0.0
        regime = "charitable_only"
        notes.append(
            f"New York AGI is above the charitable-only threshold: the "
            f"itemized deduction is {share:.0%} of the charitable "
            "contributions, and every other itemized amount is disregarded.")
    elif agi > flat_threshold:
        line_46 = round(line_45 * flat_rate, 2)
        line_47 = round(line_45 - line_46, 2)
        regime = "flat"
    elif agi > second_threshold:
        first_part = line_45 * rate
        fraction = round(min(agi - second_threshold, second_band)
                         / second_band, 4)
        line_46 = round(first_part + line_45 * second_rate * fraction, 2)
        line_47 = round(line_45 - line_46, 2)
        regime = "second_reduction"
    elif agi > first_threshold:
        fraction = round(min(agi - first_threshold, band) / band, 4)
        line_46 = round(line_45 * rate * fraction, 2)
        line_47 = round(line_45 - line_46, 2)
        regime = "first_reduction"
    else:
        line_46 = 0.0
        line_47 = line_45
        regime = "unreduced"

    line_49 = round(line_47 + college, 2)
    # Q7, ruled 2026-09-13. `standard_deduction` was optional, and omitting it
    # left `deduction` holding the ITEMIZED total — a number that READS as
    # elected to every caller, which is worse than CA's `None`: there was no
    # value to test for. When the NY standard deduction was the larger of the
    # two that UNDER-stated the deduction and over-stated the tax. Required
    # now, so `deduction` is an election in every return from here.
    standard = _amount(standard_deduction, "standard_deduction")
    use_itemized = line_49 > standard
    # "If you are married and filing separate returns, BOTH of you must take
    # the standard deduction unless BOTH of you elect to itemize." One
    # spouse's choice is not their own to make — and the coupling binds in
    # BOTH directions. Only the first was implemented: a filer whose spouse
    # itemizes still took the standard deduction whenever it was larger, which
    # is the election the instruction forbids and OVER-states the deduction.
    # `compute_ca_itemized` had forced only the spouse-ITEMIZES half, which
    # is what this comment used to cite as precedent — and citing the half
    # that was there is how CA's missing half went unswept until 2026-09-14.
    # Both functions bind both directions now.
    if filing_status == "mfs":
        # UNKNOWN is not "no", and the same reasoning as `compute_ca_itemized`
        # applies here for the same reason: once the coupling binds both ways,
        # a default False asserts something about the SPOUSE's return. Omitted,
        # it bars this filer from itemizing when their spouse in fact itemizes
        # — over-stating the deduction by up to the whole standard deduction on
        # the commonest separate-return shape. CA was found first; a finding's
        # scope is where the defect lives.
        if mfs_spouse_itemizes is None:
            raise ValueError(
                "a New York return filed separately needs "
                "mfs_spouse_itemizes — BOTH spouses must itemize or NEITHER "
                "may, so the spouse's election decides this one and "
                "defaulting it silently picks a side. tax-setup records the "
                "answer; pass True or False.")
        if mfs_spouse_itemizes and not use_itemized:
            notes.append(
                "Married filing separately: the New York standard deduction "
                "is larger, but the spouse is ITEMIZING and BOTH must elect "
                "the same way. The itemized deduction is taken here, and it "
                "is the smaller of the two.")
            use_itemized = True
        elif not mfs_spouse_itemizes:
            if use_itemized:
                notes.append(
                    "Married filing separately: the New York itemized "
                    "deduction is larger, but BOTH spouses must elect it or "
                    "NEITHER may. The standard deduction is taken here because "
                    "the spouse is not itemizing.")
            use_itemized = False
    deduction = line_49 if use_itemized else standard
    return {
        "line_46_adjustment": line_46,
        "line_47_after_adjustment": line_47,
        "line_48_college": round(college, 2),
        "line_49_total": line_49,
        "deduction": round(deduction, 2),
        "use_itemized": use_itemized,
        "regime": regime,
        "notes": notes,
    }
