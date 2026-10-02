"""Place an explicitly requested demo trade with normal risk limits and Supertrend protection.
The normal bot must be paused; this command acquires the same database lock.
"""

import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import Config
from runtime import Runner, ProcessLock, setup_logging
from execution.manual_demo import ManualDemoEntry


async def execute(config, args):
    if config.trading_mode != "testnet" or config.enable_live_trading:
        raise ValueError("MANUAL_ENTRY_TESTNET_ONLY")
    runner = Runner(config)
    try:
        await runner.startup()
        now = time.time()
        frame = await runner.load_frame(args.symbol, now)
        row = frame.iloc[-1]
        if int(row.time) != int(now) // 1800 * 1800 - 1800:
            raise ValueError("STALE_STOP_CANDLE")
        sign = 1 if args.side == "buy" else -1
        if int(row.st_direction) != sign:
            raise ValueError(
                "Supertrend does not provide a valid stop for the requested direction"
            )
        tick = await runner.client.ticker(args.symbol)
        price = float(tick["close"])
        request = ManualDemoEntry(
            args.symbol,
            "LONG" if sign == 1 else "SHORT",
            args.contracts,
            price,
            float(row.supertrend),
            int(row.time),
            args.request_id,
        )
        runner.db.event("MANUAL_DEMO_REQUEST", **request.to_dict())
        position = await runner.broker.enter(
            request, price, time.time(), float(tick["timestamp"]) / 1_000_000
        )
        if position is None:
            raise RuntimeError(
                "Manual demo entry was not filled or was rejected by risk validation; inspect bot events"
            )
        if not await runner.broker.reconcile():
            raise RuntimeError(
                "Post-entry reconciliation halted: " + runner.risk.kill_reason
            )
        position = runner.db.positions().get(args.symbol)
        report = {
            "mode": "testnet",
            "request_id": args.request_id,
            "status": "PROTECTED_OPEN" if position else "ALREADY_CLOSED",
            "position": position.to_dict() if position else None,
        }
        path = Path("artifacts") / ("manual-demo-" + args.request_id + ".json")
        path.write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        await runner.send_events()
    finally:
        await runner.client.close()
        runner.db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTCUSD")
    parser.add_argument("--side", choices=["buy", "sell"], required=True)
    parser.add_argument("--contracts", type=int, required=True)
    parser.add_argument("--request-id", required=True)
    args = parser.parse_args()
    # A stable request ID makes re-running this diagnostic unable to duplicate an entry.
    if not args.request_id.replace("-", "").isalnum():
        parser.error("request-id must be alphanumeric/hyphenated")
    config = Config.load()
    if config.trading_mode != "testnet":
        raise SystemExit("Set testnet mode explicitly; production is refused")
    setup_logging(config)
    with ProcessLock(config.db_path + ".lock"):
        asyncio.run(execute(config, args))
