"""What to do today: due cards, the weakest component, the time available. Pure function of the learner's files."""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from . import clock, errors as errmod, foundations, formats, tracks as trackmod, words as wordsmod
from .deck import Deck
from .store import Language, PracticeError, read_jsonl

REVIEW_SECONDS_PER_CARD = 30
REVIEW_SHARE = 0.4
STALE_CAP_DAYS = 21
UNKNOWN_LEVEL_GAP = 1.5


def current_step(profile: dict[str, Any]) -> float | None:
    """The first step (e.g. CLB 9, then 10, then 11) not yet reached in every skill; the target when no steps are set."""
    steps = sorted(profile.get("steps") or ([profile["target"]] if profile.get("target") else []))
    levels = [v for v in (profile.get("levels") or {}).values() if isinstance(v, (int, float))]
    if not steps:
        return None
    floor = min(levels) if levels else 0
    return next((s for s in steps if s > floor), steps[-1])


def skill_gap(profile: dict[str, Any], skill: str) -> float:
    step = current_step(profile)
    level = (profile.get("levels") or {}).get(skill)
    if step is None:
        return 0.0
    if not isinstance(level, (int, float)):
        return UNKNOWN_LEVEL_GAP
    return max(0.0, step - level)


def skill_priorities(language: Language, profile: dict[str, Any], skills: list[str], attempts: list[dict[str, Any]],
                     errors: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    tz = language.learner.timezone
    recurring = errmod.summary(errors)["recurring"]
    ranked = []
    for skill in skills:
        mine = [a for a in attempts if a["skill"] == skill]
        voice = [a for a in mine if a["kind"] == "voice"]
        reasons, gap = [], skill_gap(profile, skill)
        score = gap * 2
        if gap:
            reasons.append(f"{gap:g} below the current step" if isinstance((profile.get("levels") or {}).get(skill), (int, float))
                           else "level not known yet")
        since = clock.days_between(clock.parse(mine[-1]["created"]), now, tz) if mine else None
        stale = min(since if since is not None else STALE_CAP_DAYS, STALE_CAP_DAYS) / 7
        score += stale
        reasons.append("never practised" if since is None else f"last practised {since} day(s) ago")
        pressure = min(sum(1 for r in recurring if skill in r["skills"]), 4) * 0.5
        if pressure:
            score += pressure
            reasons.append("recurring errors")
        if skill == "speaking":
            debt_days = profile["speaking_debt_days"]
            last_voice = clock.days_between(clock.parse(voice[-1]["created"]), now, tz) if voice else None
            if last_voice is None or last_voice >= debt_days:
                score += 2
                reasons.append("speaking debt: no recorded voice answer" + ("" if last_voice is None else f" for {last_voice} days"))
        ranked.append({"skill": skill, "score": round(score, 2), "reasons": reasons})
    return sorted(ranked, key=lambda r: -r["score"])


def pick_task(tasks: list[dict[str, Any]], skill: str, attempts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tasks of a skill, least recently practised first (never-practised in format order)."""
    last = {a["task"]: a["created"] for a in attempts if a.get("task")}
    mine = [t for t in tasks if t["skill"] == skill]
    return sorted(mine, key=lambda t: (last.get(t["id"], ""), mine.index(t)))


def item_from_task(task: dict[str, Any], variant: str, why: str, number: int) -> dict[str, Any]:
    minutes = task["minutes"] if variant == "full" else task["mini"]["minutes"]
    hint = task.get("hint", "") if variant == "full" else task["mini"]["hint"]
    return {"id": f"i{number}", "kind": "task", "skill": task["skill"], "task": task["id"], "name": task["name"],
            "variant": variant, "answer": task["answer"], "minutes": minutes, "why": why, "hint": hint,
            **{k: task[k] for k in ("prep_s", "response_s", "words") if k in task}}


def review_item(language: Language, profile: dict[str, Any], deck: Deck, due: list[dict[str, Any]], now: datetime,
                budget_minutes: float, words_track: dict[str, Any] | None) -> dict[str, Any] | None:
    """Cards due plus, when a word list is set, the day's new words; together they fit a share of the minutes."""
    affordable = max(1, int(budget_minutes * 60 // REVIEW_SECONDS_PER_CARD))
    shown = min(len(due), profile["review_cap"], affordable)
    new = wordsmod.room_for_new(language, profile, words_track, deck, now, len(due), shown, affordable) if words_track else 0
    if shown + new == 0:
        return None
    reasons = ([f"{len(due)} card(s) due" + (f", doing {shown} now" if shown < len(due) else "")] if due else []) + (
        [f"{new} new word(s)"] if new else [])
    item = {"id": "i1", "kind": "review", "cards": shown, "minutes": max(1, math.ceil((shown + new) * REVIEW_SECONDS_PER_CARD / 60)),
            "why": ", ".join(reasons),
            "hint": "practice review due, grade each answer" + ("; the new words are listed under `new_cards`" if new else "")}
    if new:
        item["new"] = new
    return item


def build(language: Language, minutes: int | None, now: datetime, focus: str | None = None,
          exams: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    profile = language.profile()
    config = language.learner.config
    all_tracks = trackmod.load_tracks(language.learner.root)
    state = foundations.status(language, profile, all_tracks)
    stage = state["stage"] if state else None
    words_track = wordsmod.words_track(language, profile, all_tracks) if state else None
    exams = exams or formats.load_exams(language.learner.root)
    tasks = formats.tasks_for(profile, exams) if profile.get("exam") else []
    if not tasks and not state:
        raise PracticeError(f"no exam is set for '{language.code}'; run: practice config --lang {language.code} exam '[\"celpip\"]'")
    budget = int(minutes or profile["daily_minutes"])
    if profile.get("max_minutes"):
        budget = min(budget, int(profile["max_minutes"]))
    attempts = language.attempts()
    errors = read_jsonl(language.errors_path)
    deck = Deck(language)
    due = deck.due(now, language.learner.timezone)
    skills = [s for s in formats.SKILLS if any(t["skill"] == s for t in tasks)]
    ranking = [] if stage == "foundations" else skill_priorities(language, profile, skills, attempts, errors, now)
    if focus and stage != "foundations":
        if focus not in skills:
            raise PracticeError(f"focus must be one of {', '.join(skills)}")
        ranking = sorted(ranking, key=lambda r: r["skill"] != focus)

    items: list[dict[str, Any]] = []
    remaining = float(budget)
    lesson = foundations.next_lesson(language, profile, all_tracks) if stage == "foundations" else None
    reserve = lesson["minutes"] if lesson else 0
    review = review_item(language, profile, deck, due, now, min(budget * REVIEW_SHARE, max(0, budget - reserve)), words_track)
    if review:
        items.append(review)
        remaining -= review["minutes"]
    if lesson:
        shown = foundations.view(language, lesson, profile, build_audio=False)
        items.append({"id": f"i{len(items) + 1}", "kind": "lesson", "lesson": lesson["id"], "unit": lesson["unit"],
                      "name": shown["title"], "minutes": lesson["minutes"], "why": f"next lesson ({state['lessons_done'] + 1} of {state['lessons_total']})",
                      "hint": "practice lesson show " + lesson["id"]})
        remaining -= lesson["minutes"]
    max_items = profile["max_items"]
    for entry in ranking:
        if len(items) >= max_items:
            break
        for task in pick_task(tasks, entry["skill"], attempts):
            variant = None
            if profile["prefer_mini"] and task["mini"]["minutes"] <= remaining:
                variant = "mini"
            elif task["minutes"] <= remaining:
                variant = "full"
            elif task["mini"]["minutes"] <= remaining:
                variant = "mini"
            if variant:
                items.append(item_from_task(task, variant, "; ".join(entry["reasons"]), len(items) + 1))
                remaining -= items[-1]["minutes"]
                break
    if not any(i["kind"] in ("task", "lesson") for i in items) and remaining >= 2 and not review and not state:
        items.append({"id": f"i{len(items) + 1}", "kind": "drill", "minutes": int(min(remaining, 5)),
                      "why": "not enough time for a task: rehearse corrected sentences aloud",
                      "hint": "pick recent errors, say the corrected versions, grade as cards"})
    total = sum(i["minutes"] for i in items)
    return {
        "schema_version": 1, "lang": language.code, "generated": clock.iso(now), "minutes_available": budget,
        "minutes_planned": total, "step": current_step(profile), "light": bool(profile["dreaded"]), "stage": stage,
        "foundations": ({k: state[k] for k in ("lessons_done", "lessons_total", "complete", "next_lesson")} if state else None),
        "cards_due": len(due), "priorities": ranking, "items": items,
        "smallest_step": min(items, key=lambda i: i["minutes"]) if items else None,
    }
