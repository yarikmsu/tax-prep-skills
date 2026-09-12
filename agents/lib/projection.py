#!/usr/bin/env python3
"""Mid-year tax projection for /tax-project.

Read-only: never writes result.json or taxpayer_profile.json. Pure compute
functions + a markdown renderer + a CLI. Reuses tax_computations.py for tax
math and rollover.py for the prior-year baseline + path/year validation.
"""

import calendar
import datetime
import json
import math
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agents.lib.rollover import (  # noqa: E402
    RolloverError,
    _validate_year_arg,
    load_prior,
)
from agents.lib.tax_computations import (  # noqa: E402
    CAPITAL_LOSS_LIMIT,
    CAPITAL_LOSS_LIMIT_MFS,
    DE_MINIMIS_BALANCE,
    PRIOR_YEAR_AGI_THRESHOLD,
    PRIOR_YEAR_AGI_THRESHOLD_MFS,
    _RETURN_DUE_POSTPONED,
    _return_due_date,
    _coerce_numeric,
    prior_year_prong,
    compute_additional_medicare,
    compute_bracket_tax,
    compute_child_tax_credit,
    compute_niit,
    compute_qbi_deduction,
    compute_qdcg_tax,
    qdcg_worksheet_applies,
    compute_se_tax,
    net_capital_gains,
    _split_remainder_last,
    state_brackets_for_status,
    _SCH1A_EFFECTIVE_FROM,
    _SCH1A_EFFECTIVE_THROUGH,
)
from agents.lib.tax_law_fetcher import (  # noqa: E402
    _TN_HALL_REPEAL_YEAR,
    _none_type_allowed,
)
from agents.lib.irs_dates import (  # noqa: E402
    next_business_day,
    next_business_day_state,
)
from agents.lib.capabilities import _USPS_STATES  # noqa: E402
from agents.lib.secure_fs import redact, year_or_shape  # noqa: E402
from agents.lib.workspace import (  # noqa: E402
    WorkspaceError,
    add_workspace_argument,
    resolve_under_workspace,
    resolve_workspace,
)


class ProjectionError(Exception):
    """Raised when /tax-project cannot safely proceed."""


PAY_PERIODS = {"weekly": 52, "biweekly": 26, "semimonthly": 24, "monthly": 12}
_MFJ_LIKE = {"MFJ", "QSS"}
ES_DUE_DATES = [(1, (4, 15)), (2, (6, 15)), (3, (9, 15)), (4, (1, 15))]  # Q4 due Jan 15 next year

# CA 540-ES installments are NOT four equal quarters: 30% / 40% / 0% / 30%
# per R&TC §19136.1 (FTB Form 540-ES instructions) — there is NO September
# (Q3) installment. Q4 is due Jan 15 of the following year, like federal.
# Structural statutory constants (weights are set in statute, not indexed).
CA_ES_SCHEDULE = [(1, (4, 15), 0.30), (2, (6, 15), 0.40),
                  (3, (9, 15), 0.00), (4, (1, 15), 0.30)]

# R&TC §19136.3: taxpayers with CURRENT-year CA AGI at or above these
# thresholds may not use the prior-year safe harbor at all — they must pay
# 90% of the current-year tax. Not indexed for inflation.
CA_HIGH_INCOME_AGI_LIMIT = 1_000_000
CA_HIGH_INCOME_AGI_LIMIT_MFS = 500_000

# R&TC §19136 (FTB 5805 Part I): NO CA underpayment penalty when current-year
# tax minus withholding is under $500 ($250 MFS). Statutory-fixed, not indexed.
CA_ES_DE_MINIMIS = 500
CA_ES_DE_MINIMIS_MFS = 250

# §6654 lives in tax_computations, and this module APPLIES it rather than
# restating it: `prior_year_prong` for the 100%/110%-of-prior test and
# `DE_MINIMIS_BALANCE` for §6654(e)(1). `compute_form_2210` there prices the
# penalty on the filed return from the same statute, and `tax-safe-harbor`
# restates it in prose having no engine to call — three readers, one
# definition. The two AGI-threshold constants are imported here only to build
# `_PRIOR_AGI_THRESHOLD_TEXT`, so the three "prior AGI unavailable" notes
# cannot drift from the figure `prior_year_agi_threshold` tests against; the
# FACTORS are not imported, because nothing here restates them. Read both from
# the module that owns them — the prose-parity tests do.

_CANONICAL_STATUSES = {"SINGLE", "MFJ", "MFS", "HOH", "QSS"}


def _status_text(filing_status, where: str) -> str:
    """`filing_status` as a stripped string, refusing anything that is not one.

    The TYPE half of the parse boundary AGENTS.md §8 asks for, which
    `_canonical_status` was missing: `(filing_status or "").strip()` reached
    `.strip()` on any truthy non-string a malformed profile or result.json
    carried (`7`, `true`) and raised AttributeError. Neither CLI catches that
    — both catch ProjectionError/ValueError — so a data problem surfaced as a
    traceback instead of the refusal the value check exists to give.
    `withholding.build_report` reads the status straight off result.json with
    no check of its own, which is the path that reaches it unexamined.

    None and "" stay ABSENT and are refused by the VALUE check below with the
    message that names the canonical set; a non-string is refused here, where
    the type can still be named.
    """
    if filing_status is None:
        return ""
    if not isinstance(filing_status, str):
        raise ProjectionError(
            f"filing_status must be a string for the {where}, got "
            f"{type(filing_status).__name__}")
    return filing_status.strip()


def _canonical_status(filing_status: "str | None", where: str) -> str:
    """`filing_status` upper-cased and VALUE-checked, or ProjectionError.

    Both safe harbors read the status for the same cliffs — the $150K/$75K-MFS
    threshold that picks the 110% factor, and on the CA side the $500K
    §19136.3 limit and the $250 de minimis. A non-canonical spelling
    ("Married Filing Separately") silently takes the non-MFS window, so an MFS
    filer with $100,000 of prior AGI gets the 100% factor instead of 110% and
    the required annual payment comes out 10% LOW — the understating direction
    that ends in the penalty. Nothing downstream catches it: `_full_status_key`
    falls back to "single" just as quietly.

    Shared because the CA harbor validated and the federal one did not, so one
    profile was refused on one side of the report and quietly mis-computed on
    the other.
    """
    status = _status_text(filing_status, where).upper()
    if not status:
        # ABSENT gets its own message. Both cases used to land on the
        # "unrecognized value (0 chars)" branch below, which sends a reader
        # hunting for a bad value in a file where the key is simply missing,
        # and never names the remedy. `_status_text` folds None and "" to the
        # same place deliberately: a key present but empty IS absent.
        raise ProjectionError(
            f"filing_status is missing for the {where} — set it to one of "
            "Single/MFJ/MFS/HoH/QSS in result.json meta.filing_status, or in "
            "taxpayer_profile.json if no result.json has been computed yet")
    if status not in _CANONICAL_STATUSES:
        # Report the expected domain and value type only — never the raw
        # content. A malformed profile/result can carry a taxpayer name or
        # other misplaced sensitive value here, and redact() only masks
        # credential/SSN shapes, not names or arbitrary financial data
        # (AGENTS.md's no-sensitive-values-in-output rule).
        raise ProjectionError(
            f"filing_status must be one of Single/MFJ/MFS/HoH/QSS for the "
            f"{where}, got an unrecognized value ({len(status)} chars)")
    return status


# Total over `_CANONICAL_STATUSES`, which is what lets `_full_status_key` drop
# the `.get(fs, "single")` default that was the silent fallback: once the value
# is checked, every status HAS a key and there is nothing left to fall back to.
_FULL_STATUS_KEYS = {"MFJ": "mfj", "QSS": "mfj", "MFS": "mfs",
                     "HOH": "hoh", "SINGLE": "single"}


def _full_status_key(filing_status: "str | None",
                     where: str = "tax computation") -> str:
    """The `filing_statuses` key for a VALUE-CHECKED status, or ProjectionError.

    Validating here rather than at the callers is the whole point. The check
    first landed at the two projection/withholding call sites, which left the
    understating bug live everywhere else: `scenario.py`, `roth_ladder.py` and
    `loss_harvest.py` all read `filing_status` straight off result.json meta
    (falling back to the profile) and hand it to `_rate_kwargs`, which calls
    this. A profile spelling the status out — "Married Filing Jointly" — came
    back "single", so /tax-scenario, /tax-roth-convert and /tax-harvest priced
    a conversion or a harvest on single brackets and the single standard
    deduction while /tax-project and /tax-withholding refused the same file.
    Five callers, two of them guarded, is not a guard.

    `where` is only the message label; it defaults because most callers have
    no better name for the boundary than the one the CLI already prints.
    """
    return _FULL_STATUS_KEYS[_canonical_status(filing_status, where)]


def _binary_status_key(filing_status: "str | None",
                       where: str = "tax computation") -> str:
    """`mfj` or `single` for a VALUE-CHECKED status — see `_full_status_key`.

    The single/mfj-keyed maps (CTC phase-out, IRMAA
    tiers) collapse MFS and HoH onto `single` BY DESIGN, which is why the
    silent fallback hid here so well: the wrong answer is spelled the same as
    the right one for two of the five statuses.
    """
    return "mfj" if _canonical_status(filing_status, where) in _MFJ_LIKE else "single"


def capital_gains_limits(tax_law: dict, filing_status: "str | None",
                         where: str = "tax computation") -> "tuple[float | None, float | None]":
    """`(zero_pct_limit, fifteen_pct_limit)` for the status — the ONE read site.

    Four sites read `federal.capital_gains_rates` with their own `.get` chains
    (`scenario._rate_kwargs`, this module's worksheet branch, and through the
    first every planning skill), which is the root cause X1 and X2 of the
    09-05 review share: the status collapse (MFS and HoH onto `single`) and
    the missing-limit fall-through each had to be fixed in every copy. They
    read here now, so the status-specific keys X1 adds land in one place. An
    absent block or key reads as `None`; whether `None` is acceptable is the
    caller's question, answered by `tax_computations.qdcg_worksheet_applies`
    once it knows if there is preferential income to price. A breakpoint that
    is PRESENT but unusable is refused here, unconditionally: a wages-only
    run against a corrupt cache fails closed, where a merely unfetched one
    still runs, because a corrupt figure says the cache was not produced by
    the fetch and nothing else in it can be trusted either.

    The key is the FULL status key (X1): §1(h) gives head of household its own
    0%/15% breakpoints and married filing separately half of joint, so the
    old `_binary_status_key` collapse onto `single` under-stated HoH's 0% band
    and over-stated MFS's 15% band. QSS reads `mfj`. A legacy cache keyed
    single/mfj only reads `None` for HoH and MFS — never the single figure —
    and `tax_computations.qdcg_worksheet_applies` refuses to price
    preferential income on it, naming /tax-setup.
    """
    key = _full_status_key(filing_status, where)
    cg = (tax_law.get("federal", {}) or {}).get("capital_gains_rates", {}) or {}

    def _limit(name, per_status):
        if per_status is not None and not isinstance(per_status, dict):
            raise ProjectionError(
                f"federal.capital_gains_rates.{name} must be a per-status map "
                f"(single/mfj/mfs/hoh). Run /tax-setup to fetch current rates.")
        value = (per_status or {}).get(key)
        # `None` is "not fetched", and is the guard's question. Anything else
        # must be a positive finite number HERE, at the read site, because
        # the worksheet's `min(qdcg, limit - ordinary)` resolves against NaN
        # by keeping qdcg (every NaN comparison is False), which taxes the
        # whole preferential stack at 0% — the under-statement door the guard
        # cannot see, since `nan is None` is False. Zero or a negative pushes
        # the stack out of the 0% band instead: the over-statement door.
        # `load_tax_law` is a bare `json.loads`, which admits NaN/Infinity;
        # the positivity and ordering rules are this site's own either way.
        if value is not None and (_indexed_limit_bad(value) or value <= 0):
            raise ProjectionError(
                f"federal.capital_gains_rates.{name}.{key} must be a "
                f"positive finite number. Run /tax-setup to fetch current rates.")
        return value

    zero = _limit("zero_pct_limit", cg.get("zero_pct_limit"))
    fifteen = _limit("fifteen_pct_limit", cg.get("fifteen_pct_limit"))
    if zero is not None and fifteen is not None and fifteen < zero:
        raise ProjectionError(
            f"federal.capital_gains_rates fifteen_pct_limit.{key} is below "
            f"zero_pct_limit.{key}. Run /tax-setup to fetch current rates.")
    return zero, fifteen


_DEGRADED_MODE_LINE = (
    "- **Degraded mode:** no completed covered-year Form 2210 line-8 "
    "pre-factor prior-tax base was supplied. The prior-year prong is "
    "unresolved, so this report shows the 90%-of-current-year test "
    "only. Do not substitute raw Form 1040 line 24; complete the "
    "covered-year line-8 composition and enter "
    "`prior_year_form_2210_tax_base` to enable that prong.")


def _prior_baseline_lines(baseline_source: "str | None", year: int,
                          ca_prior_resolved: bool, degraded: bool) -> list:
    """The "Prior-year baseline" bullet and its degraded-mode companion.

    ONE copy, shared by `projection.render_markdown` and
    `withholding.render_markdown`. Both used to spell out the same two
    sentences and the same `archive/{year-1}/` interpolation independently,
    which is the drift `_PRIOR_AGI_THRESHOLD_TEXT` and `_DeMinimisFloor` were
    introduced to prevent one round earlier — and a correction here has to
    land on both readers or the two reports disagree about the same archive.

    `ca_prior_resolved` is passed in rather than derived: the two reports
    reach the CA harbor by different attribute paths, and that difference is
    genuinely theirs. Everything below this line is not.

    The clarifier is gated on the CA prong having ACTUALLY resolved, not on an
    archive merely existing: "no FEDERAL line-8 base" is not "no archive", but
    a federal-only return renders no CA section at all, and a CA one whose
    prior figures came back empty prints its own "no usable prior-year CA
    baseline" below. Saying the archive supplies a harbor in either case
    points at a figure the report does not carry.
    """
    clarifier = ""
    if baseline_source is None and ca_prior_resolved:
        clarifier = (" (federal only; an `archive/{}/` prior return IS present "
                     "and still supplies the CA prior-year harbor below)"
                     .format(year - 1))
    lines = [f"- Prior-year baseline: "
             f"{baseline_source or 'none — DEGRADED MODE'}{clarifier}"]
    if degraded:
        lines.append(_DEGRADED_MODE_LINE)
    return lines


_CA_ES_HEADING = "### CA 540-ES voucher schedule"

# The heading does NOT carry the statutory split. It used to read
# "(30% / 40% / 0% / 30%)" in BOTH renderers, above amounts that follow that
# weighting only when all four instalments are open and no estimate has been
# paid. A heading cannot qualify itself, so the weighting moved into prose
# that can — and into ONE copy, because fixing this in projection.py alone
# left withholding.py asserting the contradicting form, with each module's
# tests pinning the opposite contract for the same string.
_CA_ES_WEIGHTING = (
    "California installments are NOT four equal quarters: the statutory "
    "weighting is 30% / 40% / 0% / 30%, so there is no September (Q3) "
    "payment (R&TC §19136.1; FTB Form 540-ES).")

_CA_ES_SHARE_COLUMN_CAVEAT = (
    " The Share column reports what THIS schedule allocates, which equals the "
    "statutory weighting only while all four installments are open and no "
    "estimate has already been paid.")

_CA_SPLIT_CLOSED = (
    "The instalments above are not in the statutory 30% / 40% / 0% / 30% "
    "proportions: earlier installments have closed, and the remaining "
    "shortfall is reallocated across the open installments in proportion to "
    "their statutory weights.")

_CA_SPLIT_EARLY_ESTIMATE = (
    "The instalments above are not in the statutory 30% / 40% / 0% / 30% "
    "proportions: estimates already paid are credited against the cumulative "
    "30% / 70% / 70% / 100% checkpoints R&TC §19136.1 actually tests, so an "
    "installment already covered by those payments schedules $0 and the "
    "balance falls on the first checkpoint that is not yet met. Paying the "
    "amounts as listed meets every checkpoint.")

_CA_SPLIT_ROUNDING = (
    "The instalments above differ from the statutory 30% / 40% / 0% / 30% "
    "proportions only by cents rounding: the instalments are whole cents and "
    "the shortfall is too small to split in those proportions exactly. The "
    "amounts, not the proportions, are what to pay.")


def _ca_split_diverges(vouchers: list) -> bool:
    """Whether `vouchers` depart from the statutory 30/40/0/30 weighting.

    Measured off the AMOUNTS, not inferred from which path produced them. The
    first version of this gate asked `len(vouchers) < len(CA_ES_SCHEDULE)`,
    which sees the closed-instalment cause and is blind to the other two.
    An all-zero column (the de minimis path) has no split to describe.
    """
    total = round(sum(v.amount for v in vouchers), 2)
    if total <= 0:
        return False
    statutory = {q: w for q, _, w in CA_ES_SCHEDULE}
    return any(share != round(100 * statutory.get(v.quarter, 0.0))
               for v, share in zip(vouchers, _percent_shares(
                   [v.amount for v in vouchers])))


def _ca_split_note(vouchers: list, estimates_paid: float) -> list:
    """The sentence explaining a non-statutory CA instalment split, or [].

    Three causes, one each, and never a cause that did not apply: the
    estimates-already-paid explanation was once printed unconditionally, which
    told a taxpayer who had paid none that their payments caused the split. A
    caveat false of its reader is one they learn to skip.
    """
    if not _ca_split_diverges(vouchers):
        return []
    if len(vouchers) < len(CA_ES_SCHEDULE):
        return [_CA_SPLIT_CLOSED, ""]
    if estimates_paid > 0:
        return [_CA_SPLIT_EARLY_ESTIMATE, ""]
    return [_CA_SPLIT_ROUNDING, ""]


def _state_bracket_estimate_note(state, ca_safe_harbor) -> "str | None":
    """The qualification a non-CA state figure has to carry, or None for CA.

    One copy for the payload field and the markdown banner. Written out of the
    same discriminator the Caveats section uses — a CA run has a modeled 540
    harbor and every other taxed state does not — rather than by re-testing
    `state.code != "CA"`, so the two cannot disagree about which states are
    modeled.
    """
    if state is None or ca_safe_harbor is not None:
        return None
    # Plain prose, no markdown: this is a PAYLOAD field first and the
    # renderer's banner second, and a `**` embedded here would reach an agent
    # reading `--json` as literal asterisks.
    return (f"{state.code} is a bracket ESTIMATE — this state's return is not "
            "modeled in depth, only California is. The figures apply the "
            "state's brackets to a federally-derived taxable income and model "
            "no state-specific credit, addition or subtraction, so treat the "
            "balance as an order of magnitude rather than an amount owed.")


def _percent_shares(amounts: list) -> list:
    """Whole-percent shares of `amounts` that sum to exactly 100.

    Rounding each share on its own lets a four-row column print 24 / 44 / 0 /
    33 under a heading reading "Share of remaining" — 101%. Largest-remainder
    hands the leftover points to the rows that lost the most to truncation, so
    every row stays within one point of its true share and the column sums to
    100 by construction rather than by luck. An all-zero column (the de
    minimis path, where every voucher is $0.00) has no shares to apportion and
    prints 0% throughout.
    """
    total = round(sum(amounts), 2)
    if total <= 0:
        return [0] * len(amounts)
    exact = [100.0 * a / total for a in amounts]
    shares = [int(x) for x in exact]
    by_remainder = sorted(range(len(exact)),
                          key=lambda i: exact[i] - shares[i], reverse=True)
    for i in by_remainder[:100 - sum(shares)]:
        shares[i] += 1
    return shares


def _capital_loss_limit(filing_status: "str | None",
                        where: str = "capital-loss limit") -> float:
    """§1211(b)'s $3,000 / $1,500-MFS cap for a VALUE-CHECKED status.

    Four call sites tested `(filing_status or "").upper() == "MFS"` inline —
    the same unguarded comparison as the old key helpers, and wrong in the
    same direction: a spelled-out "Married Filing Separately" takes the
    $3,000 branch and deducts twice the cap the statute allows.
    """
    return (CAPITAL_LOSS_LIMIT_MFS
            if _canonical_status(filing_status, where) == "MFS"
            else CAPITAL_LOSS_LIMIT)


# The full federal.schedule_1a schema (tax_law_fetcher.create_empty_tax_law):
# three scalar caps + four single/mfj-keyed maps. A raw projection override can
# only be validated when EVERY one is populated — so the completeness check
# requires the whole set, not just whichever keys happen to be present.
_SCH1A_SCALAR_LIMITS = ("tips_cap", "car_loan_cap", "senior_amount_per_person")
_SCH1A_NESTED_LIMITS = ("overtime_cap", "tips_overtime_phaseout_start",
                        "car_loan_phaseout_start", "senior_phaseout_start")


def _indexed_limit_bad(v) -> bool:
    """A usable indexed cap/threshold must be a FINITE, NON-NEGATIVE real number
    (bool excluded). The compute path rejects string/NaN/Infinity/negative via
    _sched_a_amount, so the projection completeness check must reject them too —
    otherwise a corrupt cache like {"tips_cap": "25000"} or a negative figure
    would pass and let a raw override apply without the unfetched warning."""
    return (isinstance(v, bool) or not isinstance(v, (int, float))
            or not math.isfinite(v) or v < 0)


def _sch1a_limits_incomplete(block) -> bool:
    """The fetched federal.schedule_1a block is USABLE for validating a raw
    override only when EVERY indexed cap/threshold is populated. create_empty_
    tax_law seeds the whole block all-None, and inspecting only present keys
    would wave through a scaffold OR a partial cache (e.g. {"tips_cap": 25000}
    missing the others) — so validate the FULL required schema explicitly: each
    scalar cap present + non-None, and each nested map carrying BOTH single and
    mfj non-None. Any gap → incomplete (fail closed; a raw override applied
    against a partial cache would be uncapped/unphased). Codex rounds 14-16."""
    if not isinstance(block, dict):
        return True
    for key in _SCH1A_SCALAR_LIMITS:
        if _indexed_limit_bad(block.get(key)):
            return True
    for key in _SCH1A_NESTED_LIMITS:
        nested = block.get(key)
        if not isinstance(nested, dict) or any(
                _indexed_limit_bad(nested.get(k)) for k in ("single", "mfj")):
            return True
    return False


def _num(value, field: str = "") -> float:
    """Coerce to float; None->0.0; reject bool, non-finite, and unparseable strings.

    Non-finite rejection is load-bearing: json.loads accepts NaN/Infinity, and
    NaN silently defeats every downstream comparison (all NaN comparisons are
    False — e.g. a NaN ca_agi would disable the R&TC §19136.3 high-income
    check, the understating direction).
    """
    if value is None:
        return 0.0
    try:
        n = _coerce_numeric(value, default=None)
    except TypeError:
        raise ProjectionError(f"{field or 'value'} must be numeric, got bool")
    if n is None:
        # The VALUE is never echoed. A misplaced taxpayer name, email or
        # address lands in a numeric field often enough to matter, both CLIs
        # print `redact(e)`, and redact() masks SSN and credential SHAPES —
        # not names, not email addresses. So the message names the field and
        # the expected type and stops, which is the rule `_canonical_status`
        # already follows for the same reason (AGENTS.md §1: never log, echo
        # or display sensitive values). The type is safe to name and is the
        # half that actually helps: "got str" says the quotes are the problem.
        raise ProjectionError(
            f"{field or 'value'} must be numeric, got "
            f"{type(value).__name__} ({len(str(value))} chars)")
    if not math.isfinite(n):
        raise ProjectionError(f"{field or 'value'} must be finite, got {n!r}")
    return n


def _nonneg_num(value, field: str = "") -> float:
    """_num, but HARD-ERROR on a negative value instead of letting a downstream
    max(0, …) silently floor it to $0. It began as the Schedule 1-A override's
    check — a negative deduction is invalid input (the compute/scenario paths
    reject it), and silently coercing to $0 would drop the deduction and
    overstate the projected tax + safe-harbor target with no warning — and it
    now also guards every YTD figure `annualize` reads and both estimates-paid
    fields, in this module and in `withholding`, which is what put a
    taxpayer's payroll numbers behind it.

    The VALUE is not echoed. Not because a dollar figure is secret from this
    terminal — the report this refusal precedes prints wages and withholding
    to the same stdout — but because an ERROR LINE is the piece of a run that
    gets pasted into a bug report or an issue, which AGENTS.md §1 forbids
    carrying taxpayer data, and a whole projection is not. Nothing diagnostic
    is lost: `n < 0` has exactly one shape, so naming the field says the whole
    of it, and the sign the message would print is already in the file the
    reader is about to open. `_num`'s non-numeric branch drops the value for a
    stronger reason — there it can be a name or an email — and this is the
    same rule reaching the branch where the value is provably a float.
    """
    n = _num(value, field)
    if n < 0:
        raise ProjectionError(f"{field or 'value'} must be non-negative")
    return n


def _confirmed_flag(value, field: str) -> bool:
    """Strict JSON-boolean coercion for a confirmation gate.

    `bool(value)` is the wrong tool here: JSON accepts a hand-typed string
    where a boolean belongs, and Python's `bool("false")` is `True` — any
    non-empty string is truthy. A confirmation gate silently satisfied by
    that typo is the OVER-accepting direction AGENTS.md makes a hard error,
    not a value to guess at. Absent (None) reads as not confirmed; anything
    present that is not literally `true`/`false` is refused rather than
    coerced.
    """
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ProjectionError(
            f"{field} must be true or false (a JSON boolean), got "
            f"{type(value).__name__}")
    return value


# The prior-year fields whose meaning is fixed by the COVERED year. Each is
# composed under one particular year's Form 2210 line-8 instructions, or
# confirms a condition about one particular preceding year, so none of them
# survives a change of `--year` intact.
_YEAR_BOUND_PRIOR_FIELDS = (
    "prior_year_form_2210_tax_base",
    "prior_year_form_2210_agi",
    "prior_year_zero_liability_confirmed",
    "prior_year_return_filed_12_months_confirmed",
)


def _year_bound_prior_fields_supplied(inputs: dict) -> "list[str]":
    """Which year-bound prior-year fields this file actually ANSWERS.

    Presence of the KEY is not the test, because the template ships all four:
    the two figures as `null` and the two flags as `false`. Keying on presence
    would refuse every file written from the template, which is the gate that
    cries wolf AGENTS.md's bias rules and this repo's review history both warn
    about. A supplied answer is a non-null figure or a flag set to something
    other than false — which is `true`, or the malformed value `_confirmed_flag`
    refuses on its own a few lines later.
    """
    return [f for f in _YEAR_BOUND_PRIOR_FIELDS
            if inputs.get(f) not in (None, False)]


def _require_inputs_tax_year(inputs: dict, year: int) -> None:
    """Refuse a projection_inputs.json that is not FOR the year being run.

    Shared by both CLIs — /tax-project and /tax-withholding read the same file
    and the same four prior-year fields, so a guard living in one of them is a
    guard with a hole in it.

    Three failures, not one:

    - A MALFORMED `tax_year` — anything but a plain integer year — is refused
      naming only its shape, never its content.
    - A DECLARED mismatch (`tax_year` 2025, `--year` 2026) is always refused.
    - An ABSENT `tax_year` is refused only once one of the year-bound
      prior-year fields is answered. Those figures are composed under the
      COVERED year's own Form 2210 line-8 instructions, so re-running last
      year's file against this year does not merely mis-label the output: it
      feeds the wrong return's base to the prior-year prong and reports a
      required annual payment built from a return that is not the preceding
      one. There is no marker in the file to catch that after the fact, and
      the number is the one the taxpayer pays on.

    An absent `tax_year` with none of them answered still degrades: the field
    is nullable in the template, every file predating these fields leaves it
    so, and nothing else in the file is bound to a particular year. Refusing
    that case would break working files to no end.
    """
    declared = inputs.get("tax_year")
    if declared is not None:
        # TYPE before comparison, and the refusal names the SHAPE rather than
        # the value. The file is hand-edited, so a misplaced name, address or
        # whole nested object lands in this field as readily as a wrong year
        # does, and both CLIs print this message — `redact` masks SSN and
        # credential shapes only, never a name (AGENTS.md #1).
        #
        # A non-int was already refused, but by the comparison below, which
        # made nonsense of it: the string "2026" is `!= 2026`, so the file was
        # turned away as "for TY2026, not TY2026". One spelling of a year,
        # checked once — the same rule `tax_calendar._validate_tax_year` and
        # the Schedule 1-A cache-year gate already apply, and `2026.0` is
        # refused with them rather than made a fourth accepted spelling.
        if isinstance(declared, bool) or not isinstance(declared, int):
            raise ProjectionError(
                "projection_inputs.json tax_year must be the four-digit "
                "covered year, written as a number — got "
                f"{year_or_shape(declared)}. Set \"tax_year\": {year} if "
                f"this is the TY{year} file.")
        if declared != year:
            # `year_or_shape` again, not `TY{declared}`. The type check above
            # proves this is an int, and int-ness was mistaken for year-ness
            # once already: an account or policy number hand-edited into the
            # field is an int, passes that check, and lands HERE, where it was
            # printed in full. `redact` masks the nine-digit tail of that and
            # nothing shorter.
            raise ProjectionError(
                f"projection_inputs.json is for {year_or_shape(declared)}, "
                f"not TY{year}."
            )
        return
    supplied = _year_bound_prior_fields_supplied(inputs)
    if supplied:
        raise ProjectionError(
            "projection_inputs.json answers " + ", ".join(supplied) +
            f" but declares no tax_year, so it cannot be shown to be the "
            f"TY{year} file. Those fields are composed under the covered "
            f"year's own Form 2210 line-8 instructions and are wrong for any "
            f"other year. Set \"tax_year\": {year} in the file if it is this "
            f"year's, or re-derive the prior-year figures for TY{year}."
        )


