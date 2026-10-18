#!/usr/bin/env python3
"""The synthetic fixture workspace V3's goldens are scored against.

WHY SYNTHETIC, AND WHY FIXED LAW. This workspace exists to test the
AGENT-MEDIATED path — that /tax-parse and /tax-compute, driven by a model,
produce the return the engine's own functions say they should. It is not a test
of whether the tax law is current: the law here is deliberately invented, with
round brackets a reader can verify by hand, and it never changes. A golden built
on real figures would go stale every filing season and be rewritten by whoever
noticed, which is how a golden stops being a known answer.

The owner's ruling (Q1, 2026-09-07): the first golden is a synthetic
CA + federal MFJ return, hand-computed from the worksheets. Nothing in this file
is a real person, a real employer or a real account, and no identifier of any
shape appears — `ssn_last4` carries the masked form the schema asks for.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from agents.lib.capabilities import _REQUIRED_GATE_FLAGS  # noqa: E402
from agents.lib.secure_fs import secure_path, write_secure  # noqa: E402
from agents.lib.tax_law_fetcher import (  # noqa: E402
    create_empty_tax_law, save_tax_law)

YEAR = 2026
CASE = "ca_mfj_2026"

# --- The invented law. Round numbers, so every figure in the derivation can be
# --- checked with a pencil. `_sources` is filled because `verify_source_pinning`
# --- and the freshness gate read it; the URLs are the real authorities for the
# --- blocks, which is what those checks are about — the VALUES are invented.
IRS = "https://www.irs.gov/pub/irs-drop/rp-25-32.pdf"
FTB = "https://www.ftb.ca.gov/forms/2026/2026-540-booklet.html"
BRACKETS: "list[dict]" = [{"rate": 0.10, "min": 0, "max": 12000},
            {"rate": 0.22, "min": 12000, "max": 200000},
            {"rate": 0.35, "min": 200000, "max": None}]
CA_BRACKETS: "list[dict]" = [{"rate": 0.01, "min": 0, "max": 20000},
               {"rate": 0.02, "min": 20000, "max": 50000},
               {"rate": 0.04, "min": 50000, "max": 100000},
               {"rate": 0.06, "min": 100000, "max": None}]
STANDARD_DEDUCTION = {"single": 15750, "mfj": 31500, "mfs": 15750, "hoh": 23600}
CA_STANDARD_DEDUCTION = {"single": 5500, "mfj": 11000, "hoh": 11000}
CA_PERSONAL_EXEMPTION = 150
SS_WAGE_BASE = 184500
SS_RATE = 0.062
MEDICARE_RATE = 0.0145

# --- The case. One employer, three information returns, one lot.
WAGES = 185000.0
FEDERAL_WITHHELD = 28000.0
STATE_WITHHELD = 9500.0
INTEREST = 1200.0
ORDINARY_DIVIDENDS = 3000.0
QUALIFIED_DIVIDENDS = 2600.0
PROCEEDS = 22000.0
BASIS = 15000.0
STREET = "418 Mission Street"
CITY = "San Jose"
ZIP_CODE = "95112"

SDI_RATE = 0.011              # a synthetic rate; no wage ceiling, per TY2024+

PRIOR_YEAR_AGI = 190000.0     # over 150,000, so the safe harbour is 110%
PRIOR_YEAR_TAX = 25000.0      # 110% = 27,500, under the 28,000 withheld

EMPLOYER = "Acme Widgets LLC"
EMPLOYER_EIN = "12-3456789"          # a synthetic EIN, not an identifier shape
BANK = "First Meridian Savings"
BROKER = "Northgate Securities"
# First, middle, last — the shape `taxpayer_profile_template.json` asks for.
# The RESULT template carries a single joined `name`, which is the join the
# pipeline makes; `full_name` is that join, so the golden and the profile cannot
# disagree about who filed.
PRIMARY_PARTS = ("Alex", "Q.", "Sample")
SPOUSE_PARTS = ("Robin", "T.", "Sample")


def full_name(parts: "tuple[str, ...]") -> str:
    return " ".join(part for part in parts if part)


PRIMARY = full_name(PRIMARY_PARTS)
SPOUSE = full_name(SPOUSE_PARTS)


ANSWERS = f"""# Answers — {CASE}

