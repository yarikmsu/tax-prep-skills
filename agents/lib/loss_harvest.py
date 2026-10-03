#!/usr/bin/env python3
"""Tax-loss harvesting scanner for /tax-harvest (backlog #4).

Reads a brokerage `holdings.json` listing open lots and recent transactions,
classifies each losing lot as CLEAR / PARTIAL / FULLY_BLOCKED under §1091 (with
SHARE-LEVEL proration — see below), computes the post-harvest Schedule D netting
against the baseline gains in result.json, and reports the federal + state $
savings of harvesting the allowed losses this year.

Federal savings is computed via the `compute_scenario` keystone (shared with
/tax-scenario and /tax-roth-convert): the harvest is expressed as a signed
capital_gains/other_income delta, the keystone applies the $3K ordinary offset
cap correctly, and the absolute tax at the harvested rung is ANCHORED to the real
result.json line 24 — so the rendered fed_savings is the Δ around the real number.

For WA, confirmed LT excise worksheets are repriced before/after the harvest.
Other state savings is a simplified estimate: state_marginal_rate × (federal_loss_used),
under the v1 assumption that the state taxes capital gains as ordinary income
(true for CA, NJ; near-true for most non-conforming states). Degrades to
"state not modeled" when tax_law.state.brackets is empty.

Wash-sale (§1091) detection — share-level proration (v1.5):
  Per IRC §1091, a replacement buy disallows the loss ONLY on the matched share
  count, not the entire sold lot: `disallowed = (matched_qty/lot_qty) × loss`.
  We aggregate the per-ticker replacement pool once (across all in-window same-
  ticker buys, all accounts in the file pooled because the user controls account
  inclusion), then distribute it across losing lots of that ticker FIFO by
  acquired date (deterministic; matches Schwab/Fidelity default lot accounting).
  When a buy transaction omits 'quantity', the pool is +math.inf for that ticker
  (conservative full-block fallback — sparse data must not silently let losses
  through). Same-ticker is the "substantially identical" proxy; different-CUSIP
  ETF swaps are NOT flagged (caveat).

  The disallowed loss is added to the basis of the replacement shares per §1091,
  which is OUT OF SCOPE for this skill (it affects FUTURE years' Schedule D when
  those replacement shares are sold). Documented in caveats; not modeled in v1.

Never mutates result.json or taxpayer_profile.json. Writes ONLY one
scenarios/<name>.json via the reused scenario.save_scenario guard. Pure compute +
markdown renderer + guarded writer + CLI.
"""

import datetime
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agents.lib.projection import (  # noqa: E402
    ProjectionError,
    _cache_state_code_present_but_invalid,
    _capital_loss_limit,
    _full_status_key,
    _money,
    _none_type_allowed,
    _normalize_state_code,
    _num,
    _read_json,
)
from agents.lib.rollover import (  # noqa: E402
    RolloverError,
    _validate_year_arg,
)
from agents.lib.scenario import (  # noqa: E402
    ScenarioError,
    _net_capital_gains,
    _rate_kwargs,
    _validate_name,
    extract_baseline_inputs,
    medical_floor_caveat,
    medical_floor_may_move,
    read_tax_year,
    refuse_where_the_charity_cap_moves,
    reprice_outcomes,
    schedule_a_caveat,
    salt_cap_may_move,
    salt_phasedown_caveat,
    save_scenario,
)
from agents.lib.tax_computations import (  # noqa: E402
    qdcg_worksheet_applies,
    _coerce_numeric,
    compute_niit,
    compute_scenario,
    state_brackets_for_status,
)
from agents.lib.secure_fs import redact  # noqa: E402
# Reuse the NIIT helpers from /tax-roth-convert so the two anchored-skills agree
# on how baseline NII is defined and how NIIT params are pulled from tax_law.
# Any future change (e.g. adding ST gains to NII to close the v1 simplification)
# propagates to both consumers automatically.
from agents.lib.roth_ladder import (  # noqa: E402
    _net_investment_income,
    _niit_params,
)
from agents.lib.workspace import (  # noqa: E402
    WorkspaceError,
    add_workspace_argument,
    resolve_under_workspace as _resolve_under,
    resolve_workspace,
)

SCHEMA = "tax-harvest/v1"
WASH_SALE_WINDOW_DAYS = 30
LT_THRESHOLD_DAYS = 365  # IRS: holding period MUST EXCEED one year for LT character


class LossHarvestError(Exception):
    """Raised when /tax-harvest cannot safely proceed."""


# --------------------------------------------------------------------------
# Holding-period and wash-sale primitives (pure functions; testable alone)
# --------------------------------------------------------------------------

def derive_holding_period(acquired: datetime.date, as_of: datetime.date
                          ) -> 'Literal["short", "long"]':
    """IRS holding period: long-term iff (as_of - acquired) STRICTLY EXCEEDS 1 year.

    Day-of-year math: 365 days held is short-term; 366 is long. (Leap years would
    push the threshold by a day, but for v1 we use a fixed-day comparison — the
    1-2 days near the boundary are not the harvesting decision driver users care
    about. Documented in caveats.)
    """
    return "long" if (as_of - acquired).days > LT_THRESHOLD_DAYS else "short"


def _parse_txn_date(raw, label: str) -> datetime.date:
    if isinstance(raw, datetime.date):
        return raw
    if not isinstance(raw, str):
        raise LossHarvestError(f"{label} date must be YYYY-MM-DD string, got {raw!r}")
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError as e:
        raise LossHarvestError(f"{label} date {raw!r} is not a valid YYYY-MM-DD: {redact(e)}")


def check_wash_sale(ticker: str, recent_txns: 'list[dict]',
                    as_of: datetime.date) -> 'tuple[float, list[str]]':
    """Aggregate the §1091 replacement-share pool inside ±30 days of `as_of`.

    Returns (total_replacement_qty, reasons). qty == 0 / reasons == [] means no
    wash-sale activity for `ticker`. The pool is SUMMED across all in-window
    same-ticker buys regardless of account (the user controls which accounts to
    include in the file, modeling §1091's spouse + controlled-IRA reach).

    `quantity` semantics on a buy txn:
      - Present and numeric → adds to the pool by that share count.
      - Missing / None → adds math.inf to the pool. Sparse transaction data
        (the user didn't record share counts) MUST NOT silently let losses
        through; an infinite pool full-blocks any lot it touches, matching the
        v1 binary-block fallback. The user fixes this by adding share counts.
      - Bool or non-numeric → hard error.

    §1091 is bilateral: a purchase up to 30 days BEFORE or AFTER the sale poisons
    the matched share count. Caller (build_harvest) does the FIFO matching of
    this pool across losing lots of the ticker; this function is per-ticker.
    """
    target = (ticker or "").strip().upper()
    if not target:
        return 0.0, []
    total_qty = 0.0
    reasons: 'list[str]' = []
    for txn in recent_txns or []:
        if (txn.get("kind") or "buy").lower() != "buy":
            continue
        tk = (txn.get("ticker") or "").strip().upper()
        if tk != target:
            continue
        # Date errors re-raise — a malformed transaction is a data error the user
        # must fix, not a signal to silently drop.
        tdate = _parse_txn_date(txn.get("date"), label=f"transaction {tk}")
        days = abs((tdate - as_of).days)
        if days > WASH_SALE_WINDOW_DAYS:
            continue
        raw_qty = txn.get("quantity")
        if raw_qty is None:
            qty = math.inf
            qty_label = "qty unknown"
        else:
            try:
                q = _coerce_numeric(raw_qty, default=None)
            except TypeError:
                raise LossHarvestError(
                    f"transaction {tk} on {tdate.isoformat()}: "
                    f"quantity must be numeric, got bool")
            if q is None or q < 0:
                raise LossHarvestError(
                    f"transaction {tk} on {tdate.isoformat()}: "
                    f"invalid quantity {raw_qty!r}")
            qty = float(q)
            qty_label = f"qty {qty:g}"
        # Per §1091 ("stock or securities acquired"), a 0-share buy is not an
        # acquisition and not a wash-sale trigger; silently rendering "qty 0"
        # in the blocked-reason text would also be misleading. Skip cleanly.
        if qty == 0:
            continue
        total_qty += qty
        reasons.append(
            f"buy of {tk} on {tdate.isoformat()} ({days}d from sale, {qty_label})")
    return total_qty, reasons


# --------------------------------------------------------------------------
# Holdings parsing
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class HoldingLot:
    id: str
    ticker: str
    account: str
    acquired: datetime.date
    quantity: float
    cost_basis: float
    current_value: float
    unrealized_loss: float    # 0.0 when at a gain; positive when at a loss
    holding_period: 'Literal["short", "long"]'

    # Account type: True for IRA / 401(k) / 403(b) / HSA / Roth-IRA / Roth-401(k)
    # — any tax-deferred or tax-free account. Losses inside these accounts do NOT
    # flow to Schedule D (no §165 capital-loss deduction), so the lot is excluded
    # from `eligible_lot_ids` regardless of allowed_loss. Buy transactions inside
    # tax-advantaged accounts STILL contribute to the §1091 pool per Rev. Rul.
    # 2008-5 (an IRA purchase poisons a taxable sale of substantially identical
    # securities; the disallowed loss in that case is permanently lost — it
    # cannot shift to the IRA's basis). Defaults to False (taxable brokerage).
    tax_advantaged: bool

    # Wash-sale state (populated by _apply_wash_sale_prorate after parse_holdings;
    # parse_holdings emits optimistic defaults: clear / no disallowance).
    # For tax-advantaged lots, the matcher skips proration entirely — there is no
    # deductible loss for §1091 to disallow, so status stays "clear" regardless
    # of replacement-pool signal:
    disallowed_loss: float    # IRC §1091 prorated portion of unrealized_loss
    allowed_loss: float       # = unrealized_loss - disallowed_loss; what's harvestable
    wash_sale_status: 'Literal["clear", "partial", "fully_blocked"]'
    wash_sale_blocked: bool   # back-compat alias: (status == "fully_blocked")
    wash_sale_reason: 'str | None'  # joined human-readable description of in-window buys

    est_value_per_dollar: float  # heuristic for ranking (set later, after rates)
    wash_sale_adjustments: list[dict] = field(default_factory=list)


def _required_str(d: dict, key: str, label: str) -> str:
    v = d.get(key)
    if not isinstance(v, str) or not v.strip():
        raise LossHarvestError(f"{label} requires non-empty string {key!r}, got {v!r}")
    return v.strip()


def _optional_bool(d: dict, key: str, label: str, *, default: bool = False) -> bool:
    """Optional boolean field. Strict: truthy strings ('true', '1', 'yes') are
    REJECTED — a typo like `"tax_advantaged": "yes"` must not silently degrade
    to False (or accidentally True via Python truthiness) on a field whose value
    determines whether a phantom-savings recommendation is emitted."""
    if key not in d or d[key] is None:
        return default
    v = d[key]
    if not isinstance(v, bool):
        raise LossHarvestError(
            f"{label} {key!r} must be a JSON boolean (true/false), got {v!r}")
    return v


def _required_num(d: dict, key: str, label: str, *, allow_negative: bool = False) -> float:
    raw = d.get(key)
    try:
        v = _coerce_numeric(raw, default=None)
    except TypeError:
        raise LossHarvestError(f"{label} {key!r} must be numeric, got bool")
    if v is None:
        raise LossHarvestError(f"{label} {key!r} must be numeric, got {raw!r}")
    if not allow_negative and v < 0:
        raise LossHarvestError(f"{label} {key!r} cannot be negative (got {v})")
    return float(v)


