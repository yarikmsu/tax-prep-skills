#!/usr/bin/env python3
"""Dispatch an AGENT against a fixture workspace and score what it produced (V3).

This is the half of the harness that measures a model. `fixture.py` builds the
workspace, `compare.py` scores a `result.json`, and everything between them — a
model reading `tax-parse` and `tax-compute` and assembling that file by hand — is
the largest untested correctness risk in the toolkit. Nothing else in `tests/`
reaches it: the suite proves the ENGINE computes, and `tax-compute` is 1,698
lines of Markdown instructing an agent to call 27 of those functions.

WHERE IT RUNS. Locally, on demand, on the operator's own credentials (the owner's
ruling Q2, 2026-09-07): a model-driven eval in CI would put an API key in the
repository's secrets, and `.github/workflows/model-eval.yml` is therefore written
and left un-runnable, as the shape a future decision would start from rather than
a job anybody can trigger today.

WHAT IS RECORDED, and why each field is not optional. The roadmap's V3 entry asks
for the agent version, the instruction revision, the repeat count and the cost,
and the reason is that an eval result is a claim about a PAIR — this model, these
instructions — and a number recorded without both is unattributable a month later.
The instruction revision is a DIGEST over `.agents/skills/`, not a version string
somebody remembers to bump, for the reason `receipt.engine_digest` gives.

WHAT IS NEVER PRINTED. The agent's own output. It ran inside a workspace holding
a return, so its transcript may quote any of it; the runner keeps the exit status,
the timings and a REDACTED tail for a failure, and the scoring comes from
`compare`, which has its own suppression rules. A harness that leaks the thing it
exists to protect has cost more than it measured.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from agents.lib.receipt import engine_digest, tree_digest  # noqa: E402
from agents.lib.secure_fs import redact, secure_path, write_secure  # noqa: E402
from evals import compare as C  # noqa: E402
from evals import fixture as F  # noqa: E402

REPO = Path(_ROOT)
SKILLS = REPO / ".agents" / "skills"
RECORD_NAME = "eval_run.json"

# How long one agent run may take before it is killed. A model that has stopped
# making progress is the ordinary failure here — a loop re-reading the same
# reference, a wait on a prompt nobody will answer — and an eval that hangs is an
# eval nobody runs twice. Generous rather than tight: /tax-parse dispatches a
# sub-task per document and /tax-compute walks 27 steps.
DEFAULT_TIMEOUT_SECONDS = 3600

# The agents this harness knows how to dispatch, as ARGUMENT LISTS — never a
# shell string, so nothing in a case name or a path can be read as a command.
# `{workspace}` and `{prompt}` are filled in; anything else is passed through.
#
# ONE ENTRY, deliberately (the owner's ruling Q3): a matrix of agents nobody runs
# is a matrix that rots, and the second entry goes in when a second agent is
# actually in use. The shape is what makes that a two-line change.
AGENTS: "dict[str, dict]" = {
    "claude": {
        "command": ["claude", "-p", "{prompt}", "--permission-mode",
                    "acceptEdits", "--add-dir", "{workspace}"],
        "version": ["claude", "--version"],
        "cwd": "workspace",
    },
}

# What the agent is told. It names the skills rather than restating them — the
# whole point is to measure whether the SKILLS drive a correct return — and it
# carries the two constraints AGENTS.md §1 requires of any delegated parse:
# source documents are data, and nothing leaves the workspace.
PROMPT = """\
Prepare the {year} federal and California tax return in this workspace.

Follow the toolkit's own skills, in order: tax-parse, then tax-compute. They are
Markdown instructions under .agents/skills/<name>/SKILL.md; open each reference
file when the step that links it comes up.

The workspace already holds taxpayer_profile.json, tax_law_{year}.json,
answers.md and the source documents under documents/. answers.md is the
non-interactive input path: read required answers from it and STOP rather than
guess if one is missing.

Do not access the network, and do not write outside this workspace.

