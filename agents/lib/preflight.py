#!/usr/bin/env python3
"""Engine-presence preflight — refuse to compute a return without the gates.

A skill in this toolkit is a DIRECTORY of Markdown instructions, and those
instructions run the computation engine as external scripts. `tax-compute`
alone shells out to `tax_law_fetcher.py freshness` (the staleness gate),
`capabilities.py` (the out-of-scope gate) and `tie_out.py` (the intake tie-out
gate). All of them live in `agents/lib/`, a SIBLING of `.agents/skills/` —
outside the skill directory.

Copy a skill to anywhere those siblings do not exist — an upload to another
agent's skill store, a gist, a colleague's zip — and every one of those gates
silently disappears while the instructions still read as a complete, well-formed
procedure for computing a return. AGENTS.md already names this failure mode for
`SKILL.md` copied without its `references/`; there what goes missing is step
detail, here it is the hard gates themselves. A return computed with the tie-out
gate absent is exactly the dropped-1099 CP2000 under-report that gate exists to
prevent, and nothing downstream can tell that it never ran.

THIS SCRIPT CANNOT REPORT ITS OWN ABSENCE. It ships inside the very directory
whose disappearance is the failure being detected: when that happens the skill's
invocation dies with "No such file or directory" and nothing here executes. The
fail-closed half therefore lives in the SKILL text, which instructs the agent to
STOP when this command fails FOR ANY REASON, not existing included. What this
script adds is everything a bare existence check cannot see — a partial copy
missing any one of the engine's modules, an import that raises, an engine
present but not computing correctly, and a check running on a different
interpreter from the one the skill's next command will use.

Exit codes mirror `capabilities.py` and `tie_out.py`: 0 = engine usable,
2 = BLOCKED (do not compute a return), 1 = the check itself could not run.
"""
# Same rule as secure_fs, and for a sharper reason: this module runs on whatever
# interpreter the agent reached, and an unsupported one is the failure it most
# often has to REPORT. A PEP 604 annotation here is valid 3.9 grammar and raises
# at def time, so without this line the gate would not merely misreport — it
# would print nothing at all, which is worse than a wrong verdict.
from __future__ import annotations

import contextlib
import importlib
# EXPLICIT, not reached through `importlib`. Importing a package does not bind
# its submodules, so `importlib.metadata.distributions()` raised AttributeError
# on the interpreter that had not already imported it for some other reason —
# an error inside the gate, on a machine where nothing else happened to pull it
# in first. `importlib.util` is here for that same reason, not a second one.
import importlib.metadata
import importlib.util
import io
import json
import math
import os
import platform
import re
import shlex
import shutil
import stat
import sys
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).resolve().parent
# The tree this engine lives in — `<root>/agents/lib/preflight.py`. `run.sh`
# derives the SAME root from its own location and activates `<root>/.venv`, so
# this is the virtualenv every engine command actually runs on, whatever
# directory the caller happens to be in.
_ENGINE_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_ENGINE_ROOT))

MIN_PYTHON = (3, 10)
MIN_PYTHON_STR = ".".join(str(p) for p in MIN_PYTHON)

# The gate-bearing modules: the four whose silent absence produces a return that
# looks complete and is not. They are listed apart from the rest because the
# known-answer probe below runs against the first of them, and because a verdict
# names them even when it passes.
GATE_MODULES = (
    ("agents.lib.tax_computations", "the tax math every computed line depends on", "compute_bracket_tax"),
    ("agents.lib.tax_law_fetcher", "the tax-law staleness gate", "load_tax_law"),
    ("agents.lib.capabilities", "the out-of-scope capability gate", "_main"),
    ("agents.lib.tie_out", "the intake-to-result tie-out gate", "load_ledger"),
)

# The rest of the engine, and it is checked just as hard. Most of these ARE some
# skill's whole computation — `projection.py` is what /tax-project DOES — and
# nothing imports them, so a copy that keeps the four gate modules and drops
# these passes a gate-only check while the skill it powers has nothing left to
# run. Checking the gates alone would have made this preflight report OK on a
# copy with thirteen of twenty-one modules missing. `secure_fs` is the
# exception that proves the roster has to be whole rather than reasoned about:
# all four gates import it, so it is NOT one of the thirteen, and a roster
# admitting only what the gates cannot reach would have had to know that.
#
# The set is deliberately whole rather than per-skill. The failure being caught
# is `agents/lib/` not travelling with a copied skill, which is all-or-nothing;
# a per-skill roster would add a second list to keep in step with the first for
# no gain against that failure. `tests/test_preflight.py` pins this roster
# against the directory listing so a NEW module cannot be silently omitted.
SKILL_MODULES = (
    ("agents.lib.amend", "/tax-amend's Form 1040-X three-column engine", "three_column"),
    ("agents.lib.archive", "/tax-archive's year-end cutover", "archive_year"),
    ("agents.lib.crypto_importer", "/tax-parse-crypto's Form 8949 lot engine", "CryptoTransaction"),
    ("agents.lib.filing_package", "/tax-generate's filing-package assembly", "assembly_order"),
    ("agents.lib.irs_dates", "the §7503 next-business-day deadline arithmetic", "next_business_day"),
    ("agents.lib.loss_harvest", "/tax-harvest's wash-sale scanner", "derive_holding_period"),
    ("agents.lib.parse_utils", "/tax-parse's document extraction", "extract_pdf_text"),
    ("agents.lib.projection", "/tax-project's mid-year projection", "_main"),
    ("agents.lib.ptet", "/tax-ptet's election analyzer", "ptet_tax"),
    ("agents.lib.retirement_dashboard", "/tax-retirement's contribution dashboard", "compute_401k_employee_limit"),
    ("agents.lib.rollover", "/tax-rollover's carryforward pull", "_main"),
    ("agents.lib.roth_ladder", "/tax-roth-convert's conversion ladder", "_main"),
    ("agents.lib.scenario", "/tax-scenario's what-if engine", "_main"),
    ("agents.lib.secure_fs", "the owner-only permission enforcement every "
                             "runtime write goes through", "secure_path"),
    ("agents.lib.tax_calendar", "/tax-calendar's deadline calendar", "events_1040es"),
    ("agents.lib.withholding", "/tax-withholding's W-4 true-up", "_main"),
    ("agents.lib.yoy_diff", "/tax-review's year-over-year diff", "_main"),
)

ENGINE_MODULES = GATE_MODULES + SKILL_MODULES

# Known-answer probe. These brackets are SYNTHETIC and deliberately not real tax
# law: the probe proves the engine COMPUTES, never that any figure is current.
# A real bracket here would go stale (AGENTS.md §2) and would make a freshness
# problem present itself as an engine problem.
_PROBE_BRACKETS = [
    {"rate": 0.10, "min": 0, "max": 1000},
    {"rate": 0.20, "min": 1000, "max": None},
]
_PROBE_INCOME = 2000.0
_PROBE_EXPECTED = 300.0  # 1000 @ 10% + 1000 @ 20%

# The package itself, checked APART from the roster because it does not fit that
# shape: `_cli_dispatch_error` looks for `<stem>.py` beside this file and a
# package has no such leaf, and an initialiser must not carry a `__main__`
# dispatch anyway.
_GUARD_MODULE = "agents.lib"
_GUARD_ROLE = "the host-interpreter guard every engine import passes through"
_GUARD_SYMBOL = "host_interpreter_defect"
# The status the initialiser exits with when it refuses — preflight's own
# "blocked", so a skill branching on the status reads the two the same way.
_GUARD_STATUS = 2

def _venv_layout(venv=None) -> str:
    """The subdirectory a venv keeps its activate script in — `bin`, or
    `Scripts` on Windows.

    ASK THE VENV when there is one to ask. Guessing from `os.name` is nearly
    right, and wrong exactly when it matters: this check runs on whatever
    interpreter the agent reached, which in the failure being diagnosed is not
    the one that built the workspace venv. Naming the wrong directory is not
    cosmetic — under Git Bash a `source .venv/bin/activate` against a `Scripts/`
    venv fails silently, the `;` runs the gate on the host interpreter, and the
    refusal that follows advises the command that just no-oped. The rebuild
    fallback then recreates `Scripts/` and lands the reader back here, which is
    a gate with no exit at all.
    """
    # Layout is a PRESENTATION choice — which spelling to print in a remedy —
    # so an unreadable venv falls back to the platform default rather than
    # propagating. `_venv_defect` is where an OSError has to mean something.
    try:
        found = _activate_path(venv) if venv is not None else None
    except OSError:
        found = None
    if found:
        return found[0]
    return "Scripts" if os.name == "nt" else "bin"


def _activate_path(venv):
    """`(subdir, path)` for the activate script this venv actually has, or None.

    ONE probe, called by `_venv_layout` and `_venv_defect` both. They ran the
    same `("bin", "Scripts")` walk separately with different error handling,
    which is how the `.exists()` conflation below ended up fixed in one of them
    and not the other — and two functions that must agree about a venv's layout
    were free to disagree.

    `os.lstat` with an explicit FileNotFoundError, never `Path.exists()`:
    `exists()` swallows EACCES/EPERM and answers False, so an intact virtualenv
    under a permission problem was diagnosed "has no activate script" and
    answered with `rm -rf .venv`. That is the same conflation `_relocated`
    refuses in writing; the fix was applied there and not here, where the
    verdict is the destructive one. `None` means "no activate script"; the
    UNREADABLE case raises, so a caller cannot mistake it for absence.
    """
    for subdir in ("bin", "Scripts"):
        candidate = venv / subdir / "activate"
        try:
            os.lstat(candidate)
            return subdir, candidate
        except (FileNotFoundError, NotADirectoryError):
            # NotADirectoryError is a SIBLING of FileNotFoundError, not a
            # subclass: a `.venv/bin` that is a regular file (a botched copy, a
            # flattened extraction) raised it, propagated, and the venv was
            # reported "cannot tell, do not delete" when `rm -rf` is the only
            # fix. Both mean the activate script is not there.
            continue
    return None


def _cd_step(workspace: str = "") -> str:
    """`cd <root>`, when the commands below would otherwise run in the wrong one.

    Every remedy in this module names `.venv` RELATIVELY, and `--workspace` lets
    the report describe a root that is not the current directory. Without this
    the reader follows `rm -rf .venv` and `python3 -m venv .venv` in whatever
    directory they happen to be in: a stray virtualenv appears beside them, the
    real one is untouched, and the next run refuses in identical words. On the
    uncomparable path it is worse than useless — `ls -ld .venv` reports "No such
    file" about a virtualenv that was never the subject, under a heading saying
    not to delete it on this evidence.
    """
    if not workspace:
        return ""
    try:
        if os.path.samefile(workspace, os.getcwd()):
            return ""
    except OSError:
        # Cannot tell, so say the safe thing: a `cd` to the directory you are
        # already in costs nothing, and omitting one you needed repeats the
        # refusal.
        pass
    # REDACTED, like every other path this module prints. `_report_to_dict`
    # redacts `workspace` and `render_markdown` redacts `venv_error`; this
    # returned the raw `--workspace` value into the remedy text, which both
    # channels then printed verbatim — so the same JSON document carried
    # `"workspace": "/w/[REDACTED]"` and, a few fields later, the whole path
    # inside `remedy`. The structural detector in test_secure_fs walks `raise`
    # and `print` nodes and cannot see a leak that leaves by `return`.
    #
    # A path that needs redacting yields a `cd` the reader cannot paste, and
    # that is the correct trade: AGENTS.md §1 forbids emitting the value at all,
    # and only a path shaped like an SSN is touched — an ordinary workspace root
    # passes through `_redact` unchanged and stays runnable.
    #
    # QUOTED, and after the redaction rather than before it. A workspace root
    # holding a space — `~/Documents/Tax Prep` is an ordinary place to keep a
    # return — rendered as `cd /Users/x/Tax Prep`, which `cd` reads as two
    # arguments and refuses. Every remedy in this module chains off that step,
    # so the whole recovery path failed on its first command for a reader who
    # is already blocked; a metacharacter (`$`, `;`, `&`) would have been worse
    # than useless, being executed. `shlex.quote` leaves an ordinary path
    # untouched, so the common verdict is unchanged.
    return f"cd {shlex.quote(_redact(workspace))}"


def _rebuild_steps(python: str = "python3", layout: str = "bin",
                   workspace: str = "", include_rm: bool = True) -> str:
    """The rebuild procedure, in ONE place.

    It was three near-copies — two sharing a constant and the Python remedy
    retyping the same three lines with the interpreter substituted — inside a
    module whose whole thesis is that a remedy which cannot resolve its failure
    is worse than none. A third copy is a third chance to drift.

    `include_rm` rather than a caller slicing the first line off the returned
    text. `clone_remedy` did exactly that — `.split(chr(10), 1)[1]` — which is a
    positional coupling to a helper whose whole purpose is that its steps live
    in one place: adding a line here (a `cd`, as this change does) silently
    deleted the NEW first line from the clone remedy while leaving `rm -rf .venv`
    in a directory the reader had just created. It would not have failed; it
    would have printed something wrong.
    """
    # The interpreter is NAMED BY PATH, not reached through an activation. The
    # activate form silently no-ops against a `Scripts/` venv under Git Bash,
    # which is why README and QUICKSTART were rewritten away from it — and this
    # is the copy a blocked reader actually follows.
    # CHAINED, and this is the line that matters most in the module. The `cd`
    # used to sit on its own above `rm -rf .venv`: paste the block after a `cd`
    # that fails — a moved root, or a path `_cd_step` redacted, which it does
    # precisely when the path is sensitive — and the deletion runs in whatever
    # directory the reader is actually in, destroying an intact virtualenv. That
    # is the exact destruction `inspect_remedy` and the uncomparable state exist
    # to prevent, introduced by the fix for a lesser problem.
    #
    # Every step chains, not just the first: `python3 -m venv .venv` after a
    # failed `cd` builds a stray virtualenv beside the reader, and pip then
    # fills it with the pinned tree while the real one stays broken.
    steps = []
    cd = _cd_step(workspace)
    if cd:
        steps.append(cd)
    if include_rm:
        steps.append("rm -rf .venv")
    steps.append(f"{python} -m venv .venv")
    steps.append(f".venv/{layout}/python -m pip install -r requirements.txt")
    return "    " + " &&\n      ".join(steps)


# The clone URL also appears in README.md (twice), docs/QUICKSTART.md and
# CONTRIBUTING.md. Nothing pins the five against each other, and this one is
# different in kind from the four: it is the SOLE escape route for a reader
# whose skill was copied away from its engine, so a rename that updates the
# documents and misses this leaves a remedy that 404s — in the module arguing
# that a remedy which cannot resolve its failure is worse than none.
# `test_the_clone_url_matches_the_documents` compares them.
CLONE_URL = "https://github.com/yarikmsu/tax-prep-skills.git"


def clone_remedy(layout: str = "bin", python: str = "python3") -> str:
    return (
        "Run this skill from a full clone of the toolkit, where `agents/lib/` "
        "sits beside `.agents/skills/`:\n\n"
        # CHAINED, for the reason `_rebuild_steps` states: a clone that fails —
        # no network, a name already taken, a 404 after a rename — must not be
        # followed by a `cd` into a directory that is not there, and then by a
        # venv build beside the reader. This is the one remedy a reader whose
        # skill was copied away from the engine has no alternative to.
        f"    git clone {CLONE_URL} &&\n"
        "      cd tax-prep-skills &&\n"
        # From _rebuild_steps, without its `rm -rf`: retyping the venv and
        # install steps here is the fourth copy of what that helper was
        # extracted to own, and this is the path a reader whose skill was copied
        # away from the engine actually follows — a change to the install step
        # (say `--require-hashes`, which the lock now supports) must not miss it.
        # No `workspace` either: the two lines above have just put the reader
        # inside the fresh clone, which is the root every step here is relative
        # to.
        f"{_rebuild_steps(python=python, layout=layout, include_rm=False).lstrip()}\n\n"
        "A skill copied on its own carries the instructions but not the engine "
        "they call, and the instructions alone cannot compute a correct return."
    )


