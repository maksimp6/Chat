"""Android bootstrap for the existing Flask application."""

import os
from typing import Optional


def start_server(api_key: Optional[str] = None):
    """Prepare Android-private paths and start the existing Flask app."""
    if api_key:
        os.environ["YANDEX_API_KEY"] = api_key

    home = os.environ["HOME"]
    local_repo = os.path.join(home, "repo")
    os.makedirs(local_repo, exist_ok=True)
    os.environ["ALICE_LOCAL_REPO_DIR"] = local_repo
    os.chdir(home)

    # Import only after the runtime environment is ready because config.py
    # validates Yandex credentials during import.
    from app import app

    app.run(host="127.0.0.1", port=5000, threaded=True, use_reloader=False)
