"""Trading simulator — single-thread, single-DB, single active run.

Anyone who opens this page sees the same global state: an idle setup form,
a running simulation, or a finished one with its stats. There is no login
and no per-visitor session — the whole point is that only one simulation can
ever be active, and every device just reads it.
"""
import time

from flask import Flask, jsonify, render_template, request

import db
import simulation

# Bumped once per process start (i.e. every deploy, since the service is
# restarted after each change). Appended to our own static asset URLs as
# ?v=... so a stale browser cache can never serve mismatched JS/CSS against
# a newer page again — a plain reload wasn't enough to bust a long-lived
# Cache-Control, but a changed URL always forces a real fetch.
ASSET_VERSION = str(int(time.time()))


def create_app():
    app = Flask(__name__)
    # This app is under active development — a long browser cache on
    # static/ (Flask's default) has already caused a stale app.js to send
    # field names the backend no longer recognizes. Keep it uncached.
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

    @app.route('/')
    def index():
        return render_template('index.html', asset_version=ASSET_VERSION)

    @app.route('/api/state')
    def api_state():
        conn = db.get_conn()
        sim = simulation.get_state(conn)
        payload = dict(sim)
        payload['chart'] = simulation.chart_points(conn)
        if sim['status'] == 'finished':
            payload['stats'] = simulation.ranked_stats(conn)
        else:
            payload['stats'] = None
        return jsonify(payload)

    @app.route('/api/start', methods=['POST'])
    def api_start():
        conn = db.get_conn()
        with db.calc_lock:
            try:
                sim = simulation.start(conn, request.get_json(force=True))
            except (ValueError, KeyError, TypeError) as exc:
                return jsonify({'error': str(exc)}), 400
        return jsonify(dict(sim))

    @app.route('/api/tick', methods=['POST'])
    def api_tick():
        conn = db.get_conn()
        with db.calc_lock:
            sim = simulation.run_chunk(conn)
            payload = dict(sim)
            payload['chart'] = simulation.chart_points(conn)
            if sim['status'] == 'finished':
                payload['stats'] = simulation.ranked_stats(conn)
        return jsonify(payload)

    @app.route('/api/stop', methods=['POST'])
    def api_stop():
        conn = db.get_conn()
        with db.calc_lock:
            sim = simulation.stop(conn)
        return jsonify(dict(sim))

    @app.route('/api/reset', methods=['POST'])
    def api_reset():
        conn = db.get_conn()
        with db.calc_lock:
            try:
                sim = simulation.reset(conn)
            except ValueError as exc:
                return jsonify({'error': str(exc)}), 400
        return jsonify(dict(sim))

    return app


app = create_app()

if __name__ == '__main__':
    app.run(debug=True, port=8500)
