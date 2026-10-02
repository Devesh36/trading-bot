import hashlib
import hmac
import httpx
import pytest
from config import Config
from exchange.delta_client import DeltaClient, DeltaError, UnknownOrderOutcome
from risk.risk_manager import RiskManager, Decision
from storage.database import Database
from storage.models import Position
from portfolio.portfolio import Portfolio
from execution.paper_broker import PaperBroker
from execution.order_manager import OrderManager
from execution.position_manager import favorable_stop
from strategy.signal import Signal
from tests.test_position_sizing import SPEC


@pytest.mark.asyncio
async def test_paper_cannot_write_or_authenticate():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"success": True, "result": []})

    client = DeltaClient(Config(), httpx.MockTransport(handler))
    with pytest.raises(DeltaError):
        await client.create_order({}, Decision(True))
    with pytest.raises(DeltaError):
        await client.positions()
    assert not calls
    await client.close()


@pytest.mark.asyncio
async def test_signature_and_no_write_retry():
    calls = []

    def handler(request):
        calls.append(request)
        payload = (
            "POST"
            + request.headers["timestamp"]
            + "/v2/orders"
            + request.content.decode()
        )
        expected = hmac.new(b"secret", payload.encode(), hashlib.sha256).hexdigest()
        assert request.headers["signature"] == expected
        raise httpx.ReadTimeout("lost response")

    client = DeltaClient(
        Config(
            trading_mode="testnet",
            delta_testnet_api_key="key",
            delta_testnet_api_secret="secret",
        ),
        httpx.MockTransport(handler),
    )
    with pytest.raises(UnknownOrderOutcome):
        await client.create_order({"size": 1}, Decision(True))
    assert len(calls) == 1
    await client.close()


def test_paper_restart_stop_and_pnl(tmp_path):
    config = Config(database_path=str(tmp_path / "paper.db"))
    db = Database(config.db_path)
    risk = RiskManager(config, db)
    risk.reconciled = True
    portfolio = Portfolio(db, 10000)
    broker = PaperBroker(config, db, risk, portfolio)
    signal = Signal(
        "LONG", "BTCUSD", confirmation_candle=0, entry_reference=60000, supertrend=59000
    )
    p = broker.enter(signal, SPEC, 60000, 1800, {"BTCUSD": 60000}, 1800)
    assert p and portfolio.cash < 10000
    assert broker.enter(signal, SPEC, 60000, 1800, {"BTCUSD": 60000}, 1800) is None
    assert not broker.trail(p, 58000, SPEC)
    assert broker.trail(p, 59500, SPEC)
    db.close()
    db = Database(config.db_path)
    p = db.positions()["BTCUSD"]
    assert p.stop == 59500
    risk = RiskManager(config, db)
    portfolio = Portfolio(db, 10000)
    broker = PaperBroker(config, db, risk, portfolio)
    t = broker.close_position(p, 59000, 3600, "PROTECTIVE_STOP", SPEC)
    assert abs(portfolio.cash - 10000 - t["pnl"]) < 1e-8
    assert not db.positions() and t["fees"] > 0 and t["slippage"] > 0


def test_short_trailing_never_widens():
    p = Position("BTCUSD", "SHORT", 60000, 1, 0.001, 61000, 5, 0, "test")
    assert favorable_stop(p, 62000, SPEC) == 61000
    assert favorable_stop(p, 60500.4, SPEC) == 60500


class SimulatedExchange:
    """Stateful contract test: accepted writes persist even when the response is lost."""

    def __init__(self):
        self.orders = {}
        self.created = 0
        self.remote = []
        self.lost = False
        self.cancelled = []

    async def create_order(self, body, permit):
        assert permit.approved
        self.created += 1
        obj = dict(
            body,
            id=self.created,
            state="pending" if body.get("stop_price") else "closed",
            unfilled_size=0,
            average_fill_price="60000",
        )
        self.orders[body["client_order_id"]] = obj
        if self.lost:
            self.lost = False
            raise UnknownOrderOutcome("lost")
        return obj.copy()

    async def order_by_client_id(self, cid):
        if cid not in self.orders:
            raise DeltaError("not found")
        return self.orders[cid].copy()

    async def order(self, oid):
        return next(o.copy() for o in self.orders.values() if str(o["id"]) == str(oid))

    async def cancel_order(self, oid, pid, permit):
        self.cancelled.append(oid)
        for o in self.orders.values():
            if o["id"] == oid:
                o["state"] = "cancelled"

    async def edit_order(self, body, permit):
        for o in self.orders.values():
            if o["id"] == body["id"]:
                o.update(body)
                return o.copy()

    async def positions(self):
        return self.remote

    async def open_orders(self):
        return [
            o.copy() for o in self.orders.values() if o["state"] in {"open", "pending"}
        ]

    async def account(self, positions):
        return 10000, 9000


