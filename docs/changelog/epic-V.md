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

## V11 — Parser resource budgets (P2) — PR #95

**Defect:** the 100 MB cap in `parse_utils` bounds the FILE and nothing else.
A `.xlsx` is a ZIP: a few hundred kilobytes of compressible XML expands to
gigabytes, and openpyxl decompresses the whole archive into memory before this
module sees a cell. The extracted text, the page count and the time spent are
all unbounded by the file's size too. Source documents are untrusted input
(AGENTS.md §1), and this is the parser they arrive at.

**Shipped:**
- `MAX_UNCOMPRESSED_BYTES` and `MAX_COMPRESSION_RATIO`, checked from the ZIP's
  central directory BEFORE `load_workbook` runs. Both halves: a bomb is small on
  disk, which is the trick, so an absolute cap alone lets a 100 KB file through.
  The residual is stated rather than implied — a crafted archive can understate
  its own sizes, and the output budget below is what catches that.
- `MAX_EXTRACTED_CHARS`, charged incrementally per page, per row and per cell:
  the point is to stop a document that is still expanding, and a check that only
  runs at the end has already paid the cost it exists to avoid.
- `PARSE_TIME_BUDGET_SECONDS`, checked on the same schedule.
- `MAX_PAGES` bounds what ONE CALL extracts, not what the file holds —
  /tax-parse splits a long document into per-page sub-tasks, and a cap on the
  file would refuse every slice of a document the workflow is already handling
  correctly.
- Every one of them REFUSES rather than truncating, and a refusal returns
  nothing: a partial extraction that looks complete is what the tie-out gate
  structurally cannot see. `ParseBudgetError` is a `ValueError` so that every
  caller's existing handler for the size cap covers it rather than letting it
  through as a traceback.

**Not knobs:** the budgets are module constants with no environment override. A
budget a caller can raise is one the caller raises.

**Review round (Codex, local, against `main`):** one finding, fixed. The TIME
budget was checked only where a CHARACTER was produced, so a numeric-only
worksheet and a PDF page with no tables ran past the deadline without ever
reaching it. `tick()` is now its own check, called at every row and page
boundary; `charge()` calls it too. `extract_pdf_text` was never bypassed — it
charges the page's text unconditionally — and the test says so rather than
carrying a parameter that passes either way.

**Second Codex round:** two more, both fixed.

- **A numeric or empty cell cost nothing.** It occupies memory and prints in
  the CLI's JSON all the same, so charging strings alone let a 100x4 numeric
  sheet through a budget it was many times over. Every CELL is charged now —
  its text as the result carries it, plus the punctuation the JSON around it
  costs.
- **The last table-free page had no check after it.** The pre-call tick passed,
  the charge was skipped because the page yielded nothing, and the call returned
  `{}` having run well past the deadline. Both PDF routes tick AFTER the
  extraction and once more before returning, and the test advances the clock
  DURING the extraction rather than before it.

**Third Codex round:** two more, both fixed.

- **An empty row cost nothing.** A blank CSV line is `[]` — charged nothing
  while still being retained and returned, so a file of nothing but newlines
  allocated a list per line under a budget that never noticed. Every container
  costs its own brackets now.
- **The CSV route ticked and THEN masked.** Masking is the other substantial
  pass over the file, and a route that checks its deadline before doing work has
  left that work outside its own budget. The PDF and workbook routes mask inside
  their loops, so their trailing tick already covered it.

## The six open questions, ruled — 2026-09-07

Part 5 of `docs/roadmap/2026-09-05-staff-agent-review.md` recorded six questions rather
than guessing at them. All six are now ruled, and the roadmap keeps each ruling beside its
question so the trade is visible rather than inherited:

- **V3's first golden** is a SYNTHETIC CA + federal MFJ return, hand-computed from the
  worksheets — buildable in the open, and it unblocks step 4.
