# Validation record — 2026-10-02

Local implementation is validated for paper/simulation use. Authenticated testnet lifecycle and live readiness are **not established**. No live orders were placed. No bot process has been left running.

## Executed checks

- Python 3.12.3, dependencies captured in `requirements.lock.txt`.
- `python -m pytest -q`: **49 passed**. Output: [final-tests.txt](../artifacts/final-tests.txt).
- Ruff undefined/unused-name checks passed; source formatting passed. Python source compiled successfully.
- Production public BTC/ETH product, candle, and ticker endpoints responded successfully.
- India testnet public BTC/ETH product endpoints responded successfully. Evidence: [testnet-public-check.json](../artifacts/testnet-public-check.json).
- Paper startup, a separate restart, status, and reconciliation completed with live writes disabled and 10,000 USD starting equity. No qualifying new signal occurred in these brief real-data smoke runs. Logs: [first startup](../artifacts/paper-first-run.log), [restart](../artifacts/paper-restart.log), [final run](../artifacts/paper-final-run.log), [reconciliation](../artifacts/paper-reconcile.log).
- Persisted open paper positions, stop changes, fee/P&L settlement, and loss cooldown were exercised through actual SQLite-backed tests, beyond the empty-position smoke runs.
- Stateful exchange tests exercised accepted orders with lost responses, lookup without re-posting, unresolved-intent halts, partial entry fills protected before residual cancellation, imported exposure, known stop reconciliation, and triggered-stop rejection.
- Daily loss remains latched after an equity rebound and database restart, then resets on the next configured calendar day. Operational kill latches also survive restarts.
- Indicator prefix invariance proves future appended candles cannot alter earlier values. A seeded random OHLC test executes real trades and checks that changing later prices leaves earlier trades and equity identical.
- Full saved BTC/ETH CSV replays produced exactly matching trade files, equity files, and metrics. Replays use saved contract snapshots.

## Historical results

Period: **2025-10-02 07:00 UTC through 2026-10-02 07:00 UTC**. Each symbol uses a separate 10,000 USD simulated account. Data contains 17,520 test candles plus 1,000 warmup candles. Both runs use the user-selected either-line zero confirmation. Risk 1%, leverage 5x, margin-allocation cap 20%, taker fee at least 0.05% per side, and adverse slippage 0.05% per fill.

### BTCUSD

- Trades: 36; wins 15; losses 21; win rate 41.67%.
- Net P&L: +1260.86 USD; return +12.61%; maximum close-sampled drawdown 6.24%.
- Profit factor 1.977; average win 170.06 USD; average loss -61.43 USD; expectancy 35.02 USD/trade; daily-return Sharpe 1.085.
- Fees 168.75 USD; estimated slippage 168.75 USD (already reflected in simulated fills).
- LONG: 12 trades, 33.33% wins, +1176.80 USD.
- SHORT: 24 trades, 45.83% wins, +84.06 USD.
- Artifacts: [metrics](../artifacts/backtest-BTCUSD/metrics.json), [trades](../artifacts/backtest-BTCUSD/trades.csv), [equity](../artifacts/backtest-BTCUSD/equity_curve.csv), [signals](../artifacts/backtest-BTCUSD/signals.csv).

### ETHUSD

- Trades: 26; wins 7; losses 19; win rate 26.92%.
- Net P&L: -749.74 USD; return -7.50%; maximum close-sampled drawdown 9.69%.
- Profit factor 0.384; average win 66.69 USD; average loss -64.03 USD; expectancy -28.84 USD/trade; daily-return Sharpe -1.815.
- Fees 82.27 USD; estimated slippage 82.27 USD (already reflected in simulated fills).
- LONG: 12 trades, 33.33% wins, -439.58 USD.
- SHORT: 14 trades, 21.43% wins, -310.17 USD.
- Artifacts: [metrics](../artifacts/backtest-ETHUSD/metrics.json), [trades](../artifacts/backtest-ETHUSD/trades.csv), [equity](../artifacts/backtest-ETHUSD/equity_curve.csv), [signals](../artifacts/backtest-ETHUSD/signals.csv).

These are cost-modeled historical simulations, not profitability claims. Funding, taxes/GST, liquidation, variable spread/impact, and historical fee changes are excluded. The figures are not a combined BTC/ETH portfolio result.

## Chart checks

Four charts were generated and visually inspected, and all formula checks passed in [boolean-audit.json](../artifacts/chart-review/boolean-audit.json). The listed time is the confirmation candle OPEN; its close/earliest signal availability is 30 minutes later.

- BTC LONG: 2025-10-08 17:30 UTC — [chart](../artifacts/chart-review/BTCUSD-LONG.png).
- BTC SHORT: 2025-10-14 03:00 UTC — [chart](../artifacts/chart-review/BTCUSD-SHORT.png).
- ETH LONG: 2025-10-06 11:00 UTC — [chart](../artifacts/chart-review/ETHUSD-LONG.png).
- ETH SHORT: 2025-11-13 15:30 UTC — [chart](../artifacts/chart-review/ETHUSD-SHORT.png).

Inspection confirmed EMA-side filtering, the setup color flip, immediate next-candle extreme break, body direction, and MACD/histogram direction. The ETH short example shows the requested either-line rule: the setup MACD is below zero while its signal line remains above. Subsequent candles displayed in review images are not available to signal generation. This is local chart verification; no TradingView session or supplied TradingView reference was available, so independent TradingView parity remains unverified.