def parse_holdings(data: dict, *, today: datetime.date
                   ) -> 'tuple[list[HoldingLot], list[dict], datetime.date]':
    """Validate + normalize a holdings.json payload.

    Returns (lots_with_metadata, recent_transactions, as_of_date).
    Per-lot work: parses acquired date, derives holding period, computes unrealized
    loss (>= 0). Wash-sale state is set to optimistic defaults (clear / no
    disallowance) here; `_apply_wash_sale_prorate` (called by build_harvest)
    distributes the per-ticker replacement pool FIFO across losing lots.
    Sets `est_value_per_dollar` to 0 here — caller fills it after rate kwargs.
    """
    if not isinstance(data, dict):
        raise LossHarvestError("holdings.json must be a JSON object.")
    lots_raw = data.get("lots")
    if not isinstance(lots_raw, list) or len(lots_raw) == 0:
        raise LossHarvestError("holdings.json must contain at least one lot under 'lots'.")
    as_of_raw = data.get("as_of")
    as_of = _parse_txn_date(as_of_raw, "as_of") if as_of_raw else today

    recent = data.get("recent_transactions") or []
    if not isinstance(recent, list):
        raise LossHarvestError("'recent_transactions' must be a list when present.")
    # Each transaction must be a JSON object — mirrors the per-lot
    # `isinstance(raw, dict)` check below. A non-dict (string, null, list,
    # number) hand-edited into the file would otherwise raise an uncaught
    # AttributeError deep in _apply_wash_sale_prorate when it calls
    # txn.get(...), surfacing as an ugly Python traceback instead of the
    # clean LossHarvestError → "ERROR: ..." path the CLI uses.
    for idx, txn in enumerate(recent):
        if not isinstance(txn, dict):
            raise LossHarvestError(
                f"recent_transactions[{idx}] must be a JSON object, got {txn!r}")

    lots: 'list[HoldingLot]' = []
    seen_ids: set = set()
    for idx, raw in enumerate(lots_raw):
        if not isinstance(raw, dict):
            raise LossHarvestError(f"lot #{idx} must be a JSON object")
        lot_id = _required_str(raw, "id", f"lot #{idx}")
        if lot_id in seen_ids:
            raise LossHarvestError(f"lot id {lot_id!r} must be unique across holdings")
        seen_ids.add(lot_id)
        ticker = _required_str(raw, "ticker", f"lot {lot_id}").upper()
        account = _required_str(raw, "account", f"lot {lot_id}")
        acquired = _parse_txn_date(raw.get("acquired"), f"lot {lot_id} acquired")
        if acquired > as_of:
            raise LossHarvestError(
                f"lot {lot_id} acquired={acquired.isoformat()} is in the future of "
                f"as_of={as_of.isoformat()}; cannot harvest unowned shares")
        quantity = _required_num(raw, "quantity", f"lot {lot_id}")
        # Zero-quantity is a closed/empty lot (e.g., the brokerage export still
        # carries the row after a full sell). It must NOT enter the harvest
        # plan — `unrealized_loss = basis - value` would otherwise recommend
        # selling shares the taxpayer no longer owns and inflate fed_savings.
        # cost_basis=0 (gift / employer match) and current_value=0 (worthless
        # security, §165(g) harvest case) are both legitimate and allowed.
        if quantity <= 0:
            raise LossHarvestError(
                f"lot {lot_id} quantity must be > 0 (got {quantity}); "
                "closed/empty lots cannot be harvested — remove them from holdings.json"
            )
        basis = _required_num(raw, "cost_basis", f"lot {lot_id}")
        value = _required_num(raw, "current_value", f"lot {lot_id}")
        tax_advantaged = _optional_bool(raw, "tax_advantaged", f"lot {lot_id}")
        loss = round(max(0.0, basis - value), 2)
        period = derive_holding_period(acquired, as_of)
        lots.append(HoldingLot(
            id=lot_id, ticker=ticker, account=account, acquired=acquired,
            quantity=quantity, cost_basis=basis, current_value=value,
            unrealized_loss=loss, holding_period=period,
            tax_advantaged=tax_advantaged,
            disallowed_loss=0.0, allowed_loss=loss,
            wash_sale_status="clear", wash_sale_blocked=False, wash_sale_reason=None,
            est_value_per_dollar=0.0,
        ))
    return lots, recent, as_of


def _apply_wash_sale_prorate(
    lots: 'list[HoldingLot]', recent_txns: 'list[dict]', as_of: datetime.date
) -> 'list[HoldingLot]':
    """Distribute each ticker's §1091 replacement pool across losing lots FIFO.

    Returns a NEW list of HoldingLot with `disallowed_loss`, `allowed_loss`,
    `wash_sale_status`, `wash_sale_blocked`, and `wash_sale_reason` populated.

    Algorithm (per IRC §1091, with v1.5 deterministic matching):
      1. For each in-window same-ticker BUY, compute its per-buy contribution to
         the replacement pool — EXCLUDING the buy's own acquisition of harvest
         lots. Per §1091 the shares being sold are not their own replacements:
         the lot's `acquired` date marks WHICH buy txn acquired it. A buy whose
         (date, ticker) matches one or more taxable losing lots contributes only
         its EXCESS quantity (buy.qty − Σ matching harvest-lot qty), clamped at 0.
         Buys that don't match any harvest lot contribute their full quantity.
      2. Sort the losing lots of each ticker by `acquired` ASCENDING (FIFO —
         oldest sold first; matches Schwab/Fidelity default lot accounting).
      3. Walk the pool through the chain: each lot consumes
         `matched = min(remaining_pool, lot.quantity)` shares; the next lot only
         sees the remainder. When the pool exhausts, subsequent lots stay clear.
      4. Per matched lot: `disallowed_loss = (matched/lot.quantity) × loss`.
         Status is `fully_blocked` when matched >= lot.quantity (an inf pool from
         a quantity-less unmatched txn always triggers this), else `partial`.

    Multiple losing lots of the same ticker compete for ONE pool — this prevents
    the v1 bug where two losing TSLA lots each saw the same 5-share TSLA buy and
    were independently fully-blocked (double-counting the disallowance).

    Tax-advantaged-account behavior (Rev. Rul. 2008-5):
      - Buys inside tax-advantaged accounts DO contribute to the replacement
        pool (their qty counts in step 1 like any other buy).
      - Tax-advantaged LOTS are NOT subtracted from buy contributions in step 1
        (they're not deductible losses, so they don't earn an "own acquisition"
        exclusion), and they're excluded from the FIFO match in step 3.

    v1 limitation (documented in caveats):
      - When two taxable losing lots of the same ticker are acquired at
        DIFFERENT in-window dates and BOTH are harvested, IRS treats each as
        partially the other's replacement. Per-buy subtraction does not capture
        this cross-lot effect (each buy is fully consumed by its own matching
        lot). Rare; acceptable v1 simplification.
    """
    # Collect in-window BUY txns per ticker with (date, qty, reason, account).
    # The per-buy iteration is the lot-aware pool computation that check_wash_sale
    # is intentionally bypassed for here (it aggregates qty without preserving
    # per-buy date/account info needed for own-acquisition matching).
    # check_wash_sale stays as the right primitive for non-prorate callers/tests.
    in_window_buys: 'dict[str, list[dict]]' = (
        defaultdict(list))
    for txn_index, txn in enumerate(recent_txns or []):
        if (txn.get("kind") or "buy").lower() != "buy":
            continue
        tk = (txn.get("ticker") or "").strip().upper()
        if not tk:
            continue
        tdate = _parse_txn_date(txn.get("date"), label=f"transaction {tk}")
        days = abs((tdate - as_of).days)
        if days > WASH_SALE_WINDOW_DAYS:
            continue
        raw_qty = txn.get("quantity")
        if raw_qty is None:
            qty = math.inf
            qty_label = "qty unknown"
        else:
            try:
                q = _coerce_numeric(raw_qty, default=None)
            except TypeError:
                raise LossHarvestError(
                    f"transaction {tk} on {tdate.isoformat()}: "
                    f"quantity must be numeric, got bool")
            if q is None or q < 0:
                raise LossHarvestError(
                    f"transaction {tk} on {tdate.isoformat()}: "
                    f"invalid quantity {raw_qty!r}")
            qty = float(q)
            qty_label = f"qty {qty:g}"
        # 0-share buy is not a §1091 "acquired" event (see earlier P2 fix).
        if qty == 0:
            continue
        # Account is part of the self-acquisition match key. Normalize to
        # strip+lower for forgiving comparison across "Schwab Brokerage" vs
        # "schwab brokerage" etc. Empty string = "not specified".
        buy_account = (txn.get("account") or "").strip().lower()
        reason = (f"buy of {tk} on {tdate.isoformat()} "
                  f"({days}d from sale, {qty_label})")
        account_type = txn.get("account_type")
        if account_type not in (None, "taxable", "ira", "roth_ira", "other_tax_advantaged"):
            raise LossHarvestError("replacement account_type must be taxable, ira, "
                                   "roth_ira, other_tax_advantaged, or null")
        owner_is_taxpayer = txn.get("owner_is_taxpayer")
        if owner_is_taxpayer is not None and not isinstance(owner_is_taxpayer, bool):
            raise LossHarvestError("replacement owner_is_taxpayer must be a boolean or null")
        if buy_account and account_type is not None:
            states = {lot.tax_advantaged for lot in lots
                      if (lot.account or "").strip().lower() == buy_account}
            if len(states) > 1 or (states and (account_type != "taxable") not in states):
                raise LossHarvestError("replacement account_type conflicts with its listed account lots")
        basis = txn.get("cost_basis")
        if basis is not None:
            try:
                basis = _coerce_numeric(basis, default=None)
            except TypeError:
                raise LossHarvestError("replacement cost_basis must be numeric") from None
            if basis is None or not math.isfinite(basis) or basis < 0:
                raise LossHarvestError("replacement cost_basis must be finite and non-negative")
        in_window_buys[tk].append({
            "date": tdate, "quantity": qty, "reason": reason, "account": buy_account,
            "index": txn_index, "account_type": account_type, "cost_basis": basis,
            "owner_is_taxpayer": owner_is_taxpayer})

    # Per-ticker: compute lot-aware replacement pool with BUDGET CONSUMPTION
    # across same-key buys. Group buys by (date, account); each group has ONE
    # self-acquisition budget (sum of harvest lot quantities matching that
    # date+account), consumed greedily across buys in the group. Without this,
    # multiple same-day same-account buys (split fills, separate orders)
    # each re-subtract the full lot quantity → cross-buy replacements vanish
    # → fed_savings inflated (Codex P2#10).
    #
    # Match-key gates: account must match (case-insensitive, whitespace-trimmed);
    # only TAXABLE, LOSING lots earn the exclusion (held-at-gain and tax-
    # advantaged lots are replacements when they share the buy's date+account).
    #
    # Inf-qty within a consumption group: when budget > 0, assume the qty-less
    # buy IS (at least) the remaining self-acquisition (contribution 0, budget
    # → 0). When budget is exhausted (prior known-qty buys filled it), the
    # qty-less buy is a genuine unknown-qty replacement → inf contribution.
    # Sort qty-known buys first within each group so the result is
    # order-independent for mixed groups.
    pool_by_ticker: 'dict[str, float]' = {}
    reasons_by_ticker: 'dict[str, list[str]]' = {}
    replacements_by_ticker: dict[str, list[dict]] = {}
    for ticker, buys in in_window_buys.items():
        # Group buys by (date, account) within this ticker. defaultdict's
        # insertion order is preserved (Python 3.7+), so the iteration order
        # tracks input order for stable output.
        by_group: 'dict[tuple[datetime.date, str], list[dict]]' = (
            defaultdict(list))
        for buy in buys:
            by_group[(buy["date"], buy["account"])].append(buy)

        net_pool = 0.0
        kept_reasons: 'list[str]' = []
        kept_buys = []
        for (buy_date, buy_account), group_buys in by_group.items():
            # Budget for THIS (date, account) group, computed ONCE.
            budget = sum(
                l.quantity for l in lots
                if l.ticker == ticker
                and l.acquired == buy_date
                and (l.account or "").strip().lower() == buy_account
                and l.unrealized_loss > 0
                and not l.tax_advantaged
            )
            # qty-known buys first (deterministic for mixed groups). Stable sort
            # preserves input order within each category.
            group_buys.sort(key=lambda b: math.isinf(b["quantity"]))
            for buy in group_buys:
                buy_qty, reason = buy["quantity"], buy["reason"]
                if math.isinf(buy_qty):
                    if budget > 0:
                        # Qty-less buy claims remaining self-acquisition slot.
                        contribution = 0.0
                        budget = 0.0
                    else:
                        # Budget already exhausted by prior known-qty self
                        # buys → this inf is a genuine unknown-qty replacement.
                        contribution = math.inf
                else:
                    matched = min(buy_qty, budget)
                    contribution = buy_qty - matched
                    budget -= matched
                if contribution > 0:
                    net_pool += contribution
                    kept_reasons.append(reason)
                    kept_buys.append({**buy, "remaining": contribution})
        if net_pool > 0:
            pool_by_ticker[ticker] = net_pool
            reasons_by_ticker[ticker] = kept_reasons
            # Pub 550: match replacement purchases in acquisition order.
            # Input order supplies the order of separate fills on the same date.
            replacements_by_ticker[ticker] = sorted(kept_buys, key=lambda b: (b["date"], b["index"]))

    # ONLY taxable losing lots compete for the pool. A tax-advantaged lot has no
    # deductible loss for §1091 to disallow — its wash-sale status is moot — so
    # we leave its disallowed_loss=0, status="clear" and exclude it from harvest
    # downstream via build_harvest's eligible filter (it ends up in the new
    # `tax_advantaged_loss` bucket instead).
    losing_by_ticker: 'dict[str, list[HoldingLot]]' = defaultdict(list)
    for lot in lots:
        if (lot.unrealized_loss > 0
                and not lot.tax_advantaged
                and lot.ticker in pool_by_ticker):
            losing_by_ticker[lot.ticker].append(lot)

    prorated_by_id: 'dict[str, dict]' = {}
    for ticker, ticker_lots in losing_by_ticker.items():
        pool = pool_by_ticker[ticker]
        reasons = reasons_by_ticker[ticker]
        # FIFO match: oldest lot sold first under default lot accounting.
        sorted_lots = sorted(ticker_lots, key=lambda l: l.acquired)
        for lot in sorted_lots:
            if pool <= 0 or lot.quantity <= 0:
                break
            matched = min(pool, lot.quantity)
            ratio = matched / lot.quantity  # safe: lot.quantity > 0 enforced above
            disallowed = round(ratio * lot.unrealized_loss, 2)
            adjustments = []
            shares_left, allocated_shares, allocated_loss = matched, 0.0, 0.0
            for buy in replacements_by_ticker[ticker]:
                if shares_left <= 0:
                    break
                take = min(shares_left, buy["remaining"])
                if take <= 0:
                    continue
                allocated_shares += take
                cumulative = round(disallowed * allocated_shares / matched, 2)
                part_loss = round(cumulative - allocated_loss, 2)
                allocated_loss = cumulative
                known = math.isfinite(buy["quantity"])
                kind = buy["account_type"]
                taxable = known and kind == "taxable"
                # Rev. Rul. 2008-5 covers the seller's OWN IRA. Do not infer
                # that treatment for a spouse's IRA or another retirement regime.
                ira = known and kind in ("ira", "roth_ira") and buy["owner_is_taxpayer"] is True
                before = (round(buy["cost_basis"] * take / buy["quantity"], 2)
                          if taxable and buy["cost_basis"] is not None else None)
                adjustments.append({
                    "replacement_purchase_index": buy["index"],
                    "replacement_acquired": buy["date"].isoformat(),
                    "replacement_account": buy["account"], "account_type": kind,
                    "owner_is_taxpayer": buy["owner_is_taxpayer"],
                    "matched_quantity": take, "disallowed_loss": part_loss,
                    "basis_increase": part_loss if taxable else 0.0 if ira else None,
                    "permanent_loss": part_loss if ira else 0.0 if taxable else None,
                    "matched_basis_before": before,
                    "matched_basis_after": None if before is None else round(before + part_loss, 2),
                    "holding_period_days_added": (as_of - lot.acquired).days if taxable else None,
                    "complete": taxable or ira})
                buy["remaining"] -= take
                shares_left -= take
            # Boundary: matched >= lot.quantity → fully blocked. An inf pool
            # (quantity-less txn) reliably trips this branch.
            status = "fully_blocked" if matched >= lot.quantity else "partial"
            prorated_by_id[lot.id] = {
                "disallowed_loss": disallowed,
                "wash_sale_status": status,
                "wash_sale_reasons": list(reasons),
                "wash_sale_adjustments": adjustments,
            }
            pool -= matched

    enriched: 'list[HoldingLot]' = []
    for lot in lots:
        if lot.id in prorated_by_id:
            d = prorated_by_id[lot.id]
            disallowed = d["disallowed_loss"]
            status = d["wash_sale_status"]
            reason_str = "; ".join(d["wash_sale_reasons"]) or None
            adjustments = d["wash_sale_adjustments"]
        else:
            disallowed = 0.0
            status = "clear"
            reason_str = None
            adjustments = []
        allowed = round(lot.unrealized_loss - disallowed, 2)
        enriched.append(HoldingLot(
            **{**lot.__dict__,
               "disallowed_loss": disallowed,
               "allowed_loss": allowed,
               "wash_sale_status": status,
               "wash_sale_blocked": (status == "fully_blocked"),
               "wash_sale_reason": reason_str,
               "wash_sale_adjustments": adjustments},
        ))
    return enriched


