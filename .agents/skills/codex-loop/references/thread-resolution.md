# Thread-resolution commands

Read this at Step 6 after every handled root has a verified reply. This block is
a separate shell invocation: it reloads the round state persisted under `.git/`
and revalidates the handled IDs against the bot-authored root findings
materialized by Step 3 before performing any GraphQL mutation.

```bash
STATE_FILE=$(git rev-parse --git-path codex-loop-state.env) || exit 1
. "$STATE_FILE" || exit 1

if ! jq -n -e --argjson handled "$HANDLED_IDS_JSON" \
     --argjson batch "$CURRENT_BATCH_IDS_JSON" '
       ($handled | type) == "array"
       and ($batch | type) == "array"
       and (($handled | unique | length) == ($handled | length))
       and (($handled | sort) == ($batch | sort))
     ' >/dev/null; then
  echo "handled IDs do not exactly match this review batch — do not mutate threads"
  exit 1
fi
CURRENT_HEAD=$(gh pr view "$PR" --json headRefOid -q .headRefOid) || exit 1
[ "$CURRENT_HEAD" = "$PUBLISHED_SHA" ] || {
  echo "PR head moved after publication — leave handled threads open"
  exit 1
}

# Review threads as paginated GraphQL response objects. --paginate + the
# pageInfo block are what carry it past 100: PR #65 closed 99 findings, so one
# more thread and the surplus is silently absent.
review_threads() {
  gh api graphql --paginate -F owner="$OWNER" -F name="$NAME" -F num="$PR" -f query='
    query($owner:String!, $name:String!, $num:Int!, $endCursor:String) {
      repository(owner:$owner, name:$name) {
        pullRequest(number:$num) {
          reviewThreads(first:100, after:$endCursor) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id
              isResolved
              comments(first:1) { nodes { databaseId path } }
            }
          }
        }
      }
    }'
}

THREADS_BEFORE=$(mktemp) || exit 1
THREAD_IDS=$(mktemp) || exit 1
THREADS_AFTER=$(mktemp) || exit 1
trap 'rm -f "$THREADS_BEFORE" "$THREAD_IDS" "$THREADS_AFTER"' EXIT

if ! review_threads > "$THREADS_BEFORE"; then
  echo "could not fetch review threads — leave handled threads open"
  exit 1
fi
if ! jq -s -e '
  length > 0 and all(.[];
    (.errors == null)
    and ((.data.repository.pullRequest.reviewThreads.nodes | type) == "array"))
' "$THREADS_BEFORE" >/dev/null; then
  echo "invalid review-thread response — leave handled threads open"
  exit 1
fi

jq -c '
  .data.repository.pullRequest.reviewThreads.nodes[]
  | select(.isResolved | not)
  | {id, comment_id: .comments.nodes[0].databaseId,
     path: .comments.nodes[0].path}' "$THREADS_BEFORE"

if [ "$(jq 'length' <<<"$HANDLED_IDS_JSON")" -gt 0 ]; then
  if ! jq -r --argjson handled "$HANDLED_IDS_JSON" '
    .data.repository.pullRequest.reviewThreads.nodes[]
    | select(.isResolved | not)
    | select(.comments.nodes[0].databaseId as $comment_id
             | $handled | index($comment_id))
    | .id' "$THREADS_BEFORE" > "$THREAD_IDS"; then
    echo "could not map handled comments to review threads"
    exit 1
  fi

  resolution_failed=0
  while IFS= read -r id; do
    [ -n "$id" ] || continue
    gh api graphql -f id="$id" -f query='
      mutation($id:ID!) {
        resolveReviewThread(input:{threadId:$id}) { thread { isResolved } }
      }' >/dev/null || resolution_failed=1
  done < "$THREAD_IDS"
  if [ "$resolution_failed" -ne 0 ]; then
    echo "one or more review-thread mutations failed"
    exit 1
  fi

  if ! review_threads > "$THREADS_AFTER" \
     || ! jq -s -e '
       length > 0 and all(.[];
         (.errors == null)
         and ((.data.repository.pullRequest.reviewThreads.nodes | type)
              == "array"))
     ' "$THREADS_AFTER" >/dev/null \
     || ! jq -s -e --argjson handled "$HANDLED_IDS_JSON" '
       [
         .[].data.repository.pullRequest.reviewThreads.nodes[]
         | select(.comments.nodes[0].databaseId as $comment_id
                  | $handled | index($comment_id))
       ] as $threads
       | (($threads | map(.comments.nodes[0].databaseId) | unique | length)
          == ($handled | unique | length))
         and ($threads | all(.isResolved))
     ' "$THREADS_AFTER" >/dev/null; then
    echo "handled review threads are not all resolved"
    exit 1
  fi
fi
```

The mutation consumes only bare GraphQL IDs whose root `databaseId` belongs to
the already-validated current batch. Materializing query results before mutation
prevents a fetch failure from masquerading as an empty set, and the second query
proves that every handled root both exists and is resolved.
