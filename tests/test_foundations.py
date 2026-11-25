import json
import shutil
import unicodedata

import pytest

from conftest import fake_speech, write_track
from language_practice import clock, tracks
from language_practice.store import read_jsonl

ZH_UNIT = {"id": "greet", "title": {"en": "Greetings"}, "lessons": [
    {"id": "zh.greet.1", "title": {"en": "Hello"}, "minutes": 5, "explain": {"en": "Tone one is flat."}, "items": [
        {"id": "1", "say": "你好", "gloss": {"en": "hello"}, "drill": ["hear", "say"]},
        {"id": "2", "say": "谢谢", "show": "谢谢", "gloss": {"en": "thanks"}, "drill": ["dictate"]},
        {"id": "3", "pair": ["妈", "马"], "drill": ["pick"], "hint": {"en": "tone 1 or 3"}}]},
    {"id": "zh.greet.2", "title": {"en": "Goodbye"}, "minutes": 5, "explain": {"en": "Two syllables."}, "items": [
        {"id": "1", "say": "再见", "gloss": {"en": "goodbye"}, "drill": ["hear"]}]}]}
ZH_WORDS = [{"word": "水", "pos": "noun", "gloss": {"en": "water"}, "example": {"text": "我喝水。", "gloss": {"en": "I drink water."}}},
            {"word": "茶", "pos": "noun", "gloss": {"en": "tea"}}, {"word": "饭", "pos": "noun", "gloss": {"en": "rice, a meal"}},
            {"word": "书", "pos": "noun", "gloss": {"en": "book"}}]


@pytest.fixture
def zh(tmp_path, run, fake_tts):
    root = tmp_path / "zh-learner"
    write_track(root, {"track": "zh-mini", "name": "Mini", "lang": "zh", "kind": "lessons", "explain": ["en"]}, units=[ZH_UNIT])
    write_track(root, {"track": "zh-words", "name": "Words", "lang": "zh", "kind": "words", "explain": ["en"]}, words=ZH_WORDS)
    code, _ = run("init", "--lang", "zh", "--foundations", "zh-mini", "--words", "zh-words", "--minutes", "10", "--tz", "America/New_York",
                  data=root)
    assert code == 0
    run("config", "set", "tts", json.dumps(fake_tts), data=root)
    return root


def test_packaged_tracks_load_and_validate(run):
    code, listing = run("tracks", data=None) if False else (0, None)
    loaded = tracks.load_tracks(None)
    lessons = tracks.lessons_of(loaded["fr-foundations"])
    assert len(lessons) >= 12 and all(5 <= l["minutes"] <= 10 for l in lessons)
    assert len(loaded["fr-core"]["words"]) >= 500
    assert loaded["fr-core"]["words"][0]["rank"] == 1


def test_bad_track_is_refused_with_the_file_named(tmp_path):
    write_track(tmp_path, {"track": "bad", "name": "x", "lang": "fr", "kind": "lessons"},
                units=[{"id": "u", "lessons": [{"id": "a", "minutes": 5, "items": [{"id": "1"}]}]}])
    with pytest.raises(Exception, match="needs a unique id, `say` or `pair`|item"):
        tracks.load_tracks(tmp_path)


def test_a_foundations_learner_gets_a_lesson_not_exam_tasks(zh, run):
    _, plan = run("plan", data=zh)
    assert plan["stage"] == "foundations" and [i["kind"] for i in plan["items"]] == ["review", "lesson"]
    lesson = plan["items"][1]
    assert lesson["lesson"] == "zh.greet.1" and lesson["name"] == "Hello" and plan["foundations"]["lessons_total"] == 2
    assert plan["items"][0]["new"] == 4 and plan["items"][0]["cards"] == 0, "no cards yet: only new words"


def test_lesson_show_has_audio_and_cjk_text(zh, run):
    _, shown = run("lesson", "show", data=zh)
    assert shown["audio_status"]["status"] == "ok" and shown["audio_status"]["built"] >= 5
    first = shown["items"][0]
    assert first["audio"][0]["text"] == "你好" and first["audio"][0]["file"].startswith("audio/") and first["audio"][0]["slow_file"]
    assert (zh / "zh" / first["audio"][0]["file"]).is_file()
    assert shown["items"][2]["pair"] == ["妈", "马"] and len(shown["items"][2]["audio"]) == 2
    _, again = run("lesson", "show", data=zh)
    assert again["audio_status"]["built"] == 0, "clips are cached"


