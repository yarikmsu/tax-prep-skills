"""Federal forms, one file per family of them.

The counterpart of `state/`: a file here holds one body of federal law a reader
would look up under its own form number, and nothing else. What stays in
`tax_computations/__init__.py` is the spine every return walks — brackets, the
QDCG worksheet, AGI, the keystone scenario — plus the forms not yet pulled out.

The three rules are `state/`'s, and for the same reasons: shared primitives
come from `_shared.py` and NEVER from the parent package, which re-exports
these modules and would be a cycle; a sibling here may import another sibling
directly; and every public name joins the parent's re-export block in the same
change, because `tax_computations.<name>` is the address every caller uses and
some of those callers are skill markdown that no sweep can rewrite.
"""

import agents.lib  # noqa: F401 — the host-interpreter guard, as every sibling carries it
