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

## V8 — Validation receipt bound to current inputs (P1) — PR #91

**Defect:** the capability gate (Step 0.7) and the tie-out gate (Step 12.7) each
PASS against the workspace as it stood when they ran, and nothing bound that
pass to those inputs. A document arriving afterwards — the second 1099 on
Thursday — an edited figure, or a re-fetched cache left `/tax-generate
--filing-package` assembling a filing package on gates that never saw what it
was packaging, with nothing in the package saying so.

**Shipped:**
- `agents/lib/receipt.py`: per gate, the outcome plus the digest of every file
  that gate read, in `validation_receipt.json`. `verify` re-derives the input
  map from disk rather than re-hashing the recorded paths, which is what makes
  an ADDED document a drift rather than an invisible one, and it states EVERY
  reason — an agent handed one at a time re-runs the gates once per file.
- **Per gate, not per workspace.** The capability gate reads the profile and
  runs before `result.json` exists; the tie-out gate reads the ledger, the
  result, the profile, the year's cache and every document. A gate is not
  invalidated by a file it never read.
- **The invalidation set is the input set and nothing wider.** `answers.md` is
  deliberately outside it: /tax-review appends to it after the return is
  computed, and a receipt that fires on that teaches the agent to re-run gates
  it has no reason to re-run — the gates-that-cry-wolf failure PR #69 paid for.
  Summaries and reviews are outside it for the same reason.
- **The engine digest is computed, not declared.** A version constant nobody
  remembers to bump reads fresh while describing an engine that has moved; the
  digest over `agents/lib/*.py` cannot go stale because it is not a copy of
  anything. The result template's digest rides beside it, since a renamed field
  there has no other signal (AGENTS.md §8).
- **`filing_package` refuses (exit 2) on a stale receipt**, naming each reason,
  with `--draft` as the banner-marked escape hatch: a refusal with no way
  through is one an agent routes around by not running the command at all, and
  a draft nobody can tell from a final package is worse than either.
- Both gates record their verdict AFTER printing it, and a receipt-write failure
  never lowers a verdict's exit code — a permissions fault must not be able to
  mask a tax-correctness fault, which is the rule `write_report` already
  follows. A FAILED or BLOCKED gate is recorded as such: "this gate failed" and
  "this gate never ran" are both refusals, and the difference is what the user
  is told.
- The receipt is a runtime artifact in all four places that means something:
  `.gitignore`, `IGNORE_PATTERNS` in `agents/install.sh`, the PII gate's
  sensitive-filename case and the permissions hook's protected list. Its paths
  are the taxpayer's own documents, and a listing is a disclosure (AGENTS.md §1).
  It is NOT in AGENTS.md §6's list: that file is at the byte cap
  `test_agents_md_exists_and_under_size_cap` enforces, and content past it is
  invisible to Codex with no error, so the entry would have been worse than
  absent. CONTRIBUTING.md carries it instead, and says why.

**Stated, not implied:** a hash receipt detects STALENESS, not tampering —
anything that can edit `result.json` can rewrite the receipt beside it. It
guards the ordinary way a return goes wrong, and nothing in it is called a
signature.

**Review round (Codex, local, against `main`):** four findings, all fixed. Three
are the same window seen from different sides — the gap between a gate reading
its inputs and the receipt describing them.

- **[P1] A valid receipt certified another result file.** `filing_package
  --result` takes any path inside the workspace and the gates only ever validate
  `result.json`, so a package assembled from `result_draft.json` came out
  `validated: true` with no banner. `receipt.covers_result` checks the packaged
  file by NAME and by DIGEST: a copy of result.json under another name is still
  not the file the ledger tied out against.
- **[P2] The digests were taken after the verdict, not with it.** A gate reads
  its inputs, computes, prints, writes its report; hashing at the end of that
  bound a PASS to a document that arrived during it. Each gate now captures its
  input map BEFORE it evaluates and passes it to `record`, so a document
  arriving mid-run shows up as drift at verify time — which is the truth, and
  refuses the package.
