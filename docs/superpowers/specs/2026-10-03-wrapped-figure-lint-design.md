# X9 — figure lint across Markdown wraps

The old sentence-window regex excluded newlines, so a wrapped legal ceiling
escaped both the ordinary skill registry and the safe-harbor closed registry.
Earlier experiments removed that boundary globally and confused backticked
schema fields with limit words, or lost a real home-sale exclusion.

Normalize whitespace within prose blocks, retaining separate paragraphs, list
entries, headings and table rows. Pair inline backtick runs by exact delimiter
length inside a block. Mask identifier and CLI text at the same width; preserve
currency amounts and K/M suffixes. Money-bearing prose inside code remains
linted. An unmatched delimiter remains ordinary text. This is a bounded prose
lint, not a complete Markdown or tax-law parser.

Use word boundaries and a forty-character sentence window around limit words,
including minimum/floor. Record every dollar amount in a matched clause, so a
joint/separate or single/joint pair cannot hide its second amount. Preserve the
original normalized context for actionable diagnostics. Both registries call
the same detector.

The current corpus retains every legitimate former amount, loses only the
CLI `--max` false alarm, and newly detects the second home-sale exclusion,
separate-filer prior-AGI thresholds and existing estimated-tax floors. These
already-stated statutory amounts receive explicit allowances; the newly
recognized thresholds/floors are pinned to engine constants. No skill acquires
a new tax-law figure.

Ten regression tests cover both directions, multiplier/decimal preservation,
inline/unmatched delimiters, wrapped code, distance and block boundaries, the
real corpus and actual ordinary/safe-harbor gate refusals. The hook's sole
measured-cost block is updated from live collection.