- **The model-driven eval runs LOCALLY, on demand**, with the workflow written but
  disabled: no API key becomes a repository secret.
- **The cross-agent matrix starts at ONE agent** and grows when a second is in use. The
  cost is named: the five-agent portability promise stays unexercised for now.
- **V6's enforcement mechanism is to be investigated against the installed build**, with
  the residual written down — and if nothing clears the adversarial bar, V6 ships as the
  sequential fallback with a header saying the boundary was not enforced, rather than as a
  claim that reads enforced and is not.
- **Y2 keeps its definitions and its acceptance record untouched.** The season-open
  runbook's step 5 is now the ordered procedure — make the two queued definition edits,
  fetch, fresh session, replay both reviewers, re-record — and the record points at it, so
  neither half can be done without the other.
- **No font ships.** The PII gate blocks a binary blob outright, so shipping one would
  mean widening the gate this repository guards most carefully; a Cyrillic or CJK name
  keeps its page-1 declaration instead. The cost is named in the ruling: this toolkit
  already handles Cyrillic elsewhere, so a taxpayer whose own name is Cyrillic gets a
  summary that cannot print it.

## V3 (first half) — The eval fixture, the first golden, and the comparator (P1) — PR #97

**Defect:** the suite proves the ENGINE computes correctly and nothing proves the
AGENT-MEDIATED path does. `tax-compute` is Markdown instructing a model to call
27 functions and assemble `result.json` by hand; a skipped step, a
filing-status key read from the wrong place, or a figure transcribed wrong is
invisible to every test in `tests/`. The 09-05 review calls this the largest
remaining correctness risk, and V3 is the item that closes it — and it gates
every V1 and V2 slice, one golden per slice.

**Shipped, per the owner's Q1-Q3 rulings (2026-09-07):**
- `evals/fixture.py` builds a synthetic workspace: a W-2 and three information
  returns as real PDFs, a profile, and a FIXED invented tax law. The law is
  invented on purpose — a golden built on real figures goes stale every filing
  season and gets rewritten by whoever notices, which is how a golden stops
  being a known answer — and its brackets are round so the derivation can be
  checked with a pencil. Every federal block is filled, because a cache reading
  INCOMPLETE would make an eval run a test of the freshness gate rather than of
  the agent.
- `evals/goldens/ca_mfj_2026/` — the golden and its `derivation.md`, every figure
  with its arithmetic. The case is chosen so the QDCG worksheet's 0% band is
  EMPTY: ordinary income sits above the breakpoint, which is exactly the shape an
  agent gets wrong by applying the breakpoint to the preferential income instead
  of to taxable income.
- `evals/compare.py` scores a result against a golden in three classes, all
  reported: MISMATCH (a figure moved), MISSING (a step that never ran — the
  false-completion shape) and EXTRA (a section from somewhere else, the one
  people wave through). Tolerance is $1.00, the tie-out gate's own, so the two
  cannot drift apart on what "the same figure" means.
- `tests/test_evals.py` re-derives every figure in the golden from the fixture's
  inputs through the engine, so the golden cannot be hand-edited into drift; it
  also pins that the fixture is a workspace the pipeline would accept — readable
  through the masking extractor, owner-only, and federally complete.

**Two defects the tests found in this PR's own code:** the fixture law was
INCOMPLETE (37 federal null fields, so an agent would have correctly refused to
compute), and the dollar tolerance reached `meta.tax_year` — 2026 against 2025 is
one apart, and a comparator calling a return filed for the wrong year a rounding
difference is worse than no comparator. The tolerance is now money-only, keyed
the way `pdf_render` keys the same distinction.

**Not in this PR:** the model-driven runner that dispatches an agent against the
fixture, and the further cases V3 names — a missing and a corrected document,
hostile document text, a stale cache, a context interruption, and a valid
zero-income return. Each is its own golden.

**Review round (Codex, local, against `main`):** three findings, all fixed, and
two of them were the fixture failing the very gates it has to get past before
anything is scored.

