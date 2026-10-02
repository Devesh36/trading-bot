from storage.models import Position
from risk.position_sizing import size_position
from execution.position_manager import favorable_stop, stop_hit, stop_fill_reference


class PaperBroker:
    """Shares strategy/risk/contract conversion with execution; has NO exchange client."""

    def __init__(self, config, db, risk, portfolio):
        self.config = config
        self.db = db
        self.risk = risk
        self.portfolio = portfolio

    def enter(self, signal, spec, reference, now, prices, market_time):
        sign = 1 if signal.signal == "LONG" else -1
        entry = reference * (1 + sign * self.config.slippage_bps / 10000)
        equity = self.portfolio.equity(prices)
        available = self.portfolio.available(prices)
        try:
            sizing = size_position(
                equity,
                available,
                entry,
                signal.supertrend,
                signal.signal,
                spec,
                self.config,
            )
        except ValueError as e:
            self.db.event("RISK_REJECTED", symbol=signal.symbol, reason=str(e))
            return None
        decision = self.risk.validate_trade(
            now=now,
            equity=equity,
            available=available,
            signal=signal,
            sizing=sizing,
            positions=self.portfolio.positions,
            market_time=market_time,
        )
        if not decision.approved:
            self.db.event(
                "RISK_REJECTED", symbol=signal.symbol, reasons=decision.reasons
            )
            return None
        fee = entry * sizing.quantity * max(self.config.fee_rate, spec.taker_fee)
        p = Position(
            signal.symbol,
            signal.signal,
            entry,
            sizing.contracts,
            float(spec.contract_value),
            sizing.stop,
            sizing.leverage,
            int(now),
            signal.signal_id,
            fee,
            abs(entry - reference) * sizing.quantity,
            "paper-stop-" + signal.client_id(),
            "paper-" + signal.client_id(),
            reason="; ".join(signal.reason),
            last_managed_candle=signal.confirmation_candle,
        )
        with self.db.transaction():
            if not self.db.reserve_signal(signal):
                return None
            self.db.set("cash", self.portfolio.cash - fee)
            self.db.save_position(p)
            self.db.order(
                signal.client_id(),
                signal.signal_id,
                "entry",
                "filled",
                p.to_dict(),
                p.order_id,
            )
            self.db.signal_status(signal.signal_id, "filled")
            self.risk.entry_recorded(now, equity)
            self.db.event(
                "LONG_ENTRY" if sign == 1 else "SHORT_ENTRY",
                **p.to_dict(),
                estimated_loss=sizing.estimated_loss,
            )
        return p

    def close_position(self, p, reference, now, reason, spec):
        decision = self.risk.validate_trade(
            kind="exit", position=p, contracts=p.contracts, reduce_only=True
        )
        if not decision.approved:
            raise RuntimeError(str(decision.reasons))
        exit_price = reference * (1 - p.sign * self.config.slippage_bps / 10000)
        fees = exit_price * p.size * max(self.config.fee_rate, spec.taker_fee)
        gross = p.pnl(exit_price)
        net = gross - fees - p.entry_fees
        t = dict(
            p.to_dict(),
            exit=exit_price,
            exit_timestamp=int(now),
            size=p.size,
            fees=fees + p.entry_fees,
            pnl=net,
            status="closed",
            reason_for_entry=p.reason,
            reason_for_exit=reason,
            slippage=p.entry_slippage + abs(exit_price - reference) * p.size,
        )
        with self.db.transaction():
            self.db.set("cash", self.portfolio.cash + gross - fees)
            self.db.remove_position(p.symbol)
            self.db.save_trade(t)
            self.risk.loss_recorded(now, net, self.portfolio.cash)
            self.db.order(p.order_id + ":exit", p.signal_id, "exit", "filled", t)
            self.db.event("POSITION_CLOSED", **t)
        return t

    def process_bar_stop(self, p, bar, spec):
        if stop_hit(p, float(bar.high), float(bar.low)):
            return self.close_position(
                p,
                stop_fill_reference(p, float(bar.open)),
                int(bar.time) + 1800,
                "PROTECTIVE_STOP",
                spec,
            )
        return None

    def trail(self, p, level, spec):
        stop = favorable_stop(p, level, spec)
        if stop == p.stop:
            return False
        permit = self.risk.validate_trade(
            kind="edit", position=p, contracts=p.contracts, reduce_only=True, stop=stop
        )
        if not permit.approved:
            raise RuntimeError(str(permit.reasons))
        p.stop = stop
        self.db.save_position(p)
        self.db.event("STOP_UPDATED", symbol=p.symbol, stop=stop)
        return True
