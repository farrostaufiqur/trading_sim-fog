"""Production WSGI entry point for the trading simulator.

Several worker threads so page loads and /api/state polls are never stuck
queued behind an in-progress /api/tick chunk — but the actual calculation
(start/run_chunk/stop/reset) is still serialized to one at a time via
db.calc_lock, so there is still only ever one computation advancing the one
simulation at once (see CLAUDE.md: "hanya gunakan 1 thread [proses hitung],
hanya gunakan 1 db"). "Display" (reads) and "calculation" (writes) are the
two threads the requirement actually cares about, not the HTTP server's
worker count.

Env vars:
    TRADINGSIM_HOST     bind address     (default: 0.0.0.0)
    TRADINGSIM_PORT     port number      (default: 8500)
    TRADINGSIM_THREADS  worker threads   (default: 4)
"""
import os
from waitress import serve

from app import app

if __name__ == '__main__':
    host = os.environ.get('TRADINGSIM_HOST', '0.0.0.0')
    port = int(os.environ.get('TRADINGSIM_PORT', 8500))
    threads = int(os.environ.get('TRADINGSIM_THREADS', 4))

    print(f'Starting Trading Simulator (Flask, production) on http://{host}:{port}  threads={threads}')
    serve(app, host=host, port=port, threads=threads, ident='tradingsim')