def python_remedy(layout: str = "bin", defer_to: str = "",
                  workspace: str = "") -> str:
    """The version fix. `defer_to` names WHO builds the environment instead.

    Three cases, and the reason they are one string rather than a boolean: the
    rebuild is suppressed for two unrelated reasons, and each needs its own
    sentence. `"inspect"` is an uncomparable `.venv` that must not be deleted on
    this evidence; `"clone"` is a missing engine, where the clone remedy above
    has already built the environment inside the fresh checkout with this same
    interpreter. A single `keep_venv` flag covering both printed the "no
    evidence to justify deleting it" prose over a report whose problem was that
    the engine's files were gone — one symbol, two concepts, which this
    repository has paid for before.
    """
    if defer_to == "clone":
        return (
            f"Install or select Python {MIN_PYTHON_STR}+ before running the "
            f"steps above — this interpreter is older than the toolkit "
            f"supports, and `python3 -m venv` inherits the version of the "
            f"`python3` that runs it, so building the fresh clone's environment "
            f"with THIS one recreates the same failure:\n\n"
            f"    # macOS: brew install python@3.12 · Debian/Ubuntu: apt install python3.12\n\n"
            f"The clone steps already name `python3.12` for that reason. Any "
            f"{MIN_PYTHON_STR}+ interpreter works; 3.12 is named because CI "
            f"runs it."
        )
    if defer_to == "inspect":
        # The rebuild below opens with `rm -rf .venv`, and on an uncomparable
        # venv `inspect_remedy` renders on the SAME report saying **Do not
        # delete it on this evidence.** One document, two instructions, and the
        # destructive one first: a reader on a stock macOS 3.9 whose `.venv`
        # merely sits under an unreadable parent followed it and destroyed a
        # working virtualenv. The interpreter is still the root cause and still
        # has to change; only the half that deletes waits for evidence.
        return (
            f"Install or select Python {MIN_PYTHON_STR}+ — this interpreter is "
            f"older than the toolkit supports, and `python3 -m venv` inherits "
            f"the version of the `python3` that runs it:\n\n"
            f"    # macOS: brew install python@3.12 · Debian/Ubuntu: apt install python3.12\n\n"
            f"Then settle the virtualenv question below BEFORE rebuilding with "
            f"it — the rebuild starts by deleting `.venv`, which this check has "
            f"no evidence to justify. Any {MIN_PYTHON_STR}+ interpreter works; "
            f"3.12 is named because CI runs it."
        )
    return (
        f"Install or select Python {MIN_PYTHON_STR}+ and rebuild the environment "
        f"WITH it. `python3 -m venv` inherits the version of the `python3` that "
        f"runs it, so recreating `.venv` with the interpreter that just failed "
        f"this check leaves it failing — the version has to change first:\n\n"
        f"    # macOS: brew install python@3.12 · Debian/Ubuntu: apt install python3.12\n"
        f"{_rebuild_steps('python3.12', layout, workspace=workspace)}\n\n"
        f"Any {MIN_PYTHON_STR}+ interpreter works; 3.12 is named because CI runs it."
    )


# No module-level rendering here either. Keeping one "because a test asserts
# against it" is how the other three survived, and the test can call the
# function — a snapshot pinned at the `bin/` layout is a fourth copy of the same
# text in the module whose thesis is that a second copy is a second chance to
# drift.

# Why this remedy ends with an escape from itself: the diagnosis below tells a
# `.venv` that merely was not activated from one that cannot work by ENUMERATING
# the ways it can be defective, and a way not enumerated gets this advice. The
# reader then chains the activation, is refused again in the same words, and has
# no route out — `tax-setup` is none, it stops at this same gate. So the primary
# advice stays first and light, and the fallback is spelled out and conditioned
# on having already tried it, which is a state the reader can check and this
# check cannot.
# A third state, distinct from "" (fine, or merely unactivated) and from a
# defect string. A stat that FAILED is not evidence about the venv, and both
# other answers are wrong for it: "chain the activation" loops, and "delete and
# rebuild" destroys a virtualenv whose only problem may be an unreadable parent.
_UNCOMPARABLE = "\x00uncomparable"
# A FOURTH state, and it is about the caller's argument rather than about any
# virtualenv: `--workspace` named a root that does not exist. Folding it into
# the defect string made `venv_broken` true, whose remedy is `rm -rf .venv` and
# `python3 -m venv .venv` inside a directory that is not there — commands that
# cannot work, under a diagnosis that is not what went wrong.
_WORKSPACE_MISSING = "\x00workspace-missing"
# A FIFTH, and the only one that does not block: there is no `.venv` at all.
# Nothing to compare, nothing broken, and nothing this check can certify either.
_NO_VENV = "\x00no-venv"


def workspace_remedy(workspace: str = "") -> str:
    return (
        f"Check the `--workspace` argument: `{_redact(workspace)}` is not a "
        f"directory that exists. Nothing here is a statement about a "
        f"virtualenv or about the engine — this check never got far enough to "
        f"look at either, and the remedies that rebuild `.venv` would run "
        f"inside a root that is not there.\n\n"
        # Quoted for `_cd_step`'s reason: this is the one command on the page,
        # and `ls -ld /Users/x/Tax Prep` lists two directories that are not the
        # subject — under a heading saying the argument names a directory that
        # does not exist, which is then what the output appears to confirm.
        f"    ls -ld {shlex.quote(_redact(workspace))}\n\n"
        f"Pass the workspace root the return actually lives in, or drop the "
        f"option and run from that directory: with no `--workspace` this check "
        f"resolves `.venv` against the current one."
    )


def no_venv_remedy(workspace: str = "") -> str:
    """Build the workspace virtualenv that is not there.

    A function, not a paragraph inside `render_markdown`, for the reason every
    other remedy here is one: it has TWO channels to serve. The markdown warning
    carried these steps inline, so `--json` reported the same state with an
    empty `remedy` — a consumer told nothing is blocking, given no instruction,
    and left computing a return on whatever host interpreter it was on. That is
    the AGENTS.md §7 violation this warning exists to name, surviving on the
    channel nothing checked, exactly as the `probe_ran` omission did.
    """
    return (
        "Create the workspace virtualenv and install the pinned tree into it "
        "before computing a return:\n\n"
        f"{_rebuild_steps(workspace=workspace, include_rm=False)}")


def inspect_remedy(layout: str = "bin", workspace: str = "") -> str:
    cd = _cd_step(workspace)
    # Without the `cd` this remedy is worse than silent: `ls -ld .venv` run in
    # the wrong directory answers "No such file or directory" about a virtualenv
    # that was never the subject, under a heading saying not to delete it on
    # this evidence — evidence the reader has just been handed a way to
    # manufacture.
    look = f"    {cd}\n" if cd else ""
    return (
        "This check could not stat the workspace virtualenv, which says nothing "
        "about the virtualenv itself — the failure may be a permission on a "
        "parent directory, a mount that was briefly unavailable, or a lock held "
        "by another process. **Do not delete it on this evidence.** Find out "
        "which:\n\n"
        f"{look}"
        f"    ls -ld .venv .venv/{layout} .venv/{layout}/activate\n"
        "    python3 -c \"import sys; print(sys.prefix)\"\n\n"
        "If those succeed, re-run this check — a transient failure needs "
        "nothing else. If they fail too, fix the access problem they name; the "
        "virtualenv is not what is broken."
    )


def dependency_remedy(layout: str = "bin", has_venv: bool = True,
                      workspace: str = "") -> str:
    # The venv step is NOT optional on a fresh clone. A bare `pip install` there
    # targets the system interpreter, which AGENTS.md §7 forbids and PEP 668
    # refuses outright — and `tax-setup`, whose Step 6 would have created the
    # environment, is blocked by this same gate at its first step, so that route
    # is closed. Say so, the way rebuild_remedy does.
    #
    # NAMED BY PATH and CHAINED, which is `_rebuild_steps`' form and now this
    # one's. The two lines here used to be `python3 -m venv .venv && source
    # .venv/{layout}/activate` followed by an unchained `pip install`: the
    # activation is dropped by any agent that opens a fresh shell per command
    # (AGENTS.md §7), which is the exact failure `run.sh` exists to prevent, and
    # it silently no-ops on a `Scripts/` venv besides. Either way the pinned
    # tree lands in the HOST interpreter, three lines under a comment saying
    # that is forbidden.
    # NO tax-setup caveat here. This remedy rides a WARNING (a missing package
    # does not block), so `tax-setup` reaches its own Step 6 perfectly well —
    # the sentence is true only on the blocking paths, and `rebuild_remedy`
    # carries it there. A lying caveat is worse than a missing one.
    return (
        "Install the pinned dependencies into the workspace virtualenv:\n\n"
        f"{_install_steps(layout, has_venv, workspace)}\n\n"
        "The engine's own modules import fine without them — every third-party "
        "import in the engine is lazy or guarded — so this check is the only "
        "thing between an agent and a `/tax-parse` run that cannot open a PDF."
    )


def _install_steps(layout: str = "bin", has_venv: bool = True,
                   workspace: str = "") -> str:
    """The install command, in ONE place, for the two remedies that need it.

    A missing package and a drifted version have the SAME fix — reinstall the
    lock into the workspace virtualenv — and a second transcription of a
    command whose every detail is load-bearing (the venv step, the path form,
    the `&&`) is what the comment below this one records going wrong twice.
    """
    cd = _cd_step(workspace)
    prefix = f"{cd} && " if cd else ""
    return (f"    {prefix}.venv/{layout}/python -m pip install -r requirements.txt"
            if has_venv else
            _rebuild_steps(layout=layout, workspace=workspace,
                           include_rm=False))


def drift_remedy(layout: str = "bin", has_venv: bool = True,
                 workspace: str = "") -> str:
    """Installed versions are not the pinned ones.

    Its own function rather than a branch inside `dependency_remedy`: the steps
    are shared and the SENTENCES are not. That remedy closes on "the only thing
    between an agent and a /tax-parse run that cannot open a PDF", which is the
    missing-package failure and a different one from this: here the four direct
    imports all succeed, and what is wrong is the resolution underneath them.
    """
    return (
        "Reinstall the locked tree into the workspace virtualenv, which pins "
        "the whole transitive set by hash:\n\n"
        f"{_install_steps(layout, has_venv, workspace)}\n\n"
        "A version the lock does not name is one the test suite never ran "
        "against, and the packages under these pins are the document parsers — "
        "so /tax-parse, /tax-verify and /tax-parse-crypto refuse to open a "
        "document until this is restored, while the skills that open none are "
        "unaffected. That split is why the gate warns and the parse refuses. "
        "If the newer version is deliberate, mirror it into `requirements.in` "
        "and regenerate the lock (AGENTS.md §7); do not leave the workspace "
        "off it."
    )


def probe_failure_remedy() -> str:
    """Everything imported and the arithmetic came out wrong.

    Routing this to `import_failure_remedy` told the reader to "read the error
    above — it names the module and the exception" when nothing failed to import
    and no exception was raised: advice for a failure that was not reported.
    """
    return (
        "Every module imported, so this is not a missing or unreadable engine "
        "— the tax math itself returned the wrong answer on a known-answer "
        "probe with synthetic brackets. That means the installed "
        "`tax_computations.py` is not the one this check expects:\n\n"
        "    git status agents/lib/tax_computations.py\n"
        "    git diff agents/lib/tax_computations.py\n\n"
        "Restore it (`git checkout -- agents/lib/tax_computations.py`) or, if "
        "the change is deliberate, run its own self-test — "
        "`sh agents/lib/run.sh agents/lib/tax_computations.py` — which "
        "exercises the same "
        "arithmetic against IRS worksheet values."
    )


def import_failure_remedy() -> str:
    """The files are THERE and will not load — neither re-cloning nor a version
    change is the answer, and before this there was no answer at all.

    Splitting the clone remedy off `engine_missing` left this case with an empty
    "How to proceed", which is the one thing a refusal must never be.

    It covers three shapes, not one: an import that raised, a file that imported
    and exposes nothing, and a file that imported and lost its command-line
    entry point. The opening sentence used to name only the first, while the
    bullet a reader actually needs — restore the truncated file — is the same
    for all three; a stub was already routed here saying "will not import"
    about a module that had just imported.
    """
    return (
        "The engine's files are present but at least one is not the complete "
        "module — it failed to import, or imported without what the skills "
        "then call. This is neither a missing clone nor a version problem. "
        "Read the failure above — it names the module and what was wrong. The "
        "usual causes:\n\n"
        "- a partial or interrupted checkout: `git status` and `git checkout -- "
        "agents/lib/` restore a truncated file;\n"
        "- a dependency the module imports at module scope that is not "
        "installed: `.venv/bin/python -m pip install -r requirements.txt` "
        "(`.venv/Scripts/python` on Windows) — named by PATH, because a bare "
        "`pip` here targets the system interpreter, which AGENTS.md §7 forbids "
        "and PEP 668 refuses outright;\n"
        "- a local edit in progress: the traceback names the line."
    )


def _usable_venv(report) -> bool:
    """A venv you could actually `pip install` into.

    NOT "a `.venv` directory exists". A broken or unstattable one answered True
    through `Path.is_dir()` — the primitive three comments in this module ban
    for swallowing EACCES — and `dependency_remedy` then printed a bare
    `pip install` that runs against the HOST interpreter, immediately above a
    rebuild step that discards whatever it did.

    `os.lstat` with an explicit ENOENT, and `venv_broken`/`venv_uncomparable`
    both count as unusable: the rebuild has to come first for those.
    """
    if report.venv_broken or report.venv_uncomparable:
        return False
    venv = _workspace_venv(report.workspace or None)
    try:
        # `os.stat` and S_ISDIR — existence alone was MORE permissive than the
        # `is_dir()` this replaced: a regular file or a dangling symlink named
        # `.venv` lstat'd fine, read as usable, and earned a bare `pip install`
        # aimed at the host interpreter.
        return stat.S_ISDIR(os.stat(venv).st_mode)
    except OSError:
        return False


def _rebuild_interpreter(report) -> str:
    """`python3`, or a named supported version when this one is unsupported.

    A virtualenv inherits the version of the `python3` that builds it, so a
    rebuild instruction issued to a reader on 3.9 has to name something else or
    it rebuilds the same failure.
    """
    return "python3" if report.python_ok else "python3.12"


def venv_remedy(layout: str = "bin", python: str = "python3",
                workspace: str = "", escape: bool = True) -> str:
    """The activation advice, naming the activate script that ACTUALLY exists.

    A `Scripts/` venv told to `source .venv/bin/activate` gets a silent no-op,
    so the reader would be following advice their own terminal has already
    refused — and this remedy's whole escape hatch is conditioned on "if you had
    ALREADY chained it", which they cannot honestly answer when the command they
    were given never worked.

    `workspace` prepends a `cd` when the venv's root is not the directory the
    reader is standing in, and without it this remedy could hand back the
    command that just produced the refusal.

    BOTH sides are anchored to the engine tree now, and that is what keeps this
    remedy honest: `run.sh` derives its root from its own location, and
    `_workspace_venv` defaults to the same one. They disagreed once — the
    launcher anchored, this module still resolving against the CWD — and the
    disagreement was a refusal with no exit: from any directory holding its own
    `.venv`, the verdict blocked because the activated venv was the engine's,
    and the remedy was "run it through the launcher", byte-identical to the
    command that had just been run. The earlier revision of this paragraph
    described the pre-anchor behaviour and built its argument on it, so the next
    reader reasoned from a premise `run.sh` already contradicted.
    """
    cd = _cd_step(workspace)
    prefix = f"{cd} && " if cd else ""
    note = ("" if layout == "bin" else
            f"\n\n(This workspace's venv keeps its scripts in `{layout}/`, the "
            f"Windows layout. `run.sh` handles it: it tries both spellings, each "
            f"behind its own `-f` test, and on the Windows one execs `python` — "
            f"a stdlib venv puts `python.exe` in `Scripts/` and never "
            f"`python3.exe`.)")
    return (
        "Run it through the launcher, which is what every engine invocation in "
        "the skills uses — it activates the workspace virtualenv and execs the "
        "interpreter that virtualenv actually installed:\n\n"
        f"    {prefix}sh agents/lib/run.sh agents/lib/preflight.py"
        f"{note}"
        + ("" if not escape else
        "\n\nIf you had ALREADY chained it and this still blocks, then "
        "activating is not the missing step: the `.venv` cannot put you on its "
        "own interpreter, and repeating the activation will not change that. "
        "Rebuild it instead:\n\n"
        # ...and the `workspace` here too. The FIRST block in this remedy got
        # the `cd` and this one did not, so the paragraph that opens with
        # `rm -rf .venv` was the single unanchored block in the most common
        # blocking verdict there is.
        f"{_rebuild_steps(python=python, layout=layout, workspace=workspace)}")
    )


