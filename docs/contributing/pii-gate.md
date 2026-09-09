# The PII gate — `scripts/pre-commit-pii-scan.sh`

Relocated out of `AGENTS.md` §1 by Y5 (PR #113), which keeps the rules an agent breaks
without looking anything up and points here for the rest. §1 also gives the one rule
about changing this gate, and the history behind it; what follows is what the gate
actually does.

It runs as the pre-commit hook (installed by `bash agents/install.sh`) and in CI over a
per-trigger range (`scripts/ci-pii-range.sh`). Git hooks are the host-side backstop and
work the same on every agent: they fire on a plain `git commit` from anything that can
commit at all.

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
