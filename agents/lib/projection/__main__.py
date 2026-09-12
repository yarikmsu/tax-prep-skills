#!/usr/bin/env python3
"""`sh agents/lib/run.sh -m agents.lib.projection` — /tax-project's CLI.

A package cannot be executed the way a module can, so the dispatch that sat at
the foot of the old single file lives here. What it runs is unchanged, and the
command the skill names did not move: `-m agents.lib.projection` reached the
module before and reaches this file now.
"""

import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agents.lib.projection import _main  # noqa: E402

sys.exit(_main(sys.argv[1:]))