Synthetic. Every question this return raises is answered here, so a headless run
never has to ask one.

- **Estimated tax payments made for {YEAR}:** none. All federal tax was withheld
  by the employer (Form W-2 box 2).
- **Prior-year ({YEAR - 1}) return:** AGI {PRIOR_YEAR_AGI:,.0f}, total tax
  {PRIOR_YEAR_TAX:,.0f}, withholding 26,000. AGI is over 150,000, so the §6654
  safe harbour is 110% of the prior year's tax = 27,500 — covered by this year's
  28,000 of withholding, so no Form 2210 penalty applies.
- **Every information return is the PRIMARY taxpayer's**, as each document's
  RECIPIENT line states; the spouse has no reportable income this year.
- **Digital assets:** no. Neither spouse received, sold, exchanged or otherwise
  disposed of a digital asset.
- **Foreign accounts:** none, so no FBAR and no Form 8938.
- **Itemising:** no. The standard deduction is larger.
- **[document_set_complete]** Yes. The four PDFs in `documents/` are every tax
  document for {YEAR}: one Form W-2 and three information returns. There are no
  others outstanding, expected or mislaid.
- **[state_local_taxes_paid_outside_withholding]** No state or local estimated
  payments or credited overpayments during {YEAR}, and no prior-year state or
  local balance paid this year — the {YEAR - 1} return was fully covered by
  withholding. Nothing is
  deductible on Schedule A line 5a beyond the {YEAR} withholding itself.
- **[charitable_gifts]** None, cash or non-cash. Nothing is deductible on
  Schedule A line 11-12, and the §170(p) non-itemizer deduction is therefore zero
  as well.
- **Medical expenses, mortgage interest, property taxes, investment interest,
  misc 2% expenses:** none. State income tax withholding is the only Schedule A
  input this return has.
- **[corrected_documents]** No. Every one is an original; no CORRECTED box is
  checked and no payer has reissued anything.

The markers are the V5 required-answer ids — `--check-required` reads them, and a
headless run stops on any that is missing. This fixture answers every one that
applies to it, which is what makes it a workspace the pipeline would accept.
"""


# A whole second file rather than string surgery on the one above, and that is
# the lesson from doing it the other way: the `zero_income` mutation rewrote the
# `[document_set_complete]` bullet and left FIVE other sentences asserting a W-2,
# 28,000 of federal withholding and 9,500 of state. AGENTS.md §6 makes this file
# the authoritative input for a headless run, so an agent that believed it put
# 28,000 on line 25a and produced a refund on a return with no income — and the
# case then scored the agent that OBEYED the toolkit as the failure.
#
# A bullet-by-bullet patch cannot be reviewed for that. A second document can be
# read start to finish and asked one question: does anything here assume income?
ZERO_INCOME_ANSWERS = f"""# Answers — zero_income

Synthetic. Every question this return raises is answered here, so a headless run
never has to ask one.

- **Income:** NONE, for either taxpayer, of any kind. No wages, no interest, no
  dividends, no distributions, no sales, no self-employment, no rents. {YEAR} was
  a year without income, not a year whose documents are missing.
- **Estimated tax payments made for {YEAR}:** none, and none were needed.
- **Withholding:** none, federal or state. There was no employer and no payer,
  so nothing was withheld from anything.
- **Prior-year ({YEAR - 1}) return:** AGI {PRIOR_YEAR_AGI:,.0f}, total tax
  {PRIOR_YEAR_TAX:,.0f}. No §6654 penalty can arise on a year with no tax: the
  required annual payment is a percentage of a liability that is zero.
- **Digital assets:** no. Neither spouse received, sold, exchanged or otherwise
  disposed of a digital asset.