- **[P2] `record` could raise a bare `OSError`** out of `mkstemp`, a file read
  or `secure_path`, past the handler that protects a gate's exit code: a FAILED
  tie-out plus a permissions fault exited 1 with a traceback instead of 2, and
  Step 12.7 documents exit 1 as "the ledger is missing". Every I/O path in the
  module now raises `ReceiptError`, and both gates catch `OSError` besides.
- **[P2] A failed chmod left an unsecured receipt published.** The temp file is
  secured BEFORE the rename — the mode travels with the inode, and `mkstemp`
  establishes no owner-only ACL on Windows, which §1 covers too — and a failure
  removes the temp rather than leaving a readable artifact behind.

**Second Codex round:** three more, all fixed.

- **[P1] The receipt wrote document FILENAMES down.** A source document can be
  called `W2_<ssn>.pdf` — that is exactly why V0's `scan_workspace` refuses one
  — and this receipt is a file on disk, where AGENTS.md §1 forbids a full
  identifier in any intermediate. Entries are keyed by a DIGEST of the name with
  a `redact`ed display name beside it, so the number is in neither the key nor
  the value, and the messages still name the file a reader has to act on.
- **[P2] A symlinked duplicate collapsed into the file it points at.**
  `_validate_subpath` returns the RESOLVED target, and keying on that made a
  second document invisible to the check that exists to see one arrive.
  Containment is still checked on the target; the key is the name as the tie-out
  gate lists it.
- **[P2] The capability gate hashed the profile it had already parsed, again.**
  A flag flipped between the read and the hash produced a PASS receipt for a
  situation the gate would have blocked. It reads the bytes ONCE and records
  those; `gate_inputs(..., contents=...)` is how a caller says so. The tie-out
  gate has the window the other way round — it captures, then reads — which
  fails toward drift and refuses the package, so it is left as it is.

**Third Codex round:** six more, all fixed.

- **[P1] The tie-out gate's EVALUATION handler did not know the receipt's error
  type.** `gate_inputs` runs inside it, and an unreadable document raised
  `ReceiptError` past a handler that caught only `TieOutError` and
  `RolloverError` — a traceback carrying the chained `OSError` and its
  unredacted filename, which is the one thing §1 forbids printing. Both of that
  CLI's handlers now catch it, and `OSError` besides.
- **[P2] The package was verified against the file, not the bytes it was
  assembled from.** A recompute landing between the read and the check validated
  the REPLACEMENT while the package was still being built from the older result
  in memory. `filing_package` reads once and `covers_result` checks those bytes.
- **[P2] `/tax-archive` left the receipt behind.** It is in
  `EXPECTED_TOP_LEVEL_FILES` now: an archived year whose receipt stayed at the
  workspace root could never be packaged again — its gates would read as never
  having run — and the next year's gates would overwrite the evidence for the
  old one.

## V9 — A tracked, tested summary renderer (P2) — PR #92

**Defect:** `/tax-generate` INSTRUCTED the agent to write a
`generate_tax_pdf.py` into the workspace and then run it. That generator was a
per-workspace artifact — gitignored, blocked by name at the PII gate because it
carried the taxpayer's name in its own source, and, being source code written
fresh per return, the one file in the pipeline with no test, no review and no
way to tell a good one from a bad one except by reading it. AGENTS.md §8 said as
much: "check the generator by hand". The skill carried three pages of rules a
reader had to apply by eye each time — null-is-absent, no hardcoded taxpayer
data, no assistant attribution — and every one of them was a rule about code
nobody could run a test against.

**Shipped:**
- `agents/lib/pdf_render.py`: every value comes from `result.json` and nothing
  from anywhere else, so the taxpayer's name reaches the page as data and never
  as source. What it renders is decided by the SHAPE of the result rather than
  by per-section layouts — a return's sections vary, and thirty-four hand-written
  layouts is thirty-four places for the next section to be forgotten.
- The three prose rules are now three tested behaviours: a null is ABSENT (the
  shipped all-null template renders, which is the smoke test the skill has asked
  for since the generator was prose); the filer's name is read (two results with
  two names, and the module's own source is swept for either); and no assistant
  or vendor appears in the metadata or the page.
- Page 1 carries the `meta.limitations` manifest as the Scope & Limitations box
  before any dollar figure, and a `--draft` render stamps NOT VALIDATED in the
  same box with the reasons — a reader who reads one box reads that one.
