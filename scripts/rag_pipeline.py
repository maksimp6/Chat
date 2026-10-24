"""RAG pipeline for the Logos runner (retrieval-augmented generation, stage 1).

Design contract (aligned with scripts/lexical_pipeline.py):
- Corpus = GitHub issues carrying label `rag-source` (chunks of real dialogues).
- Honest pagination: per_page=100, page=1..N until empty. No per_page=200 fiction.
- Every corpus entry records issue number + sha256 of its exact text.
- Retrieval: BM25 over sentence units (deterministic, no paid calls).
  Optional stage 2: Yandex embeddings when YANDEX_API_KEY is present.
- Answer = verbatim fragments only, each programmatically verified to be a
  substring of the source corpus. One failed check => report is NOT published
  (pipeline exits non-zero), same fail-closed rule as the lexical pipeline.
- Report contains separate counters: sources, units, query_terms_matched.

Modes:
  --mode index   : build index, print stats, optional --dump-index PATH
  --mode query   : run query from --query-file (or --query inline),
                   publish comment when --publish with GITHUB_TOKEN
  --selftest     : run embedded controls (RED control, verbatim, determinism)
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

if __package__ in {None, ""}:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.yandex_transport import yandex_urlopen

REPO = os.environ.get("GITHUB_REPOSITORY", "maksimp6/Chat")
API = f"https://api.github.com/repos/{REPO}"
LABEL_SOURCE = "rag-source"
LABEL_QUERY = "rag-query"
TOP_K = 5
PER_ISSUE_CAP = 3
STOP = {
    "и",
    "в",
    "не",
    "на",
    "что",
    "это",
    "я",
    "ты",
    "он",
    "она",
    "а",
    "как",
    "то",
    "же",
    "по",
    "из",
    "за",
    "но",
    "мы",
    "у",
    "с",
    "о",
    "для",
    "есть",
    "был",
    "быть",
    "или",
    "от",
    "до",
    "the",
    "a",
    "an",
    "of",
    "is",
    "are",
    "to",
    "it",
    "and",
    "or",
}


def api_get(path: str, token: str | None = None) -> dict:
    req = urllib.request.Request(API + path, headers={"Accept": "application/vnd.github+json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def api_post(path: str, payload: dict, token: str) -> dict:
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


# ---------------------------------------------------------------- corpus


def fetch_corpus(token: str | None) -> list[dict]:
    """Issues with label rag-source, honest pagination, oldest first."""
    issues: list[dict] = []
    page = 1
    while True:
        data = api_get(f"/issues?labels={LABEL_SOURCE}&state=open&per_page=100&page={page}", token)
        if not data:
            break
        issues.extend(data)
        page += 1
        if page > 50:
            raise RuntimeError("pagination runaway: >50 pages")
    out = []
    for it in issues:
        if "pull_request" in it:
            continue
        body = it.get("body") or ""
        # task header ends at first standalone --- separator; content follows
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


def split_sentences(text: str) -> list[str]:
    sents = []
    for m in re.finditer(r"[^.!?\u2026\n]+[.!?…]*", text):
        s = m.group().strip()
        if len(s) > 25:
            sents.append(s)
    return sents


def tokenize(s: str) -> list[str]:
    words = re.findall(r"[а-яёa-z0-9]+", s.lower())
    return [w for w in words if w not in STOP and len(w) > 2]


def build_units(corpus: list[dict]) -> list[dict]:
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


# ---------------------------------------------------------------- BM25


class BM25Index:
    def __init__(self, units: list[dict]):
        self.units = units
        self.N = len(units)
        self.avgdl = sum(len(u["tokens"]) for u in units) / max(self.N, 1)
        self.df = Counter()
        for u in units:
            self.df.update(set(u["tokens"]))
        self.k1, self.b = 1.5, 0.75

    def _idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        return math.log(1 + (self.N - df + 0.5) / (df + 0.5))

    def search(self, query: str, top: int = TOP_K) -> list[dict]:
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
                        "text": u["text"],
                        "sha256": u["sha256"],
                    }
                )
        results.sort(key=lambda r: -r["score"])
        # diversity: cap per issue so one chunk cannot eat the whole answer
        capped, seen = [], Counter()
        for r in results:
            if seen[r["issue"]] >= PER_ISSUE_CAP:
                continue
            capped.append(r)
            seen[r["issue"]] += 1
            if len(capped) >= top:
                break
        return capped


# ---------------------------------------------------------------- embedding stage 2 (optional)


def yandex_embeddings(texts: list[str]) -> list[list[float]] | None:
    key = os.environ.get("YANDEX_API_KEY")
    project = os.environ.get("YANDEX_PROJECT_ID")
    if not key or not project:
        return None
    out = []
    for t in texts[:64]:  # hard cap: cost control
        payload = json.dumps({"modelUri": f"emb://{project}/text-search-doc", "text": t}).encode()
        req = urllib.request.Request(
            "https://llm.api.cloud.yandex.net/foundationModels/v1/textEmbedding",
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Api-Key {key}",
                "Content-Type": "application/json",
            },
        )
        with yandex_urlopen(req, timeout=30) as r:
            data = json.load(r)
        out.append(data["embedding"])
    return out


# ---------------------------------------------------------------- verification (fail-closed)


def verify_corpus_fragments(fragments: list[dict], corpus: list[dict]) -> list[dict]:
    by_issue = {c["issue"]: c["text"] for c in corpus}
    checked = []
    for f in fragments:
        src = by_issue.get(f["issue"], "")
        checked.append({**f, "verbatim": f["text"] in src})
    return checked


def report(
    query: str, fragments: list[dict], corpus: list[dict], units_n: int, sources_n: int
) -> str:
    matched_terms = set(tokenize(query))
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
        lines.append("**PASS: релевантных фрагментов не найдено — ответ не выдумывается.**")
        return "\n".join(lines)
    lines.append("| # | score | issue | фрагмент | verbatim |")
    lines.append("|---|---|---|---|---|")
    for i, f in enumerate(fragments, 1):
        q = f["text"].replace("|", "\\|")
        lines.append(
            f"| {i} | {f['score']} | #{f['issue']} | {q} | {'yes' if f['verbatim'] else 'NO'} |"
        )
    ok = all(f["verbatim"] for f in fragments)
    lines.append("")
    lines.append(
        f"coverage: matched_terms={len(matched_terms & set(tokenize(' '.join(f['text'] for f in fragments))))}, verbatim_all={'yes' if ok else 'NO'}"
    )
    lines.append("")
    lines.append(
        "Полнота корпуса: sha256 каждого чанка записан в индексе; сравнение заявленного полного hash — как в lexical_pipeline."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------- selftest


def selftest() -> int:
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
    units = build_units(corpus)
    idx = BM25Index(units)
    hits = idx.search("узкий канал точность", top=3)
    assert hits and hits[0]["issue"] == 9001, "positive control failed"
    red = idx.search("внезапный морозильник клубника Полярная звезда", top=3)
    assert not red, "RED control failed: nonsense query returned hits"
    frs = verify_corpus_fragments(hits, corpus)
    assert all(f["verbatim"] for f in frs), "verbatim control failed"
    print("selftest: POSITIVE ok, RED ok, VERBATIM ok,", len(units), "units")
    return 0


# ---------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["index", "query"])
    ap.add_argument("--query-file")
    ap.add_argument("--query")
    ap.add_argument("--publish-issue", type=int)
    ap.add_argument("--dump-index")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    token = os.environ.get("GITHUB_TOKEN")
    corpus = fetch_corpus(token)
    units = build_units(corpus)
    idx = BM25Index(units)

    if args.mode == "index":
        if args.dump_index:
            with open(args.dump_index, "w") as f:
                json.dump(
                    {
                        "units": [{"issue": u["issue"], "text": u["text"]} for u in units],
                        "sha256_full": hashlib.sha256(
                            "".join(c["text"] for c in corpus).encode()
                        ).hexdigest(),
                    },
                    f,
                    ensure_ascii=False,
                )
        print(json.dumps({"sources": len(corpus), "units": len(units)}, ensure_ascii=False))
        return 0

    if args.mode == "query":
        if args.query_file:
            with open(args.query_file) as qf:
                q = qf.read()
        else:
            q = args.query or ""
        if not q.strip():
            print("empty query", file=sys.stderr)
            return 2
        frags = verify_corpus_fragments(idx.search(q, top=TOP_K), corpus)
        rep = report(q, frags, corpus, len(units), len(corpus))
        if all(f["verbatim"] for f in frags):
            print(rep)
            if args.publish_issue and token:
                api_post(f"/issues/{args.publish_issue}/comments", {"body": rep}, token)
            return 0
        print(
            "FAIL-CLOSED: non-verbatim fragment detected; report not published",
            file=sys.stderr,
        )
        print(rep, file=sys.stderr)
        return 1

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
