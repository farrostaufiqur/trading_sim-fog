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

Per-epoch model. A real trade is either a buy (long) or a sell (short) —
never both at once — so each epoch first picks a direction, then stakes that
direction's percentage of the current balance into the pool. Spread is the
gap between the buy price and the sell price (the cost of a round trip),
charged every epoch regardless of direction. The pool closes back into
balance the same epoch it opened (win or lose):

    direction = 'buy' or 'sell', 50/50
    pool   = current_balance * (buy_pct if direction == 'buy' else sell_pct) / 100
    win    = random() < profit_pct / (profit_pct + loss_pct)        # 50/50 if both 0
    payout_pct = uniform(min, max) if range toggle else the fixed number
    change = +pool * payout_pct / 100   (win)
             -pool * payout_pct / 100   (loss)
    cost   = pool * spread_pct / 100    (always subtracted)
    balance = max(0, balance + change - cost)

Since buy_pct and sell_pct are each capped at 100 in the UI and are never
summed, a single epoch can never stake more than the full balance.

A repeat stops early ("depleted") if balance hits 0, otherwise it runs until
max_epoch is reached ("epoch_limit").
"""
import random
import sqlite3
import time

# A chunk is bounded by wall-clock time, not a fixed epoch count: with large
# total_simulations x max_epoch (e.g. 100,000 x 1,000 = up to 10^8 epochs),
# a fixed small chunk would mean hundreds of thousands of /api/tick round
# trips — that's what actually times out, not the computation itself (pure
# Python easily does 10^6+ simple epochs/sec). Budgeting each chunk to ~1s
# of wall time keeps the server responsive to other viewers/Stop while still
# needing only a couple hundred round trips for even the largest runs.
CHUNK_TIME_BUDGET = 1.0
CHUNK_CHECK_INTERVAL = 5000
CHUNK_MAX_STEPS = 5_000_000


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
            last_is_profit=NULL, last_direction=NULL, stop_reason=NULL,
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
    last_direction = sim['last_direction']
    pool = sim['pool'] or 0.0

    # Hoisted out of the loop — repeated sqlite3.Row string-key lookups are
    # the single biggest avoidable cost at millions of iterations.
    buy_frac = min(sim['buy_pct'], 100) / 100
    sell_frac = min(sim['sell_pct'], 100) / 100
    spread_frac = sim['spread_pct'] / 100
    profit_min, profit_max = sim['profit_min'], sim['profit_max']
    loss_min, loss_max = sim['loss_min'], sim['loss_max']
    profit_fixed = profit_min == profit_max
    loss_fixed = loss_min == loss_max
    uniform = random.uniform
    rand = random.random

    rows = []
    completed_runs = []  # (run_no, final_balance, epochs_used, stop_reason)
    overall_done = False

    start_time = time.monotonic()
    total_steps = 0
    since_check = 0

    while True:
        is_buy = rand() < 0.5
        pool = balance * (buy_frac if is_buy else sell_frac)
        win = rand() < win_prob
        if win:
            payout = profit_min if profit_fixed else uniform(profit_min, profit_max)
            change = pool * payout / 100
        else:
            payout = loss_min if loss_fixed else uniform(loss_min, loss_max)
            change = -pool * payout / 100
        cost = pool * spread_frac
        balance = balance + change - cost
        if balance < 0:
            balance = 0.0

        epoch += 1
        last_is_profit = 1 if win else 0
        last_direction = 1 if is_buy else 0
        if balance > best:
            best = balance
        if balance < worst:
            worst = balance
        rows.append((epoch, balance, last_is_profit))
        total_steps += 1
        since_check += 1

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
            # start the next repeat fresh, same starting balance — nothing
            # from the repeat that just ended needs to survive in the chart
            rows = []
            conn.execute("DELETE FROM trade_point")
            run_no += 1
            balance = balance_initial
            pool = 0.0
            epoch = 0
            best = balance_initial
            worst = balance_initial
            last_is_profit = None
            last_direction = None

        if since_check >= CHUNK_CHECK_INTERVAL:
            since_check = 0
            if total_steps >= CHUNK_MAX_STEPS or (time.monotonic() - start_time) >= CHUNK_TIME_BUDGET:
                break

    if rows:
        conn.executemany("INSERT INTO trade_point (trade_no, balance, is_profit) VALUES (?, ?, ?)", rows)

    if completed_runs:
        conn.executemany(
            "INSERT OR REPLACE INTO simulation_run (run_no, final_balance, epochs_used, stop_reason) VALUES (?, ?, ?, ?)",
            completed_runs,
        )

    if overall_done:
        conn.execute(
            """UPDATE simulation SET status='finished', current_simulation=?, current_epoch=?,
               balance=?, pool=?, best_balance=?, worst_balance=?, last_is_profit=?, last_direction=?,
               stop_reason='done', finished_at=? WHERE id=1""",
            (run_no, epoch, balance, pool, best, worst, last_is_profit, last_direction, time.strftime('%Y-%m-%d %H:%M:%S')),
        )
    else:
        conn.execute(
            """UPDATE simulation SET current_simulation=?, current_epoch=?, balance=?, pool=?,
               best_balance=?, worst_balance=?, last_is_profit=?, last_direction=? WHERE id=1""",
            (run_no, epoch, balance, pool, best, worst, last_is_profit, last_direction),
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