- **[P1] The workspace did not pass the freshness gate.** `state.itemized_phaseout`
  was absent, so the cache read INCOMPLETE, and it carried no retrieval stamp, so
  filling that alone would have left it STALE. Either stops /tax-compute at Step
  0.5. The test that was supposed to catch this checked FEDERAL nulls only; it
  asserts the whole report is FRESH now. The same probe found a second blocker
  Codex did not name: the capability gate BLOCKED the profile, because its flags
  block was empty and absence is not treated as "no", so the run would have
  stopped at Step 0.7.
- **[P1] The golden's limitations manifest was empty.** The gate emits the
  California state-scope note for this profile and /tax-compute copies it
  verbatim, so an agent doing the right thing FAILED the comparison while one
  omitting the disclosure passed — backwards, and the exact false-completion the
  harness exists to catch. The golden carries the gate's own manifest now, and a
  test keeps the two synchronised.
- **[P2] A boolean scored as a number.** `False == 0` and `True == 1` in Python,
  so a flag standing in for an amount — or an amount for a flag — came back
  clean under a bare equality.

**Second Codex round:** two more, both fixed, and the first is the worst kind of
error a golden can carry.

- **[P1] The golden encoded a shape the pipeline does not produce.** The lot sat
  directly under `part_ii_long_term` instead of in the detail ROW keyed by its
  1099-B box, so a correctly assembled result would have FAILED the comparison —
  and the tie-out gate's `capital_proceeds` check, which reads exactly those
  rows, returned None on it. `income_summary_by_person` was missing too, so
  completing Step 12 scored as EXTRA. A golden that punishes correct behaviour is
  worse than no golden; the shape is the template's now, and a test reads the
  golden through `tie_out._result_total` so it stays that way.
- **[P2] The case implied an estimated-tax penalty it did not answer.** 90% of
  the year's tax is 30,709.80 against 28,000 withheld, so Step 9.5 finds an
  underpayment and must ask the taxpayer or stop — and a case that stops to ask
  is not a known answer. The profile now carries the prior year whose 110% safe
  harbour the withholding covers, and `answers.md` records that and every other
  question the return raises, since that file is the headless input path.

**Third Codex round:** three more, all fixed, and all the same shape as the
second — the golden and the fixture disagreeing with the pipeline's own
contract.

- **[P1] Three zero-valued lines the pipeline PERSISTS were missing.** Step 9
  writes the excess-SS and Schedule 3 Part II lines; Step 9.5 writes a zero
  penalty when none applies. Absent from the golden, each scored a completed
  step as EXTRA — the harness rewarding an agent for skipping it.
- **[P2] The state block had no SDI rate.** `compute_excess_sdi` runs for every
  person with CA-wage W-2 income and hard-errors on a None rate, so Step 10.11
  stopped the computation. The rate is synthetic, the wage limit explicitly null
  (the TY2024+ shape), and the W-2 now carries CASDI in box 14.
- **[P2] The answers did not close the intake questions.** /tax-parse Step 2.5
  asks whether the document set is complete and whether any form is a
  correction; a headless run stops on an unanswered one, correctly, and a case
  that stops is not a known answer.

**Fourth Codex round:** three more, all fixed.

- **[P1] The golden omitted the CA itemised worksheet.** State withholding makes
  the Schedule A computation mandatory in Step 5, and Step 10.4 then requires
  the California one even when both elections take the standard deduction —
  they are independent elections, so the comparison must be made before either
  is chosen. Running that required step scored as EXTRA.
- **[P2] The answers left the deduction inputs open.** Schedule A asks for the
  prior-year state balance paid this year and for charitable gifts; a headless
  run stops on an unanswered one, and a cash gift would also create a §170(p)
  non-itemizer deduction. Both are answered as zero now, explicitly.
