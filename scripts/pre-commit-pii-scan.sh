#!/bin/bash
#
# PII scan — file types, sensitive filenames, and content.
#
# Usage:
#   scripts/pre-commit-pii-scan.sh                  # staged changes (git hook)
#   scripts/pre-commit-pii-scan.sh <base>...<head>  # a commit range (CI)
#
# CI calls this script rather than re-implementing it. The two used to be
# hand-mirrored, and every rule had to be edited in both places or the gate
# diverged silently — which is exactly what happened: four bypasses shipped in
# the hook and in the copy simultaneously.
#
# Every failure path BLOCKS. An unperformed scan must never look like a clean
# one, so a git command that cannot run is an error, not an empty result.
set -euo pipefail

# Byte-oriented matching, not character-oriented. Under a UTF-8 locale — the
# macOS default — grep SILENTLY SKIPS any input line that is not valid UTF-8,
# so a single stray Latin-1/cp1252 byte anywhere on a line hid EVERY match on
# it: `Client Ren\xe9 Dupont SSN <full ssn>` committed clean. This toolkit
# ingests foreign-language documents, so that byte is the expected case, not an
# exotic one. Every pattern here is pure ASCII, which makes C strictly the right
# locale: byte matching cannot be defeated by the encoding the input happens to
# use, and for the token split a non-ASCII byte becomes a separator, which can
# only expose more nine-digit runs, never hide one.
export LC_ALL=C

# Resolved paths are relative to the REPO ROOT, but a pathspec is resolved
# relative to the CURRENT DIRECTORY. Run from a subdirectory the two disagreed:
# every per-file diff matched nothing, so no content was scanned and the script
# printed its "PII scan clean" banner having inspected nothing — the precise
# false pass that banner exists to make impossible. Git runs hooks from the
# worktree root and CI runs at the workspace root, so both production callers
# were safe; a human or agent invoking this by hand was not.
SCAN_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"

# Git's empty tree, used as the parent of a root commit.
EMPTY_TREE=4b825dc642cb6eb9a060e54bf8d69288fbee4904

DIFF_RANGE="${1:-}"
if [ -n "$DIFF_RANGE" ]; then
  CONTEXT="CI"
  ESCAPE_HATCH="Remove the offending content, or add a synthetic value to .pii-allowlist."
else
  CONTEXT="COMMIT"
  ESCAPE_HATCH="If this is intentional, use: git commit --no-verify"
fi

die() {
  echo ""
  echo "=========================================="
  echo "  $CONTEXT BLOCKED — scan could not run"
  echo "=========================================="
  echo ""
  echo "  $1"
  echo ""
  echo "Refusing to report a clean result for a scan that did not happen."
  exit 1
}

[ -n "$SCAN_ROOT" ] || die "not inside a git working tree — nothing to scan against"
cd "$SCAN_ROOT" || die "cannot enter the repository root '$SCAN_ROOT'"

# --- Scratch space ----------------------------------------------------------
#
# ONE directory, mode 0700, removed by a single trap. Every intermediate this
# script writes — the raw patch above all — contains the unredacted value the
# scan exists to reject. They used to be individual mktemp files removed by
# explicit `rm` on each path, with only two of the six covered by the trap: a
# signal, a `set -e` abort, or a `die` between creation and cleanup left a full
# patch, SSN included, sitting in the system temp directory. A directory cannot
# be half-registered, and a later edit that adds a seventh file inherits the
# cleanup for free rather than having to remember it.
WORK=$(mktemp -d) || die "cannot create a temporary working directory"
trap 'rm -rf "$WORK"' EXIT
ALLOW="$WORK/allow"
RAW="$WORK/raw"
LIST="$WORK/list"
REVS="$WORK/revs"
NUMSTAT="$WORK/numstat"
PATCH="$WORK/patch"
GREP_ERR="$WORK/greperr"

# --- Scopes to scan ---------------------------------------------------------
#
# Hook mode scans the index. Range mode scans EVERY COMMIT INDIVIDUALLY:
# `git diff base...head` is only the net tree diff, so a secret added in one
# commit and removed in a later one disappears from it while remaining in the
# pushed history — and catching exactly that is why CI runs this at all.
#
# MERGE COMMITS ARE INCLUDED, against their first parent. A conflict resolution
# is content that exists in the merge and in NEITHER parent, so `--no-merges`
# dropped the only commit carrying it and both sides scanned clean. Diffing a
# merge against its first parent re-examines the merged-in branch's changes
# too — already scanned via their own commits, so redundant work but no new
# false positives — and finally sees the resolution itself.
# Local git configuration can change what `git diff` emits. `color.ui=always`
# wraps added lines in ANSI escapes so they no longer begin with the marker the
# parser looks for; `diff.external` replaces the patch with a program's output;
# a textconv driver rewrites the blob before diffing. Each one empties `added`
# and the scan reports clean. A display preference must not disable a security
# gate, so every diff here asks for machine-readable output explicitly.
DIFF_SAFE="--no-color --no-ext-diff --no-textconv"

