# W12 — test-owned hook inputs

The hook selects seven contributor suites. Each suite now declares the repository
inputs that can turn it red, beside its tests, rather than in a selector table.

`WATCHES` is a nonempty module-level literal tuple of `(priority, patterns)`
entries. Patterns are tuples of relative POSIX paths or fnmatch globs; `*`
crosses `/`. Priorities are unique nonnegative integers within a declaration.
Lower numbers win. A shared priority identifies one pattern group: its readers
must use identical patterns and their flags combine. Existing priorities leave
gaps; a new specific input can use an unused priority before a broad fallback.
When joining an existing group, copy its priority and patterns into the reader.

Only the hooks suite declares `WATCHES_DEPENDENTS`, for inputs that run the
existing four inexpensive dependency assertions rather than the whole suite.
The structural flag-to-suite roster remains in the selector; input patterns do
not. AST parsing and literal evaluation never import a suite or its engine.
Missing modules, malformed declarations, repeated writes and priority conflicts
fail selection and reach the shell's existing all-checks fallback.

An independent test recovers repository path chains, path constants and direct
engine imports from executable source and asserts each is covered by its own
reader's WATCHES, then drives the real shell to check selection. Existing helper
import, corpus reachability, glob and selection-cost tests remain. Mutation
controls verify quoted import examples do not become dependencies, a new input
changes selection without a selector or legacy-snapshot edit, and declaration
failure runs all suites through the real shell.

The audit is a contributor lint, not an arbitrary-Python filesystem tracer.
Dynamic discovery, transitive engine imports and globs still need deliberate
broad declarations; legacy behavior witnesses guard those existing scopes.
The Bash scan roots remain a separate traversal policy: new inputs must be
contained by one. Existing tests check both traversal and selection. Taxpayer
runtime paths never belong in these contributor declarations.
