---
name: codex-loop
description: Drive the automated PR review loop to a verified clean pass — trigger the review, collect findings without mistaking a re-anchored stale one for a fresh one, fix, resolve threads, repeat. Use when a pull request is open on this repository and needs to reach a clean review, or when a review has returned findings to work through.
license: Apache-2.0
disable-model-invocation: true
---

# codex-loop

Drives the review loop described in CLAUDE.md to a **verified** clean pass. The
prose there states the traps; this holds the commands, which are the part that
gets re-derived from scratch every time.

This is repository housekeeping, not part of the tax pipeline. It touches no
taxpayer data and must never carry any.

**Agent note.** The frontmatter carries `disable-model-invocation: true`, which
only some agents honour. Where it is not honoured this skill can be selected
automatically, and the steps below mutate remote state. On any agent that does
not support that key, obtain the user's explicit consent before **every remote
write**. A prior request authorizing that exact action or the complete review
loop counts; automatic skill selection and a request only to inspect or fix
findings do not. Remote writes include trigger comments, review replies, thread
resolution, pushes, PR metadata or ready-state changes, and any other GitHub
mutation. Local inspection, edits, tests and commits are not remote writes.
Everything else here needs only `gh`, `jq` and a shell.

> **Before anything else — AGENTS.md §1.** Never paste taxpayer data into an
> issue, a PR body, or a review reply. That text is retained by the platform and
> is agent input for hosted coding agents. Quote line numbers and figures from
> `agents/lib/`, never from a return. If a finding cannot be discussed without
> naming taxpayer data, discuss it locally and say so on the thread instead.

## Prerequisites

- An open PR on this repository, and `gh` authenticated.
- `jq` for the polling commands below.
- Initialize the round state once. It lives under `.git/` (local, untracked,
  owner-only), because many agents start a fresh shell for every command. Every
  later executable block reloads this file instead of relying on shell memory.
  Blocks APPEND and re-source it, so the last written value of a name wins —
  which makes a verdict that is downgraded in memory only worse than merely
  lost: the earlier affirmative is still the last line in the file, and it is
  what the next shell reads. Every retraction below is therefore appended too:

