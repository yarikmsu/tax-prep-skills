# Epic Y — Distribution and operations

Backlog: `docs/roadmap/2026-09-05-staff-agent-review.md` (adopted 2026-09-05).
Step 9 of that file's sequencing: takeable at any point after step 5.

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