def manager(tmp_path):
    c = Config(trading_mode="testnet")
    db = Database(str(tmp_path / "testnet.db"), "testnet")
    r = RiskManager(c, db)
    ex = SimulatedExchange()
    om = OrderManager(c, ex, db, r, {"BTCUSD": SPEC})
    return om, ex, db, r


@pytest.mark.asyncio
async def test_lost_response_looked_up_never_reposted(tmp_path):
    om, ex, db, r = manager(tmp_path)
    ex.lost = True
    obj = await om.submit_intent(
        "client", "signal", "entry", {"size": 1}, Decision(True)
    )
    obj2 = await om.submit_intent(
        "client", "signal", "entry", {"size": 1}, Decision(True)
    )
    assert obj["id"] == obj2["id"] and ex.created == 1


@pytest.mark.asyncio
async def test_partial_fill_protected_then_residual_cancelled(tmp_path):
    om, ex, db, r = manager(tmp_path)
    ex.orders["entry"] = dict(
        id=99, size=10, unfilled_size=6, state="open", average_fill_price="60000"
    )
    p = Position(
        "BTCUSD", "LONG", 60000, 10, 0.001, 59000, 5, 0, "signal", order_id="99"
    )
    filled = await om.settle_entry(ex.orders["entry"].copy(), p)
    assert filled.contracts == 4 and ex.cancelled == [99]
    stop = await ex.order(filled.stop_order_id)
    assert stop["size"] == 4 and stop["reduce_only"] is True


@pytest.mark.asyncio
async def test_import_unknown_position_halts_entries(tmp_path):
    om, ex, db, r = manager(tmp_path)
    ex.remote = [dict(product_id=27, size=7, entry_price="60000")]
    assert not await om.reconcile()
    assert db.positions()["BTCUSD"].contracts == 7
    assert r.kill_reason == "MISSING_EXCHANGE_PROTECTION"
    assert not r.reconciled


@pytest.mark.asyncio
async def test_unresolved_intent_survives_reconciliation(tmp_path):
    om, ex, db, r = manager(tmp_path)
    db.order("unknown", "signal", "entry", "unknown", {"size": 10})
    assert not await om.reconcile()
    assert ex.created == 0 and r.kill_reason == "UNRESOLVED_ORDER_INTENT"


@pytest.mark.asyncio
async def test_known_position_and_stop_reconcile(tmp_path):
    om, ex, db, r = manager(tmp_path)
    p = Position("BTCUSD", "LONG", 60000, 5, 0.001, 59000, 5, 0, "signal")
    db.save_position(p)
    await om.protect(p)
    ex.remote = [dict(product_id=27, size=5, entry_price="60000")]
    assert await om.reconcile()
    assert r.reconciled and not r.kill_reason


@pytest.mark.asyncio
async def test_protection_allowed_while_killed(tmp_path):
    om, ex, db, r = manager(tmp_path)
    r.kill("API_ERROR")
    p = Position("BTCUSD", "LONG", 60000, 5, 0.001, 59000, 5, 0, "signal")
    assert om.permit("protect", p, 59000).approved
    assert not r.validate_trade(
        kind="edit", position=p, contracts=5, reduce_only=True, stop=58000
    ).approved


@pytest.mark.asyncio
async def test_failed_lookup_never_retries_create(tmp_path):
    om, ex, db, r = manager(tmp_path)

    async def lost(body, permit):
        ex.created += 1
        raise UnknownOrderOutcome("no acknowledgement")

    ex.create_order = lost
    with pytest.raises(DeltaError):
        await om.submit_intent("lost", "signal", "entry", {"size": 1}, Decision(True))
    assert ex.created == 1 and r.kill_reason == "UNKNOWN_ORDER_OUTCOME"
    with pytest.raises(DeltaError):
        await om.submit_intent("lost", "signal", "entry", {"size": 1}, Decision(True))
    assert ex.created == 1


@pytest.mark.asyncio
async def test_account_failure_cannot_reconcile(tmp_path):
    om, ex, db, r = manager(tmp_path)

    async def fail(positions):
        raise DeltaError("ACCOUNT_EQUITY_UNKNOWN")

    ex.account = fail
    with pytest.raises(DeltaError):
        await om.reconcile()
    assert not r.reconciled


def test_gap_stop_uses_worse_open():
    from execution.position_manager import stop_fill_reference

    p = Position("BTCUSD", "LONG", 60000, 10, 0.001, 59000, 5, 0, "signal")
    assert stop_fill_reference(p, 58000) == 58000
    assert stop_fill_reference(p, 59500) == 59000