- **Foreign accounts:** none, so no FBAR and no Form 8938.
- **Itemising:** no. There is nothing to itemise and the standard deduction is
  larger than nothing.
- **[document_set_complete]** Yes, and it is EMPTY. `documents/` holds no files
  because there is nothing to put in it — nothing is outstanding, expected or
  mislaid. This is the answer that separates this return from one whose documents
  have not arrived.
- **[state_local_taxes_paid_outside_withholding]** No state or local estimates,
  credited overpayments, prior-year balances or withholding. Nothing is
  deductible on Schedule A line 5a.
- **[charitable_gifts]** None, cash or non-cash. Nothing is deductible on
  Schedule A line 11-12, and the §170(p) non-itemizer deduction is therefore zero
  as well.
- **Medical expenses, mortgage interest, property taxes, investment interest,
  misc 2% expenses:** none. This return has no Schedule A input at all.
- **[corrected_documents]** No. There are no documents to correct.

The markers are the V5 required-answer ids — `--check-required` reads them, and a
headless run stops on any that is missing.
"""


def tax_law() -> dict:
    """The fixed cache this case is computed against."""
    law = create_empty_tax_law(YEAR)
    fed = law["federal"]
    for status, amount in STANDARD_DEDUCTION.items():
        fed["filing_statuses"][status]["standard_deduction"] = amount
        fed["filing_statuses"][status]["brackets"] = [dict(b) for b in BRACKETS]
    fed["capital_gains_rates"] = {
        "zero_pct_limit": {"single": 49000, "mfj": 98000, "mfs": 49000, "hoh": 65600},
        "fifteen_pct_limit": {"single": 540000, "mfj": 608000, "mfs": 304000,
                              "hoh": 574000}}
    fed["social_security"]["wage_base"] = SS_WAGE_BASE
    fed["additional_medicare_tax"]["threshold"] = {"single": 200000, "mfj": 250000,
                                                   "mfs": 125000}
    fed["niit"]["threshold"] = {"single": 200000, "mfj": 250000, "mfs": 125000}
    # EVERY federal block, not only the ones this case reads. A cache that
    # reports INCOMPLETE would make an eval run a test of the freshness gate
    # rather than of the agent — the agent would correctly stop before computing
    # anything. Invented like the rest, and none of it moves the golden.
    fed["child_tax_credit"].update({"amount_per_child": 2200, "refundable_limit": 1700})
    fed["child_tax_credit"]["phase_out_start"] = {"single": 200000, "mfj": 400000}
    fed["irmaa"]["tiers"] = {
        "single": [{"magi_threshold": 106000, "annual_surcharge_per_person": 1000}],
        "mfj": [{"magi_threshold": 212000, "annual_surcharge_per_person": 1000}]}
    fed["ira_limits"].update({"contribution_limit": 7500, "catch_up_50_plus": 1100})
    fed["ira_limits"]["traditional_deduction_phaseout"] = {
        "mfj_covered": {"start": 129000, "end": 149000},
        "mfj_spouse_not_covered": {"start": 242000, "end": 252000},
        "single_covered": {"start": 81000, "end": 91000}}
    fed["ira_limits"]["roth_income_phaseout"] = {
        "mfj": {"start": 242000, "end": 252000},
        "single": {"start": 153000, "end": 168000}}
    fed["hsa_limits"].update({"self_only": 4400, "family": 8750, "catch_up_55_plus": 1000})
    fed["retirement_401k"].update({
        "elective_deferral": 24500, "catch_up_50_plus": 8000,
        "catch_up_60_63": 11250, "total_annual_limit": 72000})
    fed["retirement_401k"]["secure_2_0_high_earner_threshold"] = 150000
    fed["salt_cap"] = {"amount": 40400, "mfs_amount": 20200}
    fed["qbi_threshold"] = {"single": 197300, "mfj": 394600}
    fed["amt"]["exemption"] = {"single": 90400, "mfj": 140600, "mfs": 70300}
    fed["amt"]["phaseout_start"] = {"single": 639300, "mfj": 1278700, "mfs": 639300}
    fed["amt"]["rate_28_breakpoint"] = 244600
    law["exchange_rates"] = {"EUR": 1.08}
    law["state"] = {
        "code": "CA", "name": "California", "authority": "Franchise Tax Board (FTB)",
        "type": "graduated_income", "conforms_to_federal": False,
        "standard_deduction": dict(CA_STANDARD_DEDUCTION),
        "brackets": [dict(b) for b in CA_BRACKETS],
        "personal_exemption": {"amount": CA_PERSONAL_EXEMPTION},
        "dependent_exemption": {"amount": 466},
        "hsa_conformity": False,
        "mental_health_surcharge": {"rate": 0.01, "threshold": 1000000},
        # Required by the freshness gate: absent, the whole cache reads
        # INCOMPLETE and an agent following /tax-compute Step 0.5 correctly
        # STOPS — at which point the eval is measuring the gate and not the
        # agent, which is the one thing this fixture must never do.
        "itemized_phaseout": {"single": 250000, "mfj": 500000, "hoh": 375000},
        # Step 10.11 runs `compute_excess_sdi` for EVERY person with CA-wage W-2
        # income, and a None rate hard-errors there. The wage limit is
        # explicitly null, which is the correct TY2024+ shape (SB 951 removed
        # the ceiling) and which that function disambiguates by tax year.
        "sdi_rate": SDI_RATE,
        "sdi_wage_limit": None,
    }
    law["_sources"] = {"federal.filing_statuses": IRS,
                       "federal.capital_gains_rates": IRS,
                       "federal.social_security": IRS,
                       "federal.additional_medicare_tax": IRS,
                       "federal.niit": IRS,
                       "state.CA": FTB}
    return law


TEMPLATES = Path(__file__).resolve().parent.parent / "agents" / "templates"


def _template(name: str) -> dict:
    return json.loads((TEMPLATES / name).read_text(encoding="utf-8"))


def taxpayer_profile() -> dict:
    """The profile as `tax-setup` would leave it: the TEMPLATE, filled in.

    Hand-building it was the same defect the golden had on the other side of the
    pipeline, and it had already drifted three ways — `taxpayers.primary` for the
    template's top-level `primary`, `address.line1` for `address.street`, and an
    invented `prior_year.agi`/`total_tax` pair the schema has no keys for. A
    fixture whose whole claim is "a workspace the pipeline would accept" cannot
    be in a shape the pipeline does not write.

    The safe-harbour figures live in `answers.md` and NOT here, which is where
    /tax-compute Step 9.5 is told to read them from: the profile's `prior_year`
    block carries carryforwards, not the Form 2210 line-8 composition.
    """
    profile = _template("taxpayer_profile_template.json")
    profile["tax_year"] = YEAR
    profile["filing_status"] = "MFJ"
    for key, parts, last4, dob, occupation in (
            ("primary", PRIMARY_PARTS, "XXX-XX-4321", "1979-04-02", "Engineer"),
            ("spouse", SPOUSE_PARTS, "XXX-XX-8765", "1981-11-15", "Teacher")):
        profile[key] = {"first_name": parts[0], "middle_name": parts[1],
                        "last_name": parts[2], "ssn_last4": last4,
                        "dob": dob, "occupation": occupation}
    profile["address"] = {"street": STREET, "city": CITY,
                          "state": "CA", "zip": ZIP_CODE}
    # The template ships ONE placeholder dependent, marked as such, so that a
    # reader can see the shape. A filled profile carries the real list.
    profile["dependents"] = []
    # ANSWERED, not absent: the template ships the capability gate's REQUIRED
    # questions null (AA31), and a null one blocks /tax-compute at Step 0.7 —
    # the eval would then measure the gate instead of the agent. This case's
    # taxpayer answered each of them no. `has_capital_gains` is the one this
    # case sets: it is what `result_template.json` keys `schedule_d`'s
    # `_include_if` on, and the golden carries that section.
    for flag in _REQUIRED_GATE_FLAGS:
        profile["flags"][flag] = False
    profile["flags"]["has_capital_gains"] = True
    return profile


def _document(path: Path, title: str, lines: "list[tuple[str, str]]") -> Path:
    """One synthetic source document, laid out the way the real form prints it:
    a box label and its value, which is what `tax-parse` reads."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)
    styles = getSampleStyleSheet()
    table = Table([[label, value] for label, value in lines], colWidths=[260, 200])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    SimpleDocTemplate(str(path), pagesize=letter).build(
        [Paragraph(title, styles["Title"]), Spacer(1, 12), table])
    secure_path(path)
    return path