## Remaining checks before live

1. Supply separate India testnet credentials in `.env` and exercise an actual signal-driven order, fill, stop placement, stop amendment, reversal exit/stop fill, and flat-state reconciliation.
2. Confirm actual private API field shapes, minimum lot acceptance, USD wallet equity, margin behavior, fill commissions, and cursor pagination for the account.
3. Exercise real testnet partial fills, delayed acknowledgements, network loss, and process termination between entry/protection. Confirm the exchange stop persists after stopping Python. Local stateful tests are not a substitute for this.
4. Compare the four chart examples and additional signals against TradingView using the same Delta India contract, timeframe, history, and indicator initialization.
5. Run an extended paper session. Review feed outages, midnight loss accounting, fills, and operational alerts. Telegram transport was implemented but no real message was sent because credentials were absent.
6. Review the non-atomic entry/protection window, polling limitations, unsupported recovered/manual partial-trade accounting, and excluded backtest costs. Keep pyramiding and volume filtering disabled.
7. Only after those checks, explicitly configure both live flags yourself. This implementation never enables live trading automatically.

## Credential follow-up — 2026-10-02

The expanded suite passed **55 tests**, including read-only authentication,
environment separation, mutation rejection, and safe error-code reporting. Fresh
production-data paper startup and a separate restart also passed.

The supplied keys were confirmed to belong to Delta India demo/testnet. They are
now in the dedicated testnet fields; production fields are empty. The saved
mode remains `paper` and live trading remains disabled. Tests used an explicit
per-command `TRADING_MODE=testnet` override.

The initial production request returned `invalid_api_key`. The first correctly
routed demo request then returned `ip_not_whitelisted_for_api_key`. After the user
allowed the reported IP, authenticated testnet checks **passed**:

- BTCUSD and ETHUSD: 1,000 fully closed 30-minute candles each, valid indicator
  calculations, and fresh quotes.
- Authenticated position reads: zero open positions.
- Authenticated order reads: zero open orders.
- USD account equity and available margin: successfully determined and positive.
- Actual testnet bot startup/reconciliation: passed.
- A separate testnet restart and status read: passed; no kill latch.

Both current strategy signals were `NONE`. No exchange orders, stop amendments,
leverage changes, or cancellations were sent. These checks verify account
connectivity and startup behavior, **not** actual entry/fill/stop/exit execution.
No dummy strategy signal was fabricated. No background bot process was left
running. Live readiness remains unestablished.

Evidence: [tests](../artifacts/credential-tests.txt),
[paper cycle](../artifacts/credential-paper-run.log),
[paper restart](../artifacts/credential-paper-restart.log),
[authenticated testnet checks](../artifacts/testnet-authenticated-check.json),
[testnet startup](../artifacts/testnet-bot-run.log),
[testnet restart](../artifacts/testnet-bot-restart.log), and
[testnet status](../artifacts/testnet-bot-status.log).

Repeat account checks safely with
`python scripts/check_exchange.py --mode testnet`.

## Continuous demo trading started — 2026-10-02 07:41 UTC

At the user's explicit request, `.env` is now configured with
`TRADING_MODE=testnet` and `ENABLE_LIVE_TRADING=false`. The existing strategy was
started as a detached local process and verified across consecutive polling
cycles. At launch the account equity was 756.00353253 USD, both symbol signals
were `NONE`, there were no open positions, and no kill latch was active.

Runtime process details are in `state/testnet-runner.json`; console output is in
`logs/testnet-console.log`. `python main.py status` reads its latest saved status.
The process continues evaluating closed candles and can submit qualifying demo
orders. This launch does not establish that a subsequent entry or exit has filled.
The machine and network must remain available for continuous operation.

## User-requested manual BTC demo buy — 2026-10-02

The user explicitly requested a small BTC buy and clarified the quantity as one
contract (0.001 BTC). This entry is labelled `MANUAL_DEMO_REQUEST`; it is not a
strategy confirmation, and normal signal conditions were not changed.

- Request ID: `btc-buy-20261002-001`.
- Entry order: `2174304100`, fully filled, one BTCUSD contract at 85,836 USD.
- Configured/confirmed leverage: 5x.
- Protective order: `2174304108`, pending/active stop-market sell, reduce-only,
  one contract, stop at 85,085.50 USD from the latest bullish Supertrend.
- The position was persisted in the testnet database and reconciled against the
  exchange. The continuous bot was resumed to manage its stop and strategy exit.
- Tests: **61 passed**. Manual entries are restricted to testnet, keep all normal
  risk/margin/daily-loss/duplicate checks, and cannot increase the requested size.

The initial leverage request was rejected before order creation. The adapter now
serializes whole-number leverage as `"5"` instead of `"5.0"`; the corrected request
was accepted and its response confirmed 5x before the entry was submitted. No buy
was blindly retried or duplicated.

Evidence: `artifacts/manual-demo-btc-buy-20261002-001.json` and
`artifacts/manual-demo-execution.log`. This verifies a real demo entry, full fill,
protective-stop placement, and restart reconciliation with open exposure. It does
not yet verify an actual stop fill, stop amendment, partial fill, or exit.
