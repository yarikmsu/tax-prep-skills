#!/usr/bin/env python3
"""The required-answer contract for a headless run (V5).

`answers.md` is the non-interactive input path — AGENTS.md §6 says so: "in a
headless run, read required answers from here and hard-stop if one is missing.
Never guess." What it did not have is a way to know WHICH answers a given
workspace needs, so the hard stop arrived mid-compute, one question at a time,
after the parse and most of the arithmetic had already run.

This module is the list, and `--check-required` asks it before the run starts.

WHAT IS IN THE REGISTRY, AND WHAT IS DELIBERATELY NOT. The skills carry 55
`STOP`s between them. Almost none is a missing answer: most are the engine
absent, the tax-law cache stale, or a gate blocking — states with their own
machinery and their own exit codes. Registered here is a taxpayer FACT that a
RETURN-PIPELINE skill (`tax-parse`, `tax-compute`, `tax-parse-crypto`,
`tax-verify`, `tax-fbar`) cannot derive and must not guess. Out of scope on
purpose, because each has its own mechanism rather than an answer file:

  - `tax-setup`'s interview — it IS the interview; there is no prior file to
    read it from, and its outputs are `taxpayer_profile.json` fields.
  - `tax-rollover`'s `action_suggested == "ask"` decisions — carried per
    category by `rollover.apply_changes`, which already refuses to land one
    unanswered.
  - the planning skills (`tax-project`, `tax-retirement`, ...) — they compute
    projections, not a return; a missing input degrades a recommendation.
  - a question whose absence makes the engine DEGRADE with a loud note rather
    than stop. The prior-year Form 2210 line-8 composition is the worked
    example: `tax-and-credits.md` says pass `None` and leave the prong
    unresolved rather than infer it. Registering a documented degrade as a
    blocking answer is `docs/contributing/gates-that-cry-wolf.md` exactly.

Coverage is therefore a claim about the pipeline's taxpayer FACTS, not about
every `STOP`, and `docs/changelog/epic-V.md` names the sites still owed.

TWO KINDS, AND THE DIFFERENCE IS LOAD-BEARING.

  FACT — a figure or a yes/no the taxpayer knows and the engine cannot derive:
  the split of a prior-year capital loss, whether the document set is complete.
  Recording it once is the point; a persisted answer satisfies it for ever,
  which is why `answers.md` exists.

  ACKNOWLEDGEMENT — a decision to proceed on a KNOWN wrong-direction risk: "yes,
  report those crypto disposals at $0 basis even though that overstates the
  gain." A persisted one of these must NEVER satisfy the gate again. The facts
  it was given for are this year's — which lots, which documents, how much — and
  an acknowledgement that outlives them is a taxpayer agreeing to something
  nobody described to them. AGENTS.md §4's bias rule is the same argument from
  the other end: the acknowledgement is what converts an over-statement into an
  accepted one, and consent that was never re-asked is not acceptance.

  So an acknowledgement is cleared by `--acknowledge <id>`, valid for THAT
  invocation and written nowhere — the shape `capabilities.py` already uses.
  Without it the gate would exit 2 for ever on a workspace that had done
  everything asked of it, which teaches an agent to reason past a 2.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from agents.lib.rollover import _amount, _get_path  # noqa: E402
from agents.lib.secure_fs import redact  # noqa: E402
from agents.lib.workspace import (  # noqa: E402
    WorkspaceError,
    add_workspace_argument,
    resolve_workspace,
)

FACT = "fact"
ACKNOWLEDGEMENT = "acknowledgement"

# An answer is recorded as `- **[id]** …` on one line of `answers.md`. A marker
# rather than a heading because a heading is a place and this is a FIELD: the
# file is written by an agent and read by one, and matching prose would make
# every reword a lost answer. The human text after the marker is the answer and
# is never parsed — what is parsed is whether the id carries one.
#
# WHAT ACTUALLY HOLDS THIS: `_scan` matches ONE LINE AT A TIME. The first version
# ran a single `re.M` regex over the whole file, where `\s*` matched the newline —
# a blank marker absorbed the blank line under it and captured the next heading as
# its answer, so the shipped template parsed as fully answered and the gate exited
# 0 on a workspace where nothing had been asked. Narrowing the classes to
# horizontal whitespace is belt-and-braces; the line scanner is the guard, and a
# mutation back to the whole-file form is what goes red. Do not "simplify" this
# into a `finditer` over the text.
_MARKER = re.compile(
    r"^[ \t]*[-*][ \t]*\*\*\[(?P<id>[a-z0-9_]+)\]\*\*[ \t]*(?P<body>.*?)[ \t]*$")
# A fence is tracked the way CommonMark defines one, not by "the line looks like
# a fence". Toggling on any such line let an INNER ``` close an OUTER ```` and
# hand the marker between them back as an answer — the example satisfying the
# gate it is an example of. So: at most three spaces of indent (four is indented
# code, not a fence), and only a run of the SAME character at least as long as
# the opener, with nothing after it, closes one.
_FENCE_OPEN = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})(?P<info>[^`]*)$")
# An answer may run onto INDENTED lines directly under the marker — a cash/
# non-cash split is naturally a sub-list. Directly: one blank line ends it, which
# is what keeps the template's blank markers blank. A single TAB is an indent
# too; requiring two whitespace CHARACTERS blocked a tab-indented answer.
_CONTINUATION = re.compile(r"^(?:[ ]{2,}|\t)\S")
# An id recorded with nothing after it is NOT answered: the template ships every
# id with a blank body so a reader can see what is owed. Underscores are a
# fill-in blank and count as nothing. A DASH does not appear here — "—" is how a
# taxpayer writes "none", and reading that as unanswered blocks a return that
# has in fact answered every question.
_UNANSWERED_BODY = re.compile(r"^[\s_]*$")


@dataclass(frozen=True)
class _Inputs:
    """The workspace's files, read ONCE and shared by every predicate."""

    workspace: Path
    profile: dict
    result: dict

    @property
    def prior_year(self) -> dict:
        node = self.profile.get("prior_year")
        return node if isinstance(node, dict) else {}

    @property
    def flags(self) -> dict:
        node = self.profile.get("flags")
        return node if isinstance(node, dict) else {}


