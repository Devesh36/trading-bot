from dataclasses import replace
from config import Config
from storage.database import Database
from risk.risk_manager import RiskManager
from risk.position_sizing import size_position
from strategy.signal import Signal
from tests.test_position_sizing import SPEC


def test_limits_and_duplicate():
    c = Config()
    db = Database(":memory:")
    r = RiskManager(c, db)
    r.reconciled = True
    signal = Signal("LONG", "BTCUSD", entry_reference=60000, supertrend=59000)
    s = size_position(10000, 10000, 60000, 59000, "LONG", SPEC, c)
    args = dict(
        now=1800,
        equity=10000,
        available=10000,
        signal=signal,
        sizing=s,
        market_time=1800,
    )
    assert r.validate_trade(**args).approved
    db.reserve_signal(signal)
    assert "DUPLICATE_SIGNAL" in r.validate_trade(**args).reasons
    assert (
        "RISK_TOO_HIGH"
        in r.validate_trade(**dict(args, sizing=replace(s, estimated_loss=101))).reasons
    )
    assert "STALE_MARKET_DATA" in r.validate_trade(**dict(args, market_time=0)).reasons


def test_daily_loss_latched_and_cooldown():
    db = Database(":memory:")
    r = RiskManager(Config(), db)
    r.reconciled = True
    r.mark(1000, 10000)
    r.mark(1001, 9699)
    assert r.mark(1002, 10050)["loss_latched"]
    r.loss_recorded(1002, -10, 9699)
    d = r.validate_trade(now=1003, equity=10050, market_time=1003)
    assert "DAILY_LOSS_LIMIT" in d.reasons and "COOLDOWN_ACTIVE" in d.reasons


def test_daily_latch_survives_restart_and_resets_next_day(tmp_path):
    path = str(tmp_path / "state.db")
    db = Database(path)
    r = RiskManager(Config(), db)
    r.mark(1000, 10000)
    r.mark(1001, 9600)
    db.close()
    db = Database(path)
    r = RiskManager(Config(), db)
    assert r.mark(1002, 11000)["loss_latched"]
    assert not r.mark(1000 + 86400, 11000)["loss_latched"]


def test_kill_is_persistent(tmp_path):
    path = str(tmp_path / "state.db")
    db = Database(path)
    r = RiskManager(Config(), db)
    r.kill("API_ERROR")
    db.close()
    db = Database(path)
    assert RiskManager(Config(), db).kill_reason == "API_ERROR"


def test_nonfinite_margin_is_rejected():
    db = Database(":memory:")
    risk = RiskManager(Config(), db)
    risk.reconciled = True
    decision = risk.validate_trade(
        now=1800, equity=10000, available=float("nan"), market_time=1800
    )
    assert "INSUFFICIENT_MARGIN" in decision.reasons
