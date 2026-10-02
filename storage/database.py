import json
import sqlite3
import time
import logging
from pathlib import Path
from contextlib import contextmanager
from storage.models import Position


class Database:
    def __init__(self, path, mode="paper"):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, isolation_level=None, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        if self.conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("DATABASE_CORRUPTED")
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS signals(signal_id TEXT PRIMARY KEY,status TEXT NOT NULL,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS positions(symbol TEXT PRIMARY KEY,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS orders(client_id TEXT PRIMARY KEY,signal_id TEXT,kind TEXT,status TEXT,delta_order_id TEXT,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY, symbol TEXT,direction TEXT,entry REAL,exit REAL,size REAL,contracts INTEGER,stop REAL,leverage REAL,fees REAL,pnl REAL,signal_id TEXT,entry_timestamp INTEGER,exit_timestamp INTEGER,status TEXT,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS daily_statistics(day TEXT PRIMARY KEY,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS bot_events(id INTEGER PRIMARY KEY,timestamp REAL,event TEXT,payload TEXT NOT NULL);
        """)
        previous = self.get("mode")
        if previous and previous != mode:
            raise RuntimeError("Database belongs to a different trading mode")
        self.set("mode", mode)

    @contextmanager
    def transaction(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    def close(self):
        self.conn.close()

    def get(self, key, default=None):
        r = self.conn.execute(
            "SELECT value FROM metadata WHERE key=?", (key,)
        ).fetchone()
        return json.loads(r[0]) if r else default

    def set(self, key, value):
        self.conn.execute(
            "INSERT OR REPLACE INTO metadata VALUES (?,?)", (key, json.dumps(value))
        )

    def seen(self, sid):
        return (
            self.conn.execute(
                "SELECT 1 FROM signals WHERE signal_id=?", (sid,)
            ).fetchone()
            is not None
        )

    def reserve_signal(self, signal):
        try:
            self.conn.execute(
                "INSERT INTO signals VALUES (?,?,?)",
                (signal.signal_id, "reserved", json.dumps(signal.to_dict())),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def signal_status(self, sid, status):
        self.conn.execute(
            "UPDATE signals SET status=? WHERE signal_id=?", (status, sid)
        )

    def positions(self):
        return {
            r["symbol"]: Position(**json.loads(r["payload"]))
            for r in self.conn.execute("SELECT * FROM positions")
        }

    def save_position(self, p):
        self.conn.execute(
            "INSERT OR REPLACE INTO positions VALUES (?,?)",
            (p.symbol, json.dumps(p.to_dict())),
        )

    def remove_position(self, symbol):
        self.conn.execute("DELETE FROM positions WHERE symbol=?", (symbol,))

    def order(self, cid, sid, kind, status, payload, exchange_id=""):
        self.conn.execute(
            "INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?)",
            (cid, sid, kind, status, str(exchange_id), json.dumps(payload)),
        )

    def orders(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM orders")]

    def day(self, day):
        r = self.conn.execute(
            "SELECT payload FROM daily_statistics WHERE day=?", (day,)
        ).fetchone()
        return json.loads(r[0]) if r else None

    def save_day(self, day, stats):
        self.conn.execute(
            "INSERT OR REPLACE INTO daily_statistics VALUES (?,?)",
            (day, json.dumps(stats)),
        )

    def save_trade(self, t):
        cols = [
            "symbol",
            "direction",
            "entry",
            "exit",
            "size",
            "contracts",
            "stop",
            "leverage",
            "fees",
            "pnl",
            "signal_id",
            "entry_timestamp",
            "exit_timestamp",
            "status",
        ]
        self.conn.execute(
            f"INSERT INTO trades ({','.join(cols)},payload) VALUES ({','.join('?' for _ in range(len(cols) + 1))})",
            [t.get(c) for c in cols] + [json.dumps(t)],
        )

    def trades(self):
        return [
            json.loads(r[0])
            for r in self.conn.execute("SELECT payload FROM trades ORDER BY id")
        ]

    def event(self, event, **data):
        self.conn.execute(
            "INSERT INTO bot_events(timestamp,event,payload) VALUES (?,?,?)",
            (time.time(), event, json.dumps(data, default=str)),
        )
        logging.getLogger("bot").info("%s %s", event, json.dumps(data, default=str))