@pytest.mark.asyncio
async def test_get_signature_includes_exact_query():
    def handler(request):
        message = "GET" + request.headers["timestamp"] + request.url.raw_path.decode()
        assert (
            request.headers["signature"]
            == hmac.new(b"secret", message.encode(), hashlib.sha256).hexdigest()
        )
        return httpx.Response(200, json={"success": True, "result": []})

    c = Config(
        trading_mode="testnet",
        delta_testnet_api_key="key",
        delta_testnet_api_secret="secret",
    )
    client = DeltaClient(c, httpx.MockTransport(handler))
    await client.request(
        "GET", "/v2/orders", params={"states": "open,pending"}, private=True
    )
    await client.close()


@pytest.mark.asyncio
async def test_mismatched_order_response_is_rejected(tmp_path):
    om, ex, db, risk = manager(tmp_path)
    with pytest.raises(DeltaError, match="ORDER_IDENTITY_MISMATCH"):
        om.verify_response(
            {"id": 1, "client_order_id": "another", "state": "closed"}, "expected", {}
        )


@pytest.mark.asyncio
async def test_triggered_stop_is_not_treated_as_active(tmp_path):
    om, ex, db, risk = manager(tmp_path)
    p = Position("BTCUSD", "LONG", 60000, 5, 0.001, 59000, 5, 0, "signal")
    await om.protect(p)
    for order in ex.orders.values():
        order["state"] = "closed"
    with pytest.raises(DeltaError, match="PROTECTION_NOT_ACTIVE"):
        await om.protect(p)


@pytest.mark.asyncio
async def test_read_only_diagnostics_authenticate_without_enabling_live():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.headers["api-key"] == "production-key"
        return httpx.Response(200, json={"success": True, "result": []})

    config = Config(delta_api_key="production-key", delta_api_secret="secret")
    client = DeltaClient(config, httpx.MockTransport(handler), read_only=True)
    assert await client.positions() == []
    for method in ("POST", "PUT", "DELETE"):
        with pytest.raises(DeltaError, match="Read-only"):
            await client.request(
                method, "/v2/orders", private=True, permit=Decision(True)
            )
    assert len(calls) == 1
    assert config.trading_mode == "paper" and not config.enable_live_trading
    await client.close()


@pytest.mark.asyncio
async def test_testnet_read_only_diagnostics_use_separate_keys():
    def handler(request):
        assert request.headers["api-key"] == "testnet-key"
        assert request.url.host == "cdn-ind.testnet.deltaex.org"
        return httpx.Response(200, json={"success": True, "result": []})

    config = Config(
        trading_mode="testnet",
        delta_api_key="production-key",
        delta_api_secret="production-secret",
        delta_testnet_api_key="testnet-key",
        delta_testnet_api_secret="testnet-secret",
    )
    client = DeltaClient(config, httpx.MockTransport(handler), read_only=True)
    await client.positions()
    with pytest.raises(DeltaError, match="Read-only"):
        await client.create_order({}, Decision(True))
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,expected",
    [
        (
            {"code": "invalid_api_key", "context": {"secret": "must-not-leak"}},
            "invalid_api_key",
        ),
        ({"code": "ip_not_whitelisted_for_api_key"}, "ip_not_whitelisted_for_api_key"),
        ("UnauthorizedApiAccess", "UnauthorizedApiAccess"),
        ({"code": "must-not-leak"}, None),
    ],
)
async def test_auth_errors_preserve_safe_codes_only(error, expected):
    def handler(request):
        return httpx.Response(401, json={"success": False, "error": error})

    config = Config(delta_api_key="key", delta_api_secret="secret")
    client = DeltaClient(config, httpx.MockTransport(handler), read_only=True)
    with pytest.raises(DeltaError) as caught:
        await client.positions()
    assert str(caught.value) == "AUTHENTICATION_FAILED"
    assert caught.value.code == expected
    assert caught.value.http_status == 401
    assert "must-not-leak" not in repr(caught.value)
    await client.close()


@pytest.mark.asyncio
async def test_leverage_uses_canonical_integer_string():
    import json

    def handler(request):
        assert json.loads(request.content) == {"leverage": "5"}
        return httpx.Response(200, json={"success": True, "result": {"leverage": "5"}})

    config = Config(
        trading_mode="testnet",
        delta_testnet_api_key="key",
        delta_testnet_api_secret="secret",
    )
    client = DeltaClient(config, httpx.MockTransport(handler))
    await client.leverage(84, 5.0, Decision(True))
    await client.close()
