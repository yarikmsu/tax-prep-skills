#!/usr/bin/env python3
"""Primitives the federal side and a state module both need.

Its own file so that neither has to import the other. The package `__init__`
re-exports every state module's public names, so a state module importing a
helper FROM `__init__` would be a cycle — and the fix that dodges it (a lazy
import inside each function) makes correctness depend on which module is
imported first.

WHAT BELONGS HERE is a primitive with no form of its own: a coercion, a
validation, an allocation, a statutory RATE that more than one jurisdiction
conforms to. A function that computes a line of a return does not, however
many callers it has — that line belongs to a form, and the form belongs to a
jurisdiction.

New York needed exactly one of these. California needed nine, which is the
difference between a state whose law is self-contained and one the federal
Schedule A is threaded through: `compute_ca_itemized` starts from the federal
itemized deduction and re-computes it under R&TC §17077, so it reaches for the
same amount-parsers and the same two statutory floors the federal side does.
"""

import math
from decimal import ROUND_HALF_UP, Decimal

import agents.lib  # noqa: F401 — the host-interpreter guard, see agents/lib/__init__.py


# TWO AUTHORITIES THAT AGREE, not one rule with two readers. The federal floor
# is §213(a) at 7.5% of AGI (permanent since 2021) and the federal appreciated-
# property charitable cap is §170(b)(1)(C) at 30%; California arrives at the
# same two figures through R&TC conformity, and that is a coincidence of the
# current law rather than a definition. It has already come apart once —
# §213(a) sat at 10% through the mid-2010s while California stayed at 7.5%.
#
# So if either federal figure MOVES, this is not a one-line edit: split the
# constant, give each its own citation, and expect the CA tests that read it to
# go red in a way that reads like a stale expected value and is not. They are
# shared here because both sides really do read one figure today, and a
# duplicate would drift on the ordinary year when nothing changes.
#
# CA's own NON-conforming figures — the $1M mortgage limit it kept and the 50%
# cash charitable cap it never raised to the federal 60% — are in `state/ca.py`,
# because those are California's law and not a shared primitive.
_SCHED_A_MEDICAL_FLOOR = 0.075
_CHARITY_APPRECIATED_AGI_CAP = 0.30


def _medical_deductible(expenses: float, agi: float) -> float:
    """Medical expenses over the floor, a share of AGI floored at zero — an
    unclamped negative AGI would turn the floor into a bonus. One copy for the
    federal and CA Schedule A and the planning tools' medical gate, which
    held three."""
    return round(max(0.0, expenses - _SCHED_A_MEDICAL_FLOOR * max(0.0, agi)), 2)

_CANONICAL_FILING_STATUSES = {"SINGLE", "MFJ", "MFS", "HOH", "QSS"}


def _round_half_up(value: "float | Decimal", places: int = 0) -> Decimal:
    """Round HALF-UP (a tie away from zero) to `places` decimals, the way the
    forms say to: New York's "increase amounts from 50 to 99 cents to the next
    dollar" (IT-201-I) and North Carolina's "round to four decimal places"
    (D-401 p.22, D-400TC line 3). Python's `round` is HALF-EVEN on the BINARY
    value, so it turns 2,416.50 into 2,416 and 9 / 4,000 into 0.0022 where the
    forms say 2,417 and 0.0023 — in the direction that under-states.

    A float is read as the decimal it PRINTS as (`repr`), not its binary
    expansion, so 0.00225 is a tie; a caller holding an exact quotient passes
    it as a `Decimal` so that no float ever stands between the operands and the
    tie. Returns a `Decimal`: each caller converts to what its form carries."""
    exact = value if isinstance(value, Decimal) else Decimal(repr(value))
    return exact.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)

