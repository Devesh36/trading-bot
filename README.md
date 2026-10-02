# Delta India swing bot

An asynchronous Python 3.12+ bot for the specified 30-minute EMA200 / Supertrend(13,4) / MACD(12,26,9) strategy. Paper is the default. Signal generation is independent of order execution. Historical simulation and the running bot use the same strategy, contract conversion, risk manager, and paper broker.

**Implementation status:** paper operation and historical BTC/ETH simulations have been exercised. Authenticated India testnet account reads, startup reconciliation, and restart pass. A user-requested manual demo entry filled and its protective stop was verified; stop execution and the complete exit lifecycle remain unverified. Do not treat passing local tests as live readiness. See [validation](docs/VALIDATION.md) and [API verification](docs/API_VERIFICATION.md).

## Install and run

```bash
git clone https://github.com/Devesh36/trading-bot.git
cd trading-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
python main.py run --once
python main.py run
```

For the exact versions used during validation, install `requirements.lock.txt` instead. The workspace already has a virtual environment with dependencies installed. Copy the example only when creating a new `.env`; do not overwrite existing credentials. No credentials are required for paper mode.

```bash
python main.py status
python main.py backtest --symbol BTCUSD --days 365
python main.py backtest --symbol ETHUSD --days 365
python -m pytest -q
python scripts/chart_examples.py
```

On macOS or Linux, `./bot.sh status` and `./bot.sh run` use the project virtual environment. On Apple Silicon Macs the launcher explicitly selects ARM64 Python, including when the terminal runs under Rosetta. Install dependencies with native ARM64 Python on these Macs. An `incompatible architecture (have 'arm64', need 'x86_64')` NumPy error means the Python process was launched in Intel mode; use this launcher with the existing ARM64 environment.

To test your credentials without creating orders or changing trading state:

```bash
python scripts/check_exchange.py --mode testnet
# For production credentials, use --mode production instead.
```

This diagnostic client blocks every exchange mutation even if live flags are set.
It checks current candles/indicators, quotes, authenticated positions, open orders,
and USD equity availability, and writes a sanitized JSON report. It never prints
keys or secrets. Demo credentials belong in `DELTA_TESTNET_API_KEY` and
`DELTA_TESTNET_API_SECRET`, not the production fields. If Delta returns
`ip_not_whitelisted_for_api_key`, add the current public IP reported by Delta to
that demo API key's allowed IPs before retrying. These read-only checks do not
verify an actual entry/stop/exit lifecycle.

`BTC` and `ETH` are accepted aliases for the backtest command. `--once` completes one polling cycle and shuts down. Continuous operation is an explicit `run` command; no background daemon is installed automatically.

## Configuration

### Windows 11 without WSL

Install Python 3.12 for Windows, then open PowerShell in the cloned project folder:

```powershell
git pull --ff-only
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

Create the virtual environment on Windows; do not copy a Mac/Linux `.venv`.
No activation script or PowerShell execution-policy change is needed.
The example defaults to local paper mode. For Delta demo trading set
`TRADING_MODE=testnet`, keep `ENABLE_LIVE_TRADING=false`, and fill only
`DELTA_TESTNET_API_KEY` and `DELTA_TESTNET_API_SECRET`. Add the Windows machine's
public IP to the demo API whitelist if it differs.

When moving an existing demo account from another computer, stop its bot first
and privately copy its `.env` and `state` folder into this checkout after shutdown.
Preserve the SQLite database and candle history; do not run two computers against
the same account. The process lock coordinates only processes sharing one local
lock file, not separate computers.

Check demo credentials before starting demo trading:

```powershell
.\.venv\Scripts\python.exe scripts\check_exchange.py --mode testnet
```

After the checks pass, start the bot with `.\bot.cmd run`.
Use `.\bot.cmd status` in another PowerShell window to view its latest
saved status. Ctrl+C stops the foreground bot. Keep the computer awake and the
terminal open; this does not install an automatic startup service. The launcher
uses UTF-8 for output and the bot uses Windows file locking. Time-zone data is
installed with the dependencies.

Use `.env` (or `python main.py --env /path/to/env run`). Credentials are loaded only from that file and are never logged. Other configuration values can be overridden through process environment variables. See `.env.example` for every supported setting.

- `TRADING_MODE=paper`: production public market data with simulated orders and a separate SQLite database. Private order endpoints are blocked at the HTTP boundary.
- `TRADING_MODE=testnet`: India demo market data and private APIs; requires `DELTA_TESTNET_API_KEY` and `DELTA_TESTNET_API_SECRET`. Production keys are never used in this mode.
- `TRADING_MODE=live` **and** `ENABLE_LIVE_TRADING=true`: both are mandatory for production writes. Startup otherwise prints `LIVE TRADING DISABLED`. A live mode with the enable flag missing refuses startup.
- `SYMBOLS=BTCUSD,ETHUSD,ADAUSD,SOLUSD`: exact exchange product symbols. All four use the same strategy and share the portfolio risk limits (including at most two open positions). Instrument IDs and contract sizes are discovered from the chosen environment, never copied between environments. BNB is excluded because the India demo product catalog had no BNB contract when checked on 2026-10-02.
- Strategy parameters are fixed to the request. Unsupported changes fail configuration validation.
- Default leverage is 5, maximum 10. Any configuration above 10 is refused.
- Initial simulated balance is `PAPER_EQUITY=10000` USD. Existing persisted balances are never reset by changing this setting.
- Risk days and cooldowns use `RISK_TIMEZONE=Asia/Kolkata`; candle timestamps and chart labels use UTC.

Use an API key with only the permissions needed for trading and account reads, and the exchange's required IP allowlist. Use a dedicated account/subaccount without manual trading, other bots, unconfigured positions, or unrelated orders. Those changes intentionally halt this bot.

## Exact machine-readable strategy

Your selected interpretation is **either MACD line beyond zero**, rather than both. Let `a`, `b`, and `c` be consecutive 30-minute candles: previous, setup, and immediate confirmation. All inequalities are strict. `STdir` is +1 for green, −1 for red. `M` is MACD, `S` its signal line, and `H=M-S`.

```text
closed(x) := x.open_timestamp + 1800 <= evaluation_time
adjacent := b.time - a.time == 1800 AND c.time - b.time == 1800

bull(x) := close[x] > EMA200[x]
           AND STdir[x] == +1
           AND M[x] > S[x]
           AND H[x] > 0
           AND (M[x] > 0 OR S[x] > 0)

bear(x) := close[x] < EMA200[x]
           AND STdir[x] == -1
           AND M[x] < S[x]
           AND H[x] < 0
           AND (M[x] < 0 OR S[x] < 0)

LONG := adjacent AND closed(c)
        AND STdir[a] == -1 AND STdir[b] == +1
        AND bull(b) AND bull(c)
        AND close[c] > open[c] AND close[c] > high[b]

SHORT := adjacent AND closed(c)
         AND STdir[a] == +1 AND STdir[b] == -1
         AND bear(b) AND bear(c)
         AND close[c] < open[c] AND close[c] < low[b]

LONG_EXIT := closed(current) AND adjacent(previous, current)
             AND STdir[previous] == +1 AND STdir[current] == -1
SHORT_EXIT := closed(current) AND adjacent(previous, current)
              AND STdir[previous] == -1 AND STdir[current] == +1

LONG_STOP_HIT := traded_price <= active_stop
SHORT_STOP_HIT := traded_price >= active_stop
```

A setup starts only with a **fresh color flip**. If the next candle fails, the setup expires; a later breakout cannot revive it. Filters must hold on both setup and confirmation. Candle equality, zero histogram, and doji confirmations do not qualify. A fresh signal is executable only within `STALE_SECONDS=120` of its close and with a recent market quote. Restart does not replay old entries.

`SwingStrategy.evaluate()` returns a `Signal` containing LONG, SHORT, or NONE, timestamps, indicator values, and reasons. It never calls an exchange. `LONG`/`SHORT` are confirmed signals; logging names their event `SIGNAL_CONFIRMED`.

EMA uses an SMA seed followed by alpha=2/(period+1). MACD uses those EMA definitions, including an SMA-seeded 9-period signal EMA. Supertrend uses Wilder ATR, an arithmetic TR seed over 13 bars, recursive final bands using the previous close, and an initial bearish direction. The first true range is high−low. Numerical seeding can differ from a chart with a different historical starting point. Backtests fetch 1,000 warmup candles. The running bot persists its starting history so restarts do not silently re-seed it.

Volume, 20-bar volume average, relative volume, prior rolling 20-bar high/low, and current higher-high/lower-low flags are exposed by `enrich()` and `volume_context()`. These are descriptive rolling extrema, not centered swing pivots. They do not affect signals.

`USE_VOLUME_FILTER=false` and `ENABLE_PYRAMIDING=false` are required in V1. Enabling either raises a clear configuration error. Pyramiding settings are reserved; actual additions and an automated discretionary volume filter are deliberately unimplemented pending a reviewed deterministic specification. No averaging down occurs.

## Risk and contract conversion

Only linear, non-quanto USD-quoted/USD-settled perpetuals with underlying-unit contract values are supported. Inverse contracts, options, INR conversion, and other settlement currencies are refused. Current BTCUSD and ETHUSD specs were checked through public India endpoints.

```text
risk_budget = equity * RISK_PER_TRADE_PERCENT / 100
coin_quantity_before_costs = risk_budget / abs(entry - stop)
contracts_before_costs = coin_quantity_before_costs / contract_value

