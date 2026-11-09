"""Safe helpers for passing data across a shell boundary.

Do not interpolate external values into shell source. Pass them as argv, env,
or stdin and let the receiving process interpret only the intended command.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterable


def join_argv(argv: Iterable[str]) -> str:
    """Return shell source that preserves each argv item literally."""
    values = [str(value) for value in argv]
    if not values:
        raise ValueError("argv must not be empty")
    return shlex.join(values)
