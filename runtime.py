import asyncio
import logging
from logging.handlers import RotatingFileHandler
import math
from pathlib import Path
import time
from process_lock import ProcessLock as ProcessLock  # Public compatibility import.
import pandas as pd
from exchange.delta_client import DeltaClient
from data.market_data import history, normalize
from strategy.swing_strategy import enrich, SwingStrategy
from storage.database import Database
from risk.risk_manager import RiskManager
from portfolio.portfolio import Portfolio
from execution.paper_broker import PaperBroker
from execution.order_manager import OrderManager
from execution.position_manager import stop_hit
from notifications.telegram import TelegramNotifier
from ui.dashboard import render


def setup_logging(config):
    Path(config.log_path).parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(config.log_path, maxBytes=5_000_000, backupCount=5)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger("bot").setLevel(logging.INFO)
    logging.getLogger("bot").addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.CRITICAL)
    logging.getLogger("httpcore").setLevel(logging.CRITICAL)


class Runner:
    def __init__(self, config):
        self.config = config
        self.db = Database(config.db_path, config.trading_mode)
        self.risk = RiskManager(config, self.db)
        self.client = DeltaClient(config)
        self.notifier = TelegramNotifier(config)
        self.portfolio = (
            Portfolio(self.db, config.paper_equity)
            if config.trading_mode == "paper"
            else None
        )
        self.strategy = SwingStrategy()
        self.specs = {}
        self.frames = {}
        self.rows = {}
        self.prices = {}
        self.broker = None
        self.cache = Path(config.db_path).parent / "candles" / config.trading_mode
        self.cache.mkdir(parents=True, exist_ok=True)

    async def load_frame(self, symbol, now):
        end = int(now) // 1800 * 1800
        path = self.cache / f"{symbol}.csv"
        old = self.frames.get(symbol)
        if old is None and path.exists():
            old = normalize(pd.read_csv(path).to_dict("records"), now=now)
        start = (
            int(old.iloc[-1].time) + 1800
            if old is not None and len(old)
            else end - 1000 * 1800
        )
        if start < end:
            new = await history(self.client, symbol, start, end)
            raw = pd.concat([old, new], ignore_index=True) if old is not None else new
            raw = normalize(raw.to_dict("records"), now)
            temporary = path.with_suffix(".tmp")
            raw.to_csv(temporary, index=False)
            temporary.replace(path)
            self.frames[symbol] = raw
        elif old is not None:
            self.frames[symbol] = old
        else:
            raise ValueError("Missing history")
        return enrich(self.frames[symbol])

    async def startup(self):
        for symbol in self.config.symbol_list:
            self.specs[symbol] = await self.client.product(symbol)
        if self.portfolio:
            # Check persisted paper exposure before marking reconciliation complete.
            for symbol, p in self.portfolio.positions.items():
                if (
                    symbol not in self.specs
                    or p.contracts <= 0
                    or not math.isfinite(p.stop)
                    or p.stop <= 0
                ):
                    raise ValueError("PAPER_STATE_INVALID")
                if p.contract_value != float(self.specs[symbol].contract_value):
                    raise ValueError("CONTRACT_SPEC_CHANGED")
            if not math.isfinite(self.portfolio.cash):
                raise ValueError("PAPER_EQUITY_INVALID")
            self.risk.reconciled = True
            self.broker = PaperBroker(self.config, self.db, self.risk, self.portfolio)
        else:
            self.broker = OrderManager(
                self.config, self.client, self.db, self.risk, self.specs
            )
            await self.broker.reconcile()
        self.db.event("BOT_STARTED", mode=self.config.trading_mode)
        await self.notifier.send("BOT STARTED", f"Mode: {self.config.trading_mode}")

    async def cycle(self):
        now = time.time()
        frames = {}
        market_times = {}
        if not self.portfolio:
            await self.broker.reconcile()
        # Get all marks before portfolio-level risk calculation.
        for symbol in self.specs:
            frame = await self.load_frame(symbol, now)
            frames[symbol] = frame
            tick = await self.client.ticker(symbol)
            stamp = float(tick["timestamp"]) / 1_000_000
            if not 0 <= time.time() - stamp <= self.config.stale_seconds:
                raise ValueError("STALE_MARKET_DATA")
            price = float(tick["close"])
            if not math.isfinite(price) or price <= 0:
                raise ValueError("Invalid ticker")
            self.prices[symbol] = price
            market_times[symbol] = stamp
            if (
                frame.empty
                or int(frame.iloc[-1].time) != int(now) // 1800 * 1800 - 1800
            ):
                raise ValueError("STALE_MARKET_DATA")
        # Manage each missed closed bar, but never replay historical entries after downtime.
        for symbol, frame in frames.items():
            spec = self.specs[symbol]
            p = self.db.positions().get(symbol)
            if p:
                first_bar = max(
                    p.last_managed_candle + 1800, p.entry_timestamp // 1800 * 1800
                )
                start_index = max(1, int(frame.time.searchsorted(first_bar)))
                for i in range(start_index, len(frame)):
                    bar = frame.iloc[i]
                    p = self.db.positions().get(symbol)
                    if p is None:
                        break
                    if self.portfolio and int(bar.time) >= p.entry_timestamp:
                        self.broker.process_bar_stop(p, bar, spec)
                        p = self.db.positions().get(symbol)
                        if p is None:
                            break
                    if self.strategy.exit_required(
                        p.direction, frame.iloc[i - 1], bar, now
                    ):
                        if self.portfolio:
                            self.broker.close_position(
                                p,
                                self.prices[symbol],
                                int(now),
                                "SUPERTREND_FLIP",
                                spec,
                            )
                        else:
                            await self.broker.close_position(p, "SUPERTREND_FLIP")
                        break
                    if p.status != "recovered" and int(bar.st_direction) == p.sign:
                        if self.portfolio:
                            self.broker.trail(p, bar.supertrend, spec)
                        else:
                            await self.broker.trail(p, bar.supertrend)
                    p.last_managed_candle = int(bar.time)
                    self.db.save_position(p)
                p = self.db.positions().get(symbol)
                if (
                    p
                    and self.portfolio
                    and stop_hit(p, self.prices[symbol], self.prices[symbol])
                ):
                    self.broker.close_position(
                        p, self.prices[symbol], int(now), "PROTECTIVE_STOP", spec
                    )
            row = frame.iloc[-1]
            signal = self.strategy.evaluate(frame, symbol, now)
            self.rows[symbol] = dict(
                price=self.prices[symbol],
                ema200=float(row.ema200),
                supertrend=float(row.supertrend),
                macd=float(row.macd),
                signal=signal.signal,
            )
            equity = (
                self.portfolio.equity(self.prices)
                if self.portfolio
                else self.broker.equity
            )
            self.risk.mark(now, equity)
            processed = self.db.get("last_signal_candle:" + symbol, -1)
            if int(row.time) > processed:
                # An old valid signal is not a fresh order after a restart.
                if (
                    signal.signal != "NONE"
                    and now - (signal.confirmation_candle + 1800)
                    <= self.config.stale_seconds
                ):
                    self.db.event("SIGNAL_CONFIRMED", **signal.to_dict())
                    if self.portfolio:
                        self.broker.enter(
                            signal,
                            spec,
                            self.prices[symbol],
                            int(now),
                            self.prices,
                            market_times[symbol],
                        )
                    else:
                        await self.broker.enter(
                            signal, self.prices[symbol], int(now), market_times[symbol]
                        )
                self.db.set("last_signal_candle:" + symbol, int(row.time))
        equity = (
            self.portfolio.equity(self.prices) if self.portfolio else self.broker.equity
        )
        daily = self.risk.mark(now, equity)
        if daily["loss_latched"] and not self.db.get(
            "daily_alert:" + self.risk.day_key(now)
        ):
            self.db.event("DAILY_LOSS_LIMIT", equity=equity)
            self.db.set("daily_alert:" + self.risk.day_key(now), True)
        state = dict(
            state="HALTED"
            if self.risk.kill_reason or daily["loss_latched"]
            else "RUNNING",
            markets=self.rows,
            equity=equity,
            daily=daily,
            last_api=self.client.last_success,
            updated_at=now,
        )
        self.db.set("status", state)
        render(
            self.config, self.db, self.rows, equity, self.risk, self.client.last_success
        )
        await self.send_events()

    async def send_events(self):
        last = self.db.get("notification_cursor", 0)
        events = list(
            self.db.conn.execute(
                "SELECT * FROM bot_events WHERE id>? ORDER BY id", (last,)
            )
        )
        allowed = {
            "LONG_ENTRY",
            "SHORT_ENTRY",
            "STOP_UPDATED",
            "POSITION_CLOSED",
            "DAILY_LOSS_LIMIT",
            "API_ERROR",
            "KILL_SWITCH",
        }
        for event in events:
            if event["event"] in allowed:
                await self.notifier.send(event["event"], event["payload"])
            self.db.set("notification_cursor", event["id"])

    async def run(self, once=False):
        try:
            await self.startup()
            while True:
                try:
                    await self.cycle()
                except Exception as exc:
                    self.risk.kill("RUNTIME_FAILURE:" + type(exc).__name__)
                    self.db.event(
                        "API_ERROR", error=type(exc).__name__, reason=str(exc)[:200]
                    )
                    render(self.config, self.db)
                    await self.send_events()
                    if once:
                        raise
                if once:
                    break
                await asyncio.sleep(self.config.poll_seconds)
        except Exception as exc:
            self.risk.kill("STARTUP_OR_RUNTIME_FAILURE:" + type(exc).__name__)
            raise
        finally:
            self.db.set("status", dict(self.db.get("status", {}), state="STOPPED"))
            self.db.event("BOT_STOPPED")
            await self.notifier.send(
                "BOT STOPPED",
                f"Mode: {self.config.trading_mode}. "
                + (
                    "Paper stops are local and are now stopped."
                    if self.portfolio
                    else "Exchange protective orders remain active."
                ),
            )
            await self.client.close()
            self.db.close()
