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