def rebuild_remedy(layout: str = "bin", python: str = "python3",
                   workspace: str = "") -> str:
    return (
        "This `.venv` cannot put you on its own interpreter, so the activation "
        "`run.sh` performs is not the missing step and repeating it will not "
        "help. A virtualenv is neither relocatable nor repairable in place — "
        "its `activate` script, its console scripts' shebangs and its "
        "`pyvenv.cfg` all hold absolute paths, to the venv itself and to the "
        "base interpreter that built it — so delete and rebuild it:\n\n"
        f"{_rebuild_steps(python=python, layout=layout, workspace=workspace)}\n\n"
        "Run this yourself rather than reaching for `tax-setup`: every skill "
        "opens with this gate, `tax-setup` included, so it stops here too and "
        "never reaches the step that would have built the environment for you."
    )


# No module-level renderings of the remedies. Three of them (`REMEDY`,
# `VENV_REMEDY`, `REBUILD_REMEDY`) had no readers anywhere and were a second,
# layout-fixed `bin/` copy of texts the functions already produce — the exact
# third-copy drift `_rebuild_steps` was introduced to remove, reintroduced by
# the change that removed it. `render_markdown` calls the functions.


def _redact(text) -> str:
    """`secure_fs.redact()`, imported HERE rather than at module scope.

    This module runs precisely when `agents/lib/` may be incomplete, so a
    top-level `from agents.lib.secure_fs import redact` would make the preflight
    die with the very ImportError it exists to REPORT — and it cannot report its
    own failure to import. When `secure_fs` is itself the module that did not
    travel there is nothing to redact with, so the text is dropped rather than
    printed raw: a diagnostic is worth less than a path, and the caller is
    blocked either way.

    Applied to the VARIABLE parts of a verdict — an OSError's text, a probe
    failure, a venv diagnosis — and never to the whole document. Wrapping the
    document withheld the entire BLOCKED verdict in the one scenario this module
    exists for, a copy with no `secure_fs` to import: the agent got a withheld
    marker where it needed the block, the module list and the remedy, all of
    which are authored constants that never carried a path.
    """
    if not text:
        return ""
    try:
        from agents.lib.secure_fs import redact
        return redact(text)
    except (Exception, SystemExit):
        # SystemExit is NOT an Exception, and this runs from render_markdown and
        # _report_to_dict — both OUTSIDE _main's try. A `secure_fs.py` that calls
        # sys.exit() at import (the shape _load catches SystemExit for) therefore
        # terminated the process with that call's own status: `sys.exit(0)` made
        # the gate exit 0 with no output, which every skill reads as "engine
        # present, continue". A fail-OPEN reached through the redactor, in the
        # module whose whole premise is failing closed. Reproduced before fixing.
        return "<withheld: the redactor is part of the missing engine>"


@dataclass(frozen=True)
class ModuleFailure:
    """One gate-bearing module that could not be loaded."""
    module: str
    role: str
    error: str
    # False when `error` is a string THIS module authored, whose only variable
    # is a module basename off ENGINE_MODULES — never a path, never PII. Those
    # must survive a missing redactor: in the flagship scenario (engine copied
    # away, so `secure_fs` is gone too) all twenty-one lines collapsed to the
    # same withheld marker, and "the files are absent" became indistinguishable
    # from "the imports raised", which is the entire diagnostic value.
    needs_redaction: bool = True
    # The FILE was absent, as opposed to the import having raised. Carried
    # rather than re-derived by matching the message text: rewording `_load`'s
    # own string would otherwise flip both the headline and the remedy
    # selection, and a genuinely missing engine would stop being told to
    # re-clone while nothing about the file check had changed. `venv_broken`'s
    # comment states the same rule for the same reason.
    file_missing: bool = False


@dataclass(frozen=True)
class PreflightReport:
    python_version: str
    python_ok: bool
    failures: tuple
    probe_error: str
    modules_checked: int = 0
    venv_error: str = ""
    # Which venv remedy applies: rebuild it, or merely activate it. Carried as
    # state rather than re-derived from the message text, so rewording the
    # diagnosis cannot silently swap the advice back to the one that fails.
    venv_broken: bool = False
    # The stat failed, so nothing is known about the venv either way. Separate
    # from venv_broken because their remedies are opposites: one deletes.
    venv_uncomparable: bool = False
    # The `--workspace` root itself is not there. Neither of the two above: it
    # is a fact about the ARGUMENT, and every remedy that names `.venv` would
    # run inside a directory that does not exist.
    workspace_missing: bool = False
    # The workspace the check was run against, so the verdict can name the
    # activate script that actually exists there rather than guessing `bin/`.
    workspace: str = ""
    # Pinned third-party packages that are not importable.
    missing_dependencies: tuple = ()
    # Packages whose installed version is not the one the lock pins, as
    # `(package, installed, pinned)`, with `installed` None for one the lock
    # requires here and nothing provides. Importable is not pinned, and the lock
    # is part of the parsing correctness contract — see `_drifted_dependencies`.
    drifted_dependencies: tuple = ()
    # Why the comparison above could not be made. Separate from an empty
    # `drifted_dependencies`, which means the opposite: everything matched.
    dependency_lock_error: str = ""
    # Whether the known-answer probe actually ran.
    probe_ran: bool = False
    # There is no `.venv` in the workspace, so the engine is running on the host
    # interpreter with whatever versions it happens to carry. A WARNING, never a
    # block: `tax-setup` Step 6 creates the environment and opens with this same
    # gate, so blocking would deadlock the skill that fixes it.
    no_workspace_venv: bool = False
    # The gate ROLES this run actually loaded, not the full roster. Carried
    # rather than re-derived in the renderer for the reason every other
    # opposite-answer field is: the renderer had the roster's length and printed
    # the roster's contents, so a check of one module announced four gates.
    gates_available: tuple = ()
    # WHY the host-interpreter guard could not be certified, which is the
    # opposite of an empty one: empty means it WAS certified. A fault of the
    # probe's own environment — no space in `$TMPDIR`, a sandbox that refuses
    # the write — never a verdict about the engine, and so never blocking.
    # `_probe_environment_fault` carries the argument.
    guard_probe_error: str = ""

    def __post_init__(self):
        """The two fields are mutually exclusive, and were so only by accident.

        `check()` skips the dependency probe whenever the venv check failed —
        the result would be measured on an interpreter the same verdict then
        disclaims. That made a whole class of handling unreachable: a caveat in
        `render_markdown` explaining the measurement was taken on the wrong
        interpreter has therefore never printed, a guard in `_remedy_text`
        suppressing the install step is never consulted, and the test covering
        both hand-built the combination directly, so it validated the guards
        against a state the module cannot produce — the same could-not-fail
        shape that test's own docstring says it was rewritten to escape.

        Stating it here deletes all three. A future edit that starts probing
        dependencies anyway gets this, naming what else has to change, rather
        than a verdict that lists packages as missing under a headline saying
        this check cannot speak for the interpreter that would hold them.
        """
        if self.missing_dependencies and self.blocking:
            raise ValueError(
                "missing_dependencies on a BLOCKING report: `check()` skips the "
                "probe whenever a blocking cause is already known, because the "
                "packages would have been measured against an engine or an "
                "interpreter this same report disclaims. If that combination is "
                "now wanted, render_markdown and _remedy_text both need the "
                "dependency handling this invariant replaced.")
        # The version comparison rides the same skip for the same reason, so it
        # gets the same invariant rather than a silent second exception to it.
        if (self.drifted_dependencies or self.dependency_lock_error) \
                and self.blocking:
            raise ValueError(
                "a dependency-version verdict on a BLOCKING report: `check()` "
                "skips the lock comparison whenever a blocking cause is already "
                "known, for the reason above — the versions would have been "
                "read off an interpreter this same report disclaims.")

    @property
    def files_missing(self) -> bool:
        """A module's FILE is absent, as opposed to its import having raised.

        The two earn opposite remedies — one is "re-clone", the other never is —
        and both the headline and the remedy selection need the distinction, so
        it is derived once here rather than by re-matching the message text in
        each of them.
        """
        return any(f.file_missing for f in self.failures)

    @property
    def unactivated(self) -> bool:
        """A workspace venv exists, we are not in it, and nothing is wrong with
        it — the one venv state whose remedy is "activate and re-run".

        A property rather than the same three-term expression in `_remedy_text`
        and in `render_markdown`: the third state (`venv_uncomparable`) had to be
        added to both by hand, and a fourth would too, leaving the diagnosis
        paragraph and the remedy selection free to disagree about which case this
        is. `venv_broken`'s own comment makes exactly this argument.

        The fourth state did arrive — `workspace_missing` — and the prediction
        held: without its term here, a `--workspace` root that does not exist
        reads as "merely unactivated" and is answered with `run.sh`, which
        cannot conjure a directory.
        """
        return (bool(self.venv_error) and not self.venv_broken
                and not self.venv_uncomparable and not self.workspace_missing)

    @property
    def engine_missing(self) -> bool:
        """The engine is absent or unusable, as opposed to the ENVIRONMENT being
        wrong — which is the distinction the headline and the remedy selection
        both turn on.

        A property for the reason `unactivated` and `files_missing` are: this
        expression was written out in `_remedy_text` and again in
        `render_markdown`, and `blocking` carries the same two terms a third
        time. Change what counts as a missing engine — a fifth failure kind —
        and the remedy and the headline are free to disagree about the root
        cause on one report, which this file records having already shipped
        once.
        """
        return bool(self.failures) or bool(self.probe_error)

    @property
    def blocking(self) -> bool:
        # `missing_dependencies` is deliberately NOT here. Blocking on it made
        # a missing `reportlab` refuse `/tax-calendar`, `/tax-scenario`,
        # `/tax-ptet`, `/tax-archive` and `/tax-rollover`, none of which import
        # any of the four — a STOP-gate firing on correct data, which is the
        # shape that teaches an agent to reason past a refusal, and the next one
        # it reasons past is the tie-out gate.
        #
        # The bias direction says warn rather than block here: a missing package
        # surfaces LOUDLY at first use, as an ImportError inside /tax-parse,
        # never as a wrong number on a return. The verdict still names them, so
        # the OK line stops claiming more than it checked — which is the other
        # half of this, and the half a silent pass got wrong.
        #
        # That reasoning is only as good as its premise, and for one of the four
        # the premise was FALSE: `parse_utils` caught the `defusedxml`
        # ImportError, printed a warning, and opened `.xlsx` files anyway — so
        # the loud first use was a silent one, on untrusted input, with the XXE
        # guard the package exists to provide simply absent. Fixed where the
        # premise lives (`parse_utils.extract_excel` now refuses) rather than by
        # blocking here, because blocking on it would refuse /tax-calendar,
        # /tax-scenario and every other skill that never opens a workbook —
        # the cry-wolf shape this comment's first paragraph is about.
        return (not self.python_ok or self.engine_missing
                or bool(self.venv_error))


def _load(name: str):
    """Import `name`, returning (module, error_string). Never raises.

    SystemExit is caught alongside Exception because a module that calls
    `sys.exit()` at import time raises it, and SystemExit is not an Exception —
    an uncaught one here would exit 1 ("could not run") for what is really a
    broken engine, which is exit 2. KeyboardInterrupt is deliberately NOT
    caught: an operator interrupting the check has no verdict to report.
    """
    # stderr is captured for the duration of the import. Engine modules have
    # import-time side effects — `parse_utils` calls `defusedxml.defuse_stdlib()`
    # or prints an XXE warning to stderr when it is absent — and that warning
    # landed ABOVE this gate's own verdict, a line about a dependency the gate is
    # not reporting on. The side effects themselves are not suppressible from
    # here (a presence check that imports is a presence check that runs module
    # bodies; that is the trade for checking in-process rather than paying a
    # subprocess per module), but their OUTPUT is not this verdict's.
    # THE FILE FIRST, beside this one. An import resolves through `sys.path`,
    # and the skills do not import — every step invokes the engine as a PATH
    # (`python3 agents/lib/tie_out.py`) or as `-m` from the workspace root. A
    # tree holding this module and nothing else, on a machine where the toolkit
    # is `pip install -e`'d or on PYTHONPATH, would import all twenty-one
    # happily and report "engine present and computing correctly" — and then
    # `tax-compute` Step 12.7 exits 127, a status its "act on 0 or 2" branch
    # does not enumerate, leaving the dropped-1099 tie-out gate a silent no-op
    # behind a green preflight. Checking what the skills will actually run
    # closes the gap the import alone cannot see.
    leaf = _HERE / f"{name.rsplit('.', 1)[-1]}.py"
    try:
        # `os.stat` with an explicit ENOENT, not `Path.is_file()`. That
        # primitive is banned by name three times in this module for swallowing
        # EACCES, and here it was worse than inconsistent: it is version
        # dependent. On 3.14 an unreadable `agents/lib` made all twenty-one
        # modules report "no X.py beside preflight.py" and earned a `git clone`
        # into a directory that already exists; on 3.9 and 3.12 `is_file()`
        # RAISES PermissionError instead, outside this try, and the gate exits 1
        # "could not run". One condition, two verdicts, neither of them true.
        if not stat.S_ISREG(os.stat(leaf).st_mode):
            raise FileNotFoundError(leaf)
    except FileNotFoundError:
        return None, (f"FileNotFoundError: no {leaf.name} beside preflight.py",
                      False, True)
    except OSError as e:
        return None, (f"{type(e).__name__}: {leaf.name} could not be "
                      f"inspected: {e}", True, False)
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stdout(stderr), \
                contextlib.redirect_stderr(stderr), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return importlib.import_module(name), ("", False, False)
    except (Exception, SystemExit) as e:
        # The OS or another module wrote this one — it can name a path.
        return None, (f"{type(e).__name__}: {e}", True, False)


# Column-anchored on purpose: a guard indented inside a function or a class
# never runs at module scope, so it is not the dispatch the skills reach.
_MAIN_DISPATCH = re.compile(r"^if\s+__name__\s*==\s*['\"]__main__['\"]\s*:",
                            re.MULTILINE)


