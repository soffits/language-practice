"""A practice session: a plan, the attempts and reviews done under it, and the points it earned."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from . import clock, foundations, formats, planner, points, tracks as trackmod, words as wordsmod
from .deck import Deck
from .store import Language, PracticeError, read_jsonl


def new_id(language: Language, now: datetime) -> str:
    base = f"{now:%Y%m%d-%H%M%S}-{language.code}"
    candidate, number = base, 1
    while language.session_path(candidate).exists():
        number += 1
        candidate = f"{base}-{number}"
    return candidate


def explicit_plan(language: Language, task_ids: list[str], review: int | None, now: datetime,
                  lesson_ids: list[str] | None = None) -> dict[str, Any]:
    exams = formats.load_exams(language.learner.root)
    all_tracks = trackmod.load_tracks(language.learner.root)
    items: list[dict[str, Any]] = []
    if review:
        items.append({"id": "i1", "kind": "review", "cards": review, "minutes": max(1, review // 2), "why": "requested",
                      "hint": "practice review due, grade each answer"})
    for lesson_id in lesson_ids or []:
        lesson = trackmod.find_lesson(all_tracks, lesson_id)
        if lesson is None:
            raise PracticeError(f"unknown lesson '{lesson_id}'; see: practice lesson list")
        title = foundations.view(language, lesson, language.profile(), build_audio=False)["title"]
        items.append({"id": f"i{len(items) + 1}", "kind": "lesson", "lesson": lesson_id, "unit": lesson["unit"], "name": title,
                      "minutes": lesson["minutes"], "why": "requested", "hint": "practice lesson show " + lesson_id})
    for task_id in task_ids:
        task = formats.find_task(task_id, exams)
        if task is None:
            raise PracticeError(f"unknown task '{task_id}'; see: practice formats")
        items.append(planner.item_from_task(task, "full", "requested", len(items) + 1))
    return {"schema_version": 1, "lang": language.code, "generated": clock.iso(now), "requested": True,
            "minutes_available": None, "minutes_planned": sum(i["minutes"] for i in items), "items": items}


def introduce_new_words(language: Language, plan: dict[str, Any], now: datetime) -> None:
    """The review item's new words become cards when the session starts, so they are due and their audio is ready."""
    item = next((i for i in plan["items"] if i["kind"] == "review" and i.get("new")), None)
    if item:
        profile = language.profile()
        track = wordsmod.words_track(language, profile, trackmod.load_tracks(language.learner.root))
        item["new_cards"] = [c["id"] for c in wordsmod.introduce(language, track, item["new"], now)]


def start(language: Language, now: datetime, minutes: int | None = None, focus: str | None = None,
          tasks: list[str] | None = None, review: int | None = None, lessons: list[str] | None = None) -> dict[str, Any]:
    plan = (explicit_plan(language, tasks or [], review, now, lessons) if (tasks or review or lessons)
            else planner.build(language, minutes, now, focus))
    introduce_new_words(language, plan, now)
    session = {"id": new_id(language, now), "lang": language.code, "status": "open", "started": clock.iso(now),
               "finished": None, "plan": plan, "focused_minutes": None, "points": None}
    language.save_session(session)
    return session


def view(language: Language, session: dict[str, Any]) -> dict[str, Any]:
    """The session with live progress per plan item, derived from the attempts and reviews filed under it."""
    attempts = [a for a in language.attempts() if a.get("session") == session["id"]]
    graded = [r for r in read_jsonl(language.reviews_path) if r.get("session") == session["id"]]
    items = []
    for item in session["plan"]["items"]:
        if item["kind"] == "review":
            wanted = item["cards"] + item.get("new", 0)
            done = len(graded) >= wanted
            state = {"state": "done" if done else ("started" if graded else "todo"), "progress": f"{len(graded)}/{wanted}"}
        elif item["kind"] == "lesson":
            state = {"state": "done" if any(r["lesson"] == item["lesson"] and r.get("session") == session["id"]
                                          for r in foundations.completions(language)) else "todo"}
        elif item["kind"] == "task":
            mine = [a for a in attempts if a.get("item") == item["id"] or (not a.get("item") and a.get("task") == item["task"])]
            state = {"state": "done" if mine else "todo", "attempts": [a["id"] for a in mine]}
        else:
            state = {"state": "done" if graded and len(graded) > 0 else "todo"}
        items.append({**item, **state})
    return {**session, "plan": {**session["plan"], "items": items},
            "attempts": [a["id"] for a in attempts], "cards_reviewed": len(graded)}


