"""Word lists and the learner's relation to them: which words became cards, which are known, and what to read next.

A words track (`kind: words`) is an ordered list; the learner meets it in small daily batches that become `word` cards in
the FSRS deck. Readings are recorded with the words they introduced, so a tutor can ask for a topic and a target set of
words the learner has not met yet. Everything is derived from the deck and `readings.jsonl`.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from . import audio, clock, tracks as trackmod
from .deck import Deck, new_card
from .store import Language, PracticeError, append_jsonl, read_jsonl
from .text import nfc, normalize

KNOWN_STABILITY_DAYS = 4
DEFAULT_NEW_PER_DAY = 10
DREADED_NEW_PER_DAY = 8
DEFAULT_BAND = 100
DEFAULT_PER_READING = 5


def readings_path(language: Language):
    return language.dir / "readings.jsonl"


def display(word: dict[str, Any]) -> str:
    article = word.get("article") or ""
    if not article:
        return word["word"]
    return article + word["word"] if article.endswith(("'", "’")) else f"{article} {word['word']}"


def card_key(lang: str, word: str, track: str | None) -> str:
    return hashlib.sha1(f"word|{lang}|{track or ''}|{normalize(word)}".encode()).hexdigest()


def build_card(lang: str, word: dict[str, Any], track: str | None, now: datetime, source: str = "words") -> dict[str, Any]:
    text = display(word)
    card = new_card(lang, "word", card_key(lang, word["word"], track), now, word_id=word.get("id"), track=track,
                    rank=word.get("rank"), word=word["word"], article=word.get("article"), say=nfc(text), pos=word.get("pos"),
                    gloss=word["gloss"], example=word.get("example"), source=source,
                    category=f"vocabulary.{source}", pattern=None, hint="")
    return card


def settings(profile: dict[str, Any]) -> dict[str, Any]:
    return profile.get("foundations") or {}


def words_track(language: Language, profile: dict[str, Any], all_tracks: dict[str, dict[str, Any]],
                track_id: str | None = None) -> dict[str, Any] | None:
    chosen = track_id or settings(profile).get("words") or (profile.get("reading") or {}).get("words")
    return trackmod.get(all_tracks, chosen, "words") if chosen else None


def cap_per_day(profile: dict[str, Any]) -> int:
    configured = settings(profile).get("new_per_day")
    return int(configured) if configured is not None else (DREADED_NEW_PER_DAY if profile.get("dreaded") else DEFAULT_NEW_PER_DAY)


def _deck_words(deck: Deck, track_id: str) -> list[dict[str, Any]]:
    return [c for c in deck.cards if c["kind"] == "word" and c.get("track") == track_id]


def new_today(deck: Deck, track_id: str, now: datetime, tz: str) -> int:
    today = clock.local_date(now, tz)
    return sum(1 for c in _deck_words(deck, track_id) if clock.local_date(clock.parse(c["created"]), tz) == today)


def is_known(card: dict[str, Any]) -> bool:
    fsrs = card["fsrs"]
    return fsrs["state"] == 2 and (fsrs.get("stability") or 0) >= KNOWN_STABILITY_DAYS


def room_for_new(language: Language, profile: dict[str, Any], track: dict[str, Any], deck: Deck, now: datetime,
                 due_total: int, due_shown: int, affordable: int) -> int:
    """How many new words to start today: under the daily cap, under what the minutes can carry after the due cards,
    and nothing while the due pile is already as big as a review block."""
    tz = language.learner.timezone
    met = {c["word_id"] for c in _deck_words(deck, track["track"])}
    left = sum(1 for w in track["words"] if w["id"] not in met)
    if due_total >= profile["review_cap"]:
        return 0
    return max(0, min(cap_per_day(profile) - new_today(deck, track["track"], now, tz), affordable - due_shown, left))


def introduce(language: Language, track: dict[str, Any], count: int, now: datetime) -> list[dict[str, Any]]:
    """Turn the next `count` words of the list (by rank) into cards due now. Builds their audio."""
    with language.lock():
        deck = Deck(language)
        met = {c["word_id"] for c in _deck_words(deck, track["track"])}
        fresh = [build_card(track["lang"], w, track["track"], now) for w in track["words"] if w["id"] not in met][:count]
        deck.cards.extend(fresh)
        deck.save()
    audio.ensure(language, [(c["say"], False) for c in fresh] + [(c["example"]["text"], False) for c in fresh if c.get("example")])
    return fresh


def add_word_card(language: Language, track: dict[str, Any] | None, word: str, gloss: Any, article: str | None,
                  example: dict[str, Any] | None, source: str, now: datetime) -> tuple[dict[str, Any], bool]:
    """One word into the deck: from a words track (by id or spelling) or free-form (mined from a text or an error)."""
    entry = None
    if track:
        entry = next((w for w in track["words"] if w["id"] == word or normalize(w["word"]) == normalize(word)), None)
    if entry is None:
        if not gloss:
            raise PracticeError(f"'{word}' is not in the word list; give --gloss to add it as your own word")
        entry = {"word": word, "article": article, "gloss": gloss, "example": example}
    else:
        entry = {**entry, **({"example": example} if example else {})}
    card = build_card(language.code, entry, track["track"] if track and entry.get("id") else None, now, source)
    with language.lock():
        deck = Deck(language)
        for existing in deck.cards:
            if existing["key"] == card["key"]:
                return existing, False
        deck.cards.append(card)
        deck.save()
    audio.ensure(language, [(card["say"], False)] + ([(example["text"], False)] if example else []))
    return card, True


def read_readings(language: Language) -> list[dict[str, Any]]:
    return read_jsonl(readings_path(language))


def coverage(track: dict[str, Any], deck: Deck, readings: list[dict[str, Any]], band_size: int) -> dict[str, Any]:
    in_deck = {c["word_id"]: c for c in _deck_words(deck, track["track"])}
    read = {wid for r in readings for wid in r.get("new_words", [])}
    bands = []
    for start in range(0, len(track["words"]), band_size):
        chunk = track["words"][start:start + band_size]
        bands.append({"band": start // band_size + 1, "from": chunk[0]["rank"], "to": chunk[-1]["rank"], "total": len(chunk),
                      "in_deck": sum(1 for w in chunk if w["id"] in in_deck),
                      "known": sum(1 for w in chunk if w["id"] in in_deck and is_known(in_deck[w["id"]])),
                      "met": sum(1 for w in chunk if w["id"] in in_deck or w["id"] in read)})
    return {"in_deck": in_deck, "read": read, "bands": bands}


def status(language: Language, profile: dict[str, Any], track: dict[str, Any], now: datetime) -> dict[str, Any]:
    deck = Deck(language)
    tz = language.learner.timezone
    band_size = (profile.get("reading") or {}).get("band_size", DEFAULT_BAND)
    cover = coverage(track, deck, read_readings(language), band_size)
    left = [w for w in track["words"] if w["id"] not in cover["in_deck"]]
    return {"track": track["track"], "name": track["name"], "total": len(track["words"]), "in_deck": len(cover["in_deck"]),
            "known": sum(1 for c in cover["in_deck"].values() if is_known(c)), "new_today": new_today(deck, track["track"], now, tz),
            "cap_per_day": cap_per_day(profile), "remaining": len(left),
            "next": [{"id": w["id"], "word": display(w), "rank": w["rank"]} for w in left[:cap_per_day(profile)]],
            "bands": cover["bands"]}


def plan_reading(language: Language, profile: dict[str, Any], track: dict[str, Any], now: datetime) -> dict[str, Any]:
    """A topic (the interest read least recently) and the target words from the first band that is not mostly met."""
    reading = profile.get("reading") or {}
    interests = [i if isinstance(i, dict) else {"id": i, "label": i} for i in reading.get("interests", [])]
    domains = [d if isinstance(d, dict) else {"id": d, "label": d} for d in reading.get("domains", [])]
    past = read_readings(language)

    def stalest(options: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
        last = {}
        for row in past:
            if row.get(key):
                last[row[key]] = row["ts"]
        pick = min(options, key=lambda o: (last.get(o["id"], ""), options.index(o)), default=None)
        return {**pick, "last_read": last.get(pick["id"])} if pick else None

    band_size = reading.get("band_size", DEFAULT_BAND)
    per_reading = reading.get("new_per_reading", DEFAULT_PER_READING)
    deck = Deck(language)
    cover = coverage(track, deck, past, band_size)
    band = next((b for b in cover["bands"] if b["met"] < b["total"] * 0.9), cover["bands"][-1])
    unmet = [w for w in track["words"] if band["from"] <= w["rank"] <= band["to"] and w["id"] not in cover["in_deck"] and w["id"] not in cover["read"]]
    brief = lambda w: {"id": w["id"], "word": display(w), "gloss": trackmod.pick(w["gloss"], [reading.get("gloss_language", "zh"), "en"]), "rank": w["rank"]}
    known = [display(w) for w in track["words"] if w["id"] in cover["in_deck"] and is_known(cover["in_deck"][w["id"]])]
    seen = [display(w) for w in track["words"] if w["id"] in cover["in_deck"] or w["id"] in cover["read"]]
    return {"schema_version": 1, "lang": language.code, "generated": clock.iso(now), "readings_so_far": len(past),
            "topic": stalest(interests, "topic"), "domain": stalest(domains, "domain"),
            "words": {"track": track["track"], "band": band, "per_reading": per_reading,
                      "target": [brief(w) for w in unmet[:per_reading]], "pool": [brief(w) for w in unmet[:per_reading * 3]],
                      "known": known, "seen": seen},
            "recent": [{k: r.get(k) for k in ("id", "ts", "topic", "domain", "title")} for r in past[-5:]]}


def add_reading(language: Language, track: dict[str, Any] | None, now: datetime, *, topic: str | None, domain: str | None,
                title: str, text: str, source: str | None, new_words: list[str]) -> dict[str, Any]:
    ids = []
    for ref in new_words:
        entry = None
        if track:
            entry = next((w for w in track["words"] if w["id"] == ref or normalize(w["word"]) == normalize(ref)), None)
        ids.append(entry["id"] if entry else ref)
    number = len(read_readings(language)) + 1
    reading_id = f"{now:%Y%m%d}-r{number}"
    folder = language.dir / "readings"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{reading_id}.md").write_text(text.rstrip() + "\n", encoding="utf-8")
    row = {"id": reading_id, "ts": clock.iso(now), "topic": topic, "domain": domain, "title": title, "source": source,
           "new_words": ids, "file": f"readings/{reading_id}.md"}
    append_jsonl(readings_path(language), [row])
    return row