# Sets RECORDED_FIRST_PARENT to the first parent a commit records in its OWN
# object, or to the empty string when it records none — a true root commit.
#
# This is the one question `git rev-list --parents` and `git rev-parse <sha>^`
# cannot answer. Both report the parents THIS CLONE HAS. In a shallow clone the
# boundary commit's parents were never fetched, so both describe it exactly as
# they describe a root commit: `rev-list --parents -n 1` prints the sha alone
# and `rev-parse <sha>^` fails. The root special cases below then diff a whole
# SNAPSHOT against the empty tree — every file the boundary carries, most of
# them from history the range never asked about — and a secret that some earlier
# reviewed commit legitimately holds fails today's unrelated pull request. The
# commit OBJECT always carries the true parent list; a shallow graft only hides
# it from traversal.
#
# Only the HEADER block is read — every line before the first genuinely EMPTY
# one. That is what rules out `git cat-file -p | grep '^parent '`, which was
# tried first and false-matches a commit MESSAGE line beginning "parent " (e.g.
# "parent bug tracked separately"), silently turning any commit into a "root"
# and reopening the bypass. Folded headers (gpgsig, mergetag) continue with a
# leading space, and git renders an empty signature line as a single space, so
# neither ends the block early nor starts with "parent ". Pure bash: a `grep`
# that cannot RUN reports "no match", and no rule here may be turned off by a
# failure to test it.
RECORDED_FIRST_PARENT=""
read_recorded_first_parent() {
  # $1 — commit sha.
  local obj line
  RECORDED_FIRST_PARENT=""
  obj=$(git cat-file commit "$1" 2>/dev/null) \
    || die "could not read the commit object for '$1' — cannot tell a root commit from a shallow boundary"
  while IFS= read -r line; do
    [ -n "$line" ] || break
    case "$line" in
      "parent "*) RECORDED_FIRST_PARENT="${line#parent }"; return 0 ;;
    esac
  done <<< "$obj"
  return 0
}

SCOPES=()
if [ -n "$DIFF_RANGE" ]; then
  base="${DIFF_RANGE%%...*}"
  head="${DIFF_RANGE##*...}"
  [ -n "$head" ] || head="HEAD"
  merge_base=$(git merge-base "$base" "$head" 2>/dev/null) \
    || die "cannot find a merge base for '$base' and '$head' — was the base commit fetched? (CI needs fetch-depth: 0)"
  # Materialized to a file rather than piped from a process substitution: a
  # failure inside `< <(...)` is not the WHILE loop's status, so the trailing
  # `|| die` never fired. A failing rev-list left SCOPES empty and the script
  # exited 0 — CI passing having inspected nothing, which is the exact shape of
  # false pass this gate is built to refuse.
  if ! git rev-list "$merge_base..$head" > "$REVS" 2>/dev/null; then
    die "cannot enumerate commits in '$merge_base..$head'"
  fi
  # `A..B` excludes A, which is correct for CI: the base is already on main and
  # was scanned by the pull request that put it there. A ROOT commit is the one
  # base for which that is false — nothing precedes it, so no earlier range can
  # ever have covered it. Excluding it is what made the full-history audit
  # (`$(git rev-list --max-parents=0 HEAD)...HEAD`, the gate before publishing a
  # private repo) report CLEAN on a repository whose initial import carried a
  # full SSN — verified before this line existed. The per-commit scan below
  # already diffs a parentless commit against the empty tree; it simply never
  # received one.
  # `git rev-parse -q --verify "${merge_base}^"` exits non-zero for BOTH
  # "confirmed: no parent" and "the check itself failed" (an I/O error, a
  # broken git wrapper) — `-q` suppresses the message that would otherwise
  # tell the two apart, so treating any failure as proof of root conflates an
  # expected condition with an unexpected error. `git cat-file -p` was tried
  # next, but its output is the WHOLE commit object — header lines plus the
  # free-form message below a blank line — and grepping it for `^parent `
  # false-matches a commit message that itself starts a line with "parent "
  # (e.g. "parent bug tracked separately"), silently reopening the same
  # bypass this line exists to close. `git rev-list --parents -n 1` avoids
  # both problems: it prints exactly one line, `<sha>` followed by zero or
  # more parent shas from git's own parent-tracking, never commit-message
  # text. What it reports is the parents this CLONE HAS, which is not the same
  # question — see read_recorded_first_parent above — so the sha-alone line is
  # a NECESSARY condition for a root commit and the object header decides it.
  parents_line=$(git rev-list --parents -n 1 "$merge_base" 2>/dev/null) \
    || die "cannot determine parents of merge base '$merge_base'"
  if [ "$parents_line" = "$merge_base" ]; then
    # rev-list found no parent. Confirm against the commit's own object before
    # acting on it: in a shallow clone this is what a BOUNDARY looks like too,
    # and a boundary is an ordinary base — already scanned by the pull request
    # that landed it, so `A..B`'s exclusion is right for it and only a true root
    # earns the exception.
    read_recorded_first_parent "$merge_base"
    if [ -z "$RECORDED_FIRST_PARENT" ]; then
      printf '%s\n' "$merge_base" >> "$REVS"
    fi
  fi
  while IFS= read -r sha; do
    [ -n "$sha" ] || continue
    # The FIRST parent, which is the scoping this wants for a merge.
    #
    # Read from the commit object for the same reason as above: this used to be
    # `git rev-parse -q --verify "${sha}^" || echo "$EMPTY_TREE"`, which cannot
    # tell "records no parent" from "records one this clone does not have" and
    # silently widened the second case to a whole-snapshot scan. A recorded
    # parent that is absent is a history this script cannot scan commit by
    # commit, so it BLOCKS and says how to fix it rather than scanning something
    # else and reporting that as coverage.
    read_recorded_first_parent "$sha"
    if [ -z "$RECORDED_FIRST_PARENT" ]; then
      parent="$EMPTY_TREE"
    elif git rev-parse -q --verify "${RECORDED_FIRST_PARENT}^{commit}" > /dev/null 2>&1; then
      parent="$RECORDED_FIRST_PARENT"
    else
      die "commit '$sha' records a parent this clone does not have — a shallow history cannot be scanned commit by commit (CI needs fetch-depth: 0)"
    fi
    # "<allowlist revision>|<git diff arguments>"
    SCOPES+=("$sha|$parent $sha")
  done < "$REVS"
