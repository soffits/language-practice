import json

import pytest

from language_practice import clock
from language_practice.store import read_jsonl

ERRORS = [
    {"category": "grammar.agreement", "pattern": "subject-verb agreement", "wrong": "people who has", "right": "people who have",
     "corrected": "Many people who have children like it.", "note": "plural subject"},
    {"category": "collocation", "wrong": "universal vibe", "right": "broader appeal"},
]


def write_errors(tmp_path, errors=ERRORS, name="errors.json"):
    path = tmp_path / name
    path.write_text(json.dumps(errors, ensure_ascii=False))
    return str(path)


def test_session_flow_with_points(learner_dir, run, tmp_path, fake_ledger, speech_file):
    run("config", "set", "rewards", json.dumps({"command": fake_ledger.command, "user": "tester"}))
    code, session = run("session", "start", "--minutes", "20")
    assert code == 0 and session["status"] == "open" and session["plan"]["items"]
    audio = tmp_path / "clip.ogg"
    audio.write_bytes(b"OggS fake")
    code, voice = run("attempt", "add", "--task", "celpip.speaking.1", "--audio", str(audio), "--speech-json", str(speech_file(
        patterns=[("θ", "s", ["think", "thing"])])), "--minutes", "5", "--feedback", "Clear advice.")
    assert code == 0
    assert voice["attempt"]["kind"] == "voice" and voice["speech"]["status"] == "ok"
    assert voice["attempt"]["metrics"]["fillers_per_100_words"] == pytest.approx(2.5)
    assert voice["attempt"]["metrics"]["response_time_share"] == pytest.approx(60 / 90, abs=0.001)
    assert len(voice["speech"]["sound_cards_new"]) == 1, "a repeated wrong sound becomes a say-it card"
    code, written = run("attempt", "add", "--task", "celpip.writing.2", "--text", "word " * 170, "--minutes", "10",
                        "--errors", write_errors(tmp_path), "--feedback", "ok")
    assert written["attempt"]["within_word_range"] is True and len(written["errors"]["cards_new"]) == 2
    _, due = run("review", "due")
    assert due["due_total"] == 3
    grades = [f"{c['id']}={r}" for c, r in zip(due["cards"], ["good", "again", "easy"])]
    code, graded = run("review", "grade", *grades)
    assert code == 0 and graded["due_remaining"] <= 1 and graded["session"] == session["id"]
    code, done = run("session", "finish", "--minutes", "15")
    assert done["status"] == "finished"
    assert done["points"]["status"] == "earned" and done["points"]["points"] == 11  # 5 + voice 3 + essay 3
    assert done["points"]["trophies"] == ["First practice session"]
    assert fake_ledger.rows()[0]["source"] == f"practice:{session['id']}"
    run("session", "finish", "--minutes", "15")  # second finish changes nothing
    assert len(fake_ledger.rows()) == 1


def test_reopen_takes_the_points_back(learner_dir, run, fake_ledger):
    run("config", "set", "rewards", json.dumps({"command": fake_ledger.command, "user": "t"}))
    _, session = run("session", "start", "--task", "celpip.reading.1")
    run("attempt", "add", "--task", "celpip.reading.1", "--correct", "7", "--total", "10", "--minutes", "12")
    run("session", "finish", "--minutes", "12")
    assert len(fake_ledger.rows()) == 1
    _, reopened = run("session", "reopen", session["id"])
    assert reopened["status"] == "open" and fake_ledger.rows() == []


def test_without_a_ledger_the_session_still_finishes(learner_dir, run):
    _, session = run("session", "start", "--task", "celpip.reading.1")
    run("attempt", "add", "--task", "celpip.reading.1", "--correct", "7", "--total", "10", "--minutes", "12")
    _, done = run("session", "finish", "--minutes", "12")
    assert done["points"]["status"] == "not_configured" and done["points"]["points"] == 4


