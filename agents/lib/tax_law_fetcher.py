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
            # Form 6251 AMT parameters (ALL indexed annually — Rev. Proc.
            # inflation adjustments): exemption amount and its 25%-phase-out
            # start by status (HoH uses the SINGLE row — the form groups
            # them; QSS uses mfj), and the 26%→28% rate breakpoint (one
            # figure, halved for MFS by compute_form_6251). None until fetched;
            # compute_form_6251 hard-errors rather than guess — an unfetched
            # AMT table must not silently produce $0 AMT for an ISO
            # exerciser.
            "amt": {
                "exemption": {"single": None, "mfj": None, "mfs": None},
                "phaseout_start": {"single": None, "mfj": None, "mfs": None},
                "rate_28_breakpoint": None,
                # §55(d)(3) exemption phase-out rate. Ships None: the statutory
                # schedule (25% ≤TY2025, 50% TY2026+ per OBBBA §70107) is
                # built into compute_form_6251, so None is VALID (do NOT nag —
                # the salt_cap freshness treatment). Fetch only to OVERRIDE the
                # built-in schedule; a corrupt value (non-fraction) hard-errors.
                "phaseout_rate": None,
            },
            # OBBBA Schedule 1-A deductions (I2) — tips (§224), overtime
            # premium (§225), car-loan interest (§70203), enhanced senior
            # (§70103). ALL indexed → fetched; the phase-out MECHANICS
            # ($100/$1,000, $200/$1,000, 6%) are structural constants in
            # compute_schedule_1a. Ships all-None: these are OPT-IN per
            # taxpayer, so a null block is the DEFAULT (freshness does not nag —
            # the phaseout_rate/underpayment_rates precedent); the engine
            # hard-errors at the point of use when a taxpayer actually has one
            # of these items but the corresponding figure is unfetched.
            # tips_cap / car_loan_cap / senior_amount_per_person are scalars;
            # overtime_cap and the three phase-out thresholds are keyed
            # single/mfj (MFJ/QSS→mfj, else→single; MFS is gated to $0).
            "schedule_1a": {
                "tips_cap": None,
                "overtime_cap": {"single": None, "mfj": None},
                "car_loan_cap": None,
                "senior_amount_per_person": None,
                "tips_overtime_phaseout_start": {"single": None, "mfj": None},
                "car_loan_phaseout_start": {"single": None, "mfj": None},
                "senior_phaseout_start": {"single": None, "mfj": None},
            },
            # §170(p) non-itemizer charitable deduction (OBBBA §70424, TY2026+).
            # OPT-IN: null block is the DEFAULT (compute_charitable_non_itemizer
            # hard-errors at use when a standard-deduction filer has cash gifts
            # and the cap is unfetched). amount = non-joint; joint_amount = MFJ.
            "charitable_non_itemizer": {"amount": None, "joint_amount": None},
            # §70105 minimum QBI deduction (OBBBA, TY2026+). OPT-IN: taxpayer-
            # favorable, so compute_qbi_deduction DEGRADES (no floor) when this
            # is unfetched rather than guessing an indexed figure.
            "qbi_minimum": {"income_threshold": None, "amount": None},
        },
        "state": {},  # Populated per-state by /tax-setup
        "exchange_rates": {},  # e.g., {"EUR": <annual_avg_rate>} — fetched from IRS.gov yearly average rates
        # H4 source pinning: block-path → authoritative URL the figure was
        # fetched from (e.g. "federal.filing_statuses": "https://www.irs.gov/...").
        # /tax-setup records the primary source per fetched block; check_freshness
        # reports a NON-AUTHORITATIVE recorded URL as INCOMPLETE and lists
        # populated-but-unpinned core blocks as an advisory.
        "_sources": {},
    }