else
  # An empty revision means "read the allowlist from the index".
  SCOPES+=("|--cached")
fi

if [ ${#SCOPES[@]} -eq 0 ]; then
  # A range with no commits is legitimately nothing to scan.
  exit 0
fi

# --- Allowlist --------------------------------------------------------------
#
# Read from the snapshot being COMMITTED, not the working tree: an unstaged
# local edit to .pii-allowlist would otherwise exempt a secret that the
# committed allowlist does not cover.
#
# Resolved PER SCANNED COMMIT, not once from the range's head. Allowlisting a
# value in a later commit would otherwise retroactively exempt the commit that
# leaked it, and that commit is still in the pushed history. The practical
# consequence for a contributor: the allowlist entry must land in the same
# commit as the synthetic value, or in an earlier one.
load_allowlist() {
  # $1 — revision to read .pii-allowlist from; empty means the index.
  #
  # There is NO working-tree fallback. It used to exist for a not-yet-committed
  # allowlist, guarded by "only when the file is untracked" — but staging a
  # DELETION of the tracked file satisfies that guard, so recreating it in the
  # working tree with the sensitive literal re-enabled the fallback and exempted
  # a value the committed snapshot never allowed. An exemption must be part of
  # the snapshot being committed, with no exceptions; an unstaged or untracked
  # allowlist simply does not apply.
  local rev="$1"
  if [ -n "$rev" ]; then
    git show "${rev}:.pii-allowlist" > "$RAW" 2>/dev/null || : > "$RAW"
  else
    git show ":.pii-allowlist" > "$RAW" 2>/dev/null || : > "$RAW"
  fi
  # `grep -vFf` treats EVERY line as a literal pattern, so a comment line would
  # suppress any match containing that comment's text. Strip comments and blanks.
  grep -vE '^[[:space:]]*(#|$)' "$RAW" > "$ALLOW" || : > "$ALLOW"
}

# --- Patterns ---------------------------------------------------------------
#
# Full SSN in every separated form. The masked XXX-XX-1234 storage format has no
# leading digits and never matches.
#
# `[- ]` — one hyphen or exactly one space — was too narrow in four ways at once,
# and each committed a full SSN cleanly: a RUN of spaces (aligned columns), a
# TAB, DOT grouping and SLASH grouping. Dot grouping is not exotic here — this
# toolkit ingests European documents, where grouping a number with dots is the
# ordinary convention.
#
# The alternation requires the SAME separator on both sides, which is how an SSN
# is actually written, and that consistency is what makes widening safe. The
# naive widening — one character class plus `+` — also matches a money RANGE
# (three digits, dot, two digits, then space-hyphen-space, then four digits),
# and this repository contains five of those. Measured across every tracked line
# and every tracked pathname, the form below matches nothing but the allowlisted
# synthetic value.
#
# Examples are described rather than written out: a literal in this comment is a
# literal in a tracked file, and this scan would — correctly — block its own
# source. It has done so before.
SSN_DELIMITED='[0-9]{3}([-][0-9]{2}[-]|[.][0-9]{2}[.]|[/][0-9]{2}[/]|[[:space:]]+[0-9]{2}[[:space:]]+)[0-9]{4}'
# Nine contiguous digits is also a full SSN — a plausible result of manual entry
# or document extraction. Detected by extracting whole alphanumeric tokens and
# keeping those that are exactly nine digits, so a 10-digit phone number or a
# longer identifier is not caught and the extracted value is the bare SSN
# itself (which keeps `grep -vxFf` allowlisting exact). Measured against this
# repository at the time of writing: zero tracked 9-digit runs, so this costs
# no false positives here.
#
# Credentials: hyphens and underscores are INSIDE the secret for project-scoped
# keys (sk-proj-…, sk-ant-…) and for fine-grained GitHub tokens (github_pat_…),
# which a [A-Za-z0-9]{20,} class truncates at the first separator.
#
# AWS access-key IDs come in two forms: AKIA (long-lived) and ASIA (STS
# temporary). Short-lived is not harmless — the window is hours, and a
# repository is the wrong place to spend it.
SECRET_PATTERN='((AKIA|ASIA)[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{40,})'
# The AWS SECRET access key has no prefix — it is 40 unremarkable base64
# characters, indistinguishable from a hash or a base64 blob. The assignment
# keyword is the only thing that identifies it, so it is required; matching the
# value alone would flag every checksum in the repository.
#
# The keyword is spelled with character classes rather than matched with `grep
# -i`, because the same pattern has to work in `sed` too, and the two tools
# spell "case-insensitive" differently and not portably. A pattern that is
# case-insensitive BY CONSTRUCTION behaves identically everywhere it is used.
# `[_-]` covers aws_secret_access_key and aws-secret-access-key alike.
#
# The separator is `*`, not `{1,4}`: YAML aligns values with arbitrary runs of
# spaces, and a five-space gap slipped the credential past. `*` cannot overrun,
# because the class excludes alphanumerics — the value must still be the next
# word after the keyword.
AWS_KEY_NAME='[Aa][Ww][Ss][_-][Ss][Ee][Cc][Rr][Ee][Tt][_-][Aa][Cc][Cc][Ee][Ss][Ss][_-][Kk][Ee][Yy]'
AWS_SECRET_PATTERN="${AWS_KEY_NAME}[^A-Za-z0-9]*[A-Za-z0-9/+=]{40}"

# ONE list, consumed by both the detector and the redactor. They were separate
# lists for exactly one round, and the AWS rule was added to the detector alone
# — so the scanner blocked a path containing an AWS secret and then printed the
# secret in the block message. A rule that is detected but not redacted turns
# the gate into a disclosure channel, so the two can no longer be edited apart.
PII_PATTERNS=("$SSN_DELIMITED" "$SECRET_PATTERN" "$AWS_SECRET_PATTERN")

# Counting hits is a function because the same patterns must cover BOTH the
# added lines and the PATHNAME. A file called reports/<ssn>.txt discloses the
# number whatever its contents are, and the content scan can never see it.
#
# Extracts the MATCHES and filters those against the allowlist, rather than
# dropping any line containing an allowlisted literal. `XXX-XX-` is allowlisted
# so the masked format never trips the scan; line-level filtering meant a line
# carrying BOTH the masked form and a real SSN passed. `-x` requires an
# allowlist entry to equal a whole extracted match, so it can never suppress a
# longer secret it merely prefixes.
# `|| true` cannot tell "no match" (grep exit 1) from "grep could not run"
# (exit >= 2 — missing executable, unreadable pattern file, I/O error), so a
# broken grep produced an empty match set and the scan reported CLEAN on a
# staged SSN. Reproduced with a shim that failed only for the extraction call.
#
# The failure is signalled by printing ERROR rather than by calling `die`,
# because count_hits runs inside a command substitution: `die` there exits the
# SUBSHELL, and the caller would carry on with an empty result — re-creating the
# very fail-open being closed. The caller checks for the sentinel and dies in the
# parent shell, where exiting means something.
#
# The flag is a FILE, not a variable, for the same subshell reason one level
# down: `found+=$(_grep_out ...)` runs _grep_out in a subshell, so a variable it
# set would be discarded at the closing paren and the failure would vanish
# exactly where it must not. A file outlives the subshell.
_grep_out() {
  # $1 = input text; remaining args = grep arguments. Prints matches; records a
  # grep EXECUTION failure (exit >= 2) in $GREP_ERR, which no-match never sets.
  local text="$1" out status=0
  shift
  out=$(printf '%s\n' "$text" | grep "$@") || status=$?
  if [ "$status" -gt 1 ]; then
    echo 1 >> "$GREP_ERR"
    return 0
  fi
  printf '%s' "$out"
}

# Artifact digests are not PII, and the bare-nine-digit rule cannot tell them
# apart. A sha256 is 64 hex characters, and a maximal digit run of EXACTLY nine
# occurs inside one often enough that requirements.txt trips this rule 52 times
# the moment the dependency tree is pinned with --generate-hashes. The
# documented escape is `--no-verify`, so a gate that fires on correct data
# every time the lock is regenerated would train contributors to bypass the PII
# scan itself — the failure this repo has already paid for elsewhere.
#
# Scoped along BOTH axes so it can never widen into a hiding place:
#   - the exact PATH (never a name pattern, never a directory), and
#   - a token that must be the literal `--hash=sha256:` followed by exactly 64
#     lowercase hex characters.
# Nothing else in the file is exempt: a stray SSN on a requirements line is
# still scanned, because only the digest tokens are removed, not the lines
# holding them.
#
# The acceptance record is the second path with the same shape and the same
# stakes. It pins four sha256 digests — two agent definitions, two replay
# inputs — and renewing one means committing one, so the every-regeneration
# clause above applies verbatim: a pin whose digest happens to hold a nine-digit
# run blocks the commit that records the re-run, and `--no-verify` is the
# documented way out. Its token is `sha256 ` followed by a BACKTICKED 64 hex
# characters, which is the shape that file writes and the shape it tells a
# renewer to write; anything else there, digest-shaped or not, is scanned.
#
# What this was chosen OVER is the reason it is scoped here at all, and it is an
# alternative that was DECLINED rather than one that ever shipped: exempting the
# nine-digit run inside one of those digests in `.pii-allowlist`. That file is
# global and matched by value, so a valid-shaped nine-digit number listed there
# would be suppressed in every file and every pathname in the repository, and a
# real SSN equal to it would commit clean. An exemption that can be bound to a
# path and a token has to be, and this is where both bounds exist.
#
# Stated as a rejected option and not as history, because it was written here as
# history in three places at once — this comment, the acceptance record and
# docs/contributing/test-invariants.md — and no revision of `.pii-allowlist` has
# ever carried a digest's digits. A reader auditing the tightening looks for the
# removed entry, finds nothing, and is left deciding whether the whole rationale
# is fiction or a squash ate it.
strip_artifact_digests() {
  # $1 — repo-relative path, $2 — added text
  case "$1" in
    requirements.txt|requirements.in)
      printf '%s' "$2" | sed -E 's/--hash=sha256:[0-9a-f]{64}//g'
      ;;
    docs/ops/review-subagent-acceptance.md)
      printf '%s' "$2" | sed -E 's/sha256 `[0-9a-f]{64}`//g'
      ;;
    *) printf '%s' "$2" ;;
  esac
}

