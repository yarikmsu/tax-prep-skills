#!/usr/bin/env python3
"""Tax law data fetcher — provides cached tax rates/brackets/thresholds.

IMPORTANT: This module provides structure and caching for tax rates that are
fetched at runtime from the web by the tax-* skills. The only
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

import copy
import json
import math
import re
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agents.lib.capabilities import (  # noqa: E402
    _NO_INCOME_TAX_STATES,
    _TN_HALL_REPEAL_YEAR,
)
from agents.lib.secure_fs import (  # noqa: E402
    atomic_write, first_non_finite, is_reparse_point)
from agents.lib.tax_computations import FORM_8938_THRESHOLDS  # noqa: E402
from agents.lib.tax_computations._shared import _STATE_NAME_TO_CODE  # noqa: E402
from agents.lib.tax_computations.state.ca import (  # noqa: E402
    _SDI_NO_CEILING_FROM,
    validate_ca_exemption_phaseout,
)
from agents.lib.tax_computations.state.nc import _nc_check_child_table  # noqa: E402
from agents.lib.tax_computations.state.ny import (  # noqa: E402
    _ny_check_recapture_tiers,
    _ny_check_schedule,
    _ny_table_bases_entries,
)
from agents.lib.secure_fs import redact  # noqa: E402
from agents.lib.workspace import (  # noqa: E402
    WorkspaceError,
    add_workspace_argument,
    hold_workspace,
    resolve_workspace,
)


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
    # is_reparse_point(), not is_symlink(): the latter is False for an NTFS
    # junction and converts an lstat failure into "not a link" on Python 3.14,
    # which would certify an uninspected path as safe and then read tax law
    # through it. This one fails closed on both counts.
    if is_reparse_point(path):
        raise ValueError(
            f"{redact(path)} is a symlink or junction — refusing to read for security. "
            "Remove it and re-run /tax-setup to re-fetch tax law data."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        # Corrupt cache — remove so caller re-fetches, but warn
        print(
            f"WARNING: Corrupt tax law cache at {redact(path)} ({type(e).__name__}). "
            "Removing so data can be re-fetched.",
            file=sys.stderr,
        )
        try:
            path.unlink(missing_ok=True)
        except OSError as unlink_err:
            print(
                f"WARNING: Could not remove corrupt cache {redact(path)}: {redact(unlink_err)}. "
                "Manual deletion may be required.",
                file=sys.stderr,
            )
        return None
    # `json.loads` admits the NaN/Infinity literals and reads `1e400` as inf.
    # A non-finite figure passes every `is None` guard downstream and defeats
    # every comparison (each NaN comparison is False), which is how a corrupt
    # cache prices a whole preferential stack at 0%. Refused at the ONE read
    # site every consumer shares, so no accessor has to re-check finiteness.
    bad = first_non_finite(data)
    if bad is not None:
        raise ValueError(
            f"{redact(path)} holds a non-finite number at {bad} — the cache is "
            "corrupt; re-run /tax-setup to re-fetch tax law data.")
    return data



def save_tax_law(year: int, data: dict, workspace: str = ".", *,
                 stamp_fetched: bool = True) -> str:
    """Save tax law data to cache file atomically. Returns path.

    Uses write-to-temp-then-rename to prevent corrupt partial writes.
    Makes a shallow copy of data before adding _meta, so the caller's
    original dict is not mutated. Nested dicts are shared, not deep-copied.

    `stamp_fetched=True` (the `create` path) dates the whole cache now.
    `set_figure` passes False and keeps the `_meta` it loaded: re-dating the
    cache-wide stamp on every per-block write is E7 of the 09-05 review —
    editing one figure made every other block look freshly retrieved. The
    per-block time lives in `_provenance`; `_meta.fetched` is the legacy
    fallback for a block with no entry there, so it has to stay what it was.
    """
    path = get_tax_law_path(year, workspace)

    # Copy to avoid mutating caller's dict
    to_save = dict(data)
    if stamp_fetched:
        to_save["_meta"] = {
            "tax_year": year,
            "fetched": datetime.now().isoformat(),
            "source": "IRS.gov / state tax authority / web search",
        }
    elif not isinstance(to_save.get("_meta"), dict):
        # A hand-written or legacy cache with no usable `_meta`. Its blocks
        # have no `_provenance` either, so a `fetched` minted here would date
        # every one of them today — E7 by the fallback door. Without the
        # stamp they stay undatable and the cache reads STALE, which is true.
        to_save["_meta"] = {"tax_year": year,
                            "source": "IRS.gov / state tax authority / web search"}

    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    # ONE writer for every runtime artifact (V10): temp file beside the target,
    # created owner-only before the first byte, renamed on a clean exit and
    # removed on any failure. This module's own copy of that dance was one of
    # five; the cache is public figures rather than taxpayer data, but the
    # atomicity is the same problem and the mode costs nothing.
    with atomic_write(path) as handle:
        json.dump(to_save, handle, indent=2)

    return str(path)


def create_empty_tax_law(year: int) -> dict:
    """Create empty tax law structure for a given year.

    Skills populate this by fetching from IRS.gov over the web.
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
            # prior. Each tier pairs the threshold CMS prints with the per-beneficiary
            # ANNUAL surcharge (Part B + Part D) above the standard premium. A tier
            # applies ABOVE its threshold and the top tier AT it, so the threshold is
            # the printed figure, never the first dollar past it. Empty until /tax-setup
            # fetches; /tax-roth-convert degrades to "IRMAA not modeled" when tiers are
            # empty.
            # Each tier: {"magi_threshold": <printed $>, "annual_surcharge_per_person": <$>}.
            "irmaa": {
                "lookback_years": 2,
                "tiers": {"single": [], "mfj": []},
            },
            # Four status keys, not two (X1); the statute and the legacy-cache
            # behaviour are at the one read site, `projection.capital_gains_limits`.
            "capital_gains_rates": {
                "zero_pct_limit": {"single": None, "mfj": None, "mfs": None, "hoh": None},
                "fifteen_pct_limit": {"single": None, "mfj": None, "mfs": None, "hoh": None},
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
                # Indexes annually; every covered planning year requires a
                # fetched value, with no built-in annual default.
                "secure_2_0_high_earner_threshold": None,
            },
            "foreign_tax_credit_simplified": {
                "max_single": 300,
                "max_mfj": 600,
            },
            # §6038D reporting thresholds (Form 8938 instructions, "Reporting
            # thresholds"): US-resident MFJ is $100,000 / $150,000 — the seed
            # carried the foreign-resident SINGLE pair ($200,000 / $300,000)
            # under this key until the 2026-09-06 acceptance replay of the
            # freshness auditor found it, doubling the threshold and telling a
            # domestic couple with $120K abroad that no Form 8938 was due.
            "form_8938_thresholds": {k: dict(v) for k, v in FORM_8938_THRESHOLDS.items()},
            "fbar_threshold": 10000,
            "qbi_deduction_rate": 0.20,
            # Section 199A taxable-income threshold (indexed annually; keyed
            # single/mfj like capital_gains_rates — map MFJ/QSS→mfj, else→single;
            # MFS uses the single-side figure). Above it the Form 8995-A
            # W-2-wage/UBIA limits and SSTB phase-out apply, which the simplified
            # compute_qbi_deduction does NOT model — it flags `over_threshold`
            # so /tax-compute can warn instead of silently overstating.
            "qbi_threshold": {"single": None, "mfj": None},
            # §199A(b)(3) phase-in range (the band above qbi_threshold over which
            # the Form 8995-A W-2/UBIA limit phases in and an SSTB phases out).
            # OPTIONAL override — compute_form_8995a uses a year-aware statutory
            # default ($50k/$100k-MFJ ≤2025, $75k/$150k-MFJ TY2026+ per OBBBA
            # §70105), so this is null unless a fetched figure must correct it
            # (the amt.phaseout_rate precedent). Keyed non-MFJ ("other") / mfj.
            "qbi_phase_in_range": {"other": None, "mfj": None},
            # Federal SALT itemized-deduction cap for THIS cache's year, fetched
            # per-year because the OBBBA base ($40,000 / $20,000 MFS for
            # TY2025-2029, reverting to $10,000 / $5,000 in TY2030) steps up
            # each year by statute for 2026-2029. It is the amount BEFORE the
            # §164(b)(7)(B) MAGI phasedown, which tax_computations/federal/
            # salt.py applies per return. `ptet._salt_cap` falls back to the
            # statutory schedule when unset; both consumers refuse a mismatch
            # beyond whole-dollar rounding. `compute_schedule_a` takes it as
            # a required input and refuses None, so /tax-setup must fetch it.
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
            # Highest individual ORDINARY income tax rate by calendar year,
            # {year: rate}. Needed ONLY for the PFIC §1291 engine
            # (compute_pfic_1291): each excess-distribution amount allocated to a
            # PRIOR PFIC year is taxed at THAT year's highest ordinary rate
            # (§1291(c)(2)), not the taxpayer's bracket. Fetched on demand when a
            # PFIC taxable event exists (e.g. 37% for 2018-2025, 39.6% for
            # 2013-2017, 35% for 2003-2012 — verify + pin per year). Empty = the
            # DEFAULT (no PFIC); compute_pfic_1291 marks the return INCOMPLETE and
            # keeps the block when a needed year is absent, never guessing.
            "historical_top_rate": {},
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
            # hard-errors at use when cash gifts meet an unfetched cap, which
            # the itemize-or-standard choice does for an itemizer with gifts
            # too). amount = non-joint; joint_amount = MFJ.
            "charitable_non_itemizer": {"amount": None, "joint_amount": None},
            # §70105 minimum QBI deduction (OBBBA, TY2026+). OPT-IN: taxpayer-
            # favorable, so compute_qbi_deduction DEGRADES (no floor) when this
            # is unfetched rather than guessing an indexed figure.
            "qbi_minimum": {"income_threshold": None, "amount": None},
        },
        "state": {},  # Populated per-state by /tax-setup
        # PTET (pass-through entity tax) rate schedules, keyed by K-1 ENTITY
        # state (NOT the taxpayer's resident state — a TX resident with a CA
        # K-1 needs the CA schedule). Each entry: {"schedule": [[lower, rate,
        # base_tax], ...], "vintage": "<cite>"}. Empty until fetched — ptet.py
        # PREFERS a fetched schedule and falls back to its built-in statutory
        # schedule with a LOUD unfetched-schedule warning naming the vintage
        # (never a silent price); a corrupt block hard-errors. Fetched per
        # entity state by /tax-setup when the taxpayer has K-1 income; pinned
        # under _sources as "ptet.<STATE>". CA/NY/NJ modeled.
        "ptet": {},
        "exchange_rates": {},  # e.g., {"EUR": <annual_avg_rate>} — fetched from IRS.gov yearly average rates
        # H4 source pinning: block-path → authoritative URL the figure was
        # fetched from (e.g. "federal.filing_statuses": "https://www.irs.gov/...").
        # /tax-setup records the primary source per fetched block; check_freshness
        # reports a NON-AUTHORITATIVE recorded URL as INCOMPLETE and lists
        # populated-but-unpinned core blocks as an advisory.
        "_sources": {},
        # NOT seeded here: `_provenance`, the V4 per-block sibling of
        # `_sources` — {pin path: {"retrieved": <ISO datetime>, "applies_to":
        # <tax year, or the period the block covers>}}. A sibling rather than
        # a richer value inside `_sources`, because `_source_host_authoritative`
        # requires a string and an object there would mark every existing
        # cache INCOMPLETE. `set_figure` creates it on first write and
        # `check_freshness` dates each block from it, falling back to
        # `_meta.fetched` for a block with no entry. It stays out of this seed
        # because the freshness auditor's acceptance fixture is a digest of
        # this function's output (docs/ops/review-subagent-acceptance.md):
        # a seeded key would move that digest and demand the fresh-session
        # replay, for a block the auditor is not yet scored on.
    }


def create_retirement_conformity_template():
    """All 50 states plus DC; law deliberately unfetched, never inferred."""
    from agents.lib.capabilities import _USPS_STATES, _TERRITORIES
    return {code: dict(tax_year=None, employee_401k=None, traditional_ira=None, hsa=None)
            for code in sorted(_USPS_STATES - _TERRITORIES)}


def create_state_modifications_template() -> dict:
    """Optional reviewed rule list; materialized only by its own fetch."""
    return {"tax_year": None, "coverage_confirmed": None, "rules": []}


def create_state_local_template() -> dict:
    """Optional local jurisdictions; an empty fetched list declares none."""
    return {"tax_year": None, "coverage_confirmed": None, "jurisdictions": []}


