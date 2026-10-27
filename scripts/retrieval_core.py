"""Shared deterministic tokenization and BM25 ranking for retrieval pipelines."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Container


def split_sentences(text: str, *, min_chars: int) -> list[str]:
    sentences = []
    for match in re.finditer(r"[^.!?\u2026\n]+[.!?…]*", text):
        sentence = match.group().strip()
        if len(sentence) >= min_chars:
            sentences.append(sentence)
    return sentences


def tokenize(text: str, *, stop_words: Container[str], min_chars: int) -> list[str]:
    words = re.findall(r"[а-яёa-z0-9]+", text.lower())
    return [word for word in words if word not in stop_words and len(word) >= min_chars]


class BM25Index:
    """Rank immutable unit metadata; callers supply their query and result policy."""

    def __init__(self, units: list[dict], *, k1: float, b: float, score_precision: int):
        self.units = units
        self.N = len(units)
        self.avgdl = sum(len(unit["tokens"]) for unit in units) / max(self.N, 1)
        self.df = Counter()
        for unit in units:
            self.df.update(set(unit["tokens"]))
        self.k1, self.b = k1, b
        self.score_precision = score_precision

    def _idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        return math.log(1 + (self.N - df + 0.5) / (df + 0.5))

    def search_tokens(self, query_tokens: list[str], *, top: int, per_issue_cap: int) -> list[dict]:
        results = []
        for unit in self.units:
            counts = Counter(unit["tokens"])
            score = 0.0
            for term in query_tokens:
                frequency = counts.get(term, 0)
                if not frequency:
                    continue
                length = len(unit["tokens"])
                score += (
                    self._idf(term)
                    * frequency
                    * (self.k1 + 1)
                    / (frequency + self.k1 * (1 - self.b + self.b * length / self.avgdl))
                )
            if score > 0:
                results.append({**unit, "score": round(score, self.score_precision)})
        results.sort(key=lambda result: -result["score"])
        capped, seen = [], Counter()
        for result in results:
            if seen[result["issue"]] >= per_issue_cap:
                continue
            capped.append(result)
            seen[result["issue"]] += 1
            if len(capped) >= top:
                break
        return capped