@dataclass(frozen=True)
class ProjectedInputs:
    wages: float
    federal_withholding: float
    state_withholding: float
    medicare_wages: float
    medicare_withheld: float
    ss_wages: float
    capital_gains: float  # legacy single figure — treated as LONG-term
    qualified_dividends: float
    ordinary_dividends: float
    taxable_interest: float
    self_employment_net: float
    other_income: float
    estimated_fed_paid: float
    estimated_state_paid: float
    elapsed_fraction: float
    paychecks_remaining: int
    # ST/LT split (preferred over the legacy combined field): short-term gains
    # are ORDINARY-rate income — routing them through the preferential QDCG
    # buckets understates the projection and the safe-harbor target.
    capital_gains_short: float = 0.0
    capital_gains_long: float = 0.0
    # OBBBA Schedule 1-A total (tips + overtime premium + car-loan + senior),
    # a BELOW-AGI deduction (line 13b). Reduces taxable income and the QBI cap
    # base, NEVER AGI — so taxable SS and NIIT (MAGI-based) are unaffected.
    schedule_1a_total: float = 0.0
    # The portion of schedule_1a_total that is SELF-EMPLOYED §224 qualified
    # tips (from the projected Schedule C business). Form 8995 excludes deducted
    # tip income from QBI, so this is subtracted from the QBI base as well as
    # feeding the line-13b total — without it the same SE tips get both the
    # line-13b deduction and 20% QBI. Must not exceed self_employment_net.
    schedule_1a_se_tips: float = 0.0


@dataclass(frozen=True)
class FederalProjection:
    gross_income: float
    adjustments: float
    agi: float
    taxable_income: float
    tax_before_credits: float
    ctc: float
    se_tax: float
    additional_medicare: float
    total_tax: float
    withholding: float
    estimates_paid: float
    balance: float  # >0 owed, <0 refund
    used_qdcg: bool
    niit: float = 0.0
    qbi_over_threshold: bool = False
    schedule_1a_unfetched: bool = False


@dataclass(frozen=True)
class StateProjection:
    code: str
    taxable_income: float
    tax: float
    withholding: float
    estimates_paid: float
    balance: float
    # Projected state AGI (income before the standard deduction; state
    # adjustments are not modeled in v1). Feeds the R&TC §19136.3
    # high-income test, which is on CURRENT-year CA AGI.
    agi: float = 0.0


@dataclass(frozen=True)
class SafeHarbor:
    pct90_current: float
    prior_tax: float | None
    prior_factor: float | None
    prior_based: float | None
    required_annual: float
    withholding: float
    estimates_paid: float
    shortfall: float
    covered: bool
    degraded: bool
    # Prior tax present but prior AGI unknown → 100% factor assumed with a
    # loud understatement note (mirrors compute_form_2210's handling).
    prior_agi_missing: bool = False
    # §6654(e)(1): current tax minus WITHHOLDING (estimates do not count
    # toward this test) under $1,000 → no federal underpayment penalty can
    # arise regardless of the shortfall shown. The CaSafeHarbor analogue
    # below shipped with its de minimis; this side went without one, so the
    # federal report demanded catch-up payments for a penalty `compute_
    # form_2210` reports as impossible on the very same figures.
    de_minimis: bool = False
    # How far UNDER the floor the measured balance sits, 0.0 when the floor
    # does not apply. The floor is a cliff evaluated against an ANNUALIZED
    # projection, so "no penalty applies" is only as good as the projection:
    # this is the margin that has to be wrong for the excuse to evaporate.
    # Carried as the continuous quantity rather than a "near the cliff" flag —
    # any threshold for near would be invented, and the repo's rule is to
    # model the continuous version where a statute has a discontinuity.
    de_minimis_margin: float = 0.0
    # The gap the harbor asked for BEFORE the floor suppressed it. `shortfall`
    # is the suppressed one, so without this the payload keeps no record of
    # what was suppressed and the table prints a Required/Paid pair that does
    # not reconcile with its own Shortfall column.
    shortfall_before_de_minimis: float = 0.0
    # What the harbor is short once the floor LAPSES, ONE FIELD PER PATH, and
    # 0.0 is the answer that matters on each: nothing attaches that way,
    # because the required annual payment is met on its own. The status cell
    # asserted the opposite in every de-minimis case — $5,000 of tax against
    # $4,500 withheld and a $4,000 prior-year prong printed "$500 more tax and
    # it attaches", while at $5,500 the prong still binds against $4,500 paid
    # and nothing attaches at all. Over-stating what the taxpayer must pay is
    # the hard error whether it arrives as a total or, as here, as a warning.
    #
    # They were one field — the worse of the two — and that is the same error
    # a level down: /tax-withholding's tax does not move, so a positive coming
    # from the MORE-TAX path was printed there as a warning about withholding
    # falling, on $0 of withholding that cannot fall. A renderer reads the path
    # its own inputs can take; see `_de_minimis_cliff_paths`.
    de_minimis_cliff_more_tax: float = 0.0
    de_minimis_cliff_less_withholding: float = 0.0
    # The current-year line-4 limitation, true only
    # where an un-netted figure is actually driving a payment: a positive
    # shortfall with the floor not applying. §6654 measures Form 2210 line 4 —
    # line 24 MINUS the part-IV refundable credits — and line 4 is what feeds
    # both the 90% prong and the §6654(e)(1) floor. What arrives here is line
    # 24, which sits above those credits, so the required payment is too high
    # and the floor is applied too rarely: $5,000 of tax, $1,000 of refundable
    # AOTC and $3,500 withheld is a true $500 balance the floor excuses, shown
    # here as $1,500 with a payment to make.
    #
    # Flagged rather than derived because
    # neither caller HAS the credits. `project_federal_tax` applies the CTC as
    # a nonrefundable credit only and models no EIC or ACTC, and result.json
    # carries a field for the refundable AOTC and for the Schedule 3 Part II
    # aggregate — which mixes real refundable credits with an extension PAYMENT
    # and with excess social security, a §31 credit belonging to the OTHER side
    # of the floor's subtraction. Netting what is visible would leave the
    # disclosure needed anyway while making the figure look derived.
    current_tax_unnetted: bool = False
    # Which instalment calendar this harbor's figures belong to. Read ONLY by
    # `_voucher_catchup_note`, to refuse a note bound to the other
    # jurisdiction's weights — see `_CatchupNote`. A plain label rather than an
    # isinstance check because that gate is duck-typed on purpose.
    jurisdiction: str = "federal"


@dataclass(frozen=True)
class CaSafeHarbor:
    """CA estimated-tax safe harbor (FTB Form 5805).

    Mirrors the federal SafeHarbor structure — CA conforms to the
    lesser-of(90% current, 100/110% prior) test including the $150K/$75K-MFS
    prior-AGI threshold for the 110% factor — with one CA-specific rule:
    current-year CA AGI >= $1,000,000 ($500,000 MFS) forbids the prior-year
    exception entirely (R&TC §19136.3). `high_income_90_only` flags that path
    so the renderer can explain why the prior-year row is absent.
    """
    pct90_current: float
    prior_tax: "float | None"
    prior_factor: "float | None"
    prior_based: "float | None"
    required_annual: float
    withholding: float
    estimates_paid: float
    shortfall: float
    covered: bool
    degraded: bool
    high_income_90_only: bool
    # The §19136.3 threshold applied (by filing status) — lets renderers warn
    # when a PROXY current-year AGI sits near the cliff.
    high_income_threshold: float = 0.0
    # Prior tax present but prior AGI unknown → the 110% factor cannot be
    # tested; 100% assumed with a loud understatement note (the Form 2210
    # precedent for the same gap).
    prior_agi_missing: bool = False
    # R&TC §19136: current tax minus withholding under $500/$250-MFS → no CA
    # underpayment penalty can arise regardless of the shortfall shown.
    de_minimis: bool = False
    # The PRIOR-year prong of the same subdivision, proved by a Form 540 total
    # tax already under the floor — and ABOVE $0, the $0 case being carried by
    # its own note pair in `_ca_notes` rather than by this flag. Acted on at
    # every AGI: see `prior_year_floor_applied` for why §19136.3 does not
    # reach it, and for the round in which this engine thought it might.
    prior_year_floor: bool = False
    # The UNKNOWN sibling of the flag above, and of `_CA_EITHER_YEAR_FLOOR_NOTE`
    # below it: both of those read a KNOWN prior-year Form 540 total, and an
    # ABSENT one fell between them into silence — beside a live 540-ES
    # schedule, and beside an all-zero one whose estimates arrived late, where
    # the requirement is met and an instalment penalty has accrued anyway.
    # Both states, one flag: `_ca_notes` picks the note by `shortfall`, since
    # only one of the two can honestly say "the schedule below". Deliberately
    # NOT folded into `degraded`; the note constants name the rule and
    # `compute_ca_safe_harbor` says why the two must stay apart at the
    # §19136.3 limitation.
    prior_year_floor_unknown: bool = False
    # Distance under the CA floor — see SafeHarbor.de_minimis_margin.
    de_minimis_margin: float = 0.0
    # CA reads `ca_total_tax` as filed. The CA note stays live and
    # deliberately narrower because which credits FTB 5805 nets is not
    # something this engine has verified.
    #
    # There is deliberately NO federal twin of this field. `SafeHarbor`
    # carried one, always False and read by nothing, once the federal prong
    # began requiring an explicit completed Form 2210 line-8 composition —
    # one name meaning "live per-run flag" on this class and "compile-time
    # constant" on its sibling. A `--json` consumer writing one handler for
    # `*.prior_tax_unnetted` read that permanent false as "the federal prior
    # base IS netted", a claim the engine never makes. Do not re-add it: the
    # federal answer is that the question cannot arise.
    prior_tax_unnetted: bool = False
    # See SafeHarbor for all of these — same rule, same floor arithmetic, R&TC
    # §19136 in place of §6654(e)(1) and §6654(f). The CA note is deliberately
    # narrower for the same reason: which credits FTB 5805
    # nets is not a list this engine has verified.
    shortfall_before_de_minimis: float = 0.0
    de_minimis_cliff_more_tax: float = 0.0
    de_minimis_cliff_less_withholding: float = 0.0
    current_tax_unnetted: bool = False
    # See SafeHarbor.jurisdiction. "CA" here is what makes pairing this object
    # with the FEDERAL catch-up note a refusal instead of a wrong verdict.
    jurisdiction: str = "CA"

    @property
    def prior_year_floor_applied(self) -> bool:
        """The prior-year floor is not merely PRESENT but carrying the year —
        and it carries it at EVERY AGI, §19136.3 included.

        §19136.3 is two subdivisions and its whole operative text modifies IRC
        §6654(d)(1)(B) so that "clause (ii) shall not apply" above $1,000,000
        ($500,000 MFS). Clause (ii) is the prior-year component of the REQUIRED
        ANNUAL PAYMENT. R&TC §19136(c)(2) is not part of that computation at
        all — it is a bar on IMPOSING the addition to tax ("an addition to tax
        shall not be imposed under this section if...") — and §19136.3 does not
        mention it. FTB 5805's own General Information keeps them in that
        order: the floor sentence is unconditional ("you are not subject to the
        underpayment of estimated tax penalty"), while the $1,000,000 sentence
        is the one carrying the proviso — those taxpayers "must use the tax on
        their ... tax return to figure their penalty IF THEY ARE SUBJECT to the
        underpayment of estimated tax penalty". So the limitation decides
        `required_annual`, which stays at 90% of the current year, and this
        decides whether falling short of it costs anything.

        This read `and not self.high_income_90_only` for one round, on the
        worry that §19136.3 might reach (c)(2) after all — while the note
        printed beside it in the taxpayer's own report asserted the opposite in
        so many words ("a separate rule that §19136.3 does not reach ... the
        penalty may not apply at all"). A 540-ES schedule and that sentence
        cannot both be right, and the pair erred by collecting money the state
        cannot penalise not having.

        `or self.prior_tax == 0` because a $0 prior year proves the same prong
        by the same bound, and is only split off `prior_year_floor` so that the
        $0 note pair in `_ca_notes` is not printed twice over. Below the
        limitation it needed nothing here — the prong already zeroes
        `required_annual` — and at or above it, it needed exactly this. A
        negative prior tax is nonsense input and reaches neither term, which
        leaves it in the OVER-stating direction where nonsense belongs.

        Every consumer asking "can a penalty still arise at all?" wants THIS
        one, and spelling its ingredients out again at each such site is
        exactly how they got out of step: the flag shipped folded into
        `no_penalty` in `compute_ca_safe_harbor`, while the de-minimis cliff
        and the voucher timing note went on testing `de_minimis` alone and kept
        warning a taxpayer the subdivision had already cleared.

        A PROPERTY, not a second stored bool. Both reports build their `--json`
        harbor payload with `vars()`, which sees fields and not properties, so
        this cannot reach a consumer as a name whose meaning depends on which
        class it was read off — `prior_tax_unnetted` above is the note left by
        the run where that happened. There is no federal twin for the same
        reason: §6654 has no floor on the PRIOR year at all, only §6654(e)(2)'s
        zero, which this engine already expresses as a $0 required annual
        payment and which therefore needs nothing to survive past the
        current-year floor.
        """
        return self.prior_year_floor or self.prior_tax == 0


@dataclass(frozen=True)
class W4Adjustment:
    shortfall: float
    paychecks_remaining: int
    per_paycheck: float


@dataclass(frozen=True)
class Voucher:
    quarter: int
    due_date: str
    amount: float


@dataclass(frozen=True)
class ProjectionReport:
    year: int
    as_of: str
    pay_frequency: str
    baseline_source: str | None
    degraded: bool
    proj: ProjectedInputs
    federal: FederalProjection
    state: "StateProjection | None"
    safe_harbor: SafeHarbor
    vouchers: "list[Voucher]"
    w4_penalty: W4Adjustment
    w4_zero: W4Adjustment
    # Present only when the state projection ran AND the state is CA — the
    # only state whose estimated-payment weighting (30/40/0/30) is modeled.
    ca_safe_harbor: "CaSafeHarbor | None" = None
    ca_vouchers: "list[Voucher]" = field(default_factory=list)
    # The voucher catch-up notes, decided in build_report so the markdown and
    # the `--json` payload cannot disagree about them. They were computed
    # inside the renderer, which put them in the one output both consuming
    # skills read SECOND — /tax-project step 5 takes `--json` and step 6
    # re-runs for markdown, so an agent summarising the payload handed over
    # the under-funded earliest voucher with nothing marking it. Every other
    # renderer-only warning here (`nonresident_income_warning`,
    # `state_unsupported_note`) is a field for the same reason. None = the
    # note is not true of that schedule; see `_voucher_catchup_note`.
    voucher_catchup_note: "str | None" = None
    ca_voucher_catchup_note: "str | None" = None
    # The CALENDAR notes for the same two schedules — whether the dates beside
    # the amounts are still ahead. Fields for the reason above, and separate
    # from the catch-up pair because the two are independently true: a schedule
    # can be correctly split and entirely obsolete. See `_voucher_dates_note`.
    # None both while every printed date is still open and when the report was
    # built without a `today` to compare against.
    voucher_dates_note: "str | None" = None
    ca_voucher_dates_note: "str | None" = None
    # Which lever the covered year still has, as the lead of "Recommended
    # actions". Always set by `build_report`; see `_w4_lever_note`.
    w4_lever_note: "str | None" = None
    # True only when the fetched state block POSITIVELY declares no income
    # tax (state.type == "none" — FL, TX, ...). Lets the renderer distinguish
    # "this state has no income tax" from "no baseline / brackets not
    # fetched" when `state` is None (mirrors loss_harvest.state_no_income_tax
    # — same bias-direction rule: absence of data must never read as
    # absence of tax, so an unfetched/unknown-type cache stays False).
    state_no_income_tax: bool = False
    # 2-letter state code to name in the no-income-tax skip message (from
    # tax_law.state.code, falling back to the taxpayer's resident state).
    # Only meaningful when state_no_income_tax is True.
    no_income_tax_state_code: "str | None" = None
    # Set when tax_law.state.code (normalized) and the taxpayer's resident
    # state are BOTH known and DIFFERENT — a stale cache for the wrong
    # state (round-5 P1). None when there's nothing to warn about (codes
    # match, or one/both sides are unknown).
    state_cache_mismatch: "str | None" = None
    # Codex round-8 finding F: set whenever profile flags.
    # has_nonresident_source_income is True, REGARDLESS of which state-
    # projection path ran (FL no-tax / CA graduated / mismatch / skipped) —
    # a resident-state projection (or a resident no-tax note) says nothing
    # about a SEPARATE state's nonresident liability on out-of-state W-2/
    # K-1/rental income. This module is read-only/advisory, so unlike the
    # capabilities gate's fail-closed `_REQUIRED_GATE_FLAGS` rule an
    # absent/unset flag reads as False here (never blocks a projection);
    # only an explicit True flag ever turns this warning on.
    nonresident_income_warning: "str | None" = None
    # Codex round-24 P2: set when the state projection was skipped because
    # the cache/resident resolve to pre-repeal TN — the round-23 TN guard
    # in project_state_tax returns None (never bracket-estimate the Hall
    # regime over all income), but a silent None made render_markdown fall
    # through to the generic "no prior-year state baseline or no state
    # brackets" message, CONCEALING the real limitation: a potentially
    # real Hall-tax liability on interest/dividends needs manual handling
    # (Form INC 250). Mirrors the capability gate's Hall-specific note.
    state_unsupported_note: "str | None" = None
    # Set by `_main` when a malformed stored `as_of_date` inside a CLOSED year
    # is omitted and 12-31 is used — see `resolve_as_of`. A field rather than a renderer
    # decision for the reason `voucher_catchup_note` above is one: both
    # consuming skills read `--json` first, and a banner that says the YTD
    # figures may not be the year's is not one to leave out of the payload
    # they summarise from.
    stale_as_of_note: "str | None" = None
    # Non-null when `state` is a taxed state OTHER than California, whose 540
    # is the only one computed in depth (AGENTS.md: "other states are
    # bracket-estimate only"). A FIELD, not a renderer decision, for the
    # reason `stale_as_of_note` above is one, and it is the same defect twice
    # over: the hedge first went onto the W-4 lede only, then onto the
    # markdown table only. Both consuming skills instruct the agent to read
    # the payload BEFORE summarising anything, and the payload's `state.balance`
    # carried no qualification at all — on an open year `w4_lever_note` is the
    # lead sentence, which has no state clause, so an agent following the
    # documented flow reported a bracket estimate as an amount owed.
    state_bracket_estimate_note: "str | None" = None
    # True when an `archive/{year-1}/` prior return was passed to
    # `build_report` at all. Independent of `degraded`: `degraded` now tracks
    # only whether an explicit `prior_year_form_2210_tax_base` was supplied
    # (the two used to coincide, before an explicit base could be given
    # without an archive), and `recurring_from_prior` is populated by the
    # SKILL from that archive — this engine never reads it automatically. A
    # False here is what the renderer's recurring-income-defaulted-to-0
    # warning gates on, not `degraded`.
    has_prior_archive: bool = False


def validate_pay_frequency(pay_frequency: str) -> str:
    if pay_frequency not in PAY_PERIODS:
        raise ProjectionError(
            f"pay_frequency must be one of {sorted(PAY_PERIODS)}, got {pay_frequency!r}"
        )
    return pay_frequency


def parse_as_of(as_of_str: str, year: int) -> datetime.date:
    """Parse 'MM-DD' into a date in `year`. Rejects Jan 1 and impossible dates."""
    if not isinstance(as_of_str, str):
        raise ProjectionError(
            "as_of must be a 'MM-DD' string; invalid value omitted")
    parts = as_of_str.split("-")
    if len(parts) != 2:
        raise ProjectionError(
            "as_of must be 'MM-DD'; invalid value omitted")
    try:
        month, day = int(parts[0]), int(parts[1])
        d = datetime.date(year, month, day)
    except ValueError:
        raise ProjectionError(
            "as_of is not a real calendar date; invalid value omitted")
    if month == 1 and day == 1:
        raise ProjectionError("as_of must be after Jan 1 — no YTD data to annualize")
    return d


def default_as_of(year: int, today: datetime.date) -> str:
    """The 'MM-DD' to annualize from when the caller named no date.

    Today's MM-DD is the answer for the CURRENT year and for no other. Read
    inside a year that has already closed it is a date in the past of that
    year, so the `as_of > today` guard both CLIs carry cannot fire — 2026-01-05
    is not in the future of 2025 — and the whole finished year is annualized
    from however many days had elapsed on the day of the run. `--year 2025` on
    January 5 divides by 5/365 and multiplies a full year's withholding by
    about 73, producing a shortfall that does not exist and a voucher schedule
    telling the taxpayer to pay it. The OVER-stating direction, and silent.

    December 31 is the only defensible default for a closed year when no date
    is attached to the inputs: the year is over, so the elapsed fraction is
    1.0. An explicit `--as-of`, or an `as_of_date` stored beside the YTD
    figures, still wins — this is the DEFAULT, and both named dates answer the
    question themselves.

    Any year other than today's — past OR future — gets "12-31" rather than
    reusing today's MM-DD: `_validate_year_arg` bounds `year` to 2000-2099
    only, never against `today.year`, so a `--year` ahead of today reaches
    here too. Reusing today's raw MM-DD for a different year is itself
    unsound (a Feb-29 `today` produces an invalid date for any target year
    that is not also a leap year), not only for a future one — this closes
    the whole class rather than the future-year case alone.

    This is the DEFAULT only. Which sources outrank it is `resolve_as_of` —
    call that, not this.
    """
    return "12-31" if year != today.year else today.strftime("%m-%d")


# The OPEN-year refusal. `parse_as_of`'s own messages describe the VALUE
# ("must be 'MM-DD'"), which is the right thing to say to a caller who passed
# `--as-of` and can see what they typed. A STORED value is different: the
# reader has to be told which key, in which file, and what to put there
# instead — and the value itself is never echoed, because runtime metadata can
# hold sensitive data.
#
# This is a BREAKING change against workspaces predating it, stated here
# because that is the only place a reader meets it. The previous chain was
# `args.as_of or stored or today`, so a present-but-falsy `as_of_date` — ""
# and 0 being the usual placeholders — fell through to today's date without a
# word. Silence there is the understating direction: it stamps a date onto YTD
# figures entered against some other one. Refusing is correct; refusing without
# naming the field or the remedy is what made it look like a bug in the tool.
_INVALID_STORED_AS_OF_OPEN_YEAR = (
    "projection_inputs.json has an `as_of_date` that is not a valid MM-DD "
    "calendar date (its value is omitted here — runtime metadata can contain "
    "sensitive data). TY{year} is still OPEN, so it is refused rather than "
    "replaced: the YTD figures beside it were entered as of SOME date, and "
    "substituting today's would silently relabel them. Set `as_of_date` to "
    "the MM-DD those figures are current to, set it to null to use today's "
    "date, or pass `--as-of MM-DD` to override it for this run."
)

_INVALID_STORED_AS_OF_NOTE = (
    "> **\u26a0 Invalid stored as-of date omitted.** `projection_inputs.json` "
    "contains an `as_of_date` that is not a valid MM-DD calendar date. Its "
    "value is deliberately omitted from this report because runtime metadata "
    "can contain sensitive data. TY{year} has CLOSED, so this report reads the "
    "figures as of 12-31 instead. CONFIRM they ARE the year's finals; otherwise "
    "pass a valid `--as-of MM-DD` explicitly."
)


def resolve_as_of(explicit: "str | None", stored, year: int,
                  today: datetime.date) -> "tuple[str, str | None]":
    """The as-of date attached to the YTD figures, or a safe default.

    `--as-of` is an explicit override and wins. A valid stored `as_of_date`
    comes next because it is provenance for the figures beside it: replacing a
    June 30 stamp with December 31 does not create six more months of wages or
    withholding, it merely treats half-year actuals as full-year actuals and
    makes every downstream number actionable on false timing. Only when no
    date is supplied does `default_as_of` choose December 31 for a closed year.

    Every present stored value is validated, including false/zero/empty
    container values. A malformed value in an open year is a hard error naming
    the field and the remedy (`_INVALID_STORED_AS_OF_OPEN_YEAR`, which also
    records why that is a deliberate break with older workspaces). In a closed
    year it cannot be used or echoed — runtime metadata may contain sensitive
    data — so it falls back to the closed-year default with a redacted banner.
    """
    if explicit is not None:
        return explicit, None
    default = default_as_of(year, today)
    if stored is None:
        return default, None
    try:
        parse_as_of(stored, year)
    except ProjectionError:
        if year < today.year:
            # Do not interpolate malformed runtime metadata. Besides avoiding a
            # misleading claim that it is a date, this closes a direct output
            # path for SSNs/credentials accidentally stored in the field.
            return default, _INVALID_STORED_AS_OF_NOTE.format(year=year)
        # Raised fresh rather than re-raised: `parse_as_of`'s message is
        # written for a caller who can see the value they passed, and this
        # caller cannot. Chained with `from None` so the original — which may
        # quote the value — never reaches a traceback the agent prints.
        raise ProjectionError(
            _INVALID_STORED_AS_OF_OPEN_YEAR.format(year=year)) from None
    return stored, None


def _days_in_year(year: int) -> int:
    return 366 if calendar.isleap(year) else 365


def elapsed_fraction(as_of: datetime.date) -> float:
    return as_of.timetuple().tm_yday / _days_in_year(as_of.year)


def paychecks_remaining(pay_frequency: str, fraction: float) -> int:
    total = PAY_PERIODS[validate_pay_frequency(pay_frequency)]
    return max(0, round(total * (1 - fraction)))


def paychecks_available_for_year(pay_frequency: str, year: int,
                                  today: "datetime.date | None",
                                  as_of_count: int) -> int:
    """Paychecks on which a covered-year withholding change can still land.

    The stored/as-of date remains the provenance used to annualize YTD inputs,
    but it is not the clock for an action taken now. Once the covered year has
    ended, a new W-4 or DE 4 changes the following year's withholding and must
    never be presented as a cure for the closed year. When the real date is not
    supplied (pure library callers), retain the as-of-derived count rather than
    silently reading the system clock.
    """
    if today is None or today.year < year:
        return as_of_count
    if today.year > year:
        return 0
    return paychecks_remaining(pay_frequency, elapsed_fraction(today))


def annualize(ytd: dict, as_of: datetime.date, pay_frequency: str,
              overrides: dict, recurring: dict, estimates: dict) -> ProjectedInputs:
    frac = elapsed_fraction(as_of)
    if frac <= 0:
        raise ProjectionError("elapsed fraction must be > 0")
    ytd = ytd or {}
    overrides = overrides or {}
    recurring = recurring or {}
    estimates = estimates or {}

    def ann(key):
        # _nonneg_num: every YTD figure this annualizes — wages, the three
        # withholding fields, ss_wages — is a quantity that cannot be
        # negative. A sign typo in withholding is the costly one: it makes
        # `required - (negative + estimates)` larger, inflating the voucher
        # schedule and the 4(c) figure, which is the OVER-stating direction
        # AGENTS.md makes a hard error. Capital gains are signed and are read
        # with `_num` below, not through here.
        return round(_nonneg_num(ytd.get(key), f"ytd.{key}") / frac, 2)

    base_wages = ann("wages")
    wages = round(base_wages + _num(overrides.get("year_end_bonus"), "year_end_bonus")
                  + _num(overrides.get("rsu_vesting"), "rsu_vesting"), 2)
    ytd_medicare = _nonneg_num(ytd.get("medicare_wages"), "ytd.medicare_wages")
    medicare_wages = round(ytd_medicare / frac, 2) if ytd_medicare else wages

    cg_legacy = _num(overrides.get("capital_gains"), "capital_gains")
    cg_short = _num(overrides.get("capital_gains_short"), "capital_gains_short")
    cg_long = _num(overrides.get("capital_gains_long"), "capital_gains_long")
    if cg_legacy and (cg_short or cg_long):
        raise ProjectionError(
            "forecast_overrides has both the legacy 'capital_gains' and the "
            "'capital_gains_short'/'capital_gains_long' split — use one or the "
            "other (the legacy field is treated as long-term)."
        )

    qualified = _nonneg_num(recurring.get("qualified_dividends"), "qualified_dividends")
    ordinary = _nonneg_num(recurring.get("ordinary_dividends"), "ordinary_dividends")
    # Box 1b is the qualified SPLIT of box 1a, so it cannot exceed it. A
    # negative or oversized figure used to reach the QDCG guard as
    # `preferential_income`, where a sign slip read as "nothing to price" and
    # the LTCG beside it was taxed at ordinary rates — the X2 fall-through by
    # the input door. The scenario keystone clamps the same pair; a parse-time
    # refusal is louder, and the sign the message would print is in the file.
    if qualified > ordinary:
        raise ProjectionError(
            "qualified_dividends cannot exceed ordinary_dividends "
            "(Form 1099-DIV box 1b is the qualified part of box 1a)")

    return ProjectedInputs(
        wages=wages,
        federal_withholding=ann("federal_withholding"),
        state_withholding=ann("state_withholding"),
        medicare_wages=medicare_wages,
        medicare_withheld=ann("medicare_withheld"),
        ss_wages=ann("ss_wages"),
        capital_gains=cg_legacy,
        capital_gains_short=cg_short,
        capital_gains_long=cg_long,
        schedule_1a_total=_nonneg_num(overrides.get("schedule_1a"), "schedule_1a"),
        schedule_1a_se_tips=_nonneg_num(
            overrides.get("schedule_1a_se_tips"), "schedule_1a_se_tips"),
        qualified_dividends=qualified,
        ordinary_dividends=ordinary,
        taxable_interest=_num(recurring.get("taxable_interest"), "taxable_interest"),
        self_employment_net=_num(overrides.get("self_employment_net"), "self_employment_net"),
        other_income=_num(overrides.get("other_income"), "other_income"),
        # _nonneg_num, at the PARSE boundary, named for the JSON field the
        # user actually edits. A negative here is invalid input either way,
        # but `_num` let it through to inflate the shortfall upstream and
        # then be refused (or not) inside `compute_estimated_vouchers`
        # depending on whether the year happened to be under-withheld —
        # under a name (`estimates_paid`) absent from projection_inputs.json.
        estimated_fed_paid=_nonneg_num(
            estimates.get("federal"), "estimated_payments_made.federal"),
        estimated_state_paid=_nonneg_num(
            estimates.get("state"), "estimated_payments_made.state"),
        elapsed_fraction=round(frac, 4),
        paychecks_remaining=paychecks_remaining(pay_frequency, frac),
    )