@dataclass(frozen=True)
class RequiredAnswer:
    """One question a headless run must not guess at.

    `when` decides whether a given workspace owes it, and lives ON the entry
    rather than in a dispatch keyed by id: with the branch elsewhere, adding an
    entry and forgetting its branch produced an answer that was silently never
    owed. `when=None` means the question is raised by the RUN — a filing status
    that disagrees with the tax software cannot be predicted from the inputs —
    and `--check-required` names those as what they are instead of pretending.
    """

    id: str
    kind: str
    question: str
    asked_by: str          # the skill and step that stops without it
    required_when: str     # the condition, in the words the skill uses
    when: Optional[Callable[[_Inputs], bool]] = None


def _always(inputs: _Inputs) -> bool:
    return True


def _capital_loss_split_unknown(inputs: _Inputs) -> bool:
    prior = inputs.prior_year
    combined = _amount(prior.get("capital_loss_carryforward"))
    split = (_amount(prior.get("capital_loss_carryforward_short_term"))
             + _amount(prior.get("capital_loss_carryforward_long_term")))
    return bool(combined) and not split


def _ca_amt_credit_needed(inputs: _Inputs) -> bool:
    from agents.lib.tax_computations.state.ny_nonresident import ny_it203_plan
    address = inputs.profile.get("address")
    address = address if isinstance(address, dict) else {}
    return (isinstance(address.get("state"), str) and address["state"].strip().upper() == "CA"
            and not ny_it203_plan(inputs.profile)
            and (inputs.flags.get("ca_has_amt_credit") is True
                 or _amount(inputs.prior_year.get("ca_amt_credit_carryforward")) > 0))