def _cli_dispatch_error(name: str):
    """The module's file carries the `__main__` dispatch the skills EXECUTE.

    Every check above this one asks whether the module IMPORTS — and the skills
    do not import. They run each module as a command: `sh agents/lib/run.sh
    agents/lib/tie_out.py --workspace .`, and then act on its exit code. A file
    truncated between its last function and its `if __name__ == "__main__":`
    tail imports cleanly, exposes the roster symbol, and satisfies every other
    check here — and executing it defines some functions, falls off the end and
    exits 0 with no output. `tax-compute` Step 12.7 reads exit 0 as TIED OUT, so
    the dropped-1099 CP2000 the tie-out gate exists to catch sails through
    behind a green preflight that named that very gate as available.

    Reproduced by truncating `tie_out.py` after `load_ledger` — 18 KB of its
    107 KB. The zero-byte case the `hasattr` check above catches is the same
    failure at a coarser cut; anything truncated mid-statement raises
    SyntaxError at import and was caught before either. This is the middle band,
    and it is the one that looks healthiest.

    Checked by reading the SOURCE rather than by executing it: a subprocess per
    module is twenty-one interpreter starts on the first step of all twenty
    skills, and the CLIs that self-test on no arguments would run their whole
    self-test. Nothing here claims the CLI is correct — only that the file still
    has the entry point without which its exit code means nothing.
    """
    leaf = _HERE / f"{name.rsplit('.', 1)[-1]}.py"
    try:
        source = leaf.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        # It imported a moment ago, so this is a race or a mount that blinked —
        # but unverifiable is not verified, and the bias runs one way here: the
        # thing left uncertified is a hard gate whose silent pass is a wrong
        # return. The OS wrote this message and it can name a path.
        return (f"{type(e).__name__}: {leaf.name} imported but could not be "
                f"read back to verify its command-line entry point: {e}",
                True, False)
    if _MAIN_DISPATCH.search(source):
        return "", False, False
    # Authored text with no variable in it, so `needs_redaction` is False for
    # the reason `ModuleFailure` records: in the scenario this module exists for
    # the redactor is part of what went missing, and a message that never
    # carried a path must not collapse to the withheld marker with the ones that
    # did.
    return ("IncompleteModule: imported but carries no "
            "`if __name__ == \"__main__\":` dispatch — the skills run this "
            "file as a command, and without it it would exit 0 having done "
            "nothing", False, False)


def _probe_environment_fault(what, exc):
    """A failure of the PROBE's own environment — never a verdict about the
    engine, and never a block.

    Both probes below need a throwaway tree to answer their question in, and
    every step of building one can fail for reasons that say nothing about this
    clone: a full `/tmp`, a `$TMPDIR` inside a sandbox this process may not
    write to, a mount gone read-only. Those failures used to arrive through the
    same `except Exception` as a guard that RAISED, so a healthy engine in the
    correct virtualenv reported `engine_missing`, blocked every taxpayer-facing
    skill, and answered with `import_failure_remedy` — a re-clone and a
    reinstall, for a disk with no space on it, with the OS error redacted away.
    Reported against PR #82.

    So it is carried separately and it WARNS. What is lost is certification of
    the guard, not the guard itself: `importlib.import_module` succeeded a few
    lines above, which means the module-level refusal already ran in this
    process. Blocking every skill on a state the engine is not in is the
    cry-wolf shape AGENTS.md §4 names, and the gate an agent reasons past next
    is the tie-out.
    """
    return "", False, False, f"{what}: {type(exc).__name__}: {exc}"


def _guard_raised(exc):
    """The other half of the split above: the guard itself blew up.

    That IS a verdict about the engine — a stubbed or half-edited initialiser
    reaches here — so it blocks, and it is redacted because the exception text
    can carry the probe's temp path.
    """
    return (f"{type(exc).__name__}: the host-interpreter guard raised on the "
            f"known-answer probe: {exc}", True, False, "")


@contextlib.contextmanager
def _as_a_dash_c_invocation():
    """Make this process look like `python -c …` to the guard, and put it back.

    The wiring probe EXECUTES a copy of the initialiser, and that copy reads
    its inputs off `sys` rather than taking them as arguments. Preflight is
    exempt from the guard, so left alone the copy would take the exemption and
    the probe would read clean against any initialiser at all — vacuous, green,
    and measuring nothing. `_guard_error`'s probe escapes the same trap by
    passing the three inputs explicitly; this one cannot, so it changes them.

    THREE inputs, because the exemption reads three: `__main__.__file__` first,
    then `sys.argv[0]`, then `sys.orig_argv`. A `-c` invocation is the state
    where all three agree that the program is not preflight, and it is the one
    CPython itself produces — so the probe measures the guard against a real
    command line rather than an impossible hybrid.

    EVERY assignment happens before the caller's first filesystem call. The
    previous version set `sys.orig_argv` after building its temp tree, so an
    OSError during that build ran a `finally` holding `del sys.orig_argv`
    against a `sys` that had never had one — an AttributeError replacing the
    handled exception, and on a pre-3.10 interpreter that is exactly the
    MIN_PYTHON report preflight exists to deliver. Reported against PR #82.
    """
    absent = object()
    main = sys.modules.get("__main__")
    saved_argv = sys.argv
    saved_orig = getattr(sys, "orig_argv", absent)
    saved_file = absent if main is None else getattr(main, "__file__", absent)
    sys.argv = ["-c", ""]
    sys.orig_argv = ["python", "-c", ""]
    if main is not None and saved_file is not absent:
        del main.__file__
    try:
        yield
    finally:
        sys.argv = saved_argv
        if saved_orig is absent:
            # Below MIN_PYTHON there was no attribute to begin with, and
            # leaving a synthetic one behind would silently switch the guard's
            # own fallback branch off for the rest of this process.
            del sys.orig_argv
        else:
            sys.orig_argv = saved_orig
        if main is not None and saved_file is not absent:
            main.__file__ = saved_file


def _guard_error():
    """Certify the host-interpreter guard, which no module roster can see.

    `agents/lib/__init__.py` is the one engine file whose ABSENCE is invisible
    to every other check here. Python treats `agents/lib/` with no initialiser
    as a NAMESPACE package, so every sibling still imports, every roster entry
    still passes, and the guard that refuses a host interpreter is simply gone
    — on exactly the partial copy this module exists to detect. An emptied or
    zero-truncated initialiser is the same failure at a coarser cut, and both
    are the shape `ENGINE_MODULES` is deliberately blind to, because the roster
    is keyed on file stems and this file's stem is not its module name.

    IMPORTABLE IS NOT CORRECT here either, for the reason `_probe` exists: a
    guard reduced to `return ""` — half-applied merge, a debugging edit left in
    — passes an existence check and certifies nothing. So it is answered on a
    known input in BOTH directions. A root with no `.venv` must produce no
    opinion, because a guard that refuses one would block CI and every clone
    before `tax-setup` Step 6; the same root WITH one, judged from an
    interpreter that is not it, must produce a refusal.

    The probe passes a `-c` invocation on purpose, and passes all THREE of the
    inputs the exemption reads. The guard exempts preflight, so leaving any of
    them to default would take the exemption whenever preflight is what is
    running — which it always is here — and the probe would read "" in both
    directions: present, green, and measuring nothing. `main_file` is the one
    that made this concrete: with only the argv overridden, the probe read the
    real `__main__.__file__`, saw preflight, and certified a stub.

    Returns `(error, needs_redaction, file_missing, environment_fault)`. The
    fourth is the non-blocking channel `_probe_environment_fault` argues for;
    when it is set the first is empty, because a probe that could not run has
    no verdict to give about the engine.
    """
    initialiser = _HERE / "__init__.py"
    try:
        if not initialiser.stat().st_size:
            return ("IncompleteModule: the package initialiser is present and "
                    "EMPTY, so nothing refuses a host interpreter and every "
                    "sibling still imports", False, False, "")
    except FileNotFoundError:
        return ("ModuleNotFoundError: the package initialiser is missing, so "
                "`agents.lib` imports as a NAMESPACE package — every module "
                "below still loads and nothing refuses a host interpreter",
                False, True, "")
    except OSError as e:
        # Unverifiable is not verified, and the bias runs one way: what is left
        # uncertified here is the refusal that stands between a host
        # interpreter and a return. This one is NOT routed to
        # `_probe_environment_fault`: it is a failure to read the ENGINE's own
        # file, which is a fact about this clone rather than about a temp
        # directory. The OS wrote this and it can name a path.
        return (f"{type(e).__name__}: the package initialiser could not be "
                f"inspected: {e}", True, False, "")
    try:
        # `importlib` directly, not `_load`: that helper stats `<stem>.py`
        # beside this file to tell a missing module from a broken one, and a
        # package has no such leaf — it answered "no lib.py beside
        # preflight.py" for a package sitting right here. `SystemExit` is
        # caught alongside `Exception` for the reason `_load` catches it: the
        # thing being imported is a module that may exit on import, and this
        # check has to survive that to report it.
        module = importlib.import_module(_GUARD_MODULE)
    except (Exception, SystemExit) as e:
        return (f"{type(e).__name__}: the package could not be imported: {e}",
                True, False, "")
    guard = getattr(module, _GUARD_SYMBOL, None)
    if guard is None:
        return (f"AttributeError: imported but exposes no `{_GUARD_SYMBOL}` — "
                f"the initialiser is present and stubbed", False, False, "")
    as_dash_c = dict(prefix="/probe", base_prefix="/probe", main_file=None,
                     argv=["-c", ""], orig_argv=["python", "-c", ""])
    # `mkdtemp` plus an unconditional `rmtree(ignore_errors=True)`, not
    # `TemporaryDirectory`: that context manager raises from its own cleanup,
    # and a cleanup failure arriving as the probe returns would overwrite a
    # real engine verdict with an environment one — the misclassification this
    # whole split exists to end, reintroduced at the exit.
    try:
        root = tempfile.mkdtemp()
    except OSError as e:
        return _probe_environment_fault(
            "the known-answer probe could not create a temporary workspace", e)
    try:
        try:
            quiet = guard(root=root, **as_dash_c)
        except Exception as e:
            return _guard_raised(e)
        try:
            os.mkdir(os.path.join(root, ".venv"))
        except OSError as e:
            return _probe_environment_fault(
                "the known-answer probe could not create the `.venv` its "
                "second half is measured against", e)
        try:
            loud = guard(root=root, **as_dash_c)
        except Exception as e:
            return _guard_raised(e)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    if quiet:
        return ("IncompleteModule: the host-interpreter guard refuses a "
                "workspace that has no `.venv` — that state is CI and every "
                "clone before `tax-setup` Step 6, and a gate that fires on it "
                "teaches its reader to reason past it", False, False, "")
    if not loud:
        return ("IncompleteModule: the host-interpreter guard passed an "
                "interpreter that is not the workspace venv on the "
                "known-answer probe — present, and certifying nothing", False,
                False, "")
    return _wiring_error(initialiser)


def _wiring_error(initialiser):
    """The module-level CALL, which importing a correct file cannot reveal.

    `_guard_error` answers the exported function on a known input in both
    directions, and a cut one line further down passes every one of those
    checks: truncate the initialiser after its last `return` and
    `host_interpreter_defect` is still there, still right, and never called.
    The package then imports cleanly on any interpreter, every engine module
    loads behind it, and the refusal is simply gone — "present and certifying
    nothing" again, one level out from the shape `_probe` exists for, and
    reported against PR #82.

    So it is answered the same way, by EXECUTING the file in a state where a
    wired guard must refuse: a copy of the real initialiser in a temp root that
    HAS a `.venv` this interpreter is not. The copy reads its inputs off `sys`
    rather than taking them as arguments, so `_as_a_dash_c_invocation` supplies
    that state and restores it — preflight is exempt, and a copy left to read
    this process's own inputs would take the exemption and measure nothing.

    A source scan for the `if ... : sys.exit(...)` line was the cheaper option
    and is what `_cli_dispatch_error` does for `__main__`. It is not enough
    here: that check answers "is the text present", and the whole argument of
    this module is that present is not wired.
    """
    with _as_a_dash_c_invocation():
        try:
            root = tempfile.mkdtemp()
        except OSError as e:
            return _probe_environment_fault(
                "the import-time probe could not create a temporary "
                "workspace", e)
        try:
            try:
                package = Path(root) / "agents" / "lib"
                package.mkdir(parents=True)
                copy = package / "__init__.py"
                copy.write_bytes(initialiser.read_bytes())
                os.mkdir(os.path.join(root, ".venv"))
            except OSError as e:
                return _probe_environment_fault(
                    "the import-time probe could not build the throwaway "
                    "package it executes", e)
            spec = importlib.util.spec_from_file_location(
                "_engine_guard_wiring_probe", copy)
            module = importlib.util.module_from_spec(spec)
            # Deliberately NOT registered in `sys.modules`: this is a second
            # execution of a module that is already imported under its real
            # name, and registering it would leave the copy shadowing the real
            # package for everything preflight imports afterwards.
            #
            # The refusal writes to stderr before it exits. That stream is this
            # gate's report, so it is swallowed here — the verdict below is
            # what preflight has to say about it, not the probe's own copy of a
            # message the reader is not being blocked by.
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    spec.loader.exec_module(module)
            except SystemExit as refusal:
                if refusal.code == _GUARD_STATUS:
                    return "", False, False, ""
                return (f"IncompleteModule: the host-interpreter guard refuses "
                        f"at import with status {refusal.code!r} rather than "
                        f"{_GUARD_STATUS} — the skills branch on that status, "
                        f"and this one does not read as blocked",
                        False, False, "")
            except Exception as e:
                # The temp root reaches this message through several of the
                # exceptions it can carry, so it is redacted for the reason
                # every other path here redacts: a workspace path may carry a
                # taxpayer's name.
                return (f"{type(e).__name__}: the package initialiser raised "
                        f"on the import-time known-answer probe: {e}",
                        True, False, "")
        finally:
            shutil.rmtree(root, ignore_errors=True)
    return ("IncompleteModule: the host-interpreter guard is defined but never "
            "CALLED at import — the package imported without refusing an "
            "interpreter that is not the workspace venv, so the refusal every "
            "engine import is supposed to pass through is not there", False,
            False, "")


def _probe(math_module) -> str:
    """Known-answer check. Returns "" when the engine computes correctly."""
    try:
        total, _ = math_module.compute_bracket_tax(_PROBE_INCOME, _PROBE_BRACKETS)
    except (Exception, SystemExit) as e:
        return f"compute_bracket_tax raised {type(e).__name__}: {e}"
    try:
        got = round(float(total), 2)
    except (TypeError, ValueError):
        return f"compute_bracket_tax returned a non-numeric {total!r}"
    # BEFORE the tolerance comparison, and this is the same trap AGENTS.md §8
    # states for every parser in `agents/lib/`: NaN is a float, survives
    # `float()` and `round()`, and then defeats the check below by making it
    # False — every comparison with NaN is False, `>` included. A damaged math
    # module returning NaN was therefore certified as "present and computing
    # correctly", which is the one verdict this probe exists to withhold.
    # `inf` already failed the comparison, but it reads better here too: the
    # message names the shape rather than printing `inf` as a wrong total.
    if not math.isfinite(got):
        return (f"compute_bracket_tax returned a non-finite {got!r} on the "
                f"known-answer probe — the engine is present but not computing "
                f"correctly")
    if abs(got - _PROBE_EXPECTED) > 0.005:
        return (f"compute_bracket_tax returned {got} on the known-answer probe, "
                f"expected {_PROBE_EXPECTED} — the engine is present but not "
                f"computing correctly")
    return ""