def project_federal_tax(proj: ProjectedInputs, profile: dict, tax_law: dict,
                        requested_year: "int | None" = None) -> FederalProjection:
    # Value-checked HERE, not only in the safe harbors. Both of those validate
    # inside their prior-year branch, which a first-year workspace never
    # reaches — `prior_tax is None` takes the degraded path — so the whole
    # federal projection used to be computed from a status nothing had
    # checked. `_full_status_key` maps anything non-canonical to "single"
    # through a `.get(fs, "single")` default, single brackets always exist, and
    # no error is raised: "Married Filing Jointly" is silently taxed on single
    # brackets and the single standard deduction. This is the boundary the
    # wrong key is USED at, so it is the one that has to refuse.
    fs = _canonical_status(profile.get("filing_status"), "federal projection")
    full_key = _full_status_key(fs)
    bin_key = _binary_status_key(fs)
    fed = tax_law.get("federal", {}) or {}
    fstatus = (fed.get("filing_statuses", {}) or {}).get(full_key, {}) or {}
    brackets = fstatus.get("brackets") or []
    if not brackets:
        raise ProjectionError(
            f"No federal brackets for status '{full_key}' in tax_law. Run /tax-setup to fetch."
        )
    std = _num(fstatus.get("standard_deduction"), "standard_deduction")

    # ST/LT netting with character preservation (shared net_capital_gains). The
    # legacy combined `capital_gains` field is treated as LONG-term; short-term
    # gains are ordinary-rate income and must not enter the preferential bucket.
    net_st = proj.capital_gains_short
    net_lt = proj.capital_gains_long + proj.capital_gains
    cap_input, ordinary_short = net_capital_gains(net_st, net_lt)
    # A net capital LOSS offsets ordinary income only up to the annual limit
    # ($3,000; $1,500 MFS); the excess carries forward (not modeled here).
    loss_limit = _capital_loss_limit(fs, "federal projection")
    cap_gain_in_gross = cap_input if cap_input >= 0 else max(cap_input, -loss_limit)

    if proj.self_employment_net > 0:
        se = compute_se_tax(
            proj.self_employment_net,
            ss_wage_base=(fed.get("social_security", {}) or {}).get("wage_base"),
            w2_ss_wages=proj.ss_wages,
        )
    else:
        se = {"se_tax": 0.0, "deductible_half": 0.0, "se_earnings": 0.0}

    gross = (proj.wages + cap_gain_in_gross + ordinary_short + proj.ordinary_dividends
             + proj.taxable_interest + proj.other_income + proj.self_employment_net)
    adjustments = se["deductible_half"]
    agi = max(0.0, gross - adjustments)

    net_ltcg = max(0.0, cap_input)
    # QBI needs taxable income BEFORE QBI and net capital gain (qualified
    # dividends + net LTCG) for the Form 8995 overall cap — so it is computed
    # after AGI, mirroring compute_scenario. The fetched §199A threshold is
    # passed so the over-threshold warning (8995-A wage/UBIA/SSTB limits not
    # modeled → deduction may be overstated) surfaces in the report instead of
    # silently understating projected tax for SSTB/wage-limited taxpayers.
    # OBBBA Schedule 1-A (line 13b) is a below-AGI deduction: it comes off
    # taxable income and the QBI cap base but NOT AGI (AGI is line 366, already
    # fixed — so the taxable-SS and NIIT MAGI computations are unaffected).
    # The projected total is a forecast_overrides figure (the taxpayer/skill
    # estimates the capped, MAGI-phased amount via compute_schedule_1a); the
    # projection does not re-derive the caps. GATE by the effective-year window:
    # compute_schedule_1a returns $0 outside TY2025-2028, so a stale override
    # for TY2029+/pre-2025 must not shave taxable income (which would understate
    # tax and the safe-harbor target) — Codex P2.
    proj_year = tax_law.get("tax_year")
    sch1a = max(0.0, proj.schedule_1a_total)
    # Effective-year gate, FAIL CLOSED: apply the Schedule 1-A override ONLY
    # when the cache's tax_year is a genuine int inside TY2025-2028. A missing,
    # non-int, or out-of-window year clears it — a malformed or legacy cache
    # (or a TY2029+/pre-2025 projection) must not shave taxable income. (The
    # earlier "clear only when it's an int OUTSIDE the window" logic failed
    # open on a missing/string year — Codex round-16 P2.)
    year_ok = (isinstance(proj_year, int) and not isinstance(proj_year, bool)
               and _SCH1A_EFFECTIVE_FROM <= proj_year <= _SCH1A_EFFECTIVE_THROUGH
               # ...and, when the caller supplies the requested projection year,
               # the cache year must MATCH it — a cache built for a different
               # (even in-window) year can't validate this year's override.
               and (requested_year is None or proj_year == requested_year))
    if sch1a and not year_ok:
        sch1a = 0.0
    # MFS gate: ALL FOUR Schedule 1-A provisions are $0 for a married filer who
    # files separately — tips/overtime/senior require a joint return AND the
    # §70203 car-loan deduction is also unavailable to MFS (round 13) — so an
    # MFS override must not shave taxable income here either (Codex round-14 P2).
    if sch1a and full_key == "mfs":
        sch1a = 0.0
    # A raw override is subtracted here without re-deriving the caps/MAGI phase-
    # out (compute_schedule_1a is not called). If the indexed `federal.schedule_
    # 1a` block is missing OR only scaffold (all/any None — as create_empty_tax_
    # law seeds it), the override is applied uncapped/unphased — flag it so the
    # user knows the projected deduction may exceed the real amount rather than
    # silently trusting a possibly-raw figure.
    schedule_1a_unfetched = bool(
        sch1a > 0 and _sch1a_limits_incomplete(fed.get("schedule_1a")))
    ti_before_qbi = max(0.0, agi - std - sch1a)
    # §224 SE tips are EXCLUDED from QBI (Form 8995): when the applied Schedule
    # 1-A deduction includes self-employed qualified tips, those same dollars
    # are removed from the QBI base so they don't get both the line-13b
    # deduction and 20% QBI. Only when the deduction is actually applied (same
    # gate as sch1a) and capped at self_employment_net so the base can't go
    # negative (Codex round-17 P2).
    # Bound by BOTH self_employment_net (can't exclude more SE income than
    # exists) AND sch1a (the APPLIED deduction — SE tips can't be excluded from
    # QBI beyond what was actually deducted on line 13b; an override with
    # se_tips > schedule_1a is inconsistent and must not over-exclude). Codex P2.
    se_tips_qbi_excl = (
        min(proj.schedule_1a_se_tips, proj.self_employment_net, sch1a)
        if sch1a > 0 else 0.0)
    qbi_base = max(0.0, proj.self_employment_net - se_tips_qbi_excl)
    qbi_over_threshold = False
    # Form 8995 line 12 and the QDCG worksheet price the same quantity.
    net_capital_gain = proj.qualified_dividends + net_ltcg
    if qbi_base > 0:
        # §199A(c)(4) / Reg. §1.199A-3(b)(1)(vi): net the QBI base by the
        # deductible half of SE tax (the SE-tips exclusion is already applied in
        # qbi_base above; SE health insurance / retirement are not projection
        # inputs). Omitting it overstates the QBI deduction and understates
        # projected tax for a self-employed filer.
        qbi_res = compute_qbi_deduction(
            qbi_base, 0.0, rate=fed.get("qbi_deduction_rate", 0.20),
            taxable_income_before_qbi=ti_before_qbi,
            net_capital_gain=net_capital_gain,
            threshold=(fed.get("qbi_threshold") or {}).get(bin_key),
            half_se_tax=se["deductible_half"],
        )
        qbi = qbi_res["total_deduction"]
        qbi_over_threshold = bool(qbi_res.get("over_threshold"))
    else:
        qbi = 0.0
    taxable_income = max(0.0, ti_before_qbi - qbi)

    cg_zero, cg_fifteen = capital_gains_limits(tax_law, fs, "federal projection")
    # ProjectionError, not the engine's ValueError: this is the type every
    # caller of this module already handles, and the error is a workspace
    # state (an unfetched cache), not a bad argument.
    try:
        worksheet = qdcg_worksheet_applies(net_capital_gain, cg_zero, cg_fifteen)
    except ValueError as e:
        raise ProjectionError(str(e)) from None
    if worksheet:
        qd = compute_qdcg_tax(taxable_income, proj.qualified_dividends, net_ltcg,
                              brackets, cg_zero, cg_fifteen)
        tax_before_credits = qd["final_tax"]
        used_qdcg = qd["used_qdcg_worksheet"]
    else:
        tax_before_credits, _ = compute_bracket_tax(taxable_income, brackets)
        used_qdcg = False

    deps = profile.get("dependents") or []
    n_ctc = sum(1 for d in deps if isinstance(d, dict) and d.get("ctc_eligible"))
    ctc_cfg = fed.get("child_tax_credit", {}) or {}
    if n_ctc and ctc_cfg.get("amount_per_child"):
        ctc = compute_child_tax_credit(
            n_ctc, ctc_cfg["amount_per_child"], agi,
            (ctc_cfg.get("phase_out_start") or {}).get(bin_key) or 0,
        )["credit_allowed"]
    else:
        ctc = 0.0

    addl_cfg = fed.get("additional_medicare_tax", {}) or {}
    threshold_map = addl_cfg.get("threshold") or {}
    addl_threshold = threshold_map.get(full_key) or threshold_map.get(bin_key)
    medicare_wages = proj.medicare_wages or proj.wages
    if addl_threshold is not None:
        addl = compute_additional_medicare(
            medicare_wages, se["se_earnings"], addl_threshold,
            proj.medicare_withheld, rate=addl_cfg.get("rate", 0.009),
        )["additional_tax"]
    else:
        addl = 0.0

    # NIIT (Form 8960): 3.8% of the lesser of net investment income and MAGI
    # (≈AGI) over the threshold. Omitting it understated the projected total —
    # and therefore the safe-harbor target — for high earners with investment
    # income. Degrades to $0 when tax_law has no niit block (threshold None).
    niit_cfg = fed.get("niit", {}) or {}
    niit_thr_map = niit_cfg.get("threshold") or {}
    niit_threshold = niit_thr_map.get(full_key) or niit_thr_map.get(bin_key)
    # Form 8960 line 5a is the net gain/loss AS INCLUDED IN AGI — a net capital
    # loss reduces NII by the capped allowed amount (-$3K/-$1.5K-MFS), it is not
    # floored at zero. cap_gain_in_gross + ordinary_short is exactly that figure
    # (full combined gain when positive, capped loss when negative);
    # compute_niit itself returns 0 when total NII goes non-positive.
    nii = (proj.taxable_interest + proj.ordinary_dividends
           + cap_gain_in_gross + ordinary_short)
    niit = compute_niit(agi, nii, niit_threshold, rate=niit_cfg.get("rate", 0.038) or 0.038)

    total_tax = max(0.0, tax_before_credits - ctc) + se["se_tax"] + addl + niit
    balance = round(total_tax - proj.federal_withholding - proj.estimated_fed_paid, 2)

    return FederalProjection(
        gross_income=round(gross, 2), adjustments=round(adjustments, 2), agi=round(agi, 2),
        taxable_income=round(taxable_income, 2), tax_before_credits=round(tax_before_credits, 2),
        ctc=round(ctc, 2), se_tax=round(se["se_tax"], 2), additional_medicare=round(addl, 2),
        total_tax=round(total_tax, 2), withholding=round(proj.federal_withholding, 2),
        estimates_paid=round(proj.estimated_fed_paid, 2), balance=balance, used_qdcg=used_qdcg,
        niit=round(niit, 2), qbi_over_threshold=qbi_over_threshold,
        schedule_1a_unfetched=schedule_1a_unfetched,
    )


def _de_minimis_cliff_paths(projected_tax: float, withholding: float,
                            paid: float, required: float, margin: float,
                            prior_based: "float | None",
                            floor: float) -> "tuple[float, float]":
    """`(more_tax, less_withholding)` — what attaches on EACH path past the floor.

    The status cell names two ways for the excuse to evaporate, and they move
    different sides of the test, so both are worked:

    - MORE TAX than projected. The floor lapses at tax = withholding + floor,
      which raises the 90% prong. The prior-year prong does NOT move with the
      current year, so wherever it binds the required payment does not move
      either — which is exactly the case the old unconditional warning got
      wrong.
    - LESS WITHHOLDING than projected. The tax is unchanged, so the requirement
      is unchanged, but `paid` falls by exactly the margin. Reachable only when
      the tax is at least the floor: `margin` is `floor - (tax - withholding)`,
      so `withholding >= margin` is the same statement as `tax >= floor`, and
      below it withholding cannot fall far enough to lapse anything.

    Returned as a PAIR, not as the worse of the two, because the two paths are
    not both open in both reports. /tax-project annualizes the tax, so either
    can move; /tax-withholding reads the tax off result.json and annualizes
    only the withholding pace. Keeping the maximum let a positive from the tax
    path be printed by /tax-withholding as a warning about withholding — for
    $500 of computed tax against $0 withheld, "$500 less withholding and it
    attaches", on a figure that cannot fall and against a tax the same report
    calls actual. Over-stating exposure is the hard-error direction whether it
    arrives as a total or as a warning, so each renderer now reads the paths
    its own inputs can actually take.

    A path is 0.0 when it leaves nothing owed — the required annual payment is
    met on its own and the floor is not what is carrying the year. That is the
    whole point of computing them: the warning is only true where positive.
    """
    cliff_pct90 = round(0.90 * (withholding + floor), 2)
    cliff_required = (round(min(cliff_pct90, prior_based), 2)
                      if prior_based is not None else cliff_pct90)
    more_tax = round(max(0.0, cliff_required - paid), 2)
    less_withholding = (round(max(0.0, required - (paid - margin)), 2)
                        if projected_tax >= floor else 0.0)
    return more_tax, less_withholding


def compute_safe_harbor(projected_tax: float, prior_tax: float | None, prior_agi: float | None,
                        filing_status: str | None, withholding: float,
                        estimates_paid: float) -> SafeHarbor:
    """Federal required annual payment and §6654(e)(1) floor.

    `projected_tax` is a line-24-shaped TOTAL TAX — /tax-project annualizes it,
    /tax-withholding reads `form_1040.line_24_total_tax`. It is NOT Form 2210
    line 4, which is what §6654 actually measures: `compute_form_2210` in
    `tax_computations` states that contract on its own argument, and neither
    caller here can meet it, because the projection models the CTC only as a
    nonrefundable credit and result.json carries no field for the EIC or the
    ACTC at all. Line 4 can only be LOWER than what arrives here, so both the
    90% prong and the floor test come out too high and the shortfall shown is
    the OVER-stating direction. `current_tax_unnetted` flags exactly the runs
    where that reaches a figure the taxpayer would act on; see the field.

    `withholding` is likewise the §31(a) figure only. §6654(e)(1) reduces the
    tax by the whole §31 credit, which includes §31(b) excess social security
    (Schedule 3 line 11) — the same one-directional gap, on the other side of
    the same subtraction.

    `prior_tax`, when present, is the completed pre-factor tax base constructed
    under line 8 of the Form 2210 instructions for the covered year. It is not
    raw Form 1040 line 24 and not Form 2210 line 8 after the 100%/110% factor.
    Callers that do not have that completed composition must pass None and
    leave the prior-year prong unresolved.
    """
    # Rounded to cents ONCE, up front, so the fields this returns and the
    # arithmetic below read the same numbers. Rounding only into the dataclass
    # left `required_annual - (withholding + estimates_paid)` a cent away from
    # the `shortfall` printed beside them on a fractional-cent input — two
    # payload fields a reader adds together disagreeing — and
    # `compute_estimated_vouchers` reconstructs `shortfall + estimates`, which
    # carries the drift into the voucher column.
    withholding = round(withholding, 2)
    estimates_paid = round(estimates_paid, 2)
    pct90 = round(0.90 * projected_tax, 2)
    degraded = prior_tax is None
    prior_agi_missing = False
    if degraded:
        # filing_status is unused on this branch — the 90%-of-current-year
        # test needs no MFS/threshold information — and deliberately NOT
        # validated here: a caller with no completed prior-year base has no
        # need to supply (or have validated) one either. Callers that DO need
        # a validated status regardless of degraded state (e.g. /tax-project,
        # /tax-withholding, for their CA half) validate it themselves, one
        # level out, before calling in.
        required = pct90
        prior_factor = None
        prior_based = None
    else:
        # Prior AGI unknown → assume the 100% factor, flagged so renderers
        # print the may-be-UNDERSTATED note (the compute_form_2210 precedent,
        # pinned by the required-annual-payment parity test).
        #
        # `!= 0` for the reason its `compute_form_2210` twin carries: at a $0
        # prior-year base the factor cannot change the answer — 110% of $0 and
        # 100% of $0 are both $0 — so the note would claim an understatement
        # that is arithmetically impossible.
        prior_agi_missing = prior_agi is None and prior_tax != 0
        prior_factor, prior_based = prior_year_prong(
            prior_tax, prior_agi,
            _canonical_status(filing_status, "safe harbor") == "MFS")
        required = round(min(pct90, prior_based), 2)
    paid = round(withholding + estimates_paid, 2)
    shortfall = round(max(0.0, required - paid), 2)
    # §6654(e)(1), the CA de-minimis rule below applied to its own statute:
    # under the floor no federal penalty can arise at any payment pace, so the
    # PENALTY target is met and the shortfall driving the "to avoid penalty"
    # W-4 figure and voucher schedule reads zero. WITHHOLDING only —
    # §6654(e)(1) reduces the tax by tax withheld, not by estimates made. The
    # zero-balance target is computed by callers from the liability and still
    # shows every dollar due at filing. Suppressed at the CONSTRUCTOR, not by
    # overwriting `shortfall`: the raw gap is what decides whether a penalty
    # attaches when the floor lapses, so destroying it here is what left the
    # status cell asserting one unconditionally.
    # Rounded to cents BEFORE the floor comparison: two already-rounded
    # dollar figures can still subtract to something like 999.9999999999998
    # in raw float arithmetic, which is `< 1000` even though the true
    # balance is exactly $1,000.00 — misclassifying an on-the-floor balance
    # as under it.
    balance_before_estimates = round(projected_tax - withholding, 2)
    de_minimis = balance_before_estimates < DE_MINIMIS_BALANCE
    margin = round(DE_MINIMIS_BALANCE - balance_before_estimates, 2) if de_minimis else 0.0
    cliff_more_tax, cliff_less_withholding = (
        _de_minimis_cliff_paths(projected_tax, withholding, paid, required,
                                margin, prior_based, DE_MINIMIS_BALANCE)
        if de_minimis else (0.0, 0.0))
    return SafeHarbor(
        pct90_current=pct90, prior_tax=prior_tax, prior_factor=prior_factor,
        prior_based=prior_based, required_annual=required, withholding=withholding,
        estimates_paid=estimates_paid,
        shortfall=0.0 if de_minimis else shortfall,
        covered=de_minimis or shortfall <= 0, degraded=degraded,
        prior_agi_missing=prior_agi_missing, de_minimis=de_minimis,
        de_minimis_margin=margin,
        shortfall_before_de_minimis=shortfall,
        de_minimis_cliff_more_tax=cliff_more_tax,
        de_minimis_cliff_less_withholding=cliff_less_withholding,
        current_tax_unnetted=(not de_minimis) and shortfall > 0,
    )


def compute_w4_4c(shortfall: float, paychecks_remaining: int) -> W4Adjustment:
    if paychecks_remaining <= 0:
        per = 0.0
    else:
        per = round(max(0.0, shortfall) / paychecks_remaining, 2)
    return W4Adjustment(shortfall=round(shortfall, 2),
                        paychecks_remaining=paychecks_remaining, per_paycheck=per)


# `_split_remainder_last` MOVED to `tax_computations` (PR #146). The New York
# required-annual-payment function needed it and lives there, and importing
# upward would be a cycle — the same direction `prior_year_prong` already went.
# Re-exported by the import above so every existing caller here is unchanged.


# What a reader of `projection_inputs.json` can actually search for. Both
# voucher schedules take `estimates_paid` as a PARAMETER, and a message naming
# the parameter sent them looking for a key that is not in the file — the
# complaint `annualize` records against its own pre-fix state.
_ES_ESTIMATES_FIELD = "estimated_payments_made.federal (estimates_paid)"
_CA_ES_ESTIMATES_FIELD = "estimated_payments_made.state (estimates_paid)"


def _early_estimate_vouchers(shortfall: float, estimates_paid: float,
                             rows, instalment_fraction: Callable[[int], float]) -> list:
    """Cumulative voucher rows while the first observed deadline is open.

    Every estimate in the input has already been paid by ``as_of``. While Q1
    remains open, all of it is therefore timely for every installment and can
    be applied to each cumulative checkpoint. ``shortfall`` is already net of
    those estimates, so adding them back reconstructs the annual requirement
    net of withholding; withholding itself carries the same installment
    fraction as the requirement in both modeled jurisdictions.

    Once Q1 has passed the undated estimate total cannot establish which
    installment received it on time. Callers retain the annual-gap schedule
    plus the dated-payment warning for that case instead of inventing payment
    dates here.

    `estimates_paid` arrives ALREADY VALIDATED — both callers coerce it at
    their own entry, above their covered-year early return, and pass the
    result. Re-validating here was a third application of one check, and the
    only one of the three whose message named a field nobody could find.
    """
    paid = float(estimates_paid)
    annual_net_of_withholding = round(shortfall + paid, 2)
    vouchers = []
    allocated = 0.0
    for i, (q, due_date) in enumerate(rows):
        if i == len(rows) - 1:
            amount = round(shortfall - allocated, 2)
        else:
            cumulative_due = round(max(
                0.0, annual_net_of_withholding * instalment_fraction(q) - paid), 2)
            amount = round(max(0.0, cumulative_due - allocated), 2)
        allocated = round(allocated + amount, 2)
        vouchers.append(Voucher(q, due_date, amount))
    return vouchers


def compute_estimated_vouchers(shortfall: float, as_of: datetime.date,
                               estimates_paid: float = 0.0,
                               observed=None) -> list:
    """1040-ES voucher schedule for the deadlines still open at `as_of`.

    `observed` turns a statutory date into the date it is actually OBSERVED,
    and defaults to the FEDERAL rule. New York's IT-2105 is the same four
    equal quarters on the same statutory dates and a DIFFERENT rollover rule,
    so it passes its own — see `compute_ny_estimated_vouchers`. California's
    schedule is not a variant of this one at all (30/40/0/30, no September)
    and has its own function.

    Takes the SHORTFALL already derived, not the requirement it came from.
    This is called for two different targets — the penalty one and the
    zero-balance one — and each caller owns which quantity it is splitting:
    `safe_harbor.shortfall` for the first, `liability - withholding -
    estimates` for the second.

    It used to take `required_annual` and recompute `required - withholding -
    paid` here, with a `de_minimis` flag layered on top whose only job was to
    override that second computation wherever §6654(e)(1) had already zeroed
    the harbor's own. Two derivations of one quantity is what let the CA
    schedule keep printing instalments inside a section whose status line read
    "no CA underpayment penalty applies". One derivation, with every
    suppression already applied to it, means the NEXT statutory suppression
    propagates here without a second flag.

    Before the first observed deadline, estimates already paid are applied to
    the cumulative 25% / 50% / 75% / 100% checkpoints. This matters when an
    early estimate covers Q1: evenly splitting only the annual amount left
    would ask for money at April 15 that is not due.

    Simplification (the CA schedule below carries the same one): after an
    earlier deadline closes, the ANNUAL shortfall is divided EVENLY across the
    open deadlines because estimate dates are unavailable. Form 2210 tracks
    each installment cumulatively, so the split can put LESS at the earliest
    still-open one than that date itself requires — an UNDERSTATEMENT, which
    is why the rendered schedule carries
    `_ES_VOUCHER_CATCHUP_NOTE` wherever it can bite
    (`_voucher_catchup_note`: not on an all-zero schedule, and not while
    every deadline is still open). `/tax-safe-harbor` step 4 states the
    cumulative test that corrects it.
    """
    observe = observed or _observed_due
    open_q = []
    for q, (m, d) in ES_DUE_DATES:
        if q == 4:
            due = observe(datetime.date(as_of.year + 1, m, d))
            open_q.append((4, due.isoformat()))
            continue
        due = observe(datetime.date(as_of.year, m, d))
        # The row stores the date on which payment is actually due, not a bare
        # statutory date that has already passed during its §7503 rollover
        # window. TY2023 Q1 therefore reads April 18 everywhere — object, JSON
        # and Markdown — while remaining open through that day.
        if due >= as_of:
            open_q.append((q, due.isoformat()))
    # Q4 is appended unconditionally above, so open_q is never empty — the
    # CA twin below says the same of its own loop and carries no fallback.
    #
    # Validated BEFORE the covered-year early return, not after it. Sitting
    # below the return, the check ran only on a year that happened to be
    # under-withheld: a negative or NaN `estimates_paid` on a covered year
    # returned a clean all-zero schedule with nothing said. The field name is
    # the one in `projection_inputs.json`, because `estimates_paid` is a
    # parameter of this function and appears nowhere in the file the reader
    # edits — the same defect `annualize` fixed for the parse boundary.
    estimates = _nonneg_num(estimates_paid, _ES_ESTIMATES_FIELD)
    if shortfall <= 0:
        return [Voucher(q, dd, 0.0) for q, dd in open_q]
    if open_q[0][0] == 1 and estimates > 0:
        return _early_estimate_vouchers(
            shortfall, estimates, open_q, _federal_instalment_fraction)
    # Even weighting (1.0 per open quarter) — `_split_remainder_last` puts
    # the rounding remainder on the last one so the column sums to the
    # shortfall exactly; see its docstring for why that matters.
    amounts = _split_remainder_last(shortfall, [1.0] * len(open_q))
    return [Voucher(q, dd, amt) for (q, dd), amt in zip(open_q, amounts)]