def create_state_template(state: str, tax_year: "int | None" = None) -> dict:
    """Create empty state tax law structure. Filled from a web search.

    `tax_year` is OPTIONAL and fully backward compatible (defaults to None,
    the prior no-arg behavior/signature). It is consulted ONLY to resolve
    the year-gated TN case below — CA/NY/FL and the always-none states are
    unaffected by it.

    Round-3 finding D: a state on the closed type-none allowlist
    (capabilities._NO_INCOME_TAX_STATES) that isn't already pre-built above
    (CA/NY/FL) used to fall through to the generic type-less fallback at the
    bottom — nagging INCOMPLETE forever for `standard_deduction`/`brackets`
    fields the design never fetches for a no-tax state (the same problem the
    FL template above was built to avoid). AK/NV/SD/TX/WY get the minimal
    none-template unconditionally; TN is year-gated by
    capabilities._TN_HALL_REPEAL_YEAR (Hall tax repeal, effective TY2021) —
    without a resolvable `tax_year >= 2021` there's no way to confirm "none"
    is correct for THAT specific return, so a year-unknown or pre-2021 TN
    call falls through to the generic fallback rather than guessing.
    """
    state_templates = {
        "CA": {
            # 2-letter USPS code — load-bearing for /tax-project's CA 540-ES
            # block (projection.build_report gates on state.code == "CA").
            "code": "CA",
            "name": "California",
            "authority": "Franchise Tax Board (FTB)",
            # Regime discriminator: "graduated_income" (bracket income tax) |
            # "none" (no individual income tax — FL). Lets consumers
            # (loss_harvest banner, skills' step-10/generate/parse branches)
            # distinguish "no tax" from "not fetched". Generic fallback
            # carries NO type key — regimes are typed one modeled state at
            # a time (see docs/contributing/state-module-playbook.md).
            "type": "graduated_income",
            "conforms_to_federal": False,
            "standard_deduction": {"single": None, "mfj": None, "hoh": None},
            "brackets": [],  # [{"rate": 0.01, "min": 0, "max": 10412}, ...]
            "personal_exemption": {"amount": None},
            "dependent_exemption": {"amount": None},
            # Required Form 540 table inputs, fetched for the covered year.
            # Published rows avoid assuming that the tax table is a schedule
            # evaluated at an inferred midpoint. Each row: min/max, tax by
            # canonical lower-case status. Fetch every row the sweep touches.
            "tax_table": {"max_taxable_income": None, "rows": []},
            # Required for every CA return's line-32 credit limitation.
            "exemption_phaseout": {
                "threshold": {s: None for s in ("single", "mfj", "mfs", "hoh", "qss")},
                "band": {s: None for s in ("single", "mfj", "mfs", "hoh", "qss")},
                "reduction_per_step": None,
            },
            "amt_screening": {s: None for s in ("single", "mfj", "mfs", "hoh", "qss")},
            "underpayment_screen": {
                "minimum_balance": {s: None for s in ("single", "mfj", "mfs", "hoh", "qss")},
                "tax_fraction": None,
            },
            "schedule_ca": {"fund_asset_minimum": None},
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
            # CA Schedule P (540) AMT (N1). The 7% TMT rate and 25% exemption
            # phase-out are statutory (R&TC §17062) and live in
            # compute_ca_schedule_p; ONLY the indexed exemption amounts and
            # phase-out-start thresholds are fetched here (per filing status —
            # map MFS to its own key). None until fetched: compute_ca_schedule_p
            # hard-errors when a CA ISO exerciser needs it, so a guessed value
            # can't silently zero CA AMT. REQUIRED for every CA return,
            # including one without an ISO exercise.
            "amt": {
                "exemption": {"single": None, "mfj": None, "mfs": None, "hoh": None},
                "phaseout_start": {"single": None, "mfj": None, "mfs": None, "hoh": None},
            },
            "no_city_income_tax": True,
        },
        "NY": {
            # Maintained contract, sources and scope limits:
            # docs/contributing/state-module-ny.md
            "code": "NY",
            "name": "New York",
            "authority": "Department of Taxation and Finance",
            "type": "graduated_income",
            # NOT a federal piggyback, and this key read True until the module
            # existed. NY starts from federal AGI and then applies its own
            # IT-225 modifications, and IT-196 itemizing runs on federal rules
            # AS THEY STOOD BEFORE THE TCJA — IT-196's own line 44 adjustment
            # references the 2% miscellaneous floor. A True here is what makes
            # a bracket estimate look like a return.
            "conforms_to_federal": False,
            # SIX rows in New York's table. The trap is NOT the one it looks
            # like: `qss` is a separate ROW whose amount equals `mfj`'s today,
            # and `mfs` equals `single`'s — those are the pairings to get
            # right, and MFS is the one an agent is most likely to guess
            # belongs with MFJ. What has no analogue anywhere else in this
            # cache is `single_dependent`: a single filer who CAN be claimed
            # as a dependent on another federal return gets a different,
            # much smaller amount. Nothing in the intake asks that yet — IT-201
            # item C — so the key is ahead of its question on purpose.
            "standard_deduction": {
                "single": None,
                "single_dependent": None,
                "mfj": None,
                "mfs": None,
                "hoh": None,
                "qss": None,
            },
            # Keyed by SCHEDULE, not by status, because §601 has three and the
            # status pairings are STATUTORY IDENTITIES rather than
            # coincidences: §601(a) covers married-filing-jointly AND the
            # surviving spouse, §601(b) the head of household, §601(c) the
            # single filer AND married-filing-separately (and estates and
            # trusts, which an IT-201 never is). Five per-status lists would
            # let a fetch populate `mfj` and leave `qss` empty — and an empty
            # schedule is a silent ZERO tax, not an error. One list per
            # schedule cannot be half-fetched that way.
            "bracket_schedule_by_status": {
                "single": "c", "mfs": "c", "mfj": "a", "qss": "a", "hoh": "b",
            },
            # Each band carries the PUBLISHED CUMULATIVE BASE alongside its
            # rate and floor:
            #   [{"rate": 0.055, "min": 27900, "max": 161550, "base": 1202}, …]
            # New York publishes its schedule as "base plus rate on the excess"
            # and ROUNDS each base to the nearest dollar, so summing the bands
            # disagrees with the form — on the TY2025 married-joint schedule the
            # $323,200 base sums to 18,251.75 against a published 18,252.
            # `/tax-verify` ties line by line against software that uses the
            # published schedule, and a systematic sub-dollar drift at every
            # band reads as a defect in whichever side is checked second.
            # `base` is REQUIRED, not optional: `compute_ny_tax` refuses
            # without it rather than falling back to summation, because a
            # fallback is a second answer that differs quietly.
            "brackets": {
                "a": [],
                "b": [],
                "c": [],
            },
            # THE TRAP, and the shape is where the first draft of this block
            # got it wrong. A supplemental tax recaptures the benefit of the
            # graduated schedules above a NYAGI threshold; plain bracket math
            # UNDER-states tax there, and under-stating what a taxpayer owes is
            # the direction the bias rule calls a hard error.
            #
            # The citation is a RANGE, not one subsection. §601 carries (d)
            # through (d-7) and each later one opens "Notwithstanding the
            # provisions of subsection (d), (d-1) …" with its own taxable-year
            # window — (d-5) governs 2026, (d-6) governs 2027 through 2032.
            # Naming a single subsection in a year-parameterised toolkit dates
            # the comment the moment the window rolls.
            #
            # And it is MULTI-TIER. Above the entry threshold the taxpayer's
            # NY TAXABLE INCOME selects a tier, each with its own recapture
            # base and incremental benefit, and both of those figures move year
            # to year — they are FETCHED, not structural, or the compute
            # function would be hardcoding tax law. A shape holding only the
            # entry threshold can reproduce the first worksheet and nothing
            # else, which under-states every return above the first tier.
            "tax_table_benefit_recapture": {
                # One figure for every status, unmoved since the regime began.
                "entry_threshold": None,
                "phase_in_band": None,
                # The FIRST tier does not use a base and benefit at all: it
                # phases from the graduated result up to a FLAT RATE on the
                # whole taxable income, and that rate differs by schedule. A
                # shape that only had the base-and-benefit form could not
                # express it, which is how the first draft of this block ended
                # up unable to compute the most common affected return.
                "entry_tier": {
                    "a": {"flat_rate": None, "taxable_income_not_over": None},
                    "b": {"flat_rate": None, "taxable_income_not_over": None},
                    "c": {"flat_rate": None, "taxable_income_not_over": None},
                },
                # Per SCHEDULE, matching `brackets`. Each entry:
                # {"taxable_income_over", "taxable_income_not_over",
                #  "recapture_base", "incremental_benefit", "nyagi_excess_over"}
                "tiers": {"a": [], "b": [], "c": []},
                # The top regime is not a recapture at all: above a very high
                # NYAGI the whole taxable income is taxed at one rate with no
                # schedule. A third mechanism, and it needs its own key or the
                # compute silently falls back to the tiers.
                "full_recapture": {"nyagi_over": None, "rate": None},
            },
            # NY's tax TABLE and its rate SCHEDULE are different METHODS, and
            # the form selects between them on BOTH NYAGI and taxable income —
            # the second condition is not implied by the first, which is why
            # this cannot be folded into the recapture's threshold. The table
            # is the schedule evaluated at each row's MIDPOINT and rounded to
            # the dollar: the published TY2025 $50,000-$50,050 row reads
            # 2,587 / 2,419 / 2,503 against a schedule-at-midpoint of
            # 2,586.875 / 2,418.875 / 2,502.875, while the row FLOOR is a
            # dollar low in every case. Using the schedule where the form says
            # table is worth up to two dollars and runs BOTH ways — it
            # OVER-states for anyone in the upper half of their row, which is
            # half of all returns — against an acceptance bar of one dollar a
            # line.
            #
            # The row WIDTH is not constant: the bottom of the table uses
            # narrower rows than the $50 it settles into, so the widths are
            # fetched as segments rather than assumed. One assumed width would
            # be a guess at exactly the figure this block exists to pin.
            "tax_table": {
                "max_taxable_income": None,
                # [{"over", "not_over", "row_width"}], spanning 0 to the max.
                "row_segments": [],
                # The bands where the TABLE is built on the bands beneath
                # summed, unrounded, rather than on the schedule's printed
                # base: [{"schedule", "min"}], naming the band and never the
                # figure, which the engine sums. [] is a fetch that found the
                # table following its schedule throughout; None is NOT
                # FETCHED, which `compute_ny_tax` notes.
                "table_bases": None,
            },
            # §615(f) reduces the NY itemized deduction TWICE, §615(g) replaces
            # both with a CHARITABLE-only limit at very high NYAGI, and between
            # them sits a flat plateau. FOUR mechanisms, not two, and they are
            # mutually exclusive by band — a compute that applies the reduction
            # and then the charitable limit double-limits, which over-states
            # the deduction's loss and over-states tax.
            "itemized_reduction": {
                "first_threshold": {"single": None, "mfj": None, "mfs": None,
                                    "hoh": None, "qss": None},
                "first_band": None,
                "first_rate": None,
                # Its own band: the second worksheet divides by its own figure,
                # independently of the first. Equal today, and an undeclared
                # coupling if either moves.
                "second_threshold": None,
                "second_band": None,
                "second_rate": None,
                # Above this, a flat share of the deduction with no phase-in.
                "flat_threshold": None,
                "flat_rate": None,
                # Where §615(f) STOPS and §615(g) takes over entirely.
                "charitable_only_threshold": None,
            },
            # §615(g). Each tier carries its own YEAR WINDOW: a tier that has
            # lapsed must be expressible as lapsed, because omitting it reads
            # as "not fetched" — the same confusion `applies: None` exists to
            # prevent for the city layer.
            # [{"nyagi_over", "share", "first_year", "last_year"}]
            "charitable_limit": {"tiers": []},
            # NY has NO exemption for the taxpayer or spouse. §616 grants one
            # for each exemption allowed under IRC §151(c) — the DEPENDENT
            # subsection; §151(b), the taxpayer and spouse, is not referenced.
            # A POSITIVE declaration rather than an absent key, on the
            # `type: "none"` precedent: absence reads as "not researched".
            "personal_exemption": {"amount": 0},
            # §616 states the per-dependent amount and does not index it, but
            # it is FETCHED anyway — a statutory figure may still be amended,
            # and only AGENTS.md §2's enumerated CA exception may sit inline.
            # Watch the fetch: post-TCJA IRC §151(d)(5) zeroes the FEDERAL
            # exemption amount, while §616 keys off the §151(c) ENTITLEMENT,
            # which is why New York still allows one. A source that conflates
            # the two returns 0, and nothing else here would catch it.
            "dependent_exemption": {"amount": None},
            # Every NY credit needs somewhere to land or it lands nowhere.
            # `personal_exemption: 0` exists so an absent key cannot read as
            # "not researched"; the same argument applies to these.
            "credits": {
                # THREE tables, and the grouping is not the one the rate
                # schedules use: single alone, married-filing-separately
                # alone, and married-joint with head-of-household and the
                # surviving spouse together. MFS pairs with SINGLE for the
                # §601 schedule and gets roughly half the credit here, so a
                # shared key would halve or double it.
                #
                # Keyed on FEDERAL adjusted gross income, unlike every other
                # threshold in this block. Each band is
                # {"agi_over", "agi_not_over", …} where a null `agi_over` is
                # the lowest band — the instructions note the figure there
                # "could be 0 or a negative amount". Single's bands carry a
                # flat `amount`; the other two carry `by_exemptions`, one
                # figure per column, plus `each_additional` for a household
                # larger than the last column, which is ADDED to that column
                # rather than replacing it.
                "household": {"table": {"single": [], "mfj_hoh_qss": [],
                                        "mfs": []}},
                "empire_state_child": None,
                "child_and_dependent_care": {"table": []},
                "earned_income_share": None,
                "real_property_tax": None,
                "college_tuition": {"expense_cap": None, "max_credit": None},
            },
            # Form IT-225 carries every modification that has no line of its
            # own, under a CODE whose prefix is its schedule: A-/EA- for
            # additions, S-/ES- for subtractions, where the E forms come
            # through a partnership, S corporation or estate. New York
            # DISALLOWS several of them on a resident return rather than
            # adjusting them, and the set changes as codes are retired and
            # added — so it is fetched, not written here. Unfetched, the totals
            # are still computed and a loud note says the eligibility was not
            # checked: an absent cross-check may not become a silent pass, but
            # neither may it block a correct return.
            "it225": {"codes_not_valid_on_it201": []},
            # Subtractions that sit on the IT-201 FACE rather than on IT-225,
            # and two of them carry figures. The pension-and-annuity exclusion
            # is PER SPOUSE with an age gate and no transfer of an unused part
            # between spouses, which a single household-level figure cannot
            # express.
            "subtractions": {
                "pension_annuity_exclusion": {"amount": None,
                                              "minimum_age": None},
                # Keyed married-joint against EVERYBODY ELSE, which is New
                # York's own grouping and the same two-key shape the federal
                # child-credit phase-out uses. `single` therefore covers
                # single, head-of-household, married-filing-separately and the
                # surviving spouse — reading this with four status keys finds
                # three absent and claims an uncapped deduction.
                "college_savings_deduction": {"single": None, "mfj": None},
            },
            # The city layer — Z2. `applies` stays None until fetched so that
            # "no city tax" and "not fetched" cannot be confused, which is the
            # same distinction `type` draws for the state.
            "nyc_tax": {
                "applies": None,
                "brackets": {"a": [], "b": [], "c": []},
                # The City's own TAX TABLE, used while line 47 is under
                # `max_taxable_income`: the State's midpoint method on the
                # City's own rows, whose narrow bottom rows are not the
                # State's. Same shape as `state.tax_table`, and not shared
                # with it, or a small City return lands in the wrong row.
                "tax_table": {"max_taxable_income": None, "row_segments": [],
                              "table_bases": None},
                # TWO school tax credits, not one: a fixed amount by filing
                # status under an income ceiling, and a RATE REDUCTION that is
                # its own per-schedule bracket table. A scalar holds neither.
                "school_tax_credit": {
                    "fixed_amount": {"single": None, "mfj": None, "mfs": None,
                                     "hoh": None, "qss": None},
                    "income_ceiling": None,
                    "rate_reduction": {"a": [], "b": [], "c": []},
                },
                # The SAME three-way grouping and the SAME federal-AGI keying
                # as the state household credit above — tables 4, 5 and 6 to
                # its 1, 2 and 3 — so `compute_ny_household_credit` serves
                # both. Verified against the instructions rather than assumed
                # from the shape: same status groups, same item-C bar, same
                # "for each individual over 7" increment.
                "household_credit": {"table": {"single": [],
                                               "mfj_hoh_qss": [], "mfs": []}},
                "earned_income_share": None,
            },
            "yonkers_tax": {
                # THREE regimes, not one: a resident surcharge, a nonresident
                # earnings tax, and a part-year case with its own IT-201 line.
                # A joint return can even have one resident spouse and one
                # nonresident spouse.
                "applies": None,
                # A rate on the NY STATE TAX, not on income: line 46 less the
                # refundable credits the IT-201-I worksheet lists, with the
                # STAR reconciliation amount taken back OUT of those credits,
                # so it does not reduce the base.
                "resident_surcharge_rate": None,
                # A rate on WAGES and self-employment earnings allocated to
                # Yonkers, against which an EXCLUSION is allowed first,
                # prorated by month for a part-year filer. Omitting the
                # exclusion OVER-states the tax and bills a filer below the
                # no-file floor for a tax they do not owe. Structural and not
                # fetched: the nonresident tax is also CAPPED at what the
                # resident surcharge would have been.
                "nonresident_earnings_rate": None,
                # Form Y-203's exclusion table, the TWELVE-month row: rows of
                # {"over", "not_over", "exclusion"} on Y-203 line 3b, the top
                # row's not_over null. The engine models a whole-year
                # nonresident only, so the prorated rows are not fetched.
                "nonresident_exclusion": {"table": []},
            },
            # Metropolitan commuter transportation mobility tax, for a
            # self-employed filer. Split by GEOGRAPHY, not by income tier —
            # two zones with different rates — so an unlabelled rate list read
            # positionally mis-assigns them, which nearly doubles or halves the
            # tax. The counties are structural; the rates and the threshold are
            # not.
            #
            # The threshold is PER ZONE, computed individually even on a joint
            # return, and it is a CLIFF: once a zone's earnings exceed it the
            # WHOLE zone amount is taxed, not the excess. It also TRIPLES for
            # tax year 2026, so a fetch that lands on a pre-2026 page taxes a
            # filer who owes nothing.
            "mctmt": {
                "zone_1": {
                    "rate": None,
                    "counties": ["New York", "Bronx", "Kings", "Queens",
                                 "Richmond"],
                },
                "zone_2": {
                    "rate": None,
                    "counties": ["Rockland", "Nassau", "Suffolk", "Orange",
                                 "Putnam", "Dutchess", "Westchester"],
                },
                "net_earnings_threshold_per_zone": None,
            },
            # §685(c): 90% of the current year, or 100% of the prior year —
            # 110% above a NEW YORK AGI threshold, MFS at its own. Same SHAPE
            # as federal §6654 and deliberately NOT sharing its constants:
            # these are New York's, they are keyed to NYAGI rather than federal
            # AGI, and they may diverge. The de-minimis figure is NOT the
            # federal one, and without a key here a compute reaches for the
            # federal amount and wrongly clears a New York filer.
            "estimated_tax_safe_harbor": {
                "current_year_share": None,
                "prior_year_share": None,
                "prior_year_share_high_income": None,
                "high_income_nyagi_threshold": None,
                "high_income_nyagi_threshold_mfs": None,
                "farmer_fisherman_share": None,
                "de_minimis": None,
            },
            # EQUAL QUARTERS — April, June, September and the following
            # January — which is NOT California's 30/40/0/30, and one IT-2105
            # voucher covers NYS, NYC, Yonkers and MCTMT together. Structural,
            # so it is stated here rather than fetched. A farmer or fisherman
            # is the exception: ONE January installment at a lower share, which
            # is why that share has a key above.
            "estimated_payment_schedule": "equal_quarters",
            # Employee-side payroll items, both wage-capped and both indexed.
            # Year-aware from the first line, because the CA scar here is that
            # the SDI wage ceiling DISAPPEARED in TY2024 and a cap read as a
            # constant silently mis-computed every later return.
            "paid_family_leave": {"rate": None, "wage_cap": None},
            "disability_benefits": {"employee_rate": None, "weekly_cap": None},
        },
        "NC": {
            # Maintained fetched-law contract and TY2025 D-401 sources:
            # docs/contributing/state-module-nc.md
            # Every figure is None and FETCHED by /tax-setup step 4 — including
            # the ones the statute states and does not index (AGENTS.md §2).
            # Descriptive keys audited against check_freshness's _SKIP_KEYS:
            # name, authority and conforms_to_federal are already there.
            "code": "NC",
            "name": "North Carolina",
            "authority": "North Carolina Department of Revenue (NCDOR)",
            "type": "graduated_income",
            # NOT a piggyback: D-400 starts from federal AGI, but read under
            # the Code as of `irc_conformity_date`, with every difference
            # carried on Schedule S and an independent NC Schedule A.
            "conforms_to_federal": False,
            # ONE band — a flat rate is a one-band schedule, not a new `type`.
            # A LIST, so `state_brackets_for_status` reads it for every status;
            # the band carries all three of {rate, min, max} because the
            # bracket validator requires them, and the top band's null `max`
            # is the schedule's shape, which the freshness walker skips.
            # G.S. 105-153.7 is YEAR-GATED ("In 2025 4.25%", "After 2025
            # 3.99%") and (a1) can trigger further cuts from TY2027, so the
            # rate is fetched per year and `rate_tax_year` records the year the
            # rate page NAMES — a carried-over rate on a later return
            # over-states, and check_freshness flags a mismatch.
            "brackets": [{"rate": None, "min": 0, "max": None}],
            "rate_tax_year": None,
            # FIVE statuses (QSS equals MFJ here, and the test pins the key
            # rather than the coincidence), plus the statutory ZERO an MFS
            # filer gets when the spouse itemizes (D-401 p.14 chart).
            "standard_deduction": {
                "single": None,
                "mfj": None,
                "mfs": None,
                "hoh": None,
                "qss": None,
                "mfs_spouse_itemizes": None,
            },
            # THE KEYING TRAP: G.S. 105-153.5(a1) has THREE tables, not four
            # or five — married-joint with the surviving spouse, head of
            # household, and single with married-separate. An accessor reading
            # `qss` or `mfs` finds nothing. Keyed on FEDERAL AGI (D-400 line 6).
            # Each table: [{"agi_up_to", "per_child"}, ...] ascending, the
            # upper bound INCLUSIVE ("Up to X" / "Over X - Up to Y"), the top
            # band's `agi_up_to` null (open), per_child never rising.
            "child_deduction": {"mfj_qss": [], "hoh": [], "single_mfs": []},
            # NC Schedule A, independent of the federal election.
            "itemized": {
                # Sch A line 2; `default` covers every status but MFS.
                "real_estate_tax_cap": {"default": None, "mfs": None},
                # Sch A line 4 — household-wide, even MFS (G.S. 105-153.5(a2)).
                "mortgage_plus_property_tax_cap": None,
                "medical_agi_floor_rate": None,
                # Sch A line 8. Not named `threshold`: that key name is
                # per-filing-status everywhere else and a scalar there reads
                # as corruption (_EXPECT_STRUCTURED).
                "claim_of_right": {"repayment_threshold": None, "agi_rate": None},
            },
            # ISO date (YYYY-MM-DD) of the Code NC's Schedules S and A read —
            # G.S. 105-228.90(b)(7). Unfetched → compute refuses the year.
            "irc_conformity_date": None,
            # Form NC-40, G.S. 105-163.15. Shares are `_rate`-suffixed so the
            # ingest refuses a percentage written as 90 rather than 0.90.
            "estimated_tax": {
                "required_threshold": None,
                "current_year_rate": None,
                "prior_year_rate": None,
                "farmer_rate": None,
            },
        },
        "FL": {
            "code": "FL",
            "name": "Florida",
            "authority": "Florida Department of Revenue",
            "type": "none",
            "no_income_tax": True,
            "note": (
                "Florida imposes no personal income tax (Fla. Const. Art. "
                "VII, §5) — no state individual income tax return exists. "
                "Deliberately NO brackets/standard_deduction keys: the "
                "freshness walker flags every empty list, and a field the "
                "design never fetches would nag INCOMPLETE forever. The only "
                "fetched fields are the current general state sales-tax rate "
                "and the taxpayer's county discretionary surtax rate, feeding "
                "the federal Schedule A line 5a "
                "sales-tax election (compute_sales_tax_deduction → "
                "compute_schedule_a state_local_sales_taxes)."
            ),
            "sales_tax": {
                # Fetched at setup from current Florida DOR sales/use-tax
                # guidance. A statutory rate is still a tax figure and may
                # change by amendment; only AGENTS.md's enumerated CA surcharge
                # exception may remain inline.
                "state_rate": None,
                "county_surtax_rate": None,  # taxpayer's county rate — /tax-setup fetches
            },
        },
    }
    state_templates["WA"] = {
        "code": "WA", "name": "Washington", "authority": "Department of Revenue",
        "type": "capital_gains_excise", "brackets": [],
        "wa_capital_gains": {"tax_year": None, "standard_deduction": None,
            "charitable_minimum": None, "charitable_cap": None, "rates": []},
    }
    if state in state_templates:
        return state_templates[state]

    is_tn_repealed = (
        state == "TN" and isinstance(tax_year, int)
        and not isinstance(tax_year, bool) and tax_year >= _TN_HALL_REPEAL_YEAR
    )
    if state in _TYPE_NONE_TEMPLATE_ALWAYS or is_tn_repealed:
        name = _STATE_CODE_TO_NAME.get(state, state)
        return {
            "code": state,
            "name": name,
            "type": "none",
            "no_income_tax": True,
            "note": (
                f"{name} imposes no individual income tax "
                "(capabilities._NO_INCOME_TAX_STATES) — no state individual "
                "income tax return exists. Deliberately NO brackets/"
                "standard_deduction keys, mirroring the FL template above: "
                "the freshness walker flags every empty list, and a field "
                "the design never fetches would nag INCOMPLETE forever. The "
                "Schedule A optional-sales-tax-table election (if "
                "itemizing) uses this state's own IRS table amount, looked "
                "up manually in v1 — compute_sales_tax_deduction's county-"
                "surtax modeling (state_rate/county_surtax_rate) is FL-"
                "specific and is not extended to other no-tax states here."
            ),
        }

    return {
        "code": state,
        "name": state,
        "note": "State template not pre-built. Fetch rates from the web.",
        "standard_deduction": {"single": None, "mfj": None},
        "brackets": [],
    }


