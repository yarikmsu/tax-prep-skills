# Epic W — Engineering hygiene and maintainability

Backlog: `docs/roadmap/2026-09-05-staff-agent-review.md` (adopted 2026-09-05).
Step 9 of that file's sequencing: W1, W4 and W5 as a single hygiene PR, takeable
at any point. Taken ahead of step 4, which is blocked on the Part 5 rulings.

## W1 + W4 + W5 — Lint, types, the slow marker and the interpreter pin — PR #90

**Defect:** no linter, no type checker, no `pyproject.toml`, and the local venv
follows whatever `python3` is — 3.14 on a current macOS, against a lock compiled
for 3.12, which AGENTS.md §7 itself warns about. Twelve pyflakes findings sat on
`main` unnoticed, one of them a `pytest.skip` in a module that never imported
`pytest`: on a machine without `jq` that test raised `NameError` instead of
skipping, and the only machines that ran the line were the ones where it was
never reached.

**Shipped:**
- `pyproject.toml` — tool configuration only, with no `[project]` table: this
  repository is not a distributable package, and `agents/lib/__init__.py`
  refuses to import outside the workspace virtualenv, which a wheel installed
  elsewhere would defeat.
- **ruff, at the pyflakes-equivalent set (`F`) and nothing wider.** The default
  selection reports about nine hundred findings here, nearly all of them style
  opinions nobody has ruled on; a gate that fires nine hundred times on the day
  it lands is the gates-that-cry-wolf shape PR #69 already paid for. The 52 `F`
  findings were cleared in the same commit that turned it on. Widening is one
  rule family at a time, with that family fixed in the same change.
- **mypy, gradual, with a baseline that is a LIST OF MODULES.** Annotated code
  is checked and unannotated code is not, so the engine does not have to be
  typed before the gate can run. Eleven modules carry `ignore_errors` with the
  count they had when the gate landed; every module NOT on that list is
  enforced, so a new annotated contract in one of them fails CI, and a module
  leaves the list by being cleared. A suppressed rule set would have hidden the
  same errors everywhere instead.
- **The `F` findings, cleared.** Eighteen unused imports, twenty-nine f-strings
  with no placeholders, four unused locals, and the undefined `pytest` above.
  The two `se_earnings` locals in the SE-tax tests became the worksheet comments
  they always were, rather than being deleted: the derivation is what AGENTS.md
  §8 asks a changed assertion to carry.
- **`slow` marker (W4)** on `tests/test_claude_hooks.py` and
  `tests/test_preflight.py`, the two modules that execute real scripts in a
  subprocess once per case. `-m "not slow"` is the engine-only run — 3,165 of
  the suite's cases, against 505 skipped. It is an iteration convenience, not a
  gate: CI runs everything, and the marker skips the modules, not the risk.
  pytest-xdist is NOT adopted here — the hook module's cases share the stamp
  file, the git worktree and the process cwd, so they have to be isolated before
  a trial means anything, which is the order W4 itself prescribes.
- **`.python-version` (3.12) + a `uv` recipe (W5).** `uv venv` reads the pin and
  `uv pip sync requirements.txt` installs exactly the lock with its hashes
  verified. CONTRIBUTING.md carries both; AGENTS.md is at the 32 KiB chain cap
  Codex truncates at, so nothing was added there.
- **CI gains a `lint` job** on 3.12, with `ruff` and `mypy` PINNED — a linter is
  the one dependency whose new release adds findings, so an unpinned one turns
  an unrelated PR red on a rule nobody here has ruled on. W6 folds them into the
  hashed dev lock that covers pytest too.

**Not in scope, deliberately:** W2 (splitting the monoliths) and W3 (the hook
state machine) are separate items; the mypy baseline names what they would have
to clear. The 131 mypy findings behind the baseline are unreviewed — some look
like real defects (`crypto_importer.py:562` assigns a float where a dict is
declared) and each deserves its own change, not a bulk fix under a hygiene PR.