count_hits() {
  local text="$1" pattern found
  : > "$GREP_ERR"
  found=""
  for pattern in "${PII_PATTERNS[@]}"; do
    found+=$(_grep_out "$text" -oE "$pattern")
    found+=$'\n'
  done
  # The bare-nine-digit rule is token-based rather than a regex, so that a
  # 10-digit phone number or a longer identifier is NOT caught and the extracted
  # value is the bare SSN itself (which keeps `grep -vxFf` allowlisting exact).
  #
  # The token is a run of DIGITS. Letters used to be part of it, which merged a
  # label with its value — `SSN<nine digits>` became one token that the
  # exactly-nine-digits test rejected, and a full SSN committed cleanly. Dropping
  # letters is the same correction already applied to underscore one round
  # earlier, and for the same reason: `label<digits>` is precisely how this gets
  # written. A maximal digit run still cannot be a substring of a longer number,
  # so a 10-digit phone number remains one 10-digit token and is not caught.
  # Measured across every tracked path and every tracked line: zero nine-digit
  # runs, so this costs no false positives here.
  #
  # `grep -xE` rather than `awk '/^[0-9]{9}$/'`: interval expressions are an
  # OPTIONAL awk feature, and mawk — Debian and Ubuntu's default awk, which is
  # what CI runs — treats `{9}` as literal characters instead of erroring. This
  # rule was one `update-alternatives` away from being a silent no-op in the
  # exact venue meant to backstop `--no-verify`. grep's interval support is
  # universal, and dropping awk removes the dependency rather than betting on it.
  found+=$(_grep_out "$(_grep_out "$text" -oE '[0-9]+')" -xE '[0-9]{9}')

  local nonblank filtered
  nonblank=$(_grep_out "$found" -vE '^[[:space:]]*$')
  filtered=$(_grep_out "$nonblank" -vxFf "$ALLOW")

  if [ -s "$GREP_ERR" ]; then
    printf 'ERROR\n'
    return 0
  fi
  if [ -z "$filtered" ]; then
    printf '0\n'
    return 0
  fi
  printf '%s\n' "$filtered" | wc -l | tr -d '[:space:]'
}

