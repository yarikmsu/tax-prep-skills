#!/usr/bin/env python3
"""Rollover prior-year carryforwards, identity, dependents, and expected sources
from archive/{year}/ into current-year workspace files.

Pure functions for diff/merge logic; thin I/O wrappers at edges.
"""

import copy
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agents.lib.secure_fs import (  # noqa: E402
    HalfApplied, PublishedButNotSecured, atomic_paths, first_non_finite, redact)


class RolloverError(Exception):
    """Raised when /tax-rollover cannot safely proceed."""


# --- FIELD_MAP -------------------------------------------------------------
# Stable carryforward/identity keys → JSON paths in source and target files.
# Schema renames in newer toolkit versions don't break older archived returns
# because lookup is by stable key, not raw path.

FIELD_MAP: dict[str, dict] = {
    # Category 1: Carryforwards (dollar amounts)
    "capital_loss_carryforward": {
        "category": "carryforwards",
        # Whole schedule_d block. `_capital_loss_changes` writes the
        # short/long SPLIT fields when the archive carries the Capital Loss
        # Carryover Worksheet block, and offers a combined lower bound on this
        # legacy target — for resolution, never applied as a split — when it
        # does not.
        "source_path": "schedule_d",
        "target_file": "taxpayer_profile.json",
        "target_path": "prior_year.capital_loss_carryforward",
        "transform": "capital_loss_split",
    },
    "qbi_loss_carryforward": {
        "category": "carryforwards",
        "source_path": "form_8995_qbi.line_16_qbi_loss_carryforward",
        "target_file": "taxpayer_profile.json",
        "target_path": "prior_year.qbi_loss_carryforward",
        "transform": "abs",
    },
    "unused_ftc_carryforward": {
        "category": "carryforwards",
        "source_path": "form_1116_ftc",
        "target_file": "taxpayer_profile.json",
        "target_path": "prior_year.unused_ftc_carryforward",
        "transform": "ftc_unused",
    },
    "ira_basis": {
        "category": "carryforwards",
        "source_path": "form_8606.basis_remaining",  # Form 8606 line 14
        "target_file": "taxpayer_profile.json",
        "target_path": "prior_year.ira_basis",
        "transform": "ira_basis",
    },
    "amt_credit_carryforward": {
        "category": "carryforwards",
        # Written by /tax-compute: this year's deferral-attributable AMT
        # (compute_form_6251 form_8801_carryforward) PLUS any unused prior
        # credit (compute_form_8801_credit carryforward_remaining). Losing
        # it forfeits the ISO AMT money permanently.
        "source_path": "form_6251.form_8801_carryforward_to_next_year",
        "target_file": "taxpayer_profile.json",
        "target_path": "prior_year.amt_credit_carryforward",
        "transform": "abs",
    },
    "ca_amt_credit_carryforward": {
        "category": "carryforwards",
        # CA Schedule P (540) analogue of the federal Form 8801 carryforward
        # (N1): this year's deferral-attributable CA AMT
        # (compute_ca_schedule_p schedule_p_carryforward) PLUS any unused prior
        # CA credit. A separate carryforward from the federal one — the CA
        # exemption and TMT differ, so the CA deferral AMT is its own figure.
        "source_path": "california_540.schedule_p.carryforward_to_next_year",
        "target_file": "taxpayer_profile.json",
        "target_path": "prior_year.ca_amt_credit_carryforward",
        "transform": "abs",
    },
    "prior_overpayment_applied": {
        "category": "carryforwards",
        # Form 1040 line 36 — the part of LAST year's overpayment applied to
        # THIS year's estimated tax. It becomes part of this year's line 26,
        # "estimated tax payments and amount applied from the prior-year
        # return", and it is the one payment on that line with no document
        # behind it: no 1099, no ledger record, nothing the tie-out gate can
        # see. The profile field existed and NOTHING wrote it — dead
        # scaffolding, which is the D1 lesson this repository already learned
        # once — so the carry was manual and silently skippable.
        #
        # Losing it UNDER-states line 26, which under-states line 33's
        # payments, which OVER-states line 37: the taxpayer is billed for money
        # they have already paid. `return_builder._check_the_prior_overpayment`
        # is the other end, and refuses a line 26 smaller than this figure.
        "source_path": "form_1040.line_36_applied_to_next_year",
        "target_file": "taxpayer_profile.json",
        "target_path": "prior_year.estimated_payments_applied",
        "transform": "abs",
    },
    # Category 2: Static identity
    "primary_first_name": {
        "category": "identity",
        "source_path": "primary.first_name",
        "target_file": "taxpayer_profile.json",
        "target_path": "primary.first_name",
        "transform": None,
    },
    "primary_middle_name": {
        "category": "identity", "source_path": "primary.middle_name",
        "target_file": "taxpayer_profile.json", "target_path": "primary.middle_name",
        "transform": None,
    },
    "primary_last_name": {
        "category": "identity", "source_path": "primary.last_name",
        "target_file": "taxpayer_profile.json", "target_path": "primary.last_name",
        "transform": None,
    },
    "primary_ssn_last4": {
        "category": "identity", "source_path": "primary.ssn_last4",
        "target_file": "taxpayer_profile.json", "target_path": "primary.ssn_last4",
        "transform": None,
    },
    "primary_dob": {
        "category": "identity", "source_path": "primary.dob",
        "target_file": "taxpayer_profile.json", "target_path": "primary.dob",
        "transform": None,
    },
    "primary_occupation": {
        "category": "identity", "source_path": "primary.occupation",
        "target_file": "taxpayer_profile.json", "target_path": "primary.occupation",
        "transform": None,
    },
    "spouse_block": {
        "category": "identity", "source_path": "spouse",
        "target_file": "taxpayer_profile.json", "target_path": "spouse",
        "transform": "spouse_conditional",
    },
    "address_block": {
        "category": "identity", "source_path": "address",
        "target_file": "taxpayer_profile.json", "target_path": "address",
        "transform": None,
    },
    "filing_status": {
        "category": "identity", "source_path": "filing_status",
        "target_file": "taxpayer_profile.json", "target_path": "filing_status",
        "transform": "filing_status_ask",
    },
}


