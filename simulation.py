"""Trading simulation engine.

Runs in chunks inside a single Flask/waitress request-handling thread — never
a background thread, never a second connection. The browser calls /api/tick
back-to-back with no delay while a run is active, so the run completes "as
fast as possible" without blocking other visitors for more than one chunk at
a time (see CLAUDE.md: single thread, single DB, single active instance).

Structure: a run is `total_simulations` independent repeats. Every repeat
starts fresh from the same `balance_initial` and plays out up to `max_epoch`
epochs (trades) — stopping early only if it runs out of money. Once all
repeats are done, they're ranked against each other by final balance.

Per-epoch model — buy and sell both stake money into the pool, spread is the
buy/sell price gap charged on the round trip, and the pool closes back into
balance the same epoch (win or lose):

    pool   = current_balance * min(buy_pct + sell_pct, 100) / 100   # capped at 100% of balance
    win    = random() < profit_pct / (profit_pct + loss_pct)        # 50/50 if both 0
    payout_pct = uniform(min, max) if range toggle else the fixed number
    change = +pool * payout_pct / 100   (win)
             -pool * payout_pct / 100   (loss)
    cost   = pool * spread_pct / 100    (always subtracted)
    balance = max(0, balance + change - cost)

A repeat stops early ("depleted") if balance hits 0, otherwise it runs until
max_epoch is reached ("epoch_limit").
"""
import random
import sqlite3
import time

CHUNK_SIZE = 500


def _row_to_dict(row):
    return dict(row) if row else None


def get_state(conn):
    return _row_to_dict(conn.execute("SELECT * FROM simulation WHERE id = 1").fetchone())


def start(conn, cfg):
    sim = get_state(conn)
    if sim['status'] == 'running':
        raise ValueError('A simulation is already running')

    total_simulations = int(cfg['total_simulations'])
    if total_simulations <= 0:
        raise ValueError('Number of simulations must be positive')
    max_epoch = int(cfg['max_epoch'])
    if max_epoch <= 0:
        raise ValueError('Max epoch must be positive')

    def amount_pair(prefix):
        toggle = 1 if cfg.get(f'{prefix}_toggle') else 0
        if toggle:
            lo = float(cfg[f'{prefix}_min'])
            hi = float(cfg[f'{prefix}_max'])
            if lo > hi:
                lo, hi = hi, lo
        else:
            lo = hi = float(cfg[f'{prefix}_amount'])
        return toggle, lo, hi

    profit_toggle, profit_min, profit_max = amount_pair('profit')
    loss_toggle, loss_min, loss_max = amount_pair('loss')

    balance_initial = float(cfg['balance'])

    conn.execute("DELETE FROM trade_point")
    conn.execute("DELETE FROM simulation_run")
    conn.execute(
        """UPDATE simulation SET
            status='running',
            balance_initial=?, buy_pct=?, sell_pct=?, spread_pct=?,
            profit_pct=?, profit_toggle=?, profit_min=?, profit_max=?,
            loss_pct=?, loss_toggle=?, loss_min=?, loss_max=?,
            total_simulations=?, max_epoch=?,
            current_simulation=1, current_epoch=0,
            balance=?, pool=0, best_balance=?, worst_balance=?,
            last_is_profit=NULL, stop_reason=NULL,
            created_at=?, finished_at=NULL
           WHERE id=1""",
        (
            balance_initial, float(cfg['buy_pct']), float(cfg['sell_pct']), float(cfg['spread_pct']),
            float(cfg['profit_pct']), profit_toggle, profit_min, profit_max,
            float(cfg['loss_pct']), loss_toggle, loss_min, loss_max,
            total_simulations, max_epoch,
            balance_initial, balance_initial, balance_initial,
            time.strftime('%Y-%m-%d %H:%M:%S'),
        ),
    )
    conn.commit()
    return get_state(conn)


