"""Time: always aware UTC inside, local dates only where a person's day matters."""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo


TZ_COMMAND_TIMEOUT_S = 15


def now() -> datetime:
    """Current UTC time; `PRACTICE_NOW` (ISO 8601) pins it for tests and replays."""
    pinned = os.environ.get("PRACTICE_NOW")
    if pinned:
        return parse(pinned)
    return datetime.now(timezone.utc)


def parse(text: str) -> datetime:
    value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def zone(name: str | None) -> ZoneInfo | timezone:
    if not name or name == "UTC":
        return timezone.utc
    return ZoneInfo(name)


def _valid(name: str) -> bool:
    try:
        zone(name)
    except Exception:  # an unknown key, a malformed name, a missing tz database
        return False
    return True


def _from_command(command: Any) -> str:
    """The first line a command prints, which must be an IANA zone name."""
    argv = shlex.split(command) if isinstance(command, str) else [str(part) for part in command]
    argv = [os.path.expanduser(part) if part.startswith("~") else part for part in argv]
    done = subprocess.run(argv, capture_output=True, text=True, timeout=TZ_COMMAND_TIMEOUT_S, check=False)
    if done.returncode != 0:
        raise ValueError(f"exit {done.returncode}: {done.stderr.strip()[:200]}")
    lines = [line.strip() for line in done.stdout.splitlines() if line.strip()]
    if not lines or not _valid(lines[0]):
        raise ValueError(f"printed {lines[0]!r}, not a time zone" if lines else "printed nothing")
    return lines[0]


def current_zone(config: dict[str, Any]) -> tuple[str, str]:
    """The zone a learner's day follows right now, and where it came from: `$PRACTICE_TZ`, else the output of the
    config's `tz_command` (a command that prints where the person is), else the stored `timezone`. A source that
    fails or names no zone is reported on stderr and skipped, so a broken helper never stops practice."""
    stored = config.get("timezone") or "UTC"
    pinned = os.environ.get("PRACTICE_TZ", "").strip()
    if pinned:
        if _valid(pinned):
            return pinned, "PRACTICE_TZ"
        print(f"practice: PRACTICE_TZ={pinned!r} is not a time zone; ignored", file=sys.stderr)
    command = config.get("tz_command")
    if command:
        try:
            return _from_command(command), "tz_command"
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            print(f"practice: tz_command failed ({error}); using the stored timezone {stored}", file=sys.stderr)
    return stored, "timezone"


def local_date(value: datetime, tz_name: str | None) -> date:
    return value.astimezone(zone(tz_name)).date()


def end_of_local_day(value: datetime, tz_name: str | None) -> datetime:
    """The first instant after the person's current day; a card due before it counts as due today."""
    tz = zone(tz_name)
    day = value.astimezone(tz).date()
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    return (start + timedelta(days=1)).astimezone(timezone.utc)


def days_between(earlier: datetime, later: datetime, tz_name: str | None) -> int:
    return (local_date(later, tz_name) - local_date(earlier, tz_name)).days
