"""Foundations: lessons for a learner who cannot yet use exam tasks. Which lesson is next, what was done, the cards a
lesson leaves in the deck, and when the learner moves on to exam tasks. All derived from the files."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from . import audio, clock, tracks as trackmod
from .deck import Deck, new_card
from .store import Language, PracticeError, append_jsonl, read_jsonl
from .text import nfc
import hashlib


def lessons_path(language: Language):
    return language.dir / "lessons.jsonl"


def config(profile: dict[str, Any]) -> dict[str, Any] | None:
    return profile.get("foundations") or None


def explain_languages(profile: dict[str, Any], track: dict[str, Any] | None = None) -> list[str]:
    """Languages to read explanations in: the learner's choice first, then the track's own order (lessons carry it)."""
    chosen = (config(profile) or {}).get("explain")
    own = list((track or {}).get("explain") or (track or {}).get("explain_languages") or [])
    return [chosen, *own] if chosen else (own or ["en"])


def completions(language: Language) -> list[dict[str, Any]]:
    return read_jsonl(lessons_path(language))


def lesson_tracks(profile: dict[str, Any], all_tracks: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    settings = config(profile)
    return [trackmod.get(all_tracks, t, "lessons") for t in (settings or {}).get("tracks", [])]


def status(language: Language, profile: dict[str, Any], all_tracks: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Where the learner is in the foundations lessons; None when this language has none configured."""
    settings = config(profile)
    if not settings:
        return None
    done = {row["lesson"] for row in completions(language)}
    chosen = lesson_tracks(profile, all_tracks)
    languages = explain_languages(profile, chosen[0] if chosen else None)
    units, remaining = [], []
    for track in chosen:
        for unit in track["units"]:
            ids = [lesson["id"] for lesson in unit["lessons"]]
            units.append({"track": track["track"], "id": unit["id"], "title": trackmod.pick(unit.get("title"), languages),
                          "done": sum(1 for i in ids if i in done), "total": len(ids)})
            remaining += [lesson for lesson in unit["lessons"] if lesson["id"] not in done]
    total = sum(u["total"] for u in units)
    complete = bool(units) and not remaining
    forced = settings.get("stage") == "exam"
    stage = "exam" if (forced or complete or not units) else "foundations"
    return {"stage": stage, "forced": forced, "lessons_done": total - len(remaining), "lessons_total": total, "complete": complete,
            "next_lesson": remaining[0]["id"] if remaining else None, "units": units,
            "words_track": settings.get("words")}


def next_lesson(language: Language, profile: dict[str, Any], all_tracks: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    state = status(language, profile, all_tracks)
    return trackmod.find_lesson(all_tracks, state["next_lesson"]) if state and state["next_lesson"] else None


def lesson_texts(lesson: dict[str, Any]) -> list[tuple[str, bool]]:
    """Every clip a lesson needs: each spoken text at normal speed, and slow when the item asks for it (default yes)."""
    wanted: list[tuple[str, bool]] = []
    for item in lesson["items"]:
        for text in trackmod.spoken(item):
            wanted.append((text, False))
            if item.get("slow", True):
                wanted.append((text, True))
    return wanted


def view(language: Language, lesson: dict[str, Any], profile: dict[str, Any], build_audio: bool = True) -> dict[str, Any]:
    languages = explain_languages(profile, lesson)
    result = audio.ensure(language, lesson_texts(lesson)) if build_audio else {"status": "skipped", "built": 0}
    items = []
    for item in lesson["items"]:
        spoken = trackmod.spoken(item)
        entry = {"id": item["id"], "say": item.get("say"), "show": item.get("show", item.get("say")),
                 "gloss": trackmod.pick(item.get("gloss"), languages), "hint": trackmod.pick(item.get("hint"), languages),
                 "pair": item.get("pair"), "drill": item.get("drill", [])}
        entry["audio"] = [{"text": t, **audio.clips(language, t)} for t in spoken]
        items.append(entry)
    done = [row for row in completions(language) if row["lesson"] == lesson["id"]]
    repeats = [{"id": a["id"], "created": a["created"], "items": a.get("lesson_items", []), "status": (a.get("speech") or {}).get("status"),
                "reliability": (a.get("speech") or {}).get("reliability"), "repeat": (a.get("speech") or {}).get("repeat")}
               for a in language.attempts() if a.get("lesson") == lesson["id"]][-5:]
    return {"id": lesson["id"], "track": lesson["track"], "unit": lesson["unit"], "lang": lesson["lang"],
            "title": trackmod.pick(lesson.get("title"), languages), "minutes": lesson["minutes"],
            "explain": trackmod.pick(lesson.get("explain"), languages), "items": items,
            "cards": len(cards_for(lesson, clock.now(), clock.now())), "times_done": len(done), "attempts": repeats,
            "audio_status": {k: result.get(k) for k in ("status", "built", "failed")}}


def _key(*parts: str) -> str:
    return hashlib.sha1("|".join(nfc(p) for p in parts).encode()).hexdigest()


def cards_for(lesson: dict[str, Any], now: datetime, due: datetime) -> list[dict[str, Any]]:
    """The deck cards a lesson leaves behind: one per item and drill (two for a minimal pair)."""
    cards = []
    for item in lesson["items"]:
        for mode in item.get("drill", []):
            if mode == "pick":
                words = sorted(item["pair"])
                specs = [{"say": word, "answer": word, "choices": words, "item": f"{item['id']}.{word}"} for word in words]
            else:
                shown = item.get("show", item.get("say"))
                specs = [{"say": item["say"], "answer": shown if mode in ("hear", "dictate") else item["say"],
                          "choices": None, "item": item["id"]}]
            for spec in specs:
                card = new_card(lesson["lang"], "listen", _key("listen", mode, lesson["id"], spec["item"]), now,
                                mode=mode, lesson=lesson["id"], item=spec["item"], track=lesson["track"], say=nfc(spec["say"]),
                                show=item.get("show") if mode == "say" else None, answer=nfc(spec["answer"]),
                                choices=spec["choices"], gloss=item.get("gloss"), hint=item.get("hint"),
                                category=f"foundations.{lesson['unit']}", pattern=None)
                card["fsrs"]["due"] = clock.iso(due)
                cards.append(card)
    return cards


def finish_lesson(language: Language, lesson: dict[str, Any], now: datetime, session: str | None, minutes: float | None) -> dict[str, Any]:
    """Record a lesson as done and put its cards in the deck, due from the next local day."""
    tz = language.learner.timezone
    due = clock.end_of_local_day(now, tz)
    with language.lock():
        deck = Deck(language)
        existing = {c["key"] for c in deck.cards}
        fresh = [c for c in cards_for(lesson, now, due) if c["key"] not in existing]
        deck.cards.extend(fresh)
        deck.save()
        if not session or not any(r["lesson"] == lesson["id"] and r.get("session") == session for r in completions(language)):
            append_jsonl(lessons_path(language), [{"ts": clock.iso(now), "lesson": lesson["id"], "unit": lesson["unit"],
                                                   "session": session, "minutes": minutes, "cards_added": len(fresh)}])
    audio.ensure(language, [(c["say"], False) for c in fresh])
    return {"lesson": lesson["id"], "cards_added": len(fresh), "cards_due_from": clock.iso(due)}
