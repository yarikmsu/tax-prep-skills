#!/usr/bin/env python3
"""Crypto exchange importer for /tax-parse-crypto (backlog #8).

Ingests Coinbase / Kraken / Gemini transaction CSVs (or a normalized JSON), matches
disposals against acquisition lots via FIFO, and emits Form 8949 rows + Schedule D
totals + ordinary-income receipts + the Form 1040 page-1 digital-asset answer for
/tax-compute to consume.

CORE TAX MODEL (IRS Notice 2014-21 — virtual currency is PROPERTY):
  - Every disposal is a capital event: sell-for-USD, crypto-to-crypto TRADE (the #1
    omitted event — disposing ETH for SOL is a taxable sale of the ETH at FMV), and
    spending crypto on goods/services.
  - Income receipts (staking, rewards, interest, mining, airdrops) are ORDINARY income
    at FMV on receipt — NOT capital gain — and that FMV becomes the received coin's cost
    basis for a later disposal (we emit a paired Acquisition so the reward is not
    double-taxed when sold).
  - The WASH-SALE rule (§1091) does NOT apply to crypto — it covers "stock or
    securities," and crypto is property. This is the exact INVERSE of /tax-harvest;
    we deliberately do not apply it and say so loudly.
  - Any disposal OR income receipt makes the 1040 digital-asset question a mandatory
    "Yes"; acquire-and-hold only (buy/receive with no disposals, no rewards) is "No".

DESIGN (bias-direction taxonomy — see CLAUDE.md):
  - FIFO only, after explicit lot-selection screening. Valid specific identification
    can change basis in either direction; this engine never substitutes FIFO for it.
  - Unmatched disposals (no acquisition on record, e.g. a transfer-in with no basis) get
    diagnostic $0 cost basis + a `needs_basis` flag. In account mode missing basis
    or transfer evidence BLOCKS return preparation; diagnostic totals are not filing values.
  - All numeric inputs are validated at the parse boundary and REJECT bool / NaN /
    Infinity / negative (NaN/Inf pass isinstance and defeat `< 0` checks — the recurring
    project lesson). Unknown kind / unknown exchange / malformed date → clean
    CryptoImportError, never a raw traceback.

Account FIFO segregates explicit account IDs and transfers original lot slices.
Legacy universal imports for 2025 onward are diagnostic only (Rev. Proc. 2024-28).
Form 8949 boxes route only from reviewed broker-form evidence (AA41): each
filing-year disposal is reconciled with the 1099-DA/1099-B that reports it, or
its account's reviewed declaration that none does. Remaining scope limits:
NFTs/collectible 28% rate, DeFi, §1256, margin not modeled; state tax
handled downstream by /tax-compute; mining/staking-as-a-business (Schedule C + SE tax)
flagged, not auto-classified. Federal Form 8949 only.

Pure compute + CLI. Never mutates result.json / taxpayer_profile.json — emits a result
dict the /tax-parse-crypto skill merges.
"""

import datetime
import heapq
import json
import math
import re
import sys
from dataclasses import dataclass, field, replace
from fractions import Fraction
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agents.lib.secure_fs import redact  # noqa: E402
from agents.lib.workspace import (  # noqa: E402
    WorkspaceError,
    resolve_workspace,
)

SCHEMA = "tax-parse-crypto/v1"

# Normalized transaction kinds. Each expands to some combination of primitive events.
_KINDS = {"buy", "sell", "trade", "income", "spend", "receive", "send"}

# Float epsilon for "lot fully consumed" comparisons (share/coin quantities are floats).
_EPS = 1e-9


class CryptoImportError(Exception):
    """Raised when /tax-parse-crypto cannot safely proceed."""


# --------------------------------------------------------------------------
# Strict validators (reject bool / NaN / Infinity at the parse boundary)
# --------------------------------------------------------------------------

def _num(value, label: str, *, allow_negative: bool = False) -> float:
    """Coerce to a FINITE float. Rejects bool, NaN, Infinity, and (by default) negatives.

    json.loads happily yields NaN/Infinity and they pass isinstance(float) while
    defeating every `< 0` / range check (all comparisons with NaN are False) — so we
    must guard explicitly via math.isfinite.
    """
    if isinstance(value, bool):
        raise CryptoImportError(f"{label} must be a number, got bool: {value!r}")
    if isinstance(value, (int, float)):
        try:
            v = float(value)
        except OverflowError:
            raise CryptoImportError(f'{label} must be finite') from None
    elif isinstance(value, str):
        cleaned = value.replace(",", "").replace("$", "").strip()
        try:
            v = float(cleaned)
        except ValueError:
            raise CryptoImportError(f"{label} must be numeric, got {value!r}")
    else:
        raise CryptoImportError(f"{label} must be numeric, got {value!r}")
    if not math.isfinite(v):
        raise CryptoImportError(f"{label} must be finite, got {value!r}")
    if not allow_negative and v < 0:
        raise CryptoImportError(f"{label} cannot be negative (got {v})")
    return v


def _opt_num(value, label: str, *, allow_negative: bool = False) -> 'float | None':
    if value is None or value == "":
        return None
    return _num(value, label, allow_negative=allow_negative)


def _unsign_qty(qty: str) -> str:
    """Strip a leading +/- (incl. the unicode minus) from a quantity string. Exchanges encode
    disposals with a NEGATIVE quantity, but `kind` already carries direction and the compute
    path rejects negatives (allow_negative=False) — so adapters emit the magnitude.
    (Codex P1/P2, PR #23.)"""
    return qty.lstrip("+−-").strip() if qty[:1] in "+-−" else qty


