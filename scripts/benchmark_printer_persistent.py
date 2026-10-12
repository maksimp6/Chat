"""Persistent PrinterSession performance harness.

Only request RTT is measured; TCP setup and teardown are excluded. An explicit
hardware flag is required. No automatic reconnect, heating, motion or printing.
"""
from __future__ import annotations

import argparse
import json
import math
import socket
import statistics
from typing import Any

from alice_pro.printers.lan_address import private_ipv4
from alice_pro.printers.persistent_session import PrinterSession


def percentile(samples: list[float], fraction: float) -> float:
    if not samples:
        raise ValueError("no samples")
    ordered = sorted(samples)
    return ordered[max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))]


def run_hardware(host: str, *, requests: int = 50, warmup: int = 5) -> dict[str, Any]:
    host = private_ipv4(host)
    if requests < 1 or not 0 <= warmup <= 100:
        raise ValueError("invalid sample count")

    def connector(ip: str, port: int, *, timeout: float) -> socket.socket:
        return socket.create_connection((ip, port), timeout=timeout)

    with PrinterSession(host, connector=connector) as session:
        for _ in range(warmup):
            session.detect()
        samples = []
        for _ in range(requests):
            identity, elapsed_ms = session.detect()
            if identity["model"] != "N1":
                raise ValueError("unexpected printer model")
            samples.append(elapsed_ms)
    return {
        "mode": "real-hardware-persistent-tcp",
        "sample_count": requests,
        "warmup_count": warmup,
        "tcp_connect_included": False,
        "median_ms": round(statistics.median(samples), 3),
        "p95_ms": round(percentile(samples, 0.95), 3),
        "min_ms": round(min(samples), 3),
        "max_ms": round(max(samples), 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hardware", action="store_true")
    parser.add_argument("--host", default="")
    parser.add_argument("--requests", type=int, default=50)
    parser.add_argument("--warmup", type=int, default=5)
    args = parser.parse_args()
    if not args.hardware:
        parser.error("explicit --hardware required")
    print(json.dumps(run_hardware(args.host, requests=args.requests, warmup=args.warmup), indent=2))


if __name__ == "__main__":
    main()
