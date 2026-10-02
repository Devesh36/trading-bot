from dataclasses import replace
import pytest
from config import Config
from storage.database import Database
from risk.risk_manager import RiskManager
from risk.position_sizing import size_position
from execution.manual_demo import ManualDemoEntry, fixed_demo_size
from execution.order_manager import OrderManager
from exchange.delta_client import DeltaError
from tests.test_position_sizing import SPEC


def test_fixed_demo_size_never_increases_risk():
    maximum = size_position(1000, 1000, 60000, 59000, "LONG", SPEC, Config())
    sized = fixed_demo_size(maximum, 1, SPEC)
    assert sized.contracts == 1 and sized.quantity == 0.001
    assert sized.estimated_loss <= maximum.estimated_loss
    assert sized.margin <= maximum.margin
    with pytest.raises(ValueError):
        fixed_demo_size(maximum, maximum.contracts + 1, SPEC)
    with pytest.raises(ValueError):
        fixed_demo_size(maximum, 0.01, SPEC)


@pytest.mark.parametrize("mode", ["paper", "live", "testnet"])
def test_manual_demo_gate_and_normal_risk_limits(mode):
    config = Config(trading_mode=mode, enable_live_trading=mode == "live")
    db = Database(":memory:", mode)
    risk = RiskManager(config, db)
    risk.reconciled = True
    request = ManualDemoEntry(
        "BTCUSD", "LONG", 1, 60000, 59000, 0, "explicit-user-request"
    )
    size = fixed_demo_size(
        size_position(1000, 1000, 60000, 59000, "LONG", SPEC, config), 1, SPEC
    )
    params = dict(
        kind="manual_demo_entry",
        now=10000,
        equity=1000,
        available=1000,
        signal=request,
        sizing=size,
        market_time=10000,
    )
    result = risk.validate_trade(**params)
    assert result.approved == (mode == "testnet")
    assert request.to_dict()["source"] == "MANUAL_DEMO_REQUEST"
    assert "confirmation_candle" not in request.to_dict()
    if mode == "testnet":
        assert (
            "RISK_TOO_HIGH"
            in risk.validate_trade(
                **dict(params, sizing=replace(size, estimated_loss=11))
            ).reasons
        )
        assert (
            "STALE_MARKET_DATA"
            in risk.validate_trade(**dict(params, market_time=0)).reasons
        )
        db.reserve_signal(request)
        assert "DUPLICATE_SIGNAL" in risk.validate_trade(**params).reasons


@pytest.mark.asyncio
async def test_manual_request_cannot_enter_production():
    config = Config(trading_mode="live", enable_live_trading=True)
    manager = OrderManager(config, None, None, None, {})
    request = ManualDemoEntry("BTCUSD", "LONG", 1, 60000, 59000, 0, "one")
    with pytest.raises(DeltaError, match="TESTNET_ONLY"):
        await manager.enter(request, 60000, 10000, 10000)
