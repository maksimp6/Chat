#!/usr/bin/env python3
"""Refresh the Cloud.ru documentation mirror in docs/mirrors/cloudru/.

  python scripts/cloudru_docs_mirror.py                 # first-wave services
  python scripts/cloudru_docs_mirror.py --wave all      # every page under /docs
  python scripts/cloudru_docs_mirror.py --service pipeline --service s3e

Deterministic: no LLM is called. Unchanged pages keep their files; pages that
disappear are marked "missing" in manifest.json, never deleted.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from docs_mirror import CLOUDRU_FIRST_WAVE, Mirror, requests_fetcher  # noqa: E402

HOST = "cloud.ru"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--wave", choices=["first", "all"], default="first")
    parser.add_argument("--service", action="append", default=[], help="doc slug, e.g. pipeline")
    parser.add_argument("--out", default=str(ROOT / "docs" / "mirrors" / "cloudru"))
    parser.add_argument("--max-pages", type=int, default=20000)
    parser.add_argument("--delay", type=float, default=0.5, help="seconds between requests")
    args = parser.parse_args(argv)

    if args.service:
        slugs = args.service
    elif args.wave == "first":
        slugs = list(CLOUDRU_FIRST_WAVE)
    else:
        slugs = []

    if slugs:
        prefixes = [f"/docs/{slug}" for slug in slugs]
        seeds = [f"https://{HOST}/docs/{slug}/ug/doc-contents" for slug in slugs]
        seeds += [f"https://{HOST}/docs/{slug}/ug/index" for slug in slugs]
    else:
        prefixes = ["/docs"]
        seeds = [f"https://{HOST}/docs"]

    mirror = Mirror(
        Path(args.out),
        host=HOST,
        prefixes=prefixes,
        fetcher=requests_fetcher(),
        delay_s=args.delay,
    )
    stats = mirror.crawl(seeds, max_pages=args.max_pages)
    errors = [e for e in stats.events if e["result"] == "error"]
    print(
        json.dumps(
            {"summary": stats.as_dict(), "errors": errors[:50]}, ensure_ascii=False, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