def _charitable_carryovers_unresolved(inputs: _Inputs) -> bool:
    ledgers = inputs.prior_year.get("charitable_carryforward_ledger") or {}
    if not isinstance(ledgers, dict):
        return True
    for jurisdiction, section_name in (("federal", "schedule_a"), ("CA", "schedule_ca_itemized")):
        ledger = ledgers.get(jurisdiction)
        if ledger is not None and (not isinstance(ledger, dict) or ledger.get("verified") is not True):
            return True
        section = inputs.result.get(section_name) or {}
        if section.get("charitable_carryforward") and not section.get("charitable_ledger"):
            return True
    return False


def _filing_separately(inputs: _Inputs) -> bool:
    status = inputs.profile.get("filing_status")
    return isinstance(status, str) and status.strip().upper() in ("MFS",
                                                                  "MARRIED FILING SEPARATELY")


def _has_foreign_accounts(inputs: _Inputs) -> bool:
    flags = inputs.flags
    return bool(flags.get("has_foreign_accounts")
                or flags.get("signature_authority_foreign_accounts"))


def _crypto_disposals_lack_basis(inputs: _Inputs) -> bool:
    """`needs_basis` lives in `result.json`, and that is not the zeroed-template
    problem: `crypto_summary` is written by `tax-parse-crypto` BEFORE compute
    runs, so it is a parse-time input here, not a computed output."""
    rows = _get_path(inputs.result, "crypto_summary.needs_basis")
    return bool(rows) and isinstance(rows, list)


