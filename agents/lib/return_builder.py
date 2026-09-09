#!/usr/bin/env python3
"""Form 1040 lines 1-38, DERIVED rather than transcribed (V1).

Every figure on a 1040 is one of two things, and the distinction is the whole
design here:

  A COMPONENT is a JUDGEMENT. Whether a distribution is taxable, whether to
  itemize, which lots are long-term, how much of a 1099-K is a personal-item
  sale, what the CTC's Credit Limit Worksheet allows. Those stay with the agent
  and with the engine functions it calls; nothing in this module decides one.

  A DERIVED line is ARITHMETIC over components. Line 9 is a sum of eight
  numbers. Line 15 is a subtraction and a floor. Line 16 is the Qualified
  Dividends and Capital Gain Tax Worksheet, which is a published algorithm.
  There is exactly one right answer, it does not depend on reading a document,
  and an agent that types it is doing longhand in prose.

This module owns the second kind, plus the eight lines that are a straight
sum of parse records. It is the same line V2 drew through `tax-parse`: the
ASSEMBLY moves, the judgement does not.

WHY THIS IS WORTH A MODULE. The 1040's own arithmetic had NO check at all.
`tie_out` proves each document reached a line and `capabilities` proves the
engine can model the situation, but nothing recomputed line 9 from lines 1z-8,
or line 24 from 22 and 23. The skill's own reference file warns, in prose, that
line 6b "is IN the sum; omitting it understates AGI by exactly what the
worksheet just computed" — a warning exists because the sum is done by hand.

WHAT DERIVING THE INCOME LINES DOES TO THE TIE-OUT, stated rather than left to
be discovered. Eight of the gate's category checks (wages, both interest lines,
both dividend lines, gross social security, both withholding lines) compare a result line against the
ledger sum that now PRODUCES it. Those eight are not thereby redundant, and the
reason is worth getting right, because "the gate now catches only an edit after
the build" is the tempting answer and it is wrong — step 12.6 catches that, from
the same ledger, one step earlier and with a message that names the component.

`tie_out` is the SPECIFICATION of those eight lines; this module is a constructor
that satisfies it by construction. The gate stays because it is the CROSS-AGENT
hard gate and has to hold on a workspace where this constructor never ran — an
older workspace, a host running the skill sequentially, an agent that skipped
the step. Those checks are tautological only conditional on the builder having
run, and that is exactly the fact a gate cannot assume about itself. Deleting
them would make the gate weaker on every return the builder did not touch.

STEP 12.6 IS NOT RECEIPTED, and that is the honest limit of this slice.
`receipt.REQUIRED_GATES` covers `capabilities` and `tie_out`, so
`/tax-generate --filing-package` can prove those two ran against these inputs
and cannot prove this check did. Folding `differences()` into `tie_out` would
close it and its input set is already identical — but it would fail the hard
gate on every workspace written before line 17 existed, which is a migration
decision and not a line of code. Next slice.

WHAT IS OUT OF SCOPE REFUSES rather than guessing (AGENTS.md §4). A PFIC fund
reclassifies amounts OUT of lines 3a and 3b after the broker reported them, and
this module does not model that; a return carrying one is BLOCKED, not built
with the broker's figures.

THREE LIMITS ARE INHERITED HERE, NOT INTRODUCED, and are written down because a
module that computes line 16 is where a reader will come looking for them.

  - **A qualified dividend the TAXPAYER did not hold long enough** cannot be
    recorded anywhere in this pipeline. Line 3a is derived from the ledger's
    box-1b sum here, and `tie_out` already REQUIRED line 3a to equal that sum —
    the only sanctioned departure is a PFIC reclassification, which has its own
    field. So a holding-period disqualification failed the hard gate before this
    module existed and is refused by it now; the gap is the schema's.
  - **Collectibles (28%) and unrecaptured §1250 gain (25%)** need the Schedule D
    Tax Worksheet rather than the QDCG one. `schedule_d` models no rate-specific
    rows and `tax_computations` has no such worksheet, so an agent running the
    QDCG worksheet by hand had this gap too.
  - **The Tax Table** is required below $100,000 of taxable income and this
    engine uses exact brackets throughout, here and in every planning skill. The
    difference is at most a few dollars and is the repository's convention, not
    this module's choice.

THE PAYMENTS SIDE (second slice) had the same shape and two lines missing.
Lines 25a and 25b are ledger sums like the income lines — box 2 of a W-2 is a
transcription exactly as box 1 is — and 25d, 33, 34, 35a and 37 are arithmetic.
Line 34 and line 37 are the two sides of ONE comparison, each floored at zero,
so exactly one of them is nonzero on any return; deriving 37 as the negation of
34 would print a negative in the box on every refund. The line 38 penalty is IN
the settlement — added to what is owed, taken out of what is refunded — and
only line 34, the form's "amount overpaid", is taken before it.

What was missing there: `line_28_refundable_actc`, which
`compute_child_tax_credit` COMPUTES and the skill routes to line 28, had no
field — so the number was thrown away, line 33 omitted it, and the refund was
short by it for exactly the families the credit exists for. And
`line_36_applied_to_next_year` had none either, so line 35a equalled line 34 for
every filer who applied part of a refund forward. It is RECORDED, not carried:
`/tax-rollover` still has no reader for it, so next year's line 26 is manual.
Both are fields now. `line_27_eic` is a third, and it is
NOT computed anywhere in this repository — the field exists so the figure has
somewhere to land and reaches line 33, with the gap stated on the field itself.
"""

