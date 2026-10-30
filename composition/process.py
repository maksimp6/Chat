"""Process start-up shared by every composition root."""

import faulthandler
import signal

from core.env import load_env_file
from observability import system_log


def start() -> None:
    """Load ``core/.env``, then configure the system log; call first in every root."""
    load_env_file()
    system_log.configure()
    # A process that stops answering can be asked for every thread's stack: kill -USR1 <pid>
    # writes it to stderr (backend.log on the phone) and the process keeps running.
    if hasattr(signal, "SIGUSR1"):
        faulthandler.register(signal.SIGUSR1, all_threads=True)