def compute_ca_safe_harbor(projected_tax: float, prior_tax: "float | None",
                           prior_agi: "float | None", current_agi: float,
                           filing_status: "str | None", withholding: float,
                           estimates_paid: float) -> CaSafeHarbor:
    """CA required annual payment per FTB Form 5805.

    Same structure as the federal §6654 test (CA conforms, including the
    110%-of-prior factor when PRIOR-year AGI exceeds $150K/$75K-MFS), plus
    the R&TC §19136.3 limitation: when CURRENT-year CA AGI is at or above
    $1M ($500K MFS), the prior-year exception is unavailable and the
    required payment is 90% of the current-year tax. Note the two AGI tests
    read different years — §19136.3 looks at the year being paid,
    the 110% factor at the preceding year.
    """
    # MFS drives BOTH CA-specific cliffs here ($500K §19136.3 limit, $250 de
    # minimis) on top of the shared $75K 110% threshold, which is why the
    # validation `_canonical_status` performs matters more on this side.
    status = _canonical_status(filing_status, "CA safe harbor")
    # Rounded once up front — see the federal `compute_safe_harbor`'s
    # identical note on why the stored fields and the arithmetic must read
    # the same cents-rounded values.
    withholding = round(withholding, 2)
    estimates_paid = round(estimates_paid, 2)
    pct90 = round(0.90 * projected_tax, 2)
    mfs = status == "MFS"
    high_limit = CA_HIGH_INCOME_AGI_LIMIT_MFS if mfs else CA_HIGH_INCOME_AGI_LIMIT
    high = _num(current_agi, "current_agi") >= high_limit
    degraded = (prior_tax is None) and not high
    prior_factor = None
    prior_based = None
    prior_agi_missing = False
    if high or degraded:
        required = pct90
    else:
        # `!= 0` — see the federal twin: the 100%/110% factor is irrelevant
        # against a $0 prior-year CA tax, so flagging a possible
        # understatement there would be false.
        prior_agi_missing = prior_agi is None and prior_tax != 0
        prior_factor, prior_based = prior_year_prong(prior_tax, prior_agi, mfs)
        required = round(min(pct90, prior_based), 2)
    paid = round(withholding + estimates_paid, 2)
    shortfall = round(max(0.0, required - paid), 2)
    ca_floor = CA_ES_DE_MINIMIS_MFS if mfs else CA_ES_DE_MINIMIS
    # Rounded to cents before the floor comparison — see the federal
    # `compute_safe_harbor`'s identical fix for why raw float subtraction
    # can misclassify an exact-floor balance.
    ca_balance_before_estimates = round(projected_tax - withholding, 2)
    de_minimis = ca_balance_before_estimates < ca_floor
    # `prior_tax` bounds §19136(c)(2)'s prior-year quantity from ABOVE, which
    # is what lets a figure this engine cannot net settle a rule written about
    # tax LESS CREDITS. Stated against the actual composition rather than
    # against "a Form 540 total" in general, because it is the composition the
    # bound needs and `result_template.json` is where it is defined:
    #
    #   ca_total_tax   = ca_tax_after_credits + ca_mental_health_surcharge
    #                    + schedule_p.ca_amt
    #   §19136(c)(2)   = (§17041/17048 tax + §17062 AMT) - credits under
    #                    Part 10 or this part
    #
    # Subtracting the second from the first leaves the §17043 mental-health
    # surcharge (which the statute does not name, so it only ADDS here) plus
    # every credit the statute nets that `ca_tax_after_credits` has not
    # already taken off. Both terms are non-negative, so the difference is,
    # and a `ca_total_tax` under the floor puts the statutory figure under it
    # too — whatever those credits were, and without settling whether the
    # §19002 withholding credit is among them. `ca_total_tax` is also GROSS of
    # withholding (`ca_withholding_box17` is its sibling, not folded in); were
    # it ever netted the bound would invert silently, which is why
    # `test_the_bound_the_prong_rests_on_is_a_property_of_the_template` pins
    # the composition rather than trusting this comment.
    #
    # `> 0` because an exact $0 is already a $0 required payment through
    # `prior_year_prong` BELOW the §19136.3 limitation, and the note pair below
    # says which rule produced that — two notes for one fact reads as a bug.
    # This flag therefore selects a NOTE; the line under it decides the money,
    # and it is the one a $0 prior year has to reach.
    prior_year_floor = prior_tax is not None and 0 < prior_tax < ca_floor
    # Spelled from the same two ingredients as the `prior_year_floor_applied`
    # property, in the same order, because the local and the property drifting
    # apart is exactly how this went wrong once (see `prior_tax_unnetted`);
    # `test_the_applied_flag_and_the_property_cannot_drift` pins them equal.
    prior_year_floor_applied = prior_year_floor or prior_tax == 0
    no_penalty = de_minimis or prior_year_floor_applied
    # The third state of the same subdivision, and the only one of the three
    # that costs money: with NO prior-year CA figure, §19136(c)(2)'s prior-year
    # half is untested, and the report went on to print a voucher schedule as
    # though it had been tested and failed. Gated on the schedule actually
    # asking for something — under the de minimis the floor is moot and the
    # note would be noise.
    #
    # "Requirement already paid" is NOT the same statement, and reading it as
    # one is what the second disjunct fixes. FTB 5805 tests each instalment
    # CUMULATIVELY, so estimates that arrive after a deadline meet the annual
    # requirement without curing the instalment they were late for: the gap
    # reads zero and a penalty has still accrued. §19136(c)(2) bars the
    # addition to tax outright on either year's figure, dates and all, so an
    # unread prior Form 540 can still clear that year — and the run said
    # nothing, because the only note pointing at the prior year was gated on
    # the gap. At or above the §19136.3 limitation nothing else pointed there
    # either: `degraded` is False up there by design, so the taxpayer got the
    # timing warning and no route out of it.
    #
    # The disjunct is `_voucher_catchup_note`'s own gate minus the calendar:
    # estimates paid, and withholding short of the requirement. Withholding
    # ALONE is excluded for the reason that gate gives — §6654(g)(1) and FTB
    # 5805 Worksheet II line 2 deem it paid ratably at every deadline, so it
    # cannot create a late instalment and the floor really is moot. The
    # calendar half is left out deliberately: before the first deadline the
    # warning is silent but the lookup still settles the year, and a note that
    # appears only once the damage is done arrives too late to prevent it.
    #
    # NOT folded into `degraded`, and that is the whole distinction this flag
    # exists to keep. `degraded` says the PRONG could not be computed, and its
    # note offers to restore the prong; it carries `and not high` because at the
    # §19136.3 limitation the prong is withdrawn by statute rather than missing,
    # so up there the offer would be one the statute refuses. The FLOOR is a
    # different rule in a different section, it survives the limitation intact
    # (see `prior_year_floor_applied`), and it is therefore unresolved at every
    # AGI alike — which is why this one carries no `and not high`. Widening
    # `degraded` instead was the near miss: it would have printed the offer.
    timing_can_still_bite = estimates_paid > 0 and withholding < required
    prior_year_floor_unknown = (prior_tax is None and not de_minimis
                                and (shortfall > 0 or timing_can_still_bite))
    ca_margin = round(ca_floor - ca_balance_before_estimates, 2) if de_minimis else 0.0
    # The cliff paths answer "what attaches once the CURRENT-year floor
    # lapses", and where the PRIOR-year prong is carrying the year the answer
    # is nothing on either path: that prong is measured on a closed year and
    # no projection of this one can move it. Both floors can hold at once —
    # a small prior-year tax and a small current-year balance is the ordinary
    # shape of a small return — and the cliff was computed off `de_minimis`
    # alone, so those taxpayers were told a penalty attaches if the projection
    # runs $100 high, on a year the subdivision has already cleared outright.
    # Zeroed at SOURCE as well as branched on in the cell: the fields state
    # what attaches, and leaving a live figure in them for a future reader to
    # print is the same bug waiting on a second consumer.
    ca_cliff_more_tax, ca_cliff_less_withholding = (
        _de_minimis_cliff_paths(projected_tax, withholding, paid, required,
                                ca_margin, prior_based, ca_floor)
        if de_minimis and not prior_year_floor_applied else (0.0, 0.0))
    # Under the de minimis no CA penalty can arise, so the PENALTY target is
    # met and the reported shortfall is zero — DE 4 and "to avoid penalty"
    # vouchers must not demand payments that avoid nothing (Codex P2 on PR
    # #35). Suppressed at the CONSTRUCTOR rather than by overwriting
    # `shortfall`, so the gap it replaced survives on the object; the
    # zero-balance target is computed by callers from the liability and still
    # shows the tax due.
    return CaSafeHarbor(
        pct90_current=pct90, prior_tax=prior_tax, prior_factor=prior_factor,
        prior_based=prior_based, required_annual=required,
        withholding=withholding, estimates_paid=estimates_paid,
        shortfall=0.0 if no_penalty else shortfall,
        covered=no_penalty or shortfall <= 0, degraded=degraded,
        high_income_90_only=high, high_income_threshold=float(high_limit),
        prior_agi_missing=prior_agi_missing, de_minimis=de_minimis,
        prior_year_floor=prior_year_floor,
        prior_year_floor_unknown=prior_year_floor_unknown,
        de_minimis_margin=ca_margin, prior_tax_unnetted=prior_based is not None,
        shortfall_before_de_minimis=shortfall,
        de_minimis_cliff_more_tax=ca_cliff_more_tax,
        de_minimis_cliff_less_withholding=ca_cliff_less_withholding,
        current_tax_unnetted=(not no_penalty) and shortfall > 0,
    )


def compute_ca_estimated_vouchers(shortfall: float, as_of: datetime.date,
                                  estimates_paid: float = 0.0) -> list:
    """CA 540-ES voucher schedule under the 30/40/0/30 weighting.

    The remaining shortfall (net of withholding and of estimates already
    paid) is allocated across the still-open installments under California's
    statutory weights. Before the first observed deadline, estimates already
    paid are credited against each cumulative 30% / 70% / 70% / 100%
    checkpoint; requirement and withholding carry the same weights under FTB
    5805 Worksheet II (`_ca_instalment_fraction` states that rule and what it
    cost to get wrong). After a deadline closes, the remaining annual gap is
    allocated in proportion to the open weights because estimate dates are
    unavailable. Q3 is emitted at $0 whenever it is still open so the "no
    September CA payment" fact is visible in the schedule rather than silently
    absent. The rounding remainder lands on the last weighted installment so
    the column sums to the shortfall exactly.

    Simplification (same as the federal schedule): FTB 5805 tracks each
    installment cumulatively, so paying after a missed installment date does
    not fully cure that installment's underpayment — this schedule targets
    the ANNUAL requirement from `as_of` forward.

    Takes the SHORTFALL already derived, for the reason the federal schedule
    above states: `compute_ca_safe_harbor` reports zero under R&TC §19136 so
    the DE 4 figure does not demand payments that avoid nothing, and this
    function recomputing a shortfall of its own is what kept the schedule
    printing an instalment, and the catch-up note firing, inside a section
    whose own status line reads "no CA underpayment penalty applies".
    """
    open_q = []
    for q, (m, d), weight in CA_ES_SCHEDULE:
        # The STATE rule, resolved for CALIFORNIA — which adopts the FEDERAL
        # holiday set (R&TC §18410), so these dates agree with `_observed_due`
        # today. It stays on the state helper because the SET is a fact about
        # a jurisdiction rather than about this schedule, and these dates are
        # what a taxpayer pays on.
        if q == 4:
            observed = _state_observed_due(datetime.date(as_of.year + 1, m, d))
            open_q.append((4, observed.isoformat(), weight))
            continue
        observed = _state_observed_due(datetime.date(as_of.year, m, d))
        if observed >= as_of:
            open_q.append((q, observed.isoformat(), weight))
    # Q4 is appended unconditionally above, so open_q is never empty and
    # total_weight is never 0 (Q4 carries 0.30).
    total_weight = sum(w for _, _, w in open_q)
    # Ahead of the early return, and named for the JSON field — see the
    # federal twin above for both reasons.
    estimates = _nonneg_num(estimates_paid, _CA_ES_ESTIMATES_FIELD)
    if shortfall <= 0 or total_weight <= 0:
        return [Voucher(q, dd, 0.0) for q, dd, _ in open_q]
    if open_q[0][0] == 1 and estimates > 0:
        return _early_estimate_vouchers(
            shortfall, estimates, [(q, dd) for q, dd, _ in open_q],
            _ca_instalment_fraction)
    amounts = _split_remainder_last(shortfall, [w for _, _, w in open_q])
    return [Voucher(q, dd, amt) for (q, dd, _), amt in zip(open_q, amounts)]


def _prior_numeric(v, *, allow_negative: bool):
    """Prior-year figure sanitizer: bool, non-finite (json.loads accepts
    NaN/Infinity), and — for tax figures — negative values all degrade to
    None (→ 90%-of-current fallback) instead of corrupting the min():
    min(pct90, NaN) propagates NaN and min(pct90, -5000) marks a corrupted
    archive as "covered"."""
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return None
    f = float(v)
    if not math.isfinite(f):
        return None
    if f < 0 and not allow_negative:
        return None
    return f


# Fields whose non-zero values prove /tax-compute step 10 actually RAN —
# deliberately excludes payment/bookkeeping fields (ca_withholding_box17,
# ca_sdi_box14, estimated payments): those can be populated from W-2 parsing
# before the CA return is ever computed, so their presence proves nothing
# about ca_total_tax/ca_agi being real rather than untouched template zeros.
# A genuinely computed CA return always carries a non-zero
# ca_standard_deduction (statutory minimum ~$5K), even at zero income.
_CA_COMPUTED_EVIDENCE_FIELDS = (
    "ca_total_tax", "ca_agi", "ca_taxable_income",
    "ca_standard_deduction", "ca_tax_from_table",
)

# New York's twin, and the same rule picks the fields: an output of the
# COMPUTATION, never a figure that can arrive from parsing. Box 17 withholding
# lands in line 72 before Form IT-201 is computed at all, so line 72 proves
# nothing. A genuinely computed New York return always carries a non-zero line
# 34 — the standard deduction has a floor for every filing status, including
# the filer who can be claimed as a dependent — even at zero income, and line
# 33 may be NEGATIVE, which is non-zero and therefore evidence too.
_NY_COMPUTED_EVIDENCE_FIELDS = (
    "line_46_total_state_tax", "line_33_nyagi", "line_37_taxable_income",
    "line_34_deduction", "line_39_ny_tax",
)


def _section_computed(result: dict, section: str,
                      fields: "tuple[str, ...]") -> bool:
    """True when `section` shows COMPUTED evidence — a non-zero finite number
    in one of its compute-output fields — not mere non-emptiness.

    `yoy_diff._section_present` is too weak for this: a section holding only
    parsed W-2 withholding reads "present" while the computed totals are still
    template zeros (Codex P2 on PR #35). Which fields count is the caller's,
    because only the caller knows which of its fields a PARSER can fill.
    """
    block = (result or {}).get(section)
    if not isinstance(block, dict):
        return False
    for key in fields:
        v = block.get(key)
        if (isinstance(v, (int, float)) and not isinstance(v, bool)
                and math.isfinite(v) and v != 0):
            return True
    return False


def _ca_section_computed(result: dict) -> bool:
    """True when result's california_540 section shows COMPUTED evidence."""
    return _section_computed(result, "california_540",
                             _CA_COMPUTED_EVIDENCE_FIELDS)


def _ny_section_computed(result: dict) -> bool:
    """True when result's new_york_it201 section shows COMPUTED evidence."""
    return _section_computed(result, "new_york_it201",
                             _NY_COMPUTED_EVIDENCE_FIELDS)


def _prior_ca_tax_agi(prior):
    """Extract (prior CA total tax, prior CA AGI) from the archived
    california_540 section, or (None, None) when absent, scaffold-only,
    or non-numeric. An all-zero template scaffold (never computed) must
    not become a $0 prior-year tax — that would make ANY current shortfall
    "covered" via min(pct90, 0) — and non-zero payment/bookkeeping fields
    alone don't rescue it (_ca_section_computed). AGI may be negative
    (loss year); tax may not.

    A genuinely COMPUTED $0 IS passed through, with no confirmation asked
    for. California does not gate this the way the federal side does, and
    borrowing the federal gate here is what this shipped. R&TC §19136(c)(2)
    is a CALIFORNIA rule, not a conformity modification of §6654(e)(2): no
    addition to tax arises when the tax for EITHER the preceding year or the
    covered year, minus credits (the withholding credit included, estimated
    payments excluded), is under $500 ($250 MFS). It carries no citizenship,
    no residency and no 12-month condition — FTB 5805's own no-penalty list
    reads it for "either 2024 or 2025" and states neither. A prior-year
    `ca_total_tax` of $0 necessarily clears it: every component of that
    figure is non-negative, so tax-minus-credits cannot exceed $0.

    So a Californian whose prior Form 540 came out at $0 was told to pay 90%
    of the current year's CA tax because they could not confirm a US
    citizenship condition California never asks about. `_ca_section_computed`
    is the guard that actually matters here and it stays: it separates a
    computed $0 from an untouched template, which is the only way this figure
    can be wrong in the under-stating direction.

    §19136.3 is unaffected and still wins: `compute_ca_safe_harbor` takes
    the 90%-of-current branch at $1M/$500K-MFS CURRENT-year CA AGI whatever
    the prior year holds. (c)(2) may well still clear such a taxpayer — it is
    not one of the provisions §19136.3 reaches — but that is the OVER-stating
    direction, and `_ca_notes` hands the rule over rather than applying it
    unasked.
    """
    if prior is None:
        return None, None
    result = getattr(prior, "result", {}) or {}
    if not _ca_section_computed(result):
        return None, None
    ca = result.get("california_540", {}) or {}
    tax = _prior_numeric(ca.get("ca_total_tax"), allow_negative=False)
    return tax, _prior_numeric(ca.get("ca_agi"), allow_negative=True)


def _prior_tax_agi(completed_line8_tax=None, completed_line8_agi=None,
                   zero_liability_confirmed=False,
                   return_filed_12_months_confirmed=False):
    """Return an explicit Form 2210 line-8 pre-factor base and its paired AGI.

    Takes NO archive. The CA twin above (`_prior_ca_tax_agi`) still reads one
    and keeps its `prior` parameter; this one read the archive root's AGI until
    the pairing rule below removed the last use, and the parameter went with
    it. Two adjacent helpers whose signatures matched while their behaviour had
    become opposite is what that left: a reader debugging a DEGRADED-MODE
    report on a workspace holding a full `archive/{year-1}/` reasonably
    concluded from the signature that the archive was still an input here.

    Form 1040 line 24 cannot reconstruct this base: Form 2210's covered-year
    line-8 chart admits only enumerated other taxes and applies exclusions in
    addition to its refundable-credit treatment. The federal prior-year prong
    stays unresolved unless the planning input explicitly carries the
    completed composition.

    A base of exactly $0 zeroes the required annual payment, and there are
    TWO independent statutory routes to it. They are asked for separately
    because their preconditions do not overlap and neither implies the other:

    - Sec. 6654(e)(2) — no liability for the preceding year, that year a full
      12 months, and the taxpayer a US citizen or resident THROUGHOUT it. It
      does NOT require that a return was filed.
    - Sec. 6654(d)(1)(B)(ii) — the ORDINARY prior-year prong, at 100%/110% of
      a prior-year tax that happens to be $0. Its closing text withdraws the
      prong only when the preceding year was not a 12-month year or no return
      was filed for it; it carries NO citizenship or residency condition.

    Collapsing the two into one flag is what this shipped, and it cost the
    second: a taxpayer who filed a full 12-month prior-year return showing $0
    could not truthfully confirm the citizenship half, so the $0 was refused
    and the report demanded 90% of the current year — thousands, from someone
    the ordinary prong already covers. tax-safe-harbor SKILL.md's preconditions
    have listed them as separate tests since it shipped, so the engine-free
    skill and the engine disagreed on the same taxpayer.

    Unconfirmed on BOTH routes the $0 base is still refused rather than
    silently accepted: it would otherwise zero out the required annual payment
    on nothing more than a figure that happens to equal zero, which a short or
    part-year prior return produces often enough to be the costly default.

    `completed_line8_agi` must come from the SAME return `completed_line8_tax`
    was composed from (its amendment rule included, per SKILL.md's pairing
    rule). When the caller supplies an explicit tax base but no paired AGI,
    the archive root's AGI is NOT substituted: `tax-amend` overwrites
    `archive/{year}/result.json` with the amended return's figures, so it can
    silently pair an original-return tax base with a different, amended-return
    AGI. Missing AGI degrades to the already-modeled "prior AGI unknown" path
    (100% factor, flagged) rather than risking that mismatch.

    ABSENT and UNREADABLE are different answers, and only the first degrades.
    Both figures arrive from `projection_inputs.json`, which is hand-edited, so
    both are read at the PARSE boundary with the same coercion — `"162000"` and
    `"$162,000"` are the AGI the user supplied. Reading the AGI through the
    ARCHIVE sanitizer instead (bool/NaN/non-numeric all quietly to `None`) is
    what this shipped: a string AGI beside a string tax base was dropped, the
    110% factor was never selected, the required annual payment came out 10%
    low — the UNDERSTATING direction — and the report said "Prior-year AGI
    unavailable" about a figure sitting in the file. A present value that
    cannot be read is therefore a hard error naming the field, never a silent
    degrade: the taxpayer answered, and disbelieving the answer without saying
    so is the failure this whole prong is written to avoid.
    """
    prior_tax = (None if completed_line8_tax is None else
                 _nonneg_num(completed_line8_tax,
                             "prior_year_form_2210_tax_base"))
    if prior_tax == 0 and not (zero_liability_confirmed
                               or return_filed_12_months_confirmed):
        raise ProjectionError(
            "prior_year_form_2210_tax_base is $0. That zeroes the required "
            "annual payment under either of two independent rules, and "
            "neither is confirmed. Sec. 6654(e)(2) needs THREE things, and "
            "this $0 base is not one of them: a full 12-month prior year, "
            "NO LIABILITY for it, and US citizenship or residence "
            "throughout it. No liability means that year's Form 1040 "
            "TOTAL TAX line was $0, or that no return was required at "
            "all — never this base, which subtracts refundable credits "
            "and reaches $0 on a positive total tax. All three confirmed, "
            "set prior_year_zero_liability_confirmed to true in "
            "projection_inputs.json. Sec. 6654(d)(1)(B)(ii), the ordinary "
            "prior-year prong, needs only that the prior year was a full "
            "12-month year AND a return was filed for it, with no "
            "citizenship or residency condition — set "
            "prior_year_return_filed_12_months_confirmed to true instead. "
            "Either one is enough; leave prior_year_form_2210_tax_base null "
            "to run the prior-year prong unresolved.")
    if prior_tax is not None:
        # `_num`, not `_prior_numeric`: same parse boundary as the tax base
        # above, so the same hand-typed forms are accepted and a present but
        # unreadable value raises instead of degrading. Negative is allowed —
        # a prior-year loss year has a negative AGI and is below every
        # threshold, which is what the 100% factor already means.
        agi = (None if completed_line8_agi is None else
               _num(completed_line8_agi, "prior_year_form_2210_agi"))
        return prior_tax, agi
    # No explicit base -> the prong is unresolved, and `compute_safe_harbor`
    # takes `degraded = prior_tax is None` and never reads `prior_agi` on that
    # branch, so there is no AGI to return that anything could consume.
    return None, None


def _normalize_state_code(value: "str | None") -> "str | None":
    """Uppercase/strip to a real 2-letter USPS state code, else None. Shared by
    project_state_tax's resident-state backfill and the cache-vs-resident
    cross-check below so the two can't drift on what counts as a valid
    code.

    MEMBERSHIP, because the messages built on this already claimed it: both
    this module and `loss_harvest` refuse a malformed cache code with "not a
    valid 2-letter USPS code", and the test underneath was the SHAPE alone, so
    "ZZ" was reported as a valid code by a sentence promising otherwise — the
    lying caveat this repo ranks worse than a missing one. Tightening it moves
    every affected path in the SAFE direction: an unreal cache code becomes the
    loud malformed-code note instead of a code trusted for a comparison, and an
    unreal RESIDENT code becomes the already-modeled "resident unknown"
    degrade. It DEGRADES rather than raising, unlike `capabilities` on the same
    field: state modelling here is optional and already has a labelled
    fall-through, and a hard error over a field this module can proceed without
    would fail a whole projection on one bad character.
    """
    if isinstance(value, str):
        cleaned = value.strip().upper()
        if (len(cleaned) == 2 and cleaned.isalpha()
                and cleaned in _USPS_STATES):
            return cleaned
    return None


def _cache_state_code_present_but_invalid(state_cfg: dict) -> bool:
    """True iff `state_cfg` has a `"code"` KEY (checked via `in`, not
    `.get() is not None`) that does NOT normalize to a valid 2-letter code
    — this includes an explicit `"code": null` as well as a malformed
    string/number (e.g. "FLA", 123, "").

    Codex round-14 P2 (sharpened by round-15 P2): `_normalize_state_code`
    collapses ALL of "key absent", "key present as null", and "key present
    but malformed" to the same `None` — every consumer treats "cache code
    unknown" as "safe to backfill the resident's state and trust this
    cache's brackets/type," which is the right call ONLY for a genuinely
    ABSENT key (a legacy cache that predates the `state.code` field). The
    original round-14 fix checked `raw is not None`, which correctly caught
    "FLA" but still let an EXPLICIT `"code": null` through as if the key
    had never existed — a CA-bracket cache with `"code": null` beside an FL
    taxpayer would otherwise sail through every code-mismatch check (both
    sides need to be non-None to compare) and have its real CA brackets
    projected and LABELED as Florida tax, exactly like the "FLA" case.
    Checking `"code" in state_cfg` (key presence) instead of "value is not
    None" closes that gap: absent key -> False (legacy leniency preserved),
    key present with ANY non-normalizing value (null, malformed, wrong
    type) -> True (hard-reject, never backfill)."""
    if "code" not in state_cfg:
        return False
    return _normalize_state_code(state_cfg.get("code")) is None


def _resolve_state_no_income_tax(tax_law: dict, resident_code: "str | None",
                                  year: int):
    """Decide the no-income-tax flag, guarding against a STALE cache for a
    different state than the taxpayer's resident state (e.g. a leftover FL
    state.type == "none" cache left beside a CA profile after an address
    correction — Codex round-5 P1) AND against a cache saved for the wrong
    YEAR (Codex round-9 P2). Mirrors the /tax-compute skill-level
    cross-check (tax_law.state.code vs taxpayer_profile.address.state).

    The year check below is now also enforced EARLIER and HARDER by
    `build_report`'s `_validate_tax_law_year` call (Codex round-11 P1),
    which raises before this function — or `project_federal_tax`/
    `project_state_tax` — ever runs, so `build_report` callers never
    actually reach the year-mismatch branch here. It stays as defense in
    depth for any other/future caller of this function directly.

    Returns (no_income_tax, mismatch_note, code):
    - resident state unknown: flag False, no warning — can't verify the
      cache against anything, so this stays the safe "no baseline" skip
      note rather than a confident (and possibly wrong) claim (mirrors
      loss_harvest.state_no_income_tax's bias-direction rule).
    - resident known AND the cache's declared `tax_year` is missing,
      malformed, or does not equal `year`: flag False + a loud mismatch
      note, regardless of what the cache claims — this module reads
      `tax_law_{year}.json` directly in `_main` and never calls
      tax_law_fetcher.check_freshness (which gained an equivalent guard in
      round 8), so a mis-saved/misnamed cache — e.g. a `tax_law_2020.json`
      whose body still declares `"tax_year": 2021` — would otherwise reach
      the `type == "none"` branch below and claim "no state tax" for a
      year whose regime may have been entirely different (TN's Hall tax
      was still live in 2020, repealed only for TY2021+).
    - resident known AND year matches AND cache code known AND they
      differ: flag False + a loud mismatch note, regardless of what the
      mismatched cache claims — a cache for the wrong state can't be
      trusted either way.
    - resident known AND year matches AND (cache code matches OR cache
      code is absent): flag mirrors the cache's declared
      state.type == "none" — UNLESS `code` is known and `type: "none"`
      isn't a regime that code can legitimately carry for `year` (Codex
      round-10 P1): a same-year, code-matching CA cache with a corrupted
      `type: "none"` (hand-edited or a fetch bug) would otherwise pass
      every check above and make this module claim California has no
      individual income tax, silently dropping the CA liability/payment
      projection. `_none_type_allowed` (tax_law_fetcher's single source of
      truth for the code<->regime allowlist, also used by
      check_freshness — which this module never calls, since it reads the
      cache file directly) is the same cross-check /tax-compute's
      skill-level guard applies; a cache lacking a `code` field can't be
      checked against it and keeps degrading to the generic skip note
      (safe direction) rather than a confident no-tax claim.
    - `state.code` is PRESENT but does not normalize to a valid 2-letter
      code (Codex round-14 P2, e.g. "FLA"): flag False + a loud mismatch
      note, checked FIRST (before the resident-unknown branch, since this
      is a property of the cache itself and needs no resident to compare
      against) — `_normalize_state_code` collapses this to the same `None`
      an ABSENT code produces, which every other branch here treats as
      "safe to degrade quietly," but a present-and-corrupted code is
      affirmative evidence the cache itself is untrustworthy.
    """
    state_cfg = tax_law.get("state") or {}

    if _cache_state_code_present_but_invalid(state_cfg):
        mismatch = (f"state law cache has a malformed state.code "
                    f"({state_cfg.get('code')!r} — not a valid 2-letter "
                    f"USPS code); re-run /tax-setup; state projection "
                    f"skipped")
        return False, mismatch, None

    cache_code = _normalize_state_code(state_cfg.get("code"))
    cache_says_none = state_cfg.get("type") == "none"

    if resident_code is None:
        return False, None, None

    cache_tax_year = tax_law.get("tax_year")
    if isinstance(cache_tax_year, bool) or not isinstance(cache_tax_year, int) \
            or cache_tax_year != year:
        mismatch = (f"state law cache does not declare tax_year {year} "
                    f"(found {cache_tax_year!r}) — re-run /tax-setup; "
                    f"state projection skipped")
        return False, mismatch, None

    if cache_code is not None and cache_code != resident_code:
        mismatch = (f"state law cache is for {cache_code} but the taxpayer "
                    f"resides in {resident_code} — re-run /tax-setup; "
                    f"state projection skipped")
        return False, mismatch, None

    if cache_code is not None and cache_says_none \
            and not _none_type_allowed(cache_code, year):
        mismatch = (f"state law cache declares type \"none\" for "
                    f"{cache_code}, but {cache_code} is not a recognized "
                    f"no-income-tax state for tax year {year} (cache is "
                    f"corrupted or was hand-edited) — re-run /tax-setup; "
                    f"state projection skipped")
        return False, mismatch, None

    # INVERSE regime contradiction (Codex round-15 P1's twin here): a code
    # that IS a legitimate no-tax state for `year` but is typed something
    # OTHER than "none" while carrying real leftover brackets (e.g.
    # code: "FL", type: "graduated_income") is just as corrupted as the
    # opposite case above — without this, `no_income_tax` below would
    # silently resolve to False with NO mismatch note (cache_says_none is
    # False, so the affirmative branch never fires either), leaving the
    # renderer to show a generic "no baseline" skip instead of flagging the
    # actual data corruption. Gated on non-empty brackets — a merely
    # untyped-but-bracket-less cache isn't the dangerous case this guards.
    if cache_code is not None and not cache_says_none \
            and _none_type_allowed(cache_code, year) \
            and (state_cfg.get("brackets") or []):
        mismatch = (f"state law cache declares type "
                    f"{state_cfg.get('type')!r} for {cache_code}, but "
                    f"{cache_code} is a recognized no-income-tax state for "
                    f"tax year {year} (cache is corrupted or was "
                    f"hand-edited) — re-run /tax-setup; state projection "
                    f"skipped")
        return False, mismatch, None

    no_income_tax = cache_code is not None and cache_says_none
    code = cache_code if no_income_tax else None
    return no_income_tax, None, code


_NONRESIDENT_INCOME_WARNING = (
    "Another state's nonresident liability is not modeled — that state's "
    "tax and estimates are missing from this projection."
)


def _nonresident_income_flag(profile: dict) -> bool:
    """Advisory read of flags.has_nonresident_source_income: absent, None,
    or a non-dict/non-bool flags block all read as False. Deliberately NOT
    the capabilities gate's fail-closed rule (_REQUIRED_GATE_FLAGS hard-
    errors on an unanswered flag) — /tax-project is read-only/advisory and
    must never block a projection over an unrelated screening question the
    taxpayer hasn't answered yet; only an explicit `true` turns the warning
    on."""
    flags = profile.get("flags")
    if not isinstance(flags, dict):
        return False
    return flags.get("has_nonresident_source_income") is True


def _validate_tax_law_year(tax_law: dict, year: int) -> None:
    """Hard-stop guard (Codex round-11 P1): `tax_law`'s OWN declared
    `tax_year` must match the year this projection is running for, checked
    BEFORE `project_federal_tax`/`project_state_tax` consume the cache's
    brackets/deductions/thresholds.

    The round-9 cross-check lived only inside `_resolve_state_no_income_tax`,
    which runs at the END of `build_report` — by then `project_federal_tax`
    and `project_state_tax` had already used the (possibly wrong-year)
    cache, so a mismatch silently produced a wrong federal liability,
    safe-harbor target, voucher schedule, and W-4 recommendation while only
    a STATE-labeled `state_cache_mismatch` note appeared. This raises
    immediately instead, before either projection runs, mirroring the
    existing "No federal brackets for status... run /tax-setup" hard error
    a few lines into `project_federal_tax` — a wrong-year cache is the same
    class of unusable-cache problem as a missing one, just with data that
    happens to be present but wrong.
    """
    cache_tax_year = tax_law.get("tax_year")
    if isinstance(cache_tax_year, bool) or not isinstance(cache_tax_year, int) \
            or cache_tax_year != year:
        raise ProjectionError(
            f"tax_law_{year}.json does not declare tax_year {year} "
            f"(found {cache_tax_year!r}) — this cache cannot be trusted for "
            f"federal OR state projection (wrong brackets/deductions/"
            f"thresholds). Re-run /tax-setup to fetch a {year} cache."
        )


