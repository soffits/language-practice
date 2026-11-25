"""Text helpers that work for any script: accented Latin, CJK, mixed text."""
from __future__ import annotations

import re
import unicodedata

_CJK = (
    (0x3040, 0x30FF),  # kana
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xAC00, 0xD7AF),  # hangul syllables
    (0xF900, 0xFAFF),
    (0x20000, 0x2FA1F),
)
_WORD = re.compile(r"\w+(?:['’\-]\w+)*", re.UNICODE)


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def is_cjk(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in _CJK)


def count_units(text: str) -> int:
    """Words for space-separated scripts, characters for CJK: the unit exams count length in."""
    total = 0
    for chunk in _WORD.findall(nfc(text)):
        cjk = sum(1 for ch in chunk if is_cjk(ch))
        total += cjk + (1 if cjk < len(chunk) else 0)
    return total


def normalize(text: str) -> str:
    """Comparison key: NFC, case-folded, single spaces, edge punctuation dropped. Accents are kept."""
    value = " ".join(nfc(text).casefold().split())
    start, end = 0, len(value)
    while start < end and unicodedata.category(value[start]).startswith("P"):
        start += 1
    while end > start and unicodedata.category(value[end - 1]).startswith("P"):
        end -= 1
    return value[start:end]


def find_span(sentence: str, answer: str) -> tuple[int, int] | None:
    """Locate `answer` inside `sentence` as a whole word or phrase (any substring for CJK)."""
    sentence, answer = nfc(sentence), nfc(answer)
    if not answer:
        return None
    for flags in (0, re.IGNORECASE):
        if any(is_cjk(ch) for ch in answer):
            pattern = re.escape(answer)
        else:
            pattern = r"(?<!\w)" + re.escape(answer) + r"(?!\w)"
        match = re.search(pattern, sentence, flags)
        if match:
            return match.span()
    return None


def make_cloze(sentence: str, answer: str, number: int = 1) -> str | None:
    """Anki-style cloze: the answer replaced by {{c1::answer}}. None when it is not in the sentence."""
    span = find_span(sentence, answer)
    if span is None:
        return None
    sentence = nfc(sentence)
    return f"{sentence[:span[0]]}{{{{c{number}::{sentence[span[0]:span[1]]}}}}}{sentence[span[1]:]}"


_CLOZE = re.compile(r"\{\{c\d+::(.*?)(?:::(.*?))?\}\}")


def cloze_front(text: str, blank: str = "____") -> str:
    return _CLOZE.sub(blank, text)


def cloze_answers(text: str) -> list[str]:
    return [m.group(1) for m in _CLOZE.finditer(text)]


def cloze_back(text: str) -> str:
    return _CLOZE.sub(lambda m: m.group(1), text)


def minimal_change(wrong: str, right: str) -> tuple[str, str]:
    """Strip what both versions share at the start and end, so a card tests only what changed
    ("people who has" / "people who have" -> "has" / "have"). Words for spaced scripts, characters for CJK."""
    wrong, right = nfc(wrong), nfc(right)
    spaced = not any(is_cjk(ch) for ch in wrong + right)
    a, b = (wrong.split(), right.split()) if spaced else (list(wrong), list(right))
    start = 0
    while start < min(len(a), len(b)) and a[start] == b[start]:
        start += 1
    end = 0
    while end < min(len(a), len(b)) - start and a[len(a) - 1 - end] == b[len(b) - 1 - end]:
        end += 1
    join = " ".join if spaced else "".join
    return join(a[start:len(a) - end]), join(b[start:len(b) - end])