# --- Dataclasses -----------------------------------------------------------

@dataclass(frozen=True)
class Change:
    category: str
    json_path: str
    target_file: str
    prior_value: Any
    current_value: Any
    action_suggested: str  # "apply" | "ask" | "skip"
    reason: str


@dataclass
class PriorReturn:
    year: int
    result: dict
    profile: dict


# --- I/O ------------------------------------------------------------------

def _refuse_non_finite(obj, name: str) -> None:
    """AGENTS.md §8 at the one boundary every archived figure crosses.

    `json.loads` accepts `NaN`, `Infinity` and `-Infinity`, and parses `1e999`
    to `inf`. All of them pass `isinstance(x, (int, float))`, and every
    comparison with NaN is False — so `+inf` passed the explicit block's
    `v > 0`, a NaN skipped it and fell to the legacy path where NaN and −inf
    passed `raw >= 0`, and the carryforward written into the profile was
    `nan` or `inf` (E6 of the 2026-09-05 review). Refused here, no transform
    has to know. The walk is `secure_fs.first_non_finite`, shared with the
    tax-law cache reader.
    """
    where = first_non_finite(obj)
    if where is not None:
        raise RolloverError(
            f"{name} holds a non-finite number at {where} — NaN and "
            "Infinity are not figures; correct the file")


def _read_json(path: Path) -> dict:
    if not path.exists():
        raise RolloverError(f"{redact(path)} does not exist")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RolloverError(f"{redact(path.name)} is malformed: {redact(e)}")
    _refuse_non_finite(data, redact(path.name))
    return data


def load_prior(archive_dir: Path, year: int) -> PriorReturn:
    archive_dir = Path(archive_dir)
    if not archive_dir.exists():
        raise RolloverError(
            f"archive directory {redact(archive_dir)} does not exist")
    result = _read_json(archive_dir / "result.json")
    profile = _read_json(archive_dir / "taxpayer_profile.json")
    meta_year = result.get("meta", {}).get("tax_year")
    if meta_year != year:
        raise RolloverError(
            f"Archive at {redact(archive_dir)} is for TY{meta_year}, not "
            f"TY{year}. Refusing to roll inconsistent data."
        )
    return PriorReturn(year=year, result=result, profile=profile)


@dataclass
class CurrentState:
    result: dict
    profile: dict
    tax_law: dict


def _get_path(d: dict, dotted: str, default=None):
    """Read a.b.c from nested dict; return default if any segment missing."""
    cur = d
    for seg in dotted.split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return default
        cur = cur[seg]
    return cur


def _set_path(d: dict, dotted: str, value: Any) -> None:
    """Write value at a.b.c in nested dict, creating intermediate dicts as needed."""
    cur = d
    segs = dotted.split(".")
    for seg in segs[:-1]:
        if seg not in cur or not isinstance(cur[seg], dict):
            cur[seg] = {}
        cur = cur[seg]
    cur[segs[-1]] = value


def _is_amount(raw) -> bool:
    """Whether an amount is actually THERE.

    `_amount` below reads an absent or unreadable one as 0, which is the right
    default for something being summed and the wrong answer to "did anybody
    write this figure down?". A bool is JSON's `true`, not a dollar, and
    `abs(True)` is 1; `_read_json` refuses a non-finite float before any
    transform runs, and this is the same rule for a caller that did not come
    through it.
    """
    return (not isinstance(raw, bool) and isinstance(raw, (int, float))
            and math.isfinite(raw))


def _amount(raw) -> float:
    """`raw` as an amount, or 0 when it is absent or not one."""
    return raw if _is_amount(raw) else 0


_STATUS_CODES = {
    "SINGLE": "SINGLE", "MFJ": "MFJ", "MFS": "MFS", "HOH": "HOH", "QSS": "QSS",
    "MARRIED FILING JOINTLY": "MFJ", "MARRIED FILING SEPARATELY": "MFS",
    "HEAD OF HOUSEHOLD": "HOH", "QUALIFYING SURVIVING SPOUSE": "QSS",
    "QUALIFYING WIDOW(ER)": "QSS", "QUALIFYING WIDOWER": "QSS", "QUALIFYING WIDOW": "QSS",
}


def _status_code(filing_status) -> "str | None":
    """The canonical short code, or None for a status this module cannot read.
    Not `projection._canonical_status`: capabilities imports this module at
    load and projection imports capabilities, so the mapping is spelled here."""
    return _STATUS_CODES.get(str(filing_status or "").strip().upper())


