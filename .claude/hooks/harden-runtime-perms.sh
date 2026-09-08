#!/usr/bin/env bash
#
# Claude Code PostToolUse hook — matcher Write|Edit|NotebookEdit|Bash.
#
# AGENTS.md §1: chmod 600 every file containing PII or financial data IMMEDIATELY
# after writing it. That rule is currently carried by the skills' own prose, and
# prose is followed unevenly — a step that forgets it leaves a taxpayer's return
# world-readable with no signal anywhere.
#
# The git hooks cannot cover this. They fire at COMMIT time, and every file below
# is gitignored, so none of them ever reaches a commit for the PII scan to see.
# That gap is the entire reason a Claude-side hook exists alongside them.
#
# Bash is in the matcher on purpose: result.json and the generated PDF are
# produced by running a script, which a Write|Edit-only matcher never observes.
#
# POSIX-ONLY, deliberately, and SECURITY.md says so rather than implying
# otherwise. This re-asserts modes with `chmod`, which under a POSIX-compatible
# shell on Windows flips the read-only DOS attribute and leaves the NTFS DACL
# alone — so on that platform the guarantee rests on the documented writes,
# every one of which goes through `agents.lib.secure_fs` (pinned per skill and
# per call by tests/test_secure_fs.py). Routing this hook through that CLI
# instead would put a Python process on the critical path of every tool call,
# in a hook whose whole design is a millisecond fast path that never blocks.
# A backstop that says what it does not cover beats one that overstates it.
#
# NEVER BLOCKS. Exit 2 is the only code that feeds a hook's stderr back as an
# error the agent must handle, and this hook never uses it: a permissions hook
# that can derail a session gets switched off, and a switched-off hook protects
# nothing. It exits 1 when a chmod FAILED, which surfaces the message to the user
# and lets the tool call proceed — silence was the wrong way to get never-block,
# because a chmod that cannot run leaves a return exposed and said so nowhere.
set -u
umask 077

cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0

failed=0
warn() {
    printf 'harden-runtime-perms: %s\n' "$*" >&2
    failed=1
}

# The eight exact filenames here MIRROR the sensitive-filename case in
# scripts/pre-commit-pii-scan.sh, and `test_claude_hooks.py` parses that case and
# fails if this list stops covering it. The list had drifted on arrival —
# crypto_transactions.json (a full transaction history) and projection_inputs.json
# (YTD income and withholding) were missing, and being gitignored they are exactly
# the files the commit-time scan never sees.
#
# generate_tax_pdf.py belongs here despite being source: it is a per-workspace
# runtime artifact written on demand by tax-generate, and it carries the
# taxpayer's NAME in its section headers. It is also the one runtime file the
# commit-time PII scan structurally cannot vet, since that scan matches SSN and
# credential SHAPES and a name has neither.
#
# tax_law_<year>.json is deliberately NOT here: it holds published rates and
# brackets, which are public figures and not taxpayer data. Keeping the list
# matched to what it claims to protect is what makes the claim checkable.
for f in taxpayer_profile.json result.json answers.md parse_ledger.json \
         holdings.json projection_inputs.json crypto_transactions.json \
         validation_receipt.json eval_run.json \
         generate_tax_pdf.py FBAR_*.md [0-9][0-9][0-9][0-9]-review.md \
         tax_calendar_*.ics; do
    if [ -L "$f" ]; then
        warn "refusing to follow a symlink for a sensitive runtime file"
        continue
    fi
    [ -f "$f" ] || continue
    # A runtime filename can itself contain taxpayer identity data. Report the
    # failed class, never the basename that triggered it.
    chmod 600 "./$f" 2>/dev/null \
        || warn "could not chmod 600 a sensitive runtime file"
done

# The generated-artifact extensions are read by three call sites: this
# root-level glob loop, is_generated_artifact_name, and
# harden_workspace_artifacts' find predicate (the payload fast path needs its
# own copy of the predicate because it runs before any find). All three read
# this one array rather than repeating the list, so a new extension added
# here reaches every call site with nothing left to fall out of sync. `*~`
# has no leading dot and is handled as a literal alongside the array at each
# call site — a dot-only representation cannot express it.
GENERATED_ARTIFACT_EXTENSIONS=(pdf bak tmp swp swo)

# Bash's default globs miss dotfiles and are case-sensitive. Expand root PDFs
# plus the backup/swap shapes declared sensitive in .gitignore with both
# behaviors enabled. This covers unusual valid PDF names (`..pdf`, `...pdf`),
# mixed-case extensions, and partial copies such as `result.json.bak`.
# The `./` prefix prevents an option-like name from becoming a chmod flag.
shopt -s nullglob dotglob nocaseglob
root_artifact_globs=()
for ext in "${GENERATED_ARTIFACT_EXTENSIONS[@]}"; do
    root_artifact_globs+=("./*.$ext")
done
root_artifact_globs+=('./*~')
for f in ${root_artifact_globs[@]}; do
    if [ -L "$f" ]; then
        warn "refusing to follow a symlink for a sensitive runtime file"
        continue
    fi
    [ -f "$f" ] || continue
    chmod 600 "$f" 2>/dev/null \
        || warn "could not chmod 600 a sensitive runtime file"
done
shopt -u nocaseglob dotglob nullglob
unset root_artifact_globs

# Directories get 700 rather than merely removing group/other bits: a directory
# restored as 600 needs its owner execute bit put back before the hook can walk
# it. Mode 600 on a directory makes the tree unreadable to its own owner.
#
# INCREMENTAL, because this hook runs after every Write, Edit and Bash call —
# including read-only ones. The active runtime directories below are walked for
# entries newer than the stamp. archive/ is different: it accumulates every
# prior year's scanned PDFs, so even `find archive -newercm ...` recursively
# stats the entire history on every call. Once a stamp exists, stat the known
# archive DIRECTORIES from an owner-only index. Creating or atomically replacing
# a file changes its immediate parent directory; creating a new subtree changes
# a parent already in the index. Only that changed subtree is then walked. A
# direct Write/Edit into an older year is also covered from the hook payload.
#
# -newercm compares CTIME against the stamp, so it catches a mode change as well
# as a write. If find cannot run the test, the full walk fallback is the safe
# direction. Changed paths are materialized so a chmod failure is observed in
# this shell and prevents the stamp from advancing; `find -exec` does not
# portably propagate the invoked command's failure status.
#
# The index cannot be the whole story, because a Bash call is opaque: a helper
# script can write beneath archive/ without naming it, and a writer that
# preserves directory mtimes leaves nothing for the poll to notice. That case
# gets one traversal that asks find for the EXPOSURES — see
# harden_exposed_archive_entries — instead of the full walk plus full index
# rebuild it used to get, which is why the index above is worth having at all.
STAMP=".claude/.perms-stamp"
ARCHIVE_DIR_INDEX=".claude/.archive-dirs"
ARCHIVE_FILE_INDEX=".claude/.archive-files"
# The two manifests cannot be renamed in one filesystem operation, so something
# durable has to span the window in which one has been replaced and the other
# has not. That job was briefly given to the STAMP, by unlinking it before the
# first rename and letting its absence mean "rebuild both". It cost far more
# than it looked: ARCHIVE_INDEX_DIRTY is set by track_archive_refresh_root,
# which harden_exposed_archive_entries calls UNCONDITIONALLY, so the dominant
# path — every opaque Bash call, on a completely quiet archive — unlinked and
# recreated the stamp, opening a window in which a concurrent or resumed pass
# read no stamp and went full. Worse, the stamp is the incremental boundary for
# documents/, summaries/, reviews/ and the whole contributor tree, none of which
# was ever in doubt: one symbol carrying two independent meanings, which this
# project's own design rules name as a reliable bug source.
#
# A dedicated marker says exactly the thing being marked and nothing else. It is
# created before the first rename, removed only after the second lands, and its
# EXISTENCE forces the rebuild. The stamp goes back to the two states its
# contract has always claimed: advance on a verdict, or hold.
ARCHIVE_INDEX_DIRTY_MARKER=".claude/.archive-index-dirty"
STAMP_VERSION='2'
FORCE_FULL=0
ARCHIVE_INDEX_DIRTY=0
ARCHIVE_ROOT_CHANGED=0
ARCHIVE_FULLY_HARDENED=0
ARCHIVE_INDEX_MODES_SAFE=0

