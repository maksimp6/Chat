"""Logos web application: the composition root of the web process.

Everything the application serves is assembled by ``composition.web.install``;
``python app.py`` runs it on HOST:PORT (127.0.0.1:5000 by default).
"""

from composition import process, wiring

# Before any unit is imported: load core/.env, configure the system log and register
# every unit's host so ports resolve whenever a unit first needs them.
process.start()
wiring.register_all()

from flask import Flask

from composition import web
from core.env import env_get

app = Flask(__name__)
web.install(app)

if __name__ == "__main__":
    host = env_get("HOST", "127.0.0.1")
    port = int(env_get("PORT", "5000"))
    debug = env_get("FLASK_DEBUG", "0") == "1"
    # Each request on its own thread: a long chat turn (minutes of provider polling)
    # must not block other requests, or they are dropped (the single-threaded default
    # severed long connections). The store is already thread-safe — the cache runs a
    # background embedding thread against it alongside request handling.
    app.run(host=host, port=port, debug=debug, use_reloader=False, threaded=True)