def test_missing_ledger_command_is_reported_not_fatal(learner_dir, run):
    run("config", "set", "rewards", json.dumps({"command": ["no-such-ledger"], "user": "t"}))
    run("session", "start", "--task", "celpip.reading.1")
    run("attempt", "add", "--task", "celpip.reading.1", "--correct", "7", "--total", "10", "--minutes", "12")
    code, done = run("session", "finish", "--minutes", "12")
    assert code == 0 and done["points"]["status"] == "failed" and "not found" in done["points"]["error"]


def test_repeating_feedback_does_not_duplicate_errors_or_cards(learner_dir, run, tmp_path):
    _, first = run("attempt", "add", "--skill", "writing", "--text", "Many people who has children.")
    attempt_id = first["attempt"]["id"]
    errors = write_errors(tmp_path)
    run("attempt", "feedback", attempt_id, "--errors", errors, "--feedback", "one")
    _, again = run("attempt", "feedback", attempt_id, "--errors", errors)
    assert again["errors"]["recorded"] == 0
    _, log = run("errors")
    assert log["total"] == 2
    _, cards = run("cards", "list", "--all")
    assert len(cards["cards"]) == 2


def test_recurring_errors_surface_across_attempts(learner_dir, run, tmp_path):
    for _ in range(2):
        run("attempt", "add", "--skill", "writing", "--text", "text", "--errors", write_errors(tmp_path))
    _, log = run("errors")
    assert log["recurring"][0]["pattern"] == "subject-verb agreement" and log["recurring"][0]["attempts"] == 2
    _, cards = run("cards", "list", "--all")
    assert {c["occurrences"] for c in cards["cards"]} == {2}


def test_objective_and_progress_trend(learner_dir, run, monkeypatch):
    for day, correct in (("2026-11-10", 5), ("2026-11-12", 6), ("2026-11-14", 8)):
        monkeypatch.setenv("PRACTICE_NOW", f"{day}T15:00:00+00:00")
        run("attempt", "add", "--task", "celpip.reading.1", "--correct", str(correct), "--total", "10")
    monkeypatch.setenv("PRACTICE_NOW", "2026-11-17T15:00:00+00:00")
    _, progress = run("progress")
    accuracy = progress["skills"]["reading"]["metrics"]["accuracy"]
    assert accuracy["n"] == 3 and accuracy["direction"] == "up" and accuracy["better"] == "higher"
    assert progress["skills"]["reading"]["days_since_last"] == 3


def test_sound_streak_counts_clean_speaking_sessions(learner_dir, run, speech_file, tmp_path, monkeypatch):
    audio = tmp_path / "c.ogg"
    audio.write_bytes(b"x")
    flagged = speech_file("a.json", patterns=[("θ", "s", ["think", "thing"])])
    clean = speech_file("b.json")
    for day, result in (("10", flagged), ("12", flagged), ("14", clean), ("15", clean), ("16", clean)):
        monkeypatch.setenv("PRACTICE_NOW", f"2026-11-{day}T15:00:00+00:00")
        run("session", "start", "--task", "celpip.speaking.2")
        run("attempt", "add", "--task", "celpip.speaking.2", "--audio", str(audio), "--speech-json", str(result))
        run("session", "finish", "--minutes", "5")
    _, facts = run("facts")
    en = facts["languages"]["en"]
    assert en["sounds"] == [{"sound": "θ>s", "times_flagged": 2, "clean_streak": 3}]
    assert en["max_sound_clean_streak"] == 3 and en["sessions_finished"] == 5 and en["voice_attempts"] == 5
    assert en["speaking_sessions_analysed"] == 5 and en["attempts"]["speaking"] == 5


def test_reanalysis_is_idempotent(learner_dir, run, speech_file, tmp_path):
    audio = tmp_path / "c.ogg"
    audio.write_bytes(b"x")
    result = speech_file(patterns=[("ð", "d", ["the", "this"])])
    _, first = run("attempt", "add", "--task", "celpip.speaking.3", "--audio", str(audio), "--speech-json", str(result))
    run("attempt", "analyze", first["attempt"]["id"], "--speech-json", str(result))
    _, log = run("errors")
    assert log["total"] == 1


