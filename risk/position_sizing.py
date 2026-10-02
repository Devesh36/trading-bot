from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
import math


@dataclass(frozen=True)
class SizeResult:
    contracts: int
    quantity: float
    stop: float
    estimated_loss: float
    risk_budget: float
    margin: float
    notional: float
    leverage: float


def round_stop(stop, spec, direction):
    d = Decimal(str(stop))
    rounding = ROUND_CEILING if direction == "LONG" else ROUND_FLOOR
    return float(
        (d / spec.tick_size).to_integral_value(rounding=rounding) * spec.tick_size
    )


def size_position(equity, available, entry, stop, direction, spec, config):
    if (
        not all(math.isfinite(v) and v > 0 for v in (equity, entry, stop))
        or not math.isfinite(available)
        or available <= 0
    ):
        raise ValueError("ACCOUNT_EQUITY_UNKNOWN or INSUFFICIENT_MARGIN")
    if direction not in ("LONG", "SHORT"):
        raise ValueError("Invalid direction")
    stop = round_stop(stop, spec, direction)
    sign = 1 if direction == "LONG" else -1
    if sign * (entry - stop) <= 0:
        raise ValueError("INVALID_STOP")
    d = lambda v: Decimal(str(v))
    fee = max(config.fee_rate, spec.taker_fee)
    # Reserve adverse entry/stop slippage and round-trip fees. This is an estimate, not a guaranteed cap.
    unit_loss = d(abs(entry - stop)) + d(entry + stop) * d(
        config.slippage_bps / 10000 + fee
    )
    per_contract = spec.contract_value * unit_loss
    budget = d(equity) * d(config.risk_per_trade_percent / 100)
    effective_leverage = min(
        config.default_leverage,
        100 / spec.initial_margin_percent
        if spec.initial_margin_percent
        else config.default_leverage,
    )
    per_margin = d(entry) * spec.contract_value / d(effective_leverage)
    capital = min(d(available), d(equity) * d(config.max_position_equity_percent / 100))
    raw = min(
        budget / per_contract,
        capital / (per_margin + d(entry) * spec.contract_value * d(fee)),
    )
    if spec.max_contracts:
        raw = min(raw, d(spec.max_contracts))
    contracts = (
        int((raw / d(spec.quantity_step)).to_integral_value(rounding=ROUND_FLOOR))
        * spec.quantity_step
    )
    if contracts < spec.min_contracts:
        raise ValueError("SIZE_BELOW_MINIMUM")
    return SizeResult(
        contracts,
        float(d(contracts) * spec.contract_value),
        stop,
        float(d(contracts) * per_contract),
        float(budget),
        float(d(contracts) * per_margin),
        float(d(contracts) * d(entry) * spec.contract_value),
        effective_leverage,
    )
