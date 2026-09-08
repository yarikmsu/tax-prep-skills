#!/usr/bin/env python3
"""Diff a computed `result.json` against a golden, with tolerances (V3).

WHAT A DIFFERENCE MEANS HERE. The golden is a KNOWN ANSWER for a synthetic case,
derived from the engine's own functions and checked by hand against the
worksheets (`goldens/<case>/derivation.md`). A mismatch is therefore a claim
about the AGENT-MEDIATED path — a step skipped, a figure transcribed wrong, a
filing-status key read from the wrong place — not about the tax math, which the
unit suite already pins.

THREE CLASSES, ALL REPORTED, because they fail differently. A figure that MOVED
is a computation the agent got wrong. A field that is MISSING is a step it never
ran, and that is the false-completion shape the tie-out gate exists for. A field
that is EXTRA is the one people want to wave through — and it is how a section
belonging to another return, or a hallucinated schedule, arrives.

TOLERANCE IS ON MONEY ONLY. A dollar of rounding across a dozen documents is
noise; a changed string, a changed boolean, or a section appearing and
disappearing is never noise. The default of $1.00 matches the tie-out gate's own
`DEFAULT_TOLERANCE`, deliberately: two gates disagreeing about what "the same
figure" means is a disagreement someone eventually resolves by loosening one.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from agents.lib.pdf_render import _NOT_MONEY as _PDF_NOT_MONEY  # noqa: E402
from agents.lib.pdf_render import _is_commentary  # noqa: E402
from agents.lib.secure_fs import redact  # noqa: E402
from agents.lib.tie_out import DEFAULT_TOLERANCE  # noqa: E402

# Fields nobody can predict and nothing should be scored on: when the run
# happened, and which skill wrote last. Everything else in `meta` IS scored —
# the tax year, the filing status and the limitations manifest are answers.
IGNORED = ("meta.prepared", "meta._last_modified_by", "meta._last_modified_at")

# WHICH KEYS ARE NOT MONEY is `pdf_render`'s table, imported rather than copied:
# it is keyed off the same `result_template.json` field names, and a hand-made
# second copy had already drifted four entries in one commit (it lost `tin`,
# `disposal` and `disposals`). `status` is this module's own addition and is
# stated as a delta rather than folded in, so the shared part stays shared.
#
# The RULE is not shared, and that distinction is load-bearing. `_is_amount`
# answers "does this print with a dollar sign", so it carries `_AMOUNT_ANYWAY`
# — an override that calls `carryforward_to_next_year` money because it is. Ask
# it "may a dollar tolerance apply here" and it says yes to `tax_year`, because
# `tax` is one of its override words, and 2026 against 2025 is one apart: a
# comparator that calls a return filed for the wrong year a rounding difference
# is worse than no comparator. Found by the test that changes the year, twice —
# once when this table was first written and once when it was replaced by the
# renderer's own predicate, which is why the test is there.
_NOT_MONEY = _PDF_NOT_MONEY | {"status"}


def _tolerated(path: str) -> bool:
    """Whether the money tolerance applies at this path."""
    last = path.rsplit(".", 1)[-1].rsplit("_", 1)[-1].lower()
    return last not in _NOT_MONEY


# WHAT PRINTS IS DECIDED BY WHICH SIDE THE VALUE CAME FROM, AND BY ITS TYPE.
# Never by the field's name.
#
# Three rounds tried the name: an allowlist of identity leaves, then a rule on
# the leaf's trailing token, then an inverted default with a list of the strings
# that are answers. Each one missed the next field — `w2_summary.employer_ein`,
# then `schedule_d.<part>.<row>.source`, which holds the broker — because a list
# of names covers only the fields somebody thought of, and `--result` takes ANY
# file. The names were never the thing that distinguished the two operands.
#
# THE ASYMMETRY IS THE WHOLE POINT. `golden` is a synthetic file checked into
# this repository, past the PII gate, with a test sweeping every golden for an
# identifier shape; it cannot disclose a taxpayer, so it prints. `actual` is the
# untrusted one — pointed at a real return it holds the real name, the real
# address, the real employer — so a STRING from it never prints. A number does,
# because a figure is the thing a reader acts on and the whole report is
# worthless without one; an identifier wearing a number is caught by `redact`.
#
# The residual, stated rather than implied: a string difference shows the
# golden's side and not the result's, so a reader who needs the value the agent
# actually produced opens `result.json`, which is theirs and owner-only. That is
# the trade AGENTS.md §1 asks for — summaries reference figures, they do not
# print identity data.
_WITHHELD = "(value from the result is not shown)"
# Sections whose KEYS are people. `income_summary_by_person` is keyed by the
# taxpayers' own first names, per the result template's note, so the PATH
# carries identity even when no value is printed at all.
_PERSON_KEYED = ("income_summary_by_person",)


def _mask_path(path: str) -> str:
    segments = path.split(".")
    if len(segments) > 1 and segments[0] in _PERSON_KEYED:
        segments[1] = "<person>"
    return ".".join(segments)


def _redacted(value):
    """The value with its identifier-shaped runs masked, or None if it had none.

    The layer under the side rule, and the one that reads the VALUE. It is what
    covers the golden side — a golden is reviewed at commit, and this is the
    check that does not depend on anyone having reviewed it — and what covers a
    NUMBER from the result, which the side rule prints.

    Over-redaction is the accepted cost: `redact` masks any run of nine or more
    digits, so a money field in the hundreds of millions prints masked. That
    direction is legibility; the other is disclosure.
    """
    if value is None or isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    text = str(value)
    masked = redact(text)
    return masked if masked != text else None


def _suppressed(value):
    """What stands in for a value that does not print, or None if it does.

    A COMPOSITE is counted rather than printed whichever side it came from: an
    EXTRA whole section carries its own nested fields — a payer, an employer —
    and counting them says what moved without saying whose. A STRING is the
    side rule above; on the golden side the caller overrides it.
    """
    if isinstance(value, dict):
        return f"({len(value)} field(s); values not shown)"
    if isinstance(value, list):
        return f"({len(value)} entr{'y' if len(value) == 1 else 'ies'}; values not shown)"
    return _WITHHELD if isinstance(value, str) else None


class Difference:
    """One scored difference. `kind` is MISSING, EXTRA or MISMATCH.

    `withheld` says the RESULT's value was not printed — because it is text, or
    because it is a composite whose nested fields this class cannot see inside.
    The golden's side is printed either way, and the path always is, because the
    path is what a reader acts on.
    """

    def __init__(self, kind: str, path: str, golden=None, actual=None):
        self.kind = kind
        self.golden = golden
        self.actual = actual
        # The PATH is emitted too, and a key is as capable of carrying an
        # identifier as a value is — a result whose extra section is keyed by the
        # taxpayer's own number discloses it through the path alone.
        self.path = redact(_mask_path(path))
        self.withheld = _suppressed(actual) is not None

    def _rendered(self, value, *, trusted: bool):
        """`(what goes out, whether that is a placeholder rather than a value)`.

        One renderer for both output modes and both operands, because a report
        that hides a value in the printed form and prints it in `--json` has
        hidden nothing — and the two had already been written as separate
        ladders once. It returns the FLAG rather than letting the caller infer
        one from the text, so a golden string that happens to start with a
        bracket is not mistaken for a placeholder.

        `trusted` is the golden side, and it overrides only the STRING rule; a
        composite is counted whichever side it came from.
        """
        placeholder = _suppressed(value)
        if placeholder is not None and not (trusted and isinstance(value, str)):
            return placeholder, True
        masked = _redacted(value)
        return (value if masked is None else masked), False

    def _emit(self, value, *, trusted: bool):
        return self._rendered(value, trusted=trusted)[0]

    def _show(self, value, *, trusted: bool) -> str:
        out, placeholder = self._rendered(value, trusted=trusted)
        return out if placeholder else repr(out)

    def __str__(self) -> str:
        golden = self._show(self.golden, trusted=True)
        actual = self._show(self.actual, trusted=False)
        if self.kind == "MISSING":
            return (f"MISSING  {self.path} — the golden has {golden} "
                    "and the result has no such field")
        if self.kind == "EXTRA":
            return f"EXTRA    {self.path} = {actual} — not in the golden"
        return f"MISMATCH {self.path}: golden {golden}, result {actual}"

    def to_dict(self) -> dict:
        return {"kind": self.kind, "path": self.path,
                "withheld": self.withheld,
                "golden": self._emit(self.golden, trusted=True),
                "actual": self._emit(self.actual, trusted=False)}


def _commentary(key: str) -> bool:
    """`_include_if`, `_note*` and the like describe the SCHEMA to a reader of
    the JSON. Scoring them would make a comment edit a failed eval.

    `pdf_render` owns the underscore convention over the same template; `note`
    and `notes` are this comparator's own addition, because the renderer PRINTS
    them (a degrade banner lives there) and the scorer must not fail an eval on
    a reworded one.
    """
    return _is_commentary(key) or key in ("note", "notes")


def _is_number(value) -> bool:
    """Deliberately without `math.isfinite`, unlike the engine's own copies.

    A NaN or an infinity in the RESULT falls the safe way here without a guard:
    every comparison with NaN is false, so `abs(golden - actual) <= allowed` and
    the bare `==` below both fail and the difference is REPORTED. The guard the
    parse boundaries need exists because a non-finite value defeats a range
    check silently; here it defeats a match, which is the direction that costs a
    false MISMATCH rather than a false clean run.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