def build_report(inputs: dict, profile: dict, tax_law: dict, prior,
                 year: int, as_of: datetime.date,
                 today: "datetime.date | None" = None) -> ProjectionReport:
    """Build the report. `today` is the real date, NOT the as-of date.

    Optional because calendar notes and payment-lever availability need it;
    annualization remains a function of `as_of`. Omitting it omits the notes
    and keeps the as-of-derived paycheck count rather than guessing at the
    clock: the CLI already carries a `--today` override the tests drive.
    """
    _validate_tax_law_year(tax_law, year)
    pay_frequency = validate_pay_frequency(inputs.get("pay_frequency", "biweekly"))
    proj = annualize(inputs.get("ytd", {}), as_of, pay_frequency,
                     inputs.get("forecast_overrides", {}),
                     inputs.get("recurring_from_prior", {}),
                     inputs.get("estimated_payments_made", {}))
    proj = replace(
        proj,
        paychecks_remaining=paychecks_available_for_year(
            pay_frequency, year, today, proj.paychecks_remaining))
    # The same check one level out, because this `fs` reaches the state
    # projection and the safe harbors too and is read BEFORE the federal call
    # below would raise. Both refuse rather than one delegating to the other:
    # `project_federal_tax` is called directly as well, and a report whose
    # state half computed on a status the federal half rejected is the worse
    # failure of the two.
    fs = _canonical_status(profile.get("filing_status"), "projection")
    federal = project_federal_tax(proj, profile, tax_law, requested_year=year)
    address = profile.get("address")
    resident_state = address.get("state") if isinstance(address, dict) else None
    state = project_state_tax(proj, prior, tax_law, fs,
                              resident_state=resident_state)

    prior_tax, prior_agi = _prior_tax_agi(
        inputs.get("prior_year_form_2210_tax_base"),
        inputs.get("prior_year_form_2210_agi"),
        _confirmed_flag(inputs.get("prior_year_zero_liability_confirmed"),
                        "prior_year_zero_liability_confirmed"),
        _confirmed_flag(
            inputs.get("prior_year_return_filed_12_months_confirmed"),
            "prior_year_return_filed_12_months_confirmed"))
    safe_harbor = compute_safe_harbor(
        federal.total_tax, prior_tax, prior_agi, fs,
        withholding=federal.withholding, estimates_paid=federal.estimates_paid)

    vouchers = compute_estimated_vouchers(
        safe_harbor.shortfall, as_of, safe_harbor.estimates_paid)
    voucher_catchup_note = _voucher_catchup_note(
        _FEDERAL_CATCHUP, vouchers, safe_harbor, as_of)
    voucher_dates_note = _voucher_dates_note(
        _FEDERAL_CALENDAR, [vouchers], year, today)

    full_shortfall = max(0.0, federal.total_tax - federal.withholding - federal.estimates_paid)
    w4_penalty = compute_w4_4c(safe_harbor.shortfall, proj.paychecks_remaining)
    w4_zero = compute_w4_4c(full_shortfall, proj.paychecks_remaining)

    # CA is the only state whose voucher weighting (30/40/0/30) is modeled;
    # other states fall back to the informational note in the renderer.
    ca_safe_harbor = None
    ca_vouchers: list = []
    ca_voucher_catchup_note = None
    ca_voucher_dates_note = None
    if state is not None and state.code == "CA":
        prior_ca_tax, prior_ca_agi = _prior_ca_tax_agi(prior)
        ca_safe_harbor = compute_ca_safe_harbor(
            state.tax, prior_ca_tax, prior_ca_agi, state.agi, fs,
            withholding=state.withholding, estimates_paid=state.estimates_paid)
        ca_vouchers = compute_ca_estimated_vouchers(
            ca_safe_harbor.shortfall, as_of, ca_safe_harbor.estimates_paid)
        ca_voucher_catchup_note = _voucher_catchup_note(
            _CA_CATCHUP, ca_vouchers, ca_safe_harbor, as_of)
        ca_voucher_dates_note = _voucher_dates_note(
            _CA_CALENDAR, [ca_vouchers], year, today)

    resident_code = _normalize_state_code(resident_state)
    no_income_tax, cache_mismatch, no_income_tax_code = _resolve_state_no_income_tax(
        tax_law, resident_code, year)

    nonresident_income_warning = (
        _NONRESIDENT_INCOME_WARNING if _nonresident_income_flag(profile) else None
    )

    # Round-24 P2: name the Hall-tax skip instead of hiding it behind the
    # generic no-baseline message. Fires only when the state projection
    # actually skipped, the resolved state is TN, and no louder channel
    # (cache mismatch / legitimate post-repeal no-tax claim) already
    # explains the outcome — a post-repeal type-none TN cache takes the
    # no_income_tax path, and a TY2021+ graduated TN cache is the round-15
    # inverse contradiction and gets its mismatch note instead.
    state_unsupported_note = None
    _st_cfg = tax_law.get("state") or {}
    _tn_resolved = _normalize_state_code(_st_cfg.get("code")) or resident_code
    # Codex round-27 P2: the Hall label additionally requires the year to
    # actually BE pre-repeal — a TY2021+ TN resident with a legacy
    # code-less cache reaches here (resident backfill resolves TN, the
    # resolver degrades quietly on the absent code), and labeling THAT
    # skip "predates the Hall-tax repeal" asserts a false fact about a
    # post-repeal year. The post-repeal code-less case keeps the generic
    # skip message — its real problem is an unverifiable cache, not the
    # Hall regime.
    if (state is None and _tn_resolved == "TN"
            and year < _TN_HALL_REPEAL_YEAR
            and not no_income_tax and cache_mismatch is None):
        state_unsupported_note = (
            "pre-2021 TN Hall tax (interest & dividends ONLY) is not "
            "modeled — a bracket-based projection would fabricate tax on "
            "wages/SE/capital gains. If taxable interest/dividend income "
            "exceeded the filing threshold, handle the TN Hall return "
            "(Form INC 250) manually.")
    # A state whose cache declares a BENEFIT RECAPTURE skips for a different
    # reason, and the reader has to be told which. It is not that the brackets
    # are missing — they are there — but that the WITHHOLDING figure this
    # report would net against them belongs to more taxes than it computes.
    # Left as the generic "no baseline" skip, a New York taxpayer reads that a
    # projection was unavailable, when what happened is that the only available
    # one was wrong in the refund-inflating direction.
    elif (state is None and _st_cfg.get("tax_table_benefit_recapture")
            and not no_income_tax and cache_mismatch is None):
        state_unsupported_note = (
            f"{_tn_resolved or 'this state'} is not projected here, and the "
            "reason is not a missing cache. Its estimated-tax voucher covers "
            "the STATE tax together with local and commuter taxes this "
            "projection does not compute, while `ytd.state_withholding` on "
            "such a return is the withholding for ALL of them. Netting the "
            "two would show a refund that is not there. Run /tax-withholding, "
            "which prices the whole return against the same figure.")

    return ProjectionReport(
        year=year, as_of=as_of.strftime("%m-%d"), pay_frequency=pay_frequency,
        baseline_source=("projection_inputs.json completed Form 2210 line-8 base"
                         if prior_tax is not None else None),
        degraded=safe_harbor.degraded, has_prior_archive=prior is not None,
        proj=proj, federal=federal, state=state,
        safe_harbor=safe_harbor, vouchers=vouchers, w4_penalty=w4_penalty, w4_zero=w4_zero,
        ca_safe_harbor=ca_safe_harbor, ca_vouchers=ca_vouchers,
        voucher_catchup_note=voucher_catchup_note,
        ca_voucher_catchup_note=ca_voucher_catchup_note,
        voucher_dates_note=voucher_dates_note,
        ca_voucher_dates_note=ca_voucher_dates_note,
        w4_lever_note=_w4_lever_note(
            proj.paychecks_remaining, year, max(0.0, federal.balance),
            max(0.0, state.balance) if state is not None else 0.0,
            # Same discriminator the Caveats section uses one rule below: a CA
            # run has a modeled 540 harbor, every other taxed state does not.
            state_is_bracket_estimate=ca_safe_harbor is None),
        state_bracket_estimate_note=_state_bracket_estimate_note(
            state, ca_safe_harbor),
        state_no_income_tax=no_income_tax,
        no_income_tax_state_code=no_income_tax_code,
        state_cache_mismatch=cache_mismatch,
        nonresident_income_warning=nonresident_income_warning,
        state_unsupported_note=state_unsupported_note)


def _money(v) -> str:
    """Whole dollars, EXCEPT where that would print a non-zero amount as $0.

    In a tax table "$0" means nothing is owed, and the CA de minimis path
    prints genuinely-zero vouchers meaning exactly that — so rounding $0.01 to
    the same string makes a real obligation indistinguishable from its
    absence. It also produced the visible contradiction that surfaced this: a
    voucher schedule reading "| Q4 | 2027-01-15 | 100% | $0 |", a hundred
    percent of nothing. Reachable whenever a shortfall survives the $500 CA
    floor by cents (tax minus withholding of $500.03 leaves $0.03 to
    schedule), and the same holds for any sub-dollar figure anywhere in either
    report. Cents appear only in that window; every ordinary figure is
    unchanged.
    """
    if v is None:
        return "—"
    cents = round(abs(v), 2)
    body = (f"${cents:,.2f}" if 0 < cents < 0.995 else f"${abs(v):,.0f}")
    return f"({body})" if v < 0 else body


# The same §6654(f) gap on the OTHER year, and it reaches further: the
# current-year figure drives BOTH prongs and the §6654(e)(1) floor, where the
# prior-year one drives a single prong. `compute_form_2210` states this
# contract on its own argument — Form 2210 line 4, line 24 MINUS the part-IV
# refundable credits — and neither caller of `compute_safe_harbor` can meet it.
# The direction is one-way (line 4 <= line 24, and the §31 credit the floor
# subtracts is >= the withholding figure passed), so what over-states is the
# shortfall, which is the hard-error direction rather than the banner one.
# Gated on a shortfall actually being asserted: under the floor nothing is
# asked for, and netting cannot take a run OUT of the floor.
# Prose-formatted once from the same constants `prior_year_agi_threshold`
# reads, so the three "prior AGI unavailable" notes below (federal ×2 + CA)
# can never go stale independently of PRIOR_YEAR_AGI_THRESHOLD/_MFS the way a
# bare "$150,000/$75,000" literal in each would.
_PRIOR_AGI_THRESHOLD_TEXT = (
    f"${PRIOR_YEAR_AGI_THRESHOLD:,}/${PRIOR_YEAR_AGI_THRESHOLD_MFS:,}-MFS")

# Attached to the prior-year row wherever it is shown, and NOT gated on a
# confirmation flag. §6654(d)(1)(B)'s closing text — "Clause (ii) shall not
# apply if the preceding taxable year was not a taxable year of 12 months or
# if the individual did not file a return for such preceding taxable year" —
# is a precondition of the prong itself, not of a $0 base. The engine enforces
# the confirmation only at $0, where the prong would zero the requirement
# outright; a POSITIVE base is taken as supplied, because the flags default
# false in every template and refusing the overwhelmingly ordinary case is the
# gate that gets reasoned past.
#
# So the precondition is DISCLOSED rather than enforced here. That keeps it an
# always-true sentence about the figure beside it instead of a warning that
# fires on correct data, and it is the half a reader can act on: a taxpayer
# whose first filing year was short, or who never filed, is reading a target
# built on a prong they do not have, and 100%/110% of a small prior tax is far
# below the 90% they actually owe.
#
# NOT gated on a confirmation flag, but DOES stop at a $0 base — see
# `_prior_prong_precondition_applies` below, which owns both exclusions.
_PRIOR_PRONG_PRECONDITION_NOTE = (
    "- **The prior-year prong assumes a filed, full 12-month prior year.** "
    "§6654(d)(1)(B) withdraws it entirely if the preceding year was not a "
    "12-month year or no return was filed for it — a first short filing year "
    "or a year with no return leaves only the 90%-of-current test, and every "
    "safe-harbor figure here that came from the prior year would then be too "
    "LOW. This engine does not verify either condition for the POSITIVE "
    "prior-year tax it was given; confirm both before relying on the "
    "prior-year prong.")


def _prior_prong_precondition_applies(sh) -> bool:
    """True when `_PRIOR_PRONG_PRECONDITION_NOTE` is a TRUE sentence about the
    report it would be printed in. Shared by both renderers for the reason the
    note itself is: a suppression in only one of them is as much a drift as a
    caveat in only one.

    Two exclusions, and each makes the note a LIE rather than merely noise:

    - DEGRADED — no prior-year figure at all, so the sentence qualifies
      something that is not in the report.
    - A $0 base — reachable ONLY through a confirmation the user gave, and one
      of the two routes to it is §6654(e)(2), whose three conditions (a
      12-month prior year, no liability for it, citizenship or residency
      throughout) do NOT include filing a return. Form 2210's own instructions
      spell that out — the prior year need only have been 12 months "or would
      have been had you been required to file" — and someone under last year's
      filing threshold is the ordinary case, not an exotic one. Telling that
      taxpayer their $0 target "would then be too LOW" contradicts the correct
      result printed beside it, and the remedy the note points at is 90% of
      the current year: money §6654(e)(2) says they do not owe.
      `_prior_tax_agi` refuses an UNCONFIRMED $0, so `prior_tax == 0` here
      always means one of the two routes was affirmed and the precondition
      question has already been asked and answered.

    The §6654(d)(1)(B) closing text the note quotes is a condition of the
    ORDINARY prong, which is the only route to a POSITIVE base — §6654(e)(2)
    is a no-LIABILITY exception and cannot produce one. So a positive base is
    exactly the case the note is true of, and it is the case nothing verifies.
    """
    return not sh.degraded and sh.prior_tax != 0

_CURRENT_TAX_UNNETTED_NOTE = (
    "- **Current-year tax here is a line-24 TOTAL, NOT Form 2210 line 4.** "
    "§6654 measures the tax net of the part-IV credits other than §31, "
    "including the EIC, ACTC, refundable AOTC, net premium tax credit, fuel "
    "credit, the REFUNDABLE adoption credit (Form 8839 line 13 — refundable "
    "for tax years beginning after 2024), and the §1341 credit, and that "
    "netted figure is what "
    "BOTH tests above run on: the 90% prong, and the $1,000 de minimis of "
    "§6654(e)(1), which subtracts the whole §31 credit and so counts excess "
    "social security (Schedule 3 line 11) alongside withholding. This engine "
    "has neither adjustment: the projection treats the child tax credit as "
    "nonrefundable only, and result.json carries no field for the earned "
    "income credit or the additional child tax credit. Both gaps run the same "
    "way, so **the required annual payment and the shortfall above may be "
    "OVERSTATED** — and a year whose refundable credits bring the balance "
    "under $1,000 owes no penalty at any payment pace, however large the "
    "shortfall printed here (`/tax-safe-harbor` step 6 states that floor). "
    "Net those credits off the total tax, re-take 90% of it, and re-check the "
    "floor before paying against this figure. If this year's return claims no "
    "refundable credit at all, the figures above are already correct — but "
    "settle that against Form 2210's own line-3 enumeration for the covered "
    "year rather than against this sentence. Line 3 is where the set is "
    "defined, and it is not closed: the adoption credit named above joined it "
    "for tax years beginning after 2024.")

# Deliberately narrower than the current-year note below: CA conforms to
# §6654 through R&TC §19136, so the same netting question arises for the
# prior-year baseline too, but WHICH CA credits FTB 5805 nets against a
# PRIOR-year total is not a list this engine has verified against the FTB,
# and AGENTS.md §2 would need a live check before one were printed. Naming
# the gap is honest; naming an unverified list would be the lying caveat the
# repo ranks worse than a missing one.
_CA_PRIOR_TAX_UNNETTED_NOTE = (
    "- **Prior-year CA baseline is `ca_total_tax` as filed.** CA conforms to "
    "§6654 through R&TC §19136, and FTB 5805 nets certain CA credits against "
    "that prior-year total before it can anchor the safe-harbor prong — this "
    "engine does not net them. Check the prior-year figure against FTB "
    "5805's own instructions before relying on the CA prior-year prong "
    "(`/tax-safe-harbor` step 5).")

# R&TC §19136(c)(2) has TWO prongs and this engine can only test one of them.
# Verbatim: "...the tax imposed under Section 17041 or 17048 and the tax imposed
# under Section 17062 for the PRECEDING taxable year, minus the sum of any
# credits ... OR the tax computed ... upon the ESTIMATED INCOME FOR THE TAXABLE
# YEAR, minus the sum of any credits ..., is less than five hundred dollars".
# `compute_ca_safe_harbor` runs the second prong — current-year tax less
# withholding — and cannot run the first, which needs the PRIOR year's own
# credits and this engine carries only its total tax.
#
# The gap is one-directional and it is the bad direction: a taxpayer the first
# prong clears outright is shown a CA required annual payment, a shortfall and a
# voucher schedule, and may pay all of it. HANDED OVER rather than applied,
# because applying it turns on whether "credits ... provided by Part 10 or this
# part" reaches the §19002 withholding credit, and getting that wrong the other
# way CLEARS a penalty that attaches — under-stating what the taxpayer must pay,
# which is the failure this whole section exists to avoid. Naming the rule costs
# a bullet; deciding it unverified costs the answer.
#
# It fires OUTSIDE the de minimis, where the current-year prong already answered,
# and outside a $0 prior year, which the note above already clears by this same
# subdivision. The federal disclaimer is in it because §6654(e)(1) has no
# prior-year half at all — a reader who knows the federal floor would otherwise
# read the federal section above as evidence about this one.
# The prior-year prong of the SAME subdivision, in the subset where the figure
# this engine already holds settles it. `prior_tax` is Form 540 total tax as
# filed, which is an UPPER BOUND on §19136(c)(2)'s prior-year quantity: other
# taxes only add to the total, and the statute only ever SUBTRACTS credits from
# it. So a total under the floor proves the prong outright — whatever the
# credits were, and without settling whether the §19002 withholding credit is
# among the ones "provided by Part 10 or this part", which is the open question
# that kept the whole rule handed over rather than applied.
#
# The bound is ONE-DIRECTIONAL and that is why the two notes are not symmetric:
# under the floor proves no penalty, at or above it proves nothing either way,
# because the credits could still bring the statutory figure under.
_CA_PRIOR_YEAR_FLOOR_PROVED_NOTE = (
    "- **No CA underpayment penalty can arise for this year — the PRIOR-year "
    "floor is already met.** R&TC §19136(c)(2) imposes none when the preceding "
    "year's CA tax less credits, **or** the covered year's, is under $500 "
    "($250 MFS); FTB 5805 Part I lists both. Last year's Form 540 total tax is "
    "under that floor on its own, and credits only subtract from it, so the "
    "prong holds however those credits fall. The CA payment needed to avoid a "
    "penalty is $0 and the schedule below is not asking for one. This does NOT "
    "touch the CA tax itself, which is still due with the return.")

# The same proof at or above §19136.3, where the required annual payment above
# is 90% of the CURRENT year and a bare "$0 needed" would read as a bug against
# it. So this note explains the pair rather than restating the one: the
# limitation decides the REQUIREMENT, the floor decides the PENALTY, and they
# are different rules in different sections.
_CA_PRIOR_YEAR_FLOOR_AT_HIGH_INCOME_NOTE = (
    "- **No CA underpayment penalty can arise for this year — the PRIOR-year "
    "floor is already met — and the 90% figure above is not asking for one.** "
    "R&TC §19136.3 withdraws the prior-year PRONG at this AGI, which is why "
    "the required annual payment above is 90% of the current year rather than "
    "last year's tax. It reaches only that computation: §19136(c)(2) is a "
    "separate rule barring the addition to tax outright when the preceding "
    "year's CA tax less credits, **or** the covered year's, is under $500 "
    "($250 MFS), and FTB 5805's own instructions keep that order — the "
    "$1,000,000 rule says how to figure the penalty \"if you are subject to\" "
    "it. Last year's Form 540 total tax is under the floor on its own, and "
    "credits only subtract from it, so the prong holds however those credits "
    "fall. The CA payment needed to avoid a penalty is $0 and the schedule "
    "below is not asking for one. This does NOT touch the CA tax itself, "
    "which is still due with the return.")

# The third case of the same subdivision, and the one nothing covered: no
# prior-year CA figure at ALL. The two above read a KNOWN total — under the
# floor it is proved, at or above it the credits might still carry it under —
# and an absent one fell through both into silence. It is deliberately the
# LOUDEST of the family, because it is the only one printed beside a schedule
# that is still asking to be paid.
_CA_PRIOR_YEAR_FLOOR_UNKNOWN_NOTE = (
    "- **The prior-year half of the CA $500 floor is UNTESTED here, and it "
    "alone could zero the schedule below.** R&TC §19136(c)(2) imposes no CA "
    "underpayment penalty when the PRECEDING year's CA tax less credits, "
    "**or** the covered year's, is under $500 ($250 MFS) — FTB 5805 Part I "
    "lists both. This report has no prior-year CA figure at all, so only the "
    "covered-year half above was tested, and it did not clear. Read the TOTAL "
    "TAX line of the prior Form 540 before paying anything below: under the "
    "floor it settles the year on its own, because credits only subtract from "
    "that total and cannot carry it back over. The rule is separate from the "
    "prior-year PRONG and outlives it — R&TC §19136.3 withdraws the prong at "
    "$1,000,000 ($500,000 MFS) of current-year CA AGI and does not reach the "
    "floor — so it is worth asking at every income, including one where the "
    "row above reads 90% of the current year. §6654(e)(1) has no prior-year "
    "half, so the federal section is not evidence either way.")

# The SAME unresolved floor, printed where the schedule is all zeros. The note
# above is written against a live 540-ES column — "could zero the schedule
# below", "before paying anything below" — and both are false here: the annual
# requirement is met and nothing is outstanding. What is unsettled is whether a
# penalty ALREADY accrued on a late instalment, and the floor answers that
# question too, because §19136(c)(2) bars the addition to tax outright rather
# than adjusting an instalment. Two constants rather than one conditional
# sentence, for the reason the $0 pair above gives: a note describing the
# reader's own page gets read, and one describing a schedule they cannot see
# gets skipped — including the sentence in it that was true.
_CA_PRIOR_YEAR_FLOOR_UNKNOWN_TIMING_NOTE = (
    "- **The prior-year half of the CA $500 floor is UNTESTED here, and it "
    "alone could clear a penalty the payment PACE may already have caused.** "
    "The required annual payment above is met, so nothing is outstanding — "
    "but FTB 5805 tests each instalment cumulatively, and an estimate paid "
    "after a deadline does not cure the instalment it was late for. R&TC "
    "§19136(c)(2) imposes no CA underpayment penalty at all when the "
    "PRECEDING year's CA tax less credits, **or** the covered year's, is "
    "under $500 ($250 MFS) — FTB 5805 Part I lists both, and neither test "
    "asks WHEN anything was paid. This report has no prior-year CA figure at "
    "all, so only the covered-year half was tested, and it did not clear. "
    "Read the TOTAL TAX line of the prior Form 540 before working through any "
    "dated cumulative test: under the floor there is nothing left to test, "
    "because credits only subtract from that total and cannot carry it back "
    "over. The rule outlives the prior-year PRONG — R&TC §19136.3 withdraws "
    "the prong at $1,000,000 ($500,000 MFS) of current-year CA AGI and does "
    "not reach the floor — so it is worth asking at every income. "
    "§6654(e)(1) has no prior-year half, so the federal section is not "
    "evidence either way.")

_CA_EITHER_YEAR_FLOOR_NOTE = (
    "- **The CA $500 floor reads on EITHER year, and the prior-year half is "
    "not settled here.** R&TC §19136(c)(2) imposes no CA underpayment penalty "
    "when the PRECEDING year's CA tax less credits, **or** the covered year's, "
    "is under $500 ($250 MFS) — FTB 5805 Part I lists both. This engine tests "
    "the covered-year half (CA tax less withholding, above), and reads the "
    "prior year only as the Form 540 TOTAL, which is above the floor before "
    "any credits come off it. Those credits could still carry it under: if "
    "last year's Form 540 tax less its credits came to under $500, no CA "
    "penalty can arise this year whatever the schedule below says — check it "
    "before paying. §6654(e)(1) has no prior-year half, so the federal section "
    "is not evidence either way here.")

_CA_CURRENT_TAX_UNNETTED_NOTE = (
    "- **Current-year CA tax here is the computed Form 540 total.** The "
    "netting question the federal current-year note raises applies to the CA "
    "figures above too, and this engine does not net them either — so the CA "
    "required payment and shortfall may be OVERSTATED. Check the current-year "
    "figure against FTB 5805's own instructions before paying against it.")


def _de_minimis_margin(v: float) -> str:
    """The de-minimis margin, to the CENT.

    Everything else in these reports is whole dollars (`_money`), which is the
    right resolution for a figure someone acts on. This one is not: both floors
    are strict `<`, so the cents are what decide the cliff, and rounding a
    $0.40 margin to "$0" states the opposite of the fact — that the excuse is
    about to evaporate — in the one sentence written to convey it.
    """
    return f"${v:,.2f}"


@dataclass(frozen=True)
class _DeMinimisFloor:
    """A de-minimis floor's renderable identity, bound to its own figures.

    The status sentence existed twice, once per jurisdiction, differing only in
    the label and the statute — and the CA copy quoted "$500/$250-MFS" as a
    LITERAL where the federal one interpolated its constant, so raising
    `CA_ES_DE_MINIMIS` would have moved the arithmetic and left the sentence
    quoting the old floor. Both halves are built FROM the constants here, for
    the reason `_CatchupNote` below exists: a sentence with two renderers is
    one constant, not two copies kept in step by hand.
    """
    floor: str
    statute: str
    subject: str
    jurisdiction: str


_FEDERAL_FLOOR = _DeMinimisFloor(
    floor=f"${DE_MINIMIS_BALANCE:,}", statute="§6654(e)(1)",
    subject="tax", jurisdiction="federal")
_CA_FLOOR = _DeMinimisFloor(
    floor=f"${CA_ES_DE_MINIMIS:,}/${CA_ES_DE_MINIMIS_MFS:,}-MFS",
    statute="R&TC §19136; FTB 5805 Part I",
    subject="CA tax", jurisdiction="CA")


_CLIFF_NOTHING_ATTACHES = ("Past the floor the required annual payment is "
                           "still met on its own, so nothing attaches when it "
                           "lapses")

# The OTHER reason nothing attaches, and it needs its own sentence because
# `_CLIFF_NOTHING_ATTACHES` names a reason that is false here: the required
# annual payment is NOT met on its own in these runs, a separate exemption
# simply makes that irrelevant. A true tail under a false reason is the shape
# the `withholding < margin` case was already split out to avoid.
#
# Deliberately does not name the statute. This cell is shared with the federal
# floor, and the note that DOES name it is one file away: `_ca_notes` emits one
# of the four §19136(c)(2) prior-year notes — $0 or under-the-floor, crossed
# with below or at the §19136.3 limitation — in every run that reaches here,
# and each carries the citation, the $500/$250 figures and the warning that the
# tax itself is still due. A fifth copy is a fifth thing to drift.
_CLIFF_SECOND_EXEMPTION = (
    "A separate exemption clears the year on its own, independently of this "
    "floor and of the payment pace — see the note naming it below — so "
    "nothing attaches when the floor lapses, however the projection turns out")


def _prior_year_floor_applied(harbor) -> bool:
    """`CaSafeHarbor.prior_year_floor_applied` off a duck-typed harbor.

    `getattr` for the reason `jurisdiction` uses it two gates down: both
    callers are shared with the FEDERAL harbor, which carries no twin of this
    field on purpose (see `CaSafeHarbor.prior_tax_unnetted` for what a
    permanent False on the sibling class cost once), and the stubs these gates
    are tested with are not `CaSafeHarbor`s either. One helper rather than the
    same `getattr` string at each site, so the field can be renamed without
    leaving a silent False behind at whichever site was missed.
    """
    return getattr(harbor, "prior_year_floor_applied", False)


