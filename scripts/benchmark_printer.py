"""Deterministic, read-only Bambu LAN microbenchmark.

No network or real printer access. Performance thresholds are deliberately
not hard-coded until a hardware baseline is collected.
"""

from __future__ import annotations

import argparse
import json
import struct
import statistics
import time
from typing import Callable

from printer_service.printers.bambu_a1mini import decode_probe, merge_report
from printer_service.printers.lan_address import _route_is_local, private_ipv4

PAYLOAD = b'{"login":{"command":"detect","model":"N1"}}'
FRAME = bytes((165, 165)) + struct.pack("<H", len(PAYLOAD) + 6) + PAYLOAD + bytes((167, 167))
ROUTES = (
    "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n"
    "wlan0 001FA8C0 00000000 0001 0 0 0 00FFFFFF 0 0 0\n"
)


def measure(fn: Callable[[], object], *, iterations: int = 1000, rounds: int = 7) -> dict:
    """Report median and p95 round means in microseconds; warm up first."""
    if iterations < 1 or rounds < 2:
        raise ValueError("iterations >= 1 and rounds >= 2 required")
    for _ in range(min(iterations, 200)):
        fn()
    samples = []
    for _ in range(rounds):
        start = time.perf_counter_ns()
        for _ in range(iterations):
            fn()
        samples.append((time.perf_counter_ns() - start) / iterations / 1000)
    ordered = sorted(samples)
    return {
        "median_us": round(statistics.median(ordered), 3),
        "p95_us": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 3),
        "min_us": round(ordered[0], 3),
        "iterations": iterations,
        "rounds": rounds,
    }


def benchmarks() -> dict[str, Callable[[], object]]:
    return {
        "frame_decode": lambda: decode_probe(FRAME),
        "rfc1918_validation": lambda: private_ipv4("192.168.31.12"),
        "route_lookup": lambda: _route_is_local("192.168.31.12", "wlan0", ROUTES),
        "telemetry_merge": lambda: merge_report(
            {"print": {"gcode_state": "IDLE", "nozzle_temper": 21}},
            {"print": {"nozzle_temper": 22}},
        ),
    }


def run(iterations: int = 1000, rounds: int = 7) -> dict:
    return {
        "schema": "alice-printer-benchmark-v1",
        "unit": "microseconds",
        "mode": "mock-no-network",
        "results": {
            name: measure(fn, iterations=iterations, rounds=rounds)
            for name, fn in benchmarks().items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--rounds", type=int, default=7)
    args = parser.parse_args()
    print(json.dumps(run(args.iterations, args.rounds), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
