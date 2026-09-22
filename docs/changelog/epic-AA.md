# Epic AA — the 2026-09-20 product-owner review

Backlog: `docs/roadmap/BACKLOG.md`. Epic AA is the seven items a product-owner
review raised on 2026-09-20, the day the roadmap was collapsed to one file. They
are not a feature theme: AA2 and AA3 are process, AA4 and AA5 are test hygiene,
AA6 and AA7 are copy a first public user would hit, and AA1 is the one tax
finding.

---

## AA1 — the Earned Income Credit is disclosed instead of defaulting to zero

### What was wrong

`form_1040.line_27_eic` had a field, no computation and **no capability rule at
all**. That last part is the whole of what was distinctive about it, and a first
draft of this file claimed something stronger and false — that it was the only
manual-worksheet item the engine computes nothing of. Half the registry is
uncomputed: Forms 5695, 8936, 8839, 2555, 4137 and 8938 are all hand-worked.
Every one of them already had a rule. The EIC did not, so its gap reached no
manifest and no taxpayer.

It defaulted to 0 and flowed into line 33 (`return_builder._LINE_33_TERMS`), so
an eligible filer's refund was short by up to the whole credit with every gate
green.

**Nothing downstream could catch it, and that is what made it a P1 rather than a
documentation gap.** The tie-out gate is the mechanism built to see a dropped
figure, and checks A-D work by comparing the parse ledger to the return. A credit
issues no source document, so there is nothing to ledger and nothing to compare —
the one check designed for exactly this failure is structurally blind to it.

The direction is an UNDER-statement, which AGENTS.md §4 says degrades with a loud
banner. What it had instead was **five** prose sites saying the credit is
uncomputed — `result_template.json`'s `_note_line_27`, the tax-and-credits compute
reference, `docs/contributing/tax-gotchas.md`, `return_builder`'s module docstring
and `projection`'s. A first draft of this file counted two, which undercounted the
prior state in the direction that flattered the change. What none of the five was,
is a manifest line: every one is read by an AGENT and none reaches a taxpayer.
`meta.limitations`, which `/tax-generate` prints on page 1, said nothing.

New York needs stating precisely, because the blunt version is also false.
`state-return-ny.md` already told the agent to put each refundable credit's
amount, IT-215 included, on `meta.limitations` verbatim. That is an INSTRUCTION
an agent may skip; a rule is a line the gate emits whether anyone remembers or
not. And there are **three** New York lines, not the two a first draft carried:
IT-201 line 65 (Form IT-215), line 66 (Form IT-209, the noncustodial parent
credit) and line 70 (the NYC credit). `tax_computations/state/ny.py` names the
pair that gets missed in its own comment — "the noncustodial parent EIC and the
New York City EIC" — and the draft had picked up one of the two while asserting
the set was complete.

### What the ruling already said

**The EIC being uncomputed is a RULING, and this did not re-open it.** The
2026-07-12 review sharpened the persona to a CA resident with W-2 plus equity
comp, investments and foreign accounts, and dropped the EITC to P3 as out of
persona in the same breath that promoted AMT to P1. That decision stands, and the
deliverable here was the disclosure, NOT the computation.

What the ruling did not license is the silence. The guiding principle stated in
the paragraph immediately after it is that this toolkit's differentiator is that
it never silently understates, and that where a gap cannot be built the
requirement is to refuse or loudly degrade. A field defaulting to zero with no
gate, no question and no manifest line is neither.

### What shipped

**One flag.** `flags.may_qualify_for_eic` in `taxpayer_profile_template.json`,
asked by the `/tax-setup` interview in Group 8 beside the other credit probes.
The question screens on SHAPE and states no figure: the credit amounts and the
disqualified-income cap index annually, and the childless age band does not but
has moved by statute, so either kind written into an instruction file reads as
current while belonging to whatever year it was typed in. The interview says to
set it TRUE on a MAYBE — a false positive costs one manifest line, a false
negative costs the credit — and says outright that its own screening list is a
prompt rather than the §32 test, since every omission from it biases toward YES.

**One capability rule.** `earned_income_credit`, MANUAL, covering all four lines.
MANUAL and not UNSUPPORTED is the severity decision: an under-statement degrades
loudly rather than blocking, and a worksheet that legitimately comes out at $0 —
investment income over the cap, say — is a CORRECT return that no gate may
refuse. The rule binds each line to its OWN form and says none of them is a copy
of another.

**One tie-out advisory**, and it is TRI-STATE. `True` is the taxpayer's yes.
`False` is their no and ends it, because firing on every return would bleed the
advisory block dry for the PFIC and second-read warnings that share it. `None` —
a profile predating the question — gets a DIFFERENT advisory saying the question
is unanswered, because silence there is not deference to an assertion: there is
no assertion. `unanswered_pfic_event` is the same shape at blocking level; this
stays advisory because the direction is an under-statement.

