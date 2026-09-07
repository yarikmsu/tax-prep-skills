# Product-Owner Review + Next Backlog — 2026-08-12

> **Status 2026-09-05 — this is the ACTIVE backlog.** Shipped since the review was
> written: R4 (PR #75, `e975f6c`), R5's README link, S1 and S2 (`tax-safe-harbor`,
> PR #78, `47db081`), T2 (replay 2026-08-27, PR #81 `43ea099`), U2. Still open:
> **publishing the repository** — owner action, the only thing Epic R now waits on —
> then R6.
> T1 and T3 are calendar-triggered; U1 is filler; R2's controls are permanent and
> standing. The header and Parts 1-3 are the review as written on 2026-08-12 and are
> left that way; the Part 4 status markers and Part 5 sequencing are maintained.
> The agent-engineering backlog (Epics V-Z, ADOPTED 2026-09-05, in progress) sits beside
> this file in `2026-09-05-staff-agent-review.md`; its overlaps (R6, T1, T3, U1) defer here,
> and its Epic Z re-cuts `2026-state-support-ny-fl-wa.md` against the named next users.

Reviewer lenses: product owner with tax-adviser / enrolled-agent / IRS-officer /
security-officer / maintainer background.

Predecessor: `2026-07-20-product-owner-review.md`. **Its scheduled work is done** —
Epic M (M1 golden-path fixture, M2 runbook, M3 §6601/§6622 interest —
`compute_underpayment_interest` present in `tax_computations.py`), Epic N (N1 CA
Schedule P and N4 `state.ptet` both present — `capabilities.py:150-156`,
`tax_law_fetcher.py:327`), Epic O1/O2 (CLAUDE.md was 61 lines over an agent-neutral
AGENTS.md — 150 today, at the cap; README rewritten on the then-open PR #75). **O3 is
the only P3 carry-forward left.** Since then: PRs #66–#74 (portability, Apache-2.0,
progressive disclosure, hashed lock, PII content scan, hook hardening) and PR #75 then
open (engine preflight; merged 2026-08-17).

Spot-verified this pass by file:line, not re-derived; the full suite was not re-run.

---

## Part 1 — The headline

The 07-20 review ended by declaring the cadence change: *feature sprints end, season
ops begin, and **usage becomes the prioritizer**.* That posture is correct and it is
now the problem, because **the repository is private and has zero non-owner users.**
There is no usage to prioritize by. Every remaining coverage item is contingent on a
demand signal that cannot arrive through a closed door.

So this backlog is not a coverage backlog. Three reviews found the engine sound and a
fourth would find less. The binding constraint moved from *correctness* to
*distribution*, and the two stated goals — the work being visible, and the tool being
useful to someone — are blocked by the same setting.

**Everything below is ordered by what changes the information state, not by what is
most interesting to build.**

## Part 2 — What changed since 07-20 (and what it costs)

- **Portability shipped (#66).** Five agents can run this from a clone. That multiplies
  the value of publishing and does nothing while private.
- **The engine can now be separated from the skills (#75).** A skill copied away from
  `agents/lib/` refuses to compute rather than computing without its gates. This is a
  precondition for distribution outside a clone — it was landed before the channel
  exists, which is the right order.
- **The PII gate got a content scan (#67) and the lock got hashes (#70/#71).** Both are
  publish-grade hygiene that was not there in July.
- **New cost incurred:** the dev clone and the owner's live tax workspace are still the
  same directory. `tax_law_2025.json`, `documents/`, `summaries/` and
  `generate_tax_pdf.py` sit in the working tree beside the code. Gitignored, yes — but
  after publishing, a single `.gitignore` mistake stops being a private embarrassment.

## Part 3 — Findings by lens

**Security officer — the intake surface does not exist yet** *(closed 2026-08-12 —
see R3; the finding is kept as written because it is why the work happened)*. `.github/` holds
`dependabot.yml` and `workflows/` and nothing else: no `SECURITY.md`, no
`CONTRIBUTING.md`, no issue or PR templates. AGENTS.md §1 forbids pasting taxpayer data
into an issue or PR — but that rule is written for *agents reading the repo*, and the
day this goes public the people filing issues will be humans debugging their own
return. The first bug report will contain a W-2 unless the template structurally
discourages it. This is a product-safety item, not repo decoration, and it must land
**with** publish, not after.

**Enrolled agent — the calendar is unchanged and close.** Sep 15 (federal Q3 1040-ES,
no CA Q3), Oct 15 (TY2025 extended filing), Jan 15 (Q4), then TY2026 season open in
January 2027 — which is when M2's runbook fires for the first time in anger. The
advisory wedge planned in `docs/superpowers/plans/2026-08-12-safe-harbor-advisory.md`
is aimed at the first two of those dates — it shipped 2026-08-27 (S1), so it is live
for Sep 15 rather than expiring unbuilt.

**Maintainer — two acceptance procedures are written but never run** *(closed
2026-09-04 — see T2; the finding is kept as written because it is why the work
happened)*. CLAUDE.md specifies how to trust the review subagents: feed
`bias-direction-reviewer` the pre-fix state of a commit that fixed a directional bug
and check it names it unprompted; hand-corrupt a *copy* of `tax_law_<year>.json` with
a prior-year `salt_cap` and check `tax-law-freshness-auditor` catches it. Both are
documented as requiring a later session, and a later session has happened many times.
An unvalidated auditor is worse than none — it produces the feeling of coverage.

**Tax adviser — nothing new.** The 07-20 carry-forward list stands (8995-A
wage/UBIA/SSTB, Form 8889 return side, 2441, 5329, `/tax-harvest` §1091 basis-shift).
None of it is decision-blocking and none has a user behind it. Building any of it now
is guessing. *(Corrected 2026-09-05: three of those five were already shipped when this
was written — 8995-A as N2 (#57), the 8889 return side as N3a (#58) and 2441 as N3b
(#59), all merged 2026-07-20; `compute_form_8995a`, `compute_form_8889` and
`compute_form_2441` in `tax_computations.py`. Only 5329 and the §1091 basis-shift are
open. The conclusion survives the correction; the list did not.)*

---

## Part 4 — Backlog

Epic letters continue from Q (multi-agent portability).

### EPIC R — Publish, and survive being public — **P1**

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
  `docs/superpowers/specs/TEMPLATE-state-module.md` are a ready-made first contribution
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

### EPIC S — The seasonal advisory wedge — **P1, date-boxed**

> As a taxpayer who will never clone a repo, I want the one question this toolkit can
> answer without its engine.

- **S1 (P1, M): `tax-safe-harbor` — DONE 2026-08-27** (PR #78, squash `47db081`; suite
  2431 → 3221, CI green on 3.12 and 3.14). Executed
  `docs/superpowers/plans/2026-08-12-safe-harbor-advisory.md` — twelve tasks, three
  phases, no new tax math. Shipped inside the Sep 15 window, so the expiry clause
  (re-scope to Jan 15 / Q4, else drop to P3) never fired. The engine-free property is a
  POSITIVE declaration — `ENGINE_FREE_SKILLS` in `tests/skill_text.py`, tested in both
  directions — rather than the absence of a preflight line.
- **S2 (P2, XS): Point S1's closing handoff at R5 — DONE 2026-08-27**, shipped with S1:
  the skill's closing section sends a taxpayer who wants a real return to
  `docs/QUICKSTART.md` and says why it is not done in the chat. Settled by Part 7 — the
  advisory skill cannot produce a filed return (no PII in a hosted channel, ever), so
  its job is to answer the one question well and hand off to the local walkthrough.

### EPIC T — Season operations — **P1, calendar-triggered, non-negotiable**

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

### EPIC U — Carry-forwards — **P3**

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
`2026-09-05-staff-agent-review.md`; part-year / nonresident stays deferred there as Z8.)*

## Part 5 — Sequencing

*Re-sequenced 2026-09-05 against what has shipped.*

1. **Done:** R1, R3, R4, R5, S1, S2, T2, U2. R2's controls are standing — they started
   and do not finish.
2. **What Epic R still waits on is not engineering:** publish the repository (yours),
   then R6, which needs Discussions enabled before the pinned thread can exist. Every
   artifact the publish gate asked for — the audit, the intake surface, the first-user
   path — is in place.
3. **Autumn:** T1 when the TY2026 figures publish, after re-running T2's replay.
4. **Opportunistic:** U1, T3.

## Part 6 — Open questions (resolved 2026-08-12)

1. **Outside contributions: YES.** → R3 is a contributor surface, not a notice; the
   state-module playbook is the on-ramp.
2. **Owner's TY2026 return runs in this clone: YES.** → R2 is permanent standing
   controls, not a one-time move. The working tree holds live PII indefinitely.
3. **Success in six months: a stranger files a return with it.** → R5 promoted to P1 and
   named as the funnel target; R6 added, because that outcome is otherwise unobservable.
4. **Funnel target** — answered by Part 7.

## Part 7 — Distribution: where the funnel starts and where it lands

**The landing point is fixed by AGENTS.md §1, not chosen.** "Never run a real return on
a vendor cloud VM" applies to every hosted channel, including a chat sandbox: uploading
a W-2 to someone else's execution environment is the prohibited case. Claude.ai's custom
skill upload requires code execution to be enabled, so that channel *can* run Python —
and still must not, for a real return. **Every hosted channel is therefore
advisory-only and PII-free; filing is always local.** The funnel has exactly one shape,
and R5 is its landing page. The options below only decide where its mouth sits.

| Option | Path to a filed return | Cost | Verdict |
|---|---|---|---|
| **1. GitHub-native** — publish, add topics (`tax-preparation`, `tax`, `agent-skills`), let the community aggregators that index public `SKILL.md` repos pick it up | search / aggregator → README → clone → file | ~0, it is Epic R anyway | **Foundation.** Do it because R1–R5 already do it |
| **2. Skill-upload channel** — the advisory skill in Claude.ai (Settings → Capabilities → Upload skill) and the plugin marketplaces | chat answer → "to actually file, clone" | S1 + a listing | **Yes, if S1 ships before October.** Reaches non-developers; the chat→clone jump is the weak link |
| **3. Plugin bundle of the whole toolkit** — the officially supported skill→plugin→marketplace path, engine included | one-step install | maintenance fork of `agents/lib/` | **No.** §1 blocks real returns in the hosted sandbox, so this only serves local-agent users, who can already clone |
| **4. Seasonal community posts** — timed to Sep 15 / Oct 15 / January | high-intent reader → README → clone | a few hours, once per date | **Yes.** Highest-intent traffic available and no infrastructure; one-shot, so it needs R5 finished first |

**Recommendation:** 1 as the foundation, 4 as the deliberate act timed to the calendar,
2 only if S1 makes the October window. Skip 3.

**Competitive context (new this pass):** `filedcom/opentax` (open-source federal engine,
single binary, explicitly built for AI agents) and `habutax` (Python 1040 solver) already
occupy "an open-source tax engine exists." Differentiation is not the engine — it is the
integrity chain (parse ledger → tie-out gate → capability gate → source-pinned law →
limitations manifest) and running natively across five agents. **The README must say
that in its first screen**, or a visitor files this under "another tax calculator."