# --------------------------------------------------------------------------
# Rate helpers
# --------------------------------------------------------------------------

def _ordinary_marginal_rate(brackets: list, taxable_income: float) -> float:
    """Return the marginal ordinary-income rate at `taxable_income`.

    Each bracket entry is {"rate", "min", "max"} (max may be None for top bracket).
    """
    if not brackets:
        return 0.0
    for b in brackets:
        bmax = b.get("max")
        if bmax is None or taxable_income < bmax:
            return float(b.get("rate", 0.0))
    return float(brackets[-1].get("rate", 0.0))


def _ltcg_marginal_rate(taxable_income: float, cg_zero: 'float | None',
                        cg_fifteen: 'float | None') -> float:
    """0%, 15%, or 20% based on where taxable income lands."""
    if cg_zero is None or cg_fifteen is None:
        return 0.15  # safe planning default when tax_law incomplete
    if taxable_income <= cg_zero:
        return 0.0
    if taxable_income <= cg_fifteen:
        return 0.15
    return 0.20


def _state_marginal_rate(tax_law: dict, filing_status: 'str | None',
                         agi_proxy: float) -> 'tuple[bool, float | None]':
    """(modeled, rate) — derived from tax_law.state.brackets at `agi_proxy`.

    Returns (False, None) when there are no state brackets — caller renders the
    "state not modeled" banner and reports $0 state savings instead of guessing.
    The taxpayer's state of residence may simply have no income tax (TX, FL, …).

    `state.type == "none"` ALWAYS wins over brackets content (Codex
    round-12 P1, the harvest twin of projection.project_state_tax's same
    fix): round-11 taught tax_law_fetcher.check_freshness to flag a
    type-none cache carrying leftover real brackets as a contradiction, but
    this function reads the cache directly and never calls check_freshness
    — build_harvest's `cache_mismatch` guard (from _resolve_state_no_income
    _tax) is None for a MATCHING code/year, so a `code: "FL", type: "none"`
    cache with leftover CA brackets (copied/partially replaced, never
    stripped) would otherwise reach `_ordinary_marginal_rate` and fabricate
    state harvest savings. Checked FIRST so contradictory brackets can
    never be reached.

    The INVERSE regime contradiction is also rejected (Codex round-15 P2,
    projection.project_state_tax's twin fix): a `code` that IS a
    legitimate no-tax state for this cache's OWN declared year (per
    `_none_type_allowed`) must never be trusted for bracket-based rate
    math regardless of what `type` claims — a
    `code: "FL", type: "graduated_income"` cache with leftover CA brackets
    would otherwise sail past the `type == "none"` guard above (which only
    fires when type actually says "none") and fabricate state harvest
    savings from CA's real brackets under an FL taxpayer.
    """
    state = tax_law.get("state") or {}
    if state.get("type") == "none":
        return False, None
    _regime_code = _normalize_state_code(state.get("code"))
    if _regime_code is not None and _none_type_allowed(
            _regime_code, tax_law.get("tax_year")):
        return False, None
    # TN NEVER gets generic bracket math (Codex round-23 P2, projection.
    # project_state_tax's twin fix): post-repeal (TY2021+) TN is type
    # "none" and already short-circuits via _none_type_allowed above, so a
    # TN-coded cache reaching this point is pre-repeal (or year-corrupt)
    # and carries the Hall-tax rate as brackets. The Hall tax applied ONLY
    # to interest and dividends — applying its rate to harvested capital
    # losses would fabricate state savings that Tennessee law never
    # offered. Pre-repeal TN is UNSUPPORTED (degrade to not-modeled, the
    # safe under-statement direction) rather than mismodeled.
    if _regime_code == "TN":
        return False, None
    # `state.brackets` has two shapes and this reads whichever it is handed:
    # New York keys it by §601 rate SCHEDULE, and a dict is truthy, so the old
    # `or []` gate passed it straight into _ordinary_marginal_rate, where the
    # schedule keys arrive as strings and `b.get` raises AttributeError. See
    # state_brackets_for_status — this is `filing_status`'s FIRST use in this
    # function, which is why the parameter was here unread.
    brackets = state_brackets_for_status(
        state, _full_status_key(filing_status, "state harvest rate"))
    if not brackets:
        return False, None
    # The SCHEDULE's marginal rate, which for a state carrying a benefit
    # recapture is the LOWER of the two answers: inside the phase-in band, and
    # above the top of it, a harvested loss saves more than the schedule says.
    # Under-stating a harvest BENEFIT is the degrade direction (AGENTS.md §4),
    # unlike under-stating a liability, so it stands with the caveat rather
    # than blocking a plan.
    return True, _ordinary_marginal_rate(brackets, agi_proxy)


def state_no_income_tax(tax_law: dict) -> bool:
    """True only when the fetched state block POSITIVELY declares no income
    tax (state.type == "none" — FL). Absent/unknown type returns False so an
    unfetched cache keeps the "not modeled" warning: absence of data must
    never read as absence of tax (bias-direction rule)."""
    state = tax_law.get("state") or {}
    return state.get("type") == "none"