from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from agents.lib.projection import _full_status_key, capital_gains_limits  # noqa: E402
from agents.lib.secure_fs import redact, write_secure  # noqa: E402
from agents.lib.tax_computations import (  # noqa: E402
    compute_bracket_tax,
    compute_qdcg_tax,
    net_capital_gains,
    qdcg_worksheet_applies,
)
from agents.lib.tax_law_fetcher import load_tax_law  # noqa: E402
from agents.lib.tie_out import (  # noqa: E402
    CATEGORY_TARGETS,
    TieOutError,
    category_sums,
    load_ledger,
)

RESULT_NAME = "result.json"
LEDGER_NAME = "parse_ledger.json"
PROFILE_NAME = "taxpayer_profile.json"

# Amounts are compared and written to the cent, like every other engine output.
CENT = 0.005


class ReturnBuilderError(Exception):
    """The return cannot be built from what the workspace holds."""


class Blocked(ReturnBuilderError):
    """Exit 2: the return was read and must not be built from as it stands."""


class OutOfScope(Blocked):
    """...specifically because this slice does not model something it carries."""


# Every `form_1040` field that is a NUMBERED LINE on the form. This is the
# module's boundary, written out once: `schedule_e_k1_income`,
# `digital_asset_question` and `schedule_3_line_11_excess_ss` sit in the same
# section and are not 1040 lines. A test pins this against
# `result_template.json`, so a `line_*` field added there lands either inside
# the boundary or provably outside it — never in the gap between.
SLICE_FIELDS = frozenset({
    "line_1a_wages", "line_1h_other_earned_income", "line_1z_total_wages",
    "line_2a_tax_exempt_interest", "line_2b_taxable_interest",
    "line_3a_qualified_dividends", "line_3b_ordinary_dividends",
    "line_4a_ira_distributions", "line_4b_taxable_ira",
    "line_5a_pensions", "line_5b_taxable_pensions",
    "line_6a_social_security", "line_6b_taxable_ss",
    "line_7_capital_gain", "line_8_schedule1_other",
    "line_9_total_income", "line_10_adjustments", "line_11_agi",
    "line_12_standard_deduction", "line_13a_qbi_deduction",
    "line_13b_schedule_1a_deductions", "line_13c_charitable_non_itemizer",
    "line_14_total_deductions", "line_15_taxable_income", "line_16_tax",
    "line_17_schedule2_part1", "line_18_tax_plus_schedule2",
    "line_19_ctc", "line_20_schedule3_line8", "line_21_total_credits",
    "line_22_tax_minus_credits", "line_23_other_taxes", "line_24_total_tax",
    "line_25a_w2_withholding", "line_25b_1099_withholding",
    "line_25c_other_withholding", "line_25d_total_withholding",
    "line_26_estimated_payments", "line_27_eic", "line_28_refundable_actc",
    "line_29_refundable_aotc", "line_31_schedule3_part2",
    "line_33_total_payments", "line_34_overpayment", "line_35a_refund",
    "line_36_applied_to_next_year", "line_37_amount_owed",
    "line_38_estimated_tax_penalty",
})

# The four refundable credits and other payments that sit between line 25d and
# line 33. Line 32 is the form's own subtotal of 27-31 and has no field: line 33
# sums the parts, so a subtotal would be a second place for the same number to
# be wrong. Excess social security withholding is NOT here — it flows into
# line 31, and adding it again would count it twice (see `_check_the_payments`).
_LINE_33_TERMS = (
    "line_25d_total_withholding", "line_26_estimated_payments", "line_27_eic",
    "line_28_refundable_actc", "line_29_refundable_aotc",
    "line_31_schedule3_part2",
)


def ledger_lines() -> "dict[str, str]":
    """Canonical category -> the 1040 line it IS, for the ones that are a line.

    Read out of `tie_out.CATEGORY_TARGETS` rather than listed again, so the two
    directions cannot disagree about which line a category belongs to. A
    category qualifies only when it names EXACTLY ONE `form_1040` field inside
    this slice: `retirement_gross` names lines 4a and 5a together and the split
    between an IRA and a pension is not recoverable from a sum, and the two
    withholding categories name lines 25a and 25b, which this slice does not
    own. Those stay the agent's, and the gate keeps checking them the way it
    always has.
    """
    out = {}
    for category, (section, fields) in CATEGORY_TARGETS.items():
        if section != "form_1040" or len(fields) != 1:
            continue
        if fields[0] in SLICE_FIELDS:
            out[category] = fields[0]
    return out


# Line 1z is the sum of lines 1a THROUGH 1h, and it is derived from the schema
# rather than listed: `result_template.json` models only 1a and 1h today, and the
# six between them — household-employee wages, unreported tips, Medicaid waiver
# payments, dependent-care benefits, employer adoption benefits, Form 8919 wages
# — have no field at all. Listing the two by hand would leave a seventh silently
# out of the total on the day somebody adds it, which is the failure this whole
# module exists to stop, one level up.
_LINE_1Z_TERMS = tuple(sorted(
    f for f in SLICE_FIELDS
    if re.fullmatch(r"line_1[a-h]_\w+", f) and f != "line_1z_total_wages"))

# The eight terms of line 9, in the order the form prints them. Line 6b — the
# TAXABLE benefit, not line 6a's gross — is in the sum; the reference file
# carries a warning about omitting it, which is what doing this by hand costs.
_LINE_9_TERMS = (
    "line_1z_total_wages", "line_2b_taxable_interest",
    "line_3b_ordinary_dividends", "line_4b_taxable_ira",
    "line_5b_taxable_pensions", "line_6b_taxable_ss",
    "line_7_capital_gain", "line_8_schedule1_other",
)