# Every message this script prints names a file. When the PATHNAME is itself the
# sensitive value, printing it performs exactly the disclosure the scan exists
# to prevent — into terminal scrollback and CI logs.
redact() {
  local text="$1" pattern
  for pattern in "${PII_PATTERNS[@]}"; do
    # `#` as the delimiter: `/` appears inside the AWS value class and `|` is
    # the alternation in SECRET_PATTERN, so either would terminate the command
    # early. No pattern contains `#`.
    text=$(printf '%s' "$text" | sed -E "s#$pattern#[REDACTED]#g")
  done
  # DELIBERATELY BROADER than the detector's token rule, which requires exactly
  # nine digits: this matches nine digits ANYWHERE, including inside a longer
  # run. That costs a legitimate `statement_202604151234.pdf` its readable name
  # in the block message, and the trade was re-examined and kept — narrowing it
  # to the detector's token semantics would print an SSN embedded in a ten-digit
  # account number verbatim, which is the detected-but-not-redacted class of bug
  # that turns this gate into a disclosure channel. Over-redaction costs
  # legibility; under-redaction discloses. The message says names may be
  # redacted so a mangled one cannot be mistaken for a file that does not exist,
  # and the authoritative list is `git diff --cached --name-only`.
  printf '%s' "$text" | sed -E 's/[0-9]{9}/[REDACTED]/g'
}