def _de_minimis_cell(floor: _DeMinimisFloor, harbor,
                     tax_is_actual: bool) -> str:
    """The de-minimis status cell, shared by both jurisdictions.

    Under either floor no penalty can arise regardless of the payment pace, so
    the penalty target reads covered — with the reason, since the zero-balance
    target may still show tax due and a bare "covered" against a visible
    balance owed reads as a bug in the report rather than as the statute.

    The TAIL must not assert that a penalty attaches once the floor lapses:
    that is false wherever the required annual payment is already met.
    `de_minimis_cliff_more_tax` and `de_minimis_cliff_less_withholding` decide
    it, and 0.0 in both means nothing attaches. They are read SEPARATELY so
    the cell names only the paths its own inputs can take — a single
    worse-of-the-two figure attributes every positive to withholding, and $500
    of tax against $0 withheld then announces "$500 less withholding and it
    attaches" against withholding that cannot fall below zero.

    `tax_is_actual` names WHICH side of the measurement is the uncertain one.
    /tax-project annualizes the tax, so the projection is what may be wrong.
    /tax-withholding reads the tax from result.json — its own Assumptions call
    it "the **actual computed** tax", and only the withholding pace is
    annualized — so telling that reader to watch for "more tax than projected"
    aims them at the one input in the report that cannot move.

    Named for the TAX, never for the report: the two invert. /tax-project is
    all projection and takes `tax_is_actual=False`; a caller reasoning "my
    report is a projection, so False" is right there by accident and wrong on
    a report whose tax is actual, where it selects the branch pointing at the
    number that cannot move.

    The `withholding < margin` case has no lapse at all: `margin` is
    `floor - (tax - withholding)`, so `withholding >= margin` is the same
    statement as `tax >= floor`, and below it no withholding pace reaches the
    floor. The "nothing attaches" tail cannot carry that case either, because
    its REASON — the required annual payment being met on its own — is false
    there.

    And a SECOND exemption outranks every branch here. R&TC §19136(c)(2) reads
    on either year, so a prior-year Form 540 total already under the floor
    clears the year whatever the current-year projection does — the cliff is
    then a warning about a number that decides nothing, aimed at a taxpayer
    who is provably safe. That is the class of gate this repo treats as a
    defect, not as harmless caution: one that fires on correct data is one its
    reader learns to read past, including on the runs where it was right.
    """
    m = _de_minimis_margin(harbor.de_minimis_margin)
    cell = (f"covered — {floor.subject} minus withholding is {m} under the "
            f"{floor.floor} de minimis ({floor.statute}): no "
            f"{floor.jurisdiction} underpayment penalty applies. ")
    # Before any cliff talk: a second exemption makes the whole cliff moot.
    # It is checked first rather than folded into the branches below because
    # every one of them, the `withholding < margin` case included, is a
    # statement about the CURRENT year's test, and none of them is the reason
    # this run is safe.
    if _prior_year_floor_applied(harbor):
        return cell + _CLIFF_SECOND_EXEMPTION
    more_tax = harbor.de_minimis_cliff_more_tax
    less_withholding = harbor.de_minimis_cliff_less_withholding
    if tax_is_actual:
        if harbor.withholding < harbor.de_minimis_margin:
            return cell + (f"The {floor.subject} here is the computed "
                           f"liability and is under the floor on its own, so "
                           f"no withholding pace lapses it")
        if less_withholding <= 0:
            return cell + _CLIFF_NOTHING_ATTACHES
        return cell + (f"That is a cliff on the WITHHOLDING pace — the "
                       f"{floor.subject} here is the computed liability and "
                       f"does not move, but {m} less withholding than the "
                       f"current pace projects and it attaches")
    if more_tax > 0 and less_withholding > 0:
        return cell + (f"That is a cliff on a PROJECTION — {m} more "
                       f"{floor.subject} than projected, or that much less "
                       f"withholding, and it attaches")
    if more_tax > 0:
        return cell + (f"That is a cliff on the {floor.subject} PROJECTION — "
                       f"{m} more {floor.subject} than projected and it "
                       f"attaches; withholding is not the side that moves it")
    if less_withholding > 0:
        return cell + (f"That is a cliff on the WITHHOLDING projection — {m} "
                       f"less withholding than projected and it attaches; the "
                       f"{floor.subject} is not the side that moves it")
    return cell + _CLIFF_NOTHING_ATTACHES


def _federal_status(sh: SafeHarbor, tax_is_actual: bool = False) -> str:
    """Status cell for the federal safe-harbor table."""
    if sh.de_minimis:
        return _de_minimis_cell(_FEDERAL_FLOOR, sh, tax_is_actual)
    return "covered" if sh.covered else "underpaid"


def _ca_status(csh: CaSafeHarbor, tax_is_actual: bool = False) -> str:
    """Status cell for the CA safe-harbor table.

    The prior-year floor needs its own reason for the same argument
    `_de_minimis_cell` opens with: a bare "covered" against a visible gap reads
    as a bug in the report rather than as the statute. That gap only became
    visible when the floor started applying at the §19136.3 limitation — up
    there `required_annual` stays at 90% of the CURRENT year, so the table can
    show $108,000 required against $30,000 paid and call it covered. Below the
    limitation the prong also shrinks the requirement, and there was nothing
    for a reader to reconcile.

    Deferred to the note rather than re-citing §19136(c)(2) here, for the
    reason `_CLIFF_SECOND_EXEMPTION` gives: `_ca_notes` prints one of the four
    prior-year notes in every run that reaches this branch, each with the
    citation and the $500/$250 figures, and a copy here is a copy to drift.
    """
    if csh.de_minimis:
        return _de_minimis_cell(_CA_FLOOR, csh, tax_is_actual)
    if _prior_year_floor_applied(csh):
        return ("covered — a separate exemption clears the year on its own, "
                "whatever the required annual payment above and the payments "
                "against it come to; see the note naming it below")
    return "covered" if csh.covered else "underpaid"


def _ca_notes(csh: CaSafeHarbor) -> list:
    """Caveat bullets shared by the /tax-project and /tax-withholding CA
    blocks (single source so the two reports can't drift)."""
    notes = []
    if csh.high_income_90_only:
        notes.append(
            "- **High-income limitation (R&TC §19136.3):** CA AGI is "
            "$1,000,000 or more ($500,000 MFS), so the prior-year safe "
            "harbor is NOT available — CA requires 90% of the current-year tax.")
    # A prior-year Form 540 total tax of exactly $0 is a statutory answer, not
    # a missing figure, and saying which rule produced the $0 target is what
    # keeps it from reading as a bug. Two notes, because §19136.3 splits what
    # has to be EXPLAINED and not whether the rule applies: below the
    # limitation the $0 is visible in the required payment itself, at or above
    # it the required payment is 90% of the current year and the $0 penalty
    # target sitting beside it needs the second rule named.
    if csh.prior_tax == 0 and not csh.high_income_90_only:
        notes.append(
            "- **Prior-year CA tax was $0 (R&TC §19136(c)(2)):** no CA "
            "underpayment penalty arises when either year's tax less credits "
            "is under $500 ($250 MFS), and a $0 prior year clears that "
            "outright. This is a California rule in its own right — unlike "
            "§6654(e)(2) it asks nothing about citizenship, residency or the "
            "length of the prior year. The required annual payment below is "
            "$0 for that reason, not for want of a baseline.")
    if csh.prior_tax == 0 and csh.high_income_90_only:
        notes.append(
            "- **A $0 prior-year CA tax is on file, so no CA underpayment "
            "penalty can arise for this year.** §19136.3 withdraws the "
            "prior-year prong at this AGI, which is why the required annual "
            "payment above is 90% of the current year. It reaches only that "
            "computation: R&TC §19136(c)(2) is a separate rule barring the "
            "addition to tax outright when either year's CA tax less credits "
            "is under $500 ($250 MFS), and a $0 prior year clears it however "
            "the credits fall. The CA payment needed to avoid a penalty is $0 "
            "and the schedule below is not asking for one. The CA tax itself "
            "is still due with the return.")
    if csh.prior_year_floor and not csh.high_income_90_only:
        notes.append(_CA_PRIOR_YEAR_FLOOR_PROVED_NOTE)
    elif csh.prior_year_floor:
        notes.append(_CA_PRIOR_YEAR_FLOOR_AT_HIGH_INCOME_NOTE)
    elif csh.prior_year_floor_unknown:
        # WHICH of the two: the note above is written against a live 540-ES
        # column, this one against an all-zero one carrying an accrual the
        # annual figure cannot show. `shortfall` is the same quantity
        # `compute_ca_safe_harbor` branched the flag on, and it is zeroed only
        # under `no_penalty` — false wherever this flag is true, an absent
        # prior tax being neither the floor nor the $0 case — so the two
        # spellings cannot disagree about which reader this is.
        notes.append(_CA_PRIOR_YEAR_FLOOR_UNKNOWN_NOTE if csh.shortfall > 0
                     else _CA_PRIOR_YEAR_FLOOR_UNKNOWN_TIMING_NOTE)
    elif not csh.de_minimis and csh.prior_tax:
        # `csh.prior_tax` truthy, not `!= 0`: None is the degraded case, where
        # there is no prior figure to reason from at all, and it read as a
        # live one under the old comparison.
        notes.append(_CA_EITHER_YEAR_FLOOR_NOTE)
    if csh.degraded:
        notes.append("- **Degraded:** no usable prior-year CA baseline — "
                     "using the 90%-of-current-year test only. Either "
                     "`archive/{year-1}/` is missing, or its `result.json` "
                     "carries no COMPUTED `california_540` section (an "
                     "all-zero template scaffold is not one). That "
                     "archive is the ONLY route to the CA prior-year prong: "
                     "unlike the federal base there is no "
                     "`projection_inputs.json` field for a hand-entered Form "
                     "540 figure, so run `/tax-archive` on the prior year — "
                     "and a prior year computed outside this toolkit cannot "
                     "supply the prong at all. The §19136(c)(2) floor is a "
                     "separate question, and one the same Form 540 still "
                     "answers by hand whatever this report can compute.")
    if csh.current_tax_unnetted:
        notes.append(_CA_CURRENT_TAX_UNNETTED_NOTE)
    if csh.prior_tax_unnetted:
        notes.append(_CA_PRIOR_TAX_UNNETTED_NOTE)
    if csh.prior_agi_missing:
        notes.append(
            "- **Prior-year CA AGI unavailable** — assumed at or below the "
            f"{_PRIOR_AGI_THRESHOLD_TEXT} threshold (100% factor); if prior CA "
            "AGI exceeded it, the 110% harbor applies and the required "
            "payment shown may be UNDERSTATED.")
    notes.append(
        "- Not modeled: farmers/fishermen (FTB 5805F — 66⅔% harbor, single "
        "Jan 15 installment; this 30/40/0/30 schedule does not apply to "
        "them) and the prior-year-harbor precondition of a filed 12-month "
        "prior-year CA return.")
    return notes


_ES_VOUCHER_CATCHUP_NOTE = (
    "This schedule targets the ANNUAL requirement from the as-of date "
    "forward, split evenly across the deadlines still open — Form 2210 "
    "instead tracks each installment cumulatively against 25% of the required "
    "annual payment, so with an earlier deadline already closed the earliest "
    "still-open one can need MORE than an even share, and an installment "
    "already missed keeps accruing penalty until it is paid. Re-check that "
    "earliest amount against the cumulative test (/tax-safe-harbor step 4), "
    "and pay catch-up amounts NOW rather than at the printed dates.")

# Appended to the note above ONLY where estimates were actually paid. It is a
# statement ABOUT those payments, and this gate fires on withholding alone:
# `_something_outstanding` reduces to (requirement - withholding) x weight when
# `estimates_paid` is 0, so a taxpayer who has made no estimated payment at all
# was being told that "the estimates behind this schedule arrived as an undated
# TOTAL" and sent to re-run a dated test with no dates and nothing to date. The
# sibling timing branch below already reads `estimates_paid > 0` for exactly
# this reason; this branch did not. The rest of the note is true either way —
# only the sentence about the estimates needed the gate, so only it moved.
_ES_ESTIMATES_UNDATED = (
    " Run that test with your actual payment DATES: the estimates behind this "
    "schedule arrived as an undated TOTAL, so what it credits to each "
    "installment here may not be what was timely for that installment.")

# The other half of the same simplification, and the one that has no figure to
# print. Estimated payments arrive here as a TOTAL: nothing in the input says
# WHEN they were made, so a schedule showing nothing left to pay proves only
# that the ANNUAL requirement is met now. §6654 measures each instalment
# separately and its penalty runs from that instalment's own due date, so a
# payment that landed after the deadline it is credited against here left a
# penalty already accruing — one no later payment undoes. Silence there is the
# under-stating direction, which is the banner case rather than the hard error,
# and the banner did not exist: the all-zero guard returned None and the
# pointer to the dated test went with it.
#
# TWO ledes, one body, because two different schedules reach this note and
# only one of them is settled. "Nothing further is outstanding against the
# ANNUAL requirement" is the ALL-ZERO schedule. The same note also fires where
# nothing is merely OVERDUE and the year's remaining vouchers are printed
# directly below it — a $20,000 requirement with $15,000 paid by July 1 prints
# two $2,500 instalments — and there the settled lede over-states what has
# been paid in the same breath as the body under-states what may have accrued.
# The body is the half that is true either way.
_ES_TIMING_BODY = (
    " Estimated payments are taken here as a TOTAL, with no dates, and §6654 "
    "measures each installment separately against 25% of the required annual "
    "payment. A payment made after the installment it is credited against "
    "here left a penalty accruing from that installment's due date, and no "
    "later payment undoes it. Re-run the cumulative test with your actual "
    "payment DATES (/tax-safe-harbor step 4) before concluding no penalty "
    "applies. Withholding needs no such check — §6654(g)(1) deems it paid in "
    "equal parts on each due date.")
_ES_VOUCHER_TIMING_SETTLED = (
    "Nothing further is outstanding against the ANNUAL requirement — but the "
    "installments have their own dates." + _ES_TIMING_BODY)
_ES_VOUCHER_TIMING_SCHEDULED = (
    "No installment already due is short — but the vouchers below are still "
    "unpaid, so the ANNUAL requirement is NOT met yet, and the installments "
    "have their own dates." + _ES_TIMING_BODY)


_CA_VOUCHER_CATCHUP_NOTE = (
    "This schedule targets the ANNUAL requirement from the as-of date "
    "forward — FTB 5805 tracks each installment cumulatively, at 30% / 70% "
    "/ 70% / 100% of the requirement AND of the year's withholding alike "
    "(Worksheet II line 1 and line 2). So once a deadline has closed the "
    "earliest still-open installment can need more than its printed share, "
    "and one already missed keeps accruing penalty until it is paid — so "
    "pay catch-up amounts NOW rather than at the printed dates. Re-check "
    "that earliest amount against the cumulative test "
    "(/tax-safe-harbor step 5).")

# The CA twin of the federal clause; see it for why it is gated.
_CA_ESTIMATES_UNDATED = (
    " Run that test with your actual payment DATES: the CA estimates behind "
    "this schedule arrived as an undated TOTAL, so what it credits to each "
    "installment here may not be what was timely for that installment.")

# The CA twin of the federal timing pair; see it for why both exist.
_CA_TIMING_BODY = (
    " CA estimated payments are taken here as a TOTAL, with no dates, and FTB "
    "5805 measures each installment cumulatively at 30% / 70% / 70% / 100%. A "
    "payment made after the installment it is credited against here left a "
    "penalty accruing from that installment's due date, and no later payment "
    "undoes it. Re-run the cumulative test with your actual payment DATES "
    "(/tax-safe-harbor step 5) before concluding no CA penalty applies. CA "
    "withholding needs no such check — it is weighted the same way the "
    "requirement is, on both lines of Worksheet II.")
_CA_VOUCHER_TIMING_SETTLED = (
    "Nothing further is outstanding against the CA ANNUAL requirement — but "
    "the installments have their own dates." + _CA_TIMING_BODY)
_CA_VOUCHER_TIMING_SCHEDULED = (
    "No CA installment already due is short — but the vouchers below are "
    "still unpaid, so the CA ANNUAL requirement is NOT met yet, and the "
    "installments have their own dates." + _CA_TIMING_BODY)


def _federal_instalment_fraction(n: int) -> float:
    """Fraction of the year at federal installment n — 25/50/75/100.

    Both sides: Form 2210 line 9 requires a quarter of the required annual
    payment per column, and §6654(g)(1) deems a quarter of the year's
    withholding paid on each due date.
    """
    return n / len(ES_DUE_DATES)


def _ca_instalment_fraction(n: int) -> float:
    """Fraction of the year at CA installment n — 30/70/70/100.

    Both sides here too, which is the counter-intuitive half. FTB 5805
    Worksheet II line 1 takes the required annual payment at 30/40/0/30
    (R&TC §19136.1), and line 2 takes Part II line 3 — the year's
    WITHHOLDING — at that same 30/40/0/30, not in equal quarters. Part I
    question 3 is the exception and it is an election: it exists for a
    taxpayer who can show the actual uneven amounts and dates, and asking it
    at all presumes the default is even withholding allocated on these
    weights.

    Running the weights against quarters instead is what a revision of
    `_voucher_catchup_note` did, on the theory that the two sides diverge: it
    called a $5,000 requirement with $4,189 of CA withholding $452.75 short at
    April 15, where the schedule's own $243.30 — 30% x ($5,000 - $4,189) — is
    exactly right and nothing is owed. That is the OVER-stating direction, and
    this is the one place in the module that works it through.
    """
    return sum(w for q, _, w in CA_ES_SCHEDULE if q <= n)


@dataclass(frozen=True)
class _CatchupNote:
    """A catch-up note bound to the instalment fraction that decides it.

    Binding a note's text to the instalment fraction that decides it is the
    whole of what this class does. Passed loose, the federal note with
    `_ca_instalment_fraction` type-checks and returns a confident wrong
    verdict — 30/40/0/30 weights read as four equal quarters.

    The `harbor` argument is bound the same way, by LABEL rather than by type.
    `_voucher_catchup_note(_FEDERAL_CATCHUP, ca_vouchers, ca_safe_harbor,
    as_of)` used to type-check and return a confident wrong verdict of its
    own — 30/40/0/30 CA figures run against four equal federal quarters, which
    is the OVER-warning direction `_ca_instalment_fraction` exists to prevent.
    An isinstance check would have bought that guarantee by breaking the seam
    that makes this gate testable, because the gate is duck-typed over the
    fields the two harbors share and the tests feed it stubs. A `jurisdiction`
    string on both real harbors costs nothing and is checked with `getattr`, so
    a stub that declares none still runs and a REAL harbor cannot be paired
    with the wrong calendar. The seam stays open; the mispairing does not.

    The two timing texts ride along for the same reason `text` does: they are
    the notes for the OTHER outcome of the same gate, they name their own
    jurisdiction's statute and step, and nothing but this pairing keeps the CA
    schedule from being explained in terms of four equal federal installments.
    Which of the two the gate picks is decided by the SCHEDULE — see them.

    `estimates_undated` rides along on the same argument and is the half of
    `text` that is not true unconditionally: it is a claim about estimated
    payments, and the gate that prints `text` fires on withholding alone. It
    is a separate field rather than a second sentence so that the condition
    lives in the code that knows the answer instead of in the prose.
    """
    text: str
    estimates_undated: str
    timing_settled: str
    timing_scheduled: str
    instalment_fraction: "Callable[[int], float]"
    jurisdiction: str


_FEDERAL_CATCHUP = _CatchupNote(_ES_VOUCHER_CATCHUP_NOTE,
                                _ES_ESTIMATES_UNDATED,
                                _ES_VOUCHER_TIMING_SETTLED,
                                _ES_VOUCHER_TIMING_SCHEDULED,
                                _federal_instalment_fraction,
                                "federal")
# NEW YORK's, and it reuses the FEDERAL instalment fraction rather than
# defining a third. That is a reuse of the same QUESTION and not merely of the
# same arithmetic: `_federal_instalment_fraction` answers "what share of the
# requirement was due by instalment n under 25/50/75/100 weights", and Tax Law
# §685(c)'s instalments are four equal quarters on the same statutory dates.
# California's 30/70/70/100 is the one that differs, which is why the binding
# in `_CatchupNote` exists at all.
_NY_VOUCHER_CATCHUP_NOTE = (
    "This schedule targets the ANNUAL requirement from the as-of date "
    "forward, split evenly across the deadlines still open — Form IT-2105.9 "
    "instead tracks each installment cumulatively against 25% of the required "
    "annual payment, so with an earlier deadline already closed the earliest "
    "still-open one can need MORE than an even share, and an installment "
    "already missed keeps accruing until it is paid. Pay catch-up amounts NOW "
    "rather than at the printed dates. Remember the voucher covers the State "
    "tax, the New York City tax, the Yonkers surcharge and the MCTMT "
    "together.")
_NY_CATCHUP = _CatchupNote(_NY_VOUCHER_CATCHUP_NOTE,
                           _ES_ESTIMATES_UNDATED,
                           _ES_VOUCHER_TIMING_SETTLED,
                           _ES_VOUCHER_TIMING_SCHEDULED,
                           _federal_instalment_fraction,
                           "NY")
_CA_CATCHUP = _CatchupNote(_CA_VOUCHER_CATCHUP_NOTE,
                           _CA_ESTIMATES_UNDATED,
                           _CA_VOUCHER_TIMING_SETTLED,
                           _CA_VOUCHER_TIMING_SCHEDULED,
                           _ca_instalment_fraction,
                           "CA")


def _voucher_catchup_note(catchup: _CatchupNote, vouchers, harbor,
                          as_of: datetime.date) -> "str | None":
    """The note this schedule needs — catch-up, timing, or none.

    Returns the TEXT, not a boolean, so the one place deciding the note is the
    one place naming it: it reached /tax-project's markdown and not its
    `--json`, and both consuming skills read `--json` first.

    `harbor` is a SafeHarbor or a CaSafeHarbor, duck-typed over the four
    fields they share, with the jurisdiction checked by label — see
    `_CatchupNote`.

    The note claims the earliest still-open deadline can need MORE than the
    share printed beside it, and that a missed instalment accrues until PAID.
    Both are the cumulative test, so it is RUN here rather than proxied by
    "a deadline closed and something is owed" — that proxy erred BOTH ways,
    and over-warning is the hard-error direction: a $20,000 harbor with
    $15,000 paid by July 1 owes nothing at September 15, and the proxy still
    demanded a catch-up payment because the remaining vouchers were non-zero.

    ONE fraction runs both sides, in both jurisdictions — hence a single
    callable rather than a required one and a credited one. With one, the
    algebra collapses to (requirement - withholding) x weight, so a taxpayer
    paid only by withholding cannot be short at all. WHY both sides carry the
    same weights is on `_federal_instalment_fraction` /
    `_ca_instalment_fraction`, where the weights are applied.

    The all-zero guard is what carries both de-minimis floors (§6654(e)(1),
    R&TC §19136): under either, no penalty can arise at any pace, the harbor
    zeroes its shortfall, and the note goes quiet without knowing which
    statute excused it — the one case where quiet is the whole answer.

    ONE test decides it: what is outstanding at the LATEST PASSED instalment.
    Testing the earliest STILL-OPEN instalment against its printed share as
    well looks necessary — after September 15 a $20,000 CA schedule prints
    $20,000 at January 15, exactly what January asks, while 70% fell due in
    June (the federal schedule hides 75% the same way) — but that second test
    is DEAD. Writing X for requirement - withholding and E for estimates, the
    open side asks X * fraction(n) - E > the printed share, and federally that
    share is (X - E)/(5 - n): at n=1 it reduces to E < 0, at n=4 to
    X - E > X - E, and at n=2/n=3 to X/4 > E and X/2 > E — which ARE
    `outstanding_at(n-1) > 0`. The CA weights collapse the same way, and a
    120,000-case sweep over both schedules and nine as-of dates found no case
    where the open side fired alone. The January case is caught HERE, by June
    being a passed instalment with $14,000 outstanding. Delete that and
    nothing else in the module reports it.

    LATEST passed, not worst: money already in cures an earlier instalment
    going forward, so an instalment missed in June and covered by September is
    behind a penalty already fixed, and directing a payment for it would
    over-state.

    What this cannot see is WHEN the estimates went in — the engine takes a
    total, not dates — so a payment made after the instalment it is credited
    against here leaves the note silent where the dated test would fire. Hence
    the pointer to that test instead of a figure of its own.

    That pointer is appended only where estimates were actually paid. The gate
    fires on withholding alone (at `estimates_paid` 0 the test reduces to
    (requirement - withholding) x weight), so it told taxpayers who had made
    no estimated payment that "the estimates behind this schedule arrived as
    an undated TOTAL" and sent them to a dated test with nothing to date. A
    caveat false of the reader it reaches is one they learn to skip.

    "Nothing outstanding" returns a timing note rather than None whenever
    estimates were paid, because nothing-to-pay-now is not nothing-was-late: a
    $20,000 requirement, no withholding and a single $20,000 estimate made on
    September 30 zeroes every voucher while April, June and September all ran
    short. Gated on `estimates_paid` because withholding cannot produce that
    case — §6654(g)(1) and FTB 5805 Worksheet II line 2 both spread it across
    the instalments — and gated OUT under ANY exemption that bars the penalty
    regardless of the payment pace, where a timing caveat would be a claim
    about nothing. Two of those exist on the CA side and only one was tested:
    the current-year de minimis, and R&TC §19136(c)(2)'s prior-year prong,
    which is measured on a CLOSED year and so cannot turn on when this year's
    estimates arrived. Its taxpayers were being sent to re-run the dated
    cumulative test "before concluding no CA penalty applies", against a
    subdivision that had already concluded it.

    WHICH timing note is the schedule's own answer. `timing_settled` claims
    the ANNUAL requirement is met, true only of an all-zero schedule; on one
    still printing instalments that over-states what has been paid, so
    `timing_scheduled` says the narrower true thing — nothing already due is
    short.

    And nothing at all before the first deadline passes: every payment made by
    then is timely for every instalment, so a caveat there warns about a
    window that has not opened. A gate that fires on correct data is the one
    taxpayers learn to read past.
    """
    # A harbor that declares its jurisdiction must agree with the note's.
    # `getattr` rather than an attribute read: a stub without the field is the
    # testing seam and still runs, while both REAL harbors carry one, so the
    # `_FEDERAL_CATCHUP`-with-a-CaSafeHarbor pairing that type-checks cannot
    # reach the arithmetic below and silently answer on the wrong weights.
    harbor_jurisdiction = getattr(harbor, "jurisdiction", None)
    if (harbor_jurisdiction is not None
            and harbor_jurisdiction != catchup.jurisdiction):
        raise ProjectionError(
            f"catch-up note for {catchup.jurisdiction} was paired with a "
            f"{harbor_jurisdiction} safe harbor — the instalment weights "
            "differ (25/50/75/100 federal, 30/70/70/100 CA), so this would "
            "report a schedule short where nothing is owed")
    # Passage is a calendar fact, not a property of whichever rows survived
    # schedule filtering. In particular, April 15, 2023 was followed by a
    # weekend and DC Emancipation Day, so Q1 remained timely through April 18.
    # Inferring "passed" from a Q2 first row declared Q1 late during that
    # §7503 window. Both federal and CA schedules use these four dates.
    #
    # The FEDERAL roll deliberately, for BOTH jurisdictions. California adopts
    # the federal holiday set (R&TC §18410), so for the only state schedule
    # this module builds the two rules agree date for date and nothing turns on
    # the choice today. It is the federal one all the same, because a state
    # OUTSIDE the conforming set keeps the weekend-only roll and its deadline can
    # fall a day EARLIER: taking the federal roll treats that quarter as still
    # open for the extra day and stays silent where it might have warned. Silence is the
    # right error here: this note's whole job is to say a date is behind us,
    # and a false "you missed it" is the alarming direction. The VOUCHER date
    # is the opposite case and takes `_state_observed_due` — there a day late
    # is a payment late.
    passed_quarters = [
        q for q, (month, day) in ES_DUE_DATES
        if _observed_due(datetime.date(
            as_of.year + (1 if q == 4 else 0), month, day)) < as_of
    ]
    if not passed_quarters:
        return None
    last_passed_quarter = max(passed_quarters)
    scheduled = any(v.amount > 0 for v in vouchers)
    if scheduled and _something_outstanding(
            catchup, harbor, last_passed_quarter):
        if harbor.estimates_paid > 0:
            return catchup.text + catchup.estimates_undated
        return catchup.text
    # Withholding alone meeting the required annual payment closes off the
    # case this note warns about: §6654(g)(1)/FTB 5805 Worksheet II line 2
    # both deem withholding paid ratably at every deadline, so once it alone
    # covers `required_annual` no estimate's timing — dated or not — can ever
    # create an underpaid instalment. Without this gate a taxpayer fully
    # covered by withholding who also happened to make a small estimate (e.g.
    # $12,000 withholding against a $10,000 requirement, plus a $1,000
    # estimate) was told not to conclude no penalty applies, which is false.
    if (harbor.estimates_paid > 0
            and not (harbor.de_minimis or _prior_year_floor_applied(harbor))
            and harbor.withholding < harbor.required_annual):
        return (catchup.timing_scheduled if scheduled
                else catchup.timing_settled)
    return None


def _something_outstanding(catchup: _CatchupNote, harbor,
                           last_passed_quarter: int) -> bool:
    """The cumulative test at the LATEST PASSED instalment — see the caller.

    Called only where an OBSERVED deadline has passed. The caller supplies the
    latest such quarter directly rather than inferring it from the first row
    left in a schedule filtered against statutory dates.
    """
    def outstanding_at(m: int) -> float:
        """Cumulative requirement at instalment m, net of what counts by then.

        Requirement and withholding carry the same weight, so they net before
        it is applied; an estimated payment does not, crediting in full on the
        date it was made.
        """
        return round((harbor.required_annual - harbor.withholding)
                     * catchup.instalment_fraction(m) - harbor.estimates_paid, 2)

    return outstanding_at(last_passed_quarter) > 0


def _observed_due(statutory: datetime.date) -> datetime.date:
    """A statutory deadline as the date it is actually OBSERVED (§7503).

    Voucher rows store this observed date. A bare statutory date landing on a
    weekend or DC legal holiday can be up to four days earlier and would make
    the JSON/Markdown table contradict the gate that correctly keeps the
    installment open. TY2022's Q4 was due 2023-01-17, not January 15.

    FEDERAL only. The state twin below is a different rule, and the argument
    that once justified serving both from here — "where the two diverge this
    rolls LATE, which yields silence rather than a false 'already missed'" —
    holds for the behind-us NOTE and is exactly backwards for a voucher: a
    printed date one day late is a payment one day late.
    """
    return next_business_day(statutory)


def _ny_observed_due(statutory: datetime.date) -> datetime.date:
    """A NEW YORK IT-2105 deadline as observed.

    `irs_dates.next_business_day_state` rolls weekends only for New York,
    because NY is not in the conforming set — nobody has read General
    Construction Law §§24 and 25-a against the Department's own published due
    dates here, and the set is for states whose law HAS been read. That is the
    safe direction for a payment date: one published earlier than the statute
    allows can never make a payment late, while one published later can.

    It is therefore possible for a printed IT-2105 date to sit a day or two
    before the date New York actually observes. `_NY_CALENDAR` says so on the
    schedule rather than leaving the reader to discover it.
    """
    return next_business_day_state(statutory, "NY")


def compute_ny_estimated_vouchers(shortfall: float, as_of: datetime.date,
                                  estimates_paid: float = 0.0) -> list:
    """IT-2105 voucher schedule — FOUR EQUAL QUARTERS, New York's own dates.

    The same statutory dates as the federal 1040-ES and the same even split,
    so this is the federal function with New York's rollover rule rather than
    a third copy of the splitting logic. It is emphatically NOT the California
    schedule: 30/40/0/30 with no September instalment would tell a New York
    filer to skip a payment they owe.

    ONE VOUCHER COVERS THE STATE, THE CITY, YONKERS AND THE MCTMT together,
    which is why the liability this is split from is IT-201 line 60 and not
    line 46.
    """
    return compute_estimated_vouchers(shortfall, as_of, estimates_paid,
                                      observed=_ny_observed_due)