_LINE_14_TERMS = (
    "line_12_standard_deduction", "line_13a_qbi_deduction",
    "line_13b_schedule_1a_deductions", "line_13c_charitable_non_itemizer",
)


def form_line(field: str) -> str:
    """`line_13a_qbi_deduction` -> `13a`. The form's numbering is IN the name.

    Carrying it as a second field on every entry would be a second copy of a
    fact the field name already states, and the two would part company on the
    first typo.
    """
    return field.split("_")[1]


def _line_order(field: str) -> tuple:
    """Sort key that puts 9 before 11 and 13a before 13b — the printed order."""
    line = form_line(field)
    digits = "".join(c for c in line if c.isdigit())
    return (int(digits or 0), line[len(digits):])


@dataclass(frozen=True)
class Derivation:
    """One derived line: the field, the rule in words, and the rule."""
    field: str
    formula: str
    compute: Callable[[Callable[[str], float], "_Context"], float]


@dataclass(frozen=True)
class _Context:
    """What line 16 needs that the other lines do not: the law and the status.

    `limits` is the 0%/15% breakpoint pair, and it is present EXACTLY when the
    QDCG worksheet runs. One field for both facts rather than two booleans and
    two optionals: "the worksheet applies" and "the breakpoints are loaded" are
    the same question, `qdcg_worksheet_applies` is the one place that answers
    it, and holding them apart is how a caller comes to run half a worksheet.
    """
    brackets: list
    limits: "tuple[float, float] | None"
    qualified_dividends: float
    net_ltcg: float


def _tax(g, ctx: "_Context") -> float:
    """Line 16 — the QDCG worksheet where it applies, the brackets otherwise.

    Which of the two runs was decided in `_context`, by the shared guard: it
    refuses to price preferential income against a cache whose breakpoints were
    never fetched rather than falling through to ordinary rates, which is the
    silent over-statement it exists to stop.
    """
    taxable = g("line_15_taxable_income")
    if ctx.limits is not None:
        cg_zero, cg_fifteen = ctx.limits
        return compute_qdcg_tax(taxable, ctx.qualified_dividends, ctx.net_ltcg,
                                ctx.brackets, cg_zero, cg_fifteen)["final_tax"]
    return compute_bracket_tax(taxable, ctx.brackets)[0]


# ORDER IS THE FORM'S ORDER, and it is load-bearing: each entry may read any
# line above it and none below, which is exactly the dependency the printed
# form encodes. Line 18 must exist for line 22 to be right — see the note on
# `line_17_schedule2_part1` in result_template.json.
DERIVATIONS = (
    Derivation("line_1z_total_wages", "the sum of lines 1a through 1h",
               lambda g, c: sum(g(f) for f in _LINE_1Z_TERMS)),
    Derivation("line_9_total_income", "1z + 2b + 3b + 4b + 5b + 6b + 7 + 8",
               lambda g, c: sum(g(f) for f in _LINE_9_TERMS)),
    Derivation("line_11_agi", "9 - 10",
               lambda g, c: g("line_9_total_income") - g("line_10_adjustments")),
    Derivation("line_14_total_deductions", "12 + 13a + 13b + 13c",
               lambda g, c: sum(g(f) for f in _LINE_14_TERMS)),
    Derivation("line_15_taxable_income", "max(0, 11 - 14)",
               lambda g, c: max(0.0, g("line_11_agi") - g("line_14_total_deductions"))),
    Derivation("line_16_tax",
               "the Qualified Dividends and Capital Gain Tax Worksheet, or the "
               "ordinary brackets when there is no preferential income",
               _tax),
    Derivation("line_18_tax_plus_schedule2", "16 + 17",
               lambda g, c: g("line_16_tax") + g("line_17_schedule2_part1")),
    Derivation("line_21_total_credits", "19 + 20",
               lambda g, c: g("line_19_ctc") + g("line_20_schedule3_line8")),
    Derivation("line_22_tax_minus_credits", "max(0, 18 - 21)",
               lambda g, c: max(0.0, g("line_18_tax_plus_schedule2")
                                - g("line_21_total_credits"))),
    Derivation("line_24_total_tax", "22 + 23",
               lambda g, c: g("line_22_tax_minus_credits") + g("line_23_other_taxes")),
    Derivation("line_25d_total_withholding", "25a + 25b + 25c",
               lambda g, c: (g("line_25a_w2_withholding")
                             + g("line_25b_1099_withholding")
                             + g("line_25c_other_withholding"))),
    Derivation("line_33_total_payments", "25d + 26 + 27 + 28 + 29 + 31",
               lambda g, c: sum(g(f) for f in _LINE_33_TERMS)),
    # THE PENALTY IS IN THE SETTLEMENT, and only line 34 is taken before it.
    # Line 34 is the form's "amount overpaid" — payments against tax, full
    # stop. The 1040's line-37 instruction then says to ADD the line 38
    # estimated-tax penalty to the amount owed, and to take it OUT of the
    # refund when there is one; tax software does the same, which is what lets
    # /tax-verify compare like for like. A first draft of this slice left
    # line 38 out of both and shipped a test asserting so — $4,000 of tax
    # against a $275 penalty came back as $4,000 owed, which is the
    # under-statement direction on a figure the taxpayer pays.
    Derivation("line_34_overpayment", "max(0, 33 - 24)",
               lambda g, c: max(0.0, g("line_33_total_payments")
                                - g("line_24_total_tax"))),
    # Available = 33 - 24 - 38, split between the refund and what is carried
    # forward. A penalty larger than the overpayment leaves nothing to refund
    # and turns the remainder into a balance due, which line 37 below picks up
    # from the same three figures.
    Derivation("line_35a_refund", "max(0, 33 - 24 - 38 - 36)",
               lambda g, c: max(0.0, g("line_33_total_payments")
                                - g("line_24_total_tax")
                                - g("line_38_estimated_tax_penalty")
                                - g("line_36_applied_to_next_year"))),
    # NOT `24 - 33` reused with a sign flip. Line 34 and line 37 are the two
    # sides of one comparison and the form floors each at zero, so exactly one
    # of them is nonzero on any return; deriving 37 as `-34` would put a
    # negative in the box on every overpayment.
    Derivation("line_37_amount_owed", "max(0, 24 + 38 - 33)",
               lambda g, c: max(0.0, g("line_24_total_tax")
                                + g("line_38_estimated_tax_penalty")
                                - g("line_33_total_payments"))),
)

