"""SQLite storage for the trading simulator.

Single file, single writer, single thread by design (see CLAUDE.md at the
project root: "hanya gunakan 1 thread, hanya gunakan 1 db"). There is exactly
one simulation row (id = 1) — the whole app has one global, shared state that
every device reads the same way.
"""
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'tradingsim.db')

SCHEMA = """
CREATE TABLE IF NOT EXISTS simulation (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    status TEXT NOT NULL DEFAULT 'idle',        -- idle | running | finished | stopped

    balance_initial REAL,
    buy_pct REAL,
    sell_pct REAL,
    spread_pct REAL,
    profit_pct REAL,
    profit_toggle INTEGER,
    profit_min REAL,
    profit_max REAL,
    loss_pct REAL,
    loss_toggle INTEGER,
    loss_min REAL,
    loss_max REAL,
    total_trades INTEGER,

    current_trade INTEGER NOT NULL DEFAULT 0,
    balance REAL,
    best_balance REAL,
    worst_balance REAL,
    last_is_profit INTEGER,
    stop_reason TEXT,                            -- null | 'depleted' | 'manual' | 'done'

    created_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS trade_point (
    trade_no INTEGER PRIMARY KEY,
    balance REAL NOT NULL,
    is_profit INTEGER NOT NULL
);
"""

_conn = None


def get_conn():
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        _conn.execute("INSERT OR IGNORE INTO simulation (id, status) VALUES (1, 'idle')")
        _conn.commit()
    return _conn