**Review round (Codex, local, against `main`):** one finding, fixed. The `uv`
recipe stopped at `uv pip sync`, which makes the environment EXACTLY the lock —
the point of it, and also why it removes a test runner the lock does not pin. A
contributor following the recipe had no pytest and could not run the test
command three sections below it. `uv pip install pytest` now follows the sync,
the way the venv recipe above it already installs pytest as its own step.


## W6 — CI gaps, and the two Windows defects the job found

CI ran on `pull_request` alone, on Ubuntu, with the test runner and the linters
installed unpinned.

**Why the widening was blocked.** `github.event.pull_request.base.sha` is EMPTY
on every event except `pull_request`, and the PII gate's range was that
expression inlined. Adding a push or schedule trigger would have handed the
scanner `...HEAD`. It fails closed, so this was never a live hole — but "the
gate blocks every push" and "the gate scans the right range" are different
outcomes, and only the second is a working CI. Logic in a workflow expression
can only be run on a runner, so the range moved into `scripts/ci-pii-range.sh`.
Its two hard cases are a branch's first push (`before` is forty zeros: a valid
sha SHAPE naming no commit, while every commit on the branch is new) and a
force-push (`before` is well-formed and ORPHANED, so the range cannot resolve
and the scanner blamed `fetch-depth: 0`, a setting already correct). Both scan
from the root.

**Three locks, and the split is structural.** A single dev lock could not be
installed beside the runtime one: pip-audit pulls `requests`, which pulls
`charset-normalizer`, and resolving the two trees together landed 3.5.1 over the
3.4.9 `requirements.txt` pins. `parse_utils` then REFUSED to parse — correctly,
and loudly. Every document test went red in CI on a dependency of the AUDITOR.
Install ORDER would have hidden it; `requirements-test.txt` (pytest, coverage,
nothing shared with the runtime tree) makes it impossible, and a test pins that
no package is pinned at two versions across the pair a test job installs.

**The PII gate's digest exemption is by exact PATH**, deliberately, so the new
lock was covered by nothing and blocked its own commit with 101 matches that
were all sha256 digits. Every hashed lock is listed now, and a test pins the
SET — the documented way past that block is `--no-verify`, which is how a gate
teaches people to bypass the PII scan.

### What running on Windows actually found

Both are shipped-code defects, and neither is about ACL semantics.

