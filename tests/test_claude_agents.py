"""Claude Code subagent definitions — `.claude/agents/*.md`.

A subagent definition is a system prompt, and a system prompt full of confident,
wrong field paths is worse than no agent: the auditor looks for
`federal.salt_cap`, finds nothing where it was told to look, and reports what
reads as an absence. Nothing else in this repository imports these files, so
without the assertions below they rot in complete silence — a key renamed in
`create_empty_tax_law` leaves the whole suite green.

Two things are checked here, and both are behavioural rather than textual:

- every dotted schema path an agent names RESOLVES against the real schema
  builders in `agents/lib/tax_law_fetcher.py`;
- the tool grants match what the agents are for. These are review agents; a
  reviewer that can edit can silently "fix" what it found, and the only agent
  with network access must not also be able to read taxpayer files.
"""
import hashlib
import inspect
import json
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from agents.lib.tax_law_fetcher import (
    _AUTHORITATIVE_NONGOV_DOMAINS,
    create_empty_tax_law,
    create_state_template,
)

from tests.skill_text import PARSING_SKILLS, skill_text  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO / ".claude" / "agents"

# The agents whose output is a JUDGEMENT a human acts on. These are what the
# acceptance record covers: a replay against a known answer, because there is no
# way to check a reviewer's opinion except by giving it a defect somebody planted
# and seeing whether it says so.
REVIEW_AGENTS = {"tax-law-freshness-auditor", "bias-direction-reviewer"}

# Agents that do WORK rather than form a judgement, and whose acceptance is a
# different question. `tax-parse-worker` is not trusted to be right about a
# document — the tie-out gate checks that — it is trusted not to be able to do
# anything else, and that is checkable HERE, from its grant, rather than by
# replaying a session. Keeping the two sets apart is what stops a worker with no
# replay from either dragging the reviewers' standing claim down with it or
# quietly widening what "accepted" means.
WORKER_AGENTS = {"tax-parse-worker"}

EXPECTED_AGENTS = REVIEW_AGENTS | WORKER_AGENTS

# Every agent defined here reviews or audits. None of them writes.
#
# `Bash` is in this set, and that is the whole point of the set existing. The
# first version listed only the three obvious editors, so `bias-direction-reviewer`
# held `Bash` — `sed -i`, `echo >>`, `git checkout`, `git commit` — while
# CLAUDE.md said "neither has a write tool" and this assertion agreed. A shell IS
# a write capability; enumerating tools by their NAME rather than by what they
# can do is how a guarantee ends up documented, tested and false.
WRITE_TOOLS = {"Write", "Edit", "NotebookEdit", "Bash"}
NETWORK_TOOLS = {"WebSearch", "WebFetch"}

# Tool names OBSERVED to resolve when a sub-agent is actually dispatched on this
# host. Not a list read out of documentation, and the distinction is the whole
# point of the comment: `TodoWrite` sat in this set as a "real tool name" and the
# harness rejects it — `unrecognized [TodoWrite]`, tool list resolves to nothing,
# spawn REFUSED. A definition granting it passed every assertion in this module
# and could not be launched at all, which is the shape of failure a static check
# is worst at and this one had.
#
# So the set is now evidence, and `VERIFIED_TOOLS` below is the part of it that
# was watched to work. Adding a name means dispatching an agent that holds it and
# seeing it launch — from a FRESH session, because a definition is read at
# session start and an edit mid-session is not what a dispatch tests (measured:
# the edited grant was reported under the OLD name).
KNOWN_TOOLS = {"Read", "Write", "Edit", "NotebookEdit", "Grep", "Glob", "Bash",
               "WebSearch", "WebFetch"}

# The subset dispatched and seen to launch, with what evidence:
#   Read, Grep, Glob      — `bias-direction-reviewer` launched and used all three
#   WebSearch, WebFetch   — `tax-law-freshness-auditor`'s recorded acceptance runs
# A worker may hold ONLY these. Everything else is unverified on this host, and an
# unverified grant is how a boundary agent becomes an agent that does not exist.
VERIFIED_TOOLS = {"Read", "Grep", "Glob", "WebSearch", "WebFetch"}


def _agent_files():
    return sorted(AGENTS_DIR.glob("*.md"))


def _frontmatter(path: Path) -> dict:
    text = path.read_text()
    assert text.startswith("---\n"), f"{path.name} has no frontmatter block"
    _, block, _ = text.split("---\n", 2)
    out = {}
    for line in block.splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _tools(path: Path) -> set[str]:
    raw = _frontmatter(path).get("tools", "")
    return {t.strip() for t in raw.split(",") if t.strip()}


def _digest(raw: bytes) -> str:
    """The pin over `raw`, blind to line endings.

    Carriage returns are dropped first, and that is not cosmetic. This
    repository supports Windows contributors, `core.autocrlf=true` is what the
    Git for Windows installer recommends, and nothing here pins an EOL — so on
    such a checkout every definition is materialised with CRLF while the pin in
    the record was taken from LF bytes. Hashing raw bytes would then fail BOTH
    agents on a tree nobody has touched, and the failure would read as the one
    thing this pin exists to report: a definition edited since its verdict. A
    guard whose true and false alarms are indistinguishable is worse than the
    absence it replaces, and a contributor who cannot make it green learns to
    skip it.

    All `\\r`, not only the CRLF pairs, so the shell command the record
    documents for renewing a pin — `tr -d` piped into `shasum` — agrees with
    this to the byte on every platform. A lone CR inside a system prompt is not
    a difference the acceptance is about.

    Separate from `_live_digest` so a control can feed it both spellings of one
    file. Reading the real definition proves nothing about normalisation: there
    is only ever one copy on disk, and it has whatever endings the checkout gave
    it.
    """
    return hashlib.sha256(raw.replace(b"\r", b"")).hexdigest()


def _live_digest(name: str) -> str:
    """The pin for the definition that would be dispatched RIGHT NOW."""
    return _digest((AGENTS_DIR / f"{name}.md").read_bytes())


# The renewal command, once. THREE things have to agree about how a pin is
# taken — `_digest`, the sentence the record hands a maintainer, and the remedy
# this module prints when the audit goes red — and the third disagreed with the
# other two for a round: it said a bare `shasum -a 256`, which on a
# `core.autocrlf=true` checkout hashes the CRs that `_digest` drops. Following
# it records a pin that can NEVER satisfy the audit that printed it, so the
# remedy for a red guard leaves it red and the only way out is to stop
# believing the guard. A constant rather than a third copy of the string, and
# the record is checked against this one below.
RENEW_PIN = "tr -d '\\r' < .claude/agents/<name>.md | shasum -a 256"

# Verdict dates whose `**Model.**` field may read "Not recorded". A dispatch
# takes its model from the SESSION — neither definition carries a `model:`
# field — so the definition digest is blind to a model switch and to a change in
# the default, and a verdict stays green across both. The record now carries the
# model as a field, but the 2026-08-27 verdicts predate the requirement and
# nothing wrote it down at the time; inventing one would be the fabrication this
# whole apparatus exists to prevent.
#
# So the exemption is a NAMED, closed set rather than a default — the shape
# `GATE_ONLY_SKILLS` and `ENGINE_FREE_SKILLS` use elsewhere in this suite, and
# for the same reason: a verdict filed under any other date must state its model,
# and this list cannot grow without someone deciding that it should. Pin `model:`
# in a definition and the exemption stops applying to it at all — the field is
# inside the bytes the digest covers, so the switch becomes an edit and the edit
# already demands a re-run.
MODEL_UNRECORDED_DATES = frozenset({"2026-08-27"})


def _results_section(record: str, date: str):
    """Returns (body, problem) for the record's `## Results — <date>` section.

    The claim in CLAUDE.md carries a date, so the verdicts that back it are the
    ones filed under that date and no others. Reading verdicts from the whole
    record instead would let a superseded PASS keep vouching for a claim that
    has moved on — the same staleness these two audits exist to catch, one level
    up.

    EXACTLY ONE section may carry a date, and a second is a problem rather than
    a tie to break. Two runs on one calendar day is the ordinary way to get
    there — a replay that failed, a fix, a replay that passed — and if the later
    one is APPENDED, a `re.search` reads the first and both audits go on
    validating the superseded verdict. Picking the last instead would be a guess
    at document order that no rule here states, and it would be silent when
    wrong. So the ambiguity is reported: the record has to say which run the
    claim rests on, by replacing the section or by dating it distinctly.
    """
    if date is None:
        # A caller with no date has nothing to hold the record to, and reaching
        # `re.escape(None)` turned that into a TypeError three frames down —
        # unreadable, and raised by the guard rather than reported by it. The
        # withdrawn state is exactly when a date is absent, so the crash landed
        # on the one workflow this file went to the trouble of unblocking.
        return None, "no advertised date to check the record against"
    found = re.findall(rf"^## Results — {re.escape(date)}\s*$\n(.*?)(?=^## |\Z)",
                       record, re.M | re.S)
    if not found:
        return None, f"no `## Results — {date}` section in the record"
    if len(found) > 1:
        return None, (f"{len(found)} `## Results — {date}` sections in the "
                      "record — a claim naming one date cannot say which of "
                      "them backs it")
    # ...and the same ambiguity one level in. Both audits index the section's
    # verdicts by agent name, and a `dict` built from `re.findall` keeps the
    # LAST pair silently — so a regressed re-run written ABOVE the standing
    # verdict leaves a section that says FAIL and PASS for one reviewer and
    # resolves, in both audits at once, to the PASS. Recording a re-run in
    # place is the ordinary way to get there, and the collapse is the same
    # defect as the two-sections case: a record that says two things is a
    # record that has to be FIXED, never one to pick from. Reported here
    # because this function is the single owner of which verdicts back a
    # claim; gating it in one audit would have left the other collapsing.
    doubled = sorted({name for name in
                      re.findall(r"^### `([\w-]+)` — (?:PASS|FAIL)\b",
                                 found[0], re.M)
                      if re.findall(rf"^### `{re.escape(name)}` — (?:PASS|FAIL)\b",
                                    found[0], re.M)[1:]})
    if doubled:
        return None, (f"the `## Results — {date}` section files more than one "
                      f"verdict for {doubled} — a section carrying two verdicts "
                      "for one reviewer cannot say which of them stands")
    return found[0], None


def _unbacked(record: str, date: str, names) -> list[str]:
    """Agents CLAUDE.md advertises as accepted without a PASS filed under `date`.

    Module-level so the audit and its control share ONE definition. Restated in
    the control, the control would pass against its own copy while the audit ran
    on something else.

    A FAIL is a legitimate thing to record — it is the honest outcome of a
    regressed re-run — so it is not this that must go red. What must go red is
    the CLAIM standing over it: CLAUDE.md is the copy an agent has loaded when it
    decides whether to trust a reviewer, and it said "Both PASSED" against a
    record free to say otherwise.
    """
    section, problem = _results_section(record, date)
    if problem:
        return [problem]
    verdicts = dict(re.findall(r"^### `([\w-]+)` — (PASS|FAIL)\b", section, re.M))
    return [f"{n}: {verdicts.get(n, 'no verdict recorded')}"
            for n in sorted(names) if verdicts.get(n) != "PASS"]


# The PII gate's exempt token for this record, spelled exactly as
# `strip_artifact_digests` in scripts/pre-commit-pii-scan.sh spells it, and the
# 64-hex run that survives it. A `sed` cannot span a newline and neither can
# either of these, which is what makes the wrap and the stray digest ONE
# question rather than two.
PIN_TOKEN = re.compile(r"sha256 `[0-9a-f]{64}`")
LOOSE_DIGEST = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")


def _digest_the_gate_scans(text: str):
    """A 64-hex digest in `text` the PII gate would SCAN rather than strip.

    The gate's own question, asked with the gate's own rule: strip the exempt
    token, then look at what is left. Everything else in this module asks
    whether the pin is WELL FORMED, which is only half of it — a digest may be
    well formed and have a second one beside it.
    """
    m = LOOSE_DIGEST.search(PIN_TOKEN.sub("", text))
    return m.group() if m else None


def _pin_unstripped(body: str, digest: str):
    """Why the PII gate would scan a digest in `body`, or None.

    Checked from BOTH sides, and it was checked from one. The test was whether
    the ONE pin the caller found is WELL FORMED, so the renewal shape
    `sha256 `<new>` (was `<old>`)` passed here while the gate's per-token `sed`
    left `<old>` standing and counted its nine-digit run as an SSN. The hook
    selects this module and reports green; the commit is then blocked; and the
    check that would name the cause lives in tests/test_pii_content_scan.py,
    which the hook does not select, so its verdict arrives in CI AFTER the
    block. That is the same gap the paragraphs below claim to have closed, from
    the other side of the exemption.

    Two guards police these four pins and they had different ideas of what a
    line is. `_unpinned` and `_input_unpinned` flatten whitespace before matching
    — a pin is one sentence the record may wrap wherever it reads best, and it
    has been rewrapped once already — so a break anywhere inside it, the one
    after `sha256 ` included, still verifies. The PII gate's
    `strip_artifact_digests` is a per-LINE `sed`, so the token it drops must sit
    whole on one line; anything else is scanned like every other line in the
    file, which is deliberate and is what stops the exemption becoming a hiding
    place.

    Between those two readings sits the failure. An editor wraps a renewed pin
    after `sha256 ` — the `**Input.**` lines already wrap once, so it is one
    character of drift away — the hook runs the six modules and reports green,
    and then the commit is BLOCKED as an SSN, because a 64-character digest
    carries a run of exactly nine bare digits often enough to matter and Replay
    A's does. A digest without such a run commits and fails later in CI, in a
    test named after `requirements.txt`. Neither message names the wrap.

    So the record's own instruction — write a pin "in that one shape, on one
    line" — is checked here rather than left as prose. The gate stays the
    authority on the shape; this is the copy of the rule that fires FIRST and
    says why.

    First is the whole of what this adds. `tests/test_pii_content_scan.py`
    already reads the live record and rejects a digest written outside the
    exempt token — the same contract from the gate's side — but that module is
    not among the modules `.claude/hooks/skill-invariants.sh` selects, so its
    verdict arrives in CI, after the commit the wrap blocks. This module is
    selected by an edit to the record, so the wrap is reported before the
    renewal is committed rather than by the thing that refuses to commit it.
    """
    loose = _digest_the_gate_scans(body)
    if loose is None:
        return None
    if f"sha256 `{digest}`" not in body:
        return ("wrapped across lines, so the gate scans the digest instead of "
                "stripping it — write `sha256 `<64 hex>`` on ONE line")
    return (f"a digest ending {loose[-8:]} sits OUTSIDE the exempt "
            "`sha256 `<64 hex>`` token, so the gate scans it — a renewal that "
            "keeps the old digest beside the new one is the shape that does "
            "this; drop the old one")


def _unpinned(record: str, date: str, names) -> list[str]:
    """Agents whose verdict under `date` does not pin the bytes it judged.

    A definition is a system prompt: edit one and every recorded verdict about it
    becomes evidence about a reviewer that no longer exists, while the PASS and
    the CLAUDE.md claim resting on it both stay exactly as green. Every other
    check in this module reads a definition for its schema paths and tool grants,
    so an edit that keeps those intact — a reworded instruction, a loosened
    scope, a dropped rule — moves the agent's behaviour past all of them.

    What the pin shows is bounded, and the bound is the reason the fresh-session
    rule stays prose: it proves the verdict names the CURRENT bytes, never that a
    fresh session dispatched them. Nothing here can see the session a human ran.

    The MODEL is the half of "what was dispatched" that no digest reaches, the
    definitions naming none and a dispatch taking the session's. It is checked
    here as a recorded FIELD for the same reason the definition is pinned: it
    converts a silent inheritance into a line somebody writes and a reviewer
    reads. Where a definition does pin `model:`, the two must agree — a verdict
    naming a model the definition contradicts is describing a dispatch that
    cannot happen. The INPUT digest is checked where the fixtures are already
    built, in `TestTheAcceptanceFixtureIsActuallyCorrupt`: this function is pure
    text over the record and the control below calls it a dozen times, while
    materialising Replay A costs a `git show` and Replay B an exec of the
    generator.
    """
    section, problem = _results_section(record, date)
    if problem:
        return [problem]
    bodies = dict(re.findall(
        r"^### `([\w-]+)` — (?:PASS|FAIL)[^\n]*\n(.*?)(?=^### |\Z)",
        section, re.M | re.S))
    problems = []
    for name in sorted(names):
        flat = re.sub(r"\s+", " ", bodies.get(name, ""))
        m = re.search(rf"\*\*Definition\.\*\* `\.claude/agents/"
                      rf"{re.escape(name)}\.md`, sha256 `([0-9a-f]{{64}})`", flat)
        if not m:
            problems.append(f"{name}: verdict pins no definition digest")
        else:
            if m.group(1) != _live_digest(name):
                problems.append(f"{name}: pinned {m.group(1)[:12]}, "
                                f"live {_live_digest(name)[:12]}")
            unstripped = _pin_unstripped(bodies.get(name, ""), m.group(1))
            if unstripped:
                problems.append(f"{name}: the definition pin is {unstripped}")
        declared = _frontmatter(AGENTS_DIR / f"{name}.md").get("model")
        model = re.search(r"\*\*Model\.\*\* (?:(Not recorded)|`([\w.-]+)`)",
                          flat)
        if not model:
            problems.append(f"{name}: verdict records no model")
        elif model.group(1) and declared:
            problems.append(f"{name}: the definition pins model {declared!r} "
                            f"and the verdict names none")
        elif model.group(1) and date not in MODEL_UNRECORDED_DATES:
            problems.append(f"{name}: verdict records no model, and {date} is "
                            f"not in MODEL_UNRECORDED_DATES")
        elif model.group(2) and declared and model.group(2) != declared:
            problems.append(f"{name}: verdict names model {model.group(2)!r}, "
                            f"definition pins {declared!r}")
    return problems


