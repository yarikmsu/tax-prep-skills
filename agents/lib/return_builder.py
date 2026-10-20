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
ASSEMBLY moves, the judgement does not. AA65 additionally owns line 8 when
its government-benefits workpaper applies: confirmed non-benefit income plus
taxable UI and governmental family benefits. The extended workpaper check and
CA check run before a federal scope waiver; the base line roster stays stable.

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

STEP 12.6 IS RECEIPTED, under this module's own gate name rather than folded
into the tie-out. `--check` records its verdict through `receipt`, and
`receipt.REQUIRED_GATES` holds all three names, so `/tax-generate
--filing-package` refuses a package whose derived lines were never checked,
were checked against other inputs, or disagreed. Folding `differences()` into
`tie_out` was the other candidate and is the wrong shape twice over: this
module imports `tie_out` for the ledger rules, so the dependency cannot run
both ways, and the two gates do not read the same files — the tie-out reads the
source documents and this one cannot, which `receipt.gate_inputs` says in one
place. A workspace written before these lines existed is refused with the gate
named and re-runs Step 12.6; that is the same one-command migration V8 already
asks of the other two gates, not a new kind of demand.

MAKING THE VERDICT LOAD-BEARING MOVED THE SCOPE TEST TO THE VERY END. While out
of scope exited 2, deciding it first cost nothing. As a PASS it waived every
refusal behind it: a PFIC return with a negative line 23, a null AMT, an
excess-SS double count, a line 7 contradicting Schedule D, or a cache with no
brackets at all came back NOT CHECKED and was certified — on one field read
before the return had been looked at at all. `_check_in_scope` is now asked LAST
in `build`, after `check` and after `_context`, so the waiver is granted only to
a return that is otherwise sound AND that this year's cache can price.

AND THE WAIVER IS ONLY EVER WHOLE-RETURN. A refusal about ONE LINE, raised in
the middle of the build, cannot take it: `ledger_income`'s negative
social-security total is a `Blocked`, because passing there waived the
comparison of every other line and a hand-typed line 35a refund of $5,000 rode
through all three gates into a filing package. That is also what the case did
before this gate was receipted, so nothing regresses — the fix that would let an
SSA-repayment return complete is per-LINE declination, and it is written down in
`docs/contributing/tax-gotchas.md` rather than guessed at here.

THE §1291 PREDICATE IS THE ONE PLACE A FALSE POSITIVE IS THE DANGEROUS
DIRECTION, because it grants that waiver. `_pfic_is_in_play` reads `funds` and
the four totals that MEAN §1291 moved a line this module derives, and nothing
else: the section also records what the return WOULD have been without §1291
(`line_16_regular_tax` and its siblings), and reading those as evidence let a
return with no PFIC at all be waived. `funds` alone was the other error — the
reclassification totals are its SIBLINGS, so a section §1291 had reworked with
an empty fund list read as in scope.

AND SPLIT ONE EXCEPTION IN TWO. `OutOfScope` was
carrying both "this module does not govern this return's lines" (a computed PFIC
fund) and "the cache cannot price this return" (no brackets, no capital-gains
breakpoints). The first has no verdict to give and must not stand between a
correct return and its filing package; the second is a return nobody can compute
yet and must never ride into one. They are `OutOfScope` and `Unpriceable` now,
and only the first passes the check.

A PASS THAT VERIFIED NOTHING IS SAID OUT LOUD. `receipt.verify` returns it as a
NOTE beside the reasons; `/tax-generate` puts it in the page-1 limitations box
and heads the filing package NOT FULLY VERIFIED. Not the draft banner — the
package does describe the workspace and every gate that had a verdict passed —
and not silence, which is what AGENTS.md §4 forbids in those words. A line on
stderr at Step 12.6 is not a disclosure the taxpayer ever sees.

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
from agents.lib import receipt  # noqa: E402
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
    derive_ca_schedule_ca,
    ca_values_agree,
    derive_government_workpaper,
    government_workpaper_differences,
    derive_schedule1_other_workpaper,
    schedule1_other_differences,
    CATEGORY_TARGETS,
    TieOutError,
    _SIGNED_CATEGORIES as SIGNED_CATEGORIES,
    category_sums,
    load_ledger,
)
from agents.lib.workspace import (  # noqa: E402
    WorkspaceError,
    add_workspace_argument,
    hold_workspace,
    resolve_workspace,
)

RESULT_NAME = "result.json"
LEDGER_NAME = "parse_ledger.json"
PROFILE_NAME = "taxpayer_profile.json"

# The name this module's verdict is recorded under in `validation_receipt.json`
# — `receipt.REQUIRED_GATES` holds it, so /tax-generate --filing-package refuses
# a package whose Step 12.6 never ran, ran on other inputs, or failed.
GATE = "return_builder"

# Amounts are compared and written to the cent, like every other engine output.
CENT = 0.005


class ReturnBuilderError(Exception):
    """The return cannot be built from what the workspace holds."""


class Blocked(ReturnBuilderError):
    """Exit 2: the return was read and must not be built from as it stands."""


class OutOfScope(Blocked):
    """...specifically because this module does not GOVERN this return's lines.

    A computed PFIC fund, or a net-negative social security total: the form's
    lines 1-38 stay with the agent, by hand, and this module has no verdict to
    give about them. That is not the same as a refusal, and `--check` does not
    treat it as one — it records `receipt.NOT_APPLICABLE` and exits 0, because a
    gate that blocked a correct return's filing package forever would be firing
    on right answers. BUILDING is still refused: exit 2, write nothing.
    """


class Unpriceable(Blocked):
    """...specifically because the tax-law cache cannot price this return.

    Separated from `OutOfScope` when Step 12.6 became receipted, and the
    separation is the whole point: a cache with no brackets or no capital-gains
    breakpoints is a return nobody can compute yet (AGENTS.md §2), not one this
    module declines to govern. Folded together, an unfetched cache would have
    ridden into a filing package under the out-of-scope pass.
    """


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


