"""Trading simulator — single-thread, single-DB, single active run.

Anyone who opens this page sees the same global state: an idle setup form,
a running simulation, or a finished one with its stats. There is no login
and no per-visitor session — the whole point is that only one simulation can
ever be active, and every device just reads it.
"""
from flask import Flask, jsonify, render_template, request

import db
import simulation


def create_app():
    app = Flask(__name__)

    @app.route('/')
    def index():
        return render_template('index.html')

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
        try:
            sim = simulation.start(conn, request.get_json(force=True))
        except (ValueError, KeyError, TypeError) as exc:
            return jsonify({'error': str(exc)}), 400
        return jsonify(dict(sim))

    @app.route('/api/tick', methods=['POST'])
    def api_tick():
        conn = db.get_conn()
        sim = simulation.run_chunk(conn)
        payload = dict(sim)
        payload['chart'] = simulation.chart_points(conn)
        if sim['status'] == 'finished':
            payload['stats'] = simulation.ranked_stats(conn)
        return jsonify(payload)

    @app.route('/api/stop', methods=['POST'])
    def api_stop():
        conn = db.get_conn()
        sim = simulation.stop(conn)
        return jsonify(dict(sim))

    @app.route('/api/reset', methods=['POST'])
    def api_reset():
        conn = db.get_conn()
        try:
            sim = simulation.reset(conn)
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        return jsonify(dict(sim))

    return app


app = create_app()

if __name__ == '__main__':
    app.run(debug=True, port=8500)
