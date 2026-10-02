"""Create inspectable chart examples from saved backtest data, without TradingView."""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import pandas as pd
from strategy.swing_strategy import enrich, SwingStrategy, momentum, candle_confirmation

root = Path(__file__).resolve().parents[1]
out = root / "artifacts" / "chart-review"
out.mkdir(parents=True, exist_ok=True)
audit = []
for symbol in ["BTCUSD", "ETHUSD"]:
    base = root / "artifacts" / f"backtest-{symbol}"
    f = enrich(pd.read_csv(base / "candles.csv"))
    signals = pd.read_csv(base / "signals.csv")
    for direction in ["LONG", "SHORT"]:
        example = signals[signals.signal == direction].iloc[0]
        idx = int(f.index[f.time == example.confirmation_candle][0])
        b = f.iloc[idx - 1]
        c = f.iloc[idx]
        a = f.iloc[idx - 2]
        sign = 1 if direction == "LONG" else -1
        actual = SwingStrategy().evaluate(f, symbol, int(c.time) + 1800, index=idx)
        checks = dict(
            flip=bool(a.st_direction == -sign and b.st_direction == sign),
            setup_filters=bool(momentum(b, sign)),
            confirmation_filters=bool(momentum(c, sign)),
            next_candle=bool(c.time - b.time == 1800),
            candle_breakout=bool(candle_confirmation(b, c, sign)),
            emitted=actual.signal == direction,
        )
        assert all(checks.values())
        audit.append(
            dict(
                symbol=symbol,
                direction=direction,
                confirmation_utc=pd.to_datetime(c.time, unit="s", utc=True).isoformat(),
                setup={
                    k: float(b[k])
                    for k in [
                        "open",
                        "high",
                        "low",
                        "close",
                        "ema200",
                        "supertrend",
                        "macd",
                        "macd_signal",
                        "macd_histogram",
                    ]
                },
                confirmation={
                    k: float(c[k])
                    for k in [
                        "open",
                        "high",
                        "low",
                        "close",
                        "ema200",
                        "supertrend",
                        "macd",
                        "macd_signal",
                        "macd_histogram",
                    ]
                },
                checks=checks,
            )
        )
        w = f.iloc[idx - 14 : idx + 5].reset_index(drop=True)
        x = list(range(len(w)))
        fig, ax = plt.subplots(
            3, 1, figsize=(12, 8), sharex=True, gridspec_kw={"height_ratios": [3, 1, 1]}
        )
        for j, row in w.iterrows():
            color = "#13795b" if row.close >= row.open else "#b42318"
            ax[0].plot([j, j], [row.low, row.high], color=color, linewidth=1)
            ax[0].add_patch(
                Rectangle(
                    (j - 0.3, min(row.open, row.close)),
                    0.6,
                    max(abs(row.close - row.open), 0.01),
                    facecolor=color,
                )
            )
        ax[0].plot(x, w.ema200, label="EMA200", color="#505060")
        for d, color in [(1, "#13795b"), (-1, "#b42318")]:
            ax[0].plot(
                x,
                w.supertrend.where(w.st_direction == d),
                color=color,
                label=f"Supertrend {'green' if d == 1 else 'red'}",
                marker=".",
                markersize=3,
            )
        for j, label in [(13, "Signal flip"), (14, "Confirmation close")]:
            for axis in ax:
                axis.axvline(j, color="#344054", ls=":", alpha=0.6)
            ax[0].annotate(
                label,
                (j, w.iloc[j].high),
                xytext=(0.60 if j == 13 else 0.82, 0.97),
                textcoords="axes fraction",
                arrowprops={"arrowstyle": "->"},
                fontsize=8,
                ha="center",
            )
        ax[0].set_ylabel("Price (USD)")
        ax[0].legend(loc="lower left", fontsize=8, ncol=3)
        ax[1].bar(
            x,
            w.macd_histogram,
            color=["#13795b" if v > 0 else "#b42318" for v in w.macd_histogram],
            alpha=0.4,
            label="Histogram",
        )
        ax[1].plot(x, w.macd, label="MACD", color="#1d4ed8")
        ax[1].plot(x, w.macd_signal, label="Signal", color="#9a6700")
        ax[1].axhline(0, color="gray", lw=0.6)
        ax[1].set_ylabel("MACD (USD)")
        ax[1].legend(fontsize=8, ncol=3)
        ax[2].bar(x, w.volume, color="#98a2b3", label="Volume")
        ax[2].plot(x, w.volume_ma, color="#344054", label="20-bar mean")
        ax[2].set_ylabel("Volume (API units)")
        ax[2].legend(fontsize=8, ncol=2)
        ticks = x[::3]
        ax[2].set_xticks(
            ticks,
            [
                pd.to_datetime(w.iloc[j].time, unit="s", utc=True).strftime(
                    "%m-%d %H:%M"
                )
                for j in ticks
            ],
            rotation=20,
        )
        ax[2].set_xlabel("Candle open time (UTC), 30 minutes")
        fig.suptitle(
            f"{symbol} {direction} — confirmation candle opens {audit[-1]['confirmation_utc']}\nSource: Delta India public OHLC; future bars displayed only for review",
            fontsize=12,
        )
        fig.tight_layout()
        fig.savefig(out / f"{symbol}-{direction}.png", dpi=150)
        plt.close(fig)
(out / "boolean-audit.json").write_text(json.dumps(audit, indent=2))
print(out)