cost_adjusted_loss_per_contract = contract_value * (
  abs(entry - rounded_stop)
  + (entry + rounded_stop) * (fee_rate + slippage_bps / 10000)
)
contracts = floor_to_lot(min(risk_budget / cost_adjusted_loss_per_contract,
                            margin_and_fee_capacity,
                            instrument_position_limit))
```

The fee assumption is the greater of `FEE_RATE` and the current instrument taker fee. Stops round **toward** the entry: up to a price tick for LONG, down for SHORT. Contract quantities always round down. A nonpositive stop, wrong-side stop, subminimum lot, or estimated loss above the budget rejects the order.

The exchange order schema specifies integer contracts. V1 uses a minimum and step of one contract; the API product schema does not publish separate lot filters. Check acceptance for every additional instrument in testnet before enabling it. `ContractSpec` isolates this assumption and supports explicit minimum/step values.

Leverage affects margin capacity only; it never multiplies the risk budget. `MAX_POSITION_EQUITY_PERCENT=20` means **maximum equity allocated to initial margin per position**, with entry fees also reserved; it is not a 20% notional-exposure ceiling. Exchange initial-margin requirements can lower usable leverage. Dynamic margin scaling may cause the exchange to reject a trade; a rejection is not retried.

The independent `RiskManager` enforces daily marked-equity loss (3%), two open positions, six entries/day, and a 60-minute cooldown after a net losing trade. Daily loss is latched for the rest of that local calendar day even if equity recovers. Restart does not clear it. Open-position P&L contributes to the running daily equity check. Deposits/withdrawals are not normalized; avoid them while running.

Gap moves, liquidation, funding, taxes, and unbounded slippage can exceed an estimated stop loss. Position sizing does not guarantee a maximum realized loss.

## Orders, stops, and recovery

- SQLite reserves each `symbol:30m:direction:confirmation_timestamp` signal atomically. Client order IDs are deterministic 32-character hashes of the signal and order purpose.
- A durable order intent is committed before an exchange POST. Order creation, amendments, cancellation, and leverage changes are never blindly retried.
- A timeout/5xx is an **unknown outcome**, not a failed order. The bot looks up the client order ID. If still unresolved, it latches a halt, keeps the intent, and never submits it again. Delta's client IDs are unique among open orders; they are not assumed to provide permanent exactly-once execution. Local state is essential.
- Detected partial entry fills are protected before cancelling the remainder. The final filled quantity is re-read and the protective size amended if necessary. Unresolved/partial exits halt entries and retain reduce-only protection; they require review rather than recursively sending further market exits.
- Protection is a reduce-only stop-market order triggered by last traded price. Stops amend in place; the old stop is not cancelled before replacement. LONG stops only increase; SHORT stops only decrease.
- There is a short non-atomic window between an entry fill and protective-order acceptance. If protection fails, the bot attempts a reduce-only emergency close of known exposure. If exchange connectivity prevents both, manual action is required. Attached bracket-order behavior has not been assumed.
- Filled-price risk is checked again. If slippage breaches the reserved risk budget, the bot halts entries and requests a protective exit.
- Before starting or submitting an entry, exchange positions, open orders, balances, and local intents are reconciled. Unknown positions are imported. Missing protection, unexpected orders, unexplained size/direction changes, and unresolved intents halt new trading.
- An imported position with no verified stop is not assigned an invented stop. It is flagged for manual protection/review. Recovered positions remain entry blockers until their provenance and accounting are resolved.
- Flat exchange positions are booked from fill records. Unexplained quantities halt accounting instead of inventing P&L. Dedicated-account fills are assumed; recovered/manual partial trades need review.

A persistent kill latch blocks entries for API failures, state/account uncertainty, corrupt data, stale quotes, and unexpected execution exceptions. Existing exchange stops remain active. The bot continues trying to manage positions on later cycles where data and APIs are available. A kill latch never clears automatically on restart.

```bash
python main.py reconcile
python main.py reconcile --clear-halt --reason "Network restored; exchange orders reviewed"
```

The second command clears an operational halt only if current reconciliation succeeds. It cannot override the daily loss latch, unresolved signal/order reservations, imported exposure, or unexplained orders. Reconciliation can restore known missing protection; it creates no new entry positions. Do not delete a database to bypass a halt on an account with orders or exposure.

## Paper and backtest models

Paper mode uses real production candles and quotes with simulated equity, entry/exit fees, adverse slippage, contracts, stop fills, trailing stops, and realized/unrealized P&L. Paper stops exist locally only and cannot protect through a process outage. After restart, completed bars after entry are replayed for stop management; historical entries are not replayed. Quotes are polled every 15 seconds, so touches between polls can be missed until a completed bar is available. The partly elapsed entry bar cannot be perfectly reconstructed. Paper is not a tick-accurate execution simulator.

Backtests enter on the **next bar open** after confirmation. Supertrend exits also execute at the following open. A bar's stop check uses the level active before that bar; newly calculated trailing levels apply only to later bars. Stop gaps fill at the worse of the open and stop, plus adverse slippage. Open positions are explicitly liquidated at the final close under `END_OF_BACKTEST` for reporting. Future-bar prices do not participate in signal calculations.

Each run creates `candles.csv`, `contract_spec.json`, `signals.csv`, `trades.csv`, `equity_curve.csv`, and `metrics.json`. Metrics include total/long/short performance, win/loss counts, P&L, return, maximum marked-equity drawdown, profit factor, average wins/losses, expectancy, fees, slippage, and annualized daily-return Sharpe (365 days, zero risk-free rate). Undefined profit factor/Sharpe values are JSON `null`, not arbitrary infinities. Drawdown is sampled at 30-minute closes; intrabar drawdown can be worse.

To repeat a saved sample without fetching history or product metadata:

```bash
python main.py backtest --symbol BTCUSD --csv artifacts/backtest-BTCUSD/candles.csv --output artifacts/replay-BTCUSD
```

Keep the adjacent `contract_spec.json`; otherwise the current public product spec is fetched. CSV replay treats the first 1,000 bars as warmup. BTC and ETH reports are independent single-symbol simulations, not a combined portfolio backtest. Costs exclude funding, tax/GST, liquidation, variable spreads, market impact, and historic fee changes. Positive simulations do not establish profitability.

## Persistence, logs, dashboard, notifications

State defaults to `state/paper.db`, `state/testnet.db`, or `state/live.db`. A database is bound to its mode. Required tables are `trades`, `positions`, `orders`, `signals`, `daily_statistics`, and `bot_events`, with `metadata` for account state and cursors. Position/order payloads retain identifiers, quantities, timestamps, fees, entry/exit reasons, stops, and leverage. Related paper-state changes use transactions. WAL and synchronous FULL favor durability. SQLite integrity is checked at startup.

Back up SQLite using the SQLite backup API, or stop the bot before copying its database and WAL files. Preserve candle cache files under `state/candles/` alongside state. Do not edit cached candles during operation. A file lock prevents two local runner processes from using the same database. This is not a distributed lock across separate databases/machines/accounts.

`logs/bot.log` rotates at 5 MB with five backups. Events include symbol, signal, risk, size, price, stop, order ID, P&L, and sanitized errors where applicable. Private HTTP payloads and credentials are not logged. The Rich display shows prices, indicators, signals, open exposure/protection, daily P&L and limits, next close, and last API update. `status` shows a stored snapshot, not proof that a process is still running; inspect its update time.

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env` to enable startup/shutdown, entries, stop updates, exits, daily-loss, API-error, and kill-switch notifications. This strategy has no fixed take-profit order. Notification failures are logged without exposing the token and do not stop protective management. Delivery is best-effort, not an exactly-once messaging system.