def _venv_error(workspace=None):
    """Refuse to certify an interpreter that is not the one the engine will use.

    Returns `(message, defect)`. The defect rides along rather than being
    re-derived by a second `_venv_defect` call in `check()`: that call repeated
    two stats and a full read of `activate`, and the two could DISAGREE — this
    function's `except OSError` branch never consults the defect at all, while
    the caller's did, so a `.venv` whose path merely could not be compared, yet
    which happens to lack `bin/activate`, rendered as "delete and rebuild it"
    under a message that only said the comparison failed.

    Every engine command in the skills goes through `agents/lib/run.sh`, which
    activates the workspace venv — per command, because some agents start a
    fresh shell per command and an earlier `source` does not persist (§7). When a workspace HAS a `.venv` and this
    check is not running inside it, that chaining did not happen — so the
    verdict below would describe one interpreter while
    `python3 -m agents.lib.projection` runs on another. On a stock macOS the
    other one is /usr/bin/python3, a 3.9 this toolkit does not support. A
    verdict about the wrong interpreter is not a verdict, so it blocks.

    `sys.prefix` is the test rather than `sys.executable`: a venv's `python3` is
    a symlink chain out to the base interpreter, so resolving the executable
    lands OUTSIDE the venv and would block every correct run.
    """
    venv = _workspace_venv(workspace)
    # The ROOT before the venv. A `--workspace` naming a directory that does not
    # exist is a fact about the CALLER'S ARGUMENT, not evidence about a
    # virtualenv — but `.venv` under a nonexistent root simply fails to stat,
    # which the ENOENT branch below reads as "no workspace venv here" and
    # answers clean. The interpreter comparison then never runs at all, and a
    # typo'd or stale root certifies whatever host Python the check happens to
    # be on: the same fail-open the `os.lstat` rewrite closed for an unreadable
    # `.venv`, arriving through the argument instead of through the filesystem.
    #
    # Only when a root was SUPPLIED. The default is the current directory, which
    # exists by construction.
    if workspace is not None:
        try:
            # `os.stat`, which FOLLOWS the link. `os.lstat` succeeds on a
            # dangling symlink — a workspace left behind after a move, or one
            # pointing at an unmounted volume — so the root read as present, the
            # `.venv` under it failed to stat, and the ENOENT branch below
            # answered "no workspace venv here". Exit 0, certifying an
            # interpreter nothing compared: the same fail-open this check was
            # added to close, one primitive away. `_venv_error` pairs `lstat`
            # with `_is_dir_through_link` for the venv itself and catches the
            # dangling case correctly there.
            if not stat.S_ISDIR(os.stat(workspace).st_mode):
                # A regular file, a socket, a device. `workspace_remedy` already
                # says "is not a directory that exists" — the code only ever
                # tested the second half, so a `--workspace` pointing at a file
                # fell through to _UNCOMPARABLE and was answered with "a
                # permission on a parent directory, a mount that was briefly
                # unavailable, or a lock held by another process. Do not delete
                # it on this evidence" plus a `cd` that cannot succeed. None of
                # that is true, and none of it is about the argument that is
                # actually wrong.
                raise FileNotFoundError(workspace)
        except FileNotFoundError:
            return (f"`{workspace}` does not exist, so this check has no "
                    f"workspace to speak for"), _WORKSPACE_MISSING
        except OSError as e:
            return (f"`{workspace}` could not be inspected: {e}"), _UNCOMPARABLE
    # `os.lstat`, not `Path.is_dir()`. `is_dir()` swallows every OSError and
    # answers False, so an unstattable `.venv` — an unreadable parent, a stale
    # mount — read as "there is no venv here" and this function returned clean
    # WITHOUT ever comparing interpreters. A fail-OPEN, in the one check whose
    # entire premise is failing closed: the gate then certified a host
    # interpreter it never looked at. "Cannot tell" is the uncomparable state,
    # which already exists for the compare below.
    try:
        st = os.lstat(venv)
    except FileNotFoundError:
        # No virtualenv here at all. NOT an error — `tax-setup` Step 6 is what
        # creates one, and every skill opens with this gate, so blocking would
        # deadlock the one skill that fixes it (the same trap `rebuild_remedy`
        # already warns about). But not SILENCE either, which is what this
        # returned: the engine then runs on whatever host interpreter the caller
        # had, with whatever versions happen to be installed there, and this
        # verdict said "OK … Pinned dependencies importable" over it. The lock
        # is hash-pinned for a reason — pdfplumber 0.11.10 shipped a different
        # parsing engine under the same API — so unpinned versions are a
        # wrong-number risk, not merely an untidy one.
        #
        # Warned rather than blocked, and the marker rides the DEFECT slot so
        # `venv_error` stays empty and `blocking` is untouched.
        return "", _NO_VENV
    except OSError as e:
        detail = f"could not be inspected: {e}"
        return f"`{venv}` {detail}", _UNCOMPARABLE
    try:
        is_dir = stat.S_ISDIR(st.st_mode) or _is_dir_through_link(venv)
    except FileNotFoundError:
        # DANGLING, which is definitive: the link is there and its target is
        # not. Folding it into the uncomparable state answered a virtualenv
        # that is certainly gone with "do not delete it on this evidence",
        # when delete-and-rebuild is the only fix. `_relocated` separates
        # ENOENT from the other OSErrors for exactly this reason.
        return (f"`{venv}` is a symlink whose target no longer exists",
                "is a symlink whose target no longer exists")
    except OSError as e:
        detail = f"could not be inspected through its link: {e}"
        return f"`{venv}` {detail}", _UNCOMPARABLE
    if not is_dir:
        # PRESENT and not a directory is a DEFECT, not an absence. Returning
        # clean here was the same fail-open the lstat branch above closes,
        # reached by the other door: a `.venv` left as a regular file — a
        # flattened archive, a stray `touch`, a `cp` without `-r` — read as "no
        # workspace venv", so the interpreter comparison never ran and the gate
        # certified whatever host Python it happened to be on. `run.sh` finds no
        # activate script beside it either and execs that same host interpreter,
        # so the whole toolkit runs there behind a green verdict.
        #
        # `rm -rf .venv` first is exactly right for this one, which is what
        # makes it a defect rather than the uncomparable state: the path has to
        # go before `python3 -m venv .venv` can succeed, and a bare
        # create-without-delete remedy would fail on its first line.
        return (f"`{venv}` exists but is not a directory",
                "exists but is not a directory")
    try:
        in_venv = sys.prefix != sys.base_prefix
        # `os.path.samefile`, never `==` on resolved paths — the same rule
        # `_relocated` documents, and it belongs here for the same reason.
        # `resolve()` follows symlinks but does NOT canonicalise case, and macOS
        # and Windows are case-insensitive by default: activating through
        # `/Users/Foo/Proj/.venv` while the cwd reports `/Users/foo/proj` gives
        # two spellings of one directory, `==` says False, and every skill blocks
        # on a correctly activated run. That is the false refusal
        # `test_a_symlinked_path_to_the_same_venv_is_the_same_venv` exists to
        # prevent one function lower.
        same = in_venv and os.path.samefile(sys.prefix, venv)
    except OSError as e:
        # Report it, and report it as its OWN condition — neither "merely
        # unactivated" nor a defect. Empty made `venv_broken` False and answered
        # a failing stat with "chain the activation", which the reader chains
        # and is refused in the same words. Marking it a DEFECT was the
        # overcorrection: that answer is `rm -rf .venv`, and the stat may have
        # failed for a reason that says nothing about the venv — an EACCES on
        # `sys.prefix`, a momentarily unavailable mount, an antivirus lock. A
        # good virtualenv would be deleted on the strength of a stat that merely
        # failed, which is the jump `_relocated` refuses twenty lines below.
        detail = f"could not be compared against this interpreter: {e}"
        return f"`{venv}` {detail}", _UNCOMPARABLE
    if same:
        return "", ""
    try:
        broken = _venv_defect(venv)
    except OSError as e:
        detail = f"could not be inspected: {e}"
        return f"`{venv}` {detail}", _UNCOMPARABLE
    if broken:
        return f"`{venv}` {broken}", broken
    where = f"the virtualenv at `{sys.prefix}`" if in_venv else "no virtualenv"
    return (f"`{venv}` exists but this check is running under {where}, so it "
            f"cannot speak for the interpreter that will run the engine"), ""


def _is_dir_through_link(path) -> bool:
    """`.venv` may legitimately be a symlink to a directory; lstat sees the link.

    RAISES on a stat failure rather than answering False, and the caller turns
    that into the uncomparable state. Swallowing it was the fail-open again, one
    function over from where it was closed: a `.venv` symlink whose target
    became unreadable — an unmounted volume, an EACCES on the target's parent —
    read as "no venv here", so the interpreter comparison never happened and the
    gate exited 0 certifying an interpreter it had not looked at. The docstring
    that used to sit here claimed a stat failure could not be mistaken for "not
    a directory"; only an LSTAT failure had been answered above.
    """
    return stat.S_ISDIR(os.stat(path).st_mode)


def _workspace_venv(workspace=None) -> Path:
    """The virtualenv this check speaks for: `<engine root>/.venv` by default.

    NOT the current directory, which is what this resolved against until the
    launcher learned to anchor itself. The two disagreeing is a refusal with no
    exit: `run.sh` activates the ENGINE tree's venv, this compared it against
    the CALLER's, and from any directory holding a different `.venv` the verdict
    blocked with "run it through the launcher" — the command that had just
    produced it, byte for byte, with `rm -rf .venv` as the only escape offered
    and aimed at a virtualenv that was never the problem. Reproduced.

    Anchoring both to the same root makes them agree by construction rather than
    by the caller standing in the right place. An explicit `workspace` still
    wins, for the sibling gates' `--workspace .` form and for tests.
    """
    return (Path(workspace) if workspace else _ENGINE_ROOT) / ".venv"


def _remedy_root(report) -> str:
    """The directory the remedy's `.venv` commands have to run in.

    The venv's own parent, NOT `report.workspace`: with no `--workspace` the
    report carries none, while the virtualenv sits at the ENGINE root — so a
    reader standing in `summaries/` was handed `rm -rf .venv` relative to
    wherever they were. `_workspace_venv` is the one place that decides where
    the venv lives, so the remedies ask it rather than re-deriving.
    """
    return str(_workspace_venv(report.workspace or None).parent)


def _venv_defect(venv) -> str:
    """What is WRONG with a `.venv` we are not running in, or "".

    Diagnosis, because the causes need opposite advice. Told to chain the
    activation, someone whose `.venv` cannot work chains it, is refused again in
    the same words, and lands back here — and cannot escape by running
    `tax-setup`, which stops at this same gate before reaching its own
    environment step.

    Each answer carries its own consequence clause rather than sharing one,
    because they differ in a way the reader can see: a missing activate script
    makes `source` FAIL, while a relocated one makes it SUCCEED and do nothing.
    Telling the second reader that "activating it cannot have succeeded" states
    the opposite of what their terminal just showed them, and advice contradicted
    on its first line does not get followed to its second.

    `.exists()` follows symlinks, which is the point: one defect is a live
    `bin/python3` symlink left dangling when the base interpreter it was built
    against moved or was upgraded away.
    """
    # An OSError from either probe PROPAGATES to _venv_error, which turns it
    # into the uncomparable state. Describing it here as a defect string would
    # route it to `rm -rf .venv` — absent and unreadable earn opposite answers,
    # and this function's callers can only tell them apart if it does not
    # flatten them into one return type.
    found = _activate_path(venv)
    if found is None:
        return "has no activate script, so activating it cannot have succeeded"
    subdir, activate = found
    if not any(_exists_or_raises(venv / subdir / n)
               for n in ("python3", "python", "python.exe")):
        return ("has an activate script but no interpreter behind it, so "
                "activating it cannot have succeeded")
    return _relocated(venv, activate)


def _exists_or_raises(path) -> bool:
    """True/False for present/absent; an unreadable path RAISES rather than
    reading as absent, because absence here earns a `rm -rf`."""
    try:
        os.stat(path)          # stat, not lstat: a live symlink counts
        return True
    except (FileNotFoundError, NotADirectoryError):
        return False


# `VIRTUAL_ENV=<path>` and `export VIRTUAL_ENV=<path>`, quoted or bare — the
# forms CPython's `activate` has written across the versions this runs on (3.9
# quotes it, 3.14 exports it bare). The `unset VIRTUAL_ENV` and the standalone
# `export VIRTUAL_ENV` that also appear in that file carry no `=` and so cannot
# match; requiring the assignment is what excludes them.
#
# The trailing class holds no `\r`, and it does not need one. TWO things
# downstream already stop a CRLF `activate` from poisoning the capture:
# `_activate_target` reads with `Path.read_text()`, whose universal-newline
# translation turns CRLF into `\n` before the pattern sees it, and the `.strip()`
# on the captured value would remove a carriage return that got through anyway.
# Either alone suffices, so neither looks load-bearing in isolation — which is
# why this is written down. Lose BOTH (a `read_bytes().decode()` here, a tidied
# strip there) and `/w/.venv\r` survives the quote strip and the `$`/backtick
# filter, fails `samefile` and `exists()`, and reads as a venv that has MOVED —
# a verdict whose remedy is `rm -rf .venv`, aimed at a working virtualenv whose
# only sin is its line endings.
_ACTIVATE_HOME = re.compile(r"^[ \t]*(?:export[ \t]+)?VIRTUAL_ENV=(.+?)[ \t]*$",
                            re.M)


def _relocated(venv, activate) -> str:
    """Diagnose a `.venv` that has been MOVED away from where it was built.

    This is the defect a structural check cannot see. Rename the workspace and
    every file survives — `bin/activate` is a real file, `bin/python3` is a
    symlink into a base interpreter that never moved — so the venv looks intact
    while `activate` still exports the OLD absolute path and prepends a `bin`
    that no longer exists. `source` therefore returns 0, prints nothing, leaves
    `python3` resolving to the host interpreter, and this check refuses again.
    Called out separately because the generic remedy for that refusal is the
    activation, which is the very thing that just silently did nothing.

    `activate` is read rather than `pyvenv.cfg` because `activate` is what the
    remedy actually runs; `pyvenv.cfg`'s paths point at the base interpreter,
    which relocating the workspace does not touch.
    """
    built = _activate_target(activate)
    if built is None:
        return ""
    try:
        if os.path.samefile(built, venv):
            return ""
    except OSError:
        # Raised when either path is gone, which after a move is the usual
        # case and is itself the evidence. Anything else — unreadable parent,
        # a path we cannot stat — is not evidence, so only a baked-in path
        # that demonstrably does not exist falls through to the accusation.
        #
        # `os.lstat` and an explicit ENOENT, never `Path.exists()`: CPython's
        # `exists()` swallows EACCES/EPERM through `_ignore_error` and answers
        # False, which reads as "demonstrably does not exist" and is the exact
        # conflation the sentence above rules out. A workspace under a parent
        # that later became unreadable would be accused of having moved, and
        # that accusation's remedy is `rm -rf .venv` on an intact virtualenv.
        try:
            os.lstat(built)
            return ""              # it is there; not a relocation
        except FileNotFoundError:
            pass                   # confirmed absent — the accusation stands
        except OSError:
            return ""              # could not tell, so do not accuse
    return (f"was built at `{built}` and has been moved since, so its activate "
            f"script still puts that path on PATH — sourcing it reports no "
            f"error, changes no interpreter, and lands right back here")


def _activate_target(activate):
    """The path `activate` will put on PATH, or None when it computes one.

    None means "no literal to contradict", never "fine": an assignment built
    from a variable or a command substitution (3.14 writes a `$(cygpath …)`
    form for Cygwin) is skipped, and an activate script that DERIVES its own
    location is by construction not broken by being moved.
    """
    try:
        text = activate.read_text(errors="replace")
    except OSError:
        return None
    for raw in _ACTIVATE_HOME.findall(text):
        value = raw.strip()
        if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not value or "$" in value or "`" in value:
            continue
        return value
    return None


# The four third-party packages `requirements.txt` pins. Every engine import of
# them is lazy (inside a function) or try/except-guarded, so all twenty-one
# modules import and the probe passes with NONE of them installed — and the
# verdict then says "present and computing correctly" about an engine that
# cannot open a PDF. `tax-setup` has its own probe, but `tax-parse` and
# `tax-generate` are reachable without ever running it.
RUNTIME_DEPENDENCIES = (
    # (distribution, import name, what is lost, the skills that lose it). The
    # skills are NAMED rather than parsed back out of the role: deriving them by
    # splitting it on "(" printed `defusedxml`'s prose — "the XXE guard openpyxl
    # parses behind" — as if it were a skill. That sentence used to sit above
    # this one still describing a three-field tuple, so the file carried two
    # contradictory shapes and the stale one was first.
    # The first two are SEPARATE fields because they are separate identifiers: the
    # test pins field 0 against `requirements.in`, which names DISTRIBUTIONS,
    # while the probe imports — and for pyyaml/yaml, Pillow/PIL,
    # python-dateutil/dateutil they differ. Collapsing them would make the next
    # such dependency report "not installed" forever on a machine where it is
    # correctly installed, with a `pip install` remedy that changes nothing.
    # EVERY skill that reaches the package, not just the obvious one. The
    # verdict says in so many words that "the rest are unaffected", so a short
    # list is not a smaller claim — it is a false one, and this repository's own
    # rule is that a lying caveat is worse than a missing one. `/tax-verify`
    # runs `parse_utils.py extract` on the tax software's PDF page by page and
    # `/tax-parse-crypto` runs `extract`/`tables` on exchange documents; both
    # were told they were unaffected by a missing `pdfplumber` and both die on
    # the first call. `test_the_affected_skills_include_every_caller` derives
    # the list from the skills rather than trusting this tuple.
    ("pdfplumber", "pdfplumber", "PDF parsing",
     ("/tax-parse", "/tax-parse-crypto", "/tax-verify")),
    ("openpyxl", "openpyxl", "Excel parsing", ("/tax-parse", "/tax-setup")),
    ("reportlab", "reportlab", "PDF generation",
     ("/tax-generate", "/tax-setup")),
    ("defusedxml", "defusedxml", "the XXE guard openpyxl parses behind",
     ("/tax-parse", "/tax-setup")),
)


