# Epic V — Agent reliability and determinism

Backlog: `docs/roadmap/2026-09-05-staff-agent-review.md` (adopted 2026-09-05).
One task per PR; each entry records what shipped and the review findings folded
in, so the decisions are not re-litigated.

## V0 — Mask SSN/ITIN at the extraction boundary (P0) — PR #85

**Defect (second review, E1):** `parse_utils.py extract` and `tables` printed page
text and tables unmasked. The parse worker was told to mask the SSN AFTER reading
it, by which point the full number sat in the session transcript on disk — the
"intermediate file" AGENTS.md §1 forbids. Exposure was local (transcripts, tool
logs), never the network.

**Shipped:**
- `secure_fs.mask_ssn` — an SSN/ITIN-only masker preserving `XXX-XX-{last4}`.
  Not `redact`: that substitutes `[REDACTED]` and masks any nine-plus digit run,
  which would erase routing and account numbers, an unhyphenated EIN and the last
  four alike. The hyphen, dot and slash spellings are rewritten wherever they
  occur; the whitespace spelling and a BARE nine-digit run only under an
  identifier label, because nine bare digits is the routing-number shape exactly
  as often, and `850 00` followed by `2026` is a sub-$1,000 amount with its cents
  in their own column and then a year (a comma lookbehind protected only amounts of $1,000
  and up — the first cut erased the box amount below that line). A label is SSN,
  ITIN, "social security number", or any POSSESSIVE followed by TIN / identification
  number / identifying number ("RECIPIENT'S TIN" on every 1099, "Winner's TIN" on
  a W-2G); the possessor is enumerated only on the PAYER side (payer, employer,
  filer, issuer, lender, trustee, creditor, …), where the number is an EIN the
  forms print hyphenated anyway, so a form family the list has not met fails
  toward masking an EIN rather than leaking an SSN. Labels are word-bounded so
  `ITIN` inside "Awaiting" and `SSN` inside "Assn." are not labels, and the
  label-ABOVE-value box every W-2 and 1099 prints is covered. Every pattern is
  digit-bounded, and the delimiter alternation is shared with `redact`.
- Applied to every string `extract_pdf_text`, `extract_tables`, `extract_excel`,
  `extract_csv` and `crypto_importer._read_csv` return (the sibling reader the
  sweep found).
- The CLI catches every exception into one redacted line instead of a traceback,
  and gains `csv` / `excel` subcommands — `extract` refuses anything but a PDF, so
  the Excel/CSV group had no masked route at all. The masking invariant sits at
  the MODULE boundary — `scan_workspace` refuses a file whose NAME carries an
  identifier (`scan` printed a file named after an SSN verbatim) — so the CLI is
  a plain printer,
  with `ensure_ascii=False` so a Cyrillic column header reaches the worker as
  text, not `\u0414...` escapes. An unknown sheet name is a refusal naming the
  sheets that exist, where `{}` with exit 0 read as "nothing here".
  `crypto_importer._read_csv` IS `extract_csv`, and the importer's CLI boundary
  catches every exception the way `parse_utils`'s does, so a Latin-1 export is
  one `ERROR:` line, not a traceback.
- The E2 wording fix: tax-parse's worker rule 4 scopes reads to documents/ plus
  the `tax_law_{year}.json` exchange rate Step 4.3 needs (a rule a step
  contradicts is one the agent reasons past) and writes to summaries/;
  tax-verify's scopes reads to the one return PDF and the worker's own
  extraction file, which the template now secures like tax-parse's summaries. All
  three parsing skills name `parse_utils.py` as the ONLY reader in a sentence with
  "file-read tool"; tax-parse Steps 4 and 6.7 name the extractor.
- Tests: masker unit cases; each route compared against the UNMASKED library read
  of a synthetic file carrying the employer block, EIN, routing, account, amounts
  and an SSN, so only the identifier moves; CLI tests; doc-sync pins. No full
  identifier is persisted as a fixture — every synthetic value is assembled at
  runtime from area 000.

