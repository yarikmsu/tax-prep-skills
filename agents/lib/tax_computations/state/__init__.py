"""Per-state computation modules, one file per state.

A state's file holds everything that is that state's own law and nothing else,
so `tax_computations/__init__.py` carries only federal math plus the shared
helpers.

The re-exports that keep `tax_computations.<name>` working are in the PARENT's
`__init__.py`, not here — the callers were addressing that name before the
split and some of them are skill markdown, which no sweep can rewrite. Whether
that block belongs here instead is a real question and is recorded against W2
in `docs/roadmap/2026-09-05-staff-agent-review.md`; it is deliberately left
until a SECOND state exists to reason from, because every way of moving it
costs something at one state and the costs differ by how many there are.

This file exists so the directory is a REGULAR package: a namespace package
resolves at import time from whatever happens to be on `sys.path`, which is the
one failure that would put a second checkout's state math into this return.
"""