def _capital_loss_cap(filing_status) -> "tuple[int, bool]":
    """The prior return's §1211(b) cap, and whether it had to be ASSUMED.

    The constants are the engine's (`tax_computations.CAPITAL_LOSS_LIMIT*`),
    imported inside the function: capabilities imports this module at load
    and tax_computations sits behind projection on that path. A status this
    module cannot read takes the $3,000 cap — the SMALLER bound — and says so:
    the bound is a value the `ask` lands, so it must be the one no filer's
    true carryforward sits below (an MFS filer's is $1,500 higher, and the
    reason says that too).
    """
    from agents.lib.tax_computations import CAPITAL_LOSS_LIMIT, CAPITAL_LOSS_LIMIT_MFS
    code = _status_code(filing_status)
    if code is None:
        return CAPITAL_LOSS_LIMIT, True
    return (CAPITAL_LOSS_LIMIT_MFS if code == "MFS" else CAPITAL_LOSS_LIMIT), False


def _capital_loss_carryforward(raw, filing_status=None) -> dict:
    """The prior return's capital-loss carryforward, BY CHARACTER when known.

    A carryforward is not one figure: Schedule D line 6 takes the short-term
    part and line 14 the long-term part, and short-term loss offsets ordinary
    income first, so a combined number cannot be placed on the next return
    without a split nobody can guess (X3 of the 2026-09-05 review — the
    profile carried one figure and the compute skill had nowhere to put it).

    Returns one of:
      {"short_term": x, "long_term": y} — the explicit Capital Loss Carryover
        Worksheet block /tax-compute wrote, when its TOTAL sits between the
        bounds line 16 implies: at least abs(line 16) − cap, at most
        abs(line 16); the answer. Both ZERO when the archive positively
        establishes that nothing carries (a gain year with no block, or an
        explicit zero block beside a loss inside the cap) — an answer too,
        because a stale figure in the target profile must be retired, and
        "the archive says zero" is not "the archive is silent".
      {"combined": z, "unresolved": "contradiction"} — a block is present but
        its total is outside those bounds: below, it is the template's
        scaffolding (all zero), a half-filled one, or a sign slip; above (or
        any non-zero block beside a gain), it is the pre-deduction loss
        written where the carryover belongs. The lower bound is offered for
        RESOLUTION, never split by guess.
      {"combined": z, "unresolved": "legacy"} — no block (an archive older
        than the worksheet, or a bare line-16 number): the same lower bound,
        the same resolution. A loss INSIDE the cap with no block is this too,
        with a bound of zero: the worksheet uses less than the cap when
        taxable income is low, so "inside the cap" is not proof that nothing
        carries. `cap_assumed` is True when the prior filing status could not
        be read and the $3,000 cap — the smaller bound — was taken.
      {} — the archive is SILENT: no Schedule D figure at all.

    The derivation is a LOWER bound: the worksheet only ever uses less than
    the cap (when taxable income is low), never more.
    """
    zeros = {"short_term": 0, "long_term": 0}
    if raw is None:
        return {}
    if isinstance(raw, dict):
        if "line_16_combined" not in raw and "loss_carryforward_to_next_year" not in raw:
            return {}
        block = raw.get("loss_carryforward_to_next_year")
        line16 = _amount(raw.get("line_16_combined"))
        has_block = isinstance(block, dict)
        # An ANSWER needs both sides actually written down. A worksheet block
        # whose amounts are missing is the template's scaffolding, and reading
        # a missing amount as a computed zero turned "nobody filled this in"
        # into "the return establishes that nothing carries" — which retires a
        # live carryforward out of the target profile. A half-filled block
        # stays a contradiction, which is what the docstring above already
        # calls it.
        answered = has_block and all(_is_amount(block.get(side))
                                     for side in ("short_term", "long_term"))
        st = max(_amount(block.get("short_term")), 0) if answered else 0
        lt = max(_amount(block.get("long_term")), 0) if answered else 0
    else:
        line16, has_block, answered, st, lt = _amount(raw), False, False, 0, 0
    cap, cap_assumed = _capital_loss_cap(filing_status)
    lower_bound = max(abs(line16) - cap, 0) if line16 < 0 else 0
    upper_bound = abs(line16) if line16 < 0 else 0
    if answered and (st > 0 or lt > 0) and lower_bound <= st + lt <= upper_bound:
        return {"short_term": st, "long_term": lt}
    if answered and st == lt == 0 and lower_bound == 0:
        return dict(zeros)            # a computed zero beside a loss inside the cap
    if not has_block and line16 >= 0:
        return dict(zeros)            # a gain year: nothing carries
    return {"combined": lower_bound,
            "unresolved": "contradiction" if has_block else "legacy",
            "cap_assumed": cap_assumed}


def _transform_abs(raw):
    return abs(_amount(raw))


def _transform_ftc_unused(raw):
    """raw is the form_1116_ftc block (dict). Unused = paid - allowed."""
    if not isinstance(raw, dict):
        return 0
    paid = _amount(raw.get("foreign_tax_paid_usd"))
    allowed = _amount(raw.get("ftc_allowed"))
    return max(paid - allowed, 0)


def _transform_ira_basis(raw):
    """Form 8606 line 14 basis remaining → next-year line 2. Non-negative; 0 if absent."""
    return max(_amount(raw), 0)


TRANSFORMS = {
    "abs": _transform_abs,
    "ftc_unused": _transform_ftc_unused,
    "ira_basis": _transform_ira_basis,
}

_CAPITAL_LOSS_LEGACY = "prior_year.capital_loss_carryforward"
_CAPITAL_LOSS_SPLIT = {"short_term": "prior_year.capital_loss_carryforward_short_term",
                       "long_term": "prior_year.capital_loss_carryforward_long_term"}


