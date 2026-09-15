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
    status TEXT NOT NULL DEFAULT 'idle',        -- idle | running | finished

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
    total_simulations INTEGER,                  -- N independent repeats
    max_epoch INTEGER,                           -- trade cap per repeat

    current_simulation INTEGER NOT NULL DEFAULT 0,  -- 1..total_simulations
    current_epoch INTEGER NOT NULL DEFAULT 0,        -- 0..max_epoch, within the current repeat
    balance REAL,
    pool REAL,                                    -- money staked in the current/most recent epoch
    best_balance REAL,                            -- best epoch balance within the current repeat
    worst_balance REAL,                           -- worst epoch balance within the current repeat
    last_is_profit INTEGER,
    stop_reason TEXT,                             -- null | 'depleted' | 'epoch_limit' | 'manual' | 'done'

    created_at TEXT,
    finished_at TEXT
);

-- One epoch-by-epoch balance point for the repeat currently in progress.
-- Cleared and restarted at the beginning of every repeat.
CREATE TABLE IF NOT EXISTS trade_point (
    trade_no INTEGER PRIMARY KEY,
    balance REAL NOT NULL,
    is_profit INTEGER NOT NULL
);

-- One row per completed repeat, used for the cross-simulation ranking.
CREATE TABLE IF NOT EXISTS simulation_run (
    run_no INTEGER PRIMARY KEY,
    final_balance REAL NOT NULL,
    epochs_used INTEGER NOT NULL,
    stop_reason TEXT NOT NULL
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