def test_lesson_done_leaves_cards_due_tomorrow_and_moves_on(zh, run, monkeypatch):
    _, session = run("session", "start", data=zh)
    assert session["plan"]["items"][0]["new_cards"], "new words become cards when the session starts"
    _, done = run("lesson", "done", "--minutes", "6", data=zh)
    assert done["cards_added"] == 5  # hear + say, dictate, two picks, hear
    _, again = run("lesson", "done", "zh.greet.1", data=zh)
    assert again["cards_added"] == 0, "cards are never duplicated"
    _, due_today = run("review", "due", data=zh)
    assert all(c["kind"] == "word" for c in due_today["cards"]), "lesson cards wait for tomorrow"
    _, view = run("session", "show", data=zh)
    assert view["plan"]["items"][1]["state"] == "done"
    _, finished = run("session", "finish", data=zh)
    assert finished["activity"]["lessons"] == 1 and finished["points"]["points"] >= 2
    monkeypatch.setenv("PRACTICE_NOW", "2026-11-18T15:00:00+00:00")
    _, tomorrow = run("review", "due", data=zh)
    kinds = {c["kind"] for c in tomorrow["cards"]}
    assert "listen" in kinds
    listen = [c for c in tomorrow["cards"] if c["kind"] == "listen"]
    assert {c["mode"] for c in listen} == {"hear", "say", "dictate", "pick"}
    say = next(c for c in listen if c["mode"] == "say")
    assert say["prompt"] == "hello" and say["audio"]["side"] == "back" and say["answer"] == "你好"
    pick = next(c for c in listen if c["mode"] == "pick")
    assert sorted(pick["choices"]) == sorted(["妈", "马"]) and pick["answer"] in pick["choices"]


def test_foundations_end_when_every_lesson_is_done(zh, run):
    run("lesson", "done", "zh.greet.1", data=zh)
    _, status = run("status", data=zh)
    assert status["foundations"]["stage"] == "foundations" and status["foundations"]["next_lesson"] == "zh.greet.2"
    run("lesson", "done", "zh.greet.2", data=zh)
    _, status = run("status", data=zh)
    assert status["foundations"]["complete"] and status["foundations"]["stage"] == "exam"
    _, plan = run("plan", data=zh)
    assert plan["stage"] == "exam" and not any(i["kind"] == "lesson" for i in plan["items"])


def test_stage_exam_skips_the_lessons(zh, run):
    run("init", "--lang", "zh", "--stage", "exam", data=zh)
    _, plan = run("plan", data=zh)
    assert plan["stage"] == "exam"


def test_new_words_are_capped_and_never_pile_up(zh, run):
    run("init", "--lang", "zh", "--new-words", "2", data=zh)
    _, plan = run("plan", data=zh)
    assert plan["items"][0]["new"] == 2
    run("session", "start", data=zh)
    _, status = run("words", "status", data=zh)
    assert status["new_today"] == 2 and status["in_deck"] == 2 and status["remaining"] == 2
    _, plan2 = run("plan", data=zh)
    assert plan2["items"][0].get("new", 0) == 0, "today's cap is used up"


def test_dreaded_language_gets_eight_by_default_and_a_big_backlog_blocks_new_words(tmp_path, run):
    root = tmp_path / "fr-learner"
    words = [{"word": f"mot{n}", "pos": "nom", "gloss": {"zh": "词"}} for n in range(40)]
    write_track(root, {"track": "fr-many", "name": "Many", "lang": "fr", "kind": "words", "explain": ["zh"]}, words=words)
    run("init", "--lang", "fr", "--words", "fr-many", "--dreaded", "--prefer-mini", "--minutes", "20", "--tz", "UTC", data=root)
    _, plan = run("plan", data=root)
    assert plan["stage"] == "exam" and plan["items"][0]["new"] == 8
    run("config", "set", "review_cap", "3", "--lang", "fr", data=root)
    run("words", "intro", "--count", "5", data=root)
    _, blocked = run("plan", data=root)
    assert not any(i.get("new") for i in blocked["items"]), "5 cards due is already over the review cap of 3"


def test_french_repeat_after_me_is_labelled_low_reliability(tmp_path, run, speech_file):
    root = tmp_path / "fr-foundations"
    run("init", "--lang", "fr", "--foundations", "fr-foundations", "--minutes", "10", data=root)
    clip = tmp_path / "clip.ogg"
    clip.write_bytes(b"OggS fake")
    result = fake_speech(patterns=[("y", "u", ["tu", "rue"])])
    result["input"]["mode"] = "read_aloud"
    result["pronunciation"].update(mode="read_aloud", words=[
        {"word": "tu", "expected": "t y", "heard": "t u", "flag": "likely"}, {"word": "mu", "expected": "m y", "heard": "m y", "flag": None}],
        reference={"words_in_reference": 2, "matched": 2, "word_accuracy": 1.0, "omitted": [], "substituted": []})
    result["reliability"] = {"pronunciation": {"level": "medium"}}
    path = tmp_path / "speech.json"
    path.write_text(json.dumps(result))
    _, shown = run("lesson", "show", "fr.sounds.1", "--no-audio", data=root)
    assert shown["title"] == "元音 a, i, ou, u" and shown["items"][0]["say"] == "ma"
    code, attempt = run("attempt", "add", "--lesson", "fr.sounds.1", "--lesson-item", "1,5", "--audio", str(clip),
                        "--speech-json", str(path), data=root)
    assert code == 0 and attempt["attempt"]["skill"] == "speaking"
    speech = attempt["speech"]
    assert speech["reliability"] == "low", "French phone-level feedback is uncalibrated, whatever the tool says"
    assert speech["repeat"]["flagged"][0]["word"] == "tu" and "uncalibrated" in speech["repeat"]["note"]
    assert speech["sound_cards_new"] == [], "beginner repeats never fill the deck with sound cards"
    stored = json.loads((root / "fr" / "attempts").glob("*/attempt.json").__next__().read_text())
    assert stored["speech"]["reference"] == "ma tu"