def _req_str(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CryptoImportError(f"{label} requires a non-empty string, got {value!r}")
    return value.strip()


def _parse_date(raw, label: str) -> datetime.date:
    """Parse a date. Accepts a date, a YYYY-MM-DD string, or an ISO datetime/timestamp
    (exchange exports stamp the time — we keep only the calendar date, which is what the
    holding period turns on)."""
    if isinstance(raw, datetime.date) and not isinstance(raw, datetime.datetime):
        return raw
    if isinstance(raw, datetime.datetime):
        return raw.date()
    if not isinstance(raw, str) or not raw.strip():
        raise CryptoImportError(f"{label} date must be a YYYY-MM-DD string, got {raw!r}")
    s = raw.strip()
    # Take the date portion of an ISO timestamp ("2024-03-01T14:05:00Z" / "... 14:05:00").
    s = s.replace("Z", "").replace("T", " ").split(" ")[0]
    try:
        return datetime.date.fromisoformat(s)
    except ValueError:
        raise CryptoImportError(f"{label} date {raw!r} is not a valid YYYY-MM-DD")


def _parse_dt(raw, label: str) -> datetime.datetime:
    """Parse a full timestamp (naive). A date-only value becomes midnight. The intraday
    time is kept so FIFO lot ordering is correct for SAME-DAY sale-before-buy (a 09:00 sell
    must not borrow basis from a 12:00 buy of the same coin). Form 8949 still renders only
    the calendar date. (Codex P2, PR #23.)"""
    if isinstance(raw, datetime.datetime):
        return raw.replace(tzinfo=None)
    if isinstance(raw, datetime.date):
        return datetime.datetime(raw.year, raw.month, raw.day)
    if isinstance(raw, str) and raw.strip():
        s = raw.strip().replace("Z", "")
        try:
            return datetime.datetime.fromisoformat(s).replace(tzinfo=None)
        except ValueError:
            pass
    # Fall back to the date parser (raises a clean error on true garbage); midnight time.
    d = _parse_date(raw, label)
    return datetime.datetime(d.year, d.month, d.day)


def _money(v: float) -> float:
    """Round a USD amount to cents (the IRS-form convention used throughout the toolkit)."""
    return round(v + 0.0, 2)


# --------------------------------------------------------------------------
# Normalized transaction model + primitive events
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CryptoTransaction:
    date: datetime.date
    kind: str                                   # one of _KINDS
    asset: str                                  # ticker, e.g. BTC (the disposed/acquired coin)
    quantity: float                             # units of `asset`
    usd_amount: float                           # USD value: cost (buy), proceeds (sell/spend),
                                                #   FMV of the swap leg (trade), FMV (income)
    fee_usd: float = 0.0
    received_asset: 'str | None' = None         # trade only: the coin acquired
    received_quantity: 'float | None' = None    # trade only
    basis_usd: 'float | None' = None            # receive only: transfer-in cost basis if known
    acquired_date: 'datetime.date | None' = None  # receive only: ORIGINAL acquisition date for
                                                #   holding period (else the transfer date is used)
    income_type: 'str | None' = None            # income only
    exchange: 'str | None' = None
    txn_id: 'str | None' = None


@dataclass(frozen=True)
class Acquisition:
    asset: str
    quantity: float
    cost_basis: float       # total USD basis for `quantity`
    acquired: datetime.date
    # When set, the date the coin entered THIS pool (a `receive` transferred in later than its
    # original `acquired`). Used ONLY for the match_lots availability guard, separate from
    # `acquired` (holding period). Defaults to `acquired`. (Codex P2 round 29, PR #23.)
    available_from: 'datetime.date | None' = None
    # False for a `receive` (transfer in) with no basis_usd supplied: the lot carries $0
    # basis but that $0 is UNKNOWN, not asserted. A disposal that consumes such a lot must
    # still raise the needs_basis flag (else the user files a $0-basis gain with no warning).
    basis_known: bool = True


@dataclass(frozen=True)
class Disposal:
    asset: str
    quantity: float
    proceeds: float         # total USD proceeds (net of fee); 0 for a non-taxable transfer
    disposed: datetime.date
    # False for a `send` (transfer OUT to your own wallet): it removes units from the FIFO
    # pool but is NOT a taxable sale, so it consumes lots without emitting a Form 8949 row.
    # Removing the units matters: per-account (Rev. Proc. 2024-28) a later sale in the source
    # account must NOT borrow the sent-away lot's basis; and in the universal pool, recording
    # both transfer legs (send + receive-with-basis) then nets to one lot instead of two.
    taxable: bool = True


@dataclass(frozen=True)
class IncomeEvent:
    asset: str
    quantity: float
    fmv: float              # ordinary income recognized at receipt
    received: datetime.date
    income_type: str
    account_id: str | None = None
    txn_id: str | None = None


def _coerce_transaction(raw: dict, idx: int) -> CryptoTransaction:
    """Validate one normalized-transaction dict into a CryptoTransaction."""
    if not isinstance(raw, dict):
        raise CryptoImportError(f"transactions[{idx}] must be a JSON object, got {raw!r}")
    label = f"transactions[{idx}]"
    kind = _req_str(raw.get("kind"), f"{label}.kind").lower()
    if kind not in _KINDS:
        raise CryptoImportError(
            f"{label}.kind {kind!r} not recognized — must be one of {sorted(_KINDS)}")
    # Full timestamp (naive); preserves intraday order for same-day FIFO. Date-only inputs
    # become midnight, so calendar-date semantics are unchanged for them.
    date = _parse_dt(raw.get("date"), label)
    asset = _req_str(raw.get("asset"), f"{label}.asset").upper()
    quantity = _num(raw.get("quantity"), f"{label}.quantity")
    # `send` (transfer out) and `receive` (transfer in, priced by basis_usd) carry no
    # USD proceeds/cost figure; the taxable kinds all require one.
    usd = (0.0 if kind in ("send", "receive")
           else _num(raw.get("usd_amount"), f"{label}.usd_amount"))
    fee = _num(raw.get("fee_usd", 0) or 0, f"{label}.fee_usd")
    recv_asset = raw.get("received_asset")
    recv_qty = raw.get("received_quantity")
    basis = _opt_num(raw.get("basis_usd"), f"{label}.basis_usd")
    income_type = raw.get("income_type")
    # receive only: the coin's ORIGINAL acquisition date (its holding period started when it
    # was first bought, NOT when it was transferred in). Without it the transfer date is used,
    # which can wrongly reset a long-term coin to short-term on Form 8949. (Codex P2, PR #23.)
    acquired_date = None
    if kind == "receive" and raw.get("acquired") not in (None, ""):
        acquired_date = _parse_dt(raw.get("acquired"), f"{label}.acquired")

    if kind == "trade":
        recv_asset = _req_str(recv_asset, f"{label}.received_asset (required for a trade)").upper()
        recv_qty = _num(recv_qty, f"{label}.received_quantity (required for a trade)")
    else:
        recv_asset, recv_qty = None, None
    if kind == "income":
        income_type = (_req_str(income_type, f"{label}.income_type").lower()
                       if income_type else "income")

    # Quantity-zero validation (Codex P2, rounds 7 + 18):
    #  - A taxable DISPOSAL/income with quantity 0 is silently dropped by match_lots (a 0-qty
    #    disposal is skipped, a 0-qty income sets no basis) — losing proceeds/income and even
    #    leaving the digital-asset answer "No". Always reject.
    #  - An ACQUISITION leg with quantity 0 but a positive cost/basis silently drops that basis
    #    ($0/unit on a 0-qty lot), so a later sale wrongly shows missing/$0 basis. Reject when
    #    the leg carries cost. (A genuinely $0-cost, 0-qty buy/receive is a harmless no-op.)
    if kind in ("sell", "spend", "income") and quantity <= 0:
        raise CryptoImportError(
            f"{label}.quantity must be > 0 for a {kind} (got {quantity}) — a zero-quantity "
            f"taxable event would be silently dropped from the totals.")
    if kind == "trade":
        if quantity <= 0:
            raise CryptoImportError(
                f"{label}.quantity (the disposed leg) must be > 0 for a trade (got {quantity}).")
        if recv_qty is not None and recv_qty <= 0 and usd > 0:
            raise CryptoImportError(
                f"{label}.received_quantity must be > 0 for a trade carrying value "
                f"(got {recv_qty}) — a zero-quantity acquired leg would drop its ${usd} basis.")
    if kind == "buy" and quantity <= 0 and (usd + fee) > 0:
        raise CryptoImportError(
            f"{label}.quantity must be > 0 for a buy carrying cost (got {quantity}) — a "
            f"zero-quantity lot would silently drop its ${usd + fee} basis.")
    if kind == "receive" and quantity <= 0 and (basis or 0) > 0:
        raise CryptoImportError(
            f"{label}.quantity must be > 0 for a receive carrying basis (got {quantity}) — a "
            f"zero-quantity lot would silently drop its ${basis} basis.")

    return CryptoTransaction(
        date=date, kind=kind, asset=asset, quantity=quantity, usd_amount=usd, fee_usd=fee,
        received_asset=recv_asset, received_quantity=recv_qty, basis_usd=basis,
        income_type=income_type, acquired_date=acquired_date,
        exchange=(raw.get("exchange") or None), txn_id=(raw.get("txn_id") or None),
    )


def expand_transaction(txn: CryptoTransaction, *, per_account: bool = False
                       ) -> 'tuple[list[Acquisition], list[Disposal], list[IncomeEvent]]':
    """Expand one normalized transaction into primitive events.

    This is where the two crypto gotchas are encoded:
      - a TRADE is a Disposal of the sold leg AND an Acquisition of the bought leg;
      - INCOME is an ordinary-income receipt AND an Acquisition (basis = FMV).
    Fees reduce proceeds on disposals and add to basis on acquisitions.

    `per_account` controls how a (confirmed non-taxable) `send` is handled — the two modes
    are genuinely different and both are correct in context:
      - UNIVERSAL pool (default, per_account=False): a self-transfer between two of your
        imported accounts leaves the coins in your single pool, so `send` is a NO-OP — the
        original acquisition lot persists and supplies basis to the eventual sale anywhere.
        (Omit the matching `receive` leg in this mode, or it double-counts the lot.)
      - PER-ACCOUNT (per_account=True, Rev. Proc. 2024-28): each account is its own run, so a
        `send` REMOVES the coins from this account's pool (a non-taxable disposal, no row);
        the destination account's separate run re-adds them via `receive` with basis_usd.
    """
    acq: 'list[Acquisition]' = []
    dis: 'list[Disposal]' = []
    inc: 'list[IncomeEvent]' = []

    if txn.kind == "buy":
        acq.append(Acquisition(txn.asset, txn.quantity,
                               _money(txn.usd_amount + txn.fee_usd), txn.date))
    elif txn.kind in ("sell", "spend"):
        dis.append(Disposal(txn.asset, txn.quantity,
                            _money(txn.usd_amount - txn.fee_usd), txn.date))
    elif txn.kind == "trade":
        dis.append(Disposal(txn.asset, txn.quantity,
                            _money(txn.usd_amount - txn.fee_usd), txn.date))
        # Basis of the acquired leg = the FMV given up (the disposal's gross USD value).
        acq.append(Acquisition(txn.received_asset, txn.received_quantity,
                               _money(txn.usd_amount), txn.date))
    elif txn.kind == "income":
        inc.append(IncomeEvent(txn.asset, txn.quantity, _money(txn.usd_amount),
                               txn.date, txn.income_type or "income"))
        acq.append(Acquisition(txn.asset, txn.quantity, _money(txn.usd_amount), txn.date))
    elif txn.kind == "receive":
        # Transfer IN: not a purchase. Basis is whatever the user supplied; if absent the
        # lot carries $0 basis flagged basis_known=False so the matcher raises needs_basis
        # on any disposal that consumes it (a transfer-in with unknown basis is exactly the
        # missing-basis case the skill must stop on). An explicit basis_usd (incl. 0) is a
        # user assertion → basis_known=True.
        # Use the coin's ORIGINAL acquisition date for the holding period when supplied (a
        # transfer-in doesn't restart it); else fall back to the transfer date.
        acq.append(Acquisition(txn.asset, txn.quantity, _money(txn.basis_usd or 0.0),
                               txn.acquired_date or txn.date,
                               basis_known=txn.basis_usd is not None,
                               available_from=txn.date))
    elif txn.kind == "send":
        # Transfer OUT to own wallet — non-taxable. In per-account mode it removes the coins
        # from THIS account's pool (a non-taxable disposal: consumes lots FIFO, no row); in
        # the universal pool it is a no-op so basis stays with the coins. (Codex P2, PR #23.)
        if per_account:
            dis.append(Disposal(txn.asset, txn.quantity, 0.0, txn.date, taxable=False))
    else:  # unreachable — _coerce_transaction already gates kind
        raise CryptoImportError(f"unhandled kind {txn.kind!r}")
    return acq, dis, inc


# --------------------------------------------------------------------------
# FIFO lot engine
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Form8949Row:
    description: str
    date_acquired: str          # YYYY-MM-DD, or "UNKNOWN" for an unmatched (basis-less) slice
    date_sold: str
    proceeds: float
    cost_basis: float
    gain_loss: float
    holding_period: 'Literal["short", "long"]'
    box: str | None             # set only from reviewed broker-form evidence (AA41)
    needs_basis: bool           # True when this slice had no acquisition on record
    account_id: str | None = None
    txn_id: str | None = None
    origin_account_id: str | None = None
    origin_txn_id: str | None = None
    # Account mode only. The exact units apportion a broker form's figures;
    # `cost_basis` is then Form 8949 column (e), and `fifo_cost_basis` keeps
    # the FIFO figure beside it whenever a broker form's basis is shown there.
    quantity_exact: Fraction | None = None
    adjustment: float = 0.0
    adjustment_code: str | None = None
    form_id: str | None = None
    fifo_cost_basis: float | None = None
    broker_proceeds: float | None = None  # audit allocation from this row's form, never column (d)


class _OpenLot:
    """Mutable FIFO lot — basis tracked per unit so partial consumption is exact."""
    __slots__ = ("qty", "basis_per_unit", "acquired", "available_from", "basis_known")

    def __init__(self, qty: float, cost_basis: float, acquired: datetime.date,
                 available_from: datetime.date, basis_known: bool = True):
        self.qty = qty
        self.basis_per_unit = (cost_basis / qty) if qty > _EPS else 0.0
        self.acquired = acquired          # original acquisition → holding period
        self.available_from = available_from  # entered THIS pool → availability guard
        self.basis_known = basis_known


def match_lots(acquisitions: 'list[Acquisition]', disposals: 'list[Disposal]'
               ) -> 'list[Form8949Row]':
    """Match disposals against acquisition lots per asset, FIFO (oldest lot first).

    Each lot slice consumed by a disposal yields one Form 8949 row. A disposal whose
    quantity exceeds the available lots produces a final $0-basis row flagged
    `needs_basis` (over-states the gain — the safe direction — and is surfaced loudly).
    """
    # Per-asset FIFO queues, oldest acquisition first. Sort is stable so same-day lots
    # keep input order (a reasonable deterministic tie-break).
    queues: 'dict[str, list[_OpenLot]]' = {}
    for a in sorted(acquisitions, key=lambda x: x.acquired):
        queues.setdefault(a.asset, []).append(
            _OpenLot(a.quantity, a.cost_basis, a.acquired,
                     a.available_from or a.acquired, a.basis_known))

    rows: 'list[Form8949Row]' = []
    for d in sorted(disposals, key=lambda x: x.disposed):
        remaining = d.quantity
        if remaining <= _EPS:
            continue
        proceeds_per_unit = d.proceeds / d.quantity if d.quantity > _EPS else 0.0
        queue = queues.get(d.asset, [])
        while remaining > _EPS:
            # Consume the oldest (FIFO by acquisition) lot AVAILABLE as of the disposal date.
            # `available_from` (when the coin entered the pool) gates availability, separate
            # from `acquired` (holding period): a coin received AFTER a sale must not satisfy
            # that earlier sale even if its original acquisition predates it, and a
            # missing-history sale-before-rebuy still can't borrow the future lot. We SCAN
            # (not pop[0]+break) because availability order can differ from acquisition order.
            # (Codex P1 round 1 + P2 round 29, PR #23.)
            lot = next((l for l in queue
                        if l.qty > _EPS and l.available_from <= d.disposed), None)
            if lot is None:
                break
            take = min(remaining, lot.qty)
            # A consumed lot whose basis is unknown (basisless transfer-in) flags the row; a
            # non-taxable transfer (`send`) consumes the lot but emits NO Form 8949 row.
            if d.taxable:
                rows.append(_make_row(
                    d.asset, take, proceeds_per_unit, lot.basis_per_unit,
                    lot.acquired, d.disposed, needs_basis=not lot.basis_known))
            lot.qty -= take
            remaining -= take
        if remaining > _EPS and d.taxable:
            # No acquisition on record for this slice — $0 basis, flagged. (A non-taxable
            # send of more than the pool holds just under-removes; no tax effect to report.)
            rows.append(_make_row(
                d.asset, remaining, proceeds_per_unit, 0.0,
                acquired=None, disposed=d.disposed, needs_basis=True))
    return rows


def _holding_period(acquired: datetime.date, sold: datetime.date
                    ) -> 'Literal["short", "long"]':
    """IRS holding period for Form 8949: long-term iff held MORE THAN one year. The period
    begins the day AFTER acquisition, so the lot is long-term iff the sale date is LATER than
    the one-year anniversary of the acquisition date — equal (exactly one year) is short-term.

    This is calendar-correct across leap years, unlike a fixed >365-day count: a 2024-01-01 →
    2025-01-01 hold spans 366 days but is exactly one year, hence SHORT-term. (We use the
    precise rule here rather than loss_harvest.derive_holding_period because this produces
    actual Form 8949 rows where the boundary day changes the tax. Codex P2, PR #23.)
    """
    try:
        anniversary = acquired.replace(year=acquired.year + 1)
    except ValueError:
        # Feb 29 acquisition → no Feb 29 next year. The holding period starts Mar 1, so the
        # lot is long-term iff sold after Feb 28 of the following year (i.e. on/after Mar 1).
        anniversary = datetime.date(acquired.year + 1, 2, 28)
    return "long" if sold > anniversary else "short"


def _as_date(x) -> datetime.date:
    """Calendar date of a date or datetime (Form 8949 reports dates; holding period and the
    filing-year filter are calendar-based, even though lots are ORDERED by full timestamp)."""
    return x.date() if isinstance(x, datetime.datetime) else x


def _make_row(asset: str, qty: float, proceeds_per_unit: float, basis_per_unit: float,
              acquired, disposed, *, needs_basis: bool) -> Form8949Row:
    proceeds = _money(proceeds_per_unit * qty)
    cost = _money(basis_per_unit * qty)
    gain = _money(proceeds - cost)
    disposed_d = _as_date(disposed)
    if acquired is None:
        # Unknown acquisition date — treat as short-term (the conservative/common default
        # for an undocumented transfer-in; long-term would need proof of a >1yr hold).
        period: 'Literal["short", "long"]' = "short"
        acq_str = "UNKNOWN"
    else:
        period = _holding_period(_as_date(acquired), disposed_d)
        acq_str = _as_date(acquired).isoformat()
    return Form8949Row(
        description=f"{_fmt_qty(qty)} {asset}",
        date_acquired=acq_str, date_sold=disposed_d.isoformat(),
        proceeds=proceeds, cost_basis=cost, gain_loss=gain,
        holding_period=period,
        box=None,
        needs_basis=needs_basis,
    )


def _fmt_qty(q: float) -> str:
    """Render a coin quantity compactly (up to 8 dp, trailing zeros trimmed)."""
    s = f"{q:.8f}".rstrip("0").rstrip(".")
    return s if s else "0"


# Account FIFO keeps exact quantities and basis fractions internally. Only form
# amounts are rounded to cents; transfers move slices, never create another buy.
@dataclass
class _AccountLot:
    quantity: Fraction
    basis: Fraction
    acquired: datetime.datetime | None
    origin_account: str
    origin_txn: str
    origin_order: int
    basis_known: bool = True
    acquired_tax_date: datetime.date | None = None


def _account_datetime(value):
    # Transfers can cross exports with different offsets. Ordering uses UTC;
    # a separate original calendar date is retained for forms and year filters.
    if isinstance(value, datetime.datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.datetime.fromisoformat(value.strip().removesuffix(' UTC').replace('Z', '+00:00'))
        except ValueError as exc:
            raise CryptoImportError('Account timestamp requires an ISO date/time or UTC suffix') from exc
    else:
        return _parse_dt(value, 'account timestamp')
    return (parsed.astimezone(datetime.timezone.utc).replace(tzinfo=None)
            if parsed.tzinfo else parsed)


def _quantity_text(value):
    # Input quantities and their differences are terminating decimals. Avoid an
    # arbitrary eight-decimal display cutoff (ETH units can be much smaller).
    denominator, twos, fives = value.denominator, 0, 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        raise CryptoImportError('Account quantity is not a terminating decimal')
    scale = max(twos, fives)
    digits = str(value.numerator * 2 ** (scale-twos) * 5 ** (scale-fives)).rjust(scale+1, '0')
    return (digits[:-scale] + '.' + digits[-scale:]).rstrip('0').rstrip('.') if scale else digits


def _exact(value, label):
    _num(value, label)  # finite / bool / sign checks precede exact arithmetic
    text = value.replace(',', '').replace('$', '').strip() if isinstance(value, str) else str(value)
    try:
        return Fraction(text)
    except (ValueError, ZeroDivisionError) as exc:
        raise CryptoImportError(f'{label} must be an exact finite decimal') from exc


def _account_id(value, label):
    identifier = _req_str(value, label)
    if identifier != value or redact(identifier) != identifier:
        raise CryptoImportError(f'{label} must be an opaque identifier without sensitive values')
    return identifier


def _fraction_record(value):
    return {'numerator': str(value.numerator), 'denominator': str(value.denominator)}


def _account_lot_record(lot):
    return {'quantity': _quantity_text(lot.quantity), 'basis_usd': _money(_num(float(lot.basis), 'lot basis')),
            'quantity_exact': _fraction_record(lot.quantity), 'basis_exact': _fraction_record(lot.basis),
            'acquired': lot.acquired.isoformat() if lot.acquired else None,
            'acquired_tax_date': lot.acquired_tax_date.isoformat() if lot.acquired_tax_date else None,
            'origin_account_id': lot.origin_account, 'origin_txn_id': lot.origin_txn,
            'basis_known': lot.basis_known}


def _account_timeline(raw, txns, links):
    """Preserve account-local order and transfer causality at equal timestamps."""
    groups = {}
    for index, txn in enumerate(txns):
        groups.setdefault(txn.date, []).append(index)
    order = []
    for date, group in sorted(groups.items()):
        supplied = [raw[i].get('sequence') for i in group]
        if any(seq is not None for seq in supplied):
            if any(isinstance(seq, bool) or not isinstance(seq, int) or seq < 0 for seq in supplied):
                raise CryptoImportError('Supply non-negative integer sequence for every event at this timestamp')
            if len(set(supplied)) != len(supplied):
                raise CryptoImportError('Same-time transaction sequences must be unique')
            ranked = sorted(group, key=lambda i: raw[i]['sequence'])
        else:
            ranked = group
        # Linked receive MUST follow its send. Local source order is explicit
        # data; a conflicting same-time export needs corrected sequence evidence.
        edges = {i: set() for i in group}
        indegree = {i: 0 for i in group}
        last = {}
        def edge(before, after):
            if after not in edges[before]:
                edges[before].add(after)
                indegree[after] += 1
        for i in ranked:
            account = raw[i]['account_id']
            if account in last:
                edge(last[account], i)
            last[account] = i
        if any(seq is not None for seq in supplied):
            for before, after in zip(ranked, ranked[1:]):
                edge(before, after)
        for pair in links.values():
            before, after = pair['send'], pair['receive']
            if txns[before].date == date and txns[after].date == date:
                edge(before, after)
        rank = {index: position for position, index in enumerate(ranked)}
        ready = [(rank[i], i) for i in ranked if indegree[i] == 0]
        heapq.heapify(ready)
        done = []
        while ready:
            _, i = heapq.heappop(ready)
            done.append(i)
            for after in edges[i]:
                indegree[after] -= 1
                if indegree[after] == 0:
                    heapq.heappush(ready, (rank[after], after))
        if len(done) != len(group):
            raise CryptoImportError('Conflicting same-time account/transfer order; supply reviewed sequence evidence')
        order.extend(done)
    return order


def _match_account_fifo(raw, txns, transfer_links):
    """Segregated pools and one reconciled send/receive pair per transfer id."""
    if transfer_links is None:
        transfer_links = {}
    if not isinstance(transfer_links, dict):
        raise CryptoImportError('transfer_links must be an object keyed by transfer_id')
    seen, legs, qty = set(), {}, {}
    for i, (record, txn) in enumerate(zip(raw, txns)):
        account = _account_id(record.get('account_id'), 'account_id')
        txid = _account_id(record.get('txn_id'), 'txn_id')
        if (account, txid) in seen:
            raise CryptoImportError('Duplicate transaction id in one account; do not import a source twice')
        seen.add((account, txid))
        _exact(record.get('fee_usd', 0), 'account fee')
        if record.get('external_transfer_confirmed') is not None and txn.kind not in {'send', 'receive'}:
            raise CryptoImportError('Only transfers may carry external_transfer_confirmed')
        qty[i] = _exact(record['quantity'], 'account quantity')
        if qty[i] <= 0:
            raise CryptoImportError('Account events require a positive quantity')
        if 'external_transfer_confirmed' in record and not isinstance(record['external_transfer_confirmed'], bool):
            raise CryptoImportError('external_transfer_confirmed must be boolean')
        tid = record.get('transfer_id')
        if tid is not None:
            tid = _account_id(tid, 'transfer_id')
            if txn.kind not in {'send', 'receive'}:
                raise CryptoImportError('Only send/receive may carry transfer_id')
            if record.get('external_transfer_confirmed') is True:
                raise CryptoImportError('An internal linked transfer cannot also be external')
            legs.setdefault(tid, []).append(i)
        if txn.kind in {'send', 'receive'} and txn.fee_usd:
            raise CryptoImportError('Transfer fees require separately reviewed taxable fee events; never ignore them')
    unknown = set(transfer_links) - set(legs)
    if unknown:
        raise CryptoImportError('Transfer evidence names a transfer with no transaction legs')
    links, issues = {}, []
    def unresolved(index, reason):
        issues.append({'account_id': raw[index]['account_id'], 'txn_id': raw[index]['txn_id'],
                       'kind': txns[index].kind, 'reason': reason})
    for tid, indices in legs.items():
        sends = [i for i in indices if txns[i].kind == 'send']
        receives = [i for i in indices if txns[i].kind == 'receive']
        if len(indices) > 2 or len(sends) > 1 or len(receives) > 1:
            raise CryptoImportError('A transfer_id must identify exactly one send and one receive')
        evidence = transfer_links.get(tid)
        if evidence is not None:
            if (not isinstance(evidence, dict) or set(evidence) != {'ownership_confirmed', 'evidence'}
                    or evidence['ownership_confirmed'] is not True):
                raise CryptoImportError('Transfer evidence requires ownership_confirmed true and an evidence reference')
            _account_id(evidence['evidence'], 'transfer evidence reference')
        if not sends or not receives or evidence is None:
            for i in indices:
                unresolved(i, 'unmatched transfer or missing reviewed ownership evidence')
            continue
        send, receive = sends[0], receives[0]
        if (raw[send]['account_id'] == raw[receive]['account_id'] or txns[send].asset != txns[receive].asset
                or qty[send] != qty[receive]):
            raise CryptoImportError('Transfer accounts, asset or exact quantities do not reconcile')
        if txns[receive].date < txns[send].date:
            raise CryptoImportError('A transfer cannot arrive before it leaves the source account')
        if txns[receive].basis_usd is not None or txns[receive].acquired_date is not None:
            raise CryptoImportError('Linked receive basis/date are derived from source slices; remove manual overrides')
        links[tid] = {'send': send, 'receive': receive, 'evidence': evidence['evidence']}
    pools, transit, transfers, rows, all_incomes, disposals = {}, {}, [], [], [], []
    order = _account_timeline(raw, txns, links)
    def add(account, asset, lot):
        queue = pools.setdefault((account, asset), [])
        queue.append(lot)
        queue.sort(key=lambda entry: (entry.acquired or datetime.datetime.max, entry.origin_order))
    def consume(index):
        txn, record = txns[index], raw[index]
        remaining, slices = qty[index], []
        for lot in pools.get((record['account_id'], txn.asset), []):
            if remaining <= 0:
                break
            if lot.quantity <= 0:
                continue
            take = min(remaining, lot.quantity)
            basis = lot.basis * take / lot.quantity
            slices.append(_AccountLot(take, basis, lot.acquired, lot.origin_account,
                                      lot.origin_txn, lot.origin_order, lot.basis_known, lot.acquired_tax_date))
            lot.quantity -= take
            lot.basis -= basis
            remaining -= take
        if remaining > 0:
            unresolved(index, 'source account lacks acquisition inventory; another account cannot supply it')
            slices.append(_AccountLot(remaining, Fraction(0), None, record['account_id'],
                                      record['txn_id'], index, False))
        return slices
    for origin_order, index in enumerate(order):
        txn, record = txns[index], raw[index]
        account, txid = record['account_id'], record['txn_id']
        tid = record.get('transfer_id')
        if txn.kind == 'send':
            slices = consume(index)
            if tid in links:
                transit[tid] = slices
            elif record.get('external_transfer_confirmed') is not True:
                unresolved(index, 'outbound transfer needs a matched receive or explicit external ownership confirmation')
            transfers.append({'transfer_id': tid, 'from_account_id': account,
                              'to_account_id': raw[links[tid]['receive']]['account_id'] if tid in links else None,
                              'from_txn_id': txid,
                              'to_txn_id': raw[links[tid]['receive']]['txn_id'] if tid in links else None,
                              'received': txns[links[tid]['receive']].date.isoformat() if tid in links else None,
                              'quantity': _quantity_text(qty[index]),
                              'sent': txn.date.isoformat(), 'asset': txn.asset,
                              'slices': [_account_lot_record(lot) for lot in slices],
                              'external': record.get('external_transfer_confirmed') is True,
                              'evidence': links[tid]['evidence'] if tid in links else None})
            continue
        if txn.kind == 'receive':
            if tid in links:
                for lot in transit.pop(tid):
                    add(account, txn.asset, lot)
            else:
                known = (record.get('external_transfer_confirmed') is True
                         and txn.basis_usd is not None and txn.acquired_date is not None)
                if not known:
                    unresolved(index, 'incoming transfer needs a matched send or confirmed external basis and acquisition evidence')
                if txn.acquired_date is not None and txn.acquired_date > txn.date:
                    raise CryptoImportError('Original acquisition cannot follow the transfer receipt')
                basis = _exact(record['basis_usd'], 'external receive basis') if known else Fraction(0)
                add(account, txn.asset, _AccountLot(qty[index], basis, txn.acquired_date if known else None,
                                                    account, txid, origin_order, known,
                                                    _parse_dt(record['acquired'], 'acquired').date() if known else None))
            continue
        acquisitions, taxable, incomes = expand_transaction(txn)
        if taxable:
            tax_date = _parse_dt(record['date'], 'date')
            disposals.extend(replace(disposal, disposed=tax_date) for disposal in taxable)
            slices = consume(index)
            net = _exact(record['usd_amount'], 'disposal proceeds') - _exact(record.get('fee_usd', 0), 'disposal fee')
            # One rounding rule for every row: each slice posts the rounded
            # running total less the cents already posted, so the rows add up
            # to the disposal's rounded proceeds and basis, and a broker form
            # is reconciled against the very cents the return carries.
            proceeds = _running_cents(net * lot.quantity / qty[index] for lot in slices)
            basis = _running_cents(lot.basis for lot in slices)
            for lot, proceeds_cents, basis_cents in zip(slices, proceeds, basis):
                period = _holding_period(lot.acquired_tax_date, tax_date.date()) if lot.acquired_tax_date else 'short'
                rows.append(Form8949Row(
                    description=f'{_quantity_text(lot.quantity)} {txn.asset}',
                    date_acquired=lot.acquired_tax_date.isoformat() if lot.acquired_tax_date else 'UNKNOWN',
                    date_sold=tax_date.date().isoformat(), proceeds=_money(proceeds_cents / 100),
                    cost_basis=_money(basis_cents / 100), gain_loss=_money((proceeds_cents - basis_cents) / 100),
                    holding_period=period, box=None, needs_basis=not lot.basis_known,
                    account_id=account, txn_id=txid, origin_account_id=lot.origin_account,
                    origin_txn_id=lot.origin_txn, quantity_exact=lot.quantity))
        for acquisition in acquisitions:
            # New trade/income basis uses the existing documented fee convention.
            if txn.kind == 'buy':
                basis = _exact(record['usd_amount'], 'purchase basis') + _exact(record.get('fee_usd', 0), 'purchase fee')
                quantity = qty[index]
            elif txn.kind == 'trade':
                basis = _exact(record['usd_amount'], 'trade basis')
                quantity = _exact(record['received_quantity'], 'trade received quantity')
                if quantity <= 0:
                    raise CryptoImportError('An account trade requires a positive received quantity')
            else:
                basis, quantity = _exact(record['usd_amount'], 'income basis'), qty[index]
            add(account, acquisition.asset, _AccountLot(quantity, basis, txn.date, account, txid, origin_order,
                                                    acquired_tax_date=_parse_dt(record['date'], 'date').date()))
        all_incomes.extend(replace(event, account_id=account, txn_id=txid,
                                  received=_parse_dt(record['date'], 'date')) for event in incomes)
    inventory = {account: {} for account, _ in sorted(seen)}
    for (account, asset), lots in sorted(pools.items()):
        inventory.setdefault(account, {})[asset] = [_account_lot_record(lot) for lot in lots if lot.quantity > 0]
    return rows, all_incomes, disposals, {
        'basis_mode': 'account_fifo', 'complete': not issues and not any(row.needs_basis for row in rows),
        'unresolved_inputs': issues,
        'unresolved_transfers': [issue for issue in issues if issue['kind'] in {'send', 'receive'}],
        'transfer_ledger': transfers, 'inventory': inventory,
    }


# --------------------------------------------------------------------------
# Broker-form reconciliation (AA41)
# --------------------------------------------------------------------------
# Form 8949's check boxes for each reviewed year, from that year's
# instructions: (basis reported to the IRS, basis not reported, no broker
# form) per holding period. Box letters are form structure, not an indexed
# figure, but they still move: TY2025 took digital assets off C/F. A year
# without its own reviewed entry routes nothing.
_REPORTING_REGIMES: 'dict[int, dict]' = {
    2023: {'form_type': '1099-B', 'short': ('A', 'B', 'C'), 'long': ('D', 'E', 'F'),
           'source': 'https://www.irs.gov/pub/irs-prior/i8949--2023.pdf'},
    2024: {'form_type': '1099-B', 'short': ('A', 'B', 'C'), 'long': ('D', 'E', 'F'),
           'source': 'https://www.irs.gov/pub/irs-prior/i8949--2024.pdf'},
    2025: {'form_type': '1099-DA', 'short': ('G', 'H', 'I'), 'long': ('J', 'K', 'L'),
           'source': 'https://www.irs.gov/pub/irs-prior/i8949--2025.pdf'},
}
_REPORTING_VERIFIED = '2026-10-11'
# The result.json schedule_d row each box adds into, and its Schedule D line.
# Schedule D pairs A/G, B/H, C/I, D/J, E/K and F/L on one line; these rows
# always go through Form 8949, so A/G and D/J land on lines 1b and 8b. Digital
# assets keep rows of their own beside the 1099-B ones: the employer-stock tie
# reads a whole 1099-B row's column (g), and a crypto code-B correction merged
# into an RSU row would read as employer stock.
_SCHEDULE_D_TARGETS = {
    **dict.fromkeys('AG', ('part_i_short_term', 'line_1b_digital_assets', '1b')),
    **dict.fromkeys('BH', ('part_i_short_term', 'line_2_digital_assets', '2')),
    **dict.fromkeys('CI', ('part_i_short_term', 'line_3_digital_assets', '3')),
    **dict.fromkeys('DJ', ('part_ii_long_term', 'line_8b_digital_assets', '8b')),
    **dict.fromkeys('EK', ('part_ii_long_term', 'line_9_digital_assets', '9')),
    **dict.fromkeys('FL', ('part_ii_long_term', 'line_10_digital_assets', '10')),
}
# Every key is REQUIRED. `cost_basis` and `term` are null where the form leaves
# the box blank, never absent: a missing term would reconcile a form whose box
# disagrees with FIFO without code T.
_FORM_FIELDS = {'form_id', 'form_type', 'account_id', 'txn_id', 'asset', 'quantity',
                'date_sold', 'proceeds', 'cost_basis', 'basis_reported_to_irs', 'term'}
_FORM_FIELDS_BY_TYPE = {'1099-B': frozenset(),
                        '1099-DA': frozenset({'customer_acquisition_info'})}
# Form boxes this engine does not model. Each is REQUIRED — null, false or 0
# when blank — because a box nobody copied must not read as a blank one; filled
# in, it refuses: each changes the row (debt, tokenized securities, a loss the
# form disallows).
_FORM_UNSUPPORTED = {'ordinary', 'accrued_market_discount', 'wash_sale_loss_disallowed',
                     'loss_not_allowed'}
# The same rule per form type, for the boxes reviewed on that form (AA59). On a
# 1099-DA: 3a's "Net proceeds" (box 1f reduced by option premiums the engine does
# not price; "Gross" can show on an ordinary sale and an option's sale alike, so
# it is no key, and the capability gate asks about a sale an option's exercise
# caused instead), a
# QOF disposition in 3b (its deferred gain owes a code-Y row and Form 8997), an optional aggregate report
# in 11a and first-sale NFT proceeds in 11c (neither one disposal's figures;
# 11c is kept out of 1f), and a CUSIP, which marks a tokenized security that
# §1091 reaches. AA61 reviewed the TY2023/2024 1099-B too: box 3's
# collectibles/QOF flags, box 6's option-netted proceeds, the identifier,
# boxes 8-11's contract amounts (signed, never disposal proceeds), and box
# 13's barter receipts all require treatment outside this spot-crypto importer.
_FORM_UNSUPPORTED_BY_TYPE = {
    '1099-B': frozenset({'collectibles', 'qof', 'net_proceeds', 'cusip',
                         'contract_profit_loss', 'prior_unrealized_profit_loss',
                         'current_unrealized_profit_loss', 'aggregate_contract_profit_loss',
                         'bartering'}),
    '1099-DA': frozenset({'net_proceeds', 'qof',
                          'optional_method', 'primary_sale_proceeds', 'cusip'}),
}
_RESOLUTIONS = {'proceeds': {'taxpayer_records', 'transaction_costs', 'option_premium'},
                'basis': {'taxpayer_records'}, 'term': {'taxpayer_records'}}


def _cents(value) -> int:
    """Whole cents of an exact amount (a Fraction or a cent float), a tie
    away from zero — the rule of tax_computations._shared._round_half_up,
    which cannot take a Fraction: a non-terminating quotient has no exact
    Decimal, and a rounded one can move a tie."""
    exact = value if isinstance(value, Fraction) else Fraction(str(value))
    cents = math.floor(abs(exact) * 100 + Fraction(1, 2))
    return cents if exact >= 0 else -cents


def _running_cents(amounts) -> 'list[int]':
    """Cents per item whose running total is the rounded running total, so
    the parts always add up to the rounded whole."""
    parts, previous, running = [], 0, Fraction(0)
    for amount in amounts:
        running += amount
        parts.append(_cents(running) - previous)
        previous += parts[-1]
    return parts


def _spread(total_cents: int, weights) -> 'list[int]':
    whole = sum(weights)
    return _running_cents([Fraction(total_cents, 100) * w / whole for w in weights])


def _absorb(cents: 'list[int]', delta: int) -> 'list[int]':
    """The rows' own cents with a split form's apportionment cent on the
    largest row. Re-spreading the form's figure by units would reprice rows
    whose per-unit amount differs — lots of different basis, or the fills of
    one order at different prices — while the form's total still tied."""
    out = list(cents)
    out[out.index(max(out))] += delta
    return out


def _form_cents(value, label) -> int:
    amount = _exact(value, label)
    if (amount * 100).denominator != 1:
        raise CryptoImportError(f'{label} must be a whole-cent amount as the form shows it')
    return int(amount * 100)


# Boxes whose treatment reaches beyond the disposal a manual entry replaces:
# §1091(d) adds a disallowed wash-sale loss to the replacement lot's basis; a
# CUSIP marks a tokenized security, so its OTHER sales face §1091 too; and
# box 11c's first-sale NFT proceeds are a ledger category tie-out blocks on.
_BOXES_REACHING_OTHER_SALES = {'wash_sale_loss_disallowed', 'cusip', 'primary_sale_proceeds'}


def _manual_disposals_input(reporting, events) -> dict:
    """Disposals the preparer files by hand, keyed (account, txn): evidence."""
    raw = reporting.get('manual_disposals', [])
    if not isinstance(raw, list):
        raise CryptoImportError('manual_disposals must be a list of reviewed disposals')
    manual = {}
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) != {'account_id', 'txn_id', 'evidence'}:
            raise CryptoImportError('Each manual disposal needs account_id, txn_id and evidence')
        key = (_account_id(entry['account_id'], 'manual disposal account_id'),
               _account_id(entry['txn_id'], 'manual disposal txn_id'))
        if key not in events or key in manual:
            raise CryptoImportError('manual_disposals must name distinct transactions in this import')
        manual[key] = _account_id(entry['evidence'], 'manual disposal evidence')
    return manual


def _broker_reporting_input(reporting, events, regime, tax_year):
    if not isinstance(reporting, dict) or not set(reporting) <= {
            'accounts', 'forms', 'resolutions', 'fill_groups', 'manual_disposals'}:
        raise CryptoImportError('broker_reporting takes only accounts, forms, resolutions, fill_groups '
                                'and manual_disposals')
    raw_accounts = reporting.get('accounts', {})
    raw_forms = reporting.get('forms', [])
    raw_resolutions = reporting.get('resolutions', {})
    if not (isinstance(raw_accounts, dict) and isinstance(raw_forms, list)
            and isinstance(raw_resolutions, dict)):
        raise CryptoImportError('broker_reporting accounts and resolutions are objects; forms is a list')
    accounts = _accounts_input(raw_accounts, events)
    manual = _manual_disposals_input(reporting, events)
    forms = [_form_input(f'broker_reporting.forms[{index}]', form, events, accounts, regime, tax_year, manual)
             for index, form in enumerate(raw_forms)]
    ids = [form['form_id'] for form in forms]
    if len(set(ids)) != len(ids):
        raise CryptoImportError('broker form ids must be unique')
    for key in manual:
        # The path exists for a form the engine cannot model, never as a way
        # around reconciling one it can.
        boxes = {box for form in forms if (form['account_id'], form['txn_id']) == key for box in form['unmodeled']}
        if not boxes:
            raise CryptoImportError('A manual disposal must be reported by a broker form that fills a box this '
                                    'engine does not model; reconcile any other disposal instead')
        reaching = sorted(boxes & _BOXES_REACHING_OTHER_SALES)
        if reaching:
            raise CryptoImportError(f'A manual disposal cannot isolate {reaching}: a wash sale moves its loss into '
                                    'a replacement lot, a tokenized security subjects its other sales to the '
                                    'wash-sale rules, and first-sale NFT proceeds block the tie-out gate on their '
                                    'own; prepare this import externally')
    return accounts, forms, _resolutions_input(raw_resolutions, ids), manual


def _accounts_input(raw_accounts, events) -> dict:
    known = {account for account, _ in events}
    accounts = {}
    for account, entry in raw_accounts.items():
        if _account_id(account, 'broker_reporting account') not in known:
            raise CryptoImportError('broker_reporting names an account with no transactions in this import')
        if (not isinstance(entry, dict)
                or not {'forms_received', 'evidence'} <= set(entry) <= {'forms_received', 'evidence', 'not_reported'}
                or not isinstance(entry['forms_received'], bool)):
            raise CryptoImportError('Each broker_reporting account needs boolean forms_received and an evidence reference')
        listed = entry.get('not_reported', [])
        if not isinstance(listed, list) or (listed and not entry['forms_received']):
            raise CryptoImportError('not_reported lists disposals missing from forms an account DID receive')
        listed = [_account_id(txn, 'not_reported txn_id') for txn in listed]
        if len(set(listed)) != len(listed) or any((account, txn) not in events for txn in listed):
            raise CryptoImportError('not_reported must name distinct transactions in that account')
        accounts[account] = {'forms_received': entry['forms_received'], 'not_reported': set(listed),
                             'evidence': _account_id(entry['evidence'], 'broker_reporting evidence reference')}
    return accounts


def _form_input(label, form, events, accounts, regime, tax_year, manual=None) -> dict:
    if not isinstance(form, dict):
        raise CryptoImportError(f'{label} must be an object')
    # First, because the type decides which keys the form must carry.
    if form.get('form_type') != regime['form_type']:
        raise CryptoImportError(f"{label}.form_type must be {regime['form_type']} for TY{tax_year}")
    # Box 8 can supply dates for FIFO as well as a specific identification.
    # The explicit lot-selection answer guards the entire import before matching.
    reviewed = _FORM_FIELDS_BY_TYPE[regime['form_type']]
    if reviewed and form.get('customer_acquisition_info') is not None and not isinstance(
            form['customer_acquisition_info'], bool):
        raise CryptoImportError(f'{label}.customer_acquisition_info must be boolean or null')
    unsupported = _FORM_UNSUPPORTED | _FORM_UNSUPPORTED_BY_TYPE[regime['form_type']]
    filled = sorted(key for key in unsupported & set(form) if form[key] not in (None, False, 0))
    option = (events.get((form['account_id'], form['txn_id']), {})
              if isinstance(form.get('account_id'), str) and isinstance(form.get('txn_id'), str) else {})
    if ('net_proceeds' in filled and form.get('net_proceeds') is True and option.get('option_treatment') == 'purchased_put'
            and option.get('option_txn_id') == form.get('txn_id')):
        # An evidenced put cost is already in this disposal's source proceeds.
        # A group alias must not borrow the first member's option evidence.
        filled.remove('net_proceeds')
    is_manual = (form.get('account_id'), form.get('txn_id')) in (manual or {})
    if filled and not is_manual:
        raise CryptoImportError(f'{label} fills {filled}, which this engine does not model; list that disposal '
                                'under broker_reporting.manual_disposals and prepare it by hand (a blank box '
                                'is copied as null, false or 0)')
    missing = (_FORM_FIELDS | unsupported | reviewed) - set(form)
    unknown = set(form) - _FORM_FIELDS - unsupported - reviewed
    if missing or unknown:
        # A summary parsed before a box was read cannot say it was blank.
        hint = ('; re-read each missing box from the form itself, never from a summary that did '
                'not capture it, and copy a blank one as null' if missing else '')
        raise CryptoImportError(f'{label} is missing {sorted(missing)} or names unknown {sorted(unknown)}{hint}')
    account = _account_id(form['account_id'], f'{label}.account_id')
    txn = _account_id(form['txn_id'], f'{label}.txn_id')
    if (account, txn) not in events:
        raise CryptoImportError(f'{label} names no transaction in that account')
    declared = accounts.get(account)
    if declared is not None and (not declared['forms_received'] or txn in declared['not_reported']):
        raise CryptoImportError(f"{label} contradicts its account's reviewed reporting declaration")
    reported, term, shown = form['basis_reported_to_irs'], form['term'], form['cost_basis']
    if not isinstance(reported, bool) or term not in (None, 'short', 'long'):
        raise CryptoImportError(f'{label} needs boolean basis_reported_to_irs and term short, long or null')
    if reported and shown is None:
        raise CryptoImportError(f'{label} reports basis to the IRS but shows no cost_basis')
    try:
        date_sold = datetime.date.fromisoformat(form['date_sold'])
    except (TypeError, ValueError):
        raise CryptoImportError(f'{label}.date_sold must be YYYY-MM-DD') from None
    quantity = _exact(form['quantity'], f'{label}.quantity')
    if quantity <= 0:
        raise CryptoImportError(f'{label}.quantity must be positive')
    return {'form_id': _account_id(form['form_id'], f'{label}.form_id'),
            'account_id': account, 'txn_id': txn, 'quantity': quantity, 'date_sold': date_sold,
            'asset': _req_str(form['asset'], f'{label}.asset').upper(),
            'proceeds': _form_cents(form['proceeds'], f'{label}.proceeds'),
            'cost_basis': None if shown is None else _form_cents(shown, f'{label}.cost_basis'),
            'reported': reported, 'term': term,
            'net_proceeds': form.get('net_proceeds') is True,
            'customer_acquisition_info': form.get('customer_acquisition_info'),
            'unmodeled': filled}


def _resolutions_input(raw_resolutions, ids) -> dict:
    resolutions = {}
    for form_id, entry in raw_resolutions.items():
        choices = ({key: value for key, value in entry.items() if key != 'evidence'}
                   if isinstance(entry, dict) and 'evidence' in entry else {})
        if (form_id not in ids or not choices
                or any(not isinstance(value, str) or value not in _RESOLUTIONS.get(key, ())
                       for key, value in choices.items())):
            raise CryptoImportError('A resolution names a form, a supported proceeds/basis/term '
                                    'treatment and an evidence reference')
        resolutions[form_id] = {**choices, 'evidence': _account_id(entry['evidence'], 'resolution evidence reference')}
    return resolutions


def _same_sale_date(broker: datetime.date, imported: datetime.date) -> bool:
    """§1.6045-1(d)(2)(i)(B)(3) asks a broker for "the sale date" and names no
    time zone, so a 1099-DA may date a sale in UTC or in the customer's zone.
    Every UTC offset is under 24 hours, so the same sale is dated the import's
    calendar day or the day either side — never in another year, whose form
    it would then be."""
    return abs((broker - imported).days) <= 1 and broker.year == imported.year


def _form_record(form, group, fifo_proceeds, fifo_basis, reasons, codes='', applied=None) -> dict:
    """One form's workpaper entry: the broker's and the FIFO figures side by
    side, recorded whether or not the form reconciles — an unresolved form is
    the one a reviewer most needs both figures for."""
    applied = applied or {}
    shown = form['cost_basis']
    return {
        'form_id': form['form_id'], 'account_id': group[0].account_id, 'txn_id': form['txn_id'],
        'term': form['term'], 'basis_reported_to_irs': form['reported'],
        'customer_acquisition_info': form['customer_acquisition_info'],
        'date_sold': group[0].date_sold, 'broker_date_sold': form['date_sold'].isoformat(),
        'broker_proceeds': _money(form['proceeds'] / 100), 'fifo_proceeds': _money(sum(fifo_proceeds) / 100),
        'broker_cost_basis': None if shown is None else _money(shown / 100),
        'fifo_cost_basis': _money(sum(fifo_basis) / 100),
        'codes': codes or None,
        'resolution': {key: value for key, value in applied.items() if key != 'evidence'} or None,
        'evidence': applied.get('evidence'),
        'status': 'unresolved' if reasons else 'reconciled', 'reason': '; '.join(reasons) or None}


@dataclass
class _FormTreatment:
    """One form's Form 8949 treatment, in cents per row of its group, built
    one difference at a time. `used` names the resolutions a difference took."""
    proceeds: 'list[int]'
    column_e: 'list[int]'
    costs: 'list[int]'
    corrections: 'list[int]'
    term_codes: 'list[set]'
    codes: set = field(default_factory=set)
    used: set = field(default_factory=set)
    reasons: 'list[str]' = field(default_factory=list)
    above: int = 0


def _form_groups(rows, forms):
    """Each form with the rows it reports, or the reason they cannot pair."""
    if len(forms) == 1:
        return [(forms[0], rows)], None
    terms = [form['term'] for form in forms]
    if None in terms or len(set(terms)) != len(terms):
        return None, 'split broker forms must each state a different holding period'
    groups = [(form, [row for row in rows if row.holding_period == form['term']]) for form in forms]
    if any(not group for _, group in groups) or sum(len(group) for _, group in groups) != len(rows):
        return None, "the broker's holding-period split differs from FIFO; review its lot identification"
    return groups, None


def _exact_net(raw) -> 'Fraction | None':
    """A record's exact USD amount net of its fee, before any rounding to
    cents; None for a record that carries no USD amount (a transfer leg)."""
    if raw.get('usd_amount') is None:
        return None
    return _exact(raw['usd_amount'], 'source USD amount') - _exact(raw.get('fee_usd', 0), 'source fee')


def _treat_proceeds(t, form, gap, weights, resolution, split_cent, fee_left,
                    premium_left=0, rounded_once=False) -> 'tuple[int, int]':
    """Column (d) for a proceeds gap (FIFO less form, in cents); returns the
    recorded fee and reviewed written-call premium still left to explain a
    later form's code E."""
    if gap and split_cent and abs(gap) <= 1:
        t.proceeds = _absorb(t.proceeds, -gap)  # split-form apportionment cent
    elif gap and rounded_once:
        # A group's fills each round to cents, while the broker rounded the
        # order once; the form equals that exact total rounded, so the gap is
        # those roundings alone and the form's figure stands.
        t.proceeds = _absorb(t.proceeds, -gap)
    elif gap > 0 and resolution.get('proceeds') == 'taxpayer_records':
        t.used.add('proceeds')  # i8949 column (d): enter the correct, higher proceeds
        t.above += gap
    elif gap < 0 and resolution.get('proceeds') == 'transaction_costs':
        t.used.add('proceeds')
        if -gap > fee_left:
            t.reasons.append(f'the form exceeds FIFO proceeds by {-gap / 100:,.2f}, more than the '
                             f'{fee_left / 100:,.2f} of recorded fees left to explain it')
        else:  # code E: form proceeds in (d), unreflected costs in (g)
            t.codes.add('E')
            fee_left += gap
            t.proceeds, t.costs = _spread(form['proceeds'], weights), _spread(gap, weights)
    elif gap > 0 and resolution.get('proceeds') == 'option_premium':
        t.used.add('proceeds')
        if gap > premium_left:
            t.reasons.append(f'FIFO proceeds exceed the form by {gap / 100:,.2f}, more than the '
                             f'{premium_left / 100:,.2f} of reviewed written-call premium left to explain it')
        else:  # i8949 code E: the form's proceeds in (d), the unreflected premium in (g)
            t.codes.add('E')
            premium_left -= gap
            t.proceeds, t.costs = _spread(form['proceeds'], weights), _spread(gap, weights)
    elif gap:
        t.reasons.append(f'proceeds differ from the form by {gap / 100:+,.2f}')
    return fee_left, premium_left


def _treat_basis(t, form, gap, weights, fifo_basis, resolution, split_cent) -> None:
    """Columns (e) and (g) for a basis gap (form less FIFO, in cents)."""
    if gap and split_cent and abs(gap) <= 1:  # split-form apportionment cent; (e) is the form's
        t.column_e = _absorb(fifo_basis, gap)
    elif gap and resolution.get('basis') == 'taxpayer_records':
        t.used.add('basis')
        t.codes.add('B')
        if form['reported']:  # (e) shows the reported basis; (g) corrects it
            t.column_e = _spread(form['cost_basis'], weights)
            t.corrections = [shown - fifo for shown, fifo in zip(t.column_e, fifo_basis)]
    elif gap:
        t.reasons.append(f'cost basis differs from the form by {gap / 100:+,.2f}')


def _treat_term(t, form, group, basis_gap, resolution) -> None:
    other = [i for i, row in enumerate(group) if form['term'] not in (None, row.holding_period)]
    spans = len({row.holding_period for row in group}) > 1
    if form['reported'] and spans and (other or basis_gap):
        if other and 'term' in resolution:
            t.used.add('term')  # a real difference, one a split basis cannot settle
        t.reasons.append('a basis-reported form spans both holding periods; its basis cannot be split')
    elif other and resolution.get('term') == 'taxpayer_records':
        t.used.add('term')
        for i in other:
            t.term_codes[i].add('T')
    elif other:
        t.reasons.append('holding period differs from the form')


def _routed_rows(form, group, t, regime, fifo_basis) -> 'dict[int, Form8949Row]':
    routed = {}
    # Allocate the form's figure in proportion to each row's own column (d),
    # which is the form's figure itself wherever the form reconciles: fills of
    # one order sell at different prices, so units would misattribute it.
    weights = t.proceeds if any(t.proceeds) else [row.quantity_exact for row in group]
    broker_proceeds = _spread(form['proceeds'], weights)
    for i, row in enumerate(group):
        adjustment = t.costs[i] + t.corrections[i]
        routed[id(row)] = replace(
            row, box=regime[row.holding_period][0 if form['reported'] else 1],
            proceeds=_money(t.proceeds[i] / 100), cost_basis=_money(t.column_e[i] / 100),
            adjustment=_money(adjustment / 100), adjustment_code=''.join(sorted(t.codes | t.term_codes[i])) or None,
            gain_loss=_money((t.proceeds[i] - t.column_e[i] + adjustment) / 100),
            form_id=form['form_id'], fifo_cost_basis=_money(fifo_basis[i] / 100),
            broker_proceeds=_money(broker_proceeds[i] / 100))
    return routed


def _reconcile_forms(rows, forms, event, regime, resolutions):
    """One disposal's rows against the broker forms that report it.

    Returns ({id(row): routed row} or None, ledger records, [(form_id,
    reason)], cents of proceeds reported above the forms). A difference is never absorbed:
    it stays a reason until a resolution names its Form 8949 treatment, and
    every treatment keeps the FIFO gain in column (h). FIFO figures are the
    rows' own cents, so a form that agrees leaves every row as it was.
    """
    groups, problem = _form_groups(rows, forms)
    if groups is None:
        return _unpaired(rows, forms, problem)
    # Split forms may each round their part a cent the other way; when the
    # forms' total is FIFO's total, the forms' own apportionment stands.
    # Basis adopts that split only when every form reports it to the IRS:
    # unreported basis stays FIFO, so mixing the two can change total gain.
    split = len(forms) > 1
    proceeds_cent = split and sum(form['proceeds'] for form in forms) == sum(_cents(row.proceeds) for row in rows)
    # A single disposal's rows already add to its once-rounded total, so this
    # can move only a fill group's cents, and only within one holding period:
    # cents moved between short and long rows would move gain between parts.
    rounded_once = (len({row.holding_period for row in rows}) == 1 and event.get('net') is not None
                    and forms[0]['proceeds'] == _cents(event['net']))
    basis_cent = split and (all(form['reported'] for form in forms) and sum(
        form['cost_basis'] for form in forms) == sum(_cents(row.cost_basis) for row in rows))
    # The only costs a form can leave unreflected are the fee the import
    # recorded; a larger gap is a wrong price or valuation, never code E.
    if event.get('option_cost') and len({form['net_proceeds'] for form in forms}) > 1:
        return _unpaired(rows, forms, 'mixed gross/net option forms require external preparation')
    fee_left = event['fee'] + (event.get('option_cost', 0) if not any(form['net_proceeds'] for form in forms) else 0)
    premium_left = event.get('option_premium_received', 0)
    routed: 'dict[int, Form8949Row]' = {}
    records: 'list[dict]' = []
    problems: 'list[tuple]' = []
    above = 0
    for form, group in groups:
        fifo_proceeds = [_cents(row.proceeds) for row in group]
        fifo_basis = [_cents(row.cost_basis) for row in group]
        reasons = [reason for reason, differs in (
            ('asset differs', form['asset'] != event['asset']),
            ('date sold differs', not _same_sale_date(form['date_sold'], event['date'])),
            ('units differ', form['quantity'] != sum(row.quantity_exact for row in group))) if differs]
        if reasons:
            records.append(_form_record(form, group, fifo_proceeds, fifo_basis, reasons))
            problems.append((form['form_id'], '; '.join(reasons)))
            continue
        resolution = resolutions.get(form['form_id'], {})
        weights = [row.quantity_exact for row in group]
        t = _FormTreatment(fifo_proceeds, fifo_basis, [0] * len(group), [0] * len(group), [set() for _ in group])
        fee_left, premium_left = _treat_proceeds(t, form, sum(fifo_proceeds) - form['proceeds'], weights,
                                                 resolution, proceeds_cent, fee_left, premium_left,
                                                 rounded_once)
        basis_gap = 0 if form['cost_basis'] is None else form['cost_basis'] - sum(fifo_basis)
        _treat_basis(t, form, basis_gap, weights, fifo_basis, resolution, basis_cent)
        _treat_term(t, form, group, basis_gap, resolution)
        stale = set(resolution) - {'evidence'} - t.used
        if stale:
            t.reasons.append(f'resolution {sorted(stale)} names no difference on this form')
        applied = {key: value for key, value in resolution.items() if key in t.used | ({'evidence'} if t.used else set())}
        records.append(_form_record(form, group, fifo_proceeds, fifo_basis, t.reasons,
                                    ''.join(sorted(t.codes.union(*t.term_codes))), applied))
        if t.reasons:
            problems.append((form['form_id'], '; '.join(t.reasons)))
            continue
        above += t.above
        routed.update(_routed_rows(form, group, t, regime, fifo_basis))
    if problems:
        _block_reconciled(records, problems)
        return None, records, problems, 0
    return routed, records, [], above


def _unpaired(rows, forms, problem):
    """Split forms that pair with no FIFO part: each is still recorded, set
    against the whole disposal's figures, and named in its own issue."""
    whole = ([_cents(row.proceeds) for row in rows], [_cents(row.cost_basis) for row in rows])
    return (None, [_form_record(form, rows, *whole, [problem]) for form in forms],
            [(form['form_id'], problem) for form in forms], 0)


def _block_reconciled(records, problems) -> None:
    """A disposal routes all its rows or none, so a form that reconciles on
    its own is still blocked when another form for the same sale is not, and
    its record must say so rather than read as done."""
    others = ', '.join(form_id for form_id, _ in problems)
    for record in records:
        if record['status'] == 'reconciled':
            record.update(status='blocked', reason=f'another form for this disposal is unresolved: {others}')


def _reporting_report(tax_year, regime, issues, **ledger) -> dict:
    return {'tax_year': tax_year,
            'form_type': regime and regime['form_type'], 'source': regime and regime['source'],
            'verified': regime and _REPORTING_VERIFIED,
            'complete': not issues, 'unresolved': issues,
            **{'accounts': {}, 'forms': [], 'not_reported': [], 'proceeds_above_forms': 0.0,
               'schedule_d_routing': {}, **ledger}}


def _issue(reason, account=None, txn=None, form=None) -> dict:
    return {'account_id': account, 'txn_id': txn, 'form_id': form, 'reason': reason}


def _reporting_inputs(reporting, events, regime, tax_year):
    if reporting is None:
        return {}, [], {}, {}
    if regime is None:
        raise CryptoImportError(
            'broker_reporting needs --year' if tax_year is None else
            f'TY{tax_year} has no reviewed Form 8949 reporting regime; broker_reporting cannot route it')
    return _broker_reporting_input(reporting, events, regime, tax_year)


def _stray_evidence(forms_by_txn, accounts, by_txn) -> 'list[dict]':
    """Evidence naming no filing-year disposal. A form or a declaration that
    no form covers a disposal is evidence about THAT disposal; one naming a
    purchase or another year's sale covers nothing."""
    issues = [_issue('the form reports no filing-year disposal in this import', *key, form['form_id'])
              for key in forms_by_txn.keys() - by_txn.keys() for form in forms_by_txn[key]]
    for account, entry in accounts.items():
        for txn in sorted(entry['not_reported'] - {txn for owner, txn in by_txn if owner == account}):
            issues.append(_issue('not_reported names no filing-year disposal in this import', account, txn))
    return issues


def _fill_group_rows(reporting, by_txn, events):
    """Link reviewed fills AFTER FIFO, preserving each row's original identity.

    A group is one same-day broker sale, not an optional annual aggregate.
    Combining raw events before matching would move intervening acquisitions
    or transfers and could change basis. Only the reporting lookup is grouped.
    """
    raw = reporting.get('fill_groups', []) if isinstance(reporting, dict) else []
    if not isinstance(raw, list):
        raise CryptoImportError('fill_groups must be a list of reviewed single-sale links')
    if not raw:
        return by_txn, events, []
    grouped, expanded, records, used = dict(by_txn), dict(events), [], set()
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) != {'account_id', 'group_id', 'txn_ids', 'evidence'}:
            raise CryptoImportError('Each fill group needs account_id, group_id, txn_ids and evidence')
        account = _account_id(entry['account_id'], 'fill group account_id')
        group = _account_id(entry['group_id'], 'fill group group_id')
        evidence = _account_id(entry['evidence'], 'fill group evidence')
        listed = entry['txn_ids']
        if not isinstance(listed, list) or len(listed) < 2:
            raise CryptoImportError('A fill group must link at least two distinct filing-year disposals')
        txns = [_account_id(txn, 'fill group txn_id') for txn in listed]
        keys = [(account, txn) for txn in txns]
        key = account, group
        if len(set(keys)) != len(keys) or used.intersection(keys):
            raise CryptoImportError('Fill groups must not repeat or overlap disposals')
        if key in expanded:
            raise CryptoImportError('A fill group_id must not collide with a transaction or another group')
        if any(member not in by_txn for member in keys):
            raise CryptoImportError('Every fill group member must be a filing-year disposal in that account')
        source = [events[member] for member in keys]
        if len({e['asset'] for e in source}) != 1 or len({e['date'] for e in source}) != 1:
            raise CryptoImportError('A fill group must be one same-asset, same-day broker sale')
        grouped[key] = [row for member in keys for row in by_txn[member]]
        nets = [e['net'] for e in source]
        expanded[key] = {**source[0], 'fee': sum(e['fee'] for e in source),
                         'net': None if None in nets else sum(nets)}
        for member in keys:
            del grouped[member]
        used.update(keys)
        records.append(dict(account_id=account, group_id=group, txn_ids=txns, evidence=evidence))
    return grouped, expanded, records