def run_chunk(conn):
    """Advance up to CHUNK_SIZE epochs, possibly rolling across several
    repeats if max_epoch is small. No-op if not running."""
    sim = get_state(conn)
    if sim['status'] != 'running':
        return sim

    win_prob = 0.5
    total_pct = sim['profit_pct'] + sim['loss_pct']
    if total_pct > 0:
        win_prob = sim['profit_pct'] / total_pct

    balance_initial = sim['balance_initial']
    balance = sim['balance']
    best = sim['best_balance']
    worst = sim['worst_balance']
    epoch = sim['current_epoch']
    max_epoch = sim['max_epoch']
    run_no = sim['current_simulation']
    total_runs = sim['total_simulations']
    last_is_profit = sim['last_is_profit']
    pool = sim['pool'] or 0.0

    rows = []
    completed_runs = []  # (run_no, final_balance, epochs_used, stop_reason)
    overall_done = False
    steps_budget = CHUNK_SIZE

    while steps_budget > 0:
        pool = balance * min(sim['buy_pct'] + sim['sell_pct'], 100) / 100
        win = random.random() < win_prob
        if win:
            payout = sim['profit_min'] if sim['profit_min'] == sim['profit_max'] else random.uniform(sim['profit_min'], sim['profit_max'])
            change = pool * payout / 100
        else:
            payout = sim['loss_min'] if sim['loss_min'] == sim['loss_max'] else random.uniform(sim['loss_min'], sim['loss_max'])
            change = -pool * payout / 100
        cost = pool * sim['spread_pct'] / 100
        balance = max(0.0, balance + change - cost)

        epoch += 1
        last_is_profit = 1 if win else 0
        best = max(best, balance)
        worst = min(worst, balance)
        rows.append((epoch, balance, last_is_profit))
        steps_budget -= 1

        run_stop_reason = None
        if balance <= 0:
            run_stop_reason = 'depleted'
        elif epoch >= max_epoch:
            run_stop_reason = 'epoch_limit'

        if run_stop_reason:
            completed_runs.append((run_no, balance, epoch, run_stop_reason))
            if run_no >= total_runs:
                overall_done = True
                break
            # start the next repeat fresh, same starting balance
            if rows:
                conn.executemany("INSERT INTO trade_point (trade_no, balance, is_profit) VALUES (?, ?, ?)", rows)
                rows = []
            conn.execute("DELETE FROM trade_point")
            run_no += 1
            balance = balance_initial
            pool = 0.0
            epoch = 0
            best = balance_initial
            worst = balance_initial
            last_is_profit = None

    if rows:
        conn.executemany("INSERT INTO trade_point (trade_no, balance, is_profit) VALUES (?, ?, ?)", rows)

    for run in completed_runs:
        conn.execute(
            "INSERT OR REPLACE INTO simulation_run (run_no, final_balance, epochs_used, stop_reason) VALUES (?, ?, ?, ?)",
            run,
        )

    if overall_done:
        conn.execute(
            """UPDATE simulation SET status='finished', current_simulation=?, current_epoch=?,
               balance=?, pool=?, best_balance=?, worst_balance=?, last_is_profit=?,
               stop_reason='done', finished_at=? WHERE id=1""",
            (run_no, epoch, balance, pool, best, worst, last_is_profit, time.strftime('%Y-%m-%d %H:%M:%S')),
        )
    else:
        conn.execute(
            """UPDATE simulation SET current_simulation=?, current_epoch=?, balance=?, pool=?,
               best_balance=?, worst_balance=?, last_is_profit=? WHERE id=1""",
            (run_no, epoch, balance, pool, best, worst, last_is_profit),
        )
    conn.commit()
    return get_state(conn)


def stop(conn):
    sim = get_state(conn)
    if sim['status'] != 'running':
        return sim
    conn.execute(
        "UPDATE simulation SET status='finished', stop_reason='manual', finished_at=? WHERE id=1",
        (time.strftime('%Y-%m-%d %H:%M:%S'),),
    )
    conn.commit()
    return get_state(conn)


def reset(conn):
    sim = get_state(conn)
    if sim['status'] == 'running':
        raise ValueError('Cannot reset while running')
    conn.execute("DELETE FROM trade_point")
    conn.execute("DELETE FROM simulation_run")
    conn.execute("UPDATE simulation SET status='idle' WHERE id=1")
    conn.commit()
    return get_state(conn)


def chart_points(conn, max_points=800):
    """Epoch-by-epoch balance curve for the repeat currently in progress
    (or the last one run, once finished)."""
    total = conn.execute("SELECT COUNT(*) c FROM trade_point").fetchone()['c']
    if total == 0:
        return []
    if total <= max_points:
        rows = conn.execute("SELECT trade_no, balance, is_profit FROM trade_point ORDER BY trade_no").fetchall()
        return [dict(r) for r in rows]
    step = total / max_points
    rows = []
    for i in range(max_points):
        trade_no = int(i * step) + 1
        row = conn.execute(
            "SELECT trade_no, balance, is_profit FROM trade_point WHERE trade_no >= ? ORDER BY trade_no LIMIT 1",
            (trade_no,),
        ).fetchone()
        if row:
            rows.append(dict(row))
    last = conn.execute("SELECT trade_no, balance, is_profit FROM trade_point ORDER BY trade_no DESC LIMIT 1").fetchone()
    if last and (not rows or rows[-1]['trade_no'] != last['trade_no']):
        rows.append(dict(last))
    return rows


def ranked_stats(conn):
    """Rank breakdown across all completed repeats' final balances, best to
    worst. Only meaningful once the whole run is finished — computed once
    rather than on every status poll while running."""
    total = conn.execute("SELECT COUNT(*) c FROM simulation_run").fetchone()['c']
    if total == 0:
        return None

    balance_initial = conn.execute("SELECT balance_initial FROM simulation WHERE id=1").fetchone()['balance_initial']

    def at_rank(rank):
        row = conn.execute(
            "SELECT run_no, final_balance FROM simulation_run ORDER BY final_balance DESC, run_no ASC LIMIT 1 OFFSET ?",
            (rank - 1,),
        ).fetchone()
        return {'rank': rank, 'balance': row['final_balance'], 'run_no': row['run_no']} if row else None

    best = at_rank(1)
    worst = at_rank(total)
    median = at_rank((total + 1) // 2)

    profit_boundary_row = conn.execute(
        "SELECT MIN(rn) rn FROM (SELECT ROW_NUMBER() OVER (ORDER BY final_balance DESC, run_no ASC) rn, final_balance FROM simulation_run) WHERE final_balance < ?",
        (balance_initial,),
    ).fetchone()
    loss_start_rank = profit_boundary_row['rn'] if profit_boundary_row and profit_boundary_row['rn'] else None

    profit_bottom = at_rank(loss_start_rank - 1) if loss_start_rank and loss_start_rank > 1 else None
    loss_top = at_rank(loss_start_rank) if loss_start_rank else None

    return {
        'total': total,
        'best': best,
        'profit_bottom': profit_bottom,
        'median': median,
        'loss_top': loss_top,
        'worst': worst,
        'balance_initial': balance_initial,
    }