**Kept as designed:** the Step 5 sweep over written summaries stays as the second
layer. Enforcement of the read boundary (a closed tool set for the worker) is V6.

**Review round (Codex, local, against `main`):** four findings, all fixed.

- **A table carries its label in another CELL, and `mask_ssn` sees one cell at a
  time.** A CSV exported with an `SSN` column over bare nine-digit runs came
  straight back out of the reader built to prevent exactly that: the bare
  spelling is masked only under a label, and the label was a cell away. Tables
  now go through `secure_fs.mask_table`, which re-states the two text layouts
  with a cell boundary where the line break was — a label cell governs the cell
  to its RIGHT and its own COLUMN from that row down. It is also the one place
  an integer is masked: a workbook stores the nine bare digits as a number, and the
  "no delimiter and no label" reason `mask_ssn` leaves numbers alone stops
  holding once the column is labelled.
- **The W-2 boxes named after the program are AMOUNTS.** The noun after "social
  security" was optional, so "3 Social security wages" read as an identifier
  label and the amount under it printed with its cents in their own column
  (`850 00` then a year) was masked to `XXX-XX-2026` — the corruption of a
  figure the return is computed from that the whole labelled-only rule exists to
  avoid. The noun is now required, which costs nothing: the box that does hold
  the number says "number", and the hyphenated spelling it prints is masked with
  no label at all.
- **A masked path opens nothing.** `scan_workspace` masked the `path` it
  returned, and `scan` is the routing table /tax-parse dispatches from — so a
  document whose filename carried an SSN became a `FileNotFoundError` on a file
  sitting right there, and two filenames could mask to one path. The name is
  what has to change: the file is now refused by name, with the remedy stated,
  the way a symlink already was. Nothing is quietly dropped — tie_out's coverage
  check reads `documents/` itself and fails on a file with no ledger record.
- **A sheet name is a string the module returns.** `excel` printed it as a JSON
  key, so a workbook with a sheet named after an identifier disclosed it on the
  SUCCESS path while the refusal beside it redacted the very same name. The keys
  are masked; two sheets that mask to one key are a refusal rather than a
  silently shorter dict, which would drop a sheet of a return.

## V4 — Tax-law ingest CLI with per-block provenance (P1) — PR #87

**Defect (E7 and the hand-written cache):** the agent wrote `tax_law_<year>.json`
by hand — wrong keys, unordered brackets, MFJ below single, blog URLs under
`_sources` — and `save_tax_law` rewrote `_meta.fetched` on every save, so editing
one figure re-dated the whole cache.

**Shipped:**
- `tax_law_fetcher.py set <dotted.path> <value> --source <url> --year <y>
  [--applies-to <period>]` (`set_figure`): schema-validated path and value, year
  assertion against the cache, sanity ranges (rates in [0, 1], amounts
  non-negative, brackets contiguous and ascending from zero, dated lists and
  tiers ascending, MFJ ≥ single, fifteen ≥ zero, standard deductions positive),
  authoritative-source refusal. Every refusal writes nothing.
- `_provenance`, a sibling of `_sources` keyed by the same pin: `{retrieved,
  applies_to}` per block. Created lazily, not seeded — the auditor's acceptance
  fixture is a digest of the seed.
- `check_freshness` dates each block from `_provenance` (legacy fallback
  `_meta.fetched`), the oldest block decides, and the report carries
  `block_ages` and `oldest_block`. `_expected_pins` is the one roster for pinning
  and dating. `save_tax_law(stamp_fetched=False)` preserves the legacy stamp.
- tax-setup Step 4 gains `create` then `set`; the fetch reference and the
  season-open runbook name the command and forbid hand edits.

**Deferred, deliberately:** the freshness auditor definition is digest-pinned by
the acceptance record; teaching it `_provenance` is an edit that demands the
fresh-session replay, which rides with Y2 (the season-open runbook).

