#!/usr/bin/env python3
"""Helpers the federal side and a state module both need.

Its own file so that neither has to import the other. `state/ny.py` needs the
instalment splitter and nothing else from the rest of the engine — measured:
twenty-four New York symbols depending on exactly ONE non-New-York top-level
name — and importing it from the package `__init__` that re-exports New York
would be a cycle.
"""

import agents.lib  # noqa: F401 — the host-interpreter guard, see agents/lib/__init__.py

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
