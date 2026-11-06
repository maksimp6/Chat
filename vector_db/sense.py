"""Sense extraction: what stored texts mean for a question, as a short text, with no language model.

Owner, 05.11.2026: "we don't need full sentences from remember just do sense extraction, and
convert it to text". Only the embedding model runs. The best chunk of each of the DOCUMENTS
best-matching documents is cut into short phrases at its punctuation, and words that carry no
meaning are dropped from the ends of each phrase. A word alone joins the phrase before it,
unless that phrase is already longer than PHRASE_WORDS. A clause that holds a negation is not
cut, and after a comma, a dash or a sentence end it takes no word alone, so that a phrase does
not say the opposite of the stored text (a comma, or a quote of several words, can still part a
negation from what it negates). A phrase the question itself holds is left out, except a word
alone after another phrase, which can be the answer. Every other phrase is embedded, and
SENSE_PHRASES phrases are chosen one by one: the nearest in meaning to the question, each losing
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
# Phrases in the text, and the words of one phrase (a word alone joining it is one more; a clause
# that holds a negation stays whole, however long).
SENSE_PHRASES = 8
PHRASE_WORDS = 6
# What a phrase loses for its likeness to the phrases already chosen.
REPEAT_PENALTY = 0.6

# The speakers' labels of a stored turn (chat.mcp_routes._embed_turn) are no part of what was said.
_LABEL = re.compile(r"(?:Пользователь|Мысли Логоса|Логос):\s")
# The mark of a list item (a number, or "-" or "*" with a space after it) or a heading's number is
# no part of it, and the item before it ends there. A number opens a line or follows a sentence
# ("План: 1. Проверить порт. 2. Перезапустить сервер."). After a colon or a question mark it is an
# answer, after a lone letter and a dot a reference ("Аналог п. 41."), and a year is no list
# number. Run before _LABEL, or a reply that opens with a number ("Логос: 12. Все они…") would
# look like a list.
_LIST_MARK = re.compile(
    r"(?:^[ \t]*(?:#+[ \t]+)?|(?<=[.;] )(?<!\b[^\W\d]\. ))\d{1,3}[.)][ \t]+(?=\S)|^[ \t]*[-*][ \t]+(?=\S)",
    re.MULTILINE,
)
# A clause ends at a sentence or list mark followed by a space, a bracket, a quote, a line end or
# a dash between spaces, unless a number stands before a hyphen ("12 - 5 = 7"); a mark inside a
# word ("2+2=4", "file.py", "qwen3:4b") stays. A colon ends nothing: what follows it belongs to
# what precedes it ("Имя: Максим", "Код выхода: 0").
_CLAUSE_END = re.compile(r"([.!?…]+(?=\s|$)|[,;](?=\s|$)|[()\[\]{}«»\"“”\n]|\s[—–]\s|(?<!\d)\s-\s)")
# The marks that part a phrase with a negation from a word alone after them. A quote, a bracket, a
# question mark and a line end do not: "система не «убивает» агента", "Файла нет?\n\nЛогос: Нет.".
_PARTS = re.compile(r"[.!…,;—–-]")
# A token of nothing but these marks is markup (a heading or table mark, a rule), unless it is one
# of _SIGNS.
_EDGE = ".,;:!?()[]{}\"'«»“”‘’…*_`#|<>~=-—–"
# What stands around a word and is no part of it. The sign of "-5", the dot of ".env", the
# underscore of "_chat_turn" and the "#" of "#1413" are.
_OPENING = ",;'‘’…*`|"
_CLOSING = ".,;:!?'‘’…*`|—–"
# A sign means something between two words and nothing at the end of a phrase.
_SIGNS = frozenset(("<", ">", "<=", ">=", "=", "==", "===", "!=", "!==", "<>", "-", "*"))
# Words that say nothing at the end of a phrase; inside a phrase they stay, so it reads as said.
# Negations, numbers, "да", "есть", "можно", "надо" and "нужно" are not among them: without one of
# them a phrase says something else.
STOP_WORDS = frozenset(
    """
    а более бы был была были было быть в вам вас вдруг ведь во вот впрочем все всегда всего
    всех всю вы где даже для до его ее ей ему если еще ещё же за зачем здесь и из или им
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
) | _SIGNS
# Words that turn what stands beside them into its opposite, on either side: "не вызывает",
# "запускать нельзя".
NEGATIONS = frozenset(
    """
    без не нельзя нет ни нигде никак никогда никто ничего ничто
    no not never cannot can't don't doesn't didn't isn't aren't wasn't weren't won't wouldn't
    shouldn't couldn't
    """.split()
)
# A line that ends in a negation is taken for a hard wrap and goes on in the next line, unless the
# negation opens the line (an answer) or directly follows a colon (a value). So a finished line
# with no full stop fuses with the next one: only a length could tell it from a wrap (a script's
# "gc.pruneExpire never", then "pin_tips").
_WRAPPED_NEGATION = re.compile(
    r"(?<=[^\s:] )(" + "|".join(map(re.escape, NEGATIONS)) + r")\n[ \t]*(?=\S)", re.IGNORECASE
)