def check(result: dict, profile: "dict | None" = None) -> None:
    """Everything wrong with the return AS RECORDED, in one place and before
    any arithmetic.

    `profile` is OPTIONAL and that is a narrow allowance, not a default: one
    check below needs a figure that lives in `taxpayer_profile.json` and
    nowhere on the return, and every caller inside this module passes it. A
    caller that does not gets the other checks and is TOLD, in
    `_check_the_prior_overpayment`'s own refusal, which one it did not get.

    Split across the value-assemblers it was easier to write in, a reader
    looking for "what does this refuse" had to open a function whose name says
    it only gathers numbers.

    What is NOT here is `_check_in_scope`, and its absence is deliberate.
    Everything above is a defect in the return; scope is this module declining
    to govern a correct one, `--check` records that as a PASS, and a pass must
    never be granted ahead of a refusal it would waive. So `build` asks it LAST
    — after these checks and after the return has been priced.
    """
    _check_components(result.get("form_1040") or {})
    _check_the_amt(result)
    _check_the_payments(result)
    _check_the_prior_overpayment(result, profile)
    _check_charitable_vintages(result, profile)
    if profile is not None:
        from agents.lib.tax_computations.federal.ira_basis import validate_individual_8606
        try:
            validate_individual_8606(result, profile)
        except (ValueError, TypeError, KeyError) as exc:
            raise Blocked(f"Individual IRA basis unresolved: {exc}") from exc
    elif (result.get("form_8606") or {}).get("schema"):
        raise Blocked("Individual IRA validation needs the taxpayer profile")
    net_st, net_lt = _schedule_d_nets(result)
    _check_line_7_against_schedule_d(result, net_st + net_lt)


def _check_charitable_vintages(result, profile):
    """Bind each worksheet settlement to the opening profile and actual return."""
    from agents.lib.tax_computations.charitable import settle_charitable_ledger
    opening = ((profile or {}).get("prior_year") or {}).get("charitable_carryforward_ledger")
    if opening is None:
        opening = {}
    if not isinstance(opening, dict):
        raise Blocked("Opening charitable ledgers must be an object")
    for jurisdiction, name in (("federal", "schedule_a"), ("CA", "schedule_ca_itemized")):
        section = result.get(name) or {}
        if not isinstance(section, dict):
            raise Blocked("Charitable result section must be an object")
        prior = opening.get(jurisdiction)
        ledger = section.get("charitable_ledger")
        if prior is None and ledger is None and not section.get("charitable_carryforward"):
            continue
        if profile is None:
            raise Blocked("Charitable vintage validation needs the taxpayer profile")
        inputs = (section.get("inputs") if jurisdiction == "federal"
                  else section.get("charitable_inputs")) or {}
        if not isinstance(inputs, dict):
            raise Blocked("Charitable worksheet inputs must be an object")
        try:
            if inputs.get("charitable_prior_ledger") != prior:
                raise ValueError("Charitable worksheet opening differs from the profile")
            agi = (result.get("form_1040") or {}).get("line_11_agi")
            settled = settle_charitable_ledger(
                prior, inputs.get("charitable_worksheet"),
                tax_year=(result.get("meta") or {}).get("tax_year"),
                jurisdiction=jurisdiction, agi=agi,
                charitable_cash=inputs.get("charitable_cash"),
                charitable_noncash=inputs.get("charitable_noncash"))
            for key, expected in settled.items():
                if section.get(key) != expected:
                    raise ValueError("Charitable result differs from its reviewed vintage settlement")
            if inputs["charitable_worksheet"]["itemizes"] != section.get("use_itemized"):
                raise ValueError("Charitable worksheet differs from the return's deduction election")
        except (ValueError, TypeError, KeyError) as exc:
            raise Blocked(f"{jurisdiction} charitable carryover unresolved: {exc}") from exc


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


def _check_the_prior_overpayment(result: dict, profile: "dict | None") -> None:
    """LAST YEAR'S line 36 is part of THIS year's line 26, and nothing else
    would notice it missing.

    Form 1040 line 26 is "estimated tax payments and amount applied from
    {year-1} return" — one line carrying two things. The second has no
    document: an applied overpayment is a figure the taxpayer's PREVIOUS return
    carried forward, so it produces no 1099, no ledger record, and the tie-out
    gate — which compares intake against the return — cannot see it at all. The
    only place it exists in this workspace is
    `taxpayer_profile.json`'s `prior_year.estimated_payments_applied`, which
    /tax-rollover fills from the prior result's line 36.

    THE DIRECTION IS THE ONE AGENTS.md §4 CALLS A HARD ERROR. Omitting it
    under-states line 26, which under-states line 33's payments, which
    OVER-states line 37's amount owed — the taxpayer pays money they already
    paid. Nothing downstream catches it: every total is correct arithmetic over
    a component that is simply too small.

    An INEQUALITY, deliberately. Line 26 is the applied overpayment PLUS any
    quarterly estimates paid during the year, so it can exceed this figure by
    any amount; it can never fall below it. A caller that passes no profile
    cannot run the check at all, and it says so rather than passing — a check
    that silently does not apply is the vacuity the receipt exists to prevent.
    """
    applied = (((profile or {}).get("prior_year") or {})
               .get("estimated_payments_applied"))
    if profile is None or applied in (None, 0):
        return
    applied = _num(applied, "prior_year.estimated_payments_applied")
    if applied < 0:
        raise Blocked(
            f"taxpayer_profile.json prior_year.estimated_payments_applied is "
            f"{applied:,.2f}. An overpayment applied forward is money already "
            "paid; a negative one reaches line 26 as a payment being taken "
            "away.")
    line_26 = _path_num(result, "form_1040.line_26_estimated_payments",
                        "line_26_estimated_payments")
    if applied - line_26 > CENT:
        raise Blocked(
            f"Form 1040 line 26 is {line_26:,.2f}, and last year's return "
            f"applied {applied:,.2f} forward to this year's estimated tax "
            "(taxpayer_profile.json prior_year.estimated_payments_applied). "
            "Line 26 is \"estimated tax payments AND amount applied from the "
            "prior-year return\" — one line carrying both — so it cannot be "
            "less than what was applied. Add the applied overpayment to any "
            "quarterly estimates actually paid. Left out, line 33's payments "
            "are short by that much and line 37 bills the taxpayer for money "
            "they have already paid.")


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