def _check_fill_group_evidence(groups, accounts, forms_by_txn, resolutions):
    for group in groups:
        account, alias, members = group['account_id'], group['group_id'], set(group['txn_ids'])
        if members.intersection(accounts.get(account, {}).get('not_reported', ())):
            raise CryptoImportError('A fill group contradicts a member declared not_reported')
        if any((account, member) in forms_by_txn for member in members):
            raise CryptoImportError('A grouped fill cannot also have a direct-member broker form')
        matching = forms_by_txn.get((account, alias), [])
        if not matching:
            raise CryptoImportError('Every fill group must be linked by a broker form txn_id')
        if any(form['form_id'] in resolutions for form in matching):
            # Quantity weights assume one disposal price. Different fills can
            # have different prices, fees and holding periods; spreading a
            # group-level correction by units could move gain between terms.
            raise CryptoImportError('Grouped fills require exact form reconciliation; '
                                    'group-level resolutions are not supported')


def _manual_record(account, txn, evidence, txn_rows, txn_forms) -> dict:
    """The workpaper for a disposal left for hand preparation. Each FIFO
    slice's term is fixed by its lot whatever the preparation does to the
    money columns, so each slice carries its term and its share of the forms'
    proceeds, spread as a routed disposal's would be, and the record keeps
    both shares and the FIFO basis per term for tie-out."""
    total = sum(form['proceeds'] for form in txn_forms)
    cents = [_cents(row.proceeds) for row in txn_rows]
    units = [row.quantity_exact or Fraction(0) for row in txn_rows]
    weights = cents if any(cents) else units if any(units) else [1] * len(txn_rows)
    shares = _spread(total, weights) if total else [0] * len(txn_rows)

    def by_term(values):
        return {period: _money(sum(value for row, value in zip(txn_rows, values)
                                   if row.holding_period == period))
                for period in ('short', 'long')}
    return {
        'account_id': account, 'txn_id': txn, 'evidence': evidence,
        'form_ids': [form['form_id'] for form in txn_forms],
        'unmodeled_boxes': sorted({box for form in txn_forms for box in form['unmodeled']}),
        'broker_proceeds': _money(total / 100),
        'broker_proceeds_by_term': by_term([share / 100 for share in shares]),
        'fifo_cost_basis': by_term([row.cost_basis for row in txn_rows]),
        # Keyed as the summary and tie-out key it, a fill group's group_id
        # included, so the slices merge as given.
        'fifo_rows': [{**_row_dict(replace(row, txn_id=txn, fifo_cost_basis=row.cost_basis,
                                           broker_proceeds=_money(share / 100))),
                       'holding_period': row.holding_period}
                      for row, share in zip(txn_rows, shares)]}