def _state_observed_due(statutory: datetime.date) -> datetime.date:
    """A CALIFORNIA 540-ES deadline as observed (see `irs_dates`).

    Every caller of this helper is a CA 540-ES date, so the state code is
    fixed here rather than threaded through them. It is passed explicitly all
    the same: `next_business_day_state` REQUIRES one, because the rollover
    rule is a fact about a jurisdiction and the bug it now closes was a single
    rule standing in for every state at once.

    CA takes the FEDERAL holiday set. R&TC §18410 adopts it by reference and
    FTB's own 540-ES instructions apply it by name — TY2023's quarters are
    April 18 2023, June 15 2023, September 15 2023 and January 16 2024, the
    first rolled past observed DC Emancipation Day and the last past MLK Day.
    This helper and its federal twin therefore agree on CA dates today; they
    stay separate because the SET a state adopts is the state's own choice,
    and a state outside the conforming set still rolls weekends only.
    """
    return next_business_day_state(statutory, "CA")


@dataclass(frozen=True)
class _VoucherCalendar:
    """A voucher schedule's jurisdiction, for the notes that read a CALENDAR.

    Separate from `_CatchupNote` rather than folded into it, because the two
    notes answer different questions about the same table: that one is about
    the AMOUNT printed on a row, this one about whether the DATE beside it is
    still ahead. They are independently true — a schedule can be correctly
    split and entirely obsolete — so one object carrying both would make each
    reachable only through the other's gate, and the gate that matters here
    (`_voucher_dates_note`) reads no safe harbor at all.
    """
    form: str        # "1040-ES" / "540-ES", the instrument the dates belong to
    accrual: str     # the authority that runs, and closes, the accrual period
    bill: str        # where a fixed penalty then arrives
    step: str        # the /tax-safe-harbor step carrying the cumulative test
    # Appended to whichever note this calendar produces, when the
    # balance-due deadline those notes print is not this jurisdiction's own.
    # `_return_due_date` is FEDERAL: it reads an IRS-wide postponement table
    # with no FTB source, so on the CA side it is an assumption of conformity,
    # not a looked-up state date. CA normally does conform, which is why the
    # date is still shown — but a federal-only (or CA-only) postponement makes
    # it wrong, and a date presented without that qualification reads as
    # looked up. Empty for the jurisdiction the lookup actually covers.
    due_date_caveat: str = ""
    # How a BARE statutory date in this jurisdiction rolls to the day it is
    # actually observed: §7503's DC-legal-holiday rule federally, weekends
    # only for a state. Carried on the calendar rather than chosen inside
    # `_voucher_dates_note`, because that function's entire notion of
    # jurisdiction IS this object — with the federal rule hardcoded there it
    # rolled California's already-state-rolled dates a second time, past a
    # holiday the FTB does not keep. See the comment at its use.
    observed_due: Callable[[datetime.date], datetime.date] = _observed_due


_FEDERAL_CALENDAR = _VoucherCalendar(
    form="1040-ES", accrual="§6654(b)(2)",
    bill="on the IRS bill, or on Form 2210 filed with the return",
    step="step 4")
_CA_CALENDAR = _VoucherCalendar(
    form="540-ES", accrual="FTB 5805",
    bill="on the FTB bill, or on Form FTB 5805 filed with the return",
    step="step 5",
    observed_due=_state_observed_due,
    due_date_caveat=(
        " The balance-due date named above is the FEDERAL one, shown on the "
        "assumption that California conforms for this year — this engine has "
        "no FTB deadline source. Confirm the Form 540 date with the FTB "
        "before relying on it in any year either deadline was postponed."))


_NY_CALENDAR = _VoucherCalendar(
    form="IT-2105", accrual="Tax Law §685(c)",
    bill="on the Department's bill, or on Form IT-2105.9 filed with the return",
    step="step 4",
    observed_due=_ny_observed_due,
    due_date_caveat=(
        " The balance-due date named above is the FEDERAL one, shown on the "
        "assumption that New York conforms for this year — this engine has no "
        "Department deadline source. The instalment dates beside it roll "
        "WEEKENDS ONLY, because New York's own holiday rule (General "
        "Construction Law §§24 and 25-a) has not been read here: a date shown "
        "may be a day or two before the one New York observes, which is the "
        "safe direction for a payment and the wrong one for a missed-deadline "
        "warning. Confirm both with the Department before relying on them."))

def accrual_period_closed(year: int, today: "datetime.date | None") -> bool:
    """Has §6654(b)(2)'s accrual period for `year` closed as of `today`?

    §6654(b)(2) runs each instalment's underpayment only to the earlier of the
    day it is PAID and the 15th day of the fourth month after the year ends —
    April 15 following it, which an extension does not move. On that day the
    statutory endpoint has been reached: paying then cannot shorten the period,
    so the year's voucher schedule stops being an action. The calendar-note caller applies
    the same fixed April 15 cutoff to the modeled FTB 5805 schedule.

    `today is None` reads as OPEN. Every caller reaches this from a report
    built without a date (`build_report`'s default), and a report that cannot
    know the date must not assert the stronger, more alarming half.
    """
    if today is None:
        return False
    # §6654(b)(2)'s endpoint is the statutory April 15. Section 7503 rolls
    # installment deadlines, not the date through which underpayment accrues.
    return today >= datetime.date(year + 1, 4, 15)


_PASSED_VOUCHER_NOTE = (
    "> **⚠ {n} printed {form} date{s} already passed** ({dates}; today is "
    "{today}). An amount shown against a passed date is to pay NOW, not on the "
    "date beside it — that instalment's underpayment keeps accruing until it "
    "is PAID ({accrual}), and no later instalment cures it. {columns} "
    "`/tax-safe-harbor` {step} has the cumulative test that reprices "
    "what each passed instalment actually needed."
)

# The other side of the same date comparison, and the one that inverts the
# advice rather than sharpening it. It leads with the override because the
# catch-up note printed directly below still says an unpaid instalment keeps
# accruing — true up to this day and false after it, and leaving the two to be
# reconciled by the reader is how the stronger, wrong claim wins.
#
# "on the STATUTORY calendar" in the header is not padding. The header is the
# one line a summarising agent lifts whole, and every date this function holds
# is a statutory one: `passed_dates` normalises per §7503 and
# `accrual_period_closed` compares against a fixed April 15. Naming the
# calendar is what keeps the header from asserting something about the OTHER
# one — the §7508A postponement the caveat below says this engine cannot see.
_CLOSED_VOUCHER_NOTE = (
    "> **⚠ This {form} schedule is obsolete on the STATUTORY calendar — "
    "TY{year}'s accrual period closed on {cutoff}.** Read this BEFORE any "
    "schedule note below it: where one says an unpaid instalment keeps "
    "accruing, that stopped on {cutoff}. "
    "{accrual} runs each instalment's underpayment only to the earlier of the "
    "day it is PAID and its governing accrual cutoff — the STATUTORY {cutoff} "
    "for TY{year}, which §7503 does not roll and an extension does not move, "
    "even in a year whose FILING deadline was itself postponed past it. "
    "Today is {today}, at or past it, so any TY{year} underpayment penalty "
    "is FIXED and no payment made now reduces it by a cent.{postponed} Where "
    "no such postponement reached one of them: Do NOT send a {form} voucher "
    "for TY{year}, because its instalments belong to dates that are all "
    "behind us. Where one did, that instalment is still OPEN — the voucher "
    "is still how it is paid, on the postponed date. Any tax still owed "
    "beyond the instalments belongs to the BALANCE-DUE target for TY{year} "
    "— pay it with the return, or as a balance-due "
    "payment for that year. Have any penalty determined {bill}; this "
    "schedule does not by itself establish that an exception or "
    "annualized-income method is unavailable. The accrual cutoff above "
    "({cutoff}) and the balance-due deadline below are two DIFFERENT dates "
    "when TY{year}'s filing deadline was itself postponed or rolled; the "
    "balance-due deadline governs only when payment is due, not whether the "
    "penalty is fixed. "
    "{balance_direction} {columns}"
)


# What the three balance-target notes below say about the OTHER column, as a
# parameter rather than a sentence — because only one of the two claims is
# ever true and the branch that selects them cannot see which.
#
# All three used to assert the EMPTY one unconditionally. They are reached
# whenever no PASSED penalty row is funded, which is not the same as the
# penalty column being $0: the early-estimate path zeroes exactly the
# instalments an estimate already covered, so a schedule can read $0 at April
# and be funded at June, September and January. A $1,200 safe-harbor shortfall
# with $1,000 of estimates in by March 1 does precisely that, and on May 1 the
# note told its reader the report "identifies no underpayment amount" with
# $1,200 outstanding in the table beside it. The under-stating direction, in
# taxpayer-facing copy, in both reports' markdown and `--json`.
#
# Same shape as `_voucher_catchup_note`'s settled/scheduled pair and for the
# same reason: the narrower claim is the one that stays true.
_PENALTY_COLUMN_EMPTY = (
    "The penalty-target column is $0, so this report identifies no {accrual} "
    "underpayment amount, and the passed row is not a reason to expect that "
    "penalty.")

_PENALTY_COLUMN_FUNDED = (
    "No penalty-target instalment that has come due is funded, but later rows "
    "in that column are — so this report DOES identify a {accrual} "
    "underpayment amount, on instalments not yet reached. Read the "
    "penalty-target column, not this row, for what stops that penalty.")


# The column sentences, as a parameter. /tax-withholding renders TWO voucher
# columns and /tax-project ONE, so a note naming "the zero-balance row" told
# every /tax-project reader to distinguish rows their table does not have —
# and the direction it attaches to that row is the only reason the sentence
# exists. `balance_schedules` is exactly the signal: the caller that supplies
# one is the caller that renders both columns.
_PASSED_TWO_COLUMN = (
    "This direction is ONLY for the funded penalty-target row. Any additional "
    "zero-balance amount is an optional prepayment until the unextended "
    "balance-due deadline ({return_due}); it is not itself evidence of an "
    "underpayment penalty.")
_PASSED_ONE_COLUMN = (
    "This schedule carries the penalty target only; tax beyond it is not due "
    "until the unextended balance-due deadline ({return_due}) and is not "
    "itself evidence of an underpayment penalty.")

_CLOSED_TWO_COLUMN = (
    "The amounts above retain the target named by their column; a safe-harbor "
    "amount is only a penalty target and can be smaller than the balance due "
    "or $0. Use the projected balance (or a zero-balance target), not the "
    "safe-harbor amount, for what remains unpaid.")
_CLOSED_ONE_COLUMN = (
    "The amounts above are the penalty target only; a safe-harbor amount can "
    "be smaller than the balance due or $0. Use the projected balance, not "
    "the safe-harbor amount, for what remains unpaid.")

# §7508A is the case the obsolete-schedule note does NOT settle. It rules out
# §7503 and extensions, neither of which moves §6654(b)(2)'s cutoff — but a
# DISASTER postponement is a third thing: §7508A(a)(2) lets the Secretary
# disregard the postponed period in determining "the amount of any interest,
# penalty, additional amount, or addition to the tax", and §6654(a) adds its
# penalty TO THE TAX. Whether that reaches §6654(b)(2)'s April 15 endpoint is
# a question this engine has not resolved, so it does not silently assert the
# strongest claim in the report — "no payment made now reduces it by a cent".
#
# The caveat covers TWO claims, and they are not equally doubtful. The
# accrual-cutoff half above is the open question. The INSTALMENT half is not
# open at all: §7508A(a)(1) disregards the period in determining "whether any
# of the acts described in paragraph (1) of section 7508(a) were performed
# within the time prescribed", and §7508(a)(1)(B) is "payment of any income
# ... tax or any installment thereof" — so a covered instalment is payable on
# its postponed date, full stop, and paying it then is TIMELY. Round fifteen:
# the caveat hedged only the first half, and the note went on to say the
# schedule was obsolete and not to send a voucher, flat. Those sentences are
# false for exactly the taxpayer the caveat was written for, and expensively:
# an instalment still open under a declaration costs nothing to pay and the
# full §6654 rate to skip, so the note talked its reader out of the one action
# that was still available. Same defect as the round it followed, one sentence
# further down the same paragraph — which is why the qualification now leads
# the instruction ("Where no such postponement reached one of them:") instead
# of trailing it. An agent reads an instruction as an instruction and stops.
#
# It fires on EVERY closed year, not only the two in `_RETURN_DUE_POSTPONED`.
# That table holds the IRS-WIDE postponements and nothing else, while §7508A
# relief is overwhelmingly the other kind — a declaration covering named
# counties, of which the IRS issues dozens in a year and which
# postpones estimated-tax instalments along with the return. Nothing reaching
# this function says whether the taxpayer was inside one, so keying the caveat
# on the table answered a question the table was never asked: for every year
# but two it read "not postponed" where the truth is "not known", and printed
# the fixed-penalty verdict unqualified. A taxpayer who reads that and waits
# goes on accruing the penalty they were told was already settled — the
# direction AGENTS.md forbids guessing in, on the report's strongest claim.
#
# NOT collected as an input instead. The only person who can answer is the one
# this caveat sends to the notice, and a flag that reads "no disaster" while
# absent would delete the hedge for everyone who never saw the question — the
# same silent verdict, one indirection further from the sentence making it.
_ACCRUAL_POSTPONED_CAVEAT = (
    " **{lead}, and this engine does not model what a §7508A postponement "
    "does to the accrual cutoff above.** Such a notice disregards the "
    "postponed period in computing additions to tax; if that reaches "
    "§6654(b)(2), the period closed later than {cutoff} and a payment now may "
    "still shorten it. It moves the INSTALMENT itself whatever the answer to "
    "that — §7508A(a)(1) reaches §7508(a)(1)(B), \"payment of any income ... "
    "tax or any installment thereof\" — so an instalment printed above that "
    "the notice covers is still OPEN on its postponed date, and paying it by "
    "then is timely rather than late. Confirm both against the notice before "
    "treating this penalty as fixed or this schedule as spent.")

# The two leads onto that caveat: one states a fact this engine HOLDS, the
# other an absence it cannot fill. They are leads rather than two whole texts
# so that the half which is actually about §6654(b)(2) — the half a later
# round may resolve — exists once and cannot drift between them.
_POSTPONED_NATIONWIDE_LEAD = (
    "TY{year} is one of the two years the IRS postponed NATIONWIDE under "
    "§7508A ({postponed_to})")
_POSTPONED_UNKNOWN_LEAD = (
    "Whether TY{year} was postponed for THIS taxpayer under §7508A — by one "
    "of the disaster declarations the IRS issues dozens of a year, covering "
    "named counties rather than the country — is NOT KNOWN here")


_PASSED_BALANCE_TARGET_NOTE = (
    "> **ℹ A printed {form} date for the zero-balance target has passed** "
    "({dates}; today is {today}), but that optional target is not a penalty "
    "installment. {penalty_state} The TY{year} balance is not due until "
    "the unextended balance-due deadline, {return_due}; paying it earlier is "
    "optional. Do not read the passed row as an immediate penalty-stopping "
    "payment instruction."
)


_DUE_TODAY_BALANCE_TARGET_NOTE = (
    "> **⚠ The TY{year} balance-due deadline is today, {return_due}.** The "
    "printed {form} date for the optional zero-balance target has passed "
    "({dates}). {penalty_state} Pay any unpaid TY{year} tax as a balance-due "
    "payment for that year, not as a {form} voucher."
)


_OVERDUE_BALANCE_TARGET_NOTE = (
    "> **⚠ The printed {form} date for the zero-balance target has passed** "
    "({dates}), and TY{year}'s unextended balance-due deadline was "
    "{return_due}. {penalty_state} Do NOT send a TY{year} {form} voucher now. "
    "Reconcile the filed "
    "return and payments, then pay any unpaid TY{year} tax as a balance-due "
    "payment for that year."
)


def _voucher_dates_note(calendar: _VoucherCalendar, penalty_schedules, year: int,
                        today: "datetime.date | None", *,
                        balance_schedules=()) -> "str | None":
    """The note this schedule's DATES need, or None while they are all ahead.

    A report field rather than a renderer local for the reason
    `ProjectionReport.voucher_catchup_note` is one: both consuming skills read
    `--json` first, and a table whose dates are behind us is not a fact to
    leave out of the payload they summarise from.

    Penalty and zero-balance schedules are separate inputs because the same
    passed date means different things in the two columns. A funded penalty
    row can still be accruing; a funded zero-balance row is merely an optional
    prepayment target while the harbor column is $0. Treating the latter as a
    penalty row falsely says "pay NOW" and "expect the penalty" on a covered
    harbor. /tax-withholding supplies both groups; /tax-project has only the
    penalty schedule.

    Gated on `amount > 0`, not merely on a date having passed. An all-zero row
    asks for nothing, and a warning that fires on a table with nothing to pay
    is the gate taxpayers learn to read past.

    Only the CLOSED branch carries the §7508A hedge, and that is a scoping
    decision rather than an oversight. A postponement makes every printed date
    here EARLIER than the one that governs, so each branch's claims move in a
    different direction. The passed-voucher branch says "pay NOW" about an
    instalment whose real deadline is later — urgency over-stated, which is
    the direction this file may err in. Both balance branches name
    `_return_due_date`, which is the unextended federal date and likewise no
    later than a postponed one, so "not due until X" and "was due X" over-state
    there too. The closed branch is the only one whose claims run the other
    way: it tells a taxpayer that a still-open instalment is spent and that
    paying it changes nothing. Hedging the other three would put a disaster
    caveat on advice that is already safe — the cry-wolf shape, and this note
    is read by someone who has already been given several.

    `today` is the CLI's, not the as-of date, and the two come apart in both
    directions: `--as-of 03-01` read in August prints April and June deadlines
    that are months gone, and the closed-year default (`resolve_as_of`) puts
    as-of at 12-31 with a Q4 instalment dated the following January which today
    may be a year past. `--as-of` cannot reach that January at all — it is MM-DD
    INSIDE the covered year — so no as-of value can close that row, and only a
    real date can.
    """
    if today is None:
        return None

    def passed_dates(schedules) -> list[str]:
        return sorted({
            v.due_date for schedule in schedules for v in schedule
            # The normalizer is re-applied deliberately, NOT redundantly.
            # Both voucher builders already store a rolled date, but this
            # function is also called directly with hand-built rows, and a
            # BARE STATUTORY date there must not read as passed during its
            # §7503 window: TY2022 Q4 printed 2023-01-15, a Sunday followed by
            # MLK Day, and stayed timely through the 17th. Normalizing here is
            # what keeps that true for every caller
            # (`test_a_deadline_gate_observes_even_a_bare_statutory_date`).
            # It relies on the rule being idempotent on a date that same rule
            # already produced, which both are and which that test also pins.
            #
            # It comes off the CALENDAR because idempotence is per-rule and
            # not universal: each rule is idempotent on its OWN output, and the
            # FEDERAL rule is not idempotent on a date some WEAKER state rule
            # produced. A weekend-only state rolls Saturday April 15 2023 to
            # Monday April 17 and is paid then; the federal rule rolls that
            # Monday on again, past observed DC Emancipation Day, to April 18,
            # so the printed date reads as still ahead on the one day it had
            # just passed — silence in the direction AGENTS.md forbids guessing
            # in, the taxpayer late and not told. California is NOT that state:
            # R&TC §18410 gives it the federal set, so `_CA_CALENDAR` and
            # `_FEDERAL_CALENDAR` resolve identically today. The binding stays
            # because the set is a fact about a jurisdiction, and the next
            # state added here need not share California's.
            if v.amount > 0
            and calendar.observed_due(
                datetime.date.fromisoformat(v.due_date)) < today})

    return_due = _return_due_date(year)
    # Whether the CALLER renders a second voucher column. /tax-withholding
    # supplies `balance_schedules` and prints two; /tax-project supplies none
    # and prints one, so the two-column sentences would name a row its reader
    # cannot see.
    two_column = bool(balance_schedules)
    penalty_passed = passed_dates(penalty_schedules)
    if penalty_passed and accrual_period_closed(year, today):
        if today < return_due:
            balance_direction = (
                f"Any additional zero-balance amount is not due until "
                f"{return_due.isoformat()}; early payment is optional.")
        elif today == return_due:
            balance_direction = (
                f"Any unpaid balance is due today, {return_due.isoformat()}, "
                "as a balance-due payment, not as an estimated voucher.")
        else:
            balance_direction = (
                f"Any unpaid balance was due {return_due.isoformat()} and is "
                "paid as a balance-due payment, not as an estimated voucher.")
        cutoff = datetime.date(year + 1, 4, 15).isoformat()
        return _CLOSED_VOUCHER_NOTE.format(
            form=calendar.form, year=year, accrual=calendar.accrual,
            bill=calendar.bill, today=today.isoformat(), cutoff=cutoff,
            balance_direction=balance_direction,
            columns=(_CLOSED_TWO_COLUMN if two_column else _CLOSED_ONE_COLUMN),
            # Unconditional — see the caveat's own comment. The TABLE picks
            # the LEAD, never whether the reader is told at all.
            postponed=_ACCRUAL_POSTPONED_CAVEAT.format(
                cutoff=cutoff,
                lead=(_POSTPONED_NATIONWIDE_LEAD.format(
                          year=year,
                          postponed_to=datetime.date(
                              *_RETURN_DUE_POSTPONED[year]).isoformat())
                      if year in _RETURN_DUE_POSTPONED
                      else _POSTPONED_UNKNOWN_LEAD.format(year=year))),
        ) + calendar.due_date_caveat
    if penalty_passed:
        return _PASSED_VOUCHER_NOTE.format(
            n=len(penalty_passed),
            s="" if len(penalty_passed) == 1 else "s", form=calendar.form,
            dates=", ".join(penalty_passed), today=today.isoformat(),
            accrual=calendar.accrual, step=calendar.step,
            columns=(_PASSED_TWO_COLUMN if two_column
                     else _PASSED_ONE_COLUMN).format(
                         return_due=return_due.isoformat()),
        ) + calendar.due_date_caveat

    balance_passed = passed_dates(balance_schedules)
    if not balance_passed:
        return None
    if today < return_due:
        note = _PASSED_BALANCE_TARGET_NOTE
    elif today == return_due:
        note = _DUE_TODAY_BALANCE_TARGET_NOTE
    else:
        note = _OVERDUE_BALANCE_TARGET_NOTE
    # The whole penalty column, not just its passed rows — see the two
    # `_PENALTY_COLUMN_*` clauses. `penalty_passed` is empty here by the
    # branches above, which says nothing about the instalments still ahead.
    penalty_funded = any(v.amount > 0 for s in penalty_schedules for v in s)
    penalty_state = (_PENALTY_COLUMN_FUNDED if penalty_funded
                     else _PENALTY_COLUMN_EMPTY).format(
        accrual=calendar.accrual)
    return note.format(
        form=calendar.form, dates=", ".join(balance_passed),
        today=today.isoformat(), year=year,
        return_due=return_due.isoformat(), accrual=calendar.accrual,
        penalty_state=penalty_state) + calendar.due_date_caveat


# §6654(g)(1): withholding is deemed paid in equal parts on each due date
# whenever it was actually withheld, so a 4(c) bump cures an ALREADY-missed
# instalment where a voucher only stops further accrual. Both reports lead
# "Recommended actions" with it.
_W4_LEVER_LEAD = (
    "Withholding is treated as paid evenly across the year, so a W-4 line "
    "4(c) bump on remaining paychecks can cure an underpayment retroactively "
    "— prefer it over uneven estimates when paychecks remain.")

# And the same sentence is the wrong lead once there are none. It was printed
# unconditionally, which put "prefer it over uneven estimates" at the top of
# the section in the one state where that lever does not exist — directly above
# a 4(c) figure reading $0.00/paycheck over 0 remaining paychecks. The clause
# "when paychecks remain" makes it defensible rather than false, which is worse
# in a heading: the reader takes the recommendation and finds the number blank.
# Guaranteed on the closed-year default, where `elapsed_fraction` is exactly
# 1.0 and `paychecks_remaining` is therefore always 0.
_W4_LEVER_SPENT_DUE = (
    "> **⚠ No paychecks remain in TY{year}, so the W-4 route is closed for "
    "this year.** §6654(g)(1) spreads withholding evenly across the "
    "instalments, which is what lets a late 4(c) bump cure an early "
    "underpayment — but only withholding TAKEN in the covered year counts "
    "toward it, and there is none left to take. Extra withholding elected now "
    "is withheld against the year currently running, not TY{year}. The 4(c) "
    "figures below print $0.00 for that reason and not because nothing is "
    "owed. The FEDERAL payment target is the projected federal BALANCE DUE of "
    "**{balance}**, not the safe-harbor shortfall: the latter only avoids an "
    "underpayment penalty and can be smaller or $0. Pay the full {balance} "
    "for TY{year} using a payment method that credits that tax year; do not "
    "treat the penalty-only voucher table as the total.{state_clause} A "
    "calendar note, when "
    "present, determines whether 1040-ES is still useful for penalty accrual "
    "or the payment must be made as a balance due.")

# The state half of the DUE lede. Its sibling `_W4_LEVER_SPENT_CLEAR_STATE_DUE`
# closed this gap only where the FEDERAL balance is $0; on the branch where
# both are due, the lede went on naming the federal figure as "The payment
# target" with a larger state balance in the same report. Federal-first is
# right — the 4(c) figures and voucher table below are federal — but "the"
# payment target is not, so the federal figure is now labelled FEDERAL and the
# state one stated beside it rather than mixed into it.
# Carried by BOTH state ledes below. Outside California the state figure is a
# bracket estimate (AGENTS.md: "other states are bracket-estimate only"), and
# the only thing that said so sat in Caveats at the very bottom — past a bolded
# dollar amount at the top of "Recommended actions" that reads as an amount
# owed. Qualifying the number where it is STATED is the difference between a
# projection and a bill.
_W4_LEVER_STATE_ESTIMATE = (
    " (a bracket ESTIMATE — this state's return is not modeled in depth, so "
    "treat it as an order of magnitude, not an amount owed)")

_W4_LEVER_STATE_ALSO_DUE = (
    " A state balance of **{state_balance}**{state_estimate} is ALSO projected "
    "due — a separate payment to the state, on the state's own schedule; see "
    "the state section below.")

_W4_LEVER_SPENT_CLEAR = (
    "> **⚠ No paychecks remain in TY{year}, so the W-4 route is closed for "
    "this year.** Extra withholding elected now applies to the year currently "
    "running, not TY{year}. No federal balance is projected due, so there is "
    "no remaining payment target for this report."
)

# The FEDERAL clear case's own claim — "no remaining payment target" — is
# only true when the STATE half is also clear. A federal-only balance check
# told a taxpayer with a real state balance due that nothing further was
# owed, in the same report whose state true-up table said otherwise a few
# lines below. `_W4_LEVER_SPENT_CLEAR` stays for the genuinely all-clear case.
_W4_LEVER_SPENT_CLEAR_STATE_DUE = (
    "> **⚠ No paychecks remain in TY{year}, so the W-4 route is closed for "
    "this year.** Extra withholding elected now applies to the year currently "
    "running, not TY{year}. No federal balance is projected due, but a state "
    "balance of **{state_balance}**{state_estimate} is — see the state true-up "
    "section below. "
    "The 4(c) figures below print $0.00 for the FEDERAL target only and not "
    "because nothing is owed."
)


def _w4_lever_note(paychecks_remaining: int, year: int,
                   balance_due: float, state_balance_due: float = 0.0,
                   state_is_bracket_estimate: bool = False) -> str:
    """The lead for "Recommended actions" — which lever the year still has.

    Always a string, never None: the section has always opened with a sentence
    and its states are distinct sentences, not one sentence and its absence.
    Reads `paychecks_remaining` rather than a per-paycheck figure of $0.00,
    which is also what a fully covered year prints.

    `state_balance_due` only decides which SPENT lede fires — it is never
    mixed into `balance_due`, which stays the federal figure the DUE lede's
    own text names.

    `state_is_bracket_estimate` qualifies that figure IN PLACE. Only CA is
    computed in depth; every other taxed state is a bracket estimate, and
    bolding one as "ALSO projected due" above a caveat the reader meets at the
    bottom of the report states an estimate with the confidence of a bill.
    """
    state_estimate = (_W4_LEVER_STATE_ESTIMATE
                      if state_is_bracket_estimate else "")
    if paychecks_remaining > 0:
        return _W4_LEVER_LEAD
    if balance_due > 0:
        return _W4_LEVER_SPENT_DUE.format(
            year=year, balance=_money(balance_due),
            state_clause=(
                _W4_LEVER_STATE_ALSO_DUE.format(
                    state_balance=_money(state_balance_due),
                    state_estimate=state_estimate)
                if state_balance_due > 0 else ""))
    if state_balance_due > 0:
        return _W4_LEVER_SPENT_CLEAR_STATE_DUE.format(
            year=year, state_balance=_money(state_balance_due),
            state_estimate=state_estimate)
    return _W4_LEVER_SPENT_CLEAR.format(year=year)


