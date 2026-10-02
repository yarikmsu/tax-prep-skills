#!/bin/sh
# Regenerate every lock — each requirements*.in into its requirements*.txt —
# for the interpreter floor `.python-version` names.
#
# The floor is READ, not typed. It was hand-typed into five recipes, and the
# version a lock is resolved for is a fact the lock cannot show about itself:
# compiled for the wrong one it still installs, with the markers of an
# interpreter nobody runs. The header uv writes into each `.txt` still echoes
# the version it was given, and the CI guard reads that.
#
# Extra arguments go to every `uv pip compile`, so a refresh is
#   sh scripts/recompile-locks.sh --upgrade-package urllib3
# Without one, uv keeps the pins it finds in each existing `.txt` and resolves
# only what the `.in` changed.
set -eu

cd "$(dirname "$0")/.."

floor=$(tr -d '[:space:]' < .python-version)
case "$floor" in
    3.[0-9]|3.[0-9][0-9]) ;;
    *) echo "recompile-locks: .python-version holds '$floor', not a 3.N release" >&2
       exit 2 ;;
esac

for source in requirements*.in; do
    # Only an unmatched glob, left as the literal pattern, is not a file.
    [ -f "$source" ] || { echo "recompile-locks: no requirements*.in in $(pwd)" >&2
                          exit 2; }
    uv pip compile "$source" --generate-hashes --universal \
        --python-version "$floor" -o "${source%.in}.txt" "$@"
done
