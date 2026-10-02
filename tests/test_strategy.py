import pandas as pd
import pytest
from strategy.swing_strategy import SwingStrategy, zero_line_confirmation


def fixture(direction=1):
    rows = [
        dict(
            time=i * 1800,
            open=109,
            high=111,
            low=108,
            close=110,
            ema200=100,
            supertrend=95,
            st_direction=-1,
            macd=2,
            macd_signal=1,
            macd_histogram=1,
        )
        for i in range(203)
    ]
    rows[-2].update(st_direction=1)
    rows[-1].update(st_direction=1, open=111, high=114, low=110, close=113)
    f = pd.DataFrame(rows)
    if direction == -1:
        for k in ["open", "high", "low", "close", "ema200", "supertrend"]:
            f[k] = 220 - f[k]
        f["high"], f["low"] = f.low.copy(), f.high.copy()
        for k in ["st_direction", "macd", "macd_signal", "macd_histogram"]:
            f[k] = -f[k]
    return f


@pytest.mark.parametrize("direction,name", [(1, "LONG"), (-1, "SHORT")])
def test_signals(direction, name):
    f = fixture(direction)
    s = SwingStrategy().evaluate(f, "BTCUSD", 203 * 1800)
    assert s.signal == name and s.confirmation_candle == 202 * 1800


@pytest.mark.parametrize(
    "column,value",
    [
        ("close", 99),
        ("macd", 0),
        ("macd_histogram", 0),
        ("st_direction", -1),
        ("open", 113),
    ],
)
def test_no_signal(column, value):
    f = fixture()
    f.loc[202, column] = value
    assert SwingStrategy().evaluate(f, "BTCUSD", 203 * 1800).signal == "NONE"


def test_incomplete_and_immediate_only():
    f = fixture()
    strategy = SwingStrategy()
    assert strategy.evaluate(f, "BTCUSD", 203 * 1800 - 1).signal == "NONE"
    f.loc[201, "st_direction"] = -1
    assert strategy.evaluate(f, "BTCUSD", 203 * 1800).signal == "NONE"


def test_either_line_zero_rule():
    assert zero_line_confirmation(pd.Series(dict(macd=0.1, macd_signal=-0.1)), 1)
    assert zero_line_confirmation(pd.Series(dict(macd=0.1, macd_signal=-0.1)), -1)


@pytest.mark.parametrize("direction", [1, -1])
def test_exit_only_on_closed_flip(direction):
    f = fixture(direction)
    previous = f.iloc[-1].copy()
    current = previous.copy()
    current["time"] += 1800
    current["st_direction"] = -direction
    name = "LONG" if direction == 1 else "SHORT"
    assert SwingStrategy.exit_required(
        name, previous, current, int(current.time) + 1800
    )
    assert not SwingStrategy.exit_required(
        name, previous, current, int(current.time) + 1799
    )


def test_late_confirmation_is_not_accepted():
    f = fixture()
    f.loc[202, "close"] = 110
    extra = f.iloc[-1].copy()
    extra["time"] += 1800
    extra["close"] = 115
    f = pd.concat([f, pd.DataFrame([extra])], ignore_index=True)
    assert SwingStrategy().evaluate(f, "BTCUSD", 204 * 1800).signal == "NONE"
