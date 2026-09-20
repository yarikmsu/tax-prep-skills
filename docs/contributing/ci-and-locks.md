# CI, the three locks, and what running on Windows found

`.github/workflows/ci.yml` is the file; this is the reasoning, kept out of
AGENTS.md because that file is at a size cap Codex silently truncates past.

## Four triggers, each running what means something for it

- **`pull_request`** — the gate.
- **`push` to main** — a squash merge produces a commit no pull request ever
  tested. The tree is the branch's; the COMMIT is new, and the PII gate scans
  commits.
- **`schedule`** (weekly) — catches what changes UNDER the repository rather
  than in it: a new advisory against a pinned dependency, a runner image, an
  interpreter. It is the only trigger that can go red while the code stands
  still. It skips the PII job, which would otherwise fetch the whole history to
  print "nothing to scan".
- **`workflow_dispatch`** — asking for any of that on purpose.

GitHub disables a scheduled workflow after 60 days with no repository activity,
and re-enabling it is a button nobody is prompted to press. A season in which
nothing is committed is exactly when a stale advisory matters most, so treat a
silent schedule as a finding rather than as quiet.

A concurrency group cancels a superseded PR run and NEVER one on main: the
push-run is the only thing that scans the commit that actually landed, and a
second merge arriving while it runs would cancel it, leaving that commit
unscanned under a green tick from the run that replaced it.

## The scan range is a script, not a workflow expression

`github.event.pull_request.base.sha` is EMPTY on every event except
`pull_request`. Inlined, the moment CI gained a push or schedule trigger the
scanner would have been handed `...HEAD`. It fails closed — but "the gate blocks
every push" and "the gate scans the right range" are different outcomes, and
only the second is a working CI.

`scripts/ci-pii-range.sh` decides instead, on the gates' usual contract: **0**
with a range on stdout, **3** for "this event introduces no commits", **1** for
"the range cannot be determined". Never a fallback range — a wrong one scans
commits nobody wrote, or none at all, and both report a clean pass.

Two cases are easy to get wrong:

- **A branch's first push** has `before` = forty zeros. That is a valid sha
  SHAPE naming no commit, so a range built from it resolves to nothing while
  every commit on the branch is new. Scan the whole history.
- **A force-push** leaves `before` well-formed and ORPHANED. `actions/checkout`
  fetches refs, not unreachable objects, so the range cannot resolve and the
  scanner blames `fetch-depth: 0` — a setting that is already correct. History
  was rewritten, so what is new is not knowable from `before`: scan from the
  root.

The shallow-checkout guard asks git (`rev-parse --is-shallow-repository`) rather
than whether the root LOOKS like a sha, because a grafted boundary looks exactly
like one and the check could never have fired.

## The floor is 3.13, and it is the same number everywhere

