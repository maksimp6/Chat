#!/usr/bin/env python3
"""RAG pipeline for the Logos runner (stage 1: BM25 retrieval, verbatim-only answers).

Contract (aligned with scripts/lexical_pipeline.py):
- Corpus = GitHub issues with label `rag-source` (chunks of real dialogues).
- Honest pagination: per_page=100, page=1..N until empty.
- Retrieval: BM25 over sentence units (deterministic, no paid calls).
- Fail-closed: every answer fragment is verified verbatim against the source
  corpus; any non-verbatim fragment => report is NOT published, exit 1.
- Report counters: sources, units, fragments, verbatim_all.

Modes:
  --mode index  : build index, print stats
  --mode query  : search; publish comment when --publish-issue is given
  --selftest    : embedded controls (positive, RED, verbatim, fail-closed)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import urllib.request
from collections import Counter

REPO = os.environ.get("GITHUB_REPOSITORY", "maksimp6/Chat")
API = f"https://api.github.com/repos/{REPO}"
LABEL_SOURCE = "rag-source"
TOP_K = 5
PER_ISSUE_CAP = 3
STOP = set(
    (
        "и в не на что это я ты он она а как то же по из за но мы у с о для есть был быть или от до "
        "the a an of is are to it and or"
    ).split()
)


def api_get(path, token=None):
    req = urllib.request.Request(
        API + path, headers={"Accept": "application/vnd.github+json"}
    )
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def api_post(path, payload, token):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        API + path,
        data=data,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def fetch_corpus(token=None):
    issues, page = [], 1
    while True:
        data = api_get(
            f"/issues?labels={LABEL_SOURCE}&state=open&per_page=100&page={page}", token
        )
        if not data:
            break
        issues.extend(data)
        page += 1
        if page > 50:
            raise RuntimeError("pagination runaway")
    out = []
    for it in issues:
        if "pull_request" in it:
            continue
        body = it.get("body") or ""
        parts = body.split("\n---\n", 1)
        text = parts[1] if len(parts) > 1 else body
        out.append(
            {
                "issue": it["number"],
                "title": it["title"],
                "text": text,
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
        )
    return out


def split_sentences(text):
    return [
        m.group().strip()
        for m in re.finditer(r"[^.!?\u2026\n]+[.!?…]*", text)
        if len(m.group().strip()) > 25
    ]


def tokenize(s):
    return [
        w
        for w in re.findall(r"[а-яёa-z0-9]+", s.lower())
        if w not in STOP and len(w) > 2
    ]


def build_units(corpus):
    units = []
    for c in corpus:
        for s in split_sentences(c["text"]):
            units.append(
                {
                    "issue": c["issue"],
                    "sha256": c["sha256"],
                    "text": s,
                    "tokens": tokenize(s),
                }
            )
    return units


class BM25Index:
    def __init__(self, units):
        self.units = units
        self.N = len(units)
        self.avgdl = sum(len(u["tokens"]) for u in units) / max(self.N, 1)
        self.df = Counter()
        for u in units:
            self.df.update(set(u["tokens"]))
        self.k1, self.b = 1.5, 0.75

    def _idf(self, t):
        df = self.df.get(t, 0)
        return math.log(1 + (self.N - df + 0.5) / (df + 0.5))

    def search(self, query, top=TOP_K):
        qt = tokenize(query)
        results = []
        for u in self.units:
            tc = Counter(u["tokens"])
            score = 0.0
            for t in qt:
                f = tc.get(t, 0)
                if not f:
                    continue
                dl = len(u["tokens"])
                score += (
                    self._idf(t)
                    * f
                    * (self.k1 + 1)
                    / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
                )
            if score > 0:
                results.append(
                    {
                        "score": round(score, 3),
                        "issue": u["issue"],
                        "sha256": u["sha256"],
                        "text": u["text"],
                    }
                )
        results.sort(key=lambda r: -r["score"])
        capped, seen = [], Counter()
        for r in results:
            if seen[r["issue"]] >= PER_ISSUE_CAP:
                continue
            capped.append(r)
            seen[r["issue"]] += 1
            if len(capped) >= top:
                break
        return capped


def verify_verbatim(fragments, corpus):
    by_issue = {c["issue"]: c["text"] for c in corpus}
    return [
        {**f, "verbatim": f["text"] in by_issue.get(f["issue"], "")} for f in fragments
    ]


def report(query, fragments, units_n, sources_n):
    lines = [
        "<!-- rag-report -->",
        "## RAG-отчёт (stage 1: BM25)",
        "",
        f"- sources (issues): {sources_n}",
        f"- units (предложения): {units_n}",
        f"- query: `{query}`",
        "",
    ]
    if not fragments:
        lines.append(
            "**PASS: релевантных фрагментов не найдено — ответ не выдумывается.**"
        )
        return "\n".join(lines)
    lines.append("| # | score | issue | фрагмент | verbatim |")
    lines.append("|---|---|---|---|---|")
    for i, f in enumerate(fragments, 1):
        lines.append(
            f"| {i} | {f['score']} | #{f['issue']} | {f['text'].replace('|', '\\|')} | "
            f"{'yes' if f['verbatim'] else 'NO'} |"
        )
    ok = all(f["verbatim"] for f in fragments)
    lines += [
        "",
        f"verbatim_all: {'yes' if ok else 'NO'}",
        "Полнота корпуса: sha256 каждого чанка зафиксирован в индексе.",
    ]
    return "\n".join(lines)


def selftest():
    corpus = [
        {
            "issue": 9001,
            "title": "t1",
            "text": "Узкий канал даёт точность пересказа, но не новизну.",
            "sha256": "x",
        },
        {
            "issue": 9002,
            "title": "t2",
            "text": "APK 6819 собран из ветки feat/redmi9-stand-a1mini-slice, не из develop.",
            "sha256": "y",
        },
    ]
    idx = BM25Index(build_units(corpus))
    hits = idx.search("узкий канал точность", top=3)
    assert hits and hits[0]["issue"] == 9001, "positive control failed"
    assert not idx.search("внезапный морозильник клубника Полярная звезда", top=3), (
        "RED control failed"
    )
    frs = verify_verbatim(hits, corpus)
    assert all(f["verbatim"] for f in frs), "verbatim control failed"
    bad = verify_verbatim([{"issue": 9001, "text": "фальшивка"}], corpus)
    assert not bad[0]["verbatim"], "fail-closed control failed"
    print("selftest: POSITIVE ok, RED ok, VERBATIM ok, FAIL-CLOSED ok")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["index", "query"])
    ap.add_argument("--query")
    ap.add_argument("--query-file")
    ap.add_argument("--publish-issue", type=int)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    token = os.environ.get("GITHUB_TOKEN")
    corpus = fetch_corpus(token)
    units = build_units(corpus)
    idx = BM25Index(units)

    if args.mode == "index":
        print(
            json.dumps(
                {"sources": len(corpus), "units": len(units)}, ensure_ascii=False
            )
        )
        return 0

    q = args.query or (open(args.query_file).read() if args.query_file else "")
    if not q.strip():
        print("empty query", file=sys.stderr)
        return 2
    frags = verify_verbatim(idx.search(q), corpus)
    rep = report(q, frags, len(units), len(corpus))
    if frags and all(f["verbatim"] for f in frags):
        print(rep)
        if args.publish_issue and token:
            api_post(f"/issues/{args.publish_issue}/comments", {"body": rep}, token)
        return 0
    print("FAIL-CLOSED: non-verbatim fragment; report not published", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