_TOLERANCE_REFUSED = (
    "tolerance must be a finite, non-negative number of dollars, not {value!r}: "
    "an infinite one scores every wrong figure as a match")


def _check_tolerance(tolerance) -> None:
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) \
            or not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError(_TOLERANCE_REFUSED.format(value=tolerance))


def compare(golden, actual, *, tolerance: float = DEFAULT_TOLERANCE,
            path: str = "", ignored: "tuple[str, ...]" = IGNORED) -> "list[Difference]":
    """Every difference between `actual` and `golden`, deepest path first.

    `golden` is the TRUSTED side — a synthetic file from `goldens/`, whose values
    print. `actual` is whatever `--result` was pointed at, up to and including a
    real return, and its strings never print. Calling this with a real return as
    `golden` inverts that and is not a supported use.

    A non-finite tolerance is REFUSED rather than clamped, and the direction is
    why: `float("inf")` passed every money comparison, so a return whose tax was
    zero scored MATCH and exited 0. That is the under-statement bias AGENTS.md §4
    calls a hard error — a harness that reports a wrong return as right is worse
    than no harness. `json.loads` and `float()` both accept `Infinity`, and NaN
    defeats the `<=` in the other direction by making every comparison false.
    """
    _check_tolerance(tolerance)
    return _compare(golden, actual, tolerance=tolerance, path=path, ignored=ignored)