# The `form_8621_pfic` fields whose non-zero value MEANS §1291 moved a line this
# module derives: the two reclassifications out of lines 3a and 3b, and the
# add-on to line 24 under both the names the section records it under. Named
# EXPLICITLY, and this is the one predicate in the module where a false positive
# is the dangerous direction — it grants the waiver `--check` records as a pass,
# so "count anything non-zero" reads the section's own counterfactuals
# (`line_16_regular_tax` is what the return WOULD have been WITHOUT §1291) as
# evidence that §1291 ran. A test pins every other key in the shipped section as
# deliberately not evidence, so a field added there lands on one side or the
# other rather than in the gap between.
_PFIC_EVIDENCE = ("reclassified_from_line_3a", "reclassified_from_line_3b",
                  "total_1291_addon", "line_24_addon")


def _pfic_is_in_play(result: dict) -> bool:
    """Whether §1291 has moved a line this module derives — ONE predicate.

    `funds` is the per-fund detail and the reclassification totals are its
    SIBLINGS, not entries inside it, so a section carrying
    `reclassified_from_line_3b` with an empty `funds` list is a return §1291 has
    reworked and a funds-only test calls in scope. The builder would then rebuild
    line 3b from the ledger and restore the ordinary/qualified treatment §1291
    removed — the exact outcome this refusal exists to prevent, reached through
    the gap between two spellings of one question. The tie-out keys the same
    question on those totals (`tie_out._result_total`), so this reads BOTH
    rather than picking whichever one a partly-filled section happens to have.
    """
    section = result.get("form_8621_pfic")
    if not isinstance(section, dict):
        return False
    if section.get("funds"):
        return True
    return any(
        isinstance(section.get(key), (int, float))
        and not isinstance(section.get(key), bool) and section.get(key) != 0
        for key in _PFIC_EVIDENCE)


