import argparse
import asyncio
from dataclasses import replace, asdict
import json
from pathlib import Path
from decimal import Decimal
from exchange.models import ContractSpec
import time
import pandas as pd
from config import Config
from exchange.delta_client import DeltaClient
from data.market_data import history, normalize
from backtest.engine import BacktestEngine
from runtime import Runner, ProcessLock, setup_logging
from storage.database import Database
from ui.dashboard import console, render


async def backtest(args, config):
    # Historical simulation always uses production PUBLIC data and a paper-only configuration.
    config = replace(config, trading_mode="paper", enable_live_trading=False)
    symbol = {"BTC": "BTCUSD", "ETH": "ETHUSD"}.get(
        args.symbol.upper(), args.symbol.upper()
    )
    client = DeltaClient(config)
    end = int(time.time()) // 1800 * 1800
    start = end - args.days * 86400
    output = Path(args.output or f"artifacts/backtest-{symbol}")
    try:
        spec_path = Path(args.csv).with_name("contract_spec.json") if args.csv else None
        if spec_path and spec_path.exists():
            raw = json.loads(spec_path.read_text())
            raw["contract_value"] = Decimal(raw["contract_value"])
            raw["tick_size"] = Decimal(raw["tick_size"])
            spec = ContractSpec(**raw)
            if spec.symbol != symbol:
                raise ValueError("CSV contract symbol mismatch")
        else:
            spec = await client.product(symbol)
        if args.csv:
            candles = normalize(pd.read_csv(args.csv).to_dict("records"), now=end)
            start = int(candles.iloc[0].time) + 1000 * 1800
        else:
            candles = await history(client, symbol, start - 1000 * 1800, end)
        output.mkdir(parents=True, exist_ok=True)
        candles.to_csv(output / "candles.csv", index=False)
        (output / "contract_spec.json").write_text(
            json.dumps(asdict(spec), default=str, indent=2)
        )
        metrics, _, _ = BacktestEngine(config).run(
            candles, spec, output, start_time=start
        )
        console.print_json(json.dumps(metrics))
        console.print(f"Artifacts: {output.resolve()}")
    finally:
        await client.close()


async def reconcile_command(config, args):
    runner = Runner(config)
    old_reason = runner.risk.kill_reason
    try:
        if args.clear_halt:
            runner.risk.kill_reason = ""
        await runner.startup()
        if args.clear_halt:
            if runner.risk.kill_reason or not runner.risk.reconciled:
                raise RuntimeError("Reconciliation did not pass; halt retained")
            runner.db.set("kill_reason", "")
            runner.db.event("HALT_CLEARED", previous=old_reason, reason=args.reason)
        console.print(
            "Reconciled"
            if runner.risk.reconciled
            else "HALTED: " + runner.risk.kill_reason
        )
    except Exception:
        if args.clear_halt and not runner.risk.kill_reason:
            runner.db.set("kill_reason", old_reason or "RECONCILIATION_FAILED")
        raise
    finally:
        await runner.client.close()
        runner.db.close()


def main():
    parser = argparse.ArgumentParser(
        description="Delta India 30-minute swing bot — paper by default"
    )
    parser.add_argument("--env", default=".env")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--once", action="store_true")
    sub.add_parser("status")
    rec = sub.add_parser("reconcile")
    rec.add_argument("--clear-halt", action="store_true")
    rec.add_argument("--reason", default="")
    b = sub.add_parser("backtest")
    b.add_argument("--symbol", default="BTCUSD")
    b.add_argument("--days", type=int, default=365)
    b.add_argument("--csv")
    b.add_argument("--output")
    args = parser.parse_args()
    config = Config.load(args.env)
    console.print(
        "LIVE TRADING ENABLED"
        if config.trading_mode == "live" and config.enable_live_trading
        else "LIVE TRADING DISABLED",
        style="bold red",
    )
    setup_logging(config)
    if args.command == "backtest":
        if args.days <= 0:
            parser.error("--days must be positive")
        asyncio.run(backtest(args, config))
    elif args.command == "status":
        db = Database(config.db_path, config.trading_mode)
        try:
            render(config, db)
        finally:
            db.close()
    elif args.command == "reconcile":
        if args.clear_halt and not args.reason:
            parser.error("--clear-halt requires --reason describing what was resolved")
        with ProcessLock(config.db_path + ".lock"):
            asyncio.run(reconcile_command(config, args))
    else:
        with ProcessLock(config.db_path + ".lock"):
            asyncio.run(Runner(config).run(args.once))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Stopped. Existing exchange stops were left active.")
    except Exception as exc:
        # No raw HTTP request objects or URLs are printed.
        console.print(
            f"BOT HALTED: {type(exc).__name__}: {str(exc)[:200]}", style="bold red"
        )
        raise SystemExit(1)
