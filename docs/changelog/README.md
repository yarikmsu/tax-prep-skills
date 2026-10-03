# Changelog — implementation & review history

This directory holds the detailed **per-PR / per-Codex-round implementation
history** that was distilled out of `CLAUDE.md` (hygiene item O1). `CLAUDE.md`
keeps the **load-bearing invariants** an agent must not violate; these files
preserve the how-we-got-here narrative — what each round changed, which findings
were fixed, and which were refuted with primary-source citations (so a future
change doesn't re-litigate settled tax law).

One file per epic, newest first, plus the pre-epic base engine:

| File | Scope |
|------|-------|
| [`epic-AA.md`](epic-AA.md) | The 2026-09-20 product-owner review and what later reviews filed under it: the Earned Income Credit disclosed instead of defaulting to zero (AA1), MEASURED COSTS recording ratios rather than seconds (AA3), and a section for each AA item as it ships |
| [`epic-Z.md`](epic-Z.md) | State coverage for the named next users (NY IT-201/IT-203, NYC and Yonkers, NC D-400, WA excise, residency guardrails) |
| [`epic-Y.md`](epic-Y.md) | Distribution and operations (`TAX_WORKSPACE`, the workspace root outside the checkout) |
| [`epic-W.md`](epic-W.md) | Engineering hygiene (ruff and mypy in CI, the `slow` marker, the pinned dev interpreter) |
| [`epic-X.md`](epic-X.md) | Tax correctness, known and unfixed (capital-gains breakpoints fail closed) |
| [`epic-V.md`](epic-V.md) | Agent reliability and determinism (SSN/ITIN masking at the extraction boundary) |
| [`epic-R-U.md`](epic-R-U.md) | Publish and survive being public, the seasonal advisory wedge (`tax-safe-harbor`), season operations, carry-forwards |
| [`epic-P.md`](epic-P.md) | Florida state module (`type: "none"` regime discriminator, sales-tax election, property calendar, no-income-tax review step) |
| [`epic-O.md`](epic-O.md) | Hygiene (changelog refactor, README refresh, planning-tool carry-forwards) |
| [`epic-N.md`](epic-N.md) | In-persona coverage tier 2 (CA Schedule P, Form 8995-A, Form 8889, Form 2441) |
| [`epic-M.md`](epic-M.md) | TY2026 season readiness (golden-path fixture, underpayment interest, runbook) |
| [`epic-L.md`](epic-L.md) | Deferred-engineering round-out (§7503 rollover, `schedule_e_k1_income`) |
| [`epic-K.md`](epic-K.md) | Integrity round-out (verify override hardening, source pinning, PFIC tripwire) |
| [`epic-J.md`](epic-J.md) | Equity-comp basis correction (Form 8949 code-B, `compute_espp_disposition`) |
| [`epic-I.md`](epic-I.md) | OBBBA TY2026 readiness (Schedule 1-A, §170(p), §70105 QBI minimum, AMT rate) |
| [`pre-epic.md`](pre-epic.md) | Base computation engine + all planning skills (scenario/roth/harvest/ptet/retirement) |

This rule stopped being applied around PR #120 and the backlog absorbed the job: Epic Z's sixteen PRs, W2's package split, Y7 and Z7 all landed their narrative in `docs/roadmap/` instead. It was moved back on 2026-09-20, and `docs/roadmap/BACKLOG.md` now carries the standing rule that a shipped item leaves it for this directory.

**When resolving where a fact belongs:** a durable rule that governs a
computation (a bias direction, a fetched-vs-fallback behavior, a statutory
constant, a capability-flag name) stays in `CLAUDE.md`. A record of *when* or
*why* it was changed — PR numbers, review-round numbers, refuted-vs-fixed
findings, historical test counts — belongs here.
