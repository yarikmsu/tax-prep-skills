#!/usr/bin/env python3
"""Tax law data fetcher — provides cached tax rates/brackets/thresholds.

IMPORTANT: This module provides structure and caching for tax rates that are
fetched at runtime via WebSearch/WebFetch by Claude Code skills. The only
hardcoded values are structural constants that rarely change:
  - Additional Medicare Tax: 0.9%, thresholds $200K/$250K/$125K
  - NIIT: 3.8%, thresholds $200K/$250K/$125K
  - FBAR threshold: $10,000
  - SE tax: 15.3% rate, 92.35% net earnings factor
  - FTC simplified limits: $300 single / $600 MFJ
  - Form 8938 thresholds (by filing status and residency)
  - QBI deduction rate: 20%
All bracket/deduction/credit amounts are None until fetched from IRS.gov.

The JSON file (tax_law_<year>.json) is the cache. If missing, skills re-fetch.
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from datetime import datetime


def get_tax_law_path(year: int, workspace: str = ".") -> Path:
    """Return path to tax law cache file.

    Validates that year is a safe integer and that the resulting path
    stays within the workspace directory (prevents path traversal).
    """
    year = int(year)
    if not (2000 <= year <= 2099):
        raise ValueError(f"Year must be 2000-2099, got {year}")
    base = Path(workspace).resolve()
    result = base / f"tax_law_{year}.json"
    if not result.resolve().is_relative_to(base):
        raise ValueError(f"Path traversal detected: {result} escapes workspace {base}")
    return result


def load_tax_law(year: int, workspace: str = ".") -> dict | None:
    """Load cached tax law data. Returns None if not cached or corrupt.

    If the cache file exists but contains invalid JSON, it is removed
    and None is returned so the caller can re-fetch.
    """
    path = get_tax_law_path(year, workspace)
    if not path.exists():
        return None
    if path.is_symlink():
        raise ValueError(
            f"{path} is a symlink — refusing to read for security. "
            "Remove the symlink and re-run /tax-setup to re-fetch tax law data."
        )
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        # Corrupt cache — remove so caller re-fetches, but warn
        print(
            f"WARNING: Corrupt tax law cache at {path} ({type(e).__name__}). "
            "Removing so data can be re-fetched.",
            file=sys.stderr,
        )
        try:
            path.unlink(missing_ok=True)
        except OSError as unlink_err:
            print(
                f"WARNING: Could not remove corrupt cache {path}: {unlink_err}. "
                "Manual deletion may be required.",
                file=sys.stderr,
            )
        return None


def save_tax_law(year: int, data: dict, workspace: str = ".") -> str:
    """Save tax law data to cache file atomically. Returns path.

    Uses write-to-temp-then-rename to prevent corrupt partial writes.
    Makes a shallow copy of data before adding _meta, so the caller's
    original dict is not mutated. Nested dicts are shared, not deep-copied.
    """
    path = get_tax_law_path(year, workspace)

    # Copy to avoid mutating caller's dict
    to_save = dict(data)
    to_save["_meta"] = {
        "tax_year": year,
        "fetched": datetime.now().isoformat(),
        "source": "IRS.gov / state tax authority / Claude Code WebSearch",
    }

    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write: temp file in same directory, then rename
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(to_save, f, indent=2)
        os.replace(tmp_path, str(path))
    except BaseException:
        # BaseException (not Exception) intentional: ensures temp file cleanup
        # even on KeyboardInterrupt/SystemExit. Always re-raises after cleanup.
        try:
            os.unlink(tmp_path)
        except OSError as cleanup_err:
            print(f"WARNING: Could not remove temp file {tmp_path}: {cleanup_err}",
                  file=sys.stderr)
        raise

    return str(path)


def create_empty_tax_law(year: int) -> dict:
    """Create empty tax law structure for a given year.

    Skills populate this by fetching from IRS.gov via WebSearch.
    All values are None until fetched — skills MUST fill them in.
    """
    return {
        "tax_year": year,
        "federal": {
            "filing_statuses": {
                "single": {
                    "standard_deduction": None,
                    "brackets": [],  # [{"rate": 0.10, "min": 0, "max": 11925}, ...]
                },
                "mfj": {
                    "standard_deduction": None,
                    "brackets": [],
                },
                "mfs": {
                    "standard_deduction": None,
                    "brackets": [],
                },
                "hoh": {
                    "standard_deduction": None,
                    "brackets": [],
                },
            },
            "child_tax_credit": {
                "amount_per_child": None,
                "phase_out_start": {"single": None, "mfj": None},
                "refundable_limit": None,
            },
            "additional_medicare_tax": {
                "rate": 0.009,
                "threshold": {"single": 200000, "mfj": 250000, "mfs": 125000},
            },
            "niit": {
                "rate": 0.038,
                "threshold": {"single": 200000, "mfj": 250000, "mfs": 125000},
            },
            # IRMAA = Medicare Part B/D premium surcharge driven by MAGI from 2 years
            # prior. Tiers are MAGI lower-bounds with the per-beneficiary ANNUAL surcharge
            # (Part B + Part D) above the standard premium. Empty until /tax-setup fetches;
            # /tax-roth-convert degrades to "IRMAA not modeled" when tiers are empty.
            # Each tier: {"magi_threshold": <lower bound>, "annual_surcharge_per_person": <$>}.
            "irmaa": {
                "lookback_years": 2,
                "tiers": {"single": [], "mfj": []},
            },
            "capital_gains_rates": {
                "zero_pct_limit": {"single": None, "mfj": None},
                "fifteen_pct_limit": {"single": None, "mfj": None},
                "twenty_pct_above": True,
            },
            "social_security": {
                "wage_base": None,
                "employee_rate": 0.062,
            },
            "se_tax": {
                "rate": 0.153,
                "net_earnings_factor": 0.9235,
            },
            "ira_limits": {
                "contribution_limit": None,
                "catch_up_50_plus": None,
                "traditional_deduction_phaseout": {
                    "mfj_covered": {"start": None, "end": None},
                    "mfj_spouse_not_covered": {"start": None, "end": None},
                    "single_covered": {"start": None, "end": None},
                },
                "roth_income_phaseout": {
                    "mfj": {"start": None, "end": None},
                    "single": {"start": None, "end": None},
                },
            },
            "hsa_limits": {
                "self_only": None,
                "family": None,
                "catch_up_55_plus": None,
            },
            "retirement_401k": {
                "elective_deferral": None,
                "catch_up_50_plus": None,
                "catch_up_60_63": None,  # SECURE 2.0 super catch-up
                "total_annual_limit": None,
                # SECURE 2.0 §603 mandatory-Roth-catchup prior-year-FICA-wage
                # threshold ($145k indexed from 2023; ~$150k for TY2026).
                # Indexes annually — /tax-retirement's built-in $150k default
                # is TY2026-only and hard-errors for TY2027+ if this is unset,
                # so /tax-setup must fetch + populate it for post-2026 years.
                "secure_2_0_high_earner_threshold": None,
            },
            "foreign_tax_credit_simplified": {
                "max_single": 300,
                "max_mfj": 600,
            },
            "form_8938_thresholds": {
                "mfj_us": {"year_end": 200000, "any_time": 300000},
                "single_us": {"year_end": 50000, "any_time": 75000},
                "mfj_foreign": {"year_end": 400000, "any_time": 600000},
                "single_foreign": {"year_end": 200000, "any_time": 300000},
            },
            "fbar_threshold": 10000,
            "qbi_deduction_rate": 0.20,
            # Section 199A taxable-income threshold (indexed annually; keyed
            # single/mfj like capital_gains_rates — map MFJ/QSS→mfj, else→single;
            # MFS uses the single-side figure). Above it the Form 8995-A
            # W-2-wage/UBIA limits and SSTB phase-out apply, which the simplified
            # compute_qbi_deduction does NOT model — it flags `over_threshold`
            # so /tax-compute can warn instead of silently overstating.
            "qbi_threshold": {"single": None, "mfj": None},
            # Federal SALT itemized-deduction cap for THIS cache's year, fetched
            # per-year because the OBBBA base ($40,000 / $20,000 MFS for
            # TY2025-2029, reverting to $10,000 / $5,000 in TY2030) is indexed
            # ~1%/yr for 2026-2029. The MAGI phasedown above $500K ($250K MFS)
            # is NOT modeled — callers caveat it. Consumers (ptet._salt_cap,
            # /tax-compute itemized-vs-standard guidance) fall back to the
            # unindexed statutory schedule when unset.
            "salt_cap": {"amount": None, "mfs_amount": None},
            # IRC §6621 federal underpayment rate (short-term AFR + 3%), set
            # PER CALENDAR QUARTER. Form 2210 accrues the tax year's penalty
            # across Apr 15 TY → Apr 15 TY+1, so the list must cover every
            # rate period in that span. Each entry:
            #   {"effective_from": "YYYY-MM-DD", "annual_rate": 0.08}
            # Ships empty — /tax-setup fetches (irs.gov quarterly interest
            # rate news releases); compute_form_2210 degrades to
            # penalty-not-computed (loud note, never guessed) when empty.
            "underpayment_rates": [],
        },
        "state": {},  # Populated per-state by /tax-setup
        "exchange_rates": {},  # e.g., {"EUR": <annual_avg_rate>} — fetched from IRS.gov yearly average rates
    }


def create_state_template(state: str) -> dict:
    """Create empty state tax law structure. Filled by WebSearch."""
    state_templates = {
        "CA": {
            "name": "California",
            "authority": "Franchise Tax Board (FTB)",
            "conforms_to_federal": False,
            "standard_deduction": {"single": None, "mfj": None, "hoh": None},
            "brackets": [],  # [{"rate": 0.01, "min": 0, "max": 10412}, ...]
            "personal_exemption": {"amount": None},
            "dependent_exemption": {"amount": None},
            "hsa_conformity": False,
            "hsa_note": "CA does not recognize HSA. Add back employer contributions and HSA earnings.",
            "mental_health_surcharge": {
                "rate": 0.01,
                "threshold": 1000000,
            },
            "sdi_rate": None,
            # SDI taxable wage ceiling. SB 951 REMOVED the ceiling starting
            # TY2024 — leave None for 2024+ (compute_excess_sdi then reports
            # the excess-SDI credit as not applicable, because without a
            # per-person maximum no excess can exist). Fetch the FTB cap only
            # for pre-2024 years.
            "sdi_wage_limit": None,
            # R&TC §17077 high-AGI itemized-deduction phase-out thresholds
            # (indexed annually). FTB publishes three tiers: Single/MFS
            # (key "single" — map MFS here), MFJ/QSS (key "mfj"), and HoH.
            # None until fetched — compute_ca_itemized then SKIPS the
            # phase-out with a loud overstatement note.
            "itemized_phaseout": {"single": None, "mfj": None, "hoh": None},
            "no_city_income_tax": True,
        },
        "NY": {
            "name": "New York",
            "authority": "Department of Taxation and Finance",
            "conforms_to_federal": True,
            "standard_deduction": {"single": None, "mfj": None},
            "brackets": [],
            "nyc_tax": {"applies": None, "brackets": []},
        },
    }
    return state_templates.get(state, {
        "name": state,
        "note": "State template not pre-built. Fetch rates via WebSearch.",
        "standard_deduction": {"single": None, "mfj": None},
        "brackets": [],
    })


_FRESHNESS_DAYS = 14  # Data older than this is considered STALE


def check_freshness(year: int, workspace: str = ".") -> dict:
    """Check whether cached tax law data is fresh enough for computation.

    "Fresh" means fetched within the last _FRESHNESS_DAYS days with all
    rate/bracket fields populated (no None values, no empty bracket lists).

    Returns dict with:
      status: FRESH | STALE | INCOMPLETE | NOT_FOUND
      age_days: days since last fetch (or None)
      null_fields: list of field paths that are still None
      recommendation: human-readable next step
    """
    data = load_tax_law(year, workspace)
    if data is None:
        return {
            "status": "NOT_FOUND",
            "age_days": None,
            "null_fields": [],
            "recommendation": f"No tax law data for {year}. Run /tax-setup to fetch.",
        }

    # Compute age from _meta.fetched
    age_days = None
    meta = data.get("_meta", {})
    fetched_str = meta.get("fetched")
    if fetched_str:
        try:
            fetched_dt = datetime.fromisoformat(fetched_str)
            age_days = (datetime.now() - fetched_dt).days
        except (ValueError, TypeError) as e:
            # Malformed timestamp (e.g., manual edit or corrupt write). Treat as
            # STALE so caller re-fetches, which will also fix the timestamp.
            print(
                f"WARNING: Could not parse fetch timestamp '{fetched_str}': {e}. "
                "Treating data as STALE.",
                file=sys.stderr,
            )
            age_days = None

    # Walk the structure to find None values (skip _meta and non-rate keys)
    null_fields = []
    # state.sdi_wage_limit is LEGITIMATELY null for TY2024+: SB 951 removed
    # the CA SDI wage ceiling, and compute_excess_sdi treats None as
    # not-applicable for those years. Without this exception a correctly
    # populated 2024+ cache reports INCOMPLETE forever and skills keep asking
    # to re-fetch a value that must not exist. Pre-2024 caches still flag it
    # (the ceiling existed and must be fetched). Keep the effective year in
    # sync with tax_computations._SDI_NO_CEILING_FROM.
    _sdi_null_is_valid = (isinstance(data.get("tax_year"), int)
                          and not isinstance(data.get("tax_year"), bool)
                          and data["tax_year"] >= 2024)
    # MAINTENANCE: Two categories are skipped during None/empty scanning:
    #   1. Keys prefixed with "_" (metadata/comments) — auto-skipped
    #   2. Non-prefixed descriptive fields — add to _SKIP_KEYS below
    # Empty lists are always FLAGGED (not skipped) as they indicate missing data.
    # _SKIP_KEYS must stay in sync with descriptive fields in BOTH
    # create_empty_tax_law() AND create_state_template() (especially CA fields:
    # hsa_note, authority, conforms_to_federal, no_city_income_tax, hsa_conformity).
    # When adding a new state template, audit its descriptive keys and add them here.
    # Missing a descriptive field → false INCOMPLETE; adding a rate field → silent gap.
    _SKIP_KEYS = {"note", "hsa_note", "name", "authority",
                  "conforms_to_federal", "no_city_income_tax", "twenty_pct_above",
                  "applies", "hsa_conformity"}

    # Keys whose values must be dicts or lists, never scalars.
    # A scalar value here (int, float, str, bool) indicates either data corruption
    # (e.g., brackets: 0) or a type coercion failure (e.g., phase_out_start: "400000").
    _EXPECT_STRUCTURED = {"brackets", "threshold", "phaseout",
                          "phase_out_start", "roth_income_phaseout",
                          "traditional_deduction_phaseout", "filing_statuses",
                          "itemized_phaseout"}

    def _walk(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k.startswith("_") or k in _SKIP_KEYS:
                    continue
                if k == "sdi_wage_limit" and v is None and _sdi_null_is_valid:
                    continue
                # underpayment_rates may legitimately stay empty: a
                # withholding-only taxpayer never needs the §6621 timeline,
                # and compute_form_2210 degrades LOUDLY at the point of need
                # (penalty-not-computed note) instead of guessing. Flagging
                # it here would nag INCOMPLETE forever (the sdi_wage_limit
                # precedent). /tax-compute step 9.5 prompts the fetch when a
                # penalty actually applies.
                if k == "underpayment_rates" and isinstance(v, list) and not v:
                    continue
                child_path = f"{path}.{k}" if path else k
                if v is None:
                    if k in _EXPECT_STRUCTURED:
                        null_fields.append(f"{child_path} (expected dict/list, got None)")
                    else:
                        null_fields.append(child_path)
                elif k in _EXPECT_STRUCTURED and not isinstance(v, (dict, list)):
                    null_fields.append(f"{child_path} (expected dict/list, got {type(v).__name__}: {v!r})")
                else:
                    _walk(v, child_path)
        elif isinstance(obj, list):
            if len(obj) == 0:
                null_fields.append(f"{path} (empty list)")
            else:
                for i, item in enumerate(obj):
                    _walk(item, f"{path}[{i}]")

    _walk(data)

    # Inverse of the sdi_wage_limit null exception above: the walker only
    # visits PRESENT keys, so a pre-2024 CA cache created before the field
    # existed (key absent entirely) would report FRESH even though the cap is
    # required for the refundable excess-SDI credit — /tax-compute would later
    # hard-error with no refetch prompt. Flag the missing key explicitly.
    state_block = data.get("state")
    if (isinstance(state_block, dict)
            and state_block.get("name") == "California"
            and isinstance(data.get("tax_year"), int)
            and not isinstance(data.get("tax_year"), bool)
            and data["tax_year"] < 2024
            and "sdi_wage_limit" not in state_block):
        null_fields.append(
            "state.sdi_wage_limit (key absent — the pre-2024 SDI wage ceiling "
            "is required for the excess-SDI credit)")

    # Same class of guard for the CA itemized phase-out: a cache created
    # before the D1 schema change has no itemized_phaseout key at all, so
    # the walker would report FRESH while compute_ca_itemized silently
    # skips the §17077 phase-out (overstating high-AGI CA itemized
    # deductions). No year gate — the phase-out long predates any
    # supported year.
    if (isinstance(state_block, dict)
            and state_block.get("name") == "California"
            and "itemized_phaseout" not in state_block):
        null_fields.append(
            "state.itemized_phaseout (key absent — the R&TC §17077 "
            "thresholds are required for the CA itemized phase-out)")

    # Flag empty exchange_rates — skills depend on rates for foreign conversions
    if isinstance(data.get("exchange_rates"), dict) and not data["exchange_rates"]:
        null_fields.append("exchange_rates (no rates loaded)")

    # Flag empty state section if state data was expected
    if isinstance(data.get("state"), dict) and not data["state"]:
        null_fields.append("state (no state tax data configured)")

    # Determine status
    if null_fields:
        status = "INCOMPLETE"
        recommendation = (
            f"Tax law data has {len(null_fields)} unfilled field(s). "
            "Re-fetch missing values via WebSearch."
        )
    elif age_days is None:
        status = "STALE"
        recommendation = (
            "Tax law data has no valid fetch timestamp. "
            "Re-fetch to establish freshness baseline."
        )
    elif age_days > _FRESHNESS_DAYS:
        status = "STALE"
        recommendation = (
            f"Tax law data is {age_days} days old. "
            "WebSearch for new legislation since fetch date, re-fetch if found."
        )
    else:
        status = "FRESH"
        recommendation = "Tax law data is current. Proceed with computation."

    return {
        "status": status,
        "age_days": age_days,
        "null_fields": null_fields,
        "recommendation": recommendation,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python tax_law_fetcher.py <create|load|freshness|state> <year> [state|workspace] [workspace]")
        sys.exit(1)

    cmd = sys.argv[1]
    if len(sys.argv) > 2:
        try:
            year = int(sys.argv[2])
        except ValueError:
            print(f"Error: '{sys.argv[2]}' is not a valid year. Provide a 4-digit year (e.g., 2025).",
                  file=sys.stderr)
            sys.exit(1)
        if not (2000 <= year <= 2099):
            print(f"Error: Year {year} out of range. Must be 2000-2099.",
                  file=sys.stderr)
            sys.exit(1)
    else:
        year = datetime.now().year - 1

    if cmd == "create":
        data = create_empty_tax_law(year)
        workspace = sys.argv[3] if len(sys.argv) > 3 else "."
        path = save_tax_law(year, data, workspace)
        print(f"Created empty tax law template: {path}")
    elif cmd == "load":
        workspace = sys.argv[3] if len(sys.argv) > 3 else "."
        data = load_tax_law(year, workspace)
        if data:
            print(json.dumps(data, indent=2))
        else:
            print(f"No cached tax law for {year}. Run /tax-setup to fetch.")
    elif cmd == "freshness":
        workspace = sys.argv[3] if len(sys.argv) > 3 else "."
        result = check_freshness(year, workspace)
        print(json.dumps(result, indent=2))
    elif cmd == "state":
        state = sys.argv[3] if len(sys.argv) > 3 else "CA"
        template = create_state_template(state)
        print(json.dumps(template, indent=2))
    else:
        print(f"Error: Unknown command '{cmd}'. Valid commands: create, load, freshness, state",
              file=sys.stderr)
        sys.exit(1)
