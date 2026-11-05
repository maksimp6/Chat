"""Sense extraction: what stored texts mean for a question, as a short text, with no language model.

Owner, 05.11.2026: "we don't need full sentences from remember just do sense extraction, and
convert it to text". Only the embedding model runs. The best chunk of each of the DOCUMENTS
best-matching documents is cut into short phrases at its punctuation, words that carry no
meaning are dropped from the ends of each phrase, every phrase is embedded, and the
SENSE_PHRASES phrases nearest in meaning to the question, without near-repeats, are joined
into one line of text, best first.

Nothing is generated: every word of the text is a stored word, so a vector is turned into
text by finding the stored phrases nearest to it. ``remember`` gives this text to the model
instead of whole chunks, and the pinned dialog with the embeddings database
(``chat.mcp_routes``) answers with it. On the owner's PC 32 short phrases embed in ~6 s.
"""

from __future__ import annotations

import math
import re
from typing import Any

from vector_db import store

__all__ = ["DOCUMENTS", "SENSE_PHRASES", "answer", "best_documents", "extract", "phrases"]

# Documents read per question; the best chunk of each. From the traces of 04-05.11.2026: the
# only piece with the asked-for answer was the third document found.
DOCUMENTS = 3
# Phrases in the text, and the most words in one phrase.
SENSE_PHRASES = 8
PHRASE_WORDS = 6
# Two phrases this close in meaning say the same thing: the later one is left out.
REPEAT_SIMILARITY = 0.9

# The speakers' labels of a stored turn (chat.mcp_routes._embed_turn) are no part of what was said.
_LABEL = re.compile(r"(?:Пользователь|Мысли Логоса|Логос):\s")
# A clause ends at a sentence or list mark followed by a space, a bracket, a quote, a line end or
# a dash between spaces; a mark inside a word ("2+2=4", "file.py", "qwen3:4b") stays.
_CLAUSE_END = re.compile(r"[.!?…]+(?=\s|$)|[,;:](?=\s|$)|[()\[\]{}«»\"“”\n]|\s[—–-]\s")
_EDGE = ".,;:!?()[]{}\"'«»“”‘’…*_`#|<>~=-—–"
# Words that say nothing at the end of a phrase; inside a phrase they stay, so it reads as said.
STOP_WORDS = frozenset(
    """
    а без более бы был была были было быть в вам вас вдруг ведь во вот впрочем все всегда всего
    всех всю вы где да даже для до его ее ей ему если есть еще ещё же за зачем здесь и из или им
    иногда их к как какая какой когда конечно кто куда ли лучше между меня мне много может можно
    мой моя мы на над надо наконец нас не него нее ней нельзя нет ни нибудь никогда ним них ничего
    но ну нужно о об один он она они оно опять от перед по под после потом потому почти при про
    просто раз разве с сам свою себе себя сейчас со совсем так такой там тебя тем теперь то тогда
    того тоже только том тот три тут ты у уж уже хм хорошо хоть чего чем через что чтоб чтобы чуть
    эти этого этой этом этот эту я итак ладно значит поэтому
    a about after all also am an and any are as at be because been but by can could did do does
    for from had has have he her here him his how i if in into is it its just like me more most
    my no not now of on one only or our out she should so some such than that the their them
    then there these they this those to too up us was we were what when where which who why will
    with would you your hmm ok okay well
    """.split()
)


def _trimmed(words: list[str]) -> list[str]:
    start, end = 0, len(words)
    while start < end and words[start].casefold() in STOP_WORDS:
        start += 1
    while end > start and words[end - 1].casefold() in STOP_WORDS:
        end -= 1
    return words[start:end]


def _carries_sense(words: list[str]) -> bool:
    """Two words and more, or one that is more than a word: "2+2=4", "file.py"."""
    return len(words) >= 2 or (len(words) == 1 and not words[0].isalpha())


def phrases(text: str) -> list[str]:
    """The short phrases of ``text`` in order: its clauses, each cut to PHRASE_WORDS words."""
    found = []
    for clause in _CLAUSE_END.split(_LABEL.sub(" ", text)):
        words = [word for word in (raw.strip(_EDGE) for raw in clause.split()) if word]
        for start in range(0, len(words), PHRASE_WORDS):
            phrase = _trimmed(words[start : start + PHRASE_WORDS])
            if _carries_sense(phrase):
                found.append(" ".join(phrase))
    return found


def best_documents(hits: list[dict[str, Any]], count: int = DOCUMENTS) -> list[dict[str, Any]]:
    """The best chunk of each of the ``count`` best documents of ``hits`` (a search's results, best first).

    A result of whole chunks repeated one past turn: the chunks of a document share its id."""
    picked, documents = [], set()
    for hit in hits:
        document = (hit.get("collection"), hit.get("id"))
        if document in documents:
            continue
        documents.add(document)
        picked.append(hit)
        if len(picked) == count:
            break
    return picked


def extract(query: str, hits: list[dict[str, Any]], trace: Any = None) -> dict[str, Any]:
    """The sense of the chunks ``hits`` for ``query``: ``{"text", "candidates", "tokens"}``."""
    candidates: dict[str, str] = {}
    for hit in hits:
        for phrase in phrases(str(hit.get("text") or "")):
            candidates.setdefault(phrase.casefold(), phrase)
    found = list(candidates.values())
    if not found:
        return {"text": "", "candidates": 0, "tokens": 0}
    vectors, tokens = store.unit_vectors([query, *found], trace=trace)
    asked = vectors[0]
    ranked = sorted(
        ((math.sumprod(asked, vector), phrase, vector) for phrase, vector in zip(found, vectors[1:], strict=True)),
        key=lambda item: item[0],
        reverse=True,
    )
    chosen: list[tuple[float, str, Any]] = []
    for score, phrase, vector in ranked:
        if any(math.sumprod(vector, other[2]) >= REPEAT_SIMILARITY for other in chosen):
            continue
        chosen.append((score, phrase, vector))
        if len(chosen) == SENSE_PHRASES:
            break
    text = "; ".join(phrase for _, phrase, _ in chosen)
    if trace is not None and hasattr(trace, "add_event"):
        trace.add_event("sense_extracted", {
            "documents": [
                {key: hit.get(key) for key in ("collection", "id", "chunk", "score")} for hit in hits
            ],
            "candidates": len(found),
            "phrases": [{"phrase": phrase, "score": round(score, 6)} for score, phrase, _ in chosen],
            "tokens": tokens,
        })
    return {"text": text, "candidates": len(found), "tokens": tokens}


def _said_in(hit: dict[str, Any], conversation: str) -> bool:
    metadata = hit.get("metadata")
    return isinstance(metadata, dict) and metadata.get("conversation_id") == conversation


def answer(question: str, *, exclude_conversation: str | None = None, trace: Any = None) -> dict[str, Any]:
    """What the vector database holds on ``question``: ``{"text", "searched", "tokens"}``.

    A stored turn of ``exclude_conversation`` is left out, so a dialog does not read itself back."""
    found = store.search(question, None, None, trace=trace)
    hits = [
        hit for hit in found["results"]
        if exclude_conversation is None or not _said_in(hit, exclude_conversation)
    ]
    sense = extract(question, best_documents(hits), trace=trace)
    return {
        "text": sense["text"],
        "searched": len(hits),
        "tokens": int(found.get("tokens") or 0) + sense["tokens"],
    }
