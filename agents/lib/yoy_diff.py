#!/usr/bin/env python3
"""YoY What-Changed diff for /tax-review.

Read-only: never writes workspace files. Pure compute functions + a markdown
renderer + a CLI. Reuses load_prior/_get_path/etc from rollover.py.
"""

import json
import math
import sys
from dataclasses import dataclass
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
from agents.lib.secure_fs import redact  # noqa: E402
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


@dataclass(frozen=True)
class LineMover:
    label: str
    json_path: str
    current: float
    prior: float
    delta: float


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
    "schedule_c", "schedule_d", "form_8889_hsa", "form_1116_ftc",
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


def compute_headlines(prior: dict, current: dict, tax_law: dict,
                      prior_tax_law: dict | None = None) -> list:
    p_agi = _num(prior, "form_1040.line_11_agi")
    c_agi = _num(current, "form_1040.line_11_agi")
    p_ti = _num(prior, "form_1040.line_15_taxable_income")
    c_ti = _num(current, "form_1040.line_15_taxable_income")
    p_tax = _num(prior, "form_1040.line_24_total_tax")
    c_tax = _num(current, "form_1040.line_24_total_tax")
    p_pay = _num(prior, "form_1040.line_33_total_payments")
    c_pay = _num(current, "form_1040.line_33_total_payments")
    p_net = _num(prior, "form_1040.line_35a_refund") - _num(prior, "form_1040.line_37_amount_owed")
    c_net = _num(current, "form_1040.line_35a_refund") - _num(current, "form_1040.line_37_amount_owed")

    p_eff = round(p_tax / p_agi * 100, 1) if p_agi else None
    c_eff = round(c_tax / c_agi * 100, 1) if c_agi else None
    eff_delta = round(c_eff - p_eff, 1) if (p_eff is not None and c_eff is not None) else None

    # Prior bracket must use the prior year's schedule — brackets inflation-adjust
    # annually, so reusing the current-year tax_law would misreport the prior
    # marginal rate (and the Δ) near bracket boundaries. Fall back to current-year
    # law only when the prior year's law isn't available.
    p_mb = _marginal_bracket(p_ti, prior_tax_law or tax_law, prior.get("meta", {}).get("filing_status"))
    c_mb = _marginal_bracket(c_ti, tax_law, current.get("meta", {}).get("filing_status"))
    mb_delta = round(c_mb - p_mb, 1) if (p_mb is not None and c_mb is not None) else None

    return [
        HeadlineMetric("AGI", c_agi, p_agi, c_agi - p_agi, _pct_change(p_agi, c_agi), "$"),
        HeadlineMetric("Taxable income", c_ti, p_ti, c_ti - p_ti, _pct_change(p_ti, c_ti), "$"),
        HeadlineMetric("Marginal bracket", c_mb, p_mb, mb_delta, None, "pts"),
        HeadlineMetric("Effective rate", c_eff, p_eff, eff_delta, None, "pts"),
        HeadlineMetric("Total tax", c_tax, p_tax, c_tax - p_tax, _pct_change(p_tax, c_tax), "$"),
        HeadlineMetric("Total payments", c_pay, p_pay, c_pay - p_pay, _pct_change(p_pay, c_pay), "$"),
        HeadlineMetric("Refund / (Owed)", c_net, p_net, c_net - p_net, None, "$"),
    ]


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


def compute_yoy_diff(prior: dict, current: dict, tax_law: dict,
                     threshold: float = 1000, prior_tax_law: dict | None = None) -> YoYReport:
    prior_year = int(prior.get("meta", {}).get("tax_year", 0))
    current_year = int(current.get("meta", {}).get("tax_year", 0))
    return YoYReport(
        current_year=current_year,
        prior_year=prior_year,
        headlines=compute_headlines(prior, current, tax_law, prior_tax_law),
        line_movers=compute_line_movers(prior, current, threshold),
        sections_changed=compute_section_changes(prior, current),
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

    lines += ["### Headlines", "",
              f"| Metric | TY{report.prior_year} | TY{report.current_year} | Δ | Δ % |",
              "|---|---:|---:|---:|---:|"]
    for m in report.headlines:
        if m.unit == "pts":
            cur, pri, dlt = _fmt_pts(m.current), _fmt_pts(m.prior), _fmt_delta_pts(m.delta)
        else:
            cur, pri, dlt = _fmt_money(m.current), _fmt_money(m.prior), _fmt_delta_money(m.delta)
        lines.append(f"| {m.label} | {pri} | {cur} | {dlt} | {_fmt_pct(m.delta_pct)} |")
    lines.append("")

    lines += ["### Line movers (sorted by |Δ|)", ""]
    if report.line_movers:
        lines += [f"| Line | TY{report.prior_year} | TY{report.current_year} | Δ |",
                  "|---|---:|---:|---:|"]
        for mv in report.line_movers:
            lines.append(f"| {mv.label} | {_fmt_money(mv.prior)} | {_fmt_money(mv.current)} | {_fmt_delta_money(mv.delta)} |")
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


def _report_to_dict(r: YoYReport) -> dict:
    return {
        "current_year": r.current_year,
        "prior_year": r.prior_year,
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
    except (WorkspaceError, RolloverError) as e:
        print(f"ERROR: {redact(e)}", file=sys.stderr)
        return 1

    report = compute_yoy_diff(prior.result, current, tax_law,
                              threshold=args.threshold, prior_tax_law=prior_tax_law)
    if args.json:
        print(json.dumps(_report_to_dict(report), indent=2, default=str))
    else:
        print(render_markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
