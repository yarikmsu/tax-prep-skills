"""§164(b)(7) — the federal SALT deduction cap, and its phasedown on modified AGI.

One copy of the rule, for the return and for planning. It lived in `ptet.py`
alone until AA16 found the return's own Schedule A capping state and local tax
at the UNREDUCED amount: `compute_schedule_a` took whatever cap it was handed,
the compute step handed it the fetched `federal.salt_cap`, and a single filer
with $700,000 of AGI deducted $40,000 where the statute allows $10,000. The
note it printed on every Schedule A warned no one. `compute_schedule_a` now
applies the phasedown through `salt_cap` below, and `ptet` imports the same
function, so the two cannot drift.

The functions take VALIDATED numbers and a boolean for a separate return. Each
caller validates in its own terms first — `ptet` raises `PtetError`,
`compute_schedule_a` raises `ValueError` — because the two report to
different people and the rule itself should not choose between them.
"""

import agents.lib  # noqa: F401 — the host-interpreter guard

# §164(b)(7) as amended by OBBBA (Pub. L. 119-21 §70120). Verified against the
# section text 2026-09-13. This is a STATUTORY STEP SCHEDULE, not an indexed
# figure: the statute NAMES the first two amounts and gives a formula for the
# rest, and no inflation index appears anywhere in the section. That is why
# these may be constants at all — AGENTS.md §2's third enumerated exception,
# on the `tax-safe-harbor` terms: pinned to the statute by test. The fetched
# cache wins for the AMOUNT only (`federal.salt_cap`, the `base` below); the
# threshold, rate and floor have no cache field, so these are what runs.
SALT_CAP_2025 = 40000.0              # (A)(i)
SALT_CAP_2026 = 40400.0              # (A)(ii)
SALT_STEP = 1.01                     # (A)(iii), 101% of the preceding year
SALT_CAP_REVERSION = 10000.0         # (A)(iv), years beginning after 2029
SALT_MAGI_THRESHOLD_2025 = 500000.0  # (B)(ii)(I)
SALT_MAGI_THRESHOLD_2026 = 505000.0  # (B)(ii)(II), then 101% a year
SALT_PHASEDOWN_RATE = 0.30           # (B)(i)
SALT_PHASEDOWN_FLOOR = 10000.0       # (B)(iii)
SALT_FIRST_OBBBA_YEAR = 2025
SALT_LAST_OBBBA_YEAR = 2029


def phasedown_applies(tax_year: int) -> bool:
    """§164(b)(7)(B) can only reduce an amount (A) actually raised. Outside
    TY2025-2029 the cap IS the $10,000 floor, so the reduction is a no-op —
    and a STOP or a banner about a missing MAGI in those years is noise about
    a figure that cannot change the answer."""
    return SALT_FIRST_OBBBA_YEAR <= tax_year <= SALT_LAST_OBBBA_YEAR


def _step_from(base: float, base_year: int, tax_year: int) -> float:
    """101% a year, compounded from a NAMED anchor. §164(b)(7)(A)(iii)/(B)(ii)(III).

    The statute states no rounding for the steps, so none is invented here
    beyond settling the binary representation at cents. The IRS-published
    figure is what the cache carries, and the cache wins.
    """
    amount = base
    for _ in range(base_year, tax_year):
        amount *= SALT_STEP
    return round(amount, 2)


def base_cap(tax_year: int) -> float:
    """§164(b)(7)(A) — the applicable limitation amount before (B) and before
    the §164(b)(6)(B) halving for a separate return."""
    if tax_year < SALT_FIRST_OBBBA_YEAR or tax_year > SALT_LAST_OBBBA_YEAR:
        return SALT_CAP_REVERSION
    if tax_year == SALT_FIRST_OBBBA_YEAR:
        return SALT_CAP_2025
    return _step_from(SALT_CAP_2026, 2026, tax_year)


def phasedown_rate(mfs: bool) -> float:
    """§164(b)(7)(B)(i)'s rate, with §164(b)(6)(B)'s halving distributed
    through the reduction for a separate return."""
    return SALT_PHASEDOWN_RATE / 2 if mfs else SALT_PHASEDOWN_RATE


