#!/usr/bin/env python3
"""YoY What-Changed diff for /tax-review.

Read-only unless `--save`, which writes the one owner-only report under
`reviews/`, beside the /tax-review workpaper and away from `summaries/`, whose
`*.md` files the parse and compute steps read as source-document figures. Pure compute functions + a markdown renderer + a CLI. Reuses
load_prior/_get_path/etc from rollover.py.
"""

import json
import math
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agents.lib.rollover import (  # noqa: E402
    _amount,
    RolloverError,
    _get_path,
    _read_json,
    _validate_year_arg,
    load_prior,
)
from agents.lib.secure_fs import (  # noqa: E402
    is_reparse_point,
    redact,
    secure_path,
    write_secure,
)
from agents.lib.tax_computations.state.registry import (  # noqa: E402
    STATE_SECTIONS,
)
from agents.lib.workspace import (  # noqa: E402
    WorkspaceError,
    add_workspace_argument,
    resolve_under_workspace,
    resolve_workspace,
)


@dataclass(frozen=True)
class HeadlineMetric:
    label: str
    current: Any
    prior: Any
    delta: Any
    delta_pct: float | None
    unit: str
    # Values for the report's `trend_years`, oldest first; None where that
    # year's figure is unknown (a marginal bracket without that year's law).
    history: tuple = ()


@dataclass(frozen=True)
class LineMover:
    label: str
    json_path: str
    current: float
    prior: float
    delta: float
    history: tuple = ()


@dataclass(frozen=True)
class SectionChange:
    kind: str
    section: str
    note: str


@dataclass(frozen=True)
class YoYReport:
    current_year: int
    prior_year: int
    headlines: list
    line_movers: list
    sections_changed: list
    # Archived years before `prior_year`, oldest first. They add columns;
    # every delta, and the choice of movers, stays prior year to this year.
    trend_years: tuple = ()


_MFJ_LIKE = {"MFJ", "QSS"}

# (label, dotted path). A new result.json field will NOT auto-appear here —
# a maintainer must add it. Deliberate: explicit + noise-free over automatic.
LINE_MOVER_FIELDS: list[tuple[str, str]] = [
    ("Wages (1040 line 1z)", "form_1040.line_1z_total_wages"),
    ("Taxable interest (1040 line 2b)", "form_1040.line_2b_taxable_interest"),
    ("Ordinary dividends (1040 line 3b)", "form_1040.line_3b_ordinary_dividends"),
    ("Qualified dividends (1040 line 3a)", "form_1040.line_3a_qualified_dividends"),
    ("Taxable IRA (1040 line 4b)", "form_1040.line_4b_taxable_ira"),
    ("Taxable pensions (1040 line 5b)", "form_1040.line_5b_taxable_pensions"),
    ("Capital gain (1040 line 7)", "form_1040.line_7_capital_gain"),
    ("Schedule 1 other income (line 8)", "form_1040.line_8_schedule1_other"),
    ("Adjustments (1040 line 10)", "form_1040.line_10_adjustments"),
    ("QBI deduction (1040 line 13a)", "form_1040.line_13a_qbi_deduction"),
    ("Schedule 1-A deductions (1040 line 13b)",
     "form_1040.line_13b_schedule_1a_deductions"),
    ("§170(p) non-itemizer charitable deduction (1040 line 13c)",
     "form_1040.line_13c_charitable_non_itemizer"),
    ("Tax (1040 line 16)", "form_1040.line_16_tax"),
    ("Child Tax Credit (1040 line 19)", "form_1040.line_19_ctc"),
    ("Other taxes (1040 line 23)", "form_1040.line_23_other_taxes"),
    ("Total withholding (1040 line 25d)", "form_1040.line_25d_total_withholding"),
    ("Estimated payments (1040 line 26)", "form_1040.line_26_estimated_payments"),
    ("Schedule C net", "schedule_c.line_31_net"),
    ("SE tax", "schedule_c.se_tax"),
    ("Net capital gain/loss (Sch D)", "schedule_d.line_16_combined"),
    ("HSA deduction (Form 8889)", "form_8889_hsa.line_13_hsa_deduction"),
    ("Foreign tax credit (Form 1116)", "form_1116_ftc.ftc_allowed"),
    ("Additional Medicare Tax (8959)", "form_8959_additional_medicare.line_18_total_additional_tax"),
    ("NIIT", "federal_tax_computation.niit"),
]

