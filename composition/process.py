"""Process start-up shared by every composition root."""

from core.env import load_env_file
from observability import system_log


def start() -> None:
    """Load ``core/.env``, then configure the system log; call first in every root."""
    load_env_file()
    system_log.configure()
