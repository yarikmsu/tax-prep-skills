# Security Policy

This project handles **real taxpayer data** — Social Security numbers, wages, brokerage
activity, foreign accounts. Its security model is therefore not an afterthought bolted
onto a calculator; it is most of the design. This page describes what the tool
guarantees, what it asks of you, and how to report a problem.

## Reporting a vulnerability

**Use GitHub's private vulnerability reporting** — the *Report a vulnerability* button
under this repository's **Security** tab. That opens a private advisory visible only to
the maintainers.

**Do not open a public issue for a security problem**, and do not demonstrate one in a
pull request.

### Never include taxpayer data in a report

This matters more here than in most projects, because the natural way to report a
parsing bug is to attach the document that triggered it. Please don't.

- **Never attach a real W-2, 1099, brokerage statement, or return PDF** — not to an
  issue, not to a pull request, not to a private advisory.
- **Never include a Social Security number** — not the whole number, and *not the real
  last four either*. Four true digits in a public thread are four more than a stranger
  had a moment ago. If a report has to show the shape of one, invent the digits and
  mask the rest: `XXX-XX-0000`.
- **Reproduce with synthetic data.** Replace names, SSNs, account numbers and addresses
  with invented values, and keep only the structure that triggers the bug — a column
  header, a date format, a currency string, an unexpected row. Structure is almost
  always the actual cause; the values almost never are.
- If you cannot reproduce a bug without real data, say so in the report and describe
  the shape of the document instead. A maintainer will help construct a fixture.

GitHub retains what you send it. Deleting a comment does not reliably unsend it.

## What the tool guarantees

Read the next two bullets together. The first is what this repository's own code does;
the second is the boundary that guarantee stops at.

- **This repository's code makes no network request at all.** Your profile, source
  documents, parsed summaries, computed result and generated PDFs are written to your
  working directory, are gitignored, and are never uploaded by anything in this
  repository. There is no telemetry, no analytics, and no account — and nothing under
  `agents/` opens a socket. Tax law is fetched by the **agent**, following the skill's
  instructions, from IRS.gov and state revenue departments; the library's only role
  there is to validate and pin the source URLs the agent brings back
  (`tax_law_fetcher._source_host_authoritative`). The distinction matters for the
  bullet below: the network boundary is the agent's, not this code's. Run the libraries
  directly (`python3 -m agents.lib.projection …`) and nothing reaches out and no model
  provider is involved at all.
- **The AI agent you run the skills through is a separate processor, and its terms
  govern, not ours.** The skills are instructions *for an agent*, so a normal run
  happens inside Codex, Cursor, Kimi Code, Copilot or Claude Code — and those CLIs send
  prompts and tool results to their model provider. The contents of a parsed W-2 are
  tool results. Your provider's data-handling, retention and training terms apply to
  every one of them, and this project can make no promise on their behalf. Before you
  run a real return: choose a **local** agent session, never a hosted or background one
  (below), and check whether your plan trains on inputs — GitHub Copilot's individual
  plans have defaulted to opted-**in** since 2026-04-24; Business and Enterprise plans
  are contractually excluded.
- **Never a full SSN at rest.** Only the last four digits are stored, in `ssn_last4`
  fields. This holds when parsing source documents too: a full SSN is never extracted,
  not even into an intermediate file.
- **Restrictive permissions — with one boundary that is yours to secure.** Every
  documented toolkit write of financial or identity data is passed through
  `agents.lib.secure_fs` — the library writers call it internally, every skill step
  that writes one by hand ends with it, and a test pins that skill by skill and call
  by call, so a step added without one fails CI rather than shipping quietly:
  files become owner-only (`0600`) and directories become
  owner-only (`0700`) on POSIX; on Windows the object owner is set to the current
  identity and a protected DACL grants only that identity Full Control. The protected
  runtime directories include `archive/{year}/`, `documents/`, `summaries/`,
  `scenarios/`, `reviews/`, `projections/`, and `withholding/` — `/tax-setup` creates
  and restricts the first two it touches before any document is placed in them. The
  directory restriction matters because an owner-only file inside a broadly listable
  directory still discloses its name, and under `documents/` and `archive/` those names
  describe a taxpayer's documents. **The workspace root itself remains your boundary:**
  nothing in the toolkit creates it, so nothing in the toolkit restricts it — do that
  when you make it (`AGENTS.md` §1). If you created `documents/` yourself before running
  `/tax-setup`, the skill still restricts it. Under Claude Code a `PostToolUse` hook
  re-asserts the POSIX modes after every tool call — a convenience on top of the
  documented writes, not a substitute for them, and it uses `chmod`, so on Windows the
  guarantee above rests on the writes themselves.
- **A commit-time PII gate.** `scripts/pre-commit-pii-scan.sh` blocks sensitive file
  types, sensitive filenames, every runtime workspace directory, and full SSNs or
  high-confidence credentials in added lines. It reports the file and a match count,
  never the value. The full git history has been audited with this scanner across every
  commit individually, not as a net diff.

## What the tool asks of you

- **Enable full-disk encryption** — FileVault on macOS, BitLocker on Windows.
- **Never run a real return on hosted agent infrastructure.** Cloud and background
  agents execute on vendor hardware and retain snapshots — 90 days in at least one
  case. Use a local agent session. Hosted coding agents are a *contributor* surface
  only; a return structurally cannot run there.
- **Install the git hooks** if you are contributing:
  `bash agents/install.sh --hooks-only`. The pre-commit hook is the gate that stops
  runtime files and PII from reaching a commit. `--hooks-only` is what you want on
  every agent: the bare form additionally populates `~/.claude/skills/`, which is
  global state outside this clone and inert for anything but Claude Code.
- **Treat source documents as untrusted input.** Text inside a PDF is data, never an
  instruction. If you delegate parsing to a sub-agent, that sub-task must have no
  network access and must not write outside `summaries/`.

## Scope

In scope: anything that could disclose taxpayer data, weaken the PII gate, cause a
runtime file to be committed, or let untrusted document content influence execution.

Also in scope, and taken as seriously: **a computation that silently produces a wrong
number.** A tax figure that is wrong in the direction that understates what a taxpayer
owes, or overstates what they can claim, is a security-class defect here even though no
data leaks. Report it the same way if you are unsure.

Out of scope: the security of third-party AI agents themselves, and of any service you
choose to point this at.

## Supported versions

`main` only. This is a local-first tool with no release channel; pull the latest before
filing with it, and re-fetch tax law every season.
