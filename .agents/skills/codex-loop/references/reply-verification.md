# Reply composition and verification

Read this at Step 6, once Step 5 has published the fixes and before any thread is
resolved. This block is a separate shell invocation: it reloads the round state
persisted under `.git/` and re-derives every reply body from the per-comment
class Step 4 recorded, so which finding got which disposition never depends on
shell memory — and so a body is never retyped from recollection at the one point
in the loop where the text becomes public and permanent.

One body is never reused across the batch, and neither opening is typed at reply
time. A single hardcoded body is how a round of pure refutations — PR #23 round
17 was exactly that round — posts "Fixed in …" on findings nothing touched, and
how a `{placeholder}` nobody substituted becomes the round's public answer to a
real finding. The two openings SKILL.md Step 6 quotes are the literal forms the
verification at the end of this file compares against, separator included;
composing them here rather than accepting whatever was typed is what keeps the
refutation class distinguishable from the fix class on the wire.

```bash
STATE_FILE=$(git rev-parse --git-path codex-loop-state.env) || exit 1
. "$STATE_FILE" || exit 1
if ! jq -n -e --argjson handled "$HANDLED_IDS_JSON" \
 --argjson batch "$CURRENT_BATCH_IDS_JSON" '
       (($handled | unique | length) == ($handled | length))
       and (($handled | sort) == ($batch | sort))
     ' >/dev/null; then
  echo "handled IDs do not exactly match this review batch — do not reply"
  exit 1
fi
CURRENT_HEAD=$(gh pr view "$PR" --json headRefOid -q .headRefOid) || exit 1
[ "$CURRENT_HEAD" = "$PUBLISHED_SHA" ] || {
  echo "PR head moved after publication — leave handled threads open"
  exit 1
}
PUBLISHED_PREFIX=${PUBLISHED_SHA:0:7}

# One body per handled root, keyed by comment id, plus the opening it must
# start with — kept beside the body so the verification below can name the
# expected CLASS without deriving it a second time from the class field, which
# is where the two copies would drift. The opening is chosen by `class` and
# never typed: an unrecognised class calls jq's `error`, failing the whole
# pipeline, rather than falling through to the fix wording, which is the
# direction that lies to the reviewer.
REPLY_BODIES_JSON=$(jq -c --arg sha "$PUBLISHED_PREFIX" '
  map({key: (.id | tostring),
       value: ((if .class == "fix"
                  then "Fixed in \($sha) — "
                elif .class == "refute"
                  then "Reviewed at \($sha) — no code change: "
                else error("unknown reply class for comment \(.id)")
                end) as $opening
               | {opening: $opening, body: ($opening + .reason)})})
  | from_entries' <<<"$REPLY_PLAN_JSON") || exit 1

# Last stop before the text is public. Three things are checked because each
# fails on its own: the composed set must cover exactly the handled roots (a
# missing entry silently skips a thread that Step 4 promised to answer), every
# opening must be one of the two sanctioned forms carrying THIS published head,
# and no body may still contain a brace. That last one is not cosmetic — braces
# are this repository's placeholder convention, so a body containing one is a
# reply that answers nothing, about to be posted and then resolved. A reason
# that genuinely needs a literal brace is reworded, not exempted.
if ! jq -e --argjson handled "$HANDLED_IDS_JSON" --arg sha "$PUBLISHED_PREFIX" '
       (keys_unsorted | map(tonumber) | sort) == ($handled | sort)
       and all(.[]; (.body | test("[{}]") | not)
                    and (.opening == "Fixed in \($sha) — "
                         or .opening == "Reviewed at \($sha) — no code change: "))
     ' <<<"$REPLY_BODIES_JSON" >/dev/null; then
  echo "prepared replies do not match the handled roots, or one is unsubstituted"
  exit 1
fi

HANDLED_COMMENT_IDS=()
while IFS= read -r COMMENT_ID; do
  HANDLED_COMMENT_IDS+=("$COMMENT_ID")
done < <(jq -r '.[]' <<<"$HANDLED_IDS_JSON")
reply_failed=0
for COMMENT_ID in "${HANDLED_COMMENT_IDS[@]}"; do
  # `-re` so a comment with no prepared body exits non-zero instead of posting
  # the four characters `null`. The validation above already forbids that state;
  # this is the cheap second lock on the one operation that cannot be undone.
  if ! BODY=$(jq -re --arg id "$COMMENT_ID" '.[$id].body' \
                <<<"$REPLY_BODIES_JSON"); then
    reply_failed=1
    continue
  fi
  gh api "repos/$OWNER/$NAME/pulls/$PR/comments/$COMMENT_ID/replies" \
    -f body="$BODY" >/dev/null || reply_failed=1
done
if [ "$reply_failed" -ne 0 ]; then
  echo "one or more review replies failed — leave handled threads open"
  exit 1
fi

# Re-read the REST comments and prove each handled root carries a published
# reply of ITS OWN class before any GraphQL resolution is attempted. Matching
# only the commit SHA is what lets the wrong class through: a fix claim posted
# on a refuted finding names the same SHA and passes, and the thread is then
# resolved on a statement that no code changed and code changed. Requiring the
# reply to START with that comment's own opening pins class and head together,
# and re-reading rather than trusting the loop's exits means a successful final
# reply cannot hide an earlier network or authentication failure.
if ! REPLIES_AFTER=$(gh api --paginate \
       "repos/$OWNER/$NAME/pulls/$PR/comments"); then
  echo "could not verify review replies — leave handled threads open"
  exit 1
fi
if ! printf '%s\n' "$REPLIES_AFTER" | jq -s -e \
 --argjson bodies "$REPLY_BODIES_JSON" '
       [.[][] | select(.in_reply_to_id != null)] as $replies
       | all($bodies | to_entries[];
           (.key | tonumber) as $root
           | .value.opening as $opening
           | any($replies[];
               (.in_reply_to_id == $root) and (.body | startswith($opening))))
     ' >/dev/null; then
  echo "handled review threads are not all answered in their recorded class"
  exit 1
fi
```

The reply text is derived end to end from the plan Step 4 committed to while the
findings were open, and every gate here fails closed by leaving threads
unanswered and unresolved. An unanswered thread costs one more round; a thread
answered "Fixed in …" and resolved on a fix that does not exist costs the
finding.
