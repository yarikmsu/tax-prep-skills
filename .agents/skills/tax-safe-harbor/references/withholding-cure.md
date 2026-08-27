# Route A detail — sizing 4(c) against a PASSED instalment

The second of route A's two figures, read when step 3's `still to pay` has been computed and any
deadline in the covered year has already passed. Skip it only where no deadline has passed yet:
with nothing behind them, the year's gap is the whole of what withholding has to close.

Everything here is the federal test. California substitutes its own cumulative weights on both
sides of it — see `references/california.md`, which states that substitution once for step 4 and
step 5 alike; it applies to this page unchanged.

## The gap this closes

`still to pay` is the YEAR's gap: the requirement less the withholding the year will end with
and the estimates already made. Withholding credits RATABLY — §6654(g)(1) deems an equal part of
it paid on each instalment due date, whenever it was actually taken — so raising it now also
raises what counts at every deadline **already passed**.

Those two facts come apart in one case, and it is not an exotic one. Where a late estimated
payment met the year's total but left an early instalment short, `still to pay` is **zero**
while a passed instalment is still accruing penalty. A 4(c) figure sized from `still to pay`
alone then answers **$0** to the question this skill exists to answer, and the timing check in
step 4 reports a shortfall nothing can undo — when in fact a larger 4(c) undoes it.

## The formula

For each deadline that has already passed:

```
withholding needed to cure deadline n =
    (required by deadline n - estimates credited by deadline n) / cumulative fraction at n

extra needed to cure deadline n =
    that - expected full-year withholding, floored at zero
```

Take the LARGEST result across the passed deadlines, and carry it beside `still to pay` rather
than in place of it.

This is step 4's own cumulative test solved for the withholding instead of the payment. That
test asks whether `W x f + E` reaches `R x f`, where `f` is the cumulative fraction at the
deadline, `W` the year's withholding, `E` the estimates credited by then and `R` the required
annual payment; solving for `W` gives the line above. `f` is `n / 4` federally and California's
30% / 70% / 70% / 100% in step 5 — the same substitution the cumulative test itself takes, which
is why the formula is written in terms of `f` rather than in quarters.

**With no estimates paid the two figures COINCIDE.** `E` is zero at every deadline, so the
formula reduces to `R` — full-year withholding equal to the year's requirement — and the extra
needed is exactly `still to pay`. That is why step 3's worked example shows a single number, and
why this page is easy to leave out: the divergence appears only when estimates arrived LATE,
which is the same condition that leaves an early instalment short.

## Worked example

Required for the year $20,000; expected full-year withholding $8,000; a single $12,000 estimate
paid September 15. Step 3 gives `still to pay` = $20,000 - ($8,000 + $12,000) = **$0**.

| Deadline | Required by then | Credited by then | Short |
|---|---:|---:|---:|
| April 15 (f = 0.25) | $5,000 | $2,000 | $3,000 |
| June 15 (f = 0.50) | $10,000 | $4,000 | $6,000 |
| Sept 15 (f = 0.75) | $15,000 | $18,000 | — |

April has been accruing since April 15 and June since June 15; the September estimate stopped
both from accruing further on the day it landed, and undid neither. To cure April:
`($5,000 - $0) / 0.25 = $20,000` of full-year withholding, **$12,000 more** than the $8,000
expected. June asks the same $20,000. September is already covered. So the figure is **$12,000**
against **$0** from the annual gap alone — and $12,000 spread over the pay periods left is the
4(c) amount that erases both spring shortfalls retroactively.

## Give both figures — do not silently recommend the larger

Curing a passed instalment this way can require withholding far above the year's own
requirement: $32,000 credited against a $20,000 requirement in the example above. **Do not call
the excess a refund.** The requirement is a PENALTY target — the lesser of 90% of this year's tax
and the prior-year prong — and not the tax itself. Where the prior-year prong is the lesser one
it is anchored on a year that may bear no relation to this one, so the actual tax can sit well
above $32,000 and the "excess" be no excess at all. Withholding is a §31 credit against the tax
actually due: it comes back only to the extent it passes that figure, and otherwise it has
settled the April balance early. This skill does not compute the current-year tax, so it cannot
say which, and promising the money back is the under-statement it may not make.

What IS certain is the TIMING, and that is the trade to put to the taxpayer: the money is out of
their hands from the pay period it is withheld until the return is filed, whether it ends up
refunded or spent on the balance. For someone who needs that cash it is the worse trade even
though it is the smaller penalty.

So report both figures, say which instalments each one cures, and leave the choice with the
taxpayer. **This skill does not price the penalty** — §6621 resets the underpayment rate
quarterly and a rate stated here goes stale within months — so the comparison against their own
cost of money is theirs to make and cannot be made for them here. Recommending the larger figure
unasked is the over-statement this skill may not make; omitting it is the under-statement it
may not make either. Both figures, and the reason each exists.

## Where this does not apply

- **No deadline has passed yet.** Nothing is behind, `E` credited by a passed deadline is not a
  question, and `still to pay` is the answer.
- **The covered year has no payroll left.** Route A is closed outright; see step 4's check,
  which comes before this page.
- **Figure 6's date has reached the April 15 after the covered year.** §6654(b)(2) has closed
  every instalment's underpayment period, the penalty is FIXED, and no withholding on any year
  reduces it — **unless §7508A postponed the covered year for this taxpayer**, which a disaster
  declaration covering their counties does as readily as TY2019 and TY2020 did nationally.
  §7508A(a)(2) disregards the postponed period in computing additions to tax and §6654's penalty
  is one; whether that carries to §6654(b)(2)'s endpoint is not settled here. Step 4's paragraph
  on this window is the whole answer, caveat included — give it rather than restating this
  bullet, which states the ordinary case only.
