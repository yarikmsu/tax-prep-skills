# Epic Y — Distribution and operations

Backlog: `docs/roadmap/2026-09-05-staff-agent-review.md` (adopted 2026-09-05).
Step 9 of that file's sequencing: takeable at any point after step 5.

## Y5 — The instruction files carry the alarms, not the answers — PR #113

**Defect:** AGENTS.md and CLAUDE.md were 39 KB loaded into every session before any
skill, and the only control on that was a byte cap sitting TEN BYTES above the file.
That is not a budget, it is a cliff. Y4 needed five lines in AGENTS.md §6 and had to
evict three unrelated passages to fit them — and which safeguard died was decided by
whoever was editing, under time pressure, at the end of a task about something else.
That is a terrible way to choose which rule survives, and it had already happened
three times.

**A cap can only force a choice. What makes the choice safe is everything else in
this change.**

**The criterion, written into §9 so the next editor inherits it rather than
re-deriving it:** a line earns its place in an instruction file if an agent who never
looks it up would otherwise do the wrong thing. Those are the ALARMS. Everything else
is an ANSWER, and an answer belongs behind a pointer — because you cannot raise an
alarm you never received, while an answer you do not have, you look up.

Applied, that turns out to cut cleanly:

- **Keep** that the PII gate is single and that rules go in that script alone; that
  `.pii-allowlist` must be STAGED with the synthetic value, allowlisting afterwards
  clearing nothing; that `tests/fixtures/` is exempt from the filename rules ONLY.
- **Move** what the scan matches, how it ranges over commits, and the two environment
  defects it pins — `LC_ALL=C` and the `cd` to the root. Nobody needs those until
  they are editing the scan, at which point they are reading the scan.

`AGENTS.md` 28,662 → 25,897 bytes; `CLAUDE.md` 10,437 → 7,079; the chain a Claude
session loads, 39,099 → 32,976. Four relocations: the hook and subagent internals to
the new `docs/contributing/claude-automation.md`, the PII scan's mechanism to the new
`pii-gate.md`, the tax-law cache's key paths to the new `tax-law-cache.md`, and the
capability gate's manifest to `skill-catalog.md`, which already held the scope limits.

**Three passages were deleted rather than relocated, because they were already
duplicated or already wrong.** The pdfplumber 0.11.10 story in §7 is verbatim in
`ci-and-locks.md`, under a §9 heading promising nothing below it is duplicated above.
The `agents/lib/` module roster in §5 had gone stale the moment Y4 added
`workspace.py` — it is now a description of what the directory holds rather than a
list, and the only names left are the four the self-test claim PINS. §5's
`docs/contributing/` line named five of what are now ten files, and points at the §9
index instead.

**The one list that stayed is the one a test pins.** Removing the four self-testing
module names turned `TestSelfTestClaim` red within seconds, and that is the whole
distinction: a list under test does not drift, so it is not an "answer" in the sense
this rule cares about. Only unpinned lists rot.

**The cap is now a BUDGET, and the test says why.** `AGENTS_MAX_BYTES` is 26 KiB
against Codex's 32 KiB chain limit, and a test pins that the cap leaves at least an
eighth of that limit unspent — because the CHAIN also carries a nested AGENTS.md and
the user's own global instructions, which nobody in this repository writes and which a
cap set AT the limit would let spend the whole budget silently. CLAUDE.md gained a
byte cap beside its line cap: every line there is wrapped prose, so re-flowing a
section onto fewer, longer lines sheds no weight at all while the line count drops.

**The half of a relocation that is easy to lose is REACHABILITY.** Moving text out is
one edit; linking it is another, and only the first is visible in the diff you were
reading. So §9 now indexes `docs/contributing/` in FULL, and
`test_the_contributing_index_is_complete` iterates the DIRECTORY rather than a
hand-kept list. `test_every_relocation_pointer_resolves` derives its roster by regex
over both instruction files, with a FLOOR under it, because a derived roster fails by
shrinking invisibly. And an agent editing a hook reads the Hooks section, not the
documentation index, so the section a file LEFT must point at where it went.

**A mutation probe came back VACUOUS and named the defect in my own test.** Deleting
the §9 index row for `pii-gate.md` left the suite green: the assertion searched the
whole of AGENTS.md, and §1's own bullet links that file too. A test called
"the index is complete" was checking "the file is mentioned somewhere". It is scoped
to §9's table ROWS now, and the probe is red.

**Review found the same shape five more times, and one of them was the whole change
pointed at itself.**

- **§9 asserted that nothing below it is duplicated above, and the first version broke
  that in nine places** — 18% of CLAUDE.md was verbatim inside the file it had just
  relocated into, the reviewer-grant sentence included. Every pair was free to
  disagree, in a change whose entire argument is that a second copy is how
  disagreement starts. The claim was PROSE, so nothing caught it. The docs now say why
  a rule is what it is rather than saying the rule again, and
  `test_a_relocation_target_does_not_restate_what_the_file_kept` holds every
  relocation target to it: a shared run of twelve consecutive words is the threshold,
  which leaves identifiers and short technical phrases legal and a restated rule not.
  The measured overlap went from sixty-eight shared runs to none.
