"""Medicare IRMAA tier validation and per-beneficiary surcharge arithmetic.

No premium or MAGI amount is embedded here. The tier counts, strict lower
boundaries and inclusive top boundary are the structure of 42 USC 1395r(i)(3).
The rounding rule is (i)(5)(B). Sources must explicitly cover the premium year.
"""

import math

import agents.lib  # noqa: F401 — interpreter guard
from agents.lib.tax_computations._shared import _normalize_filing_status


def irmaa_status_key(filing_status: str, lived_apart: bool | None = None) -> str:
    fs = _normalize_filing_status(filing_status)
    if fs == "MFS":
        if type(lived_apart) is not bool:
            raise ValueError("MFS IRMAA needs an explicit lived-apart-all-year answer")
        return "single" if lived_apart else "mfs"
    # A qualifying surviving spouse uses the unmarried Medicare table,
    # regardless of the joint ordinary-income brackets on their return.
    return "mfj" if fs == "MFJ" else "single"


def validate_irmaa_tiers(raw, premium_year, *, separate=False) -> list[dict]:
    if raw is None or raw == []:
        return []
    if not isinstance(raw, list):
        raise ValueError("IRMAA tiers must be a list")
    if type(premium_year) is not int or premium_year < 2007:
        raise ValueError("IRMAA tier validation requires its covered premium year from 2007")
    # The fifth ordinary tier starts in 2019. The 2018 compression leaves
    # cohabiting MFS a single surcharge row; other years have two. These are
    # table structures, not fetched thresholds or premiums.
    count = (1 if premium_year == 2018 else 2) if separate else (5 if premium_year >= 2019 else 4)
    if len(raw) != count:
        raise ValueError(f"IRMAA requires the complete statutory {count}-tier table; refetch the premium-year table")
    cleaned = []
    for tier in raw:
        if not isinstance(tier, dict):
            raise ValueError("IRMAA tier must be an object")
        item = {}
        for key in ("magi_threshold", "annual_surcharge_per_person"):
            value = tier.get(key)
            try:
                number = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else math.nan
            except OverflowError:
                number = math.nan
            if not math.isfinite(number) or number <= 0:
                raise ValueError(f"IRMAA tier has a non-numeric, nonfinite or nonpositive {key}")
            item[key] = number
        cleaned.append(item)
    cleaned.sort(key=lambda t: t["magi_threshold"])
    for index, item in enumerate(cleaned):
        # (i)(5)(A) freezes 2018/2019 rather than indexing: their published
        # tables retain half-thousand boundaries. (i)(5)(B) rounds indexed
        # years to thousands. Neither encoding admits a first-dollar +1. From
        # 2028 the joint top tier is 150% of an indexed, thousand-rounded
        # figure ((i)(3)(C)(ii), (i)(5)(C)), so it may land on a half-thousand.
        top_of_joint = not separate and premium_year >= 2028 and index == len(cleaned) - 1
        quantum = 500 if premium_year in {2018, 2019} or top_of_joint else 1000
        if item["magi_threshold"] % quantum:
            raise ValueError("IRMAA threshold must be the printed rounded boundary, never the old first-dollar value; refetch")
    if any(b["magi_threshold"] <= a["magi_threshold"]
           or b["annual_surcharge_per_person"] <= a["annual_surcharge_per_person"]
           for a, b in zip(cleaned, cleaned[1:])):
        raise ValueError("IRMAA thresholds and surcharges must increase strictly without duplicates")
    return cleaned


def read_irmaa_tiers(tax_law: dict, filing_status: str, *, lived_apart=None) -> list[dict]:
    fs = _normalize_filing_status(filing_status)
    fed = tax_law.get("federal", {})
    if not isinstance(fed, dict):
        raise ValueError("IRMAA federal cache must be an object")
    block = fed.get("irmaa")
    if block is None:
        return []
    if not isinstance(block, dict) or not isinstance(block.get("tiers"), dict):
        raise ValueError("IRMAA cache and tiers must be objects")
    if all(value == [] for value in block["tiers"].values()):
        return []
    key = irmaa_status_key(fs, lived_apart)
    return validate_irmaa_tiers(block["tiers"].get(key), tax_law.get("tax_year"), separate=key == "mfs")


def irmaa_tiers_reached(magi: float, tiers: list[dict], *, separate=False, premium_year=2020) -> int:
    try:
        finite = math.isfinite(magi)
    except (OverflowError, TypeError):
        finite = False
    if isinstance(magi, bool) or not isinstance(magi, (int, float)) or not finite:
        raise ValueError("IRMAA MAGI must be a finite number")
    cleaned = validate_irmaa_tiers(tiers, premium_year, separate=separate)
    top = len(cleaned) - 1  # Complete statutory count already enforced above.
    inclusive_top = premium_year >= 2019
    return sum(magi > t["magi_threshold"] or (i == top and magi == t["magi_threshold"])
               for i, t in enumerate(cleaned)) if inclusive_top else sum(
                   magi > t["magi_threshold"] for t in cleaned)


def irmaa_surcharge(magi: float, tiers: list[dict], *, separate=False, premium_year=2020) -> float:
    cleaned = validate_irmaa_tiers(tiers, premium_year, separate=separate)
    reached = irmaa_tiers_reached(magi, cleaned, separate=separate, premium_year=premium_year)
    return round(cleaned[reached - 1]["annual_surcharge_per_person"], 2) if reached else 0.0
