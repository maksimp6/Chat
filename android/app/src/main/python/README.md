# Python runtime payload

Place the complete Alice Pro Python source tree here during the Android packaging step.

The wrapper intentionally does not duplicate or fork the backend. `android_server.py` imports `app` from this directory and starts the existing Flask application.