# Form 1040 line 17 — Schedule 2 Part I. Derived, but not a derivation: it is
# INJECTED from another section, exactly as the ledger lines are, and computes
# nothing from the lines around it.
AMT_FIELD = "line_17_schedule2_part1"

# Every derived line, in the order the form PRINTS them, which is also the
# order they are reported in. Sorted from the field names rather than listed:
# a hand-kept order is a third copy of the form's numbering, after the field
# name and the sort key, and it is the copy nothing would notice going stale.
DERIVED_ORDER = tuple(sorted(
    set(ledger_lines().values()) | {AMT_FIELD} | {d.field for d in DERIVATIONS},
    key=_line_order))
DERIVED_FIELDS = frozenset(DERIVED_ORDER)
COMPONENT_FIELDS = SLICE_FIELDS - DERIVED_FIELDS

# What each derived line IS, in words, for the report a refusal prints. The
# agent is told to fix the COMPONENT rather than the line, so the message has
# to say which components the line was built from.
RULES = {d.field: d.formula for d in DERIVATIONS}
RULES.update({field: f"the parse ledger's {category} total"
              for category, field in ledger_lines().items()})
RULES[AMT_FIELD] = "Schedule 2 Part I = the Form 6251 AMT"


# The only two COMPONENTS that may carry a NEGATIVE figure: a capital LOSS on
# line 7 and a Schedule 1 net loss on line 8. Every other input — every income
# line, every deduction, every credit, every tax — is a magnitude, and a
# negative one flows straight through the sums into a total tax below zero,
# which comes back as a refund the taxpayer is not owed. Type and finiteness
# alone do not catch it: -50,000 is a perfectly good float.
#
# DERIVED lines are not held to this. Line 9 is negative for a large enough
# Schedule 1 loss and line 11 for a taxpayer whose business lost money, both of
# which are returns the IRS accepts; line 15 is where it stops mattering and it
# carries the statutory floor.
MAY_BE_NEGATIVE = frozenset({"line_7_capital_gain", "line_8_schedule1_other"})