# The three index/stamp candidates below are created by mktemp INSIDE the
# repository, because each must land on the same filesystem as the file it
# atomically replaces. That makes them the one class of scratch file $TEMP_DIR's
# removal cannot cover, and .claude/.archive-files.XXXXXX is a NUL-separated
# list of every path under archive/ — taxpayer data by any reading. Track each
# one so the trap can remove it if this pass does not reach its `mv`.
INREPO_TEMP_FILES=()
track_inrepo_temp() {
    INREPO_TEMP_FILES[${#INREPO_TEMP_FILES[@]}]=$1
}

TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/harden-runtime-perms.XXXXXX" 2>/dev/null) \
    || TEMP_DIR=''
if [ -n "$TEMP_DIR" ]; then
    CHANGED_LIST="$TEMP_DIR/changed"
    TREE_LIST="$TEMP_DIR/tree"
    FILE_LIST="$TEMP_DIR/files"
    NOW_MARKER="$TEMP_DIR/now"
    ARCHIVE_BOUNDARY="$TEMP_DIR/archive-boundary"
    ARCHIVE_REFRESH_ROOTS="$TEMP_DIR/archive-refresh-roots"
else
    CHANGED_LIST=''
    TREE_LIST=''
    FILE_LIST=''
    NOW_MARKER=''
    ARCHIVE_BOUNDARY=''
    ARCHIVE_REFRESH_ROOTS=''
    warn "could not create private scratch space for the permissions pass"
fi

cleanup() {
    [ -n "$TEMP_DIR" ] && rm -rf "$TEMP_DIR" 2>/dev/null || true
    # An append-only list is safe here because `mv -f` CONSUMES the candidate
    # name: a refresh that completed has already renamed its file, so this rm
    # finds nothing and cannot undo an installed index or stamp. What it does
    # remove is the candidate of a pass that died between mktemp and mv — and
    # that is the routine case, not the exotic one, because the registered hook
    # timeout kills a slow pass mid-refresh. .gitignore names only the four
    # exact index and stamp files, so a surviving .claude/.archive-files.k9Xz2A
    # is untracked, is a list of every path under archive/, and `git add .`
    # stages it.
    if [ "${#INREPO_TEMP_FILES[@]}" -gt 0 ]; then
        rm -f "${INREPO_TEMP_FILES[@]}" 2>/dev/null || true
    fi
}
trap cleanup EXIT
# What closes the leak above is listing the in-repo candidates in cleanup at
# all — it previously removed only $TEMP_DIR, so a candidate outlived every
# abnormal end. This second trap is narrower than it looks, and the reason is
# worth recording because the obvious justification for it is wrong. Measured
# here (bash 3.2.57, an EXIT trap plus a mktemp candidate, killed mid-pass): an
# UNTRAPPED SIGTERM still runs the EXIT trap and still removes the candidate,
# both while bash waits on a foreground command and while it executes
# shell-level code. So this trap is not what rescues the file from a hook
# timeout. What it adds is promptness and a deterministic status — the removal
# stops waiting on whatever is currently running, and the pass ends on this
# hook's own 1, meaning "something was not done" and never "blocked", rather
# than on 143.
#
# It is a mitigation, not a guarantee, and both limits are measured too. A
# TRAPPED signal is deferred until the foreground command returns, so a kill
# during a long find is handled only when that find ends — the candidate was
# still on disk a full second after the kill — and SIGKILL runs no trap at all
# and does leave .claude/.archive-dirs.XXXXXX behind. The ignore rules for these
# candidate names are the other half of the fix precisely because this half
# cannot be made complete.
trap 'cleanup; exit 1' TERM INT HUP

# Compare the stamp to a file created by this pass rather than to the local
# clock rendered as text. If the clock moved backwards, every new file appears
# older than the old stamp and an incremental pass would skip it forever. A
# temporary marker gives Bash's portable -nt operator a filesystem-time
# reference. Backdate both scan boundaries immediately, while they still
# represent the pass start: strict `find -newer` predicates otherwise miss a
# write that shares a coarse filesystem timestamp tick with the boundary. GNU
# touch's relative-time form also uses the time at the END of a slow pass if it
# is delayed. The full pass rewrites the stamp back onto the corrected timeline.
# Do not create the marker when .claude/ is absent: this hook must not create
# infrastructure in a taxpayer workspace.
backdate_marker() {
    touch -A -000001 "$1" 2>/dev/null \
        || touch -d '1 second ago' "$1" 2>/dev/null
}

if [ -L .claude ]; then
    FORCE_FULL=1
    warn "refusing to trust a symlinked permissions metadata directory"
elif [ -n "$NOW_MARKER" ] && [ -d .claude ]; then
    if ! : > "$NOW_MARKER" 2>/dev/null \
       || ! : > "$ARCHIVE_BOUNDARY" 2>/dev/null \
       || ! : > "$ARCHIVE_REFRESH_ROOTS" 2>/dev/null; then
        warn "could not create the permissions pass boundary safely"
        NOW_MARKER=''
        FORCE_FULL=1
    elif ! backdate_marker "$NOW_MARKER" \
       || ! backdate_marker "$ARCHIVE_BOUNDARY"; then
        warn "could not prepare the permissions pass boundary safely"
        NOW_MARKER=''
        FORCE_FULL=1
    elif [ -L "$STAMP" ]; then
        FORCE_FULL=1
        warn "refusing to follow a symlink for the permissions stamp"
    elif [ -f "$STAMP" ]; then
        [ "$(sed -n '1p' "$STAMP" 2>/dev/null)" = "$STAMP_VERSION" ] \
            || FORCE_FULL=1
        [ "$STAMP" -nt "$NOW_MARKER" ] && FORCE_FULL=1
    else
        # No stamp yet, or a failed pass held the old one back and something
        # later removed it. Either way there is no boundary to be incremental
        # against.
        FORCE_FULL=1
    fi
fi

# Independent of the stamp, and deliberately so: this asks only whether the two
# archive manifests were left mid-publish, and answering yes costs a rebuild of
# those two files rather than the incremental boundary of every other runtime
# tree. Checked with -e OR -L so a marker replaced by a symlink still counts as
# present — the safe direction is always "rebuild".
if [ -e "$ARCHIVE_INDEX_DIRTY_MARKER" ] \
   || [ -L "$ARCHIVE_INDEX_DIRTY_MARKER" ]; then
    FORCE_FULL=1
fi

path_has_symlink_component() {
    local candidate=${1#./}
    local current=''
    local component

    case "$candidate" in
        ''|/*) return 0 ;;
    esac

    while [ -n "$candidate" ]; do
        component=${candidate%%/*}
        if [ "$candidate" = "$component" ]; then
            candidate=''
        else
            candidate=${candidate#*/}
        fi

        case "$component" in
            ''|.|..) return 0 ;;
        esac
        if [ -n "$current" ]; then
            current="$current/$component"
        else
            current=$component
        fi
        [ -L "$current" ] && return 0
    done

    return 1
}

harden_one() {
    local entry=$1
    local chmod_target=$entry
    if path_has_symlink_component "$entry"; then
        # chmod follows symlinks on supported hosts. Refuse the whole path,
        # including a regular file reached through a symlinked parent, so a
        # crafted runtime tree cannot change permissions outside this clone.
        # Never print the path: a runtime basename may itself contain identity.
        warn "refusing to follow a symlink in a sensitive runtime path"
        return
    fi
    # A root-relative name can start with `-` (an absolute payload path like
    # /repo/-return.pdf strips to the root-relative -return.pdf before ever
    # reaching this function), and GNU chmod's argv-permuting option parser
    # reads a leading `-` as an unrecognized flag rather than a filename —
    # BSD chmod does not permute argv and cannot reproduce it. `[ -d ]`/
    # `[ -f ]` below are unaffected: shell test operators take a fixed
    # argument in a fixed position, not a getopt-style scan, so they see
    # `$entry` fine either way. The `./` prefix is applied here, once, so
    # every caller gets the defense for free instead of having to
    # rediscover it — a caller that already prefixed `./` itself is
    # unaffected, since the prefixed string no longer starts with `-`.
    case "$chmod_target" in
        -*) chmod_target="./$chmod_target" ;;
    esac
    if [ -d "$entry" ]; then
        chmod 700 "$chmod_target" 2>/dev/null \
            || warn "could not harden a sensitive runtime directory"
    elif [ -f "$entry" ]; then
        chmod 600 "$chmod_target" 2>/dev/null \
            || warn "could not chmod 600 a sensitive runtime file"
    fi
}

