"""The error log: what went wrong, where, how often. Every error with a usable correction becomes a review card."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from . import clock, deck as deckmod
from .store import Language, PracticeError, append_jsonl, read_jsonl
from .text import normalize

ERROR_FIELDS = ("category", "pattern", "wrong", "right", "sentence", "corrected", "note", "card", "skill")


def clean(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PracticeError("each error must be a JSON object with at least wrong and right")
    unknown = set(raw) - set(ERROR_FIELDS)
    if unknown:
        raise PracticeError(f"unknown error field(s) {sorted(unknown)}; allowed: {', '.join(ERROR_FIELDS)}")
    error = {k: (v.strip() if isinstance(v, str) else v) for k, v in raw.items() if v not in (None, "")}
    if not (error.get("wrong") or error.get("right") or error.get("note")):
        raise PracticeError("an error needs wrong, right or note")
    error["category"] = str(error.get("category", "other")).lower().replace(" ", "_")
    if error.get("card", "auto") not in ("auto", "cloze", "correction", "none"):
        raise PracticeError("card must be auto, cloze, correction or none")
    return error


def _same(row: dict[str, Any], error: dict[str, Any], attempt_id: str) -> bool:
    return (row["attempt"] == attempt_id and row["category"] == error["category"]
            and normalize(row.get("wrong", "")) == normalize(error.get("wrong", ""))
            and normalize(row.get("right", "")) == normalize(error.get("right", "")))


def record(language: Language, attempt: dict[str, Any], raw_errors: list[dict[str, Any]],
           now: datetime, source: str = "assistant") -> dict[str, Any]:
    """Log errors for an attempt and put their cards on the deck. Repeating the same call changes nothing."""
    existing = read_jsonl(language.errors_path)
    deck = deckmod.Deck(language)
    rows, new_cards, recurring = [], [], []
    counter = sum(1 for row in existing if row["attempt"] == attempt["id"])
    for raw in raw_errors:
        error = clean(raw)
        if any(_same(row, error, attempt["id"]) for row in existing + rows):
            continue
        counter += 1
        card = deckmod.build_from_error(language.code, error, now)
        error.pop("card", None)
        skill = error.pop("skill", attempt["skill"])
        row = {"id": f"{attempt['id']}-e{counter}", "ts": clock.iso(now), "lang": language.code,
               "attempt": attempt["id"], "session": attempt.get("session"), "skill": skill,
               "task": attempt.get("task"), "source": source, **error, "card": None}
        if card is not None:
            stored, is_new = deck.add(card, now)
            row["card"] = stored["id"]
            (new_cards if is_new else recurring).append(stored["id"])
        rows.append(row)
    if rows:
        append_jsonl(language.errors_path, rows)
        deck.save()
    return {"recorded": len(rows), "ids": [r["id"] for r in rows], "cards_new": new_cards, "cards_recurring": recurring}


def record_sound_patterns(language: Language, attempt: dict[str, Any], patterns: list[dict[str, Any]],
                          now: datetime) -> dict[str, Any]:
    """Repeated wrong sounds from speech-check: logged as errors, with a say-it card for each new sound."""
    existing = read_jsonl(language.errors_path)
    deck = deckmod.Deck(language)
    rows, new_cards, recurring = [], [], []
    for item in patterns:
        pattern = f"{item['expected']}>{item['heard']}"
        if any(r["attempt"] == attempt["id"] and r.get("pattern") == pattern for r in existing):
            continue
        card, is_new = deck.add(deckmod.build_say_card(language.code, item["expected"], item["heard"], item["words"], now), now)
        (new_cards if is_new else recurring).append(card["id"])
        rows.append({"id": f"{attempt['id']}-s{len(rows) + 1}", "ts": clock.iso(now), "lang": language.code,
                     "attempt": attempt["id"], "session": attempt.get("session"), "skill": "speaking",
                     "task": attempt.get("task"), "source": "speech-check", "category": "pronunciation.sound",
                     "pattern": pattern, "wrong": item["heard"], "right": item["expected"],
                     "note": "words: " + ", ".join(item["words"]), "card": card["id"]})
    if rows:
        append_jsonl(language.errors_path, rows)
        deck.save()
    return {"recorded": len(rows), "cards_new": new_cards, "cards_recurring": recurring}


def summary(errors: list[dict[str, Any]], min_repeat: int = 2) -> dict[str, Any]:
    """Totals per category and the patterns that keep coming back (two or more attempts)."""
    by_category: dict[str, int] = defaultdict(int)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in errors:
        by_category[row["category"]] += 1
        groups[row.get("pattern") or row["category"]].append(row)
    recurring = []
    for key, rows in groups.items():
        attempts = {r["attempt"] for r in rows}
        if len(attempts) >= min_repeat:
            recurring.append({
                "pattern": key, "category": rows[-1]["category"], "count": len(rows), "attempts": len(attempts),
                "first_seen": rows[0]["ts"], "last_seen": rows[-1]["ts"], "skills": sorted({r["skill"] for r in rows}),
                "examples": [f"{r.get('wrong', '')} → {r.get('right', '')}" for r in rows[-3:]],
            })
    recurring.sort(key=lambda r: (-r["attempts"], -r["count"]))
    return {"total": len(errors), "by_category": dict(sorted(by_category.items(), key=lambda kv: -kv[1])), "recurring": recurring}