def _resolve_state_no_income_tax(tax_law: dict, resident_code: "str | None",
                                  year: int):
    """Decide the no-income-tax flag, guarded against a STALE cache for a
    different state than the taxpayer's resident state (round-6 P2 — the
    loss_harvest twin of projection._resolve_state_no_income_tax: a leftover
    FL state.type == "none" cache left beside a CA taxpayer's workspace must
    not print "$0 — correct and complete") AND against a cache saved for the
    wrong YEAR (round-9 P2, projection's twin fix: `tax_law_{year}.json` is
    read directly by this module's CLI with no freshness check of its own).

    The year check below is now also enforced EARLIER and HARDER by
    `build_harvest`'s own top-level cache-year guard (Codex round-11 P2,
    right after `read_tax_year`), which raises before this function — or
    `_rate_kwargs`/`compute_scenario` — ever runs, so `build_harvest`
    callers never actually reach the year-mismatch branch here. It stays as
    defense in depth for any other/future caller of this function directly.

    Returns (no_income_tax, mismatch_note):
    - cache's declared `tax_year` is missing, malformed, or != `year`: flag
      False + a loud mismatch note, checked FIRST and applying to EVERY
      branch below — including resident-state-unknown, since that branch
      trusts `cache_says_none` affirmatively with no other cross-check at
      all, and a wrong-year cache can't be trusted to declare "no state
      tax" for THIS year (e.g. a cache actually built for a repeal year
      reused against a pre-repeal year would otherwise mask a real Hall-tax
      obligation).
    - resident state UNKNOWN (no profile / absent address.state) AND year
      matches: flag False, no mismatch note (Codex round-22 P2 — this
      REPLACED the pre-round-22 "legacy cache-only" behavior that mirrored
      state_no_income_tax(tax_law) on trust). "$0 — correct and complete"
      is an affirmative claim about the TAXPAYER's state, and with no
      resident state there is no one to attribute the cache's no-tax
      declaration to — a stale FL cache beside a profile whose address
      block was lost would otherwise print the confident claim for a
      taxpayer who may well live in CA. projection's
      _resolve_state_no_income_tax already degrades quietly here
      (False, None, None); this brings loss_harvest in line. The quiet
      (no-mismatch) degrade is deliberate: absence of an address is not
      evidence of cache corruption, so the banner falls back to the
      ordinary "not modeled" path rather than a fabricated warning.
    - resident known AND year matches AND cache code known AND they DIFFER:
      flag False + a loud mismatch note, regardless of what the mismatched
      cache claims — a cache for the wrong state can't be trusted either
      way.
    - resident known AND year matches AND cache code MATCHES: flag mirrors
      the cache's declared state.type == "none".
    - resident known AND year matches AND cache code ABSENT (round-7 P2):
      flag is False, no mismatch note. A KNOWN resident is fresh evidence a
      code-less cache can't be checked against, so it can no longer affirm
      "$0 — correct and complete" on trust alone — mirrors
      projection._resolve_state_no_income_tax's asymmetry (that function
      requires a resolvable code before the affirmative claim; this brought
      loss_harvest in line with it). The banner falls back to the ordinary
      "not modeled" path instead of either a false mismatch warning or a
      false affirmation. Unknown-resident + code-less cache is unaffected —
      that's the branch above.
    - cache code KNOWN AND cache_says_none AND `type: "none"` isn't a
      regime that code can legitimately carry for `year` (round-10 P2,
      projection's twin fix): flag False + a loud mismatch note,
      regardless of whether the resident state is known — a code-matching
      CA cache with a corrupted `type: "none"` (no brackets to fall back
      on) would otherwise print "$0 — correct and complete" for a state
      that very much taxes capital gains. Checked via `_none_type_allowed`
      (tax_law_fetcher's single source of truth for the code<->regime
      allowlist) BEFORE the resident-unknown branch, since that branch
      would otherwise trust the corrupted claim just as readily as a
      genuine one.
    - `state.code` is PRESENT but does not normalize to a valid 2-letter
      code (Codex round-14 P2, e.g. "FLA"): flag False + a loud mismatch
      note, checked FIRST of all (before even the year check) — this is a
      property of the cache itself and needs no resident/year to compare
      against. `_normalize_state_code` collapses this to the same `None`
      an ABSENT code produces, which the code-mismatch branch above
      treats as "nothing to compare, safe to let brackets through" — a
      CA-bracket cache whose code was corrupted to "FLA" would otherwise
      skip that check (both sides must be non-None) and reach
      `_state_marginal_rate`, fabricating state harvest savings from CA's
      real brackets under a taxpayer whose actual resident state never
      gets compared against anything.
    """
    state_cfg = tax_law.get("state") or {}

    if _cache_state_code_present_but_invalid(state_cfg):
        mismatch = (f"state law cache has a malformed state.code "
                    f"({state_cfg.get('code')!r} — not a valid 2-letter "
                    f"USPS code); re-run /tax-setup; state savings not "
                    f"modeled")
        return False, mismatch

    cache_code = _normalize_state_code(state_cfg.get("code"))
    cache_says_none = state_no_income_tax(tax_law)

    cache_tax_year = tax_law.get("tax_year")
    if isinstance(cache_tax_year, bool) or not isinstance(cache_tax_year, int) \
            or cache_tax_year != year:
        mismatch = (f"state law cache does not declare tax_year {year} "
                    f"(found {cache_tax_year!r}) — re-run /tax-setup; "
                    f"state savings not modeled")
        return False, mismatch

    if cache_code is not None and cache_says_none \
            and not _none_type_allowed(cache_code, year):
        mismatch = (f"state law cache declares type \"none\" for "
                    f"{cache_code}, but {cache_code} is not a recognized "
                    f"no-income-tax state for tax year {year} (cache is "
                    f"corrupted or was hand-edited) — re-run /tax-setup; "
                    f"state savings not modeled")
        return False, mismatch

    # INVERSE regime contradiction (Codex round-15 P2, projection's twin
    # fix): a code that IS a legitimate no-tax state for `year` but is
    # typed something OTHER than "none" while carrying real leftover
    # brackets (e.g. code: "FL", type: "graduated_income") is just as
    # corrupted as the opposite case above — without this, no_income_tax
    # below would silently resolve to False with NO mismatch note
    # (cache_says_none is False), leaving the caller to show a generic
    # "not modeled" banner instead of flagging the actual data corruption.
    # Gated on non-empty brackets — a merely untyped-but-bracket-less cache
    # isn't the dangerous case this guards. Checked before the
    # resident-unknown branch, matching this function's existing ordering
    # for cache-vs-year-vs-regime checks.
    if cache_code is not None and not cache_says_none \
            and _none_type_allowed(cache_code, year) \
            and (state_cfg.get("brackets") or []):
        mismatch = (f"state law cache declares type "
                    f"{state_cfg.get('type')!r} for {cache_code}, but "
                    f"{cache_code} is a recognized no-income-tax state for "
                    f"tax year {year} (cache is corrupted or was "
                    f"hand-edited) — re-run /tax-setup; state savings not "
                    f"modeled")
        return False, mismatch

    if resident_code is None:
        # Round-22 P2: never make the affirmative no-tax claim without a
        # resident state to attribute it to — degrade like projection does.
        return False, None

    if cache_code is not None and cache_code != resident_code:
        mismatch = (f"state law cache is for {cache_code} but the taxpayer "
                    f"resides in {resident_code} — re-run /tax-setup; "
                    f"state savings not modeled")
        return False, mismatch

    no_income_tax = cache_code is not None and cache_says_none
    return no_income_tax, None


# --------------------------------------------------------------------------
# Baseline Schedule D figures
# --------------------------------------------------------------------------

def _baseline_capital_gains(result: dict) -> 'tuple[float, float]':
    """Extract baseline (net_st, net_lt) from result.json.

    Falls back gracefully when Schedule D detail is absent: treats line 7
    combined as net LT (matches scenario.py extract_baseline_inputs).
    """
    sd = result.get("schedule_d")
    if isinstance(sd, dict):
        net_st = _num(((sd.get("part_i_short_term") or {})
                       .get("line_7_net_short_term") or 0), "net_st")
        net_lt = _num(((sd.get("part_ii_long_term") or {})
                       .get("line_15_net_long_term") or 0), "net_lt")
        if net_st or net_lt:
            return net_st, net_lt
        # No detail — fall through to combined
        combined = _num(sd.get("line_16_combined") or 0, "line_16_combined")
        return 0.0, combined
    return 0.0, _num(((result.get("form_1040") or {})
                      .get("line_7_capital_gain") or 0), "line_7")


# --------------------------------------------------------------------------
# Report dataclasses
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class HarvestReport:
    schema: str
    name: str
    tax_year: int
    created: str
    as_of: str
    filing_status: 'str | None'
    anchor: dict  # {"line_24", "baseline_recompute_total"}

    # Lot inventory (every lot, ranked by est value × loss desc within eligible)
    lots: 'list[HoldingLot]'
    eligible_lot_ids: 'list[str]'

    # Aggregates
    st_loss_harvested: float
    lt_loss_harvested: float
    total_loss_harvested: float
    wash_sale_blocked_loss: float
    # Total unrealized loss locked inside tax-advantaged accounts (IRA, 401(k),
    # HSA, etc.) — not deductible on Schedule D under §165, so excluded from
    # `eligible_lot_ids` regardless of allowed_loss. Reported for transparency so
    # the user sees losses in retirement accounts and can consider portfolio
    # rebalancing (no tax mechanism, but a real position-management signal).
    tax_advantaged_loss: float

    # Schedule D netting outcome (signed; gains positive, losses negative)
    baseline_net_st_gain: float
    baseline_net_lt_gain: float
    new_net_st_gain: float
    new_net_lt_gain: float
    capital_loss_used_against_ordinary: float  # the $3K/$1.5K-MFS portion (>= 0)
    capital_loss_carryforward: float           # excess (>= 0)

    # Federal anchored savings (regular tax + NIIT layered on top)
    baseline_fed_tax: float
    harvested_fed_tax: float
    fed_savings: float
    # NIIT (Net Investment Income Tax, §1411) — layered OUTSIDE the keystone in
    # the same pattern /tax-roth-convert uses. compute_scenario deliberately
    # omits NIIT; line_24 already includes baseline NIIT (Schedule 2 line 12 →
    # 1040 line 23 → line 24). We compute baseline & harvested NIIT from the
    # keystone's AGI + the inputs' NII (interest + ord_div + max(0, LT cap gain),
    # same v1 simplification as the Roth ladder — ST gains routed to ordinary
    # income are not added back) and integrate ONLY the *delta* into the anchor.
    baseline_niit: float           # for transparency in JSON / markdown
    harvested_niit: float
    niit_relief: float             # = baseline_niit - harvested_niit (>= 0 for a harvest)
    niit_modeled: bool             # False when no `federal.niit.threshold` in tax_law

    # State savings
    state_modeled: bool
    state_mtr: 'float | None'
    state_savings: float

    total_savings: float

    # Rates surfaced for transparency in the markdown
    fed_ordinary_mtr: float
    fed_ltcg_mtr: float

    # True when tax_law.state.type == "none" (FL, TX, …) — distinguishes
    # "this state has no income tax, $0 is correct" from "state data was
    # never fetched" for the not-modeled banner in render_markdown. Guarded
    # against a stale cache for the WRONG state (round-6 P2) — see
    # _resolve_state_no_income_tax.
    state_no_income_tax: bool = False
    # Set when tax_law.state.code (normalized) and the taxpayer's resident
    # state (taxpayer_profile.json address.state) are BOTH known and
    # DIFFERENT — a stale cache for the wrong state (round-6 P2, the
    # loss_harvest twin of projection.ProjectionReport.state_cache_mismatch).
    # None when there's nothing to warn about (codes match, or one/both
    # sides are unknown).
    state_cache_mismatch: "str | None" = None
    state_excise_unpriced: bool = False
    state_excise_note: "str | None" = None
    # Codex round-23 P2 (the harvest twin of projection.ProjectionReport.
    # nonresident_income_warning, round-8 finding F there): set whenever
    # profile flags.has_nonresident_source_income is True, REGARDLESS of
    # which state path resolved — a resident-state no-tax claim (or a
    # modeled resident-state rate) says nothing about a SEPARATE source
    # state's unmodeled tax on the harvested gains/losses, which can change
    # both the value and the ranking of harvested lots. Advisory read:
    # absent/None flags read False (this module never blocks a harvest
    # over an unanswered screening question).
    nonresident_income_warning: "str | None" = None
    # Set when the state's own cache declares a BENEFIT RECAPTURE — the
    # supplemental tax that claws back the graduated schedule above an income
    # threshold (New York Tax Law §601(d-x) is the one modeled here). Inside
    # the phase-in band and above the top of it, a dollar of harvested loss
    # saves MORE than the schedule's marginal rate, so `state_mtr` is a FLOOR
    # for those taxpayers, not the answer. Said out loud rather than silently
    # improved: under-stating a harvest BENEFIT is the degrade direction, and
    # deriving the true marginal rate needs the recapture worksheet, not a
    # bracket lookup.
    state_rate_floor_note: "str | None" = None
    salt_cap_may_move: bool = True  # gates salt_phasedown_caveat
    medical_floor_may_move: bool = True  # gates medical_floor_caveat
    # AA23: Schedule A re-priced at the harvested AGI, and what that still held.
    schedule_a_repriced: bool = False
    schedule_a_held: "list[str]" = field(default_factory=list)