def _input_unpinned(record: str, date: str, name: str, live: str):
    """`name`'s recorded INPUT pin under `date` against `live`, or None.

    A PREDICATE and not an inline comparison, for the reason every other audit
    here is one: a control has to be able to drive it over a doctored record. The
    first spelling compared the two digests inside the tests that build the
    fixtures, and nothing anywhere exercised the comparison — replacing it with
    `True` left the whole module green, which is this file's own subject arriving
    in the check written to close it.

    Separate from `_unpinned` rather than folded into it because answering it
    needs the fixture BUILT: Replay A costs a `git show` and Replay B an exec of
    the record's generator, while `_unpinned` is pure text and the control calls
    it a dozen times. So the caller builds, and this scores.
    """
    section, problem = _results_section(record, date)
    if problem:
        return problem
    bodies = dict(re.findall(
        r"^### `([\w-]+)` — (?:PASS|FAIL)[^\n]*\n(.*?)(?=^### |\Z)",
        section, re.M | re.S))
    flat = re.sub(r"\s+", " ", bodies.get(name, ""))
    m = re.search(r"\*\*Input\.\*\* [^*]*?sha256 `([0-9a-f]{64})`", flat)
    if not m:
        return (f"{name}: verdict pins no input digest, so the PASS is recorded "
                f"against a fixture nobody can identify")
    unstripped = _pin_unstripped(bodies.get(name, ""), m.group(1))
    if unstripped:
        # The same shape rule the definition pin is held to, and this is the
        # line likelier to break it: the `**Input.**` sentences already wrap
        # once, so a renewal that wraps one character earlier lands here.
        return f"{name}: the input pin is {unstripped}"
    if m.group(1) != live:
        return (f"{name}: verdict pins input {m.group(1)[:12]}, the replay "
                f"builds {live[:12]} — the fixture moved after the run that was "
                f"scored")
    return None


def _replay_sections(record: str):
    """Returns ({agent: body}, problem) for the record's `## Replay` sections.

    The `## Results` sibling above rejects a duplicate rather than picking from
    it, and this is the same question about the other half of the document: not
    "which verdict stands" but "which FIXTURE was scored". A revised replay
    appended for an agent already covered — a re-pinned SHA at season open is
    the ordinary way to get one — collapsed through `dict(re.findall(...))` to
    the LAST section for the marker check, while the two helpers reading the pin
    and the criterion `re.search` the whole file and got the FIRST. The record
    then certified one fixture and scored another, and the advertised PASS could
    not say which replay it covered. Both halves silent, in opposite directions,
    which is why picking either end is a guess and not a tie-break.

    Reported here because this is the single owner of which section answers a
    replay question. Gating it in the marker check alone would have left the pin
    and the criterion reading past it, exactly as gating the results collapse in
    one audit would have left the other one collapsing.
    """
    found = re.findall(r"^## Replay [A-Z] — `([\w-]+)`\n(.*?)(?=^## |\Z)",
                       record, re.M | re.S)
    doubled = sorted({name for name, _ in found
                      if [n for n, _ in found if n == name][1:]})
    if doubled:
        return None, (f"the record carries more than one `## Replay` section "
                      f"for {doubled} — a record holding two fixtures for one "
                      "reviewer cannot say which one its PASS was scored "
                      "against. Replace the superseded replay rather than "
                      "appending beside it")
    return dict(found), None


# What CLAUDE.md says INSTEAD of a pass claim, when a replay has failed and the
# reviewers are not currently accepted. A declared state, never an absence: the
# audits below are held to whatever CLAUDE.md advertises, and if silence counted
# as "nothing advertised" then deleting one sentence would retire the entire
# acceptance apparatus with a green suite — the vacuity every guard in this file
# exists to refuse. Saying it out loud costs one line and is reviewable.
UNCLAIMED = "**Neither reviewer is currently accepted**"


def _record_dates(record: str) -> list[str]:
    """Every date the record files a Results section under.

    The RECORD's own date, not the claim's. The control below audits `_unbacked`
    and `_unpinned`, which are functions of the record, and driving it from
    CLAUDE.md coupled it to a sentence that is allowed to be absent: in the
    withdrawn state `_claim_date()` is None, `re.escape(None)` raises, and the
    one state this file went to the trouble of making writable could not be
    committed after all. The fix for a workflow-blocking guard has to be
    committable in the state it unblocks, or it has moved the block rather than
    lifted it.
    """
    return re.findall(r"^## Results — (\d{4}-\d{2}-\d{2})\s*$", record, re.M)


def _code_spans_removed(text: str) -> str:
    """`text` with CommonMark code spans replaced by a space.

    A run of N backticks opens a span that only a run of EXACTLY N closes, so
    ``` ``a ` b`` ``` is one span holding a backtick and not two spans with prose
    between them. Written as one greedy pair of runs it closed at the INNER
    backtick and handed the rest of the span back as prose — and the rest of that
    span is where a quoted example puts its text. Quote the pass claim that way
    and CLAUDE.md is read as making two, or, with no live claim in the file at
    all, a historical example becomes the standing one and the record is held to
    a date nobody declared. Both directions, off one delimiter rule.

    An UNCLOSED run is literal text and stays, which is CommonMark's rule and
    the safe reading besides: a stray backtick cannot swallow the sentences
    after it.
    """
    out, i, n = [], 0, len(text)
    while i < n:
        if text[i] != "`":
            out.append(text[i])
            i += 1
            continue
        j = i
        while j < n and text[j] == "`":
            j += 1
        run, k, close = j - i, j, -1
        while k < n:
            if text[k] != "`":
                k += 1
                continue
            end = k
            while end < n and text[end] == "`":
                end += 1
            if end - k == run:
                close = k
                break
            k = end
        if close < 0:
            out.append(text[i:j])
            i = j
        else:
            out.append(" ")
            i = close + run
    return "".join(out)


def _prose(text: str) -> str:
    """`text` with fenced blocks and inline code spans removed, flattened.

    A file that DOCUMENTS a sentence is not a file that says it, and the audit
    below could not tell the two apart. The record and both of its own messages
    tell a maintainer whose replay failed to write `**Neither reviewer is
    currently accepted**` into CLAUDE.md — and writing that instruction there,
    inside the backticks that mark it as a quotation, made the file "state" both
    the claim and its withdrawal at once: three tests red and the tool-time hook
    blocking the edit, on a document making exactly one claim. A remedy that
    cannot be described in the file it is a remedy for is the same failure as one
    that cannot be committed, which is the state this function already has a
    branch for.

    Fences first, because a fence owns whole lines; then the flatten; then the
    inline spans, because a span may wrap a line break and would not match
    otherwise. The FENCE closing rule is CommonMark's — same character, at least
    as long, no info string — so a four-backtick fence quoting a three-backtick
    one is not closed by its content; the SPAN rule is CommonMark's too and lives
    in `_code_spans_removed`, which is where a span holding a backtick used to
    leak half of itself into the prose.

    `test_preflight.py` carries the same fence rule for a different question, and
    the two are separate deliberately: neither test module may import the
    other's helpers, and a third copy is the point at which it moves to
    `tests/skill_text.py` instead.
    """
    kept, opener = [], None
    for line in text.splitlines():
        fence = re.match(r"^\s*(?P<mark>```+|~~~+)(?P<info>.*)$", line)
        if fence:
            mark = fence.group("mark")
            if opener is None:
                opener = mark[0], len(mark)
            elif (mark[0] == opener[0] and len(mark) >= opener[1]
                    and not fence.group("info").strip()):
                opener = None
            continue
        if opener is None:
            kept.append(line)
    return _code_spans_removed(re.sub(r"\s+", " ", "\n".join(kept)))


def _acceptance_claim(text: str | None = None):
    """Returns (date, problem) for the acceptance CLAUDE.md advertises.

    `(None, None)` is the third outcome and the one that took a review round to
    notice was missing: the reviewers are declared NOT accepted. The audits below
    told a maintainer whose replay had failed to "take the claim out of
    CLAUDE.md", and doing exactly that turned the suite red, because absence was
    read as a parse failure. A documented remedy that cannot be committed green
    is not a remedy — it is pressure to record a PASS that did not happen, aimed
    at the one person following the procedure honestly.

    CLAUDE.md, not the record, is the load-bearing copy: it is what an agent has
    in context when it decides to trust a report, and the record is a document
    that agent may never open. So the claim is the thing the audits below hold
    the record to, rather than the other way round.

    EXACTLY ONE claim may stand, for the reason `_results_section` requires
    exactly one dated section, and with a worse ending. A re-run APPENDED rather
    than substituted leaves `re.search` returning the OLDER claim, so both audits
    hold the record to a date it does back — and pass — while the date the file
    now advertises to every agent that loads it was never checked against
    anything. Unlike the duplicate-section case there is not even a stale PASS
    behind the winning claim to make the two verdicts nearly alike: the second
    date is unbacked entirely, and the green is bought by not reading it.

    Takes the text so a control can feed it a doctored one. Reading CLAUDE.md
    proves nothing about the parser — there is one copy, and it says what it says.
    """
    if text is None:
        text = (REPO / "CLAUDE.md").read_text()
    flat = _prose(text)
    found = re.findall(r"\*\*Both PASSED (\d{4}-\d{2}-\d{2})\*\*", flat)
    unclaimed = UNCLAIMED in flat
    if found and unclaimed:
        # Checked BEFORE the date is returned, because returning it first is what
        # made this the quiet failure rather than the loud one. Withdrawing an
        # acceptance means REPLACING the claim; a maintainer who adds the
        # withdrawal and leaves the old sentence standing has written both trust
        # signals into the file an agent loads, and the audits went on validating
        # the stale PASS — green, on a CLAUDE.md that says two opposite things.
        return None, (
            f"CLAUDE.md states `**Both PASSED {found[0]}**` AND `{UNCLAIMED}`. "
            "Those are the two halves of one fact and an agent reads whichever "
            "it reaches first — withdraw the acceptance by REPLACING the claim, "
            "not by adding a second sentence beside it")
    if len(found) == 1:
        return found[0], None
    if not found:
        if unclaimed:
            return None, None
        return None, (
            "CLAUDE.md states neither `**Both PASSED <date>**` nor "
            f"`{UNCLAIMED}`. That sentence is the trust signal — the one an "
            "agent has loaded when it decides whether a report from these "
            "reviewers means anything — so if it has been reworded, reword "
            "`_acceptance_claim` with it. If a replay genuinely FAILED, say so "
            f"by writing `{UNCLAIMED}`: an unstated claim and an unbacked one "
            "are not the same thing, and the not-accepted state has to be "
            "WRITABLE or the honest outcome is the one nobody can commit")
    return None, (
        f"CLAUDE.md makes {len(found)} `**Both PASSED <date>**` claims "
        f"({', '.join(found)}) — an agent reads whichever it reaches first, so "
        "there is no fact here for the record to be held to. Replace the old "
        "claim rather than appending the new one")


class TestAgentDefinitions:
    def test_the_expected_agents_are_present(self):
        """Explicit, like the skill registry it mirrors. An empty
        `.claude/agents/` would satisfy every other assertion in this module by
        having nothing to check, so the set is named rather than globbed."""
        assert {f.stem for f in _agent_files()} == EXPECTED_AGENTS

    def test_every_agent_declares_an_explicit_tool_allowlist(self):
        """The load-bearing one, and the reason the three grant checks below are
        not vacuous.

        An agent definition with NO `tools:` field inherits every tool — Write
        and Edit included. The grant assertions each work by intersecting the
        declared set with a forbidden set, so a deleted `tools:` line makes them
        all pass against an agent that can now do anything. The permissive
        default has to be forbidden here, or the checks silence themselves at
        exactly the moment the grant becomes dangerous.
        """
        for f in _agent_files():
            assert _tools(f), (
                f"{f.name} declares no `tools:` — which grants ALL tools, and "
                "turns every grant assertion in this file into a no-op")

    def test_frontmatter_values_have_no_unquoted_colon(self):
        """Same construct, same reason, as the skills' check: an unquoted scalar
        containing ': ' is a YAML parse error, and a definition that fails to
        parse disappears with no message."""
        for f in _agent_files():
            block = f.read_text().split("---\n", 2)[1]
            for line in block.splitlines():
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                key, _, value = line.partition(":")
                v = value.strip()
                assert not (v and v[0] not in "\"'" and ": " in v), (
                    f"{f.name}: unquoted colon in {key.strip()!r}")

    def test_frontmatter_name_matches_the_filename(self):
        for f in _agent_files():
            assert _frontmatter(f).get("name") == f.stem, (
                f"{f.name}: frontmatter name must equal the filename stem — "
                "that is what the agent is invoked by")

    def test_every_description_says_when_to_use_the_agent(self):
        """Same reasoning, and the same literal, as the skills' `Use when` rule
        in test_portability.py: the description is the only text loaded before
        selection, so an agent described purely by what it does gives the model
        nothing to trigger on and is never selected. The exact phrase is
        required rather than any wording that conveys a trigger, because a
        phrasing-tolerant check is one reword away from asserting nothing."""
        for f in _agent_files():
            desc = _frontmatter(f).get("description", "")
            assert desc, f"{f.name} has no description"
            assert "use when" in desc.lower(), (
                f"{f.name}: description has no 'Use when ...' trigger clause")

    def test_tool_grants_are_real_tool_names(self):
        for f in _agent_files():
            unknown = _tools(f) - KNOWN_TOOLS
            assert not unknown, (
                f"{f.name} grants {sorted(unknown)}, which are not tool names. "
                "A misspelt grant is silent: the agent simply does not have the "
                "capability its prompt assumes.")

    def test_every_agent_is_covered_by_one_acceptance_regime(self):
        """The two sets partition `.claude/agents/`, and neither may be empty.

        A file in neither set is an agent nothing decides how to accept — it
        passes every grant check below and is covered by no record and no
        boundary assertion, which is the state each of those was written to make
        impossible."""
        assert REVIEW_AGENTS & WORKER_AGENTS == set()
        assert REVIEW_AGENTS and WORKER_AGENTS
        # The completeness half is `test_the_expected_agents_are_present`, whose
        # `EXPECTED_AGENTS` is this union; restating it here made one edit turn
        # two tests red and said nothing the first did not.

    def test_review_agents_cannot_write(self):
        """A reviewer that can edit can resolve its own findings, and a finding
        that was quietly fixed is a finding nobody reviewed. Both agents here
        report; the human decides."""
        for f in _agent_files():
            granted = _tools(f) & WRITE_TOOLS
            assert not granted, (
                f"{f.name} may write ({sorted(granted)}) — these agents audit "
                "and report, and an auditor that edits its own subject cannot "
                "be trusted about it")

    # The most a networked agent may hold. Stated as a closed set rather than as
    # another forbidden list, so that this stays checkable now that `Bash` is
    # forbidden for every agent here and a no-shell assertion could no longer
    # fail on its own. `TodoWrite`, `AskUserQuestion` and anything added later
    # have to be admitted deliberately.
    NETWORK_AGENT_MAX = {"Read", "Grep", "Glob", "WebSearch", "WebFetch"}

    def test_a_networked_agent_is_held_to_a_minimal_tool_set(self):
        """AGENTS.md §1 keeps network access away from anything touching
        taxpayer data. The freshness auditor is the only agent here that needs
        the web, and its input — `tax_law_<year>.json` — holds published rates
        and no PII. Granting it Bash as well would put an arbitrary shell and
        an outbound connection in the same context inside a workspace holding a
        real return, which is precisely the combination that rule exists to
        prevent. Least privilege is cheap here: it reads one JSON file.

        Named for what it actually enforces. `Read` still reaches every file in
        the workspace, and only the prompt narrows that — so this bounds the
        agent's capabilities rather than making it unable to see taxpayer data,
        and a name claiming the latter would be the stronger guarantee nobody
        checked.
        """
        for f in _agent_files():
            tools = _tools(f)
            if not (tools & NETWORK_TOOLS):
                continue
            excess = sorted(tools - self.NETWORK_AGENT_MAX)
            assert not excess, (
                f"{f.name} has network access and also {excess}; a networked "
                "agent in this workspace is held to reading one JSON file")