- **Generation and validation consume the same input generation.** The renderer
  makes V8's check the way `filing_package` does: a stale receipt is exit 2 with
  every reason named, and `--draft` is the banner-marked way through. That is
  step 6 of the backlog's sequencing, which is why V9 follows V8.
- A number is an AMOUNT unless its key says otherwise: "Tax year $2,026.00" was
  the first thing the renderer printed, and years, codes, counts, ratios and
  rates are keyed out. A misclassification prints a figure without its dollar
  sign, which is legible, rather than inventing one.
- An OPTIONAL section nobody filled in is skipped (`_include_if` present, every
  figure zero or No); a section without that marker prints even when it is all
  zeros, because `balance_due_summary` saying the refund is $0.00 is the answer
  rather than the absence of one.

**Kept, against the backlog's own wording:** the PII gate still blocks
`generate_tax_pdf.py` by name. Retiring that rule would remove a live protection
from every workspace that has already run the old skill, and those workspaces
still have the file — AGENTS.md §6 now calls it legacy and says never to run it.
AGENTS.md §8's "check the generator by hand" rule is gone, replaced by the
tracked renderer's own tests.

**Review round (Codex, local, against `main`):** three findings, all fixed, and
all three are things a POPULATED return loses that the template's own emptiness
hid.

- **A real limitations sentence ran off the page.** reportlab draws a plain
  string in a table cell on ONE line and lets it past the frame; a long section
  VALUE is clipped instead, which loses the half nobody can see. Every cell is a
  wrapping, escaped `Paragraph` now — escaped because an ampersand in an
  employer's name would otherwise read as markup. The layout test measures the
  drawn words' positions, because a text assertion cannot see a sentence that is
  present and off-page.
- **A section that IS a list rendered nothing.** `dependents` is one, and a
  walker that only descended into dicts dropped the whole section without a
  word.
- **A computation warning was read as commentary.** `compute_form_2210` returns
  a ZERO penalty with a note saying a penalty applies but the rate is unfetched;
  classifying every `notes` field as commentary left `$0.00` standing alone — a
  loud degrade turned quiet, which AGENTS.md §4 says must never happen. Only
  UNDERSCORED keys are commentary now: some `note` fields are template labels
  and printing one is harmless, while dropping a warning is not.

**Second Codex round:** four more, all fixed.

- **A field name is not always the official line.** `line_1c_1099b_box_c` is
  Schedule D line 3 and `line_14c_qualified_medical` is Form 8889 line 15 — a
  filer transcribing by our labels would put the figure on the wrong line of a
  real form. /tax-generate's own caveat list is now a table in the renderer, and
  those rows say what they are instead of claiming a number they do not have.
- **An amount in the payer's own currency printed with a dollar sign**, beside
  the converted USD figure from the same section: two different amounts wearing
  one unit.
- **A taxpayer's name went through reportlab's markup parser unescaped.** An
  unknown tag was swallowed; a recognised one raised a ValueError carrying the
  whole name into a traceback, which is identity data in terminal output. One
  `_escape` now covers the headings as well as the cells.
- **A narrative longer than a page took the whole document down.** A table
  splits BETWEEN rows by default, so one worksheet note pasted into `notes` made
  a row taller than the frame and raised LayoutError for the entire summary.
  Rows split inside now, and the section heading is no longer wrapped in a
  `KeepTogether` that contradicts it.

**Third Codex round:** six more, all fixed.

- **An override belonged to another form.** `line_15_taxable_distributions`
  exists in `form_8606` too, and a global by-field-name correction labelled an
  IRA distribution "Form 8889 line 16" — sending the reader to an unrelated HSA
  form. The table is keyed by section as well as field now.
- **Dropping the dollar sign dropped the cents.** `:g` carries six significant
  digits, so a local-currency 12345.67 printed as 12345.7 and larger amounts
  lost whole units. A currency figure is an AMOUNT with two decimal places; only
  years, codes and counts print bare.