def _capital_loss_changes(prior: "PriorReturn", profile: dict, raw) -> list:
    """The carryforward category's capital-loss entries, per `_capital_loss_carryforward`.

    Two things the generic loop does not know. A joint-year carryforward is
    not the separate-year filer's: Reg. §1.1212-1(c) allocates it between the
    spouses by each one's own net loss, and a decedent's dies with them (Rev.
    Rul. 74-175), so an MFJ → anything transition turns every split write
    into an `ask`. And /tax-compute's STOP on a combined-only figure is armed
    only while both split fields are zero, so the combined branch asks to
    zero a non-zero split beside the bound it offers.
    """
    def change(json_path, prior_value, current_value, action, reason):
        return Change(category="carryforwards", json_path=json_path,
                      target_file="taxpayer_profile.json", prior_value=prior_value,
                      current_value=current_value, action_suggested=action, reason=reason)
    status = (prior.result.get("meta") or {}).get("filing_status") \
        or prior.profile.get("filing_status")
    # Fails CLOSED on a current status this module cannot read (the profile
    # template ships `null`): the joint figure is applied only to a joint return.
    left_joint = (_status_code(status) == "MFJ"
                  and _status_code(profile.get("filing_status")) != "MFJ")
    found = _capital_loss_carryforward(raw, status)
    out = []
    if "short_term" in found:
        for side, json_path in _CAPITAL_LOSS_SPLIT.items():
            prior_value = found[side]
            current_value = _get_path(profile, json_path, default=0)
            if not prior_value and not current_value:
                continue
            if current_value and current_value != prior_value:
                why = ("the archived return shows no carryforward remains"
                       if not prior_value else f"prior ({prior_value})")
                out.append(change(json_path, prior_value, current_value, "ask",
                                  f"Current value ({current_value}) differs from {why}"))
            elif left_joint:
                out.append(change(
                    json_path, prior_value, current_value, "ask",
                    "A joint-year carryforward is allocated between the spouses by "
                    "each one's own net loss (Reg. §1.1212-1(c)), and a decedent's "
                    "dies with them (Rev. Rul. 74-175) — confirm this filer's share"))
            else:
                out.append(change(json_path, prior_value, current_value, "apply",
                                  "Pulled from schedule_d.loss_carryforward_to_next_year"
                                  f".{side}"))
        legacy_current = _get_path(profile, _CAPITAL_LOSS_LEGACY, default=0)
        if legacy_current and (found["short_term"] or found["long_term"]):
            out.append(change(_CAPITAL_LOSS_LEGACY, 0, legacy_current, "apply",
                              "Legacy combined figure superseded by the short/long split"))
        elif legacy_current:
            out.append(change(_CAPITAL_LOSS_LEGACY, 0, legacy_current, "ask",
                              f"Current value ({legacy_current}) differs from the archived "
                              "return, which shows no carryforward remains"))
    elif "combined" in found:
        why = ("the archived worksheet block totals outside the bounds line 16 implies "
               "(a correct carryover is between the loss beyond the cap and the loss)"
               if found["unresolved"] == "contradiction"
               else "the archived return predates the Capital Loss Carryover Worksheet block"
               if found["combined"] else
               "the archived return shows a loss inside the cap and no worksheet block; "
               "the worksheet uses less than the cap when taxable income is low, so a "
               "carryforward may remain")
        if found.get("cap_assumed"):
            why += ("; the prior filing status could not be read, so the $3,000 cap was "
                    "assumed — an MFS filer's bound is $1,500 higher")
        current_value = _get_path(profile, _CAPITAL_LOSS_LEGACY, default=0)
        # A bound of ZERO establishes nothing, so it must not be able to LAND.
        # `apply_changes` writes an `ask`'s prior_value once its category is
        # approved, and a zero there wrote "no carryforward remains" into the
        # profile on the strength of an archive saying the opposite — the
        # reason on this very change says a carryforward may remain — and the
        # split asks below zeroed a figure the filer may have keyed from the
        # paper worksheet. With nothing to write, the change carries the
        # profile's own value: the question still stands in the report, and
        # approving it changes nothing.
        landing = found["combined"] or current_value
        out.append(change(
            _CAPITAL_LOSS_LEGACY, landing, current_value, "ask",
            f"Combined lower bound only — {why}; resolve the short/long split from the "
            "prior return's Capital Loss Carryover Worksheet (lines 8 and 13) before "
            "/tax-compute, which cannot place a combined figure on Schedule D lines 6 and 14"))
        for side, json_path in _CAPITAL_LOSS_SPLIT.items():
            current_value = _get_path(profile, json_path, default=0)
            # Only a bound there IS can be run past: with no combined figure to
            # arm the STOP, zeroing the split would delete the one carryforward
            # in the workspace to protect a figure that is not there.
            if current_value and found["combined"]:
                out.append(change(
                    json_path, 0, current_value, "ask",
                    "The archive supports only a combined lower bound; a non-zero split "
                    "here would let /tax-compute run past its STOP on that figure"))
    return out