_FRESHNESS_DAYS = 14  # Data older than this is considered STALE

# H4 source pinning: authoritative sources a fetched rate/bracket may be pinned
# to. A web search can surface a blog or a stale aggregator; recording a URL
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
    # Florida DOR does not use a .gov domain — floridarevenue.com IS the
    # primary state tax authority (verified via myflorida.com directory).
    "floridarevenue.com",
})

# The rule's two tables, read by `_source_host_authoritative` and published by
# `source_host_policy`. `gov` sits in the domain list as one more entry because
# the rule treats it as one: `host == "gov" or host.endswith(".gov")` is the
# same test every listed domain gets. One table, so the copy the auditor reads
# cannot lose a clause the rule has — the first one dropped the subdomain match
# and would have rejected the `www.law.cornell.edu` pins real caches carry.
_SOURCE_SCHEMES = ("https", "http")
_AUTHORITATIVE_DOMAINS = ("gov", *sorted(_AUTHORITATIVE_NONGOV_DOMAINS))

# Inverse of the shared US state/DC map (code → display name). A legacy cache
# with only `name: "California"` normalizes to the documented `state.CA` pin
# rather than a bogus `state.California` advisory. Also used for
# create_state_template's minimal none-template (round-3 finding D). Every code this module ever
# assigns "name" to via that inverse is a single-word or two-word state name,
# so a plain .title() is exact (no "district of columbia"-style multi-word
# capitalization edge case in play — DC is never on the type-none allowlist).
_STATE_CODE_TO_NAME = {code: name.title() for name, code in _STATE_NAME_TO_CODE.items()}

# States on the closed type-none allowlist (capabilities._NO_INCOME_TAX_STATES)
# that get create_state_template's minimal none-template UNCONDITIONALLY (no
# tax_year needed) — TN is the one exception, year-gated separately in
# create_state_template via capabilities._TN_HALL_REPEAL_YEAR.
_TYPE_NONE_TEMPLATE_ALWAYS = _NO_INCOME_TAX_STATES - {"FL", "TN"}

# States with a pre-built `graduated_income` template, whose regime is
# therefore KNOWN: a cache for one of them must carry `type`, and must carry
# this one. Read by `_expected_state_regime` and the absent-type guard in
# check_freshness, so the two cannot list different states.
_GRADUATED_MODELED_STATES = ("CA", "NY", "NC")

# North Carolina's child-deduction tables (G.S. 105-153.5(a1)): THREE keys,
# each a list of {agi_up_to, per_child} bands whose top `agi_up_to` is null.
_NC_CHILD_TABLE = re.compile(r"^state\.child_deduction\.(mfj_qss|hoh|single_mfs)$")


def _open_top_key(path: str) -> "str | None":
    """The key whose null on a table's LAST row is the table's open top, not an
    unfetched figure — `max` on a bracket schedule, `agi_up_to` on a North
    Carolina child-deduction table — or None for a list that has no such row."""
    if path.endswith("brackets") or path == "state.wa_capital_gains.rates":
        return "max"
    if _NC_CHILD_TABLE.match(path):
        return "agi_up_to"
    return None


def _absent_keys(ref, node, prefix: str) -> "list[str]":
    """Dotted paths of every key the seed `ref` carries that `node` lacks,
    recursing through dicts present in both. Lists are leaves: their rows are
    data, not schema."""
    if not isinstance(ref, dict) or not isinstance(node, dict):
        return []
    found = []
    for key, sub in ref.items():
        path = f"{prefix}.{key}"
        if key not in node:
            found.append(path)
        else:
            found.extend(_absent_keys(sub, node[key], path))
    return found


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


def _expected_state_regime(code: str, tax_year) -> "str | None":
    """The statutorily-settled `state.type` regime for `code`, or None when
    `code` isn't in this closed expected-regime map — everything outside the
    map keeps the vocabulary-only validation in check_freshness (regimes are
    typed one modeled state at a time per the state-module playbook, so an
    unmodeled code like CO is unconstrained here).

    Round-3 finding C: vocabulary-only checking (is "type" a recognized WORD)
    lets a CA cache carry type "none" and pass, because "none" is itself a
    valid word — just wrong for CA. This resolves the regime a real state
    code is EXPECTED to carry so the two can be cross-checked.

    CA/NY are graduated_income. FL and the rest of
    capabilities._NO_INCOME_TAX_STATES (AK/NV/SD/TX/WY, plus TN) are "none"
    — EXCEPT TN is year-gated the same way capabilities._tn_hall_repealed
    gates the taxpayer-facing note: TN's Hall tax on interest/dividends was
    still live through TY2020 and was only fully repealed effective TY2021,
    so a pre-2021 (or year-unknown) TN cache is expected graduated_income,
    matching the conservative "not yet repealed" default `_tn_hall_repealed`
    uses when the year can't be resolved.
    """
    if code == "WA":
        return "capital_gains_excise"
    if code in _GRADUATED_MODELED_STATES:
        return "graduated_income"
    if code == "TN":
        year = tax_year
        if isinstance(year, bool) or not isinstance(year, int):
            return "graduated_income"
        return "none" if year >= _TN_HALL_REPEAL_YEAR else "graduated_income"
    if code in _NO_INCOME_TAX_STATES:  # AK, FL, NV, SD, TX, WY (TN handled above)
        return "none"
    return None


def _none_type_allowed(code: str, tax_year) -> bool:
    """Round-4 finding A: `state.type == "none"` is an AFFIRMATIVE claim
    that `code` has NO individual income tax — unlike `graduated_income`
    (the safe default for an unmodeled/unknown code), asserting "none" for
    a code that ISN'T a real no-tax state (CO, WA, a typo) is itself an
    over-statement bug the round-3 fix missed: that fix only compared
    "none" against a KNOWN different expected regime, so an unmodeled code
    sailed through because `_expected_state_regime` returns None for it and
    the old comparison skipped whenever the expectation was None.

    This is a strict allowlist check, not a "matches expected" check. The
    single source of truth is `_expected_state_regime` above, which itself
    reads capabilities._NO_INCOME_TAX_STATES (AK/FL/NV/SD/TX/WY, plus TN)
    and gates TN on capabilities._TN_HALL_REPEAL_YEAR (2021) — those two
    constants move together; do not duplicate the allowlist here.
    """
    return _expected_state_regime(code, tax_year) == "none"


def state_regimes(tax_year: int) -> dict:
    """Every code `_expected_state_regime` knows for `tax_year`, with its regime.

    The freshness auditor's item 8 needs this table and may not read this file,
    so `tax_law_export` ships it beside the copy. Asked of the function rather
    than rebuilt from the sets it reads, so a regime added there arrives here
    unchanged, TN's repeal year included. A code absent from it is a generic
    bracket-estimate state: `type` may be absent, and `none` is never allowed.
    """
    regimes = {code: _expected_state_regime(code, tax_year)
               for code in sorted(set(_STATE_NAME_TO_CODE.values()))}
    return {code: regime for code, regime in regimes.items() if regime is not None}


def state_regime_code(data: dict) -> "str | None":
    """The code `check_freshness` looks the state block's regime up by, or None.

    The auditor's item 8 reads it against `state_regimes`. Normalised as the
    pin is — a code upper-cased, a display name mapped — because a block coded
    `fl` declares Florida's regime however `set` spelled it. `_state_pin_code`
    falls back to the bare pin suffix 'state', which names no state; that is
    None here, so "no code" has one spelling.
    """
    state = data.get("state")
    if not isinstance(state, dict):
        return None
    code = _state_pin_code(state)
    return None if code == "state" else code

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
    if parsed.scheme not in _SOURCE_SCHEMES:
        return False
    host = parsed.hostname
    if not host:
        return False
    host = host.lower()
    return any(host == d or host.endswith("." + d) for d in _AUTHORITATIVE_DOMAINS)


