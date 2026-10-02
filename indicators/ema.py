import numpy as np
import pandas as pd


def ema(values, period):
    """SMA-seeded EMA, alpha=2/(n+1); NaN before n finite observations."""
    x = np.asarray(values, dtype=float)
    out = np.full(len(x), np.nan)
    seed = []
    prev = None
    for i, v in enumerate(x):
        if not np.isfinite(v):
            seed = []
            prev = None
            continue
        if prev is None:
            seed.append(v)
            if len(seed) == period:
                prev = float(np.mean(seed))
                out[i] = prev
        else:
            prev = prev + (2 / (period + 1)) * (v - prev)
            out[i] = prev
    return pd.Series(out, index=getattr(values, "index", None))