def _diff_carryforwards(prior: PriorReturn, current: CurrentState) -> list[Change]:
    out: list[Change] = []
    for key, entry in FIELD_MAP.items():
        if entry["category"] != "carryforwards":
            continue
        raw = _get_path(prior.result, entry["source_path"])
        if raw is None:
            # Schema drift backward: missing source path
            continue
        if entry.get("transform") == "capital_loss_split":
            out.extend(_capital_loss_changes(prior, current.profile, raw))
            continue
        transform = TRANSFORMS.get(entry.get("transform"))
        prior_value = transform(raw) if transform else raw
        if not prior_value:  # zero or empty → no diff entry
            continue
        target_doc = current.profile if entry["target_file"] == "taxpayer_profile.json" else current.result
        current_value = _get_path(target_doc, entry["target_path"], default=0)
        if current_value and current_value != prior_value:
            action = "ask"
            reason = f"Current value ({current_value}) differs from prior ({prior_value})"
        else:
            action = "apply"
            reason = f"Pulled from {entry['source_path']}"
        out.append(Change(
            category="carryforwards",
            json_path=entry["target_path"],
            target_file=entry["target_file"],
            prior_value=prior_value,
            current_value=current_value,
            action_suggested=action,
            reason=reason,
        ))
    return out


def _diff_identity(prior: PriorReturn, current: CurrentState) -> list[Change]:
    out: list[Change] = []
    new_filing_status = current.profile.get("filing_status")
    spouse_ok_statuses = {"MFJ", "MFS", "QSS"}

    for key, entry in FIELD_MAP.items():
        if entry["category"] != "identity":
            continue
        raw = _get_path(prior.profile, entry["source_path"])
        target_doc = current.profile  # all identity targets live in profile
        current_value = _get_path(target_doc, entry["target_path"])
        transform = entry.get("transform")

        if transform == "spouse_conditional":
            if new_filing_status not in spouse_ok_statuses:
                if isinstance(raw, dict) and any(raw.values()):
                    out.append(Change(
                        category="identity",
                        json_path=entry["target_path"],
                        target_file=entry["target_file"],
                        prior_value=raw,
                        current_value=current_value,
                        action_suggested="skip",
                        reason=f"Spouse block dropped: new filing status {new_filing_status} does not permit spouse",
                    ))
                continue

        if transform == "filing_status_ask":
            # Filing status always surfaces as ask (never silently overwritten),
            # even when prior and current values match — confirm every year.
            if raw is not None:
                out.append(Change(
                    category="identity",
                    json_path=entry["target_path"],
                    target_file=entry["target_file"],
                    prior_value=raw,
                    current_value=current_value,
                    action_suggested="ask",
                    reason="Filing status changes affect the entire return — confirm",
                ))
            continue

        if raw is None:
            continue
        if current_value == raw:
            continue
        action = "ask" if (current_value not in (None, "", 0)) else "apply"
        out.append(Change(
            category="identity",
            json_path=entry["target_path"],
            target_file=entry["target_file"],
            prior_value=raw,
            current_value=current_value,
            action_suggested=action,
            reason=f"Pulled from prior profile.{entry['source_path']}",
        ))
    return out


def _age_at_end_of_year(dob_str: str, year: int) -> int:
    """Return age at Dec 31 of given year. Raises ValueError on malformed DOB."""
    dob = date.fromisoformat(dob_str)
    end = date(year, 12, 31)
    return end.year - dob.year - ((end.month, end.day) < (dob.month, dob.day))


def _diff_dependents(prior: PriorReturn, current: CurrentState, tax_law_new: dict) -> list[Change]:
    out: list[Change] = []
    prior_deps = prior.profile.get("dependents", []) or []
    new_year = current.profile.get("tax_year") or _get_path(current.result, "meta.tax_year")
    ctc_age = tax_law_new.get("dependent_age_thresholds", {}).get("ctc_age", 17)
    qc_general = tax_law_new.get("dependent_age_thresholds", {}).get("qualifying_child_general", 19)
    qc_student = tax_law_new.get("dependent_age_thresholds", {}).get("qualifying_child_student", 24)

    for idx, dep in enumerate(prior_deps):
        if not isinstance(dep, dict):
            continue
        dob = dep.get("dob")
        if not dob:
            continue
        try:
            new_age = _age_at_end_of_year(dob, new_year)
        except (ValueError, TypeError):
            out.append(Change(
                category="dependents",
                json_path=f"dependents[{idx}]",
                target_file="taxpayer_profile.json",
                prior_value=dep,
                current_value=None,
                action_suggested="skip",
                reason=f"Malformed DOB: {dob!r} — skipping age tick",
            ))
            continue

        new_dep = dict(dep)
        new_dep["ctc_eligible"] = new_age < ctc_age
        warnings = []
        if dep.get("ctc_eligible") and not new_dep["ctc_eligible"]:
            warnings.append(
                f"turned {new_age} — no longer CTC-eligible (now qualifies for $500 "
                "Credit for Other Dependents if still a dependent)"
            )
        if new_age == qc_general:
            warnings.append(f"turned {new_age} — qualifying-child general age cutoff")
        if new_age == qc_student:
            warnings.append(f"turned {new_age} — qualifying-child student age cutoff")

        action = "ask" if warnings else "apply"
        reason = "; ".join(warnings) if warnings else f"Age tick {new_age} — no transitions"
        out.append(Change(
            category="dependents",
            json_path=f"dependents[{idx}]",
            target_file="taxpayer_profile.json",
            prior_value=new_dep,
            current_value=None,
            action_suggested=action,
            reason=reason,
        ))
    return out