def _missing_dependencies():
    """Names `requirements.txt` pins that are not importable, with their role."""
    missing = []
    for dist, import_name, role, skills in RUNTIME_DEPENDENCIES:
        # IMPORT it, do not merely locate it. `find_spec` answers "is there a
        # package by this name", which a half-installed or ABI-broken
        # `pdfplumber` satisfies while its `__init__` raises on a missing
        # `pdfminer.six` — and the verdict then claimed "Pinned dependencies
        # importable" and `/tax-parse` died on the first PDF, which is the one
        # outcome dependency_remedy's own text says this check prevents.
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()), \
                    warnings.catch_warnings():
                warnings.simplefilter("ignore")
                importlib.import_module(import_name)
        except (Exception, SystemExit):
            missing.append((dist, role, skills))
    return tuple(missing)


# `name==version`, at column zero, with the extras and the environment marker
# `uv pip compile --universal` writes around it. The `--hash=` continuation
# lines are indented, so `^` excludes them without a second pattern.
#
# The marker is CAPTURED, not merely tolerated. It was discarded while the only
# question asked of the lock was "does the installed version match this pin",
# which a marker cannot change. The question below is "is this pin installed AT
# ALL", and there the marker is the whole answer: a `--universal` lock names
# packages that this interpreter is not supposed to have.
_LOCK_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?=="
                       r"([^\s;\\]+)[ \t]*(?:;[ \t]*([^\\\n]*))?", re.MULTILINE)

# One term of a PEP 508 marker: a bare variable, a comparison operator, and a
# quoted literal. Anchored at both ends, so anything richer fails to match and
# is reported as undecidable rather than guessed at.
_MARKER_TERM = re.compile(r"^([a-z_]+)\s*(==|!=|<=|>=|<|>)\s*(['\"])([^'\"]*)\3$")

# The two marker variables that hold a VERSION, where `'3.9' < '3.12'` is false
# as strings and true as releases. Every other key this evaluator knows is an
# opaque string, compared only for equality.
_MARKER_VERSION_KEYS = ("python_version", "python_full_version")


def _marker_environment() -> dict:
    """The PEP 508 keys this module will answer, and deliberately no others.

    `packaging.markers` is the real evaluator and is not available: it is not in
    `requirements.txt`, so a workspace built from the lock alone — the one this
    check exists to certify — cannot import it. Depending on it would mean
    depending on a package outside the very pin set under audit.

    Anything absent from this dict makes its marker undecidable, which is
    reported. That is the whole design: the failure mode being closed here is a
    check that reports "nothing missing" for pins it never examined, so an
    evaluator that quietly guessed at an unfamiliar key would reintroduce it one
    layer down.
    """
    return {
        "os_name": os.name,
        "sys_platform": sys.platform,
        "platform_machine": platform.machine(),
        "platform_system": platform.system(),
        "platform_python_implementation": platform.python_implementation(),
        "implementation_name": sys.implementation.name,
        "python_version": "%d.%d" % sys.version_info[:2],
        "python_full_version": platform.python_version(),
    }


def _release(text: str):
    """`"3.12.4"` -> `(3, 12, 4)`, or None when it is not a plain release.

    None rather than a best effort. A pre-release (`3.14.0rc1`) has an ordering
    PEP 440 defines and a dotted-integer split does not, and the two disagree
    exactly at the boundary a `python_full_version <` marker is placed on — so
    the marker is reported as undecidable instead of decided wrongly.
    """
    parts = text.split(".")
    if not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def _marker_term(term: str, env: dict):
    """One comparison: True, False, or None for "this evaluator cannot say"."""
    parsed = _MARKER_TERM.match(term.strip())
    if parsed is None:
        return None
    name, op, _quote, literal = parsed.groups()
    if name not in env:
        return None
    left, right = env[name], literal
    if name in _MARKER_VERSION_KEYS:
        left, right = _release(left), _release(right)
        if left is None or right is None:
            return None
    elif op not in ("==", "!="):
        # An ordering comparison on an opaque string — `sys_platform > 'linux'`
        # — means whatever the sort order says, which is not a fact about this
        # machine. Undecidable, not False.
        return None
    return {"==": left == right, "!=": left != right,
            "<": left < right, "<=": left <= right,
            ">": left > right, ">=": left >= right}[op]


def _marker_applies(marker: str):
    """Whether a lock line's environment marker selects THIS interpreter.

    True, False, or None for "not decidable here" — three answers, because the
    caller does three different things with them and collapsing the third into
    either of the others is a fail-open or a permanent false alarm. An
    undecidable marker on an installed package changes nothing (it is installed,
    so it applied); on an ABSENT one it is the difference between "the lock
    requires this and it is gone" and "this interpreter is not supposed to have
    it", and only the reader can tell which.

    `and`-joined comparisons only. `or` and parentheses return None rather than
    a partial reading — `test_every_marker_in_the_shipped_lock_is_decidable`
    turns CI red the day the regenerated lock uses one, which is when a human is
    already looking at the lock diff.
    """
    if not marker:
        return True
    if "(" in marker or ")" in marker or re.search(r"\bor\b", marker):
        return None
    env = _marker_environment()
    verdicts = [_marker_term(term, env)
                for term in re.split(r"\s+and\s+", marker.strip())]
    # A definite False anywhere settles an `and` chain whatever the rest says,
    # so it outranks an undecidable sibling. Ordering these the other way would
    # report `cffi ; implementation_name != 'PyPy' and <something new>` as
    # undecidable on PyPy, where it is plainly excluded.
    if False in verdicts:
        return False
    return None if None in verdicts else True


def _pins_here(lines):
    """Split one name's pin lines by whether they describe THIS interpreter.

    Returns `(applicable, undecided)` — the versions whose marker definitely
    selects this machine, and the versions whose marker this evaluator could not
    read. A version whose marker definitely EXCLUDES this machine is in neither:
    the lock does not govern that package here, so it is neither required nor a
    version to compare against.

    The split is per VERSION and not per name, because that is the distinction a
    `--universal` lock turns on. Two lines under opposite markers
    (`python_full_version < '3.12'` and `>=`) carry two different versions, and
    exactly one of them is the one this interpreter is meant to have; the other
    is a version the suite never ran against on this machine. Pooling both into
    one set and accepting whichever matched certified the wrong-interpreter
    install as locked.
    """
    applicable = {version for version, verdict in lines if verdict is True}
    undecided = {version for version, verdict in lines if verdict is None}
    return applicable, undecided