def _reconcile_broker_reporting(rows, tax_year, events, reporting):
    """Route filing-year account rows to Form 8949 boxes from explicit evidence.

    A row's box comes from a broker form that reports its disposal, or from
    the account's reviewed declaration that no form covers it — never from
    the absence of a form in the import. A row without that evidence keeps no
    box and leaves the report incomplete.
    """
    regime = _REPORTING_REGIMES.get(tax_year) if tax_year is not None else None
    by_txn: 'dict[tuple, list]' = {}
    for row in rows:
        by_txn.setdefault((row.account_id, row.txn_id), []).append(row)
    by_txn, events, fill_groups = _fill_group_rows(reporting, by_txn, events)
    accounts, forms, resolutions, manual = _reporting_inputs(reporting, events, regime, tax_year)
    forms_by_txn: 'dict[tuple, list]' = {}
    for form in forms:
        forms_by_txn.setdefault((form['account_id'], form['txn_id']), []).append(form)
    _check_fill_group_evidence(fill_groups, accounts, forms_by_txn, resolutions)
    issues = _stray_evidence(forms_by_txn, accounts, by_txn)
    if by_txn and regime is None:
        issues.append(_issue('Form 8949 routing needs the filing year (--year)' if tax_year is None else
                             f'TY{tax_year} has no reviewed Form 8949 reporting regime'))
        by_txn = {}
    routed, records, not_reported, above, manual_records, excluded = {}, [], [], 0, [], set()
    for (account, txn), txn_rows in by_txn.items():
        declared = accounts.get(account)
        if (account, txn) in manual:
            # Prepared by hand: FIFO has consumed its lots, but none of its
            # rows is routed or totalled here. The ledger still holds its
            # form, so tie-out fails until the hand-prepared row is merged.
            excluded.update(id(row) for row in txn_rows)
            manual_records.append(_manual_record(account, txn, manual[(account, txn)], txn_rows,
                                                 forms_by_txn.get((account, txn), [])))
        elif declared is None:
            issues.append(_issue('declare whether this account received broker forms for the filing year',
                                 account, txn))
        elif not declared['forms_received'] or txn in declared['not_reported']:
            routed.update({id(row): replace(row, box=regime[row.holding_period][2], fifo_cost_basis=row.cost_basis)
                           for row in txn_rows})
            not_reported.append({'account_id': account, 'txn_id': txn, 'evidence': declared['evidence']})
        elif (account, txn) not in forms_by_txn:
            issues.append(_issue('no broker form reports this disposal; add it, or list the disposal under '
                                 'not_reported', account, txn))
        else:
            new_rows, form_records, problems, extra = _reconcile_forms(
                txn_rows, forms_by_txn[(account, txn)], events[(account, txn)], regime, resolutions)
            records += form_records
            issues += [_issue(reason, account, txn, form_id) for form_id, reason in problems]
            if new_rows is not None:
                above += extra
                routed.update(new_rows)
    final = [routed.get(id(row)) or replace(row, fifo_cost_basis=row.cost_basis)
             for row in rows if id(row) not in excluded]
    return final, _reporting_report(
        tax_year, regime, issues, forms=records, not_reported=not_reported,
        **({'manual_disposals': manual_records} if manual_records else {}),
        accounts={account: {'forms_received': entry['forms_received'], 'evidence': entry['evidence'],
                            'not_reported': sorted(entry['not_reported'])} for account, entry in accounts.items()},
        proceeds_above_forms=_money(above / 100), schedule_d_routing=_schedule_d_routing(final),
        **({'fill_groups': fill_groups} if fill_groups else {}))


# The amount keys of a result.json Schedule D row, which a box's totals add
# into key for key: `cost`, not the Form 8949 row's `cost_basis`.
_ROUTED_AMOUNTS = ('proceeds', 'cost', 'adjustment', 'gain')


def _schedule_d_routing(rows) -> 'dict[str, dict]':
    """Each box's Schedule D row and its totals, keyed as that row is, the
    column (f) codes of its rows joined as that row's `adjustment_code`."""
    routing: 'dict[str, dict]' = {}
    codes: 'dict[str, set]' = {}
    for row in rows:
        if row.box is None:
            continue
        part, target, line = _SCHEDULE_D_TARGETS[row.box]
        entry = routing.setdefault(row.box, {'part': part, 'row': target, 'schedule_d_line': line,
                                             **dict.fromkeys(_ROUTED_AMOUNTS, 0), 'rows': 0,
                                             'broker_proceeds': None if row.broker_proceeds is None else 0})
        for key, value in zip(_ROUTED_AMOUNTS, (row.proceeds, row.cost_basis, row.adjustment, row.gain_loss)):
            entry[key] += _cents(value)
        if row.broker_proceeds is not None:
            entry['broker_proceeds'] += _cents(row.broker_proceeds)
        entry['rows'] += 1
        codes.setdefault(row.box, set()).update(row.adjustment_code or '')
    for box, entry in routing.items():
        for key in _ROUTED_AMOUNTS:
            entry[key] = _money(entry[key] / 100)
        if entry['broker_proceeds'] is not None:
            entry['broker_proceeds'] = _money(entry['broker_proceeds'] / 100)
        entry['adjustment_code'] = ''.join(sorted(codes[box])) or None
    return dict(sorted(routing.items()))


# --------------------------------------------------------------------------
# Aggregator
# --------------------------------------------------------------------------