**Review round (bias reviewer 6, code review 7, cleanup 16; before merge):**
- `_meta` fallback minted a `fetched` stamp for a cache with no usable `_meta`,
  dating every un-provenanced block today — E7 by the fallback door; a
  `stamp_fetched=False` save now writes `_meta` without one, so such a cache
  stays undatable and reads STALE.
- `_provenance` was keyed at BLOCK scope, so one re-fetched leaf re-dated its
  siblings and replaced the block's source with a page holding only that leaf.
  It is keyed by the figure written (the pin for a whole-block write), each
  entry carrying its source; the oldest entry under a pin dates the block, and
  `_sources[pin]` keeps the first source unless the whole block is rewritten.
- `_check_brackets` accepted a closed top bracket, and `compute_bracket_tax`
  returned the tax on the part below it — an unbounded silent under-statement.
  The last `max` must be null, and the engine raises when income runs past a
  closed table. The IRS's inclusive "over X" reading is normalised, not refused.
- `applies_to` was checked only when it happened to be an `int`; it is validated
  at write time (the tax year, a calendar year for `historical_top_rate`, an
  optional `YYYY-Qn` for `underpayment_rates`), and a `retrieved` in the future
  is INCOMPLETE.
- A block written with fewer keys than the seed replaced a fuller one, and a
  leaf under a missing parent materialised `{}` — both invisible to the
  freshness walker. A block write must carry exactly the seed's keys; a missing
  parent is seeded from the schema. Whole `federal`/`ptet` writes are refused
  (they crashed in the pin key).
- The MFJ-at-least-single rule is derived from the schema (every dict with
  numeric `mfj`/`single` leaves) plus a short table of non-sibling orderings
  (hoh ≥ single ≥ mfs, 15% ≥ 0% breakpoints, `refundable_limit` ≤
  `amount_per_child` per §24(d)(1)(A)); an indexed amount of zero is refused
  everywhere, not at five listed paths.
- `check_freshness` runs the write path's validation (`validate_cache`) over the
  whole cache, so a hand-edited figure reads INCOMPLETE; `load_tax_law` refuses
  a cache holding any non-finite number (`1e400` parses to inf and defeated every
  `is None` guard), reported as INCOMPLETE by `freshness`.
- Cleanup: one dotted walker, one ascending-list validator, path-anchored table
  validators, `_check_number` derives the rate-ness from the path, one `now` per
  audit.

**Review round (Codex, local, against the branch below):** four findings, all
fixed. Three of them are about a figure's AGE, which is the whole of what the
freshness contract measures.

- **A leaf write re-dated the figures beside it.** A block was dated from its
  `_provenance` entries alone once it had any, so one `set` against a cache
  written before this command reported every untouched figure in that block as
  retrieved today — stale law passing the freshness contract, the direction
  AGENTS.md §4 makes a hard error, reached by the ingest path's ordinary use.
  A dotted entry now dates ONE figure and the block keeps `_meta.fetched` for
  the rest; the legacy stamp drops out once every populated figure under the
  pin is covered, so re-fetching a block figure by figure still clears it.
- **A block write left the entries inside it behind.** They went on dating the
  block from a retrieval the write had just overwritten, so a complete
  re-fetch could not clear STALE — a gate crying wolf on correct data. A write
  now supersedes every entry under its own path.
- **A `retrieved` time with an offset crashed the check.** `fromisoformat`
  returns an AWARE datetime for a stamp written `+00:00` or with a `Z`, and
  comparing one against the naive `datetime.now()` this module dates
  everything from raises TypeError — reachable through `set_figure` itself by
  passing an aware `now`. Both sides are normalised to local wall clock.
- **A state could not say it has no standard deduction.** New Jersey has none;
  the schema's null means "not fetched", so refusing every zero left the
  figure unstatable — writing it failed and leaving it null read INCOMPLETE
  for ever on a figure that had been read correctly. A zero is a figure under
  `state.`; a federal zero is still the unfetched state wearing a number,
  every federal amount here being indexed.