## Continuous operation and troubleshooting

Use Python 3.12+ on Windows, macOS, or Linux; process locking uses `msvcrt` on Windows and `fcntl` on macOS/Linux. Keep the machine awake, its clock synchronized, and networking reliable. Run in a terminal or a user-managed service. Stop with Ctrl-C. Shutdown does not close exchange positions or cancel protection. The bot never auto-deploys or automatically enables live mode.

- `AUTHENTICATION_FAILED`: check mode-specific keys, account/API permissions, IP allowlist, and clock synchronization. One authentication failure is enough to halt entries.
- `STALE_MARKET_DATA`, `GAP_IN_CANDLES`, `HISTORY_INCOMPLETE`: resolve connectivity or missing data; missing candles are not fabricated. Check the cached history before resuming.
- `UNKNOWN_ORDER_OUTCOME` or `UNRESOLVED_ORDER_INTENT`: inspect the client ID in Delta and SQLite. Do not delete the reservation or retry the POST manually.
- `MISSING_EXCHANGE_PROTECTION`: inspect the actual position immediately. Known stops are restored where possible; unknown stops require your decision.
- `POSITION_STATE_MISMATCH`, `RECOVERED_POSITION_REQUIRES_REVIEW`, or `UNEXPECTED_OPEN_ORDER`: stop other trading activity on the dedicated account and resolve actual exposure/orders before retrying reconciliation.
- `ACCOUNT_EQUITY_UNKNOWN`: V1 requires a USD wallet and explicit position unrealized P&L. It does not guess INR/USD conversion or unsupported cross-collateral accounting.
- `DATABASE_CORRUPTED`: stop entries, preserve evidence/backups, and reconcile with exchange state before restoring any database.
- No signals: the strategy is intentionally selective. A color flip with no qualifying immediately following candle produces no trade.

