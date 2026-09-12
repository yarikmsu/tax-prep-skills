"""Per-state computation modules, one file per state.

A state's file holds that state's FORM math — the lines of its return — so
`tax_computations/__init__.py` carries only federal math plus the shared
helpers. It is not everything about the state: the PLANNING-side blocks live
beside their federal twins in the modules that own those questions
(`projection.compute_ca_safe_harbor` and its NY counterpart, `withholding.py`,
`tax_calendar.py`, `ptet.py`), because what they share is the question, not the
jurisdiction. `docs/contributing/state-module-playbook.md` draws the line.

The re-exports that keep `tax_computations.<name>` working are in the PARENT's
`__init__.py`, not here — the callers were addressing that name before the
split and some of them are skill markdown, which no sweep can rewrite. Whether
they belong here instead was left open until a second state existed to reason
from, and California settled it: they stay where they are. Pulling them back
out of an aggregating `state/__init__.py` needs either a star import, which
under `select = ["F"]` reports every unresolved name in a 5,100-line file, or a
second explicit list, which is worse than one. The reasoning is recorded
against W2 in `docs/roadmap/2026-09-05-staff-agent-review.md`.

WHAT A NEW STATE LOOKS LIKE, from the two that are here: one file named by the
postal code, every indexed figure an argument, anything inline statutory and
unindexed with its citation (AGENTS.md §2), shared primitives imported from
`_shared.py` and never from the parent — that would be a cycle — and the public
names added to the parent's re-export block in the same change.

This file exists so the directory is a REGULAR package: a namespace package
resolves at import time from whatever happens to be on `sys.path`, which is the
one failure that would put a second checkout's state math into this return.
"""
