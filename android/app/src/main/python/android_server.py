"""Android bootstrap for the existing Flask application.

The complete server source is intentionally not duplicated here. The release
packaging step must bundle the repository Python sources and invoke this
module before opening the WebView.
"""

def start_server():
    from app import app
    app.run(host="127.0.0.1", port=5000, threaded=True, use_reloader=False)
