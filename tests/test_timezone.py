"""The zone a learner's day follows: the stored one, a command that says where the person is, or a pinned value."""
import json
import sys

from language_practice import clock
from language_practice.store import Learner


def prints(zone: str) -> list[str]:
    return [sys.executable, "-c", f"print({zone!r})"]


def test_the_stored_zone_is_used_when_nothing_else_says_where_the_person_is(learner_dir, run):
    code, out = run("tz")
    assert code == 0 and out == {"timezone": "America/New_York", "source": "timezone", "stored": "America/New_York"}


def test_a_tz_command_names_the_zone_and_the_stored_one_stays_untouched(learner_dir, run):
    run("config", "set", "tz_command", json.dumps(prints("Asia/Shanghai")))
    code, out = run("tz")
    assert code == 0 and out == {"timezone": "Asia/Shanghai", "source": "tz_command", "stored": "America/New_York"}
    assert Learner(learner_dir).config["timezone"] == "America/New_York"
    run("config", "set", "points.cap", "21")  # saving the config never writes the zone it found
    assert Learner(learner_dir).config["timezone"] == "America/New_York"


def test_practice_tz_beats_the_command_and_the_command_beats_the_stored_zone(learner_dir, run, monkeypatch):
    run("config", "set", "tz_command", json.dumps(prints("Asia/Shanghai")))
    monkeypatch.setenv("PRACTICE_TZ", "Europe/Paris")
    assert run("tz")[1]["timezone"] == "Europe/Paris" and run("tz")[1]["source"] == "PRACTICE_TZ"


def test_a_command_may_be_a_string_and_only_its_first_line_counts(learner_dir, run):
    run("config", "set", "tz_command", json.dumps(f"{sys.executable} -c \"print('Asia/Tokyo'); print('ignored')\""))
    assert run("tz")[1]["timezone"] == "Asia/Tokyo"


def test_a_broken_source_is_reported_and_skipped_so_practice_still_works(learner_dir, run, monkeypatch, capsys):
    for command in (["definitely-not-a-command-xyz"], prints("Not/AZone"), prints(""), [sys.executable, "-c", "raise SystemExit(3)"]):
        run("config", "set", "tz_command", json.dumps(command))
        code, out = run("tz")
        assert code == 0 and out["timezone"] == "America/New_York" and out["source"] == "timezone", command
    monkeypatch.setenv("PRACTICE_TZ", "nowhere")
    assert run("tz")[1]["source"] == "timezone"


def test_status_and_due_cards_count_the_day_of_the_zone_the_person_is_in(learner_dir, run, tmp_path):
    errors = tmp_path / "errors.json"
    errors.write_text(json.dumps([{"category": "collocation", "wrong": "universal vibe", "right": "broader appeal"}]))
    run("session", "start", "--minutes", "10")
    run("attempt", "add", "--task", "celpip.writing.1", "--text", "x " * 30, "--errors", str(errors))
    deck = Learner(learner_dir).language("en").dir / "deck.jsonl"
    cards = [json.loads(line) for line in deck.read_text().splitlines()]
    cards[0]["fsrs"]["due"] = "2026-11-18T03:00:00+00:00"  # 22:00 on the 17th in New York, 11:00 on the 18th in Shanghai
    deck.write_text("".join(json.dumps(card) + "\n" for card in cards))
    assert run("review", "stats")[1]["due_today"] == 1
    run("config", "set", "tz_command", json.dumps(prints("Asia/Shanghai")))
    assert run("review", "stats")[1]["due_today"] == 0