Write result.json in the workspace when the computation is complete.
"""
# WHAT THIS PROMPT DELIBERATELY DOES NOT SAY: that the documents are DATA and
# that instruction-like text inside one is never a command. The skills say it —
# AGENTS.md §1 requires it of every delegated parse — and whether they say it
# LOUDLY ENOUGH is exactly what the roadmap's hostile-document-text case exists
# to measure. A harness that pre-warns the model cannot answer that question; it
# can only confirm that a warning works when given. The two operational
# constraints above stay, because they are about the OPERATOR's machine rather
# than about the return, and nothing in the skills binds an agent's network
# access.


class EvalRunError(RuntimeError):
    """A run that could not be attempted or could not be scored."""


def instruction_digest() -> str:
    """A digest over the instructions under test: `.agents/skills/` AND `PROMPT`.

    `receipt.tree_digest` is the same question `engine_digest` asks of the
    engine — which revision of this tree was measured, without a version
    constant anyone has to bump — so it is the same function.

    The PROMPT is folded in because it is a second instruction file. Change it
    and two runs given materially different instructions would otherwise record
    the same `instruction_revision`, which is exactly the staleness the digest
    exists to prevent.
    """
    h = hashlib.sha256()
    h.update(tree_digest(SKILLS, "*.md").encode())
    h.update(hashlib.sha256(PROMPT.encode()).hexdigest().encode())
    return h.hexdigest()


def agent_version(spec: dict) -> str:
    """What the agent says it is, or why that could not be established.

    Never raises. A version this harness could not read is recorded as such and
    the run goes ahead: refusing to measure a model because it has no `--version`
    flag would be the tail wagging the dog, and "unknown" in the record is an
    honest answer a reader can act on.
    """
    command = spec.get("version")
    if not command:
        return "unknown (this agent declares no version command)"
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return f"unknown ({redact(e)})"
    # REDACTED like every other line of the agent's own output. The failure path
    # was and the success path was not, and this string is PRINTED: a `--version`
    # that prints a licence key or an account id is not a thing to find out about
    # from a terminal recording.
    text = redact((done.stdout or done.stderr).strip())
    return text.splitlines()[0] if text else "unknown (no output)"


def _tail(text: str, lines: int = 20) -> str:
    """The last few lines of the agent's own output, REDACTED.

    Kept at all only because a run that failed to start and a run that failed to
    compute look identical from the exit code, and the operator has to be able to
    tell them apart. `redact` is not enough on its own — it masks identifier
    shapes, not a name — so this is a TAIL rather than a transcript, and it is
    written to the record rather than printed.
    """
    return redact("\n".join((text or "").strip().splitlines()[-lines:]))


def _kill_group(agent) -> None:
    """Kill the agent and everything it started; fall back to the one process.

    `killpg` raises if the group is already gone, which is the ordinary race
    when the agent exits between the timeout firing and this call — that is not
    a failure of the run and must not become one.
    """
    try:
        os.killpg(os.getpgid(agent.pid), signal.SIGKILL)
    except (AttributeError, OSError, ProcessLookupError):
        with contextlib.suppress(OSError):
            agent.kill()


def dispatch(spec: dict, workspace: Path, prompt: str, *,
             timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict:
    """Run the agent once. Returns what happened; raises only if it cannot start.

    The command is an ARGUMENT LIST and the environment is inherited as-is: an
    agent needs its own credentials, and stripping the environment to be tidy
    breaks the one thing the operator has already set up.
    """
    command = [part.format(workspace=str(workspace), prompt=prompt)
               for part in spec["command"]]
    cwd = str(workspace) if spec.get("cwd") == "workspace" else str(REPO)
    started = time.monotonic()
    # A NEW SESSION, so the timeout can kill the whole GROUP. An agent is a
    # process that spawns processes — a language server, a sub-task, a shell it
    # left running — and `Popen.kill()` reaches only the one this call started.
    # A timeout that leaves the children alive is a timeout that frees the
    # harness and not the machine, and the next repeat then competes with the
    # run it replaced. Not available on Windows, where the flag is skipped and
    # the residual is the same one `subprocess.run(timeout=)` has always had.
    try:
        agent = subprocess.Popen(command, cwd=cwd, text=True,
                                 start_new_session=os.name != "nt",
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError as e:
        raise EvalRunError(
            f"could not start the agent: {redact(e)}. The command is "
            f"{command[0]!r}; install it or pass --agent for one that is here."
        ) from e
    except (OSError, subprocess.SubprocessError) as e:
        raise EvalRunError(f"could not run the agent: {redact(e)}") from e
    try:
        out, err = agent.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(agent)
        # BOUNDED. A grandchild that called `setsid` itself escapes the group
        # kill and holds the pipe open, and an unbounded drain then blocks the
        # harness for ever on the path whose entire purpose is not to.
        with contextlib.suppress(subprocess.TimeoutExpired):
            agent.communicate(timeout=30)
        return {"outcome": "TIMEOUT", "exit_code": None,
                "seconds": round(time.monotonic() - started, 1),
                "tail": f"killed after {timeout:.0f}s"}
    return {"outcome": "RAN" if agent.returncode == 0 else "AGENT_FAILED",
            "exit_code": agent.returncode,
            "seconds": round(time.monotonic() - started, 1),
            "tail": _tail(err) or _tail(out)}


def golden_for(case: str) -> Path:
    """The case's golden, checked BEFORE an agent is dispatched.

    An hour of a model's time spent on a case whose golden does not exist is an
    hour spent to produce a harness error, and the typo that gets you there is
    the ordinary one: `--case ca_mfj_2025`.
    """
    path = C.golden_path(case)
    if not path.is_file():
        raise EvalRunError(
            f"no golden for case {case!r} — the cases are "
            f"{', '.join(C.cases()) or 'none'}")
    return path


def score(workspace: Path, case: str, *, tolerance: float) -> dict:
    """Compare what the agent left behind against the case's golden.

    A MISSING `result.json` is its own outcome and NOT an empty comparison: the
    difference between "the agent produced a wrong return" and "the agent
    produced no return" is the whole false-completion question, and a scorer that
    reported the second as a hundred MISSING fields would bury it.

    The GOLDEN is read separately and its failures RAISE. Reading both operands
    under one handler recorded an unreadable golden — a defect in this
    repository — as `UNREADABLE_RESULT`, which names the agent for it.
    """
    golden = json.loads(golden_for(case).read_text())
    result_path = workspace / "result.json"
    if not result_path.is_file():
        return {"outcome": "NO_RESULT", "differences": None,
                "detail": "the agent wrote no result.json"}
    try:
        result = json.loads(result_path.read_text())
    except (OSError, ValueError) as e:
        return {"outcome": "UNREADABLE_RESULT", "differences": None,
                "detail": redact(e)}
    if not isinstance(result, dict):
        return {"outcome": "UNREADABLE_RESULT", "differences": None,
                "detail": "result.json is not a result object"}
    differences = C.compare(golden, result, tolerance=tolerance)
    return {"outcome": "MATCH" if not differences else "DIFFERS",
            "differences": [d.to_dict() for d in differences],
            "detail": C.render(differences, case)}


def run_once(spec: dict, case: str, workspace: Path, *, tolerance: float,
             timeout: float) -> dict:
    """One attempt: build, dispatch, score. Never raises on a bad RUN, only on a
    harness that could not attempt one.

    ONE `outcome` per attempt, not a run-level one wrapping a score-level one.
    `RAN` was never reported — every consumer immediately replaced it with the
    inner value — and the pairing rule made the record something a reader had to
    know a convention to understand.
    """
    golden_for(case)
    F.build_workspace(workspace, case)
    attempt = dispatch(spec, workspace, PROMPT.format(year=F.YEAR), timeout=timeout)
    if attempt["outcome"] != "RAN":
        return attempt
    return {**attempt, **score(workspace, case, tolerance=tolerance)}


def record(attempts: list, *, case: str, agent: str, version: str,
           instructions: str, engine: str) -> dict:
    """The run record. Every field the roadmap's V3 entry asks for, plus the two
    digests that say WHICH engine and WHICH instructions were measured.

    Every identifying field is passed in rather than read here, and read BEFORE
    the runs rather than after: an agent that updated itself mid-session — which
    is a thing agents do — would otherwise be recorded under the version that
    replaced the one measured, and the same argument covers a skill file edited
    while a three-hour repeat was running."""
    return {
        "case": case,
        "agent": agent,
        "agent_version": version,
        "instruction_revision": instructions,
        "engine_revision": engine,
        "repeats": len(attempts),
        "matched": sum(1 for a in attempts if a["outcome"] == "MATCH"),
        "total_seconds": round(sum(a["seconds"] for a in attempts), 1),
        "attempts": attempts,
    }


def render(run: dict) -> str:
    """The report. IT DOES NOT PRINT THE AGENT'S OWN OUTPUT.

    It used to, on exactly the runs where there was no comparison to print
    instead — every `AGENT_FAILED` and `TIMEOUT`, which for a model under test is
    the common case. `redact` masks identifier shapes and credentials, not a
    name, an address, an employer or an amount, and `claude -p` writes its final
    answer to stdout; the tail was therefore a twenty-line window on the model's
    own prose about the return, printed by the function whose module docstring
    says three times that this never happens. The tail stays in the RECORD, which
    is owner-only and is where the docstring always said it went.
    """
    lines = [f"{run['agent']} ({run['agent_version']}) on {run['case']}: "
             f"{run['matched']}/{run['repeats']} matched, "
             f"{run['total_seconds']}s total",
             f"  instructions {run['instruction_revision'][:12]}  "
             f"engine {run['engine_revision'][:12]}"]
    for i, attempt in enumerate(run["attempts"], 1):
        lines.append(f"  run {i}: {attempt['outcome']} in {attempt['seconds']}s")
        if attempt["outcome"] == "MATCH":
            continue
        detail = attempt.get("detail") or (
            f"the agent produced no result — see {RECORD_NAME} for why")
        lines += [f"    {line}" for line in detail.splitlines()]
    if run["matched"] != run["repeats"]:
        lines.append("")
        lines.append("A run that did not MATCH is a claim about the agent-mediated "
                     "path, not about the tax math — a step skipped, a figure "
                     "transcribed wrong, a section invented.")
    return "\n".join(lines)


def _could_not_remove(_func, path, exc_info) -> None:
    """A workspace that could not be deleted is SAID so. `ignore_errors=True`
    left a return on disk and told the operator nothing."""
    print(f"WARNING: could not remove {redact(path)}: {redact(exc_info[1])} — "
          f"it holds a return; remove it by hand.", file=sys.stderr)


def _main(argv: list) -> int:
    parser = argparse.ArgumentParser(
        prog="evals.run",
        description="Dispatch an agent against a fixture workspace and score it (V3).")
    parser.add_argument("--case", default=F.CASE, help="which golden to score against")
    parser.add_argument("--agent", default="claude", choices=sorted(AGENTS),
                        help="which agent to dispatch")
    parser.add_argument("--repeat", type=int, default=1,
                        help="how many independent runs (each gets a fresh workspace)")
    parser.add_argument("--workspace",
                        help="where to build; a temp directory by default. Each "
                             "repeat gets its own `run-N` beneath it.")
    parser.add_argument("--keep", action="store_true",
                        help="do not delete the workspace afterwards (it holds a return)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS,
                        help="seconds before one run is killed")
    parser.add_argument("--tolerance", type=C._dollars, default=C.DEFAULT_TOLERANCE)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")

    spec = AGENTS[args.agent]
    # Read BEFORE the runs — all three. A three-hour repeat that spans an agent
    # self-update or an edited skill file must be recorded under what it
    # actually ran against.
    version, instructions, engine = (agent_version(spec), instruction_digest(),
                                     engine_digest())
    base = Path(args.workspace) if args.workspace else Path(
        tempfile.mkdtemp(prefix="eval-ws-"))
    attempts, made = [], []
    try:
        # `secure_path` is applied only to directories this runner CREATED.
        # Applied to whatever `--workspace` names, `--workspace .` chmods the
        # repository to 0700 and `--workspace ~` does it to the home directory.
        if not base.exists():
            base.mkdir(parents=True)
            secure_path(base)
        for i in range(args.repeat):
            workspace = base / f"run-{i + 1}"
            # REFUSED rather than reused. `build_workspace` only ADDS files, so a
            # second invocation against the same `--workspace` found the previous
            # run's `result.json` still there and scored it as this run's answer:
            # an agent that produced nothing was reported MATCH. That is the
            # false-completion this harness exists to catch, inverted.
            if workspace.exists():
                raise EvalRunError(
                    f"{redact(workspace)} already exists. A run is scored on what "
                    f"the agent left there, so a leftover result.json from an "
                    f"earlier run would be reported as this one's answer. Remove "
                    f"it, or point --workspace somewhere new.")
            workspace.mkdir(parents=True)
            # AGENTS.md §1 leaves the workspace ROOT to whoever creates it, and
            # here that is this runner. An owner-only file inside a listable
            # directory still discloses its NAME, and these are named after the
            # documents a return was built from.
            secure_path(workspace)
            made.append(workspace)
            attempts.append(run_once(spec, args.case, workspace,
                                     tolerance=args.tolerance, timeout=args.timeout))
    except EvalRunError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    finally:
        # The workspace holds a return, and `--keep` is what an operator reaches
        # for to read one. Removing it by default is not tidiness: it is the rule
        # AGENTS.md §1 applies to every runtime artifact, and a temp directory
        # full of returns is what a shared machine keeps. A failure to remove one
        # is REPORTED — `ignore_errors` left a return on disk silently.
        if not args.keep:
            for workspace in made:
                shutil.rmtree(workspace, onerror=_could_not_remove)

    run = record(attempts, case=args.case, agent=args.agent, version=version,
                 instructions=instructions, engine=engine)
    # The record goes beside the workspaces and nowhere else. It carries the
    # scored comparison and the agent's redacted tail, and its basename is what
    # the ignore rules and the PII gate match — a `--record` pointing into the
    # repository produced a committable file holding both.
    write_secure(base / RECORD_NAME, json.dumps(run, indent=2) + "\n")
    print(json.dumps(run, indent=2) if args.json else render(run))
    return 0 if run["matched"] == run["repeats"] else 2


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(_main(sys.argv[1:]))