def _compare(golden, actual, *, tolerance: float, path: str,
             ignored: "tuple[str, ...]") -> "list[Difference]":
    """The recursion. Split out so the tolerance is checked once per call and
    not once per node."""
    if path in ignored:
        return []
    if isinstance(golden, dict):
        if not isinstance(actual, dict):
            return [Difference("MISMATCH", path or "(root)", golden, actual)]
        found = []
        for key in golden:
            if _commentary(key):
                continue
            here = f"{path}.{key}" if path else key
            if here in ignored:
                continue
            if key not in actual:
                found.append(Difference("MISSING", here, golden[key], None))
            else:
                found.extend(_compare(golden[key], actual[key], tolerance=tolerance,
                                      path=here, ignored=ignored))
        for key in actual:
            if _commentary(key) or key in golden:
                continue
            here = f"{path}.{key}" if path else key
            if here not in ignored:
                found.append(Difference("EXTRA", here, None, actual[key]))
        return found
    if isinstance(golden, list):
        if not isinstance(actual, list):
            return [Difference("MISMATCH", path, golden, actual)]
        if len(golden) != len(actual):
            return [Difference("MISMATCH", f"{path} (length)", len(golden), len(actual))]
        found = []
        for i, (want, got) in enumerate(zip(golden, actual)):
            found.extend(_compare(want, got, tolerance=tolerance,
                                  path=f"{path}[{i}]", ignored=ignored))
        return found
    if _is_number(golden) and _is_number(actual):
        allowed = tolerance if _tolerated(path) else 0
        return [] if abs(golden - actual) <= allowed else [
            Difference("MISMATCH", path, golden, actual)]
    # A BOOLEAN is never a number here, and Python disagrees: `False == 0` and
    # `True == 1`, so a flag standing in for an amount — or an amount for a flag
    # — scored clean under a bare equality. Both are malformed agent output of
    # exactly the kind this comparator exists to catch.
    if isinstance(golden, bool) != isinstance(actual, bool):
        return [Difference("MISMATCH", path, golden, actual)]
    return [] if golden == actual else [Difference("MISMATCH", path, golden, actual)]