restore_owner_traversal() {
    local tree=$1
    # find evaluates -exec for a directory before descending into it, so this
    # repairs a nested mode-600 directory in time to reach the files below it.
    # It does not follow symlinks; the component guard has already rejected the
    # root, and harden_one rejects every symlink in the materialized pass.
    find "$tree" -type d -exec chmod 700 {} \; >/dev/null 2>&1 \
        || warn "could not restore traversal of a sensitive runtime directory"
}

# ARCHIVE_REFRESH_ROOTS records which subtrees this pass invalidated in the
# archive indexes, and it has to stay an ANTICHAIN — no root may contain
# another. Both refresh functions append `find <root> -type d`/`-type f` for
# every root they hold, so two nesting roots put every path under the inner one
# into .claude/.archive-dirs and .claude/.archive-files TWICE; after that
# archive_changed_directories stats and re-hardens that subtree twice per call,
# and each later partial refresh copies both copies forward, so the index grows
# instead of converging.
#
# Which means the covering test has to run in BOTH directions. Testing only
# whether an existing root already contains the new tree — the half that was
# here — leaves `harden_full_tree archive/2024/documents` followed by
# `harden_full_tree archive/2024` recording both, which is the exact duplicate
# above. A new tree that contains existing roots must REPLACE them.
track_archive_refresh_root() {
    local tree=$1
    local indexed_root
    local covered=0
    local replaced=0
    local rewrite

    case "$tree" in
        archive|archive/*) ;;
        *) return ;;
    esac
    ARCHIVE_INDEX_DIRTY=1
    [ -n "$ARCHIVE_REFRESH_ROOTS" ] || return
    if [ "$tree" = archive ]; then
        # The whole archive contains every possible root, so the antichain
        # collapses to the one entry that sends both refreshes down their
        # full-rebuild path.
        : > "$ARCHIVE_REFRESH_ROOTS" \
            && printf 'archive\0' >> "$ARCHIVE_REFRESH_ROOTS" \
            || warn "could not track an archive permissions refresh"
        return
    fi

    # Build the surviving set beside the list rather than editing it in place:
    # a NUL-separated file cannot be rewritten while it is being read, and a
    # rewrite that fails must leave the original roots tracked rather than lose
    # them silently — losing one means its subtree keeps a stale index entry
    # that no later pass knows to replace.
    rewrite="${ARCHIVE_REFRESH_ROOTS}.rewrite"
    if ! : > "$rewrite"; then
        warn "could not track an archive permissions refresh"
        return
    fi
    while IFS= read -r -d '' indexed_root; do
        case "$tree" in
            "$indexed_root"|"$indexed_root"/*) covered=1 ;;
        esac
        case "$indexed_root" in
            "$tree"/*) replaced=1; continue ;;
        esac
        printf '%s\0' "$indexed_root" >> "$rewrite" || {
            warn "could not track an archive permissions refresh"
            return
        }
    done < "$ARCHIVE_REFRESH_ROOTS"

    if [ "$replaced" -eq 1 ]; then
        # `covered` and `replaced` are mutually exclusive while the antichain
        # holds; honouring both anyway is what makes one broken list converge
        # rather than reseed the nesting it is being rewritten to remove.
        { [ "$covered" -eq 1 ] || printf '%s\0' "$tree" >> "$rewrite"; } \
            && mv -f "$rewrite" "$ARCHIVE_REFRESH_ROOTS" 2>/dev/null \
            || warn "could not track an archive permissions refresh"
        return
    fi
    rm -f "$rewrite" 2>/dev/null || true
    [ "$covered" -eq 1 ] \
        || printf '%s\0' "$tree" >> "$ARCHIVE_REFRESH_ROOTS" \
        || warn "could not track an archive permissions refresh"
}

harden_full_tree() {
    local tree=$1
    if path_has_symlink_component "$tree"; then
        warn "refusing to follow a symlink in a sensitive runtime path"
        return
    fi
    if [ -z "$TREE_LIST" ] || [ -z "$FILE_LIST" ]; then
        warn "could not inspect a sensitive runtime directory safely"
        return
    fi
    track_archive_refresh_root "$tree"
    # Restore owner traversal before find. Doing this only when find has
    # already succeeded leaves a mode-600 directory permanently unwalkable.
    harden_one "$tree"
    restore_owner_traversal "$tree"
    if find "$tree" -print0 > "$TREE_LIST" 2>/dev/null; then
        : > "$FILE_LIST" || {
            warn "could not prepare the sensitive runtime file batch"
            return
        }
        while IFS= read -r -d '' entry; do
            if path_has_symlink_component "$entry"; then
                warn "refusing to follow a symlink in a sensitive runtime path"
            elif [ -d "$entry" ]; then
                # restore_owner_traversal already made descent possible; repeat
                # through harden_one so any chmod failure is observed here.
                harden_one "$entry"
            elif [ -f "$entry" ]; then
                printf '%s\0' "$entry" >> "$FILE_LIST" || {
                    warn "could not prepare the sensitive runtime file batch"
                    return
                }
            fi
        done < "$TREE_LIST"
        if [ -s "$FILE_LIST" ]; then
            xargs -0 chmod 600 < "$FILE_LIST" >/dev/null 2>&1 \
                || warn "could not chmod 600 sensitive runtime files"
        fi
        # Recorded only on the path that actually completed the walk, and only
        # for the whole tree: it is what lets the payload-driven net below skip
        # a second traversal of the same history in the same pass. A refusal or
        # a failed find leaves it clear, so an incomplete walk still gets the
        # net's second look.
        case "$tree" in
            archive) ARCHIVE_FULLY_HARDENED=1 ;;
        esac
        return
    fi
    warn "could not inspect a sensitive runtime directory safely"
}

archive_changed_directories() {
    local directory
    local saw_root=0

    [ -n "$CHANGED_LIST" ] && [ -f "$STAMP" ] \
        && [ -f "$ARCHIVE_DIR_INDEX" ] && [ ! -L "$ARCHIVE_DIR_INDEX" ] \
        || return 1
    : > "$CHANGED_LIST" || return 1
    while IFS= read -r -d '' directory; do
        case "$directory" in
            archive) saw_root=1 ;;
            archive/*) ;;
            *) return 1 ;;
        esac
        # A deleted directory needs no chmod. Its indexed parent changed when
        # it was removed and will refresh the index after that parent is walked.
        [ -e "$directory" ] || [ -L "$directory" ] || continue
        path_has_symlink_component "$directory" && return 1
        [ -d "$directory" ] || return 1
        # Bash 3.2's `-nt` compares whole seconds on macOS. `find -newermm`
        # preserves the filesystem's sub-second precision, which is necessary
        # when a detached writer finishes in the same second as the last hook.
        # A chmod changes directory ctime but not mtime. Checking the exact mode
        # of every already-indexed directory catches that exposure without
        # switching the timestamp predicate to ctime: this hook's own chmod
        # calls also change ctime and would otherwise retrigger the same subtree
        # forever.
        : > "$TREE_LIST" || return 1
        find "$directory" -maxdepth 0 \
            \( -newermm "$ARCHIVE_DIR_INDEX" -o ! -perm 700 \) \
            -print0 > "$TREE_LIST" 2>/dev/null || return 1
        [ -s "$TREE_LIST" ] || continue
        if [ "$directory" = archive ]; then
            ARCHIVE_ROOT_CHANGED=1
            # A new top-level year must not make every historical year pay for
            # the root metadata change. Materialize only new/changed immediate
            # entries; the new year itself will be walked below.
            find archive -mindepth 1 -maxdepth 1 \
                -newermm "$ARCHIVE_DIR_INDEX" -print0 \
                >> "$CHANGED_LIST" 2>/dev/null || return 1
        else
            printf '%s\0' "$directory" >> "$CHANGED_LIST" || return 1
        fi
    done < "$ARCHIVE_DIR_INDEX"
    [ "$saw_root" -eq 1 ]
}

refresh_archive_directory_index() {
    local candidate
    local directory
    local refresh_root
    local covered
    local full_refresh=0
    local refresh_failed=0

    [ -d archive ] && [ -d .claude ] && [ ! -L .claude ] \
        && [ ! -L "$ARCHIVE_DIR_INDEX" ] || return 1
    candidate=$(mktemp ".claude/.archive-dirs.XXXXXX" 2>/dev/null) || return 1
    track_inrepo_temp "$candidate"
    chmod 600 "$candidate" 2>/dev/null || {
        rm -f "$candidate" 2>/dev/null || true
        return 1
    }

    if [ ! -f "$ARCHIVE_DIR_INDEX" ]; then
        full_refresh=1
    elif [ -n "$ARCHIVE_REFRESH_ROOTS" ]; then
        while IFS= read -r -d '' refresh_root; do
            [ "$refresh_root" = archive ] && full_refresh=1 && break
        done < "$ARCHIVE_REFRESH_ROOTS"
    fi

    if [ "$full_refresh" -eq 1 ]; then
        find archive -type d -print0 > "$candidate" 2>/dev/null \
            || refresh_failed=1
    else
        : > "$candidate" || refresh_failed=1
        # Retain unchanged indexed directories without traversing their files.
        # Entries covered by a changed subtree are replaced from that subtree's
        # post-hardening walk, which adds new directories and drops deletions.
        while IFS= read -r -d '' directory; do
            case "$directory" in
                archive|archive/*) ;;
                *) refresh_failed=1; break ;;
            esac
            [ -d "$directory" ] && ! path_has_symlink_component "$directory" \
                || continue
            covered=0
            if [ -n "$ARCHIVE_REFRESH_ROOTS" ]; then
                while IFS= read -r -d '' refresh_root; do
                    case "$directory" in
                        "$refresh_root"|"$refresh_root"/*) covered=1; break ;;
                    esac
                done < "$ARCHIVE_REFRESH_ROOTS"
            fi
            [ "$covered" -eq 1 ] \
                || printf '%s\0' "$directory" >> "$candidate" \
                || { refresh_failed=1; break; }
        done < "$ARCHIVE_DIR_INDEX"
        if [ "$refresh_failed" -eq 0 ] && [ -n "$ARCHIVE_REFRESH_ROOTS" ]; then
            while IFS= read -r -d '' refresh_root; do
                case "$refresh_root" in
                    archive/*) ;;
                    *) refresh_failed=1; break ;;
                esac
                [ -d "$refresh_root" ] \
                    && ! path_has_symlink_component "$refresh_root" \
                    || continue
                find "$refresh_root" -type d -print0 \
                    >> "$candidate" 2>/dev/null \
                    || { refresh_failed=1; break; }
            done < "$ARCHIVE_REFRESH_ROOTS"
        fi
    fi
    if [ "$refresh_failed" -ne 0 ] \
       || ! touch -r "$ARCHIVE_BOUNDARY" "$candidate" 2>/dev/null \
       || ! mv -f "$candidate" "$ARCHIVE_DIR_INDEX" 2>/dev/null; then
        rm -f "$candidate" 2>/dev/null || true
        return 1
    fi
}

refresh_archive_file_index() {
    local candidate
    local entry
    local refresh_root
    local covered
    local full_refresh=0
    local refresh_failed=0

    [ -d archive ] && [ -d .claude ] && [ ! -L .claude ] \
        && [ ! -L "$ARCHIVE_FILE_INDEX" ] || return 1
    candidate=$(mktemp ".claude/.archive-files.XXXXXX" 2>/dev/null) || return 1
    track_inrepo_temp "$candidate"
    # This chmod is the one that matters, and it is placed here rather than
    # before the `mv` for a reason: it closes the window between creation and
    # the first write, so a host mktemp that ignored the umask cannot leave the
    # list of every archived path readable while it is being filled. Nothing
    # between here and the rename touches the mode, so re-asserting it later
    # would only re-verify what this call already established.
    chmod 600 "$candidate" 2>/dev/null || {
        rm -f "$candidate" 2>/dev/null || true
        return 1
    }

    if [ ! -f "$ARCHIVE_FILE_INDEX" ]; then
        full_refresh=1
    elif [ -n "$ARCHIVE_REFRESH_ROOTS" ]; then
        while IFS= read -r -d '' refresh_root; do
            [ "$refresh_root" = archive ] && full_refresh=1 && break
        done < "$ARCHIVE_REFRESH_ROOTS"
    fi

    if [ "$full_refresh" -eq 1 ]; then
        find archive -type f -print0 > "$candidate" 2>/dev/null \
            || refresh_failed=1
    else
        : > "$candidate" || refresh_failed=1
        while IFS= read -r -d '' entry; do
            case "$entry" in archive/*) ;; *) refresh_failed=1; break ;; esac
            [ -f "$entry" ] && ! path_has_symlink_component "$entry" \
                || continue
            covered=0
            if [ -n "$ARCHIVE_REFRESH_ROOTS" ]; then
                while IFS= read -r -d '' refresh_root; do
                    case "$entry" in
                        "$refresh_root"|"$refresh_root"/*) covered=1; break ;;
                    esac
                done < "$ARCHIVE_REFRESH_ROOTS"
            fi
            [ "$covered" -eq 1 ] \
                || printf '%s\0' "$entry" >> "$candidate" \
                || { refresh_failed=1; break; }
        done < "$ARCHIVE_FILE_INDEX"
        if [ "$refresh_failed" -eq 0 ] && [ -n "$ARCHIVE_REFRESH_ROOTS" ]; then
            while IFS= read -r -d '' refresh_root; do
                case "$refresh_root" in archive/*) ;; *) refresh_failed=1; break ;; esac
                [ -d "$refresh_root" ] \
                    && ! path_has_symlink_component "$refresh_root" \
                    || continue
                find "$refresh_root" -type f -print0 \
                    >> "$candidate" 2>/dev/null \
                    || { refresh_failed=1; break; }
            done < "$ARCHIVE_REFRESH_ROOTS"
        fi
    fi
    if [ "$refresh_failed" -ne 0 ] \
       || ! mv -f "$candidate" "$ARCHIVE_FILE_INDEX" 2>/dev/null; then
        rm -f "$candidate" 2>/dev/null || true
        return 1
    fi
}

# Fail CLOSED: if the marker cannot be created, a pass killed mid-publish would
# leave a mismatched manifest pair with nothing anywhere saying so, which is the
# one outcome this pair of helpers exists to prevent. Refusing to publish at all
# costs a rebuild; publishing unmarked costs silent, permanent under-checking.
mark_archive_index_dirty() {
    [ -d .claude ] && [ ! -L .claude ] || return 1
    [ ! -L "$ARCHIVE_INDEX_DIRTY_MARKER" ] || return 1
    : > "$ARCHIVE_INDEX_DIRTY_MARKER" 2>/dev/null || return 1
    chmod 600 "$ARCHIVE_INDEX_DIRTY_MARKER" 2>/dev/null
}

clear_archive_index_dirty() {
    rm -f "$ARCHIVE_INDEX_DIRTY_MARKER" 2>/dev/null
}

# `find -perm 600` is an exact-bits test on both the BSD find macOS ships and
# GNU find, and it is already this file's idiom for asking about a mode — see
# the exposure predicate in harden_exposed_archive_entries. There is no portable
# shell builtin for it, and stat's flags differ between the two hosts. A find
# that cannot run answers "not confirmed", which is the safe direction.
index_mode_is_owner_only() {
    local wrong
    wrong=$(find "$1" ! -perm 600 -print 2>/dev/null) || return 1
    [ -z "$wrong" ]
}

reassert_archive_index_modes() {
    local index
    local repaired_all=1
    local arrived_owner_only=1
    local present=0

    # Two separable questions, conflated here twice before. REPAIR is
    # unconditional: a manifest that exists lists taxpayer archive pathnames, so
    # it is chmod 600 whatever its sibling is doing. The previous existence
    # precheck returned before ANY chmod, so a missing .archive-files left an
    # 0644 .archive-dirs — a NUL-separated list of every archived directory —
    # exactly as it found it, while the comment above it claimed both chmods
    # were always attempted.
    for index in "$ARCHIVE_DIR_INDEX" "$ARCHIVE_FILE_INDEX"; do
        [ -f "$index" ] && [ ! -L "$index" ] || continue
        present=$((present + 1))
        index_mode_is_owner_only "$index" || arrived_owner_only=0
        chmod 600 "$index" 2>/dev/null || {
            warn "could not chmod 600 an archive permissions index"
            repaired_all=0
        }
    done

    # TRUST is the stricter question, and a repair cannot answer it. A manifest
    # that ARRIVED at anything but 600 is evidence it was produced or touched by
    # something that is not this hook — a `tar -p` restore, a foreign umask,
    # another user — so its CONTENTS have unknown provenance. The incremental
    # branch reads .archive-files as the COMPLETE list of archived files, so a
    # truncated one silently retires every omitted path from checking for ever:
    # the check that did not run is indistinguishable from the check that
    # passed. chmod-ing it now says nothing about who wrote it before, and the
    # comment that used to sit here asserted precisely that non-sequitur.
    #
    # Deliberately NOT a warn. The caller's fallback rebuilds both manifests
    # from the filesystem, so the pass really does end with everything correct
    # and exit 0 is honest; a warn would hold the stamp AND — since the refresh
    # block requires failed=0 — block the very rebuild that repairs this.
    [ "$present" -eq 2 ] && [ "$repaired_all" -eq 1 ] \
        && [ "$arrived_owner_only" -eq 1 ]
}

harden_indexed_archive_files() {
    # This re-tests what ARCHIVE_INDEX_MODES_SAFE — the only path that reaches
    # here — already established. That is deliberate: SAFE was decided before a
    # potentially long incremental walk, and the manifest can be replaced by a
    # symlink inside that window. The guard is a TOCTOU re-check, not a
    # redundant copy of the gate, and its full-walk fallback is the safe
    # direction. Deleting it because it "cannot fire" removes the only cover for
    # the race.
    [ -n "$CHANGED_LIST" ] && [ -f "$ARCHIVE_FILE_INDEX" ] \
        && [ ! -L "$ARCHIVE_FILE_INDEX" ] || {
        harden_full_tree archive
        return
    }
    # Directory mtimes cannot reveal chmod on an existing file. Check the
    # owner-only file manifest in one process, without recursively walking the
    # archive or printing identity-bearing paths. Python is a project runtime
    # requirement; its absence or any malformed/stale index falls back safely.
    if ! command -v python3 >/dev/null 2>&1 \
       || ! python3 - "$ARCHIVE_FILE_INDEX" "$CHANGED_LIST" <<'PY' 2>/dev/null
import os
import stat
import sys

try:
    entries = open(sys.argv[1], "rb").read().split(b"\0")
    with open(sys.argv[2], "wb") as exposed:
        for raw in entries:
            if not raw:
                continue
            parts = raw.split(b"/")
            if len(parts) < 2 or parts[0] != b"archive" \
                    or any(p in (b"", b".", b"..") for p in parts):
                raise ValueError("invalid archive file index")
            path = os.fsdecode(raw)
            try:
                info = os.lstat(path)
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode):
                raise ValueError("symlink in archive file index")
            if stat.S_ISREG(info.st_mode) \
                    and stat.S_IMODE(info.st_mode) != 0o600:
                exposed.write(raw + b"\0")
except Exception:
    raise SystemExit(1)
PY
    then
        harden_full_tree archive
        return
    fi
    while IFS= read -r -d '' entry; do
        harden_one "$entry"
    done < "$CHANGED_LIST"
}

# The safety net for a tool call whose effect on archive/ the payload cannot
# bound: an opaque `python3 -m pytest tests/ -q`, an archive path built from a
# shell variable, a malformed payload, a host without jq.
#
# It used to be `harden_full_tree archive`, and that made the incremental
# .archive-dirs / .archive-files design near-dead on the dominant path. Every
# command AGENTS.md §7 documents — the pytest run, `git status`, a `source
# .venv/bin/activate && …` chain — fails the read-only whitelist, so each one
# walked all of archive/ through the per-entry shell loop and an xargs chmod
# over every archived file. Measured on this host against a 3021-entry synthetic
# archive, that was 18.7s of hook per tool call, after `ls | wc -l`.
#
# What the net actually has to establish is one property: nothing under archive/
# is readable by anyone but its owner. It does NOT have to re-apply a mode a
# file already carries. So ask find for the EXPOSURES — a directory that is not
# exactly 700, a file that is not exactly 600, or any symlink, which is refused
# and never followed exactly as the full walk refuses it. One traversal, and
# zero chmod processes when the tree is already correct: 0.33s for the same
# pass, index refresh included. Nothing the full walk would have changed is
# missed, because "the walk would have changed it" and "its mode is not the
# target mode" are the same statement.
#
# Deliberately NOT `-newercm "$STAMP"`, the obvious mirror of
# harden_changed_tree. The traversal costs the same either way — find stats
# every entry to evaluate any predicate at all — while a stamp-filtered scan
# only sees an exposure whose ctime is newer than the last completed pass. A
# drift that this net misses once, because the next tool call was a Write or a
# read-only Bash and did not run it, falls behind the advancing stamp and turns
# invisible permanently. The mode predicate has no such window: it re-detects an
# exposure on every call until something actually repairs it.
#
# The scan replaces the full walk but NOT the index rebuild that walk performed
# as a SIDE EFFECT, which is why this function also marks the whole archive for
# refresh. `harden_full_tree archive` set ARCHIVE_INDEX_DIRTY, so the dominant
# path rebuilt .claude/.archive-dirs and .claude/.archive-files on every call.
# Drop that and a subtree which ARRIVES already correct — `rsync -a`, `tar -p`,
# a restore from backup, every one of which preserves directory mtimes — enters
# neither index: the mtime poll has nothing to notice and the file manifest does
# not list it, so a later `chmod 644` inside it is invisible to
# harden_indexed_archive_files and survives every Write, Edit and read-only Bash
# call. Measured, two workspaces alike but for which hook they run: after a new
# year is added with correct modes and archive/'s mtime restored, the pre-repair
# index holds both files and the scan-only index holds only the older one; after
# `chmod 644` on the newer file, one Write pass leaves it 600 under the
# pre-repair hook and 644 under the scan-only one.
#
# Restoring it costs two more `find archive` traversals, and measurement says
# that is the cheap way rather than the wasteful one. Folding all three outputs
# into a SINGLE traversal is possible — BSD find has no -fprint0 (`find:
# -fprint0: unknown primary or operator` here), but a python3 os.scandir walk
# can emit exposures, directories and files together — and it does not pay for
# itself: at 3021 entries the three finds are 0.041s against the one walk's
# 0.069s, and at 30061 entries 0.210s against 0.189s. A wash at every size
# measured, bought by putting python3 on the dominant path — the path whose
# fallback, when python3 is missing, is the 18.7s full walk.
harden_exposed_archive_entries() {
    local entry
    local saw_untraversable_directory=0

    # A completed full walk in this same pass already set every mode and already
    # marked the index, so the scan could only re-stat the history to find
    # nothing.
    [ "$ARCHIVE_FULLY_HARDENED" -eq 0 ] || return
    if [ -z "$CHANGED_LIST" ] \
       || ! find archive \
                \( -type d ! -perm 700 -o -type f ! -perm 600 -o -type l \) \
                -print0 > "$CHANGED_LIST" 2>/dev/null; then
        # A directory with no READ bit stops the scan here, as a failed exit
        # status — see the table in the loop below for the measurement, and for
        # the other, quieter half of this case. The fallback is what repairs
        # such a directory, because harden_full_tree's restore_owner_traversal
        # chmods each directory BEFORE find descends into it: measured, a
        # mode-000 archive/2024/sub holding a 644 file comes out of a single
        # pass at 700/600, silently and with exit 0.
        harden_full_tree archive
        return
    fi
    # Unconditional, and BEFORE the loop, because the case the index would
    # otherwise never learn about is precisely the one that produces an EMPTY
    # exposure list. A refusal or a failed find above returns without it, so an
    # incomplete scan never installs itself as a complete manifest.
    track_archive_refresh_root archive
    while IFS= read -r -d '' entry; do
        # A directory this user cannot both read and execute is one find could
        # not descend into, so the scan itself may be incomplete. How that
        # arrives depends on WHICH bit is missing, and only one of the two ways
        # is visible to the guard above. Measured on the BSD find macOS ships,
        # against archive/2025/reviews holding a 644 file, running exactly the
        # predicate above:
        #
        #   000, 300 (no read bit)   find exits 1, "Permission denied" on
        #                            stderr -> the guard above already took the
        #                            full-walk fallback
        #   600, 400 (read, no exec) find exits 0, stderr EMPTY, prints the
        #                            directory itself and silently omits every
        #                            entry beneath it
        #
        # The second row is this branch's entire reason to exist: the scan
        # reports a clean archive while a 644 file sits one level down, and no
        # exit status anywhere says otherwise. An earlier revision described
        # BOTH rows as stderr-only, which is wrong for the first row and made
        # the guard above read as redundant. No GNU find is installed on this
        # host, so the Linux side of the table is unmeasured; the code is
        # correct either way, since a find that instead fails on row two lands
        # in the fallback and a find that behaves like BSD's lands here.
        #
        # Test the effective permission with the shell builtins rather than
        # treating every non-700 directory as suspect: a 755 year from an
        # `mkdir -p` under the default umask needs its chmod, not a second full
        # traversal of the archive.
        if [ ! -L "$entry" ] && [ -d "$entry" ] \
           && { [ ! -r "$entry" ] || [ ! -x "$entry" ]; }; then
            saw_untraversable_directory=1
        fi
        harden_one "$entry"
    done < "$CHANGED_LIST"
    if [ "$saw_untraversable_directory" -eq 1 ]; then
        # harden_one has since restored 700 on those directories, so this walk
        # reaches what the scan could not. It also runs restore_owner_traversal,
        # which is what repairs a mode-600 directory nested deeper still.
        harden_full_tree archive
    fi
}

harden_changed_tree() {
    local tree=$1
    if path_has_symlink_component "$tree"; then
        warn "refusing to follow a symlink in a sensitive runtime path"
        return
    fi
    harden_one "$tree"
    restore_owner_traversal "$tree"
    if [ -n "$CHANGED_LIST" ] && [ "$FORCE_FULL" -eq 0 ] && [ -f "$STAMP" ] \
       && find "$tree" -newercm "$STAMP" -print0 > "$CHANGED_LIST" 2>/dev/null; then
        while IFS= read -r -d '' entry; do
            harden_one "$entry"
        done < "$CHANGED_LIST"
        return
    fi
    harden_full_tree "$tree"
}

# LC_ALL=C makes every byte 0-255 valid input to tr, avoiding "Illegal byte
# sequence" under a locale (e.g. the UTF-8 one this host defaults to) where
# some byte in a scanner/OCR-produced filename is not valid text —
# scripts/pre-commit-pii-scan.sh pins the same fix for the same reason. Case
# is folded ASCII-only, which matches every name this file ever compares
# against: the runtime/pruned tree names and the generated-artifact
# extensions are all seven-bit. A failed fork returns non-zero; every caller
# treats that as "no match", the safe direction for a check that only widens
# what gets hardened, never narrows it.
lower_ascii() {
    LC_ALL=C tr '[:upper:]' '[:lower:]'
}

# Both lists are stated ONCE, here, rather than separately inside the find
# predicate below and the payload fast path's own classification: that
# duplication is what previously let the fast path's case-alias handling for
# .git/.venv exist without covering the other eight trees the same way, and
# left the eventual fix un-checked against the find predicate it has to
# agree with. RUNTIME_ARTIFACT_TREES are hardened directly by the fast path;
# PRUNED_HOME_TREES are excluded from it because harden_workspace_artifacts'
# find walk prunes them instead.
RUNTIME_ARTIFACT_TREES=(documents summaries reviews scenarios projections
                         withholding archive .planning)
PRUNED_HOME_TREES=(.git .venv)

# Classifies a Write/Edit/NotebookEdit payload's top-level path segment
# against both lists above, writing the result to EDITED_TOP_TREE_KIND
# ('runtime', 'pruned', or '' for neither) rather than returning it, so the
# `-ef` filesystem check below runs at most once per hook call. An exact
# spelling match needs no filesystem probe — the dominant case, an exact top-
# level spelling, is the only one every skill ever writes, and it pays no
# subshell or tr fork at all. Only when NO candidate matches exactly does
# this fold case, once rather than once per candidate, and confirm a folded
# match with `-ef` (same device and inode; a bash test builtin, not `stat`,
# whose flags this project already avoids relying on because they differ
# between BSD and GNU — see index_mode_is_owner_only) before trusting it: on
# a case-sensitive host (CI's Linux) a folded-case match can be an ordinary,
# UNRELATED directory — e.g. a distinct `.VENV` that is not an alias of
# `.venv` at all — and treating it as the tree it merely resembles would
# exclude it from the fast path and strand a file inside it at whatever mode
# it arrived with if the later workspace scan times out.
classify_edited_top_tree() {
    local segment=$1
    local segment_lower
    local tree

    EDITED_TOP_TREE_KIND=''
    [ -n "$segment" ] || return

    for tree in "${RUNTIME_ARTIFACT_TREES[@]}" "${PRUNED_HOME_TREES[@]}"; do
        if [ "$segment" = "$tree" ]; then
            case "$tree" in
                .git|.venv) EDITED_TOP_TREE_KIND='pruned' ;;
                *) EDITED_TOP_TREE_KIND='runtime' ;;
            esac
            return
        fi
    done

    segment_lower=$(printf '%s' "$segment" | lower_ascii) || return
    for tree in "${RUNTIME_ARTIFACT_TREES[@]}" "${PRUNED_HOME_TREES[@]}"; do
        [ "$segment_lower" = "$tree" ] || continue
        [ -e "$tree" ] && [ "$segment" -ef "$tree" ] || continue
        case "$tree" in
            .git|.venv) EDITED_TOP_TREE_KIND='pruned' ;;
            *) EDITED_TOP_TREE_KIND='runtime' ;;
        esac
        return
    done
}

# The generated-artifact extension set backs three call sites: the root-level
# glob loop above, this check, and harden_workspace_artifacts' find predicate
# below (the payload fast path needs its own copy of the predicate because it
# runs before any find). All three read GENERATED_ARTIFACT_EXTENSIONS rather
# than repeating the list, so a new extension added once reaches every call
# site with nothing left to fall out of sync.
is_generated_artifact_name() {
    local lowered
    local ext
    # `case` is case-sensitive and bash 3.2 has no ${var,,}, so lower the
    # basename to match find's -iname. `*~` carries no case either way, and
    # has no leading dot, so it is checked as a literal rather than folded
    # into the extension loop below.
    lowered=$(printf '%s' "${1##*/}" | lower_ascii) || return 1
    case "$lowered" in
        *~) return 0 ;;
    esac
    for ext in "${GENERATED_ARTIFACT_EXTENSIONS[@]}"; do
        case "$lowered" in
            *".$ext") return 0 ;;
        esac
    done
    return 1
}