- **The statutory dollar figures ended up in two files.** The safe-harbor $150k/$75k,
  the ACTC $2,500 floor and 15% rate, the $500 ODC — restated in `tax-law-cache.md`
  while AGENTS.md §8 still carried them. A FIGURE in two files is two files to change
  when it moves, which is §2's failure with no downstream check. §8 keeps them.
- **The acceptance claim was duplicated, and it is a live trust signal.**
  `docs/ops/review-subagent-acceptance.md` repairs a FAILED replay by replacing
  CLAUDE.md's `**Both PASSED <date>**`, and a test guards that file for holding
  exactly one such claim. A second copy in `claude-automation.md` would have gone on
  advertising a pass the repair had just withdrawn — the exact stale signal the guard
  exists to prevent, arriving through the documentation meant to reduce drift.
- **The cap test licensed a return to the cap it was written against.** "Reserve an
  eighth of Codex's 32 KiB" reads like a principle and permits 28 KiB, which is the
  pre-Y5 cap. An inequality that sanctions the status quo ante is worse than no test.
  The number is pinned outright now; the reserve is recorded as what CHOSE it.
- **The load-bearing rules had nothing holding them in.** Every other test in the
  class rewards a SMALLER file, so the cheapest way to go green was to delete a rule —
  and `test_security_rules_are_front_loaded` did not cover it, asking only for the
  words "ssn", "chmod 600" and "never" somewhere in the first hundred lines, which
  survives deleting the full-SSN prohibition outright. `test_the_cap_cannot_be_met_by_deleting_a_rule`
  is the counterweight, and without it this change made the instruction files LESS
  safe than it found them.

**Four more, each a pointer that resolved to the wrong place.** `pii-gate.md` derived
the gate's directory coverage from §6's runtime FILES rather than §1's longer
directory list, dropping `projections/`, `withholding/`, `.planning/` and `archive/`.
`docs/ops/` was indexed nowhere, leaving the season-open runbook unreachable from
either instruction file. AGENTS.md §8 still said pull requests are auto-reviewed by a
bot, which CLAUDE.md has recorded as OFF since 2026-09-07 — a disagreement the two
files are not allowed to have. And `CONTRIBUTING.md` explained that
`validation_receipt.json` is absent from §6's list "only because that file is at the
byte cap", which is precedent for exactly the trade Y5 exists to end; the receipt is
in §6 now.

**Three compressions changed meaning, and one changed it in the dangerous direction.**
"pytest 2-5, or a chmod that FAILED, holds the stamp" had become "a hook that could not
RUN holds it" — but a chmod that failed DID run, and produced something that reads like
a verdict, so an editor would advance the boundary on it. The other two: `tax-setup`'s
obligation to fetch `irmaa.tiers` had been demoted from an imperative to a description,
and "docstring" had dropped out of the list of places a MEASURED COSTS absolute may not
be restated — which is precisely where this repository restates them.

## Y4 — A workspace root outside the checkout — PR #112