Relocated out of AGENTS.md §7 by Y5 (PR #113), which keeps the rule — a green
local suite is not a green CI, so reinstall from the lock rather than letting the
venv drift off it — and points here for the evidence.

**There used to be two numbers.** `MIN_PYTHON` in `preflight.py` said 3.10 and
refused anything older; `.python-version`, the three lock headers and CI's lower
matrix leg said 3.12. The gap between them — 3.10 and 3.11 — was a support claim
in README, QUICKSTART and AGENTS.md §7 that no job and no machine in this project
had ever run. Nothing was wrong with the code; the claim was simply untested, and
a toolkit that computes real returns should not carry one.

So the floor is now ONE number, 3.13, and it is the oldest interpreter CI runs.
Every file that states it is held as a set by
`tests/test_ci_workflow.py::TestTheInterpreterFloorIsOneNumber` — `.python-version`,
both `pyproject.toml` tool targets, the three `.in` recipes, the three generated
lock headers, `MIN_PYTHON`, the matrix's lower leg, the three single-version job
pins, the step conditions that select the coverage leg, and the requirement
sentence in AGENTS.md, README, CONTRIBUTING and QUICKSTART. A prose pin drifts;
this one goes red.

**3.12 WOULD ALSO HAVE CLOSED THE GAP, and cheaper.** It was a CI leg and the
locks' target, so it was never the untested part — only 3.10 and 3.11 were. The
owner chose 3.13 anyway, and the cost of that extra step is worth naming rather
than leaving a later reader to rediscover: 3.12 is the default `python3` on
Ubuntu 24.04 LTS, which the engine now refuses. The current LTS, 26.04, ships
3.14 and clears the floor; 24.04 and Debian 12 package no 3.13 at all, which is
why `preflight.py`'s remedy leads with `uv python install` rather than a distro
package.

What this does NOT do is newly bless 3.14. It was already a matrix leg and
already inside "Python 3.10+", so a local run was always on a claimed version.
What changed is narrower, and worth stating exactly rather than generously: the
pin no longer names a version nothing here runs. The interpreter a contributor
gets from `python3 -m venv` is still ABOVE it — on a current macOS that is 3.14,
a release up — which is exactly why a green local suite is not a green CI and
why the matrix keeps a second leg.
The untested range is gone from the BOTTOM of the claim. The top of it — every
release after 3.14 — is open by construction and always will be; QUICKSTART is
the only one of the four documents that says so.

### Why two legs, then, and not one

Because the interpreter changes ANSWERS, not just availability, and 3.13 and 3.14
differ in ways this suite can already see:

- **t-strings.** 3.14 gave them their own token TYPES —
  `TSTRING_START`/`MIDDLE`/`END` — where 3.13 lexes `t"..."` as a NAME plus a
  STRING. `test_preflight.py::test_the_body_mask_covers_a_literal_on_every_interpreter`
  exists to make the verdict AGREE across that split, so the 3.14 leg is the
  only thing in this repository that exercises the `TSTRING_*` path at all.
- **`Path.is_symlink()`.** 3.14 converts every `OSError` to `False`, which would
  turn access-denied into a false safety certificate. The repository fails closed
  around it rather than inheriting the newer behaviour — by NOT calling it, so
  unlike the split above no test here produces a different outcome per leg.

Which is the honest general form: **each leg is the only place its own
interpreter runs.** The suite is written so the two agree, and that is the point
— a verdict that differed per leg would be the defect, not the justification.

The difference that first made the point is STILL LIVE, and this paragraph said
it was history until the boundary was checked. `Popen.communicate()` re-flushes
a stdin the caller already closed, which raises `ValueError` up to and including
**3.13** and does not on 3.14: `except ValueError` sits beside the
`BrokenPipeError` arm in 3.14's `_communicate` and not in v3.13.9's. A test
passed locally and failed in CI on the interpreter alone.

Raising the floor to 3.13 did NOT retire that split, which is what the earlier
wording assumed when it put the boundary at 3.12/3.13 — it moved the raising
half from a leg being dropped onto the leg that is now the floor. Three
differences separate the legs, not two.

## Three locks, and why the split is structural

| lock | holds | installed by |
|---|---|---|
| `requirements.txt` | the four runtime dependencies | every job that runs the engine |
| `requirements-test.txt` | pytest, coverage | the test jobs |
| `requirements-dev.txt` | ruff, mypy, pip-audit | lint and audit |

The test lock exists because a single dev lock could not be installed beside the
runtime one. pip-audit pulls `requests`, which pulls `charset-normalizer`, and
resolving the two trees together landed 3.5.1 over the 3.4.9 `requirements.txt`
pins. `parse_utils` then REFUSED to parse — correctly, and loudly: a parser
version the lock does not name is one the suite never ran against (AGENTS.md
§7). Every test that touches a document went red on a dependency of the AUDITOR.

Install ORDER would have hidden it. Two locks make it impossible instead, and a
test pins that no package is pinned at two versions across the pair a test job
installs.

ruff and mypy are version-pinned in `requirements-dev.in` as well as in the
lock: a linter is the one dependency whose new release adds FINDINGS, so an
unpinned one turns an unrelated pull request red on a rule nobody here has ruled
on. pip-audit is deliberately not pinned that way — its new findings are real
advisories, and going red on one is the job.

## Regenerating a lock

**`requirements.txt` is GENERATED — edit `requirements.in`.** The `.in` file
holds the four direct dependencies; `requirements.txt` is the compiled lock,
pinning the whole transitive tree with `--hash=sha256`. Pinning only direct
dependencies is not pinning: pdfplumber 0.11.10 shipped no pdfplumber code at
all, only a `pdfminer.six` bump — and `pdfminer.six` is the engine document
parsing actually runs on. Hashes make pip refuse an artifact that does not
match, so a version republished with different contents fails the install
instead of silently parsing a return. After editing the `.in`, regenerate with
the SAME flags and commit both files:

```bash
uv pip compile requirements.in --generate-hashes --universal \
    --python-version 3.13 -o requirements.txt
```

`--python-version 3.13` is the FLOOR (above) and `--universal` keeps the
resolution valid on macOS and on the Windows job; changing either silently
changes what the lock covers. A Dependabot PR bumps the lock — mirror the
version into the `.in` in the same PR, or the next regeneration reverts it.

**Changing a compile FLAG is not a dependency bump, and uv already keeps it
from becoming one** — provided `-o` names the lock that already exists. uv
preserves the pins it finds in the output file and re-resolves only what it must,
so moving `--python-version` produces the flag's effect and nothing else. Use
`--upgrade` when a refresh is what you actually want; the two are separate
changes and belong in separate commits.

Do not reach for a hand-built constraints file to force this. The obvious recipe
— grep the `name==version` lines out of the lock and pass them back with `-c` —
drops the environment MARKERS, and a `--universal` lock legitimately pins one
name twice under opposite markers. Those return as two unconditional constraints
that cannot both hold.

Raising the floor from 3.12 to 3.13 moved no pinned version at all: all three
committed locks are byte-identical to a plain in-place regeneration at 3.13,
apart from the `-o` path uv echoes into the header. The flag's entire effect was
one annotation — `typing-extensions` is no longer reachable through
`cyclonedx-python-lib`, whose requirement on it is conditional on
`python_version < "3.13"` — and the package is still pinned, via mypy.

## Coverage: read the caveat before the number

The report is a build ARTIFACT, not an upload to a coverage service. This
repository is private and its subject is real returns; a third-party upload is a
new destination for repository content AND a new credential in repository
secrets, and AGENTS.md §1 does not spend either on a convenience.

It measures the pytest process ONLY. Much of this suite drives the engine in a
SUBPROCESS — every `run.sh` invocation, every `-m agents.lib.X` CLI drive, the
whole of `test_preflight` — so `preflight.py`, every `_main` and every CLI arm
read as unexercised when they are among the most exercised code in the tree. A
reader who does not know that draws exactly the wrong conclusion from the lowest
rows. There is no `fail_under`: a threshold picked today becomes the target.

## What running on Windows found

AGENTS.md claims Windows support in 27 places. Nothing had ever run there. The
first job produced two shipped-code defects, neither of them about ACL
semantics:

**1. Every text read used the platform's encoding.** `open`, `read_text` and
`write_text` default to `locale.getencoding()` — UTF-8 on the hosts this was
developed on, **cp1252 on Windows**. `agents/lib/` reads `result.json`,
`answers.md`, `parse_ledger.json` and the tax-law cache that way, and this
repository's own templates carry em-dashes and section signs, so a Windows
user's return raised `UnicodeDecodeError: 'charmap' codec can't decode byte
0x8f` rather than parsing. Everything in `agents/` and `evals/` names
`encoding="utf-8"` now, and a test sweeps for the next one.

The repository had paid for this family of bug once already, in shell: the PII
gate pins `LC_ALL=C` because one Latin-1 byte under a UTF-8 locale hid every
match on its line. Same mistake, other direction.

**2. `secure_fs` could not restrict anything.** It set the DACL with the
`Get-Acl`/`Set-Acl` cmdlets, and a stock `windows-latest` host cannot autoload
`Microsoft.PowerShell.Security` — `CouldNotAutoloadMatchingModule`. Every call
failed, so §1's owner-only guarantee did not hold on the whole platform. It
failed LOUDLY (an `OSError`, not a silent pass), so a real run would have
stopped rather than leaving a file exposed — but the platform was unusable and
nothing said so.

Adding `-ExecutionPolicy Bypass` did NOT fix it; the problem is the DEPENDENCY,
not the permission. The ACL is read and written through `GetAccessControl()` /
`SetAccessControl()` on the `FileSystemInfo` the script already holds: System.IO
methods, present in the .NET Framework Windows PowerShell runs on, needing no
module to be discovered, autoloaded or permitted. The bypass stays, because a
restrictive policy is the *other* thing that can stop the script and its absence
is invisible from POSIX.

## Why the Windows job is scoped the way it is

It runs `test_secure_fs.py` and `test_host_guard.py`. Thirty tests in the first
carry an explicit `posix_only` skip: they read back a `0o600`/`0o700` NTFS does
not have, create a symlink Windows needs a privilege for, or need a
case-sensitive path.

That is not narrowing the job until it passes, and the distinction matters.
Every Windows assertion that module previously held either stubbed
`subprocess.run` — proving the PowerShell was ASSEMBLED, never that it did
anything — or read back a POSIX mode. A job that ran the module and went green
would have proved PowerShell was invoked and nothing else.

`TestTheWindowsAclIsActuallyRestrictive` is what makes the job worth having. It
reads the DACL back with `icacls`, a different tool from the one that wrote it,
and asserts what §1 actually claims: no broad principal, no inherited rule, an
inheritable directory ACE, and a file created inside a secured directory covered
too. `_secure_path_windows` verifies its own work as well, and that is worth
having — but a writer checking its own write with the same API cannot see an API
that lies.