def phasedown_floor(mfs: bool) -> float:
    """§164(b)(7)(B)(iii)'s floor, halved for a separate return.

    Beside the threshold helper below because the halving convention is the
    same one, and open-coding it is what let the PTET banner quote a flat 30%
    while the cap applied 15% — one copy drifting from another, twofold, in a
    caveat whose whole job was to describe the rule correctly.
    """
    return SALT_PHASEDOWN_FLOOR / 2 if mfs else SALT_PHASEDOWN_FLOOR


def cap_floor(cap: float, mfs: bool) -> float:
    """The lowest cap any MAGI can produce — §164(b)(7)(B)(iii)'s floor, but
    never ABOVE the cap it is flooring, because a floor on a REDUCTION cannot
    raise the thing reduced. `salt_cap` makes the same clamp on the live
    figure; one rule, so a change to it cannot leave this copy green."""
    return min(cap, phasedown_floor(mfs))


def magi_threshold(tax_year: int, mfs: bool) -> float:
    """§164(b)(7)(B)(ii), halved for a separate return by (B)(i)'s own words."""
    if tax_year <= SALT_FIRST_OBBBA_YEAR:
        threshold = SALT_MAGI_THRESHOLD_2025
    else:
        threshold = _step_from(SALT_MAGI_THRESHOLD_2026, 2026, tax_year)
    return threshold / 2 if mfs else threshold


def salt_cap(tax_year: int, mfs: bool, base: "float | None" = None,
             modified_agi: "float | None" = None) -> float:
    """The SALT cap for the year, a separate return or not, and the MAGI.

    `base` is the year's amount for this kind of return — a fetched
    `federal.salt_cap` figure, already halved when it is `mfs_amount` — or
    None for the statutory schedule. The cache carries the year's STATUTORY
    amount, the same for every taxpayer, and (B) reduces it per RETURN, so the
    reduction applies to whichever base is used. Applying it only on the
    statutory path once made a populated cache the one way to get the
    unreduced cap.

    `modified_agi` is AGI plus what §164(b)(7)(B)(iv) adds back, which the IRS
    worksheet reads as Form 2555 lines 45 and 50 (the housing DEDUCTION
    included), Form 4563 line 15 and excluded Puerto Rico income. It can be
    negative. None means the reduction is not applied, which leaves
    the cap too HIGH; each caller decides what that means for its output, and
    the return refuses it.

    Per-status arithmetic, which is the halving distributed rather than a second
    rule: §164(b)(6)(B) halves the amount, (B)(i) halves the threshold in its
    own words, so for a separate return the reduction RATE is likewise half
    (15% of the excess over the halved threshold) and the (B)(iii) floor
    arrives as $5,000. Algebraically identical to reducing the joint amount and
    halving at the end, and it keeps a cached `mfs_amount` — already a separate
    return's figure — from being halved twice.

    MFS ORDERING, stated at length because the section can be read two ways and
    they differ FOURFOLD for a real filer. Taken here: (B)(i)'s halved threshold
    and the halved amount together, floor $5,000. The alternative — compute the
    whole of (7) on a joint basis against the FULL $500,000 threshold and halve
    only at the end — gives an MFS filer with $350,000 of TY2025 MAGI a $20,000
    cap where this gives $5,000. The one taken is the only reading under which
    (B)(i)'s "half the threshold amount in the case of a married individual
    filing a separate return" does anything at all; on the joint-basis reading
    the reduction never consults an MFS threshold and that clause is inoperative.
    It also keeps an MFS reversion year at $5,000.
    """
    if base is None:
        statutory = base_cap(tax_year)
        base = statutory / 2 if mfs else statutory
    if modified_agi is not None and phasedown_applies(tax_year):
        excess = max(0.0, modified_agi - magi_threshold(tax_year, mfs))
        # §164(b)(7)(B)(iii) floors the RESULT OF THE REDUCTION, and a floor
        # on a reduction cannot raise the thing it is reducing. Written as a
        # bare `max(floor, ...)` it did: a cache carrying a cap BELOW $10,000
        # came back RAISED to it the moment a MAGI was supplied, so passing
        # --magi increased the modeled headroom. Never above the base.
        base = min(base, max(phasedown_floor(mfs), base - phasedown_rate(mfs) * excess))
    return round(base, 2)