def _split_remainder_last(shortfall: float, weights: "list[float]") -> "list[float]":
    """Split `shortfall` proportionally to `weights`, rounding remainder on
    the LAST entry carrying positive weight — so the column always sums to
    `shortfall` exactly, rather than a cent short from reusing one rounded
    per-item share (three open quarters splitting $100.00 evenly round to
    $33.33 x 3 = $99.99). Shared by the federal schedule (uniform weight,
    `weights = [1.0] * n`) and the CA 30/40/0/30 one, which used to
    duplicate this allocation with slightly different shapes — CA's zero-
    weight Q3 slot is why the remainder goes on the last POSITIVE-weight
    entry rather than simply the last one.

    `weights` must sum to a positive total and have at least one positive
    entry — both callers guarantee this before reaching here (Q4 always
    carries positive weight in both schedules), and both floor `shortfall` at
    zero before calling (each returns all-zero rows at `shortfall <= 0`).

    NO SHARE MAY GO NEGATIVE, and the remainder-on-the-last design is exactly
    what could produce one. Each earlier share rounds INDEPENDENTLY, so on a
    shortfall smaller than a cent per open quarter every one of them rounds UP
    and their sum overshoots the total the last entry is then given what is
    left of: `_split_remainder_last(0.02, [1, 1, 1, 1])` allocated three
    one-cent shares against two cents and printed a Q4 voucher of **-$0.01**,
    in both report paths. So the running allocation is capped at what actually
    remains, which cannot over-allocate and therefore cannot drive the
    remainder below zero.

    The cap is a NO-OP wherever the shortfall is large enough for the rounding
    to behave — every ordinary schedule — so the existing splits are unchanged
    ($100.00 over three open quarters is still 33.33 / 33.33 / 33.34). Where it
    does bite, the cents land at the EARLIEST open deadlines and the later ones
    read $0.00: paying a cent sooner than proportionality asks is the direction
    that cannot accrue penalty, and a $0.00 voucher row is already what a
    zero-weight CA Q3 prints.


    MOVED here from `projection` (PR #146) because `compute_ny_required_annual_
    payment` needs it: New York's IT-2105 instalments are four equal quarters
    and it hand-rolled the split, reproducing the exact negative share this
    docstring already records — `compute_ny_required_annual_payment(0.02)` gave
    `[0.01, 0.01, 0.01, -0.01]`. A helper whose docstring names the defect,
    duplicated by code that then has the defect, is the argument for one copy.
    """
    total = sum(weights)
    last = max(i for i, w in enumerate(weights) if w > 0)
    shares = []
    allocated = 0.0
    for i, w in enumerate(weights):
        remaining = round(shortfall - allocated, 2)
        if i == last:
            amt = remaining
        else:
            amt = min(round(shortfall * w / total, 2), remaining)
            allocated = round(allocated + amt, 2)
        shares.append(amt)
    return shares