# Optional sections checked for appeared/disappeared.
OPTIONAL_SECTIONS: list[str] = [
    "schedule_c", "schedule_d", "schedule_e_rentals", "form_8889_hsa", "form_1116_ftc",
    "form_8938_fatca", "fbar_fincen_114", "form_1099r_401k_rollover",
    "form_8959_additional_medicare", "form_8995_qbi", "ira_contribution",
    # Every state section the registry knows (W10).
    *(s.section for s in STATE_SECTIONS),
]

# Sections whose real "content" is a fixed affirmative STATEMENT rather than
# non-zero numbers/lists — the generic _is_nonempty scan (which excludes all
# strings/booleans on purpose, since most sections' string/bool fields are
# scaffold defaults like accounting_method="Cash") would otherwise never see
# florida_state as "present": a typical non-itemizing FL filer's section is
# `no_income_tax_statement` (a string) + `state_return_required: false` (a
# bool) + an all-zero sales_tax_deduction block — every field the generic
# scan counts is zero/bool/string, so a real, correctly-populated FL section
# would read identically to an unpopulated template scaffold, and move-year
# appear/disappear tracking (e.g. NY -> FL) would never fire. Map each listed
# section to the ONE key whose non-empty-string value means "this section was
# actually populated for this year" (not left as a null scaffold default).
INFORMATIONAL_SECTIONS: dict[str, str] = {
    s.section: s.informational_key for s in STATE_SECTIONS
    if s.informational_key is not None}

# Movers PER STATE SECTION, not one flat list.
#
# It was a flat CA-only list gated on `california_540` being present in BOTH
# years, which is correct for one modeled state and silently wrong for two:
# appending New York's paths to it would have gated NEW YORK's movers on
# CALIFORNIA's sections, so a New Yorker with no CA history got none of them
# and a taxpayer who moved CA -> NY got a full set of CA movers read off an
# absent section. The gate belongs to the SECTION whose fields it guards.
#
# A label says "State ..." rather than naming the state, because the label is
# what a reader sees beside the figure and the SECTION is what decides whether
# the row appears at all — the two years being compared are the same
# taxpayer's, so both sections have to be present for the delta to mean
# anything, and that is what `_state_fields_present` requires.
# Each state's rows live in the registry (W10), beside its section; a state
# the toolkit does not compute has none and gets no movers.
STATE_MOVER_FIELDS: dict[str, list[tuple[str, str]]] = {
    s.section: list(s.mover_fields) for s in STATE_SECTIONS if s.mover_fields}


def _num(d: dict, dotted: str) -> float:
    return float(_amount(_get_path(d, dotted, default=0)))


def _status_key(filing_status: str | None) -> str:
    fs = (filing_status or "").upper()
    if fs in _MFJ_LIKE:
        return "mfj"
    return fs.lower()


def _marginal_bracket(taxable_income: float, tax_law: dict, filing_status: str | None):
    """Return the marginal rate as a percentage (e.g. 24.0), or None if no brackets."""
    brackets = (
        tax_law.get("federal", {})
        .get("filing_statuses", {})
        .get(_status_key(filing_status), {})
        .get("brackets", [])
    )
    if not brackets:
        return None
    # Assumes contiguous integer brackets (IRS schedules + tax_law_fetcher);
    # a fractional gap between max and the next min would fall through to None.
    rate = None
    for b in brackets:
        lo = b.get("min", 0) or 0
        hi = b.get("max")
        if taxable_income >= lo and (hi is None or taxable_income <= hi):
            rate = b.get("rate")
    return round(rate * 100, 1) if rate is not None else None