def source_host_policy() -> dict:
    """`_source_host_authoritative` as data, for a reader that cannot run it.

    The freshness auditor applies this rule to every pin and may not read this
    file, so `tax_law_export` ships it beside the copy. Built from the two
    tables the rule itself reads, and `match` states the one comparison it
    makes over them.
    """
    return {
        "rule": "_source_host_authoritative",
        "schemes": list(_SOURCE_SCHEMES),
        "domains": list(_AUTHORITATIVE_DOMAINS),
        "match": ("a URL passes when its scheme is one of `schemes` and its "
                  "hostname, lower-cased, equals one of `domains` or ends with "
                  "'.' followed by one: www.law.cornell.edu passes on "
                  "law.cornell.edu, any .gov host on gov, and irs.gov.evil.com "
                  "on nothing"),
    }


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
    """Whether the block at `dotted` holds real data (see `_has_real_data`)."""
    return _has_real_data(_get_path(data, dotted))


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
    expected = _expected_pins(data)
    unpinned = [src for src, path in expected
                if _block_populated(data, path) and src not in sources]
    return {"non_authoritative": non_authoritative, "unpinned": unpinned}


def _expected_pins(data: dict) -> "list[tuple[str, str]]":
    """`(pin key, data path)` for every populated block that needs provenance.

    The one roster `verify_source_pinning` audits `_sources` against and
    `check_freshness` dates block by block from `_provenance`; deriving it in
    two places is how a block could be pinned but never dated, or the
    reverse.
    """
    # (source_key, data_path): every populated structured federal block pins by
    # its own path (derived from the cache, so child_tax_credit / niit / amt /
    # etc. are all audited, not just filing_statuses + capital_gains_rates). The
    # cache holds ONE state as a flat block under `state` (state.code,
    # state.brackets, …), NOT keyed by code — so its primary expected pin is
    # `state.<code>` (what /tax-setup records) whose DATA lives at `state`.
    # Iterating arbitrary state field keys would emit bogus state.code/
    # state.name/state.brackets advisories and never credit the real state.CA
    # pin. Explicit independently-fetched sub-blocks are added below.
    expected = [(b, b) for b in _fetched_federal_blocks(data)]
    state = data.get("state")
    if isinstance(state, dict) and _block_populated(data, "state"):
        code = _state_pin_code(state)
        src_key = f"state.{code}" if code != "state" else "state"
        expected.append((src_key, "state"))
        for kind in ("modifications", "local"):
            if kind in state:
                expected.append((f"{src_key}.{kind}", f"state.{kind}"))
        # FL fetches two independent figures from two DOR publications. The
        # general rate retains the primary `state.FL` pin; the taxpayer's
        # county rate requires its own provenance. Besides making the audit
        # complete, this extra required key is the schema-migration signal for
        # legacy FL caches whose sole `state.FL` pin pointed at the county table
        # while state_rate remained the old hardcoded seed.
        if code == "FL" and isinstance(state.get("sales_tax"), dict):
            expected.append((
                "state.FL.county_surtax_rate",
                "state.sales_tax.county_surtax_rate",
            ))
    # exchange_rates is fetched data (IRS yearly-average rates) that directly
    # scales reported USD foreign income / FBAR values — audit it too.
    if _block_populated(data, "exchange_rates"):
        expected.append(("exchange_rates", "exchange_rates"))
    conformity = data.get("retirement_conformity")
    if isinstance(conformity, dict):
        for code, row in conformity.items():
            if isinstance(row, dict) and any(type(row.get(k)) is bool
                    for k in ("employee_401k", "traditional_ira", "hsa")):
                expected.append((f"retirement_conformity.{code}", f"retirement_conformity.{code}"))
    ptet = data.get("ptet")
    if isinstance(ptet, dict):
        for code, block in ptet.items():
            if isinstance(code, str) and not code.startswith("_") \
                    and _has_real_data(block):
                expected.append((f"ptet.{code}", f"ptet.{code}"))
    return expected


def source_pins(data: dict) -> dict:
    """`_expected_pins` as a table, each pin to the data path it covers.

    The freshness auditor needs the roster for its item 4, and for item 9 to
    read a `_provenance` entry `set` keyed by a pin: a state pin is not its
    path, so `state.FL.county_surtax_rate` dates a figure under `sales_tax`
    that no lookup by path ever reaches. It may not read this file, so
    `tax_law_export` ships the table beside the copy.
    """
    return dict(_expected_pins(data))


def multi_period_pins() -> list:
    """`_MULTI_PERIOD_PINS` as data, for the auditor's item 9 `applies_to` rule."""
    return sorted(_MULTI_PERIOD_PINS)


def _naive(when: datetime) -> datetime:
    """A stored timestamp as local wall clock, whether or not it carries an offset.

    `datetime.fromisoformat` returns an AWARE datetime for a stamp written
    `...+00:00` or with the `Z` a fetched page's own timestamp usually carries,
    and comparing one against the naive `datetime.now()` this module dates
    everything from raises TypeError — a crash where a freshness verdict
    belongs, reachable through the public writer by passing an aware `now` to
    `set_figure`. Converting to local time and dropping the offset puts both
    sides in one frame.
    """
    return when.astimezone().replace(tzinfo=None) if when.tzinfo else when


