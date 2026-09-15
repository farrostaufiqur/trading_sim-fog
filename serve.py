"""Production WSGI entry point for the trading simulator.

Deliberately single-threaded (threads=1): the whole app relies on exactly
one worker touching the one SQLite connection at a time — see CLAUDE.md
("hanya gunakan 1 thread, hanya gunakan 1 db").

Env vars:
    TRADINGSIM_HOST    bind address    (default: 0.0.0.0)
    TRADINGSIM_PORT    port number     (default: 8500)
"""
import os
from waitress import serve

from app import app

if __name__ == '__main__':
    host = os.environ.get('TRADINGSIM_HOST', '0.0.0.0')
    port = int(os.environ.get('TRADINGSIM_PORT', 8500))

    print(f'Starting Trading Simulator (Flask, production) on http://{host}:{port}  threads=1')
    serve(app, host=host, port=port, threads=1, ident='tradingsim')
