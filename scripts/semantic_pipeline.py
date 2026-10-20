"""Semantic pipeline for the Logos runner (stage 2: LLM over BM25 candidates).

Design contract (aligned with scripts/rag_pipeline.py):
- Corpus = GitHub issues carrying label `rag-source`; honest per_page=100 pagination.
- Retrieval: BM25 over sentence units (deterministic, free) -> top-K candidates.
- One bounded YandexGPT call per query (model aliceai-llm-flash, background=False,
  store=False, max_output_tokens<=4096): the model may only RE-SELECT and cite
  verbatim fragments from the candidate list; it must refuse when nothing is
  relevant (RED contract).
- Fail-closed: every cited fragment is verified verbatim against the corpus
  BEFORE publication. One failed check => report is NOT published, exit 1.
- RED contract: nonsense query => refusal expected; if the model cites anything
  on a RED query, the report is marked RED_FAIL (still verbatim-checked).
- Budget: exactly one model call per query run; usage recorded in the report.

Modes:
  --mode query     : run query from --query-file or --query, publish to --publish-issue
  --mode red       : same, but treat the query as a negative control (refusal expected)
  --selftest       : offline controls (RED prompt contract, JSON parsing, verbatim)
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import sys
import urllib.request
from collections import Counter

REPO = os.environ.get("GITHUB_REPOSITORY", "maksimp6/Chat")
API = f"https://api.github.com/repos/{REPO}"
LABEL_SOURCE = "rag-source"
TOP_K = 30
PER_ISSUE_CAP = 6
MODEL = "aliceai-llm-flash"
HOST = "ai.api.cloud.yandex.net"
MAX_OUTPUT_TOKENS = 4096
MAX_RESPONSE_BYTES = 262144
STOP = {
    "и", "в", "не", "на", "что", "это", "я", "ты", "он", "она", "а", "как", "то", "же",
    "по", "из", "за", "но", "мы", "у", "с", "о", "для", "есть", "был", "быть", "или",
    "от", "до", "the", "a", "an", "of", "is", "are", "to", "it", "and", "or",
}

INSTRUCTIONS = (
    "Ты работаешь с корпусом источников. Правила: 1) цитируй ТОЛЬКО дословные"
    " фрагменты из списка кандидатов, не переформулируй их и не склеивай;"
    " 2) на каждый выбранный фрагмент дай его номер id и точную цитату;"
    " 3) если ни один кандидат не отвечает на вопрос — верни refusal=true и"
    " пустой список citations, не выдумывай ничего; 4) не добавляй фактов,"
    " которых нет в кандидатах. Ответ — только JSON вида"
    ' {"answer": "<краткий ответ по-русски>", "refusal": false,'
    ' "citations": [{"id": <номер>, "quote": "<дословный фрагмент>"}]}'
)


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
        headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def fetch_corpus(token: str | None) -> list[dict]:
    issues: list[dict] = []
    page = 1
    while True:
        data = api_get(
            f"/issues?labels={LABEL_SOURCE}&state=open&per_page=100&page={page}", token
        )
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
        parts = body.split("\n---\n", 1)
        text = parts[1] if len(parts) > 1 else body
        out.append(
            {
                "issue": it["number"],
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
            units.append({"issue": c["issue"], "text": s, "tokens": tokenize(s)})
    return units


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

    def search(self, query: str, top: int = TOP_K, per_issue_cap: int = PER_ISSUE_CAP) -> list[dict]:
        import math as _m
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
                results.append({"score": round(score, 3), "issue": u["issue"], "text": u["text"]})
        results.sort(key=lambda r: -r["score"])
        capped, seen = [], Counter()
        for r in results:
            if seen[r["issue"]] >= per_issue_cap:
                continue
            capped.append(r)
            seen[r["issue"]] += 1
            if len(capped) >= top:
                break
        return capped


def build_prompt(query: str, candidates: list[dict]) -> str:
    lines = [f"Вопрос: {query}", "", "Кандидаты (фрагменты корпуса):"]
    for i, c in enumerate(candidates, 1):
        q = c["text"].replace("\n", " ")
        lines.append(f"[{i}] (issue #{c['issue']}) {q}")
    lines += [
        "",
        "Выбери фрагменты, отвечающие на вопрос. Цитируй дословно. Если релевантных нет — refusal=true.",
        "Ответ — только JSON.",
    ]
    return "\n".join(lines)


def yandex_call(project: str, key: str, prompt: str) -> dict:
    """One bounded sync call; fixed destination, no retries, no redirects."""
    payload = {
        "model": f"gpt://{project}/{MODEL}/latest",
        "input": prompt,
        "instructions": INSTRUCTIONS,
        "background": False,
        "store": False,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
    }
    conn = http.client.HTTPSConnection(HOST, timeout=90)
    body = json.dumps(payload).encode()
    headers = {
        "Authorization": f"Api-Key {key}",
        "Content-Type": "application/json",
        "x-yc-project-id": project,
        "OpenAI-Project": project,
    }
    try:
        conn.request("POST", "/v1/responses", body=body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read(MAX_RESPONSE_BYTES + 1)
        if resp.status != 200:
            return {"error": f"provider_http_{resp.status}"}
        if len(raw) > MAX_RESPONSE_BYTES:
            return {"error": "provider_response_too_large"}
        data = json.loads(raw)
    finally:
        conn.close()
    if data.get("status") != "completed":
        return {"error": "provider_response_not_completed"}
    chunks = []
    for item in data.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if isinstance(part, dict) and part.get("type") == "output_text":
                chunks.append(part.get("text") or "")
    if not chunks:
        return {"error": "provider_text_missing"}
    usage = data.get("usage") or {}
    return {
        "text": "".join(chunks),
        "usage": {k: v for k, v in usage.items() if isinstance(v, int) and not isinstance(v, bool)},
    }


def parse_model_json(text: str) -> dict:
    cleaned = text.strip()
    m = re.search(r"```(?:json)?\s*(.+?)```", cleaned, re.S)
    if m:
        cleaned = m.group(1).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("model_output_not_json")
    return json.loads(cleaned[start : end + 1])


def verify_citations(citations: list[dict], candidates: list[dict], corpus: list[dict]) -> list[dict]:
    cand_by_id = {i + 1: c for i, c in enumerate(candidates)}
    src_by_issue = {c["issue"]: c["text"] for c in corpus}
    out = []
    for cit in citations:
        cid = cit.get("id")
        quote = (cit.get("quote") or "").strip()
        cand = cand_by_id.get(cid)
        if cand is None:
            out.append({"id": cid, "verbatim": False, "reason": "unknown_candidate_id"})
            continue
        verbatim = quote in src_by_issue.get(cand["issue"], "")
        out.append({"id": cid, "issue": cand["issue"], "quote": quote, "verbatim": verbatim})
    return out


def report(query, mode, answer, refusal, citations, usage, n_sources, n_units, model) -> str:
    lines = [
        "<!-- semantic-report -->",
        "## Семантический отчёт (stage 2: BM25 → YandexGPT)",
        "",
        f"- sources (issues): {n_sources}",
        f"- units (предложения): {n_units}",
        f"- query: `{query}`",
        f"- model: {model}",
        f"- usage: {json.dumps(usage, ensure_ascii=False) if usage else 'n/a'}",
        f"- mode: {mode}",
        "",
    ]
    if refusal:
        lines.append("**REFUSAL: модель не нашла релевантных фрагментов — ответ не выдуман.**")
        if mode == "red":
            lines.append("RED control: **PASS** (отказ на бессмысленном запросе)")
        return "\n".join(lines)
    lines.append(f"Ответ: {answer}")
    lines.append("")
    lines.append("| # | issue | цитата | verbatim |")
    lines.append("|---|---|---|---|")
    for i, c in enumerate(citations, 1):
        q = (c.get("quote") or "").replace("|", "\\|")
        v = "yes" if c.get("verbatim") else "NO"
        lines.append(f"| {i} | #{c.get('issue')} | {q} | {v} |")
    ok = all(c.get("verbatim") for c in citations)
    lines.append("")
    lines.append(f"verbatim_all: {'yes' if ok else 'NO'}")
    if mode == "red" and citations:
        lines.append("RED control: **FAIL** (модель цитирует на бессмысленном запросе)")
    return "\n".join(lines)


def selftest() -> int:
    corpus = [
        {"issue": 9001, "text": "Узкий канал даёт точность пересказа, но не новизну.", "sha256": "x"},
        {"issue": 9002, "text": "APK 6819 собран из ветки feat/redmi9-stand-a1mini-slice.", "sha256": "y"},
    ]
    units = build_units(corpus)
    idx = BM25Index(units)
    hits = idx.search("узкий канал точность", top=3)
    assert hits and hits[0]["issue"] == 9001, "positive control failed"
    red = idx.search("внезапный морозильник клубника Полярная звезда", top=3)
    assert not red, "RED control failed"
    cits = verify_citations([{"id": 1, "quote": hits[0]["text"]}], hits, corpus)
    assert cits[0]["verbatim"], "verbatim control failed"
    # JSON parsing control
    obj = parse_model_json('```json\n{"answer": "a", "refusal": false, "citations": []}\n```')
    assert obj["refusal"] is False
    # refusal contract in report
    rep = report("q", "red", "", True, [], {}, 2, len(units), MODEL)
    assert "REFUSAL" in rep and "PASS" in rep
    print("selftest: POSITIVE ok, RED ok, VERBATIM ok, JSON ok,", len(units), "units")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["query", "red"])
    ap.add_argument("--query-file")
    ap.add_argument("--query")
    ap.add_argument("--publish-issue", type=int)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    token = os.environ.get("GITHUB_TOKEN")
    key = os.environ.get("YANDEX_API_KEY")
    project = os.environ.get("YANDEX_PROJECT_ID")
    if not key or not project:
        print("missing YANDEX_API_KEY/YANDEX_PROJECT_ID", file=sys.stderr)
        return 2

    corpus = fetch_corpus(token)
    units = build_units(corpus)
    idx = BM25Index(units)

    if args.query_file:
        with open(args.query_file) as qf:
            q = qf.read().strip()
    else:
        q = (args.query or "").strip()
    if not q:
        print("empty query", file=sys.stderr)
        return 2

    candidates = idx.search(q, top=TOP_K)
    if not candidates:
        rep = report(q, args.mode, "", True, [], None, len(corpus), len(units), MODEL)
        print(rep)
        if args.publish_issue and token:
            api_post(f"/issues/{args.publish_issue}/comments", {"body": rep}, token)
        return 0

    prompt = build_prompt(q, candidates)
    result = yandex_call(project, key, prompt)
    if "error" in result:
        print(f"model call failed: {result['error']}", file=sys.stderr)
        return 3
    try:
        obj = parse_model_json(result["text"])
    except ValueError:
        print("FAIL-CLOSED: model output not parseable as JSON; report not published", file=sys.stderr)
        return 1
    refusal = bool(obj.get("refusal"))
    citations = verify_citations(obj.get("citations") or [], candidates, corpus)
    rep = report(q, args.mode, obj.get("answer", ""), refusal, citations, result.get("usage"), len(corpus), len(units), MODEL)
    all_verbatim = all(c.get("verbatim") for c in citations) if citations else True
    red_fail = args.mode == "red" and bool(citations)
    if all_verbatim and not red_fail:
        print(rep)
        if args.publish_issue and token:
            api_post(f"/issues/{args.publish_issue}/comments", {"body": rep}, token)
        return 0
    print("FAIL-CLOSED: verbatim or RED contract violated; report not published", file=sys.stderr)
    print(rep, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
