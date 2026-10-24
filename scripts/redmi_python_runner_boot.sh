#!/data/data/com.termux/files/usr/bin/sh
/data/data/com.termux/files/usr/bin/termux-wake-lock
exec /data/data/com.termux/files/usr/bin/python "$HOME/github-python-runner/runner.py" start
