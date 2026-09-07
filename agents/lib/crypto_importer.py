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
  - FIFO only (the IRS default absent a contemporaneous specific-ID election). HIFO/LIFO
    minimize gains and would UNDER-report tax if chosen silently → forbidden in v1.
  - Unmatched disposals (no acquisition on record, e.g. a transfer-in with no basis) get
    $0 cost basis + a `needs_basis` flag. $0 basis OVER-states the gain (the safe,
    over-reporting direction) → banner + degrade, every affected row surfaced — rather
    than a hard error that would block the whole import on one missing record.
  - All numeric inputs are validated at the parse boundary and REJECT bool / NaN /
    Infinity / negative (NaN/Inf pass isinstance and defeat `< 0` checks — the recurring
    project lesson). Unknown kind / unknown exchange / malformed date → clean
    CryptoImportError, never a raw traceback.

v1 SIMPLIFICATIONS (caveated, not silently wrong): one per-asset FIFO pool across all
provided transactions (universal basis; Rev. Proc. 2024-28 per-wallet segregation is a
v1.1 item — run per account for strict treatment); Box C/F default (no 1099-B/1099-DA
reconciliation); NFTs/collectible 28% rate, DeFi, §1256, margin not modeled; state tax
handled downstream by /tax-compute; mining/staking-as-a-business (Schedule C + SE tax)
flagged, not auto-classified. Federal Form 8949 only.

Pure compute + CLI. Never mutates result.json / taxpayer_profile.json — emits a result
dict the /tax-parse-crypto skill merges.
"""

import datetime
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agents.lib.secure_fs import redact  # noqa: E402

SCHEMA = "tax-parse-crypto/v1"

# Normalized transaction kinds. Each expands to some combination of primitive events.
_KINDS = {"buy", "sell", "trade", "income", "spend", "receive", "send"}

# Disposals default to Form 8949 Box C (short-term) / Box F (long-term): "transaction not
# reported on a 1099-B." Most exchanges issued no 1099-B; 1099-DA (TY2025+) reconciliation
# is a v1.1 item. Box is a per-row field so the skill/user can override.
_BOX_SHORT = "C"
_BOX_LONG = "F"

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
        v = float(value)
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
    box: str                    # C (short) or F (long)
    needs_basis: bool           # True when this slice had no acquisition on record


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
        box=_BOX_LONG if period == "long" else _BOX_SHORT,
        needs_basis=needs_basis,
    )


def _fmt_qty(q: float) -> str:
    """Render a coin quantity compactly (up to 8 dp, trailing zeros trimmed)."""
    s = f"{q:.8f}".rstrip("0").rstrip(".")
    return s if s else "0"


# --------------------------------------------------------------------------
# Aggregator
# --------------------------------------------------------------------------

def build_crypto_import(transactions: 'list[dict]', *,
                        tax_year: 'int | None' = None,
                        per_account: bool = False) -> dict:
    """Build the full crypto-import result from a list of normalized transaction dicts.

    Returns a dict with form_8949 (short/long row lists), schedule_d totals,
    ordinary_income (total + per-event breakdown), digital_asset_question, crypto_summary
    (per-asset realized gain), needs_basis (disposals lacking an acquisition), and caveats.
    """
    if not isinstance(transactions, list):
        raise CryptoImportError("transactions must be a JSON array.")

    txns = [_coerce_transaction(t, i) for i, t in enumerate(transactions)]

    acquisitions: 'list[Acquisition]' = []
    disposals: 'list[Disposal]' = []
    all_incomes: 'list[IncomeEvent]' = []
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
    rows_all = match_lots(acquisitions, disposals)
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
    digital_asset = "Yes" if (rows or incomes) else "No"

    return {
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
                 "date": e.received.isoformat(), "income_type": e.income_type}
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


def _totals(rows: 'list[Form8949Row]') -> dict:
    return {
        "proceeds": _money(sum(r.proceeds for r in rows)),
        "cost_basis": _money(sum(r.cost_basis for r in rows)),
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
        "run the importer per account for strict per-wallet treatment.",
        "Form 8949 Box C/F default ('not reported on a 1099-B'). Form 1099-DA (TY2025+) "
        "is not reconciled in v1.",
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


def _gemini_base(symbol: str) -> str:
    return _gemini_split(symbol)[0]


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
        "| Term | Proceeds | Cost basis | Gain/(loss) | Rows |",
        "|------|---------:|-----------:|------------:|-----:|",
        _sd_row("Short-term", sd["short_term"]),
        _sd_row("Long-term", sd["long_term"]),
        f"| **Net** | | | **${sd['net_gain']:,.2f}** | |",
        "",
    ]
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
    lines += ["## Caveats", ""]
    lines += [f"- {c}" for c in result["caveats"]]
    return "\n".join(lines) + "\n"


def _sd_row(label: str, t: dict) -> str:
    return (f"| {label} | ${t['proceeds']:,.2f} | ${t['cost_basis']:,.2f} | "
            f"${t['gain']:,.2f} | {t['rows']} |")


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


def _cli_adapt(argv: 'list[str]') -> int:
    if len(argv) < 2 or "--exchange" not in argv:
        print("Usage: crypto_importer.py adapt <file.csv> --exchange "
              "<coinbase|kraken|gemini> [--assume-self-transfers]", file=sys.stderr)
        return 2
    path = argv[0]
    exchange = _flag_value(argv, "--exchange")
    assume_self = "--assume-self-transfers" in argv
    rows = _read_csv(path)
    txns = adapt_csv(rows, exchange, assume_self_transfers=assume_self)
    print(json.dumps({"transactions": txns}, indent=2))
    return 0


def _cli_compute(argv: 'list[str]') -> int:
    if not argv:
        print("Usage: crypto_importer.py compute <transactions.json> [--year YYYY] "
              "[--per-account] [--markdown]", file=sys.stderr)
        return 2
    path = argv[0]
    p = Path(path)
    if not p.is_file():
        print(f"ERROR: file not found: {redact(path)}", file=sys.stderr)
        return 1
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        # main() only catches CryptoImportError; re-raise so a user-edited empty/malformed
        # normalized file produces the promised clean ERROR, not a raw traceback. (Codex P2.)
        raise CryptoImportError(f"{redact(path)} is not valid JSON: {redact(e)}")
    txns = data.get("transactions", data) if isinstance(data, dict) else data
    year = None
    if "--year" in argv:
        raw_year = _flag_value(argv, "--year")
        try:
            year = int(raw_year)
        except ValueError:
            raise CryptoImportError(f"--year must be an integer, got {raw_year!r}")
    per_account = "--per-account" in argv
    result = build_crypto_import(txns, tax_year=year, per_account=per_account)
    if "--markdown" in argv:
        print(render_markdown(result))
    else:
        print(json.dumps(result, indent=2))
    return 0


def _self_test() -> int:
    """Minimal self-test (run with no args). Mirrors tax_computations.py convention."""
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
    r = build_crypto_import(txns, tax_year=2024)
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
    except CryptoImportError as e:
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