_STATE_RATE_FLOOR_NOTE = (
    "This state's tax includes a benefit recapture above an income "
    "threshold, so the state rate above is the SCHEDULE's — a floor. A "
    "taxpayer inside or above the recapture band saves more per harvested "
    "dollar than these figures show."
)

_NONRESIDENT_INCOME_WARNING = (
    "Another state's nonresident liability is not modeled — the source "
    "state's tax on harvested gains/losses is missing from these savings "
    "figures and can change both the value and the ranking of harvested "
    "lots."
)


def _nonresident_income_flag(profile: dict) -> bool:
    """Advisory read of flags.has_nonresident_source_income (Codex round-23
    P2, mirrors projection._nonresident_income_flag): absent, None, or a
    non-dict flags block all read as False. Deliberately NOT the
    capabilities gate's fail-closed rule — /tax-harvest is read-only/
    advisory and must never block over an unanswered screening question;
    only an explicit `true` turns the warning on."""
    flags = profile.get("flags")
    if not isinstance(flags, dict):
        return False
    return flags.get("has_nonresident_source_income") is True


# --------------------------------------------------------------------------
# Core build
# --------------------------------------------------------------------------

def _wa_harvest_pricer(result, profile, tax_law, holdings, year, lots, as_of):
    """Return a reviewed excise-difference callable, or an explicit degrade note."""
    from agents.lib.tax_computations import compute_wa_capital_gains
    review = holdings.get("wa_harvest")
    args = profile.get("washington_inputs")
    stored = result.get("washington_ltcg_excise")
    if not isinstance(review, dict) or review.get("deductions_unchanged") is not True:
        return None, "Confirm unchanged WA deductions in holdings.wa_harvest."
    if not isinstance(args, dict) or not isinstance(stored, dict):
        return None, "Run /tax-compute with the reviewed washington_inputs worksheet first."
    taxable = review.get("taxable_lots")
    lt_lots = [l for l in lots if l.allowed_loss > 0 and not l.tax_advantaged
               and l.holding_period == "long"]
    if (not isinstance(taxable, dict) or set(taxable) != {l.id for l in lt_lots}
            or any(type(v) is not bool for v in taxable.values())):
        return None, "Classify every eligible LT lot with a boolean in wa_harvest.taxable_lots."
    if as_of.year != year:
        return None, "WA harvest trade date must be in the computed tax year."
    # The legacy federal planner uses 365 days; WA savings must not call a
    # leap-year anniversary sale long-term. Exact anniversaries are still ST.
    for lot in lt_lots:
        try:
            anniversary = lot.acquired.replace(year=lot.acquired.year + 1)
        except ValueError:  # Feb 29 acquisition, next year is not leap.
            anniversary = lot.acquired.replace(year=lot.acquired.year + 1, day=28)
        if taxable[lot.id] and as_of <= anniversary:
            return None, "Confirm actual long-term holding periods; an anniversary sale is short-term."
    if args.get("other_jurisdiction_credits") or args.get("b_o_tax_paid_on_same_gains"):
        return None, "WA credit allocations must be repriced for the proposed sales; not modeled."
    sd = result.get("schedule_d")
    sd = sd.get("part_ii_long_term") if isinstance(sd, dict) else None
    if not isinstance(sd, dict):
        return None, "WA pricing requires explicit Schedule D line 15; rerun /tax-compute."
    try:
        net_lt = _required_num(sd, "line_15_net_long_term", "Schedule D", allow_negative=True)
        baseline = compute_wa_capital_gains(year, profile.get("filing_status"), net_lt,
                                           tax_law.get("state"), **args)
        for key in ("computed_tax_year", "federal_net_long_term", "adjusted_wa_gain",
                    "standard_deduction", "business_deduction", "charitable_deduction",
                    "taxable_wa_gain", "credits", "total_tax"):
            if type(stored.get(key)) not in (int, float) or stored[key] != baseline[key]:
                return None, "WA baseline differs from the current worksheet/cache; rerun /tax-compute."
        def price(selected):
            loss = sum(l.allowed_loss for l in selected
                       if l.holding_period == "long" and taxable.get(l.id) is True)
            after = compute_wa_capital_gains(year, profile.get("filing_status"), net_lt - loss,
                                            tax_law["state"], **args)
            return round(max(0.0, baseline["total_tax"] - after["total_tax"]), 2)
        # Verify the combined scenario too: a business deduction can exceed the
        # reduced base, which requires its own eligibility worksheet.
        price(lt_lots)
    except (ValueError, TypeError, LossHarvestError):
        return None, "WA worksheet or annual law is incomplete/unsupported; rerun /tax-compute."
    return price, "WA excise repriced from allowed, classified LT losses; ST losses save no WA tax. Deductions held unchanged; no credits."