def _diff_expected_sources(prior: PriorReturn, current: CurrentState) -> list[Change]:
    pr = prior.result
    w2_employers = []
    w2 = pr.get("w2_summary") or {}
    if w2.get("employer"):
        w2_employers.append({"name": w2["employer"], "ein": w2.get("employer_ein", "")})

    interest_payers = [
        {"name": item["payer"], "country_code": item.get("country", "US")}
        for item in (pr.get("interest_detail", {}).get("items") or [])
        if item.get("payer")
    ]
    dividend_payers = [
        {"name": item["payer"]}
        for item in (pr.get("dividend_detail", {}).get("items") or [])
        if item.get("payer")
    ]
    # Foreign accounts: dedup by (institution, country_code, account_last4)
    seen: set[tuple] = set()
    foreign_accounts = []
    for src in ("form_8938_fatca", "fbar_fincen_114"):
        for acc in (pr.get(src, {}).get("accounts") or []):
            key = (acc.get("institution"), acc.get("country_code"), acc.get("account_last4"))
            if all(k is not None for k in key) and key not in seen:
                seen.add(key)
                foreign_accounts.append({
                    "institution": acc["institution"],
                    "country_code": acc["country_code"],
                    "account_last4": acc["account_last4"],
                })

    rows = [
        ("expected_sources.w2_employers", w2_employers),
        ("expected_sources.interest_payers", interest_payers),
        ("expected_sources.dividend_payers", dividend_payers),
        ("expected_sources.brokerages", []),
        ("expected_sources.foreign_accounts", foreign_accounts),
    ]
    out: list[Change] = []
    for path, value in rows:
        if not value:
            continue
        current_value = _get_path(current.profile, path, default=[])
        action = "ask" if current_value else "apply"
        out.append(Change(
            category="expected_sources",
            json_path=path,
            target_file="taxpayer_profile.json",
            prior_value=value,
            current_value=current_value,
            action_suggested=action,
            reason="Derived from prior result.json payer/account lists",
        ))
    return out


# Per-year event fields on a foreign-holding entry — TRUE only for the tax year
# the distribution/sale happened. They MUST be re-collected from this year's
# documents each year, never carried forward (a stale prior-year sale would
# silently re-assert a §1291 event that didn't occur this year).
_FOREIGN_HOLDING_EVENT_FIELDS = {
    "event_date": None,
    "distribution_date": None,
    "disposition_date": None,
    "received_distribution": False,
    "disposed": False,
    "disposition_gain_usd": None,
    "position_fully_closed": None,
}


def _roll_foreign_holdings_forward(prior_holdings: Any, prior_year: int) -> list:
    """Carry each prior-year foreign holding into the new year so the §1291 base
    history is not lost (Form 8621 needs the full zero-inclusive distribution
    history and the correct holding-period length every year the fund is held).

    Preserves the STATIC per-fund facts (kind, fund_domicile, custody, currency,
    acquisition_date, pfic_election, …) and the accumulated ``distributions_by_year``
    cumulative history; INCREMENTS ``preceding_holding_years`` by one (the tax year
    just completed becomes a preceding year); and RESETS the per-year event fields
    (event_date / received_distribution / disposed / disposition_gain_usd) so this
    year's distribution/sale is re-collected from documents rather than inherited.
    The carried entry surfaces as an ``ask`` — the preparer confirms the fund is
    still held and folds this year's distribution total into distributions_by_year.
    """
    if not isinstance(prior_holdings, list):
        return []
    rolled: list = []
    for h in prior_holdings:
        if not isinstance(h, dict):
            continue
        # A fund whose position was FULLY closed in the prior year is gone — do NOT
        # carry it forward (resetting `disposed` to False would resurrect a sold
        # fund with stale history; Codex round 9). But a PARTIAL disposition still
        # sets `disposed: true` while shares (and their base history) remain —
        # dropping it would silently lose that history and omit future distributions
        # (Codex round 18). So drop ONLY on an EXPLICIT full close; a partial or an
        # UNKNOWN (`position_fully_closed` absent) disposition is carried forward
        # (fail-safe against understatement — the preparer confirms via the `ask`).
        if h.get("disposed") is True and h.get("position_fully_closed") is True:
            continue
        nh = copy.deepcopy(h)
        phy = nh.get("preceding_holding_years")
        if isinstance(phy, int) and not isinstance(phy, bool) and phy >= 0:
            nh["preceding_holding_years"] = phy + 1
        # The fund was held THROUGH `prior_year`; that year is now a preceding
        # base-period year. If it had NO distribution, distributions_by_year lacks
        # an entry for it — so when the fund later distributes,
        # compute_pfic_excess_distribution sees a short zero-inclusive history and
        # marks the result INCOMPLETE, permanently blocking the return (Codex round
        # 22). Add an explicit `{prior_year: 0}` when absent — INCLUDING when the
        # mapping itself is missing (a fund with no prior distributions can validly
        # have no `distributions_by_year` at all; initialize it, Codex round 31).
        dby = nh.get("distributions_by_year")
        if not isinstance(dby, dict):
            dby = {}
            nh["distributions_by_year"] = dby
        if str(prior_year) not in dby and prior_year not in dby:
            dby[str(prior_year)] = 0
        for field_name, reset_value in _FOREIGN_HOLDING_EVENT_FIELDS.items():
            if field_name in nh:
                nh[field_name] = reset_value
        rolled.append(nh)
    return rolled


