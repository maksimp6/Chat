"""One explicit switch that lifts the built-in restrictions in Logos.

Off by default. Setting LOGOS_UNRESTRICTED=true turns off, for this process:

- tool and agent approval gates;
- protected-path and secret-file guards in the filesystem tools, including the
  project-directory boundary;
- the command guard in run_command;
- SSH command allowlist, shell-control and privileged-command checks;
- provider usage quotas and the requirement for a user identity;
- cloud budget blocks and the budget controller's limits.

Owner settings are not restrictions and still apply: the SSH enable and
read-only switches, the quota numbers, and the budget amounts.
"""

from __future__ import annotations

import logging

from core.env import env_get

logger = logging.getLogger("logos.restrictions")
_announced = False


def restrictions_lifted() -> bool:
    """True when LOGOS_UNRESTRICTED is set; logs a warning the first time it is."""
    global _announced
    lifted = env_get("LOGOS_UNRESTRICTED", "").strip().lower() in {"1", "true", "yes"}
    if lifted and not _announced:
        _announced = True
        logger.warning("LOGOS_UNRESTRICTED is set: built-in restrictions are not enforced")
    return lifted