def _pct_change(prior: float, current: float) -> float | None:
    if prior == 0:
        return None
    return round((current - prior) / abs(prior) * 100, 1)


# (label, unit, whether a percentage change is shown), in report order.
_HEADLINES = (("AGI", "$", True), ("Taxable income", "$", True),
              ("Marginal bracket", "pts", False), ("Effective rate", "pts", False),
              ("Total tax", "$", True), ("Total payments", "$", True),
              ("Refund / (Owed)", "$", False))


def _headline_values(doc: dict, tax_law: dict | None) -> dict:
    """One year's headline figures. With `tax_law` None (a trend year read
    without its own law) there is no marginal bracket rather than one priced on
    another year's schedule; the prior year's fallback is compute_headlines'."""
    agi = _num(doc, "form_1040.line_11_agi")
    ti = _num(doc, "form_1040.line_15_taxable_income")
    tax = _num(doc, "form_1040.line_24_total_tax")
    return {
        "AGI": agi, "Taxable income": ti,
        "Marginal bracket": (_marginal_bracket(ti, tax_law, doc.get("meta", {}).get("filing_status"))
                             if tax_law is not None else None),
        "Effective rate": round(tax / agi * 100, 1) if agi else None,
        "Total tax": tax, "Total payments": _num(doc, "form_1040.line_33_total_payments"),
        "Refund / (Owed)": (_num(doc, "form_1040.line_35a_refund")
                            - _num(doc, "form_1040.line_37_amount_owed")),
    }


def compute_headlines(prior: dict, current: dict, tax_law: dict,
                      prior_tax_law: dict | None = None) -> list:
    # Prior bracket must use the prior year's schedule — brackets inflation-adjust
    # annually, so reusing the current-year tax_law would misreport the prior
    # marginal rate (and the Δ) near bracket boundaries. Fall back to current-year
    # law only when the prior year's law isn't available.
    p = _headline_values(prior, prior_tax_law or tax_law)
    c = _headline_values(current, tax_law)
    metrics = []
    for label, unit, with_pct in _HEADLINES:
        pv, cv = p[label], c[label]
        if unit == "pts":
            delta = round(cv - pv, 1) if (pv is not None and cv is not None) else None
        else:
            delta = cv - pv
        metrics.append(HeadlineMetric(label, cv, pv, delta, _pct_change(pv, cv) if with_pct else None, unit))
    return metrics


def _state_fields_present(prior: dict, current: dict) -> list:
    """The mover rows for every state section BOTH years carry substantively.

    Gating on real content (via `_section_present`) rather than mere key
    presence avoids artificial deltas when one year keeps a zeroed template
    scaffold and the other has real values.

    Per SECTION, because the gate belongs to the fields it guards: a flat list
    would have New York's rows appear or vanish on whether a CALIFORNIA section
    was present. A taxpayer who moved between two modeled states has neither
    section in both years and gets no state movers at all — which is right, a
    delta between two different states' tax is not a movement in anything.
    """
    rows: "list[tuple[str, str]]" = []
    for section, fields in STATE_MOVER_FIELDS.items():
        if _section_present(prior, section) and _section_present(current, section):
            rows.extend(fields)
    return rows


def compute_line_movers(prior: dict, current: dict, threshold: float) -> list:
    fields = LINE_MOVER_FIELDS + _state_fields_present(prior, current)
    movers = []
    for label, path in fields:
        p = _num(prior, path)
        c = _num(current, path)
        delta = c - p
        if abs(delta) >= threshold:
            movers.append(LineMover(label=label, json_path=path, current=c, prior=p, delta=delta))
    movers.sort(key=lambda m: abs(m.delta), reverse=True)
    return movers


_EMPTY_SCALARS = (0, 0.0, None, "", False)


