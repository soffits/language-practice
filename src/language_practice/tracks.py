"""Learning tracks are data: lesson tracks (units of short listen-and-repeat lessons) and word lists.

Packaged under `data/tracks/<id>/`, overridable or extendable per learner in `<data>/tracks/<id>/`:

    track.json            id, name, lang (what is learned), explain (languages the explanations are written in), kind
    units/NN-name.json    kind "lessons": {id, title, lessons: [...]}, in file-name order
    words.json            kind "words": {words: [...]}, in frequency order

Text fields are a plain string (the same in every language) or an object keyed by language code ({"zh": ..., "en": ...}).
"""
from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

from .store import PracticeError
from .text import nfc

KINDS = ("lessons", "words")
DRILLS = ("hear", "say", "dictate", "pick")


def pick(value: Any, languages: list[str]) -> str:
    """A text field in the first of `languages` that has it; plain strings are the same everywhere."""
    if isinstance(value, dict):
        for code in languages:
            if value.get(code):
                return value[code]
        return next(iter(value.values()), "")
    return value or ""


def _read(path: Any) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PracticeError(f"cannot read track file {path}: {exc}") from exc


def _load_folder(folder: Any) -> dict[str, Any]:
    track = _read(folder / "track.json")
    problems = [key for key in ("track", "name", "lang", "kind") if not track.get(key)]
    if problems or track["kind"] not in KINDS:
        raise PracticeError(f"track in {folder}: needs {', '.join(problems) or 'kind lessons or words'}")
    track.setdefault("explain", ["en"])
    if track["kind"] == "words":
        track["words"] = _read(folder / "words.json")["words"]
        return validate_words(track)
    units = [_read(entry) for entry in sorted((folder / "units").iterdir(), key=lambda p: p.name) if entry.name.endswith(".json")]
    track["units"] = units
    return validate_lessons(track)


def validate_words(track: dict[str, Any]) -> dict[str, Any]:
    seen: set[str] = set()
    for rank, word in enumerate(track["words"], 1):
        word_id = word.get("id") or f"{track['track']}:{rank}"
        if not word.get("word") or not word.get("gloss") or word_id in seen:
            raise PracticeError(f"track {track['track']}: word {rank} needs a unique id, `word` and `gloss`")
        seen.add(word_id)
        word["id"], word["rank"] = word_id, rank
    return track


def validate_lessons(track: dict[str, Any]) -> dict[str, Any]:
    seen: set[str] = set()
    for unit in track["units"]:
        if not unit.get("id") or not unit.get("lessons"):
            raise PracticeError(f"track {track['track']}: every unit needs an id and lessons")
        for lesson in unit["lessons"]:
            lesson_id = lesson.get("id", "")
            if not lesson_id or lesson_id in seen or not lesson.get("items") or not lesson.get("minutes"):
                raise PracticeError(f"track {track['track']}: lesson '{lesson_id}' needs a unique id, minutes and items")
            seen.add(lesson_id)
            lesson.update(unit=unit["id"], track=track["track"], lang=track["lang"], explain_languages=track["explain"])
            ids: set[str] = set()
            for item in lesson["items"]:
                item_id = str(item.get("id", ""))
                if not item_id or item_id in ids or not (item.get("say") or item.get("pair")):
                    raise PracticeError(f"lesson {lesson_id}: item '{item_id}' needs a unique id and `say` or `pair`")
                ids.add(item_id)
                item["id"] = item_id
                unknown = [d for d in item.get("drill", []) if d not in DRILLS]
                if unknown or ("pick" in item.get("drill", []) and not item.get("pair")):
                    raise PracticeError(f"lesson {lesson_id}, item {item_id}: bad drill {unknown or 'pick needs a pair'}")
    return track


def load_tracks(data_root: Path | None = None) -> dict[str, dict[str, Any]]:
    tracks: dict[str, dict[str, Any]] = {}
    folder = resources.files("language_practice") / "data" / "tracks"
    for entry in sorted(folder.iterdir(), key=lambda p: p.name) if folder.is_dir() else []:
        if entry.is_dir():
            track = _load_folder(entry)
            tracks[track["track"]] = track
    local = data_root / "tracks" if data_root else None
    if local and local.exists():
        for entry in sorted(local.iterdir()):
            if entry.is_dir():
                track = _load_folder(entry)
                tracks[track["track"]] = track
    return tracks


def get(tracks: dict[str, dict[str, Any]], track_id: str, kind: str) -> dict[str, Any]:
    track = tracks.get(track_id)
    if track is None or track["kind"] != kind:
        known = [t for t, v in tracks.items() if v["kind"] == kind]
        raise PracticeError(f"no {kind} track '{track_id}'; known: {', '.join(known) or 'none'}")
    return track


def lessons_of(track: dict[str, Any]) -> list[dict[str, Any]]:
    return [lesson for unit in track["units"] for lesson in unit["lessons"]]


def find_lesson(tracks: dict[str, dict[str, Any]], lesson_id: str) -> dict[str, Any] | None:
    for track in tracks.values():
        if track["kind"] == "lessons":
            for lesson in lessons_of(track):
                if lesson["id"] == lesson_id:
                    return lesson
    return None


def spoken(item: dict[str, Any]) -> list[str]:
    """What is said aloud for an item: its sentence, or both words of a minimal pair."""
    return [nfc(t) for t in (item["pair"] if item.get("pair") else [item["say"]])]
