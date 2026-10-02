from decimal import Decimal
import pytest
from exchange.models import ContractSpec
from config import Config
from risk.position_sizing import size_position, round_stop

SPEC = ContractSpec("BTCUSD", 27, Decimal(".001"), Decimal(".5"))


def test_contract_conversion_and_round_down():
    c = Config(fee_rate=0, slippage_bps=0, max_position_equity_percent=100)
    spec = ContractSpec("BTCUSD", 27, Decimal(".001"), Decimal(".5"), taker_fee=0)
    s = size_position(10000, 10000, 60000, 59000, "LONG", spec, c)
    assert s.contracts == 100 and s.quantity == 0.1 and s.estimated_loss == 100
    assert round_stop(59000.1, spec, "LONG") == 59000.5
    assert round_stop(61000.9, spec, "SHORT") == 61000.5


def test_costs_never_exceed_risk():
    s = size_position(10000, 10000, 60000, 59000, "LONG", SPEC, Config())
    assert s.estimated_loss <= 100 and s.contracts < 100
    with pytest.raises(ValueError):
        size_position(10000, 10000, 60000, 61000, "LONG", SPEC, Config())


def test_instrument_margin_can_reduce_effective_leverage():
    from dataclasses import replace

    spec = replace(SPEC, initial_margin_percent=25)
    size = size_position(10000, 10000, 60000, 59000, "LONG", spec, Config())
    assert size.leverage == 4
    assert abs(size.margin - size.notional / 4) < 1e-8