harden_workspace_artifacts() {
    # Generated PDFs and their editor/generator backup copies may be written to
    # an arbitrary workspace subdirectory, not only the root or a named runtime
    # tree. Walk the contributor tree but prune the large/private trees handled
    # elsewhere (and .git/.venv). Find's default -P behavior does not follow
    # symlinked directories, while harden_one rejects a symlink component on
    # every materialized artifact path.
    #
    # The predicate is built ONCE, from PRUNED_HOME_TREES, RUNTIME_ARTIFACT_TREES
    # and GENERATED_ARTIFACT_EXTENSIONS — the same arrays the payload fast path
    # reads. Stating the prune and name lists a second time here, independent of
    # those arrays, was the previous design, and it was two more chances for the
    # two walks to start covering different files.
    local prune_predicate=()
    local name_predicate=()
    local tree
    local ext

    for tree in "${PRUNED_HOME_TREES[@]}" "${RUNTIME_ARTIFACT_TREES[@]}"; do
        [ ${#prune_predicate[@]} -eq 0 ] || prune_predicate+=(-o)
        prune_predicate+=(-path "./$tree")
    done
    for ext in "${GENERATED_ARTIFACT_EXTENSIONS[@]}"; do
        [ ${#name_predicate[@]} -eq 0 ] || name_predicate+=(-o)
        name_predicate+=(-iname "*.$ext")
    done
    name_predicate+=(-o -name '*~')

    local walk=(
        . \( "${prune_predicate[@]}" \) -prune -o
        -type f \( "${name_predicate[@]}" \)
    )
    if [ -z "$CHANGED_LIST" ]; then
        warn "could not inspect generated workspace artifacts safely"
        return
    fi
    if [ "$FORCE_FULL" -eq 0 ] && [ -f "$STAMP" ] \
       && find "${walk[@]}" -newercm "$STAMP" -print0 \
            > "$CHANGED_LIST" 2>/dev/null; then
        :
    elif ! find "${walk[@]}" -print0 > "$CHANGED_LIST" 2>/dev/null; then
        warn "could not inspect generated workspace artifacts safely"
        return
    fi
    while IFS= read -r -d '' entry; do
        harden_one "$entry"
    done < "$CHANGED_LIST"
}

# Read and parse the completed tool call BEFORE any recursive tree scan. A full
# pass can exceed Claude's hook timeout, and harden_full_tree batches file chmods
# until traversal completes. The exact Write/Edit/NotebookEdit target therefore
# gets its owner-only mode first; even a later timeout cannot strand the file the
# just-completed tool call named at 0644. Never print the target: its basename may
# itself contain taxpayer identity data.
payload=$(cat) || payload=''
tool_name=''
edited=''
edited_archive_fallback=0
if [ -n "$payload" ] && command -v jq >/dev/null 2>&1; then
    tool_name=$(printf '%s' "$payload" \
        | jq -r '.tool_name // empty' 2>/dev/null) || tool_name=''
    if [ "$tool_name" = Write ] || [ "$tool_name" = Edit ] \
       || [ "$tool_name" = NotebookEdit ]; then
        edited=$(printf '%s' "$payload" \
                | jq -r '.tool_input.file_path // .tool_input.notebook_path // empty' \
              2>/dev/null) || edited=''
        edited_in_workspace=1
        # A tool may report a physically-resolved file_path while $PWD holds the
        # logical one — the cd above is logical, so CLAUDE_PROJECT_DIR reached
        # through a symlinked parent keeps that spelling. Matching only $PWD
        # classified such a target as EXTERNAL, which discards it entirely and
        # makes this whole fast path inert on that host. The relative remainder
        # is valid from cwd under either spelling, since both name one directory.
        # An empty prefix here would be a disaster ("/*" matches every absolute
        # path and strips nothing), so a failed `pwd -P` falls back to $PWD and
        # the arm becomes a harmless duplicate.
        physical_pwd=$(pwd -P 2>/dev/null) || physical_pwd=''
        [ -n "$physical_pwd" ] || physical_pwd=$PWD
        case "$edited" in
            "$PWD"/*) edited=${edited#"$PWD"/} ;;
            "$physical_pwd"/*) edited=${edited#"$physical_pwd"/} ;;
            /*) edited_in_workspace=0; edited='' ;;
        esac
        case "/$edited/" in
            */../*|*/./*)
                case "$edited" in
                    *archive/*)
                        [ "$edited_in_workspace" -eq 1 ] \
                            && edited_archive_fallback=1
                        ;;
                esac
                edited=''
                ;;
        esac
        harden_payload_target=0
        edited_top=${edited%%/*}
        classify_edited_top_tree "$edited_top"
        case "$EDITED_TOP_TREE_KIND" in
            runtime)
                harden_payload_target=1
                ;;
            pruned)
                # harden_workspace_artifacts prunes this tree (or its alias)
                # by the same check; nothing to harden here.
                ;;
            *)
                # A generator may place a return in any subdirectory,
                # not only a named runtime tree. harden_workspace_artifacts
                # does find those, but it still walks the whole
                # contributor tree to do it, and naming the exact file
                # skips that walk entirely. Root-level artifacts are
                # hardened at pass start; this covers the nested ones,
                # for the tools that report a path at all.
                is_generated_artifact_name "$edited" \
                    && harden_payload_target=1
                ;;
        esac
        if [ "$harden_payload_target" -eq 1 ] \
           && { [ -e "$edited" ] || [ -L "$edited" ]; }; then
            harden_one "$edited"
        fi
    fi
fi

# Manifest filenames disclose the archived documents they list. Re-harden them
# immediately after the exact tool target and before any potentially slow tree
# walk, even when a missing/invalid stamp will make this pass rebuild them.
# Failure sets failed through warn, preserving the old stamp and rejecting every
# path that would read the indexes during this call.
if reassert_archive_index_modes; then
    ARCHIVE_INDEX_MODES_SAFE=1
fi

# AFTER the runtime-tree walks, not before. A prior version of this pass ran
# harden_workspace_artifacts first on the theory that it was "bounded by
# construction" for having pruned every large tree — false: it prunes only
# the NAMED trees below, and anything else a workspace happens to contain (a
# nested checkout, a build output, an unrelated data dump) is walked in full.
# That is not a bound, it is an unenumerated set. `documents/` is bounded by
# something real — what the taxpayer actually scanned in — and AGENTS.md
# singles it out as the directory that "needs its own rule" precisely because
# it holds the raw source PII. The two walks are disjoint and their order is
# free on correctness grounds alone, so it is decided by which class of file
# a timeout is allowed to strand: an unindexed generated PDF outside every
# named tree, or an indexed taxpayer document inside one. The named trees go
# first. The residual gap this accepts — an opaque-Bash-written PDF outside
# every named tree AND outside the workspace root, colliding with a slow
# named-tree walk — needs three things to compound, and the documented
# generation flow (tax-generate/SKILL.md) writes to the workspace root,
# which harden_one covers at pass start regardless of this walk's order.
for d in documents summaries reviews scenarios projections withholding .planning; do
    [ -e "$d" ] || [ -L "$d" ] || continue
    harden_changed_tree "$d"
done

harden_workspace_artifacts

if [ -e archive ] || [ -L archive ]; then
    if path_has_symlink_component archive; then
        warn "refusing to follow a symlink in a sensitive runtime path"
    elif [ "$FORCE_FULL" -eq 0 ] && [ "$ARCHIVE_INDEX_MODES_SAFE" -eq 1 ] \
       && archive_changed_directories; then
        harden_one archive
        while IFS= read -r -d '' changed_entry; do
            if [ -d "$changed_entry" ]; then
                harden_full_tree "$changed_entry"
            else
                harden_one "$changed_entry"
            fi
        done < "$CHANGED_LIST"
        if [ "$ARCHIVE_ROOT_CHANGED" -ne 0 ]; then
            track_archive_refresh_root archive
        fi
        # Belongs to the INCREMENTAL branch only. It exists to find the one
        # thing a directory-mtime poll cannot see — a chmod on an existing file
        # — and it pays for that with a python3 process and an lstat of the
        # whole file manifest. Run unconditionally, as it was, it also ran
        # straight after the full walk in the else branch below, searching for
        # modes that walk had just finished guaranteeing were absent.
        harden_indexed_archive_files
    else
        harden_full_tree archive
    fi
fi

# Claude supplies the completed tool call as JSON on stdin. Write/Edit identify
# their exact file, letting an edit inside an existing archived year be hardened
# without traversing sibling years. Bash supplies its command: concrete
# `archive/YYYY` references re-harden only those years, while a dynamic or
# module-driven archive command falls back to the exposure scan. A Bash command
# that does not name archive is still unsafe to ignore when it can run an
# arbitrary script or interpreter: that script can write beneath archive/
# without exposing its destination in the payload. Only a deliberately narrow
# set of simple read-only commands avoids the scan entirely. Never print a
# target or command: either can contain taxpayer identity data.
bash_command_is_obviously_read_only() {
    local command_text=$1
    local command_kind
    local command_name
    local normalized
    local resolved

    # Shell composition/substitution makes even a read-only-looking first
    # command opaque. Treat false negatives as security failures and false
    # positives as a bounded performance cost.
    case "$command_text" in
        *$'\n'*|*';'*|*'&'*|*'|'*|*'>'*|*'<'*|*'`'*|*'$'*)
            return 1
            ;;
    esac
    # These options let otherwise read-oriented tools invoke a program or
    # write output. Keep the whitelist about effects, not command names.
    case " $command_text " in
        *' --output '*|*' --output='*|*' --pre '*|*' --pre='*|*' -O '*)
            return 1
            ;;
    esac

    normalized=${command_text#"${command_text%%[![:space:]]*}"}
    normalized=${normalized%"${normalized##*[![:space:]]}"}
    case "$normalized" in
        pwd|pwd\ -*|ls|ls\ *|stat|stat\ *|wc|wc\ *|head|head\ *|\
        tail|tail\ *|grep|grep\ *) ;;
        *) return 1 ;;
    esac
    command_name=${normalized%%[[:space:]]*}
    command_kind=$(type -t -- "$command_name" 2>/dev/null) || return 1
    if [ "$command_name" = pwd ]; then
        [ "$command_kind" = builtin ]
        return
    fi
    # A PATH shim or exported function can turn a safe utility name into an
    # arbitrary archive writer. Only the host's standard read-only binary is
    # eligible for the optimization.
    [ "$command_kind" = file ] || return 1
    resolved=$(type -P -- "$command_name" 2>/dev/null) || return 1
    case "$resolved" in
        "/bin/$command_name"|"/usr/bin/$command_name") return 0 ;;
    esac
    return 1
}

bash_archive_command_is_bounded() {
    local command_text=$1
    local normalized
    local command_name
    local destination
    local prefix

    # Multiple commands or substitutions can run an arbitrary helper whose
    # archive effects are unrelated to the literal paths visible here.
    case "$command_text" in
        *$'\n'*|*';'*|*'&'*|*'|'*|*'`'*|*'$'*|*'<('*|*'>('*) return 1 ;;
    esac
    normalized=${command_text#"${command_text%%[![:space:]]*}"}
    command_name=${normalized%%[[:space:]]*}
    # Keep the year-scoped optimization only for a direct shell redirection by
    # a non-helper-capable builtin. General file tools can write through an
    # alias/symlink not visible in the literal archive tokens, and Git commands
    # can invoke configured helpers. Those require the full archive pass.
    case "$command_name" in
        printf|echo) ;;
        *) return 1 ;;
    esac
    [ "$(type -t -- "$command_name" 2>/dev/null)" = builtin ] || return 1
    case "$command_text" in
        *'>'*) ;;
        *) return 1 ;;
    esac
    prefix=${command_text%%>*}
    destination=${command_text#*>}
    destination=${destination#"${destination%%[![:space:]]*}"}
    destination=${destination%"${destination##*[![:space:]]}"}
    # Exactly one redirection to an unquoted, traversal-free literal path. A
    # quoted/aliased/variable destination is safe only under a full walk.
    case "$prefix" in *'>'*|*'<'*) return 1 ;; esac
    case "$destination" in
        *[[:space:]]*|*[!-A-Za-z0-9._/]*|*'>'*|*'<'*|*/../*|*/./*) return 1 ;;
        archive/[0-9][0-9][0-9][0-9]/*) ;;
        *) return 1 ;;
    esac
    path_has_symlink_component "$destination" && return 1
    return 0
}

# The payload above was read WHOLE, the way skill-invariants.sh reads it.
# `IFS= read -r`
# stops at the first newline, so a pretty-printed hook JSON — or any payload
# carrying a real newline — arrived here as `{`. jq then returned an empty
# tool_name, the Write/Edit branch below never ran, and with it the ONLY path
# that hardens the exact archive/YYYY/... file the payload names, so every call
# degraded to the malformed-payload fallback. The hook kept exiting 0 and said
# nothing, which is what made losing that precision invisible.
#
# An absent payload still means silence: a hook that cannot identify its target
# must not guess, and the incremental archive pass above has already run.
if [ -n "$payload" ]; then
    if command -v jq >/dev/null 2>&1; then
        if [ "$tool_name" = Write ] || [ "$tool_name" = Edit ] \
           || [ "$tool_name" = NotebookEdit ]; then
            [ "$edited_archive_fallback" -eq 1 ] && [ -d archive ] \
                && harden_exposed_archive_entries
        elif [ "$tool_name" = Bash ] && [ -d archive ]; then
            command_text=$(printf '%s' "$payload" \
                | jq -r '.tool_input.command // empty' 2>/dev/null) \
                || command_text=''
            bash_command_read_only=0
            bash_command_is_obviously_read_only "$command_text" \
                && bash_command_read_only=1
            case "$command_text" in
                *archive*)
                    if [ "$bash_command_read_only" -eq 1 ]; then
                        : # A read cannot create an exposed archive artifact.
                    elif ! bash_archive_command_is_bounded "$command_text"; then
                        harden_exposed_archive_entries
                    else
                        archive_targets=$(printf '%s' "$command_text" \
                            | grep -Eo 'archive/[0-9]{4}' 2>/dev/null) \
                            || archive_targets=''
                        nonliteral_archive_text=$(printf '%s' "$command_text" \
                            | sed -E 's#archive/[0-9]{4}(/|$)#/#g' 2>/dev/null) \
                            || nonliteral_archive_text=$command_text
                        case "$nonliteral_archive_text" in
                            *archive*) archive_targets='' ;;
                        esac
                        if [ -n "$archive_targets" ]; then
                            while IFS= read -r archive_target; do
                                case "$archive_target" in
                                    archive/[0-9][0-9][0-9][0-9])
                                        [ -d "$archive_target" ] \
                                            && harden_full_tree "$archive_target"
                                        ;;
                                esac
                            done <<< "$archive_targets"
                        else
                            # A path built from a shell variable/glob, or a
                            # command mixing one with a literal year, cannot be
                            # bounded to the regex matches.
                            harden_exposed_archive_entries
                        fi
                    fi
                    ;;
                *)
                    # `bash helper.sh`, Python modules and other opaque calls
                    # can mutate an existing nested archive file without
                    # changing its year directory's timestamp. The command
                    # payload cannot bound those effects, so scan all of archive
                    # for exposures after the call. This is the dominant branch
                    # — nearly every command a session runs lands here — which
                    # is why it scans for the mode instead of re-applying it.
                    [ "$bash_command_read_only" -eq 1 ] \
                        || harden_exposed_archive_entries
                    ;;
            esac
        elif [ -z "$tool_name" ] && [ -d archive ]; then
            # A malformed payload must fail in the safe direction: the unknown
            # tool may have been Bash and may have written beneath archive/.
            harden_exposed_archive_entries
        fi
    elif [ -d archive ]; then
        # Without jq the tool cannot be identified. The exposure scan needs no
        # payload to be complete — it asks the filesystem, not the tool — so
        # the security contract survives losing the bounded year optimization.
        harden_exposed_archive_entries
    fi
fi

if [ "$failed" -eq 0 ] && [ "$ARCHIVE_INDEX_DIRTY" -eq 1 ] \
   && [ -d .claude ] && [ ! -L .claude ]; then
    # Mark BEFORE the first rename, clear only after the second one lands.
    # Anything that ends this pass in between — the configured hook timeout, a
    # SIGKILL, a failed mv — leaves the marker behind, and the next call rebuilds
    # both manifests from the filesystem rather than trusting a mismatched pair.
    if ! mark_archive_index_dirty \
       || ! refresh_archive_directory_index \
       || ! refresh_archive_file_index; then
        warn "could not refresh the archive permissions index safely"
    elif ! clear_archive_index_dirty; then
        # Both manifests are correct and only the marker is stuck. That costs a
        # full rebuild on every later call, which is the safe direction, but it
        # is a real failure and silence about it is how it becomes permanent.
        warn "could not clear the archive permissions index marker"
    fi
fi

# Only where .claude/ already exists — this hook must not CREATE anything in a
# workspace. Without a stamp every pass is a full walk, which is correct and
# merely slower, so the missing-directory case degrades in the safe direction.
# A failed chmod/scan pass must leave the old stamp in place: otherwise an
# unchanged file whose chmod failed is older than a new stamp and every later
# incremental pass skips it permanently. ADVANCE OR HOLD, never a third state —
# there is no case in which this hook unlinks the stamp. Archive-index
# publication once did exactly that, using the stamp's absence to mean "rebuild
# both manifests"; it now marks that window with its own file
# ($ARCHIVE_INDEX_DIRTY_MARKER), because absence-as-dirty-marker also discarded
# the incremental boundary for every unrelated runtime tree and left a window in
# which a kill destroyed a consistent stamp.
write_stamp() {
    local candidate
    candidate=$(mktemp ".claude/.perms-stamp.XXXXXX" 2>/dev/null) || return 1
    track_inrepo_temp "$candidate"
    printf '%s\n' "$STAMP_VERSION" > "$candidate" 2>/dev/null || {
        rm -f "$candidate" 2>/dev/null || true
        return 1
    }

    # The next boundary is this pass's START, not its end. A detached producer
    # can write into a tree after that tree was scanned while this hook is still
    # working elsewhere; an end-time stamp would make that exposed file look
    # old forever. The marker was already given a one-second overlap at pass
    # start so GNU touch cannot accidentally calculate it relative to pass end.
    [ -n "$NOW_MARKER" ] && [ -f "$NOW_MARKER" ] \
        && touch -r "$NOW_MARKER" "$candidate" 2>/dev/null || {
        rm -f "$candidate" 2>/dev/null || true
        return 1
    }
    mv -f "$candidate" "$STAMP" 2>/dev/null || {
        rm -f "$candidate" 2>/dev/null || true
        return 1
    }
}

# The symlink arm is a TOCTOU re-check of the pass-start guard, which already
# warns and sets failed=1 for a stamp that was a symlink then — and failed=0 is
# required here. It can therefore only fire if the stamp BECAME a symlink during
# this pass, which is the whole point: mv -f onto a symlink writes through it,
# outside the workspace. Do not delete this as already-guaranteed.
if [ "$failed" -eq 0 ] && [ -d .claude ] && [ ! -L .claude ] \
   && [ ! -L "$STAMP" ]; then
    write_stamp || warn "could not advance the permissions stamp safely"
fi

exit "$failed"