# Every registered question, with the condition that makes a workspace owe it.
REQUIRED_ANSWERS: "tuple[RequiredAnswer, ...]" = (
    RequiredAnswer(
        id="ca_amt_credit_worksheet",
        kind=FACT,
        question="From the worked FTB 3510, what CA AMT credit is available and "
                 "ALLOWABLE this year, including a worked zero? Preserve the "
                 "available amount less the allowable credit plus new ISO credit for rollover.",
        asked_by="tax-compute state-return.md",
        required_when="a CA return has a prior-year AMT credit flag or carryforward",
        when=_ca_amt_credit_needed,
    ),
    RequiredAnswer(
        id="document_set_complete",
        kind=FACT,
        question="Is the document set complete — is every W-2, 1099 and "
                 "statement for the year in documents/?",
        asked_by="tax-parse Step 2.5",
        required_when="always, before parsing",
        when=_always,
    ),
    RequiredAnswer(
        id="corrected_documents",
        kind=FACT,
        question="Is any document a CORRECTED form (W-2c, corrected 1099), and "
                 "if so which figure does it supersede?",
        asked_by="tax-parse Step 2.5",
        required_when="always, before parsing",
        when=_always,
    ),
    RequiredAnswer(
        id="capital_loss_carryforward_split",
        kind=FACT,
        question="From the prior return's Capital Loss Carryover Worksheet "
                 "(lines 8 and 13): how much of the carryforward is SHORT-term "
                 "and how much LONG-term?",
        asked_by="tax-compute income-and-agi.md",
        required_when="the legacy combined prior_year.capital_loss_carryforward "
                      "is non-zero and both split fields are zero",
        when=_capital_loss_split_unknown,
    ),
    RequiredAnswer(
        # The former state-only id cannot answer the added local-payment
        # questions. A fresh id re-asks them on a resumed workspace (AA59).
        id="state_local_taxes_paid_outside_withholding",
        kind=FACT,
        question="State and local estimated payments made in good faith "
                 "DURING this tax year, any prior-year overpayment credited to "
                 "them included, "
                 "and any prior-year state or local balance paid during it, "
                 "without its penalties or interest. Filing separately, only "
                 "your own share. (Schedule A line 5a — neither is on a W-2.)",
        asked_by="tax-compute income-and-agi.md",
        required_when="always — a resident of a state with no income tax "
                      "can still pay a former or another state's tax, and no "
                      "profile field records last year's state; the two "
                      "figures are inputs to compute_schedule_a and appear on "
                      "no document",
        when=_always,
    ),
    RequiredAnswer(
        id="charitable_gifts",
        kind=FACT,
        question="Cash and non-cash charitable gifts for the year, with the "
                 "documentation you hold for each (a written acknowledgment is "
                 "required for every gift of $250 or more).",
        asked_by="tax-compute income-and-agi.md",
        required_when="always — cash gifts feed the §170(p) non-itemizer "
                      "deduction, so a STANDARD-deduction filer in a state with "
                      "no income tax is asked too; unasked, that deduction is "
                      "silently zero and the tax overstated",
        when=_always,
    ),
    RequiredAnswer(
        id="charitable_carryovers",
        kind=FACT,
        question="Resolve each charitable carryover's vintage, category, jurisdiction, "
                 "eligibility and balance from prior worksheets; document the manual resolution.",
        asked_by="tax-compute income-and-agi.md",
        required_when="an opening charitable ledger is unresolved or a closing total lacks vintages",
        when=_charitable_carryovers_unresolved,
    ),
    RequiredAnswer(
        id="mfs_spouse_itemizes",
        kind=FACT,
        question="Does your spouse itemize ON EACH RETURN they file — "
                 "federal, and California or New York if either applies? The "
                 "elections are INDEPENDENT (a spouse can take the federal "
                 "standard deduction and itemize for CA), so record the state "
                 "answer separately where it differs and pass each one to its "
                 "own function. It binds this return BOTH ways: §63(c)(6) "
                 "zeroes the standard deduction of both spouses once either "
                 "itemizes, so a yes forces itemizing whatever the total, and "
                 "a no bars it even when it is worth more. A no is an ANSWER, "
                 "not an absence — the engine refuses to guess it. If the "
                 "answer is no, also record whether the spouse files their "
                 "own SEPARATE return or is considered unmarried and files as "
                 "head of household: the coupling does not bind the second, "
                 "and the two answers differ by the whole margin.",
        asked_by="tax-compute income-and-agi.md",
        required_when="the filing status is married filing separately",
        when=_filing_separately,
    ),
    RequiredAnswer(
        id="foreign_account_completeness",
        kind=FACT,
        question="Is this the COMPLETE list of foreign financial accounts you "
                 "held at ANY point during the year, including accounts closed "
                 "mid-year and ones you only have signature authority over?",
        asked_by="tax-fbar Step 1.5",
        required_when="the profile reports foreign accounts or signature "
                      "authority over one; an omitted account is an unfiled FBAR",
        when=_has_foreign_accounts,
    ),
    RequiredAnswer(
        id="crypto_disposals_without_basis",
        kind=ACKNOWLEDGEMENT,
        question="These disposals have no acquisition on record and were given "
                 "$0 basis, which OVERSTATES the gain. Supply the missing "
                 "acquisition, or accept over-reporting on those rows.",
        asked_by="tax-parse-crypto Step 4",
        required_when="result.json crypto_summary.needs_basis is non-empty",
        when=_crypto_disposals_lack_basis,
    ),
    RequiredAnswer(
        id="software_filing_status_mismatch",
        kind=FACT,
        question="The tax software's filing status differs from the computed "
                 "return's. Which is right?",
        asked_by="tax-verify Step 3",
        required_when="the comparison finds a status discrepancy",
        when=None,
    ),
)

_BY_ID = {a.id: a for a in REQUIRED_ANSWERS}
RAISED_BY_THE_RUN = tuple(a for a in REQUIRED_ANSWERS if a.when is None)
ASKING_SKILLS = tuple(sorted({a.asked_by.split()[0] for a in REQUIRED_ANSWERS}))


@dataclass(frozen=True)
class AnswersFile:
    """What `answers.md` turned out to be, so nothing has to read it twice."""

    exists: bool
    recorded: "dict[str, str]"
    markers: int