class TestTheParseWorkerBoundaryIsDrawnByAbsence:
    """V6. The parse worker's acceptance is not a replay, because it forms no
    judgement to replay — it is trusted not to be ABLE to do anything but read
    the text it was handed. That is checkable here, from the grant.

    The measurement that shaped the grant — which host mechanisms fail closed and
    which do not — is stated once, with its date and build, in
    `docs/ops/review-subagent-acceptance.md`. It is not restated here: this
    repository has been bitten enough times by one quantity kept in several
    places that `skill-invariants.sh`'s MEASURED COSTS block exists for it, and a
    host fact copied into a test docstring goes stale exactly as quietly.

    Its conclusion is the design: one mechanism fails closed, and it is not
    granting the capability. Everything below asserts an absence.
    """

    # A CLOSED set, deliberately, and not another forbidden list. The forbidden
    # lists in this module exist where an agent needs real capabilities and the
    # question is which; here the answer is "as close to none as launches", so
    # the checkable form is the whole allowed set. A tool added to Claude Code
    # cannot quietly become available to this worker — it has to be admitted on
    # this line, by someone who has to say why a parse worker needs it.
    #
    # `Glob` is the grant and it is a FLOOR, not a need. An empty resolved tool
    # set is refused at launch, so the grant cannot be nothing, and `Glob` is the
    # weakest capability VERIFIED to work here: it matches path names and returns
    # no file content, so it is neither a read of contents, nor a write, nor a
    # request. The prompt tells the worker it has no use for it.
    #
    # The residual, stated rather than implied: `Glob` can enumerate path names,
    # and a listing would reach the summary. That is reconnaissance with a local
    # sink — the worker cannot send it anywhere — and it is the price of the
    # launch floor. A tool that touches nothing at all would be better, and
    # `TodoWrite` looked like one until the harness rejected the name.
    WORKER_MAX = {"Glob"}

    def _worker(self):
        (path,) = [f for f in _agent_files() if f.stem in WORKER_AGENTS]
        return path

    def test_the_worker_holds_nothing_that_reads_writes_runs_or_fetches(self):
        granted = _tools(self._worker())
        assert granted, "an empty grant fails to launch; see WORKER_MAX"
        excess = sorted(granted - self.WORKER_MAX)
        assert not excess, (
            f"tax-parse-worker holds {excess}. Its whole boundary is that it "
            "holds no capability — a document's injected text cannot make it "
            "fetch, read or write something it has no tool for, and nothing "
            "else on this build fails closed")

    def test_the_worker_grants_only_a_tool_verified_to_launch(self):
        """The assertion this module did not have, and the one that would have
        caught a worker nobody could dispatch.

        `TodoWrite` was in `KNOWN_TOOLS` as a real tool name. The harness rejects
        it — the tool list resolves to nothing and the spawn is REFUSED — so the
        definition passed every check here and the agent did not exist. A name
        this repository has not watched launch is not evidence about anything."""
        assert _tools(self._worker()) <= VERIFIED_TOOLS, (
            "the parse worker may hold only a tool dispatched and seen to "
            "launch on this host; an unverified grant is how a boundary agent "
            "becomes an agent that cannot be spawned")

    def test_it_reads_no_file_and_the_definition_says_why(self):
        """The grants a reader would assume are safe, and the two reasons they
        are not: `Read` is not path-confined on this build, and reading a
        document directly bypasses the extraction masking — the P0 V0 fixed.
        `Glob` is excluded from this bar because it returns no content; the
        launch floor forces it and the definition says so."""
        granted = _tools(self._worker())
        assert not (granted & {"Read", "Grep"})
        assert "Do not call it" in self._worker().read_text(), (
            "the one tool it holds is a launch floor, and the prompt has to say "
            "the worker has no use for it")
        body = self._worker().read_text()
        assert "NOT to be confined" in body or "not confined" in body, (
            "the definition must say Read is unconfined and point at the probe "
            "— the next maintainer's first instinct is to grant it")
        assert "review-subagent-acceptance.md" in body, (
            "and it must NAME the record rather than restate the measurement")
        assert "full" in body and "SSN" in body

    def test_it_declares_the_document_text_to_be_data(self):
        """The prompt-level half, which still matters: the worker cannot ACT on
        an injected instruction, but it can still be fooled into transcribing a
        wrong figure, and the note it writes is how a taxpayer learns a document
        they were sent carried one."""
        body = self._worker().read_text()
        assert "IS DATA" in body or "is data" in body.lower()
        assert "Notes" in body, "an injection has to be reported, not just ignored"

    def test_both_modes_specify_the_same_summary_shape(self):
        """The summary feeds `parse_ledger.json` and `tax-compute`. Written out
        twice — once in the worker's prompt, once in the reference the sequential
        path follows — the two had already drifted by a clause, so ENFORCED and
        SEQUENTIAL could emit differently-shaped summaries of the same return."""
        def block(text: str) -> str:
            after = text.split("# {Group Name} — Tax Year {year}", 1)[1]
            return after.split("```", 1)[0].strip()

        worker = block(self._worker().read_text())
        reference = block((REPO / ".agents" / "skills" / "tax-parse"
                           / "references" / "parse-task-delegation.md").read_text())
        assert worker == reference, (
            "the worker's summary format and the reference's have diverged:\n"
            f"worker:\n{worker}\n\nreference:\n{reference}")

    BOUNDARY_DOC = REPO / "docs" / "contributing" / "parse-boundary.md"

    def test_every_skill_that_parses_an_untrusted_file_names_the_boundary(self):
        """The sweep, and the shape it settled on.

        V6 hardened `tax-parse` and left two siblings parsing untrusted files
        under the same six prompt-level rules — a rule the delegation reference
        then stated and they broke. A finding's scope is where the defect lives,
        not where it was spotted.

        What each skill must carry is the POINTER and the worker's name, not the
        contract: three copies of it drifted inside a single round, one saying
        the worker returns markdown while its own skill said JSON."""
        for name in PARSING_SKILLS:
            text = skill_text(name)
            assert "parse-boundary.md" in text, (
                f"{name} parses an untrusted file and does not point at the "
                "boundary contract; a copy of it in each skill is what drifted")
            assert "tax-parse-worker" in text, (
                f"{name} names no enforced worker")

    def test_the_boundary_contract_states_the_banner_and_the_fence_once(self):
        """Both are LITERAL — an agent copies them — and both are the whole
        distinction between an enforced parse and an unenforced one. Pinned
        exactly, in the one file that holds them, so a reword is a decision.

        The fence rule is pinned on the phrase that carries the claim rather than
        on the word `marker`: a skill saying "use a fixed marker" would satisfy
        any looser check, and a fixed marker is precisely the attack."""
        banner = ("> Parse boundary NOT enforced — parsed in the main session "
                  "under prompt-level\n> rules, because this agent cannot give a "
                  "sub-task a closed tool set.")
        contract = self.BOUNDARY_DOC.read_text()
        assert banner in contract, "the verbatim banner is not in the contract"
        assert "a fresh random marker\nper dispatch" in contract, (
            "the contract must require a PER-DISPATCH marker — a fixed one lets "
            "a file close its own data frame")
        # And every skill EMITS it, so every skill carries it verbatim: the
        # banner travels with a copied skill directory and this file does not.
        off = [n for n in PARSING_SKILLS if banner not in skill_text(n)]
        assert not off, (
            f"these skills state the banner differently or not at all: {off}. "
            "It is the only artifact distinguishing an enforced parse from an "
            "unenforced one, and a reworded one reads as a different guarantee")

    def test_enforced_mode_says_the_PARENT_extracts(self):
        """The contradiction each skill had for one revision: a mode block
        saying the worker holds no tools, and a prompt four lines later telling
        it to run `parse_utils.py`. A rule the step beside it contradicts is one
        an agent learns to reason past — this repository has a name for that."""
        for name in PARSING_SKILLS:
            text = skill_text(name)
            assert "never touches" in text or "does the extraction" in text, (
                f"{name}'s enforced mode does not say the PARENT extracts; a "
                "worker with no file tool cannot run the command its own "
                "prompt names")

    def test_the_delegation_reference_still_exists(self):
        """The one thing the sweep above does not cover: `tax-parse`'s dispatch
        detail is a reference file, and `skill_text` folding it into the skill
        would read a deleted one as a shorter skill rather than a missing step."""
        assert (REPO / ".agents" / "skills" / "tax-parse" / "references"
                / "parse-task-delegation.md").is_file()


class TestFreshnessAuditorMatchesTheSchema:
    """The auditor's checklist is only useful if the fields it names exist."""

    AUDITOR = AGENTS_DIR / "tax-law-freshness-auditor.md"

    # Backticked, fully-qualified paths only. A bare `salt_cap` in prose is
    # ambiguous, so the agent doc spells these out — which is what makes them
    # checkable. Stated as a limit rather than papered over: an unqualified
    # mention is NOT covered here.
    PATH_RE = re.compile(r"`((?:federal|state)\.[A-Za-z0-9_.]+)`")

    def _schema(self) -> dict:
        law = create_empty_tax_law(2026)
        law["state"] = create_state_template("CA", 2026)
        return law

    def _resolve(self, schema: dict, dotted: str):
        node = schema
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return None, False
            node = node[part]
        return node, True

    def test_the_auditor_names_paths_that_exist_in_the_schema(self):
        text = self.AUDITOR.read_text()
        paths = sorted(set(self.PATH_RE.findall(text)))
        assert paths, (
            "no schema paths found in the auditor prompt — either the checklist "
            "is gone or it stopped using backticked fully-qualified paths, and "
            "either way this test has stopped checking anything")
        schema = self._schema()
        missing = [p for p in paths if not self._resolve(schema, p)[1]]
        assert not missing, (
            f"the freshness auditor tells the agent to check {missing}, which "
            "do not exist in create_empty_tax_law / create_state_template. It "
            "would look where it was told, find nothing, and report an absence "
            "that is really a renamed key.")

    def test_the_fields_called_empty_on_arrival_really_are(self):
        """`irmaa.tiers` ships empty and `salt_cap` ships unset — the auditor
        tells the user to confirm they were FETCHED. If a future schema seeded
        them, that instruction would send people chasing a value that is
        already there, and the genuinely-unfetched case would look identical."""
        schema = self._schema()
        tiers, ok = self._resolve(schema, "federal.irmaa.tiers")
        assert ok and all(not v for v in tiers.values()), (
            "federal.irmaa.tiers is no longer empty on arrival")
        salt, ok = self._resolve(schema, "federal.salt_cap")
        assert ok and salt.get("amount") is None, (
            "federal.salt_cap now ships with a value")

    def test_a_clean_report_requires_full_cache_verification(self):
        """The drift-prone shortlist is emphasis, not the audit boundary.

        Ordinary brackets and deductions are just as capable of carrying a
        prior-year value, so checking only the novel indexed fields can certify
        a partially stale cache.
        """
        text = self.AUDITOR.read_text().lower()
        assert "walk the entire cache schema" in text
        assert re.search(r"every\s+populated\s+tax-law\s+figure", text)
        assert "cannot be clean" in text

    def test_filename_year_must_match_payload_year_before_audit(self):
        """A copied cache cannot be certified under its stale embedded year."""
        text = self.AUDITOR.read_text().lower()
        assert "filename/payload year identity" in text
        assert re.search(
            r"tax_law_<year>\.json.{0,120}top-level `tax_year`", text, re.S
        )
        assert "mismatched payload year is **incomplete**" in text
        assert "hard\n   stop" in text

    def test_a_clean_report_rejects_populated_blocks_without_source_pins(self):
        """Live verification cannot replace the cache's chain of custody.

        Checking only the `_sources` entries that exist passes vacuously when a
        populated fetched block has no entry at all.
        """
        text = self.AUDITOR.read_text().lower()
        assert "source-pin completeness" in text
        assert "derive the fetched blocks from the schema" in text
        assert "unpinned" in text
        assert re.search(
            r"populated fetched block unpinned cannot\s+receive a clean\s+report",
            text,
        )
        assert "apply the stop rule" in text

    def test_a_canonical_no_tax_state_still_requires_a_source_pin(self):
        """A template-equal type:none block is still an affirmative law claim."""
        text = self.AUDITOR.read_text().lower()
        assert "single-state block whenever it is populated" in text
        assert '`type: "none"` state block' in text
        assert "even when it equals the `create_state_template` seed" in text

    def test_florida_sales_tax_requires_both_provenance_pins(self):
        """The general-rate pin cannot stand in for the county-rate pin.

        The fetcher treats those values as separately sourced blocks.  An
        auditor that checks only the generic state pin can certify the exact
        legacy cache that check_freshness rejects as INCOMPLETE.
        """
        text = self.AUDITOR.read_text()
        assert '_sources["state.FL"]' in text
        assert '_sources["state.FL.county_surtax_rate"]' in text
        assert re.search(
            r"Florida.{0,300}require(?:s|d)?\s+both.{0,300}separate",
            text,
            re.DOTALL,
        )

    def test_generic_estimate_states_may_omit_the_regime_type(self):
        """The auditor must accept the same generic shape as freshness does."""
        text = self.AUDITOR.read_text().lower()
        assert "modeled typed states" in text
        assert "generic bracket-estimate state blocks" in text
        assert "intentionally omit `state.type`" in text
        assert "must not be rejected" in text
        assert "colorado" in text

    def test_source_authority_matches_the_fetcher_policy(self):
        """The auditor must not reject a source the canonical cache accepts."""
        text = self.AUDITOR.read_text().lower()
        assert "_source_host_authoritative" in text
        for domain in _AUTHORITATIVE_NONGOV_DOMAINS:
            assert domain in text, (
                f"freshness auditor omits authoritative exception {domain}")

    def test_multi_period_fields_are_checked_against_their_effective_periods(self):
        """Not every cache leaf describes the return's filing year.

        Form 2210 crosses into TY+1 and PFIC computations consume rates from
        prior calendar years. Requiring either source to name the filing year
        rejects correct, schema-required data and makes a clean audit
        impossible whenever those optional blocks are populated.
        """
        text = self.AUDITOR.read_text().lower()
        assert "`federal.underpayment_rates`" in text
        assert re.search(
            r"underpayment_rates.{0,220}calendar quarter.{0,100}effective_from",
            text,
            re.DOTALL,
        )
        assert re.search(
            r"coverage through the filing deadline\s+in ty\+1 is expected", text
        )
        assert "`federal.historical_top_rate`" in text
        assert re.search(
            r"historical_top_rate.{0,180}calendar-year\s+key", text, re.DOTALL
        )
        assert re.search(
            r"ca `mental_health_surcharge\.rate` and `?\.threshold`?.{0,220}"
            r"r&tc §17043", text, re.DOTALL
        )
        assert "statutory-fixed, non-indexed constants" in text
        assert "do not require the statute to repeat each future" in text
        assert "these three are the only period exceptions" in text

    def test_bias_reviewer_allows_the_governed_ca_surcharge_constants(self):
        """The reviewer must share AGENTS.md's narrow hardcoded-law exception."""
        text = (AGENTS_DIR / "bias-direction-reviewer.md").read_text().lower()
        assert "ca `mental_health_surcharge` rate and threshold" in text
        assert "r&tc" in text and "§17043" in text