def activity(language: Language, session_id: str) -> dict[str, Any]:
    attempts = [a for a in language.attempts() if a.get("session") == session_id]
    graded = [r for r in read_jsonl(language.reviews_path) if r.get("session") == session_id]
    exams = formats.load_exams(language.learner.root)
    full_writing = 0
    for attempt in attempts:
        task = formats.find_task(attempt["task"], exams) if attempt.get("task") else None
        minimum = ((task or {}).get("words") or {}).get("min", 0)
        if (attempt["skill"] == "writing" and attempt["kind"] == "text" and attempt.get("variant") == "full"
                and task and task.get("hard") and (attempt.get("word_count") or 0) >= minimum):
            full_writing += 1
    lessons = [r for r in foundations.completions(language) if r.get("session") == session_id]
    spent = sum(a["minutes"] for a in attempts if a.get("minutes")) + sum(r.get("minutes") or 0 for r in lessons)
    return {"attempts": len(attempts), "voice": sum(1 for a in attempts if a["kind"] == "voice"),
            "full_writing": full_writing, "cards": len(graded), "attempt_minutes": spent, "lessons": len(lessons)}


def finish(language: Language, session_id: str, now: datetime, focused_minutes: float | None = None,
           give_points: bool = True) -> dict[str, Any]:
    session = language.session(session_id)
    if session["status"] == "finished":
        return session
    if session["status"] != "open":
        raise PracticeError(f"session is {session['status']}; start a new one")
    profile, config = language.profile(), language.learner.config
    done = activity(language, session_id)
    if focused_minutes is None:
        planned = sum(i["minutes"] for i in view(language, session)["plan"]["items"] if i["state"] == "done")
        focused_minutes = done["attempt_minutes"] or planned
    session.update(status="finished", finished=clock.iso(now), focused_minutes=focused_minutes, activity=done)
    value = points.compute(config["points"], profile["dreaded"], focused_minutes, done["voice"], done["full_writing"], done["cards"])
    what = f"{points.language_name(language.code)} practice: " + ", ".join(
            part for part in (f"{done['lessons']} lesson(s)" if done["lessons"] else "",
                              f"{done['attempts']} attempt(s)" if done["attempts"] else "", f"{done['voice']} voice answer(s)" if done["voice"] else "",
                              f"{done['cards']} card(s) reviewed" if done["cards"] else "", f"{focused_minutes:g} min") if part)
    record = {"points": value["points"], "parts": value["parts"], "capped": value["capped"], "what": what}
    if give_points and value["points"] > 0:
        record.update(points.earn(config["rewards"], what, value["points"], session_id))
    else:
        record["status"] = "skipped" if value["points"] == 0 else "not_requested"
    session["points"] = record
    language.save_session(session)
    return session


def set_prompt(language: Language, session_id: str, item_id: str, text: str) -> dict[str, Any]:
    """Attach the tutor's prompt text to a task item of an open session, so a page or the learner can see it. Empty text removes it."""
    session = language.session(session_id)
    if session["status"] != "open":
        raise PracticeError(f"session is {session['status']}; prompts go on open sessions")
    item = next((i for i in session["plan"]["items"] if i["id"] == item_id), None)
    if item is None or item["kind"] != "task":
        raise PracticeError(f"no task item '{item_id}' in session {session_id}")
    if text.strip():
        item["prompt"] = text.strip()
    else:
        item.pop("prompt", None)
    language.save_session(session)
    return session


def retry_points(language: Language, session_id: str) -> dict[str, Any]:
    session = language.session(session_id)
    if session["status"] != "finished":
        raise PracticeError("only a finished session earns points")
    p = session["points"]
    session["points"] = {**p, **points.earn(language.learner.config["rewards"], p["what"], p["points"], session_id)}
    language.save_session(session)
    return session


def reopen(language: Language, session_id: str) -> dict[str, Any]:
    """Back to open: takes the points back from the ledger (by source), so a finish can be redone honestly."""
    session = language.session(session_id)
    if session["status"] != "finished":
        raise PracticeError("session is not finished")
    outcome = points.undo(language.learner.config["rewards"], session_id)
    session.update(status="open", finished=None, points={"status": "reopened", "undo": outcome})
    language.save_session(session)
    return session


def abandon(language: Language, session_id: str, now: datetime) -> dict[str, Any]:
    session = language.session(session_id)
    session.update(status="abandoned", finished=clock.iso(now))
    language.save_session(session)
    return session
