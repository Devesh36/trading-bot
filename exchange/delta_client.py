"""Verified India REST boundary. No write is retried, including timeouts/5xx.

All broker writes require a RiskManager-issued permit. Paper cannot reach writes.
See docs/API_VERIFICATION.md for supported schemas and outstanding validation.
"""

import asyncio
import hashlib
import hmac
import json
import time
from decimal import Decimal
import re
from urllib.parse import urlencode, quote
import httpx
from exchange.models import ContractSpec


class DeltaError(RuntimeError):
    def __init__(self, message, *, code=None, http_status=None):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


class UnknownOrderOutcome(DeltaError):
    pass


class DeltaClient:
    def __init__(self, config, transport=None, *, read_only=False):
        self.config = config
        # Explicit diagnostics may authenticate production GETs while paper mode
        # stays enabled. They cannot mutate the account, even with a risk permit.
        self.read_only = read_only
        self.http = httpx.AsyncClient(
            base_url=config.base_url,
            timeout=15,
            transport=transport,
            headers={
                "User-Agent": "delta-swing-bot/1.0",
                "Content-Type": "application/json",
            },
        )
        self.last_success = 0.0

    async def close(self):
        await self.http.aclose()

    async def request(
        self, method, path, params=None, body=None, private=False, permit=None
    ):
        writing = method != "GET"
        if writing:
            if self.read_only:
                raise DeltaError("Read-only client cannot mutate the exchange")
            if self.config.trading_mode == "paper":
                raise DeltaError("Paper mode cannot call a private write endpoint")
            if (
                self.config.trading_mode == "live"
                and not self.config.enable_live_trading
            ):
                raise DeltaError("LIVE TRADING DISABLED")
            if permit is None or not permit.approved:
                raise DeltaError(
                    "Risk approval required before every exchange mutation"
                )
        query = "?" + urlencode(params) if params else ""
        payload = json.dumps(body, separators=(",", ":")) if body is not None else ""
        for attempt in range(3 if not writing else 1):
            headers = {}
            if private:
                key, secret = (
                    (self.config.delta_api_key, self.config.delta_api_secret)
                    if self.read_only and self.config.trading_mode == "paper"
                    else self.config.credentials
                )
                if not key or not secret:
                    raise DeltaError("Missing mode-specific .env credentials")
                stamp = str(int(time.time()))
                signature = hmac.new(
                    secret.encode(),
                    (method + stamp + path + query + payload).encode(),
                    hashlib.sha256,
                ).hexdigest()
                headers = {"api-key": key, "timestamp": stamp, "signature": signature}
            try:
                r = await self.http.request(
                    method,
                    path + query,
                    content=payload.encode() if payload else None,
                    headers=headers,
                )
                if r.status_code == 429 or r.status_code >= 500:
                    if writing:
                        raise UnknownOrderOutcome(
                            f"Unknown exchange mutation outcome: HTTP {r.status_code}"
                        )
                    if attempt < 2:
                        await asyncio.sleep(min(5, 2**attempt))
                        continue
                if r.status_code in (401, 403):
                    # Keep only documented error identifiers. Never expose an
                    # arbitrary server message/context that could echo a secret.
                    code = None
                    try:
                        error = r.json().get("error")
                        candidate = (
                            error.get("code") if isinstance(error, dict) else error
                        )
                        if candidate in {
                            "invalid_api_key",
                            "invalid_signature",
                            "ip_not_whitelisted_for_api_key",
                            "ip_not_whitelisted",
                            "SignatureExpired",
                            "UnauthorizedApiAccess",
                            "signature_expired",
                            "request_expired",
                            "api_key_not_found",
                        }:
                            code = candidate
                    except (ValueError, AttributeError, TypeError):
                        pass
                    raise DeltaError(
                        "AUTHENTICATION_FAILED", code=code, http_status=r.status_code
                    )
                if r.status_code >= 400:
                    # Never include response bodies, request URLs or auth headers in errors.
                    code = None
                    try:
                        error = r.json().get("error")
                        candidate = (
                            error.get("code") if isinstance(error, dict) else error
                        )
                        credentials = (
                            self.config.delta_api_key,
                            self.config.delta_api_secret,
                            self.config.delta_testnet_api_key,
                            self.config.delta_testnet_api_secret,
                        )
                        if (
                            isinstance(candidate, str)
                            and re.fullmatch(r"[A-Za-z_]{1,80}", candidate)
                            and not any(
                                secret and secret in candidate for secret in credentials
                            )
                        ):
                            code = candidate
                    except (ValueError, AttributeError, TypeError):
                        pass
                    raise DeltaError(
                        f"Delta HTTP {r.status_code}" + (f": {code}" if code else ""),
                        code=code,
                        http_status=r.status_code,
                    )
                obj = r.json()
                if obj.get("success") is not True:
                    code = str(obj.get("error", {}).get("code", "unknown"))
                    raise DeltaError(f"Delta rejected request: {code[:80]}")
                self.last_success = time.time()
                return obj
            except (httpx.TransportError, ValueError) as exc:
                if writing:
                    raise UnknownOrderOutcome(
                        "Unknown exchange mutation outcome; reconcile before retry"
                    ) from None
                if attempt == 2:
                    raise DeltaError(
                        f"Delta read failed: {type(exc).__name__}"
                    ) from None
                await asyncio.sleep(2**attempt)
        raise DeltaError("API_UNAVAILABLE")

    async def product(self, symbol):
        return ContractSpec.from_api(
            (await self.request("GET", f"/v2/products/{quote(symbol, safe='')}"))[
                "result"
            ]
        )

    async def candles(self, symbol, start, end):
        return (
            await self.request(
                "GET",
                "/v2/history/candles",
                params={
                    "resolution": "30m",
                    "symbol": symbol,
                    "start": int(start),
                    "end": int(end),
                },
            )
        )["result"]

    async def ticker(self, symbol):
        return (await self.request("GET", f"/v2/tickers/{quote(symbol, safe='')}"))[
            "result"
        ]

    async def paged(self, path, params=None):
        output = []
        after = None
        seen = set()
        for _ in range(1000):
            query = dict(params or {}, page_size=100)
            if after:
                query["after"] = after
            obj = await self.request("GET", path, params=query, private=True)
            output.extend(obj["result"])
            after = obj.get("meta", {}).get("after")
            if not after:
                return output
            if after in seen:
                raise DeltaError("Repeated pagination cursor")
            seen.add(after)
        raise DeltaError("Pagination limit exceeded")

    async def positions(self):
        return (await self.request("GET", "/v2/positions/margined", private=True))[
            "result"
        ]

    async def open_orders(self):
        return await self.paged("/v2/orders", {"states": "open,pending"})

    async def account(self, positions):
        obj = await self.request("GET", "/v2/wallet/balances", private=True)
        wallets = [w for w in obj["result"] if w["asset_symbol"] == "USD"]
        if len(wallets) != 1:
            raise DeltaError(
                "ACCOUNT_EQUITY_UNKNOWN: a USD wallet is required; INR conversion is unsupported"
            )
        w = wallets[0]
        # Sum marked UPNL ourselves from verified linear USD product/positions at caller.
        if any("unrealized_pnl" not in p for p in positions if int(p["size"])):
            raise DeltaError("ACCOUNT_EQUITY_UNKNOWN: position unrealized_pnl missing")
        equity = float(w["balance"]) + sum(
            float(p["unrealized_pnl"]) for p in positions if int(p["size"])
        )
        return equity, float(w["available_balance"])

    async def order(self, order_id):
        return (await self.request("GET", f"/v2/orders/{order_id}", private=True))[
            "result"
        ]

    async def order_by_client_id(self, cid):
        return (
            await self.request(
                "GET", f"/v2/orders/client_order_id/{quote(cid, safe='')}", private=True
            )
        )["result"]

    async def create_order(self, body, permit):
        return (
            await self.request(
                "POST", "/v2/orders", body=body, private=True, permit=permit
            )
        )["result"]

    async def edit_order(self, body, permit):
        return (
            await self.request(
                "PUT", "/v2/orders", body=body, private=True, permit=permit
            )
        )["result"]

    async def cancel_order(self, order_id, product_id, permit):
        return (
            await self.request(
                "DELETE",
                "/v2/orders",
                body={"id": int(order_id), "product_id": product_id},
                private=True,
                permit=permit,
            )
        )["result"]

    async def leverage(self, product_id, value, permit):
        result = await self.request(
            "POST",
            f"/v2/products/{product_id}/orders/leverage",
            body={"leverage": format(Decimal(str(value)).normalize(), "f")},
            private=True,
            permit=permit,
        )
        if Decimal(str(result["result"]["leverage"])) != Decimal(str(value)):
            raise DeltaError("LEVERAGE_NOT_CONFIRMED")
        return result
