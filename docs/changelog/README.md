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
| [`epic-O.md`](epic-O.md) | Hygiene (changelog refactor, README refresh, planning-tool carry-forwards) |
| [`epic-N.md`](epic-N.md) | In-persona coverage tier 2 (CA Schedule P, Form 8995-A, Form 8889, Form 2441) |
| [`epic-M.md`](epic-M.md) | TY2026 season readiness (golden-path fixture, underpayment interest, runbook) |
| [`epic-L.md`](epic-L.md) | Deferred-engineering round-out (§7503 rollover, `schedule_e_k1_income`) |
| [`epic-K.md`](epic-K.md) | Integrity round-out (verify override hardening, source pinning, PFIC tripwire) |
| [`epic-J.md`](epic-J.md) | Equity-comp basis correction (Form 8949 code-B, `compute_espp_disposition`) |
| [`epic-I.md`](epic-I.md) | OBBBA TY2026 readiness (Schedule 1-A, §170(p), §70105 QBI minimum, AMT rate) |
| [`pre-epic.md`](pre-epic.md) | Base computation engine + all planning skills (scenario/roth/harvest/ptet/retirement) |

**When resolving where a fact belongs:** a durable rule that governs a
computation (a bias direction, a fetched-vs-fallback behavior, a statutory
constant, a capability-flag name) stays in `CLAUDE.md`. A record of *when* or
*why* it was changed — PR numbers, review-round numbers, refuted-vs-fixed
findings, historical test counts — belongs here.
