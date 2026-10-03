# Washington capital-gains excise — TY2025

No individual wage income tax. This separate excise uses Schedule D line 15
LONG-TERM net gains, never line 16; short-term losses cannot reduce it.
Crypto LT disposals are intangibles allocated by domicile at disposal. Use the
crypto importer's reviewed `washington_allocation` workpaper; ST disposals and
staking/reward receipt income do not enter this base. Federal LT totals already
include the crypto rows: do not add them again.
Full-year WA domicile only. Separate spouses and registered domestic partners
require community/household allocations and refuse. Special federal gains
(section 1256, QOF, other departures under RCW 82.87.020) require a separate
normalized worksheet; this module refuses them.

Record `taxpayer_profile.washington_inputs` with every keyword required by
`compute_wa_capital_gains`; explicit zero/empty-list answers require confirmation.
Set `household_is_separate: false` only after confirming no separately filed
spouse/community allocation is needed, including a married HOH filer. Unknown
or true refuses. Record `registered_domestic_partner` explicitly too.
Ask whether all asset exemptions and allocations were reviewed. Each adjustment
row uses a unique asset/group `id`, `reason: exempt|nonallocated` and signed
`gain_loss` ALREADY INCLUDED in line 15. Excluding a loss adds it back.
Review real estate, retirement assets, condemnation, qualifying farm livestock,
depreciable business assets, timber, fishing privileges and dealership goodwill
under RCW 82.87.050. Do not auto-classify from a payer name. Intangibles follow
domicile at disposal; tangible-property allocation follows RCW 82.87.100.

`excluded_lt_carryforward` includes only pre-2022, nonallocated or exempt LT
carryforwards actually deducted federally. These categories must be disjoint;
their sum cannot exceed the positive magnitude of Schedule D line 14. Never
classify these carryforwards again as asset adjustments. Qualifying family
business deduction requires .070 eligibility; charitable gifts must qualify
under section 170(c) AND be to organizations principally directed/managed in
WA (.080). Review ALL eligible deductions before confirming classifications.
Spouses share ONE deduction, threshold and cap. No federal itemizing election.

Credits require the other return, tax-paid evidence and a same-asset WA tax
allocation worksheet. Each other-jurisdiction row uses `jurisdiction`,
`tax_paid`, `wa_tax_on_same_gains`; the lesser is allowed, nonrefundable.
B&O paid on the same sale credits this excise starting TY2025; earlier years
used the opposite direction and are outside this module. `credits_verified`
confirms eligibility, no overlapping allocation and no estimated-payment
misclassification. Record WA payments separately and confirm extension/payment
history: either triggers filing even when net tax is zero.

Run after the federal Schedule D is complete and the yearly WA block fetched:

```bash
sh agents/lib/run.sh -c '
import json
from agents.lib.workspace import resolve_workspace
from agents.lib.secure_fs import write_secure
from agents.lib.tax_computations import compute_wa_capital_gains
w = resolve_workspace()
p = json.loads((w / "taxpayer_profile.json").read_text())
r = json.loads((w / "result.json").read_text())
law = json.loads((w / ("tax_law_" + str(p["tax_year"]) + ".json")).read_text())
if p["address"]["state"] != "WA" or law["state"]["code"] != "WA" or law["tax_year"] != p["tax_year"]: raise ValueError("WA cache/profile mismatch")
if r["meta"]["tax_year"] != p["tax_year"] or r["meta"]["filing_status"] != p["filing_status"]: raise ValueError("Complete this year/status federal return first")
lt = r["schedule_d"]["part_ii_long_term"]
args = dict(p["washington_inputs"])
args["federal_lt_carryforward_used"] = abs(lt["line_14_prior_year_carryover"])
wa = compute_wa_capital_gains(p["tax_year"], p["filing_status"], lt["line_15_net_long_term"], law["state"], **args)
r["washington_ltcg_excise"] = wa
r["meta"]["limitations"] = list(dict.fromkeys(r["meta"].get("limitations", []) + wa["notes"]))
write_secure(w / "result.json", json.dumps(r, indent=2) + "\n")
'
```

Continue all federal builder/tie-out/receipt gates. Submit separately through
My DOR, electronically with a federal copy. No required quarterly estimates.
Payment remains due at the original April deadline; federal extension alone
does not extend WA. Request separately through My DOR with a federal extension.
If a final federal change affects WA gains or liability, amend WA within
90 days under RCW 82.87.110(8). A generated summary does not submit either return.

Remind WA filers to check the separate
[Working Families Tax Credit application](https://workingfamiliescredit.wa.gov/apply).
Eligibility resembles federal EITC with program-specific differences; ITIN
filers may qualify when they would meet EITC requirements except for the ITIN.
Verify [current eligibility](https://workingfamiliescredit.wa.gov/eligibility),
including residency, age/children and income rules, even if federal EIC is zero
or not computed. Record the application decision in answers.md. WFTC is neither
a federal return line nor an offset against the WA capital-gains excise; do not
invent an amount or imply this toolkit submits the separate application.
