"""Sense extraction: what stored texts mean for a question, as a short text, with no language model.

Owner, 05.11.2026: "we don't need full sentences from remember just do sense extraction, and
convert it to text". Only the embedding model runs. The best chunk of each of the DOCUMENTS
best-matching documents is cut into short phrases at its punctuation, words that carry no
meaning are dropped from the ends of each phrase, a word alone joins the phrase next to it, a
negation stays with what it negates (a phrase must not say the opposite of the stored text), a
phrase the question itself holds is left out, every other phrase is embedded, and SENSE_PHRASES
phrases are chosen one by one: the nearest in meaning to the question, each losing
REPEAT_PENALTY of its likeness to the phrases already chosen (maximal marginal relevance). They
are joined into one line of text, best first. Tried on the owner's dialogs on 05.11.2026:
nearness alone gave eight rewordings of the question; this rule brought the facts up ("функция
remember не требует параметров", "2+2=4"), and scoring by the likeness to the whole chunk
instead of the question gave noise.

Nothing is generated: every word of the text is a stored word, without the punctuation around
it, so a vector is turned into text by finding the stored phrases nearest to it. ``remember``
gives this text to the model instead of whole chunks, and the pinned dialog with the embeddings
database (``chat.mcp_routes``) answers with it. On the owner's PC 32 short phrases embed in ~6 s.
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
# Phrases in the text, and the words of one phrase (a word alone joining it is one more).
SENSE_PHRASES = 8
PHRASE_WORDS = 6
# What a phrase loses for its likeness to the phrases already chosen.
REPEAT_PENALTY = 0.6

# The speakers' labels of a stored turn (chat.mcp_routes._embed_turn) are no part of what was said.
_LABEL = re.compile(r"(?:Пользователь|Мысли Логоса|Логос):\s")
# The number of a list item is no part of the item.
_LIST_MARK = re.compile(r"^[ \t]*\d+[.)][ \t]+(?=\S)", re.MULTILINE)
# A clause ends at a sentence or list mark followed by a space, a bracket, a quote, a line end or
# a dash between spaces; a mark inside a word ("2+2=4", "file.py", "qwen3:4b") stays. A colon ends
# nothing: what follows it belongs to what precedes it ("Имя: Максим", "Код выхода: 0").
_CLAUSE_END = re.compile(r"[.!?…]+(?=\s|$)|[,;](?=\s|$)|[()\[\]{}«»\"“”\n]|\s[—–-]\s")
# A token of nothing but these marks is markup: a list, heading or table mark, a rule.
_EDGE = ".,;:!?()[]{}\"'«»“”‘’…*_`#|<>~=-—–"
# What stands around a word and is no part of it. The sign of "-5", the dot of ".env", the
# underscore of "_chat_turn" and the "#" of "#1413" are.
_OPENING = ",;'‘’…*`|"
_CLOSING = ".,;:!?'‘’…*`|—–"
# A comparison means something between two words and nothing at the end of a phrase.
_COMPARISONS = frozenset(("<", ">", "<=", ">=", "=", "==", "!="))
# Words that say nothing at the end of a phrase; inside a phrase they stay, so it reads as said.
# Negations, numbers, "да", "можно", "надо" and "нужно" are not among them: without one of them
# a phrase says something else.
STOP_WORDS = frozenset(
    """
    а более бы был была были было быть в вам вас вдруг ведь во вот впрочем все всегда всего
    всех всю вы где даже для до его ее ей ему если есть еще ещё же за зачем здесь и из или им
    иногда их к как какая какой когда конечно кто куда ли лучше между меня мне много может
    мой моя мы на над наконец нас него нее ней нибудь ним них
    но ну о об он она они оно опять от перед по под после потом потому почти при про
    просто раз разве с сам свою себе себя сейчас со совсем так такой там тебя тем теперь то тогда
    того тоже только том тот тут ты у уж уже хм хорошо хоть чего чем через что чтоб чтобы чуть
    эти этого этой этом этот эту я итак ладно значит поэтому
    a about after all also am an and any are as at be because been but by can could did do does
    for from had has have he her here him his how i if in into is it its just like me more most
    my now of on only or our out she should so some such than that the their them
    then there these they this those to too up us was we were what when where which who why will
    with would you your hmm ok okay well
    """.split()
) | _COMPARISONS
# Words that turn what follows them into its opposite: a cut does not part them from it.
NEGATIONS = frozenset(
    """
    без не нельзя нет ни нигде никак никогда никто ничего ничто
    no not never cannot can't don't doesn't didn't isn't aren't wasn't weren't won't wouldn't
    shouldn't couldn't
    """.split()
)


def _word(raw: str) -> str:
    """``raw`` without the marks around it; a list, heading or table mark alone is nothing."""
    if raw in _COMPARISONS:
        return raw
    return raw.lstrip(_OPENING).rstrip(_CLOSING) if raw.strip(_EDGE) else ""


def _blocks(words: list[str]) -> list[list[str]]:
    """``words`` in blocks of at most PHRASE_WORDS. A negation among a block's last three words
    opens the next block, so it stays with what it negates."""
    blocks, start = [], 0
    while start < len(words):
        end = start + PHRASE_WORDS
        if end < len(words):
            end = next((i for i in range(end - 1, end - 4, -1) if words[i].casefold() in NEGATIONS), end)
        blocks.append(words[start:end])
        start = end
    return blocks


def _trimmed(words: list[str]) -> list[str]:
    start, end = 0, len(words)
    while start < end and words[start].casefold() in STOP_WORDS:
        start += 1
    while end > start and words[end - 1].casefold() in STOP_WORDS:
        end -= 1
    return words[start:end]


def _carries_sense(words: list[str]) -> bool:
    """Two words and more, or one that is more than a word: "2+2=4", "file.py", not "8080" or "кто-то"."""
    if len(words) != 1:
        return len(words) >= 2
    word = words[0].replace("-", "")
    return any(map(str.isalnum, word)) and not word.isalpha() and not word.isdigit()


def phrases(text: str) -> list[str]:
    """The short phrases of ``text`` in order: its clauses, in blocks of PHRASE_WORDS words.

    A word alone joins the phrase next to it, where it means something: "Логос: Барсик.", "г. Москва"."""
    found: list[list[str]] = []
    for clause in _CLAUSE_END.split(_LIST_MARK.sub("", _LABEL.sub(" ", text))):
        words = [word for word in map(_word, clause.split()) if word]
        for block in _blocks(words):
            phrase = _trimmed(block)
            if found and len(found[-1]) <= PHRASE_WORDS and not (_carries_sense(phrase) and _carries_sense(found[-1])):
                found[-1] += phrase
            elif phrase:
                found.append(phrase)
    return [" ".join(phrase) for phrase in found if any(map(str.isalnum, "".join(phrase)))]


def _usable(text: str, asked_text: str) -> list[str]:
    """The phrases of ``text`` that add to the question: what it says itself adds nothing."""
    return [phrase for phrase in phrases(text) if phrase.casefold() not in asked_text]


def best_documents(query: str, hits: list[dict[str, Any]], count: int = DOCUMENTS) -> list[dict[str, Any]]:
    """The best chunk of each of the ``count`` best documents of ``hits`` (a search's results, best first).

    A result of whole chunks repeated one past turn: the chunks of a document share its id. A chunk
    with nothing to add to ``query`` takes no place, and neither does a word-for-word copy of a
    chunk already read: the same question in the pinned dialog stores the same turn again."""
    asked_text = query.casefold()
    picked, documents, texts = [], set(), set()
    for hit in hits:
        document = (hit.get("collection"), hit.get("id"))
        text = str(hit.get("text") or "")
        if document in documents or text in texts or not _usable(text, asked_text):
            continue
        documents.add(document)
        texts.add(text)
        picked.append(hit)
        if len(picked) == count:
            break
    return picked


def extract(
    query: str, hits: list[dict[str, Any]], trace: Any = None, shown: frozenset | set = frozenset()
) -> dict[str, Any]:
    """The sense of the chunks ``hits`` for ``query``: ``{"text", "candidates", "tokens"}``.

    ``shown`` holds the phrases already given (casefolded). They are chosen as usual and left out
    of the text afterwards, so the same question gets nothing rather than the next best phrases."""
    asked_text = query.casefold()
    candidates: dict[str, str] = {}
    for hit in hits:
        for phrase in _usable(str(hit.get("text") or ""), asked_text):
            candidates.setdefault(phrase.casefold(), phrase)
    found = list(candidates.values())
    if not found:
        return {"text": "", "candidates": 0, "tokens": 0}
    vectors, tokens = store.unit_vectors([query, *found], trace=trace)
    asked, units = vectors[0], vectors[1:]
    scores = [math.sumprod(asked, unit) for unit in units]
    pool, chosen = list(range(len(found))), []
    while pool and len(chosen) < SENSE_PHRASES:
        best = max(
            pool,
            key=lambda i: scores[i]
            - REPEAT_PENALTY * max((math.sumprod(units[i], units[j]) for j in chosen), default=0.0),
        )
        pool.remove(best)
        chosen.append(best)
    given = [i for i in chosen if found[i].casefold() not in shown]
    text = "; ".join(found[i] for i in given)
    if trace is not None and hasattr(trace, "add_event"):
        trace.add_event("sense_extracted", {
            "documents": [
                {key: hit.get(key) for key in ("collection", "id", "chunk", "score")} for hit in hits
            ],
            "candidates": len(found),
            "phrases": [{"phrase": found[i], "score": round(scores[i], 6)} for i in given],
            "repeats_left_out": len(chosen) - len(given),
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
    sense = extract(question, best_documents(question, hits), trace=trace)
    return {
        "text": sense["text"],
        "searched": len(hits),
        "tokens": int(found.get("tokens") or 0) + sense["tokens"],
    }
