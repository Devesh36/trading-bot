from pathlib import Path
import json
import pandas as pd
from data.market_data import normalize
from strategy.swing_strategy import SwingStrategy, enrich
from strategy.signal import Signal
from storage.database import Database
from risk.risk_manager import RiskManager
from portfolio.portfolio import Portfolio
from execution.paper_broker import PaperBroker
from backtest.metrics import summarize


class BacktestEngine:
    def __init__(self, config):
        self.config = config

    def run(self, candles, spec, output=None, start_time=None):
        f = enrich(normalize(candles.to_dict("records")))
        db = Database(":memory:")
        risk = RiskManager(self.config, db)
        risk.reconciled = True
        portfolio = Portfolio(db, self.config.paper_equity)
        broker = PaperBroker(self.config, db, risk, portfolio)
        strategy = SwingStrategy()
        pending = None
        pending_exit = False
        curve = []
        signals = []
        symbol = spec.symbol
        for i in range(200, len(f)):
            bar = f.iloc[i]
            now = int(bar.time)
            if start_time is not None and now < start_time:
                continue
            prices = {symbol: float(f.iloc[i - 1].close)}
            # Day baseline precedes today's price changes; positions carry across midnight.
            risk.daily(now, portfolio.equity(prices))
            prices[symbol] = float(bar.open)
            risk.mark(now, portfolio.equity(prices))
            if pending_exit:
                p = portfolio.positions.get(symbol)
                if p:
                    broker.close_position(
                        p, float(bar.open), now, "SUPERTREND_FLIP", spec
                    )
                pending_exit = False
            if pending:
                broker.enter(pending, spec, float(bar.open), now, prices, now)
                pending = None
            p = portfolio.positions.get(symbol)
            if p:
                broker.process_bar_stop(p, bar, spec)
            prices[symbol] = float(bar.close)
            p = portfolio.positions.get(symbol)
            if p:
                # Closed-candle exits fill at NEXT open; bar stop always uses previously active level.
                if strategy.exit_required(p.direction, f.iloc[i - 1], bar, now + 1800):
                    pending_exit = True
                elif int(bar.st_direction) == p.sign:
                    broker.trail(p, bar.supertrend, spec)
            risk.mark(now + 1799, portfolio.equity(prices))
            signal = strategy.evaluate(f, symbol, now + 1800, index=i)
            if signal.signal != "NONE":
                signals.append(signal.to_dict())
                pending = signal
            curve.append(dict(timestamp=now + 1800, equity=portfolio.equity(prices)))
        if portfolio.positions:
            p = portfolio.positions[symbol]
            bar = f.iloc[-1]
            broker.close_position(
                p, float(bar.close), int(bar.time) + 1800, "END_OF_BACKTEST", spec
            )
            curve[-1]["equity"] = portfolio.cash
        trades = db.trades()
        metrics = summarize(trades, curve, self.config.paper_equity)
        metrics.update(
            symbol=symbol,
            candles=len(f),
            initial_equity=self.config.paper_equity,
            first_candle=int(f.iloc[0].time) if len(f) else None,
            last_candle=int(f.iloc[-1].time) if len(f) else None,
            confirmed_signals=len(signals),
            test_start=start_time,
            execution_model="next bar open, adverse slippage, prior active stop, mark-to-market equity",
            excludes=[
                "funding",
                "taxes",
                "liquidation",
                "variable spread",
                "market impact",
            ],
        )
        if output:
            directory = Path(output)
            directory.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(
                trades,
                columns=list(trades[0])
                if trades
                else [
                    "symbol",
                    "direction",
                    "entry",
                    "exit",
                    "contracts",
                    "pnl",
                    "fees",
                ],
            ).to_csv(directory / "trades.csv", index=False)
            pd.DataFrame(curve, columns=["timestamp", "equity"]).to_csv(
                directory / "equity_curve.csv", index=False
            )
            pd.DataFrame(signals, columns=list(Signal().to_dict())).to_csv(
                directory / "signals.csv", index=False
            )
            (directory / "metrics.json").write_text(
                json.dumps(metrics, indent=2, allow_nan=False)
            )
        db.close()
        return metrics, trades, curve
