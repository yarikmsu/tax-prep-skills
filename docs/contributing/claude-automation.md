# Claude Code automation — hooks and subagents

Relocated out of `CLAUDE.md` by Y5 (PR #113). That file is loaded into every session
before any skill, so it keeps the ALARMS — the rules an agent breaks without ever
thinking to look something up — and this file holds the ANSWERS.

**So this file does not restate a rule the instruction file kept; it says why the rule
is what it is.** Read the two together. Where a passage here looks like it is missing
its conclusion, the conclusion is the rule, stated once — and a second statement of it
here is how the two would start to disagree, which is the same argument the three-way
mirror below makes about a fourth copy, applied to this file.

Both mechanisms are driven by `tests/test_claude_hooks.py` and
`tests/test_claude_agents.py`, real scripts and failing paths included: **a hook that
always passes and one that never fires look identical from outside.**

---

## The two `PostToolUse` hooks

`.claude/settings.json` is tracked, and registers both. The tool-time/commit-time split
is why they do not replace the git hooks and the git hooks do not replace them:
everything `harden-runtime-perms.sh` protects is gitignored, so a commit hook never sees
it at all, and nothing here runs for an agent that is not Claude Code.

**Both re-run themselves under bash's privileged mode (`-p`)**, in the form the PII scan
uses and explains (`pii-gate.md`). Bash imports every function exported into its
environment, and runs the file `BASH_ENV` names, before a script's first line, and a
function runs ahead of the PATH lookup. So a stub `chmod` in a contributor's shell left
`result.json` world-readable behind an exit 0, and a stub `jq` left the skill hook's
payload unread over a red suite. Privileged mode imports no function and reads no
`BASH_ENV`. The permissions hook re-runs first; the skill hook second, after the
statement that turns an inherited `xtrace` off, so the one traced line its test allows
stays one. The functions stay in the environment, so neither covers what it starts, and
the skill hook's verdict comes from a suite that starts bash. The permissions hook also
loses its own sight of them, so before trusting `echo` as a bounded write or `ls` as a
read it asks whether that name arrived as a function. Any `BASH_ENV` counts as yes,
since the hook's own start already ran it and a second run can come out different;
for an exported function, a child bash answers, importing it as the agent's shell
did. Bash 3.2, the
macOS `/bin/bash`, still reads `GLOBIGNORE` and `CDPATH` in that mode; the skill hook
clears `CDPATH` itself.

### `skill-invariants.sh` (`Write|Edit|Bash`, timeout 480s)

**It fires on CHANGE, not on state.** A Write/Edit is filtered by its path. A Bash
payload is opaque — the hook cannot read a command and know what it touched — so it is
filtered by `.claude/.skill-invariants-stamp` plus one `find -newercm`, and exits 0
without starting pytest when nothing watched moved. That fast path — the first row of
`skill-invariants.sh` MEASURED COSTS, under a thousandth of a cold pass — is what stops
`ls` and `git status` from each buying a pytest run.

**It times its own pass (AA9).** Past its registered timeout the hook is CANCELLED, and
a cancelled check reads to the agent exactly like a pass. When a pass uses three
quarters or more of the timeout `settings.json` registers for it, the hook says so —
exit 1, surfaced and not blocking, after the stamp has advanced — naming that pass's own
seconds. It reads the timeout from `settings.json` rather than restating it, so it is a
comparison against a threshold and never a recorded reading; a timeout it cannot read
is no warning.

It then runs only the CHECKS the changed paths can turn red: seven test modules, or a
node-id subset of one of them. Two tables decide that, and CLAUDE.md states the rule for
adding to them; what the rule is protecting:

- `_ARMS`, in `.claude/hooks/lib/hook_selection.py`, maps the changed path to modules,
  so it is per-PATH and never per-directory-that-feels-related. The arms are ordered and
  the FIRST match wins, which is why a file with its own arm does not fall through to
  its directory's. The script hands the whole scan to that port in one process and
  fails CLOSED on any answer it cannot use — a missing selector, a dead interpreter, or
  a module name the script does not declare all run EVERY module rather than none.
  It was 218 lines of `case` in the script itself until W3 slice 2.
- `WATCHED_ROOTS` is where the `find` sweep starts, and entries are ROOTS rather than
  paths — `agents` already reaches `agents/lib`, so adding the deeper path buys a second
  traversal and nothing else. The bare directory matters on its own: it is what a
  DELETE, or an ADD, looks like to `find`.

Half-covering is the shape to avoid, and its worse direction is a path in
`WATCHED_ROOTS` with no arm: visible to a Bash call, selecting nothing, reading as
covered.

Exit 0 silent, 2 blocking with the failing tail, 1 when TERM/INT/HUP killed the pass —
that one has no verdict, so it must not block — and 1 also for a green pass that used
three quarters of its timeout (AA9), with the warning on stderr where the signal's is
empty. The hook no-ops without `jq` or a
`.venv`.

### `harden-runtime-perms.sh` (`Write|Edit|NotebookEdit|Bash`, timeout 60s)

Re-asserts AGENTS.md §1's `chmod 600` on the runtime artifacts and its `chmod 700` on
the runtime directories. `Bash` is in the matcher because `result.json` is written by a
script, not by an editor tool.

**It times its own walk (AA24)**, as the skill hook does its pass: when a walk uses
three quarters or more of the timeout `settings.json` registers for it, it says so after
hardening and advancing the stamp, with the same non-blocking exit 1. A cancelled walk
leaves exposed whatever it had not reached, and AA4's test pins the walk's shape in
chmods, not its cost.

It exits 1 to surface a failed permission change without blocking the call, and never
the blocking exit code 2: a permission it could not set is a warning to a human, not a
reason to refuse the edit that prompted it.

**Its archive net asks `find` for wrong MODES, not `-newercm "$STAMP"`.** A drift that
a stamp-filtered scan misses once falls behind the advancing stamp forever — the file
is older than every subsequent boundary, so nothing looks at it again. Scanning for the
wrong mode has no such memory.

### Why the stamps turn on a verdict

CLAUDE.md gives the advance-or-hold rule. What it is buying:

**Advancing on RED** is the counter-intuitive half. If a failure held the boundary, the
`git diff` and `pytest` a person runs to diagnose it would each re-trigger the same
failing pass — the diagnosis gated behind the thing being diagnosed. So a failure is
reported once, and the work of fixing it is free.

**Holding when the hook could not RUN** is the other half, and a chmod that FAILED
belongs there rather than with the verdicts: it ran, and it produced something that
reads like an answer, but it did not establish anything about the tree.

Red is REMEMBERED — stamp v2 is a version line, then one line per red check — and
unioned into the next pass AFTER the fast path, so a quiescent call stays free. Green
clears it.

**Advance or hold, never a third state.** The archive manifests' mid-publish flag is
`.archive-index-dirty`'s PRESENCE, not this stamp's ABSENCE. Unlinking the stamp to
signal that ran on every opaque Bash call, and put one symbol in charge of the archive
pair's consistency and of every unrelated tree's change boundary at once.

### Why every timing lives in the `MEASURED COSTS` block

The block is in `skill-invariants.sh`, and CLAUDE.md gives the rule about quoting from
it. The rule is absolute rather than a preference because the change that introduced it
shipped THREE contradictory sets of one quantity, in three files, each reading as
current.

The exemption for a threshold the code compares against is not a loophole: a budget is
CHOSEN above the measurements and then enforced on them, so it is an input to the block
rather than a copy of it. The test that tells the two apart is direction — if the number
would change when the machine changes, it is a measurement.

The block records counts, ratios and ordering, and no absolute seconds (ruling Q13 in
`docs/roadmap/BACKLOG.md`): the maintainer's host could not reliably meet the idle-machine
precondition an absolute needs, and nothing that read the block needed one. HEADROOM is
enforced by a test that derives its floor from the collected count, not by a reading. An
overrun does not fail the hook; it CANCELS it, silently, which is indistinguishable from a
pass, so a change to one of the thresholds MEASURED COSTS names re-times one cold pass.

### Why the `.claude/` artifacts need three lists

CLAUDE.md names the three and the rule. Each covers a case the others cannot:

- The tracked **`.gitignore`** covers this clone.
- **`IGNORE_PATTERNS`** in `agents/install.sh` covers a workspace whose `.gitignore` is
  OLDER than the artifact, or missing — which is every clone made before the artifact
  existed, and the only reason this is not one list.
- The **PII gate** covers the commit itself, and is the only one that fails closed.

A test pins the first two as SETS, in both directions, so neither a quiet addition nor a
quiet removal can move one without the other. Nothing pins the third to them, because
the gate's `.claude/` case is scoped differently — there the directory is the scope and
the basename is the rule.

The `*` suffix is about `mktemp`: the hooks write a sibling and rename it over the real
name, so a hook killed mid-write leaves the sibling holding a name the bare pattern does
not match. On `.archive-index-dirty`, written in place and holding nothing, the glob is
merely defensive — it costs nothing and stating one convention beats stating two.

---

## The three subagent definitions

There are two acceptance regimes, and every definition is in exactly one — a rule
CLAUDE.md states and `tests/test_claude_agents.py` enforces. The regimes differ because
what they are accepting differs: a reviewer forms a JUDGEMENT, which can only be tested
by replaying a known answer, while the worker forms none and is accepted on its grant.

### Two read-only reviewers

CLAUDE.md states what neither is granted. Each exists for a gap nothing else covers:

**`tax-law-freshness-auditor`** is the only check on AGENTS.md §2. Nothing else can be:
`tax-review` audits the aggregated result and cannot see that a bracket was right for
last year, and the tie-out compares the ledger to the result, so correct parsing of a
wrong figure ties out perfectly.

As the only agent with network access it is held to a closed minimal tool set — a shell
plus an outbound connection in a workspace holding a real return being exactly what §1
forbids. That bound is enforced by the GRANT; "read `tax_law_<year>.json` and nothing
else" is only prompt-level, and the difference is the whole point.

V7 moved that sentence out of the prompt and into the filesystem. `tax_law_export.py`
copies the cache into a fresh directory holding nothing else and the auditor is launched
there, so the workspace is not somewhere it declines to look — it is somewhere it is not.
That is the V6 pattern rather than a new one: the parent extracts and hands over only what
the sub-task needs.

**The copy is FAITHFUL, and the first draft got that exactly wrong.** It filtered the
cache against the schema — and this agent's job includes reporting keys the schema does
not define. Its item 6 is "nothing statutory-fixed has leaked in"; its item 8 requires
`state.type` as a positive declaration. A cache falsely calling Colorado a no-income-tax
state reached the auditor as a clean generic block, because the filter had removed the
thing the finding was made of. A sanitiser that resolves findings on behalf of the
reviewer it feeds is worse than no sanitiser, and the failure is invisible: the report
said DROPPED and the audit said clean.

So nothing is removed. The export REFUSES when the cache holds anything SSN- or
credential-shaped — in a KEY as well as a value, since `{<an SSN>: "Jane"}` puts the
value in the key position where a value-only scan never looks — and copies verbatim
otherwise. Identity data never reaches the copy because there is no copy. What it does
NOT check is freshness, schema validity, or source authority: the auditor's own items 3
and 4 do the last of those, better than a regex would.

**It isolates nothing unless `TAX_WORKSPACE` is set.** Unset, the workspace is the clone,
and the definition tells the agent four times to read `agents/lib/tax_law_fetcher.py` for
the schema — so handing it the schema hands it the directory holding
`taxpayer_profile.json`. Y4 is a precondition here, not a neighbour.

**`bias-direction-reviewer`** is scoped to `agents/lib/` diffs and instructed to report
NOTHING that is not a directional-bias or unstated-precondition finding. The general
review already happens elsewhere, and a second general reviewer is noise that buries the
one thing this agent is for.

### One parse worker

**`tax-parse-worker`** (V6) holds a CLOSED grant containing nothing that reads, writes,
runs or fetches. The parent extracts through `parse_utils.py` and hands over the masked
text, so a PDF's injected instruction cannot be acted on whatever it says.

It is enforced by ABSENCE, which on this host is the one mechanism that fails closed: a
`PreToolUse` hook that times out lets the call through, `permissions` rules are
session-wide rather than per-agent, and `Read` is measurably NOT directory-confined.
Probed, not assumed.

It gets no acceptance replay because it forms no judgement — its acceptance is the grant
assertion plus the probe in `docs/ops/review-subagent-acceptance.md`. Hosts without a
closed sub-task tool set take the SEQUENTIAL path, banner-marked NOT enforced. The
shared contract is `docs/contributing/parse-boundary.md`.

### Acceptance before trusting either reviewer

Plausibility is not evidence about a reviewer: a definition that has been broken still
produces findings that read well. Only a replay whose correct answer is known in advance
distinguishes the two — and it has to run in a session started AFTER the edit, since
definitions are read once at session start.

**The acceptance state is claimed in exactly one place, and this file is not it.** That
is load-bearing rather than tidiness: `docs/ops/review-subagent-acceptance.md` repairs a
FAILED replay by replacing the instruction file's `**Both PASSED <date>**` with
`**Neither reviewer is currently accepted**`, and `tests/test_claude_agents.py` guards
that file for holding exactly one such claim. A second copy here would go on advertising
a pass the repair had just withdrawn — the stale trust signal that guard exists to make
impossible, arriving through the documentation that was supposed to reduce drift.

The recipe, the pre-committed criteria and the traps live in
`docs/ops/review-subagent-acceptance.md` — including that a squash merge leaves nothing
in `<sha>^`, so `--grep=P1` is the wrong search. Re-run before the season-open runbook.