def write_w2(path, extra_lines=None, *, federal_withheld=FEDERAL_WITHHELD,
             state_withheld=STATE_WITHHELD) -> Path:
    """The fixture's W-2, as its own writer so a CASE can rewrite it.

    `extra_lines` appends rows AFTER the boxes and changes no figure. That is
    what the hostile-document-text case needs and the only thing it needs: an
    injection that also moved a number would confound "did the agent resist the
    instruction" with "did it compute the new number", and only the first is
    being asked. The golden stays the golden.

    The two WITHHELD overrides are the corrected-document case's, and the pair it
    can move is not a free choice. Boxes 2 and 17 are the only figures on this
    form that drive nothing else: box 1 feeds boxes 5, 16 and — through the
    rates — 6 and 14, so superseding WAGES would leave four boxes that the W-2c
    does not mention carrying the old number, and the corrected state would be
    the internally inconsistent one. An agent applying the correction exactly as
    written would then miss the golden by four boxes it was never told about.
    """
    wages = WAGES
    ss_wages = min(wages, SS_WAGE_BASE)
    return _document(path, f"Form W-2 — {YEAR}", [
        ("c Employer's name", EMPLOYER),
        ("b Employer identification number (EIN)", EMPLOYER_EIN),
        ("e Employee's name", PRIMARY),
        ("1 Wages, tips, other compensation", f"{wages:,.2f}"),
        ("2 Federal income tax withheld", f"{federal_withheld:,.2f}"),
        ("3 Social security wages", f"{ss_wages:,.2f}"),
        ("4 Social security tax withheld", f"{round(ss_wages * SS_RATE, 2):,.2f}"),
        ("5 Medicare wages and tips", f"{wages:,.2f}"),
        ("6 Medicare tax withheld", f"{round(wages * MEDICARE_RATE, 2):,.2f}"),
        ("16 State wages, tips, etc.", f"{wages:,.2f}"),
        ("17 State income tax", f"{state_withheld:,.2f}"),
        ("14 Other — CASDI", f"{round(wages * SDI_RATE, 2):,.2f}"),
    ] + list(extra_lines or []))