def _is_nonempty(value) -> bool:
    """True if a value carries real tax content. Descriptive scaffold defaults from
    result_template.json are NOT content: _-prefixed keys (_include_if, _note_*,
    _comment), string fields (e.g. accounting_method='Cash', category=...), and
    boolean flags (e.g. fbar_fincen_114.filed_separately=true,
    form_8938_fatca.required=false). Only non-zero numbers and non-empty lists/dicts
    count, so a retained-but-unused template block isn't mistaken for a used form.
    Note: bool must be excluded explicitly — isinstance(True, int) is True in Python,
    so a True flag would otherwise read as a non-zero number."""
    if isinstance(value, dict):
        return any(_is_nonempty(v) for k, v in value.items() if not str(k).startswith("_"))
    if isinstance(value, list):
        return len(value) > 0
    if isinstance(value, (str, bool)):
        return False
    return value not in _EMPTY_SCALARS


def _section_present(doc: dict, section: str) -> bool:
    """A section 'exists' when present and holding real tax content (non-zero numbers
    or non-empty lists). Scaffold-only blocks — template string defaults and _-prefixed
    keys — do not count. See _is_nonempty.

    INFORMATIONAL_SECTIONS is the exception: for a section listed there (e.g.
    florida_state), presence is keyed on that ONE informational string field
    holding real (non-empty) content, since the rest of the section is
    legitimately all zeros/booleans/strings even when correctly populated."""
    block = doc.get(section)
    if not isinstance(block, dict):
        return False
    if section in INFORMATIONAL_SECTIONS:
        value = block.get(INFORMATIONAL_SECTIONS[section])
        return isinstance(value, str) and value.strip() != ""
    return any(_is_nonempty(v) for k, v in block.items() if not str(k).startswith("_"))


def compute_section_changes(prior: dict, current: dict) -> list:
    changes = []
    for section in OPTIONAL_SECTIONS:
        in_prior = _section_present(prior, section)
        in_current = _section_present(current, section)
        if in_current and not in_prior:
            changes.append(SectionChange("appeared", section,
                                         f"{section} present this year, not last year"))
        elif in_prior and not in_current:
            changes.append(SectionChange("disappeared", section,
                                         f"{section} present last year, not this year"))
    return changes


_STATE_SECTION_OF = {path: section for section, fields in STATE_MOVER_FIELDS.items()
                     for _, path in fields}


def _mover_history(path: str, earlier) -> tuple:
    """A mover's value in each earlier year; None for a state row whose
    section that year does not carry, rather than a zero read off its absence."""
    section = _STATE_SECTION_OF.get(path)
    return tuple(None if section and not _section_present(doc, section) else _num(doc, path)
                 for _, doc, _ in earlier)


def compute_yoy_diff(prior: dict, current: dict, tax_law: dict,
                     threshold: float = 1000, prior_tax_law: dict | None = None,
                     earlier=()) -> YoYReport:
    """`earlier` is (year, result, tax_law or None) for each archived year
    before the prior one, oldest first; each adds a column, never a delta."""
    prior_year = int(prior.get("meta", {}).get("tax_year", 0))
    current_year = int(current.get("meta", {}).get("tax_year", 0))
    years = tuple(year for year, _, _ in earlier)
    if list(years) != sorted(set(years)) or (years and years[-1] >= prior_year):
        raise ValueError("earlier years must be distinct, oldest first, and before the prior year")
    trend = [_headline_values(doc, law) for _, doc, law in earlier]
    headlines = [replace(m, history=tuple(values[m.label] for values in trend))
                 for m in compute_headlines(prior, current, tax_law, prior_tax_law)]
    movers = [replace(m, history=_mover_history(m.json_path, earlier))
              for m in compute_line_movers(prior, current, threshold)]
    return YoYReport(
        current_year=current_year,
        prior_year=prior_year,
        headlines=headlines,
        line_movers=movers,
        sections_changed=compute_section_changes(prior, current),
        trend_years=years,
    )


def _fmt_money(v) -> str:
    if v is None:
        return "—"
    body = f"${abs(v):,.0f}"
    return f"({body})" if v < 0 else body


