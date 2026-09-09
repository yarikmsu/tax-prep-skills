#!/usr/bin/env bash
# Which commit range the PII gate should scan, for the event that triggered CI.
#
# WHY THIS IS A SCRIPT AND NOT A LINE OF YAML. The workflow used to pass
# `${{ github.event.pull_request.base.sha }}...HEAD` unconditionally. That
# expression is EMPTY on every event except `pull_request`, so the moment CI
# gains a push or schedule trigger the gate is handed `...HEAD`. The scanner
# refuses that rather than reporting a clean pass — it fails closed, which is
# the one reason this was not a live hole — but "the gate blocks every push"
# and "the gate scans the right range" are different outcomes, and only the
# second is a working CI. Logic in a workflow expression cannot be run
# anywhere but on a runner; logic here has tests.
#
# CONTRACT, matching the gates in `agents/lib/`:
#   exit 0 — the range is on stdout, ready to pass to pre-commit-pii-scan.sh
#   exit 3 — nothing to scan: this event introduces no commits
#   exit 1 — the range CANNOT be determined. Never a fallback range: a wrong
#            one scans commits nobody wrote, or none at all, and both report a
#            clean pass. Fail closed and say which variable was missing.
#
# Inputs are environment variables rather than arguments so the workflow can
# hand them straight from the event payload:
#   EVENT_NAME        github.event_name
#   PR_BASE_SHA       github.event.pull_request.base.sha
#   PUSH_BEFORE_SHA   github.event.before
set -eu

_is_sha() {
    case "$1" in
        *[!0-9a-fA-F]*|"") return 1 ;;
        *) [ "${#1}" -eq 40 ] ;;
    esac
}

# `before` on the first push to a branch, and after some history rewrites, is
# forty zeros — "there was nothing here". It is a valid sha shape and names no
# commit, so a range built from it fails to resolve.
_is_null_sha() {
    case "$1" in
        0000000000000000000000000000000000000000) return 0 ;;
        *) return 1 ;;
    esac
}

# Whether `$1` names a commit this checkout actually has. A sha can be
# well-formed and unreachable: after a force-push, `github.event.before` names a
# commit the new history has orphaned, and `actions/checkout` fetches REFS, not
# unreachable objects.
_have_commit() {
    git cat-file -e "$1^{commit}" 2>/dev/null
}

_whole_history() {
    # A shallow checkout's `rev-list --max-parents=0` returns the GRAFTED
    # BOUNDARY, which is a well-formed sha naming a commit with no parents
    # HERE and parents in the real history. It passes every shape check, so
    # asking git whether the repository is shallow is the only way to tell —
    # a guard written as "does the root look like a sha" could never fire.
    if [ "$(git rev-parse --is-shallow-repository)" = "true" ]; then
        echo "this is a shallow checkout, so there is no history to scan — CI needs fetch-depth: 0" >&2
        exit 1
    fi
    # The FIRST root, oldest first, so a repository that has more than one (a
    # grafted import, a merged unrelated history) still yields a range that
    # reaches the beginning rather than one that silently starts partway.
    root=$(git rev-list --max-parents=0 HEAD | tail -1)
    if ! _is_sha "$root"; then
        echo "cannot find a root commit to scan from" >&2
        exit 1
    fi
    printf '%s...HEAD\n' "$root"
}

case "${EVENT_NAME:-}" in
    pull_request|pull_request_target)
        base=${PR_BASE_SHA:-}
        if ! _is_sha "$base"; then
            echo "EVENT_NAME=$EVENT_NAME but PR_BASE_SHA is '${base}' — refusing to guess a range" >&2
            exit 1
        fi
        printf '%s...HEAD\n' "$base"
        ;;
    push)
        before=${PUSH_BEFORE_SHA:-}
        # A branch's first push has no "before", so every commit on it is new
        # and the whole history IS the delta. Scanning it is slower and right;
        # scanning nothing would be fast and would report a clean pass.
        if _is_null_sha "$before"; then
            _whole_history
        elif _is_sha "$before" && _have_commit "$before"; then
            printf '%s...HEAD\n' "$before"
        elif _is_sha "$before"; then
            # A FORCE-PUSH. `before` is well-formed and orphaned, so a range
            # built from it cannot resolve — and the scanner's failure names
            # `fetch-depth: 0`, a setting that is already correct, which sends
            # the reader after the wrong thing. History was REWRITTEN, so what
            # is new is not knowable from `before`: scan all of it.
            echo "note: ${before} is not in this history (a force-push?) — scanning from the root" >&2
            _whole_history
        else
            echo "EVENT_NAME=push but PUSH_BEFORE_SHA is '${before}' — refusing to guess a range" >&2
            exit 1
        fi
        ;;
    schedule|workflow_dispatch)
        # Nothing was pushed. A scheduled run exists to catch what changed
        # UNDER the repository — a new advisory, an interpreter release — not
        # what changed in it, and re-scanning the whole history weekly would
        # report the same verdict every week until somebody stopped reading it.
        exit 3
        ;;
    "")
        echo "EVENT_NAME is unset — this script is for CI; the git hook scans the index" >&2
        exit 1
        ;;
    *)
        # A trigger nobody has thought about gets no range, not a default one.
        echo "no PII-scan range is defined for EVENT_NAME=${EVENT_NAME} — add one here" >&2
        exit 1
        ;;
esac