def render(differences: "list[Difference]", case: str) -> str:
    if not differences:
        return f"MATCH: the result is the {case} golden, within tolerance."
    lines = [f"{len(differences)} difference(s) against the {case} golden:"]
    lines += [f"  {d}" for d in differences]
    lines.append("")
    lines.append("A MISSING field is a step that did not run — the false-completion "
                 "shape. An EXTRA field is a section that came from somewhere else. "
                 "Neither is rounding.")
    return "\n".join(lines)


GOLDENS = Path(__file__).resolve().parent / "goldens"


def golden_path(case: str) -> Path:
    """Where a case's golden lives. One answer, because the layout was written
    out at four call sites and renaming the file would have fixed some of
    them."""
    return GOLDENS / case / "golden_result.json"


def cases() -> "list[str]":
    """The cases that have a golden, for an error message that can name them."""
    if not GOLDENS.is_dir():
        return []
    return sorted(p.name for p in GOLDENS.iterdir()
                  if (p / "golden_result.json").is_file())


def _load(path: Path, what: str) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise SystemExit(f"ERROR: could not read {what} at {redact(path)}: {redact(e)}")
    if not isinstance(data, dict):
        raise SystemExit(f"ERROR: {redact(path)} is not a result object")
    return data


def _dollars(text: str) -> float:
    """`--tolerance`, refused at the boundary rather than in a traceback.

    `float()` accepts `inf`, `Infinity` and `nan`, and argparse's own `type`
    error is the only thing between them and `compare`'s guard — which raises,
    correctly, but out of `_main` as an unhandled exception. One predicate and
    one sentence behind both, so the two cannot come to disagree about what a
    tolerance is.
    """
    try:
        value = float(text)
        _check_tolerance(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from e
    return value


def _main(argv: list) -> int:
    parser = argparse.ArgumentParser(
        prog="evals.compare",
        description="Score a computed result.json against a golden (V3).")
    parser.add_argument("--result", required=True, help="the result.json to score")
    parser.add_argument("--case", default="ca_mfj_2026", help="the golden to score against")
    parser.add_argument("--tolerance", type=_dollars, default=DEFAULT_TOLERANCE,
                        help="how many dollars apart still counts as the same "
                             "figure (money fields only)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    golden = golden_path(args.case)
    if not golden.is_file():
        print(f"ERROR: no golden for case {args.case!r} — the cases are "
              f"{', '.join(cases()) or 'none'}", file=sys.stderr)
        return 1
    differences = compare(_load(golden, "the golden"),
                          _load(Path(args.result), "the result"),
                          tolerance=args.tolerance)
    if args.json:
        print(json.dumps({"case": args.case, "matched": not differences,
                          "differences": [d.to_dict() for d in differences]}, indent=2))
    else:
        print(render(differences, args.case))
    return 0 if not differences else 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
