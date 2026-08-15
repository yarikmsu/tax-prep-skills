# Quickstart — from clone to a real number in 15 minutes

This walkthrough produces actual tax figures from **bundled synthetic fixtures**. It
needs no tax documents and no personal information. Nothing you run here touches a real
return.

Cloning the repository and installing dependencies (step 2, below) does need network
access, the same as any git/pip workflow. Every step after that — running the fixture
projection and reading its output — needs none: no tax-law fetch, no telemetry, no
outbound request of any kind.

If you are evaluating whether this project is worth your time, this is the page to run.

---

## 1. Requirements

Python 3.10 or newer. CI runs the suite on **3.12 and 3.14**, so those are the two
versions known to be green.

## 2. Install

```bash
git clone https://github.com/yarikmsu/tax-prep-skills.git
cd tax-prep-skills
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` is a compiled lock that pins the entire transitive tree with
`--hash=sha256`, so pip refuses any artifact whose contents do not match. Never
`pip install --break-system-packages`; use the virtual environment.

## 3. Produce a mid-year tax projection

```bash
python3 -m agents.lib.projection \
  --inputs tests/fixtures/projection/projection_inputs.json \
  --workspace tests/fixtures/projection \
  --year 2026 \
  --today 2026-08-12
```

`--today` pins the run date so your output matches this page exactly; omit it to use
the current date. You should see, among a longer report:

```
## Projected full-year federal

| Item | Amount |
|---|---:|
| Gross income | $122,494 |
| Taxable income | $92,494 |
| **Total tax** | $14,459 |
| **Projected balance** | $343 (owed) |

## Safe harbor analysis

| Test | Amount |
|---|---:|
| 90% of projected current-year tax | $13,013 |
| 100% of prior-year tax | $14,000 |
| **Required annual payment** | $13,013 |
| Status | covered |
```

Note what the report does *not* do: it states its own assumptions, names the
second-order effects it is knowingly leaving out, and says which direction each one
biases the answer. That is the house style, and it is the point of the project.

## 4. Scan a portfolio for harvestable losses

```bash
python3 -m agents.lib.loss_harvest \
  --result result.json \
  --workspace tests/fixtures/harvest \
  --holdings holdings.json \
  --year 2026 \
  --today 2026-12-15 \
  --no-save
```

Paths are resolved relative to `--workspace`. `--no-save` keeps the run from writing a
scenario file into the fixture directory. Expected output includes:

```
| Total unrealized losses (all lots) | $8,500 |
| Wash-sale disallowed (§1091) | $750 |
| Tax-advantaged accounts (not deductible) | $1,500 |
| **Eligible to harvest this year** | **$6,250** |
| **Federal tax savings (total)** | **$1,305** |
```

The wash-sale and tax-advantaged-account lines are subtractions the tool makes *against*
your interest. A scanner that skipped them would report a larger, wronger number.

---

## These figures are a demonstration, never a filing basis

The fixtures under `tests/fixtures/` carry their own `tax_law_2026.json`, which is test
data. **It is not fetched tax law and must never be treated as any.** It is
structurally distinguishable from the real thing: a genuine cache carries a `_sources`
block pinning each figure to the authoritative page it came from, and the fixture has
none, so it would fail the source-pinning check a real run performs.

A real run fetches current-year rates, brackets and thresholds from IRS.gov and the
state revenue department at runtime, pins the sources, and **stops and asks you** if it
cannot confirm a figure is current. **No figure that changes from year to year is
hardcoded** — every bracket, standard deduction, cap, credit amount and indexed
threshold is fetched and source-pinned, because a stale one produces a wrong return
that looks completely plausible and nothing downstream can detect it.

That is not the same as "no number appears in the code," and if you are auditing
freshness the difference matters. A small set of **structural constants set in statute
and not indexed** is compiled in — the SE-tax rate and its 92.35% net-earnings factor,
the 0.9% Additional Medicare and 3.8% NIIT rates, and the 20% QBI percentage are
examples, not the whole list. They reach the math as function *parameters with
defaults*, so a fetched value overrides one whenever the cache carries it. The
authoritative enumerations are `agents/lib/tax_law_fetcher.py`'s module docstring and
`AGENTS.md` §8; they are deliberately not re-listed here, because a third copy would
drift. Separately, `AGENTS.md` §2 carries one narrow enumerated exception for a *state*
figure: California's statutory-fixed `mental_health_surcharge` rate and threshold,
written into the code alongside its R&TC §17043 citation.

## What a real run looks like

```
tax-setup → tax-parse → tax-compute → tax-generate → tax-verify → tax-review
```

Invoke skills using your agent's own syntax — they are discovered natively by Codex,
Cursor, Kimi Code and Copilot from `.agents/skills/`, and by Claude Code through
`.claude/skills/`. If your agent has no skill mechanism, the files are plain Markdown
and are written to be followed directly.

Three rules govern a real run, and they are not negotiable:

- **Runtime files are local and gitignored.** Your profile, documents, computed result
  and generated PDFs are written to your working directory, are never uploaded by
  anything in this repository, and must never be committed. Your *agent* is the
  exception, and not a small one: its CLI sends prompts and tool results — the contents
  of a parsed W-2 among them — to its model provider, under that provider's retention
  and training terms. `SECURITY.md` has the detail.
- **Never run a real return on hosted agent infrastructure.** Cloud and background
  agents execute on vendor hardware and retain snapshots. Use a local session.
- **Never paste taxpayer data into an issue or pull request.**

## Why this is not another tax calculator

The engine is the least interesting part. What is unusual is the chain that makes an
answer *checkable*:

- **A tie-out gate** compares every parsed source document against the computed result
  and fails the return when they disagree — so a dropped 1099 cannot pass silently,
  which is the single most common way an automated return goes wrong.
- **A capability gate** blocks rather than guesses when a return needs something the
  engine does not model, naming the form, and records the limitation on page 1 of the
  output instead of hiding it.
- **Bias direction decides severity.** When data is missing, a fallback that overstates
  the number you act on is treated as a hard error; one that understates it degrades
  with a loud banner. Silence is never an option.

## Next

- `AGENTS.md` — the project's operating rules, security first.
- `docs/contributing/skill-catalog.md` — what each skill does and its scope limits.
- `CONTRIBUTING.md` — how to add a state, which is the best first contribution.