def _check_in_scope(result: dict) -> None:
    """Refuse the situations this module does not GOVERN.

    Called LAST from `check`, after every refusal that applies to any return —
    see the note there. What this raises is an `OutOfScope`, which `--check`
    records as a pass, so it must never be the reason a checkable defect went
    unlooked-at.
    """
    if _pfic_is_in_play(result):
        raise OutOfScope(
            "the return carries a computed §1291 PFIC result. It moves amounts OUT "
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
        if total < 0 and category in SIGNED_CATEGORIES:
            # `ss_benefits` is the one category that can legitimately go
            # negative (repayments exceeding gross benefits), and the tie-out
            # already records that line 6a has no stable convention for it. A
            # negative gross benefit on the form is not a figure this module
            # gets to invent.
            #
            # BLOCKED, not OUT OF SCOPE, and the difference is the whole reason
            # the two exist. This refusal is about ONE LINE, and it fires in the
            # middle of the build — so granting the pass here would waive the
            # comparison of every OTHER line on the return: a hand-typed line
            # 35a refund of $5,000 on a return with no payments and no tax rode
            # straight through all three gates into a filing package. The pass
            # belongs only to a WHOLE-RETURN condition decided before anything
            # is derived. This is also what this case did before Step 12.6 was
            # receipted, so nothing regresses; deriving the rest of the return
            # and declining line 6a alone is the fix that would let an
            # SSA-repayment return complete, and it is written down in
            # docs/contributing/tax-gotchas.md rather than guessed at here.
            raise Blocked(
                f"{redact(where)} sums {category} to {total:,.2f}, a NEGATIVE total for "
                f"Form 1040 {field.split('_')[1]}. Repayments exceeding gross "
                "benefits have no line-6a convention to derive — taxable "
                "benefits are $0 and the repayment may be deductible (Pub 915). "
                "Keep Form 1040 lines 1-38 by hand for this return; this gate "
                "cannot stand aside for one line without standing aside for "
                "all of them.")
        if total < 0:
            # Any OTHER category summing negative is corrupt data, not a return
            # this module declines to govern, and the two must not share a
            # verdict now that one of them passes: `load_ledger` refuses a
            # negative value in an unsigned category record by record, so a
            # negative SUM here means these records did not come through it.
            # Unreachable on the CLI path, where `load` always does — and worth
            # a line rather than a shared message that tells every category the
            # social-security story.
            raise Blocked(
                f"{redact(where)} sums {category} to {total:,.2f}, and only "
                f"{'/'.join(sorted(SIGNED_CATEGORIES))} may be negative. The "
                "ledger's own validator refuses a negative in any other "
                "category, so these records did not come through it.")
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
        raise Unpriceable(
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
        # An Unpriceable so the CLI reports it as a BLOCK naming /tax-setup
        # rather than as a crash.
        raise Unpriceable(str(e)) from None
    return _Context(
        brackets=brackets,
        limits=pair if worksheet else None,
        qualified_dividends=qualified,
        net_ltcg=net_ltcg,
    )


def government_workpaper(result: dict, ledger, profile: dict) -> "dict | None":
    try:
        return derive_government_workpaper(ledger, result, profile)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        raise Blocked(f"Government benefits: {redact(exc)}") from None


def schedule1_other_workpaper(result: dict, ledger, profile: dict) -> "dict | None":
    try:
        return derive_schedule1_other_workpaper(ledger, result, profile)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        raise Blocked(f"Schedule1 other income: {redact(exc)}") from None


def government_differences(result: dict, built: "dict | None") -> list[str]:
    try:
        return government_workpaper_differences(result, built)
    except ValueError as exc:
        raise Blocked(f"Government benefits: {redact(exc)}") from None


def build(result: dict, tax_law: dict, ledger, filing_status: str,
          profile: "dict | None" = None) -> "dict[str, float]":
    """Every derived line of Form 1040 lines 1-38, from the components.

    Returns the field -> value map; it does not mutate `result`. The INJECTED
    lines — the ledger's eight and the AMT — land on the working copy FIRST,
    because line 9 sums lines the ledger produces: deriving a total from the
    figures already on the return while replacing those figures underneath it
    is how a total ends up describing an earlier version of the same return.
    """
    if not isinstance(result, dict) or not isinstance(result.get("form_1040"), dict):
        raise ReturnBuilderError(f"{RESULT_NAME} has no form_1040 section")
    other_income = schedule1_other_workpaper(result, ledger, profile or {})
    government = government_workpaper(result, ledger, profile or {})
    settled_income = (government["total"] if government is not None else
                      other_income["total"] if other_income is not None else None)
    result = ({**result, "form_1040": {**result["form_1040"],
                                     "line_8_schedule1_other": settled_income}}
              if settled_income is not None else result)
    check(result, profile)

    working = {**result["form_1040"], **ledger_income(ledger),
               AMT_FIELD: _amt(result)}

    def g(field: str) -> float:
        return _num(working.get(field, 0), f"form_1040.{field}")

    ctx = _context({**result, "form_1040": working}, tax_law, filing_status)
    # LAST OF ALL, and after `_context`: the waiver `--check` records as a pass
    # is granted only to a return that is otherwise sound AND that this year's
    # cache can actually price. Asked earlier, it waived every refusal behind it
    # — a return with no brackets in the cache came back NOT CHECKED and was
    # certified, which is the one thing AGENTS.md §2 says nothing downstream
    # catches.
    _check_in_scope(result)
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
    """The parsed document alone, for a caller with no receipt to bind."""
    return _read_json_bytes(path, what)[0]


def _read_json_bytes(path: Path, what: str) -> "tuple[dict, bytes]":
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
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as e:
        raise ReturnBuilderError(
            f"{redact(what)} could not be read: {redact(e)}") from None
    if not isinstance(data, dict):
        raise ReturnBuilderError(f"{redact(what)} must be a JSON object")
    return data, raw


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
    """The four inputs, each validated by whoever owns its rules, AND the exact
    bytes each was parsed from.

    The bytes are the fifth return value because the receipt is bound to them.
    Hashing the paths separately — before the read, as the other gates do —
    leaves a window: a `result.json` replaced between the hash and the parse
    produced a PASS bound to bytes the builder never evaluated, and the same
    check re-run a second later exited 2. Digesting what was actually parsed
    closes it by construction rather than by narrowing the window.

    The cache goes through `tax_law_fetcher.load_tax_law`, not a plain JSON
    read: that is the ONE read site every consumer shares, and it refuses two
    things nothing here would have caught. A cache reached through a symlink or
    an NTFS junction is refused rather than followed, and a non-finite number
    ANYWHERE in it is refused rather than carried — a NaN bracket rate passes
    the "is this a non-empty list" check below and then defeats every
    comparison inside the worksheet, which is how a corrupt cache prices a
    whole preferential stack at 0%. That loader owns its own read, so the cache
    is the one input opened twice and the only one where the window survives —
    named here rather than left for a reader to find, and inside the receipt's
    stated threat model: it detects staleness, not a hostile process.
    """
    workspace = Path(workspace)
    contents: "dict[str, bytes]" = {}
    result, contents[RESULT_NAME] = _read_json_bytes(
        workspace / RESULT_NAME, RESULT_NAME)
    profile, contents[PROFILE_NAME] = _read_json_bytes(
        workspace / PROFILE_NAME, PROFILE_NAME)
    raw_ledger, contents[LEDGER_NAME] = _read_json_bytes(
        workspace / LEDGER_NAME, LEDGER_NAME)
    try:
        ledger = load_ledger(raw_ledger)
    except TieOutError as e:
        raise ReturnBuilderError(f"{LEDGER_NAME} is not valid: {redact(e)}") from None
    year = _tax_year(result, profile)
    cache = workspace / f"tax_law_{year}.json"
    if cache.is_file():
        try:
            contents[cache.name] = cache.read_bytes()
        except OSError:
            # The loader below reports whatever is wrong with it; an unreadable
            # cache simply goes unhashed, which `gate_inputs` treats as absent.
            contents.pop(cache.name, None)
    try:
        tax_law = load_tax_law(year, str(workspace))
    except ValueError as e:
        raise ReturnBuilderError(f"the tax-law cache: {redact(e)}") from None
    if tax_law is None:
        raise ReturnBuilderError(
            f"tax_law_{year}.json is missing or corrupt. Run /tax-setup to "
            "fetch it; Form 1040 line 16 cannot be computed without it.")
    return result, profile, ledger, tax_law, contents


def _ca_payments(profile: dict, year: int, status: str) -> tuple[dict, str | None]:
    """Reviewed Form540 line72 sources, never a second manually typed total."""
    import datetime
    from agents.lib.capabilities import _USPS_STATES
    status = _full_status_key(status)
    if type(year) is not int or not 2000 <= year <= 2099:
        raise ValueError("CA payment destination must be a valid tax year")
    flags = profile.get("flags") or {}
    if not isinstance(flags, dict):
        raise ValueError("profile.flags must be an object")
    answered = flags.get("ca_has_nonw2_payments")
    if not isinstance(answered, bool):
        raise ValueError("answer ca_has_nonw2_payments, including an explicit no")
    payments = profile.get("estimated_payments", {})
    if not isinstance(payments, dict) or not isinstance(payments.get("state", []), list):
        raise ValueError("estimated_payments.state must be the reviewed payment list")
    if payments.get("ca_verified") is not None and not isinstance(payments["ca_verified"], bool):
        raise ValueError("estimated_payments.ca_verified must be a boolean or unanswered null")
    prior = profile.get("prior_year", {})
    if not isinstance(prior, dict):
        raise ValueError("prior_year must be an object")

    def money(value, where):
        # Refusals identify fields, not account identifiers or raw source data.
        try:
            checked = _maybe_num(value)
        except OverflowError:
            checked = None
        if checked is None or checked < 0:
            raise ValueError(f"{where} must be a finite nonnegative amount")
        return round(checked, 2)

    opening = money(prior.get("ca_estimated_payments_applied", 0), "prior_year.ca_estimated_payments_applied")
    totals = dict(estimates=0.0, extension=0.0, k1_allocations=0.0,
                  prior_applied=opening, total=0.0, payment_count=0, unknown_date_count=0,
                  external_record_count=0)
    kinds = {"estimate": "estimates", "extension": "extension", "k1_estimate": "k1_allocations"}
    keys = {"payment_id", "state", "tax_year", "date", "kind", "amount", "person"}
    ids = set()
    joint_separate = individual_joint = False
    for index, record in enumerate(payments.get("state", [])):
        where = f"estimated_payments.state[{index}]"
        if not isinstance(record, dict) or set(record) != keys:
            raise ValueError(f"{where}: re-read/migrate the source; require payment_id, state, tax_year, date, kind, amount and person")
        identifier = record["payment_id"]
        if (not isinstance(identifier, str) or not identifier.strip()
                or identifier != identifier.strip() or identifier in ids):
            raise ValueError(f"{where}: payment_id must be nonempty and unique")
        ids.add(identifier)
        state = record["state"]
        if not isinstance(state, str) or state not in _USPS_STATES:
            raise ValueError(f"{where}: require a valid uppercase state code")
        destination = record["tax_year"]
        if type(destination) is not int or not 2000 <= destination <= 2099:
            raise ValueError(f"{where}: require the credited tax year")
        kind, person = record["kind"], record["person"]
        if not isinstance(kind, str) or kind not in kinds:
            raise ValueError(f"{where}: kind must be estimate, extension or k1_estimate; opening credit is separate")
        if not isinstance(person, str) or person not in ("primary", "spouse", "joint"):
            raise ValueError(f"{where}: person must identify primary, spouse or joint account ownership")
        date = record["date"]
        if date is not None:
            try:
                if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                    raise ValueError
                if datetime.date.fromisoformat(date) > datetime.date.today():
                    raise ValueError
            except ValueError:
                raise ValueError(f"{where}: date must be an actual ISO payment date, not in the future, or explicit null") from None
        amount = money(record["amount"], where + ".amount")
        if not amount:
            continue
        if state != "CA" or destination != year:
            totals["external_record_count"] += 1
            continue
        if person == "spouse" and status != "mfj":
            raise ValueError(f"{where}: a spouse's separate-account payment cannot pay a nonjoint return")
        totals[kinds[kind]] += amount
        totals["payment_count"] += 1
        totals["unknown_date_count"] += int(date is None)
        joint_separate |= person == "joint" and status != "mfj"
        # Booklet line 72: a statement is due when payments were made under
        # both SSNs, which the spouse's own account means on a joint return.
        individual_joint |= person == "spouse" and status == "mfj"
    totals["total"] = money(sum(totals[k] for k in ("estimates", "extension", "k1_allocations", "prior_applied")), "CA line72 total")
    for key in ("estimates", "extension", "k1_allocations"):
        totals[key] = round(totals[key], 2)
    if answered != (totals["total"] > 0):
        raise ValueError("ca_has_nonw2_payments disagrees with the current-year CA payments/opening credit; reconcile the sources")
    if answered and payments.get("ca_verified") is not True:
        raise ValueError("reconcile claimable CA payment amounts, ownership/allocation and credited year; set estimated_payments.ca_verified")
    notes = []
    if totals["unknown_date_count"]:
        notes.append("Some credited payments have unknown dates; FTB5805 timing is not computed.")
    if totals["external_record_count"]:
        notes.append("Payments for other states/tax years are excluded; confirm the appropriate returns claim them.")
    if joint_separate:
        notes.append("Confirm the joint-estimate allocation was arranged with FTB before separate filing.")
    if individual_joint:
        notes.append("Attach the required Form540 explanation for individual-account payments combined onto a joint return.")
    return totals, ("CA payments: " + " ".join(notes)) if notes else None


def build_california(result: dict, tax_law: dict, ledger, profile: dict) -> dict:
    """The CA half of the receipted check, independently of federal scope."""
    from agents.lib.capabilities import _profile_state
    from agents.lib.projection import _ca_section_computed
    from agents.lib.tax_computations import compute_ca_540, ny_it203_plan
    from agents.lib.tax_computations.state.ca import CA_540_COMPONENT_LINES
    from agents.lib.tax_computations._shared import _normalize_filing_status
    try:
        address = profile.get("address") or {}
        selected = (bool(address.get("state")) and _profile_state(profile) == "CA"
                    and not ny_it203_plan(profile))
        if not selected and not _ca_section_computed(result):
            return {}
        ca = result.get("california_540")
        if not isinstance(ca, dict):
            raise ValueError("selected CA return is missing california_540")
        status = _filing_status(profile, result)
        fs = _full_status_key(status)
        ca_status = _normalize_filing_status(ca.get("ca_filing_status")).lower()
        if ca_status != fs:
            raise ValueError("Form 540 with a different CA filing status needs a separate pro-forma federal return; "
                             "this builder cannot reuse the federal AGI/status for it")
        flags = profile.get("flags", {})
        eligibility = profile.get("ca_exemptions")
        if not isinstance(eligibility, dict):
            raise ValueError("record ca_exemptions eligibility in the profile")

        def fact(key, source=eligibility):
            value = source.get(key)
            if not isinstance(value, bool):
                raise ValueError(f"answer California exemption eligibility: {key}")
            return value

        own_claimed = fact("primary_can_be_claimed")
        if fs == "qss" and own_claimed:
            raise ValueError("resolve QSS eligibility before counting personal exemptions")
        own_blind = fact("primary_blind")
        own_senior = fact("age_65_plus_primary", flags)
        spouse_claimed = fact("spouse_can_be_claimed") if fs == "mfj" else True
        spouse_blind = fact("spouse_blind") if fs == "mfj" else False
        spouse_senior = fact("age_65_plus_spouse", flags) if fs == "mfj" else False
        personal = (2 if fs == "qss" else int(not own_claimed))
        if fs == "mfj":
            personal += int(not spouse_claimed)
        blind = int(own_blind and not own_claimed) + int(spouse_blind and not spouse_claimed)
        senior = int(own_senior and not own_claimed) + int(spouse_senior and not spouse_claimed)
        # The screening flag and the per-person answers describe one fact;
        # disagreeing, the line 8 credit would follow whichever was wrong.
        if flags.get("ca_has_blind_exemption") is not (blind > 0):
            raise ValueError("flags.ca_has_blind_exemption must agree with the eligible blind filers in ca_exemptions")
        # Schedule P line 22 limits a child's exemption (under 19, or a full-time
        # student under 24, not filing jointly) to earned income plus a yearly
        # base. The builder applies the full exemption, so it cannot price one.
        if fs != "mfj" and own_claimed and fact("primary_schedule_p_child"):
            raise ValueError("Schedule P line 22's child exemption limit is not computed; prepare Form 540 "
                             "line 61 and Schedule P outside this toolkit")
        deps = profile.get("dependents")
        if not isinstance(deps, list) or any(not isinstance(d, dict) for d in deps):
            raise ValueError("profile.dependents must be the confirmed dependent list")
        if any(type(d.get("_template_placeholder", False)) is not bool for d in deps):
            raise ValueError("dependent template markers must be booleans")
        count = sum(not d.get("_template_placeholder", False) for d in deps)
        if ca.get("ca_preference_adjustments_confirmed") is not True:
            raise ValueError("work and confirm all Schedule P Part I adjustments, including signed reductions")
        adjustments = ca.get("schedule_ca_adjustments")
        if not isinstance(adjustments, dict):
            raise ValueError("record Schedule CA totals before building Form 540")
        state = tax_law.get("state")
        method = ca.get("ca_deduction_method")
        if not isinstance(state, dict) or not isinstance(method, str):
            raise ValueError("record the CA law block and elected deduction method")
        adjustments = derive_ca_schedule_ca(ledger, result, profile, tax_law)
        components = {f"line_{n}": ca.get(f"line_{n}") for n in CA_540_COMPONENT_LINES}
        sums = category_sums(ledger)
        components["line_71"] = round(sums.get("state_withholding_ca", 0) + sums.get("ca_1099_withholding", 0), 2)
        components["line_73"] = sums.get("ca_592b_593_withholding", 0)
        payments, payment_note = _ca_payments(profile, _tax_year(result, profile), fs)
        components["line_72"] = payments["total"]
        forward = flags.get("ca_applies_overpayment_forward")
        amount = _num(components["line_98"], "CA line98 forward election")
        # Form 540 stores this component to cents; rebuilding must ask the
        # same election question as the rounded return and rollover.
        if not isinstance(forward, bool) or forward != (round(amount, 2) > 0):
            raise ValueError("answer ca_applies_overpayment_forward and reconcile it with the explicit line98 election")
        built = compute_ca_540(tax_year=_tax_year(result, profile), filing_status=status,
            state_law=state, federal_agi=_num((result.get("form_1040") or {}).get("line_11_agi"), "federal AGI"),
            schedule_ca_additions=_num(adjustments.get("total_additions"), "Schedule CA additions"),
            schedule_ca_subtractions=_num(adjustments.get("total_subtractions"), "Schedule CA subtractions"),
            deduction=_num(ca.get("ca_deduction"), "CA deduction"), deduction_method=method,
            personal_count=personal, blind_count=blind, senior_count=senior, dependent_count=count,
            ca_preference_adjustments=_num(ca.get("ca_preference_adjustments"), "CA Schedule P adjustments"),
            lump_sum_tax=_num(ca.get("ca_lump_sum_tax"), "CA Schedule G-1 tax in line 34"),
            iso_bargain_element=_num((ca.get("schedule_p") or {}).get("iso_bargain_element"), "CA ISO spread"), components=components)
        p = {**(ca.get("schedule_p") or {}), **built["schedule_p"]}
        generated = p["schedule_p_carryforward"]
        prior = _num((profile.get("prior_year") or {}).get("ca_amt_credit_carryforward", 0), "prior CA AMT credit")
        if prior < 0:
            raise ValueError("prior CA AMT credit cannot be negative")
        opening = flags.get("ca_has_amt_credit") or prior
        supplied_credit = any(p.get(key) not in (None, 0) for key in
                              ("ftb3510_credit_available", "ftb3510_credit_allowable"))
        if supplied_credit and not opening:
            raise ValueError("resolve ca_has_amt_credit before dropping a supplied FTB 3510 amount")
        if opening:
            available = _num(p.get("ftb3510_credit_available"), "FTB 3510 credit available")
            allowable = _num(p.get("ftb3510_credit_allowable"), "FTB 3510 credit allowable")
            if p.get("ftb3510_worksheet_confirmed") is not True or not 0 <= allowable <= available:
                raise ValueError("confirm the worked FTB 3510 available and allowable credit")
            if p["ca_amt"] > 0 and allowable > 0:
                raise ValueError("a current CA AMT liability permits no prior-year AMT credit")
        else:
            available = allowable = 0
        p.update(iso_credit_generated=generated,
                 carryforward_to_next_year=round(available - allowable + generated, 2))
        built.update(schedule_p=p, schedule_ca_adjustments=adjustments,
            ca_withholding_box17=sums.get("state_withholding_ca", 0),
            ca_payment_summary=payments, ca_payment_note=payment_note,
            ca_withholding_1099=sums.get("ca_1099_withholding", 0),
            ca_withholding_592b_593=sums.get("ca_592b_593_withholding", 0))
        return built
    except (ValueError, TypeError, KeyError, AttributeError, ReturnBuilderError) as e:
        raise Blocked(f"Form 540: {redact(e)}") from None


def california_summary(result: dict, derived: dict) -> dict:
    """Keep the existing signed summary consistent with checked settlement."""
    if not derived or "balance_due_summary" not in result:
        return {}
    if not isinstance(result["balance_due_summary"], dict):
        raise Blocked("balance_due_summary must be an object")
    f = result.get("form_1040") or {}
    federal = round(_num(f.get("line_35a_refund"), "federal refund")
                    - _num(f.get("line_37_amount_owed"), "federal amount owed"), 2)
    california = round(derived["ca_refund"] - derived["ca_amount_owed"], 2)
    return {"federal_refund": federal, "california_refund": california,
            "net_refund": round(federal + california, 2)}


def california_differences(result: dict, derived: dict) -> list[str]:
    ca = result.get("california_540") or {}
    # Nested workpapers and the screened note are load-bearing too.
    agrees = ca_values_agree
    fields = [field for field, value in derived.items() if field not in ca or not agrees(ca.get(field), value)]
    if derived and type(ca.get("computed_tax_year")) is not int and "computed_tax_year" not in fields:
        fields.append("computed_tax_year")
    notes = (result.get("meta") or {}).get("limitations", [])
    if derived and (not isinstance(notes, list) or any(not isinstance(n, str) for n in notes)):
        raise Blocked("Form 540 requires meta.limitations to be a list of disclosure strings")
    if derived and derived["line_113_note"] not in notes:
        fields.append("meta.limitations (Form 540 line 113)")
    if derived.get("ca_payment_note") and derived["ca_payment_note"] not in notes:
        fields.append("meta.limitations (CA payments)")
    if derived and any(n.startswith("CA payments:") and n != derived.get("ca_payment_note") for n in notes):
        fields.append("meta.limitations (stale CA payment disclosure)")
    source_note = (derived.get("schedule_ca_adjustments") or {}).get("source_note")
    if source_note and source_note not in notes:
        fields.append("meta.limitations (CA investment worksheets)")
    if derived and any(n.startswith("CA investment worksheets:") and n != source_note for n in notes):
        fields.append("meta.limitations (stale CA investment disclosure)")
    summary = california_summary(result, derived)
    for key, value in summary.items():
        if not agrees(result["balance_due_summary"].get(key), value):
            fields.append(f"balance_due_summary.{key}")
    return fields


def write(workspace: Path, result: dict, derived: "dict[str, float]", *, california=None,
          government=None, other_income=None) -> bool:
    """Publish the derived lines. Returns whether anything was written.

    NOT WRITING AN UNCHANGED FILE IS THE CONTRACT, not an optimization: V8's
    validation receipt binds each gate's verdict to the digest of the files it
    read, so republishing an identical `result.json` moves that digest and
    invalidates a pass that is still true. `/tax-generate --filing-package`
    then refuses a package whose gates did run.
    """
    existing = result.get("schedule_1_government_benefits")
    if government is None and isinstance(existing, dict) and (existing.get("other_income") is not None
            or existing.get("computed_tax_year") is not None
            or any(existing.get(k) for k in ("unemployment_gross", "family_leave_gross", "total"))):
        raise Blocked("publish the active government-benefits workpaper with its checked line8")
    gov_changes = government_differences(result, government)
    existing_other = result.get("schedule_1_other_income")
    if other_income is None and isinstance(existing_other, dict) and (any(
            existing_other.get(k) is not None for k in ("remaining_income", "computed_tax_year"))
            or any(existing_other.get(k) for k in ("miscellaneous_other_income", "total"))):
        raise Blocked("publish the Schedule1 other-income workpaper with its checked line8")
    other_changes = schedule1_other_differences(result, other_income, government)
    if not differences(result, derived) and not california_differences(result, california or {}) and not gov_changes and not other_changes:
        return False
    updated = {**result, "form_1040": {**result["form_1040"], **derived}}
    if other_income is not None:
        updated["schedule_1_other_income"] = other_income
        updated["form_1040"]["line_8_schedule1_other"] = other_income["total"]
    if government is not None:
        updated["schedule_1_government_benefits"] = government
        updated["form_1040"]["line_8_schedule1_other"] = government["total"]
    if california:
        updated["california_540"] = {**result["california_540"], **california}
        summary = california_summary(updated, california)
        if summary:
            updated["balance_due_summary"] = {**result["balance_due_summary"], **summary}
        meta = dict(result.get("meta") or {})
        notes = [n for n in meta.get("limitations", []) if not n.startswith(("Form 540 line 113:", "CA payments:", "CA investment worksheets:"))]
        notes.append(california["line_113_note"])
        if california.get("ca_payment_note"):
            notes.append(california["ca_payment_note"])
        if california["schedule_ca_adjustments"].get("source_note"):
            notes.append(california["schedule_ca_adjustments"]["source_note"])
        updated["meta"] = {**meta, "limitations": notes}
    meta = dict(updated.get("meta") or {})
    notes = meta.get("limitations", [])
    if government is not None or (isinstance(notes, list) and any(
            isinstance(n, str) and n.startswith("Government benefits:") for n in notes)):
        notes = [n for n in notes if not n.startswith("Government benefits:")]
        if government is not None and government.get("source_note"):
            notes.append(government["source_note"])
        updated["meta"] = {**meta, "limitations": notes}
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


def _record_the_check(workspace: Path, inputs: "dict | None", outcome: str,
                      exit_code: int) -> int:
    """Record Step 12.6's verdict — AFTER it is printed and its code decided.

    The order, and the rule that a receipt which cannot be written never lowers
    a verdict, live once in `receipt.record_after_verdict`; all three gates end
    through it. What is THIS gate's own is the mode test below. A verdict that
    is not a PASS is still RECORDED, because `/tax-generate --filing-package`
    has to tell "this gate disagreed" from "this gate never ran" — both refuse a
    package, and only one of them is answered by running the gate.

    `inputs is None` is the WRITE mode, which asserts nothing and so certifies
    nothing: it rewrites the very file the receipt would be bound to.
    """
    if inputs is None:
        return exit_code
    return receipt.record_after_verdict(workspace, GATE, outcome, exit_code,
                                        inputs=inputs)


def _main(argv: list) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="agents.lib.return_builder",
        description="Derive Form 1040 lines 1-38 from the parsed records, the "
                    "profile and the tax-law cache.")
    add_workspace_argument(parser)
    parser.add_argument("--check", action="store_true",
                        help="report the lines that disagree and write nothing; "
                             "exit 2 if any do. This mode, and only this mode, "
                             "records Step 12.6's verdict in the validation "
                             "receipt")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if not exc.code else 1

    try:
        # `resolve_workspace` owns the "is this a usable directory" refusal now,
        # and the local copy that stood here went with it — the message it
        # printed named neither $TAX_WORKSPACE nor where the path came from.
        workspace = resolve_workspace(args.workspace)
        hold_workspace(workspace, "return_builder")
    except WorkspaceError as e:
        print(f"Error: {redact(e)}", file=sys.stderr)
        return 1
    inputs = None
    try:
        result, profile, ledger, tax_law, contents = load(workspace)
        if args.check:
            # The digests this verdict will be bound to, taken from THE BYTES
            # `load` parsed rather than from a second read of the same paths.
            # A `result.json` replaced between a hash and the parse produced a
            # PASS bound to bytes the builder never evaluated — a gate
            # certifying a return it never saw, which is what the receipt
            # exists to catch (V8).
            inputs = receipt.gate_inputs(workspace, GATE, contents=contents)
        other_income = schedule1_other_workpaper(result, ledger, profile)
        government = government_workpaper(result, ledger, profile)
        other_changes = schedule1_other_differences(result, other_income, government)
        if args.check and other_changes:
            print("BLOCKED: Schedule1 other income disagrees: " + ", ".join(other_changes), file=sys.stderr)
            return _record_the_check(workspace, inputs, "FAILED", 2)
        gov_changes = government_differences(result, government)
        if args.check and gov_changes:
            print("BLOCKED: government benefits disagree: " + ", ".join(gov_changes), file=sys.stderr)
            return _record_the_check(workspace, inputs, "FAILED", 2)
        california = build_california(result, tax_law, ledger, profile) if args.check else {}
        ca_changes = california_differences(result, california)
        if args.check and ca_changes:
            print("BLOCKED: Form 540 derived fields disagree: " + ", ".join(ca_changes), file=sys.stderr)
            return _record_the_check(workspace, inputs, "FAILED", 2)
        # This CA check precedes the federal scope decision: a PFIC waiver
        # of Form 1040 arithmetic must never waive the separate state return.
        if args.check and california:
            print("Form 540 derived lines, Schedule P and screened line-113 note agree.")
            print(california["line_113_note"])
        derived = build(result, tax_law, ledger,
                        _filing_status(profile, result), profile)
        changes = differences(result, derived)
        if args.check:
            if changes:
                print(f"BLOCKED: {len(changes)} derived line(s) on Form 1040 "
                      f"disagree with the components they are computed from:\n"
                      f"{_report(changes)}", file=sys.stderr)
                return _record_the_check(workspace, inputs, "FAILED", 2)
            print(f"All {len(derived)} derived lines of Form 1040 lines 1-38 "
                  "agree with their components.")
            return _record_the_check(workspace, inputs, receipt.PASS, 0)
        california = build_california({**result, "form_1040": {**result["form_1040"], **derived}},
                                      tax_law, ledger, profile)
        if write(workspace, result, derived, california=california, government=government, other_income=other_income):
            print(f"Rewrote {len(changes)} derived line(s) of Form 1040 "
                  f"lines 1-38:\n{_report(changes)}\n"
                  "Re-read result.json before using any figure below the "
                  "highest line listed.")
            if government is not None:
                print("Rebuilt the government-benefits workpaper and Form1040 line8; re-run --check.")
            if other_income is not None:
                print("Rebuilt the Schedule1 other-income workpaper and Form1040 line8; re-run --check.")
            if california:
                print("Rebuilt Form 540 and Schedule P; re-run --check before generating a package.")
                print(california["line_113_note"])
        else:
            print(f"All {len(derived)} derived lines of Form 1040 lines 1-38 "
                  "already agree with their components; nothing written.")
    except OutOfScope as e:
        # NOT a failure in CHECK mode: these lines were never this module's to
        # assert on this return, so there is no verdict to fail. Refusing would
        # stand between a correct, complete return and its filing package for
        # ever, with `--draft` — which stamps NOT VALIDATED across a return that
        # is fine — as the only way past. BUILDING is still refused: the whole
        # point is that this module must not write these lines here.
        if not args.check:
            print(f"BLOCKED: {redact(e)}", file=sys.stderr)
            return 2
        # On STDOUT, and that is deliberate: the run SUCCEEDED, so a reader
        # who only prints stderr on a non-zero exit would see an empty stdout
        # and read it as "checked, fine" — the loudest possible way to lose a
        # warning. The tie-out surfaces its unverifiable categories the same
        # way, inside the report it prints on a pass.
        print(f"NOT CHECKED — Form 1040 lines 1-38 are kept by hand on this "
              f"return, and nothing here verified them: {redact(e)}")
        return _record_the_check(workspace, inputs, receipt.NOT_APPLICABLE, 0)
    except Blocked as e:
        # A return whose own components contradict each other, and a cache that
        # cannot price it, are the same verdict — read, and refused. The receipt
        # records WHICH refusal it was, because the remedies differ: a blocked
        # return needs the situation resolved, a failed one needs a component
        # fixed.
        print(f"BLOCKED: {redact(e)}", file=sys.stderr)
        return _record_the_check(workspace, inputs, "BLOCKED", 2)
    except (ReturnBuilderError, receipt.ReceiptError) as e:
        # Could not RUN — no verdict, so nothing is recorded. A receipt written
        # here would say the gate ran and disagreed, sending the agent to look
        # for a wrong line when the input is what is missing.
        print(f"Error: {redact(e)}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"Error: {redact(e)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(_main(sys.argv[1:]))