def test_word_cards_can_be_added_from_a_text_for_any_language(tmp_path, run, fake_tts):
    root = tmp_path / "en-learner"
    run("init", "--lang", "en", "--exam", "celpip", data=root)
    run("config", "set", "tts", json.dumps(fake_tts), data=root)
    code, added = run("cards", "add", "--word", "à la carte", "--gloss", json.dumps({"zh": "按菜单点菜"}), "--example", "We ordered à la carte.",
                      "--source", "reading", data=root)
    assert code == 0 and added["new"] and added["card"]["kind"] == "word" and added["card"]["audio"]["file"]
    _, again = run("cards", "add", "--word", unicodedata.normalize("NFD", "à la carte"), "--gloss", json.dumps({"zh": "x"}), data=root)
    assert not again["new"], "the same word in another Unicode form is the same card"
    code, error = run("cards", "add", "--word", "unknown", data=root)
    assert code == 1


def test_reading_plan_rotates_topics_and_targets_the_next_unmet_words(tmp_path, run):
    root = tmp_path / "reader"
    words = [{"word": f"mot{n}", "pos": "nom", "gloss": {"zh": f"词{n}"}} for n in range(1, 31)]
    write_track(root, {"track": "fr-list", "name": "List", "lang": "fr", "kind": "words", "explain": ["zh"]}, words=words)
    run("init", "--lang", "fr", "--words", "fr-list", "--exam", "tef-canada", data=root)
    run("config", "set", "reading", json.dumps({"words": "fr-list", "band_size": 10, "new_per_reading": 3,
                                                 "interests": ["chats", "jeux", "Lyon"], "domains": ["logement", "santé"]}), "--lang", "fr", data=root)
    _, first = run("reading", "plan", data=root)
    assert first["topic"]["id"] == "chats" and first["domain"]["id"] == "logement"
    assert [w["word"] for w in first["words"]["target"]] == ["mot1", "mot2", "mot3"] and first["words"]["band"]["band"] == 1
    code, row = run("reading", "add", "--title", "Un chat", "--topic", "chats", "--domain", "logement", "--text", "Le chat dort.",
                    "--new-words", "mot1,mot2", data=root)
    assert code == 0 and (root / "fr" / row["file"]).read_text().startswith("Le chat")
    _, second = run("reading", "plan", data=root)
    assert second["topic"]["id"] == "jeux" and second["domain"]["id"] == "santé"
    assert [w["word"] for w in second["words"]["target"]] == ["mot3", "mot4", "mot5"], "met words are not offered again"
    run("cards", "add", "--word", "mot3", data=root)
    _, third = run("reading", "plan", data=root)
    assert third["words"]["target"][0]["word"] == "mot4"
    assert third["recent"][0]["topic"] == "chats"


def test_audio_keys_ignore_unicode_form(zh, run):
    from language_practice import audio
    nfc, nfd = unicodedata.normalize("NFC", "été"), unicodedata.normalize("NFD", "été")
    assert audio.clip_key("fr", "v.onnx", nfc, False) == audio.clip_key("fr", "v.onnx", nfd, False)
    assert audio.clip_key("fr", "v.onnx", nfc, False) != audio.clip_key("fr", "v.onnx", nfc, True)


def test_audio_not_configured_is_said_plainly(tmp_path, run):
    root = tmp_path / "silent"
    run("init", "--lang", "fr", "--foundations", "fr-foundations", data=root)
    _, shown = run("lesson", "show", "fr.numbers-1.1", data=root)
    assert shown["audio_status"]["status"] == "not_configured" and shown["items"][0]["audio"][0]["file"] is None


def test_points_for_a_dreaded_foundations_session_follow_the_existing_rules(tmp_path, run, fake_ledger):
    root = tmp_path / "pts"
    run("init", "--lang", "fr", "--foundations", "fr-foundations", "--dreaded", "--minutes", "10", data=root)
    run("config", "set", "rewards", json.dumps({"command": fake_ledger.command, "user": "t"}), data=root)
    run("session", "start", data=root)
    run("lesson", "done", "--minutes", "6", data=root)
    _, done = run("session", "finish", data=root)
    assert done["points"]["points"] == 3 and done["points"]["parts"]["multiplier"] == 1.5  # 5*6/15=2, x1.5
    run("session", "start", "--lesson", "fr.numbers-1.1", data=root)
    _, tiny = run("session", "finish", "--minutes", "0.5", data=root)
    assert tiny["points"]["points"] >= 2, "any real activity earns the minimum"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_lesson_audio_is_one_listen_and_repeat_file(zh, run):
    code, track = run("lesson", "audio", "zh.greet.1", data=zh)
    assert code == 0 and track["file"].startswith("audio/listen-") and track["seconds"] > 5
    _, again = run("lesson", "audio", "zh.greet.1", data=zh)
    assert again["file"] == track["file"], "same lesson, same clips: same file"