def _fmt_delta_money(v) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else "−"
    return f"{sign}${abs(v):,.0f}"


def _fmt_pts(v) -> str:
    if v is None:
        return "—"
    return f"{v:g}%"


def _fmt_delta_pts(v) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else "−"
    return f"{sign}{abs(v):g} pts"


def _fmt_pct(v) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else "−"
    return f"{sign}{abs(v):g}%"


def render_markdown(report: YoYReport) -> str:
    lines = [f"## YoY What-Changed — TY{report.prior_year} → TY{report.current_year}", ""]
    trend_heads = "".join(f" TY{year} |" for year in report.trend_years)
    trend_rule = "---:|" * len(report.trend_years)
    if report.trend_years:
        lines += [f"_Earlier years are shown for context; every Δ is TY{report.prior_year} → "
                  f"TY{report.current_year}._", ""]

    lines += ["### Headlines", "",
              f"| Metric |{trend_heads} TY{report.prior_year} | TY{report.current_year} | Δ | Δ % |",
              f"|---|{trend_rule}---:|---:|---:|---:|"]
    for m in report.headlines:
        fmt = _fmt_pts if m.unit == "pts" else _fmt_money
        dlt = _fmt_delta_pts(m.delta) if m.unit == "pts" else _fmt_delta_money(m.delta)
        earlier = "".join(f" {fmt(v)} |" for v in m.history)
        lines.append(f"| {m.label} |{earlier} {fmt(m.prior)} | {fmt(m.current)} | {dlt} | {_fmt_pct(m.delta_pct)} |")
    lines.append("")

    lines += ["### Line movers (sorted by |Δ|)", ""]
    if report.line_movers:
        lines += [f"| Line |{trend_heads} TY{report.prior_year} | TY{report.current_year} | Δ |",
                  f"|---|{trend_rule}---:|---:|---:|"]
        for mv in report.line_movers:
            earlier = "".join(f" {_fmt_money(v)} |" for v in mv.history)
            lines.append(f"| {mv.label} |{earlier} {_fmt_money(mv.prior)} | {_fmt_money(mv.current)} | {_fmt_delta_money(mv.delta)} |")
    else:
        lines.append("_No line items moved ≥ threshold._")
    lines.append("")

    appeared = [c for c in report.sections_changed if c.kind == "appeared"]
    disappeared = [c for c in report.sections_changed if c.kind == "disappeared"]
    lines += ["### Sections that appeared", ""]
    lines += [f"- **{c.section}** — {c.note}" for c in appeared] or ["- (none)"]
    lines += ["", "### Sections that disappeared", ""]
    lines += [f"- **{c.section}** — {c.note}" for c in disappeared] or ["- (none)"]

    return "\n".join(lines) + "\n"


# --- I/O wrappers (CLI edge) ----------------------------------------------

def _load_current_result(workspace: Path) -> dict:
    return _read_json(workspace / "result.json")


def _load_tax_law(workspace: Path, year: int) -> dict:
    path = workspace / f"tax_law_{year}.json"
    return _read_json(path) if path.exists() else {}


def save_report(workspace: Path, report: YoYReport) -> Path:
    """Write the markdown report owner-only to `reviews/`, the one file
    `--save` creates. A linked `reviews/` would carry the write, and the
    chmod, outside the workspace, so it refuses."""
    reviews = workspace / "reviews"
    try:
        linked = is_reparse_point(reviews)
    except OSError as e:
        raise RolloverError(f"Could not inspect {redact(reviews)}: {redact(e)}") from None
    if linked:
        raise RolloverError(f"{redact(reviews)} is a symlink or junction; refusing to write through it")
    try:
        reviews.mkdir(mode=0o700, exist_ok=True)
        secure_path(reviews)
        return write_secure(reviews / f"yoy_diff_{report.prior_year}_to_{report.current_year}.md",
                            render_markdown(report))
    except OSError as e:
        raise RolloverError(f"Could not save the YoY report: {redact(e)}") from None


