import numpy as np
import pandas as pd
from backtest.engine import BacktestEngine
from config import Config
from tests.test_position_sizing import SPEC


def synthetic():
    rng = np.random.default_rng(0)
    n = 1800
    t = np.arange(n)
    c = 60000 + np.cumsum(rng.normal(0, 150, n))
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame(
        dict(
            time=t * 1800,
            open=o,
            high=np.maximum(o, c) + rng.uniform(1, 40, n),
            low=np.minimum(o, c) - rng.uniform(1, 40, n),
            close=c,
            volume=10,
        )
    )


def test_backtest_real_pipeline(tmp_path):
    metrics, trades, curve = BacktestEngine(Config()).run(synthetic(), SPEC, tmp_path)
    assert len(curve) == 1600
    assert len(trades) >= 4
    assert metrics["total_trades"] == len(trades)
    assert (tmp_path / "equity_curve.csv").exists()
    assert abs(metrics["net_pnl"] - (curve[-1]["equity"] - 10000)) < 1e-7


def test_future_prices_do_not_change_past_trades():
    frame = synthetic()
    changed = frame.copy()
    cutoff = 800 * 1800
    changed.loc[800:, ["open", "high", "low", "close"]] *= 1.2
    _, a, ea = BacktestEngine(Config()).run(frame, SPEC)
    _, b, eb = BacktestEngine(Config()).run(changed, SPEC)
    assert len([t for t in a if t["exit_timestamp"] < cutoff]) >= 3
    assert [t for t in a if t["exit_timestamp"] < cutoff] == [
        t for t in b if t["exit_timestamp"] < cutoff
    ]
    assert [e for e in ea if e["timestamp"] < cutoff] == [
        e for e in eb if e["timestamp"] < cutoff
    ]