def build_harvest(result: dict, profile: dict, tax_law: dict, holdings: dict,
                  year: int, *, name: str, today: datetime.date) -> HarvestReport:
    """Compute the harvest plan + anchored tax savings for `holdings` at `as_of`.

    Steps:
      1. Validate result.json is for `year` (reuses scenario.read_tax_year).
      2. Validate the scenario name early (matches /tax-scenario/-roth-convert
         user-experience: name errors should not require a full compute first).
      3. Parse holdings → per-lot loss + holding period + wash-sale flag.
      4. Aggregate ST/LT losses across ELIGIBLE (non-wash-blocked) at-loss lots.
      5. Net against baseline Schedule D gains → new_net_st, new_net_lt.
      6. Express the harvest as a delta to the compute_scenario keystone:
         capital_gains delta (LT preferential change) and other_income delta
         (ST ordinary change), recovered from re-applying _net_capital_gains.
      7. Anchor harvested federal tax to the real line 24.
      8. Estimate state savings as state_mtr × federal_taxable_income_reduction
         (degraded to $0 when state.brackets is empty).
    """
    read_tax_year(result, year)
    _validate_name(name)

    # Cache-year hard-stop guard (Codex round-11 P2, loss_harvest's twin of
    # projection._validate_tax_law_year): a wrong-year tax_law_{year}.json
    # (missing/malformed/mismatched top-level tax_year) must be rejected
    # BEFORE _rate_kwargs/compute_scenario ever consume its federal ordinary
    # brackets, capital-gains thresholds, NIIT inputs, or deductions — the
    # round-9 year check inside _resolve_state_no_income_tax (below) only
    # disables the STATE portion, but by the time it runs `fed_savings` has
    # already been computed off the stale FEDERAL cache, so the report and
    # any saved scenario would recommend trades using a wrong dollar amount
    # while displaying only a state-cache warning.
    cache_tax_year = tax_law.get("tax_year")
    if isinstance(cache_tax_year, bool) or not isinstance(cache_tax_year, int) \
            or cache_tax_year != year:
        raise LossHarvestError(
            f"tax_law_{year}.json does not declare tax_year {year} "
            f"(found {cache_tax_year!r}) — this cache cannot be trusted for "
            f"federal harvest math (wrong brackets/capital-gains thresholds/"
            f"NIIT inputs/deductions). Re-run /tax-setup to fetch a {year} "
            f"cache."
        )

    filing_status = ((result.get("meta", {}) or {}).get("filing_status")
                     or profile.get("filing_status"))
    rate_kwargs = _rate_kwargs(profile, tax_law, filing_status, result)
    base_inputs = extract_baseline_inputs(result)

    # CG-threshold guard, at the ENTRY point rather than inside the keystone:
    # `compute_scenario` now refuses a missing breakpoint itself (X2), but its
    # message names the engine, and a harvest is the one caller where the
    # over-statement direction is fed_savings INFLATED (the opposite of
    # NIIT/IRMAA/state missing-data, which UNDERSTATES and degrades with a
    # banner). A harvest can only DECREASE capital_gains, so an eager check
    # on the baseline is sufficient. The condition is the keystone's own
    # guard over the keystone's own quantity — a net LOSS is not preferential
    # and box 1b is capped at box 1a, the same expression `roth_ladder`
    # spells — so what counts as preferential income cannot drift from it.
    preferential = (max(0.0, _num(base_inputs.get("capital_gains", 0), "capital_gains"))
                    + min(max(0.0, _num(base_inputs.get("qualified_dividends", 0),
                                        "qualified_dividends")),
                          max(0.0, _num(base_inputs.get("ordinary_dividends", 0),
                                        "ordinary_dividends"))))
    try:
        qdcg_worksheet_applies(preferential, rate_kwargs.get("cg_zero_limit"),
                               rate_kwargs.get("cg_fifteen_limit"))
    except ValueError:
        raise LossHarvestError(
            f"tax_law_{year}.json has incomplete federal.capital_gains_rates "
            f"(missing zero_pct_limit or fifteen_pct_limit for filing status "
            f"'{filing_status}'). Preferential income (LT capital gains or "
            "qualified dividends) would be silently priced at ordinary rates, "
            "INFLATING fed_savings. Run /tax-setup to fetch the thresholds, then "
            "re-run /tax-harvest."
        ) from None

    line24 = round(_num(((result.get("form_1040", {}) or {})
                         .get("line_24_total_tax")), "line_24"), 2)

    # Per-lot inventory; wash-sale state populated by the prorate matcher below.
    lots, txns, as_of = parse_holdings(holdings, today=today)
    lots = _apply_wash_sale_prorate(lots, txns, as_of)

    # Rates needed for est_value_per_dollar (ranking) — based on baseline state
    base_out = compute_scenario(base_inputs, {}, **rate_kwargs)
    baseline_recompute = base_out["total_federal_tax"]
    anchor = {"line_24": line24, "baseline_recompute_total": baseline_recompute}

    base_brackets = rate_kwargs["brackets"]
    ord_mtr = _ordinary_marginal_rate(base_brackets, base_out["taxable_income"])
    ltcg_mtr = _ltcg_marginal_rate(base_out["taxable_income"],
                                   rate_kwargs.get("cg_zero_limit"),
                                   rate_kwargs.get("cg_fifteen_limit"))

    # Cross-check the cache's declared state against the taxpayer's resident
    # state BEFORE deriving any state-rate figure (round-7 P1 — round-6 only
    # recorded the mismatch as a warning, but `_state_marginal_rate` and
    # `state_savings` had already consumed the STALE brackets sitting right
    # next to it: an FL resident with a leftover CA cache showed real CA
    # dollars in `total_savings`). On a known mismatch, state is forced
    # unmodeled entirely — no rate, no ranking weight, no savings — rather
    # than trusting a cache that admits it's for the wrong state.
    address = profile.get("address") if isinstance(profile, dict) else None
    resident_state = address.get("state") if isinstance(address, dict) else None
    resident_code = _normalize_state_code(resident_state)
    no_income_tax, cache_mismatch = _resolve_state_no_income_tax(
        tax_law, resident_code, year)

    if cache_mismatch is not None:
        state_modeled, state_mtr = False, None
    else:
        state_modeled, state_mtr = _state_marginal_rate(tax_law, filing_status,
                                                        base_out["taxable_income"])
    state_rate_floor_note = (
        _STATE_RATE_FLOOR_NOTE
        if state_modeled and (tax_law.get("state") or {}).get(
            "tax_table_benefit_recapture")
        else None)
    wa_price, excise_note = None, None
    if resident_code == "WA" and cache_mismatch is None:
        wa_price, excise_note = _wa_harvest_pricer(
            result, profile, tax_law, holdings, year, lots, as_of)
        state_modeled = wa_price is not None
        state_mtr = None
    state_rate_for_ranking = state_mtr or 0.0

    # Fill est_value_per_dollar — ST losses rank above LT losses because they can
    # offset ordinary income at ord_mtr; LT losses offset LT gains at ltcg_mtr
    # (only excess flows to ordinary). Lots with no ALLOWED loss (fully blocked
    # or gains) get 0 so they sink in the ranking.
    enriched: 'list[HoldingLot]' = []
    for lot in lots:
        if lot.allowed_loss <= 0:
            value_per_dollar = 0.0
        else:
            fed_rate = ord_mtr if lot.holding_period == "short" else ltcg_mtr
            wa_rate = (wa_price([lot]) / lot.allowed_loss
                       if wa_price is not None and not lot.tax_advantaged else 0.0)
            value_per_dollar = round(fed_rate + state_rate_for_ranking + wa_rate, 4)
        enriched.append(HoldingLot(
            **{**lot.__dict__, "est_value_per_dollar": value_per_dollar}))

    # Eligible = TAXABLE lots with ANY harvestable loss left after wash-sale
    # proration. Tax-advantaged lots (IRA/401(k)/HSA/Roth) are EXCLUDED even
    # when allowed_loss > 0: a loss inside those accounts is not deductible
    # under §165 and so does not flow to Schedule D, so recommending the sell
    # would be a phantom-savings recommendation. They're routed to
    # `tax_advantaged_loss` for visibility instead. Sort: value_per_dollar ×
    # allowed_loss desc — partial lots rank by their allowable portion only.
    eligible = [l for l in enriched
                if l.allowed_loss > 0 and not l.tax_advantaged]
    eligible.sort(key=lambda l: l.est_value_per_dollar * l.allowed_loss, reverse=True)
    others = [l for l in enriched if l not in eligible]
    sorted_lots = eligible + others

    st_loss = round(sum(l.allowed_loss for l in eligible
                        if l.holding_period == "short"), 2)
    lt_loss = round(sum(l.allowed_loss for l in eligible
                        if l.holding_period == "long"), 2)
    total_loss = round(st_loss + lt_loss, 2)
    # `wash_sale_blocked_loss` is the DISALLOWED portion across taxable lots
    # (partial lots contribute their disallowed slice; fully-blocked lots
    # contribute the whole unrealized loss). Tax-advantaged lots NEVER appear
    # here even with a same-ticker buy in the window — their wash-sale status
    # is left "clear" because their loss isn't deductible to begin with.
    blocked_loss = round(sum(l.disallowed_loss for l in enriched), 2)
    # Total unrealized loss locked in tax-advantaged accounts (not deductible).
    # Reconciliation invariant the renderer relies on:
    #   total_loss + blocked_loss + tax_advantaged_loss == Σ unrealized_loss over all lots
    tax_advantaged_loss = round(sum(l.unrealized_loss for l in enriched
                                    if l.tax_advantaged), 2)

    # Baseline Schedule D
    base_st, base_lt = _baseline_capital_gains(result)
    base_cap_input, base_ord_short = _net_capital_gains(base_st, base_lt)

    # Post-harvest Schedule D
    new_st = round(base_st - st_loss, 2)
    new_lt = round(base_lt - lt_loss, 2)
    new_cap_input, new_ord_short = _net_capital_gains(new_st, new_lt)

    # Loss-cap accounting (signals to the user, separate from the keystone math
    # which applies the cap internally via capital_loss_limit):
    combined = new_st + new_lt
    loss_cap = _capital_loss_limit(filing_status, "harvest")
    if combined < 0:
        used_against_ordinary = min(float(loss_cap), -combined)
        carryforward = round(-combined - used_against_ordinary, 2)
    else:
        used_against_ordinary = 0.0
        carryforward = 0.0

    # Federal anchored recompute via the keystone, expressed as a verbatim delta.
    delta = {
        "capital_gains": round(new_cap_input - base_cap_input, 2),
        "other_income": round(new_ord_short - base_ord_short, 2),
    }

    # NIIT params + baseline NII pulled BEFORE the no-delta short-circuit so
    # they're available for the report regardless of whether a harvest is
    # actually applied (transparency: the user sees baseline NIIT even when
    # there's nothing to harvest).
    niit_threshold, niit_rate = _niit_params(tax_law, filing_status)
    baseline_nii = _net_investment_income(base_inputs)
    baseline_niit = compute_niit(base_out["agi"], baseline_nii,
                                 niit_threshold, rate=niit_rate)
    niit_modeled = niit_threshold is not None

    if delta["capital_gains"] == 0 and delta["other_income"] == 0:
        # Anchor-invariant: no harvest → harvested = line_24 exactly. baseline
        # and harvested NIIT are the same (no marginal effect to add).
        harvested_anchored = line24
        harvested_niit = baseline_niit
        harvested_agi = base_out["agi"]
        harv_held = base_out
        repriced, held_notes = False, []
    else:
        harv_held = compute_scenario(base_inputs, delta, **rate_kwargs)
        # AA23: the federal side on a Schedule A priced at the harvested AGI,
        # where the return recorded what a re-run needs. The state estimate
        # below keeps the held figure: it prices a federal taxable-income
        # change at a STATE rate, and the federal deduction is not the state's.
        (harv_out,), repriced, held_notes = reprice_outcomes(
            result, rate_kwargs, base_out["agi"], [(base_inputs, delta)],
            [harv_held])
        harvested_agi = harv_out["agi"]
        # Layer NIIT OUTSIDE the keystone (which omits it). The DELTA is what
        # the anchored figure consumes; we don't subtract baseline_niit (which
        # would double-count, since line_24 already carries the real baseline
        # NIIT). Mirrors the architectural pattern from /tax-roth-convert.
        harvested_inputs = dict(base_inputs)
        for k, v in delta.items():
            harvested_inputs[k] = round(
                _coerce_numeric(harvested_inputs.get(k, 0), default=0) + v, 2)
        harvested_nii = _net_investment_income(harvested_inputs)
        harvested_niit = compute_niit(harv_out["agi"], harvested_nii,
                                      niit_threshold, rate=niit_rate)
        niit_delta = round(harvested_niit - baseline_niit, 2)  # <= 0 for a harvest
        regular_tax_delta = harv_out["total_federal_tax"] - baseline_recompute
        harvested_anchored = round(line24 + regular_tax_delta + niit_delta, 2)

    fed_savings = round(line24 - harvested_anchored, 2)
    niit_relief = round(baseline_niit - harvested_niit, 2)

    # State savings — simplified: state taxes (most) capital gains as ordinary,
    # so the state taxable-income reduction ≈ federal cap-gain AGI reduction,
    # priced at the state ordinary marginal rate. Falls back to the keystone's
    # full taxable-income delta when available (cleaner second-order treatment).
    if wa_price is not None:
        state_savings = wa_price(eligible)
    elif state_modeled:
        if delta["capital_gains"] == 0 and delta["other_income"] == 0:
            ti_reduction = 0.0
        else:
            ti_reduction = round(base_out["taxable_income"] - harv_held["taxable_income"], 2)
        state_savings = round(max(0.0, ti_reduction) * (state_mtr or 0.0), 2)
    else:
        state_savings = 0.0

    refuse_where_the_charity_cap_moves(result, base_out["agi"], [harvested_agi], repriced=repriced)
    return HarvestReport(
        schema=SCHEMA, name=name, tax_year=year, created=today.isoformat(),
        as_of=as_of.isoformat(), filing_status=filing_status, anchor=anchor,
        lots=sorted_lots, eligible_lot_ids=[l.id for l in eligible],
        st_loss_harvested=st_loss, lt_loss_harvested=lt_loss,
        total_loss_harvested=total_loss, wash_sale_blocked_loss=blocked_loss,
        tax_advantaged_loss=tax_advantaged_loss,
        baseline_net_st_gain=round(base_st, 2),
        baseline_net_lt_gain=round(base_lt, 2),
        new_net_st_gain=round(new_st, 2),
        new_net_lt_gain=round(new_lt, 2),
        capital_loss_used_against_ordinary=round(used_against_ordinary, 2),
        capital_loss_carryforward=round(carryforward, 2),
        baseline_fed_tax=line24, harvested_fed_tax=harvested_anchored,
        fed_savings=fed_savings,
        baseline_niit=baseline_niit, harvested_niit=harvested_niit,
        niit_relief=niit_relief, niit_modeled=niit_modeled,
        state_modeled=state_modeled, state_mtr=state_mtr,
        state_savings=state_savings,
        state_rate_floor_note=state_rate_floor_note,
        state_excise_unpriced=((tax_law.get("state") or {}).get("code") == "WA" and wa_price is None),
        state_excise_note=excise_note,
        total_savings=round(fed_savings + state_savings, 2),
        fed_ordinary_mtr=ord_mtr, fed_ltcg_mtr=ltcg_mtr,
        state_no_income_tax=no_income_tax,
        state_cache_mismatch=cache_mismatch,
        nonresident_income_warning=(
            _NONRESIDENT_INCOME_WARNING if _nonresident_income_flag(profile)
            else None),
        salt_cap_may_move=(not repriced and salt_cap_may_move(
            result, tax_law, year, filing_status, base_out["agi"],
            [harvested_agi])),
        medical_floor_may_move=(not repriced and medical_floor_may_move(
            result, base_out["agi"], [harvested_agi])),
        schedule_a_repriced=repriced, schedule_a_held=held_notes,
    )


# --------------------------------------------------------------------------
# Serialization + rendering
# --------------------------------------------------------------------------

def _lot_to_dict(l: HoldingLot) -> dict:
    d = dict(l.__dict__)
    d["acquired"] = l.acquired.isoformat()
    return d


def _report_to_dict(r: HarvestReport) -> dict:
    return {
        "schema": r.schema, "name": r.name, "tax_year": r.tax_year,
        "created": r.created, "as_of": r.as_of, "filing_status": r.filing_status,
        "anchor": r.anchor,
        "lots": [_lot_to_dict(l) for l in r.lots],
        "eligible_lot_ids": r.eligible_lot_ids,
        "st_loss_harvested": r.st_loss_harvested,
        "lt_loss_harvested": r.lt_loss_harvested,
        "total_loss_harvested": r.total_loss_harvested,
        "wash_sale_blocked_loss": r.wash_sale_blocked_loss,
        "tax_advantaged_loss": r.tax_advantaged_loss,
        "baseline_net_st_gain": r.baseline_net_st_gain,
        "baseline_net_lt_gain": r.baseline_net_lt_gain,
        "new_net_st_gain": r.new_net_st_gain,
        "new_net_lt_gain": r.new_net_lt_gain,
        "capital_loss_used_against_ordinary": r.capital_loss_used_against_ordinary,
        "capital_loss_carryforward": r.capital_loss_carryforward,
        "baseline_fed_tax": r.baseline_fed_tax,
        "harvested_fed_tax": r.harvested_fed_tax,
        "fed_savings": r.fed_savings,
        "baseline_niit": r.baseline_niit, "harvested_niit": r.harvested_niit,
        "niit_relief": r.niit_relief, "niit_modeled": r.niit_modeled,
        "state_modeled": r.state_modeled, "state_mtr": r.state_mtr,
        "state_savings": r.state_savings, "total_savings": r.total_savings,
        "state_rate_floor_note": r.state_rate_floor_note,
        "state_excise_unpriced": r.state_excise_unpriced,
        "state_excise_note": r.state_excise_note,
        "fed_ordinary_mtr": r.fed_ordinary_mtr, "fed_ltcg_mtr": r.fed_ltcg_mtr,
        "state_no_income_tax": r.state_no_income_tax,
        "state_cache_mismatch": r.state_cache_mismatch,
        "nonresident_income_warning": r.nonresident_income_warning,
        "salt_cap_may_move": r.salt_cap_may_move,
        "medical_floor_may_move": r.medical_floor_may_move,
        "schedule_a_repriced": r.schedule_a_repriced,
        "schedule_a_held": r.schedule_a_held,
    }