def _coerce_numeric(value, default=None):
    """Coerce a value to float, handling string amounts like '$5,000'.

    Returns None by default for unparseable values so callers must handle
    the failure explicitly. Pass default=0 only for non-financial display fields.

    Rejects bool (isinstance(True, int) is True in Python, but booleans
    are never valid financial amounts — always a data error).
    """
    if isinstance(value, bool):
        raise TypeError(f"Expected numeric value, got bool: {value}")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("$", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return default
    return default


def _normalize_filing_status(filing_status) -> str:
    """Canonical filing-status validation (repo VALUE-validation rule):
    'Married Filing Jointly' or '' must hard-error, not fall through to a
    Single-shaped default."""
    if not isinstance(filing_status, str):
        raise ValueError(f"filing_status must be a string, got {type(filing_status).__name__}")
    fs = filing_status.strip().upper()
    if fs not in _CANONICAL_FILING_STATUSES:
        raise ValueError(
            f"filing_status must be one of Single/MFJ/MFS/HoH/QSS, got {filing_status!r}"
        )
    return fs


def _multi_employer_excess(
    withheld: "list[float]",
    wages: "list[float]",
    rate: float,
    wage_cap: float,
) -> "tuple[float, float]":
    """Split withholding above a per-person maximum into (return_credit,
    employer_error_amount).

    IRS Topic 608 (and EDD DE 1964 for CA SDI): anything an employer withheld
    beyond what that employer's SUBJECT WAGES support — min(wages, cap) × rate
    — is an employer error, recovered from the employer (or Form 843), NEVER
    as a credit on the return. Withholding alone cannot establish the credit:
    two below-base W-2s with erroneous over-withholding can exceed the annual
    max without any creditable excess existing. Only the correctly-withheld
    portions count, and only their multi-employer total above the per-person
    max is creditable.
    """
    clamped_total = 0.0
    employer_error = 0.0
    for w_amt, wg in zip(withheld, wages):
        proper = min(wg, wage_cap) * rate  # what this employer SHOULD have withheld, at most
        allowed = min(w_amt, proper)
        clamped_total += allowed
        employer_error += w_amt - allowed
    per_person_max = round(wage_cap * rate, 2)
    credit = (max(0.0, round(clamped_total - per_person_max, 2))
              if len(withheld) >= 2 else 0.0)
    return credit, round(employer_error, 2)


def _validated_withholding_list(withheld_by_employer, what: str) -> "list[float]":
    if not isinstance(withheld_by_employer, list):
        raise ValueError(f"{what} must be a list of per-employer amounts")
    out = []
    for i, raw in enumerate(withheld_by_employer):
        try:
            v = _coerce_numeric(raw, default=None)
        except TypeError:
            raise ValueError(f"{what}[{i}] must be numeric, got bool")
        if v is None or not math.isfinite(v) or v < 0:
            raise ValueError(f"{what}[{i}] must be a finite non-negative number, got {raw!r}")
        out.append(v)
    return out


def _amount(value, what: str, allow_negative: bool = False,
            negative_hint: str = "") -> float:
    """A user-supplied money figure, validated at the parse boundary.

    `bool` first, because `isinstance(True, int)` — a flag that reached a money
    field would otherwise be worth a dollar. Then FINITE, because `json.loads`
    accepts `NaN` and `Infinity`, both pass the isinstance test, and every
    comparison with NaN is False, so the range check below silently does
    nothing (AGENTS.md §8).

    `negative_hint` is appended to the NEGATIVE message alone, for a caller
    whose domain explains what a negative there MEANS — New York enters both
    its addition and its subtraction schedules as positive amounts, so a
    negative on one of them is a figure on the wrong schedule rather than a
    typo. That sentence used to justify a third copy of this whole function in
    the NY module; the sentence is the only part that was ever NY-specific.

    `allow_negative` is the only other axis these ever differed on. It was two
    functions — `_sched_a_amount` and `_f2210_amount` — with byte-identical
    bodies and identical messages, one of them simply lacking the flag. Two
    federal FORM numbers in the names of a shared primitive is also the wrong
    label for what this is: a state module reaching for it had to know that
    "the Form 2210 one is the signed one". Both names survive below as
    aliases, because ninety-eight call sites spell them and the prose in three
    documents names them; new code calls `_amount`.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{what} must be a number, got {value!r}")
    v = float(value)
    if not math.isfinite(v):
        raise ValueError(f"{what} must be finite, got {value!r}")
    if v < 0 and not allow_negative:
        raise ValueError(f"{what} cannot be negative, got {v}{negative_hint}")
    return v


# The same function under the two names the engine already calls it by. Not
# wrappers: the identical object, so there is one body to fix and no way for a
# caller to be reading a stale copy of the rule.
_sched_a_amount = _amount
_f2210_amount = _amount


def _prorated_mortgage_interest(interest: float, avg_balance, limit: float,
                                notes: list, label: str) -> float:
    """Pub 936 simplified method: deductible interest = interest ×
    limit / average balance when the balance exceeds the limit."""
    if avg_balance is None:
        if interest > 0:
            notes.append(
                f"{label}: average mortgage balance not provided — interest "
                f"taken in full, ASSUMING acquisition debt ≤ ${limit:,.0f}; "
                "if the balance exceeds the limit this OVERSTATES the "
                "deduction (Pub 936 proration)")
        return interest
    bal = _sched_a_amount(avg_balance, "average_mortgage_balance")
    if bal <= limit or bal == 0:
        return interest
    notes.append(
        f"{label}: interest prorated × {limit:,.0f}/{bal:,.0f} per the "
        "Pub 936 average-balance method")
    return interest * limit / bal

# A GUARD, and the reason one is a primitive. `qdcg_worksheet_applies` decides
# nothing about a form: it answers whether the fetched capital-gains
# breakpoints are usable at all, and REFUSES rather than falling through to
# ordinary rates when half of them are there. The X2 finding was four divergent
# read sites, so there is exactly one, and both `compute_scenario` (the spine)
# and `compute_form_6251` (a form) ask it — which is what puts it here rather
# than in either.

def qdcg_worksheet_applies(
    preferential_income: float,
    cg_zero_limit: "float | None",
    cg_fifteen_limit: "float | None",
) -> bool:
    """Whether the QDCG worksheet must run — and refuse when it cannot.

    The one guard shared by `compute_scenario`, `compute_form_6251`'s Part III
    coordination, `loss_harvest`'s entry point and
    `projection.project_federal_tax`, which has its own worksheet branch and
    never calls `compute_scenario`. Both used to read `limit is not None` as
    the condition for the worksheet and fall through to `compute_bracket_tax`
    otherwise, so a cache whose `federal.capital_gains_rates` was never
    fetched priced qualified dividends and net long-term gain at ORDINARY
    rates. That over-states the tax the user acts on — a Roth rung's marginal
    cost, a safe-harbor payment — which is the direction AGENTS.md §4 makes a
    hard error, and it was silent: nothing downstream can tell 0/15/20% from
    22/24/32% on a figure that looks plausible either way. Only `loss_harvest`
    guarded it, at its own entry point.

    `preferential_income` is qualified dividends plus net long-term gain; a net
    LOSS is capped and deducted from ordinary income and is not preferential,
    so a wages-only or loss-only case stays usable without the limits — an
    ordinary-income what-if is not wrong for lack of a breakpoint it never
    reaches. Returns False when there is nothing to price, True when both
    breakpoints are present, and raises when income exists and either is
    missing: half a worksheet is the same fall-through by another door.
    """
    if preferential_income <= 0:
        return False
    if cg_zero_limit is None or cg_fifteen_limit is None:
        raise ValueError(
            "Capital gains rate thresholds not loaded from tax_law JSON "
            "(federal.capital_gains_rates zero_pct_limit / fifteen_pct_limit "
            "for this filing status), and the return has qualified dividends "
            "or a net long-term gain that they price. Refusing rather than "
            "taxing that income at ordinary rates. Run /tax-setup to fetch "
            "current rates.")
    return True
