import math
import pandas as pd

SECONDS = 1800
COLUMNS = ["time", "open", "high", "low", "close", "volume"]


def normalize(rows, now=None):
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=COLUMNS)
    if not set(COLUMNS) <= set(frame):
        raise ValueError("Malformed candles")
    frame = frame[COLUMNS].copy()
    for name in COLUMNS:
        frame[name] = pd.to_numeric(frame[name], errors="raise")
        if not frame[name].map(math.isfinite).all():
            raise ValueError("Nonfinite candle")
    if (frame.time % SECONDS != 0).any():
        raise ValueError("Candle timestamps must be UTC open seconds aligned to 30m")
    if frame.time.duplicated().any():
        if (
            frame[frame.time.duplicated(False)].groupby("time").nunique().max().max()
            > 1
        ):
            raise ValueError("Conflicting duplicate candles")
    frame = frame.drop_duplicates("time").sort_values("time").reset_index(drop=True)
    if now is not None:
        frame = frame[frame.time + SECONDS <= now].reset_index(drop=True)
    if (
        (frame.low <= 0)
        | (frame.volume < 0)
        | (frame.high < frame[["open", "close", "low"]].max(axis=1))
        | (frame.low > frame[["open", "close", "high"]].min(axis=1))
    ).any():
        raise ValueError("Invalid OHLCV")
    if len(frame) > 1 and (frame.time.diff().dropna() != SECONDS).any():
        raise ValueError("GAP_IN_CANDLES")
    frame["time"] = frame.time.astype("int64")
    return frame


async def history(client, symbol, start, end):
    # Docs cap a response at 2000 candles; bounded 1000-candle windows avoid truncation.
    rows = []
    cursor = int(start) // SECONDS * SECONDS
    end = int(end) // SECONDS * SECONDS
    while cursor < end:
        until = min(end, cursor + 1000 * SECONDS)
        batch = await client.candles(symbol, cursor, until - 1)
        rows.extend(r for r in batch if cursor <= int(r["time"]) < until)
        cursor = until
    result = normalize(rows, now=end)
    if (
        result.empty
        or int(result.iloc[0].time) > int(start) // SECONDS * SECONDS
        or int(result.iloc[-1].time) != end - SECONDS
    ):
        raise ValueError("HISTORY_INCOMPLETE")
    return result