def _opens_fence(line: str) -> "tuple[str, int] | None":
    match = _FENCE_OPEN.match(line)
    if match is None:
        return None
    fence = match.group("fence")
    return fence[0], len(fence)


def _closes(line: str, fence: "tuple[str, int]") -> bool:
    char, length = fence
    stripped = line.strip()
    return (line[:len(line) - len(line.lstrip(" "))].count(" ") <= 3
            and len(stripped) >= length
            and set(stripped) == {char})


def _scan(text: str) -> "tuple[dict[str, str], int]":
    """`({id: answer}, how many markers were seen at all)`.

    A line scanner rather than one multi-line regex: it has to skip FENCED
    blocks, because both skills instruct an agent to record `- **[id]** answer`
    and the natural way to show that is a code block, which would otherwise
    satisfy the gate with its own example.
    """
    found: "dict[str, str]" = {}
    markers = 0
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    fence = None            # (delimiter character, opening length) while inside one
    i = 0
    while i < len(lines):
        if fence is not None:
            if _closes(lines[i], fence):
                fence = None
            i += 1
            continue
        opened = _opens_fence(lines[i])
        if opened is not None:
            fence = opened
            i += 1
            continue
        match = _MARKER.match(lines[i])
        if match is None:
            i += 1
            continue
        markers += 1
        body = [match.group("body")]
        j = i + 1
        while (j < len(lines) and _CONTINUATION.match(lines[j])
               and _opens_fence(lines[j]) is None and not _MARKER.match(lines[j])):
            body.append(lines[j].strip())
            j += 1
        answer = " ".join(part for part in body if part.strip()).strip()
        if not _UNANSWERED_BODY.match(answer):
            found[match.group("id")] = answer
        i = j
    return found, markers


def parse_answers(text: str) -> "dict[str, str]":
    """The ids `answers.md` records an answer for, mapped to that answer."""
    return _scan(text)[0]


class AnswersUnreadable(Exception):
    """An input exists but could not be read — a could-not-run, never a clean."""


def _read_json(path: Path) -> dict:
    """An ABSENT document reads as empty; an unreadable one RAISES.

    Every other `_read_json` in `agents/lib/` raises on both. This check runs
    before the workspace is complete — a missing `result.json` is the normal
    first-run state, not a fault — so absence alone is tolerated. Absence is
    where the tolerance stops: one revision read a truncated
    `taxpayer_profile.json` as `{}` and reported a workspace owing nothing,
    exit 0, with the MFS and foreign-account questions silently dropped. A
    failed read is a could-not-run and the CLI turns it into exit 1.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (WorkspaceError, OSError, ValueError) as exc:
        raise AnswersUnreadable(f"{redact(path)} could not be read") from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise AnswersUnreadable(f"{redact(path)} is not readable JSON") from exc
    if not isinstance(data, dict):
        raise AnswersUnreadable(f"{redact(path)} is not a JSON object")
    return data


def load_answers(workspace: Path) -> AnswersFile:
    path = workspace / "answers.md"
    if not path.is_file():
        return AnswersFile(False, {}, 0)
    try:
        text = path.read_text(encoding="utf-8")
    except (WorkspaceError, OSError, ValueError) as exc:
        # RAISED, but never with the exception's own message: `OSError.filename`
        # is the workspace path, and the workspace is named for the taxpayer.
        # An unreadable answers.md is a could-not-run — reporting it as "you owe
        # everything" (exit 2) invites the answers to be typed again into a file
        # that already has them, and under `--for` a scope with nothing
        # applicable would have reported CLEAN on a file nobody could read.
        raise AnswersUnreadable(f"{redact(path)} could not be read") from exc
    recorded, markers = _scan(text)
    return AnswersFile(True, recorded, markers)


def _read_inputs(workspace: Path) -> _Inputs:
    """The workspace's files. No predicate reads the tax-law cache: the one
    that did, asking for state payments only where the resident state taxes
    income, read a generic state's untyped cache as no tax and missed a
    mover's old state, so that answer is now owed by every workspace (AA59)."""
    return _Inputs(
        workspace=workspace,
        profile=_read_json(workspace / "taxpayer_profile.json"),
        result=_read_json(workspace / "result.json"),
    )


