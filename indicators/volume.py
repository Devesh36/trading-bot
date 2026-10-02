def volume_context(frame, period=20):
    f = frame.copy()
    f["volume_ma"] = f.volume.rolling(period).mean()
    f["relative_volume"] = f.volume / f.volume_ma.replace(0, float("nan"))
    # Prior completed rolling extrema; no centered/future pivot detection.
    f["recent_swing_high"] = f.high.shift(1).rolling(period).max()
    f["recent_swing_low"] = f.low.shift(1).rolling(period).min()
    f["higher_high"] = f.high > f.recent_swing_high
    f["lower_low"] = f.low < f.recent_swing_low
    return f
