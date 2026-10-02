import numpy as np
import pandas as pd


def trade_metrics(trades):
    pnl = np.array([t["pnl"] for t in trades], float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    return dict(
        total_trades=len(pnl),
        winning_trades=len(wins),
        losing_trades=len(losses),
        win_rate=100 * len(wins) / len(pnl) if len(pnl) else 0,
        net_pnl=float(pnl.sum()),
        profit_factor=float(wins.sum() / abs(losses.sum())) if len(losses) else None,
        average_win=float(wins.mean()) if len(wins) else 0,
        average_loss=float(losses.mean()) if len(losses) else 0,
        expectancy=float(pnl.mean()) if len(pnl) else 0,
        fees=sum(t["fees"] for t in trades),
        estimated_slippage=sum(t["slippage"] for t in trades),
    )


def summarize(trades, curve, initial):
    m = trade_metrics(trades)
    eq = pd.Series([r["equity"] for r in curve], dtype=float)
    # Include starting equity in running peak to capture drawdown from the first fill.
    peaks = (
        pd.concat([pd.Series([initial]), eq], ignore_index=True)
        .cummax()
        .iloc[1:]
        .reset_index(drop=True)
    )
    m["return_percent"] = 100 * (eq.iloc[-1] / initial - 1) if len(eq) else 0
    m["maximum_drawdown_percent"] = (
        float(((peaks - eq) / peaks).max() * 100) if len(eq) else 0
    )
    if len(curve):
        daily = (
            pd.Series(
                eq.to_numpy(),
                index=pd.to_datetime(
                    [r["timestamp"] for r in curve], unit="s", utc=True
                ),
            )
            .resample("1D")
            .last()
        )
        returns = daily.pct_change().dropna()
        m["sharpe_ratio"] = (
            float(np.sqrt(365) * returns.mean() / returns.std(ddof=1))
            if len(returns) > 1 and returns.std(ddof=1) > 0
            else None
        )
    else:
        m["sharpe_ratio"] = None
    m["LONG"] = trade_metrics([t for t in trades if t["direction"] == "LONG"])
    m["SHORT"] = trade_metrics([t for t in trades if t["direction"] == "SHORT"])
    return m
