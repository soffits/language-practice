"""Recording one attempt: a text answer, a voice clip, or an objective set; plus feedback and errors."""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from . import clock, errors as errmod, formats, speech, tracks
from .store import Language, PracticeError, read_jsonl, write_json
from .text import count_units

AUDIO_SUFFIXES = {".ogg", ".oga", ".opus", ".mp3", ".m4a", ".wav", ".flac", ".aac", ".webm", ".mp4"}


def load_errors(source: str | None) -> list[dict[str, Any]]:
    """Errors from a JSON file (`-` reads stdin): a list, or an object with an `errors` list."""
    if not source:
        return []
    text = sys.stdin.read() if source == "-" else Path(source).expanduser().read_text(encoding="utf-8")
    data = json.loads(text)
    items = data.get("errors") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise PracticeError("the errors file must hold a JSON list (or an object with an 'errors' list)")
    return items


def next_attempt_id(language: Language, session_id: str | None, now: datetime) -> str:
    stem = session_id or f"{now:%Y%m%d-%H%M%S}-{language.code}"
    number = 1
    while language.attempt_dir(f"{stem}-a{number}").exists():
        number += 1
    return f"{stem}-a{number}"


def open_session_id(language: Language) -> str | None:
    opened = [s["id"] for s in language.sessions() if s["status"] == "open"]
    return opened[-1] if len(opened) == 1 else None


def lesson_reference(language: Language, lesson_id: str, item_ids: list[str] | None) -> tuple[dict[str, Any], str]:
    """The lesson and the text the learner was meant to repeat: the chosen items (default all), in lesson order."""
    lesson = tracks.find_lesson(tracks.load_tracks(language.learner.root), lesson_id)
    if lesson is None:
        raise PracticeError(f"unknown lesson '{lesson_id}'; see: practice lesson list")
    chosen = [i for i in lesson["items"] if not item_ids or i["id"] in item_ids]
    if not chosen:
        raise PracticeError(f"no item {', '.join(item_ids or [])} in lesson {lesson_id}")
    return lesson, " ".join(" ".join(tracks.spoken(i)) for i in chosen)


def add(language: Language, *, now: datetime, session: str | None, item: str | None, skill: str | None,
        task_id: str | None, variant: str, text: str | None, audio: Path | None, correct: int | None, total: int | None,
        prompt: str | None, minutes: float | None, reference: str | None, speech_json: Path | None,
        no_speech: bool, feedback: str | None, errors: list[dict[str, Any]], estimate: str | None,
        note: str | None, lesson: str | None = None, lesson_items: list[str] | None = None) -> dict[str, Any]:
    exams = formats.load_exams(language.learner.root)
    task = formats.find_task(task_id, exams) if task_id else None
    if task_id and task is None:
        raise PracticeError(f"unknown task '{task_id}'; see: practice formats")
    if lesson:
        if audio is None:
            raise PracticeError("a lesson repeat is a voice clip: give --audio")
        _, wanted = lesson_reference(language, lesson, lesson_items)
        reference, skill = reference or wanted, skill or "speaking"
    skill = skill or (task["skill"] if task else None)
    if skill not in formats.SKILLS:
        raise PracticeError(f"give --skill ({', '.join(formats.SKILLS)}) or a --task that implies it")
    if session is None:
        session = open_session_id(language)
    elif not language.session_path(session).exists():
        raise PracticeError(f"no session '{session}'")
    if prompt is None and item and session:
        planned = next((i for i in language.session(session)["plan"]["items"] if i["id"] == item), None)
        prompt = (planned or {}).get("prompt")
    given = [x for x in (text, audio, correct) if x not in (None, "")]
    if len(given) != 1:
        raise PracticeError("give exactly one of --text/--text-file, --audio, or --correct with --total")
    kind = "voice" if audio else ("objective" if correct is not None else "text")
    attempt_id = next_attempt_id(language, session, now)
    folder = language.attempt_dir(attempt_id)
    folder.mkdir(parents=True, exist_ok=True)
    attempt: dict[str, Any] = {
        "id": attempt_id, "lang": language.code, "session": session, "item": item, "skill": skill, "task": task_id,
        "variant": variant, "kind": kind, "created": clock.iso(now), "minutes": minutes, "prompt": prompt,
        "metrics": {}, "errors": [], "estimate": parse_estimate(estimate), "note": note,
        **({"lesson": lesson, "lesson_items": lesson_items or []} if lesson else {}),
    }
    if kind == "text":
        (folder / "answer.txt").write_text(text, encoding="utf-8")
        attempt["answer_file"] = "answer.txt"
        attempt["word_count"] = count_units(text)
        attempt["metrics"]["words"] = attempt["word_count"]
        words = (task or {}).get("words")
        if words and variant == "full":
            attempt["within_word_range"] = words.get("min", 0) <= attempt["word_count"] <= words.get("max", 10**9)
    elif kind == "objective":
        if not total or correct < 0 or correct > total:
            raise PracticeError("--correct needs --total, and correct must be between 0 and total")
        attempt["objective"] = {"correct": correct, "total": total}
        attempt["metrics"]["accuracy"] = round(correct / total, 3)
    else:
        if not audio.exists() or audio.suffix.lower() not in AUDIO_SUFFIXES:
            raise PracticeError(f"audio file not found or not an audio type: {audio}")
        attempt["audio_file"] = None
        if language.learner.config["speech"]["keep_audio"]:
            kept = folder / f"audio{audio.suffix.lower()}"
            shutil.copyfile(audio, kept)
            attempt["audio_file"] = kept.name
        attempt["speech"] = {"status": "pending", "reference": reference}
    language.save_attempt(attempt)
    if kind == "voice":
        analyze(language, attempt_id, now=now, source=audio, speech_json=speech_json, skip=no_speech)
    return finish_feedback(language, attempt_id, now=now, feedback=feedback, errors=errors)


