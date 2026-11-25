"""The review deck: cards generated from the learner's own errors, scheduled with FSRS (py-fsrs)."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any

from fsrs import Card, Rating, Scheduler

from . import audio as audiomod, clock
from .store import Language, PracticeError, append_jsonl, read_jsonl, write_jsonl
from .tracks import pick as pick_text
from .text import cloze_answers, cloze_back, cloze_front, count_units, make_cloze, minimal_change, normalize

RATINGS = {"again": Rating.Again, "hard": Rating.Hard, "good": Rating.Good, "easy": Rating.Easy,
           "1": Rating.Again, "2": Rating.Hard, "3": Rating.Good, "4": Rating.Easy}
STATES = {1: "learning", 2: "review", 3: "relearning"}
MATURE_DAYS = 21
CLOZE_MAX_UNITS = 4


def scheduler(config: dict[str, Any]) -> Scheduler:
    settings = config["fsrs"]
    return Scheduler(
        desired_retention=settings["desired_retention"],
        learning_steps=tuple(timedelta(minutes=m) for m in settings["learning_steps_minutes"]),
        relearning_steps=tuple(timedelta(minutes=m) for m in settings["relearning_steps_minutes"]),
        maximum_interval=settings["maximum_interval_days"],
        enable_fuzzing=settings["fuzzing"],
    )


def card_key(kind: str, wrong: str, right: str) -> str:
    return hashlib.sha1(f"{kind}|{normalize(wrong)}|{normalize(right)}".encode()).hexdigest()


def new_card(lang: str, kind: str, key: str, now: datetime, **fields: Any) -> dict[str, Any]:
    fsrs_card = Card(due=now)
    return {
        "id": f"c-{key[:10]}",
        "key": key,
        "kind": kind,
        "lang": lang,
        **fields,
        "created": clock.iso(now),
        "occurrences": 1,
        "suspended": False,
        "fsrs": fsrs_card.to_dict(),
    }


def build_from_error(lang: str, error: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    """One card for one error. `error["card"]` may force cloze, correction or none."""
    wrong, right = error.get("wrong") or "", error.get("right") or ""
    sentence, corrected = error.get("sentence") or "", error.get("corrected") or ""
    wanted = error.get("card", "auto")
    if wanted == "none" or not (wrong or right):
        return None
    core_wrong, core_right = minimal_change(wrong, right) if wrong and right else (wrong, right)
    answer = core_right or right
    cloze = (make_cloze(corrected, answer) or make_cloze(corrected, right)) if corrected and right else None
    short = answer and count_units(answer) <= CLOZE_MAX_UNITS
    kind = wanted if wanted in ("cloze", "correction") else ("cloze" if cloze and short else "correction")
    if kind == "cloze" and not cloze:
        kind = "correction"
    hint = error.get("note") or error.get("category") or ""
    common = {"category": error.get("category", "other"), "pattern": error.get("pattern"), "hint": hint,
              "wrong": core_wrong or wrong}
    if kind == "cloze":
        return new_card(lang, "cloze", card_key("cloze", wrong, right), now, text=cloze, **common)
    front = sentence or wrong
    back = corrected or right
    if not back:
        return None
    return new_card(lang, "correction", card_key("correction", wrong, right), now, front=front, back=back, **common)


def build_say_card(lang: str, expected: str, heard: str, words: list[str], now: datetime) -> dict[str, Any]:
    front = f"Say these aloud, listening for /{expected}/ (it was heard as /{heard}/): {', '.join(words)}"
    return new_card(lang, "say", card_key("say", heard, expected), now, front=front, back=f"/{expected}/",
                    category="pronunciation.sound", pattern=f"{expected}>{heard}", hint="")


LISTEN_PROMPTS = {"hear": "Listen. What is it?", "say": "Say it aloud, then listen to check.", "dictate": "Listen and write it down.",
                  "pick": "Listen. Which one did you hear?"}


def _audio(card: dict[str, Any], side: str, language: Any) -> dict[str, Any]:
    clips = audiomod.clips(language, card["say"]) if language is not None else {"file": None, "slow_file": None}
    return {"text": card["say"], "side": side, **clips}


def present(card: dict[str, Any], with_answer: bool = True, language: Any = None, languages: list[str] | None = None) -> dict[str, Any]:
    """The review view of a card: what to ask and what the answer is. Audio cards carry `audio` (the text and, when
    built, the clip paths relative to the language folder); the page plays it on the side named by `audio.side`."""
    languages = languages or ["zh", "en"]
    extra: dict[str, Any] = {}
    if card["kind"] == "cloze":
        prompt, answer = cloze_front(card["text"]), ", ".join(cloze_answers(card["text"]))
        full = cloze_back(card["text"])
    elif card["kind"] == "correction":
        prompt, answer, full = f"Correct this: {card['front']}", card["back"], card["back"]
    elif card["kind"] == "listen":
        mode = card["mode"]
        gloss = pick_text(card.get("gloss"), languages)
        prompt = (card.get("show") or gloss) if mode == "say" and (card.get("show") or gloss) else LISTEN_PROMPTS[mode]
        answer = card["answer"]
        full = f"{answer} {gloss}".strip() if mode == "hear" else answer
        extra = {"mode": mode, "audio": _audio(card, "back" if mode == "say" else "front", language), "choices": card.get("choices"),
                 "show": card.get("show"), "gloss": gloss, "lesson": card.get("lesson")}
    elif card["kind"] == "word":
        gloss = pick_text(card["gloss"], languages)
        example = card.get("example") or {}
        prompt = card["say"]
        answer = gloss
        full = gloss + (f"\n{example['text']} {pick_text(example.get('gloss'), languages)}".rstrip() if example else "")
        extra = {"mode": "recognise", "audio": _audio(card, "front", language), "gloss": gloss, "pos": card.get("pos"),
                 "word": card["word"], "rank": card.get("rank"), "track": card.get("track"),
                 "example": {"text": example["text"], "gloss": pick_text(example.get("gloss"), languages),
                             **(audiomod.clips(language, example["text"]) if language is not None else {})} if example else None}
    else:
        prompt, answer, full = card["front"], card.get("back", ""), card.get("back", "")
    view = {"id": card["id"], "kind": card["kind"], "category": card.get("category"), "hint": pick_text(card.get("hint"), languages),
            "prompt": prompt, "occurrences": card.get("occurrences", 1), "you_wrote": card.get("wrong") or None,
            "new": card["fsrs"].get("last_review") is None, **extra}
    if with_answer:
        view.update(answer=answer, full=full)
    return view


def fsrs_card(card: dict[str, Any]) -> Card:
    return Card.from_dict(card["fsrs"])


def due_at(card: dict[str, Any]) -> datetime:
    return clock.parse(card["fsrs"]["due"])


class Deck:
    def __init__(self, language: Language) -> None:
        self.language = language
        self.cards = read_jsonl(language.deck_path)

    def save(self) -> None:
        write_jsonl(self.language.deck_path, self.cards)

    def get(self, card_id: str) -> dict[str, Any]:
        for card in self.cards:
            if card["id"] == card_id:
                return card
        raise PracticeError(f"no card '{card_id}' in language '{self.language.code}'")

    def add(self, card: dict[str, Any], now: datetime) -> tuple[dict[str, Any], bool]:
        """Add a card; a card with the same key is a recurrence: counted, unsuspended and made due now."""
        for existing in self.cards:
            if existing["key"] == card["key"]:
                existing["occurrences"] = existing.get("occurrences", 1) + 1
                existing["suspended"] = False
                existing["fsrs"]["due"] = clock.iso(min(due_at(existing), now))
                return existing, False
        self.cards.append(card)
        return card, True

    def due(self, now: datetime, tz: str | None, limit: int | None = None, include_today: bool = True) -> list[dict[str, Any]]:
        horizon = clock.end_of_local_day(now, tz) if include_today else now
        sched = scheduler({"fsrs": {"desired_retention": 0.9, "learning_steps_minutes": [], "relearning_steps_minutes": [],
                                    "maximum_interval_days": 180, "fuzzing": False}})
        due = [c for c in self.cards if not c["suspended"] and due_at(c) < horizon]
        due.sort(key=lambda c: (sched.get_card_retrievability(fsrs_card(c), now), due_at(c)))
        return due[:limit] if limit else due

    def grade(self, card_id: str, rating_name: str, config: dict[str, Any], now: datetime,
              session: str | None = None, answer: str | None = None) -> dict[str, Any]:
        rating = RATINGS.get(rating_name.lower())
        if rating is None:
            raise PracticeError(f"rating must be again, hard, good or easy (or 1-4), not '{rating_name}'")
        card = self.get(card_id)
        sched = scheduler(config)
        before = fsrs_card(card)
        retrievability = sched.get_card_retrievability(before, now) if before.last_review else None
        updated, log = sched.review_card(before, rating, now)
        card["fsrs"] = updated.to_dict()
        row = {"ts": clock.iso(now), "card": card_id, "rating": int(rating), "rating_name": rating.name.lower(),
               "retrievability_before": retrievability, "session": session, "answer": answer, "fsrs_log": log.to_dict()}
        append_jsonl(self.language.reviews_path, [row])
        return {"card": card_id, "rating": rating.name.lower(), "next_due": card["fsrs"]["due"],
                "state": STATES.get(updated.state.value, "new"), "stability_days": updated.stability}

    def stats(self, now: datetime, tz: str | None) -> dict[str, Any]:
        by_state = {"learning": 0, "review": 0, "relearning": 0}
        mature = 0
        for card in self.cards:
            state = card["fsrs"]["state"]
            by_state[STATES.get(state, "learning")] += 1
            if (card["fsrs"].get("stability") or 0) >= MATURE_DAYS and state == 2:
                mature += 1
        return {
            "total": len(self.cards),
            "due_now": len(self.due(now, tz, include_today=False)),
            "due_today": len(self.due(now, tz)),
            "suspended": sum(1 for c in self.cards if c["suspended"]),
            "mature": mature,
            "by_state": by_state,
        }