def render_markdown(r: ProjectionReport) -> str:
    f = r.federal
    sh = r.safe_harbor
    lines = [f"# Mid-Year Tax Projection — TY{r.year} (as of {r.as_of})", ""]
    if r.stale_as_of_note:
        lines += [r.stale_as_of_note, ""]

    lines += ["## Assumptions", ""]
    lines.append(f"- Elapsed fraction of year: {r.proj.elapsed_fraction:.0%}")
    lines.append(f"- Pay frequency: {r.pay_frequency} ({r.proj.paychecks_remaining} paychecks remaining)")
    lines.append("- Steady wages and withholding are annualized from YTD (assumes the rest of the "
                 "year continues at the YTD rate); bonuses, vesting, and capital gains are taken "
                 "as the full-year amounts you entered.")
    lines += _prior_baseline_lines(
        r.baseline_source, r.year,
        ca_prior_resolved=(r.ca_safe_harbor is not None
                           and r.ca_safe_harbor.prior_based is not None),
        degraded=r.degraded)
    # Independent of `degraded`: an explicit `prior_year_form_2210_tax_base`
    # can be supplied with no `archive/{year-1}/` at all, clearing `degraded`
    # while `recurring_from_prior` (interest/dividends, populated by the
    # SKILL from that same archive) is still silently defaulted to 0.
    if not r.has_prior_archive:
        lines.append(
            "- **No prior-year archive:** `recurring_from_prior` "
            "(interest/dividends) was not pre-filled from a prior return and "
            "defaults to $0 unless entered directly in "
            "`projection_inputs.json` — real recurring income left at $0 "
            "understates the projection.")
    if r.proj.federal_withholding == 0:
        lines.append("- **Note:** projected federal withholding is $0 — verify YTD withholding was entered.")
    lines.append(
        "- **Second-order effects (use /tax-compute for the exact figures):** "
        "the standard deduction is the base amount — no §63(f) age-65/blind "
        "add-on (slightly overstates the safe-harbor target near a threshold). "
        "Self-employed §224 tips are removed from the QBI base ONLY when you "
        "supply `forecast_overrides.schedule_1a_se_tips`; if a Schedule 1-A "
        "override includes SE tips but that split is omitted, those dollars "
        "stay in QBI and the projection understates tax. The §70105 minimum "
        "QBI deduction (TY2026+, ~$400 for a small ACTIVE trade) is NOT applied "
        "in this forecast — so projected tax may be slightly OVERSTATED (safe "
        "direction) for an eligible small business; /tax-compute applies it.")
    lines.append("")

    lines += ["## Projected full-year federal", "",
              "| Item | Amount |", "|---|---:|",
              f"| Gross income | {_money(f.gross_income)} |",
              f"| Adjustments | {_money(f.adjustments)} |",
              f"| AGI | {_money(f.agi)} |",
              f"| Taxable income | {_money(f.taxable_income)} |",
              f"| Tax before credits{' (QDCG worksheet)' if f.used_qdcg else ''} | {_money(f.tax_before_credits)} |",
              f"| Child Tax Credit | {_money(-f.ctc)} |",
              f"| SE tax | {_money(f.se_tax)} |",
              f"| Additional Medicare | {_money(f.additional_medicare)} |",
              f"| NIIT (Form 8960) | {_money(f.niit)} |",
              f"| **Total tax** | {_money(f.total_tax)} |",
              f"| Withholding | {_money(-f.withholding)} |",
              f"| Estimates paid | {_money(-f.estimates_paid)} |",
              f"| **Projected balance** | {_money(f.balance)} {'(refund)' if f.balance < 0 else '(owed)'} |",
              ""]

    lines += ["## Safe harbor analysis", "",
              "| Test | Amount |", "|---|---:|",
              f"| 90% of projected current-year tax | {_money(sh.pct90_current)} |"]
    if not sh.degraded:
        lines.append(f"| {sh.prior_factor:.0%} of prior-year tax | {_money(sh.prior_based)} |")
    lines += [f"| **Required annual payment** | {_money(sh.required_annual)} |",
              f"| Withholding + estimates paid | {_money(sh.withholding + sh.estimates_paid)} |",
              f"| **Shortfall** | {_money(sh.shortfall)} |",
              f"| Status | {_federal_status(sh)} |", ""]
    if sh.current_tax_unnetted:
        lines += [_CURRENT_TAX_UNNETTED_NOTE, ""]
    if sh.prior_agi_missing:
        lines += ["- **Prior-year AGI unavailable** — assumed at or below the "
                  f"{_PRIOR_AGI_THRESHOLD_TEXT} threshold (100% factor); if "
                  "prior AGI exceeded it, the 110% harbor applies and the "
                  "required payment shown may be UNDERSTATED.", ""]
    if _prior_prong_precondition_applies(sh):
        lines += [_PRIOR_PRONG_PRECONDITION_NOTE, ""]

    # The lead names the lever the year still HAS — `_w4_lever_note`. It is
    # here rather than below the schedule because a 4(c) bump cures an
    # ALREADY-missed instalment where a voucher only stops further accrual, so
    # the catch-up note's "pay catch-up amounts NOW" would otherwise steer the
    # reader to the weaker remedy at the one moment the stronger one is on
    # screen. Where there is no payroll left it says the opposite, which is
    # exactly as load-bearing.
    lines += ["## Recommended actions", ""]
    if r.w4_lever_note:
        lines += [r.w4_lever_note, ""]
    lines += ["### 1040-ES voucher schedule", "",
              "| Quarter | Due | Amount |", "|---|---|---:|"]
    for v in r.vouchers:
        lines.append(f"| Q{v.quarter} | {v.due_date} | {_money(v.amount)} |")
    lines.append("")
    # DATES before AMOUNTS: the closed-accrual note contradicts the catch-up
    # note's "keeps accruing until it is paid" and says so explicitly, which
    # only works if the reader meets it first.
    if r.voucher_dates_note:
        lines += [r.voucher_dates_note, ""]
    if r.voucher_catchup_note:
        lines += [r.voucher_catchup_note, ""]
    lines += ["### W-4 line 4(c) per-paycheck extra withholding",
              f"- To avoid penalty (safe harbor): **{_money(r.w4_penalty.per_paycheck)}/paycheck** "
              f"over {r.w4_penalty.paychecks_remaining} remaining paychecks",
              f"- To reach zero balance: **{_money(r.w4_zero.per_paycheck)}/paycheck**", ""]

    lines += ["## State projection", ""]
    if r.nonresident_income_warning:
        lines += [f"> **⚠ Nonresident income exposure:** "
                  f"{r.nonresident_income_warning}", ""]
    if r.state is None:
        if r.state_cache_mismatch:
            lines.append(f"_⚠ **State cache mismatch:** {r.state_cache_mismatch}._")
        elif r.state_no_income_tax:
            code = r.no_income_tax_state_code or "This state"
            lines.append(
                f'_State projection: none — {code} levies no individual '
                f'income tax (state.type = "none")._'
            )
        elif r.state_unsupported_note:
            lines.append(f"_⚠ **State not modeled:** {r.state_unsupported_note}_")
        else:
            lines.append("_Skipped — no prior-year state baseline or no state brackets in tax_law._")
    else:
        s = r.state
        # The TABLE carries the bracket-estimate qualification too, not only
        # the W-4 lede. Qualifying one surface and not the other is worse than
        # qualifying neither: the lede says "see the state section below" and
        # sends the reader from the hedged sentence to a bolded, unhedged
        # restatement of the same figure, which reads as the authoritative one.
        # Only California is computed in depth (AGENTS.md: "other states are
        # bracket-estimate only"); the Caveats line about scheduling qualifies
        # the SCHEDULE, never the amount.
        lines += ["| Item | Amount |", "|---|---:|",
                  f"| State ({s.code}) taxable income | {_money(s.taxable_income)} |",
                  f"| State tax | {_money(s.tax)} |",
                  f"| State withholding | {_money(-s.withholding)} |",
                  f"| **State balance** | {_money(s.balance)} "
                  f"{'(refund)' if s.balance < 0 else '(owed)'} |"]
        if r.state_bracket_estimate_note:
            lines += ["", f"> **⚠ {r.state_bracket_estimate_note}**"]
    if r.ca_safe_harbor is not None:
        csh = r.ca_safe_harbor
        lines += ["", "### CA safe harbor (FTB 5805)", "",
                  "| Test | Amount |", "|---|---:|",
                  f"| 90% of projected current-year CA tax | {_money(csh.pct90_current)} |"]
        if csh.prior_based is not None:
            lines.append(f"| {csh.prior_factor:.0%} of prior-year CA tax | {_money(csh.prior_based)} |")
        lines += [f"| **Required annual payment** | {_money(csh.required_annual)} |",
                  f"| CA withholding + estimates paid | {_money(csh.withholding + csh.estimates_paid)} |",
                  f"| **Shortfall** | {_money(csh.shortfall)} |",
                  f"| Status | {_ca_status(csh)} |", ""]
        lines += _ca_notes(csh)
        # The §19136.3 test above ran on a PROXY CA AGI (federal-composition
        # income; no CA adjustments like the HSA add-back) — say so, and get
        # loud near the cliff where the proxy could mask a crossing.
        lines.append(
            "- Projected CA AGI here is a federal-composition proxy (CA "
            "adjustments such as the HSA add-back are not modeled) — verify "
            "against Schedule CA when near the §19136.3 threshold.")
        if (r.state is not None and not csh.high_income_90_only
                and csh.high_income_threshold > 0
                and r.state.agi >= 0.9 * csh.high_income_threshold):
            lines.append(
                f"- **⚠ Projected CA AGI ({_money(r.state.agi)}) is within "
                f"10% of the R&TC §19136.3 threshold "
                f"({_money(csh.high_income_threshold)}).** If true CA AGI "
                f"reaches the threshold, the prior-year safe harbor is "
                f"unavailable and the required payment jumps to 90% of "
                f"current-year tax ({_money(csh.pct90_current)}).")
        lines += ["", _CA_ES_HEADING, "",
                  _CA_ES_WEIGHTING + _CA_ES_SHARE_COLUMN_CAVEAT, "",
                  "| Installment | Due | Share of remaining | Amount |",
                  "|---|---|---:|---:|"]
        # Share of the REMAINING shortfall, derived from the AMOUNTS beside it
        # rather than recomputed from the statutory weights. Printing a weight
        # next to an amount allocated on some other rule misleads, and it does
        # not matter whether the mismatch comes from renormalization (the first
        # version of this, "30%" beside 100% of the shortfall after Jun 15) or
        # from the schedule taking a different path entirely: before the first
        # deadline `compute_ca_estimated_vouchers` allocates against the
        # cumulative 30/70/70/100 checkpoints NET of estimates already paid, so
        # a $700 shortfall with $300 already in prints 0/57/0/43 while the
        # renormalized weights read 30/40/0/30 — a table contradicting itself
        # on its own two columns. Reading the amounts cannot drift from them by
        # construction, and on the `_split_remainder_last` path it reproduces
        # the renormalized weight to the rounding of the column it is summing.
        # Share of the REMAINING shortfall, derived from the AMOUNTS beside it
        # rather than recomputed from the statutory weights — a weight printed
        # next to an amount allocated on some other rule misleads, and reading
        # the amounts cannot drift from the column it sits next to.
        for v, share in zip(r.ca_vouchers,
                            _percent_shares([v.amount for v in r.ca_vouchers])):
            lines.append(f"| Q{v.quarter} | {v.due_date} | {share}% | "
                         f"{_money(v.amount)} |")
        lines.append("")
        lines += _ca_split_note(r.ca_vouchers, csh.estimates_paid)
        if r.ca_voucher_dates_note:
            lines += [r.ca_voucher_dates_note, ""]
        if r.ca_voucher_catchup_note:
            lines.append(r.ca_voucher_catchup_note)
    lines += ["",
              "## Caveats",
              "- This is a projection, not a filed return. Re-run after any material income change."]
    if r.ca_safe_harbor is not None:
        lines.append("- CA mandatory e-pay (R&TC §19011.5): once any estimated/extension "
                     "payment exceeds $20,000 or total tax liability exceeds $80,000, ALL "
                     "subsequent CA payments must be made electronically (FTB Web Pay) — "
                     "a 1% penalty applies to paper payments.")
    else:
        lines.append("- State estimated-payment scheduling is informational; state rules vary.")
    if f.qbi_over_threshold:
        lines.append(
            "- **QBI over the §199A threshold:** the Form 8995-A W-2-wage/UBIA limits "
            "and SSTB phase-out are NOT modeled — the projected QBI deduction may be "
            "overstated (an SSTB phases to $0), understating projected tax and the "
            "safe-harbor target. Verify against Form 8995-A.")
    if f.schedule_1a_unfetched:
        lines.append(
            "- **Schedule 1-A override applied WITHOUT fetched caps:** a "
            "`forecast_overrides.schedule_1a` amount was subtracted but "
            "`federal.schedule_1a` (the indexed caps/thresholds) is unfetched, so "
            "the raw figure was NOT capped or MAGI-phased — the projected deduction "
            "may exceed the real Schedule 1-A amount, understating projected tax and "
            "the safe-harbor target. Fetch the block via /tax-setup, or enter the "
            "already-capped/phased amount.")
    lines.append("")
    return "\n".join(lines) + "\n"


def _report_to_dict(r: ProjectionReport) -> dict:
    return {
        "year": r.year, "as_of": r.as_of, "pay_frequency": r.pay_frequency,
        "baseline_source": r.baseline_source, "degraded": r.degraded,
        "has_prior_archive": r.has_prior_archive,
        "proj": vars(r.proj), "federal": vars(r.federal),
        "state": (vars(r.state) if r.state is not None else None),
        "safe_harbor": vars(r.safe_harbor),
        "vouchers": [vars(v) for v in r.vouchers],
        "w4_penalty": vars(r.w4_penalty), "w4_zero": vars(r.w4_zero),
        "ca_safe_harbor": (vars(r.ca_safe_harbor)
                           if r.ca_safe_harbor is not None else None),
        "ca_vouchers": [vars(v) for v in r.ca_vouchers],
        "voucher_catchup_note": r.voucher_catchup_note,
        "ca_voucher_catchup_note": r.ca_voucher_catchup_note,
        "voucher_dates_note": r.voucher_dates_note,
        "ca_voucher_dates_note": r.ca_voucher_dates_note,
        "w4_lever_note": r.w4_lever_note,
        "state_no_income_tax": r.state_no_income_tax,
        "no_income_tax_state_code": r.no_income_tax_state_code,
        "state_cache_mismatch": r.state_cache_mismatch,
        "nonresident_income_warning": r.nonresident_income_warning,
        "state_unsupported_note": r.state_unsupported_note,
        "state_bracket_estimate_note": r.state_bracket_estimate_note,
        "stale_as_of_note": r.stale_as_of_note,
    }


def load_prior_baseline(archive_dir, prior_year: int):
    """Return PriorReturn for archive_dir, or None if the directory is absent
    (degraded mode). Re-raises a year mismatch as ProjectionError."""
    p = Path(archive_dir)
    if not p.exists():
        return None
    try:
        return load_prior(p, prior_year)
    except RolloverError as e:
        raise ProjectionError(str(e)) from e


def _read_json(path: Path) -> dict:
    if not path.exists():
        raise ProjectionError(f"{redact(path)} does not exist")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ProjectionError(f"{redact(path.name)} is malformed: {redact(e)}")


def _main(argv: list) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="agents.lib.projection")
    parser.add_argument("--inputs", required=True, help="path to projection_inputs.json")
    add_workspace_argument(parser)
    parser.add_argument("--year", required=True, help="current tax year")
    parser.add_argument(
        "--as-of", dest="as_of", default=None,
        help="MM-DD (default: today, or 12-31 once the year has closed; a valid "
             "stored as_of_date is preserved as YTD provenance in any year)")
    parser.add_argument("--today", default=None,
                        help="override today's date as YYYY-MM-DD (testing only)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    args = parser.parse_args(argv)

    try:
        year = _validate_year_arg(args.year)
        # --workspace is trusted-by-contract (the taxpayer's own dir); --inputs is
        # constrained to live under it so a stray/relative path can't escape the workspace.
        workspace = resolve_workspace(args.workspace).resolve()
        inputs_path = resolve_under_workspace(args.inputs, workspace)
        inputs = _read_json(inputs_path)

        _require_inputs_tax_year(inputs, year)

        today = (datetime.date.fromisoformat(args.today) if args.today
                 else datetime.date.today())
        as_of_str, stale_as_of_note = resolve_as_of(
            args.as_of, inputs.get("as_of_date"), year, today)
        as_of = parse_as_of(as_of_str, year)
        if as_of > today:
            raise ProjectionError(f"as_of {as_of.isoformat()} is in the future (today {today.isoformat()})")

        tax_law_path = workspace / f"tax_law_{year}.json"
        tax_law = _read_json(tax_law_path) if tax_law_path.exists() else {}
        profile = _read_json(workspace / "taxpayer_profile.json")
        prior = load_prior_baseline(workspace / "archive" / str(year - 1), year - 1)
    except (WorkspaceError, ProjectionError) as e:
        print(f"ERROR: {redact(e)}", file=sys.stderr)
        return 1
    except RolloverError as e:
        print(f"ERROR: {redact(e)}", file=sys.stderr)
        return 1

    try:
        report = build_report(inputs, profile, tax_law, prior, year, as_of,
                              today=today)
    except (ProjectionError, ValueError) as e:
        print(f"ERROR: {redact(e)}", file=sys.stderr)
        return 1

    if stale_as_of_note:
        report = replace(report, stale_as_of_note=stale_as_of_note)

    if args.json:
        print(json.dumps(_report_to_dict(report), indent=2, default=str))
    else:
        print(render_markdown(report))
    return 0


# State result sections known to the toolkit; extend as more states are added.
STATE_SECTION_KEYS = ["california_540", "florida_state", "new_york_it201"]

# Which state CODE each section key represents — keep in sync with
# STATE_SECTION_KEYS above. Used by _detect_prior_state_code to tell WHICH
# state a prior section is evidence for, since the two sections are not
# interchangeable (Codex round-15 P2).
_STATE_SECTION_CODES: dict = {"california_540": "CA", "florida_state": "FL",
                              "new_york_it201": "NY"}


def _detect_prior_state(prior_result: dict) -> str | None:
    for key in STATE_SECTION_KEYS:
        if key in (prior_result or {}):
            return key
    return None


def _detect_prior_state_code(prior_result: dict) -> "str | None":
    """The STATE CODE implied by whichever known section is present in
    `prior_result` (via `_STATE_SECTION_CODES`), or None if no known
    section is present or the section isn't in the mapping.

    Distinct from `_detect_prior_state` (which returns the SECTION KEY,
    e.g. "florida_state") — Codex round-15 P2: `project_state_tax`'s
    has-prior-state gate used to treat ANY known section as
    interchangeable proof of "this taxpayer files a state return." Adding
    `florida_state` to `STATE_SECTION_KEYS` made that collapse dangerous: a
    prior `florida_state` section is evidence the taxpayer was an FL
    resident, NOT evidence for a completely unrelated current-year CA (or
    other) cache. With an unusable `resident_state`, the old boolean gate
    would let a prior FL section bypass the residency check for any
    graduated-income cache."""
    section_key = _detect_prior_state(prior_result)
    if section_key is None:
        return None
    return _STATE_SECTION_CODES.get(section_key)


def project_state_tax(proj: ProjectedInputs, prior, tax_law: dict,
                      filing_status: str | None,
                      resident_state: "str | None" = None):
    """Project state tax only when a prior-year state section exists AND
    tax_law carries state brackets. Returns StateProjection or None.

    `resident_state` (profile address.state) backfills the state code when
    the tax_law cache predates the `state.code` field — without it the CA
    540-ES block would silently never fire on a schema-conformant cache
    (reviewer P1). It ALSO substitutes for the prior-state-section gate:
    that gate was only ever a "files this state" proxy, and a first-year /
    missing-archive CA resident still needs the state projection and the
    CA 540-ES schedule (Codex P2 on PR #35) — so the projection runs
    whenever the prior return carries a state section OR the resident
    state matches the configured state law. A KNOWN cache-vs-resident
    mismatch (both sides known, both different) always returns None
    regardless of the prior-state-section gate (round-6 P1).

    `state.type == "none"` ALWAYS wins over brackets content (Codex
    round-12 P1): round-11 taught tax_law_fetcher.check_freshness to flag a
    type-none cache carrying leftover real brackets as a contradiction, but
    this function reads the cache directly and never calls check_freshness
    — so a `code: "FL", type: "none"` cache with leftover CA brackets
    (copied/partially replaced, never stripped) would sail through the
    `if not brackets: return None` guard below and have this function
    invent a Florida tax liability from CA's brackets. The type
    discriminator is checked FIRST and unconditionally short-circuits to
    None; `build_report`'s `_resolve_state_no_income_tax` is the one place
    that decides what to say about a no-tax claim (including flagging a
    corrupted regime-vs-code combination) — this function's only job is to
    never compute a bracket-based liability for a state that says it has
    none.

    A PRESENT-but-INVALID `state.code` (e.g. "FLA") is rejected outright,
    never backfilled (Codex round-14 P2): `_normalize_state_code` collapses
    it to the same `None` a genuinely ABSENT code produces, and `None`
    normally means "safe to backfill the resident's state and trust this
    cache." That's correct for a legacy code-less cache, but a CA-bracket
    cache whose code was corrupted to "FLA" beside an FL profile would
    otherwise have `cache_code` read as unknown, skip the mismatch check
    below (which requires BOTH sides known), get backfilled to the
    resident's "FL", and have CA's real brackets projected and LABELED as
    Florida tax.

    The INVERSE regime contradiction is also rejected (Codex round-15 P1):
    a `code` that IS a legitimate no-tax state for this cache's OWN
    declared year (per `_none_type_allowed`) must never be trusted for
    bracket-based math regardless of what `type` claims — a
    `code: "FL", type: "graduated_income"` cache retaining real CA
    brackets/standard_deduction would otherwise sail past the
    `type == "none"` guard above (which only fires when type actually says
    "none") and have this function invent a Florida tax liability from the
    leftover CA brackets. Checked before brackets are even read."""
    state_cfg = tax_law.get("state") or {}
    if state_cfg.get("type") == "none":
        return None
    if _cache_state_code_present_but_invalid(state_cfg):
        return None
    _regime_code = _normalize_state_code(state_cfg.get("code"))
    if _regime_code is not None and _none_type_allowed(
            _regime_code, tax_law.get("tax_year")):
        return None
    # `state.brackets` has TWO shapes. California's is a flat list of bands;
    # New York's is a dict keyed by §601 rate SCHEDULE, and a non-empty dict
    # is truthy, so the old `or []` gate handed the schedule KEYS to
    # compute_bracket_tax and raised `AttributeError: 'str' object has no
    # attribute 'keys'` from inside the bracket loop. One reader resolves
    # both: tax_computations.state_brackets_for_status.
    # The status is canonicalised HERE, not inside the accessor: `qss` reads
    # schedule (a) with `mfj` by statute and the collapse is already this
    # module's, so the engine does not grow a second copy of it.
    brackets = state_brackets_for_status(
        state_cfg, _full_status_key(filing_status, "state projection"))
    if not brackets:
        return None
    resident = _normalize_state_code(resident_state)
    cache_code = _normalize_state_code(state_cfg.get("code"))
    # A known cache-vs-resident mismatch is disqualifying regardless of the
    # has_prior_state gate below: an FL resident with an FL prior-year
    # state section (has_prior_state=True) but a stale CA cache left beside
    # the workspace must NOT have CA brackets projected onto FL income —
    # has_prior_state is only a "files a state return" proxy, not proof the
    # cache is for the RIGHT state (round-6 P1, the graduated-brackets twin
    # of _resolve_state_no_income_tax's no-tax-flag guard below). Unknown
    # resident or unknown cache code falls through unchanged.
    if resident is not None and cache_code is not None and resident != cache_code:
        return None
    # Code resolution order: explicit cache field, then the taxpayer's
    # resident state, then a placeholder (older caches lack `state.code`).
    code = cache_code or resident or "STATE"
    # TN NEVER gets generic bracket math (Codex round-23 P2): post-repeal
    # (TY2021+) TN is type "none" and already short-circuits via the
    # _none_type_allowed inverse-contradiction guard above, so reaching
    # this point with a TN code means a pre-repeal (or year-corrupt) cache
    # carrying the Hall-tax rate as brackets. The Hall tax applied ONLY to
    # interest and dividends — this function builds taxable income from
    # wages/SE/capital gains too, so routing it through ordinary
    # graduated-state math would fabricate a liability for a TY2020 TN
    # wage earner. Pre-repeal TN is UNSUPPORTED (skip, the safe
    # under-statement direction) rather than mismodeled. Checked after the
    # resident backfill so a code-less Hall cache beside a TN resident is
    # caught too.
    if code == "TN":
        return None
    # The prior-section fallback requires the PRIOR SECTION'S OWN state
    # identity to match the cache's ACTUAL declared code, not merely "some
    # known state section exists" (Codex round-15 P2): adding florida_state
    # to STATE_SECTION_KEYS made "has_prior_state" collapse every modeled
    # state into one interchangeable signal — with an unusable resident
    # (e.g. a broken/missing address.state), a prior florida_state section
    # would satisfy this gate for a COMPLETELY UNRELATED current-year CA
    # (or other) cache, publishing that cache's liability with zero
    # evidence the taxpayer is even a CA resident this year.
    # _detect_prior_state_code resolves WHICH state the prior section
    # actually represents. Compared against `cache_code` specifically (not
    # the resolved `code`, which can be the generic "STATE" placeholder
    # when the cache is code-LESS): a code-less legacy cache makes no
    # specific state claim to contradict, so the historical permissive
    # "any known prior section is enough" leniency still applies there —
    # only a cache that names a SPECIFIC, conflicting state code loses it.
    prior_state_code = (_detect_prior_state_code(getattr(prior, "result", {}))
                        if prior is not None else None)
    if cache_code is not None:
        has_prior_state = prior_state_code == cache_code
    else:
        has_prior_state = prior_state_code is not None
    if not has_prior_state and (resident is None or resident != code):
        return None
    # The FULL status key: California gives head of household the joint
    # amount, and the template carries `hoh`, so collapsing it onto `single`
    # halved the deduction and over-stated the state tax the taxpayer pays
    # as an estimate. A status the map does not carry takes its stand-in
    # (separate and, where the template has no `hoh` row — the generic
    # bracket-estimate states — head of household read single; surviving
    # spouse reads joint), never a silent zero; a key the map carries at None
    # is an unfetched cache, refused like the capital-gains breakpoints are.
    std_map = state_cfg.get("standard_deduction") or {}
    std_key = _full_status_key(filing_status, "state projection")
    if std_key not in std_map:
        std_key = {"mfs": "single", "hoh": "single", "qss": "mfj"}.get(std_key, std_key)
    if std_key in std_map and std_map[std_key] is None:
        raise ProjectionError(
            f"state.standard_deduction.{std_key} is unfetched. Run /tax-setup "
            "to fetch current rates.")
    std = _num(std_map.get(std_key), "state.standard_deduction")
    # Same capital-gains netting + loss cap as the federal side (v1 assumption:
    # the state taxes capital gains as ordinary income and conforms to the
    # federal $3K/$1.5K-MFS loss limit, as CA does) — the split
    # capital_gains_short/_long fields must reach state income too, not just
    # the legacy combined field.
    net_st = proj.capital_gains_short
    net_lt = proj.capital_gains_long + proj.capital_gains
    cap_input, ordinary_short = net_capital_gains(net_st, net_lt)
    loss_limit = _capital_loss_limit(filing_status, "state projection")
    cap_in_income = (cap_input if cap_input >= 0
                     else max(cap_input, -loss_limit)) + ordinary_short
    # The deductible half of SE tax is a federal above-the-line adjustment
    # already inside federal AGI — and CA Form 540 STARTS from federal AGI,
    # so the state proxy must subtract it too. Omitting it inflated the
    # §19136.3 high-income test (~$1.01M SE income read as over the $1M
    # cliff when true CA AGI was ~$986K — Codex P2 on PR #35). Mirrors the
    # federal-side compute_se_tax call.
    if proj.self_employment_net > 0:
        fed = tax_law.get("federal", {}) or {}
        se = compute_se_tax(
            proj.self_employment_net,
            ss_wage_base=(fed.get("social_security", {}) or {}).get("wage_base"),
            w2_ss_wages=proj.ss_wages,
        )
        se_deduction = se["deductible_half"]
    else:
        se_deduction = 0.0
    state_agi = (proj.wages + cap_in_income + proj.ordinary_dividends
                 + proj.taxable_interest + proj.other_income
                 + proj.self_employment_net - se_deduction)
    state_ti = max(0.0, state_agi - std)
    # A state whose cache declares a BENEFIT RECAPTURE gets NO PROJECTION.
    # Keyed on the BLOCK's presence and not on a state name: a cache declaring
    # a recapture is a cache whose plain bracket math is incomplete, whoever it
    # belongs to.
    recapture = state_cfg.get("tax_table_benefit_recapture")
    if recapture:
        # REFUSED, and this reverses what the first version of this branch did.
        #
        # `compute_ny_tax` returns IT-201 LINE 39 — the State tax alone. This
        # function then subtracts `proj.state_withholding`, and on a New York
        # return that field means ALL New York withholding: box 17, box 19 for
        # a City locality, and any Yonkers amount, because ONE IT-2105 voucher
        # is measured against all four taxes and `/tax-withholding` tells the
        # taxpayer to enter it that way. One symbol, two concepts — and the two
        # errors compound in the SAME direction.
        #
        # Measured: a Manhattan single filer on $200,000 of wages, entering
        # $17,500 of combined withholding, was shown a state BALANCE of
        # -$5,800 — a projected refund — against a city tax this function never
        # computed. Over-stating a refund is the direction AGENTS.md §4 calls a
        # hard error, and a banner does not make it a degrade.
        #
        # So no figure is published. `/tax-withholding` computes line 60 — all
        # four taxes — against the same withholding definition, and is the
        # tool for this question; `build_report`'s note below says so.
        return None
    tax, _ = compute_bracket_tax(state_ti, brackets)
    # CA mental-health services tax: 1% of taxable income over $1M is part
    # of Form 540 total tax — omitting it understates the safe-harbor
    # target for exactly the high earners the §19136.3 rule forces onto
    # 90%-of-current (Codex P2 on PR #35). Applied only when the cache
    # carries the block with both fields.
    mh = state_cfg.get("mental_health_surcharge") or {}
    mh_rate, mh_threshold = mh.get("rate"), mh.get("threshold")
    if (isinstance(mh_rate, (int, float)) and not isinstance(mh_rate, bool)
            and isinstance(mh_threshold, (int, float))
            and not isinstance(mh_threshold, bool)
            and math.isfinite(mh_rate) and math.isfinite(mh_threshold)
            and state_ti > mh_threshold):
        tax += mh_rate * (state_ti - mh_threshold)
    balance = round(tax - proj.state_withholding - proj.estimated_state_paid, 2)
    return StateProjection(
        code=code, taxable_income=round(state_ti, 2),
        tax=round(tax, 2), withholding=round(proj.state_withholding, 2),
        estimates_paid=round(proj.estimated_state_paid, 2), balance=balance,
        agi=round(state_agi, 2),
    )


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
