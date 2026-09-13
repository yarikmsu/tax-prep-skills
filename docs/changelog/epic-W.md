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
York missing a rule its sibling already had. Both directions are pinned now,
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