def _populated_leaves(node, prefix: str) -> list:
    """Every figure actually present under `prefix`, as dotted paths.

    A list is one figure, not a container — a bracket table is retrieved and
    dated whole. An empty list and a null are the unfetched state and are not
    figures, so a block holding only those has nothing for an entry to cover.
    """
    if isinstance(node, dict):
        found = []
        for key, value in node.items():
            found.extend(_populated_leaves(value, f"{prefix}.{key}" if prefix else key))
        return found
    return [] if node is None or node == [] else [prefix]


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
    try:
        data = load_tax_law(year, workspace)
    except ValueError as e:
        return {
            "status": "INCOMPLETE",
            "age_days": None,
            "null_fields": [str(e)],
            "recommendation": "The cache holds a non-finite number; re-run /tax-setup to re-fetch tax law data.",
        }
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
            fetched_dt = _naive(datetime.fromisoformat(fetched_str))
            age_days = (datetime.now() - fetched_dt).days
        except (ValueError, TypeError) as e:
            # Malformed timestamp (e.g., manual edit or corrupt write). Treat as
            # STALE so caller re-fetches, which will also fix the timestamp.
            print(
                f"WARNING: Could not parse fetch timestamp '{fetched_str}': {redact(e)}. "
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
    # (the ceiling existed and must be fetched). READ from the engine rather
    # than repeated here: this used to be a literal `2024` under a comment
    # saying "keep in sync with tax_computations._SDI_NO_CEILING_FROM", and
    # W2's state split made that pointer resolve to nothing while the literal
    # stayed — a quantity in two places with the pointer between them broken,
    # which is how it gets to be two different quantities.
    _sdi_null_is_valid = (isinstance(data.get("tax_year"), int)
                          and not isinstance(data.get("tax_year"), bool)
                          and data["tax_year"] >= _SDI_NO_CEILING_FROM)
    # MAINTENANCE: Two categories are skipped during None/empty scanning:
    #   1. Keys prefixed with "_" (metadata/comments) — auto-skipped
    #   2. Non-prefixed descriptive fields — add to _SKIP_KEYS below
    # Empty lists are always FLAGGED (not skipped) as they indicate missing data.
    # _SKIP_KEYS must stay in sync with descriptive fields in BOTH
    # create_empty_tax_law() AND create_state_template() (especially CA fields:
    # hsa_note, authority, conforms_to_federal, no_city_income_tax, hsa_conformity).
    # When adding a new state template, audit its descriptive keys and add them here.
    # Missing a descriptive field → false INCOMPLETE; adding a rate field → silent gap.
    # "type" is deliberately NOT in this set (Codex P2): it's the
    # graduated_income/none regime discriminator, not a purely descriptive
    # field — a deleted/null/misspelled value must be caught, not skipped.
    # See the explicit state.type validation below the walk.
    _SKIP_KEYS = {"note", "hsa_note", "name", "authority",
                  "conforms_to_federal", "no_city_income_tax", "twenty_pct_above",
                  "applies", "hsa_conformity", "no_income_tax", "retirement_conformity"}

    # Keys whose values must be dicts or lists, never scalars.
    # A scalar value here (int, float, str, bool) indicates either data corruption
    # (e.g., brackets: 0) or a type coercion failure (e.g., phase_out_start: "400000").
    _EXPECT_STRUCTURED = {"brackets", "threshold", "phaseout",
                          "phase_out_start", "roth_income_phaseout",
                          "traditional_deduction_phaseout", "filing_statuses",
                          "itemized_phaseout",
                          # FL-only (Codex round-17 P2): the FL-specific
                          # absent-key guard below (`"sales_tax" not in
                          # state_block`) only fires when the key is
                          # entirely ABSENT, and its `isinstance(..., dict)`
                          # elif only fires when the value IS a dict — a
                          # PRESENT scalar (e.g. "sales_tax": "oops") slips
                          # through both, and the generic walker's `else:
                          # _walk(v, child_path)` branch does nothing for a
                          # bare non-dict/non-list scalar either. Listing it
                          # here makes the generic walker itself flag a
                          # non-dict/non-list sales_tax value.
                          "sales_tax"}

    # Exact PATHS where a key listed above is legitimately a bare scalar.
    # Keyed by full path, never by key name: the whole value of
    # _EXPECT_STRUCTURED is that a scalar `niit.threshold` is corruption, and a
    # key-level exemption would silence that everywhere at once.
    #
    # Add an entry only when the value is set in STATUTE and never indexed, so
    # there is no annual fetch for it to be stale against — which is exactly why
    # create_state_template hardcodes it rather than leaving it None.
    _SCALAR_BY_DESIGN = {
        # CA mental-health services surcharge, Prop 63 / R&TC §17043: a flat
        # $1,000,000 taxable-income threshold, unindexed since 2004.
        "state.mental_health_surcharge.threshold",
    }
    # Paths where `[]` is a FETCHED value and only None is the gap. New York's
    # table_bases: an empty list is a fetch that found the table following its
    # schedule in every band — the City's TY2025 table does. Keyed by PATH,
    # as above, so the same name elsewhere still flags.
    _EMPTY_LIST_BY_DESIGN = {
        "state.tax_table.table_bases",
        "state.nyc_tax.tax_table.table_bases",
    }

    if (isinstance(data.get("state"), dict) and data["state"].get("code") == "WA"
            and data.get("state", {}).get("type") == "capital_gains_excise"):
        _EMPTY_LIST_BY_DESIGN.add("state.brackets")

    def _walk(obj, path=""):
        # Optional CA planning figures are required by the state repricer,
        # not by every filed-return workflow. The cache validator still audits
        # populated values; missing ones yield a named planning limitation.
        if (isinstance(data.get("state"), dict) and data["state"].get("code") == "CA"
                and path == "state.amt_screening"):
            return
        # These optional tagged blocks have their own complete validators;
        # empty confirmed lists and inactive null parameters are valid data.
        if path in ("state.modifications", "state.local"):
            return
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
                # federal.qbi_phase_in_range is an OPTIONAL override — null is
                # the DEFAULT (compute_form_8995a uses the year-aware statutory
                # range), like amt.phaseout_rate. Skip when unfetched.
                if k == "qbi_phase_in_range" and isinstance(v, dict):
                    continue
                # federal.historical_top_rate is fetched ONLY when a PFIC §1291
                # taxable event exists — an empty dict is the DEFAULT for the vast
                # majority of returns, and compute_pfic_1291 degrades LOUDLY
                # (INCOMPLETE, keeps the block) at the point of use when a needed
                # prior-year rate is absent. Skip so it doesn't nag INCOMPLETE
                # forever (the underpayment_rates precedent).
                if k == "historical_top_rate" and isinstance(v, dict):
                    continue
                child_path = f"{path}.{k}" if path else k
                # _EXPECT_STRUCTURED matches on the KEY NAME, and `threshold` is
                # a dict keyed by filing status almost everywhere it appears
                # (additional_medicare_tax, niit, irmaa). CA's mental-health
                # services surcharge is the exception: create_state_template
                # hardcodes threshold=1000000 as a bare int, because Prop 63
                # (R&TC §17043) set it in statute and never indexed it. The
                # key-name rule therefore flagged the toolkit's OWN canonical
                # CA template, so every California return reported INCOMPLETE
                # at /tax-compute Step 0.5 forever — the sdi_wage_limit /
                # underpayment_rates / phaseout_rate failure a fourth time.
                #
                # This one is worse than nagging. Step 0.5 says INCOMPLETE plus
                # no live web means STOP, so a spurious INCOMPLETE trains the
                # agent to reason its way PAST the freshness gate — verified: an
                # agent hit this, correctly diagnosed the false positive, then
                # reclassified the cache FRESH and computed a full return on
                # placeholder brackets. A gate that cries wolf gets overridden.
                #
                # Scoped to the exact PATH, never the key: a scalar
                # `niit.threshold` is still real corruption and still flags.
                exempt = child_path in _SCALAR_BY_DESIGN
                if v is None:
                    # Still a gap, whatever its shape — but do not tell the
                    # reader to supply a dict for a field that must be a scalar.
                    if k in _EXPECT_STRUCTURED and not exempt:
                        null_fields.append(f"{child_path} (expected dict/list, got None)")
                    else:
                        null_fields.append(child_path)
                elif k in _EXPECT_STRUCTURED and not exempt and not isinstance(v, (dict, list)):
                    null_fields.append(f"{child_path} (expected dict/list, got {type(v).__name__}: {v!r})")
                else:
                    _walk(v, child_path)
        elif isinstance(obj, list):
            if len(obj) == 0:
                if path not in _EMPTY_LIST_BY_DESIGN:
                    null_fields.append(f"{path} (empty list)")
            else:
                open_key = _open_top_key(path)
                for i, item in enumerate(obj):
                    # A bracket table is open at the top: the LAST bracket's
                    # null `max` is the schedule's shape, not an unfetched
                    # figure (a closed top bracket is what `set` refuses).
                    # North Carolina's child-deduction tables are the same
                    # shape under another key (`agi_up_to`).
                    if (open_key and i == len(obj) - 1
                            and isinstance(item, dict) and item.get(open_key) is None):
                        item = {k: v for k, v in item.items() if k != open_key}
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

    # FL sales_tax block absent-key guard (Codex round-13 P2): a FL cache
    # with the WHOLE sales_tax block deleted contributes no state-specific
    # null_fields at all — the recursive walker only examines PRESENT keys,
    # and the type-absent-key guard below covers `type`, not this separate
    # required block. `/tax-compute` must read
    # `state.sales_tax.county_surtax_rate` to produce the mandatory
    # Schedule A line 5a candidate (`compute_sales_tax_deduction`), so a
    # partially migrated or hand-edited cache missing the block (or missing
    # just the sub-key) must be refetched rather than silently read as
    # fresh — which would otherwise let `/tax-compute` either hard-fail
    # with no earlier refetch prompt or, worse, silently omit the
    # deduction. Same absent-key precedent as sdi_wage_limit/
    # itemized_phaseout above. FL-specific: no other type-none state models
    # a sales_tax block in v1 (see tax-compute.md step 2 — the automated
    # computation is FL-only).
    if isinstance(state_block, dict) and _state_pin_code(state_block) == "FL":
        if "sales_tax" not in state_block:
            null_fields.append(
                "state.sales_tax (key absent — required for FL's Schedule A "
                "line 5a sales-tax election)")
        elif isinstance(state_block["sales_tax"], dict):
            if "state_rate" not in state_block["sales_tax"]:
                null_fields.append(
                    "state.sales_tax.state_rate (key absent — required for "
                    "FL's Schedule A line 5a sales-tax election)")
            if "county_surtax_rate" not in state_block["sales_tax"]:
                null_fields.append(
                    "state.sales_tax.county_surtax_rate (key absent — required "
                    "for FL's Schedule A line 5a sales-tax election)")

    # NC absent-key guard, the same class as the FL one above: the walker
    # visits only PRESENT keys, so a cache missing one of the THREE
    # child-deduction tables — or any other key the template seeds — reads
    # FRESH while the compute has nothing to read for that taxpayer. Every
    # key the template carries is required, the template being the schema.
    if isinstance(state_block, dict) and _state_pin_code(state_block) == "NC":
        null_fields.extend(
            f"{path} (key absent — the North Carolina template seeds it and "
            "/tax-setup step 4 fetches it)"
            for path in _absent_keys(create_state_template("NC"), state_block,
                                     "state"))

    if isinstance(state_block, dict) and _state_pin_code(state_block) == "WA":
        null_fields.extend(f"{p} (key absent — required for WA excise)"
                           for p in _absent_keys(create_state_template("WA"), state_block, "state"))
        if state_block.get("brackets") != []:
            null_fields.append("state.brackets (WA excise requires EMPTY ordinary brackets)")
        cfg = state_block.get("wa_capital_gains", {})
        if not isinstance(cfg, dict) or type(cfg.get("tax_year")) is not int or cfg.get("tax_year") != data.get("tax_year"):
            null_fields.append("state.wa_capital_gains.tax_year (fetch parameters for this year)")

    # state.type regime-discriminator guard (Codex P2): "type" was removed
    # from _SKIP_KEYS above so a PRESENT-but-null or misspelled value is no
    # longer skipped over silently — a deleted/corrupted discriminator must
    # not pass freshness. A present type is validated against the two
    # recognized regimes; a null value is already caught by the walker
    # itself (present key + None value -> "state.type" in null_fields), so
    # this only needs to catch a present NON-None value that isn't
    # recognized. A cache with NO "type" key at all is legacy/generic (the
    # key is typed one modeled state at a time per the playbook) and is
    # deliberately NOT flagged here.
    if (isinstance(state_block, dict) and "type" in state_block
            and state_block["type"] is not None
            and state_block["type"] not in ("graduated_income", "none", "capital_gains_excise")):
        null_fields.append(
            "state.type (unrecognized regime — expected "
            "graduated_income|capital_gains_excise|none)")

    # Inverse of the guard above (Codex round-2 P2): deleting the "type" key
    # ENTIRELY still passed the previous guard, because it only fires when
    # "type" IS present. That's fine for a generic/legacy state (regimes are
    # typed one modeled state at a time), but a MODELED typed state must
    # always carry the discriminator; a hand-edited or half-migrated cache
    # missing it would silently degrade to "generic, not flagged" for a
    # state /tax-compute/harvest/etc. actively branch on. Same absent-key
    # precedent as sdi_wage_limit / itemized_phaseout / federal.amt above.
    #
    # Round-5 finding B: this used to hardcode ("CA", "NY", "FL"), but
    # create_state_template now also generates typed "none" caches for
    # AK/NV/SD/TX/WY and (year-gated) TN — those were silently exempt from
    # the absent-key requirement even though they're just as modeled as FL.
    # Reuses `_none_type_allowed` (itself backed by the single
    # `_expected_state_regime` table, which already gates TN on
    # `_TN_HALL_REPEAL_YEAR`) instead of duplicating that allowlist here —
    # CA/NY are the only two states with a permanently-known regime that
    # ISN'T "none", so they're still named explicitly.
    if isinstance(state_block, dict) and "type" not in state_block:
        _absent_type_code = _state_pin_code(state_block)
        if (_absent_type_code == "WA" or _absent_type_code in _GRADUATED_MODELED_STATES
                or _none_type_allowed(_absent_type_code, data.get("tax_year"))):
            null_fields.append(
                "state.type (key absent — required for modeled states "
                f"{'/'.join(_GRADUATED_MODELED_STATES)} (graduated_income) and "
                "FL/AK/NV/SD/TX/WY/TN "
                "(none, once TN's year-aware regime resolves to none))")

    # state.type vs state.code cross-check (Codex round-3 P1 finding C,
    # sharpened by round-4 finding A): the two guards above only validate
    # VOCABULARY — "none" and "graduated_income" are both recognized words,
    # so a CA cache carrying type "none" (or an FL cache carrying
    # "graduated_income") passes them silently.
    #
    # "none" is checked as a strict ALLOWLIST membership (_none_type_allowed
    # — code in point-3 that gates TN on _TN_HALL_REPEAL_YEAR): round-3 only
    # flagged "none" when it conflicted with a KNOWN different expected
    # regime, so an unmodeled/unknown code (CO, WA, a typo) typed "none"
    # sailed through silently, because that check skipped whenever
    # _expected_state_regime returned None. "none" is an affirmative
    # no-tax claim, so EVERY code outside the closed allowlist is flagged
    # here — known-wrong (CA, pre-2021 TN) and unmodeled (CO, WA) alike.
    #
    # "graduated_income" keeps the ORIGINAL vocabulary-only-unless-known
    # behavior (safe direction — it's the conservative default for an
    # unmodeled code): only flagged when _expected_state_regime resolves to
    # a DIFFERENT known regime (e.g. FL typed graduated_income; FL's
    # expected regime is "none"). An unmodeled code (CO) typed
    # graduated_income stays unconstrained.
    if (isinstance(state_block, dict)
            and state_block.get("type") in ("graduated_income", "none", "capital_gains_excise")):
        _regime_code = _state_pin_code(state_block)
        _type = state_block["type"]
        if _type == "none":
            if not _none_type_allowed(_regime_code, data.get("tax_year")):
                null_fields.append(
                    f'state.type ("none" not allowed for state.code '
                    f'"{_regime_code}" — not a recognized no-income-tax '
                    f'state)')
            # Contradiction guard (Codex round-11 P1): an ALLOWED no-tax
            # code can still carry leftover income-tax computation fields
            # from a copied/partially-replaced graduated_income cache (e.g.
            # a CA cache hand-edited to code: "FL", type: "none" without
            # stripping CA's real brackets/standard_deduction). Every
            # type-none consumer (project_state_tax, loss_harvest's
            # _state_marginal_rate) only checks `if brackets:` before using
            # them — it has no reason to also distrust a "none" cache that
            # happens to carry brackets — so leftover REAL data here would
            # silently produce invented state tax/harvest savings for a
            # state that has none. A genuinely no-tax state's brackets/
            # standard_deduction are never fetched AT ALL (see
            # create_state_template's FL/AK/NV/SD/TX/WY/TN templates —
            # deliberately no such keys), so any REAL data (not just an
            # empty scaffold list/all-None dict — `_has_real_data`) here is
            # self-contradictory with the "none" declaration.
            if _has_real_data(state_block.get("brackets")):
                null_fields.append(
                    f'state.brackets (non-empty, but state.code '
                    f'"{_regime_code}" is type "none" — a no-income-tax '
                    f'state has no brackets; this is leftover/contradictory '
                    f'data, likely from a different state\'s cache)')
            if _has_real_data(state_block.get("standard_deduction")):
                null_fields.append(
                    f'state.standard_deduction (populated, but state.code '
                    f'"{_regime_code}" is type "none" — a no-income-tax '
                    f'state has no standard deduction; this is leftover/'
                    f'contradictory data, likely from a different state\'s '
                    f'cache)')
        else:
            _expected = _expected_state_regime(_regime_code, data.get("tax_year"))
            if (_expected is not None and _type != _expected) or (_type == "capital_gains_excise" and _regime_code != "WA"):
                null_fields.append(
                    f'state.type ("{_type}" conflicts with '
                    f'state.code "{_regime_code}" — expected {_expected})')

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

    # Cache-year mismatch guard (Codex round-8 finding C): every year-gated
    # check above (_none_type_allowed, the TN Hall-tax repeal boundary,
    # _sdi_null_is_valid) trusts data.get("tax_year") — none of them compare
    # it against the YEAR THE CACHE WAS LOADED FOR (the `year` argument). A
    # mis-saved/misnamed cache (e.g. `tax_law_2020.json` whose body still
    # declares `"tax_year": 2021` from a copy/paste) would silently defeat
    # that year gating: check_freshness would read the WRONG year's rules
    # (TN's Hall tax, the SDI ceiling, etc.) while reporting FRESH. Flag any
    # mismatch — and a missing key too, since the walk above treats a
    # missing tax_year as "no year info" and several of the year-gated
    # checks above degrade to a conservative default rather than erroring.
    _cache_tax_year = data.get("tax_year")
    if isinstance(_cache_tax_year, bool) or not isinstance(_cache_tax_year, int):
        null_fields.append(
            f"tax_year (missing or invalid — cache has no valid tax_year "
            f"but was loaded for {year} — refetch or fix the filename)")
    elif _cache_tax_year != year:
        null_fields.append(
            f"tax_year (cache declares {_cache_tax_year} but was loaded "
            f"for {year} — refetch or fix the filename)")

    # H4 source pinning: a recorded but NON-AUTHORITATIVE source is INCOMPLETE
    # (a non-primary URL affirmatively taints the figure); populated-but-
    # unpinned core blocks are normally a softer advisory that never flips
    # FRESH (legacy caches predate the field). FL's two sales-tax pins are the
    # exception: the separate county pin is also the migration discriminator
    # from the old schema, where state.FL pointed at the county table and the
    # general rate was a hardcoded seed. Missing either pin must force a live
    # refetch rather than grandfather that plausible stale value.
    unpinned_sources = []
    try:
        pinning = verify_source_pinning(data)
        for block, url in pinning["non_authoritative"]:
            null_fields.append(
                f"_sources.{block} (non-authoritative source {url!r} — pin to a "
                "primary irs.gov/ftb.ca.gov/congress.gov URL)")
        unpinned_sources = pinning["unpinned"]
        for block, figure in (
            ("state.FL", "Florida general sales-tax rate"),
            ("state.FL.county_surtax_rate", "Florida county surtax rate"),
        ):
            if block in unpinned_sources:
                null_fields.append(
                    f"_sources.{block} (key absent — {figure} requires a "
                    "separate authoritative source pin; refetch legacy FL "
                    "caches)")
    except ValueError as e:
        null_fields.append(f"_sources ({e})")

    # V4 per-block retrieval time. `_meta.fetched` stood in for every block's
    # retrieval time and `save_tax_law` rewrote it on every save (E7), so one
    # edited figure re-dated the whole cache. Each populated block is dated
    # from `_provenance` now, the cache-wide stamp serving only a block with
    # no entry (a legacy cache, or one hand-edited around this command), and
    # the OLDEST block decides — a cache is as fresh as its stalest figure.
    now = datetime.now()
    expected = _expected_pins(data)
    expected_pins = {pin for pin, _p in expected}
    provenance = data.get("_provenance")
    if provenance is not None and not isinstance(provenance, dict):
        null_fields.append(
            f"_provenance (must be an object keyed by figure or block, got "
            f"{type(provenance).__name__})")
        provenance = {}
    provenance = provenance or {}
    # Entries are keyed by the figure WRITTEN (a dotted path) or, for a
    # whole-block write, by the block's pin; every entry groups under the
    # pin `_pin_key` gives its path, and the OLDEST entry under a pin dates
    # the block — one re-fetched leaf never re-dates its siblings.
    entry_ages: dict = {}
    for key, entry in provenance.items():
        pin = key if key in expected_pins else _pin_key(key, data)
        try:
            if not isinstance(entry, dict):
                raise TypeError
            retrieved = _naive(datetime.fromisoformat(entry.get("retrieved")))
        except (TypeError, ValueError):
            null_fields.append(
                f"_provenance.{key} (each entry is an object with an ISO "
                "`retrieved` time and an `applies_to` period)")
            entry_ages.setdefault(pin, []).append(None)
            continue
        if retrieved > now:
            null_fields.append(
                f"_provenance.{key} (retrieved {entry.get('retrieved')} is in the "
                "future — a block cannot be younger than now)")
            entry_ages.setdefault(pin, []).append(None)
            continue
        entry_ages.setdefault(pin, []).append((now - retrieved).days)
        period = entry.get("applies_to")
        if pin not in _MULTI_PERIOD_PINS and period is not None and (
                isinstance(period, bool) or not isinstance(period, int)
                or period != year):
            null_fields.append(
                f"_provenance.{key} (applies_to {period!r} — this figure was "
                f"retrieved for another period than the {year} cache it sits in)")
    try:
        legacy_age = (now - _naive(datetime.fromisoformat(fetched_str))).days \
            if fetched_str else None
    except (ValueError, TypeError):
        legacy_age = None
    block_ages: dict = {}
    for pin, path in expected:
        ages = list(entry_ages.get(pin, ()))
        # An entry keyed by the PIN, or by any path above a figure, covers
        # everything under it — that is the whole-block write. A DOTTED entry
        # covers one figure, and the block's other figures are still as old as
        # `_meta.fetched` says they are: taking the leaf's age for the block
        # reported every untouched sibling as freshly retrieved, which is a
        # legacy cache updated one `set` at a time reading FRESH on figures
        # nobody has looked at. The legacy stamp drops out only once every
        # populated figure under the pin is covered, so re-fetching a block
        # figure by figure still clears it.
        # `ages` is non-empty exactly when some entry sits under this pin; a
        # block with an entry and nothing populated yet is covered by it, which
        # is what a whole-block write of a block that is legitimately empty
        # looks like.
        # An entry keyed by the PIN covers its block whatever the block's path
        # is — a state pin is `state.TX` over a block at `state`, so matching
        # entry keys against leaf paths alone would leave every state figure
        # uncovered and the block dated by a stamp its own entry supersedes.
        leaves = _populated_leaves(_get_path(data, path), path)
        covered = bool(ages) and (pin in provenance or all(
            any(leaf == key or leaf.startswith(f"{key}.") for key in provenance)
            for leaf in leaves))
        if not covered:
            ages.append(legacy_age)
        block_ages[pin] = (None if not ages or any(a is None for a in ages)
                           else max(ages))
    dated = {pin: age for pin, age in block_ages.items() if age is not None}
    oldest_block = max(dated, key=dated.get) if dated else None
    if block_ages:
        age_days = block_ages[oldest_block] if len(dated) == len(block_ages) else None
    # The write path's rules run over the whole cache here too — a cache
    # written before this command, or hand-edited around it, is otherwise the
    # one population none of them ever sees.
    null_fields.extend(validate_cache(data, year))

    # Determine status
    if null_fields:
        status = "INCOMPLETE"
        recommendation = (
            f"Tax law data has {len(null_fields)} unfilled field(s). "
            "Re-fetch missing values from the web."
        )
    elif age_days is None:
        status = "STALE"
        recommendation = (
            "Tax law data has no valid fetch timestamp. "
            "Re-fetch to establish freshness baseline."
        )
    elif age_days > _FRESHNESS_DAYS:
        status = "STALE"
        which = f" ({oldest_block} is the oldest block)" if oldest_block else ""
        recommendation = (
            f"Tax law data is {age_days} days old{which}. "
            "Search the web for new legislation since the fetch date, re-fetch if found."
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
        "block_ages": block_ages,
        "oldest_block": oldest_block,
        "recommendation": recommendation,
    }


# ---------------------------------------------------------------------------
# V4 — the ingest path: `set <dotted.path> <value> --source <url> --year <y>`
#
# The agent used to write this cache by hand, and every defect of a hand-written
# JSON file arrived in real returns: a figure under the wrong key (which reads
# as "unfetched" and falls to a default), a bracket list out of order, an MFJ
# figure below the single one, a blog URL pasted under `_sources`, and — E7 of
# the 2026-09-05 review — `save_tax_law` re-dating the WHOLE cache on every save,
# so a one-figure edit made every stale block look freshly retrieved. `set_figure`
# is the one write dotted: the path must exist in the schema seed, the value must
# have the seed's shape and pass the range checks below, the source host must be
# one `_source_host_authoritative` accepts, and the block is dated in
# `_provenance` while `_meta.fetched` is left alone.
#
# An allowlisted host certifies the SOURCE, never the figure's meaning: the
# checks here are shape and sanity, and the year assertion is against the cache
# the agent named — whether the page actually names the tax year being filed is
# still the agent's reading and the freshness auditor's check.
# ---------------------------------------------------------------------------

class TaxLawSetError(ValueError):
    """A refused `set`: nothing was written."""


# Key names whose value is a fraction of one. Everything else numeric is an
# amount (dollars, a year, a count), which must be finite and POSITIVE: for an
# indexed figure a zero is the unfetched state wearing a number.
_RATE_KEY_SUFFIXES = ("rate", "factor")
# Dynamic-keyed dicts: the seed ships them empty and the KEY is data.
_DYNAMIC = {
    "exchange_rates": ("currency", r"^[A-Z]{3}$", "a 3-letter upper-case ISO currency code"),
    "federal.historical_top_rate": ("year", r"^(19|20)[0-9]{2}$", "a 4-digit calendar year"),
    "ptet": ("state", r"^[A-Z]{2}$", "a 2-letter upper-case state code"),
}
# Blocks whose applicable period is not the return's tax year, so a
# `_provenance.applies_to` naming another period is correct there and nowhere
# else: a calendar year for the historical top rate, `YYYY-Qn` for an
# underpayment-rate quarter.
_MULTI_PERIOD_PINS = frozenset({"federal.underpayment_rates",
                                "federal.historical_top_rate"})
_QUARTER = re.compile(r"^(19|20)[0-9]{2}-Q[1-4]$")
# (higher, lower) pairs that are NOT sibling single/mfj leaves — those are
# derived from the schema by `_sibling_pairs`, so a block the table has not
# met is covered the day it is seeded.
_ORDERED_PAIRS = (
    # The four standard deductions sit transposed (filing_statuses.<status>.
    # standard_deduction), so the chain rule cannot see them; the joint/single
    # pair is stated directly so it holds before head of household is fetched.
    ("federal.filing_statuses.mfj.standard_deduction",
     "federal.filing_statuses.single.standard_deduction"),
    ("federal.filing_statuses.mfj.standard_deduction",
     "federal.filing_statuses.hoh.standard_deduction"),
    ("federal.filing_statuses.hoh.standard_deduction",
     "federal.filing_statuses.single.standard_deduction"),
    ("federal.filing_statuses.single.standard_deduction",
     "federal.filing_statuses.mfs.standard_deduction"),
    # The 15% breakpoint sits above the 0% one, per status.
    ("federal.capital_gains_rates.fifteen_pct_limit.single",
     "federal.capital_gains_rates.zero_pct_limit.single"),
    ("federal.capital_gains_rates.fifteen_pct_limit.mfj",
     "federal.capital_gains_rates.zero_pct_limit.mfj"),
    ("federal.capital_gains_rates.fifteen_pct_limit.mfs",
     "federal.capital_gains_rates.zero_pct_limit.mfs"),
    ("federal.capital_gains_rates.fifteen_pct_limit.hoh",
     "federal.capital_gains_rates.zero_pct_limit.hoh"),
    ("federal.salt_cap.amount", "federal.salt_cap.mfs_amount"),
    # §24(d)(1)(A): the refundable portion is a subset of the credit.
    ("federal.child_tax_credit.amount_per_child",
     "federal.child_tax_credit.refundable_limit"),
)
# Every per-status map in the cache descends this way: joint, then head of
# household, then single, then separate (half of joint). It holds for every
# map the seed and the state templates carry — an empirical regularity of
# the indexed figures, not a theorem — so a map that legitimately breaks it
# is named here and skipped, rather than the rule weakened for all.
_STATUS_CHAIN = ("mfj", "hoh", "single", "mfs")
_UNORDERED_STATUS_MAPS = frozenset()
# The relations §1(h) actually imposes on the breakpoints, beyond "not below":
# head of household's own amount sits STRICTLY above the "any other
# individual" amount (§1(h)(1)(D)(i)(II)/(III)), and the 15% breakpoint for a
# separate filer is half of joint, strictly below single's. A copied `single`
# column — the likeliest ingest slip, since many secondary tables print only
# single/joint/separate — satisfies every non-strict pair and none of these.
_STRICT_PAIRS = (
    ("federal.capital_gains_rates.zero_pct_limit.hoh",
     "federal.capital_gains_rates.zero_pct_limit.single"),
    ("federal.capital_gains_rates.fifteen_pct_limit.hoh",
     "federal.capital_gains_rates.fifteen_pct_limit.single"),
    ("federal.capital_gains_rates.fifteen_pct_limit.single",
     "federal.capital_gains_rates.fifteen_pct_limit.mfs"),
)
# §1(h)(1)(B)(i) puts separate and single in the same class for the 0% amount:
# equal in every published table.
_EQUAL_PAIRS = (
    ("federal.capital_gains_rates.zero_pct_limit.mfs",
     "federal.capital_gains_rates.zero_pct_limit.single"),
)
# Blocks the seed ships COMPLETE — bill-fixed rates and thresholds, excluded
# from source pinning because nothing indexes them. A cache whose copy differs
# from the seed was written by an older seed (the Form 8938 `mfj_us` row was
# wrong until 2026-09-06) or by hand, and reads INCOMPLETE until re-created.
_STRUCTURAL_BLOCKS = ("federal.se_tax", "federal.niit",
                      "federal.additional_medicare_tax",
                      "federal.foreign_tax_credit_simplified",
                      "federal.form_8938_thresholds")
# Path patterns whose value is a table with its own validator. Anchored on the
# PATH, never the bare leaf name: a future key called `schedule` or `brackets`
# elsewhere in the schema must not inherit a validator meant for another.
_TABLE_VALIDATORS = (
    (re.compile(r"^state\.tax_table\.rows$"), "ca_tax_rows"),
    (re.compile(r"^state\.wa_capital_gains\.rates$"), "brackets"),
    (re.compile(r"^(federal\.filing_statuses\.[a-z]+|state)\.brackets$"), "brackets"),
    # New York's brackets are keyed by §601 rate schedule, the State's and the
    # City's alike, so the table is each schedule and not the block holding them.
    (re.compile(r"^state\.(nyc_tax\.)?brackets\.[a-z]$"), "ny_schedule"),
    (re.compile(r"^state\.tax_table_benefit_recapture\.tiers\.[a-z]$"),
     "ny_recapture_tiers"),
    (re.compile(r"^federal\.underpayment_rates$"), "underpayment_rates"),
    (re.compile(r"^ptet\.[A-Z]{2}\.schedule$"), "ptet_schedule"),
    (re.compile(r"^federal\.irmaa\.tiers\.(single|mfj)$"), "tiers"),
    (re.compile(r"^state\.(nyc_tax\.)?tax_table\.table_bases$"), "table_bases"),
    (_NC_CHILD_TABLE, "nc_child_deduction"),
)
# Leaves whose fetched value is an ISO calendar date, not a number. The seed is
# None (unfetched), which the generic checker otherwise reads as a NUMBER — so
# `set` refused every date and a correctly fetched one read as malformed.
_ISO_DATE_PATHS = frozenset({"state.irc_conformity_date"})


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _is_rate_key(key: str) -> bool:
    return key.endswith(_RATE_KEY_SUFFIXES)


def _get_path(data, dotted: str):
    node = data
    for seg in dotted.split("."):
        if not isinstance(node, dict) or seg not in node:
            return None
        node = node[seg]
    return node


def _zero_is_a_figure(dotted: str) -> bool:
    """Whether a zero AMOUNT at this path is a figure or the unfetched state.

    A state can genuinely have no standard deduction and no personal exemption
    — New Jersey has neither — and the schema's null means "not fetched", so
    refusing zero everywhere left no way to say "this state has none" and the
    cache stayed INCOMPLETE for ever on a figure that had been read correctly.
    The asymmetry is deliberate: every federal amount here is indexed and none
    of them is ever zero, so a federal zero is still the unfetched state
    wearing a number.
    """
    return dotted.split(".", 1)[0] == "state"


def _check_number(dotted: str, value, *, is_rate: "bool | None" = None,
                  allow_zero: bool = False) -> None:
    if is_rate is None:
        is_rate = _is_rate_key(dotted.rsplit(".", 1)[-1])
    if not _is_number(value):
        raise TaxLawSetError(
            f"{dotted}: expected a finite number, got {type(value).__name__}")
    if value < 0:
        raise TaxLawSetError(f"{dotted}: a tax figure cannot be negative ({value})")
    if is_rate and value > 1:
        raise TaxLawSetError(
            f"{dotted}: a rate is a fraction of one, got {value} — write 0.07, not 7")
    if not is_rate and not allow_zero and value == 0 and not _zero_is_a_figure(dotted):
        raise TaxLawSetError(
            f"{dotted}: an indexed amount of zero is the unfetched state wearing "
            "a number; leave the seed's null in place until the figure is read")


def _check_brackets(dotted: str, value) -> None:
    """A progressive schedule: contiguous, ascending, open at the top.

    Bounds are HALF-OPEN — each bracket's `min` equals the previous `max`,
    which is how `compute_bracket_tax` sizes them. The IRS prints them
    inclusive ("over $11,925 but not over $48,475", then "over $48,475"), so a
    `min` one dollar above the previous `max` is the published reading and is
    normalised in place, not refused. The last `max` must be null: a closed top
    bracket leaves income above it untaxed, silently, in every consumer.
    """
    if not isinstance(value, list):
        raise TaxLawSetError(f"{dotted}: brackets must be a list of "
                             "{rate, min, max} objects")
    if not value:
        raise TaxLawSetError(f"{dotted}: an empty bracket list is the unfetched "
                             "state, not a figure")
    prev_max, prev_rate = 0, -1.0
    for i, b in enumerate(value):
        if not isinstance(b, dict) or not {"rate", "min", "max"} <= set(b):
            raise TaxLawSetError(
                f"{dotted}[{i}]: each bracket needs rate, min and max")
        rate, lo, hi = b["rate"], b["min"], b["max"]
        if not _is_number(rate) or not 0 < rate <= 1:
            raise TaxLawSetError(f"{dotted}[{i}]: rate must be in (0, 1], got {rate!r}")
        if not _is_number(lo) or lo < 0:
            raise TaxLawSetError(f"{dotted}[{i}]: min must be a non-negative number")
        if i == 0 and lo != 0:
            raise TaxLawSetError(f"{dotted}[0]: the first bracket starts at zero, got {lo}")
        if i > 0 and lo == prev_max + 1:
            b["min"] = lo = prev_max          # the inclusive reading, normalised
        if i > 0 and lo != prev_max:
            raise TaxLawSetError(
                f"{dotted}[{i}]: brackets must be contiguous — min {lo} does not "
                f"meet the previous max {prev_max} (bounds are half-open: each "
                "min equals the previous max; the IRS's 'over X' reading is "
                "accepted as X + 1 and normalised)")
        if rate <= prev_rate:
            raise TaxLawSetError(f"{dotted}[{i}]: rates must be ascending")
        if hi is None:
            if i != len(value) - 1:
                raise TaxLawSetError(
                    f"{dotted}[{i}]: only the last bracket may have max null")
        elif not _is_number(hi) or hi <= lo:
            raise TaxLawSetError(f"{dotted}[{i}]: max must exceed min")
        elif i == len(value) - 1:
            raise TaxLawSetError(
                f"{dotted}[{i}]: the last bracket's max must be null — a closed "
                "top bracket leaves every dollar above it untaxed")
        prev_max, prev_rate = hi, rate


def _check_ascending(dotted: str, value, *, fields: tuple, order_by: str,
                     key_of=None, first_is_zero: bool = False,
                     rate_fields: tuple = ()) -> None:
    """A non-empty list of entries, each carrying `fields`, strictly ascending
    by `order_by` (through `key_of`, e.g. a date parse), every numeric field a
    valid figure. Shared by the dated lists, the IRMAA tiers and the PTET
    schedule, which used to be three copies of this loop."""
    if not isinstance(value, list) or not value:
        raise TaxLawSetError(f"{dotted}: expected a non-empty list")
    prev = None
    for i, e in enumerate(value):
        if not isinstance(e, dict) or not set(fields) <= set(e):
            raise TaxLawSetError(f"{dotted}[{i}]: each entry needs {', '.join(fields)}")
        try:
            k = key_of(e[order_by]) if key_of else e[order_by]
        except (TypeError, ValueError):
            raise TaxLawSetError(f"{dotted}[{i}]: {order_by} is not readable") from None
        if i == 0 and first_is_zero and k != 0:
            raise TaxLawSetError(f"{dotted}[0]: the first {order_by} is 0")
        if prev is not None and not k > prev:
            raise TaxLawSetError(f"{dotted}: entries must be ascending by {order_by}")
        prev = k
        for f in fields:
            if f != order_by or key_of is None:
                _check_number(f"{dotted}[{i}].{f}", e[f], is_rate=f in rate_fields,
                              allow_zero=True)


def _check_nc_child_table(dotted: str, value) -> None:
    """Use the compute-boundary validator for both ingest and cache audit."""
    try:
        _nc_check_child_table(dotted, value)
    except ValueError as exc:
        raise TaxLawSetError(str(exc)) from None


def _check_iso_date(dotted: str, value) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise TaxLawSetError(f"{dotted}: expected an ISO date YYYY-MM-DD, got {value!r}")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise TaxLawSetError(f"{dotted}: {value!r} is not a calendar date") from None


def _rate_year_problems(data: dict, year: int) -> "list[str]":
    """A year-gated state rate (North Carolina, G.S. 105-153.7) must have been
    read from a page naming THIS year. `state.rate_tax_year` records the year
    the page named; a different one is a carried-over rate, which over-states
    once the statute has cut it."""
    state = data.get("state")
    if not isinstance(state, dict) or state.get("rate_tax_year") is None:
        return []
    named = state["rate_tax_year"]
    if isinstance(named, bool) or not isinstance(named, int):
        return [f"state.rate_tax_year ({named!r}) must be an integer year"]
    if named != year:
        return [f"state.rate_tax_year ({named!r}) is not {year} — the state rate "
                f"was read from a page for another year; the rate is set by "
                f"year in statute, so re-fetch it for {year}"]
    return []


def _stamp_rate_year(data: dict, dotted: str, year: int) -> None:
    """North Carolina's `rate_tax_year` is WRITTEN BY the rate write, never on
    its own: an attestation set separately could vouch for a rate carried over
    from another year, and a rate re-written later would keep the old one.
    Writing the brackets (or the whole block) into THIS year's cache stamps
    the cache year when the band holds a rate, and clears it when it does not."""
    state = data.get("state")
    if not (isinstance(state, dict) and _state_pin_code(state) == "NC"
            and (dotted == "state" or dotted == "state.brackets"
                 or dotted.startswith("state.brackets."))):
        return
    band = state.get("brackets")
    rated = (isinstance(band, list) and band and isinstance(band[0], dict)
             and _is_number(band[0].get("rate")))
    state["rate_tax_year"] = year if rated else None


def _check_table(kind: str, dotted: str, value, *, writing: bool = True) -> None:
    """`writing` is False for the freshness audit, which reads a cache as
    stored: a rule that normalises in place must not let the audit pass a
    value the file still holds un-normalised."""
    if kind == "brackets":
        _check_brackets(dotted, value)
    elif kind == "ca_tax_rows":
        from agents.lib.tax_computations.state.planning import validate_ca_tax_rows
        try:
            validate_ca_tax_rows(value)
        except ValueError as exc:
            raise TaxLawSetError(str(exc)) from None
    elif kind in ("ny_schedule", "ny_recapture_tiers"):
        # New York's own rules are the engine's, so the two cannot drift. A
        # schedule meets the generic bracket rules first, which normalise an
        # inclusive "over X" floor; the audit then holds the STORED copy to
        # New York's, because the engine reads the file as it is.
        schedule = dotted.rsplit(".", 1)[-1]
        stored = copy.deepcopy(value)
        if kind == "ny_schedule":
            _check_brackets(dotted, value)
        elif not isinstance(value, list) or not value:
            raise TaxLawSetError(f"{dotted}: expected a non-empty list of tiers")
        check = _ny_check_schedule if kind == "ny_schedule" else _ny_check_recapture_tiers
        try:
            check(value if writing else stored, schedule)
        except ValueError as exc:
            raise TaxLawSetError(f"{dotted}: {exc}") from None
    elif kind == "underpayment_rates":
        _check_ascending(dotted, value, fields=("effective_from", "annual_rate"),
                         order_by="effective_from", rate_fields=("annual_rate",),
                         key_of=lambda d: datetime.strptime(str(d), "%Y-%m-%d"))
    elif kind == "tiers":
        _check_ascending(dotted, value,
                         fields=("magi_threshold", "annual_surcharge_per_person"),
                         order_by="magi_threshold")
    elif kind == "table_bases":
        # EMPTY is a value here, not the unfetched state: the table was
        # compared with its schedule and follows it throughout. The shape is
        # the engine's own check, so the two cannot drift.
        try:
            _ny_table_bases_entries(value, dotted)
        except ValueError as exc:
            raise TaxLawSetError(str(exc)) from None
    elif kind == "nc_child_deduction":
        _check_nc_child_table(dotted, value)
    elif kind == "ptet_schedule":
        rows = []
        for i, row in enumerate(value if isinstance(value, list) else []):
            if not isinstance(row, list) or len(row) != 3:
                raise TaxLawSetError(f"{dotted}[{i}]: each row is [lower, rate, base_tax]")
            rows.append(dict(zip(("lower", "rate", "base_tax"), row)))
        _check_ascending(dotted, rows or value, fields=("lower", "rate", "base_tax"),
                         order_by="lower", first_is_zero=True, rate_fields=("rate",))


def _check_against(dotted: str, ref, value, *, top: bool = True,
                   strict: bool = True) -> None:
    """`value` has the shape `ref` (the seed at `dotted`) prescribes, recursively.

    `top` is the leaf or block the command names. Below it, a whole-block write
    (the agent pasting `create_state_template` output, then filling fields one
    by one) may carry the seed's own unfetched leaves — a null where the seed
    is null, an empty list where the seed's is empty. The figure named on the
    command line is never allowed to be one of those. A block write must carry
    EXACTLY the seed's keys (`strict`): a partial block would replace a fuller
    one, and the freshness walker cannot see a key that is gone. The freshness
    audit runs this over caches it did not write, so it validates the keys that
    are present and leaves absence to the walker.
    """
    if dotted in ("state.modifications", "state.local"):
        from agents.lib.tax_computations.state.generic import (
            validate_state_local, validate_state_modifications,
        )
        validator = (validate_state_modifications if dotted.endswith("modifications")
                     else validate_state_local)
        try:
            validator(value)
        except ValueError as exc:
            raise TaxLawSetError(str(exc)) from None
        return
    if value is None:
        if not top and ref is None:
            return
        raise TaxLawSetError(f"{dotted}: null is the unfetched state, not a figure")
    # The seed's own unfetched table — `[]`, or a band whose figures are
    # still null (North Carolina's one-band schedule) — inside a block write.
    if not top and isinstance(ref, list) and value in ([], ref):
        return
    for pattern, kind in _TABLE_VALIDATORS:
        # A table the schema keys by schedule or status (New York's brackets)
        # is a block of tables, not one: it recurses below, and each table
        # matches its own pattern. Read as one, every fetched New York cache
        # failed (AA28). A table validator applies where the seed is a list.
        if pattern.match(dotted) and not isinstance(ref, dict):
            _check_table(kind, dotted, value, writing=strict)
            return
    if re.fullmatch(r"retirement_conformity\.[A-Z]{2}\.(employee_401k|traditional_ira|hsa)", dotted):
        if type(value) is not bool:
            raise TaxLawSetError(f"{dotted}: expected true or false")
        return
    if isinstance(ref, list):
        if not isinstance(value, list):
            raise TaxLawSetError(f"{dotted}: expected a list")
    elif isinstance(ref, dict):
        if not isinstance(value, dict) or not value:
            raise TaxLawSetError(f"{dotted}: expected a non-empty object with keys "
                                 f"{sorted(ref)}")
        dynamic = _DYNAMIC.get(dotted)
        if strict and not dynamic and set(value) != set(ref):
            raise TaxLawSetError(
                f"{dotted}: a block is written with exactly the schema's keys "
                f"{sorted(ref)} — extra {sorted(set(value) - set(ref))}, missing "
                f"{sorted(set(ref) - set(value))}; set one leaf at a time, or "
                "paste the seed's block and fill it")
        for k, v in value.items():
            child = f"{dotted}.{k}"
            if dynamic:
                _check_dynamic_key(dotted, k)
                _check_against(child, _dynamic_ref(dotted), v, top=False, strict=strict)
            elif k in ref:
                _check_against(child, ref[k], v, top=False, strict=strict)
    elif isinstance(ref, bool):
        if not isinstance(value, bool):
            raise TaxLawSetError(f"{dotted}: expected true or false")
    elif isinstance(ref, str):
        if not isinstance(value, str) or not value.strip():
            raise TaxLawSetError(f"{dotted}: expected a non-empty string")
    elif dotted in _ISO_DATE_PATHS:
        _check_iso_date(dotted, value)
    else:
        # A None seed (unfetched) or a numeric structural constant.
        parent = dotted.rsplit(".", 1)[0] if "." in dotted else ""
        _check_number(dotted, value, is_rate=(parent == "federal.historical_top_rate") or None)
        if parent in ("exchange_rates", "federal.historical_top_rate") and value <= 0:
            raise TaxLawSetError(f"{dotted}: must be positive")


def _check_dynamic_key(parent: str, key: str) -> None:
    what, pattern, desc = _DYNAMIC[parent]
    if not isinstance(key, str) or not re.match(pattern, key):
        raise TaxLawSetError(f"{parent}.{key}: the {what} key must be {desc}")


def _dynamic_ref(parent: str):
    """The seed a dynamic dict's entries are validated against."""
    if parent == "ptet":
        return {"schedule": [], "vintage": ""}
    return None  # a number: an exchange rate or a historical top rate


def _seed_root(dotted: str, data: dict, year: int):
    """The schema tree `dotted` is resolved in, and the segments under it."""
    segs = dotted.split(".")
    # Optional planning blocks materialize only on their own fetch. Adding one
    # must not change a core return scaffold or the pinned auditor fixture.
    if segs[0] == "retirement_conformity" and len(segs) > 1:
        return create_retirement_conformity_template(), segs[1:], "retirement_conformity"
    if segs[0] == "state" and len(segs) > 1:
        code = declared_state_code(data)
        if code is None:
            raise TaxLawSetError(
                f"{dotted}: no state block yet — set `state` to the "
                "create_state_template output first, then its fields")
        return state_schema(code, year), segs[1:], "state"
    return create_empty_tax_law(year), segs, ""


def declared_state_code(data: dict) -> "str | None":
    """The `state.code` a `set` under the state block is validated by, AS
    WRITTEN, or None when there is none and `set` refuses the block.

    Not `_state_pin_code`, which upper-cases a code and maps a display name for
    the PIN's sake: `create_state_template("ca")` is the generic template, so a
    block coded `ca` was validated against generic keys. `tax_law_export` hands
    the auditor the seed through this, so it is the template the block was
    actually written against.
    """
    state = data.get("state")
    if isinstance(state, dict) and isinstance(state.get("code"), str):
        return state["code"]
    return None


def state_schema(code: str, year: int) -> dict:
    """The state block a write is checked against: the state's template plus the
    two optional rule blocks, which materialize only on their own fetch.

    One builder, because two readers need the same answer: `set` validates a
    state path against it, and `tax_law_export` hands it to the freshness
    auditor as the schema that auditor checks a cache against.
    """
    schema = create_state_template(code, tax_year=year)
    schema["modifications"] = create_state_modifications_template()
    schema["local"] = create_state_local_template()
    return schema


def _reference_for(dotted: str, data: dict, value, year: int):
    """The seed node at `dotted`, or a TaxLawSetError naming why there is none."""
    segs = dotted.split(".")
    if len(segs) > 2 and segs[:2] in (["state", "modifications"], ["state", "local"]):
        raise TaxLawSetError("Write the complete optional state rule block with its year and source")
    if segs[:2] in (["state", "modifications"], ["state", "local"]):
        state = data.get("state")
        if not isinstance(state, dict) or state.get("code") not in _STATE_NAME_TO_CODE.values():
            raise TaxLawSetError("Optional rule blocks require a valid state code")
    if any(seg.startswith("_") for seg in segs) or dotted == "tax_year":
        raise TaxLawSetError(
            f"{dotted}: identity and metadata are not figures — tax_year is set "
            "by `create`, and _sources/_provenance are written by this command")
    if dotted in ("federal", "ptet", "retirement_conformity"):
        raise TaxLawSetError(
            f"{dotted}: too coarse — every block under it is pinned and dated on "
            f"its own, so write `{dotted}.<block>` (or a leaf) one at a time")
    if dotted == "state":
        if not isinstance(value, dict) or not isinstance(value.get("code"), str):
            raise TaxLawSetError("state: the whole block must be an object "
                                 "carrying a 2-letter `code`")
        return create_state_template(value["code"], tax_year=year)
    node, segs, dotted = _seed_root(dotted, data, year)
    for seg in segs:
        parent = dotted
        dotted = f"{dotted}.{seg}" if dotted else seg
        if parent in _DYNAMIC:
            _check_dynamic_key(parent, seg)
            node = _dynamic_ref(parent)
            continue
        if not isinstance(node, dict) or seg not in node:
            where = f"under {parent}" if parent else "at the top level"
            known = sorted(k for k in node if not k.startswith("_")) \
                if isinstance(node, dict) else []
            raise TaxLawSetError(
                f"{dotted}: not in the schema {where} — known keys are {known} "
                "(see create_empty_tax_law / create_state_template)")
        node = node[seg]
    return node


def _set_path(data: dict, dotted: str, value, year: int) -> None:
    """Write `value` at `dotted`, materialising a missing parent from the
    SCHEMA rather than as `{}`: an empty parent hides every sibling the
    freshness walker would otherwise report absent."""
    import copy
    seed, segs, root = _seed_root(dotted, data, year)
    node = data
    if root:
        if not isinstance(data.get(root), dict):
            data[root] = {}
        node = data[root]
    for seg in segs[:-1]:
        nxt = node.get(seg)
        if not isinstance(nxt, dict):
            nxt = copy.deepcopy(seed.get(seg)) if isinstance(seed, dict) and \
                isinstance(seed.get(seg), dict) else {}
            node[seg] = nxt
        node = nxt
        seed = seed.get(seg) if isinstance(seed, dict) else None
    node[segs[-1]] = value


def _pin_key(dotted: str, data: dict) -> str:
    """The `_sources` / `_provenance` pin a dotted path belongs to."""
    segs = dotted.split(".")
    if segs[0] in ("federal", "ptet", "retirement_conformity"):
        return ".".join(segs[:2])
    if segs[0] == "state":
        code = _state_pin_code(data.get("state") or {})
        if len(segs) >= 2 and segs[1] in ("modifications", "local"):
            return f"state.{code}.{segs[1]}"
        if code == "FL" and dotted == "state.sales_tax.county_surtax_rate":
            return "state.FL.county_surtax_rate"
        return f"state.{code}"
    return segs[0]


def _sibling_pairs(data: dict) -> "list[tuple[str, str]]":
    """Every per-status map in the cache, as (higher, lower) pairs down the
    `_STATUS_CHAIN` over whichever of its keys hold numbers — derived, so a
    block the ordering table never met is covered the day it is seeded, and a
    fifth status is one entry in the chain."""
    pairs = []

    def walk(node, dotted):
        if not isinstance(node, dict):
            return
        if dotted not in _UNORDERED_STATUS_MAPS:
            present = [k for k in _STATUS_CHAIN if _is_number(node.get(k))]
            pairs.extend((f"{dotted}.{hi}", f"{dotted}.{lo}")
                         for hi, lo in zip(present, present[1:]))
        for k, v in node.items():
            if not str(k).startswith("_"):
                walk(v, f"{dotted}.{k}" if dotted else str(k))
    walk(data, "")
    return pairs


def _sanity(data: dict) -> None:
    """Cross-field checks over the whole cache after a write."""
    for higher, lower in _sibling_pairs(data) + list(_ORDERED_PAIRS):
        hi, lo = _get_path(data, higher), _get_path(data, lower)
        if _is_number(hi) and _is_number(lo) and hi < lo:
            raise TaxLawSetError(
                f"{higher} ({hi}) is below {lower} ({lo}) — the joint or "
                "higher figure can never sit below the other; one of the "
                "two was read wrong")
    for higher, lower in _STRICT_PAIRS:
        hi, lo = _get_path(data, higher), _get_path(data, lower)
        if _is_number(hi) and _is_number(lo) and hi <= lo:
            raise TaxLawSetError(
                f"{higher} ({hi}) is not above {lower} ({lo}) — §1(h) gives it "
                "its own, strictly higher amount; a copied column is the usual "
                "cause")
    for left, right in _EQUAL_PAIRS:
        a, b = _get_path(data, left), _get_path(data, right)
        if _is_number(a) and _is_number(b) and a != b:
            raise TaxLawSetError(
                f"{left} ({a}) differs from {right} ({b}) — §1(h) puts the two "
                "in the same class; they are equal in every published table")


def _structural_drift(data: dict, year: int) -> "list[str]":
    """Leaves of the seed-complete blocks whose cache value is not the seed's."""
    seed = create_empty_tax_law(year)
    problems = []

    def walk(ref, node, dotted):
        if isinstance(ref, dict):
            for k, v in ref.items():
                walk(v, (node or {}).get(k) if isinstance(node, dict) else None,
                     f"{dotted}.{k}")
        elif _is_number(ref) and _is_number(node) and node != ref:
            problems.append(
                f"{dotted} ({node}) differs from the seed's {ref} — a bill-fixed "
                "figure this command never writes; the cache was created by an "
                "older seed or edited by hand. Re-run `create` and `set` the "
                "fetched figures again")
    for block in _STRUCTURAL_BLOCKS:
        walk(_get_path(seed, block), _get_path(data, block), block)
    return problems


def validate_cache(data: dict, year: int) -> "list[str]":
    """The write path's rules over a whole cache: every populated block against
    its seed (unfetched leaves allowed), then the cross-field orderings. One
    string per violation, for `check_freshness` to report as INCOMPLETE."""
    problems = []
    state = data.get("state")
    if isinstance(state, dict) and _state_pin_code(state) == "CA":
        try:
            validate_ca_exemption_phaseout(state.get("exemption_phaseout"))
        except ValueError as exc:
            problems.append(str(exc))
        try:
            from agents.lib.tax_computations.state.ca import validate_ca_return_law
            validate_ca_return_law(state)
        except ValueError as exc:
            problems.append(str(exc))
    if isinstance(state, dict):
        from agents.lib.tax_computations.state.generic import (
            validate_state_local, validate_state_modifications,
        )
        for kind, validator in (("modifications", validate_state_modifications),
                                ("local", validate_state_local)):
            if kind in state:
                try:
                    validator(state[kind], year)
                except ValueError as exc:
                    problems.append(f"state.{kind}: {exc}")
    for _pin, path in _expected_pins(data):
        value = _get_path(data, path)
        if not _has_real_data(value):
            continue
        try:
            if path == "state":
                ref = create_state_template(_state_pin_code(value), tax_year=year)
            else:
                ref = _reference_for(path, data, value, year)
            _check_against(path, ref, value, top=False, strict=False)
        except TaxLawSetError as e:
            problems.append(str(e))
    try:
        _sanity(data)
    except TaxLawSetError as e:
        problems.append(str(e))
    matrix = data.get("retirement_conformity")
    if matrix is not None:
        if not isinstance(matrix, dict):
            problems.append("retirement_conformity must be an object")
        else:
            schema = create_retirement_conformity_template()
            for code, row in matrix.items():
                if code not in schema or not isinstance(row, dict):
                    problems.append(f"retirement_conformity.{code}: unknown jurisdiction or invalid row")
                    continue
                try:
                    _check_against(f"retirement_conformity.{code}", schema[code], row,
                                   top=False, strict=False)
                except TaxLawSetError as e:
                    problems.append(str(e))
                if any(row.get(k) is not None for k in ("employee_401k", "traditional_ira", "hsa")):
                    if type(row.get("tax_year")) is not int or row["tax_year"] != year:
                        problems.append(f"retirement_conformity.{code}.tax_year must match this year")
    problems.extend(_structural_drift(data, year))
    problems.extend(_rate_year_problems(data, year))
    # The walker sees only keys that are present. A cache keyed single/mfj
    # (pre-X1) reads None for head of household and separate at the accessor,
    # which then refuses naming /tax-setup — while FRESH said proceed.
    cg = _get_path(data, "federal.capital_gains_rates")
    if isinstance(cg, dict):
        for limit in ("zero_pct_limit", "fifteen_pct_limit"):
            per_status = cg.get(limit)
            if isinstance(per_status, dict) and any(_is_number(v) for v in per_status.values()):
                for status in ("single", "mfj", "mfs", "hoh"):
                    if status not in per_status:
                        problems.append(
                            f"federal.capital_gains_rates.{limit}.{status} (key absent — "
                            "the §1(h) breakpoints are keyed by all four statuses; a "
                            "two-key cache predates that and must be re-fetched)")
    return problems


def _check_applies_to(pin: str, dotted: str, period, year: int):
    """The period a figure covers: the tax year unless the pin is multi-period."""
    if pin not in _MULTI_PERIOD_PINS:
        if period is None:
            return year
        if isinstance(period, bool) or not isinstance(period, int) or period != year:
            raise TaxLawSetError(
                f"{dotted}: applies_to {period!r} — this figure is a {year} figure "
                f"or it does not belong in the {year} cache; --applies-to is "
                "only for historical_top_rate (a year) and underpayment_rates "
                "(a YYYY-Qn quarter)")
        return year
    if pin == "federal.historical_top_rate":
        key_year = int(dotted.rsplit(".", 1)[-1]) if dotted != pin else None
        if period is None:
            period = key_year
        if isinstance(period, bool) or not isinstance(period, int) \
                or (key_year is not None and period != key_year):
            raise TaxLawSetError(
                f"{dotted}: applies_to must be the calendar year the rate "
                f"applied to ({key_year})")
        return period
    # underpayment_rates: every entry carries its own effective date, so the
    # period is optional — a year or a YYYY-Qn quarter when given.
    if period is None:
        return year
    if (isinstance(period, bool) or not (isinstance(period, int)
                                          or (isinstance(period, str) and _QUARTER.match(period)))):
        raise TaxLawSetError(
            f"{dotted}: applies_to must be a calendar year or a YYYY-Qn quarter")
    return period


def set_figure(year: int, dotted: str, value, source: str, workspace: str = ".",
               *, applies_to=None, now: "datetime | None" = None) -> dict:
    """Write ONE validated figure into `tax_law_<year>.json`, pin its source
    and date it. Returns {"dotted", "pin", "retrieved", "applies_to"}.

    Refuses, writing nothing, when: the cache does not exist (run `create`),
    its `tax_year` is not `year`, the dotted is outside the schema, the value
    is null or the wrong shape or out of range, a block write does not carry
    the schema's exact keys, an ordering is violated, the period is not the
    one the pin allows, or the source host is not one the pinning policy
    accepts.

    `_provenance` is keyed by the FIGURE written (the block's pin when the
    whole block is), each entry carrying its own source, so one re-fetched
    leaf never re-dates its siblings; `_sources[pin]` records the first
    source a block was read from and is replaced only by a whole-block write.
    """
    year = int(year)
    if not _source_host_authoritative(source):
        raise TaxLawSetError(
            f"source {source!r} is not authoritative — an http(s) URL on a "
            ".gov host or one of the explicit primary-authority exceptions "
            "(_source_host_authoritative); a figure without a primary source "
            "is not written")
    if not isinstance(dotted, str) or not dotted or not all(dotted.split(".")):
        raise TaxLawSetError(f"path {dotted!r} is not a dotted key path")
    data = load_tax_law(year, workspace)
    if data is None:
        raise TaxLawSetError(
            f"no tax_law_{year}.json in {redact(workspace)} — run `create {year}` "
            "first; this command writes figures into the seed, never a new file")
    if data.get("tax_year") != year:
        raise TaxLawSetError(
            f"the cache declares tax_year {data.get('tax_year')!r} but --year is "
            f"{year} — refusing to write a figure for one year into another's cache")
    if re.fullmatch(r"retirement_conformity\.[A-Z]{2}\.tax_year", dotted):
        raise TaxLawSetError("Write the complete conformity row with its year and source")
    if dotted == "state.wa_capital_gains.tax_year":
        raise TaxLawSetError("Write the complete WA parameters block with its year and source; never a separate year attestation")
    if dotted == "state.rate_tax_year":
        raise TaxLawSetError(
            "state.rate_tax_year is stamped by writing state.brackets into this "
            "year's cache, never set on its own — an attestation written apart "
            "from the rate can vouch for a rate carried over from another year")
    ref = _reference_for(dotted, data, value, year)
    _check_against(dotted, ref, value)
    if dotted in ("state.modifications", "state.local") and value.get("tax_year") != year:
        raise TaxLawSetError("Optional state rule block must explicitly name this cache year")
    _set_path(data, dotted, value, year)
    _stamp_rate_year(data, dotted, year)
    if dotted == "state" or dotted == "state.wa_capital_gains":
        wa_cfg = data.get("state", {}).get("wa_capital_gains")
        if data.get("state", {}).get("code") == "WA" and (not isinstance(wa_cfg, dict) or type(wa_cfg.get("tax_year")) is not int or wa_cfg["tax_year"] != year):
            raise TaxLawSetError("WA parameters must explicitly name this cache year")
    elif dotted.startswith("state.wa_capital_gains."):
        data["state"]["wa_capital_gains"]["tax_year"] = None
    if dotted.startswith("retirement_conformity."):
        parts = dotted.split(".")
        row = data["retirement_conformity"][parts[1]]
        if len(parts) == 2:
            if type(row.get("tax_year")) is not int or row["tax_year"] != year:
                raise TaxLawSetError("Conformity row must explicitly name this cache year")
        else:
            row["tax_year"] = None  # Partial edits cannot re-attest siblings.
    _sanity(data)
    for problem in _rate_year_problems(data, year):
        raise TaxLawSetError(problem)
    pin = _pin_key(dotted, data)
    period = _check_applies_to(pin, dotted, applies_to, year)
    when = (now or datetime.now()).replace(microsecond=0).isoformat()
    whole_block = dotted == "state" or dotted in {p for _pin, p in _expected_pins(data)}
    sources = data.get("_sources")
    if not isinstance(sources, dict):
        sources = {}
    if whole_block or pin not in sources:
        sources[pin] = source
    data["_sources"] = sources
    provenance = data.get("_provenance")
    if not isinstance(provenance, dict):
        provenance = {}
    key = pin if whole_block else dotted
    # A write REPLACES everything under its own path, so an entry for a figure
    # inside it is superseded. Left behind, that entry goes on dating the block
    # from a retrieval this write has just overwritten — a re-fetched block
    # could never clear STALE, which is a gate crying wolf on correct data.
    for superseded in [k for k in provenance if k.startswith(f"{key}.")]:
        del provenance[superseded]
    provenance[key] = {"retrieved": when, "applies_to": period, "source": source}
    data["_provenance"] = provenance
    save_tax_law(year, data, workspace, stamp_fetched=False)
    return {"dotted": dotted, "pin": pin, "retrieved": when, "applies_to": period}


def _parse_cli_value(raw: str):
    """JSON when it parses; a bare word is a string. A value that starts like
    JSON and fails is a refusal, not a string — `[{bad` is a typo, not a name."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        if raw.lstrip()[:1] in ("[", "{", '"'):
            raise TaxLawSetError(f"value is not valid JSON: {redact(e)}") from None
        return raw


def _cli_workspace(index: int) -> str:
    """The workspace for a positional-argv subcommand, or exit 1 saying why.

    These three subcommands take their workspace as a bare positional and used
    to default it to `"."`. `resolve_workspace` supplies `$TAX_WORKSPACE`
    instead, and its refusal has to reach the user as a line rather than a
    traceback — this block has no `try` of its own around the dispatch.
    """
    try:
        return str(resolve_workspace(
            sys.argv[index] if len(sys.argv) > index else None))
    except WorkspaceError as e:
        print(f"Error: {redact(e)}", file=sys.stderr)
        sys.exit(1)


def _set_main(argv: list) -> int:
    import argparse
    p = argparse.ArgumentParser(
        prog="tax_law_fetcher.py set",
        description="Write one validated tax-law figure, pin its source and date its block.")
    p.add_argument("dotted", help="dotted key path, e.g. federal.filing_statuses.mfj.standard_deduction")
    p.add_argument("value", help="the figure as JSON (a number, a list, an object); a bare word is a string")
    p.add_argument("--source", required=True, help="the authoritative URL the figure was read from")
    p.add_argument("--year", required=True, type=int, help="the tax year; must equal the cache's tax_year")
    add_workspace_argument(p)
    p.add_argument("--applies-to", default=None,
                   help="the period the figure covers when it is not the tax year "
                        "(a calendar year for historical_top_rate, a quarter for underpayment_rates)")
    args = p.parse_args(argv)
    try:
        period = args.applies_to
        if period is not None and re.fullmatch(r"\d{4}", period):
            period = int(period)
        workspace = resolve_workspace(args.workspace)
        hold_workspace(workspace, "tax_law_fetcher")
        out = set_figure(args.year, args.dotted, _parse_cli_value(args.value),
                         args.source, str(workspace), applies_to=period)
    except (WorkspaceError, ValueError) as e:  # TaxLawSetError is one
        print(f"Error: {redact(e)}", file=sys.stderr)
        return 1
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python tax_law_fetcher.py <create|load|freshness|state> <year> [state|workspace] [workspace]\n"
              "       python tax_law_fetcher.py set <dotted.path> <value> --source <url> --year <y> [--workspace .] [--applies-to <period>]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "set":
        sys.exit(_set_main(sys.argv[2:]))
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
        workspace = _cli_workspace(3)
        # `create` OVERWRITES `tax_law_{year}.json` wholesale, which is the
        # most destructive thing this module does — `set` edits one figure and
        # was locked first only because it was the subcommand being read at the
        # time. `load`, `freshness` and `state` take no lock: they read, or
        # print a template, and refusing them while a compute ran would be a
        # gate firing on an operation that cannot corrupt anything.
        hold_workspace(workspace, "tax_law_fetcher")
        path = save_tax_law(year, data, workspace)
        print(f"Created empty tax law template: {redact(path)}")
    elif cmd == "load":
        workspace = _cli_workspace(3)
        data = load_tax_law(year, workspace)
        if data:
            print(json.dumps(data, indent=2))
        else:
            print(f"No cached tax law for {year}. Run /tax-setup to fetch.")
    elif cmd == "freshness":
        workspace = _cli_workspace(3)
        # THE EXIT CODE ANSWERS ONE QUESTION: must this run STOP? It is the
        # gates' code — 2 blocked, 1 could not run, 0 otherwise — and it is
        # spent on NOT_FOUND alone, because NOT_FOUND is the only status whose
        # documented remedy is a stop. Step 0.5 says so in every skill that runs
        # this: "NOT_FOUND: STOP. Tell user to run /tax-setup first."
        #
        # This command used to exit 0 for ALL FOUR statuses, so a workspace with
        # no cache — that documented STOP — reported success to anything reading
        # `$?`. AGENTS.md §2 opens by saying a stale figure "silently produces a
        # wrong return that looks completely plausible" and that there is no
        # downstream check for it; this is that check, and it was the only gate
        # that could not fail.
        #
        # STALE and INCOMPLETE STAY 0, and the first version of this fix got that
        # wrong. Their remedies are inline and the same step spells them out —
        # STALE: check the web, "if no changes, proceed"; INCOMPLETE: re-fetch
        # the fields named in `null_fields`, then proceed. Neither is a stop.
        # Worse, INCOMPLETE fires on CORRECT DATA: `exchange_rates` is empty for
        # every domestic-only taxpayer, so blocking on it would have exited 2 on
        # every Step 0.5 of a wholly ordinary return. `gates-that-cry-wolf.md` is
        # about exactly that — a STOP-gate firing on correct data teaches the
        # agent to reason past it, and then it is worth nothing on the day it is
        # right.
        #
        # The JSON is what a reader ACTS on — which figures, how old, what to
        # re-fetch — and is printed for every status. The exit code carries the
        # one bit a script can act on without parsing it.
        try:
            result = check_freshness(year, workspace)
        except OSError as e:
            # REACHABLE, and a claim that it was not stood here for one revision:
            # `load_tax_law` reads the cache with `read_text()`, so a file this
            # process cannot read — AGENTS.md §1 mandates 0600 on it, and a
            # restored backup or a file owned by another uid is the ordinary way
            # there — raises PermissionError straight through. "The figures are
            # stale" and "I could not tell" are different instructions, and a
            # traceback is neither.
            print(f"Error: could not check freshness: {redact(e)}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(result, indent=2))
        sys.exit(2 if result.get("status") == "NOT_FOUND" else 0)
    elif cmd == "state":
        state = sys.argv[3] if len(sys.argv) > 3 else "CA"
        # `year` (parsed above from argv[2], defaulting to last calendar
        # year) is now threaded through as tax_year — round-3 finding D:
        # create_state_template needs the year to resolve TN's Hall-tax
        # repeal gate (pre-2021 vs 2021+). CA/NY/FL and the always-none
        # states (AK/NV/SD/TX/WY) are unaffected by it.
        template = create_state_template(state, tax_year=year)
        print(json.dumps(template, indent=2))
    else:
        print(f"Error: Unknown command '{cmd}'. Valid commands: create, load, freshness, state, set",
              file=sys.stderr)
        sys.exit(1)
