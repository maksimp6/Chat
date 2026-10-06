#!/usr/bin/env python3
"""Build root stacked-PR status from GitHub topology."""

from __future__ import annotations

import argparse
import json

from scripts.stacked_pr_topology import collect_github_nodes, root_status_payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--base", default="master")
    args = parser.parse_args()

    nodes = collect_github_nodes(args.repo)
    payload = root_status_payload(nodes, args.pr, args.base)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