def _diff_foreign_holdings(prior: PriorReturn, current: CurrentState) -> list[Change]:
    """Carry foreign-holdings (PFIC / §988) per-fund history forward. Surfaces as a
    single ``ask`` change over ``foreign_holdings_detail.holdings`` — the preparer
    must confirm each fund is still held and add this year's events before compute."""
    prior_holdings = _get_path(prior.profile, "foreign_holdings_detail.holdings")
    rolled = _roll_foreign_holdings_forward(prior_holdings, prior.year)
    if not rolled:
        return []
    current_value = _get_path(current.profile, "foreign_holdings_detail.holdings", default=[])
    # If the current profile already carries holdings (the preparer re-entered
    # them), never silently overwrite — surface for confirmation.
    return [Change(
        category="foreign_holdings",
        json_path="foreign_holdings_detail.holdings",
        target_file="taxpayer_profile.json",
        prior_value=rolled,
        current_value=current_value,
        action_suggested="ask",
        reason=("Prior-year foreign holdings carried forward (preceding_holding_years "
                "incremented, per-year distribution/sale events reset). Confirm each "
                "fund is still held and add THIS year's distribution total to "
                "distributions_by_year before /tax-compute runs §1291."),
    )]


def compute_diff(
    prior: PriorReturn,
    current: CurrentState,
    categories: set[str],
    tax_law_new: dict,
) -> dict[str, list[Change]]:
    out: dict[str, list[Change]] = {}
    if "carryforwards" in categories:
        out["carryforwards"] = _diff_carryforwards(prior, current)
    if "identity" in categories:
        out["identity"] = _diff_identity(prior, current)
    if "dependents" in categories:
        out["dependents"] = _diff_dependents(prior, current, tax_law_new)
    if "expected_sources" in categories:
        out["expected_sources"] = _diff_expected_sources(prior, current)
    if "foreign_holdings" in categories:
        out["foreign_holdings"] = _diff_foreign_holdings(prior, current)
    return out


# --- apply_changes -----------------------------------------------------------

@dataclass
class WritePaths:
    result: Path
    profile: Path


@dataclass
class ApplyReport:
    written: list[str] = field(default_factory=list)
    skipped_categories: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def apply_changes(
    changes: dict[str, list[Change]],
    category_decisions: dict[str, str],  # category -> "apply" | "skip"
    targets: WritePaths,
) -> ApplyReport:
    """Apply selected categories atomically. All-or-nothing across all selected categories."""
    report = ApplyReport()
    profile = _read_json(targets.profile)
    result = _read_json(targets.result)
    dirty: set = set()

    any_changes = False
    for category, change_list in changes.items():
        if category_decisions.get(category) != "apply":
            report.skipped_categories.append(category)
            continue
        for change in change_list:
            if change.action_suggested == "skip":
                report.warnings.append(f"{category}: skipped {change.json_path} ({change.reason})")
                continue
            target_doc = profile if change.target_file == "taxpayer_profile.json" else result
            dirty.add(change.target_file)
            if change.json_path.startswith("dependents["):
                # Dependents list: append/replace by index
                idx = int(change.json_path[len("dependents["):-1])
                deps = target_doc.setdefault("dependents", [])
                while len(deps) <= idx:
                    deps.append({})
                deps[idx] = change.prior_value
            else:
                _set_path(target_doc, change.json_path, change.prior_value)
            report.written.append(f"{change.target_file}:{change.json_path}")
            any_changes = True

    if not any_changes:
        return report

    # ONE WRITER, including for the two-file case. This was the last hand-rolled
    # temp-then-rename in the engine, kept out of V10's first half because the
    # single-file helper could not express a transaction. `secure_fs.atomic_paths`
    # is the shape that can, and moving here fixed three things the local copy
    # had: a FIXED scratch name, so two applies running at once wrote the same
    # file and one published the other's bytes; a rollback via `write_bytes`,
    # which can half-succeed; and a handler that read the target back to decide
    # whether to restore, so a read that itself failed escaped the handler.
    #
    # ONLY THE DOCUMENTS THAT CHANGED ARE PUBLISHED, and that is not a
    # micro-optimisation. Every `FIELD_MAP` entry targets the profile today, so
    # the old code re-serialised `result.json` and republished it unchanged in
    # intent but CHANGED IN BYTES — which moves its digest, and V8's receipt
    # binds the tie-out gate's verdict to exactly that digest. A rollover run
    # after a compute therefore invalidated the receipt and blocked
    # `--filing-package`, for a file it had not edited.
    #
    # It also collapses the residual. `atomic_paths` cannot close the window a
    # CRASH between two renames leaves open; publishing one file means there is
    # no window, and one file is what every call in the toolkit makes today. The
    # transaction stays for the day a `result.json` target lands in `FIELD_MAP`.
    to_write = [(targets.profile, profile)] if "taxpayer_profile.json" in dirty else []
    if "result.json" in dirty:
        to_write.append((targets.result, result))
    try:
        with atomic_paths(*[path for path, _ in to_write]) as temps:
            for temp, (_, document) in zip(temps, to_write):
                temp.write_text(json.dumps(document, indent=2), encoding="utf-8")
    except HalfApplied as e:
        raise RolloverError(
            f"Write failed and the rollback did not complete — the workspace is "
            f"HALF APPLIED and must not be trusted: {redact(e)}") from e
    except PublishedButNotSecured as e:
        raise RolloverError(
            f"Changes WERE applied, but a file could not be restricted to the "
            f"owner: {redact(e)}") from e
    except OSError as e:
        raise RolloverError(
            f"Atomic write failed, no changes applied: {redact(e)}") from e

    return report


# --- CLI entry point ----------------------------------------------------------

