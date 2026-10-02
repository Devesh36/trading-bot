"""Pure, causal strategy. No exchange imports or execution side effects."""

import math
from indicators.ema import ema
from indicators.macd import macd
from indicators.supertrend import supertrend
from indicators.volume import volume_context
from strategy.signal import Signal


def enrich(frame):
    f = volume_context(frame)
    f["ema200"] = ema(f.close, 200)
    f["macd"], f["macd_signal"], f["macd_histogram"] = macd(f.close)
    return f.join(supertrend(f))


def zero_line_confirmation(row, direction):
    # User-selected interpretation: either line may satisfy the zero-line test.
    return (
        (row.macd > 0 or row.macd_signal > 0)
        if direction == 1
        else (row.macd < 0 or row.macd_signal < 0)
    )


def momentum(row, direction):
    if not all(
        math.isfinite(float(row[k]))
        for k in ("ema200", "supertrend", "macd", "macd_signal", "macd_histogram")
    ):
        return False
    return (
        direction * (row.close - row.ema200) > 0
        and int(row.st_direction) == direction
        and direction * (row.macd - row.macd_signal) > 0
        and direction * row.macd_histogram > 0
        and zero_line_confirmation(row, direction)
    )


def candle_confirmation(setup, confirm, direction):
    return (
        (confirm.close > confirm.open and confirm.close > setup.high)
        if direction == 1
        else (confirm.close < confirm.open and confirm.close < setup.low)
    )


class SwingStrategy:
    def evaluate(self, frame, symbol, now, index=None):
        i = len(frame) - 1 if index is None else index
        if i < 200:
            return Signal(symbol=symbol)
        a, b, c = (frame.iloc[j] for j in (i - 2, i - 1, i))
        if c.time + 1800 > now or b.time + 1800 != c.time or a.time + 1800 != b.time:
            return Signal(symbol=symbol)
        for direction, name in ((1, "LONG"), (-1, "SHORT")):
            if (
                a.st_direction == -direction
                and b.st_direction == direction
                and momentum(b, direction)
                and momentum(c, direction)
                and candle_confirmation(b, c, direction)
            ):
                return Signal(
                    name,
                    symbol,
                    "30m",
                    int(b.time),
                    int(c.time),
                    float(c.close),
                    float(c.ema200),
                    float(c.supertrend),
                    float(c.macd),
                    float(c.macd_signal),
                    float(c.macd_histogram),
                    (
                        "EMA200 filter",
                        "fresh Supertrend flip",
                        "MACD and histogram agree",
                        "either MACD line beyond zero",
                        "next closed candle breaks signal extreme",
                    ),
                )
        return Signal(symbol=symbol)

    @staticmethod
    def exit_required(direction, previous, current, now):
        sign = 1 if direction == "LONG" else -1
        return bool(
            current.time + 1800 <= now
            and current.time - previous.time == 1800
            and previous.st_direction == sign
            and current.st_direction == -sign
        )