**Defect:** every CLI in `agents/lib/` took a `--workspace`, every skill passed
`.`, and `sh agents/lib/run.sh` resolves from the repository — so the workspace
WAS the checkout. A development clone and a live return shared one directory,
and everything that kept them apart was policing: the `.gitignore` list,
`install.sh`'s `IGNORE_PATTERNS`, and the pre-commit PII scan. Three mechanisms
backstopping a single decision nobody had made deliberately. The 08-12 review
accepted the risk as permanent (R2's standing controls); this is the one
variable that retires it instead.

**`TAX_WORKSPACE` names the directory holding the return.** The precedence is
explicit `--workspace`, then the variable, then `.` — and that last step is the
whole of the backwards compatibility: unset, every command means what it meant
before, because a toolkit that refuses to run until an environment variable is
set is a migration nobody asked for.

**What does NOT move, and getting it backwards breaks every command:** the
working directory stays the CHECKOUT. `run.sh` is invoked by a relative path
from the skills and anchors the venv to its own location, and
`agents/lib/__init__.py` refuses to import outside that venv. The agent runs
FROM the clone and points AT the workspace. The variable moves the data, never
the code.

**Two sweeps, and the second is the one that would have made the first a lie.**

- Twenty CLIs declare `--workspace` through `workspace.add_workspace_argument`
  and resolve it through `workspace.resolve_workspace`, so the variable is
  honoured, the flag's help says the same thing everywhere, and a directory
  that is not there is refused with one message that names both the path and
  where the path came from. Two modules had hand-rolled that refusal with a
  message that could say neither.
- The PATH ARGUMENTS beside it — `--result result.json`, `--profile
  taxpayer_profile.json` — resolved against the process CWD. That was invisible
  while the CWD and the workspace were the same directory and is fatal the
  moment they are not: `_validate_subpath` would refuse a file in the clone for
  being outside the workspace, which is a confusing way to say "you are in the
  wrong directory". The rule already existed as `_resolve_under` in
  `scenario.py`, imported by two planning modules and unknown to the six others
  that needed it. It moved to `workspace.resolve_under_workspace` and they all
  use it.

**Four CLIs spell the workspace differently and were nearly missed** — every
one found by review, not by the sweep, because the sweep greps for
`--workspace`. `yoy_diff --current .`, `rollover --to .`, `archive`, which took
no flag at all and used `Path.cwd()`, and `crypto_importer`, which takes a bare
positional path. The archive one is the worst of them: year-end cutover MOVES
`documents/` and `reviews/` under `archive/{year}/`, so it would have archived
whatever directory the command ran from. The two flags are kept as ALIASES of
`--workspace` rather than renamed — a documented command that starts erroring is
worse than a second spelling — and both land on the same dest so the code has
one name for it.

`crypto_importer` gets the RELATIVE half only: a bare `documents/coinbase.csv`
now means the workspace, and an absolute path outside it is still accepted,
because that has always been the contract there and narrowing it is V6's
question about arbitrary file reads rather than this one about which directory a
relative path means. Changing both at once would have hidden the second behind
the first.

**The structural sweeps cannot see any of those four**, and that limit is worth
stating rather than papering over: they grep for the flag NAME. What does cover
the shape is a second sweep for `Path.cwd()`, plus naming the positional pair in
the module that has them.

**The one flag that is NOT this**, written down in three places because it reads
identical: `preflight --workspace` names the CODE root it resolves `.venv`
against. Pointed at `$TAX_WORKSPACE` it would look for the engine's venv inside
the taxpayer's directory, find none, and certify a run against an interpreter
the engine is not installed in — the exact defect the host guard exists to
catch, arriving through the gate that is supposed to catch it. A test pins the
distinction from both sides.

**Two refusals the resolver now owns**, both lifted from CLIs that had them and
given to the ones that did not: an EMPTY `--workspace` is an error rather than
a silent fallback to the current directory (`--workspace ""` is a caller whose
own lookup returned nothing), and an empty `$TAX_WORKSPACE` reads as unset for
the same reason — `Path("")` is `.`, which is the one outcome this exists to
prevent.

**`WorkspaceError` is its own class, not `FileNotFoundError`.** The builtin was
the first attempt and looked free: it is an `OSError`, and half the CLIs here
already catch one, so half the exception tuples needed no edit. That is exactly
what is wrong with it — the other half do not, and widening them would have made
every unrelated IO fault in those commands exit 1 with a one-line message
instead of the traceback that says where it happened.

**Verification.** 20 new tests, including two structural sweeps that fail if a
CLI goes back to `Path(args.workspace)` or declares the flag by hand — the
failure is otherwise invisible, because the flag still works and only the
variable is lost. Mutation probes: a CLI reverted to the raw path, the variable
ignored in the resolver, and the flag redeclared locally, each red then restored.

**The local `codex exec` round found four more, and three of them were the same
shape as the ones review had already found: a path resolved against the process
directory, somewhere the sweep could not see.**

- **`secure_fs`'s own CLI.** Every skill spells its targets as bare names —
  `secure_fs taxpayer_profile.json` — and roughly forty of those strings are
  pinned by test. Left against the CWD, the documented call would have chmod'ed
  a stale copy in the checkout while the real profile kept whatever mode it was
  created with: the one file AGENTS.md §1 is most explicit about, and the fix is
  one line because every skill already spells it relatively.
- **`/tax-setup`'s own `mkdir`.** The shell loop created `documents/` and
  `summaries/` beside the source while `secure_fs` (fixed above) secured the
  ones in the workspace — the two disagreeing about which directory they meant,
  which is worse than either being wrong alone. `workspace.py --path` prints the
  directory and nothing else for exactly this caller, and the loop is rooted on
  it.
- **`parse_utils`' positional path.** Resolving the workspace and leaving the
  path alone was worse than doing neither: the containment check then refused
  the checkout's own `documents/` for being outside the workspace, so
  `scan documents/` failed rather than scanning the taxpayer's.
- **Four planning CLIs caught `WorkspaceError` around the wrong block.** The
  resolver runs in the first `try`; the handlers had landed on the second, so a
  `$TAX_WORKSPACE` pointing nowhere came back as a traceback.

**And one that only exists because a module can have two identities.**
`resolve_under_workspace` raises through `rollover._validate_subpath`, and
`rollover.py` run as a SCRIPT is `__main__` — so the `RolloverError` its own CLI
catches is a different class object from the `agents.lib.rollover.RolloverError`
raised inside the helper. A containment refusal came back as a traceback instead
of the line it used to print. The helper re-raises as `WorkspaceError` now:
every caller already handles that, and none of them can be sure which identity
of `RolloverError` it is holding.