def _load_current_state(workspace: Path, year: int) -> CurrentState:
    result = _read_json(workspace / "result.json")
    profile = _read_json(workspace / "taxpayer_profile.json")
    tax_law_path = workspace / f"tax_law_{year}.json"
    tax_law = _read_json(tax_law_path) if tax_law_path.exists() else {}
    return CurrentState(result=result, profile=profile, tax_law=tax_law)


def _validate_subpath(candidate: Path, workspace: Path) -> Path:
    """Ensure candidate is under workspace (no path traversal)."""
    candidate = candidate.resolve()
    workspace = workspace.resolve()
    try:
        candidate.relative_to(workspace)
    except ValueError:
        raise RolloverError(
            f"Path {redact(candidate)} is outside workspace "
            f"{redact(workspace)}")
    return candidate


def _validate_year_arg(value: str) -> int:
    try:
        year = int(value)
    except ValueError:
        raise RolloverError(
            f"Year must be a 4-digit integer, got {redact(repr(value))}")
    if not (2000 <= year <= 2099):
        raise RolloverError(f"Year {year} out of range (2000-2099)")
    return year


ROLLOVER_CATEGORIES = frozenset({
    "carryforwards",
    "identity",
    "dependents",
    "expected_sources",
    "foreign_holdings",
})


def _parse_categories(value: str) -> set[str]:
    """Normalize the CLI list and reject tokens the diff engine would ignore."""
    categories = [category.strip() for category in value.split(",")]
    if not categories or any(not category for category in categories):
        raise RolloverError("Categories must be a non-empty comma-separated list")
    unknown = sorted(set(categories) - ROLLOVER_CATEGORIES)
    if unknown:
        raise RolloverError(
            "Unknown rollover categories: " + ", ".join(unknown)
        )
    return set(categories)


def _change_to_dict(c: Change) -> dict:
    return {
        "category": c.category,
        "json_path": c.json_path,
        "target_file": c.target_file,
        "prior_value": c.prior_value,
        "current_value": c.current_value,
        "action_suggested": c.action_suggested,
        "reason": c.reason,
    }


def _main(argv: list[str]) -> int:
    # IMPORTED HERE, not at module scope: `workspace` imports `_validate_subpath`
    # from this module, so a top-level import back would be a cycle. The CLI is
    # the only part of this file that needs it, which is what makes the local
    # import the honest shape rather than a workaround.
    from agents.lib.workspace import (
        WorkspaceError,
        add_workspace_argument,
        resolve_under_workspace,
        hold_workspace,
    resolve_workspace,
    )

    import argparse
    parser = argparse.ArgumentParser(prog="agents.lib.rollover")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for cmd in ("diff", "apply"):
        p = sub.add_parser(cmd)
        p.add_argument("--from", dest="src", required=True)
        add_workspace_argument(p, "--to", dest="dst")
        p.add_argument("--year", required=True)
        if cmd == "diff":
            p.add_argument("--json", action="store_true")
            p.add_argument("--categories", default="carryforwards,identity,dependents,expected_sources,foreign_holdings")
        else:
            p.add_argument("--categories", required=True)
    args = parser.parse_args(argv)

    try:
        year = _validate_year_arg(args.year)
        categories = _parse_categories(args.categories)
        # `--to` IS the workspace under an older name, kept as an alias so the
        # documented command still runs; `--from archive/2025` resolves under
        # it rather than under whatever directory the command was run from.
        workspace = resolve_workspace(args.dst).resolve()
        hold_workspace(workspace, "rollover")
        src = resolve_under_workspace(args.src, workspace)
        # CLI's --year is the NEW year; archive should contain prior return (year - 1)
        prior = load_prior(src, year - 1)
        # Inside the boundary: the workspace files go through the same
        # non-finite refusal as the archive, and it must print as one line.
        current = _load_current_state(workspace, year)
        diff = compute_diff(prior, current, categories=categories,
                            tax_law_new=current.tax_law)
    except (WorkspaceError, RolloverError) as e:
        print(f"ERROR: {redact(e)}", file=sys.stderr)
        return 1

    if args.cmd == "diff":
        if args.json:
            payload = {cat: [_change_to_dict(c) for c in changes] for cat, changes in diff.items()}
            print(json.dumps(payload, indent=2, default=str))
        else:
            for cat, changes in diff.items():
                print(f"\n## {cat}")
                for c in changes:
                    # The VALUES stay raw, and that is deliberate: this is
                    # the diff a user approves field by field, so it has to
                    # show what it is proposing, and redact() masks any run
                    # of nine or more digits — a legitimate nine-figure
                    # amount would print as [REDACTED]. Identity is stored
                    # masked already (ssn_last4 holds XXX-XX-1234, which
                    # §1 requires and which redact() leaves alone). The
                    # json_path is a path and is redacted.
                    print(f"  [{c.action_suggested}] {redact(c.json_path)}: "
                          f"{c.prior_value!r} (currently {c.current_value!r})"
                          f" — {c.reason}")
        return 0

    # apply
    decisions = {cat: "apply" for cat in categories}
    targets = WritePaths(result=workspace / "result.json", profile=workspace / "taxpayer_profile.json")
    try:
        report = apply_changes(diff, category_decisions=decisions, targets=targets)
    except (WorkspaceError, RolloverError) as e:
        print(f"ERROR: {redact(e)}", file=sys.stderr)
        return 1
    print(f"Applied {len(report.written)} changes")
    for line in report.written:
        # Each line is "{target_file}:{json_path}" — a runtime path.
        print(f"  + {redact(line)}")
    for w in report.warnings:
        print(f"  ! {w}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
