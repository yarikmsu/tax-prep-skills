# A filing status that changed between the two years

Step 1 collects ONE prior-year figure 1 and ONE prior-year AGI. Both assume the two years were
filed the same way. When they were not, that pair is either short a return or holding a joint
one that has to be split, and the ordinary procedure quietly does the wrong arithmetic on it.

## Separate last year, joint this year

Add BOTH prior returns' figure 1 together, each netted as figure 1 says. Step 2's factor is then
taken ONCE, against their **COMBINED** prior-year AGI — not once per return. Treas. Reg.
§1.6654-2(e)(3)(i) writes this exact case out: the tax shown on the return for the preceding
taxable year "shall be the sum of both the tax shown on the return of the taxpayer and the tax
shown on the return of the taxpayer's spouse for such preceding year". That makes the prior year
ONE quantity, and §6654(d)(1)(C)(i) has one place to put a factor — it substitutes 110% inside
clause (ii) of §6654(d)(1)(B), and there is a single clause (ii) to substitute in, not one per
spouse. The threshold agrees: $150,000 is the joint-scale figure, halved to $75,000 by
§6654(d)(1)(C)(ii) for a filer who is SEPARATE in the covered year, and this filer is JOINT in
the covered year — so the AGI put against it is the joint-scale one, which is the two added.

**Form 2210's line 8 does not settle this, and reading its paragraph ORDER as though it did is
the trap.** Line 8 tests "the AGI on your 2024 return", singular, and then says separately to
"add your 2024 tax (as figured earlier) to your spouse's 2024 tax (as figured earlier)". Whether
"as figured earlier" reaches back past the 110% sentence or stops at the composition before it is
stated nowhere — not there, not in Pub 505, not in IRM 20.1.3. An ordering is not an instruction,
and no per-return AGI test appears in any of them.

The two readings differ in exactly ONE window: the combined AGI clears $150,000 and **neither**
separate AGI does — two $100,000 AGIs, say. Nowhere else, because a sum is at least as large as
either half. **In that window take the combined reading.** It is the higher of the two, by a
tenth of the whole prior-year tax, and the per-return one is the one that can come out SHORT —
the single direction this skill promises cannot happen. It also closes an arbitrage nothing in
the statute intends: the same two incomes would owe 110% had the couple filed jointly last year
and 100% because they did not. Ask for both AGIs; the sum needs both of them. And where that
window is live, SAY it is — the figure carries a ten per cent band a preparer may read the other
way, and stating it as settled is the error this paragraph replaces.

## Joint last year, separate this year

The joint tax is ALLOCATED, not carried whole and not halved. Treas. Reg. §1.6654-2(e)(1) gives
the separate return "that portion of the tax which bears the same ratio to the whole of the tax
as the amount of the tax for which the taxpayer would have been liable bears to the sum of the
taxes for which the taxpayer and his spouse would have been liable had each spouse filed a
separate return for the preceding taxable year":

```
figure 1 for this filer = joint figure 1 x  (this spouse's hypothetical separate tax)
 -------------------------------------------
                                            (sum of BOTH spouses' hypothetical separate taxes)
```

Both hypothetical figures are computed on the PRIOR year's income, filing separately. **Neither
appears on the joint return**, which is what makes this the one branch that cannot be answered
from the figures step 1 collects.

The AGI test does not split with the tax: §6654(d)(1)(C) reads the AGI shown on the PRECEDING
year's return, and that return is the joint one — so this filer tests the **whole joint AGI
against the $75,000 threshold**, the separate-return one they now file under, not a half share
and not $150,000. That combination is harsh on purpose, and harsh is the safe direction here.

## Before refusing: the figures already collected may settle it anyway

The allocation fraction is this spouse's hypothetical tax over the sum of BOTH, so it cannot
exceed 1. **Figure 1 for this filer is therefore at most the WHOLE joint tax**, on every possible
split. And step 2's factor needs no split at all — it reads the joint AGI, as above. So the
prior-year prong is capped at `factor x whole joint prior-year tax` from figures step 1 already
collected, and the required annual payment is the LESSER of that and the 90% prong, so it is
capped there too.

Then walk the four deadlines with step 4's own arithmetic, the ceiling standing in for the
required annual payment: `ceiling x n / 4` against `expected full-year withholding x n / 4` plus
estimates dated on or before that date. If every deadline is covered, the harbor is met **on
every allocation**, and there is nothing left to compute: say they are inside it, that no split
can change that, and stop. A taxpayer who is provably safe should not be sent away to have their
prior year recomputed twice.

**Scale the ceiling too — both sides carry the same `n / 4`.** The requirement does under
§6654(d)(1)(A) and the withholding under §6654(g)(1)'s ratable rule, so testing each cumulative
payment against the WHOLE annual ceiling asks for January's money in April. Someone with no
withholding paying $5,000 a quarter against a $20,000 ceiling covers every instalment they could
possibly owe on any split; the unscaled test clears them only at the last deadline, having sent
them away at the first three.

This works in ONE direction only. Falling SHORT at any of those deadlines proves nothing — the
true figure 1 is somewhere below the ceiling, so the real requirement is lower and may already be
met. Never quote the gap to a scaled ceiling as a shortfall; it is an upper bound, not a number
anyone owes. Only once the ceiling fails to settle it does the refusal below apply.

## If the two hypothetical figures cannot be had

**Refuse, and route to step 6.** Do not invent the split, and do not reach for either shortcut:

- **Half each** understates whenever the spouses' incomes differed, which is the ordinary case
  and the one direction this skill promises cannot happen.
- **The whole joint tax** is safe in direction and wrong in size — the skill's own licence is
  "too high, never too low", and a figure that overshoots enough gets the whole answer
  disregarded, which is how a reader ends up paying nothing at all.

Recomputing the prior year twice on separate-filing assumptions is a return-preparation task,
not an advisory one. Say that it is what the allocation needs, and hand it over.
