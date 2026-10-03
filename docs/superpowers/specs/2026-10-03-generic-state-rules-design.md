# W10 — generic state rule blocks

The registry and its wiring checks are already shipped. The remaining Epic P
deferrals are reusable `state.modifications` and `state.local` cache shapes.
This change adds those shapes and consumes them in generic state workpapers
and projections. It adds no named state's filing module or city return.

## Decisions before implementation

- Regime: the existing state regime and dedicated module win. These blocks
  cannot turn a no-income-tax or excise regime into an AGI-based return.
- Residency: existing capability gates remain; no mover or nonresident math.
- Starting income: federal AGI, adjusted by confirmed annual worksheet amounts.
  A rule identifies an addition or subtraction; caps, exclusions and eligibility
  are resolved from the fetched instructions before entering its amount.
- Coverage: a fetched block explicitly confirms that its rule list is complete
  for the covered year. An empty confirmed list means no rules; absence retains
  the existing legacy estimate behavior and must never certify conformity.
- Tax tables, recapture, credits and deductions: existing dedicated modules
  retain them. The blocks do not make plain brackets a complete return.
- Local taxes: identify the jurisdiction, base, method, parameters and form.
  Every jurisdiction needs an explicit applicability answer. An applicable
  local tax blocks generic projection and names its manual form; only a
  dedicated module may price it. This prevents combined withholding from
  creating a refund against a missing local liability.
- Parameters: rates/brackets are fetched, never seeded. Modification inputs
  are reviewed worksheet outputs, not embedded expressions or executable code.
- Provenance: each optional block has its own source pin and covered-year
  attestation. Only a complete block write can attest it. Existing scaffold
  output stays unchanged, preserving the auditor's accepted fixture.
- Registrations: no new result section, withholding category, calendar, return
  builder or package form. The existing state registry remains the sole roster.

## Contract

Both blocks carry `tax_year`, `coverage_confirmed: true` and a list. Modification
`rules` contain exactly `id`, `description`, `direction` (`add` or `subtract`).
Amounts must match the rule ids exactly, including explicit zeroes.

Local `jurisdictions` contain exactly `id`, `name`, `form`, `tax_base`, `method`,
`rate`, `brackets`. Bases are `local_taxable_income`, `wages`, or `state_tax`;
methods are `rate`, `brackets`, or `manual`. Inactive parameter fields remain
null/empty; active rates and schedules pass the normal fetched-figure checks.
Ids are unique lower-case identifiers. Unknown keys, duplicate ids, non-finite
amounts, unconfirmed coverage, stale years and missing applicability refuse.

Projection overrides contain full-year `state_modification_amounts` and
`state_local_applicability`. They are not annualized from YTD values. The
adjusted state AGI also feeds the existing CA high-income harbor.

The return skill uses the helpers for generic workpapers, retains its estimate
limitation, and stops on applicable manual local forms. NY, CA, NC and WA keep
their dedicated return paths. Synthetic tests use hand-computed arithmetic and
mutation cases for missing data, local withholding, provenance and year drift.