class TestTheAcceptanceFixtureIsActuallyCorrupt:
    """`docs/ops/review-subagent-acceptance.md` validates each reviewer by
    replay, and a replay is only evidence while its INPUT still holds the defect
    the reviewer is scored on finding. There are TWO inputs, and for a while only
    one of them was checked.

    Replay B hands the freshness auditor a cache carrying the PRE-OBBBA SALT cap
    under a covered payload year and requires the agent to name it. Replay A
    hands the bias reviewer a historical `tax_computations.py`, pinned by SHA,
    and requires it to name the missing Form 8995 overall cap. The SHA is the
    fixture — point it at the FIX rather than at its parent, or at any revision
    that never carried the defect, and the reviewer\'s honest empty report is the
    correct answer to a clean file. Every test, digest and recorded PASS stays
    green, which is this class\'s whole subject arriving through the door nobody
    was watching.

    That proves something only while the two figures actually differ. Nothing in
    the acceptance run itself can tell "the auditor caught the plant" from "there
    was nothing to catch" — a clean report is the correct answer to an uncorrupted
    cache, and it reads exactly like the failure this whole procedure exists to
    detect. So the plant is DERIVED from `ptet._salt_cap` rather than typed into
    the doc, and this pins the gap: when the statutory schedule changes, the
    acceptance goes red here instead of going quietly vacuous over there.

    The tests after it guard the other halves of the same property, each a
    different way for the record to prove nothing while reading exactly like a
    record that does: one that has quietly stopped covering an agent, one whose
    procedure has lost the fresh-session step, one whose PASS describes a
    definition since edited, and one that CLAUDE.md advertises past.
    """

    RECORD = REPO / "docs" / "ops" / "review-subagent-acceptance.md"

    REPLAY_A = "bias-direction-reviewer"

    REPLAY_B = "tax-law-freshness-auditor"

    # The wall-clock bound on every `git` call below. Generous because a cold
    # object read on a large repository is not instant, and finite because
    # `git show` on a blobless clone is a NETWORK fetch: against a remote that
    # is gone it waits on the transport, and a test that waits inside a
    # PostToolUse hook is a hook cancelled at its own limit, which the caller
    # cannot tell from a pass. A BUDGET, which CLAUDE.md's one-owner rule for
    # timings exempts as a threshold the code compares against: chosen far
    # above any healthy read rather than derived from MEASURED COSTS, so it is
    # reached only by something that is not answering the question.
    GIT_TIMEOUT = 60

    def _replay_a_body(self):
        """Replay A's own section, by the agent it replays.

        By AGENT and not by the letter: the letter is where the section sits and
        can be renumbered, while the reviewer a fixture was built to catch is
        what the pin and the criterion below are about. Scoped at all because
        both of them used to `re.search` the WHOLE record for a pin and four
        figures that appear once today — a coupling to document order that reads
        as a rule and is not one. A second `git show` anywhere above Replay A,
        in an intro or a fetch note, and the criterion would have been scored
        against a file the reviewer was never handed.
        """
        sections, problem = _replay_sections(self.RECORD.read_text())
        assert problem is None, problem
        assert self.REPLAY_A in sections, (
            f"no `## Replay` section for {self.REPLAY_A} — see "
            "test_the_acceptance_doc_covers_every_agent_that_exists, which owns "
            "that failure and says what a replay has to contain")
        return sections[self.REPLAY_A]

    def _replay_a_pin(self):
        """The revision and path Replay A copies, READ out of the record.

        Kept here as a constant instead, this test would go on checking the
        commit the record USED to name — green against a fixture nobody feeds
        the reviewer any more, which is the failure one level up from the one it
        is guarding.

        Read out of the fenced RECIPE, not out of the section. Scoping to the
        section closed one route to the wrong pin and left the other open: the
        first `git show` anywhere in Replay A won, and Replay A is prose about a
        commit — a comparison line, a note on the revision this one replaced, a
        worked example of the search that found it — any of which is a `git
        show` a reader would write and none of which is the input the reviewer
        is handed. The recipe is the thing a human runs, so the recipe is what
        the guard reads, and a SECOND pin in there is refused rather than picked
        between: a recipe naming two blobs cannot say which one was scored.
        """
        recipe = re.search(r"```sh\n(?P<body>.*?)\n```", self._replay_a_body(),
                           re.S)
        assert recipe, (
            "Replay A no longer carries a fenced `sh` recipe — this test reads "
            "the pin from the command a human actually runs, so a reformat that "
            "moves it has to be noticed rather than silently skipped")
        pins = re.findall(r"git show\s+([0-9a-f]{7,40}):(\S+)",
                          recipe.group("body"))
        assert len(pins) == 1, (
            f"Replay A\'s recipe names {len(pins)} `git show <sha>:<path>` "
            f"inputs ({pins}) — one is the fixture the PASS was scored against "
            "and this test cannot say which, so name exactly one")
        return pins[0]

    def _replay_a_criterion(self):
        """The function the pass criterion names and the four figures it states.

        Read rather than restated for the same reason the years are: the
        criterion is what a human scores the replay against, and a test carrying
        its own copy of the numbers cannot tell a correct record from one whose
        arithmetic has been edited out from under the fixture.
        """
        # Flattened first: the criterion is a paragraph, so every phrase this
        # reads is one line break away from matching nothing.
        text = re.sub(r"\s+", " ", self._replay_a_body())
        target = re.search(r"`(\w+)` omits the Form 8995 overall cap", text)
        figures = re.search(
            r"\$([\d,]+) QBI against \$([\d,]+) taxable income returns a "
            r"\$([\d,]+) deduction where line 14 allows \$([\d,]+)", text)
        assert target and figures, (
            "Replay A's pass criterion no longer states the function it scores "
            "and the four figures that demonstrate it — `<name>` omits the Form "
            "8995 overall cap, then `$<qbi> QBI against $<income> taxable "
            "income returns a $<overstated> deduction where line 14 allows "
            "$<allowed>`. Reword the sentence around that shape rather than "
            "through it: this test scores the fixture against those numbers")
        return (target.group(1),
                *(float(g.replace(",", "")) for g in figures.groups()))

    def _historical_module(self, sha, path):
        """The pinned file, executed in a namespace of its own.

        A subprocess, which the rest of this module avoids — see the renewal
        test's note on `shasum`. `git` is different in kind from that: it is
        required by every workflow in this repository and by CI's own checkout,
        where `shasum` is a perl script whose presence is a platform question.
        Four `git` calls at most, and only a FAILING read pays for the last
        three — `show`, then `cat-file`, `rev-parse` and one `config` read to
        tell a pin the clone cannot reach from one that is simply wrong. Against
        skill-invariants.sh MEASURED COSTS this module is one of the four cheap
        rows, which is why a call is worth counting here at all.

        Every call carries a TIMEOUT, because `git show` here is not necessarily
        local. A blobless clone (`--filter=blob:none`) has the commit and not the
        blob, so reading one is a network fetch from the promisor remote — and an
        unreachable remote is a test that blocks for as long as the transport
        waits, inside a pytest run a PostToolUse hook is holding a tool call
        open for. A timeout makes that a failure that says so; without one it is
        a hook cancelled at its own limit, which reads exactly like a pass.

        A FAILURE, and it has to be, which the first spelling of this got wrong
        in the one way that costs the whole guard: both timeout arms called
        `pytest.skip`. A skip reads exactly like a pass — the sentence above,
        arriving one line later — so on the slow-promisor clone the timeout was
        written for, the pin check silently stopped running. The environments
        that genuinely cannot answer are diagnosed from GIT facts below, never
        from a clock, and that is what keeps this from crying wolf on a slow
        machine: nothing here times out on a repository whose objects are local.

        BYTES, not `text=True`, and the reason is the digest three lines down.
        Universal-newline decoding rewrites a lone `\r` to `\n`, while the
        renewal command this record documents — `git show ... | tr -d '\r' |
        shasum` — DELETES it, so the two disagree on any blob holding a bare CR
        and the pin taken by the documented recipe could never satisfy the guard
        that printed it. `_digest` drops CRs for exactly this reason and cannot
        drop one that has already become a newline. The current pin holds none,
        which is what made the disagreement invisible.
        """
        try:
            show = subprocess.run(["git", "show", f"{sha}:{path}"], cwd=REPO,
                                  capture_output=True, timeout=self.GIT_TIMEOUT)
        except OSError:                     # no git on PATH: cannot answer
            pytest.skip("git is unavailable, so Replay A's pin cannot be read")
        except subprocess.TimeoutExpired:
            # A FAILURE, and the docstring above says why: without a timeout
            # this is a hook cancelled at its own limit, which reads exactly
            # like a pass. A skip reads exactly like a pass too. Written as one
            # it undid the whole reason the timeout was added — on a blobless
            # clone with a slow promisor, which is the ONE environment the
            # timeout exists for, the Replay A pin guard stopped running and
            # reported nothing but a number in pytest's skip count.
            #
            # Unlike the shallow and blobless arms below, this is not an
            # environment that cannot answer. It is one that did not answer in
            # time, and the difference is that those two are diagnosed from git
            # facts while this is diagnosed from a clock. A slow transport and a
            # hung one are indistinguishable here, so the guard says what it
            # knows and leaves the reader to decide.
            pytest.fail(f"`git show {sha}:{path}` did not return within "
                        f"{self.GIT_TIMEOUT}s, so Replay A's pin was not "
                        "checked. On a blobless clone this read is a fetch "
                        "from the promisor remote — run it once by hand, or "
                        "fetch the blob, and re-run")
        source = show.stdout.decode("utf-8").replace("\r", "")
        if show.returncode:
            # A pin the clone cannot REACH and a pin that does not EXIST are
            # different facts and only one of them is the record's fault. CI
            # checks out full history for this job precisely so the guard runs
            # there; a contributor's `--depth` clone is an environment that
            # cannot answer, and a red suite for it is a gate crying wolf.
            #
            # Shallowness alone does not say WHICH, and taking the skip on it
            # made every `git show` failure an environment problem. `git show`
            # also fails on a misspelled PATH, on a pin that names a tree or a
            # tag, on a repository that has the commit and lost the blob — all
            # of them the record's fault, all of them silently excused in a
            # `--depth` clone by a message asserting the checkout lacks a commit
            # it HAS. So the commit object is asked about directly, and only its
            # genuine absence can reach the skip.
            def git(*args):
                try:
                    return subprocess.run(("git",) + args, cwd=REPO,
                                          capture_output=True, text=True,
                                          timeout=self.GIT_TIMEOUT)
                except subprocess.TimeoutExpired:
                    # These three are local object-database reads — `cat-file`,
                    # `rev-parse`, `config` — with no remote to be slow. A
                    # timeout here is not an environment that cannot answer,
                    # and skipping on one hid the classification the arms below
                    # are made of: with the read itself already failed, a skip
                    # here means nothing was ever decided about WHY.
                    pytest.fail(f"`git {' '.join(args)}` did not return within "
                                f"{self.GIT_TIMEOUT}s, so the failure to read "
                                f"Replay A's pin could not be classified")

            present = git("cat-file", "-e", f"{sha}^{{commit}}").returncode == 0
            shallow = git("rev-parse", "--is-shallow-repository")
            if shallow.stdout.strip() == "true" and not present:
                pytest.skip(f"shallow clone: {sha} is not in this checkout")
            # A BLOBLESS clone is the second environment that cannot answer, and
            # it is invisible to both questions above: `--filter=blob:none`
            # fetches every commit and no file content, so the commit IS here,
            # the clone is NOT shallow, and a `git show` that fails did so
            # because the promisor remote could not be reached. Diagnosed by the
            # config `--filter` writes rather than by the message, which is the
            # transport's and not git's to keep stable. Without this the guard
            # asserted "the commit is here and the path is not in it" — blaming
            # the record's path, and telling a maintainer to re-point a pin that
            # is exactly right — for a blob that the commit does contain.
            # ...and the blobless arm needed the same separation the shallow
            # arm above it got, for the same reason and one environment along.
            # Keyed on `partial and present` alone it excused EVERY `git show`
            # failure in a filtered checkout: misspell Replay A's path, or
            # re-point it at a revision that never carried the file, and a
            # broken pin skipped green under a message blaming a promisor that
            # was never asked about it — a guard reporting the wrong reason for
            # its own silence, which is worse than no guard because the message
            # is what stops anyone looking.
            #
            # The TREE is what separates the two, and a blobless clone has its
            # trees: `--filter=blob:none` omits blob content and fetches every
            # commit and tree, so `ls-tree` answers from the local object
            # database exactly where `show` had to reach the remote. A path the
            # tree does not hold is the record's fault in any clone.
            listed = git("ls-tree", "--name-only", "-r", f"{sha}^{{tree}}",
                         "--", path)
            in_tree = (listed.returncode == 0
                       and path in listed.stdout.splitlines())
            partial = bool(git("config", "--get",
                               "extensions.partialclone").stdout.strip())
            if partial and present and in_tree:
                pytest.skip(f"blobless clone: {sha}:{path} is not local and the "
                            "promisor remote could not be read")
            raise AssertionError(
                f"Replay A pins `{sha}:{path}`, which this repository cannot "
                f"read — {'the commit is here and the path is not in it'
                          if present else 'the commit is not in this clone'}: "
                f"{show.stderr.decode('utf-8', 'replace').strip()[:200]}. The "
                "replay cannot be run at all, so the PASS recorded against it "
                "is unreproducible — re-point the pin at a revision and path "
                "that carry the defect")
        # `__file__` included, because a module that reads its own path is
        # ordinary and this namespace had none: the engine resolves its root
        # from `__file__`, so a pin moved to a revision that does raised
        # NameError from the exec instead of failing the signature assertion the
        # guard exists to reach. A guard whose failure arrives as a different
        # error reports the wrong reason for its own red, which is the defect
        # the shallow-clone arm below was written for, one line along. The value
        # is where the file WOULD sit in a checkout, which is what the historical
        # code would have seen.
        namespace = {"__name__": "t2_replay_a_fixture",
                     "__file__": str(REPO / path)}
        exec(compile(source, f"<{sha}:{path}>", "exec"), namespace)
        # The source the verdict's INPUT pin is taken over, handed back through
        # the namespace so the digest costs no second `git show` — a subprocess,
        # in a module that is one of the cheap MEASURED COSTS rows. Set AFTER the
        # exec, so a name the historical module happens to define cannot be what
        # gets hashed. The decode is pinned to UTF-8 for the same digest: the
        # blob carries `§` and a locale-dependent decode would re-encode to
        # different bytes, failing the pin on a correct tree in whichever locale
        # CI runs.
        namespace["__t2_source__"] = source
        return namespace

    def test_a_git_timeout_fails_rather_than_skipping(self, monkeypatch):
        """The control on the arm that decides whether this guard runs at all.

        `_historical_module` calls `git` five times at most, each with a
        timeout, and both timeout arms were written as `pytest.skip`. A skip
        reads exactly like a pass — the sentence the timeout was added for,
        arriving one line later — so on the slow-promisor blobless clone that
        the bound exists for, the Replay A pin check quietly stopped running and
        left only a number in pytest's skip count.

        Driven by making `subprocess.run` raise, because nothing else can
        produce a timeout on demand: waiting for a real one would put a minute
        into a module the hook runs on every edit, and asserting on the source
        text would test the spelling rather than the behaviour.
        """
        def times_out(args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=list(args), timeout=1)

        monkeypatch.setattr(subprocess, "run", times_out)
        with pytest.raises(pytest.fail.Exception, match="did not return within"):
            self._historical_module("0" * 40, "agents/lib/tax_computations.py")

        # ...and the classifying calls too. Reached only once the read has
        # already failed, so a skip there means nothing was ever decided about
        # WHY — the same silence one frame in.
        calls = []

        def show_fails_then_times_out(args, **kwargs):
            calls.append(list(args))
            if len(calls) == 1:
                return subprocess.CompletedProcess(args, 128, b"", b"boom")
            raise subprocess.TimeoutExpired(cmd=list(args), timeout=1)

        monkeypatch.setattr(subprocess, "run", show_fails_then_times_out)
        with pytest.raises(pytest.fail.Exception, match="could not be classified"):
            self._historical_module("0" * 40, "agents/lib/tax_computations.py")
        assert len(calls) > 1, (
            "the classifying calls were never reached, so this half of the "
            "control proves nothing")

    def test_the_pinned_revision_still_carries_the_qbi_defect(self):
        """Run the record's own Replay A input; check the answer it must give.

        Replay B's fixture is GENERATED, so it drifts by an edit to the
        generator; Replay A's is a git object, so it drifts by an edit to the
        SHA — one character of the record, and the class of the change is
        invisible in a diff. Point it at `a696dac` instead of its parent, at any
        later revision, or at a hash that no longer resolves, and the bias
        reviewer's report on it is honestly empty. Empty is also what a BROKEN
        reviewer returns, and the record cannot tell them apart: that is the
        whole reason this class exists, and Replay A was the half of it with no
        guard.

        The known answer is the criterion's own demonstration. The pinned
        function takes no taxable-income argument at all, so it cannot apply
        Form 8995 lines 11-14 and returns the FULL 20% — the over-stated
        deduction, under-stated tax, which is the direction the reviewer is
        scored on naming. The shipped function, given the same case, returns the
        capped figure and says so. Both halves are asserted: the signature check
        is what makes a pin moved to a FIXED revision fail legibly, because a
        fixed function called the old way skips the cap too and returns the same
        20% the defect does.
        """
        sha, path = self._replay_a_pin()
        target, qbi, income, overstated, allowed = self._replay_a_criterion()
        namespace = self._historical_module(sha, path)

        # ...and the verdict names THIS blob. "Still carries the defect" is what
        # every assertion below establishes, and it is not the same claim: two
        # revisions can both carry it, so a pin moved from one to the other
        # leaves this test green, `_unpinned` green, and the recorded PASS
        # describing a report on a file that was never dispatched. Checked here
        # because the bytes are already in hand.
        date = self._claim_date()
        if date is not None:
            problem = _input_unpinned(
                self.RECORD.read_text(), date, self.REPLAY_A,
                _digest(namespace["__t2_source__"].encode("utf-8")))
            assert not problem, (
                f"{problem} — `{sha}:{path}` is not what the recorded PASS was "
                "scored against, so re-run the replay from a FRESH session and "
                "renew both pins")
        assert target in namespace, (
            f"Replay A's criterion is written against `{target}`, which "
            f"{sha}:{path} does not define — the pin and the criterion are "
            "describing different code")

        pinned = namespace[target]
        assert "taxable_income_before_qbi" not in inspect.signature(
                pinned).parameters, (
            f"the pinned `{target}` already TAKES the taxable income the Form "
            "8995 cap is computed from, so the defect Replay A is scored on is "
            f"not in {sha} — the pin has moved to a revision at or after the "
            "fix, and the reviewer's empty report on it would be correct")
        was = pinned(qbi, 0)
        assert was["total_deduction"] == overstated, (
            f"the pinned `{target}` returns "
            f"{was['total_deduction']} on the criterion's own case, not the "
            f"{overstated} it states — the record scores the replay against a "
            "figure its fixture does not produce")
        assert "income_cap_applied" not in was, (
            f"the pinned `{target}` reports the cap it is supposed to omit")

        # The other end of the same case, from the engine that shipped: the
        # criterion's `line 14 allows` figure is a claim about THIS code, and a
        # replay scored against a cap the engine does not actually apply would
        # be scored against nothing.
        from agents.lib import tax_computations

        now = getattr(tax_computations, target)(
            qbi, 0, taxable_income_before_qbi=income)
        assert now["total_deduction"] == allowed and now["income_cap_applied"], (
            f"the shipped `{target}` returns {now['total_deduction']} with the "
            f"cap {'applied' if now['income_cap_applied'] else 'skipped'}, "
            f"where Replay A's criterion states {allowed} with the cap applied "
            "— the fix the replay is written against is not what the engine does")

    def test_a_shallow_clone_excuses_a_missing_commit_and_nothing_else(
            self, tmp_path, monkeypatch):
        """The skip above is for a clone that CANNOT ANSWER, not for any `git
        show` that fails.

        Keyed on shallowness alone it excused both, and the two are not the same
        fact. `git show <sha>:<path>` fails on a misspelled path, on a pin that
        names a tree or a tag, on a commit whose blob is gone — every one of
        them a broken record, and every one of them waved through on a
        contributor's `--depth` clone by a skip whose message says the checkout
        lacks a commit it is holding. That is a guard reporting the wrong reason
        for its own silence: worse than no guard, because the message is what
        stops anyone looking.

        Tested against a REAL shallow repository rather than a stubbed `git`.
        Shallowness is a fact about a file — `.git/shallow`, listing the commits
        whose parents are missing, which is exactly what `clone --depth` writes
        — so a two-object repository carrying one can be built in milliseconds
        and every `git` call under test is the real one. A stub would pass
        against its own idea of which invocation git is asked to run, and the
        invocation is the thing this changed.

        Built with the operator's HOOKS and templates switched off, and that is
        not tidiness. `git commit` honours a global `core.hooksPath`, and
        `agents/install.sh` already acknowledges contributors who set one — so on
        such a machine the commit exited non-zero, the guard's own discriminating
        control took the skip below, and a skipped test exits green. The control
        that proves this guard is not vacuous was itself vacuous, on an ordinary
        configuration, with no symptom anywhere. What the skip can still mean is
        now exactly one thing: git is absent or cannot run at all.
        """
        repo = tmp_path / "shallow"
        repo.mkdir()
        empty = tmp_path / "nohooks"
        empty.mkdir()
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
               "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={empty}",
               "-c", f"init.templateDir={empty}"]
        try:
            subprocess.run(git + ["init", "-q", str(repo)], check=True,
                           capture_output=True)
            (repo / "mod.py").write_text("MARKER = 1\n")
            subprocess.run(git + ["add", "mod.py"], cwd=repo, check=True,
                           capture_output=True)
            subprocess.run(git + ["commit", "-qm", "one"], cwd=repo, check=True,
                           capture_output=True)
            sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                                 check=True, capture_output=True,
                                 text=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            pytest.skip(f"git cannot build the fixture repository: {exc}")
        (repo / ".git" / "shallow").write_text(f"{sha}\n")
        monkeypatch.setitem(globals(), "REPO", repo)
        assert subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"], cwd=repo,
            capture_output=True, text=True).stdout.strip() == "true", (
            "the fixture repository does not report as shallow, so this test "
            "would exercise the ordinary arm and pin nothing")

        # The control: on this repository a GOOD pin reads, so a failure below
        # is the pin's and not the fixture's.
        assert self._historical_module(sha, "mod.py")["MARKER"] == 1

        # The commit is present, so the path is what is wrong — and a shallow
        # clone is no excuse for that. Caught rather than `pytest.raises`d,
        # because the failure this pins is a SKIP: raises would let the skip
        # through, and a skipped test exits green, so the guard could be
        # reverted under a passing suite.
        try:
            self._historical_module(sha, "does_not_exist.py")
        except pytest.skip.Exception as excused:
            pytest.fail(f"a bad PATH in a shallow clone took the skip meant "
                        f"for history the clone lacks: {excused}")
        except AssertionError as bad:
            assert "the path is not in it" in str(bad), (
                f"a bad PATH in a shallow clone is reported as something "
                f"other than a bad path: {bad}")
        else:
            pytest.fail("a pin naming a path its commit does not hold was "
                        "read without complaint")

        # ...and the arm that must survive: history the clone genuinely lacks
        # is an environment that cannot answer, and skipping is right there.
        absent = "0" * 39 + "1"
        with pytest.raises(pytest.skip.Exception) as skipped:
            self._historical_module(absent, "mod.py")
        assert absent in str(skipped.value), (
            f"the shallow-clone skip no longer names the unreachable commit: "
            f"{skipped.value}")

    def test_a_blobless_clone_is_not_reported_as_a_broken_pin(
            self, tmp_path, monkeypatch):
        """The OTHER environment that cannot answer, and the one both questions
        above read as the record's fault.

        `git clone --filter=blob:none` fetches every commit and no file content.
        The commit is therefore present and the clone is not shallow, so a
        `git show` that fails — because the promisor remote is unreachable, which
        is every offline moment and every clone of a repository since moved —
        fell through to the assertion, and that assertion states the one thing
        that is definitely not true: "the commit is here and the path is not in
        it". It then tells the maintainer to re-point a pin that is exactly
        right. A guard whose remedy is wrong is worse than one that stays quiet,
        because the remedy is what stops anyone looking further.

        Built as a REAL partial clone rather than stubbed, on the same argument
        the shallow control makes: what changed is which `git` invocation the
        guard makes, and a stub would agree with whatever the test assumed. The
        two config keys are exactly what `--filter=blob:none` writes, the blob is
        removed the way a filtered fetch never writes it, and `origin` is left
        undefined so the promisor fetch fails the way an unreachable remote does.
        """
        repo = tmp_path / "blobless"
        repo.mkdir()
        empty = tmp_path / "nohooks"
        empty.mkdir()
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
               "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={empty}",
               "-c", f"init.templateDir={empty}"]
        try:
            subprocess.run(git + ["init", "-q", str(repo)], check=True,
                           capture_output=True)
            (repo / "mod.py").write_text("MARKER = 1\n")
            subprocess.run(git + ["add", "mod.py"], cwd=repo, check=True,
                           capture_output=True)
            subprocess.run(git + ["commit", "-qm", "one"], cwd=repo, check=True,
                           capture_output=True)
            sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                                 check=True, capture_output=True,
                                 text=True).stdout.strip()
            blob = subprocess.run(["git", "rev-parse", "HEAD:mod.py"], cwd=repo,
                                  check=True, capture_output=True,
                                  text=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            pytest.skip(f"git cannot build the fixture repository: {exc}")
        loose = repo / ".git" / "objects" / blob[:2] / blob[2:]
        if not loose.exists():
            pytest.skip("the fixture blob is packed, so it cannot be removed "
                        "the way a filtered fetch never writes it")
        loose.unlink()
        # Exactly the pair `--filter=blob:none` writes. The format version is
        # not optional: git refuses to read an extension under version 0.
        for key, value in (("core.repositoryformatversion", "1"),
                           ("extensions.partialclone", "origin")):
            subprocess.run(["git", "config", key, value], cwd=repo, check=True,
                           capture_output=True)
        monkeypatch.setitem(globals(), "REPO", repo)

        # The fixture has to be the shape the guard is being asked about, or
        # this passes against an ordinary broken pin: the commit present, the
        # clone not shallow, and the blob unreadable.
        assert subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"],
                              cwd=repo, capture_output=True).returncode == 0, (
            "the fixture lost its commit, so this would exercise the shallow "
            "arm instead")
        assert subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"], cwd=repo,
            capture_output=True, text=True).stdout.strip() == "false", (
            "the fixture reports as shallow, so the arm above would take it")
        assert subprocess.run(["git", "show", f"{sha}:mod.py"], cwd=repo,
                              capture_output=True).returncode != 0, (
            "the fixture can still read its blob, so there is nothing here for "
            "the guard to misdiagnose")

        # The bad-PATH control the shallow arm carries and this one did not,
        # which is the whole of how the same defect survived into the second
        # environment. The commit is present, the clone is partial, and the
        # TREE does not hold this path — so `git show` fails for the record's
        # own reason and the filtered checkout is no excuse for it. Ordered
        # before the arm that must survive, because that one ends in a skip and
        # a skip ends the test.
        try:
            self._historical_module(sha, "does_not_exist.py")
        except pytest.skip.Exception as excused:
            pytest.fail(f"a bad PATH in a blobless clone took the skip meant "
                        f"for a blob the promisor holds: {excused}")
        except AssertionError as bad:
            assert "the path is not in it" in str(bad), (
                f"a bad PATH in a blobless clone is reported as something "
                f"other than a bad path: {bad}")
        else:
            pytest.fail("a pin naming a path its commit does not hold was "
                        "read without complaint")

        # Caught rather than `raises`d for the reason the shallow control gives:
        # the failure being pinned is a SKIP, and a skipped test exits green.
        try:
            self._historical_module(sha, "mod.py")
        except pytest.skip.Exception as excused:
            assert "blobless" in str(excused), (
                f"a blobless clone took a skip that describes something else: "
                f"{excused}")
        except AssertionError as bad:
            pytest.fail(f"a blobless clone was reported as a broken pin, with a "
                        f"remedy that would re-point a correct one: {bad}")
        else:
            pytest.fail("a blob this clone cannot read was read without "
                        "complaint")

    def test_the_replay_blob_is_read_the_way_the_record_says_to_take_it(
            self, tmp_path, monkeypatch):
        """The pin the guard computes and the pin the record tells a maintainer
        to compute have to be the same number.

        The record's renewal command is `git show <sha>:<path> | tr -d '\r' |
        shasum -a 256`, and `tr -d` DELETES every carriage return. The guard read
        the blob with `text=True`, and universal-newline decoding REWRITES a lone
        `\r` to `\n` — a different byte, not a dropped one — so on a blob holding
        a bare CR the two disagreed and `_digest`, which drops CRs precisely so
        the two agree, could not drop one that had already become a newline. A
        maintainer following the documented command would then record a pin that
        can never satisfy the audit that printed it: the remedy for a red guard
        leaves it red, and the only way out is to stop believing the guard.

        Latent on the live pin, which holds no CR — which is exactly why it is
        exercised on a blob that does. A normalisation nothing tests is a
        normalisation that is whatever the last edit left behind.
        """
        repo = tmp_path / "cr"
        repo.mkdir()
        empty = tmp_path / "nohooks"
        empty.mkdir()
        # `core.autocrlf=false` so git stores the bytes written, and this test
        # is about the guard's decode rather than about a checkout filter.
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
               "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={empty}",
               "-c", f"init.templateDir={empty}", "-c", "core.autocrlf=false"]
        # The bare CR sits between two COMMENTS, so the blob is valid python
        # under both decodings and the assertion below is about the bytes rather
        # than about which of them happens to raise. Under `tr -d` the two
        # comments join into one; under universal newlines they stay two.
        raw = b"MARKER = 1\nTHIRD = 3\n# a\r# b\n"
        try:
            subprocess.run(git + ["init", "-q", str(repo)], check=True,
                           capture_output=True)
            (repo / "mod.py").write_bytes(raw)
            subprocess.run(git + ["add", "mod.py"], cwd=repo, check=True,
                           capture_output=True)
            subprocess.run(git + ["commit", "-qm", "one"], cwd=repo, check=True,
                           capture_output=True)
            sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                                 check=True, capture_output=True,
                                 text=True).stdout.strip()
            stored = subprocess.run(["git", "show", f"{sha}:mod.py"], cwd=repo,
                                    check=True, capture_output=True).stdout
        except (OSError, subprocess.CalledProcessError) as exc:
            pytest.skip(f"git cannot build the fixture repository: {exc}")
        assert b"\r" in stored, (
            "git stored the fixture without its carriage returns, so this test "
            "would compare two identical decodes and pin nothing")

        monkeypatch.setitem(globals(), "REPO", repo)
        namespace = self._historical_module(sha, "mod.py")
        assert namespace["MARKER"] == 1 and namespace["THIRD"] == 3, (
            "the fixture module did not execute, so the source handed back is "
            "not the source of a module that ran")
        # `tr -d '\r'`, exactly: every CR gone, and nothing turned into a
        # newline in its place.
        assert namespace["__t2_source__"] == stored.decode("utf-8").replace(
            "\r", ""), (
            "the guard's source is not what the record's renewal command "
            "produces — a lone carriage return was rewritten rather than "
            "dropped")
        assert _digest(namespace["__t2_source__"].encode("utf-8")) == _digest(
            stored), (
            "the pin the guard computes and the pin the record documents "
            "disagree on a blob holding a bare carriage return")

    def test_every_git_call_in_the_replay_guard_is_bounded(self):
        """A `git show` here may be a NETWORK fetch, so an unbounded one hangs.

        On a blobless clone the read above goes to the promisor remote, and
        against a remote that is unreachable rather than absent the transport
        waits. That wait happens inside a pytest run that a PostToolUse hook is
        holding a tool call open for, so the outcome is a hook CANCELLED at its
        own limit — which produces no verdict and is indistinguishable from a
        hook that passed. The bound is the difference between a slow failure
        that says what it is and a silence.

        Read off the SOURCE rather than by timing a call, because what has to
        hold is that no invocation is added without one. A timing test passes
        for whichever calls happen to be fast today.
        """
        import ast

        tree = ast.parse(Path(__file__).read_text())
        guard = next((n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef)
                      and n.name == "_historical_module"), None)
        assert guard is not None, (
            "`_historical_module` is gone or renamed — this test pins the "
            "timeout on every git call it makes")
        calls = [n for n in ast.walk(guard) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == "run"]
        assert calls, "the guard starts no subprocesses, so it reads no pin"
        unbounded = [ast.unparse(c)[:60] for c in calls
                     if not any(k.arg == "timeout" for k in c.keywords)]
        assert not unbounded, (
            f"{len(unbounded)} `subprocess.run` call(s) in the replay guard "
            f"carry no timeout: {unbounded} — a promisor fetch against an "
            "unreachable remote then hangs the suite, and inside the hook that "
            "is a cancellation nobody can tell from a pass")

    def test_ci_checks_out_the_history_the_replay_pin_needs(self):
        """The guard above SKIPS on a shallow clone, and CI is where a shallow
        clone is the default.

        Skipping there is the right answer to an environment that cannot reach
        the object — a red suite on a `--depth` clone is a gate crying wolf —
        but it makes the workflow's checkout depth part of whether the guard
        runs at all. Every other assertion about that guard is satisfied by a
        job that never executes it. So the depth is pinned where the cost of
        being wrong is invisible: `fetch-depth: 0` deleted from the tests job
        leaves Replay A unchecked in the one place every contributor's change
        passes through, and nothing else in the suite would say so.
        """
        # Bounded by INDENTATION, not by a following job key. Requiring one
        # made `test:` being the last job in the file read as a job that is not
        # there, and the message would then have blamed a checkout depth that is
        # present — a guard reporting the wrong reason for its own red.
        ci = (REPO / ".github" / "workflows" / "ci.yml").read_text()
        job = re.search(r"^  test:\n(?P<body>(?:(?!  \S)[^\n]*\n)*)", ci, re.M)
        assert job, ("ci.yml no longer opens a `test:` job this test can read — "
                     "it pins that job's checkout depth")
        # By STEP rather than by the lines immediately under `uses:`. The old
        # read admitted only comments and a bare `with:` between the two, so
        # adding any other key — `persist-credentials: false`, say — or writing
        # them in the other order failed with a message saying the depth had
        # been deleted while it sat two lines below. YAML mapping keys are
        # unordered and a guard that says otherwise is one people route around.
        steps = re.split(r"\n(?=\s*-\s)", job.group("body"))
        checkout = [s for s in steps if "actions/checkout@" in s]
        assert len(checkout) == 1, (
            f"ci.yml's tests job has {len(checkout)} `actions/checkout` steps; "
            "this test pins the depth of the one that checks the repository out")
        assert re.search(r"^\s*fetch-depth:\s*0\b", checkout[0], re.M), (
            "ci.yml's tests job checks out at the default depth, so "
            "`git show <sha>` cannot reach the revision Replay A pins and "
            "test_the_pinned_revision_still_carries_the_qbi_defect skips in "
            "CI — restore `fetch-depth: 0` rather than deleting the guard it "
            "feeds")

    def _replay_b_body(self):
        """Replay B's own section, on `_replay_a_body`'s terms and for its reason.

        The two helpers below read a generator out of the record — its declared
        years, and the fenced body they then execute — and both `re.search`ed the
        WHOLE document for a `YEAR, PRE_OBBBA` line and a `run.sh` heredoc. Each
        appears once today, which is a fact about the current text and not a rule
        the record is held to. A second `run.sh - <<'PY'` block anywhere above
        Replay B — a worked example in the intro, a fetch note, a superseded
        replay left in place — and these guards would have certified THAT while
        the freshness auditor was handed something else, which is the failure
        Replay A was scoped to close with its sibling left open beside it.
        """
        sections, problem = _replay_sections(self.RECORD.read_text())
        assert problem is None, problem
        assert self.REPLAY_B in sections, (
            f"no `## Replay` section for {self.REPLAY_B} — see "
            "test_the_acceptance_doc_covers_every_agent_that_exists, which owns "
            "that failure and says what a replay has to contain")
        return sections[self.REPLAY_B]

    def _years_from_the_record(self):
        """READ the years out of the record's own generator instead of keeping a
        second copy here, which is what this class held first and what made it
        unable to see the failure below.

        Out of the GENERATOR BODY and not the section around it, so the years
        this scores and the code that gets executed are one text. Read from the
        section, a `YEAR, PRE_OBBBA` line in a note beside the fixture — the
        superseded pair, an explanation of what the two mean — answered for the
        pair the generator actually plants.
        """
        m = re.search(r"^YEAR,\s*PRE_OBBBA\s*=\s*(\d{4}),\s*(\d{4})\b",
                      self._generator_body(), re.M)
        assert m, (
            "the record's generator no longer names its years as "
            "`YEAR, PRE_OBBBA = <covered>, <pre>` — this test reads them from "
            "there so the two cannot drift apart, so a reformat that hides them "
            "has to be noticed rather than silently skipped")
        return int(m.group(1)), int(m.group(2))

    def test_the_planted_salt_cap_differs_from_the_covered_year(self):
        """The trap is the OBBBA window CLOSING, and it arrives by the calendar
        rather than by anyone editing anything.

        The cap is $10,000 before TY2025, raised for TY2025-2029, and it REVERTS
        to $10,000 in TY2030. So the pre-OBBBA plant and the correct figure are
        the same number for any covered year from 2030 on: the fixture stops
        being corrupt, the auditor's clean report becomes the right answer, and
        the acceptance passes having proved nothing — which is the exact failure
        the whole procedure exists to detect. Whoever re-runs this for a TY2030
        return will update the record's covered year as a matter of course, and
        that is the moment this has to be red.
        """
        from agents.lib.ptet import _salt_cap

        covered, pre_obbba = self._years_from_the_record()
        for status in ("single", "MFS"):
            planted = _salt_cap(pre_obbba, status)
            correct = _salt_cap(covered, status)
            assert planted != correct, (
                f"the record plants the TY{pre_obbba} SALT cap ({planted}) in a "
                f"TY{covered} cache to see whether the auditor catches it, but "
                f"the schedule returns the same figure for both years ({status}) "
                "— the fixture is UNCORRUPTED and the acceptance would pass on "
                "an empty finding. If the covered year moved past the OBBBA "
                "window, the plant has to move with it: pick a year whose cap "
                "the covered year's genuinely differs from, and re-run the "
                "replay rather than trusting its last verdict")

    def _generator_body(self):
        """The fenced generator, unwrapped from its `run.sh` heredoc.

        Reading the SOURCE and re-deriving what it ought to produce is what the
        first version of this class did, and it could not see a generator that
        stopped corrupting anything: swap `PRE_OBBBA` for `YEAR` in the plant, or
        drop the assignment, and the years still differ, so every assertion held
        while the fixture became honest. The body is executed below and the CACHE
        IT WRITES is what gets checked.
        """
        found = re.findall(r"```sh\n[^\n]*run\.sh - <<'PY'\n(.*?)\nPY\n```",
                           self._replay_b_body(), re.S)
        assert found, (
            "the record's fenced generator no longer parses as a `run.sh` "
            "heredoc — the acceptance fixture is built by code this test can no "
            "longer execute, so nothing checks that it plants a wrong figure")
        # Refused rather than picked between, on the terms `_replay_a_pin`
        # refuses two `git show` inputs and `_replay_sections` two sections for
        # one reviewer. A revised generator appended beside the one it replaces
        # is the ordinary way to get a second, and `re.search` took the FIRST —
        # so the corruption audit went on scoring the superseded fixture while
        # the PASS was filed against a run of the new one, which may plant
        # nothing at all.
        assert len(found) == 1, (
            f"Replay B carries {len(found)} fenced `run.sh` generators, and "
            "this test executes the one the acceptance was scored against — "
            "replace the superseded generator rather than appending beside it")
        return found[0]

    def test_the_documented_generator_writes_a_corrupt_cache(self, tmp_path,
                                                             monkeypatch):
        """Run the record's own generator; audit the file it produces.

        The failure this catches is a generator that still declares its two years
        and still runs, but no longer plants the wrong one — `_salt_cap(YEAR, …)`
        instead of `_salt_cap(PRE_OBBBA, …)`, or the assignment deleted so the
        schema's `None` default survives. Every such fixture is UNCORRUPT, the
        auditor's clean report on it is correct, and the replay proves nothing.
        Checked against the source alone, all of them stay green.

        Executed in-process rather than through `run.sh` deliberately: the
        launcher spelling is already pinned textually by test_preflight's
        DOCUMENTS check, and a subprocess here would add a whole interpreter
        start to a module skill-invariants.sh MEASURED COSTS records among its
        cheap rows — which is exactly what makes this the module a subprocess
        is most expensive in, relative to what it otherwise costs.
        """
        covered, pre_obbba = self._years_from_the_record()
        body = self._generator_body()
        # Redirected through `tempfile.tempdir` rather than by rewriting a path
        # literal in the body. The literal was `/tmp/t2-auditor`, which is a
        # POSIX absolute path: under Git Bash with the native Windows
        # interpreter it resolves to the current drive's root, where the replay
        # may simply be unable to write — and Windows clones are a supported
        # contributor surface. `mkdtemp` asks the platform instead, which is also
        # what makes the record's isolation claim structural: a fresh directory
        # every run cannot have last year's cache sitting beside this year's.
        assert "tempfile.mkdtemp(" in body, (
            "the generator no longer asks the platform for a scratch directory, "
            "so this test's redirection points at nothing and it would write "
            "wherever the record says — re-point the redirection rather than "
            "deleting this guard")
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
        exec(compile(body, str(self.RECORD), "exec"), {"__name__": "__main__"})

        written = sorted(tmp_path.glob("*/tax_law_*.json"))
        assert len(written) == 1, (
            f"the generator wrote {[f.name for f in written]}; the replay hands "
            "the auditor ONE cache and a directory holding several is the "
            "ambiguity the record's own isolation note exists to prevent")
        assert written[0].name == f"tax_law_{covered}.json", (
            f"the generator declares TY{covered} but wrote {written[0].name} — "
            "the payload year the auditor sees is not the one the pass criterion "
            "is written against")

        from agents.lib.ptet import _salt_cap

        law = json.loads(written[0].read_text())
        # The filename is the auditor's INPUT; `tax_year` is what it audits
        # against, and its check 0 makes a mismatch between them a hard STOP —
        # INCOMPLETE, before a single figure is looked at. So a generator built
        # with `create_empty_tax_law(PRE_OBBBA)` under the covered year's
        # filename still writes the planted cap, still satisfies every
        # assertion below, and hands the replay a fixture whose audit ends
        # before it can reach the finding the pass criterion is written on.
        # Checked on the two years the generator declares rather than on the
        # pair agreeing with each other: they agree trivially if BOTH move.
        assert law.get("tax_year") == covered, (
            f"the generator names tax_law_{covered}.json but its payload "
            f"declares tax_year {law.get('tax_year')!r} — the auditor stops at "
            "that mismatch as INCOMPLETE and never reaches the SALT-cap finding "
            "this replay scores")

        cap = law["federal"]["salt_cap"]
        for field, status in (("amount", "single"), ("mfs_amount", "MFS")):
            planted, correct = cap[field], _salt_cap(covered, status)
            assert planted == _salt_cap(pre_obbba, status), (
                f"the cache's {field} is {planted!r}, which is not the TY"
                f"{pre_obbba} figure the generator claims to plant — the plant "
                "was changed or removed, and an auditor reporting this cache "
                "clean would be RIGHT")
            assert planted != correct, (
                f"the cache's {field} ({planted}) already equals the correct TY"
                f"{covered} figure, so the fixture is not corrupt and the replay "
                "would pass on an empty finding — see the OBBBA-window note in "
                "the record and move the pre-year")

        # ...and the verdict names THIS cache. Everything above establishes that
        # the generator still plants a stale cap; none of it says the auditor was
        # shown this one. The digest is a function of the ENGINE, so a field
        # added to `create_empty_tax_law` moves it — which is the record's "after
        # a schema change" trigger, arriving as a red test instead of as prose a
        # maintainer has to remember.
        date = self._claim_date()
        if date is not None:
            problem = _input_unpinned(self.RECORD.read_text(), date,
                                      self.REPLAY_B,
                                      _digest(written[0].read_bytes()))
            assert not problem, (
                f"{problem} — the generator no longer writes the cache the "
                "auditor was scored on, so re-run the replay from a FRESH "
                "session and renew both pins")

    def test_the_record_requires_a_fresh_session_before_dispatch(self):
        """The acceptance can pass on a definition it never ran.

        Claude Code loads `.claude/agents/` at session start, so a session open
        when a definition was edited keeps dispatching the OLD one. Replay it
        there and the report comes from the reviewer being replaced, gets scored
        against criteria written for its replacement, and PASS is recorded for a
        definition nothing tested — a false PASS with no symptom, since a stale
        reviewer is exactly as fluent as a current one.

        CLAUDE.md carried this as "definitions load at session start — so, a
        later session" until a rewrite of that paragraph dropped it, and the
        record written in the same change never restored it while its *When to
        re-run* list grew the one trigger that depends on it. Nothing was
        watching, which is why this test exists rather than a third prose copy.
        """
        text = self.RECORD.read_text()
        # Flattened before matching: both files are wrapped prose, and the
        # CLAUDE.md phrase below lands with `FRESH` ending one line and
        # `session` opening the next. A raw substring check reported it ABSENT
        # from a file that states it — the rewrap failure the hooks module's
        # own MEASURED COSTS test already carries a note about.
        flat = re.sub(r"\s+", " ", text)
        assert "**Dispatch from a FRESH session.**" in flat, (
            f"{self.RECORD.name} no longer opens its procedure with the "
            "fresh-session precondition. If it has been reworded, reword this "
            "assertion with it — do not delete it: without that step the "
            "procedure validates whatever definition the session started with")

        bullet = re.search(r"^- After editing either definition"
                           r"(?:[^\n]*\n(?:  [^\n]*\n)*)", text, re.M)
        assert bullet and "new session" in re.sub(r"\s+", " ", bullet.group(0)), (
            "the *When to re-run* trigger for an edited definition does not say "
            "to start a new session — that bullet is the one route to this "
            "document where the stale-definition trap is certain to be live")

        assert "FRESH session" in re.sub(
                r"\s+", " ", (REPO / "CLAUDE.md").read_text()), (
            "CLAUDE.md dropped the fresh-session constraint. It is the copy an "
            "agent has LOADED when it decides how to run the acceptance, so the "
            "record having it is not sufficient")

    def test_the_acceptance_doc_covers_every_agent_that_exists(self):
        """A renamed or added agent must not leave an acceptance record that
        silently stops covering it — the record is the only evidence these two
        were ever tested against a known answer rather than for plausibility."""
        assert self.RECORD.is_file(), (
            "the acceptance record named by CLAUDE.md is missing")
        text = self.RECORD.read_text()

        # A bare `name in text` was the first spelling and it could not fail:
        # each agent is named several times OUTSIDE its replay — in the intro,
        # the contamination check, the results headings, the re-run section — so
        # deleting a whole `## Replay` section left every mention standing and
        # the check green on a record no longer holding that agent's INPUT or
        # its pass criterion. What the record has to contain is the replay, so
        # that is what is required: a `## Replay <letter> — <agent>` heading, and
        # inside that section the two things a replay is made of.
        sections, problem = _replay_sections(text)
        assert problem is None, problem
        for name in REVIEW_AGENTS:
            assert name in sections, (
                f"{name} has no `## Replay` section in {self.RECORD.name} "
                f"(found {sorted(sections) or 'none'}); CLAUDE.md requires "
                "replaying a known answer before either agent is trusted, and a "
                "record that merely MENTIONS an agent is not that")
            body = sections[name]
            for marker in ("**Input.**", "**Pass criterion.**", "**Fail if**"):
                assert marker in body, (
                    f"{name}'s replay section has no {marker} — a replay "
                    "without an input or a pre-committed criterion is the "
                    "plausibility judgement this procedure exists to replace")

            # The fail condition has to make a NAMED finding mandatory, and the
            # phrasing is what this checks because the phrasing is what broke.
            # Replay A's criterion states one bar and two credits; its fail
            # condition said "names none of the three", which passes a report
            # that found a credit and walked past the bar — a contradiction
            # between two paragraphs of the document that IS the pre-committed
            # criterion, and one that resolves into a recorded PASS. An "any of
            # N" condition cannot express a bar; a negated naming requirement is
            # the shape that can.
            fail_if = re.split(r"\n\s*\n", body.split("**Fail if**", 1)[1])[0]
            assert re.search(r"(?:does not|never) names?\b", fail_if), (
                f"{name}'s `**Fail if**` states no finding whose ABSENCE fails "
                f"the replay: {' '.join(fail_if.split())[:120]!r}. The pass "
                "criterion names a bar; if the fail condition is satisfied by "
                "any finding at all, a reviewer that missed the bar is recorded "
                "as having passed")
            # It pins the SHAPE and cannot pin the referent: a negated name is
            # satisfied by naming a KEY, and Replay B named one. That fixture
            # turned out to hold a second, unplanted defect on the same key, so
            # a report finding only the credit satisfied this condition and
            # missed the bar — the identical gap one layer in. The record now
            # requires the FINDING rather than the key, and says in prose that
            # this test cannot check the difference.

        # The verdicts are the other half — a section can hold a criterion and
        # never have been RUN against it — and they are checked by
        # `test_the_advertised_pass_is_backed_by_the_record` below rather than
        # here. That one asks the same question strictly: not "is a verdict
        # written down" but "is it a PASS, filed under the date CLAUDE.md
        # advertises". A loop here accepting `(PASS|FAIL)` anywhere in the file
        # would be a second, weaker owner of one question, green on exactly the
        # record the strict one rejects.

    def test_a_second_replay_for_one_agent_is_an_ambiguity_not_a_tie(
            self, tmp_path):
        """The control for `_replay_sections`, and for the scoping under it.

        Appending a revised replay is the ordinary way to record a re-pinned
        fixture, and it used to leave the record saying two things at once: the
        marker check read the LAST section, the pin and the criterion the FIRST.
        Nothing was red, and the PASS filed against "Replay A" no longer named a
        single fixture. Two silent halves pointing opposite ways is not a
        document to pick from, so the ambiguity is reported.

        The second half is the scoping itself, which the duplicate check alone
        does not prove: a `git show` line planted ABOVE Replay A is not a
        duplicate SECTION at all, and a helper searching the whole record would
        still read it. That is the shape a fetch note or a worked example in the
        intro actually has.
        """
        record = self.RECORD.read_text()

        sections, problem = _replay_sections(record)
        assert problem is None, problem
        assert set(sections) == REVIEW_AGENTS, (
            f"the live record parses to {sorted(sections)}, so every fixture "
            "below is built on something other than the document this scores")

        m = re.search(r"^## Replay [A-Z] — `" + re.escape(self.REPLAY_A)
                      + r"`\n.*?(?=^## )", record, re.M | re.S)
        assert m, "Replay A's section is not where this control splices it"
        revised = m.group(0).replace("Replay A", "Replay C", 1)
        assert revised != m.group(0), "the fixture edit landed nowhere"

        doubled, problem = _replay_sections(record + "\n" + revised)
        assert doubled is None and self.REPLAY_A in problem, (
            "a second replay section for one reviewer collapsed into one "
            f"instead of being reported: {problem!r}")

        # ...and the same appended verbatim, which is what a copy-paste revision
        # looks like before the SHA is edited. Identical text is still two
        # fixtures as far as the record is concerned.
        assert _replay_sections(record + "\n" + m.group(0))[1], (
            "an exactly duplicated replay section was collapsed silently")

        # The scoping. A decoy pin above Replay A, which no duplicate check can
        # see, and the helpers must still read the section's own.
        real_sha, real_path = self._replay_a_pin()
        decoy = "\n```sh\ngit show 0123456789abcdef:agents/lib/decoy.py\n```\n"
        head, sep, tail = record.partition("## Replay A")
        assert sep, "the record no longer opens Replay A with that heading"
        planted = tmp_path / "record.md"
        planted.write_text(head + decoy + sep + tail)
        probe = type(self)()
        probe.RECORD = planted
        assert probe._replay_a_pin() == (real_sha, real_path), (
            "a `git show` line written above Replay A was read as its pin — "
            "the helper is searching the record rather than the section")
        assert probe._replay_a_criterion() == self._replay_a_criterion(), (
            "the criterion moved when text outside Replay A did")

        # ...and the half the section scoping does NOT cover, which is where the
        # decoy actually belongs. A `git show` INSIDE Replay A and outside its
        # recipe is not a duplicate section and not text above the heading — it
        # is the ordinary prose of a replay note, and it won on being first.
        # Planted here it points at an older, still-defective revision, so the
        # fixture guard would have gone green against a blob nobody is handed.
        head, sep, tail = record.partition("## Replay A")
        after = tail.index("\n") + 1
        note = ("\nThe revision this replaced was "
                "`git show ae0f073:agents/lib/tax_computations.py`.\n")
        within = tmp_path / "record-within.md"
        within.write_text(head + sep + tail[:after] + note + tail[after:])
        probe = type(self)()
        probe.RECORD = within
        assert probe._replay_a_pin() == (real_sha, real_path), (
            "a `git show` written in Replay A's own prose was read as its pin — "
            "the helper is searching the section rather than the recipe a human "
            "runs")

        # A recipe naming TWO blobs is refused rather than picked between, on
        # the terms `_replay_sections` refuses two sections for one reviewer:
        # one of them was scored and the record cannot say which.
        two_pins = tmp_path / "record-two-pins.md"
        two_pins.write_text(record.replace(
            f"git show {real_sha}:{real_path}",
            f"git show {real_sha}:{real_path}\n"
            f"git show 0123456:{real_path}", 1))
        probe = type(self)()
        probe.RECORD = two_pins
        with pytest.raises(AssertionError, match="names 2"):
            probe._replay_a_pin()

        # Replay B's two helpers take the same decoy, because they read a
        # GENERATOR out of the record and a fenced `run.sh` heredoc above Replay
        # B is neither a duplicate section nor part of Replay B. The years are
        # deliberately not adjacent to the real ones: a helper reading the decoy
        # would report a fixture nobody built.
        real_years = self._years_from_the_record()
        real_body = self._generator_body()
        decoy_gen = ("\n```sh\nsh agents/lib/run.sh - <<'PY'\n"
                     "YEAR, PRE_OBBBA = 1999, 1998\nPY\n```\n")
        head, sep, tail = record.partition("## Replay B")
        assert sep, "the record no longer opens Replay B with that heading"
        planted_b = tmp_path / "record-b.md"
        planted_b.write_text(head + decoy_gen + sep + tail)
        probe = type(self)()
        probe.RECORD = planted_b
        assert probe._years_from_the_record() == real_years, (
            "a `YEAR, PRE_OBBBA` line written above Replay B was read as the "
            "fixture's years — the helper is searching the record rather than "
            "the section")
        assert probe._generator_body() == real_body, (
            "a fenced `run.sh` heredoc above Replay B was taken for the "
            "acceptance generator, so the corruption audit would run against a "
            "fixture the reviewer is never handed")

        # ...and the same block INSIDE Replay B, which the scoping cannot see
        # and which is what appending a revised generator actually looks like.
        # Two of them is an ambiguity, not a tie: one was executed for the
        # recorded PASS and the record does not say which.
        two_gens = tmp_path / "record-two-gens.md"
        head, sep, tail = record.partition("## Replay B")
        after = tail.index("\n") + 1        # past the rest of the heading line
        two_gens.write_text(head + sep + tail[:after] + decoy_gen + tail[after:])
        probe = type(self)()
        probe.RECORD = two_gens
        with pytest.raises(AssertionError, match="fenced `run.sh` generators"):
            probe._generator_body()

    def _claim_date(self):
        """The advertised date, or None when CLAUDE.md declares no acceptance.

        Only a PROBLEM fails here. Absence with the declaration is a legitimate
        state and the callers skip; absence without it is the silent deletion
        that would make every audit below vacuous.
        """
        date, problem = _acceptance_claim()
        assert problem is None, problem
        return date

    def test_the_advertised_pass_is_backed_by_the_record(self):
        """CLAUDE.md says both reviewers passed. The record is free to disagree.

        The verdict check this replaces accepted `PASS|FAIL` alike, on the sound
        ground that recording a FAIL is the honest outcome of a regressed re-run.
        What it missed is that CLAUDE.md does not record a verdict, it makes a
        CLAIM — so a re-run that regressed, was recorded as FAIL exactly as the
        procedure asks, and left CLAUDE.md alone, went green while the file every
        agent loads went on advertising a reviewer that had just failed its
        acceptance. The honest half of the workflow was the half that broke it.

        So the claim is what the record is held to: whatever CLAUDE.md advertises
        must resolve to an all-PASS results section filed under the date it
        names. A FAIL is still recordable — it just costs the claim, which is the
        correct price and the only one that keeps the two from drifting.
        """
        date = self._claim_date()
        if date is None:
            return                      # nothing advertised, nothing to back
        unbacked = _unbacked(self.RECORD.read_text(), date, REVIEW_AGENTS)
        assert not unbacked, (
            f"CLAUDE.md advertises both reviewers as accepted on {date}, and "
            f"{self.RECORD.name} does not back it: {unbacked}. Either re-run the "
            "replay from a FRESH session and record the pass, or replace the "
            f"claim in CLAUDE.md with `{UNCLAIMED}` — an unvalidated reviewer "
            "advertised as validated is the one failure mode worse than having "
            "no reviewer at all")

    def test_a_recorded_verdict_pins_the_definition_it_judged(self):
        """A verdict is evidence about the BYTES it was dispatched against.

        Edit a definition and every check in this module still passes: the
        frontmatter is intact, the tool grants unchanged, the schema paths still
        resolve. None of those is the agent's behaviour. The behaviour is the
        prompt, and a reworded instruction, a loosened scope or a dropped rule
        moves it past all of them while the recorded PASS — and the CLAUDE.md
        claim resting on that PASS — stay exactly as green.

        This is the stale-definition trap the record already warns about in
        prose, arriving by the other door. The prose one is about a session that
        loaded the wrong definition; this one is about a record that describes
        one. Both end at a PASS for something nothing tested, and neither has a
        symptom, a reviewer running an edited prompt being just as fluent.

        What the pin can show is bounded and the bound is why the fresh-session
        rule stays prose: it proves the verdict names the current bytes, never
        that a fresh session dispatched them. Renewing a pin without re-running
        is therefore possible, and the record says in as many words that doing so
        records a judgement. That is the point — it converts a silent omission
        into a deliberate edit somebody reviews.
        """
        date = self._claim_date()
        if date is None:
            return                      # no verdict is being relied on
        unpinned = _unpinned(self.RECORD.read_text(), date, REVIEW_AGENTS)
        assert not unpinned, (
            f"a recorded verdict does not name what it was dispatched with: "
            f"{unpinned}. If a definition was edited, re-run its replay from a "
            f"FRESH session and renew the pin with `{RENEW_PIN}` — the `tr` "
            "is not optional and this message used to omit it: `_digest` drops "
            "every carriage return before hashing, so on a `core.autocrlf=true` "
            "checkout the bare `shasum` this used to name records a pin that "
            "can never satisfy the audit that printed it")

    def test_the_runbook_names_the_re_run_the_record_points_at_it_for(self):
        """The record's "When to re-run" list names the season-open runbook as
        its first trigger, and CLAUDE.md repeats it. The runbook itself said
        nothing at all — so the one document a maintainer actually follows at
        season open never mentioned the acceptance it is supposed to trigger,
        and the pointer resolved in one direction only.

        That is the shape this whole module is about: a claim resting on
        something nobody checked. Both halves are asserted, because either one
        alone is a pointer to nowhere — a runbook step naming no record, or a
        record naming a runbook that does not carry the step.
        """
        record = self.RECORD.read_text()
        assert "the season-open runbook" in record, (
            f"{self.RECORD.name} no longer names the runbook among its re-run "
            "triggers, so the step in that runbook points at nothing")

        runbook = REPO / "docs" / "ops" / "season-open-runbook.md"
        assert runbook.is_file(), (
            f"{runbook} is gone and the record still names it as the first "
            "trigger for a re-run")
        text = runbook.read_text()
        assert self.RECORD.name in text, (
            f"{runbook.name} does not name {self.RECORD.name}, so the record's "
            "own first re-run trigger is a document that never mentions it — "
            "the acceptance is then re-run only by whoever remembers to")
        assert "fresh session" in text.lower(), (
            f"{runbook.name} names the acceptance without its one procedural "
            "condition: a definition loads at SESSION START, so a same-session "
            "replay tests the bytes the session began with, not the edited ones")

    def test_the_record_documents_the_line_ending_safe_renewal(self):
        """The record's renewal command has to agree with `_digest`.

        It is the one a maintainer actually follows — this module's message is
        only reached once the audit is already red — and the failure it prevents
        has no symptom on the platform most contributors are on. A pin taken
        with a bare `shasum` on an LF checkout is CORRECT; the same command on a
        CRLF one records a digest that no run of this suite can ever accept.
        Pinned textually rather than by execution: `shasum` is a perl script
        Git for Windows ships, so running it to check the pin would be testing
        the platform. Every subprocess this module starts is a `git` one —
        Replay A's pinned blob, the two calls that classify a failure to read it,
        and the fixture repository that controls those — and git is the exception
        that states the rule, being required by every workflow here and by CI's
        own checkout, where `shasum`'s presence is a platform question. This
        module is one of the cheap rows in skill-invariants.sh MEASURED COSTS,
        which is what makes a subprocess proportionally dear in it.
        """
        flat = re.sub(r"\s+", " ", self.RECORD.read_text())
        assert RENEW_PIN in flat, (
            f"{self.RECORD.name} no longer documents the renewal command as "
            f"`{RENEW_PIN}`. `_digest` hashes the definition with every "
            "carriage return dropped, so a command that does not drop them "
            "produces a pin this suite rejects on a `core.autocrlf=true` "
            "checkout — reword the sentence around it, not the command")

    def test_a_quoted_claim_is_not_a_claim(self):
        """CLAUDE.md is allowed to DESCRIBE the withdrawal it prescribes.

        Both of the messages above, and the record's own *If a replay FAILS*
        paragraph, tell a maintainer to replace the pass claim with the
        withdrawal sentence. Writing that instruction into CLAUDE.md — inside the
        backticks that mark it as a quotation, which is how every other rule in
        that file is written — made the parser read the file as stating the claim
        AND its withdrawal at once: three tests red, and the tool-time hook
        blocking the edit, on a document making exactly one claim.

        The failure is the shape this parser already has a branch for. The
        not-accepted state was made writable because a procedure whose honest
        outcome cannot be committed pressures the one person following it
        honestly; a remedy that cannot be WRITTEN DOWN in the file it is a remedy
        for fails the same way, one step earlier.
        """
        live = (REPO / "CLAUDE.md").read_text()
        date, problem = _acceptance_claim(live)
        assert problem is None, problem

        for quoted in (f"\nIf a replay fails, replace the claim with "
                       f"`{UNCLAIMED}`.\n",
                       f"\nThe withdrawal reads:\n\n```md\n{UNCLAIMED}\n```\n",
                       "\nAn earlier record advertised "
                       "`**Both PASSED 2020-01-01**`.\n",
                       # A span holding a backtick, which is why it opens with
                       # two. Closed at the INNER one, the remainder of the span
                       # — the half a quotation puts its text in — was handed
                       # back as prose.
                       "\nAn earlier record advertised "
                       "``x ` **Both PASSED 2020-01-01** y``.\n"):
            assert _acceptance_claim(live + quoted) == (date, None), (
                f"CLAUDE.md documenting a sentence was read as saying it: "
                f"{quoted!r} -> {_acceptance_claim(live + quoted)}")

        # The half that must not move: written as PROSE, both sentences are the
        # file speaking, and both branches still fire.
        #
        # Built from a SYNTHETIC base holding exactly one claim, never from
        # `live`. Appending to the live file makes the fixture's meaning depend
        # on which state CLAUDE.md is currently in, and the two states give it
        # opposite ones: in the PASS state `live + UNCLAIMED` is a claim beside
        # its withdrawal, and in the WITHDRAWN state it is the withdrawal twice,
        # which is not a conflict and is correctly not reported. So this
        # assertion — the control on the escape hatch — fails the moment the
        # escape hatch is TAKEN, and the documented remedy for a failed replay
        # could not be committed after all. That is this file's own subject
        # again: a remedy has to be green in the state it unblocks, and here the
        # thing standing in the way was the test written to prove it was.
        claiming = "**Both PASSED 2020-01-01**\n"
        assert _acceptance_claim(claiming + f"\n{UNCLAIMED}\n")[1], (
            "a withdrawal stated in prose beside a standing claim was not "
            "reported — that is the file advertising two opposite trust signals")
        assert _acceptance_claim(
            claiming + "\n**Both PASSED 2021-01-01**\n")[1], (
            "a second pass claim stated in prose was not reported")
        assert _acceptance_claim(claiming) == ("2020-01-01", None), (
            "the single-claim base this control is built on does not itself "
            "parse as one claim, so neither fixture above means what it says")
        assert _acceptance_claim(f"{UNCLAIMED}\n") == (None, None), (
            "the withdrawn state stopped being committable")
        assert _acceptance_claim("nothing is claimed here\n")[1], (
            "a CLAUDE.md advertising neither sentence was read as a declared "
            "state rather than as the silent deletion it is")

        # The bound on the span rule, stated against `_prose` because through
        # `_acceptance_claim` it depends on how many backticks CLAUDE.md happens
        # to carry. An unclosed run is literal text: reading it as a span would
        # hide every sentence after a typo, which is the fail-open half of the
        # same delimiter question.
        assert "**Both PASSED 2020-01-01**" in _prose(
            "A stray ` backtick, then **Both PASSED 2020-01-01**."), (
            "an unclosed backtick run swallowed the prose after it")
        assert "**Both PASSED 2020-01-01**" not in _prose(
            "``x ` **Both PASSED 2020-01-01** y``"), (
            "a code span holding a backtick leaked its own text as prose")

    def test_the_two_record_audits_discriminate(self, monkeypatch):
        """The control pair. Both audits above are green on a correct record and
        green on a broken audit, and from outside those look identical — this
        file's own recurring finding, one level up from the fixture it opens
        with. So both predicates are run over doctored records whose answers are
        known, and it is the PREDICATES the audits call, not copies of them: a
        control that restates the logic passes against its own restatement while
        the audit runs on something else.
        """
        record = self.RECORD.read_text()
        dates = _record_dates(record)
        assert dates, ("the record files Results under no date at all — this "
                       "control has no section to doctor")
        # The section the audits themselves would read: the one CLAUDE.md names,
        # or the most recent when acceptance is withdrawn and it names none.
        #
        # Requiring the record to hold exactly ONE dated section — which this
        # asked for until Codex pointed at it — is a constraint neither audit
        # has. `_results_section` selects by date and ignores every other
        # section, which is precisely what lets a later re-run be filed under a
        # new date with the old one KEPT as history. Forbidding that here made
        # the distinct-date workflow uncommittable, and it is the workflow
        # `_results_section`'s own ambiguity message recommends: told that two
        # same-dated sections cannot say which backs the claim, the maintainer
        # dates them distinctly and lands on this assertion instead. A guard
        # that blocks the remedy it prints is worse than no guard.
        #
        # Every fixture below is therefore built by doctoring THAT section and
        # splicing it back, never by editing the whole file. A `.replace(…, 1)`
        # over a record with history lands on the first match, which is in the
        # superseded section — the audits read the target section, see the
        # undoctored text, and the control fails on a fixture edit that went
        # somewhere else entirely.
        date = self._claim_date() or max(dates)

        def locate(text):
            m = re.search(
                rf"^## Results — {re.escape(date)}\s*$\n(.*?)(?=^## |\Z)",
                text, re.M | re.S)
            assert m, (
                f"the record files no `## Results — {date}` section, so there "
                "is nothing for this control to doctor and nothing for the "
                "audits to read either")
            return m

        # A superseded run, filed under an earlier date and kept — the history
        # the paragraph above is about. Prepended so every fixture below is
        # built on a record that HAS one, which is what makes the section
        # scoping testable rather than merely written down: the copy is
        # textually identical to the claimed section, so a fixture edit that
        # searches the whole file lands HERE, the audits read the claimed
        # section, see it undoctored, and the assertion that the edit was
        # detected fails. Doctor the wrong section and this control says so.
        record = f"## Results — 2000-01-01\n{locate(record).group(1)}\n{record}"
        span = locate(record)

        def spliced(section):
            """`record` with the section dated `date` replaced by `section`."""
            return record[:span.start(1)] + section + record[span.end(1):]

        # ...and the audits ignore it, which is the property that makes keeping
        # history legal at all. Asserted before anything is doctored, so a
        # failure here reads as the audits reaching across sections rather than
        # as a fixture problem.
        assert not _unbacked(record, date, REVIEW_AGENTS), (
            "a section filed under another date was read as the claim's own")
        assert not _unpinned(record, date, REVIEW_AGENTS)

        # Every verdict forced to PASS, so the baseline is all-PASS whatever the
        # record currently says. A control that assumed the live record was green
        # would go red the day a replay legitimately failed — the withdrawn state
        # this file documents — and it would go red as a CONTROL failure, which
        # reads as the audits having broken rather than as a replay having.
        section = re.sub(r"^(### `[\w-]+` — )FAIL", r"\1PASS", span.group(1),
                         flags=re.M)
        baseline = spliced(section)
        assert not _unbacked(baseline, date, REVIEW_AGENTS)
        assert not _unpinned(baseline, date, REVIEW_AGENTS)

        name = "bias-direction-reviewer"
        regressed = section.replace(f"### `{name}` — PASS",
                                    f"### `{name}` — FAIL", 1)
        assert regressed != section, "the fixture edit landed nowhere"
        assert _unbacked(spliced(regressed), date,
                         REVIEW_AGENTS) == [f"{name}: FAIL"], (
            "a recorded FAIL still backs the CLAUDE.md pass claim")

        dropped = section.replace(f"### `{name}` — PASS\n", "", 1)
        assert dropped != section, "the verdict-removal fixture edit landed nowhere"
        assert _unbacked(spliced(dropped), date, REVIEW_AGENTS) == [
            f"{name}: no verdict recorded"], (
            "an agent with no verdict at all still backs the claim")

        assert _unbacked(baseline, "1999-01-01", REVIEW_AGENTS), (
            "a claim naming a date the record never ran was read as backed")

        # Two runs on one calendar day, the later one appended. Reading the
        # first section leaves both audits validating the superseded PASS.
        # Distinct dates are the documented way out and are NOT an ambiguity —
        # the assertion at the top of this control exists to keep that true.
        doubled = (baseline + f"\n## Results — {date}\n\n"
                   f"### `{name}` — FAIL\n\nthe re-run regressed.\n")
        for audit in (_unbacked, _unpinned):
            problems = audit(doubled, date, REVIEW_AGENTS)
            assert len(problems) == 1 and "2 `## Results" in problems[0], (
                f"{audit.__name__} picked one of two same-dated results "
                f"sections instead of reporting the ambiguity: {problems}")

        # Two verdicts for ONE reviewer inside the claimed section, which is
        # what recording a re-run IN PLACE produces. Both audits index the
        # section by agent name through a `dict`, and a dict built from
        # `re.findall` keeps the LAST pair — so the FAIL written above the
        # standing PASS resolved, silently and in both audits at once, to the
        # PASS. Asserted in BOTH orders: with the FAIL last the collapse
        # happened to give the right answer, which is the reading that makes a
        # contradictory record look like a working guard.
        for doctored in (
                section.replace(f"### `{name}` — PASS",
                                f"### `{name}` — FAIL\n\nregressed.\n\n"
                                f"### `{name}` — PASS", 1),
                section.replace(f"### `{name}` — PASS",
                                f"### `{name}` — PASS\n\nsuperseded.\n\n"
                                f"### `{name}` — FAIL", 1)):
            assert doctored != section, "the doubled-verdict edit landed nowhere"
            for audit in (_unbacked, _unpinned):
                problems = audit(spliced(doctored), date, REVIEW_AGENTS)
                assert (len(problems) == 1
                        and "more than one verdict" in problems[0]), (
                    f"{audit.__name__} collapsed two verdicts for one reviewer "
                    f"instead of reporting the ambiguity: {problems}")

        # A CRLF checkout is the SAME definition. Hashing raw bytes failed both
        # agents on an untouched Windows tree, and that failure is textually the
        # one this pin exists to raise.
        lf = b"---\nname: a-reviewer\n---\n\nReview nothing.\n"
        assert _digest(lf) == _digest(lf.replace(b"\n", b"\r\n")), (
            "the pin reads a line ending as an edit to the prompt")
        assert _digest(lf) != _digest(lf.replace(b"nothing", b"everything")), (
            "the pin is blind to the content it exists to pin")

        edited = section.replace(_live_digest(name), "0" * 64, 1)
        assert edited != section, "the digest fixture edit landed nowhere"
        assert len(_unpinned(spliced(edited), date, REVIEW_AGENTS)) == 1, (
            "a verdict pinning the wrong definition bytes passed the audit")

        # Matched ACROSS the line break, never as "line, then digest line". A
        # pin is one sentence the record may wrap wherever it reads best, and it
        # has already been rewrapped once; a fixture edit coupled to the wrapping
        # lands nowhere the next time, and an edit that lands nowhere doctors
        # nothing — the control then passes on the record as written and says
        # the audit rejected a defect it was never shown. The two `assert
        # ... != section` lines below exist because that happened.
        stripped = re.sub(r"\*\*Definition\.\*\*[^*]*?`[0-9a-f]{64}`\.\n",
                          "", section)
        assert stripped != section, "the pin-removal fixture edit landed nowhere"
        assert (len(_unpinned(spliced(stripped), date, REVIEW_AGENTS))
                == len(REVIEW_AGENTS)), (
            "a verdict pinning no definition at all passed the audit")

        # The MODEL field, on the same terms. A dispatch takes its model from the
        # session, so no digest here can see a switch — the field is the only
        # record of it, and an audit that stopped reading the field is green in
        # exactly the way a correct record is.
        def only(text, when):
            """A minimal record holding `section` under the date `when`."""
            return f"## Results — {when}\n{text}"

        unmodelled = re.sub(r"\*\*Model\.\*\* [^\n]*\n", "", section)
        assert unmodelled != section, "the model-removal fixture edit landed nowhere"
        assert (len(_unpinned(spliced(unmodelled), date, REVIEW_AGENTS))
                == len(REVIEW_AGENTS)), (
            "a verdict recording no model at all passed the audit")

        # ...and the exemption is by DATE and closed, never a default. The same
        # verdicts read under a date nobody grandfathered must report, or the
        # requirement is retired for every future run by the one that predates
        # it.
        later = "2999-01-01"
        assert later not in MODEL_UNRECORDED_DATES, (
            "this control's date was grandfathered, so it proves nothing")
        # Built, not read off the record: the 2026-08-27 verdicts were the
        # only ones for a while and they read "Not recorded", so this control
        # once used `section` as it stood — and went red the day a later
        # section named its model, having asserted a fixture shape the record
        # no longer had.
        unrecorded = re.sub(r"\*\*Model\.\*\* [^\n]*",
                            "**Model.** Not recorded — see above.", section)
        assert "**Model.**" in section, "the section carries no model line to unrecord"
        assert "**Model.** Not recorded — see above." in unrecorded, (
            "the model-unrecording fixture edit landed nowhere")
        assert (len(_unpinned(only(unrecorded, later), later, REVIEW_AGENTS))
                == len(REVIEW_AGENTS)), (
            "`Not recorded` was accepted under a date outside "
            "MODEL_UNRECORDED_DATES, so the exemption is a default")
        named = re.sub(r"\*\*Model\.\*\* [^\n]*",
                       "**Model.** `a-model-id`.", section)
        assert not _unpinned(only(named, later), later, REVIEW_AGENTS), (
            "a verdict that DOES name its model was reported anyway")

        # The arm that binds a `model:` pinned in a definition, which neither
        # carries today — patched in, because an arm nothing exercises stays
        # wrong for as long as the field stays absent, and the whole point of
        # writing it now is that pinning the field later is the real fix.
        monkeypatch.setattr(f"{__name__}._frontmatter",
                            lambda path: {"model": "pinned-model"})
        assert _unpinned(only(section, date), date, REVIEW_AGENTS), (
            "a definition pinning a model was satisfied by a verdict that "
            "records none, though the grandfathering cannot apply to a model "
            "the definition itself names")
        assert _unpinned(only(named, later), later, REVIEW_AGENTS), (
            "a verdict naming a model the definition contradicts passed — that "
            "pair describes a dispatch that cannot happen")
        agreed = re.sub(r"\*\*Model\.\*\* [^\n]*",
                        "**Model.** `pinned-model`.", section)
        assert not _unpinned(only(agreed, later), later, REVIEW_AGENTS), (
            "a verdict naming exactly the model its definition pins was "
            "reported, so the arm rejects the agreeing case too")

        # The INPUT pin's predicate, driven the same way. Its two callers build
        # a real fixture and pass its digest, so on a correct tree they only ever
        # exercise the AGREEING case — the comparison itself is unexercised, and
        # an unexercised comparison is the vacuity this whole class is about.
        # Here the digest is synthetic and the record is doctored, so all three
        # arms run.
        # Scoped to `name`'s own verdict: the section holds one pin per
        # reviewer and an unscoped search reads the FIRST, which is the other
        # agent's — the control then feeds one reviewer's digest to the other's
        # verdict and fails on a correct record.
        own = re.search(rf"^### `{re.escape(name)}` — PASS\n(.*?)(?=^### |\Z)",
                        section, re.M | re.S)
        assert own, f"{name} has no verdict in the section this control doctors"
        pin = re.search(r"\*\*Input\.\*\* [^*]*?sha256\s+`([0-9a-f]{64})`",
                        own.group(1), re.S)
        assert pin, ("the section files no input pin, so this control has "
                     "nothing to doctor and the two fixture tests are scoring "
                     "against a verdict that names no fixture")
        assert not _input_unpinned(spliced(section), date, name, pin.group(1)), (
            "a verdict whose input pin MATCHES what the replay built was "
            "reported anyway")
        assert _input_unpinned(spliced(section), date, name, "0" * 64), (
            "a verdict pinning an input the replay did not build passed — two "
            "revisions can both carry the defect, which is the whole reason "
            "this pin exists beside the fixture's own corruption check")
        no_input = re.sub(r"\*\*Input\.\*\*[^*]*?`[0-9a-f]{64}`\.\n",
                          "", section)
        assert no_input != section, "the input-removal fixture edit landed nowhere"
        assert _input_unpinned(spliced(no_input), date, name, pin.group(1)), (
            "a verdict pinning no input at all passed the audit")

        # ...and the pin WRAPPED, which is the shape both predicates used to
        # verify and the PII gate cannot strip. Doctored by breaking the line
        # after `sha256 ` — one character of drift from how the `**Input.**`
        # sentences already wrap — so the flattened match still finds a valid
        # 64-hex pin and only the raw line shape differs. Both predicates are
        # driven, because the two carry the rule separately and a fix applied to
        # one is the sibling sweep this repository keeps re-learning.
        for wrapped, audit in (
                (section.replace(f"sha256 `{pin.group(1)}`",
                                 f"sha256\n`{pin.group(1)}`", 1),
                 lambda sec: _input_unpinned(spliced(sec), date, name,
                                             pin.group(1))),
                (section.replace(f"sha256 `{_live_digest(name)}`",
                                 f"sha256\n`{_live_digest(name)}`", 1),
                 lambda sec: _unpinned(spliced(sec), date, REVIEW_AGENTS))):
            assert wrapped != section, "the wrapped-pin fixture edit landed nowhere"
            problem = audit(wrapped)
            assert problem, (
                "a pin wrapped across lines verified, so the hook reports green "
                "on a renewal the PII gate then blocks as an SSN")
            assert "ONE line" in str(problem), (
                f"the wrapped pin is reported as something other than a wrap: "
                f"{problem}")

        # ...and the OTHER shape the gate scans, which the well-formedness test
        # could not see at all. `strip_artifact_digests` drops the exempt token
        # per occurrence, so `sha256 `<new>` (was `<old>`)` leaves `<old>`
        # standing to be read like any other line — and a 64-character digest
        # carries a run of exactly nine bare digits often enough to matter.
        # Replay A's current one does, which is why this is the shape
        # tests/test_pii_content_scan.py names as the one that slipped through.
        # Both predicates again, for the reason the loop above drives both.
        old_pin = "0123456789" + "a" * 54
        assert LOOSE_DIGEST.fullmatch(old_pin), "the stale-pin fixture is not a digest"
        for kept, audit in (
                (section.replace(f"sha256 `{pin.group(1)}`",
                                 f"sha256 `{pin.group(1)}` (was `{old_pin}`)", 1),
                 lambda sec: _input_unpinned(spliced(sec), date, name,
                                             pin.group(1))),
                (section.replace(f"sha256 `{_live_digest(name)}`",
                                 f"sha256 `{_live_digest(name)}` "
                                 f"(was `{old_pin}`)", 1),
                 lambda sec: _unpinned(spliced(sec), date, REVIEW_AGENTS))):
            assert kept != section, "the kept-old-pin fixture edit landed nowhere"
            problem = audit(kept)
            assert problem, (
                "a renewal that keeps the old digest beside the new one "
                "verified, so the hook reports green and the commit is then "
                "blocked as an SSN by a gate that names neither")
            assert "OUTSIDE the exempt" in str(problem), (
                f"the stray digest is reported as something else: {problem}")

        # The predicate itself, on the record as written: nothing survives the
        # gate's own strip. The two audits above are per-VERDICT and the gate
        # reads the whole file, so a digest in the prose between sections is a
        # block neither of them can see.
        assert _digest_the_gate_scans(self.RECORD.read_text()) is None, (
            "a 64-hex digest in the record sits outside the exempt "
            "`sha256 `<64 hex>`` token, so the PII gate scans it and the "
            "commit is blocked — write every digest in that one shape")

    def test_the_acceptance_claim_parser_discriminates(self):
        """The control for the step BEFORE both audits — which date they run on.

        The two above are held to a claim, so a parser that reads the wrong claim
        moves them wholesale onto a question nobody asked and they answer it
        correctly and greenly. That is the same silent pass, one level further
        up, and the same reason the control feeds `_acceptance_claim` itself
        rather than a restatement of its regex.
        """
        live = (REPO / "CLAUDE.md").read_text()
        assert _acceptance_claim(live) == (self._claim_date(), None), (
            "the parser disagrees with itself about the live CLAUDE.md")

        # Synthetic from here down, and that is the fix for a control that used
        # to build its fixtures by APPENDING to the live file. Doing so made each
        # case depend on which state CLAUDE.md happens to be in: the doubled-claim
        # fixture stops being a doubled claim the day the live file carries a
        # withdrawal instead, and the control then asserts the wrong message
        # about the right verdict.
        one = "prose **Both PASSED 2026-08-27** more prose"

        date, problem = _acceptance_claim(one + "\n**Both PASSED 2029-01-01**\n")
        assert date is None and "2 `**Both PASSED" in problem, (
            f"an appended second claim was resolved to one date instead of "
            f"reported: {date!r}, {problem!r}")

        # A claim and a withdrawal are the two halves of one fact, and adding the
        # second without removing the first is how a withdrawal gets written. The
        # date used to be returned before the withdrawal was even looked for, so
        # the audits validated the stale PASS and the file said both things.
        date, problem = _acceptance_claim(f"{one}\n{UNCLAIMED}\n")
        assert date is None and UNCLAIMED in problem and "2026-08-27" in problem, (
            f"CLAUDE.md advertising an acceptance AND a withdrawal resolved to "
            f"one of them: {date!r}, {problem!r}")

        date, problem = _acceptance_claim("no claim here")
        assert date is None and "states neither" in problem, (
            f"a CLAUDE.md making no claim at all parsed as one: {date!r}")

        # The third outcome, and the reason it is a DECLARATION. A replay may
        # legitimately fail, and the audits above tell the maintainer to take the
        # claim out of CLAUDE.md — which used to turn this red, so the documented
        # honest state was the one state nobody could commit. Saying it out loud
        # is accepted; saying nothing still is not, or one deletion would retire
        # the whole apparatus with a green suite.
        assert _acceptance_claim(f"...\n{UNCLAIMED}\n") == (None, None), (
            "CLAUDE.md declaring the reviewers unaccepted was read as a problem")
        assert _acceptance_claim("")[1], (
            "silence was accepted as a declaration that nothing is claimed")

        # Nothing downstream may CRASH on the absent date either. The audits skip
        # when acceptance is withdrawn, so this is defence rather than a path in
        # use — but the crash it replaces was a TypeError from `re.escape`, which
        # reads as the module being broken rather than as a claim being absent.
        for audit in (_unbacked, _unpinned):
            problems = audit(self.RECORD.read_text(), None, REVIEW_AGENTS)
            assert problems == ["no advertised date to check the record against"], (
                f"{audit.__name__} raised or reported oddly on an absent date: "
                f"{problems}")