```bash
PR="{pull request number}"      # e.g. PR=73
# Guard the bootstrap, not only the API calls it feeds. Every name here is
# interpolated into `repos/$OWNER/$NAME/issues/$PR/...` by later blocks that
# source this file and never re-derive it, so a failure here surfaces blocks
# away as a 404 — which, read through the Step 3 filters, yields no comments and
# no findings and is indistinguishable from the clean pass that ends the loop.
# An unauthenticated `gh` exits non-zero AND prints nothing — check both.
if ! REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner) \
   || [ -z "$REPO" ]; then
  echo "could not identify the repository — authenticate gh and start again"
  exit 1
fi
OWNER=${REPO%/*}; NAME=${REPO#*/}
# `${REPO%/*}` on a slashless string strips nothing, leaving OWNER === NAME ===
# REPO: a well-formed path naming a repository nobody asked for. `PR` is the one
# value typed by hand, and must fail here, not once persisted and sourced below.
if [ "$OWNER" = "$REPO" ] || [ -z "$OWNER" ] || [ -z "$NAME" ]; then
  echo "'$REPO' is not owner/name — do not build API paths from it"
  exit 1
fi
case $PR in ''|*[!0-9]*) echo "PR must be the PR NUMBER"; exit 1;; esac
CODEX_BOT='chatgpt-codex-connector[bot]'
STATE_FILE=$(git rev-parse --git-path codex-loop-state.env) || exit 1
umask 077
{
  printf 'PR=%q\nREPO=%q\nOWNER=%q\nNAME=%q\nCODEX_BOT=%q\n' \
    "$PR" "$REPO" "$OWNER" "$NAME" "$CODEX_BOT"
  printf 'TRIGGER=%q\nHEAD_AT_TRIGGER=%q\nCURRENT_BATCH_IDS_JSON=%q\n' \
    '' '' '[]'
  printf 'REPLY_PLAN_JSON=%q\nHANDLED_IDS_JSON=%q\nPUBLISHED_SHA=%q\n' \
    '[]' '[]' ''
  printf 'REVIEW_CLEAN=%q\nCI_GREEN=%q\n' '0' '0'
} > "$STATE_FILE" || exit 1
chmod 600 "$STATE_FILE" || exit 1
```

## The loop

### 1. Self-review first

Read the diff against the review's own lenses before triggering it. This
converges in far fewer rounds than round-tripping every finding through the bot,
and it is the cheapest step in the loop.

The lenses themselves are agent-neutral and live in the repository docs: AGENTS.md
§4 for the bias-direction rule (a fallback that OVER-states the number the user
acts on is a hard error; one that UNDER-states it degrades with a banner), and
`docs/contributing/tax-gotchas.md` for the rules that have already caused real
bugs. Read those and apply them yourself.

If your agent supports delegating to a specialised reviewer, this repository
ships one for `agents/lib/` diffs — `bias-direction-reviewer`, in
`.claude/agents/`, which Claude Code discovers natively and whose prompt any
other agent can read as a checklist. Without that capability, the degrade is
simply to work through the same two documents in order; nothing in the loop
depends on it.

### 2. Trigger, and take the timestamp from GitHub

This step posts a remote comment. Apply the consent rule above before running
it; reaching Step 2 is not itself consent.

```bash
STATE_FILE=$(git rev-parse --git-path codex-loop-state.env) || exit 1
. "$STATE_FILE" || exit 1
if ! HEAD_AT_TRIGGER=$(gh pr view "$PR" --json headRefOid -q .headRefOid) \
   || [ -z "$HEAD_AT_TRIGGER" ]; then
  echo "could not identify the PR head — do not trigger or poll"
  exit 1
fi

# Post through the API so the timestamp comes back from GITHUB's clock. A local
# `date -u` is this machine's clock, and one running a few seconds fast — routine
# without NTP, and common in containers — puts $TRIGGER ahead of a review that
# lands immediately after, which then vanishes from all three surfaces below. A
# dropped finding is indistinguishable from the clean pass that ends the loop.
if ! TRIGGER=$(gh api "repos/$OWNER/$NAME/issues/$PR/comments" \
                 -f body="@codex review" --jq .created_at) \
   || [ -z "$TRIGGER" ]; then
  echo "review trigger failed or returned no timestamp — do not poll"
  exit 1
fi
# One append, every per-round name in it. Each is stale the moment a new trigger
# exists — last round's batch, its replies, its verdicts — and the file is
# last-write-wins, so a name left out here is not "unset", it is silently
# carried forward. REVIEW_CLEAN and CI_GREEN are the dangerous two: carried
# forward as 1 they certify this round before it has produced any evidence.
printf 'HEAD_AT_TRIGGER=%q\nTRIGGER=%q\nCURRENT_BATCH_IDS_JSON=%q\nHANDLED_IDS_JSON=%q\nPUBLISHED_SHA=%q\nREVIEW_CLEAN=%q\nREPLY_PLAN_JSON=%q\nCI_GREEN=%q\n' \
  "$HEAD_AT_TRIGGER" "$TRIGGER" '[]' '[]' '' '0' '[]' '0' >> "$STATE_FILE" || {
  echo "could not persist trigger state — do not poll"
  exit 1
}
```

The filters below use `>=`, so the trigger comment itself appears in the issue
comments — that is deliberate, because `>` would also drop anything landing in
the same second. Skip your own comment when reading the output.

A plain `git push` does **not** trigger a review. Only PR open,
ready-for-review, or this comment does. Pushing and then waiting is the single
most common way to sit for an hour on a review that was never requested.

### 3. Poll all three surfaces

A clean pass arrives as a top-level **issue comment** ("Didn't find any major
issues"), not as a formal review. Polling `reviewThreads` alone waits forever on
the one outcome that ends the loop.

```bash
STATE_FILE=$(git rev-parse --git-path codex-loop-state.env) || exit 1
. "$STATE_FILE" || exit 1
# Issue comments — where a clean pass appears
gh api --paginate "repos/$OWNER/$NAME/issues/$PR/comments" \
 --jq ".[] | select(.created_at >= \"$TRIGGER\") | {user: .user.login, body: .body[0:200]}"

# Formal reviews
gh api --paginate "repos/$OWNER/$NAME/pulls/$PR/reviews" \
 --jq ".[] | select(.submitted_at >= \"$TRIGGER\") | {user: .user.login, state, body: .body[0:200]}"

# Inline findings (P1/P2). Materialize the exact root-bot batch used by every
# later handled-ID guard; do not retype IDs from a broader endpoint.
if ! INLINE_COMMENTS=$(gh api --paginate \
       "repos/$OWNER/$NAME/pulls/$PR/comments"); then
  echo "could not fetch inline comments — do not handle or mutate threads"
  exit 1
fi
CURRENT_BATCH_IDS_JSON=$(printf '%s\n' "$INLINE_COMMENTS" | jq -s -c \
 --arg trigger "$TRIGGER" --arg bot "$CODEX_BOT" \
 --arg head "$HEAD_AT_TRIGGER" '
    [.[][] | select(.created_at >= $trigger
                    and .user.login == $bot
                    and .in_reply_to_id == null
                    and .original_commit_id == $head) | .id]') || exit 1
printf 'CURRENT_BATCH_IDS_JSON=%q\n' "$CURRENT_BATCH_IDS_JSON" \
  >> "$STATE_FILE" || exit 1
printf '%s\n' "$INLINE_COMMENTS" | jq -s \
 --argjson batch "$CURRENT_BATCH_IDS_JSON" '
    .[][] | select(.id as $id | $batch | index($id))
    | {id, path, line, body: .body[0:300]}'
```

The persisted inline batch is deliberately limited to root findings from `$CODEX_BOT`
that were created against `$HEAD_AT_TRIGGER`. Human comments,
replies, and late findings from an older in-flight review share this endpoint;
presenting any of them as a finding can put its ID into `HANDLED_IDS_JSON` — the
Step 4 list every guard, reply and resolution downstream reads, and which
`references/reply-verification.md` expands into the `HANDLED_COMMENT_IDS` array
it posts from — and so resolve a thread this round never owned.

**`--paginate` is not optional here.** These endpoints return 30 items per page,
oldest first, and this loop routinely runs long — 28 rounds on PR #65, each
adding a trigger comment, bot output and replies. Past page one the newest
comments, which is to say every one this round produced, are on a page the
command never fetches. The filter then matches nothing on all three surfaces,
which is exactly what a clean pass looks like.

**Filter findings by both `created_at` and `original_commit_id`.** GitHub
re-anchors an un-outdated inline comment's `commit_id` to the current PR HEAD,
so never use that re-anchored `commit_id` for batch identity.
`original_commit_id` stays bound to the head the comment actually reviewed;
pairing it with the trigger timestamp rejects both old re-anchored findings and
late results from an older in-flight review. A clean-pass issue comment is
different: its body names the reviewed commit, and the ending gate below must
match that value to `$HEAD_AT_TRIGGER` rather than trusting time alone.

### 4. Work each finding

Findings are tagged P1 / P2. Treat the tag as an opening bid, not a verdict —
this repository's own severity rule is directional (AGENTS.md §4): a fallback
that OVER-states the number the user acts on is a hard error whatever the bot
labelled it.

Three legitimate outcomes, and the third is not a lesser one:

1. **Fix it.** Add the test that would have caught it. If the finding is about a
   guard, verify the test by making the guard fail on purpose — confirming the
   passing case only proves the happy path.
2. **Fix the docs.** Some findings are real but land on prose rather than code.
3. **Push back, with the source.** A reasoned refutation is a valid resolution.
   PR #23 round 17 refuted a Kraken finding on the ground that end-USD stablecoin
   tickers are textually indistinguishable from fiat USD in concatenated pair
   names, so the "fix" would mis-reject common pairs. Its reasoning and citation
   go into the `refute` record below, which is what Step 6 posts.

Record the REST ID of every root inline comment actually handled in this round,
which of the three outcomes it got, and the one line that will be posted back on
its thread. Do not add an older thread merely because it is still unresolved:

```bash
STATE_FILE=$(git rev-parse --git-path codex-loop-state.env) || exit 1
. "$STATE_FILE" || exit 1
# One record per handled finding. `class` is the disposition, not decoration:
# Step 6 composes the reply opening FROM it. Outcomes 1 and 2 above are `fix`
# (something was committed); outcome 3 is `refute`. Recording a refutation as a
# fix posts a public claim that code changed when none did, and then resolves
# the thread on that claim — strictly worse than saying nothing, because the bot
# and the next human both read the finding as addressed and stop looking.
# Substitute every `{...}`: braces are this repository's placeholder convention,
# and the guards here and in Step 6 refuse to publish one.
REPLY_PLAN_JSON=$(jq -n -c '[
  {id: 12345678, class: "fix",
   reason: "{one line on what changed and why}"},
  {id: 12345679, class: "refute",
   reason: "{the sourced reason no change is correct}"}
]') || exit 1
HANDLED_IDS_JSON=$(jq -c 'map(.id)' <<<"$REPLY_PLAN_JSON") || exit 1
if ! jq -n -e --argjson handled "$HANDLED_IDS_JSON" \
 --argjson batch "$CURRENT_BATCH_IDS_JSON" '
       (($handled | unique | length) == ($handled | length))
       and (($handled | sort) == ($batch | sort))
     ' >/dev/null; then
  echo "handled IDs do not exactly match this review batch"
  exit 1
fi
# The guard above proves the plan names the right findings; this one proves each
# record can become a reply at all. It belongs HERE, while the findings are
# still open in front of you: the same defect reaching Step 6 is one API call
# from being permanent, and reaching neither is a brace placeholder published on
# a public thread as this round's answer to a real finding.
if ! jq -e '
       all(.[]; (.id | type) == "number"
                and (.class == "fix" or .class == "refute")
                and (.reason | type) == "string"
                and (.reason | test("[^[:space:]]"))
                and (.reason | test("[{}]") | not))
     ' <<<"$REPLY_PLAN_JSON" >/dev/null; then
  echo "a reply record has no class, no reason, or an unsubstituted placeholder"
  exit 1
fi
printf 'REPLY_PLAN_JSON=%q\nHANDLED_IDS_JSON=%q\n' \
  "$REPLY_PLAN_JSON" "$HANDLED_IDS_JSON" >> "$STATE_FILE" || exit 1
```

An empty plan means there is nothing to reply to or resolve in Step 6.

### 5. Commit and publish fixes before changing thread state

Never reply "fixed" or resolve a finding while its code exists only locally.
If the commit fails, push authorization is declined, the push fails, or the PR
head does not become the local commit, leave every handled thread open. GitHub
must not present unchanged remote code as addressed.

First commit any local fixes. Stage only the exact paths changed for this round;
never sweep unrelated worktree changes into the review commit:

```bash
STATE_FILE=$(git rev-parse --git-path codex-loop-state.env) || exit 1
. "$STATE_FILE" || exit 1
git diff --check
git status --short
git add -- path/to/changed-file
git commit -m "fix: address review findings"
```

If every handled finding is a sourced refutation and there is no local diff,
there is nothing to commit. Otherwise a successful commit is mandatory.

A push publishes code and is a remote write. Apply the consent rule above
before running it; local permission to fix or commit does not imply permission
to push. Push whenever the local commit is not already the PR head, then verify
the publication rather than trusting the command's exit alone:

```bash
STATE_FILE=$(git rev-parse --git-path codex-loop-state.env) || exit 1
. "$STATE_FILE" || exit 1
if ! jq -n -e --argjson handled "$HANDLED_IDS_JSON" \
 --argjson batch "$CURRENT_BATCH_IDS_JSON" '
       (($handled | unique | length) == ($handled | length))
       and (($handled | sort) == ($batch | sort))
     ' >/dev/null; then
  echo "handled IDs do not exactly match this review batch — do not push"
  exit 1
fi
LOCAL_SHA=$(git rev-parse HEAD)
PR_HEAD=$(gh pr view "$PR" --json headRefOid -q .headRefOid)
if [ "$PR_HEAD" != "$LOCAL_SHA" ]; then
  git push -u origin "$(git branch --show-current)"
fi
PUBLISHED_SHA=$(gh pr view "$PR" --json headRefOid -q .headRefOid)
[ "$PUBLISHED_SHA" = "$LOCAL_SHA" ] || {
  echo "PR head does not contain the handled fixes — leave threads open"
  exit 1
}
printf 'PUBLISHED_SHA=%q\n' "$PUBLISHED_SHA" >> "$STATE_FILE" || exit 1
```

The push is plain: the pre-push hook scans the commits it would publish rather
than refusing it, so `--no-verify` here would skip that scan. Check whether your
agent's own instruction file says to hand this command to the user rather than
running it — in this repository, Claude Code sessions do.

### 6. Reply, then resolve only the published threads

Reply on every handled thread — an unanswered thread is indistinguishable from
an ignored one. A reply is a remote write, so apply the consent rule above
before posting it. Every reply class must include `$PUBLISHED_PREFIX`, which is
the verification marker below, and the two openings are quoted here in the exact
form the verifier accepts, trailing separator included: a fix opens
`Fixed in $PUBLISHED_PREFIX — `, a refutation opens
`Reviewed at $PUBLISHED_PREFIX — no code change: `, and the recorded reason is
appended straight onto it. Both are compared CHARACTER FOR CHARACTER, em dash
and spacing included, so a body typed without the separator is refused instead
of posted: the openings are the only thing telling a fix from a refutation on
the wire, and a match loose enough to accept one missing its separator is loose
enough to accept a fix claim as the answer to a REFUTED finding. Neither is
typed at reply time. This marker is still meaningful when the round has
only refutations: it identifies the head whose finding was examined.

Read and run
[references/reply-verification.md](references/reply-verification.md). It reloads
the round state, re-checks the batch and the published head, composes one body
per handled root from its recorded class, refuses any body still carrying a
brace, posts them, and then proves from GitHub's own copy that every handled
thread received a reply of the RIGHT class before anything is resolved.

There is no REST endpoint for thread resolution. Before that remote mutation,
read and run [references/thread-resolution.md](references/thread-resolution.md).
It reloads the persisted round state, validates handled roots against the
current filtered batch, paginates GraphQL, resolves only the joined thread IDs,
and verifies the result with a second query.

### 7. Re-trigger

Go back to Step 2 after the published threads are answered and resolved. **A
push alone does not re-trigger the review.**

## When the loop actually ends

All three conditions, not any subset:

1. A clean pass — a bot comment opening with "Didn't find any major issues",
   behind emphasis, a heading or the bot's label — arrived after `$TRIGGER`.
2. Its `Reviewed commit` matches `$HEAD_AT_TRIGGER`.
3. The PR HEAD is still `$HEAD_AT_TRIGGER`.

```bash
STATE_FILE=$(git rev-parse --git-path codex-loop-state.env) || exit 1
. "$STATE_FILE" || exit 1
# The marker is matched ANCHORED to the body's start, through leading markdown
# markup and the bot's optional product label, and no further. The previous
# form, `startswith("Codex Review: Didn't find any major issues")`, rejects —
# measured on this host — the documented marker alone, `## Didn't …`, and
# `**Codex Review:** Didn't …`, all three of which this accepts. The class is
# emphasis and headings ONLY: `>` and the backtick are held OUT, blockquote,
# code fence and inline-code span being how markdown QUOTES — with the backtick
# in, a body opening by fencing the marker matched, so "it said <marker> but I
# disagree" read as the bot's own pass. What anchor and class miss fails CLOSED
# — a renamed product string, a typographic apostrophe in "Didn't", a marker
# only ever fenced: REVIEWED_COMMIT comes back EMPTY, as a failed fetch also
# does, REVIEW_CLEAN persists as 0, and the loop re-triggers for ever. The
# remedy for that hang is another markup form here, never a default of 1.
REVIEWED_COMMIT=$(
  gh api --paginate "repos/$OWNER/$NAME/issues/$PR/comments" \
 --jq "[ .[]
      | select(.created_at >= \"$TRIGGER\")
      | select(.user.login == \"$CODEX_BOT\")
      | select(.body | test(\"^[[:space:]#*_]*\"
                            + \"(Codex Review:?[[:space:]#*_]*)?\"
                            + \"Didn't find any major issues\"))
      | .body | capture(\"Reviewed commit:[^0-9a-f]*(?<sha>[0-9a-f]{7,40})\").sha ]
      | ((map(select(. as \$c | \"$HEAD_AT_TRIGGER\" | startswith(\$c))) | last) // last // empty)" \
    | tail -1
)
CURRENT_HEAD=$(gh pr view "$PR" --json headRefOid -q .headRefOid)
REVIEW_CLEAN=0

if [ -z "$REVIEWED_COMMIT" ]; then
  echo "no parseable clean pass for this trigger"
elif [[ "$HEAD_AT_TRIGGER" != "$REVIEWED_COMMIT"* ]]; then
  echo "stale clean pass reviewed $REVIEWED_COMMIT, not $HEAD_AT_TRIGGER"
elif [ "$CURRENT_HEAD" != "$HEAD_AT_TRIGGER" ]; then
  echo "HEAD moved after the trigger — re-trigger on $CURRENT_HEAD"
else
  REVIEW_CLEAN=1
  echo "clean pass covers current HEAD $CURRENT_HEAD"
fi
printf 'REVIEW_CLEAN=%q\n' "$REVIEW_CLEAN" >> "$STATE_FILE" || exit 1
```

A response from an older in-flight review can land after this round's trigger,
so timestamp plus an unchanged current HEAD is insufficient: the body's short
`Reviewed commit`, prefix-matched against the trigger SHA, binds the pass to the
request's actual target. jq PREFERS a head-bound candidate but falls back to the
latest, so a stale-only set reaches the `elif` and is reported as stale, by SHA.

Then confirm CI, bound to the reviewed commit rather than to a freshly-read PR
head — and assert the list is non-empty before reading anything into it. After
CI finishes, read the PR head one final time so a push during the wait cannot
pair A's clean review with B's green checks:

```bash
STATE_FILE=$(git rev-parse --git-path codex-loop-state.env) || exit 1
. "$STATE_FILE" || exit 1
SHA=$HEAD_AT_TRIGGER
CI_GREEN=0
runs=''

if ! runs=$(gh run list --commit "$SHA" --json name,status,conclusion); then
  echo "NOT GREEN: could not fetch CI runs for $SHA — wait and poll again"
elif [ "$(jq 'length' <<<"$runs")" -eq 0 ]; then
  echo "NOT GREEN: no runs exist for $SHA yet — wait and poll again"
elif ! jq -e 'all(.[]; .status == "completed")' >/dev/null <<<"$runs"; then
  echo "NOT GREEN: still running — wait and poll again"
elif jq -e 'all(.[]; .conclusion == "success")' >/dev/null <<<"$runs"; then
  CI_GREEN=1
else
  jq -r '.[] | select(.conclusion != "success") | "FAILED \(.name): \(.conclusion)"' <<<"$runs"
fi

FINAL_HEAD=$(gh pr view "$PR" --json headRefOid -q .headRefOid)
if [ "$FINAL_HEAD" != "$HEAD_AT_TRIGGER" ]; then
  CI_GREEN=0
  REVIEW_CLEAN=0
  echo "HEAD moved while CI was polled — re-trigger on $FINAL_HEAD"
fi
# Append BOTH verdicts, the retraction above included, before reporting either.
# Withdrawing REVIEW_CLEAN in shell memory alone is not a neutral omission: the
# block that set it to 1 already appended that line, and the file is
# last-write-wins, so the next fresh shell sources a clean pass for a HEAD the
# bot never reviewed and the loop ends there. That is precisely the defeat the
# three-condition gate above exists to prevent, arriving through the state file
# rather than through the timestamp.
printf 'REVIEW_CLEAN=%q\nCI_GREEN=%q\n' "$REVIEW_CLEAN" "$CI_GREEN" \
  >> "$STATE_FILE" || {
  echo "could not persist the round verdict — treat this round as unfinished"
  exit 1
}
if [ "$REVIEW_CLEAN" -eq 1 ] && [ "$CI_GREEN" -eq 1 ]; then
  echo "clean review and green CI both cover current HEAD $FINAL_HEAD"
fi
```

`gh pr checks` may still be reporting the *previous* commit's finished runs, and
an empty check list trivially satisfies "nothing pending" — both exit the wait on
a green that was never measured. **The empty case is the one to guard, not just
to know about:** seconds after a push the workflow runs do not exist yet, so the
bare command returns `[]`, nothing is failing, and "no failures" reads as green
on a commit CI never looked at. That is why the branch above reports NOT GREEN
rather than falling through.

## Non-interactive use

Every command here is non-interactive already. In a headless run, do not invent
a resolution for a finding you cannot verify: record it as unresolved and stop.
Record decisions in the PR thread, and anything about the repository's own
conventions in the docs — never in `answers.md`, which is taxpayer workspace.

## Ending on a limit rather than a pass

The review service has usage limits, and hitting one mid-loop is normal — it
happened twice during PR #23. That is not a clean pass. Record which findings
were surfaced and fixed, confirm no thread is unresolved and CI is green, and
say plainly that the loop ended on a limit. Round counts from this repository's
history, for calibration: 28 rounds on PR #65, 19 on PR #23, 16 on PR #20.