def create_state_template(state: str) -> dict:
    """Create empty state tax law structure. Filled by WebSearch."""
    state_templates = {
        "CA": {
            # 2-letter USPS code — load-bearing for /tax-project's CA 540-ES
            # block (projection.build_report gates on state.code == "CA").
            "code": "CA",
            "name": "California",
            "authority": "Franchise Tax Board (FTB)",
            "conforms_to_federal": False,
            "standard_deduction": {"single": None, "mfj": None, "hoh": None},
            "brackets": [],  # [{"rate": 0.01, "min": 0, "max": 10412}, ...]
            "personal_exemption": {"amount": None},
            "dependent_exemption": {"amount": None},
            "hsa_conformity": False,
            "hsa_note": "CA does not recognize HSA. Add back employer contributions and HSA earnings.",
            # Statutory-structural (R&TC §17043): the 1% Mental Health
            # Services Tax rate and its $1,000,000 taxable-income threshold
            # are set in statute (Prop 63, 2004) and not indexed — inline
            # constants with citation, per the repo rule for non-indexed law.
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
            "code": "NY",
            "name": "New York",
            "authority": "Department of Taxation and Finance",
            "conforms_to_federal": True,
            "standard_deduction": {"single": None, "mfj": None},
            "brackets": [],
            "nyc_tax": {"applies": None, "brackets": []},
        },
    }
    return state_templates.get(state, {
        "code": state,
        "name": state,
        "note": "State template not pre-built. Fetch rates via WebSearch.",
        "standard_deduction": {"single": None, "mfj": None},
        "brackets": [],
    })


_FRESHNESS_DAYS = 14  # Data older than this is considered STALE

# H4 source pinning: authoritative sources a fetched rate/bracket may be pinned
# to. An LLM WebSearch can surface a blog or a stale aggregator; recording a URL
# from a non-primary host is treated as UNVERIFIED (→ INCOMPLETE) so a
# non-primary figure can't silently flow into a filed return.
#
# The `.gov` TLD is restricted to bona-fide US government entities (managed by
# CISA), so ANY `.gov` host is authoritative — this covers every federal agency
# (irs.gov, ssa.gov, treasury.gov, congress.gov, govinfo.gov, federalregister.
# gov) AND every state tax authority (ftb.ca.gov, edd.ca.gov, tax.ny.gov,
# nj.gov, revenue.wi.gov, …) without enumerating 50 states. Non-`.gov`
# authoritative legal sources are listed explicitly below (Cornell LII hosts the
# U.S. Code on .edu). A lookalike like `irs.gov.evil.com` ends in `.com`, not
# `.gov`, so it is correctly rejected.
_AUTHORITATIVE_NONGOV_DOMAINS = frozenset({
    "law.cornell.edu",    # LII U.S. Code
})

# US state/territory display-name → USPS code, so a legacy state cache with only
# `name: "California"` (no `code`) normalizes to the documented `state.CA` pin
# key rather than a bogus `state.California` advisory.
_STATE_NAME_TO_CODE = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "district of columbia": "DC", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN",
    "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI",
    "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}


def _state_pin_code(state: dict) -> str:
    """The documented `state.<CODE>` pin suffix for a single-state cache block:
    the explicit 2-letter `code` if valid, else the USPS code mapped from the
    display `name`, else 'state' (bare) so an unrecognized legacy block never
    emits a display-name advisory that a real code pin can't satisfy."""
    code = state.get("code")
    if isinstance(code, str) and len(code) == 2 and code.isalpha():
        return code.upper()
    name = state.get("name")
    if isinstance(name, str):
        mapped = _STATE_NAME_TO_CODE.get(name.strip().lower())
        if mapped:
            return mapped
    return "state"

# Federal blocks that SHOULD carry a `_sources` pin are DERIVED from the cache
# (every populated structured `federal.<block>`), not a fixed short list — a
# hardcoded pair would leave child_tax_credit / additional_medicare_tax / niit /
# amt / salt_cap unaudited even though they feed scenario/projection/compute.
# Absence of a pin is an advisory (unpinned), not a status flip; a recorded
# non-authoritative URL is the harder INCOMPLETE failure. Scalars (e.g.
# fbar_threshold) are lower-risk statutory constants and are not audited — only
# structured rate/bracket/threshold blocks (dict/list) are.


def _source_host_authoritative(url) -> bool:
    """True iff `url` is a non-empty string whose host is a US government (`.gov`)
    site or an explicit non-`.gov` authoritative legal source. Anything else — a
    blog, aggregator, news site, a bare string, a malformed URL — is NOT
    authoritative. `.gov` is registry-restricted to government entities, so a
    lookalike (`irs.gov.evil.com`) resolves to a `.com` host and is rejected."""
    if not isinstance(url, str) or not url.strip():
        return False
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    # Must be a retrievable web URL — reject javascript:/file:/ftp:/data: even
    # when a government hostname is present (`javascript://www.irs.gov/x` parses
    # a hostname but is not a fetchable authoritative source).
    if parsed.scheme not in ("https", "http"):
        return False
    host = parsed.hostname
    if not host:
        return False
    host = host.lower()
    if host == "gov" or host.endswith(".gov"):
        return True
    return any(host == d or host.endswith("." + d)
               for d in _AUTHORITATIVE_NONGOV_DOMAINS)


