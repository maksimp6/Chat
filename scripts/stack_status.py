#!/usr/bin/env python3
"""Resolve stacked-PR root and render its current topology."""

from __future__ import annotations

import argparse
import json
import subprocess

from scripts.stacked_pr_topology import (
    build_stack,
    nodes_from_github,
    render_mermaid,
    root_number_for,
)


def collect(repo: str, pr_number: int) -> dict[str, object]:
    completed = subprocess.run(
        ["gh", "api", "--paginate", f"/repos/{repo}/pulls?state=all&per_page=100"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    if not isinstance(payload, list):
        raise ValueError("invalid GitHub pull request payload")
    nodes = nodes_from_github(payload)
    root_number = root_number_for(nodes, pr_number)
    topology = build_stack(nodes, root_number)
    return {
        "root_pr": root_number,
        "mermaid": render_mermaid(topology),
        "descendants": [node.number for node in topology.descendants],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    result = collect(args.repo, args.pr)
    print(json.dumps(result, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
