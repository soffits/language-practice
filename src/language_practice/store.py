"""Plain-file storage: one folder per learner, one subfolder per language. See docs/data-layout.md."""
from __future__ import annotations

import copy
import fcntl
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import clock

SCHEMA_VERSION = 1
LANG_CODE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8})*$")

DEFAULT_CONFIG: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "learner": "",
    "name": "",
    "timezone": "UTC",
    "tz_command": None,
    "fsrs": {
        "desired_retention": 0.9,
        "learning_steps_minutes": [],
        "relearning_steps_minutes": [],
        "maximum_interval_days": 180,
        "fuzzing": True,
    },
    "speech": {"command": ["speech-check"], "args": [], "timeout_s": 900, "keep_audio": True},
    "rewards": {"command": None, "global_args": [], "user": None, "kind": "practice"},
    "tts": {"command": None, "voices": {}, "format": "mp3", "slow_length_scale": 1.4, "workers": 3, "timeout_s": 120},
    "points": {
        "per_15_minutes": 5,
        "cap": 20,
        "voice_bonus": 3,
        "voice_bonus_max": 2,
        "essay_bonus": 3,
        "essay_bonus_max": 1,
        "review_bonus_per_5_cards": 1,
        "review_bonus_cap": 3,
        "dreaded_multiplier": 1.5,
        "dreaded_cap": 30,
        "minimum": 2,
    },
}

DEFAULT_PROFILE: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "lang": "",
    "exam": [],
    "target": None,
    "steps": [],
    "levels": {},
    "daily_minutes": 20,
    "max_items": 3,
    "review_cap": 20,
    "prefer_mini": False,
    "dreaded": False,
    "foundations": None,
    "reading": None,
    "speaking_debt_days": 7,
    "notes": "",
}


class PracticeError(Exception):
    """A problem the person at the keyboard can fix; the CLI prints it without a traceback."""


def merged(defaults: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(defaults)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merged(result[key], value)
        else:
            result[key] = value
    return result


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(data, out, ensure_ascii=False, indent=2)
            out.write("\n")
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    return [json.loads(line) for line in lines if line.strip()]


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as out:
        for row in rows:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            for row in rows:
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def set_dotted(target: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = target
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = node[part] = {}
        node = child
    node[parts[-1]] = value


class Language:
    """One language of one learner: profile, deck, error log, sessions and attempts."""

    def __init__(self, learner: "Learner", code: str) -> None:
        self.learner = learner
        self.code = code
        self.dir = learner.root / code
        self._handle = None
        self._depth = 0

    @property
    def profile_path(self) -> Path:
        return self.dir / "profile.json"

    @property
    def deck_path(self) -> Path:
        return self.dir / "deck.jsonl"

    @property
    def reviews_path(self) -> Path:
        return self.dir / "reviews.jsonl"

    @property
    def errors_path(self) -> Path:
        return self.dir / "errors.jsonl"

    def exists(self) -> bool:
        return self.profile_path.exists()

    def profile(self) -> dict[str, Any]:
        stored = read_json(self.profile_path)
        if stored is None:
            raise PracticeError(f"language '{self.code}' is not set up here; run: practice init --lang {self.code}")
        return merged(DEFAULT_PROFILE, stored)

    def save_profile(self, profile: dict[str, Any]) -> None:
        write_json(self.profile_path, profile)

    @contextmanager
    def lock(self) -> Iterator[None]:
        """Exclusive lock on this language's files; re-entrant within the process."""
        if self._handle is not None:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        with (self.dir / ".lock").open("w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            self._handle = handle
            try:
                yield
            finally:
                self._handle = None
                fcntl.flock(handle, fcntl.LOCK_UN)

    @contextmanager
    def released(self) -> Iterator[None]:
        """Let other commands in while a slow step (speech analysis) runs; the lock is taken again after."""
        handle = self._handle
        if handle is None:
            yield
            return
        fcntl.flock(handle, fcntl.LOCK_UN)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_EX)

    def session_path(self, session_id: str) -> Path:
        return self.dir / "sessions" / f"{session_id}.json"

    def attempt_dir(self, attempt_id: str) -> Path:
        return self.dir / "attempts" / attempt_id

    def sessions(self) -> list[dict[str, Any]]:
        folder = self.dir / "sessions"
        rows = [read_json(p) for p in sorted(folder.glob("*.json"))] if folder.exists() else []
        return [row for row in rows if row]

    def session(self, session_id: str) -> dict[str, Any]:
        data = read_json(self.session_path(session_id))
        if data is None:
            raise PracticeError(f"no session '{session_id}' in language '{self.code}'")
        return data

    def save_session(self, session: dict[str, Any]) -> None:
        write_json(self.session_path(session["id"]), session)

    def attempts(self) -> list[dict[str, Any]]:
        folder = self.dir / "attempts"
        rows = [read_json(p / "attempt.json") for p in sorted(folder.glob("*"))] if folder.exists() else []
        return sorted((row for row in rows if row), key=lambda a: a["created"])

    def attempt(self, attempt_id: str) -> dict[str, Any]:
        data = read_json(self.attempt_dir(attempt_id) / "attempt.json")
        if data is None:
            raise PracticeError(f"no attempt '{attempt_id}' in language '{self.code}'")
        return data

    def save_attempt(self, attempt: dict[str, Any]) -> None:
        write_json(self.attempt_dir(attempt["id"]) / "attempt.json", attempt)


class Learner:
    """The data directory of one person."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._zone: tuple[str, str] | None = None

    @property
    def config_path(self) -> Path:
        return self.root / "learner.json"

    def exists(self) -> bool:
        return self.config_path.exists()

    @property
    def config(self) -> dict[str, Any]:
        stored = read_json(self.config_path)
        if stored is None:
            raise PracticeError(f"no learner data in {self.root}; run: practice init --lang <code>")
        return merged(DEFAULT_CONFIG, stored)

    @property
    def zone(self) -> tuple[str, str]:
        """(zone name, source) of the learner's day right now; asked once per run. See `clock.current_zone`."""
        if self._zone is None:
            self._zone = clock.current_zone(self.config)
        return self._zone

    @property
    def timezone(self) -> str:
        return self.zone[0]

    def save_config(self, config: dict[str, Any]) -> None:
        write_json(self.config_path, config)

    def language(self, code: str) -> Language:
        code = code.lower()
        if not LANG_CODE.match(code):
            raise PracticeError(f"'{code}' is not a language code (examples: en, fr, zh-hans)")
        return Language(self, code)

    def languages(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(p.name for p in self.root.iterdir() if (p / "profile.json").exists())

    def resolve_language(self, code: str | None) -> Language:
        if code:
            return self.language(code)
        known = self.languages()
        if len(known) == 1:
            return self.language(known[0])
        if not known:
            raise PracticeError("no language set up yet; run: practice init --lang <code>")
        raise PracticeError(f"several languages here ({', '.join(known)}); pass --lang")


def resolve_root(data: str | None, learner: str | None) -> Path:
    """`--data DIR`, else `$PRACTICE_DATA`, else `$PRACTICE_ROOT/<learner>` (`--learner` or `$PRACTICE_LEARNER`)."""
    chosen = data or os.environ.get("PRACTICE_DATA")
    if chosen:
        return Path(chosen).expanduser()
    root = os.environ.get("PRACTICE_ROOT")
    person = learner or os.environ.get("PRACTICE_LEARNER")
    if root and person:
        return Path(root).expanduser() / person
    raise PracticeError("no data directory: pass --data DIR, or --learner ID with $PRACTICE_ROOT set")