def _has_real_data(node) -> bool:
    """True iff `node` contains at least one real (non-None, non-empty) leaf.
    A create_empty_tax_law scaffold block (a dict whose leaves are all None,
    e.g. an unfetched `federal.amt`) is NOT real data — checking only that the
    top-level dict is non-empty would wrongly flag it as a populated block."""
    if node is None:
        return False
    if isinstance(node, dict):
        return any(_has_real_data(v) for v in node.values())
    if isinstance(node, list):
        return any(_has_real_data(v) for v in node)
    if isinstance(node, str):
        return len(node.strip()) > 0
    return True  # a number/bool leaf is real data


def _block_populated(data: dict, dotted: str) -> bool:
    """Whether a dotted block path holds real (non-None, non-empty) data —
    scaffold dicts with all-None leaves do NOT count."""
    node = data
    for seg in dotted.split("."):
        if not isinstance(node, dict) or seg not in node:
            return False
        node = node[seg]
    return _has_real_data(node)


def _fetched_federal_blocks(data: dict) -> list:
    """Populated `federal.<block>` paths that genuinely need a source pin — the
    FETCHED, indexed rate/bracket/threshold tables. A block qualifies iff it is
    STRUCTURED (dict/list) AND it DIFFERS from its create_empty_tax_law seed
    (i.e. a fetched slot was actually filled). That equality test cleanly
    excludes, without an enumerated list: fully-seeded structural-constant
    blocks (se_tax, niit, additional_medicare_tax, foreign_tax_credit_simplified,
    form_8938_thresholds — bill-fixed rates the template ships complete) AND
    not-yet-fetched blocks whose only content is a structural sub-constant
    (an empty `irmaa` still equals its {lookback_years, tiers:[]} seed). Blocks
    the template doesn't seed at all (e.g. `amt`, added by /tax-setup) are
    audited once populated. Scalars (fbar_threshold) are excluded as lower-risk
    statutory constants."""
    fed = data.get("federal")
    if not isinstance(fed, dict):
        return []
    ref = create_empty_tax_law(2025).get("federal", {})  # structure is year-independent
    blocks = []
    for key, val in fed.items():
        if not isinstance(key, str) or key.startswith("_"):
            continue
        if not isinstance(val, (dict, list)):
            continue  # scalars: lower-risk statutory constants, not audited
        if key in ref and val == ref[key]:
            continue  # unchanged seed → structural constant OR not yet fetched
        if not _block_populated(data, f"federal.{key}"):
            continue
        blocks.append(f"federal.{key}")
    return blocks


