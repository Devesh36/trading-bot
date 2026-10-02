from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
import math


@dataclass(frozen=True)
class Decision:
    approved: bool
    reasons: tuple = ()


class RiskManager:
    def __init__(self, config, db):
        self.config = config
        self.db = db
        self.reconciled = False
        self.kill_reason = db.get("kill_reason", "")

    def kill(self, reason):
        self.kill_reason = reason
        self.db.set("kill_reason", reason)
        self.db.event("KILL_SWITCH", reason=reason)

    def day_key(self, now):
        return (
            datetime.fromtimestamp(now, ZoneInfo(self.config.risk_timezone))
            .date()
            .isoformat()
        )

    def daily(self, now, equity):
        day = self.day_key(now)
        stats = self.db.day(day)
        if stats is None:
            if not math.isfinite(equity) or equity <= 0:
                raise ValueError("ACCOUNT_EQUITY_UNKNOWN")
            stats = {
                "start_equity": equity,
                "trades": 0,
                "realized_pnl": 0.0,
                "last_equity": equity,
                "loss_latched": False,
            }
            self.db.save_day(day, stats)
        return stats

    def mark(self, now, equity):
        s = self.daily(now, equity)
        s["last_equity"] = equity
        if equity <= s["start_equity"] * (1 - self.config.max_daily_loss_percent / 100):
            s["loss_latched"] = True
        self.db.save_day(self.day_key(now), s)
        return s

    def entry_recorded(self, now, equity):
        s = self.daily(now, equity)
        s["trades"] += 1
        self.db.save_day(self.day_key(now), s)

    def loss_recorded(self, now, pnl, equity):
        s = self.daily(now, equity)
        s["realized_pnl"] += pnl
        self.db.save_day(self.day_key(now), s)
        if pnl < 0:
            self.db.set("last_loss_time", now)

    def validate_trade(
        self,
        *,
        kind="entry",
        now=0,
        equity=0,
        available=0,
        signal=None,
        sizing=None,
        positions=None,
        market_time=0,
        reduce_only=False,
        contracts=0,
        position=None,
        stop=None,
    ):
        # Exits/protection remain possible when entries are halted, but cannot increase exposure.
        if kind in {"protect", "exit", "cancel", "edit"}:
            reasons = []
            if (
                position is None
                or not reduce_only
                or not 0 < contracts <= position.contracts
            ):
                reasons.append("INVALID_REDUCE_ONLY_ACTION")
            if stop is not None and position is not None:
                if not math.isfinite(stop) or stop <= 0:
                    reasons.append("INVALID_STOP")
                if position.sign * (stop - position.stop) < -1e-9:
                    reasons.append("STOP_WIDENING")
            return Decision(not reasons, tuple(reasons))
        reasons = []
        manual = kind == "manual_demo_entry"
        if manual and self.config.trading_mode != "testnet":
            reasons.append("MANUAL_ENTRY_TESTNET_ONLY")
        positions = positions or {}
        if self.kill_reason:
            reasons.append(self.kill_reason)
        if not self.reconciled:
            reasons.append("NOT_RECONCILED")
        if not math.isfinite(equity) or equity <= 0:
            return Decision(False, ("ACCOUNT_EQUITY_UNKNOWN",))
        if not math.isfinite(available) or available < 0:
            reasons.append("INSUFFICIENT_MARGIN")
        s = self.mark(now, equity)
        if s["loss_latched"]:
            reasons.append("DAILY_LOSS_LIMIT")
        if s["trades"] >= self.config.max_trades_per_day:
            reasons.append("DAILY_TRADE_LIMIT")
        if len(positions) >= self.config.max_open_positions:
            reasons.append("POSITION_LIMIT")
        if signal is None or signal.signal == "NONE":
            reasons.append("NO_SIGNAL")
        else:
            if not manual and signal.confirmation_candle + 1800 > now:
                reasons.append("INCOMPLETE_CONFIRMATION")
            if (
                not manual
                and now - (signal.confirmation_candle + 1800)
                > self.config.stale_seconds
            ):
                reasons.append("STALE_SIGNAL")
            if signal.symbol in positions:
                reasons.append("CONFLICTING_POSITION")
            if self.db.seen(signal.signal_id):
                reasons.append("DUPLICATE_SIGNAL")
        if now - market_time > self.config.stale_seconds or now < market_time:
            reasons.append("STALE_MARKET_DATA")
        last = self.db.get("last_loss_time")
        if (
            last is not None
            and now - last < self.config.cooldown_after_loss_minutes * 60
        ):
            reasons.append("COOLDOWN_ACTIVE")
        if sizing is None:
            reasons.append("INVALID_STOP")
        else:
            if (
                not all(
                    math.isfinite(x)
                    for x in (sizing.estimated_loss, sizing.margin, sizing.stop)
                )
                or sizing.estimated_loss
                > equity * self.config.risk_per_trade_percent / 100 + 1e-9
            ):
                reasons.append("RISK_TOO_HIGH")
            if (
                sizing.contracts < 1
                or sizing.stop <= 0
                or (
                    signal
                    and (1 if signal.signal == "LONG" else -1)
                    * (signal.entry_reference - sizing.stop)
                    <= 0
                )
            ):
                reasons.append("INVALID_STOP")
            if (
                sizing.margin > available
                or sizing.margin
                > equity * self.config.max_position_equity_percent / 100 + 1e-9
            ):
                reasons.append("INSUFFICIENT_MARGIN")
        return Decision(not reasons, tuple(dict.fromkeys(reasons)))
