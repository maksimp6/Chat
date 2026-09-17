"""Android bootstrap for the existing Flask application."""


def start_server():
    """Start the unchanged Alice Pro Flask app on the loopback interface."""
    from app import app

    app.run(host="127.0.0.1", port=5000, threaded=True, use_reloader=False)