The advisory lives in the tie-out rather than in the gate that writes the
manifest for a structural reason. `capabilities.py` cannot see the result: it
runs before `result.json` exists, and its receipt input set is
`taxpayer_profile.json` alone (`receipt._INPUTS`), pinned by
`test_a_result_edit_leaves_the_capability_gate_alone`. A gate that read
`result.json` would go stale on every compute write, which is the cry-wolf shape
this repository has a recorded lesson about.

### What review caught

Three reviewers read the first draft. Every finding below was reproduced against
the primary source before being fixed.

- **An OVER-statement path in the advisory's own remedy.** It said "work the
  covered year's worksheet for each line named above (Pub 596 / the 1040
  instructions)" over a list that includes the New York lines. The New York and
  City credits are not the federal credit, so following that literally puts a
  federal figure on three New York lines — and `compute_ny_settlement` sums them
  straight into the refund. §4 calls an over-statement a hard error. The remedy
  now builds its authority list from the lines actually named, and the comment
  above it claimed that scoping before the code did it.
- **The NYC line was named to every New York filer.**
  `refundable_credits_and_payments` carries no `_include_if`; only the sibling
  `nyc` block is gated on `flags.nyc_resident`. Line 70 now has a gate section.
- **A present section with an absent field went silent.** `_result_total` one
  screen up in the same file settles the same question the other way: "a present
  section always yields a number". Form 1040 always has a line 27 — only the JSON
  key is missing — and `return_builder.build` reads it as `working.get(field, 0)`,
  contributing a real $0 to line 33 either way.
- **A negative figure silenced the advisory** (`value == 0`). An EIC is never
  negative. The federal line is refused upstream by `_check_components`; the three
  New York lines are not, and this gate also runs standalone.
- **`_EIC_LINES` was not pinned to `result_template.json`**, and every fixture is
  hand-built, so the constant and the fixtures could have drifted together. The
  convention existed twice in the same test file.
- **Nothing linked the flag name across the two modules.** Renaming it in the
  template and the rule would have left the suite green while the tie-out read a
  key that no longer existed.
- **`projection/__init__.py` still said `result.json` carries no EIC or ACTC
  field** — false since V1's second slice added both. The sibling the prose sweep
  missed.
- **`/tax-compute` Step 12.7's advisory enumeration** was not updated, though
  `/tax-review`'s was.
- The `docs/contributing/tax-gotchas.md` bullet called the EIC "the largest single
  number on the return". At the plateau the credit is a fraction of earned income
  by construction, so line 1z is always larger. Carried-forward wording, re-asserted
  and now corrected.

### What a second review caught

One local Codex pass over the corrected branch, two findings, both reproduced:

- **Deleting the New York credit block silenced the advisory; emptying it did
  not.** The scope check read the IMMEDIATE parent, so a `new_york_it201` with
  no `refundable_credits_and_payments` inside it reported nothing. It now scopes
  by the return's top-level section.
- **A federal NO suppressed the noncustodial parent credit.** Form IT-209 has its
  own eligibility test, not the federal EIC's, so a flag worded as a federal
  screen let a filer who fails the federal test leave line 66 silently at 0. The
  interview now asks for a YES from a New York noncustodial parent paying
  court-ordered support, and the rule's situation names that credit.

A third pass found one more, in the interview's list of what the prompt leaves
out: it stated the more-than-half-year US home requirement as universal. That is
the test WITHOUT a qualifying child; with one, it is the child's residence with
the filer, and a birth or death in the year still counts. Read as written, it
let an agent screen an eligible filer who arrived mid-year with a newborn to NO,
and a NO silences both disclosures. The list now scopes the rule and says none of
it is a reason to answer NO.

### Two things deliberately not done

**Not registered in `answers.REQUIRED_ANSWERS`.** A registered answer BLOCKS
(`answers._main` returns 2 while one is owed) and the interview asks for a TRUE on
a MAYBE. Coupling the two makes the liberal answer the expensive one and teaches
people to answer no, which destroys the screen the rule depends on.

**The tie-out does not reject a non-bool flag the way `capabilities._flag_value`
does.** A reviewer proposed aligning them. Reproduced instead: `capabilities`
exits 1 with `flags.may_qualify_for_eic must be true/false, got str: 'true'` at
Step 0.7, long before the tie-out runs, so the stated failure — both disclosures
vanishing silently — does not occur. Changing `is True` for this one flag while
`has_pfic_taxable_event` beside it keeps it would create the inconsistency the
finding is about. A present non-bool reads as UNANSWERED here, which is the loud
direction.

### What it is not

Not a computation, and not a step towards one. No EIC table, threshold, cap or age
band is recorded anywhere in this change; a test enforces that over the rule, the
advisory and both JSON notes, exempting only `$0`. `/tax-review`'s H3
due-diligence block, which already carried the screening criteria, gains a pointer
to the two runtime signals and the note that their ABSENCE is itself a review
finding: if the H3 checks suggest eligibility and `may_qualify_for_eic` is false,
the screening question was answered wrong.
