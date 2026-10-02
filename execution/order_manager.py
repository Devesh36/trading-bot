"""Durable intents, explicit reconciliation, reduce-only protective order management.

Only this module calls authenticated mutations. An uncertain write is never repeated.
The kill latch survives restarts; all unknown exposure is imported and blocks entries.
"""

import asyncio
import hashlib
import math
import time
from exchange.delta_client import DeltaError, UnknownOrderOutcome
from storage.models import Position
from risk.position_sizing import size_position
from execution.position_manager import favorable_stop
from execution.manual_demo import ManualDemoEntry, fixed_demo_size

TERMINAL = {"closed", "cancelled"}


class OrderManager:
    def __init__(self, config, client, db, risk, specs):
        self.config = config
        self.client = client
        self.db = db
        self.risk = risk
        self.specs = specs
        self.equity = 0
        self.available = 0
        self.lock = asyncio.Lock()

    def permit(self, kind, p, stop=None):
        d = self.risk.validate_trade(
            kind=kind, position=p, contracts=p.contracts, reduce_only=True, stop=stop
        )
        if not d.approved:
            raise RuntimeError(str(d.reasons))
        return d

    def verify_response(self, obj, cid, body):
        if obj.get("client_order_id") != cid or not obj.get("id"):
            raise DeltaError("ORDER_IDENTITY_MISMATCH")
        for key in ("product_id", "side", "size", "order_type", "reduce_only"):
            if key in body and obj.get(key) != body[key]:
                raise DeltaError("ORDER_RESPONSE_MISMATCH:" + key)
        if obj.get("state") not in {"open", "pending", "closed", "cancelled"}:
            raise DeltaError("UNKNOWN_ORDER_STATE")
        return obj

    async def submit_intent(self, cid, sid, kind, body, permit):
        existing = next((x for x in self.db.orders() if x["client_id"] == cid), None)
        if existing:
            # Every duplicate intent is resolved by read; never re-created even on a 404.
            return self.verify_response(
                await self.client.order_by_client_id(cid), cid, body
            )
        self.db.order(cid, sid, kind, "submitting", body)
        try:
            obj = await self.client.create_order(
                dict(body, client_order_id=cid), permit
            )
        except UnknownOrderOutcome:
            self.db.order(cid, sid, kind, "unknown", body)
            try:
                obj = await self.client.order_by_client_id(cid)
            except DeltaError:
                self.risk.kill("UNKNOWN_ORDER_OUTCOME")
                raise
        except DeltaError:
            self.db.order(cid, sid, kind, "rejected", body)
            raise
        self.verify_response(obj, cid, body)
        self.db.order(cid, sid, kind, obj["state"], obj, obj["id"])
        self.db.event(
            "ORDER_SUBMITTED",
            order_id=obj["id"],
            kind=kind,
            signal_id=sid,
            status=obj["state"],
        )
        return obj

    async def protect(self, p):
        if p.stop_order_id:
            existing = await self.client.order(p.stop_order_id)
            if existing["state"] not in {"open", "pending"}:
                raise DeltaError("PROTECTION_NOT_ACTIVE")
            return existing
        cid = "ds" + hashlib.sha256((p.signal_id + ":stop").encode()).hexdigest()[:30]
        body = dict(
            product_id=self.specs[p.symbol].product_id,
            size=p.contracts,
            side="sell" if p.sign == 1 else "buy",
            order_type="market_order",
            stop_order_type="stop_loss_order",
            stop_price=str(p.stop),
            stop_trigger_method="last_traded_price",
            reduce_only=True,
        )
        obj = await self.submit_intent(
            cid, p.signal_id, "protect", body, self.permit("protect", p, p.stop)
        )
        if obj["state"] not in {"open", "pending"}:
            self.risk.kill("PROTECTION_NOT_ACTIVE")
            raise DeltaError("Protective stop is not active; reconcile immediately")
        p.stop_order_id = str(obj["id"])
        self.db.save_position(p)
        self.db.event(
            "STOP_UPDATED", symbol=p.symbol, stop=p.stop, order_id=p.stop_order_id
        )
        return obj

    async def settle_entry(self, obj, p):
        """Protect partial fills before cancelling remainder, then resize to final fill."""
        for _ in range(4):
            filled = int(obj["size"]) - int(obj["unfilled_size"])
            if filled > 0:
                if not obj.get("average_fill_price"):
                    raise DeltaError("Missing fill price")
                p.contracts = filled
                p.entry = float(obj["average_fill_price"])
                self.db.save_position(p)
                await self.protect(p)
            if obj["state"] in TERMINAL:
                break
            if filled > 0:
                await self.client.cancel_order(
                    obj["id"], self.specs[p.symbol].product_id, self.permit("cancel", p)
                )
            await asyncio.sleep(0.25)
            obj = await self.client.order(obj["id"])
        if obj["state"] not in TERMINAL:
            # A still-open IOC response is unexpected. Cancel the known order ID;
            # never leave a zero-fill entry capable of later opening naked exposure.
            await self.client.cancel_order(
                obj["id"], self.specs[p.symbol].product_id, self.permit("cancel", p)
            )
            self.risk.kill("ENTRY_NOT_TERMINAL")
            raise DeltaError(
                "Entry remains unresolved; existing partial fills retain protection"
            )
        filled = int(obj["size"]) - int(obj["unfilled_size"])
        if filled <= 0:
            self.db.remove_position(p.symbol)
            self.db.signal_status(p.signal_id, "unfilled")
            return None
        p.contracts = filled
        p.entry = float(obj["average_fill_price"])
        self.db.save_position(p)
        await self.protect(p)
        stop = await self.client.order(p.stop_order_id)
        if int(stop["size"]) != filled:
            stop = await self.client.edit_order(
                dict(
                    id=int(p.stop_order_id),
                    product_id=self.specs[p.symbol].product_id,
                    size=filled,
                    stop_price=str(p.stop),
                ),
                self.permit("edit", p, p.stop),
            )
            if int(stop["size"]) != filled:
                raise DeltaError("Stop size not confirmed")
        self.db.signal_status(p.signal_id, "filled")
        return p

    async def enter(self, signal, reference, now, market_time):
        manual = isinstance(signal, ManualDemoEntry)
        if manual and self.config.trading_mode != "testnet":
            raise DeltaError("MANUAL_ENTRY_TESTNET_ONLY")
        kind = "manual_demo_entry" if manual else "entry"
        async with self.lock:
            await self.reconcile()
            spec = self.specs[signal.symbol]
            sizing = size_position(
                self.equity,
                self.available,
                reference,
                signal.supertrend,
                signal.signal,
                spec,
                self.config,
            )
            if manual:
                sizing = fixed_demo_size(sizing, signal.contracts, spec)
            permit = self.risk.validate_trade(
                kind=kind,
                now=now,
                equity=self.equity,
                available=self.available,
                signal=signal,
                sizing=sizing,
                positions=self.db.positions(),
                market_time=market_time,
            )
            if not permit.approved:
                self.db.event(
                    "RISK_REJECTED", symbol=signal.symbol, reasons=permit.reasons
                )
                return None
            self.db.event(
                "RISK_APPROVED",
                symbol=signal.symbol,
                estimated_loss=sizing.estimated_loss,
                contracts=sizing.contracts,
                stop=sizing.stop,
            )
            # Leverage is idempotent configuration, nevertheless no blind write retry.
            await self.client.leverage(spec.product_id, sizing.leverage, permit)
            permit = self.risk.validate_trade(
                kind=kind,
                now=time.time(),
                equity=self.equity,
                available=self.available,
                signal=signal,
                sizing=sizing,
                positions=self.db.positions(),
                market_time=market_time,
            )
            if not permit.approved:
                return None
            if not self.db.reserve_signal(signal):
                return None
            p = Position(
                signal.symbol,
                signal.signal,
                reference,
                sizing.contracts,
                float(spec.contract_value),
                sizing.stop,
                sizing.leverage,
                int(now),
                signal.signal_id,
                reason="; ".join(signal.reason),
                last_managed_candle=signal.stop_candle
                if manual
                else signal.confirmation_candle,
            )
            body = dict(
                product_id=spec.product_id,
                size=sizing.contracts,
                side="buy" if signal.signal == "LONG" else "sell",
                order_type="market_order",
                time_in_force="ioc",
                reduce_only=False,
            )
            try:
                obj = await self.submit_intent(
                    signal.client_id(), signal.signal_id, "entry", body, permit
                )
                p.order_id = str(obj["id"])
                p = await self.settle_entry(obj, p)
                if p:
                    self.risk.entry_recorded(now, self.equity)
                    self.db.event(
                        "LONG_ENTRY" if p.sign == 1 else "SHORT_ENTRY", **p.to_dict()
                    )
                    actual_loss = (
                        abs(p.entry - p.stop)
                        + (p.entry + p.stop)
                        * (
                            max(self.config.fee_rate, spec.taker_fee)
                            + self.config.slippage_bps / 10000
                        )
                    ) * p.size
                    if (
                        p.sign * (p.entry - p.stop) <= 0
                        or actual_loss > sizing.risk_budget + 1e-8
                    ):
                        self.risk.kill("FILL_RISK_TOO_HIGH")
                        await self.close_position(p, "FILL_RISK_TOO_HIGH")
                return p
            except Exception as exc:
                if isinstance(exc, DeltaError) and not isinstance(
                    exc, UnknownOrderOutcome
                ):
                    entry_intent = next(
                        (
                            o
                            for o in self.db.orders()
                            if o["client_id"] == signal.client_id()
                        ),
                        None,
                    )
                    if entry_intent and entry_intent["status"] == "rejected":
                        self.db.signal_status(signal.signal_id, "rejected")
                self.risk.kill("EXECUTION_EXCEPTION:" + type(exc).__name__)
                # If known filled exposure exists, try to protect it before returning control.
                known = self.db.positions().get(signal.symbol)
                if known:
                    try:
                        await self.protect(known)
                    except Exception:
                        try:
                            await self.close_position(known, "PROTECTION_FAILED")
                        except Exception:
                            self.db.event(
                                "MANUAL_ACTION_REQUIRED",
                                symbol=signal.symbol,
                                reason="Protection and emergency exit unavailable",
                            )
                raise

    async def trail(self, p, level):
        stop = favorable_stop(p, level, self.specs[p.symbol])
        if stop == p.stop:
            return
        await self.protect(p)
        # In-place amendment keeps the existing exchange stop until edit is accepted.
        try:
            obj = await self.client.edit_order(
                dict(
                    id=int(p.stop_order_id),
                    product_id=self.specs[p.symbol].product_id,
                    size=p.contracts,
                    stop_price=str(stop),
                ),
                self.permit("edit", p, stop),
            )
        except UnknownOrderOutcome:
            obj = await self.client.order(p.stop_order_id)
            if float(obj.get("stop_price", 0)) != stop:
                self.risk.kill("UNKNOWN_STOP_EDIT")
                raise
        if obj["state"] not in {"open", "pending"} or float(obj["stop_price"]) != stop:
            raise DeltaError("Stop amendment not confirmed")
        p.stop = stop
        self.db.save_position(p)
        self.db.event(
            "STOP_UPDATED", symbol=p.symbol, stop=stop, order_id=p.stop_order_id
        )

    async def close_position(self, p, reason):
        # Re-read size so a concurrently triggered stop cannot reverse the position.
        remote = await self.client.positions()
        row = next(
            (
                r
                for r in remote
                if int(r["product_id"]) == self.specs[p.symbol].product_id
                and int(r["size"])
            ),
            None,
        )
        if row is None:
            await self.reconcile()
            return
        if (1 if int(row["size"]) > 0 else -1) != p.sign:
            raise DeltaError("POSITION_DIRECTION_MISMATCH")
        p.contracts = abs(int(row["size"]))
        # Same exit intent reused after response loss. Partial terminal exits require reconciliation/manual action.
        cid = "ds" + hashlib.sha256((p.signal_id + ":exit").encode()).hexdigest()[:30]
        obj = await self.submit_intent(
            cid,
            p.signal_id,
            "exit",
            dict(
                product_id=self.specs[p.symbol].product_id,
                size=p.contracts,
                side="sell" if p.sign == 1 else "buy",
                order_type="market_order",
                time_in_force="ioc",
                reduce_only=True,
            ),
            self.permit("exit", p),
        )
        for _ in range(4):
            if obj["state"] in TERMINAL:
                break
            await asyncio.sleep(0.25)
            obj = await self.client.order(obj["id"])
        self.db.event(
            "EXIT_REQUESTED", symbol=p.symbol, reason=reason, order_id=obj["id"]
        )
        if obj["state"] not in TERMINAL or int(obj["unfilled_size"]) > 0:
            self.risk.kill("PARTIAL_OR_UNCERTAIN_EXIT")
        await self.reconcile()

    async def record_closed(self, p):
        fills = await self.client.paged(
            "/v2/fills",
            {
                "product_ids": self.specs[p.symbol].product_id,
                "start_time": p.entry_timestamp * 1_000_000,
            },
        )
        fills = [
            f for f in fills if int(f["product_id"]) == self.specs[p.symbol].product_id
        ]
        closing = [f for f in fills if f["side"] == ("sell" if p.sign == 1 else "buy")]
        opening = [f for f in fills if f["side"] == ("buy" if p.sign == 1 else "sell")]
        # Dedicated account assumption. Unexplained quantities halt accounting rather than guess.
        if (
            sum(int(f["size"]) for f in closing) != p.contracts
            or sum(int(f["size"]) for f in opening) != p.contracts
        ):
            raise DeltaError("FILL_ACCOUNTING_MISMATCH")
        price = sum(float(f["price"]) * int(f["size"]) for f in closing) / p.contracts
        fees = sum(float(f["commission"]) for f in fills)
        now = int(time.time())
        pnl = p.pnl(price) - fees
        trade = dict(
            p.to_dict(),
            exit=price,
            size=p.size,
            fees=fees,
            pnl=pnl,
            exit_timestamp=now,
            reason_for_exit="EXCHANGE_FLAT",
            status="closed",
            slippage=0,
        )
        with self.db.transaction():
            self.db.save_trade(trade)
            self.db.remove_position(p.symbol)
            self.risk.loss_recorded(now, pnl, self.equity)
            self.db.event("POSITION_CLOSED", **trade)

    async def reconcile(self):
        self.risk.reconciled = False
        remote = await self.client.positions()
        orders = await self.client.open_orders()
        nonzero = [p for p in remote if int(p["size"])]
        ids = {s.product_id: symbol for symbol, s in self.specs.items()}
        if any(int(p["product_id"]) not in ids for p in nonzero):
            self.risk.kill("UNMANAGED_EXCHANGE_POSITION")
            raise DeltaError(
                "Dedicated account contains unsupported or unconfigured positions"
            )
        self.equity, self.available = await self.client.account(nonzero)
        if (
            not math.isfinite(self.equity)
            or self.equity <= 0
            or not math.isfinite(self.available)
        ):
            raise DeltaError("ACCOUNT_EQUITY_UNKNOWN")
        self.risk.mark(time.time(), self.equity)
        cancelled_ids = set()
        local = self.db.positions()
        remote_symbols = {ids[int(r["product_id"])] for r in nonzero}
        # Resolve any durable network intents first; absence is UNKNOWN, never permission to resubmit.
        for intent in self.db.orders():
            if intent["status"] in {"submitting", "unknown"}:
                try:
                    obj = await self.client.order_by_client_id(intent["client_id"])
                    self.db.order(
                        intent["client_id"],
                        intent["signal_id"],
                        intent["kind"],
                        obj["state"],
                        obj,
                        obj["id"],
                    )
                except DeltaError:
                    self.risk.kill("UNRESOLVED_ORDER_INTENT")
        for r in nonzero:
            symbol = ids[int(r["product_id"])]
            size = abs(int(r["size"]))
            sign = 1 if int(r["size"]) > 0 else -1
            stops = [
                o
                for o in orders
                if int(o["product_id"]) == int(r["product_id"])
                and o.get("reduce_only") is True
                and o.get("stop_order_type") == "stop_loss_order"
                and o.get("order_type") == "market_order"
                and o.get("stop_trigger_method") == "last_traded_price"
                and o.get("side") == ("sell" if sign == 1 else "buy")
                and int(o["size"]) >= size
                and o.get("state") in {"open", "pending"}
                and float(o.get("stop_price") or 0) > 0
            ]
            p = local.get(symbol)
            if p is None:
                # Stop level may only come from an actual protective exchange order.
                level = float(stops[0]["stop_price"]) if stops else 0
                p = Position(
                    symbol,
                    "LONG" if sign == 1 else "SHORT",
                    float(r["entry_price"]),
                    size,
                    float(self.specs[symbol].contract_value),
                    level,
                    0,  # Unknown imported leverage; do not invent account settings.
                    int(time.time()),
                    "recovered:" + str(r["product_id"]),
                    status="recovered",
                    reason="Imported exchange exposure",
                )
                self.db.save_position(p)
                self.risk.kill("RECOVERED_POSITION_REQUIRES_REVIEW")
            elif (
                p.contracts != size
                or p.sign != sign
                or abs(p.entry - float(r["entry_price"]))
                > float(self.specs[symbol].tick_size)
            ):
                self.risk.kill("POSITION_STATE_MISMATCH")
                # Persist actual exposure, but do not invent accounting/stop changes.
                p.contracts = size
                p.direction = "LONG" if sign == 1 else "SHORT"
                p.entry = float(r["entry_price"])
                p.status = "recovered"
                self.db.save_position(p)
            if p.status == "recovered":
                self.risk.kill("RECOVERED_POSITION_REQUIRES_REVIEW")
            if not stops:
                self.risk.kill("MISSING_EXCHANGE_PROTECTION")
                if p.stop > 0 and p.status != "recovered":
                    p.stop_order_id = ""
                    await self.protect(p)
            else:
                chosen = max(stops, key=lambda o: sign * float(o["stop_price"]))
                level = float(chosen["stop_price"])
                if p.stop and sign * (level - p.stop) < -1e-9:
                    self.risk.kill("EXCHANGE_STOP_WIDENED")
                else:
                    p.stop = level
                p.stop_order_id = str(chosen["id"])
                self.db.save_position(p)
        for symbol, p in local.items():
            if symbol not in remote_symbols:
                # Cancel stale reduce-only protection only after the exchange is flat.
                if p.stop_order_id and any(
                    str(o["id"]) == p.stop_order_id for o in orders
                ):
                    await self.client.cancel_order(
                        p.stop_order_id,
                        self.specs[symbol].product_id,
                        self.permit("cancel", p),
                    )
                    cancelled_ids.add(p.stop_order_id)
                if p.status == "recovered":
                    self.db.remove_position(symbol)
                else:
                    await self.record_closed(p)
        known_stop_ids = {p.stop_order_id for p in self.db.positions().values()}
        for o in orders:
            if str(o["id"]) not in known_stop_ids | cancelled_ids and o.get(
                "state"
            ) in {"open", "pending"}:
                self.risk.kill("UNEXPECTED_OPEN_ORDER")
        reserved = self.db.conn.execute(
            "SELECT signal_id FROM signals WHERE status='reserved'"
        ).fetchall()
        if reserved:
            self.risk.kill("UNRESOLVED_SIGNAL_RESERVATION")
        self.risk.reconciled = not bool(self.risk.kill_reason)
        return self.risk.reconciled
