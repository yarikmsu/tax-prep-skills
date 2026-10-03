# W3: one invariant-pass runtime

The previous stamp port was abandoned because it duplicated an eighteen-line
round trip while leaving three red-token lists and all RUN flags in Bash. This
change removes those flags and their readers/writers together. One CHECKS mapping
in `.claude/hooks/lib/hook_runtime.py` supplies the selector's structural roster,
the persisted tokens, and the pytest paths. The selector reads it as an AST
literal; it never imports the runtime or a test module.

The shell retains environment hardening, payload extraction, the executable-venv
check, and the cheap filesystem boundary. This is Q12's bootstrap: it creates a
backdated pass-start marker, refuses unsafe/untrusted stamps, scans watched
CTIMEs, and exits without starting an interpreter when a Bash call changed
nothing. These trust checks stay in the bootstrap because moving them to Python
would charge interpreter startup on that most frequent path. It then execs the
runtime using NUL-framed stdin; edited document names never reach argv. Write/Edit
first classifies its exact path; an unrelated edit exits, while a watched edit
unions the pending filesystem scan or selects all checks if trust failed.

The runtime owns the entire selected pass: selector protocol/status validation,
the union with prior red checks, pytest arguments, verdict handling, stamp
replacement, signal cleanup, timing warning, and bounded failure output. No Bash
selection, red reader/writer, pytest execution, or stamp writer remains beside it.
An unrelated edit does not retry red checks. A red verdict advances the boundary
and records every selected check; a green verdict clears them; pytest 2–5 leave
both boundary and memory intact.

Byte reads preserve the abandoned port's two lessons: NUL bytes are removed as
Bash command substitution removed them, but CR is retained. The bootstrap still
uses raw sed for version trust, so CRLF never becomes a trusted version. Stamp
writes recheck both directory and stamp symlinks after pytest, preserve marker
mtime rather than pass-end time, and track a same-filesystem candidate until
atomic replacement. Signals wait for the foreground command before cleanup,
preventing a delayed touch from recreating a candidate.

The existing subprocess hook suite remains the main behavioral contract.
Source-shape assertions move to the actual Python owner, rather than keeping
fake RUN declarations in Bash to satisfy them. A differential against the saved
pre-change hook compares exit status, stderr, pytest selection, and stamp body;
empty stdout alone is never an equivalence claim. Added byte-format regressions
cover NUL and CRLF, and the no-interpreter fast path is tested with a logging
workspace interpreter. The count owner remains the shell's MEASURED COSTS block.