def write_w2c(path, *, previously, corrected) -> Path:
    """A Form W-2c, which prints PREVIOUSLY REPORTED beside CORRECT.

    Both columns on purpose: that is how the real form reads, and it is what
    makes the case a test rather than a swap. A document carrying only the new
    figure is just a second W-2, and an agent that adds it to the first would be
    doing something defensible. Printing what it supersedes is what turns
    "which number" into a question with one right answer.
    """
    rows = [("c Employer's name", EMPLOYER),
            ("b Employer identification number (EIN)", EMPLOYER_EIN),
            ("e Employee's name", PRIMARY)]
    for box, label in ((2, "Federal income tax withheld"),
                       (17, "State income tax")):
        rows.append((f"{box} {label} — previously reported",
                     f"{previously[box]:,.2f}"))
        rows.append((f"{box} {label} — correct information",
                     f"{corrected[box]:,.2f}"))
    return _document(path, f"Form W-2c — Corrected Wage and Tax Statement — {YEAR}",
                     rows)


def build_workspace(dest, case: str = CASE) -> Path:
    """Write the whole workspace and return its path.

    A case is a WORKSPACE as well as a golden. `evals.run --case` once selected
    only a golden, so `--case zero_income_2026` built this CA MFJ return, scored
    it against a zero-income golden, and reported the model wrong for it. An
    unknown name still raises; a known one now builds the workspace that case
    means, by deriving it from this one — see `evals/cases.py`.
    """
    from evals import cases as CASES
    spec = CASES.case(case)          # raises on an unknown name
    dest = Path(dest)
    (dest / "documents").mkdir(parents=True, exist_ok=True)
    (dest / "summaries").mkdir(exist_ok=True)
    for directory in (dest / "documents", dest / "summaries"):
        secure_path(directory)

    write_w2(dest / "documents" / "W2_Acme.pdf")
    _document(dest / "documents" / "1099INT_Meridian.pdf",
              f"Form 1099-INT — {YEAR}", [
                  ("PAYER'S name", BANK),
                  ("RECIPIENT'S name", PRIMARY),
                  ("1 Interest income", f"{INTEREST:,.2f}"),
                  ("4 Federal income tax withheld", "0.00"),
              ])
    _document(dest / "documents" / "1099DIV_Northgate.pdf",
              f"Form 1099-DIV — {YEAR}", [
                  ("PAYER'S name", BROKER),
                  ("RECIPIENT'S name", PRIMARY),
                  ("1a Total ordinary dividends", f"{ORDINARY_DIVIDENDS:,.2f}"),
                  ("1b Qualified dividends", f"{QUALIFIED_DIVIDENDS:,.2f}"),
                  ("2a Total capital gain distributions", "0.00"),
              ])
    _document(dest / "documents" / "1099B_Northgate.pdf",
              f"Form 1099-B — {YEAR}", [
                  ("PAYER'S name", BROKER),
                  ("RECIPIENT'S name", PRIMARY),
                  ("Description", "120 sh. Meridian Index Fund"),
                  ("1c Date sold", f"{YEAR}-08-14"),
                  ("1b Date acquired", f"{YEAR - 3}-02-01"),
                  ("1d Proceeds", f"{PROCEEDS:,.2f}"),
                  ("1e Cost or other basis", f"{BASIS:,.2f}"),
                  ("Applicable checkbox", "D — long-term, basis reported to IRS"),
              ])

    write_secure(dest / "taxpayer_profile.json",
                 json.dumps(taxpayer_profile(), indent=2) + "\n")
    # AGENTS.md §6: in a headless run the required answers are READ from here and
    # a missing one is a hard stop. A case that stops to ask a question is not a
    # known answer, so every question this return raises is answered here.
    write_secure(dest / "answers.md", ANSWERS)
    # Through `save_tax_law`, which stamps `_meta.fetched` with NOW: a cache
    # with no retrieval time reads STALE however complete it is, and a STALE
    # cache stops /tax-compute exactly as an incomplete one does.
    save_tax_law(YEAR, tax_law(), str(dest))
    secure_path(dest / f"tax_law_{YEAR}.json")
    # The case's mutation goes LAST, on a complete workspace. Written as a
    # derivation rather than as a second builder because every refusal case is
    # the happy path with one thing wrong, and a case built from scratch would
    # be free to differ in ways nobody chose — which is how a case stops
    # measuring the thing it is named for.
    if spec.mutate is not None:
        spec.mutate(dest)
    return dest


def _main(argv: list) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="evals.fixture",
        description="Build the synthetic eval workspace (V3). Nothing here is a "
                    "real person, employer or account.")
    parser.add_argument("--dest", required=True)
    # A case is a WORKSPACE and not only a golden, and this CLI could reach only
    # the base one — so the README's own recipe for driving the halves by hand
    # could not build any case a mutation defines. Harmless while there was one;
    # a second golden makes it load-bearing.
    parser.add_argument("--case", default=CASE,
                        help="which case's workspace to build")
    args = parser.parse_args(argv)
    try:
        dest = build_workspace(args.dest, args.case)
    except ValueError as e:      # an unknown case names the ones there are
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"built the {args.case} workspace in {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