- **[P2] The comparator printed identity values.** `--result` takes any file and
  the golden being synthetic constrains nothing about what is handed to it:
  pointed at a real return, a name or address mismatch printed the taxpayer's
  own details to the terminal and into `--json`. The difference is reported and
  the value is not — the PATH is what a reader acts on — and the suppression is
  scoped, so a tax line still shows its figures.

**Fifth Codex round:** two more, both fixed, and the first REVERSES what the
fourth round asked for — which is worth recording rather than quietly settling.

- **[P2] The CA worksheet is computed, not persisted.** The fourth round read
  the result TEMPLATE and concluded the section belongs whenever the
  computation runs; the SKILL says to write it "when itemizing", and this case
  takes the standard deduction. The skill is the contract, so the golden carries
  no section and the test pins the comparison instead — with an assertion that
  fails loudly if the case ever drifts into itemising.
- **[P2] A composite difference printed identity data.** Identity detection
  reads the difference's PATH and cannot see inside an EXTRA whole section,
  which carries its own payer or employer; and `income_summary_by_person` is
  keyed by the taxpayers' first names, so the path itself carries identity.
  Composites are counted rather than printed, and a person-keyed segment is
  masked, in both output modes.


**Sixth Codex round:** two more, both fixed, and both are the previous round's
fix reaching one field further than it did.

- **[P1] Identity suppression was a list of NAMES, so it covered only the fields
  somebody thought of.** `w2_summary.employer_ein` was not on it, and an EIN is
  an identifier `redact` structurally cannot see — two digits, a hyphen, seven
  digits is neither an SSN spelling nor a nine-digit run — so the employer's tax
  ID printed verbatim in both output modes. Two rules now, because one cannot
  reach both cases: the leaf's TRAILING TOKEN decides identity, which reaches
  `payer_tin` and `spouse_ssn` without either being listed; and every emitted
  scalar and path goes through `redact`, which reads the VALUE and so covers an
  identifier arriving under a key no list will ever hold — an EXTRA, printed with
  its value, is exactly how one arrives. Over-redaction is the accepted cost: a
  money field in the hundreds of millions prints masked, and that direction is
  legibility while the other is disclosure.
- **[P2] The golden was hand-written line by line, not the initialized
  template.** Step 5 copies `result_template.json` and fills it in; it removes
  inapplicable SECTIONS, not individual lines, and it invents none. So the golden
  failed a run that did everything right, in both directions at once — twenty
  template-retained lines and the profile's own `dob` and `occupation` scored as
  EXTRA. The golden is the initialized template now, and the test pins the SHAPE
  against that template rather than listing lines: the hand-listed version of it
  named three lines and missed twenty. The removed sections are pinned as an
  explicit set, so a section dropped because filling it in was awkward has to be
  argued for there.

**Seventh round (self-review, the automated reviewer having run out of budget
mid-pass):** two more, both fixed, and the first is the sixth round's fix having
to reach one layer deeper rather than one field further.

- **[P1] An infinite `--tolerance` scored every wrong figure as a match.**
  `float()` accepts `inf`, `Infinity` and `nan`; with any of them a return whose
  tax was **zero** compared clean against the golden and the CLI exited 0. That
  is the under-statement bias AGENTS.md §4 calls a hard error — a harness that
  reports a wrong return as right is worse than no harness — and AGENTS.md §8
  already required `math.isfinite` at every numeric parse boundary. Refused in
  `compare` itself, so the library entry the tests call cannot be handed one, and
  again at the argparse boundary, so the CLI answers with a message rather than a
  traceback.