def _report_to_dict(r: YoYReport) -> dict:
    return {
        "current_year": r.current_year,
        "prior_year": r.prior_year,
        "trend_years": list(r.trend_years),
        "headlines": [vars(m) for m in r.headlines],
        "line_movers": [vars(m) for m in r.line_movers],
        "sections_changed": [vars(c) for c in r.sections_changed],
    }


def _main(argv: list) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="agents.lib.yoy_diff")
    parser.add_argument("--prior", required=True, help="archive/{prior_year} directory")
    add_workspace_argument(parser, "--current",
                           help="the workspace holding this year's return "
                                "(default: $TAX_WORKSPACE, else the current directory)")
    parser.add_argument("--year", required=True, help="current (new) tax year")
    parser.add_argument("--threshold", type=float, default=1000,
                        help="minimum absolute dollar change to include in line movers (default: 1000)")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable JSON instead of markdown")
    parser.add_argument("--trend-years", type=int, default=0, choices=range(0, 4),
                        metavar="{0..3}",
                        help="add columns for up to three archived years before the prior one, "
                             "read from archive/{year} under the workspace (default: 0)")
    parser.add_argument("--save", action="store_true",
                        help="also write the markdown report owner-only to "
                             "reviews/yoy_diff_{prior}_to_{year}.md")
    args = parser.parse_args(argv)

    # argparse type=float accepts nan/inf; reject them — a nan threshold makes every
    # `abs(delta) >= threshold` false, silently suppressing all movers with exit 0.
    if not math.isfinite(args.threshold) or args.threshold < 0:
        print("ERROR: --threshold must be a finite number >= 0", file=sys.stderr)
        return 1

    try:
        year = _validate_year_arg(args.year)
        # `--current` IS the workspace under an older name, so it takes
        # $TAX_WORKSPACE like every other one, and `--prior archive/2025`
        # resolves under it rather than under whatever directory the command
        # ran from.
        workspace = resolve_workspace(args.workspace).resolve()
        prior_dir = resolve_under_workspace(args.prior, workspace)
        prior = load_prior(prior_dir, year - 1)
        current = _load_current_result(workspace)
        current_year_meta = current.get("meta", {}).get("tax_year")
        if current_year_meta != year:
            raise RolloverError(
                f"Current result.json is for TY{current_year_meta}, not TY{year}. "
                "Refusing to diff mismatched data."
            )
        tax_law = _load_tax_law(workspace, year)
        # Prior-year tax law lives in the archive (moved there by /tax-archive);
        # used so the prior marginal bracket reflects that year's schedule.
        prior_tax_law = _load_tax_law(prior_dir, year - 1)
        earlier = []
        for back in range(args.trend_years, 0, -1):
            older = year - 1 - back
            # Under the workspace, as --prior is: a linked archive/{year}
            # must not bring another directory's figures into the report.
            older_dir = resolve_under_workspace(f"archive/{older}", workspace)
            # An asked-for column is never silently dropped: every year named
            # must have its archive, which load_prior checks for that year.
            earlier.append((older, load_prior(older_dir, older).result,
                            _load_tax_law(older_dir, older) or None))
        report = compute_yoy_diff(prior.result, current, tax_law, threshold=args.threshold,
                                  prior_tax_law=prior_tax_law, earlier=earlier)
        saved = save_report(workspace, report) if args.save else None
    except (WorkspaceError, RolloverError) as e:
        print(f"ERROR: {redact(e)}", file=sys.stderr)
        return 1
    except (OSError, AttributeError, TypeError) as e:
        # An unreadable archive file, or a result or law that is not the
        # object it should be: a clean refusal, never a traceback whose
        # OSError would carry the raw path.
        print(f"ERROR: could not read the archived returns: {redact(e)}", file=sys.stderr)
        return 1

    if saved is not None:
        print(f"Saved {saved.relative_to(workspace)}", file=sys.stderr)
    if args.json:
        print(json.dumps(_report_to_dict(report), indent=2, default=str))
    else:
        print(render_markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
