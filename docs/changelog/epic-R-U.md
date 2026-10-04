# Epics R-U — Publish, the advisory wedge, season operations, carry-forwards

Backlog: the product-owner review adopted 2026-08-12, deleted 2026-09-20 when
`docs/roadmap/` collapsed to one file and available in git history. Its open items (R6/Y1, T1/Y2, T3/X6, U1/X7) moved to `docs/roadmap/BACKLOG.md`;
this is the record of what shipped, moved unedited.

**R1's result is the one entry here that a future run depends on.** The
full-history PII audit has a known baseline — one adjudicated-benign finding — and
an audit that reports anything else is a NEW finding rather than a re-litigation.
That sentence is why this file exists instead of the deletion being clean.

**FOUR ENTRIES BELOW ARE STILL OPEN, and their status is NOT here.** R6, T1, T3
and U1 are carried in `docs/roadmap/BACKLOG.md` as **Y1**, **Y2**, **X6** and
**X7**, and the backlog is where their status is stated. They are kept in this
file only because deleting them out of the middle of a moved-unedited record
would misrepresent what the 2026-08-12 backlog said. Read them as history — a
changelog entry is never a to-do list, which is the rule the backlog's own
collapse was about.

---

## Epic R — Publish, and survive being public

> As someone whose work is invisible and whose tool is unusable by anyone else, I want
> the repository public, with the intake surface that a public tax tool needs.

- **R1 (P1, S): Full-history PII audit — the gate. DONE 2026-08-12; verdict: publishable.**
  `bash scripts/pre-commit-pii-scan.sh $(git rev-list --max-parents=0 HEAD)...HEAD`,
  every commit individually. Two things came out of running it:

  **The command as written skipped the root commit.** `A...B` excludes A, so the audit
  covered 82 of 83 commits — omitting the initial import, the commit most likely to
  carry a file nobody reviewed. Reproduced on a synthetic repo (root commit holding a
  full SSN → `PII scan clean`), fixed in `scripts/pre-commit-pii-scan.sh` by appending
  the base when `<base>^` does not resolve, and pinned by a mutation-verified test pair
  that also holds CI's non-root behavior unchanged.

  **Result over all 83 commits: one finding, adjudicated benign.** Three matches of a
  single SSN-shaped literal in `docs/plans/2026-03-07-pii-guard-plan.md` — added in
  `71a1878`, deleted in `b9d4cba`, so present only in history, which is precisely what
  per-commit scanning exists to see. The value is the canonical documentation-example
  SSN and the surrounding lines are `echo "SSN: …" > /tmp/test-pii.txt` and
  `# Expected: match on …`: it is the test plan FOR this scanner. No taxpayer data.
  Zero blocked file types, filenames, or runtime workspace paths anywhere in history.

  **This finding cannot be cleared by the allowlist, by design.** `.pii-allowlist` is
  read from each scanned commit, so an entry added today does not exempt `71a1878`.
  The options are to accept it (recommended — the disclosure risk is nil and the value
  is a published IRS-style example) or to rewrite history, which re-writes every SHA
  after `71a1878`, breaks PR #75's base, and buys nothing. **Recommendation: accept, and
  keep this record so the next run of R1 is comparing against a known result rather than
  re-litigating it.** A future audit that reports anything other than exactly this file
  is a new finding.
- **R2 (P1, S, and PERMANENT): Standing controls for a public repo with live PII in its
  working tree.** Answer to open question 3 is **yes** — the owner's TY2026 return will
  run in this clone. So the one-time "move the workspace out" is off the table and the
  risk is structural and permanent: a public repository whose working tree holds real
  taxpayer documents, indefinitely. That converts R2 from a cleanup into ongoing
  controls. Specifically: never `git add -A`; the archive-time `chmod 700` discipline
  (AGENTS.md §1) matters more once the directory is also a publishing surface; and note
  the sharp edge — **the documented push command is `git push --no-verify`**, which
  disables the pre-push hook by convention. Commit-time gating is therefore the only
  local gate that actually runs. Acceptance: a deliberate attempt to stage a file from
  `documents/` is blocked, tested, not assumed.
