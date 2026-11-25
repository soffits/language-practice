"""Progress per skill over time, recurring errors, speaking signals, and the facts a points ledger may build trophies on."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from . import clock, errors as errmod, formats, foundations, planner, words as wordsmod
from .deck import Deck
from .store import Language, read_jsonl

BETTER = {
    "pauses_per_min": "lower", "long_pauses": "lower", "long_pauses_per_min": "lower", "longest_pause_s": "lower",
    "fillers": "lower", "fillers_per_100_words": "lower", "repetitions": "lower", "self_corrections": "lower",
    "errors_per_100_words": "lower", "phone_error_rate": "lower", "words_flagged_likely": "lower",
    "repeated_sound_patterns": "lower", "flat_sentence_share": "lower",
    "articulation_rate_syll_per_s": "higher", "mean_length_of_run_words": "higher", "phonation_time_ratio": "higher",
    "median_sentence_range_st": "higher", "accuracy": "higher", "asr_confidence_mean": "higher",
}
SERIES_LENGTH = 12


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def trend(points: list[tuple[str, float, str | None]]) -> dict[str, Any]:
    """Latest three values against the three before; 'insufficient' until there are at least two attempts."""
    values = [v for _, v, _ in points]
    out: dict[str, Any] = {"n": len(values), "last": values[-1], "series": [{"ts": t, "value": v, "task": k} for t, v, k in points[-SERIES_LENGTH:]]}
    if len(values) < 2:
        return {**out, "direction": "insufficient", "change": None}
    recent, earlier = values[-3:], values[:-3][-3:]
    if not earlier:
        recent, earlier = values[-1:], values[:-1]
    change = _mean(recent) - _mean(earlier)
    base = abs(_mean(earlier)) or 1.0
    direction = "flat" if abs(change) / base < 0.05 else ("up" if change > 0 else "down")
    last_task = points[-1][2]
    same = [v for _, v, k in points[:-1] if k == last_task and k]
    out.update(direction=direction, change=round(change, 3), recent_mean=round(_mean(recent), 3), earlier_mean=round(_mean(earlier), 3))
    if same:
        out["same_task"] = {"task": last_task, "previous_mean": round(_mean(same), 3), "attempts": len(same) + 1}
    return out


def skill_progress(attempts: list[dict[str, Any]], skill: str, now: datetime, tz: str | None) -> dict[str, Any]:
    mine = [a for a in attempts if a["skill"] == skill]
    metrics: dict[str, list[tuple[str, float, str | None]]] = defaultdict(list)
    for attempt in mine:
        for name, value in attempt["metrics"].items():
            metrics[name].append((attempt["created"], value, attempt.get("task")))
    result: dict[str, Any] = {
        "attempts": len(mine), "voice_attempts": sum(1 for a in mine if a["kind"] == "voice"),
        "last_attempt": mine[-1]["created"] if mine else None,
        "days_since_last": clock.days_between(clock.parse(mine[-1]["created"]), now, tz) if mine else None,
        "tasks": sorted({a["task"] for a in mine if a.get("task")}),
        "metrics": {name: {**trend(points), "better": BETTER.get(name)} for name, points in sorted(metrics.items())},
        "estimates": [{"ts": a["created"], "low": a["estimate"]["low"], "high": a["estimate"]["high"], "task": a.get("task")}
                      for a in mine if a.get("estimate")][-SERIES_LENGTH:],
    }
    return result


def sound_history(attempts: list[dict[str, Any]], errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """For every wrong sound speech-check flagged as repeated: how often, and how many analysed speaking sessions since."""
    analysed = [a for a in attempts if a["kind"] == "voice" and a.get("speech", {}).get("status") == "ok"]
    order: list[str] = []
    for attempt in analysed:
        key = attempt.get("session") or attempt["id"]
        if key not in order:
            order.append(key)
    sessions_by_attempt = {a["id"]: a.get("session") or a["id"] for a in analysed}
    flagged: dict[str, set[str]] = defaultdict(set)
    stamps: dict[str, list[str]] = defaultdict(list)
    for row in errors:
        if row.get("source") == "speech-check" and row["attempt"] in sessions_by_attempt:
            flagged[row["pattern"]].add(sessions_by_attempt[row["attempt"]])
            stamps[row["pattern"]].append(row["ts"])
    sounds = []
    for pattern, sessions in flagged.items():
        last_index = max(order.index(s) for s in sessions)
        expected, _, heard = pattern.partition(">")
        sounds.append({"sound": pattern, "expected": expected, "heard": heard, "times_flagged": len(sessions),
                       "first_flagged": min(stamps[pattern]), "last_flagged": max(stamps[pattern]),
                       "clean_streak": len(order) - 1 - last_index})
    return sorted(sounds, key=lambda s: (s["clean_streak"], -s["times_flagged"]))


def speaking_debt(profile: dict[str, Any], attempts: list[dict[str, Any]], now: datetime, tz: str | None) -> dict[str, Any]:
    voice = [a for a in attempts if a["skill"] == "speaking" and a["kind"] == "voice"]
    since = clock.days_between(clock.parse(voice[-1]["created"]), now, tz) if voice else None
    limit = profile["speaking_debt_days"]
    recent = [a for a in voice if clock.parse(a["created"]) > now - timedelta(days=14)]
    return {"days_since_voice_answer": since, "limit_days": limit, "owed": since is None or since >= limit,
            "voice_answers_last_14_days": len(recent)}


def progress(language: Language, now: datetime) -> dict[str, Any]:
    profile, config = language.profile(), language.learner.config
    tz = language.learner.timezone
    attempts, errors = language.attempts(), read_jsonl(language.errors_path)
    sessions = language.sessions()
    finished = [s for s in sessions if s["status"] == "finished"]
    return {
        "schema_version": 1, "learner": config["learner"], "lang": language.code, "generated": clock.iso(now),
        "profile": {k: profile.get(k) for k in ("exam", "target", "steps", "levels", "dreaded")},
        "step": planner.current_step(profile),
        "sessions": {"finished": len(finished), "open": sum(1 for s in sessions if s["status"] == "open"),
                     "abandoned": sum(1 for s in sessions if s["status"] == "abandoned"),
                     "last_finished": finished[-1]["finished"] if finished else None,
                     "focused_minutes": round(sum(s.get("focused_minutes") or 0 for s in finished), 1)},
        "skills": {skill: skill_progress(attempts, skill, now, tz) for skill in formats.SKILLS},
        "errors": errmod.summary(errors),
        "sounds": sound_history(attempts, errors),
        "speaking_debt": speaking_debt(profile, attempts, now, tz),
        "cards": Deck(language).stats(now, tz),
    }


def facts(learner_root_language: list[Language], now: datetime) -> dict[str, Any]:
    """Flat counters and streaks, one block per language: see docs/facts.md. Only grows; nothing here can be lost."""
    out: dict[str, Any] = {}
    for language in learner_root_language:
        profile, config = language.profile(), language.learner.config
        tz = language.learner.timezone
        attempts, errors = language.attempts(), read_jsonl(language.errors_path)
        sessions = [s for s in language.sessions() if s["status"] == "finished"]
        reviews = read_jsonl(language.reviews_path)
        deck_cards = Deck(language).cards
        deck = Deck(language).stats(now, tz)
        week_ago = now - timedelta(days=7)
        sounds = sound_history(attempts, errors)
        days = {clock.local_date(clock.parse(s["finished"]), tz).isoformat() for s in sessions}
        out[language.code] = {
            "sessions_finished": len(sessions),
            "sessions_finished_last_7_days": sum(1 for s in sessions if clock.parse(s["finished"]) > week_ago),
            "days_practised": len(days),
            "focused_minutes": round(sum(s.get("focused_minutes") or 0 for s in sessions), 1),
            "attempts": {skill: sum(1 for a in attempts if a["skill"] == skill) for skill in formats.SKILLS},
            "voice_attempts": sum(1 for a in attempts if a["kind"] == "voice"),
            "speaking_sessions_analysed": len({a.get("session") or a["id"] for a in attempts
                                               if a["kind"] == "voice" and a.get("speech", {}).get("status") == "ok"}),
            "reviews_total": len(reviews),
            "cards_total": deck["total"],
            "cards_mature": deck["mature"],
            "errors_logged": len(errors),
            "lessons_done": len({row["lesson"] for row in foundations.completions(language)}),
            "words_in_deck": sum(1 for c in deck_cards if c["kind"] == "word"),
            "words_known": sum(1 for c in deck_cards if c["kind"] == "word" and wordsmod.is_known(c)),
            "sounds": [{k: s[k] for k in ("sound", "times_flagged", "clean_streak")} for s in sounds],
            "max_sound_clean_streak": max((s["clean_streak"] for s in sounds), default=0),
            "speaking_debt_owed": speaking_debt(profile, attempts, now, tz)["owed"],
        }
    return {"schema_version": 1, "learner": language.learner.config["learner"] if learner_root_language else None,
            "generated": clock.iso(now), "languages": out}
