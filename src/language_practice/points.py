"""Points for doing, never for scores: minutes of focused work, kinds of task done, due cards reviewed.

The ledger is an external command (rewards.command in learner.json); it keeps the books, this module only
says how many points a finished session is worth and asks it to record them once per session id.
"""
from __future__ import annotations

import json
import math
import os
import shlex
import shutil
import subprocess
from typing import Any

LANGUAGE_NAMES = {"en": "English", "fr": "French", "es": "Spanish", "de": "German", "zh": "Chinese", "ja": "Japanese"}


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code.split("-")[0], code)


def compute(settings: dict[str, Any], dreaded: bool, focused_minutes: float, voice_attempts: int,
            full_writing_attempts: int, cards_reviewed: int) -> dict[str, Any]:
    """Only counts and minutes go in: no level, score, estimate or error count can change the result."""
    base = settings["per_15_minutes"] * focused_minutes / 15
    voice = settings["voice_bonus"] * min(voice_attempts, settings["voice_bonus_max"])
    essay = settings["essay_bonus"] * min(full_writing_attempts, settings["essay_bonus_max"])
    review = min(settings["review_bonus_per_5_cards"] * (cards_reviewed // 5), settings["review_bonus_cap"])
    raw = base + voice + essay + review
    cap = settings["dreaded_cap"] if dreaded else settings["cap"]
    if dreaded:
        raw *= settings["dreaded_multiplier"]
    active = focused_minutes > 0 or voice_attempts or full_writing_attempts or cards_reviewed
    points = min(cap, max(settings["minimum"], math.floor(raw + 0.5))) if active else 0
    return {"points": int(points), "capped": raw > cap, "parts": {
        "time": round(base, 2), "voice": voice, "essay": essay, "review": review,
        "multiplier": settings["dreaded_multiplier"] if dreaded else 1}}


def _command(rewards: dict[str, Any]) -> list[str] | None:
    command = rewards.get("command")
    if not command:
        return None
    parts = shlex.split(command) if isinstance(command, str) else list(command)
    return [os.path.expanduser(part) for part in parts]


def _call(rewards: dict[str, Any], *args: str) -> dict[str, Any]:
    command = _command(rewards)
    if command is None:
        return {"status": "not_configured"}
    if shutil.which(command[0]) is None:
        return {"status": "failed", "error": f"rewards command '{command[0]}' not found"}
    done = subprocess.run([*command, *rewards.get("global_args", []), *args], capture_output=True, text=True, timeout=60)
    if done.returncode != 0:
        return {"status": "failed", "error": (done.stderr or done.stdout).strip()[-300:]}
    try:
        response = json.loads(done.stdout)
    except json.JSONDecodeError:
        return {"status": "failed", "error": "ledger did not answer in JSON"}
    return {"status": "earned" if response.get("recorded", True) else "not_recorded", "response": response,
            "trophies": [t.get("name", t.get("key")) for t in response.get("trophies", [])]}


def earn(rewards: dict[str, Any], what: str, points: int, session_id: str) -> dict[str, Any]:
    if not rewards.get("user"):
        return {"status": "not_configured"}
    return _call(rewards, "earn", rewards["user"], what, "--points", str(points), "--source", f"practice:{session_id}",
                 "--kind", rewards.get("kind", "practice"), "--json")


def undo(rewards: dict[str, Any], session_id: str) -> dict[str, Any]:
    if not rewards.get("user"):
        return {"status": "not_configured"}
    return _call(rewards, "undo", rewards["user"], "--source", f"practice:{session_id}", "--json")
