import numpy as np
import pandas as pd


def supertrend(frame, period=13, multiplier=4.0):
    """Wilder ATR seeded by mean(TR[0:n]); initial valid direction is bearish.
    Final bands recurse using the PREVIOUS close; flips require strict crossing.
    """
    h = frame.high.to_numpy(float)
    l = frame.low.to_numpy(float)
    c = frame.close.to_numpy(float)
    n = len(c)
    atr = np.full(n, np.nan)
    upper = atr.copy()
    lower = atr.copy()
    st = atr.copy()
    direction = np.zeros(n, dtype=int)
    if not n:
        return pd.DataFrame(
            dict(atr=atr, supertrend=st, st_direction=direction), index=frame.index
        )
    prev = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(abs(h - prev), abs(l - prev)))
    if n >= period:
        atr[period - 1] = np.mean(tr[:period])
        for i in range(period, n):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
        for i in range(period - 1, n):
            bu = (h[i] + l[i]) / 2 + multiplier * atr[i]
            bl = (h[i] + l[i]) / 2 - multiplier * atr[i]
            if i == period - 1:
                upper[i] = bu
                lower[i] = bl
                direction[i] = -1
                st[i] = bu
                continue
            upper[i] = (
                bu if bu < upper[i - 1] or c[i - 1] > upper[i - 1] else upper[i - 1]
            )
            lower[i] = (
                bl if bl > lower[i - 1] or c[i - 1] < lower[i - 1] else lower[i - 1]
            )
            if direction[i - 1] == -1:
                direction[i] = 1 if c[i] > upper[i] else -1
            else:
                direction[i] = -1 if c[i] < lower[i] else 1
            st[i] = lower[i] if direction[i] == 1 else upper[i]
    return pd.DataFrame(
        dict(atr=atr, supertrend=st, st_direction=direction), index=frame.index
    )