def test_failed_speech_analysis_keeps_the_attempt(learner_dir, run, tmp_path):
    run("config", "set", "speech.command", json.dumps(["no-such-speech-tool"]))
    audio = tmp_path / "c.ogg"
    audio.write_bytes(b"x")
    code, result = run("attempt", "add", "--task", "celpip.speaking.3", "--audio", str(audio))
    assert code == 0 and result["speech"]["status"] == "failed" and "not found" in result["speech"]["error"]


def test_bad_input_gives_clear_errors(learner_dir, run, tmp_path):
    assert run("attempt", "add", "--text", "x")[0] == 1  # no skill or task
    assert run("attempt", "add", "--skill", "writing")[0] == 1  # no answer
    code, message = run("review", "grade", "nope=good")
    assert code == 1 and "no card" in message
    assert run("plan", "--lang", "xx")[0] == 1
    assert run("session", "show")[0] == 1


def test_chinese_learner_cards_and_word_counts(tmp_path, run):
    run("init", "--lang", "zh-hans", "--exam", "celpip")  # exam data is language-agnostic; the text handling is what matters
    path = tmp_path / "e.json"
    path.write_text(json.dumps([{"category": "语法.了", "wrong": "我昨天去商店", "right": "我昨天去了商店", "corrected": "我昨天去了商店。"}], ensure_ascii=False))
    _, result = run("attempt", "add", "--lang", "zh-hans", "--skill", "writing", "--text", "我昨天去商店。", "--errors", str(path))
    assert result["attempt"]["word_count"] == 6
    _, due = run("review", "due", "--lang", "zh-hans")
    assert due["cards"][0]["prompt"] == "我昨天去____商店。" and due["cards"][0]["answer"] == "了"


def test_two_languages_stay_apart(learner_dir, run):
    run("init", "--lang", "fr", "--exam", "tef-canada")
    assert run("status")[0] == 1  # ambiguous without --lang
    _, fr = run("status", "--lang", "fr")
    _, en = run("status", "--lang", "en")
    assert fr["lang"] == "fr" and en["lang"] == "en"
    _, facts = run("facts")
    assert set(facts["languages"]) == {"en", "fr"}


def test_data_files_are_plain_and_readable(learner_dir, run, tmp_path):
    run("attempt", "add", "--skill", "writing", "--text", "Café déjà vu 我", "--errors", write_errors(tmp_path, [{"wrong": "à", "right": "a"}]))
    deck = (learner_dir / "en" / "deck.jsonl").read_text(encoding="utf-8")
    assert json.loads(deck.splitlines()[0])["kind"] == "correction"
    assert "Café déjà vu 我" in next((learner_dir / "en" / "attempts").glob("*/answer.txt")).read_text(encoding="utf-8")
    assert read_jsonl(learner_dir / "en" / "errors.jsonl")[0]["wrong"] == "à"


def test_show_and_points_default_to_the_latest_session(learner_dir, run):
    _, started = run("session", "start", "--task", "celpip.reading.1")
    run("attempt", "add", "--task", "celpip.reading.1", "--correct", "5", "--total", "10", "--minutes", "10")
    run("session", "finish", "--minutes", "10")
    _, shown = run("session", "show")
    assert shown["id"] == started["id"] and shown["status"] == "finished"


def test_prompt_on_a_plan_item_reaches_the_attempt(learner_dir, run):
    _, session = run("session", "start", "--task", "celpip.writing.2")
    code, shown = run("session", "prompt", session["id"], "--item", "i1", "--text", "  Describe a trip. Why 旅行 matters?  ")
    assert code == 0 and shown["plan"]["items"][0]["prompt"] == "Describe a trip. Why 旅行 matters?"
    _, written = run("attempt", "add", "--item", "i1", "--task", "celpip.writing.2", "--text", "word " * 5)
    _, stored = run("attempt", "show", written["attempt"]["id"])
    assert stored["prompt"] == "Describe a trip. Why 旅行 matters?"
    code, error = run("session", "prompt", session["id"], "--item", "i9", "--text", "x")
    assert code == 1 and "no task item" in error
    _, cleared = run("session", "prompt", "--item", "i1", "--text", "")
    assert "prompt" not in cleared["plan"]["items"][0]