def _word(raw: str) -> str:
    """``raw`` without the marks around it. A heading or table mark or a rule alone is nothing, and
    a sign stays for _trimmed."""
    if raw in _SIGNS:
        return raw
    return raw.lstrip(_OPENING).rstrip(_CLOSING) if raw.strip(_EDGE) else ""


def _negated(words: list[str]) -> bool:
    return not NEGATIONS.isdisjoint(word.casefold() for word in words)


def _blocks(words: list[str]) -> list[list[str]]:
    """``words`` in blocks of PHRASE_WORDS, or whole if they hold a negation: a cut would leave one
    side of it saying the opposite ("Запускать тесты на этом устройстве без отдельной команды нельзя")."""
    if _negated(words):
        return [words]
    return [words[start : start + PHRASE_WORDS] for start in range(0, len(words), PHRASE_WORDS)]


def _trimmed(words: list[str]) -> list[str]:
    """``words`` without the stop words at their ends; the word after a negation stays: "Ошибки не было"."""
    start, end = 0, len(words)
    while start < end and words[start].casefold() in STOP_WORDS:
        start += 1
    while end > start and words[end - 1].casefold() in STOP_WORDS and not (
        end - 2 >= start and words[end - 2].casefold() in NEGATIONS
    ):
        end -= 1
    return words[start:end]


def _carries_sense(words: list[str]) -> bool:
    """Two words and more, or one that is more than a word: "2+2=4", "file.py", not "8080" or "кто-то"."""
    if len(words) != 1:
        return len(words) >= 2
    word = words[0].replace("-", "")
    return any(map(str.isalnum, word)) and not word.isalpha() and not word.isdigit()


def _takes_word(phrase: list[str], parted: bool) -> bool:
    """Whether a word alone can join ``phrase``: not one already longer than PHRASE_WORDS, and not
    one with a negation that a mark ``parted`` from the word, which would fall under it."""
    return len(phrase) <= PHRASE_WORDS and not (parted and _negated(phrase))


def phrases(text: str) -> list[str]:
    """The short phrases of ``text`` in order: its clauses, in blocks of PHRASE_WORDS words, or whole
    if they hold a negation.

    A word alone joins the phrase before it, where it means something ("Логос: Барсик.", "г. Москва"),
    unless that phrase is already longer than PHRASE_WORDS, or holds a negation and a comma, a dash
    or a sentence end parts them ("Порт не 8080, а 9090.")."""
    found: list[list[str]] = []
    parted = False
    pieces = _CLAUSE_END.split(_WRAPPED_NEGATION.sub(r"\1 ", _LABEL.sub(" ", _LIST_MARK.sub(".\n", text))))
    for clause, mark in zip(pieces[::2], pieces[1::2] + [""]):
        words = [word for word in map(_word, clause.split()) if word]
        for block in _blocks(words):
            phrase = _trimmed(block)
            if found and not _carries_sense(phrase) and _takes_word(found[-1], parted):
                found[-1] += phrase
            elif phrase:
                found.append(phrase)
            parted = parted and not phrase
        parted = parted or _PARTS.search(mark) is not None
    return [" ".join(phrase) for phrase in found if any(map(str.isalnum, "".join(phrase)))]


def _usable(text: str, asked_text: str) -> list[str]:
    """The phrases of ``text`` that add to the question: what it says itself adds nothing, but a word
    alone after another phrase can be the answer ("Можно или нельзя?", "Логос: Нельзя.")."""
    found = phrases(text)
    return [
        phrase for index, phrase in enumerate(found)
        if phrase.casefold() not in asked_text or (index and " " not in phrase)
    ]


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

    ``shown`` holds the keys of the phrases already given (the ``key`` of each phrase of a
    ``sense_extracted`` event: the trace redacts a phrase that looks like a secret, so its text
    would never match). They are chosen as usual and left out of the text afterwards, so the same
    question gets nothing rather than the next best phrases."""
    asked_text = query.casefold()
    candidates: dict[str, str] = {}
    for hit in hits:
        for phrase in _usable(str(hit.get("text") or ""), asked_text):
            candidates.setdefault(phrase.casefold(), phrase)
    found, keys = list(candidates.values()), list(map(hash, candidates))
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
    given = [i for i in chosen if keys[i] not in shown]
    text = "; ".join(found[i] for i in given)
    if trace is not None and hasattr(trace, "add_event"):
        trace.add_event("sense_extracted", {
            "documents": [
                {key: hit.get(key) for key in ("collection", "id", "chunk", "score")} for hit in hits
            ],
            "candidates": len(found),
            "phrases": [{"phrase": found[i], "key": keys[i], "score": round(scores[i], 6)} for i in given],
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
