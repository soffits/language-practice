"""Exam task formats are data: packaged JSON files, overridable per learner in `<data>/formats/`."""
from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

from .store import PracticeError

SKILLS = ("listening", "reading", "writing", "speaking")


def load_exams(data_root: Path | None = None) -> dict[str, dict[str, Any]]:
    exams: dict[str, dict[str, Any]] = {}
    folder = resources.files("language_practice") / "data" / "formats"
    for entry in sorted(folder.iterdir(), key=lambda p: p.name):
        if entry.name.endswith(".json"):
            exam = json.loads(entry.read_text(encoding="utf-8"))
            exams[exam["exam"]] = exam
    local = data_root / "formats" if data_root else None
    if local and local.exists():
        for path in sorted(local.glob("*.json")):
            exam = json.loads(path.read_text(encoding="utf-8"))
            exams[exam["exam"]] = exam
    return exams


def categories() -> dict[str, list[str]]:
    text = (resources.files("language_practice") / "data" / "categories.json").read_text(encoding="utf-8")
    return json.loads(text)["categories"]


def exam_ids(profile: dict[str, Any]) -> list[str]:
    exam = profile.get("exam") or []
    return [exam] if isinstance(exam, str) else list(exam)


def tasks_for(profile: dict[str, Any], exams: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for exam_id in exam_ids(profile):
        exam = exams.get(exam_id)
        if exam is None:
            raise PracticeError(f"unknown exam '{exam_id}'; known: {', '.join(sorted(exams)) or 'none'}")
        result.extend(exam["tasks"])
    return result


def find_task(task_id: str, exams: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    for exam in exams.values():
        for task in exam["tasks"]:
            if task["id"] == task_id:
                return task
    return None