- **[P1] Identity suppression was still a list of names, and the third list
  missed the third field.** `schedule_d.<part>.<row>.source` holds the 1099-B the
  lot came from, which names a brokerage, and it was on no list — after
  `employer_ein` had been on no list the round before. **The default for TEXT is
  now inverted**: a string is withheld unless its leaf is named as one of the
  strings that are ANSWERS (the filing status, the codes, the limitations
  manifest, the dates). The two directions of drift are not comparable — a new
  template field missing from an identity list discloses, while one missing from
  this list merely prints as "not shown" beside a path that still says which
  field moved — and §1 is absolute where legibility is not. Numbers are
  unaffected, because a figure is what a reader acts on and an identifier wearing
  a number is caught by `redact`. A test sweeps `result_template.json` for every
  text-capable leaf and pins the withheld SET, so a new one forces the decision
  to be made out loud, and pins that no printable name has been left behind by a
  rename.

**Cleanup round (four review agents over the whole of `evals/`, in parallel):**
the reuse, simplification, efficiency and altitude passes agreed on one thing
from four directions, and it retired the previous round's fix.

- **Identity suppression is decided by the SIDE and the TYPE, never by the field
  name.** Three rounds had been lists of names — an identity allowlist, then a
  trailing-token rule, then an inverted default with a list of the printable
  strings — and the altitude pass measured the result: across the golden's 195
  scalar leaves, the three name lists' entire residual effect over the inverted
  default was one leaf holding `null`. The lists could not be moved by any test.
  The distinction that always mattered is which operand a value came from:
  `golden` is a synthetic file in this repository, past the PII gate and swept
  for identifier shapes by a test, so it prints; `actual` is whatever `--result`
  was pointed at, so its strings never print. Numbers print, because a figure is
  what a reader acts on. Sixty names and four constants gone, and it covers
  every field nobody has thought of, which is what all four rounds were reaching
  for. The residual is stated in the module and the README: a string difference
  shows the golden's side only.
- **The fixture's profile was hand-built and had drifted from its own template
  in three ways** — `taxpayers.primary` for the template's top-level `primary`,
  `address.line1` for `address.street`, and an invented `prior_year.agi` /
  `total_tax` pair the schema has no keys for (Step 9.5 reads that composition
  from `answers.md`, which the fixture already writes). Worse, the hand-built
  `flags` block had seven entries and `has_capital_gains` was not one of them,
  while `schedule_d`'s `_include_if` keys on exactly that flag and the golden
  carries the section: the profile described a different case than the golden
  scored. It is the template filled in now, pinned by the mirror of the golden's
  own shape test.
- **The renderer's not-money table is imported, not copied** — the copy had lost
  `tin`, `disposal` and `disposals` in one commit — but the RULE is deliberately
  not shared, and the test that changes the tax year caught the attempt to share
  it: `pdf_render._is_amount` answers "does this print with a dollar sign", and
  its `_AMOUNT_ANYWAY` override calls `tax_year` money, which puts 2026 and 2025
  within the dollar tolerance.
- **The golden's arithmetic is pinned, not only its keys.** Twenty-three of its
  own figures were asserted nowhere, so a hand-typed wrong one there would have
  failed a correct agent — the same "harness measuring itself" the shape test
  fixed, one level down. Every identity added is one any correct return
  satisfies, and each was mutation-checked.
- Smaller, from the same pass: the tolerance is validated once per call rather
  than once per node, and by one predicate behind both the library and the CLI;
  `_show`/`_emit` are one ladder; the templates are read once for the module;
  three of the four fixture builds share one module-scoped workspace; two of the
  three CLI tests drive `_main` in process, leaving one genuine end-to-end spawn.

**Deferred, with the reasoning recorded rather than dropped:** building the
golden from the fixture through the engine (`evals/golden.py build --case`)
instead of maintaining it by hand. The altitude pass makes the case and it is a
good one — the README commits to five more cases, and six hand-filled copies of a
268-line template is where the bill lands. It is deferred to those cases rather
than done here, because the generator's value is in the second golden onwards and
this PR is one task. Also deferred: `_read_json` is a fifth copy of one loader
(`tie_out`, `amend`, `rollover`, `projection` hold the others), which is a
five-site library change and not this PR's.
