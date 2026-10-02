# Delta India API verification — 2026-10-02

Primary sources checked before implementation:

- [Official REST and WebSocket documentation](https://docs.delta.exchange/)
- [Official Python client](https://github.com/delta-exchange/python-rest-client/blob/master/delta_rest_client/delta_rest_client.py)
- [Official order-type guide](https://guides.delta.exchange/delta-exchange-india-user-guide/trading-guide/order-types)

Production REST is `https://api.india.delta.exchange`; India testnet REST is `https://cdn-ind.testnet.deltaex.org`. Public WebSocket endpoints are `wss://public-socket.india.delta.exchange` and `wss://socket-ind-pub.testnet.deltaex.org`; private endpoints are `wss://socket.india.delta.exchange` and `wss://socket-ind.testnet.deltaex.org`. V1 uses asynchronous REST polling. No legacy WebSocket authentication/channel implementation is included. Closed candles are obtained through REST, not assumed closed from streaming updates.

Implemented contracts (all paths include `/v2`):

- Public product lookup: `GET /products/{symbol}`; runtime product ID, contract value, tick, state, trading status, initial margin, position limit, taker fee, quote/settlement/underlying currencies.
- Market history: `GET /history/candles`, `resolution=30m`, symbol, start/end epoch seconds. Documentation limits responses to 2,000 candles; windows here contain at most 1,000.
- Quotes: `GET /tickers/{symbol}`; `close` is the last trade quote, and `timestamp` is microseconds. Both schemas were exercised against BTCUSD/ETHUSD production public endpoints.
- Accounts: `GET /wallet/balances`; require explicit USD balance/available balance and add position `unrealized_pnl`. No arbitrary use of aggregate `meta.net_equity` across currencies, and no `/profile` call: API-key access to that endpoint was removed in the documented August 2026 change.
- Positions: `GET /positions/margined`; signed contract size encodes direction. Only configured linear USD perpetuals are supported.
- Active orders: paginated `GET /orders`, `states=open,pending`.
- Fills: paginated `GET /fills`, product IDs, start timestamp in microseconds. Fill quantity, execution price, and commission support closed-position accounting.
- Order creation: `POST /orders`; integer size, product ID, market type, side, IOC entries/exits, deterministic client ID, and Boolean reduce-only for exits/protection.
- Stop protection: market stop-loss, explicit stop price, last-traded-price trigger, reduce-only. Existing protection is edited through `PUT /orders`, never cancelled first to trail it.
- Cancellation: `DELETE /orders` with order/product ID.
- Order reads: `GET /orders/{id}` and `GET /orders/client_order_id/{client_oid}`.
- Leverage: `POST /products/{product_id}/orders/leverage`.

Authentication is HMAC-SHA256 over method + timestamp + path + exact encoded query (including `?`) + exact JSON body. The same bytes are signed and sent. Headers include API key, timestamp, signature, content type, and user agent. Read requests have bounded transient retries; authenticated writes do not retry.

The August 31, 2026 client-ID clarification only promises uniqueness across open orders. The bot therefore makes no claim of server-side permanent idempotency. Locally persisted reservations and durable intents remain necessary after orders close. A missing lookup after a timed-out submission never authorizes a new POST.

Unverified with an authenticated account: actual IOC/partial-fill lifecycle, stop amendment acceptance/trigger behavior, private pagination behavior, account-specific USD equity/margin accounting, and order lookup retention. The lot minimum/step is represented explicitly in `ContractSpec`; the current product schema exposes no separate lot fields and the order schema uses integer size. One contract minimum/step is used for supported linear products and must be acceptance-tested for additional instruments. Product IDs differ by environment.

Public observations at implementation time: production BTCUSD product ID 27, contract value 0.001 BTC and price tick 0.5 USD. These observations are not hardcoded into production lookup. Full snapshots for both checked products are in each backtest directory's `contract_spec.json`.

No production order, leverage, cancellation, or other private mutation was called during development. No private testnet request was called without testnet credentials.

The India testnet public endpoint was also exercised successfully. BTCUSD resolved to product ID 84, value 0.001 BTC, tick 0.1 USD; ETHUSD resolved to ID 1699, value 0.01 ETH, tick 0.05 USD. These differ from production and are fetched independently. Evidence: `artifacts/testnet-public-check.json`.

Authenticated follow-up: after correcting demo-key routing and the API IP allowlist,
`GET /positions/margined`, paginated `GET /orders`, and `GET /wallet/balances`
passed on India testnet. Current USD equity and available margin were determined
successfully. Testnet startup/reconciliation and a separate restart also passed.
No testnet mutations were sent because no current strategy signal qualified;
actual order, stop, partial-fill, and exit behavior remain unverified. Evidence:
`artifacts/testnet-authenticated-check.json` and `artifacts/testnet-bot-*.log`.
