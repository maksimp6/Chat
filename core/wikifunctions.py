# SPDX-License-Identifier: Apache-2.0
# Adapted from Wikifunctions Z21953, revision 162115 (function Z21951).
# Changes: descriptive names, integer annotations and normalized indentation.
# Source, original code and license: third_party/wikifunctions/.
"""Pinned local Wikifunctions implementations used by Logos."""


# WIKIFUNCTIONS Z21951 / Z21953@162115 — НЕ РЕДАКТИРОВАТЬ.
def clamp_integer(minimum: int, value: int, maximum: int) -> int:
    """Clamp an already-converted integer between the caller's ordered bounds."""
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value