def _canonical_dist(name: str) -> str:
    """PEP 503 normalisation, applied to BOTH sides of every comparison.

    The lock says `pdfminer-six`; `importlib.metadata` reports `pdfminer.six`,
    the name on the distribution's own metadata. Compared raw, the one
    transitive package whose republication actually changes what a document
    parses to is the one that never matches a pin and is silently skipped.
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def _drifted_dependencies():
    """Installed versions that are not the ones `requirements.txt` pins.

    The four import probes above answer "can it be imported", and the verdict
    then said "Pinned dependencies importable" — true, and not what the lock is
    for. AGENTS.md §7: the lock pins the whole TRANSITIVE tree with hashes
    because pdfplumber 0.11.10 shipped no pdfplumber code at all, only a
    `pdfminer.six` bump, and `pdfminer.six` is the engine document parsing
    actually runs on. A venv assembled by `pip install pdfplumber openpyxl
    reportlab defusedxml` imports all four and parses documents on a resolution
    nobody tested.

    Returns `(drift, lock_error)`. Drift is a tuple of
    `(package, installed, pinned)`, where `installed` is None for a pin the lock
    requires on this interpreter and nothing provides. `lock_error` is set when
    the lock could not be read at all — which is not the same as "nothing
    drifted" and must not render as it: `agents/lib/` copied into a tree with no
    `requirements.txt` is precisely the shape this whole module exists for.

    Which pin to compare against is a QUESTION, not a lookup, and `_pins_here`
    answers it. A `--universal` lock names one package twice under opposite
    markers at two different versions; only one of them is the version this
    interpreter is meant to have, and the other is one the suite has never run
    against here. Comparing against the pooled set — accepting the installed
    version if it matched either — certified the wrong-interpreter install as
    locked, which is the same fail-open as not comparing at all.

    ABSENT is checked as well as MISMATCHED, and it needs its own pass. This
    walks the installed distributions and compares the ones the lock also names,
    so a locked package that is simply not there matches nothing, is compared
    against nothing, and was reported as a clean tree — while the verdict went
    on to certify the locked versions. The four import probes above do not cover
    it either: they import the four DIRECT dependencies, and the packages that
    can go missing without an import failing are the transitive ones nothing
    imports until it needs them (`cryptography` is reached only by an encrypted
    PDF; `pillow` and `pypdfium2` only by a page render).

    Bias direction, stated because it decides the severity — and it is TWO
    severities, at two boundaries:

    * At this gate, a WARNING alongside the missing packages. Blocking here
      would refuse /tax-calendar, /tax-scenario and every other skill that opens
      no document, on a signal parsed out of a text file, which is the cry-wolf
      shape that teaches an agent to reason past the gates that matter.
    * At the parse boundary, a REFUSAL. `parse_utils` reads these same records
      and will not open a PDF or a workbook while a package the parse itself
      runs on is off the lock, because that is where an untested parser turns
      into a wrong figure on a return, silently and in either direction. The
      warning here says so, so the two channels do not contradict each other.
    """
    lock = _ENGINE_ROOT / "requirements.txt"
    try:
        text = lock.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return (), (f"{type(e).__name__}: requirements.txt could not be read, "
                    f"so the installed versions were not compared against the "
                    f"lock at all: {e}")
    pins = {}
    for match in _LOCK_PIN.finditer(text):
        name = _canonical_dist(match.group(1))
        # Every LINE is kept, and the version is kept PAIRED with its marker's
        # verdict. Neither half survives a fold: two lines under opposite
        # markers hold two different versions, so keeping whichever came last
        # reports the correct install as drift, and pooling both into one set
        # accepts the version meant for the other interpreter. `_pins_here`
        # states the rule once, over the whole list.
        pins.setdefault(name, []).append(
            (match.group(2), _marker_applies((match.group(3) or "").strip())))
    if not pins:
        return (), ("requirements.txt parsed to no pins at all, so the "
                    "installed versions were not compared against the lock")
    drift = []
    seen = set()
    contradictory = []
    unreadable = []
    for dist in importlib.metadata.distributions():
        try:
            raw = dist.metadata["Name"]
        except (KeyError, TypeError, OSError):
            # A distribution whose METADATA is unreadable or malformed. Skipped
            # rather than raised: one broken sibling in site-packages must not
            # turn a version comparison into a gate that cannot run.
            continue
        if not raw:
            continue
        name = _canonical_dist(raw)
        # FIRST occurrence wins, because that is what an import resolves to:
        # `distributions()` yields in `sys.path` order, and two copies of one
        # package across two path entries would otherwise be reported as drift
        # against each other.
        if name in seen or name not in pins:
            # Not in the lock at all is not drift — `pip`, `pytest` and the
            # build tooling live in the same virtualenv and are pinned nowhere.
            seen.add(name)
            continue
        seen.add(name)
        applicable, undecided = _pins_here(pins[name])
        if len(applicable) > 1:
            # Two readable markers, both selecting this machine, at two
            # different versions. No install satisfies both, so there is no
            # single pin to compare against and any answer here would be one of
            # them picked arbitrarily — which is the fail-open being closed.
            # `uv pip compile` does not emit this; a hand-edited lock does.
            contradictory.append(name)
        elif applicable:
            if dist.version not in applicable:
                # Against the applicable pin ALONE. A record naming both lines
                # tells a reader on this interpreter to install the version
                # meant for the other one.
                drift.append((name, dist.version, ", ".join(sorted(applicable))))
        elif undecided and dist.version not in undecided:
            # No readable pin applies and none of the unreadable ones names what
            # is installed. Whether that is drift depends on the marker this
            # evaluator could not read, so it is reported as undecided rather
            # than answered — the same rule the absent side follows below.
            unreadable.append(name)
        # Left silent: every line definitely EXCLUDED here (the lock does not
        # govern this package on this interpreter — PyPy ships its own `cffi`,
        # which `pip install -r requirements.txt` will neither install nor
        # remove), and an installed version that an unreadable pin names, which
        # is a match whichever way that marker was meant to read.
    # The other direction. Everything above answers "is what is HERE what the
    # lock says"; nothing yet answers "is everything the lock says here at all".
    absent = []
    for name in sorted(pins):
        if name in seen:
            continue
        applicable, undecided = _pins_here(pins[name])
        if applicable:
            drift.append((name, None, ", ".join(sorted(applicable))))
        elif undecided:
            absent.append(name)
    # Whatever could not be DECIDED, in one channel. Each of these routes to
    # `lock_error` rather than to a drift record: a pin this interpreter is
    # genuinely not meant to have would otherwise raise an alarm that the
    # printed remedy cannot clear — `pip install -r requirements.txt` will not
    # install a pin whose marker excludes the machine — and a warning that
    # survives its own fix is the one an agent learns to skip. Reported rather
    # than dropped, because dropping it is the fail-open this pass exists to
    # close, and `parse_utils` refuses to open a document on a lock error just
    # as it does on named drift.
    parts = []
    if contradictory:
        parts.append(f"{', '.join(sorted(contradictory))} — the lock names more "
                     "than one version whose marker selects this interpreter, "
                     "so no single pin is the one to compare against")
    if unreadable:
        parts.append(f"{', '.join(sorted(unreadable))} — installed at a version "
                     "no pin this evaluator could read names, so which pin "
                     "governs here was not decided")
    if absent:
        parts.append(f"{', '.join(absent)} — nothing installed provides them, "
                     "and whether the lock requires them on this interpreter "
                     "was not decided")
    error = ""
    if parts:
        error = ("environment markers that could not be evaluated: "
                 + "; ".join(parts))
    # By NAME. The records now hold a None in the version slot, and sorting
    # whole tuples would compare None against a string the moment two names
    # collide — which they cannot today, and which is not a property to leave a
    # sort depending on.
    return tuple(sorted(drift, key=lambda record: record[0])), error


def check(modules=ENGINE_MODULES, workspace=None) -> PreflightReport:
    """Verify the computation engine is present, importable, and correct."""
    version = ".".join(str(p) for p in sys.version_info[:3])
    python_ok = sys.version_info[:2] >= MIN_PYTHON

    failures = []
    # The probe needs ONE module and this captures that one, rather than
    # accumulating all twenty-one and looking the name back up as a string. The
    # lookup was the hazard, not the memory: rename the math module in
    # GATE_MODULES and `loaded.get("agents.lib.tax_computations")` silently
    # returns None, the probe never runs, and no probe failure is reported —
    # while the rename also produces a load failure that masks the silence.
    math_module = None
    probe_name = GATE_MODULES[0][0]
    for name, role, required in modules:
        module, (error, needs_redaction, file_missing) = _load(name)
        if not error and not hasattr(module, required):
            # PRESENT and IMPORTABLE is not USABLE. An emptied or stub file —
            # an interrupted copy, a failed download, an LFS pointer stripped —
            # imports perfectly and exposes nothing, and the gate said "OK,
            # gates available: the intake-to-result tie-out gate" over it.
            # Worse downstream: `python3 agents/lib/tie_out.py` then exits 0
            # with no output, which tax-compute Step 12.7 reads as TIED OUT, so
            # a dropped 1099 sails through behind a green preflight. Reproduced
            # by truncating tie_out.py to zero bytes. Anything truncated
            # mid-statement raises SyntaxError and was already caught; this is
            # the case that was not.
            error, needs_redaction, file_missing = (
                f"AttributeError: imported but exposes no `{required}` — the "
                f"file is present and empty or stubbed", False, False)
        if not error:
            # IMPORTABLE and EXPORTING the symbol is still not EXECUTABLE, and
            # executing is what the skills do. `_cli_dispatch_error` states the
            # cut that passes both checks above and turns a hard gate into an
            # exit-0 no-op.
            error, needs_redaction, file_missing = _cli_dispatch_error(name)
        if error:
            failures.append(
                ModuleFailure(name, role, error, needs_redaction, file_missing))
        elif name == probe_name:
            math_module = module

    # The guard is checked whatever roster was passed, because `modules` is a
    # caller's choice and this one is not optional: it is the file whose
    # absence leaves every other module importing perfectly.
    guard_error, guard_redaction, guard_missing, guard_probe_error = \
        _guard_error()
    if guard_error:
        failures.append(ModuleFailure(_GUARD_MODULE, _GUARD_ROLE, guard_error,
                                      guard_redaction, guard_missing))

    # When the math module failed to load its own failure is already reported
    # and a probe error would just repeat it.
    probe_error = _probe(math_module) if math_module is not None else ""
    # A roster that did not include the math module leaves the probe unrun, and
    # the OK verdict said "present and computing correctly" over it — with
    # `modules_checked` at 0 and the full gate list printed. Say what was
    # actually checked instead of asserting what was not.
    probe_ran = math_module is not None

    venv_error, defect = _venv_error(workspace)
    # The dependency probe IMPORTS four third-party packages, which is ~0.29s of
    # a gate that runs first in all twenty skills. Skipped when the venv check
    # already failed: the result would be measured on an interpreter the verdict
    # then disclaims in the same document ("they say nothing about it yet"), so
    # it is time spent producing something discarded.
    # Skipped on ANY blocking report, not just a venv one. This imports the
    # four heaviest third-party packages — `parse_utils`' `defuse_stdlib()`
    # among them — on the first step of all twenty skills, and the result is
    # discarded by every path that blocks: the verdict has a root cause already,
    # and a package list measured against an engine that will not import is not
    # what the reader acts on next.
    #
    # It also makes the invariant a real one rather than a near-miss.
    # `missing_dependencies` now cannot co-exist with ANY blocking cause, which
    # `__post_init__` states and which deletes the last of the guards written
    # for a combination that could not occur.
    would_block = (not python_ok or failures or probe_error or venv_error)
    missing_deps = () if would_block else _missing_dependencies()
    drifted_deps, lock_error = ((), "") if would_block \
        else _drifted_dependencies()
    no_venv = defect == _NO_VENV
    if no_venv:
        defect = ""
    checked_names = {name for name, _role, _sym in modules}
    gates_available = tuple(role for name, role, _sym in GATE_MODULES
                            if name in checked_names)
    uncomparable = defect == _UNCOMPARABLE
    workspace_missing = defect == _WORKSPACE_MISSING

    # BY KEYWORD. Eleven positional arguments put `venv_broken` and
    # `venv_uncomparable` side by side as bare booleans at this one call site,
    # and their remedies are opposites: broken renders `rm -rf .venv`,
    # uncomparable renders "do not delete it on this evidence". Swapping the two
    # in the dataclass, or inserting a field between them, still type-checks and
    # still runs — it just starts telling readers to destroy intact
    # virtualenvs. Nothing else structurally prevented that; the tests all build
    # this object by keyword already.
    return PreflightReport(
        python_version=version,
        python_ok=python_ok,
        failures=tuple(failures),
        probe_error=probe_error,
        modules_checked=len(modules),
        venv_error=venv_error,
        venv_broken=bool(defect) and not uncomparable and not workspace_missing,
        venv_uncomparable=uncomparable,
        workspace_missing=workspace_missing,
        workspace="" if workspace is None else str(workspace),
        missing_dependencies=missing_deps,
        drifted_dependencies=drifted_deps,
        dependency_lock_error=lock_error,
        probe_ran=probe_ran,
        gates_available=gates_available,
        guard_probe_error=guard_probe_error,
        no_workspace_venv=no_venv)


def _warning_remedy_text(report: PreflightReport) -> str:
    """The remedy for a NON-blocking report — the three warning kinds.

    One owner, for the reason `_remedy_text` has one: this expression was
    written out in `render_markdown` and again in `_report_to_dict`, and the two
    have to agree (a test exists because they once did not) with nothing
    structurally forcing it.

    ONE remedy, in priority order, never a concatenation. All three end in the
    same `pip install -r requirements.txt`, and with no `.venv` at all
    `_usable_venv` is False, so each one already opens by CREATING the
    environment — emitting two would hand a consumer the same
    `python3 -m venv .venv` twice, which is the duplication three comments in
    `_remedy_text` suppress on the blocking side.
    """
    # `workspace` on every branch. This is the WARNING path — the one a healthy
    # workspace actually reaches — and it was the call site left behind when the
    # blocking remedies learned to say `cd`: a `pip install` run one directory
    # over targets the host interpreter, which is the failure this whole family
    # of remedies exists to prevent.
    layout = _venv_layout(_workspace_venv(report.workspace or None))
    usable = _usable_venv(report)
    root = _remedy_root(report)
    if report.missing_dependencies:
        return dependency_remedy(layout, usable, workspace=root)
    # Ahead of the no-venv remedy and behind the missing one, because the three
    # are ordered by how much each says: reinstalling the lock installs what is
    # missing, restores what drifted, and creates the virtualenv when there is
    # none, while the no-venv remedy alone says nothing about a version.
    if report.drifted_dependencies or report.dependency_lock_error:
        return drift_remedy(layout, usable, workspace=root)
    return no_venv_remedy(root) if report.no_workspace_venv else ""


def _remedy_text(report: PreflightReport) -> str:
    """Every remedy that applies, in the order to try them.

    Shared by the markdown verdict and the --json report rather than
    built inside the renderer: a consumer given `blocking: true` and no
    remedy has a refusal it cannot act on, which is the one thing
    render_markdown's own docstring says a refusal must never be.
    """
    if report.workspace_missing:
        # FIRST and ALONE. Every other remedy in this module names `.venv`
        # relative to a root that is not there, so each would print commands
        # that cannot run, under a diagnosis that is not what went wrong.
        return workspace_remedy(report.workspace)
    unactivated = report.unactivated
    files_missing = report.files_missing
    # A refusal states the remedy for the failure AT HAND, and a remedy that
    # cannot resolve what was reported is worse than none: it is followed, it
    # fails, and the reader is back here with no route out. "Re-clone the
    # toolkit" is useless to someone whose virtualenv was merely not activated;
    # it is also useless under Python 3.9, since the `python3 -m venv` inside it
    # inherits the very version that failed. Each cause carries its own.
    # Ordered by what to try FIRST, which is the root cause when there is one.
    # An unsupported interpreter fails the engine's imports outright — ten
    # modules use PEP 604 `X | None`, so 3.9 reports them all missing. Leading
    # with "re-clone the toolkit" would send the reader down a path that cannot
    # work, to fix files that are not the problem.
    #
    # AN UNACTIVATED WORKSPACE VENV OUTRANKS THE VERSION, and getting that order
    # wrong was destructive rather than merely unhelpful. On a stock macOS the
    # host `python3` is 3.9, so a workspace holding a perfectly good 3.12 `.venv`
    # that simply was not activated reports python_ok=False plus every PEP 604
    # module missing — and the version remedy LEADS WITH `rm -rf .venv`. The
    # reader deletes the one interpreter that would have fixed the failure, and
    # the note above used to point them at the version while doing it. The old
    # Python is the SYMPTOM here; the missing activation is the cause, so it goes
    # first and the version advice follows for the case where activating is not
    # enough.
    layout = _venv_layout(_workspace_venv(report.workspace or None))
    remedies = []
    if unactivated:
        # ONE instruction, and the others are SUPPRESSED rather than demoted.
        # Reordering alone was a half-fix: `rm -rf .venv` still printed in this
        # exact case, under a sentence — "recreating `.venv` with the
        # interpreter that just failed leaves it failing" — that is FALSE here,
        # because the `.venv` is the 3.12 nobody activated. A reader who skims
        # to the second shell block deletes the interpreter that would have
        # fixed the run.
        #
        # Suppression is also the honest verdict, not merely the safe one:
        # every other failure on this report was observed on the WRONG
        # interpreter and is a symptom until the right one is running. The
        # engine may be complete and the version fine; nothing here can say so
        # yet. Activate, re-run, and let the next verdict diagnose what is left.
        # The interpreter passed to the escape hatch is the one that will WORK,
        # not the one running now. Its rebuild step is reached by a reader who
        # already chained the activation, and a bare `python3 -m venv` there
        # builds a second venv from the very interpreter that failed the version
        # check — the loop `python_remedy` exists to break, reintroduced by the
        # suppression above.
        #
        # EXCEPT a missing FILE, which no interpreter change can conjure. That
        # check never touches the interpreter, so suppressing the clone remedy
        # for it left the reader with an activation that cannot help and, as its
        # escape hatch, `rm -rf .venv` aimed at an intact virtualenv — while the
        # one remedy that would work went unprinted.
        #
        # ...and when the files ARE missing, the clone remedy LEADS. The
        # activation advice names `sh agents/lib/run.sh`, and on this report
        # `agents/lib/` is what is gone — so the first instruction under "How to
        # proceed" was a launcher that exits 127 in that tree, above the one
        # remedy that would work. The interpreter has to be named there too:
        # `clone_remedy` builds with `_rebuild_interpreter` silently, so a reader
        # on a stock macOS 3.9 who is not told why sees `python3.12` twice, uses
        # their own `python3`, and rebuilds the identical failure — the loop
        # `defer_to="clone"` exists to break, which this branch never reached.
        if report.files_missing:
            remedies.append(clone_remedy(
                layout, python=_rebuild_interpreter(report)))
            if not report.python_ok:
                remedies.append(python_remedy(
                    layout, defer_to="clone", workspace=_remedy_root(report)))
        # `escape=not files_missing`: the escape hatch ends in `_rebuild_steps`,
        # and when the clone remedy above has ALREADY built the environment two
        # paragraphs earlier, that hatch tells the reader to delete what they
        # just made and build it again. One report, the venv built twice with an
        # `rm -rf` between — the third instance of the collision the `else`
        # branch guards with `rebuild_follows`, arriving through the ordering
        # fix that put the clone first.
        remedies.append(venv_remedy(layout, python=_rebuild_interpreter(report),
                                    workspace=_remedy_root(report),
                                    escape=not report.files_missing))
    else:
        # Every remedy that names an interpreter names one that WORKS. A bare
        # `python3 -m venv` inside the clone or rebuild advice builds a venv
        # from the interpreter that just failed the version check — the loop
        # `python_remedy` exists to break, arriving through the other two.
        python = _rebuild_interpreter(report)
        # A missing FILE leads, matching the headline. `render_markdown` argues
        # that a file not on disk is a fact no version change alters and ranks
        # it first; appending the version remedy before the clone one made the
        # two disagree about the root cause on one report — and the version
        # remedy opens with `rm -rf`.
        if files_missing:
            remedies.append(clone_remedy(layout, python=python))
        # A clone and a rebuild BOTH rendering printed `python3 -m venv .venv`
        # and the same pip line twice, with an `rm -rf .venv` between them,
        # under two prose paragraphs — and a reader given the venv built twice
        # cannot tell whether that is one procedure or two. It is the identical
        # duplication `python_remedy` is suppressed for eight lines below, and
        # it was checked for only one of the two pairs that collide. The clone
        # remedy wins: it is the one that restores the engine, and its own steps
        # already build the environment inside the fresh checkout.
        rebuild_suppressed = files_missing
        # `python_remedy` is SUPPRESSED when the venv remedy will also render
        # `_rebuild_steps`: the two printed the same three destructive lines
        # under different prose, and a reader given `rm -rf .venv` twice cannot
        # tell whether that is one step or two. The version is named inside the
        # rebuild instead, via `_rebuild_interpreter`.
        # ...and `files_missing` suppresses its rebuild for the SAME reason the
        # line above suppresses the venv one: the clone remedy has already built
        # the environment inside the fresh checkout. Naming only the venv pair
        # left the flagship verdict — a skill copied away from the engine on a
        # stock macOS 3.9, which is both conditions at once — printing
        # `python3.12 -m venv .venv` and the pip line twice with an `rm -rf`
        # wedged between them. Two of the three pairs that can collide were
        # checked; this is the third.
        rebuild_follows = (bool(report.venv_error)
                           and not report.venv_uncomparable) or files_missing
        if not report.python_ok and not rebuild_follows:
            # ...and when the venv could not be compared, the version remedy
            # renders WITHOUT its rebuild: `inspect_remedy` is appended below on
            # that same report and says not to delete `.venv`.
            remedies.append(python_remedy(
                layout,
                defer_to="inspect" if report.venv_uncomparable else "",
                workspace=_remedy_root(report)))
        elif not report.python_ok and files_missing:
            # The version is still the root cause and must still be NAMED — the
            # clone remedy builds with `_rebuild_interpreter`, but silently, and
            # a reader on 3.9 who is not told why cannot know that using their
            # own `python3` there rebuilds the same failure.
            remedies.append(python_remedy(
                layout, defer_to="clone", workspace=_remedy_root(report)))
        # `files_missing`, not `engine_missing`. An import that RAISED is not a
        # missing clone: on a stock macOS 3.9 every PEP 604 module fails inside
        # a complete checkout, and telling that reader to `git clone` into a
        # directory that already exists — under "a skill copied on its own
        # carries the instructions but not the engine" — is advice that fails on
        # its first line, which is this module's own definition of worse than
        # none.
        # BOTH, when both were reported. Gating the probe remedy on "no module
        # also failed" dropped it whenever an import error accompanied wrong
        # arithmetic — and wrong arithmetic is the more dangerous of the two,
        # left as a bullet with no way out.
        if report.probe_error:
            remedies.append(probe_failure_remedy())
        if report.failures and report.python_ok and not files_missing:
            # `not files_missing`, or the flagship verdict prints BOTH: the
            # clone remedy that fixes it, and then import_failure_remedy
            # asserting "the engine's files are present but at least one will
            # not import" directly under a headline saying they are missing.
            remedies.append(import_failure_remedy())
        if report.venv_uncomparable:
            remedies.append(inspect_remedy(layout, workspace=_remedy_root(report)))
        elif report.venv_error and not rebuild_suppressed:
            remedies.append(rebuild_remedy(layout, python=python,
                                           workspace=_remedy_root(report)))
        # No ordering argument here any more, and none is needed: the comment
        # this replaced justified the venv remedy coming before the dependency
        # one ("installing into a broken environment and then being told to
        # delete it"), a sequence `__post_init__` now makes unbuildable —
        # `missing_dependencies` and `venv_error` cannot both be set. A comment
        # defending an impossible ordering reads as live reasoning and is the
        # third dead guard this file has carried on that same pair.
        # No dependency remedy on this path either: a blocking report cannot
        # carry packages. `_warning_remedy_text` is where that remedy lives, and
        # the WARNING verdict is the only one that can reach it.
    return "\n\n".join(remedies)


def render_markdown(report: PreflightReport) -> str:
    """Human-readable verdict. A refusal always states how to proceed."""
    if not report.blocking:
        warnings = []
        if report.no_workspace_venv:
            warnings.append(
                "**No workspace virtualenv.** This ran on the host interpreter, "
                "so the versions the engine will use are whatever that "
                "interpreter carries — not the hash-pinned tree in "
                "`requirements.txt`. AGENTS.md §7 requires the virtualenv, and "
                "the lock exists because a republished parsing library changes "
                "what a document parses to.\n\n"
                f"{no_venv_remedy(_remedy_root(report))}")
        if report.guard_probe_error:
            warnings.append(
                "**The host-interpreter guard was not CERTIFIED.** "
                f"{_redact(report.guard_probe_error)}\n\n"
                "This says nothing against the engine, which is why it is a "
                "warning: the package imported, so the refusal that stands "
                "between a host interpreter and a return did run for this "
                "process. What went unanswered is whether it would refuse a "
                "wrong one. Re-run this check once the temporary directory is "
                "writable again.")
        # Computed ONCE, above the branch. The warning branch had its own
        # hardcoded "and computing correctly" and so re-made, three lines from
        # the fix, the over-claim the plain-OK branch was rewritten to stop: a
        # roster without the math module leaves the probe unrun, and one missing
        # package was enough to route the verdict through the copy that asserts
        # the arithmetic anyway.
        computed = ("and computing correctly" if report.probe_ran else
                    "(arithmetic NOT probed — the roster did not include the "
                    "math module)")
        # Hoisted for the same reason `computed` is. The gate list said what
        # this run VERIFIED, and it appeared only on the plain-OK branch — so a
        # single missing package, or a workspace without a virtualenv, silently
        # deleted the sentence naming which gates are available. Two branches,
        # one claim: neither warning has anything to do with which gates loaded.
        # The gates that were CHECKED, not the full roster: gating on
        # `probe_ran` alone and then printing all four meant a roster holding
        # only the math module still asserted the staleness, capability and
        # tie-out gates were available — three claims about modules nothing
        # loaded.
        gates = (f" Gates available: {', '.join(report.gates_available)}."
                 if report.probe_ran and report.gates_available else "")
        if report.missing_dependencies:
            names = ", ".join(f"`{n}`" for n, _, _ in report.missing_dependencies)
            affected = sorted({sk for _, _, sks in report.missing_dependencies
                               for sk in sks})
            warnings.append(
                f"**Not installed: {names}.** The skills that need them "
                f"({', '.join(affected)}) will fail at first use; the rest are "
                f"unaffected, which is why this is a warning and not a block."
                f"\n\n{_warning_remedy_text(report)}")
        # AFTER the missing-package block, because it refers back to it. Both
        # are fixed by the same `pip install -r requirements.txt`, so the
        # command is printed once and this block points at it rather than
        # repeating it three lines below itself.
        if report.drifted_dependencies or report.dependency_lock_error:
            # BOTH, when both. These were an if/else, so a report carrying an
            # unevaluable marker alongside real drift printed the marker
            # sentence and swallowed every package name — the actionable half of
            # the warning — under a headline saying nothing was verified.
            parts = []
            if report.drifted_dependencies:
                named = ", ".join(
                    (f"`{pkg}` NOT INSTALLED (lock pins {pinned})"
                     if installed is None
                     else f"`{pkg}` {installed} (lock pins {pinned})")
                    for pkg, installed, pinned in report.drifted_dependencies)
                parts.append(
                    f"**Installed versions differ from the lock: {named}.** "
                    f"A version the lock does not name is one the suite never "
                    f"ran against, and the packages under these pins are the "
                    f"document parsers — so parsing REFUSES to open a PDF or a "
                    f"workbook until the pinned tree is restored. The skills "
                    f"that open no document are unaffected, which is why this "
                    f"is a warning here rather than a block.")
            if report.dependency_lock_error:
                parts.append(f"**Dependency versions NOT verified.** "
                             f"{_redact(report.dependency_lock_error)}")
            detail = "\n\n".join(parts)
            if report.missing_dependencies:
                detail += ("\n\nThe install step above restores the pinned "
                           "tree and fixes this too.")
            else:
                warnings_remedy = drift_remedy(
                    _venv_layout(_workspace_venv(report.workspace or None)),
                    _usable_venv(report), workspace=_remedy_root(report))
                detail += f"\n\n{warnings_remedy}"
            warnings.append(detail)
        if warnings:
            # ONE warning verdict for both kinds. A second `return` per warning
            # meant whichever was checked first hid the other — and the two are
            # independent: a workspace with no virtualenv and a missing package
            # is the ordinary state of a clone whose setup was never run.
            body = "\n\n".join(warnings)
            return (
                f"# Engine preflight: OK, with {len(warnings)} warning(s)\n\n"
                f"Computation engine present {computed} — "
                f"{report.modules_checked} modules, Python "
                f"{report.python_version}.{gates}\n\n"
                f"## Warnings\n\n{body}")
        # "at the locked versions" is EARNED here and nowhere else: this branch
        # is reached only when nothing is missing, nothing drifted, and the lock
        # was readable. The sentence used to stop at "importable", which is a
        # smaller claim than the lock makes — the engine can import a
        # `pdfminer.six` the suite never ran against — and every warning branch
        # above returns before this line.
        return (f"# Engine preflight: OK\n\n"
                f"Computation engine present {computed} — "
                f"{report.modules_checked} modules, Python "
                f"{report.python_version}.{gates} "
                f"Pinned dependencies importable at the locked versions.")

    engine_missing = report.engine_missing
    files_missing = report.files_missing
    # The headline names the ROOT cause, and an old interpreter is not a missing
    # engine. `engine_missing or not python_ok` used to collapse the two, so a
    # 3.9 run printed "the computation engine is missing or unusable" and then,
    # ten lines down, "most likely symptoms of the interpreter, not of a missing
    # engine" — the banner and the diagnosis stating opposite causes on this
    # module's flagship scenario. Ordered by which explains the others: an
    # unsupported interpreter fails the imports, so it comes first and the
    # module failures are its symptom.
    if report.workspace_missing:
        # FIRST of all, because it is not a statement about this workspace's
        # engine or its virtualenv — the check never got far enough to look at
        # either. Without its own branch it fell to the `else`, whose banner
        # blames the interpreter check, two paragraphs above a remedy saying in
        # so many words that nothing here is about an interpreter: one document,
        # two causes, on the state that was deliberately split out BECAUSE it is
        # a fact about the caller's argument.
        cause = ("The `--workspace` argument names a directory that does not "
                 "exist, so there is no workspace for this check to speak for.")
    elif files_missing:
        # Then a missing FILE, ahead of the interpreter: a file that is not on
        # disk is a fact no version change alters. Selecting on `not python_ok`
        # alone printed "the failures below are that, not a missing engine" over
        # a list of FileNotFoundErrors, while the remedy below simultaneously
        # emitted the clone instructions because it knew better.
        cause = ("Engine modules are missing from `agents/lib/` — this skill "
                 "has been separated from the code it runs.")
    elif not report.python_ok:
        cause = ("This interpreter is older than the toolkit supports, so the "
                 "engine's modules cannot even be imported \u2014 the failures below "
                 "are that, not a missing engine.")
    elif engine_missing:
        cause = ("The computation engine this skill depends on is missing or "
                 "unusable, and the skill's hard gates cannot run.")
    else:
        cause = ("This check could not verify the interpreter that will "
                 "actually run the engine, so its verdict would not be about "
                 "that engine.")
    lines = ["# Engine preflight: BLOCKED", "",
             f"**Do not compute, parse, or emit a return.** {cause}", ""]

    if not report.python_ok:
        lines += [f"- Python {report.python_version} is older than the required "
                  f"{MIN_PYTHON_STR}.", ""]

    for failure in report.failures:
        detail = (_redact(failure.error) if failure.needs_redaction
                  else failure.error)
        lines += [f"- `{failure.module}` — {failure.role} — could not be "
                  f"loaded: {detail}"]
    if report.failures:
        lines.append("")

    if report.probe_error:
        lines += [f"- {_redact(report.probe_error)}", ""]
        if report.unactivated:
            # `_remedy_text` suppresses every remedy but the activation on this
            # path, deliberately — the arithmetic came out wrong on an
            # interpreter this report disclaims, so it is a symptom until the
            # right one is running. Deliberate or not, without this the reader
            # gets "the engine is not computing correctly" and, underneath, an
            # activation step that structurally cannot fix wrong tax math. Say
            # which of the two it is. The dependency bullets carry the same
            # sentence for the same reason.
            lines += ["  Measured on the interpreter this ran on, not the "
                      "workspace venv — activate and re-run before treating it "
                      "as an engine fault.", ""]

    # On this path too, and for the reason it is not part of `blocking`: the
    # reader has a root cause already, and dropping the line would leave the
    # one uncertified gate unmentioned in the document that lists every other.
    if report.guard_probe_error:
        lines += [f"- the host-interpreter guard was not certified: "
                  f"{_redact(report.guard_probe_error)}", ""]

    unactivated = report.unactivated

    if not report.python_ok and report.failures and not files_missing:
        lines += ["Those module failures are most likely symptoms of the "
                  "interpreter, not of a missing engine — the toolkit uses "
                  "syntax older Pythons reject. "
                  + ("Activate the workspace venv and re-run before concluding "
                     "anything about the version or the files: a usable "
                     "interpreter is already sitting in this workspace."
                     if unactivated else
                     "Fix the version and re-run before concluding anything is "
                     "absent."), ""]

    # NO dependency section on a blocking verdict, and none is reachable:
    # `__post_init__` refuses a blocking report that carries packages, because
    # `check()` never probes for them once a blocking cause is known. What stood
    # here was a bullet list plus a sentence explaining why it had no remedy
    # under it — two layers of handling for a state the module cannot build.

    if report.venv_error:
        lines += [f"- {_redact(report.venv_error)}", ""]

    lines += ["## How to proceed", "", _remedy_text(report)]
    return "\n".join(lines)


def _report_to_dict(report: PreflightReport) -> dict:
    return {
        "python_version": report.python_version,
        "python_ok": report.python_ok,
        "blocking": report.blocking,
        # `file_missing` per failure, because the two answers are OPPOSITE and a
        # consumer cannot re-derive the distinction from the message text — which
        # is precisely why `ModuleFailure` carries it as state. Without it a JSON
        # reader cannot tell "the engine's files are gone, re-clone" from "the
        # files are there and the imports raised, do NOT re-clone".
        "failures": [{"module": f.module, "role": f.role,
                      "file_missing": f.file_missing,
                      "error": _redact(f.error) if f.needs_redaction
                      else f.error}
                     for f in report.failures],
        "probe_error": _redact(report.probe_error),
        # Separate from `failures` on purpose: a consumer that treated this as
        # one would block on an environment fault, which is the whole thing
        # `_probe_environment_fault` splits apart.
        "guard_probe_error": _redact(report.guard_probe_error),
        # An empty `probe_error` means one of two things and the markdown path
        # distinguishes them ("and computing correctly" against "arithmetic NOT
        # probed"). Emitting only the error left the JSON channel asserting the
        # stronger of the two by omission — the same over-claim the markdown
        # verdict was fixed for, surviving on the channel nothing checked.
        "probe_ran": report.probe_ran,
        "modules_checked": report.modules_checked,
        "venv_error": _redact(report.venv_error),
        "venv_broken": report.venv_broken,
        "venv_uncomparable": report.venv_uncomparable,
        # ...and the fourth. Without it a consumer sees venv_broken false,
        # venv_uncomparable false and a non-empty venv_error — which is exactly
        # `unactivated`, whose remedy is "run the launcher" — and advises that
        # against a root that does not exist. Every other opposite-remedy state
        # is exported for this reason; this one was added and not carried.
        "workspace_missing": report.workspace_missing,
        # The one warning STATE, and it was exported nowhere. A consumer read
        # `blocking: false`, an empty `venv_error` (this condition deliberately
        # does not write that field) and an empty `remedy`, and could not tell a
        # correctly set-up workspace from one with no virtualenv at all — so it
        # went on to compute the return on the host interpreter with whatever
        # versions it carries, which AGENTS.md §7 forbids and the hash-pinned
        # lock exists to prevent. The markdown channel warned about exactly
        # this; only the JSON one was silent.
        "no_workspace_venv": report.no_workspace_venv,
        # Name, role and affected skills — the markdown bullets carry all three,
        # and a consumer given only names cannot say which skills are affected
        # without re-deriving RUNTIME_DEPENDENCIES for itself.
        "missing_dependencies": [{"package": n, "role": r, "skills": list(sk)}
                                 for n, r, sk in report.missing_dependencies],
        # Installed AND pinned, not a bare list of names: "pdfplumber drifted"
        # is not actionable, and a consumer that has to re-read the lock to find
        # out from what to what is a consumer that will not. `installed` is
        # null for a locked package nothing provides — a STATE, not the string
        # the markdown channel renders, so a consumer separates "wrong version"
        # from "absent" without matching on prose it does not control.
        "drifted_dependencies": [{"package": p, "installed": i, "pinned": v}
                                 for p, i, v in report.drifted_dependencies],
        # An empty `drifted_dependencies` means every installed pin MATCHED.
        # This field is the other reason it can be empty — the lock was not
        # readable, so nothing was compared — and without it the JSON channel
        # reports "no drift" for a check that never ran, which is the fail-open
        # this module fixes everywhere it finds it.
        "dependency_lock_error": _redact(report.dependency_lock_error),
        "workspace": _redact(report.workspace),
        # The REMEDY, not only the diagnosis. render_markdown's docstring says a
        # refusal always states how to proceed, and a --json consumer was given
        # `blocking: true` and no way to act on it: it could not show the reader
        # the activation command, the version fix or the clone instructions, and
        # could not even tell which applied without re-deriving `unactivated`
        # for itself. Empty on a pass, where there is nothing to proceed from.
        # A WARNING carries a remedy too. `blocking` alone left a consumer with
        # `missing_dependencies` populated and `remedy: ""` — the same
        # diagnosis-without-instruction this field was added to remove, and a
        # disagreement with the markdown path, which prints a full
        # `## How to proceed` for that identical report.
        "remedy": (_remedy_text(report) if report.blocking
                   else _warning_remedy_text(report)),
    }


def _main(argv: list) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="agents.lib.preflight",
        description="Verify the computation engine is present before a skill "
                    "computes anything.")
    parser.add_argument("--json", action="store_true",
                        help="emit JSON instead of markdown")
    # Every sibling gate the skills call takes this (`capabilities.py
    # --workspace .`, `tie_out.py --workspace .`), so the option exists and is
    # honest about what it buys — which is LESS than the first version of this
    # comment claimed. It closes the wrong-CWD hole only for a caller that
    # supplies an ABSOLUTE root; the skills pass no `--workspace` at all, and
    # `--workspace .` resolves to the same CWD the default already uses, so an
    # agent whose sub-task starts in `summaries/` is still certified against a
    # `.venv` that is not there. Closing that needs the gate command to carry a
    # real root, which is a change to twenty skills and is not made here.
    parser.add_argument("--workspace", default=None,
                        help="workspace root to resolve `.venv` against "
                             "(default: the current directory)")
    args = parser.parse_args(argv)

    try:
        report = check(workspace=args.workspace)
    except (Exception, SystemExit) as e:
        # The TYPE unredacted, the message through the redactor. When the
        # missing module IS secure_fs, `_redact` can only answer with its
        # withheld marker, and this line then carried no fact at all —
        # not the exception class, not the module. The class name is
        # authored text, never a path, which is the same distinction
        # `ModuleFailure.needs_redaction` exists to make; the render path
        # twelve lines below already prints it this way.
        print(f"ERROR: {type(e).__name__}: {_redact(e)}", file=sys.stderr)
        return 1

    # Rendering is inside a try of its own. `check()` succeeding does not make
    # the rest safe: everything below reaches the engine again through _redact,
    # and an escape here would leave the process exiting on someone else's
    # status. There is a verdict at this point, so the floor is BLOCKED.
    try:
        if args.json:
            print(json.dumps(_report_to_dict(report), indent=2))
        else:
            print(render_markdown(report))
    except (Exception, SystemExit) as e:
        print(f"ERROR: the verdict could not be rendered: {type(e).__name__}",
              file=sys.stderr)
        return 2
    return 2 if report.blocking else 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