def _maybe_num(value) -> "float | None":
    """The amount rounded to the cent, or None if it is not a usable one.

    `json.loads` accepts NaN and Infinity, both of which pass an isinstance
    check and then defeat every comparison they touch — `max(0, nan)` is nan,
    and a nan line 15 prices the whole return at nan without raising anywhere.
    A bool passes `isinstance(x, int)`, which is how `true` reads as $1.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return round(float(value), 2)


def _num(value, where: str) -> float:
    """The same question, answered by refusing instead of by None.

    Which of the two a caller wants is the difference between reading a line
    and reporting on one: an unusable COMPONENT must stop the build, while an
    unusable line already on the return is simply one the build replaces.
    """
    checked = _maybe_num(value)
    if checked is None:
        raise ReturnBuilderError(
            f"{redact(where)} must be a finite number, got "
            f"{redact(repr(value))}")
    # The RAW value, not `_maybe_num`'s rounded one: rounding an INPUT would
    # quietly move a component before anything summed it.
    return float(value)


def _path_num(doc: dict, dotted: str, where: str) -> float:
    """A dotted path into a nested section, absent reading as 0.

    The parameter is `dotted`, not `path`: the redaction sweep in
    `tests/test_secure_fs.py` reads a `{path...}` interpolation as a runtime
    filesystem path reaching a message unredacted, and it is structural on
    purpose — it cannot tell this one apart, and a name that has to be argued
    about is the wrong name.
    """
    node: object = doc
    for part in dotted.split("."):
        if not isinstance(node, dict):
            raise ReturnBuilderError(f"{where}: {dotted} is not a nested object")
        node = node.get(part)
        if node is None:
            return 0.0
    return _num(node, where)


def _amt(result: dict) -> float:
    """Schedule 2 Part I, which this slice reads as the Form 6251 AMT alone.

    Part I is line 1 (AMT) plus line 2 (excess advance premium tax credit), and
    Form 8962 is a capability-gate BLOCK, so the AMT is the whole of it on any
    return that reaches here. What it must NOT be is checked in `_check`.
    """
    return round(_path_num(result, "form_6251.amt", "form_6251.amt"), 2)


def _check_the_amt(result: dict) -> None:
    """A PRESENT `form_6251` whose `amt` is null is not a zero AMT: the section
    is written when the form applies, so a null there is a computation that did
    not finish, and read as zero it drops out of total tax exactly as the
    missing line-17 field used to.

    A recorded line 17 ABOVE the AMT means Schedule 2 Part I carries something
    else, and rebuilding it from the AMT alone would take the difference out of
    total tax. Refuse rather than replace. BELOW the AMT is the ordinary
    stale-or-not-yet-filled case — the first build pass runs before Step 6.5 —
    and is simply rebuilt, because a gate that fires on correct in-progress work
    is one an agent learns to reason past.
    """
    form_6251 = result.get("form_6251")
    if isinstance(form_6251, dict) and form_6251.get("amt") is None:
        raise Blocked(
            "form_6251 is present and its `amt` is null. The section is written "
            "when the form applies, so a null AMT is an unfinished computation "
            "rather than a zero — and read as zero it drops straight out of "
            "Form 1040 lines 18, 22 and 24.")
    recorded = (result.get("form_1040") or {}).get(AMT_FIELD)
    if recorded is None:
        return
    recorded = _num(recorded, f"form_1040.{AMT_FIELD}")
    amt = _amt(result)
    if recorded - amt > CENT:
        raise Blocked(
            f"Form 1040 line 17 reads {recorded:,.2f} against a Form 6251 "
            f"AMT of {amt:,.2f}. Schedule 2 Part I is the AMT plus excess "
            "advance premium tax credit, and the second is a capability-"
            "gate block — so a larger line 17 is something this slice does "
            "not model, and rebuilding it from the AMT alone would remove "
            "the difference from total tax.")


def _check_components(lines: dict) -> None:
    """The COMPONENTS, before anything is summed over them.

    Both checks are on the INPUTS only, and that boundary is the point. A
    DERIVED line may legitimately be negative — line 9 with a large Schedule 1
    loss, line 11 for a taxpayer whose business lost money — so checking the
    sign inside the reader every rule calls would refuse a correct return at
    the moment its own arithmetic produced the figure. Line 15 is where a
    negative stops mattering, and it has the statutory floor.
    """
    missing = sorted(f for f in COMPONENT_FIELDS if lines.get(f) is None)
    if missing:
        raise Blocked(
            f"form_1040 is missing {len(missing)} line(s) this slice sums over: "
            f"{', '.join(missing)}. An ABSENT component is not a zero — it is a "
            "line nobody has decided yet, and reading it as zero drops that "
            "income or that tax out of every total below it while the totals "
            "still look computed. `result_template.json` ships every one of "
            "them at 0, so a missing one means the section was never "
            "initialised; run /tax-setup.")
    for field in sorted(COMPONENT_FIELDS - MAY_BE_NEGATIVE):
        value = _num(lines[field], f"form_1040.{field}")
        if value < 0:
            raise Blocked(
                f"form_1040.{field} is {value:,.2f}. Only a capital loss "
                "(line 7) and a Schedule 1 net loss (line 8) may be negative "
                "among the components of Form 1040 lines 1-38; a negative "
                "anywhere else reaches total tax as a refund nobody is owed.")


def _schedule_d_nets(result: dict) -> "tuple[float, float]":
    """Schedule D's short- and long-term nets — the ONE read of them.

    Both the line-7 cross-check and the line-16 worksheet need these, and
    reading them twice is how the check and the computation come to disagree
    about what Schedule D says.
    """
    return (
        _path_num(result, "schedule_d.part_i_short_term.line_7_net_short_term",
                  "schedule_d short-term net"),
        _path_num(result, "schedule_d.part_ii_long_term.line_15_net_long_term",
                  "schedule_d long-term net"),
    )


def check(result: dict) -> None:
    """Everything this module refuses, in one place and before any arithmetic.

    Split across the value-assemblers it was easier to write in, a reader
    looking for "what does this refuse" had to open a function whose name says
    it only gathers numbers.
    """
    _check_in_scope(result)
    _check_components(result.get("form_1040") or {})
    _check_the_amt(result)
    _check_the_payments(result)
    net_st, net_lt = _schedule_d_nets(result)
    _check_line_7_against_schedule_d(result, net_st + net_lt)


def _check_the_payments(result: dict) -> None:
    """Excess social security withholding is inside line 31, not beside it.

    `schedule_3_line_11_excess_ss` is persisted at the 1040 level so
    `/tax-generate` and `/tax-verify` can audit the refundable payment instead
    of watching it fold into a total — but it FLOWS INTO line 31, which is what
    line 33 adds. The pair is the second independent source this section has,
    the way Schedule D is line 7's: a figure recorded on its own line and larger
    than the total it is part of is either double-counted downstream or missing
    from line 31, and both directions move a refund.
    """
    excess = _path_num(result, "form_1040.schedule_3_line_11_excess_ss",
                       "schedule_3_line_11_excess_ss")
    if excess < 0:
        raise Blocked(
            f"form_1040.schedule_3_line_11_excess_ss is {excess:,.2f}. It is a "
            "credit, and a negative one reaches line 33 as a payment nobody "
            "made.")
    part_2 = _path_num(result, "form_1040.line_31_schedule3_part2",
                       "line_31_schedule3_part2")
    if excess - part_2 > CENT:
        raise Blocked(
            f"Schedule 3 line 11 (excess social security withholding) is "
            f"{excess:,.2f} against a Schedule 3 Part II total of "
            f"{part_2:,.2f} on Form 1040 line 31. Line 11 is INSIDE Part II — "
            "line 33 adds line 31 and not the two — so an excess larger than "
            "the total it belongs to means either that line 31 never picked it "
            "up, which loses the credit, or that it is being counted twice.")


def _check_the_refund(available: float, applied: float) -> None:
    """Line 36 cannot exceed what is left after the penalty — checked AFTER
    the build, alone.

    Every other refusal is a pre-condition over components. This one is a
    component bounded by DERIVED figures: what can be carried forward is
    payments minus tax minus the line 38 penalty, and none of the three is
    known until the arithmetic has run.

    The bound is `available`, not line 34, and the difference is the whole
    reason this is a refusal rather than a floor. Line 35a floors at zero, so
    applying forward money the penalty has already consumed would silently
    ZERO the refund and leave the penalty unpaid on a return that shows
    nothing owed — the taxpayer's remaining liability erased by a `max()`.
    """
    # `max(0, available)`, because a balance-due return has a NEGATIVE
    # available and applies nothing forward — comparing against the raw figure
    # blocked every ordinary return that owed tax, on a line reading 0.
    if applied - max(0.0, available) > CENT:
        raise Blocked(
            f"Form 1040 line 36 applies {applied:,.2f} to next year's "
            f"estimated tax, against {max(0.0, available):,.2f} actually available "
            "(line 33 minus line 24 minus the line 38 penalty). Line 35a "
            "floors at zero, so the excess would not show up as a negative "
            "refund — it would vanish, leaving the penalty unpaid on a return "
            "that shows nothing owed. Reduce what is carried forward.")


def _check_line_7_against_schedule_d(result: dict, combined: float) -> None:
    """Line 7 must be the Schedule D result, and this is the guard the totals
    cannot supply for themselves.

    Every derived line reads its components and believes them, so a component
    the agent has not filled in reads 0 and the total below it is computed over
    a return missing that income — while the QDCG worksheet, which reads
    Schedule D DIRECTLY, still prices the gain and shrinks the ordinary band by
    it. Both halves look computed and the tax comes out low, which is the
    direction AGENTS.md §4 makes a hard error. Line 7 is the one component with
    an independent second source in the file, so it is the one that can be
    checked rather than trusted.

    A net GAIN must equal line 7 exactly. A net LOSS may not: §1211 caps the
    deduction at 3,000 (1,500 MFS) and the Capital Loss Carryover Worksheet
    decides how much of it is used, which is the agent's judgement and not
    recomputed here — so the check is only that line 7 lies between the full
    loss and zero. That is the widest band the statute allows and it still
    catches a dropped or sign-flipped line.
    """
    line_7 = (result.get("form_1040") or {}).get("line_7_capital_gain")
    line_7 = _num(0 if line_7 is None else line_7, "form_1040.line_7_capital_gain")
    if combined >= 0:
        ok = abs(line_7 - combined) < CENT
    else:
        ok = combined - CENT <= line_7 <= CENT
    if not ok:
        raise Blocked(
            f"Form 1040 line 7 reads {line_7:,.2f} against a Schedule D that "
            f"nets {combined:,.2f}. A net gain goes on line 7 whole; a net loss "
            "goes on capped by §1211 at 3,000 (1,500 MFS), so line 7 must lie "
            "between the loss and zero. Line 7 is the one component with a "
            "second source in this file, and building the totals over a line 7 "
            "that disagrees with it drops the gain from income while the "
            "worksheet still prices it — the under-statement direction.")


def _check_in_scope(result: dict) -> None:
    """Refuse the situations this slice cannot build correctly."""
    pfic = result.get("form_8621_pfic")
    funds = (pfic or {}).get("funds") if isinstance(pfic, dict) else None
    if isinstance(funds, list) and funds:
        raise OutOfScope(
            "the return carries a computed PFIC fund. §1291 moves amounts OUT "
            "of lines 3a and 3b after the broker reported them "
            "(reclassified_from_line_3a / _3b) and adds a deferred-tax charge "
            "to line 24 that is not line 22 plus line 23. This slice models "
            "neither, and building lines 3a/3b from the ledger would restore "
            "the qualified treatment §1291 forbids. Keep Form 1040 lines 1-38 "
            "by hand for this return.")


def ledger_income(ledger, where: str = "parse_ledger.json") -> "dict[str, float]":
    """The 1040 income lines that ARE a sum of the parse records."""
    sums = category_sums(ledger)
    lines = ledger_lines()
    out = {}
    for category, field in lines.items():
        total = round(sums.get(category, 0.0), 2)
        if total < 0:
            # Only `ss_benefits` can legitimately go negative (repayments
            # exceeding gross benefits), and the tie-out already records that
            # line 6a has no stable convention for it. Refusing is the same
            # answer said earlier: a negative gross benefit on the form is not
            # a figure this module gets to invent.
            raise OutOfScope(
                f"{redact(where)} sums {category} to {total:,.2f}, a NEGATIVE total for "
                f"Form 1040 {field.split('_')[1]}. Repayments exceeding gross "
                "benefits have no line-6a convention to derive — taxable "
                "benefits are $0 and the repayment may be deductible (Pub 915). "
                "Complete this line by hand and verify before filing.")
        out[field] = total
    return out


def _context(result: dict, tax_law: dict, filing_status: str) -> _Context:
    """The law and the Schedule D figures line 16 prices.

    `net_ltcg` is Schedule D line 15 run through the shared `net_capital_gains`
    character rule, not line 7 and not the long-term row alone: a short-term
    loss absorbed by a long-term gain leaves a gain that is still preferential,
    and a net loss is not preferential at all. Every other consumer of the
    worksheet nets the same way through the same function.
    """
    key = _full_status_key(filing_status, "return builder")
    federal = tax_law.get("federal") or {}
    statuses = federal.get("filing_statuses") or {}
    brackets = (statuses.get(key) or {}).get("brackets")
    if not isinstance(brackets, list) or not brackets:
        raise OutOfScope(
            f"tax_law federal.filing_statuses.{key}.brackets is missing or "
            "empty, so Form 1040 line 16 cannot be computed. Run /tax-setup.")
    net_st, net_lt = _schedule_d_nets(result)
    cap_input, _ordinary_short = net_capital_gains(net_st, net_lt)
    # The floor below is what decides whether the WORKSHEET RUNS, not what the
    # worksheet is handed: `compute_qdcg_tax` clamps a negative `net_ltcg`
    # itself, so a small loss reaches the same tax either way. A loss LARGER
    # than the qualified dividends is the case that bites — unfloored, the
    # preferential total goes negative, `qdcg_worksheet_applies` reports False,
    # and the dividends are priced at ordinary rates. `projection` floors it at
    # the same point for the same reason.
    form_1040 = result.get("form_1040") or {}
    qualified = _num(form_1040.get("line_3a_qualified_dividends", 0),
                     "form_1040.line_3a_qualified_dividends")
    net_ltcg = max(0.0, cap_input)
    cg_zero, cg_fifteen = capital_gains_limits(tax_law, filing_status, "return builder")
    # The pair, and then whether it MATTERS — in that order and not the other
    # way round. `pair` only restates "the cache carries both breakpoints";
    # `qdcg_worksheet_applies` is what decides whether a missing one is a
    # refusal (there is preferential income to price) or simply irrelevant (a
    # wages-only return), and it raises rather than returning True on half a
    # pair. So `pair` is never a second guard: when it is None and the answer
    # matters, the line below has already raised.
    pair = None if cg_zero is None or cg_fifteen is None else (cg_zero, cg_fifteen)
    try:
        worksheet = qdcg_worksheet_applies(qualified + net_ltcg, cg_zero, cg_fifteen)
    except ValueError as e:
        # An OutOfScope so the CLI reports it as a BLOCK naming /tax-setup
        # rather than as a crash.
        raise OutOfScope(str(e)) from None
    return _Context(
        brackets=brackets,
        limits=pair if worksheet else None,
        qualified_dividends=qualified,
        net_ltcg=net_ltcg,
    )


def build(result: dict, tax_law: dict, ledger, filing_status: str) -> "dict[str, float]":
    """Every derived line of Form 1040 lines 1-38, from the components.

    Returns the field -> value map; it does not mutate `result`. The INJECTED
    lines — the ledger's eight and the AMT — land on the working copy FIRST,
    because line 9 sums lines the ledger produces: deriving a total from the
    figures already on the return while replacing those figures underneath it
    is how a total ends up describing an earlier version of the same return.
    """
    if not isinstance(result, dict) or not isinstance(result.get("form_1040"), dict):
        raise ReturnBuilderError(f"{RESULT_NAME} has no form_1040 section")
    check(result)

    working = {**result["form_1040"], **ledger_income(ledger),
               AMT_FIELD: _amt(result)}

    def g(field: str) -> float:
        return _num(working.get(field, 0), f"form_1040.{field}")

    ctx = _context({**result, "form_1040": working}, tax_law, filing_status)
    for d in DERIVATIONS:
        working[d.field] = round(d.compute(g, ctx), 2)
    _check_the_refund(
        g("line_33_total_payments") - g("line_24_total_tax")
        - g("line_38_estimated_tax_penalty"),
        g("line_36_applied_to_next_year"))
    return {field: working[field] for field in DERIVED_ORDER}


@dataclass(frozen=True)
class Change:
    field: str
    was: "float | None"   # None = the line is absent, or is not a usable number
    now: float


def differences(result: dict, derived: "dict[str, float]") -> "list[Change]":
    """Every derived line whose recorded value is not what the rules produce.

    Walked in DERIVED_ORDER, so the result is already in the form's order and
    every key is one `build` produced — no fallback for a field that cannot
    occur, which would read as a guard over nothing.
    """
    form_1040 = result.get("form_1040") or {}
    out = []
    for field in DERIVED_ORDER:
        value = derived[field]
        was = _maybe_num(form_1040.get(field))
        if was is not None and abs(was - value) < CENT:
            continue
        out.append(Change(field=field, was=was, now=value))
    return out


def _read_json(path: Path, what: str) -> dict:
    """`what` and `_num`'s `where` are both PUBLIC labels, so both are
    redacted where they are interpolated even though every caller today passes
    a bare filename. The structural sweep in `tests/test_secure_fs.py` reads
    `{path}`-shaped names, not `{what}`, so it is blind to the day a caller
    passes an absolute workspace path to make a message more precise — and on a
    taxpayer's machine that path is their name. `redact` is a no-op on a bare
    filename, which is the point: the safety is visible rather than argued.
    """
    if not path.exists():
        raise ReturnBuilderError(f"{redact(what)} not found in the workspace")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ReturnBuilderError(
            f"{redact(what)} could not be read: {redact(e)}") from None
    if not isinstance(data, dict):
        raise ReturnBuilderError(f"{redact(what)} must be a JSON object")
    return data


def _filing_status(profile: dict, result: dict) -> str:
    """The status, from the return's own meta and then the profile.

    Same precedence as every other consumer: `result.json` meta records what
    this return was computed as, and a profile edited after the fact must not
    silently re-price a return that was built under the old status.
    """
    status = (result.get("meta") or {}).get("filing_status")
    if not status:
        status = profile.get("filing_status")
    if not isinstance(status, str) or not status.strip():
        raise ReturnBuilderError(
            "no filing status in result.json meta or taxpayer_profile.json — "
            "the brackets and the capital-gains breakpoints are per status")
    return status


def _tax_year(result: dict, profile: dict) -> int:
    year = (result.get("meta") or {}).get("tax_year") or profile.get("tax_year")
    if isinstance(year, bool) or not isinstance(year, int):
        raise ReturnBuilderError(
            f"no usable tax year in {RESULT_NAME} meta or {PROFILE_NAME}; "
            f"got {redact(repr(year))}")
    # The 2000-2099 RANGE is `get_tax_law_path`'s, reached through
    # `load_tax_law` below, and is not re-checked here: that bound already has
    # five copies in this package and a sixth would be one more thing to keep
    # true.
    return year


def load(workspace: Path) -> tuple:
    """The four inputs, each validated by whoever owns its rules.

    The cache goes through `tax_law_fetcher.load_tax_law`, not a plain JSON
    read: that is the ONE read site every consumer shares, and it refuses two
    things nothing here would have caught. A cache reached through a symlink or
    an NTFS junction is refused rather than followed, and a non-finite number
    ANYWHERE in it is refused rather than carried — a NaN bracket rate passes
    the "is this a non-empty list" check below and then defeats every
    comparison inside the worksheet, which is how a corrupt cache prices a
    whole preferential stack at 0%.
    """
    workspace = Path(workspace)
    result = _read_json(workspace / RESULT_NAME, RESULT_NAME)
    profile = _read_json(workspace / PROFILE_NAME, PROFILE_NAME)
    raw_ledger = _read_json(workspace / LEDGER_NAME, LEDGER_NAME)
    try:
        ledger = load_ledger(raw_ledger)
    except TieOutError as e:
        raise ReturnBuilderError(f"{LEDGER_NAME} is not valid: {redact(e)}") from None
    year = _tax_year(result, profile)
    try:
        tax_law = load_tax_law(year, str(workspace))
    except ValueError as e:
        raise ReturnBuilderError(f"the tax-law cache: {redact(e)}") from None
    if tax_law is None:
        raise ReturnBuilderError(
            f"tax_law_{year}.json is missing or corrupt. Run /tax-setup to "
            "fetch it; Form 1040 line 16 cannot be computed without it.")
    return result, profile, ledger, tax_law


def write(workspace: Path, result: dict, derived: "dict[str, float]") -> bool:
    """Publish the derived lines. Returns whether anything was written.

    NOT WRITING AN UNCHANGED FILE IS THE CONTRACT, not an optimization: V8's
    validation receipt binds each gate's verdict to the digest of the files it
    read, so republishing an identical `result.json` moves that digest and
    invalidates a pass that is still true. `/tax-generate --filing-package`
    then refuses a package whose gates did run.
    """
    if not differences(result, derived):
        return False
    updated = {**result, "form_1040": {**result["form_1040"], **derived}}
    # `write_secure` is temp-then-replace with the descriptor secured before
    # the first byte, so there is nothing to chmod afterwards; the trailing
    # `secure_path` this used to carry was a no-op that would have run AFTER
    # `os.replace` and reported exit 1 over a write that had already landed.
    #
    # No trailing newline, matching `rollover.apply_changes` — the other writer
    # of this file. Two writers with two byte-formats churn the digest V8's
    # receipt binds each gate's verdict to, which is the thing this function
    # goes out of its way not to do.
    write_secure(Path(workspace) / RESULT_NAME, json.dumps(updated, indent=2))
    return True


def _report(changes: "list[Change]") -> str:
    """One row per line, and the RULE beside it.

    The agent is told to fix the component rather than the line, so the message
    has to say which components the line was built from — a bare
    `line 24: 99.00 -> 4,000.00` leaves it guessing at what to look at.
    """
    rows = []
    for c in changes:
        was = "absent" if c.was is None else f"{c.was:,.2f}"
        rows.append(f"  line {form_line(c.field):<3} {was:>14} -> {c.now:>14,.2f}"
                    f"   {c.field}  ({RULES[c.field]})")
    return "\n".join(rows)


def _main(argv: list) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="agents.lib.return_builder",
        description="Derive Form 1040 lines 1-38 from the parsed records, the "
                    "profile and the tax-law cache (V1, first slice).")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--check", action="store_true",
                        help="report the lines that disagree and write nothing; "
                             "exit 2 if any do")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if not exc.code else 1

    workspace = Path(args.workspace)
    if not workspace.is_dir():
        print(f"Error: no workspace at {redact(workspace)}", file=sys.stderr)
        return 1
    try:
        result, profile, ledger, tax_law = load(workspace)
        derived = build(result, tax_law, ledger, _filing_status(profile, result))
        changes = differences(result, derived)
        if args.check:
            if changes:
                print(f"BLOCKED: {len(changes)} derived line(s) on Form 1040 "
                      f"disagree with the components they are computed from:\n"
                      f"{_report(changes)}", file=sys.stderr)
                return 2
            print(f"All {len(derived)} derived lines of Form 1040 lines 1-38 "
                  "agree with their components.")
            return 0
        if write(workspace, result, derived):
            print(f"Rewrote {len(changes)} derived line(s) of Form 1040 "
                  f"lines 1-38:\n{_report(changes)}\n"
                  "Re-read result.json before using any figure below the "
                  "highest line listed.")
        else:
            print(f"All {len(derived)} derived lines of Form 1040 lines 1-38 "
                  "already agree with their components; nothing written.")
    except Blocked as e:
        # OutOfScope is a Blocked, so this one arm covers both: a return this
        # slice does not model and a return whose own components contradict
        # each other are the same verdict — read, and refused.
        print(f"BLOCKED: {redact(e)}", file=sys.stderr)
        return 2
    except ReturnBuilderError as e:
        print(f"Error: {redact(e)}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"Error: {redact(e)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(_main(sys.argv[1:]))