- **A failed render left a partial PDF at the umask's mode.** reportlab creates
  the file and then writes it, so the securing step at the end never ran on a
  build that died part way through — an exhausted quota is the ordinary case.
  The document is built into a SECURED temp file beside the target and renamed,
  and a failure removes it.
- **A carryforward was read as a year.** `carryforward_to_next_year` ends in
  "year" and is dollars; the suffix rule printed it bare and rounded. A key that
  says carryforward, basis, balance, credit, tax, income, loss or payment is an
  amount whatever its last word is.
- **Box A and Box D name both destinations.** Those transactions reach Schedule
  D lines 1a/8a only when they carry no adjustment; an adjusted one routes
  through Form 8949 to 1b/8b, and a label stating one destination is wrong half
  the time.
- **An alphabet the font does not carry is DECLARED.** The renderer embeds
  Bitstream Vera (bundled with reportlab: Latin and Greek), so a Cyrillic or CJK
  name prints as empty boxes — page 1 now says how many characters and where to
  read them instead, rather than losing a name in silence. Shipping a font that
  covers them is a licence-and-size decision, recorded as Q6 in the backlog
  rather than taken here. The characters themselves are not echoed into that
  message: they are the ones the font cannot draw.

**Fourth Codex round (against the merged commit, once the review limit reset):**
three findings, all fixed in the follow-up PR.

- **A non-finite figure printed as a computed one.** `json.loads` admits the NaN
  and Infinity literals, both pass `isinstance(x, (int, float))`, and the
  renderer formatted them — `($nan)`, `$inf` — into a document that looks like
  every other one, receipt and all, because the receipt checks freshness and not
  arithmetic. `secure_fs.first_non_finite` refuses the render, which is
  AGENTS.md §8's parse-boundary rule applied at the boundary where a figure is
  PRINTED.
- **The exchange rates went with the `meta` block.** They are the only figures
  in it, nothing else in the PDF states them, and a foreign-currency return
  could not be reconciled without them; they render as their own short section.
- **The skill still required the retired generator.** A fresh workspace cannot
  satisfy a prerequisite naming a file this skill no longer creates, and an
  agent following the list stops before it reaches the working renderer.

## V10 (first half) — One writer for every runtime artifact (P2) — PR #93

**Defect:** five modules had hand-rolled a temp-then-rename, each having earned
the same properties separately, and two more wrote IN PLACE. `scenario.py` and
`tie_out.py` opened the real target with `O_TRUNC`, so an interrupted write left
a truncated scenario file, or a truncated tie-out report, under the name the
next reader trusts — and the second is the hard gate's own record, the artifact
V8's receipt binds a filing package to.

**Shipped:** `secure_fs.atomic_path` / `atomic_write` / `write_secure`, and every
one of those writers routed through them. What the copies had between them is
kept once: created at 0600 BEFORE the first byte (with `secure_fd` on the
descriptor, so Windows gets its ACL at the same moment) rather than
written-then-chmod'd; a unique temp name in the TARGET's directory, so the
rename is on one filesystem and two runs cannot collide; `os.replace`, atomic on
POSIX and carrying the mode across; and the temp removed on ANY failure, so a
half-written file never survives under a name something else will read.

Routed: `tax_law_fetcher.save_tax_law`, `receipt._write`, `pdf_render.render`
(which needs the PATH form — reportlab opens the file itself),
`tax_calendar.write_calendar`, `scenario.save_scenario` and
`tie_out.write_report`. The tests that pinned each copy's invariants now pin
them at the one boundary that owns them.

**Not in this PR, and named rather than implied:** `rollover.apply_changes`
writes TWO files as one transaction, with a snapshot and a rollback if the
second replace fails — a single-file helper cannot express that, and rewriting
it deserves its own change. V10's other half (a restart that resumes from
validated inputs; concurrent runs serializing or refusing) is a design about
process state rather than about writing a file, and it is not started here.

**Review round (Codex, local, against `main`):** one finding, fixed. Securing
the temp file's DESCRIPTOR sat outside the cleanup scope, so a failure there —
the Windows ACL path shells out, and that can fail like anything else — left the
temp on disk to accumulate over retries. Every writer this replaced cleaned up
in that case; the securing is inside the same scope now, with the descriptor
closed either way.