def parse_estimate(text: str | None) -> dict[str, Any] | None:
    """An estimate is a range the assistant gives ('7-8' or '7'); it is information, never an input to points."""
    if not text:
        return None
    low, _, high = text.replace("–", "-").partition("-")
    try:
        return {"low": float(low), "high": float(high or low)}
    except ValueError as exc:
        raise PracticeError("estimate must be a number or a range such as 7-8") from exc


def analyze(language: Language, attempt_id: str, *, now: datetime, source: Path | None = None,
            speech_json: Path | None = None, skip: bool = False) -> dict[str, Any]:
    """Run speech-check on the attempt's audio (or import a result already made) and update metrics and sound errors."""
    attempt = language.attempt(attempt_id)
    if attempt["kind"] != "voice":
        raise PracticeError("only voice attempts can be analysed")
    folder = language.attempt_dir(attempt_id)
    reference = attempt.get("speech", {}).get("reference")
    result, error = None, None
    if skip:
        attempt["speech"] = {"status": "skipped", "reference": reference}
    else:
        try:
            if speech_json:
                result = json.loads(Path(speech_json).expanduser().read_text(encoding="utf-8"))
            else:
                audio = source or (folder / attempt["audio_file"] if attempt.get("audio_file") else None)
                if audio is None:
                    raise PracticeError("the audio was not kept; pass the clip again with add --audio")
                with language.released():
                    result = speech.run(audio, language.code, language.learner.config["speech"], reference)
        except PracticeError as exc:
            error = str(exc)
            attempt["speech"] = {"status": "failed", "error": error, "reference": reference}
    if result is not None:
        write_json(folder / "speech-check.json", result)
        task = formats.find_task(attempt["task"], formats.load_exams(language.learner.root)) if attempt.get("task") else None
        attempt["metrics"] = {**attempt["metrics"], **speech.metrics(result, (task or {}).get("response_s"))}
        text = speech.transcript_text(result)
        if text:
            (folder / "answer.txt").write_text(text, encoding="utf-8")
            attempt["answer_file"] = "answer.txt"
            attempt["word_count"] = int(attempt["metrics"].get("words", count_units(text)))
        attempt["speech"] = {"status": "ok", "file": "speech-check.json", "reference": reference,
                             "tool": result.get("tool"), "warnings": (result.get("recording") or {}).get("warnings", []),
                             "reliability": speech.reliability(result, language.code)}
        if attempt.get("lesson"):
            attempt["speech"]["repeat"] = speech.repeat_feedback(result, language.code)
        sounds = errmod.record_sound_patterns(language, attempt, [] if attempt.get("lesson") else speech.repeated_patterns(result), now)
        attempt["errors"] = sorted(set(attempt["errors"]) | {r["id"] for r in read_jsonl(language.errors_path)
                                                              if r["attempt"] == attempt_id})
        attempt["speech"]["sound_cards_new"] = sounds["cards_new"]
    language.save_attempt(attempt)
    return attempt


def finish_feedback(language: Language, attempt_id: str, *, now: datetime, feedback: str | None,
                    errors: list[dict[str, Any]]) -> dict[str, Any]:
    """Store the assistant's feedback text and errors for an attempt; errors become cards. Safe to repeat."""
    attempt = language.attempt(attempt_id)
    folder = language.attempt_dir(attempt_id)
    if feedback:
        (folder / "feedback.md").write_text(feedback.rstrip() + "\n", encoding="utf-8")
        attempt["feedback_file"] = "feedback.md"
    logged = errmod.record(language, attempt, errors, now) if errors else {"recorded": 0, "ids": [], "cards_new": [], "cards_recurring": []}
    rows = [r for r in read_jsonl(language.errors_path) if r["attempt"] == attempt_id]
    attempt["errors"] = [r["id"] for r in rows]
    own = [r for r in rows if r["source"] == "assistant"]
    words = attempt.get("word_count") or 0
    if own and words and attempt["kind"] != "objective":
        attempt["metrics"]["errors_per_100_words"] = round(len(own) * 100 / words, 2)
    language.save_attempt(attempt)
    return {"attempt": attempt, "errors": logged}