def verify_source_pinning(data: dict) -> dict:
    """H4: audit `_sources` (block-path → authoritative URL) against the cache.

    Returns {"non_authoritative": [(block, url), ...], "unpinned": [block, ...]}.
      - non_authoritative: a `_sources` entry whose URL host is NOT an
        authoritative domain — the hard failure (→ INCOMPLETE): a recorded but
        non-primary source is affirmatively suspect.
      - unpinned: a populated core block (federal + each configured state) with
        NO `_sources` entry — an advisory (legacy caches predate pinning), not
        a status flip.
    """
    sources = data.get("_sources")
    if sources is not None and not isinstance(sources, dict):
        raise ValueError(f"_sources must be an object, got {type(sources).__name__}")
    sources = sources or {}
    non_authoritative = []
    for block, url in sources.items():
        if isinstance(block, str) and block.startswith("_"):
            continue
        if not _source_host_authoritative(url):
            non_authoritative.append((block, url))
    # (source_key, data_path): every populated structured federal block pins by
    # its own path (derived from the cache, so child_tax_credit / niit / amt /
    # etc. are all audited, not just filing_statuses + capital_gains_rates). The
    # cache holds ONE state as a flat block under `state` (state.code,
    # state.brackets, …), NOT keyed by code — so it is a SINGLE expected pin
    # `state.<code>` (what /tax-setup records) whose DATA lives at `state`.
    # Iterating the state's field keys would emit bogus state.code/state.name/
    # state.brackets advisories and never credit the real state.CA pin.
    expected = [(b, b) for b in _fetched_federal_blocks(data)]
    state = data.get("state")
    if isinstance(state, dict) and _block_populated(data, "state"):
        code = _state_pin_code(state)
        src_key = f"state.{code}" if code != "state" else "state"
        expected.append((src_key, "state"))
    # exchange_rates is fetched data (IRS yearly-average rates) that directly
    # scales reported USD foreign income / FBAR values — audit it too.
    if _block_populated(data, "exchange_rates"):
        expected.append(("exchange_rates", "exchange_rates"))
    unpinned = [src for src, path in expected
                if _block_populated(data, path) and src not in sources]
    return {"non_authoritative": non_authoritative, "unpinned": unpinned}


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
                # federal.amt.phaseout_rate legitimately stays None: the
                # §55(d)(3) statutory schedule (25%/50% at TY2026 per §70107)
                # is built into compute_form_6251, so a null here is the
                # DEFAULT, not a gap — it's fetched only to OVERRIDE. Flagging
                # it would nag INCOMPLETE forever (the underpayment_rates /
                # sdi_wage_limit precedent). A non-null corrupt value still
                # hard-errors at compute time (_resolve_amt_phaseout_rate).
                if k == "phaseout_rate" and v is None:
                    continue
                # federal.schedule_1a is OPT-IN per taxpayer (tips/overtime/
                # car-loan/senior) — a null block is the DEFAULT, not a gap.
                # compute_schedule_1a hard-errors at the point of use when a
                # taxpayer actually has one of these items with an unfetched
                # figure, so nagging INCOMPLETE here for every return is wrong
                # (the phaseout_rate/underpayment_rates precedent). Skip the
                # whole subtree.
                if k == "schedule_1a" and isinstance(v, dict):
                    continue
                # federal.charitable_non_itemizer (§170(p)) and
                # federal.qbi_minimum (§70105) are OPT-IN TY2026+ blocks, same
                # as schedule_1a — a null block is the DEFAULT, and the compute
                # functions degrade/hard-error at the point of use. Skip both.
                if k in ("charitable_non_itemizer", "qbi_minimum") \
                        and isinstance(v, dict):
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

    # federal.amt absent-key guard (same class): a pre-D9 cache has no amt
    # key, so the walker reported FRESH while compute_form_6251 would only
    # hard-error at point of need with no earlier refetch prompt. No year
    # gate — the AMT exemption table exists every year.
    fed_block = data.get("federal")
    if isinstance(fed_block, dict) and "amt" not in fed_block:
        null_fields.append(
            "federal.amt (key absent — the AMT exemption/phase-out/"
            "breakpoint table is required for Form 6251)")

    # Flag empty exchange_rates — skills depend on rates for foreign conversions
    if isinstance(data.get("exchange_rates"), dict) and not data["exchange_rates"]:
        null_fields.append("exchange_rates (no rates loaded)")

    # Flag empty state section if state data was expected
    if isinstance(data.get("state"), dict) and not data["state"]:
        null_fields.append("state (no state tax data configured)")

    # H4 source pinning: a recorded but NON-AUTHORITATIVE source is INCOMPLETE
    # (a non-primary URL affirmatively taints the figure); populated-but-
    # unpinned core blocks are a softer advisory that never flips FRESH (legacy
    # caches predate the field). A malformed _sources object hard-errors.
    unpinned_sources = []
    try:
        pinning = verify_source_pinning(data)
        for block, url in pinning["non_authoritative"]:
            null_fields.append(
                f"_sources.{block} (non-authoritative source {url!r} — pin to a "
                "primary irs.gov/ftb.ca.gov/congress.gov URL)")
        unpinned_sources = pinning["unpinned"]
    except ValueError as e:
        null_fields.append(f"_sources ({e})")

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

    if unpinned_sources:
        # Advisory only — never flips FRESH (legacy caches predate _sources).
        recommendation += (
            f" NOTE: {len(unpinned_sources)} populated block(s) are UNPINNED "
            f"(no _sources URL): {', '.join(unpinned_sources)}. Record the "
            "authoritative source per block so the figures are traceable.")

    return {
        "status": status,
        "age_days": age_days,
        "null_fields": null_fields,
        "unpinned_sources": unpinned_sources,
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
