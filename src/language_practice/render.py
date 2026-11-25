"""Readable text for the terminal (the JSON from every command is the stable interface)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .store import Language, read_json


def _read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.exists() else None


def attempt_detail(language: Language, attempt: dict[str, Any]) -> dict[str, Any]:
    folder = language.attempt_dir(attempt["id"])
    speech = read_json(folder / "speech-check.json")
    return {**attempt, "answer_text": _read(folder / "answer.txt"), "feedback": _read(folder / "feedback.md"),
            "speech_summary": (speech or {}).get("summary_text"), "folder": str(folder)}


def attempt_result(language: Language, attempt: dict[str, Any], errors: dict[str, Any]) -> dict[str, Any]:
    detail = attempt_detail(language, attempt)
    speech = detail.get("speech") or {}
    return {"attempt": {k: detail.get(k) for k in ("id", "session", "item", "skill", "task", "variant", "kind", "word_count",
                                                      "within_word_range", "minutes", "metrics", "estimate", "folder")},
            "speech": {"status": speech.get("status"), "error": speech.get("error"), "warnings": speech.get("warnings", []),
                       "reliability": speech.get("reliability"), "repeat": speech.get("repeat"),
                       "summary_text": detail["speech_summary"], "sound_cards_new": speech.get("sound_cards_new", []), "transcript": detail["answer_text"] if attempt["kind"] == "voice" else None,
                       "file": str(language.attempt_dir(attempt["id"]) / "speech-check.json") if speech.get("status") == "ok" else None}
            if attempt["kind"] == "voice" else None,
            "errors": errors, "error_ids": attempt["errors"]}


def _plan(plan: dict[str, Any]) -> list[str]:
    lines = []
    for item in plan["items"]:
        mark = {"done": "[x]", "started": "[~]"}.get(item.get("state"), "[ ]")
        if item["kind"] == "review":
            what = f"review {item['cards']} card(s)" + (f" and learn {item['new']} new word(s)" if item.get("new") else "")
        elif item["kind"] == "lesson":
            what = f"lesson {item['lesson']}: {item['name']}"
        elif item["kind"] == "task":
            what = f"{item['name']} ({item['task']}, {item['variant']})"
        else:
            what = "say corrected sentences aloud"
        lines.append(f"{mark} {item['id']}  {item['minutes']} min  {what}")
        lines.append(f"      why: {item['why']}")
        if item.get("hint"):
            lines.append(f"      do: {item['hint']}")
    return lines


def text(command: str, action: str | None, result: dict[str, Any]) -> str:
    if command == "plan":
        head = f"{result['lang']}: {result['minutes_planned']} of {result['minutes_available']} min planned, {result['cards_due']} card(s) due"
        return "\n".join([head, *_plan(result)])
    if command == "session" and "plan" in result:
        head = f"session {result['id']} ({result['status']})"
        points = result.get("points")
        if points:
            head += f", points {points.get('points')} [{points.get('status')}]"
            head += "".join(f"\n🏆 new trophy: {name}" for name in points.get("trophies", []))
        return "\n".join([head, *_plan(result["plan"])])
    if command == "review" and action == "due":
        lines = [f"{result['due_total']} due, showing {result['shown']}"]
        for card in result["cards"]:
            lines.append(f"- {card['id']} [{card['kind']}] {card['prompt']}")
            if card.get("hint"):
                lines.append(f"    hint: {card['hint']}")
            if "answer" in card:
                lines.append(f"    answer: {card['answer']}")
        return "\n".join(lines)
    if command == "review" and action == "grade":
        return "\n".join([f"{g['card']}: {g['rating']} -> next {g['next_due']}" for g in result["graded"]] + [f"{result['due_remaining']} still due today"])
    if command == "attempt" and "attempt" in result and "error_ids" in result:
        a = result["attempt"]
        lines = [f"attempt {a['id']} ({a['skill']}, {a['kind']})"]
        if a.get("word_count") is not None:
            lines.append(f"words: {a['word_count']}" + ("" if a.get("within_word_range") is None else f" (in range: {a['within_word_range']})"))
        speech = result.get("speech")
        if speech:
            lines.append(f"speech-check: {speech['status']}" + (f" ({speech['error']})" if speech.get("error") else "")
                         + (f", pronunciation reliability {speech['reliability']}" if speech.get("reliability") else ""))
            if speech.get("summary_text"):
                lines.append(speech["summary_text"])
        e = result["errors"]
        lines.append(f"errors logged: {e['recorded']}, new cards: {len(e['cards_new'])}, recurring: {len(e['cards_recurring'])}")
        return "\n".join(lines)
    if command == "lesson" and action == "show":
        lines = [f"{result['id']}  {result['title']}  ({result['minutes']} min, audio {result['audio_status']['status']})", result["explain"], ""]
        for item in result["items"]:
            lines.append(f"{item['id']:>3}  {item['show'] or ' / '.join(item['pair'] or [])}  {item['gloss']}".rstrip())
        return "\n".join(lines)
    if command == "lesson" and action == "list":
        return "\n".join(f"[{'x' if l['done'] else ' '}] {l['id']}  {l['title']}  {l['minutes']} min" for l in result["lessons"])
    if command == "errors":
        lines = [f"{result['total']} error(s) logged"]
        lines += [f"  {k}: {v}" for k, v in result["by_category"].items()]
        lines += [f"recurring: {r['pattern']} x{r['count']} in {r['attempts']} attempts: {'; '.join(r['examples'])}" for r in result["recurring"]]
        return "\n".join(lines)
    if command == "status":
        c = result["cards"]
        return (f"{result['lang']}: {c['due_today']} card(s) due today ({c['total']} total), {result['sessions_finished']} session(s) done, "
                f"open: {', '.join(result['open_sessions']) or 'none'}; speaking debt owed: {result['speaking_debt']['owed']}")
    if command == "progress":
        lines = [f"{result['lang']}  step {result['step']}  sessions {result['sessions']['finished']}  cards {result['cards']['total']} ({result['cards']['due_today']} due)"]
        for skill, data in result["skills"].items():
            if not data["attempts"]:
                continue
            lines.append(f"{skill}: {data['attempts']} attempt(s), last {data['days_since_last']} day(s) ago")
            for name, m in data["metrics"].items():
                if m["n"] >= 2:
                    lines.append(f"    {name}: {m['last']} ({m['direction']}, {m['change']:+})")
        lines += [f"sound {s['sound']}: flagged {s['times_flagged']}x, clean for {s['clean_streak']} session(s)" for s in result["sounds"]]
        return "\n".join(lines)
    return json.dumps(result, ensure_ascii=False, indent=2)