_OPTION_SOURCES = {year: f'https://www.irs.gov/pub/irs-prior/p550--{year}.pdf'
                   for year in (2023, 2024, 2025)}


def _apply_option_premiums(transactions, raw_review, account_mode, tax_year):
    """Price only externally reviewed plain USD premiums, before any FIFO use.

    This is not an option classifier. The workpaper must exclude compensation,
    section 1256, straddles, holding-period adjustments and other special rules.
    Original source values are bound explicitly, including premiums already in
    the export, so the same cost cannot silently be applied a second time.
    """
    if raw_review is None:
        return transactions, None
    required = {'scope', 'all_accounts_and_prior_lots_reviewed', 'unsupported_items', 'evidence', 'events'}
    if (not isinstance(raw_review, dict) or set(raw_review) != required
            or raw_review['scope'] != 'plain_usd_premiums_no_other_tax_effects'
            or raw_review['all_accounts_and_prior_lots_reviewed'] is not True
            or raw_review['unsupported_items'] != []
            or not isinstance(raw_review['events'], list) or not raw_review['events']):
        raise CryptoImportError('Option review requires all accounts/prior lots reviewed, empty unsupported_items, evidence and events')
    evidence = _account_id(raw_review['evidence'], 'option scope evidence')
    if not account_mode or tax_year not in _REPORTING_REGIMES or tax_year not in _OPTION_SOURCES:
        raise CryptoImportError('Option review requires explicit account/transaction IDs and a reviewed filing year')
    indices = {}
    for index, raw in enumerate(transactions):
        if not isinstance(raw, dict):
            raise CryptoImportError('Option review requires transaction objects')
        key = (_account_id(raw.get('account_id'), 'source account_id'),
               _account_id(raw.get('txn_id'), 'source txn_id'))
        if key in indices:
            raise CryptoImportError('Option review requires unique account/transaction IDs')
        indices[key] = index
    # Table 4-3, Pub. 550 (reviewed TY2023-2025). These are signs, not rates.
    directions = {'purchased_call': ('buy', 1), 'written_put': ('buy', -1),
                  'written_call': ('sell', 1), 'purchased_put': ('sell', -1)}
    fields = {'account_id', 'txn_id', 'expected', 'treatment', 'premium_usd',
              'source_includes_premium', 'fees_exclude_premium', 'option_opened', 'evidence'}
    updated, records, used = list(transactions), [], set()
    for entry in raw_review['events']:
        if not isinstance(entry, dict) or set(entry) != fields:
            raise CryptoImportError('Option event needs source context, treatment, premium, inclusion answer, option_opened and evidence')
        key = (_account_id(entry['account_id'], 'option account_id'),
               _account_id(entry['txn_id'], 'option txn_id'))
        if key not in indices:
            raise CryptoImportError('Option event names no source transaction in that account')
        if key in used:
            raise CryptoImportError('Option events must not repeat a transaction')
        original, expected = transactions[indices[key]], entry['expected']
        context = {'date', 'asset', 'kind', 'quantity', 'usd_amount', 'fee_usd'}
        if not isinstance(expected, dict) or set(expected) != context:
            raise CryptoImportError('Option expected context must include date, asset, kind, quantity, usd_amount and fee_usd')
        for name in context:
            actual = original.get(name, 0) if name == 'fee_usd' else original.get(name)
            if name in {'date', 'asset', 'kind'}:
                agrees = expected[name] == actual
            else:
                agrees = _exact(expected[name], 'expected option value') == _exact(actual, 'source option value')
            if not agrees:
                raise CryptoImportError('Option source context is stale; re-read the original record')
        treatment = entry['treatment']
        if not isinstance(treatment, str) or treatment not in directions:
            raise CryptoImportError('Option treatment must be a reviewed purchased/written put/call exercise')
        kind, sign = directions[treatment]
        if original['kind'] != kind or _exact(original['quantity'], 'option quantity') <= 0:
            raise CryptoImportError('Option exercise requires a positive-quantity buy or direct USD sell of the underlying')
        included = entry['source_includes_premium']
        if type(included) is not bool:
            raise CryptoImportError('source_includes_premium must be an explicit boolean')
        if entry['fees_exclude_premium'] is not True:
            raise CryptoImportError('Confirm fees_exclude_premium true so the premium is not counted twice')
        premium = Fraction(_form_cents(entry['premium_usd'], 'option premium'), 100)
        if premium <= 0:
            raise CryptoImportError('Option premium must be positive')
        amount = _exact(original['usd_amount'], 'option source USD amount')
        base = amount - sign * premium if included else amount
        applied = amount if included else amount + sign * premium
        if base < 0 or applied < 0:
            raise CryptoImportError('Option premium leaves a negative underlying or adjusted amount')
        try:
            opened = datetime.date.fromisoformat(entry['option_opened'])
        except (ValueError, TypeError):
            raise CryptoImportError('option_opened must be YYYY-MM-DD') from None
        if entry['option_opened'] != opened.isoformat() or opened > _parse_dt(original['date'], 'option exercise date').date():
            raise CryptoImportError('option_opened must be an exact date no later than exercise')
        event_evidence = _account_id(entry['evidence'], 'option event evidence')
        updated[indices[key]] = {**original, 'usd_amount': _quantity_text(applied)}
        records.append({**entry, 'expected': dict(expected), 'evidence': event_evidence,
                        'applied_usd_amount': _quantity_text(applied)})
        used.add(key)
    return updated, dict(scope=raw_review['scope'], all_accounts_and_prior_lots_reviewed=True,
                         unsupported_items=[], source_url=_OPTION_SOURCES[tax_year], evidence=evidence, events=records)


def _check_put_holding_periods(rows, review, assets=None, inventory=None, transfers=None):
    """A purchased put's sale keeps its lots' long-term holding period only
    when no same-asset lot was held one year or less when the put was bought,
    or bought before it was exercised: Pub. 550's married-put rule makes such
    holdings short-term and resets their holding period. The engine models
    neither, so it refuses rather than file a wrong term for any of them."""
    if review is None:
        return
    for event in review['events']:
        if event['treatment'] != 'purchased_put':
            continue
        selected = [row for row in rows if (row.account_id, row.txn_id) == (event['account_id'], event['txn_id'])]
        opened = datetime.date.fromisoformat(event['option_opened'])
        if not selected or any(row.needs_basis or row.date_acquired == 'UNKNOWN' or
                _holding_period(datetime.date.fromisoformat(row.date_acquired), opened) != 'long' for row in selected):
            raise CryptoImportError('Purchased put needs external holding-period treatment: every disposed lot must already be long-term when the put was bought')
        if assets is None:
            continue
        asset = assets[(event['account_id'], event['txn_id'])]
        exercised = datetime.date.fromisoformat(selected[0].date_sold)
        if _married_put_lots(rows, assets, inventory or {}, asset, opened, exercised, transfers or []):
            raise CryptoImportError('Purchased put needs external holding-period treatment: a same-asset lot in some '
                                    'account was held one year or less when the put was bought, or bought before '
                                    'it was exercised (Pub. 550 married-put rule)')


def _married_put_lots(rows, assets, inventory, asset, opened, exercised, transfers=()) -> bool:
    """Whether any lot of `asset`, in any account, is one the married-put rule
    reaches: still held on `opened` with a short-term holding period then, or
    acquired after `opened` and no later than `exercised`. A lot is its slices
    across the rows, plus what inventory still holds, plus what an external
    send moved to a wallet the taxpayer still owns, keyed by its origin."""
    lots: 'dict[tuple, list]' = {}
    for row in rows:
        if assets.get((row.account_id, row.txn_id)) != asset:
            continue
        key = (row.origin_account_id, row.origin_txn_id, row.date_acquired)
        entry = lots.setdefault(key, [Fraction(0), Fraction(0)])
        entry[0] += row.quantity_exact or 0
        if datetime.date.fromisoformat(row.date_sold) < opened:
            entry[1] += row.quantity_exact or 0
    held_elsewhere = [entry['slices'] for entry in transfers if entry['external'] and entry['asset'] == asset]
    for held in [*inventory.values(), *({asset: slices} for slices in held_elsewhere)]:
        for lot in held.get(asset, []):
            key = (lot['origin_account_id'], lot['origin_txn_id'], lot['acquired_tax_date'] or 'UNKNOWN')
            exact = lot['quantity_exact']
            lots.setdefault(key, [Fraction(0), Fraction(0)])[0] += Fraction(int(exact['numerator']), int(exact['denominator']))
    for (_, _, acquired), (total, sold_before) in lots.items():
        if acquired == 'UNKNOWN':
            return True
        bought = datetime.date.fromisoformat(acquired)
        if opened < bought <= exercised:
            return True
        if bought <= opened and total > sold_before and _holding_period(bought, opened) != 'long':
            return True
    return False


def _tax_calendar_review(raw):
    if raw is None:
        return None
    required = {'calendar_label', 'all_account_dates_reviewed', 'evidence'}
    if (not isinstance(raw, dict) or not required <= set(raw) <= required | {'naive_timestamps_are_utc'}
            or raw['all_account_dates_reviewed'] is not True):
        raise CryptoImportError('Tax calendar review requires its label, evidence and all_account_dates_reviewed true')
    if 'naive_timestamps_are_utc' in raw and type(raw['naive_timestamps_are_utc']) is not bool:
        raise CryptoImportError('naive_timestamps_are_utc must be an explicit boolean')
    _account_id(raw['calendar_label'], 'reviewed calendar label')
    _account_id(raw['evidence'], 'tax calendar evidence')
    return dict(raw)


def _reviewed_tax_timestamp(value, *, naive_utc=False):
    if not isinstance(value, str):
        raise CryptoImportError('Tax date correction requires a full ISO timestamp with an offset, never a date alone')
    text = value.strip()
    text = (text.removesuffix(' UTC') + ('+00:00' if text.endswith(' UTC') else '')).replace('Z', '+00:00')
    match = re.fullmatch(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}'
                         r'(?::\d{2}(?:[.,](?P<fraction>\d+))?)?'
                         r'(?P<offset>[+-]\d{2}(?::?\d{2})?)?', text)
    if not match:
        raise CryptoImportError('Tax date correction requires a full ISO timestamp with an HH:MM UTC offset, never a date alone')
    offset_digits = match['offset'][1:].replace(':', '') if match['offset'] else ''
    if len(offset_digits) == 4 and int(offset_digits[2:]) >= 60:
        raise CryptoImportError('Tax date correction has an invalid UTC offset')
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        raise CryptoImportError('Tax date correction requires a valid ISO timestamp') from None
    if parsed.tzinfo is None:
        if not naive_utc:
            raise CryptoImportError('An original naive timestamp needs explicit UTC source confirmation; the corrected timestamp needs an offset')
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    elif not -12 * 3600 <= parsed.utcoffset().total_seconds() <= 14 * 3600:
        # Civil time runs from UTC-12:00 to UTC+14:00; an offset outside it
        # names no calendar and could only push a date across a year.
        raise CryptoImportError('Tax date correction offset must lie between -12:00 and +14:00')
    try:
        utc = parsed.astimezone(datetime.timezone.utc)
        fraction = Fraction('0.' + match['fraction']) if match['fraction'] else Fraction(0)
    except (ValueError, OverflowError):
        raise CryptoImportError('Tax date correction instant is outside the supported calendar range') from None
    # fromisoformat truncates submicrosecond digits. Compare the full decimal
    # fraction, not its rounded/truncated datetime, to certify the same instant.
    instant = utc.toordinal() * 86400 + utc.hour * 3600 + utc.minute * 60 + utc.second + fraction
    return parsed, instant


def _apply_source_corrections(transactions, corrections, account_mode, calendar_review=None):
    """Apply evidenced facts before FIFO, never a tolerance against a form.

    Expected context and values bind a correction to its original event.
    Dates need a separately reviewed calendar and an unchanged actual instant.
    The caller's source records stay intact.
    """
    calendar = _tax_calendar_review(calendar_review)
    if corrections is None:
        if calendar:
            raise CryptoImportError('Tax calendar review has no date corrections')
        return transactions, [], None, []
    if not isinstance(corrections, list):
        raise CryptoImportError('source_corrections must be a list of reviewed source facts')
    if not corrections:
        if calendar:
            raise CryptoImportError('Tax calendar review has no date corrections')
        return transactions, [], None, []
    if not account_mode:
        raise CryptoImportError('Source corrections require explicit account and transaction IDs')
    indices = {}
    for index, raw in enumerate(transactions):
        if not isinstance(raw, dict):
            raise CryptoImportError('Source corrections require transaction objects')
        key = (_account_id(raw.get('account_id'), 'source account_id'),
               _account_id(raw.get('txn_id'), 'source txn_id'))
        if key in indices:
            raise CryptoImportError('Source corrections require unique account/transaction IDs')
        indices[key] = index
    updated, records, used, date_changes = list(transactions), [], set(), False
    moves = []
    for correction in corrections:
        if not isinstance(correction, dict) or set(correction) != {
                'account_id', 'txn_id', 'expected', 'corrected', 'evidence'}:
            raise CryptoImportError('A source correction needs account_id, txn_id, expected, corrected and evidence')
        key = (_account_id(correction['account_id'], 'correction account_id'),
               _account_id(correction['txn_id'], 'correction txn_id'))
        evidence = _account_id(correction['evidence'], 'source correction evidence')
        if key not in indices:
            raise CryptoImportError('A source correction names no transaction in that account')
        if key in used:
            raise CryptoImportError('Source corrections must not repeat a transaction')
        values, expected = correction['corrected'], correction['expected']
        if not isinstance(values, dict) or not values or not set(values) <= {'quantity', 'usd_amount', 'date'}:
            raise CryptoImportError('A source correction changes only quantity, usd_amount or a reviewed tax date')
        context = {'date', 'asset', 'kind'}
        if not isinstance(expected, dict) or set(expected) != context | set(values):
            raise CryptoImportError('Expected source context needs date, asset, kind and each corrected field')
        raw = transactions[indices[key]]
        if any(expected[field] != raw.get(field) for field in context):
            raise CryptoImportError('Source correction context is stale; re-read the original record')
        kind = _req_str(raw.get('kind'), 'source kind').lower()
        if 'usd_amount' in values and kind in {'send', 'receive'}:
            raise CryptoImportError('Transfers do not use usd_amount; correct their basis/transfer evidence externally')
        for name, value in values.items():
            if name not in raw:
                raise CryptoImportError('A source correction must name an existing source field')
            if name == 'date':
                if calendar is None:
                    raise CryptoImportError('A tax date correction requires review of all account dates')
                original_date, original_instant = _reviewed_tax_timestamp(
                    raw[name], naive_utc=calendar.get('naive_timestamps_are_utc', False))
                corrected_date, corrected_instant = _reviewed_tax_timestamp(value)
                if original_instant != corrected_instant:
                    raise CryptoImportError('A tax date correction must preserve the exact UTC instant')
                days = abs((corrected_date.date() - original_date.date()).days)
                if days == 0 or days > 1:
                    raise CryptoImportError('A tax date correction must change the calendar date by one day')
                if corrected_date.year != original_date.year:
                    moves.append((key, original_date.year, corrected_date.year))
                date_changes = True
                continue
            if name == 'quantity' and not isinstance(value, str):
                # json.loads rounds a long number to a float before this sees
                # it, so the "full precision" fact would be recorded rounded.
                raise CryptoImportError('A corrected quantity must be a decimal string; '
                                        'a JSON number cannot carry every source decimal')
            try:
                original = _exact(raw[name], 'original source value')
                claimed = _exact(expected[name], 'expected source value')
                corrected = _exact(value, 'corrected source value')
            except OverflowError:
                raise CryptoImportError('Source correction values must be finite') from None
            if original != claimed:
                raise CryptoImportError('Source correction value is stale; re-read the original record')
            if corrected == original:
                raise CryptoImportError('A source correction must change each named value')
        updated[indices[key]] = {**raw, **values}
        used.add(key)
        records.append(dict(account_id=key[0], txn_id=key[1], expected=dict(expected),
                            corrected=dict(values), evidence=evidence))
    if calendar and not date_changes:
        raise CryptoImportError('Tax calendar review has no date corrections')
    return updated, records, calendar if date_changes else None, moves