def _pct(v) -> str:
    return "—" if v is None else f"{v:.1%}"


def render_markdown(r: HarvestReport) -> str:
    lines = [f"# Tax-Loss Harvest \"{r.name}\" — TY{r.tax_year}", ""]

    lines += ["## What this models", "",
              f"- As of: {r.as_of}",
              f"- Filing status: {r.filing_status or 'unknown'}",
              f"- Federal ordinary marginal rate: **{_pct(r.fed_ordinary_mtr)}**; "
              f"LTCG rate at current income: **{_pct(r.fed_ltcg_mtr)}**",
              f"- Federal tax at each plan is **anchored** to your real "
              f"`result.json` line 24 ({_money(r.baseline_fed_tax)}); only the "
              "marginal effect of harvesting is computed.", ""]

    if r.nonresident_income_warning:
        lines += [f"> **⚠ Nonresident income exposure:** "
                  f"{r.nonresident_income_warning}", ""]
    if r.state_rate_floor_note:
        lines += [f"> **State rate is a floor:** {r.state_rate_floor_note}", ""]
    if r.state_cache_mismatch:
        # A known cache-vs-resident mismatch always wins over any stale claim
        # the cache makes (mirrors /tax-project's precedence rule) — never
        # print "$0 — correct and complete" for the wrong state's cache.
        lines += [f"> **⚠ State cache mismatch:** {r.state_cache_mismatch}.", ""]
    elif not r.state_modeled:
        if r.state_no_income_tax and r.nonresident_income_warning:
            # Codex round-23 P2: with nonresident source-state exposure the
            # resident-state no-tax fact still holds, but "complete" does
            # NOT — the source state's unmodeled tax can change the value
            # and ranking of harvested lots.
            lines += ["> **State savings: $0 for the RESIDENT state (no "
                      "individual income tax) — NOT the complete picture.** "
                      "`flags.has_nonresident_source_income` is set: another "
                      "state's tax on this income is not modeled here, so "
                      "the fed_savings figure alone understates what a "
                      "harvest decision should weigh.", ""]
        elif r.state_excise_unpriced:
            lines += ["> **WA excise savings not modeled:** there is no individual wage income tax. Only qualifying long-term gains/losses affect the separate excise; this scanner reports federal savings and does not price WA excise savings.", ""]
        elif r.state_no_income_tax:
            lines += ["> **State savings: $0 — correct and complete.** This "
                      "state levies no individual income tax "
                      "(`tax_law.state.type = \"none\"`), so there are no "
                      "state-side savings to model; the fed_savings figure "
                      "is the complete picture.", ""]
        else:
            lines += ["> **State savings not modeled** — `tax_law.state.brackets` is empty "
                      "(no income tax, or `/tax-setup` did not fetch state brackets). The "
                      "fed_savings figure is correct, but a state-tax taxpayer is leaving "
                      "additional savings on the table. Run `/tax-setup` to fetch them.", ""]
    if r.state_excise_note:
        lines += [f"> **WA excise:** {r.state_excise_note}", ""]
    if not r.niit_modeled:
        lines += [f"> **⚠ NIIT NOT MODELED** — no `federal.niit.threshold` in "
                  f"`tax_law_{r.tax_year}.json`. The 3.8% surtax (§1411) is **excluded "
                  "from fed_savings**; a high-income taxpayer above the NIIT threshold "
                  "is missing up to 3.8% × harvested loss of additional relief. Run "
                  "`/tax-setup` to fetch the threshold.", ""]

    # Reconciliation invariant (see build_harvest):
    #   total_loss_harvested + wash_sale_blocked_loss + tax_advantaged_loss
    #   = Σ unrealized_loss across every lot. The "Total unrealized losses"
    #   row in the bottom-line table makes this visible to the user so they
    #   can trace every dollar from holdings to a category.
    total_unrealized = round(r.total_loss_harvested + r.wash_sale_blocked_loss
                             + r.tax_advantaged_loss, 2)
    lines += ["## Bottom line", "",
              "| Item | Amount |", "|---|---:|",
              f"| Total unrealized losses (all lots) | "
              f"{_money(total_unrealized)} |",
              f"| Wash-sale disallowed (§1091) | {_money(r.wash_sale_blocked_loss)} |",
              f"| Tax-advantaged accounts (not deductible) | "
              f"{_money(r.tax_advantaged_loss)} |",
              f"| **Eligible to harvest this year** | "
              f"**{_money(r.total_loss_harvested)}** |",
              f"|   of which short-term | {_money(r.st_loss_harvested)} |",
              f"|   of which long-term | {_money(r.lt_loss_harvested)} |"]
    # When NIIT applies (modeled + the harvest actually moves the surtax), show
    # the regular-tax-relief vs NIIT-relief breakdown so the user sees exactly
    # where the savings came from. When NIIT_relief is $0 (below threshold or
    # not modeled), collapse to the single `Federal tax savings` row to keep
    # the table tight.
    regular_tax_relief = round(r.fed_savings - r.niit_relief, 2)
    if r.niit_modeled and r.niit_relief != 0:
        lines += [
            f"| Federal regular-tax relief | {_money(regular_tax_relief)} |",
            f"| Federal NIIT relief (§1411, 3.8%) | {_money(r.niit_relief)} |",
            f"| **Federal tax savings (total)** | **{_money(r.fed_savings)}** |",
        ]
    else:
        lines.append(f"| Federal tax savings | **{_money(r.fed_savings)}** |")
    if r.state_modeled:
        state_savings_suffix = ""
    elif r.state_no_income_tax:
        state_savings_suffix = " (no state income tax)"
    else:
        state_savings_suffix = " (not modeled)"
    lines += [f"| State tax savings (est.) | "
              f"{_money(r.state_savings)}"
              f"{state_savings_suffix} |",
              f"| **Total savings** | **{_money(r.total_savings)}** |",
              f"| Capital-loss carryforward to next year | "
              f"{_money(r.capital_loss_carryforward)} |", ""]

    lines += ["## Schedule D after harvest", "",
              "| | Baseline | After harvest |", "|---|---:|---:|",
              f"| Net short-term gain/(loss) | "
              f"{_money(r.baseline_net_st_gain)} | {_money(r.new_net_st_gain)} |",
              f"| Net long-term gain/(loss) | "
              f"{_money(r.baseline_net_lt_gain)} | {_money(r.new_net_lt_gain)} |",
              f"| Used against ordinary income (capped) | $0 | "
              f"{_money(r.capital_loss_used_against_ordinary)} |", ""]

    # Eligible lots — the harvest plan, ranked. Partial-block lots appear here
    # (with their allowed/disallowed split) because their allowed portion IS
    # harvestable; only fully-blocked lots go to the wash-sale section.
    lines += ["## Harvest plan (eligible lots, ranked)", ""]
    eligible = [l for l in r.lots if l.id in r.eligible_lot_ids]
    if not eligible:
        lines.append("- No eligible losing lots — nothing to harvest.")
    else:
        lines += ["| Lot | Ticker | Account | Held | Allowed loss | "
                  "Disallowed (§1091) | Est. $ saved/$ |",
                  "|---|---|---|---|---:|---:|---:|"]
        for l in eligible:
            disallowed_cell = (_money(l.disallowed_loss)
                               if l.disallowed_loss > 0
                               else "—")
            lines.append(
                f"| {l.id} | {l.ticker} | {l.account} | "
                f"{l.holding_period.upper()} | {_money(l.allowed_loss)} | "
                f"{disallowed_cell} | {_pct(l.est_value_per_dollar)} |")
        # Partial-block footnote — only render when there's anything partial to footnote.
        partials = [l for l in eligible if l.wash_sale_status == "partial"]
        if partials:
            lines += ["",
                      "_**Partial wash-sale blocks** (lots with `Disallowed` populated): "
                      "a same-ticker buy in the ±30-day §1091 window matches part of the "
                      "sold quantity. Per IRC §1091, only the matched share count is "
                      "disallowed; the rest of the loss is fully harvestable. The disallowed "
                      "portion may shift to taxable replacement basis; an IRA/Roth replacement "
                      "loses it permanently. See the replacement basis plan._"]
    lines.append("")

    adjustments = [(lot, a) for lot in r.lots for a in lot.wash_sale_adjustments]
    if adjustments:
        lines += ["## Wash-sale replacement basis plan", "",
                  "If the full losing lots are sold as priced, these adjustments apply "
                  "to matched replacement shares. They add no current-year savings. "
                  "The source holdings remain unchanged; reconcile the plan with "
                  "broker records after the actual trades.", "",
                  "| Sold lot | Replacement date | Account | Shares | Basis increase | Permanent loss |",
                  "|---|---|---|---:|---:|---:|"]
        for lot, a in adjustments:
            increase = "Unresolved" if a["basis_increase"] is None else _money(a["basis_increase"])
            permanent = "Unresolved" if a["permanent_loss"] is None else _money(a["permanent_loss"])
            lines.append(f"| {lot.id} | {a['replacement_acquired']} | {a['replacement_account']} | "
                         f"{a['matched_quantity']:g} | {increase} | {permanent} |")
        if any(not a["complete"] for _, a in adjustments):
            lines += ["", "> **⚠ Replacement basis is unresolved:** supply actual purchase quantities "
                      "and account types, and confirm ownership of IRA replacements. "
                      "Other tax-advantaged accounts require manual treatment."]
        lines.append("")

    # Wash-sale blocked lots — ONLY fully blocked (status == "fully_blocked").
    # Partial-block lots are in the eligible table above with a disallowance row.
    blocked = [l for l in r.lots if l.wash_sale_status == "fully_blocked"]
    if blocked:
        lines += ["## Fully wash-sale blocked lots", "",
                  "_These lots have a same-ticker replacement buy of at least the lot's "
                  "share count in the ±30-day §1091 window — 100% of the loss is "
                  "disallowed this year. Wait 31 days past the last/next buy, or harvest "
                  "other lots instead._", "",
                  "_**For taxable replacements, the disallowed loss is DEFERRED, not lost** (§1091(d)): it is added "
                  "to the cost basis of the replacement shares, and the original holding "
                  "period tacks on (§1223), so the tax benefit is recovered when those "
                  "replacement shares are later sold in a non-wash transaction. This "
                  "basis plan above records the adjustment where its inputs are known. "
                  "Future-year tax savings depend on later trades. **Exception:** if the replacement was bought inside an "
                  "seller's own IRA/Roth (Rev. Rul. 2008-5), the loss is PERMANENTLY lost — an IRA has "
                  "no adjustable basis to absorb it._", "",
                  "| Lot | Ticker | Account | Loss | Blocked by |",
                  "|---|---|---|---:|---|"]
        for l in blocked:
            lines.append(
                f"| {l.id} | {l.ticker} | {l.account} | "
                f"{_money(l.unrealized_loss)} | {l.wash_sale_reason or '—'} |")
        lines.append("")

    # Tax-advantaged account losses — listed separately so the user sees them
    # but knows the skill is NOT recommending a sell-for-tax-savings (no §165
    # capital-loss deduction inside IRA/401(k)/HSA/Roth). Surfacing them as
    # a discrete section is more honest than silently dropping the lot.
    tax_adv = [l for l in r.lots
               if l.tax_advantaged and l.unrealized_loss > 0]
    if tax_adv:
        lines += ["## Tax-advantaged account positions (no deductible loss)", "",
                  "_These positions are at an unrealized loss inside a tax-advantaged "
                  "account (IRA, 401(k), HSA, Roth). §165 does not allow a capital-loss "
                  "deduction for sales inside such accounts, so harvesting here would "
                  "yield $0 in tax savings. Listed for portfolio-management visibility "
                  "only. Per Rev. Rul. 2008-5, buys in these accounts STILL trigger "
                  "§1091 on harvests in your taxable accounts._", "",
                  "| Lot | Ticker | Account | Held | Unrealized loss |",
                  "|---|---|---|---|---:|"]
        for l in tax_adv:
            lines.append(
                f"| {l.id} | {l.ticker} | {l.account} | "
                f"{l.holding_period.upper()} | {_money(l.unrealized_loss)} |")
        lines.append("")

    # Gain lots — for visibility (no action recommended this year)
    gains = [l for l in r.lots if l.unrealized_loss == 0]
    if gains:
        lines += ["## Positions at a gain (no harvest action)", "",
                  "| Lot | Ticker | Account | Held | Unrealized gain |",
                  "|---|---|---|---|---:|"]
        for l in gains:
            unr_gain = round(l.current_value - l.cost_basis, 2)
            lines.append(
                f"| {l.id} | {l.ticker} | {l.account} | "
                f"{l.holding_period.upper()} | {_money(unr_gain)} |")
        lines.append("")

    lines += ["## Caveats",
              "- **Wash sale §1091 is bilateral and SHARE-PRORATED.** A buy of N "
              "replacement shares in the ±30-day window disallows the loss on only "
              "those N matched shares (not the entire lot). Multiple losing lots of the "
              "same ticker compete for ONE replacement pool, matched FIFO by acquired "
              "date (matches Schwab/Fidelity default lot accounting).",
              "- **A lot's own acquisition is NOT a replacement for itself** — match key "
              "is `(date, ticker, account)`. When a buy transaction's date AND ticker AND "
              "account match a harvest lot's `acquired` date, ticker, and account, the "
              "lot's quantity is subtracted from that buy's contribution to the pool. "
              "Only the EXCESS (if any) counts as genuine §1091 replacement. A "
              "same-ticker, same-day buy in a DIFFERENT account (spouse, IRA, controlled "
              "trust) is a genuine cross-account replacement per Rev. Rul. 2008-5 + "
              "spousal-account rules and correctly blocks the lot. Account comparison is "
              "case-insensitive and whitespace-trimmed (\"Schwab Brokerage\" matches "
              "\"  schwab brokerage  \"). Buy txns WITHOUT an `account` field cannot earn "
              "self-exclusion (the lot's account is always populated per schema), so the "
              "buy is treated as a genuine replacement — populate `account` on every buy "
              "txn (brokerage CSVs always have it) for the textbook simple-case "
              "self-exclusion. Quantity-less buys matching `(date, ticker, account)` are "
              "assumed to BE that lot's acquisition (contribution 0); quantity-less buys "
              "NOT matching any harvest lot (different date, ticker, OR account) stay "
              "treated as full-block (conservative — add share counts for precise "
              "proration).",
              "- **Same-key buys share a SINGLE self-acquisition budget** that's consumed "
              "across them collectively. Split fills, separate same-day orders, and "
              "execution chunks that all share `(date, ticker, account)` collectively "
              "match the matching lot quantity ONCE — not once per buy row. Excess "
              "across the entire group is the genuine §1091 replacement. Within a mixed "
              "group of qty-known and qty-less buys, qty-known buys consume the budget "
              "first; a qty-less buy with remaining budget assumes the slot "
              "(contribution 0), a qty-less buy with no remaining budget is a genuine "
              "unknown-qty replacement (full block). The result is order-independent "
              "(qty-known buys sort first).",
              "- **Cross-lot wash sales between multiple harvested same-ticker lots** "
              "acquired at DIFFERENT in-window dates are not fully captured in v1 — "
              "each buy is consumed by its own matching lot, so IRS's "
              "each-lot-is-the-other's-replacement effect is under-counted. Rare; "
              "documented v1 simplification.",
              "- **Replacement basis is a plan, not a current-year deduction.** Known "
              "taxable replacements receive the disallowed loss and the sold lot's "
              "holding period; the seller's own IRA/Roth-IRA permanently loses that loss. "
              "Unknown quantities or account types remain unresolved. Future-year "
              "Schedule D tax and chained wash sales are not priced here.",
              "- After harvesting, do NOT buy the same security (in any pooled account, "
              "including IRAs and your spouse's accounts) for 31 days.",
              "- **Tax-advantaged-account lots are excluded from harvest** (`tax_advantaged: "
              "true` per lot in `holdings.json`). §165 does not allow Schedule D capital "
              "losses for sales inside IRA/401(k)/HSA/Roth accounts — harvesting there yields "
              "$0 tax savings. However, per **Rev. Rul. 2008-5**, BUY transactions in those "
              "accounts STILL trigger §1091 on harvests in your taxable accounts, and the "
              "disallowed loss in that case is **permanently lost** (the IRA has no basis to "
              "absorb it). Include IRA accounts in `holdings.json` so the matching pool is "
              "correct, but mark the lots `tax_advantaged: true`.",
              "- **Same-ticker is the v1 'substantially identical' proxy.** Swapping into "
              "a different-CUSIP ETF tracking the same index (the very strategy users "
              "want) is NOT flagged here — but the IRS has no bright-line rule on this; "
              "consult guidance for your situation.",
              "- **Accounts are pooled across the holdings file.** The user controls which "
              "accounts to include — to model §1091's spouse/IRA reach, include those "
              "accounts' transactions.",
              "- **Federal only on the anchored side.** State savings is a simplified "
              "estimate (state ordinary marginal rate × federal taxable-income reduction); "
              "states with preferential capital-gains treatment or non-conformity may "
              "differ. Degrades to $0 when state brackets aren't fetched.",
              "- **Holding period uses fixed 365-day comparison** (>365 = long). Leap-year "
              "boundary cases within 1–2 days of one year may differ from a calendar-month "
              "calculation.",
              "- **All dates are TRADE dates, not settlement dates** (per IRS Pub. 550). "
              "Gain/loss on securities traded on an established market is recognized in "
              "the tax year of the trade date, regardless of T+2 settlement timing — a "
              "trade executed on Dec 31 counts for the current year even though cash "
              "settles in early January. §1091's ±30-day wash-sale window is also "
              "measured from trade date. Exception: closing-leg of a short sale uses the "
              "settlement date per §1233.",
              "- The keystone's $3K/$1.5K-MFS loss cap is applied; the capital_loss_"
              "carryforward is what flows to next year's Schedule D.",
              "- **`tax_law.federal.capital_gains_rates` is REQUIRED** when the baseline "
              "has LT capital gains or qualified dividends. The keystone falls back to "
              "ordinary rates on preferential income when either the 0%- or 15%-bracket "
              "threshold is missing, which would INFLATE fed_savings — the scanner "
              "refuses to run in that state. Run `/tax-setup` if /tax-harvest errors out.",
              "- **NIIT relief (§1411, 3.8%)** is layered OUTSIDE the keystone via "
              "`compute_niit` (the keystone deliberately omits NIIT, so we recompute it "
              "for baseline and harvested inputs and integrate only the *delta* into "
              "the anchor). A high-MAGI taxpayer harvesting capital losses gets relief "
              "both from a lower NII base AND from MAGI dropping further below (or even "
              "below) the §1411 threshold; both are reflected. Net investment income "
              "uses `interest + ordinary_dividends + max(0, LT capital_gains)` — net "
              "**short-term** gains routed to ordinary income are NOT added back, a "
              "v1 simplification shared with `/tax-roth-convert`. Effect: if you have "
              "large net ST gains and harvest ST losses, the NIIT relief shown may "
              "understate by ~3.8% × (ST losses harvested).",
              "- **LTCG bracket down-shifting** (a harvest reducing AGI can drop "
              "preferential income from the 20% → 15% or 15% → 0% bracket) IS reflected "
              "automatically — the QDCG worksheet inside the keystone re-prices "
              "preferential income at the post-harvest taxable-income position.",
              "- OBBBA Schedule 1-A (line 13b) deductions are held at the baseline "
              "amount: a harvest that lowers MAGI through a phase-out restores "
              "deduction this report does not, so it may save more than shown."]
    lines += schedule_a_caveat(r.schedule_a_repriced, r.schedule_a_held)
    if r.schedule_a_repriced and r.state_modeled:
        lines += ["- The state estimate keeps the held deduction: it prices the "
                  "federal taxable-income change at a state rate, and the "
                  "re-priced Schedule A is federal. Where the harvest lowers the "
                  "medical floor or raises the SALT cap, it may save more than "
                  "shown."]
    lines += salt_phasedown_caveat(
        r.salt_cap_may_move, "a harvest that lowers modified AGI while it is above the "
        "threshold can save more than shown.")
    lines += medical_floor_caveat(
        r.medical_floor_may_move, "a harvest can save more than shown.")
    lines += ["- This is a planning estimate, not a filed return. Confirm with your broker's "
              "realized lot reports before executing trades.", ""]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _main(argv: list) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="agents.lib.loss_harvest")
    parser.add_argument("--result", required=True, help="path to result.json")
    add_workspace_argument(parser)
    parser.add_argument("--year", required=True, help="current tax year")
    parser.add_argument("--holdings", required=True,
                        help="path to holdings.json (must live under workspace)")
    parser.add_argument("--name", default="harvest",
                        help="scenario name (single path segment, default: harvest)")
    parser.add_argument("--today", default=None,
                        help="override today's date YYYY-MM-DD (testing only)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    parser.add_argument("--no-save", action="store_true",
                        help="do not write scenarios/<name>.json")
    args = parser.parse_args(argv)

    try:
        year = _validate_year_arg(args.year)
        workspace = resolve_workspace(args.workspace).resolve()
        result_path = _resolve_under(args.result, workspace)
        result = _read_json(result_path)
        read_tax_year(result, year)

        profile = _read_json(workspace / "taxpayer_profile.json")
        tax_law_path = workspace / f"tax_law_{year}.json"
        tax_law = _read_json(tax_law_path) if tax_law_path.exists() else {}

        holdings_path = _resolve_under(args.holdings, workspace)
        holdings = _read_json(holdings_path)

        name = _validate_name(args.name)
        today = (datetime.date.fromisoformat(args.today) if args.today
                 else datetime.date.today())
    except (WorkspaceError, LossHarvestError, ScenarioError, RolloverError,
            ProjectionError, ValueError) as e:
        print(f"ERROR: {redact(e)}", file=sys.stderr)
        return 1

    try:
        report = build_harvest(result, profile, tax_law, holdings, year,
                               name=name, today=today)
    except (LossHarvestError, ScenarioError, ProjectionError, ValueError) as e:
        print(f"ERROR: {redact(e)}", file=sys.stderr)
        return 1

    data = _report_to_dict(report)
    if not args.no_save:
        try:
            out = save_scenario(workspace, name, data)
            print(f"# Saved {redact(out)}", file=sys.stderr)
        except (ScenarioError, RolloverError) as e:
            print(f"ERROR: {redact(e)}", file=sys.stderr)
            return 1

    if args.json:
        print(json.dumps(data, indent=2, default=str))
    else:
        print(render_markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