- **R3 (P1, M): Public intake surface — DONE 2026-08-12.** Shipped `SECURITY.md`,
  `CONTRIBUTING.md`, `.github/ISSUE_TEMPLATE/{config,bug_report,state_module}.yml` and
  `.github/pull_request_template.md`. Blank issues are disabled, the bug form opens with
  a REQUIRED redaction acknowledgement plus a worked redacted repro, and both a security
  report and a wrong-computed-number report route to private advisories. Two follow-ups
  are GitHub-side and yours: enable **Discussions** (R6 depends on it) and create the
  `good first issue` state issues once the repo is public. Original scope, for the
  record:

  Answer to open
  question 2 is **yes**, outside contributions are accepted, so this is a real
  contributor surface rather than a "fork it" notice. `SECURITY.md` (how to report,
  plus an explicit never-include-taxpayer-data line), `CONTRIBUTING.md` (feature
  branches, `bash agents/install.sh` for the git hooks, the PII gate and `.pii-allowlist`
  staging rule, `requirements.in` not `requirements.txt`, the no-AI-attribution rule,
  Python 3.12/3.14 CI), a PR template, and an issue template whose first field is a
  redaction warning with a synthetic example. **The on-ramp already exists**: AGENTS.md
  invites community state modules and `docs/contributing/state-module-playbook.md` plus
  [TEMPLATE-state-module.md](https://github.com/yarikmsu/tax-prep-skills/blob/ba4ab34d0e75/docs/superpowers/specs/TEMPLATE-state-module.md) are a ready-made first contribution
  — label two states as `good first issue` and point CONTRIBUTING.md at them.
  Acceptance: the bug-report template makes pasting a real W-2 feel wrong, and a
  first-time contributor can add a state without asking a question.
- **R4 (P2, XS): Merge PR #75 — DONE 2026-08-17** (squash `e975f6c`). The README front
  door shipped with it, which is what closed R5's remaining link.
- **R5 (P1, S): The first-user path — the funnel target. DONE 2026-08-12.**
  `docs/QUICKSTART.md`: clone → venv → two engine commands → real figures, on the
  bundled `tests/fixtures/` workspaces, with no personal data and no network access.
  Both command sequences were RUN before being written down and the excerpts are their
  actual output — the page makes positive claims about behavior, so verifying them is
  the work, not a formality. It says outright that the fixture `tax_law_2026.json` is
  test data carrying no `_sources` block, which is what structurally distinguishes it
  from a fetched cache. The `README.md` link was deferred to avoid a conflict with PR
  #75's README rewrite and landed in that merge — `README.md:17`, the first line under
  the title, which is also where Part 7 says the differentiation has to be. R5 closed.
- **R6 (P2, XS): One place for a stranger to say they filed.** There is no telemetry and
  there should not be — the tool is local-first by design, so *filing is structurally
  unobservable*. The only signal available is voluntary contact. Open GitHub Discussions
  with one pinned thread asking exactly that. Without it, the success criterion cannot
  be evaluated even if it is met.

## Epic S — The seasonal advisory wedge

> As a taxpayer who will never clone a repo, I want the one question this toolkit can
> answer without its engine.

- **S1 (P1, M): `tax-safe-harbor` — DONE 2026-08-27** (PR #78, squash `47db081`; suite
  2431 → 3221, CI green on 3.12 and 3.14). Executed
  [2026-08-12-safe-harbor-advisory.md](https://github.com/yarikmsu/tax-prep-skills/blob/ba4ab34d0e75/docs/superpowers/plans/2026-08-12-safe-harbor-advisory.md) — twelve tasks, three
  phases, no new tax math. Shipped inside the Sep 15 window, so the expiry clause
  (re-scope to Jan 15 / Q4, else drop to P3) never fired. The engine-free property is a
  POSITIVE declaration — `ENGINE_FREE_SKILLS` in `tests/skill_text.py`, tested in both
  directions — rather than the absence of a preflight line.
- **S2 (P2, XS): Point S1's closing handoff at R5 — DONE 2026-08-27**, shipped with S1:
  the skill's closing section sends a taxpayer who wants a real return to
  `docs/QUICKSTART.md` and says why it is not done in the chat. Settled by Part 7 — the
  advisory skill cannot produce a filed return (no PII in a hosted channel, ever), so
  its job is to answer the one question well and hand off to the local walkthrough.

## Epic T — Season operations

> As the operator, I want the TY2026 season to open on verified law rather than on a
> user's first return.

- **T1 (P1, S): Execute the M2 season-open runbook** when TY2026 figures publish
  (Rev. Proc., typically autumn; runbook fires Dec 2026 – Jan 2027). Live fetch, K2
  source pin per block, ending in `verify_source_pinning` clean. M1's fixture proves
  wiring; only this proves fetchability.
- **T2 (P2, S): Validate the two review subagents — DONE.** Both agents PASSED the
  replay on 2026-08-27; the record merged 2026-09-04 (PR #81, squash `43ea099`). The
  replay date is the one that ages, not the merge. The procedure is the one CLAUDE.md
  specifies — replay a known answer, never plausibility. The pre-committed criteria, the
  planted defects and the traps are recorded in
  `docs/ops/review-subagent-acceptance.md`.
  **Re-run it before T1 leans on the freshness auditor**: definitions load at session
  start, so an edited one is not what a same-session replay tests.
- **T3 (P3, XS): Annual statutory re-verification** per M2's list (§6651 floor, SS wage
  base, retirement limits, SALT cap year). Unindexed is not immortal.

## Epic U — Carry-forwards

- **U1**: O3 unchanged (deduction-only marginal probe, scenario-keystone §63(f)/SE-tips
  threading, `/tax-harvest` §1091 basis-shift). Opportunistic filler.
- **U2**: Housekeeping — DONE. `backup/pre-squash-engine-preflight` is gone from origin.

### Explicitly NOT scheduled

5329, rental Schedule E, kiddie tax, crypto (G1), NYC / part-year / nonresident,
additional states. Each is real work with no user behind it. *(Corrected 2026-09-05:
8995-A limits, the Form 8889 return side and 2441 were listed here and should not have
been — see the tax-adviser lens above. Scheduling shipped work is the expensive
direction of this error, so the list is fixed rather than annotated in place.)* The
07-20 review already said usage decides these; Epic R is what makes usage possible.
Building any of them before then is inventing a persona. *(Superseded in part
2026-09-05: the owner named the next users — CA first, FL and NYC at P2, NC and WA at P3 — so
"NYC" and "additional states" now have a persona. They are scheduled as Epic Z in
the staff agent review; part-year / nonresident stays deferred as Z8.)*