def applicable(workspace: Path, *, for_skill: "str | None" = None) -> "list[RequiredAnswer]":
    """Which required answers THIS workspace owes, from the files on disk."""
    inputs = _read_inputs(workspace)
    return [a for a in REQUIRED_ANSWERS
            if a.when is not None and a.when(inputs)
            and (for_skill is None or a.asked_by.split()[0] == for_skill)]


def missing(workspace: Path, *, acknowledged: "frozenset[str] | set[str]" = frozenset(),
            for_skill: "str | None" = None) -> "tuple[list[RequiredAnswer], AnswersFile]":
    """`(what this workspace still owes, what `answers.md` turned out to be)`.

    An ACKNOWLEDGEMENT is never satisfied by what is on file — only by
    `--acknowledge`, for this invocation. The facts one was given for are this
    year's, and a persisted acknowledgement is a taxpayer agreeing to something
    nobody described to them.
    """
    answers = load_answers(workspace)
    owed = []
    for answer in applicable(workspace, for_skill=for_skill):
        satisfied = (answer.id in acknowledged if answer.kind == ACKNOWLEDGEMENT
                     else answer.id in answers.recorded)
        if not satisfied:
            owed.append(answer)
    return owed, answers


def _render(owed: "list[RequiredAnswer]", answers: AnswersFile, workspace: Path) -> str:
    lines = []
    if not answers.exists:
        lines.append(f"No answers.md in {redact(workspace)} — every answer below "
                     "is owed. Run /tax-setup to create one.")
    elif not answers.markers:
        lines.append("answers.md records no answer ids. It predates the required-"
                     "answers contract, so nothing here can be read as answered; "
                     "the template's Required answers section is the shape.")

    if owed:
        lines.append(f"{len(owed)} required answer(s) missing — a headless run "
                     "will STOP on each in turn:")
        for answer in owed:
            again = (" (recorded, but an ACKNOWLEDGEMENT is re-asked every run — "
                     "clear it with --acknowledge)"
                     if answer.id in answers.recorded else "")
            lines.append("")
            lines.append(f"  [{answer.id}] {answer.kind.upper()}{again}")
            lines.append(f"    asked by : {answer.asked_by}")
            lines.append(f"    required : {answer.required_when}")
            lines.append(f"    question : {answer.question}")
    else:
        lines.append("Every required answer this workspace owes is recorded.")

    if RAISED_BY_THE_RUN:
        lines.append("")
        lines.append("Raised by the run itself, so not predictable from the "
                     "inputs and NOT checked here: "
                     + ", ".join(a.id for a in RAISED_BY_THE_RUN))
    return "\n".join(lines)


def render(workspace: Path, *, acknowledged: "frozenset[str] | set[str]" = frozenset(),
           for_skill: "str | None" = None) -> str:
    owed, answers = missing(workspace, acknowledged=acknowledged, for_skill=for_skill)
    return _render(owed, answers, workspace)


