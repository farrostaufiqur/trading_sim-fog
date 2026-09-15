"""SQLite storage for the trading simulator.

Single database file, single active simulation — there is exactly one
simulation row (id = 1), the whole app has one global, shared state that
every device reads the same way. That's the "single DB" rule (see CLAUDE.md
at the project root); it's about there only ever being one instance/one
active run, not about the HTTP server having one worker thread.

Each thread gets its own sqlite3 connection to the same file (the standard,
safe way to use SQLite from a multi-threaded server — a single connection
shared across real OS threads is not); WAL mode lets GET requests (state
polling, page loads) keep reading while a /api/tick calculation is mid-write
instead of queuing behind it.
"""
import os
import sqlite3
import threading

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
    last_direction INTEGER,                        -- 1 = buy (long), 0 = sell (short)
    stop_reason TEXT,                             -- null | 'epoch_limit' | 'manual' | 'done'

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

# Serializes the actual calculation (start/run_chunk/stop/reset) to one at a
# time, same as before — this is the "1 thread" that matters: only one
# /api/tick calculation ever runs at once, no matter how many HTTP worker
# threads waitress has. Plain reads (get_state, chart_points, ranked_stats)
# don't take this lock, so a page load or another viewer's poll is never
# stuck behind an in-progress chunk.
calc_lock = threading.Lock()

_local = threading.local()


def get_conn():
    conn = getattr(_local, 'conn', None)
    if conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.executescript(SCHEMA)
        conn.execute("INSERT OR IGNORE INTO simulation (id, status) VALUES (1, 'idle')")
        conn.commit()
        _local.conn = conn
    return conn
