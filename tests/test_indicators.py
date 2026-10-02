import numpy as np
import pandas as pd
from indicators.ema import ema
from indicators.macd import macd
from indicators.supertrend import supertrend
from strategy.swing_strategy import enrich


def test_ema_seed_and_recursion():
    x = ema(pd.Series([1, 2, 3, 4, 5]), 3)
    assert x.iloc[:2].isna().all()
    np.testing.assert_allclose(x.iloc[2:], [2, 3, 4])


def test_macd_constant_and_linear():
    m, s, h = macd(pd.Series(np.ones(100) * 42))
    assert m.iloc[25] == 0 and np.isnan(s.iloc[32]) and s.iloc[33] == 0
    assert h.iloc[-1] == 0
    m, s, h = macd(pd.Series(np.arange(100, dtype=float)))
    np.testing.assert_allclose(
        [m.iloc[-1], s.iloc[-1], h.iloc[-1]], [7, 7, 0], atol=1e-10
    )


def test_supertrend_hand_fixture():
    f = pd.DataFrame(
        dict(
            high=[11, 12, 13, 20, 9], low=[9, 10, 11, 18, 7], close=[10, 11, 12, 19, 8]
        )
    )
    s = supertrend(f, period=3, multiplier=1)
    np.testing.assert_allclose(s.atr.iloc[2:], [2, 4, 20 / 3])
    assert s.st_direction.tolist() == [0, 0, -1, 1, -1]
    np.testing.assert_allclose(s.supertrend.iloc[2:], [14, 15, 44 / 3])


def test_causal_prefix_invariance():
    rng = np.random.default_rng(4)
    c = 100 + np.cumsum(rng.normal(size=400))
    f = pd.DataFrame(
        dict(
            time=np.arange(400) * 1800,
            open=c,
            high=c + 1,
            low=c - 1,
            close=c,
            volume=10,
        )
    )
    full = enrich(f)
    pd.testing.assert_frame_equal(full.iloc[:300], enrich(f.iloc[:300]))
