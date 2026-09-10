# The PII gate — `scripts/pre-commit-pii-scan.sh`

Relocated out of `AGENTS.md` §1 by Y5 (PR #113), which keeps the rules an agent breaks
without looking anything up and points here for the rest. §1 also gives the one rule
about changing this gate, and the history behind it; what follows is what the gate
actually does.

It runs at three moments, all of them the same script: as the pre-commit hook over the
index, as the pre-push hook over the commits a push would publish, and in CI over a
per-trigger range (`scripts/ci-pii-range.sh`). Git hooks are the host-side backstop and
work the same on every agent: they fire on a plain `git commit` from anything that can
commit at all. Both hooks are installed by `bash agents/install.sh`.

**The installed hook is a COPY, and editing the source does not move it.** The body lives
in a heredoc in `agents/install.sh`; `.git/hooks/pre-push` is what actually runs, and it
changes only when the installer is re-run (`bash agents/install.sh --hooks-only`). The
tests never see this, because each one installs into a throwaway clone — so the gap is
between the source and the clone you are pushing FROM, and it was found the ordinary way:
by editing a block message and then reading the installed hook and finding the old one.
Re-run the installer after touching either hook, the same way a skill needs re-installing
after its references are split.

The pre-push hook replaced a blanket refusal (Y6), and the reason is one layer up from
what it scans. That refusal made `--no-verify` the working push form, and `--no-verify`
is one flag covering both hooks: a workflow that types it every time has already learned
to type it at the moment the content scan is the only thing between a full SSN and a
commit. A gate that must be bypassed to do the ordinary thing is training.

**What the trade is, stated rather than denied.** The refusal made a human look before
anything left the machine; the hook decides automatically for the classes the scanner
recognises, and content it does not recognise still goes. That residual is the one this
repository already names elsewhere: a taxpayer's NAME in a section header has neither an
SSN shape nor a credential shape. So is a COMMIT MESSAGE, and an annotated TAG message —
the script reads changed files and pathnames, never the message, at every one of its
three call sites. The banner says what it inspected for that reason; nothing above it
should be read as wider.

What the hook hands the script is a RANGE, so the per-commit reasoning below applies — a
value added and later removed is still in the history being published. Recovering that
range is where the work is, and every defect found in it had the same shape: the hook
believed something it had not established.

**The bound comes from the destination, asked.** Remote-tracking refs were it, and they
are a local CACHE of what some remote held at the last fetch — a different question from
what THIS push's destination has. `remote.<name>.pushurl`, an `insteadOf` rewrite, a
remote that rewrote or deleted history since the fetch, and a push by URL with no
tracking refs at all each make the cache say "already there" about a commit the
destination has never seen, and the ref was then skipped entirely. `git ls-remote` against
the hook's SECOND argument — where git is actually pushing — settles it, and the shas it
advertises that this clone cannot traverse are dropped, which can only widen the scan. It
is asked lazily, so a push whose refs all carry a base costs no round trip.

**The oldest outgoing commit is found by TOPOLOGY.** `git rev-list` orders by committer
date, and a parent is not guaranteed to follow its children: taking the last line picked
a DESCENDANT, whose parent — itself outgoing — became the base and was excluded. Under
`--topo-order --reverse` the first line is a genuine ancestor. Then the range is CHECKED
against the enumerated set rather than reasoned about, because a base is a single commit
and an outgoing set need not be a single chain. That self-check is belt to the ordering's
braces, and it earns its place: with the ordering deliberately broken it is what refuses
the push.

Five cases sit at the edges of that range:

- **A ref DELETION** publishes nothing, and git marks it with an all-zero LOCAL sha.
  Scanning it would refuse the ordinary post-merge cleanup, which is how `--no-verify`
  returns through a different door. The destination guard runs FIRST regardless: deleting
  a branch is still a write to it, and ordering the skip above the guard once made
  `git push origin --delete main` the one form of writing to `main` that was allowed.
- **A ROOT commit** as the oldest outgoing member has no parent to be the base, and the
  script handles that: given the root itself it re-includes it and diffs against the
  empty tree. The hook adds it back when counting, since the range covers one more commit
  than `base..head` names.
- **A SHALLOW boundary** looks identical to that root from `git rev-list`, and is not.
  The script's root check reads the commit OBJECT, finds the parent this clone never
  fetched, and does NOT re-include it — so the same base silently drops the oldest
  commit of the push. The hook reads the object too, which is why it reads one at all,
  and refuses instead.
- **NOTHING OUTGOING** — a tag pointing at an already-pushed commit, or a force REWIND to
  an ancestor. It says so. A hook that prints nothing cannot be told from one that did
  not run, and treating a rewind as an unperformed scan blocked a correct push.
- **A TARGET THAT IS NOT A COMMIT.** `git rev-list <blob>` succeeds with EMPTY output, so
  a tag on a blob or a tree read as "publishes no commit" and went out carrying its whole
  content, unscanned.

**Exit 0 from the script is not evidence that it ran.** It is read from the WORKING TREE
at push time, so what executes is whatever is on disk — a half-finished local edit, or a
rewrite the push itself would publish. Replacing it with `exit 0` published a full SSN
with no output at all. The hook requires the affirmative coverage line instead, matched
WHOLE and with its commit count read as a NUMBER: a substring test accepted a stub whose
entire output was "No PII scan clean result is available". A count is a verification; a
substring is a coincidence waiting to happen.

The count is compared with `-ge`, not for equality, and that is not laziness. The script
derives its own from `merge-base(base, head)` and the hook's comes from `base..head`;
those agree whenever the base is an ancestor, which is every ordinary push, and a force
push onto a history with more than one merge base makes the script enumerate MORE.
Demanding equality would refuse a correct push, and the property actually wanted is that
the scan covered at least what was enumerated. The same verification covers `--names`,
which has its own way of succeeding without working: given nothing on stdin it reports
zero names checked and exits 0.

**A message before the name check must not print the name.** Everything after it may, the
name having passed these patterns by then — but the refusal for a name check that
reported nothing cannot, because the name is the candidate value and writing it into
scrollback is the disclosure the check exists to prevent. That is the
detected-but-not-redacted shape this gate has already paid for once, and the first version
of that message had it.

Two smaller ones, both about what a subprocess inherits. The scanner is run with its
stdin CLOSED, because it would otherwise receive the remaining ref lines — anything that
consumed them made the hook's next `read` see EOF, and every ref after the first went out
with neither a scan nor the destination guard. And `GIT_NO_REPLACE_OBJECTS=1` is exported
for the whole hook, because a `git replace` mapping rewrites what every command here READS
while the pack sent to the remote carries the originals.

**A REF NAME is the one published thing the content scan cannot reach**, and the argument
is the pathname rule's exactly: a branch or tag named after an SSN discloses it whatever
it points at, and nothing downstream masks it. So the rule stays here rather than moving
into the hook — `--names <rev>` reads names on stdin and applies these same patterns, with
`.pii-allowlist` read from the revision being published, on the same terms as every other
mode. The hook hands it both ref names before it looks at any content.

`PII_SCAN_CONTEXT` sets the wording and nothing else. It decides the banner's label, the
escape hatch (an allowlist entry added on top does not clear the commit already carrying
the value), the command that lists a redacted path — `--cached` names an index that is
empty at push time — and the shallow-history remedy, which is a `fetch-depth` setting only
in CI. The set is CLOSED and an unknown value is an error, for the reason
`agents/lib/capabilities.py` gives about its own ids: lowercase `push` printed a banner
reading "push BLOCKED" and quietly dropped the push-specific remedy. Every RULE stays in
the script; the label is all a caller may set.

## What it blocks

Four rules, in the order they are cheapest to reason about:

1. **Sensitive file types** — the extensions a taxpayer's documents arrive as.
2. **Sensitive filenames.** The list stays in the script rather than in prose.
   `generate_tax_pdf.py` is its counter-intuitive member: source code the content scan
   cannot vet, carrying the taxpayer's NAME, which has neither an SSN nor a credential
   shape. Nothing about the file's contents would ever stop it.
3. **Every runtime workspace directory §1 names** — which is a longer list than §6's
   runtime FILES, and the difference matters here: `projections/`, `withholding/`,
   `.planning/` and `archive/` are directory rules with no file entry to hang off.
   Each is matched root-anchored AND nested, so a clone checked out inside another one
   is covered too.
   `.claude/` is the one directory that cannot take a directory rule, because it also
   holds tracked project config — there the directory is the SCOPE and the basename is
   the RULE.
4. **Content**, over the ADDED lines only: full SSNs in every written form (hyphen, a
   run of spaces, tab, dot or slash — the SAME separator on both sides, which is what
   keeps a money range from reading as an SSN — or nine bare digits), plus
   high-confidence credentials. It reports file and match count and never the value.

**The same SSN patterns run against the PATHNAME, before any exemption** — a file named
after an SSN discloses it whatever it contains — and every path printed in a block
message is redacted.

`tests/fixtures/` and `documents/.gitkeep` are exempt from the filename and path rules
ONLY. The content and pathname scans still cover them.

## What it scans

- **No argument:** the index. This is the git-hook mode.
- **`<base>...<head>`:** each commit in the range INDIVIDUALLY, merge commits included
  and scanned against their first parent. A net diff hides a secret that a later commit
  deleted, and a conflict resolution exists in neither parent.

`.pii-allowlist` is read from **each scanned commit** — from the index in hook mode,
with no working-tree fallback. So an exemption must be staged together with the
synthetic value it covers, or earlier; allowlisting afterwards does not clear the commit
that already leaked it.

## How it fails

Closed, deliberately, in every direction:

- **Every git failure BLOCKS.**
- **So does a `grep` that cannot RUN.** `|| true` cannot tell "no match" from "grep
  failed", and the two are the opposite verdict.
- Every intermediate lives in ONE 0700 work directory removed by a single trap: the raw
  patch holds the unredacted value.

Two environment defects it pins, both of which produced a clean banner over a real
match:

- **`LC_ALL=C`** — under a UTF-8 locale `grep` silently skips any line that is not valid
  UTF-8, and one Latin-1 byte hid every match on its line.
- **`cd` to the repository root** — a pathspec resolved from a subdirectory matched
  nothing, while still printing the clean banner.