**Every text read used the platform's encoding.** `open`, `read_text` and
`write_text` default to `locale.getencoding()` — UTF-8 on the hosts this was
developed on, cp1252 on Windows. `agents/lib/` reads `result.json`,
`answers.md`, `parse_ledger.json` and the tax-law cache that way, and this
repository's own templates carry em-dashes and section signs, so a Windows
user's return raised `UnicodeDecodeError: 'charmap' codec can't decode byte
0x8f` rather than parsing. The repository had paid for this family of bug once
already, in shell: the PII gate pins `LC_ALL=C` because one Latin-1 byte under a
UTF-8 locale hid every match on its line. Same mistake, other direction.

**`secure_fs` could not restrict anything.** It set the DACL with `Get-Acl` /
`Set-Acl`, and a stock `windows-latest` host cannot autoload
`Microsoft.PowerShell.Security` — `CouldNotAutoloadMatchingModule`. Every call
failed, so AGENTS.md §1's owner-only guarantee did not hold on the whole
platform: nothing this toolkit writes could be made owner-only there.
`-ExecutionPolicy Bypass` did NOT fix it — the problem is the DEPENDENCY, not
the permission — and the ACL now goes through `GetAccessControl()` /
`SetAccessControl()` on the `FileSystemInfo` the script already holds: System.IO
methods needing no module to be discovered, autoloaded or permitted. It failed
LOUDLY, raising rather than passing silently, so a real run would have stopped
rather than leaving a file exposed. The platform was simply unusable, and
nothing said so, because nothing had ever run there.

### Why the job is scoped as it is

Thirty tests carry an explicit `posix_only` skip — they read back a mode NTFS
does not have, create a symlink Windows needs a privilege for, or need a
case-sensitive path. That is NOT narrowing the job until it passes, and the
distinction is the whole point: every Windows assertion the module previously
held either stubbed `subprocess.run` — proving the PowerShell was ASSEMBLED,
never that it did anything — or read back a POSIX mode. A job that ran the
module and went green would have proved PowerShell was invoked and nothing else,
which is the "a check that always passes and one that never fires look identical
from outside" failure this repository already names.

`TestTheWindowsAclIsActuallyRestrictive` is what makes it worth having. It reads
the DACL back with `icacls` — a different tool from the one that wrote it — and
asserts no broad principal, no inherited rule, an inheritable directory ACE, and
a child created inside a secured directory covered too. Its bare-chmod case
compares the DACL before and after rather than looking for a broad principal to
disappear: a per-user temp directory on a runner grants none to begin with, so
the first spelling asserted a premise false on the very host it runs on.

### Not implemented, and why

W6's text asks for "the model-driven evals of V3 on their own schedule".
**Superseded by the owner's Q2 ruling**: those runs happen locally, on
credentials already on the operator's machine, so no API key becomes a
repository secret and a scored comparison of a tax return never lands in a
durable CI log. A test pins that no job reads a secret or dispatches the runner.

Coverage is a build ARTIFACT with no third-party upload, and no `fail_under`. It
measures the pytest process only, so `preflight.py` and every CLI arm read as
unexercised when they are among the most exercised code here — stated where the
number is configured, and pinned, because a reader who does not know that draws
the opposite conclusion from the lowest rows.

## W2 residue — the itemize-vs-standard election, and NY's unvalidated status

Q7 and Q9 of the backlog's Part 5b, ruled by the owner 2026-09-13 and shipped
together because both change an entry-point contract in `tax_computations/state/`.

**Q7 — one election, three contracts.** `compute_schedule_a` required a
`standard_deduction`; its two state siblings did not, and what each returned
without one was the defect:

- `compute_ca_itemized` set `use_itemized` to `None` and omitted
  `election_margin` — both of which a caller testing for truth reads as "do not
  itemize".
- `compute_ny_itemized` was worse. `deduction` was already holding the ITEMIZED
  total when the election was skipped, so there was no sentinel at all: the
  field a caller reads as elected held a number that was not.

Either way a taxpayer whose STANDARD deduction was the larger of the two got the
itemized figure — under-stating the deduction and over-stating the tax. Slice 3
had given both paths a loud banner, which is what the under-statement direction
is owed. The ruling went past that: a warning that an election MAY be wrong is
not a substitute for making it. Both parameters are required and keyword-only —
keyword-only rather than reordered, because these functions have positional
callers and moving a parameter into an occupied slot converts a would-be
`TypeError` into a silently wrong value. Both degrade arms are deleted with the
paths that needed them.

**Q9 — New York validated no filing status at all.** Six public functions took
one, four compared lowercase literals, and none called
`_shared._normalize_filing_status` — so the canonical `"MFJ"` that function
returns took the SINGLE branch rather than raising: the wrong-but-plausible
branch AGENTS.md §8 names as the reason value-validation exists. NY now routes
through `_ny_status`, which delegates to the shared validator and lower-cases,
because NY keys its own tables and every cache block it reads in lowercase.

The guard is DERIVED, not listed. `TestEveryStateEntryPointValidatesItsFilingStatus`
asks the tree which public functions in `state/` take a `filing_status` and
requires each to normalise it, so a seventh function added tomorrow cannot arrive
unvalidated — a hand-written roster of the six would have read exactly like a
file with nothing left to fix. Two companions keep the sweep honest: one asserts
it inspected both depth modules (a sweep that sees nothing is green), and one
proves `_ny_status` is not a hollow wrapper, since accepting a delegating name on
faith would have read green over precisely the defect it was written for.

One refusal got SHARPER rather than merely earlier: `"qualifying_widow"` used to
reach the §601 schedule map and be reported as a missing schedule, which reads as
"go fetch it" for a status that does not exist. It is refused by value now, and
the §601 message is left to a canonical status the cache genuinely lacks.

**`_ny_amount` is gone**, a third copy of `_shared._amount` across 51 call sites.
The only NY-specific thing about it was one sentence — that New York enters both
modification schedules as positive amounts, so a negative is a figure on the
wrong schedule. `_amount` takes that as `negative_hint` at the boundaries where
it is true, instead of a whole function carrying it everywhere it is not. The
first draft wired the hint into the IT-225 entry path alone and silently dropped
it from the named addition and subtraction lines, which are the same kind of
figure; a review reproduced that, and both are covered now.

### What the review of this change found

Four findings, all reproduced against the tree, and one of them is a tax defect
rather than a defect in the change:

**New York's MFS coupling bound in one direction only.** IT-196-I says "BOTH of
you must take the standard deduction unless BOTH of you elect to itemize", and
the code's own comment said so — while implementing only the half where the
spouse does NOT itemize. A filer whose spouse WAS itemizing still took the
standard deduction whenever it was the larger: the election the instruction
forbids, and it OVER-states the deduction. `compute_ca_itemized` has forced the
mirror-image federal §63(c)(6)(A) case since it was written, so this was New
York missing a rule its sibling already had.

> **Corrected 2026-09-14.** That last clause was false as a claim about the
> sibling. `compute_ca_itemized` had forced ONLY the half being looked at, and
> was missing the reverse — California's own "both or neither" — from #33
> (2026-07-13) until epic X's seventh round the day after this was written.
> Left in place because the reasoning above is the record of what was believed
> at the time, and because this is the sentence that made the sibling look
> swept. A sibling inspected closely enough to cite is not a sibling checked. Both directions are pinned now,
including the case where the spouses happen to agree and no coupling note is
owed.

**The status sweep admitted a false green twice**, and each was caught by
someone trying it rather than reading it. First it accepted a normalising call
on any value, so a module validating a SPOUSE's status would have read as
validating its own — the argument ties the call to the parameter now. Then it
accepted a call the function can skip: wrapping `state/ny.py`'s real
normalisation in `if False:` left the class green while `compute_ny_agi`
accepted `filing_status="invalid"` and returned normally. Only the function's
own top-level statements are read now, because validation that runs on some
paths is not validation. Both false greens are probes in the class, alongside
one asserting the shape both modules actually use still passes — a sweep nothing
can satisfy is the other way to be useless.

### Recorded, not fixed here — X8

Sweeping the siblings after the conversion found the same defect class outside
`state/`, and two instances are REPRODUCED: `ptet._salt_cap(2026, "Married
Filing Separately")` returns **$40,000** where `"MFS"` returns $20,000 — double
the SALT cap for an MFS filer, over-stating the PTET benefit it prices — and
`retirement_dashboard` reads the `single_covered` IRA phase-out window for that
same spelling where `"MFJ"` reads `mfj_covered`. Both are the wrong-but-plausible
branch. They are a separate contract change in three more modules, so they are
X8 in the backlog with the reproductions attached, not silent scope on an
engine-contract PR.

## W3 — The hook's largest decision moves to Python — PRs #163, #164

The backlog's framing: *"3,800 lines of bash tested by 5,200 lines of Python is
the wrong language boundary."* Its acceptance criterion is the opposite of a
rewrite — *"contracts first, extracted incrementally, exit codes and output
preserved"* — and two slices is what that bought.

**Slice 1 (#163) landed the port beside the bash it copied**, wired into
nothing, held to `select_modules_for` by a differential running both
implementations over every tracked path in the repository plus an adversarial
list. A port that changes behaviour on the day it lands cannot be told apart
from a port that changes behaviour by mistake; landing the CONTRACT first is
what separates them.

**Slice 2 (#164) spent that differential and deleted the `case`.** 218 lines of
arms, 678 lines from the function's declaration to its closing brace, replaced
by an EIGHTEEN-line shell function — eighty-one with its own reasoning — that pipes the scan to
`.claude/hooks/lib/hook_selection.py` and sets the flags it names. The reasoning
did NOT travel with the arms: the deleted function carried 456 comment lines
and `_ARMS` carries 19, which is the trade turning arms into data makes and is
recorded here rather than counted as a formatting change. The differential could not survive the flip and was never meant to:
with one implementation left there is no second answer to compare against, and
keeping it would have pinned the port to a shell revision that can never grow
the next arm. What replaced it runs the REAL HOOK and reads the pytest argv,
which covers the wiring the differential structurally could not see.

**Three things the flip had to get right, each of which fails silently:**

- **A pipe would have lost every flag.** `printf ... | select_modules_from`
  runs the function in a SUBSHELL, where the eight `RUN_` assignments die with
  it and the branch reads back the zeros it started with — a green hook over an
  edit that had just selected five modules. A process substitution does not.
- **One process, not one per path.** `case` was a shell function call costing
  nothing measurable; an interpreter start is not. The loop moved INSIDE the
  process, so a scan handing over a thousand changed paths pays that start
  once. `union_for` is where the accumulation the shell did in globals now
  lives, and a port returning the LAST path's answer would pass every per-path
  test while narrowing a whole scan to whatever `find` printed last.
- **Fail CLOSED, in all four directions.** A missing selector, a dead
  interpreter, a raise, or a module name the script does not declare all run
  EVERY module. A selector that cannot answer must never be able to answer
  "nothing". The undeclared-flag case is the one that needed saying out loud:
  `eval "RUN_$m=1"` on a name nothing declares would CREATE it, nothing
  downstream would read it, and the pass would go quiet over a real
  disagreement between the two files — so each name is checked against the
  script's own declarations rather than against a seventh copy of the roster.

**The port is located beside the SCRIPT, not by CWD**, which is the rest of the
file's convention. The port is part of the hook rather than part of the
repository it inspects, and the test harness drives the script against
synthetic workspaces that hold neither — by CWD every one of them would have
answered "cannot answer" for every path, and a hook that selects everything
satisfies nothing that file asserts.

**What the flip retired, beyond the bash.** One cry-wolf test went with the
text parser it was validating, and ONE of the two failure modes went with it: a
scan over `case` arms cannot tell an arm's prose from its code, and it counted
`# RUN_SECUREFS=1  (temporarily off)` as a selection. `_ARMS` is DATA — a tuple
of pattern tuples and frozensets — so prose cannot be read as code there.

The OTHER mode survived, and this paragraph claimed it had not. Reading a
multi-pattern arm is still a question that can be asked wrongly: matching the
arm's first pattern, or the whole tuple, reproduces the `a|b)` failure exactly,
over data instead of over text. Review caught it because the replacement test
asked about a path sitting in a ONE-pattern arm and so passed under both
mutations; it derives the multi-pattern arms from the table now.

**What it cost, stated rather than left to be found.** The per-pattern coverage
guard used to read its obligation out of the SHELL, so deleting an arm from the
port left that obligation standing and the test went red. There is no second
copy now, and an arm deleted from `_ARMS` takes its own obligation with it.
What still holds the arms to something outside themselves is
`test_the_watched_set_still_covers_what_the_modules_read`, which DERIVES what
each test module reads and requires an arm to select it — the assertion that
caught the real misses. This paragraph credited
`test_every_watched_source_path_reaches_its_module` instead, which iterates the
hand-written `WATCHED_SOURCE_PATHS` and derives nothing; naming the wrong one
would have left a reader trusting a transcription for the guarantee only the
derivation gives. The narrower guard keeps the job it can still do honestly: no
arm in the table is unreachable prose.

**The fast path spawns no interpreter at all**, which took a review round to
get right — and which is a defect this slice REPAIRED IN ITSELF rather than an
improvement it delivered. The first draft ran the port unconditionally,
including on the two hottest no-op paths: a Bash call where `find` printed
nothing, and a Write to a file outside the project. An empty list selects
nothing whoever is asked, so both say so directly now and the interpreter is
never started.

The row itself is UNCHANGED by this slice, and the branch claimed otherwise
until the rebase disproved it. Both readings behind that claim — 0.08-0.10s and
0.05-0.06s — are drafts of this slice. The tree without it settles the
question: the sitting taken for the environment-hardening change, with no port
on the fast path and the `case` still in place, reads that row at 0.05-0.06s
too. The fast path returns before any selection happens by either
implementation. Seconds are in MEASURED COSTS and nowhere else.

### The review round, because most of it landed on this commit's own prose

Four review angles plus a local adversarial pass. The port itself came back
clean — and it was re-derived AGAIN after the rebase, against the trunk's bash
rather than the one this branch was written against, with no mapping
differences. The corpus is 404 paths: every tracked file, every ancestor
directory of one, and the twenty-three adversarial cases slice 1 defined. This
paragraph said 532, a figure that does not come out of that corpus on any
revision of this branch — the repository has held 301 tracked files throughout
— so it is replaced by one a reader can re-derive. Almost every finding was in
the wiring or in a sentence about it.

**Two of the comments were false, and both flattered the change:**

- `SELECTOR="$(dirname "$0")/lib/..."` carried a comment saying the value was
  captured "before the `cd` moves away from the frame `$0` is relative to".
  Assigning a RELATIVE string ahead of a `cd` pins nothing — invoked by a
  relative path with `CLAUDE_PROJECT_DIR` elsewhere, it resolves against the
  project afterwards, to a different file or none. `pwd` is what pins it, and
  it does now. The same comment sold script-relative resolution as a design
  property when the honest reason is the test harness, which drives the real
  script against workspaces holding no `lib/`; in production the two
  resolutions coincide, and where they diverge script-relative is the WORSE
  answer. Recorded as a harness debt rather than dressed up.
- `union_for`'s test claimed to cover the subshell defect. It never invokes
  the hook. That defect is covered — rewriting either call site as a pipe
  turns thirty tests in this module red, measured — but not by that test, and
  a test claiming coverage it does not have is worse than one claiming none.

**And one paragraph got the residual backwards.** The per-pattern coverage
guard now reads its obligation out of `_ARMS`, so a deleted arm deletes its
own requirement. The first attempt at saying so claimed the affected arms
were "read by no module" and their loss "coverage rather than correctness" —
both wrong: `test_secure_fs.py` sweeps `agents/**/*.py` and
`test_portability.py` globs `docs/ops/*.md`, so those arms guard real reads.
The derived anchor misses them because it finds path LITERALS and these are
globs.

**`_ARM_SNAPSHOT` is the repair**, and it is what the flip actually cost.
Until now the arms existed twice and a differential required them to agree, so
changing one meant editing two files. The arms are pinned as data now — in
source order, since first match wins and two patterns appear in two arms
each — so a deletion or a narrowing still takes a deliberate second edit.
A second copy of the DATA, not a second implementation: it cannot catch a
wrong answer the table itself states, and it is not trying to.

**Three fail-closed holes, none of which the exit status could reach:**

- An EMPTY selector file is valid Python: it runs clean, prints nothing, exits
  0, and the shell read that as "no module can break this path". Reachable
  rather than exotic — the hook fires on every Write, the selector included.
  A `SELECTION1` protocol line makes "the port ran" a positive claim.
- `printf -v` was assigning a flag and its status was ignored. An exported
  shell function named `printf` forwarding to `/usr/bin/printf` shadows the
  builtin and rejects `-v`: reproduced on bash 3.2, assignment refused, flag
  left at zero, the function returning success over it. `builtin printf`, and
  the flag read back afterwards rather than the status trusted.
- The loop was `for module in $out`, which does pathname expansion as well as
  word splitting — with the CWD inside a directory that can hold `documents/`
  and `summaries/`. It failed closed on the charset check, but that is the
  validator carrying a hazard the loop should not create. `while IFS= read`
  does neither.

**Paths go in on STDIN, and that is a privacy property with a test now.** argv
is world-readable through `ps`, and the path being classified can name a
taxpayer's document — the same exposure the hook's generic message label
exists to prevent. Nothing in the code said so, so the obvious simplification
(pass it as an argument) would have been made without objection.
