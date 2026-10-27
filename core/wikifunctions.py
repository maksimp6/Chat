# SPDX-License-Identifier: Apache-2.0
# Adapted from Wikifunctions Z21953, revision 162115 (function Z21951).
# Changes: descriptive names, integer annotations and normalized indentation.
# Source, original code and license: third_party/wikifunctions/.
"""Pinned local Wikifunctions implementations used by Logos."""

import statistics
from collections.abc import Iterable


# WIKIFUNCTIONS Z21951 / Z21953@162115 — НЕ РЕДАКТИРОВАТЬ.
def clamp_integer(minimum: int, value: int, maximum: int) -> int:
    """Clamp an already-converted integer between the caller's ordered bounds."""
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value


# WIKIFUNCTIONS Z22583 / Z22587@168295 — НЕ РЕДАКТИРОВАТЬ.
def mean_float64_list(values: Iterable[int | float]) -> float:
    """Mean of numeric values using the pinned Wikifunctions Python implementation."""
    return statistics.mean(values)


# WIKIFUNCTIONS Z22588 / Z22589@168298 — НЕ РЕДАКТИРОВАТЬ.
def median_float64_list(values: Iterable[int | float]) -> float:
    """Median of numeric values using the pinned Wikifunctions Python implementation."""
    return statistics.median(values)


# WIKIFUNCTIONS Z10079 / Z10082@143513 — НЕ РЕДАКТИРОВАТЬ.
def trim_string(value: str) -> str:
    """Trim surrounding whitespace with the pinned Python implementation."""
    return value.strip()


# WIKIFUNCTIONS Z10047 / Z10050@207180 — НЕ РЕДАКТИРОВАТЬ.
def lowercase_string(value: str) -> str:
    """Lowercase a string with the pinned Python implementation."""
    return value.lower()