BLOCKED_EXTENSIONS="pdf|xlsx|xls|csv|doc|docx|png|jpg|jpeg|gif|bmp|tiff|heic|zip|tar|gz"

BLOCKED=()
CONTENT_BLOCKED=()
scanned_files=0

for scope in "${SCOPES[@]}"; do
  load_allowlist "${scope%%|*}"

  # Word-split intentionally: the diff arguments are either "--cached" or
  # "<parent> <sha>".
  # shellcheck disable=SC2086
  set -- ${scope#*|}

  # --- File list, NUL-delimited --------------------------------------------
  #
  # `--name-only` WITHOUT -z quotes any path containing non-ASCII or special
  # characters ("documents/\303\251tat.pdf"). The quoted form matches neither
  # the extension rules below nor a `-- <path>` pathspec, so a foreign-language
  # filename slipped BOTH scans. This toolkit ingests foreign-language documents.
  #
  # The exit status is captured explicitly: inside a process substitution a
  # failing git would leave the list empty and the scan would report success on
  # a diff it never computed.
  #
  # `--diff-filter=d` is a DENYLIST — every status except deletion. The previous
  # allowlist (ACMR) silently dropped typechange (T): replacing a tracked symlink
  # with a regular file bypassed the filename rules and the content scan alike.
  # Enumerating the statuses that matter is the same shape of mistake twice over,
  # so this names the only status that is genuinely safe to skip — a deletion adds
  # no lines and creates no file.
  if ! git diff $DIFF_SAFE "$@" --name-only -z --diff-filter=d > "$LIST" 2>/dev/null; then
    die "'git diff ${scope#*|} --name-only' failed"
  fi

  FILES=()
  while IFS= read -r -d '' f; do
    FILES+=("$f")
  done < "$LIST"

  [ ${#FILES[@]} -eq 0 ] && continue

  for file in "${FILES[@]}"; do
    # `${file##*/}` rather than `basename "$file"`: a path whose name begins
    # with a dash is parsed as an OPTION by basename. `documents/--version.pdf`
    # made it exit non-zero with a raw "illegal option" message, aborting the
    # scan before any banner — fail-closed, but indistinguishable from a crash
    # and in breach of this script's own rule that a refusal explains itself.
    # Parameter expansion has no option parsing and spawns no process.
    filename="${file##*/}"
    # Every message below prints this, never the raw path.
    safe_file=$(redact "$file")

    scanned_files=$((scanned_files + 1))

    # --- Pathname scan ------------------------------------------------------
    #
    # The path is checked BEFORE the exemptions, because no exemption should
    # ever cover an SSN in a filename. reports/<ssn>.txt would otherwise commit
    # cleanly: the filename rules only know extensions and known names, and the
    # content rules only read added lines.
    path_hits=$(count_hits "$file")
    # Checked HERE, in the parent shell: `die` inside the command substitution
    # above would exit only that subshell and leave the scan running blind.
    [ "$path_hits" = "ERROR" ] && die "grep could not run while scanning a pathname"
    if [ "${path_hits:-0}" -gt 0 ]; then
      CONTENT_BLOCKED+=("  BLOCKED PATHNAME ($path_hits match(es)): $safe_file")
      continue
    fi

    # --- Filename / path rules --------------------------------------------
    #
    # Synthetic test fixtures must share filenames with real workspace files,
    # because the code under test reads result.json / taxpayer_profile.json /
    # tax_law_*.json from a workspace directory. They are exempt from the
    # FILENAME rules only — the content scan below still covers them, since a
    # real SSN in a synthetic fixture is exactly the mistake it exists to catch.
    #
    # documents/.gitkeep is tracked on purpose (it is what makes the directory
    # exist in a fresh clone), so it is exempt from the `documents/` path rule —
    # and from that rule ONLY. It used to `continue` before this block, which
    # skipped the content scan too, so a .gitkeep edited to hold an SSN passed.
    case "$file" in
      tests/fixtures/*|documents/.gitkeep) ;;
      *)
        # Runtime workspace directories. These are gitignored, but `git add -f`
        # bypasses .gitignore, and a text artifact under summaries/ or
        # documents/ carries financial data that the content patterns would not
        # recognise as sensitive.
        case "$file" in
          documents/*|*/documents/*|summaries/*|*/summaries/*|reviews/*|*/reviews/*|scenarios/*|*/scenarios/*|projections/*|*/projections/*|withholding/*|*/withholding/*|.planning/*|*/.planning/*|archive/*|*/archive/*)
            BLOCKED+=("  BLOCKED PATH (runtime workspace): $safe_file")
            continue ;;
        esac

        # Agent runtime state under `.claude/` — the same category as the
        # runtime directories above, reached the same way (gitignored, so
        # anything arriving here came through `git add -f` or a workspace whose
        # .gitignore predates the entry), but it cannot be written as a
        # directory rule. `.claude/` legitimately holds tracked project config:
        # settings.json carries the PostToolUse hooks and skills/ holds the
        # relative symlinks Claude Code discovers through. Blocking the
        # directory would make the repository's own configuration
        # uncommittable, and a gate that fires on correct data is how
        # contributors learn to reach for --no-verify. So the directory is the
        # SCOPE and the basename is the RULE, which is why this is two nested
        # cases rather than one list of full paths — and the outer scope covers
        # the nested form (`*/.claude/*`) for a workspace checked out a level
        # down inside another clone, where no root-anchored pattern reaches.
        #
        # The content scan below cannot stand in for this rule. `.archive-files`
        # and `.archive-dirs` are NUL-separated lists of paths beneath archive/,
        # which is a taxpayer's scanned documents named after what they are —
        # and a pathname has neither an SSN shape nor a credential shape, so a
        # full-history index of one is read through and pronounced clean. The
        # two stamps carry no identity themselves, but they are per-machine
        # state that no other clone can act on.
        #
        # Each hook-written name ends in `*` to cover the `mktemp` sibling it is
        # constructed as before being moved over the real name
        # (`.perms-stamp.XXXXXX`). A hook killed by its configured timeout
        # mid-refresh leaves that sibling behind holding what the finished file
        # would have held, which makes the leftover the LIKELIER of the two to
        # be lying loose when someone runs `git add .`.
        #
        # Matching is the shell's own `case` globbing, as above: no external
        # matcher means no execution failure that could read as "no match".
        case "$file" in
          .claude/*|*/.claude/*)
            case "$filename" in
              .perms-stamp*|.skill-invariants-stamp*|.archive-dirs*|\
              .archive-files*|.archive-index-dirty*|*.local.md|*.local.json)
                BLOCKED+=("  BLOCKED PATH (agent runtime state): $safe_file")
                continue ;;
            esac ;;
        esac

        # `printf`, not `echo`: bash's echo consumes a leading -n/-e/-E as a
        # flag, so a file named exactly `-e` would be tested as an EMPTY name.
        if printf '%s\n' "$filename" | grep -qiE "\.($BLOCKED_EXTENSIONS)$"; then
          ext=$(printf '%s\n' "$filename" | grep -oiE "\.($BLOCKED_EXTENSIONS)$" | tr '[:upper:]' '[:lower:]')
          BLOCKED+=("  BLOCKED FILE TYPE ($ext): $safe_file")
          continue
        fi

        # Exact runtime filenames. All of them are gitignored, so anything
        # reaching here arrived through `git add -f` or a workspace whose
        # .gitignore predates the entry — the same route as the directory rules
        # above, and the whole reason a filename rule exists alongside them.
        #
        # generate_tax_pdf.py is the one entry that cannot be inferred from the
        # rest, and the tempting reading is precisely wrong: it is SOURCE CODE,
        # so it looks like the kind of file a repository is for, and the content
        # scan below looks like the layer that should vet it. That scan cannot.
        # It matches SSN and credential SHAPES, and tax-generate writes this
        # generator per workspace with the taxpayer's NAME in its section
        # headers — a name has neither shape, so the file is read line by line
        # and pronounced clean. .gitignore says so in its own comment and
        # AGENTS.md §1 counts it as sensitive; the permissions hook already
        # chmods it 600. Being named by every one of those and not by this case
        # is what let `git add -f generate_tax_pdf.py` commit a taxpayer's name.
        # The filename rule is the only layer with any grip on this file.
        case "$filename" in
          taxpayer_profile.json|result.json|answers.md|parse_ledger.json|holdings.json|projection_inputs.json|crypto_transactions.json|validation_receipt.json|eval_run.json|generate_tax_pdf.py)
            BLOCKED+=("  BLOCKED FILENAME ($(redact "$filename")): $safe_file")
            continue ;;
        esac

        if printf '%s\n' "$filename" | grep -qE '^(tax_law_.*\.json|FBAR_.*\.md|[0-9]{4}-review\.md|tax_calendar_.*\.ics)$'; then
          BLOCKED+=("  BLOCKED FILENAME ($(redact "$filename")): $safe_file")
          continue
        fi
        ;;
    esac

    # --- Content scan ------------------------------------------------------
    #
    # `:(literal)` stops a filename containing glob metacharacters from being
    # interpreted as a pathspec pattern.
    spec=":(literal)$file"

    # A blob git classifies as binary produces NO added lines — only a
    # "Binary files differ" summary — so an added-lines scan reads it as clean.
    # A UTF-16 notes.txt holding an SSN is the concrete case, and byte-level
    # grepping would not find it either (the digits are NUL-interleaved).
    # Absence of scannable content fails CLOSED. Genuinely binary formats are
    # already rejected above, so reaching here is anomalous.
    if ! git diff $DIFF_SAFE "$@" --numstat --diff-filter=d -- "$spec" > "$NUMSTAT" 2>/dev/null; then
      die "'git diff --numstat' failed for $safe_file"
    fi
    if [ "$(awk 'NR==1{print $1}' "$NUMSTAT")" = "-" ]; then
      CONTENT_BLOCKED+=("  BLOCKED BINARY (content not scannable): $safe_file")
      continue
    fi

    # Added lines only, so editing elsewhere in a file does not re-flag content
    # that was already committed.
    #
    # `--output-indicator-new` marks added lines with '>' instead of '+'. The
    # `+++ b/path` file header keeps its '+' prefix, so content is separable
    # from the header without a heuristic — a real line beginning "++ " renders
    # as "+++ " under the default indicator and was being discarded as a header.
    if ! git diff $DIFF_SAFE "$@" -U0 --output-indicator-new='>' --diff-filter=d -- "$spec" \
         > "$PATCH" 2>/dev/null; then
      die "'git diff -U0' failed for $safe_file (git 2.20+ required for --output-indicator-new)"
    fi
    added=$(sed -n 's/^>//p' "$PATCH")
    [ -n "$added" ] || continue

    hits=$(count_hits "$(strip_artifact_digests "$file" "$added")")
    [ "$hits" = "ERROR" ] && die "grep could not run while scanning $safe_file"
    if [ "${hits:-0}" -gt 0 ]; then
      # Report the FILE and a COUNT only. Never echo the match — that would put
      # the value into terminal scrollback and CI logs.
      CONTENT_BLOCKED+=("  BLOCKED CONTENT ($hits match(es)): $safe_file")
    fi
  done
done

if [ ${#BLOCKED[@]} -gt 0 ]; then
  echo ""
  echo "=========================================="
  echo "  $CONTEXT BLOCKED — Sensitive files found"
  echo "=========================================="
  echo ""
  printf '%s\n' "${BLOCKED[@]}" | sort -u
  echo ""
  echo "These files should never be committed."
  echo "A name shown as [REDACTED] held an SSN-shaped or credential-shaped run;"
  echo "list the real paths with: git diff --cached --name-only"
  echo "$ESCAPE_HATCH"
  exit 1
fi

if [ ${#CONTENT_BLOCKED[@]} -gt 0 ]; then
  echo ""
  echo "=========================================="
  echo "  $CONTEXT BLOCKED — PII pattern in content"
  echo "=========================================="
  echo ""
  printf '%s\n' "${CONTENT_BLOCKED[@]}" | sort -u
  echo ""
  echo "A full SSN or credential appears in the changes, or a file could not be"
  echo "scanned because git treats it as binary."
  echo "Store only the last four digits, as XXX-XX-1234."
  echo "A name shown as [REDACTED] held an SSN-shaped or credential-shaped run;"
  echo "list the real paths with: git diff --cached --name-only"
  echo "If the value is synthetic, add it to .pii-allowlist."
  echo "$ESCAPE_HATCH"
  exit 1
fi

# In range mode the CI log is the only record this gate ran, and a silent exit 0
# is indistinguishable there from a scan that examined nothing — a false pass
# that looks exactly like a real one. State the coverage. The git hook stays
# quiet on success, where silence is the right signal.
if [ -n "$DIFF_RANGE" ]; then
  echo "PII scan clean — ${#SCOPES[@]} commit(s) inspected individually,"
  echo "${scanned_files} changed file(s) checked for sensitive file types,"
  echo "workspace paths, and SSN/credential patterns in added lines."
fi

exit 0