def template_section() -> str:
    """The `answers.md` template's Required-answers block, GENERATED.

    It was hand-copied, which put every question in two places: 21 sentences
    free to drift, and a drift here is a taxpayer answering a question the gate
    no longer asks. `tests/test_answers.py` pins the shipped template against
    this, so the registry stays the one copy.
    """
    lines = []
    for answer in REQUIRED_ANSWERS:
        if answer.when is None:
            continue
        lines.append(f"### {answer.question}")
        lines.append("")
        lines.append(f"*Asked by {answer.asked_by}; required when "
                     f"{answer.required_when}.*")
        lines.append("")
        if answer.kind == ACKNOWLEDGEMENT:
            lines.append("**This is an ACKNOWLEDGEMENT, and it is re-asked every "
                         "run.** It accepts a known wrong-direction risk on THIS "
                         "year's figures, so a recorded one never satisfies it "
                         "again — the run clears it with `--acknowledge`.")
            lines.append("")
        lines.append(f"- **[{answer.id}]**")
        lines.append("")
    lines.append("*Raised by the run itself rather than predictable from the "
                 "inputs, so it is not listed above and `--check-required` names "
                 "it separately:* "
                 + ", ".join(f"`{a.id}`" for a in RAISED_BY_THE_RUN) + ".")
    return "\n".join(lines)


def _validated_acknowledged(raw: "str | None") -> "frozenset[str]":
    """Mirrors `capabilities.py`: an unknown id is a HARD error, never a silent
    no-op — a typo would otherwise read as an acknowledgement that acknowledged
    nothing. A FACT cannot be acknowledged away; the remedy is answering it."""
    if not raw:
        return frozenset()
    ids = {part.strip() for part in raw.split(",") if part.strip()}
    unknown = sorted(ids - set(_BY_ID))
    if unknown:
        raise ValueError(f"unknown required-answer ids: {unknown}. "
                         f"Known ids: {sorted(_BY_ID)}")
    facts = sorted(i for i in ids if _BY_ID[i].kind != ACKNOWLEDGEMENT)
    if facts:
        raise ValueError(f"{facts} are FACTs, not acknowledgements, and cannot be "
                         "acknowledged away — record the answer in answers.md as "
                         "`- **[id]** answer` instead.")
    return frozenset(ids)


def _main(argv: list) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="agents.lib.answers",
        description="What a headless run still needs answered (V5).")
    add_workspace_argument(parser)
    parser.add_argument("--check-required", action="store_true",
                        help="list the required answers this workspace owes")
    parser.add_argument("--for", dest="for_skill", default=None,
                        choices=list(ASKING_SKILLS),
                        help="only the answers this skill asks for; without it, "
                             "every skill's, which is the pre-run survey")
    parser.add_argument("--acknowledge", default=None,
                        help="comma-separated ACKNOWLEDGEMENT ids accepted for "
                             "THIS invocation only; never persisted")
    parser.add_argument("--json", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits 2 on a bad flag, and 2 is BLOCKED in the gates'
        # contract. A misspelt `--for tax-comptue` is a could-not-run: reporting
        # it as blocked tells a caller the workspace owes answers it does not.
        return 0 if not exc.code else 1

    try:
        # The directory refusal moved into `resolve_workspace`, whose message
        # names $TAX_WORKSPACE and where the path came from.
        workspace = resolve_workspace(args.workspace)
    except WorkspaceError as e:
        print(f"Error: {redact(e)}", file=sys.stderr)
        return 1
    if not args.check_required:
        parser.print_usage()
        return 1
    try:
        acknowledged = _validated_acknowledged(args.acknowledge)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        owed, answers = missing(workspace, acknowledged=acknowledged,
                                for_skill=args.for_skill)
    except AnswersUnreadable as exc:
        print(f"Error: {exc} — the required answers could not be checked",
              file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({
            "missing": [{"id": a.id, "kind": a.kind, "question": a.question,
                         "asked_by": a.asked_by, "required_when": a.required_when}
                        for a in owed],
            "recorded": sorted(answers.recorded),
            "acknowledged_this_run": sorted(acknowledged),
            "raised_by_the_run": [a.id for a in RAISED_BY_THE_RUN],
        }, indent=2))
    else:
        print(_render(owed, answers, workspace))
    # THE GATES' CONTRACT: 2 is blocked, and a missing required answer is a stop
    # — that is what AGENTS.md §6 says a headless run does with one. 0 when the
    # workspace owes nothing.
    return 2 if owed else 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(_main(sys.argv[1:]))
