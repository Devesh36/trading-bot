from datetime import datetime, timezone
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()


def render(config, db, rows=None, equity=None, risk=None, last_api=None):
    state = db.get("status", {})
    kill = risk.kill_reason if risk else db.get("kill_reason", "")
    banner = f"DELTA SWING BOT | MODE: {config.trading_mode.upper()}\nStatus: " + (
        "HALTED — " + kill if kill else state.get("state", "STOPPED")
    )
    console.print(Panel(banner, style="bold red" if kill else "bold cyan"))
    table = Table("Symbol", "Price", "EMA200", "Supertrend", "MACD", "Signal")
    for symbol, r in (rows or state.get("markets", {})).items():
        table.add_row(
            symbol,
            *(f"{r.get(k, 0):.4f}" for k in ("price", "ema200", "supertrend", "macd")),
            r.get("signal", "NONE"),
        )
    console.print(table)
    positions = Table(
        "Position", "Contracts", "Entry", "Stop", "Leverage", "Protection"
    )
    for p in db.positions().values():
        positions.add_row(
            f"{p.symbol} {p.direction}",
            str(p.contracts),
            f"{p.entry:.4f}",
            f"{p.stop:.4f}",
            str(p.leverage),
            p.stop_order_id or "MISSING",
        )
    console.print(positions)
    now = datetime.now(timezone.utc).timestamp()
    next_close = (int(now) // 1800 + 1) * 1800
    stats = (
        risk.daily(now, equity)
        if risk and equity and equity > 0
        else state.get("daily", {})
    )
    pnl = stats.get("last_equity", 0) - stats.get("start_equity", 0)
    console.print(
        f"Equity: {equity if equity is not None else state.get('equity', 'unknown')} USD | Today's P&L: {pnl:.2f} USD\n"
        f"Daily loss limit: {config.max_daily_loss_percent}% | Daily halt: {stats.get('loss_latched', False)}\n"
        f"Next candle: {datetime.fromtimestamp(next_close, timezone.utc).isoformat()}\nLast API update: {last_api or state.get('last_api', 'never')}"
    )