def _timestamp_instant(value, naive_utc):
    """A recorded timestamp as an aware instant, or None when it has no
    calendar to re-read: a date alone, or a naive time nobody confirmed was
    UTC."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    text = (text.removesuffix(' UTC') + ('+00:00' if text.endswith(' UTC') else '')).replace('Z', '+00:00')
    if not re.match(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}', text):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.timezone.utc) if naive_utc else None
    return parsed


def _check_reviewed_calendar(rows_all, transactions, records, calendar) -> None:
    """A corrected date moves one record to another calendar. Near the
    one-year line, a row whose two dates are read in different calendars can
    flip between short and long term while each date is individually right.
    Within three days of it (two offsets put one instant at most two calendar
    days apart, and a February 29 anniversary adds a third), both dates the
    row uses must be what both instants read in one of the two offsets. The
    comparison is with the dates the row uses, so a timestamp whose recorded
    date and instant disagree refuses as well."""
    corrected = {(r['account_id'], r['txn_id']) for r in records if 'date' in r['corrected']}
    by_key = {(t['account_id'], t['txn_id']): t for t in transactions}
    naive_utc = calendar.get('naive_timestamps_are_utc', False)
    window = datetime.timedelta(days=3)
    for row in rows_all:
        sale, origin = (row.account_id, row.txn_id), (row.origin_account_id, row.origin_txn_id)
        if row.date_acquired == 'UNKNOWN' or not ({sale, origin} & corrected):
            continue
        acquired, sold = (datetime.date.fromisoformat(row.date_acquired),
                          datetime.date.fromisoformat(row.date_sold))
        if _holding_period(acquired, sold - window) == _holding_period(acquired, sold + window):
            continue
        source = by_key.get(origin, {})
        # The importer strips and lowercases kinds; read them the same way or
        # a padded ' receive' compares its transfer date, not its acquisition.
        receive = str(source.get('kind', '')).strip().lower() == 'receive'
        acquired_raw = source.get('acquired') if receive else source.get('date')
        ends = (_timestamp_instant(by_key.get(sale, {}).get('date'), naive_utc),
                _timestamp_instant(acquired_raw, naive_utc))
        if None in ends or not any(
                (ends[0].astimezone(zone).date(), ends[1].astimezone(zone).date()) == (sold, acquired)
                for zone in {end.tzinfo for end in ends}):
            raise CryptoImportError('A reviewed tax date leaves a sale within three days of its one-year holding '
                                    'period with dates that no one calendar gives; correct the other date into '
                                    'the same calendar, or prepare that sale externally')


def _prior_years_corrected(rows_all, records, tax_year) -> 'list[int]':
    """Earlier years whose disposals a correction changes: the corrected sale
    itself, or a sale that consumed a corrected acquisition. Those returns are
    usually filed, and the corrected history now disagrees with them."""
    if tax_year is None:
        return []
    corrected = {(r['account_id'], r['txn_id']) for r in records}
    return sorted({int(row.date_sold[:4]) for row in rows_all
                   if int(row.date_sold[:4]) < tax_year
                   and ((row.account_id, row.txn_id) in corrected
                        or (row.origin_account_id, row.origin_txn_id) in corrected)})


def build_crypto_import(transactions: 'list[dict]', *,
                        tax_year: 'int | None' = None,
                        specific_identification: bool | None = None,
                        per_account: bool = False,
                        account_aware: bool | None = None,
                        transfer_links: dict | None = None,
                        wa_domicile_by_date: "dict | None" = None,
                        broker_reporting: dict | None = None) -> dict:
    """Build the full crypto-import result from a list of normalized transaction dicts.

    Returns a dict with form_8949 (short/long row lists), schedule_d totals,
    ordinary_income (total + per-event breakdown), digital_asset_question, crypto_summary
    (per-asset realized gain), needs_basis (disposals lacking an acquisition), and caveats.
    broker_reporting is the reviewed broker-form evidence that routes each reported
    row to a Form 8949 box; without it no row carries a box (AA41).
    Its source_corrections replaces evidenced quantity/USD facts before matching,
    bound to the original account event and retained in the output workpaper.
    specific_identification must be explicitly False before any FIFO lot removal.
    True requires external lot-selection preparation; None never means FIFO.
    """
    if not isinstance(transactions, list):
        raise CryptoImportError("transactions must be a JSON array.")

    if tax_year is not None and (isinstance(tax_year, bool) or not isinstance(tax_year, int)
                                 or not 1 <= tax_year <= 9999):
        raise CryptoImportError('tax_year must be a valid integer calendar year')
    if not isinstance(per_account, bool):
        raise CryptoImportError('per_account must be boolean')
    tagged = any(isinstance(t, dict) and 'account_id' in t for t in transactions)
    if account_aware is not None and not isinstance(account_aware, bool):
        raise CryptoImportError('account_aware must be boolean or absent')
    if tagged and account_aware is False:
        raise CryptoImportError('Tagged accounts cannot use a universal FIFO pool')
    account_mode = tagged or account_aware is True or transfer_links is not None
    corrections = (broker_reporting.get('source_corrections')
                   if isinstance(broker_reporting, dict) else None)
    calendar_review = (broker_reporting.get('tax_calendar_review')
                       if isinstance(broker_reporting, dict) else None)
    transactions, correction_records, reviewed_calendar, date_moves = _apply_source_corrections(
        transactions, corrections, account_mode, calendar_review)
    option_review = broker_reporting.get('option_exercise_review') if isinstance(broker_reporting, dict) else None
    if isinstance(broker_reporting, dict) and 'option_exercise_review' in broker_reporting and option_review is None:
        raise CryptoImportError('option_exercise_review must be a complete reviewed object, never null')
    transactions, reviewed_options = _apply_option_premiums(transactions, option_review, account_mode, tax_year)
    if reviewed_options and isinstance(broker_reporting.get('fill_groups'), list):
        sales = {(event['account_id'], event['txn_id']) for event in reviewed_options['events']
                 if event['treatment'] in {'written_call', 'purchased_put'}}
        for group in broker_reporting['fill_groups']:
            if isinstance(group, dict) and isinstance(group.get('txn_ids'), list) and any(
                    (group.get('account_id'), txn) in sales for txn in group['txn_ids']
                    if isinstance(group.get('account_id'), str) and isinstance(txn, str)):
                raise CryptoImportError('Grouped option-caused disposals require external premium allocation; use individual forms')
    if isinstance(broker_reporting, dict) and {'source_corrections', 'tax_calendar_review', 'option_exercise_review'} & set(broker_reporting):
        broker_reporting = dict(broker_reporting)
        broker_reporting.pop('source_corrections', None)  # Consumed before the broker-form validator.
        broker_reporting.pop('tax_calendar_review', None)
        broker_reporting.pop('option_exercise_review', None)
    txns = [_coerce_transaction(t, i) for i, t in enumerate(transactions)]
    if specific_identification is not None and not isinstance(specific_identification, bool):
        raise CryptoImportError('specific_identification must be boolean or null')
    removes_lots = any(t.kind in ('sell', 'spend', 'trade') or
                       (t.kind == 'send' and (account_mode or per_account)) for t in txns)
    # Only removing lots depends on their selection; rewards and buys still price.
    if removes_lots and specific_identification is True:
        raise CryptoImportError('Specific identification is not modeled by this FIFO engine; '
                                'prepare the selected lots and Form 8949 externally. '
                                'Acknowledging a capability limitation does not enable FIFO pricing.')
    if removes_lots and specific_identification is not False:
        raise CryptoImportError('specific_identification is unanswered: ask about lot selection at '
                                'every broker and wallet, including standing instructions and prior '
                                'history. Record an explicit false only after FIFO is confirmed.')
    if account_mode:
        txns = [replace(t, date=_account_datetime(raw['date']),
                        acquired_date=_account_datetime(raw['acquired']) if raw.get('acquired') else None)
                for raw, t in zip(transactions, txns)]

    acquisitions: 'list[Acquisition]' = []
    disposals: 'list[Disposal]' = []
    all_incomes: 'list[IncomeEvent]' = []
    if not account_mode:
        for t in txns:
            a, d, i = expand_transaction(t, per_account=per_account)
            acquisitions.extend(a)
            disposals.extend(d)
            all_incomes.extend(i)

    # FIFO runs over ALL years so lot consumption is chronologically correct (a prior-year
    # sale consumes the oldest lot before a filing-year sale sees it). But only events whose
    # date falls in `tax_year` belong on THIS return — filter the reported rows/income to
    # the filing year, keeping prior-year acquisitions purely for basis. Out-of-year events
    # are excluded from every total (not just caveated) and reported as a count. When
    # tax_year is None (no filtering requested), everything is reported. (Codex P1, PR #23.)
    account_report = None
    if account_mode:
        rows_all, all_incomes, disposals, account_report = _match_account_fifo(transactions, txns, transfer_links)
    else:
        rows_all = match_lots(acquisitions, disposals)
    _check_put_holding_periods(
        rows_all, reviewed_options,
        assets={(raw['account_id'], raw['txn_id']): txn.asset for raw, txn in zip(transactions, txns)}
        if account_mode else None,
        inventory=account_report['inventory'] if account_report else None,
        transfers=account_report['transfer_ledger'] if account_report else None)
    # FIFO orders by instant while each row's dates are read in its own
    # record's offset, so records exported in different offsets can fund a
    # sale from a lot whose printed acquisition date is later than the sale's.
    inverted = next((row for row in rows_all
                     if row.date_acquired != 'UNKNOWN' and row.date_acquired > row.date_sold), None)
    if inverted is not None:
        where = (f' (sale {inverted.account_id}/{inverted.txn_id}, lot {inverted.origin_account_id}/'
                 f'{inverted.origin_txn_id})' if inverted.txn_id else '')
        raise CryptoImportError(f'An acquisition date falls after its disposal{where}: the records are read in '
                                'different UTC offsets; restate them in one calendar and reconcile all account dates')
    year_moves = []
    if reviewed_calendar:
        _check_reviewed_calendar(rows_all, transactions, correction_records, reviewed_calendar)
        taxable_keys = ({(r.account_id, r.txn_id) for r in rows_all}
                        | {(e.account_id, e.txn_id) for e in all_incomes})
        year_moves = [move for move in date_moves if move[0] in taxable_keys]
        if any(new > old for _, old, new in year_moves):
            # The earlier year's import dates the event in that year. Without
            # this correction it reports it there, so the later return would
            # report it again; with it, the earlier return excludes it while
            # the later import, which cannot carry it, re-dates it back:
            # reported twice or not at all.
            raise CryptoImportError('A reviewed tax date cannot move a disposal or income event into a later '
                                    'year; report it in the year its import dates it, or prepare the '
                                    'year-end case externally')
    taxable_disposals = [d for d in disposals if d.taxable]  # excludes non-taxable `send`
    if tax_year is not None:
        rows = [r for r in rows_all if int(r.date_sold[:4]) == tax_year]
        incomes = [e for e in all_incomes if e.received.year == tax_year]
        reported_disposals = [d for d in taxable_disposals if d.disposed.year == tax_year]
        off_year = (len(rows_all) - len(rows)) + (len(all_incomes) - len(incomes))
    else:
        rows = rows_all
        incomes = all_incomes
        reported_disposals = taxable_disposals
        off_year = 0
    if account_mode:
        events = {(raw['account_id'], raw['txn_id']): {'asset': txn.asset, 'fee': _cents(txn.fee_usd),
                                                        'date': _parse_dt(raw['date'], 'date').date(),
                                                        'net': _exact_net(raw)}
                  for raw, txn in zip(transactions, txns)}
        for event in reviewed_options['events'] if reviewed_options else []:
            record = events[(event['account_id'], event['txn_id'])]
            record.update(option_treatment=event['treatment'], option_txn_id=event['txn_id'])
            if event['treatment'] == 'purchased_put':
                record['option_cost'] = _form_cents(event['premium_usd'], 'reviewed put cost')
            elif event['treatment'] == 'written_call':
                record['option_premium_received'] = _form_cents(event['premium_usd'], 'reviewed call premium')
        rows, reporting = _reconcile_broker_reporting(rows, tax_year, events, broker_reporting)
    else:
        if broker_reporting is not None:
            raise CryptoImportError('broker_reporting needs an account-aware import with explicit account '
                                    'and transaction IDs')
        reporting = _reporting_report(tax_year, None, [_issue(
            'Form 8949 routing needs an account-aware import with explicit account '
            'and transaction IDs')] if rows else [])

    short_rows = [r for r in rows if r.holding_period == "short"]
    long_rows = [r for r in rows if r.holding_period == "long"]

    sched_d = {
        "short_term": _totals(short_rows),
        "long_term": _totals(long_rows),
    }
    sched_d["net_gain"] = _money(
        sched_d["short_term"]["gain"] + sched_d["long_term"]["gain"])

    # Ordinary income from receipts (staking/rewards/etc.) — routes to Schedule 1 line 8
    # by default (mining/staking-as-a-business → Schedule C + SE tax, flagged for the user).
    income_total = _money(sum(e.fmv for e in incomes))
    income_breakdown: 'dict[str, float]' = {}
    for e in incomes:
        income_breakdown[e.income_type] = _money(
            income_breakdown.get(e.income_type, 0.0) + e.fmv)

    # Per-asset realized gain summary (capital only).
    summary: 'dict[str, dict]' = {}
    for r in rows:
        asset = r.description.split(" ", 1)[1] if " " in r.description else r.description
        s = summary.setdefault(asset, {"proceeds": 0.0, "cost_basis": 0.0, "gain": 0.0,
                                       "rows": 0})
        s["proceeds"] = _money(s["proceeds"] + r.proceeds)
        s["cost_basis"] = _money(s["cost_basis"] + r.cost_basis)
        s["gain"] = _money(s["gain"] + r.gain_loss)
        s["rows"] += 1

    needs_basis = [
        {"description": r.description, "date_sold": r.date_sold, "proceeds": r.proceeds}
        for r in rows if r.needs_basis
    ]

    # Digital-asset question: Yes if any REPORTED (filing-year) disposal OR income receipt;
    # else No. Uses the filtered sets so an out-of-year-only history doesn't force a Yes on
    # a year with no actual digital-asset activity. (rows are disposals; incomes is filtered.)
    # A disposal left for manual preparation is still a disposal this year.
    digital_asset = "Yes" if (rows or incomes or reporting.get('manual_disposals')) else "No"

    result = {
        "schema": SCHEMA,
        "digital_asset_question": digital_asset,
        "form_8949": {
            "part_i_short_term": [_row_dict(r) for r in short_rows],
            "part_ii_long_term": [_row_dict(r) for r in long_rows],
        },
        "schedule_d": sched_d,
        "ordinary_income": {
            "total": income_total,
            "by_type": income_breakdown,
            "events": [
                {"asset": e.asset, "quantity": e.quantity, "fmv": e.fmv,
                 "date": e.received.isoformat(), "income_type": e.income_type,
                 **({"account_id": e.account_id, "txn_id": e.txn_id} if e.account_id is not None else {})}
                for e in incomes
            ],
            "routing_note": ("Ordinary income at FMV on receipt (Schedule 1 line 8). "
                             "If mining/staking is a trade or business, route to "
                             "Schedule C + SE tax instead — not auto-classified in v1."),
        },
        "crypto_summary": {
            "per_asset": summary,
            "realized_capital_gain": sched_d["net_gain"],
            "ordinary_income": income_total,
            "disposals": len(reported_disposals),
            "needs_basis_count": len(needs_basis),
        },
        "needs_basis": needs_basis,
        "caveats": _caveats(needs_basis, off_year=off_year, tax_year=tax_year),
    }

    if account_report is not None:
        result['account_fifo'] = account_report
        result['crypto_summary'].update(basis_mode='account_fifo', complete=account_report['complete'])
        result['crypto_summary']['unresolved_inputs'] = account_report['unresolved_inputs']
        result['caveats'] = [c for c in result['caveats'] if 'ONE per-asset FIFO pool' not in c]
        result['caveats'].append('FIFO pools are segregated by explicit account_id; reviewed transfers carry original lot slices.')
        if not account_report['complete']:
            result['caveats'].insert(0, 'BLOCKED: unresolved account inventory/transfer evidence; do not merge these diagnostic totals into the return.')
    elif not per_account and (tax_year or max((t.date.year for t in txns), default=0)) >= 2025:
        # Statutory transition year, not an annual rate. Rev. Proc. 2024-28.
        result['crypto_summary'].update(basis_mode='legacy_universal', complete=False,
                                       unresolved_inputs=['Explicit wallet/account allocation is required from 2025'])
        result['caveats'].insert(0, 'BLOCKED: universal basis cannot establish wallet/account FIFO from 2025; supply explicit account IDs.')
    result['broker_reporting'] = reporting
    if correction_records:
        result['source_corrections'] = correction_records
        result['caveats'].append(f'{len(correction_records)} reviewed source correction(s) applied before FIFO; '
                                 'preserve the source_corrections JSON workpaper and its evidence.')
    if reviewed_calendar:
        result['tax_calendar_review'] = reviewed_calendar
        result['caveats'].append('Filing dates follow the externally reviewed tax calendar; '
                                 'preserve tax_calendar_review with the source-correction workpaper.')
    if year_moves:
        pairs = ', '.join(sorted({f'TY{old} to TY{new}' for _, old, new in year_moves}))
        result['caveats'].insert(0, f'YEAR MOVE: a reviewed tax date moves {len(year_moves)} disposal or income '
                                    f'event(s) from {pairs}. Every import covering those years must carry the '
                                    'same tax_calendar_review and source_corrections, or one year reports the '
                                    'event again; review a filed return among them for /tax-amend.')
    if reviewed_options:
        result['crypto_summary']['option_exercise_review'] = reviewed_options
        result['caveats'].append('Plain USD option premiums follow the external scope/exercise review; '
                                 'preserve option_exercise_review and the normalized source for tie-out recomputation.')
    # A reviewed premium changes earlier-year rows exactly as a correction
    # does, and those returns were filed pricing the sale without it.
    prior = _prior_years_corrected(
        rows_all, correction_records + (reviewed_options['events'] if reviewed_options else []), tax_year)
    if prior:
        result['caveats'].insert(0, f"PRIOR-YEAR RETURN: a source correction or reviewed option premium changes "
                                    f"disposals reported for TY{', TY'.join(map(str, prior))}; review whether that "
                                    'return needs Form 1040-X (/tax-amend) so both years use the same basis.')
    result['crypto_summary'].update(reporting_complete=reporting['complete'],
                                    reporting_unresolved=reporting['unresolved'],
                                    reporting_proceeds_above_forms=reporting['proceeds_above_forms'])
    manual = reporting.get('manual_disposals', [])
    if manual:
        if reviewed_options:
            # Tie-out rebuilds an option return from this source and compares
            # every account row, which a hand-prepared row cannot match.
            raise CryptoImportError('An option-reviewed import cannot also leave a disposal for manual '
                                    'preparation; prepare that import externally')
        result['crypto_summary']['manual_disposals'] = [
            {'account_id': entry['account_id'], 'txn_id': entry['txn_id'],
             'broker_proceeds': entry['broker_proceeds'],
             'broker_proceeds_by_term': entry['broker_proceeds_by_term'],
             'fifo_cost_basis': entry['fifo_cost_basis']} for entry in manual]
        boxes = sorted({box for entry in manual for box in entry['unmodeled_boxes']})
        result['caveats'].insert(0, f'MANUAL: {len(manual)} disposal(s) carry a broker-form box this engine '
                                    f"does not model ({', '.join(boxes)}); they are left out of Form 8949 and "
                                    'Schedule D here. Prepare each by hand from broker_reporting.manual_disposals '
                                    'and merge its row before tie-out, which fails until it is on the return.')
    if not reporting['complete']:
        result['caveats'].insert(0, 'BLOCKED: Form 8949 reporting is unresolved; reconcile broker forms '
                                    '(broker_reporting.unresolved) before merging rows into a return.')
    elif rows:
        result['caveats'].append(
            f"Form 8949 boxes route from reviewed TY{tax_year} Form {reporting['form_type']} evidence; a "
            "broker-form difference carries its column (f) code and column (g) adjustment, so column (h) "
            "keeps the FIFO gain.")
    if wa_domicile_by_date is not None:
        result["washington_allocation"] = washington_crypto_allocation(
            result, wa_domicile_by_date)
    return result


def washington_crypto_allocation(result: dict, domicile_by_date: dict) -> dict:
    """LT crypto domicile workpaper; no tax/deduction or return-scope inference.

    Signed nonallocated rows are already in federal LT totals. A later LT sale
    of staking coins is included; staking income upon receipt is not.
    """
    from agents.lib.projection import _normalize_state_code
    rows = result["form_8949"]["part_ii_long_term"]
    dates = {row["date_sold"] for row in rows}
    if not isinstance(domicile_by_date, dict) or set(domicile_by_date) != dates:
        raise CryptoImportError("WA domicile map must cover exactly every LT disposal date; never prorate.")
    states = {}
    for date, state in domicile_by_date.items():
        code = _normalize_state_code(state)
        if code is None:
            raise CryptoImportError("Every LT sale date needs a valid explicit domicile state.")
        states[date] = code
    allocated = 0.0
    adjustments = []
    for i, row in enumerate(rows):
        gain = _num(row["gain_loss"], "LT crypto gain/loss", allow_negative=True)
        if states[row["date_sold"]] == "WA":
            allocated += gain
        else:
            adjustments.append(dict(id=f"crypto-lt-{i}", reason="nonallocated", gain_loss=gain))
    return {"allocated_lt_gain_loss": _money(allocated), "adjustments": adjustments,
            "domicile_by_date": states,
            "note": "LT crypto only, domicile at disposal. ST and receipt income excluded. "
                    "Merge federal LT totals first; do not add this base again. "
                    "Part-year/nonresident WA returns remain unsupported."}


def _totals(rows: 'list[Form8949Row]') -> dict:
    return {
        "proceeds": _money(sum(r.proceeds for r in rows)),
        "cost_basis": _money(sum(r.cost_basis for r in rows)),
        "adjustment": _money(sum(r.adjustment for r in rows)),
        "gain": _money(sum(r.gain_loss for r in rows)),
        "rows": len(rows),
    }


def _row_dict(r: Form8949Row) -> dict:
    return {
        "description": r.description,
        "date_acquired": r.date_acquired,
        "date_sold": r.date_sold,
        "proceeds": r.proceeds,
        "cost_basis": r.cost_basis,
        "gain_loss": r.gain_loss,
        "box": r.box,
        "needs_basis": r.needs_basis,
        **({"account_id": r.account_id, "txn_id": r.txn_id,
            "origin_account_id": r.origin_account_id, "origin_txn_id": r.origin_txn_id,
            "adjustment_code": r.adjustment_code, "adjustment": r.adjustment,
            "fifo_cost_basis": r.fifo_cost_basis, "form_id": r.form_id,
            "broker_proceeds": r.broker_proceeds}
           if r.account_id is not None else {}),
    }


def _caveats(needs_basis: list, *, off_year: int = 0,
             tax_year: 'int | None' = None) -> 'list[str]':
    caveats = [
        "Crypto is PROPERTY (IRS Notice 2014-21): the wash-sale rule (§1091) does NOT "
        "apply — unlike /tax-harvest for equities. Losses are not disallowed by a "
        "re-purchase within 30 days.",
        "Cost basis matched FIFO (the IRS default absent a contemporaneous "
        "specific-identification election). HIFO/LIFO are not modeled in v1.",
        "v1 uses ONE per-asset FIFO pool across all provided transactions (universal "
        "basis). Rev. Proc. 2024-28 requires per-wallet/per-account basis from 2025 — "
        "use explicit account IDs; --per-account output is diagnostic and routes no Form 8949 box.",
        "Stablecoins are digital assets: a market quoted in a stablecoin (USDT/USDC/DAI/"
        "GUSD/TUSD/BUSD/PYUSD) is a crypto-to-crypto disposition (use the JSON 'trade' path), "
        "not a USD trade, and is rejected. A stablecoin whose ticker ENDS in 'USD' "
        "(TUSD/BUSD/PYUSD/GUSD) is disambiguated from fiat USD by recognizing the base asset "
        "(ETHTUSD = ETH/TUSD is rejected; XBTUSD = XBT/USD is fiat); the rare residual is a "
        "pair where an UNRECOGNIZED base trades against an end-USD stablecoin, which is read "
        "as USD — normalize via 'trade' if that applies.",
        "Crypto-to-crypto trades ARE taxable dispositions and are included. Internal "
        "transfers between your own wallets (kind 'send'/'receive') are non-taxable — "
        "ensure both legs are not double-counted as a sale + buy.",
        "NFTs/collectible 28% rate, DeFi/liquidity-pool events, §1256 futures, and margin "
        "are not modeled in v1.",
    ]
    if off_year:
        caveats.insert(0,
            f"ℹ {off_year} taxable event(s) (sell/trade/spend/income) fall OUTSIDE tax "
            f"year {tax_year} and were EXCLUDED from the totals above — they belong on a "
            f"different year's return. (Prior-year acquisitions are kept and used for basis; "
            f"only the reported rows/income are filtered to {tax_year}.)")
    if needs_basis:
        caveats.insert(0,
            f"⚠ {len(needs_basis)} disposal(s) had NO acquisition on record and were "
            f"assigned $0 cost basis — this OVER-states the gain. Supply the missing "
            f"acquisitions (or a 'receive' with basis_usd) and re-run. See needs_basis.")
    return caveats


# --------------------------------------------------------------------------
# Exchange CSV adapters
# --------------------------------------------------------------------------
# Each adapter maps an exchange's raw rows (list[list[str]] from csv.reader) into a list
# of normalized transaction dicts (the same shape _coerce_transaction accepts). They are
# HEADER-NAME driven (case-insensitive, order-independent) so a reordered/extra column
# does not break parsing. Real exports vary and change over time; the /tax-parse-crypto
# skill bridges variants to these canonical headers via a subagent when needed.

# Only true US dollars are fiat. Stablecoins (USDT/USDC/DAI/…) are DIGITAL ASSETS per IRS
# guidance, so a pair quoted in a stablecoin is a crypto-to-crypto swap (a reportable
# disposition of the stablecoin), NOT a USD buy/sell — treating them as fiat would drop the
# stablecoin leg and under-report. (Codex P2, PR #23.)
_KRAKEN_USD = {"USD", "ZUSD"}
# Stablecoin quote tickers that do NOT end in "USD" — no suffix collision with fiat USD.
_KRAKEN_STABLECOINS = {"USDT", "USDC", "DAI"}
# Stablecoins whose ticker ITSELF ends in "USD" (TUSD/BUSD/PYUSD/GUSD). These are textually
# ambiguous with a fiat-USD pair whose base ends in the preceding letters (XBTUSD, DOTUSD,
# BNBUSD all end "...USD"), so we accept the stablecoin split ONLY when the residual base is a
# RECOGNIZED asset: ETHTUSD → ETH (known) is ETH/TUSD; XBTUSD → XB (unknown) is XBT/USD.
# (Codex P1, PR #23.)
_KRAKEN_USD_STABLECOINS = {"TUSD", "BUSD", "PYUSD", "GUSD"}
# Recognized Kraken base assets (aliased forms) — used only to disambiguate the end-USD
# stablecoin case above. Need not be exhaustive: a base absent here just falls through to the
# fiat-USD reading (an unrecognized base trading against an end-USD stablecoin is the rare,
# documented residual). The bogus fragments of fiat pairs ("XB","DO","BN","ETHT") are NOT
# real tickers, so they never appear here.
_KNOWN_KRAKEN_BASES = {
    "BTC", "ETH", "LTC", "XRP", "BCH", "ADA", "DOT", "SOL", "DOGE", "MATIC", "LINK", "XLM",
    "ATOM", "ALGO", "AVAX", "UNI", "AAVE", "XMR", "ETC", "ZEC", "TRX", "XTZ", "EOS", "FIL",
    "NEAR", "APT", "ARB", "OP", "INJ", "SUI", "TIA", "SHIB", "MANA", "SAND", "GRT", "CRV",
    "MKR", "SNX", "COMP", "YFI", "BAT", "USDT", "USDC", "DAI", "BNB", "PEPE", "LDO", "RNDR",
}
# Kraken uses legacy asset codes (crypto prefixed "X", fiat prefixed "Z"). Normalize them to
# modern tickers so a Kraken disposal matches Coinbase/normalized lots for the same coin in a
# universal import (else XLTC ≠ LTC → phantom $0-basis rows). (Codex P2, PR #23.)
_TICKER_ALIASES = {
    "XBT": "BTC", "XXBT": "BTC", "XETH": "ETH", "ZUSD": "USD",
    "XLTC": "LTC", "XXRP": "XRP", "XXMR": "XMR", "XXLM": "XLM", "XZEC": "ZEC",
    "XETC": "ETC", "XREP": "REP", "XMLN": "MLN", "XXDG": "DOGE", "XDG": "DOGE",
    "XNMC": "NMC", "XXVN": "XVN", "XDAO": "DAO",
}


def _alias(ticker: str) -> str:
    t = (ticker or "").strip().upper()
    return _TICKER_ALIASES.get(t, t)


def _header_index(header: 'list[str]') -> 'dict[str, int]':
    return {(h or "").strip().lower(): i for i, h in enumerate(header)}


def _cell(row: 'list[str]', idx: 'dict[str, int]', *names: str, default: str = "") -> str:
    for n in names:
        j = idx.get(n.lower())
        if j is not None and j < len(row):
            return (row[j] or "").strip()
    return default


def _skip_preamble(rows: 'list[list[str]]', *header_markers: str) -> 'list[list[str]]':
    """Coinbase/Gemini exports prepend banner lines before the real header. Find the first
    row whose cells contain all `header_markers` and return rows from there."""
    markers = [m.lower() for m in header_markers]
    for i, row in enumerate(rows):
        cells = [(c or "").strip().lower() for c in row]
        if all(any(m in c for c in cells) for m in markers):
            return rows[i:]
    return rows


def adapt_coinbase(rows: 'list[list[str]]', *, assume_self_transfers: bool = False
                   ) -> 'list[dict]':
    """Coinbase transaction-history CSV.

    Recognized 'Transaction Type' values: Buy, Sell, Convert (crypto-to-crypto, the
    received leg parsed from the Notes 'Converted X A to Y B'), Send, Receive, and any
    type containing 'Income'/'Reward'/'Staking'/'Interest'/'Earn' (ordinary income).

    Coinbase 'Send' AND 'Receive' are both AMBIGUOUS: a transfer to/from your OWN wallet is
    non-taxable, but a Send to someone else is a TAXABLE disposal and a Receive of a
    payment/income is ORDINARY income at FMV. The Transaction Type alone does not prove
    ownership, so both hard-fail unless `assume_self_transfers=True` (the caller has confirmed
    every Send and Receive is an own-wallet transfer). Defaulting to non-taxable would
    UNDER-report a disposal or income receipt — the forbidden bias direction. (Codex P1/P2.)
    """
    rows = _skip_preamble(rows, "transaction type", "asset")
    if not rows:
        return []
    idx = _header_index(rows[0])
    out: 'list[dict]' = []
    for row in rows[1:]:
        if not any((c or "").strip() for c in row):
            continue
        ttype = _cell(row, idx, "transaction type").lower()
        asset = _alias(_cell(row, idx, "asset"))
        # Advanced-trade exports put the filled amount in a 'Size' column (NOT the base ticker
        # 'Size Unit') rather than 'Quantity Transacted'. (Codex P2, PR #23.)
        qty = _cell(row, idx, "quantity transacted", "quantity", "size", "filled size")
        # Coinbase encodes disposals with a NEGATIVE quantity; normalize to the magnitude.
        # (Codex P1, PR #23.)
        qty = _unsign_qty(qty)
        ts = _cell(row, idx, "timestamp", "time", "date")
        # Amount headers vary: some exports prefix them with the price currency
        # ("USD Subtotal", "USD Total (inclusive of fees)", "USD Fees"). Recognize both. A
        # missing amount must NOT silently become $0 (that would wipe the Form 8949/Schedule D
        # figures for the file) — see the hard-fail guard below. (Codex P1, PR #23.)
        subtotal = _cell(row, idx, "subtotal", "usd subtotal")
        total = _cell(row, idx, "total (inclusive of fees and/or spread)",
                      "usd total (inclusive of fees and/or spread)",
                      "usd total (inclusive of fees)", "total", "usd total")
        fee = _cell(row, idx, "fees and/or spread", "usd fees and/or spread", "usd fees",
                    "fees", "fee") or "0"
        notes = _cell(row, idx, "notes")
        # USD figure: prefer Subtotal (pre-fee) and let expand_transaction apply the fee.
        # If Subtotal is absent, fall back to Total (INCLUSIVE of fees) and zero the fee —
        # the inclusive total already nets it, so passing the fee again would double-count
        # (buy basis += fee twice, sell proceeds -= fee twice). (Codex P2, PR #23.)
        if subtotal:
            usd, fee_eff = subtotal, fee
        elif total:
            usd, fee_eff = total, "0"
        else:
            usd, fee_eff = "", "0"   # no amount column found — guarded below

        # A value-bearing row (buy/sell/convert/income) with no amount column is a parse
        # failure, not a $0 transaction — refuse rather than silently zero the return.
        recognized_value = (
            ttype in ("buy", "advanced trade buy", "advance trade buy", "sell",
                      "advanced trade sell", "advance trade sell", "convert", "converted")
            or any(k in ttype for k in ("income", "reward", "staking", "interest", "earn")))
        if recognized_value and not usd:
            raise CryptoImportError(
                f"Coinbase row ({ttype!r} {asset} {ts}): no USD amount column found "
                f"(looked for Subtotal/Total and their USD-prefixed variants). Refusing to "
                f"import with $0 basis/proceeds — fix the export header or normalize manually.")
        # The unprefixed Subtotal/Total are denominated in the export's price currency. If
        # that's not USD (a EUR/GBP Coinbase report), the amounts are NOT dollars — refuse
        # rather than silently put foreign-currency figures on Form 8949 as USD. This importer
        # is USD-only; convert externally or normalize with usd_amount in USD. (Codex P2.)
        currency = _cell(row, idx, "spot price currency", "price currency", "currency")
        if not currency:
            # Advanced Trade exports may omit the spot-currency columns and encode the market
            # only in a Product/product_id column (e.g. "ETH-BTC"); use its quote leg. (Codex
            # P1, PR #23.)
            product = _cell(row, idx, "product", "product id", "product_id", "market")
            if "-" in product:
                currency = product.upper().rsplit("-", 1)[-1]
        if not currency:
            # An Advanced Trade fill on a crypto-quoted market often names it only in the Notes
            # (e.g. "... on XLM-BTC"). Parse a TICKER-TICKER market and take the quote leg so a
            # crypto-to-crypto disposition isn't imported as a plain USD trade (which would drop
            # the quote-asset disposal). Date strings like 2024-01-01 don't match (digits, not a
            # letter-led ticker). (Codex P1, PR #23.)
            m = re.search(r"\b[A-Z][A-Z0-9]{1,9}-([A-Z][A-Z0-9]{1,9})\b", (notes or "").upper())
            if m:
                currency = m.group(1)
        if recognized_value and currency and currency.upper() != "USD":
            raise CryptoImportError(
                f"Coinbase row ({ttype!r} {asset} {ts}): Spot Price Currency is "
                f"{currency!r}, not USD. This importer is USD-only — convert the amounts to "
                f"USD (or normalize manually with usd_amount in USD) before importing.")
        base = {"date": ts, "asset": asset, "quantity": qty, "fee_usd": fee_eff,
                "exchange": "coinbase"}
        if ttype in ("buy", "advanced trade buy", "advance trade buy"):
            out.append({**base, "kind": "buy", "usd_amount": usd})
        elif ttype in ("sell", "advanced trade sell", "advance trade sell"):
            out.append({**base, "kind": "sell", "usd_amount": usd})
        elif ttype in ("convert", "converted"):
            recv_asset, recv_qty = _parse_coinbase_convert(notes)
            out.append({**base, "kind": "trade", "usd_amount": usd,
                        "received_asset": recv_asset, "received_quantity": recv_qty})
        elif ttype == "send":
            if not assume_self_transfers:
                raise CryptoImportError(
                    f"Coinbase 'Send' ({asset} {ts}) is ambiguous: a transfer to your OWN "
                    f"wallet is non-taxable, but a payment or gift to someone else is a "
                    f"TAXABLE disposal. Re-run adapt with --assume-self-transfers if EVERY "
                    f"send is to a wallet you control, or normalize this row manually as "
                    f"kind 'spend' (taxable; usd_amount = FMV at the time) or a gift.")
            out.append({**base, "kind": "send"})
        elif ttype == "receive":
            if not assume_self_transfers:
                raise CryptoImportError(
                    f"Coinbase 'Receive' ({asset} {ts}) is ambiguous: a transfer IN from your "
                    f"OWN wallet is non-taxable, but a payment or other income receipt is "
                    f"ORDINARY income at FMV. Re-run adapt with --assume-self-transfers if "
                    f"EVERY receive is from a wallet you control, or normalize this row "
                    f"manually as kind 'income' (usd_amount = FMV at receipt).")
            out.append({**base, "kind": "receive"})
        elif any(k in ttype for k in ("income", "reward", "staking", "interest", "earn")):
            itype = ("staking" if "staking" in ttype else
                     "interest" if "interest" in ttype else
                     "reward" if ("reward" in ttype or "earn" in ttype) else "income")
            out.append({**base, "kind": "income", "usd_amount": usd, "income_type": itype})
        else:
            raise CryptoImportError(
                f"Coinbase: unrecognized Transaction Type {ttype!r}. Map it to a "
                f"normalized kind manually and re-run via the normalized JSON path.")
    return out


_CONVERT_RE = re.compile(
    r"converted\s+([\d.,]+)\s+(\w+)\s+to\s+([\d.,]+)\s+(\w+)", re.IGNORECASE)


def _parse_coinbase_convert(notes: str) -> 'tuple[str, str]':
    """Parse 'Converted 0.5 ETH to 12.3 SOL' → (received_asset, received_quantity)."""
    m = _CONVERT_RE.search(notes or "")
    if not m:
        raise CryptoImportError(
            f"Coinbase Convert row: cannot parse received asset/quantity from Notes "
            f"{notes!r}. Expected 'Converted <qty> <A> to <qty> <B>'.")
    recv_qty = m.group(3).replace(",", "")
    recv_asset = _alias(m.group(4))
    return recv_asset, recv_qty


def adapt_kraken(rows: 'list[list[str]]', *, assume_self_transfers: bool = False
                 ) -> 'list[dict]':
    """Kraken trades.csv (one row per fill).

    Columns used: time, pair, type (buy/sell), cost (USD), fee, vol (base quantity).
    Only fiat/stablecoin-quoted pairs (…USD/USDT/…) are mapped to buy/sell; a
    crypto-to-crypto pair (e.g. ETHXBT) is rejected with guidance to use the normalized
    path (it needs a USD FMV the trades file does not carry).
    """
    if not rows:
        return []
    idx = _header_index(rows[0])
    out: 'list[dict]' = []
    for row in rows[1:]:
        if not any((c or "").strip() for c in row):
            continue
        ttype = _cell(row, idx, "type").lower()
        pair = _cell(row, idx, "pair").upper()
        ts = _cell(row, idx, "time", "date")
        cost = _cell(row, idx, "cost")
        fee = _cell(row, idx, "fee") or "0"
        vol = _cell(row, idx, "vol", "volume", "amount")
        base_asset, quote = _split_kraken_pair(pair)
        if quote in _KRAKEN_STABLECOINS or quote in _KRAKEN_USD_STABLECOINS:
            raise CryptoImportError(
                f"Kraken: pair {pair!r} is quoted in the stablecoin {quote}, which is a "
                f"DIGITAL ASSET — funding the trade with it is a reportable crypto-to-crypto "
                f"disposition, not a USD buy/sell. Provide it via the normalized JSON path "
                f"(kind 'trade' with usd_amount = USD FMV at the time of the swap).")
        if quote not in _KRAKEN_USD:
            raise CryptoImportError(
                f"Kraken: crypto-to-crypto pair {pair!r} carries no USD value in "
                f"trades.csv. Provide it via the normalized JSON path (kind 'trade' with "
                f"usd_amount = FMV at the time of the swap).")
        # A trade with no 'cost' (USD) value must NOT silently become $0 — that would zero
        # the basis/proceeds and understate Form 8949/Schedule D. Hard-fail like Coinbase.
        # (Codex P2, PR #23.)
        if ttype in ("buy", "sell") and not cost:
            raise CryptoImportError(
                f"Kraken row ({ttype!r} {pair} {ts}): no 'cost' (USD) column/value found. "
                f"Refusing to import with $0 basis/proceeds — fix the export or normalize "
                f"manually.")
        rec = {"date": ts, "asset": base_asset, "quantity": vol, "usd_amount": cost,
               "fee_usd": fee, "exchange": "kraken"}
        if ttype == "buy":
            out.append({**rec, "kind": "buy"})
        elif ttype == "sell":
            out.append({**rec, "kind": "sell"})
        else:
            raise CryptoImportError(f"Kraken: unrecognized trade type {ttype!r}.")
    return out


def _split_kraken_pair(pair: str) -> 'tuple[str, str]':
    """Split a Kraken pair like 'XBTUSD'/'XXBTZUSD'/'ETHUSDT' into (base, quote). USD/ZUSD
    normalize to 'USD'; stablecoin quotes keep their ticker so the caller can reject them as
    digital-asset swaps rather than USD trades."""
    # Some Kraken exports delimit the pair (XBT/USD, ADA/USD, XBT-USD). Strip separators before
    # suffix-splitting, else the USD branch returns a base like "XBT/" that can't match lots
    # from other exchanges in a universal import. Kraken tickers are alphanumeric, so removing
    # '/' and '-' is safe. (Codex P2, PR #23.)
    p = pair.upper().replace("/", "").replace("-", "")
    # 1. End-USD stablecoins (TUSD/BUSD/PYUSD/GUSD): accept the stablecoin split only when the
    #    residual base is a RECOGNIZED asset (ETHTUSD → ETH); otherwise it is a fiat-USD pair
    #    whose base happens to end in T/B/PY/G (XBTUSD → XB is not real → fiat). (Codex P1.)
    for q in sorted(_KRAKEN_USD_STABLECOINS, key=len, reverse=True):
        if p.endswith(q):
            base = p[: -len(q)]
            if base and _alias(base) in _KNOWN_KRAKEN_BASES:
                return _alias(base), q
    # 2. True fiat (so XBTUSD = XBT/USD).
    for q in sorted(_KRAKEN_USD, key=len, reverse=True):  # ZUSD before USD
        if p.endswith(q):
            return _alias(p[: -len(q)]), "USD"
    # 3. Non-end-USD stablecoins.
    for q in sorted(_KRAKEN_STABLECOINS, key=len, reverse=True):
        if p.endswith(q):
            return _alias(p[: -len(q)]), q
    # No recognized quote suffix — return whole as base, empty quote (caller rejects).
    return _alias(p), ""


def adapt_gemini(rows: 'list[list[str]]', *, assume_self_transfers: bool = False
                 ) -> 'list[dict]':
    """Gemini transaction CSV (simplified canonical headers).

    Columns used: Date, Type, Symbol (e.g. BTCUSD or BTC), USD Amount, Fee (USD),
    Crypto Amount. Types: Buy/Sell → trades; a Credit naming Earn/Interest/Staking/Reward →
    ordinary income; a BARE Credit/Deposit (inbound transfer) and a Debit/Withdrawal
    (outbound) are AMBIGUOUS (own-wallet transfer vs income/disposal) and hard-fail unless
    `assume_self_transfers=True` (same rule as Coinbase Send/Receive). (Codex P1.)
    """
    rows = _skip_preamble(rows, "type", "symbol")
    if not rows:
        return []
    idx = _header_index(rows[0])
    out: 'list[dict]' = []
    for row in rows[1:]:
        if not any((c or "").strip() for c in row):
            continue
        ttype = _cell(row, idx, "type").lower()
        symbol = _cell(row, idx, "symbol").upper()
        asset, quote = _gemini_split(symbol)
        # Gemini splits the stamp into Date + Time (UTC); combine them so the intraday FIFO
        # guard works (a same-day 09:00 sell must not borrow basis from a 12:00 buy).
        # (Codex P2, PR #23.)
        date_cell = _cell(row, idx, "date")
        time_cell = _cell(row, idx, "time (utc)", "time")
        ts = f"{date_cell} {time_cell}" if (date_cell and time_cell) else (date_cell or time_cell)
        usd = _cell(row, idx, "usd amount", "usd amount usd", "usd")
        fee = _cell(row, idx, "fee (usd)", "trading fee (usd)", "fee") or "0"
        # Gemini's outflow-sign convention can put a NEGATIVE 'Crypto Amount' on a Sell/Debit;
        # `kind` carries direction, so emit the magnitude (same as Coinbase). (Codex P2, PR #23.)
        qty = _unsign_qty(_cell(row, idx, "crypto amount", "amount", "quantity"))
        base = {"date": ts, "asset": asset, "quantity": qty, "fee_usd": fee,
                "exchange": "gemini"}

        def _need_usd():
            # A value-bearing row with no recognized USD Amount must NOT silently become $0
            # — that would zero proceeds/basis/ordinary income. (Codex P2, PR #23.)
            if not usd:
                raise CryptoImportError(
                    f"Gemini row ({ttype!r} {symbol} {date_cell}): no 'USD Amount' "
                    f"column/value found. Refusing to import with $0 — fix the export or "
                    f"normalize manually.")

        # A 'Credit' with an explicit income word (Earn/Interest/Staking/Reward) is ordinary
        # income; a BARE 'Credit'/'Deposit' is an ambiguous inbound transfer (own-wallet vs a
        # reward) and is gated like Coinbase 'Receive' — emitting income unconditionally would
        # OVER-report a non-taxable self-transfer as Schedule 1 income. (Codex P1, PR #23.)
        income_hit = next((h for h in ("interest", "staking", "reward", "earn", "income")
                           if h in ttype), None)
        def _require_usd_quote():
            # buy/sell must be USD-quoted. ANY other quote — a stablecoin (BTCUSDT/BTCGUSD)
            # OR a crypto-cross ActiveTrader pair like ETHBTC (which _gemini_split leaves as
            # quote == "" / a non-USD ticker) — is a crypto-to-crypto disposition, not a USD
            # trade; reject so the disposed leg isn't dropped as a fake USD asset. Require
            # quote == "USD" rather than only blocking the known stablecoins. (Codex P1, PR #23.)
            if quote == "USD":
                return
            # A non-USD FIAT pair (BTCEUR/ETHGBP) needs currency-CONVERSION guidance, not the
            # crypto-to-crypto message — a fiat-settled disposal is converted to USD, not run
            # through the 'trade' path. _gemini_split leaves these as quote == "", so detect
            # the fiat suffix on the symbol directly. (Codex P2, PR #23.)
            for _fiat in ("EUR", "GBP", "CAD", "JPY", "AUD", "CHF", "SGD", "HKD"):
                if symbol.endswith(_fiat) and len(symbol) > len(_fiat):
                    raise CryptoImportError(
                        f"Gemini: pair {symbol!r} is quoted in {_fiat}, a non-USD fiat. This "
                        f"importer is USD-only — convert the amounts to USD (or normalize "
                        f"manually with usd_amount in USD) before importing.")
            raise CryptoImportError(
                f"Gemini: pair {symbol!r} is not USD-quoted (quote="
                f"{quote or 'unrecognized'!r}). A stablecoin- or crypto-quoted market "
                f"(e.g. BTCUSDT, ETHBTC) is a crypto-to-crypto disposition, not a USD "
                f"trade — normalize via the JSON 'trade' path (usd_amount = USD FMV).")

        if ttype == "buy":
            _require_usd_quote(); _need_usd()
            out.append({**base, "kind": "buy", "usd_amount": usd})
        elif ttype == "sell":
            _require_usd_quote(); _need_usd()
            out.append({**base, "kind": "sell", "usd_amount": usd})
        elif income_hit:
            _need_usd()
            itype = "interest" if income_hit == "interest" else \
                    "staking" if income_hit == "staking" else \
                    "reward" if income_hit in ("reward", "earn") else "income"
            out.append({**base, "kind": "income", "usd_amount": usd, "income_type": itype})
        elif ttype in ("credit", "deposit"):
            if not assume_self_transfers:
                raise CryptoImportError(
                    f"Gemini 'Credit'/'Deposit' ({asset} {ts}) is ambiguous: an own-wallet "
                    f"transfer IN is non-taxable, but an Earn/interest/reward credit is "
                    f"ORDINARY income at FMV. Re-run adapt with --assume-self-transfers if "
                    f"it's an own-wallet transfer, or normalize this row manually as kind "
                    f"'income' (usd_amount = FMV at receipt).")
            out.append({**base, "kind": "receive"})
        elif ttype in ("debit", "withdrawal"):
            if not assume_self_transfers:
                raise CryptoImportError(
                    f"Gemini 'Debit' ({asset} {ts}) is ambiguous: an own-wallet withdrawal "
                    f"is non-taxable, but a payment or gift is a TAXABLE disposal. Re-run "
                    f"adapt with --assume-self-transfers if every withdrawal is to a wallet "
                    f"you control, or normalize this row manually as kind 'spend' "
                    f"(taxable; usd_amount = FMV).")
            out.append({**base, "kind": "send"})
        else:
            raise CryptoImportError(f"Gemini: unrecognized Type {ttype!r}.")
    return out


# Gemini quote currencies. USDT/USDC/DAI/GUSD are digital-asset stablecoins → a market quoted
# in them is a crypto-to-crypto disposition, not a USD trade. (GUSD ends in "USD" but Gemini
# base tickers don't end in "G", so it doesn't shadow a plain "…USD" pair the way Kraken's
# TUSD/BUSD do against XBT/DOT/BNB.)
_GEMINI_STABLECOINS = ("USDT", "USDC", "DAI", "GUSD")
_GEMINI_QUOTES = _GEMINI_STABLECOINS + ("USD",)


def _gemini_split(symbol: str) -> 'tuple[str, str]':
    """Split a Gemini symbol into (base, quote). 'BTCUSD' → ('BTC','USD'); 'BTCGUSD' →
    ('BTC','GUSD'); a bare ticker → (ticker, '')."""
    s = symbol.upper()
    # A bare stablecoin/USD ticker (e.g. "GUSD") is the ASSET itself, not a <base>+USD pair —
    # without this guard "GUSD" splits to base "G"/quote "USD" and a GUSD credit books basis
    # under "G" while a later "GUSDUSD" sale books under "GUSD". (Codex P2, PR #23.)
    if s in _GEMINI_QUOTES:
        return _alias(s), ""
    for q in sorted(_GEMINI_QUOTES, key=len, reverse=True):
        if s.endswith(q) and len(s) > len(q):
            return _alias(s[: -len(q)]), q
    return _alias(s), ""


_ADAPTERS = {
    "coinbase": adapt_coinbase,
    "kraken": adapt_kraken,
    "gemini": adapt_gemini,
}


def adapt_csv(rows: 'list[list[str]]', exchange: str, *,
              assume_self_transfers: bool = False) -> 'list[dict]':
    ex = (exchange or "").strip().lower()
    if ex not in _ADAPTERS:
        raise CryptoImportError(
            f"Unknown exchange {exchange!r}. Supported: {sorted(_ADAPTERS)} "
            f"(or pre-normalize to JSON and use the 'compute' path).")
    return _ADAPTERS[ex](rows, assume_self_transfers=assume_self_transfers)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def render_markdown(result: dict) -> str:
    sd = result["schedule_d"]
    lines = [
        "# Crypto Tax Import — Form 8949 / Schedule D",
        "",
        f"**Digital-asset question (Form 1040 page 1): {result['digital_asset_question']}**",
        "",
        "## Capital gains/losses (Schedule D)",
        "",
        "| Term | Proceeds | Cost basis | Adjustment | Gain/(loss) | Rows |",
        "|------|---------:|-----------:|-----------:|------------:|-----:|",
        _sd_row("Short-term", sd["short_term"]),
        _sd_row("Long-term", sd["long_term"]),
        f"| **Net** | | | | **${sd['net_gain']:,.2f}** | |",
        "",
    ]
    summary = result.get('crypto_summary', {})
    reporting = result.get('broker_reporting') or {}
    if summary.get('complete') is False:
        lines[0:0] = ['# BLOCKED — diagnostic import; unresolved account/transfer evidence', '']
        for issue in result.get('account_fifo', {}).get('unresolved_inputs', []):
            lines.append(f"- {issue['account_id']} / {issue['txn_id']}: {issue['reason']}")
        lines.append('')
    if summary.get('reporting_complete') is False:
        lines[0:0] = ['# BLOCKED — Form 8949 reporting unresolved; reconcile broker forms', '']
        for issue in reporting.get('unresolved', []):
            where = ' / '.join(issue[key] for key in ('account_id', 'txn_id', 'form_id') if issue.get(key))
            lines.append(f"- {where or 'import'}: {issue['reason']}")
        lines.append('')
    if reporting.get('schedule_d_routing'):
        lines += ["## Form 8949 routing", "",
                  "| Box | Schedule D line | Proceeds | Cost basis | Adjustment | Gain/(loss) | Rows |",
                  "|-----|-----------------|---------:|-----------:|-----------:|------------:|-----:|"]
        lines += [f"| {box} | {e['schedule_d_line']} | ${e['proceeds']:,.2f} | ${e['cost']:,.2f} | "
                  f"${e['adjustment']:,.2f} | ${e['gain']:,.2f} | {e['rows']} |"
                  for box, e in reporting['schedule_d_routing'].items()]
        lines.append("")
    oi = result["ordinary_income"]
    if oi["total"]:
        lines += [
            f"## Ordinary income (receipts): ${oi['total']:,.2f}",
            "",
            "_" + oi["routing_note"] + "_",
            "",
        ]
        for itype, amt in sorted(oi["by_type"].items()):
            lines.append(f"- {itype}: ${amt:,.2f}")
        lines.append("")
    if result["needs_basis"]:
        lines += ["## ⚠ Disposals missing cost basis (assigned $0 — overstates gain)", ""]
        for nb in result["needs_basis"]:
            lines.append(f"- {nb['description']} sold {nb['date_sold']} "
                         f"for ${nb['proceeds']:,.2f}")
        lines.append("")
    if "washington_allocation" in result:
        wa = result["washington_allocation"]
        lines += [f"## WA allocated LT crypto gain/(loss): ${wa['allocated_lt_gain_loss']:,.2f}",
                  "", wa["note"], ""]
    lines += ["## Caveats", ""]
    lines += [f"- {c}" for c in result["caveats"]]
    return "\n".join(lines) + "\n"


def _sd_row(label: str, t: dict) -> str:
    return (f"| {label} | ${t['proceeds']:,.2f} | ${t['cost_basis']:,.2f} | "
            f"${t['adjustment']:,.2f} | ${t['gain']:,.2f} | {t['rows']} |")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _read_csv(path: str) -> 'list[list[str]]':
    # Through `parse_utils.extract_csv`, so the path guards and the per-cell
    # masking apply here too — an adapter's error quotes the row it rejected,
    # and `adapt` prints the notes column through to the normalized JSON.
    from agents.lib.parse_utils import extract_csv
    return extract_csv(path)


def _flag_value(argv: 'list[str]', flag: str) -> str:
    """Return the operand following `flag`, or raise a clean CryptoImportError if it is
    missing (flag is last) or is itself another flag — so a malformed command fails with a
    clean ERROR, not an uncaught IndexError traceback. (Codex P2, PR #23.)"""
    i = argv.index(flag)
    if i + 1 >= len(argv) or argv[i + 1].startswith("--"):
        raise CryptoImportError(f"{flag} requires a value.")
    return argv[i + 1]


def _under_the_workspace(path_arg: str) -> str:
    """A RELATIVE path means a file in the taxpayer's workspace.

    `documents/coinbase.csv` is how the skill spells it, and that used to mean
    the directory the command ran from — which under `$TAX_WORKSPACE` is the
    checkout (Y4). These two commands take their path as a bare positional, so
    the structural sweep in `tests/test_workspace.py` cannot see them; they are
    named there instead.

    NOT `resolve_under_workspace`, which also CONFINES the result. An absolute
    path outside the workspace has always been accepted here — a CSV sitting in
    a downloads folder is the ordinary case — and narrowing that is V6's
    question about arbitrary file reads, not this one about which directory a
    relative path means. Changing both at once would have hidden the second
    behind the first.
    """
    raw = Path(path_arg)
    return str(raw if raw.is_absolute() else resolve_workspace() / raw)


def _cli_adapt(argv: 'list[str]') -> int:
    if len(argv) < 2 or "--exchange" not in argv:
        print("Usage: crypto_importer.py adapt <file.csv> --exchange "
              "<coinbase|kraken|gemini> [--assume-self-transfers] [--account-id ID]", file=sys.stderr)
        return 2
    path = _under_the_workspace(argv[0])
    exchange = _flag_value(argv, "--exchange")
    assume_self = "--assume-self-transfers" in argv
    rows = _read_csv(path)
    txns = adapt_csv(rows, exchange, assume_self_transfers=assume_self)
    if '--account-id' in argv:
        from hashlib import sha256
        account = _account_id(_flag_value(argv, '--account-id'), 'account_id')
        # Alphabetic encoding avoids digit runs that the opaque-ID privacy guard
        # would reject, while retaining the export digest's deterministic identity.
        source = sha256(json.dumps(rows, sort_keys=True).encode('utf-8')).hexdigest()[:20]
        source = source.translate(str.maketrans('0123456789abcdef', 'abcdefghijklmnop'))
        for i, txn in enumerate(txns):
            txn['account_id'] = account
            txn['txn_id'] = f'{source}:{i}'
    print(json.dumps({"transactions": txns}, indent=2))
    return 0


def _cli_compute(argv: 'list[str]') -> int:
    if not argv:
        print("Usage: crypto_importer.py compute <transactions.json> [--year YYYY] "
              "[--account-aware] [--per-account] [--markdown]", file=sys.stderr)
        return 2
    path = _under_the_workspace(argv[0])
    p = Path(path)
    if not p.is_file():
        print(f"ERROR: file not found: {redact(path)}", file=sys.stderr)
        return 1
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        # main() only catches CryptoImportError; re-raise so a user-edited empty/malformed
        # normalized file produces the promised clean ERROR, not a raw traceback. (Codex P2.)
        raise CryptoImportError(f"{redact(path)} is not valid JSON: {redact(e)}")
    txns = data.get("transactions", data) if isinstance(data, dict) else data
    if isinstance(data, dict) and {'source_corrections', 'tax_calendar_review', 'option_exercise_review'} & set(data):
        raise CryptoImportError('Put source_corrections, tax_calendar_review and option_exercise_review inside broker_reporting; top-level fields are not applied')
    year = None
    if "--year" in argv:
        raw_year = _flag_value(argv, "--year")
        try:
            year = int(raw_year)
        except ValueError:
            raise CryptoImportError(f"--year must be an integer, got {raw_year!r}")
    if isinstance(data, dict) and "wa_domicile_by_date" in data and not isinstance(data["wa_domicile_by_date"], dict):
        raise CryptoImportError("wa_domicile_by_date must be an explicit object when supplied.")
    per_account = "--per-account" in argv
    result = build_crypto_import(txns, tax_year=year, per_account=per_account,
                                 account_aware=True if '--account-aware' in argv else None,
                                 transfer_links=data.get('transfer_links') if isinstance(data, dict) else None,
                                 wa_domicile_by_date=(data.get("wa_domicile_by_date")
                                                     if isinstance(data, dict) else None),
                                 broker_reporting=(data.get('broker_reporting')
                                                   if isinstance(data, dict) else None),
                                 specific_identification=(data.get('specific_identification')
                                                          if isinstance(data, dict) else None))
    if "--markdown" in argv:
        print(render_markdown(result))
    else:
        print(json.dumps(result, indent=2))
    summary = result['crypto_summary']
    return 2 if summary.get('complete') is False or summary.get('reporting_complete') is False else 0


def _self_test() -> int:
    """Minimal self-test (run with no args). Mirrors the tax_computations convention."""
    txns = [
        {"date": "2023-01-10", "kind": "buy", "asset": "BTC", "quantity": 1.0,
         "usd_amount": 20000, "fee_usd": 50},
        {"date": "2024-06-01", "kind": "sell", "asset": "BTC", "quantity": 0.5,
         "usd_amount": 25000, "fee_usd": 25},  # long-term (held >1yr)
        {"date": "2024-03-01", "kind": "income", "asset": "ETH", "quantity": 2.0,
         "usd_amount": 4000, "income_type": "staking"},
        {"date": "2024-09-01", "kind": "trade", "asset": "ETH", "quantity": 2.0,
         "usd_amount": 5000, "received_asset": "SOL", "received_quantity": 100},
    ]
    r = build_crypto_import(txns, tax_year=2024, specific_identification=False)
    assert r["digital_asset_question"] == "Yes", r["digital_asset_question"]
    # BTC: 0.5 sold, proceeds 24975, basis = 0.5 * 20050 = 10025, gain 14950, long-term.
    lt = r["schedule_d"]["long_term"]
    assert lt["gain"] == 14950.0, lt
    # ETH staking income $4000 ordinary; ETH disposed in the trade → basis 4000, proceeds
    # 5000 → $1000 short-term gain (acquired 2024-03-01, sold 2024-09-01).
    assert r["ordinary_income"]["total"] == 4000.0, r["ordinary_income"]
    st = r["schedule_d"]["short_term"]
    assert st["gain"] == 1000.0, st
    assert r["crypto_summary"]["needs_basis_count"] == 0, r["crypto_summary"]
    # A universal pool cannot route a Form 8949 box (AA41); the same events in one
    # account with a reviewed no-form declaration route TY2024's C/F.
    assert r["crypto_summary"]["reporting_complete"] is False, r["broker_reporting"]
    account = [dict(t, account_id="exchange-A", txn_id=f"t{i}") for i, t in enumerate(txns)]
    r = build_crypto_import(account, tax_year=2024, specific_identification=False, broker_reporting={"accounts": {
        "exchange-A": {"forms_received": False, "evidence": "self-test-declaration"}}})
    assert r["crypto_summary"]["reporting_complete"] is True, r["broker_reporting"]
    boxes = [(row["box"], row["gain_loss"]) for part in r["form_8949"].values() for row in part]
    assert boxes == [("C", 1000.0), ("F", 14950.0)], boxes
    print("crypto_importer self-test: OK")
    print(render_markdown(r))
    return 0


def main(argv: 'list[str]') -> int:
    if not argv:
        return _self_test()
    cmd, rest = argv[0], argv[1:]
    try:
        if cmd == "adapt":
            return _cli_adapt(rest)
        if cmd == "compute":
            return _cli_compute(rest)
        print(f"ERROR: unknown command {cmd!r}. Use: adapt | compute | (no args = self-test)",
              file=sys.stderr)
        return 2
    except (WorkspaceError, CryptoImportError) as e:
        print(f"ERROR: {redact(e)}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 — the boundary, not a handler
        # Everything else — a Latin-1 export's UnicodeDecodeError, a refused
        # path — used to propagate as a traceback into the same transcript
        # the masking exists to keep clean. Same shape as parse_utils's.
        print(f"ERROR: {type(e).__name__}: {redact(e)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
