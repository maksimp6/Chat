# SPDX-License-Identifier: Apache-2.0
# Adapted from Wikifunctions Z21953@162115 and Z19201@283594.
# Changes: descriptive names, types, indentation and streaming string deduplication.
# Source, original code and license: third_party/wikifunctions/.
"""Pinned local Wikifunctions implementations used by Logos."""

from collections.abc import Iterable, Iterator


def clamp_integer(minimum: int, value: int, maximum: int) -> int:
    """Clamp an already-converted integer between the caller's ordered bounds."""
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value


def unique_strings(values: Iterable[str]) -> Iterator[str]:
    """Yield first occurrences in order, adapting Z13078/Z19201@283594.

    Keep the source's equality-based list algorithm, yielding each newly
    appended value so a caller can stop without consuming the whole input.
    """
    unique: list[str] = []
    for value in values:
        if value not in unique:
            unique.append(value)
            yield value