## Project layout

```text
trading/
  main.py, config.py, runtime.py
  .env.example, requirements.txt, requirements.lock.txt, pyproject.toml
  exchange/        delta_client.py, models.py
  data/            market_data.py
  indicators/      ema.py, macd.py, supertrend.py, volume.py
  strategy/        swing_strategy.py, signal.py, volume_filter.py
  risk/            risk_manager.py, position_sizing.py
  execution/       order_manager.py, paper_broker.py, position_manager.py
  portfolio/       portfolio.py
  backtest/        engine.py, metrics.py
  storage/         database.py, models.py
  notifications/   telegram.py
  ui/              dashboard.py
  tests/           config, indicators, strategy, sizing, risk, data,
                   idempotency, execution, backtest tests
  scripts/         chart_examples.py
  docs/            API_VERIFICATION.md, VALIDATION.md
  artifacts/       backtests, logs from validation, chart-review examples
  state/           mode-specific SQLite and candle caches (local only)
```

## Before live trading

The remaining checks are authenticated India testnet entry/fill/protection/amendment/exit and restart behavior, real response-loss/partial-fill behavior, minimum lot acceptance, stop persistence during process termination, account equity/margin semantics for your account, and side-by-side TradingView chart agreement with matched feed/history/indicator initialization. The supplied testnet credentials now pass account reads; actual strategy-driven order execution is still required to finish these. Also inspect strategy behavior over a longer paper session and set fees/slippage for your actual account. Live mode must be enabled by you, explicitly, after reviewing these results.

## Explicit manual demo trades

`scripts/manual_demo_trade.py` is available only for an explicit user-requested
manual testnet entry. It refuses production mode and retains risk, margin,
daily-loss, cooldown, stale-quote, and duplicate checks. It uses the last closed
Supertrend level as protection and records `MANUAL_DEMO_REQUEST`, never a fabricated
strategy signal. Requested contracts must be whole numbers within the risk-sized
maximum. The normal bot must be paused first; both processes use the same lock.
A stable `--request-id` prevents re-running the request from opening another entry.
After execution, resume the normal bot to manage the recorded position. The normal
strategy never calls this manual-entry path.
