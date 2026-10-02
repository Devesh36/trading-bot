"""Authenticated GET-only connectivity checks; no orders or local trading-state edits."""

import argparse
import asyncio
from datetime import datetime, timezone
from dataclasses import replace
import json
import math
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import Config
from data.market_data import history
from exchange.delta_client import DeltaClient, DeltaError
from strategy.swing_strategy import enrich, SwingStrategy


async def check(config):
    client = DeltaClient(config, read_only=True)
    results = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "configured_mode": config.trading_mode,
        "endpoint": config.base_url,
        "read_only": True,
        "checks": {},
    }
    checks = results["checks"]
    try:
        specs = {}
        for symbol in config.symbol_list:
            try:
                spec = await client.product(symbol)
                specs[symbol] = spec
                now = time.time()
                end = int(now) // 1800 * 1800
                frame = enrich(await history(client, symbol, end - 1000 * 1800, end))
                tick = await client.ticker(symbol)
                age = time.time() - float(tick["timestamp"]) / 1_000_000
                if not 0 <= age <= config.stale_seconds:
                    raise DeltaError("STALE_MARKET_DATA")
                signal = SwingStrategy().evaluate(frame, symbol, time.time())
                checks[symbol] = {
                    "status": "PASS",
                    "candles": len(frame),
                    "last_closed_candle": int(frame.iloc[-1].time),
                    "signal": signal.signal,
                    "ticker_age_seconds": round(age, 2),
                }
            except Exception as exc:
                checks[symbol] = {"status": "FAIL", **error_details(exc)}
        positions = None
        try:
            positions = await client.positions()
            checks["authenticated_positions"] = {
                "status": "PASS",
                "open_position_count": sum(int(p["size"]) != 0 for p in positions),
            }
        except Exception as exc:
            checks["authenticated_positions"] = {
                "status": "FAIL",
                **error_details(exc),
            }
        # Stop private checks on authentication failure; avoid repeatedly submitting bad credentials.
        if checks["authenticated_positions"].get("error") == "AUTHENTICATION_FAILED":
            checks["authenticated_orders"] = {
                "status": "SKIPPED",
                "reason": "Authentication failed",
            }
            checks["account_equity"] = {
                "status": "SKIPPED",
                "reason": "Authentication failed",
            }
        else:
            try:
                orders = await client.open_orders()
                checks["authenticated_orders"] = {
                    "status": "PASS",
                    "open_order_count": len(orders),
                }
            except Exception as exc:
                checks["authenticated_orders"] = {
                    "status": "FAIL",
                    **error_details(exc),
                }
            try:
                known_ids = {s.product_id for s in specs.values()}
                if positions is None or any(
                    int(p["size"]) and int(p["product_id"]) not in known_ids
                    for p in positions
                ):
                    raise DeltaError(
                        "Equity check requires known configured USD positions"
                    )
                equity, available = await client.account(positions)
                if not math.isfinite(equity) or not math.isfinite(available):
                    raise DeltaError("ACCOUNT_EQUITY_UNKNOWN")
                checks["account_equity"] = {
                    "status": "PASS",
                    "currency": "USD",
                    "positive_equity": equity > 0,
                    "positive_available_margin": available > 0,
                }
            except Exception as exc:
                checks["account_equity"] = {"status": "FAIL", **error_details(exc)}
        results["passed"] = all(v["status"] == "PASS" for v in checks.values())
        results["order_lifecycle"] = "NOT_TESTED_READ_ONLY"
        return results
    finally:
        await client.close()


def error_details(exc):
    # Never render raw transport exceptions, headers, request bodies, or credentials.
    details = {"error": str(exc) if isinstance(exc, DeltaError) else type(exc).__name__}
    if isinstance(exc, DeltaError):
        if exc.code:
            details["exchange_error_code"] = exc.code
        if exc.http_status:
            details["http_status"] = exc.http_status
    return details


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default=".env")
    parser.add_argument("--mode", choices=["production", "testnet"], default=None)
    parser.add_argument("--output", default="artifacts/exchange-check.json")
    args = parser.parse_args()
    config = Config.load(args.env)
    if args.mode:
        config = replace(
            config,
            trading_mode="testnet" if args.mode == "testnet" else "paper",
            enable_live_trading=False,
        )
    report = asyncio.run(check(config))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["passed"] else 1)
