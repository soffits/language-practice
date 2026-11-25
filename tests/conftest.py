import json
import os
import stat
import sys
from pathlib import Path

import pytest

from language_practice import cli
from language_practice.store import Learner


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("PRACTICE_DATA", "PRACTICE_ROOT", "PRACTICE_LEARNER", "PRACTICE_NOW", "PRACTICE_TZ", "REWARDS_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PRACTICE_NOW", "2026-11-17T15:00:00+00:00")


@pytest.fixture
def run(tmp_path, capsys):
    """Run the CLI as a user would; returns (exit code, parsed JSON or text)."""
    def call(*argv, data=None, expect_json=True):
        args = ["--data", str(data or tmp_path / "learner"), *argv]
        if expect_json:
            args.append("--json")
        code = cli.main(args)
        out = capsys.readouterr()
        if code:
            return code, out.err
        return code, (json.loads(out.out) if expect_json else out.out)
    return call


@pytest.fixture
def learner_dir(tmp_path, run):
    run("init", "--lang", "en", "--tz", "America/New_York", "--steps", "7,8,9", "--target", "9",
        "--level", "listening=7", "--level", "reading=8", "--level", "writing=6", "--level", "speaking=6")
    return tmp_path / "learner"


def fake_speech(duration=60.0, words=120, patterns=(), long_pauses=2, fillers=3, flagged=1, warnings=()):
    """A minimal speech-check result with the fields practice reads."""
    return {
        "schema_version": 1, "tool": {"name": "speech-check", "version": "test"},
        "input": {"file": "clip.ogg", "duration_s": duration, "language": "en", "mode": "free_speech"},
        "recording": {"warnings": list(warnings)},
        "transcript": {"text": "I think the weather is nice " * 3, "words": [
            {"i": i, "text": "w", "start": i, "end": i + 0.5, "confidence": 0.9, "kind": "word"} for i in range(5)]},
        "fluency": {"available": True, "speaking_span_s": duration - 4, "words": words, "speech_rate_wpm": words * 60 / duration,
                    "articulation_rate_syll_per_s": 3.9, "phonation_time_ratio": 0.8, "mean_length_of_run_words": 6.5,
                    "pauses": {"per_minute": 9.0, "long_count": long_pauses, "longest_s": 1.4},
                    "fillers": {"count": fillers, "per_100_words": fillers * 100 / words},
                    "repetitions": {"count": 1}, "self_corrections": {"count": 0}},
        "pronunciation": {"available": True, "overall": {"words_flagged_likely": flagged, "phone_error_rate": 0.12},
                          "patterns": [{"expected": e, "heard": h, "occurrences": 3, "words": w, "repeated": True} for e, h, w in patterns]},
        "intonation": {"available": True, "summary": {"sentences_with_stable_pitch": 4, "flat_sentences": 1, "median_sentence_range_st": 7.2}},
        "summary_text": "fake summary",
    }


@pytest.fixture
def speech_file(tmp_path):
    def make(name="speech.json", **kwargs):
        path = tmp_path / name
        path.write_text(json.dumps(fake_speech(**kwargs)))
        return path
    return make


@pytest.fixture
def fake_ledger(tmp_path):
    """A stand-in for the points ledger command: logs every call, answers like the real JSON API, once per source."""
    script = tmp_path / "ledger.py"
    log = tmp_path / "ledger-log.jsonl"
    script.write_text(f'''import json, sys
args = sys.argv[1:]
log = {str(log)!r}
rows = [json.loads(l) for l in open(log)] if __import__("os").path.exists(log) else []
source = args[args.index("--source") + 1]
if args[0] == "earn":
    again = [r for r in rows if r["source"] == source and r["cmd"] == "earn"]
    if not again:
        rows.append({{"cmd": "earn", "source": source, "points": int(args[args.index("--points") + 1]), "what": args[2], "argv": args}})
    print(json.dumps({{"recorded": True, "event": {{"points": int(args[args.index("--points") + 1]), "source": source}}, "trophies": [{{"key": "first_practice", "name": "First practice session"}}]}}))
else:
    rows = [r for r in rows if r["source"] != source]
    print(json.dumps({{"recorded": True, "undone": 1}}))
open(log, "w").write("".join(json.dumps(r) + "\\n" for r in rows))
''')
    class Ledger:
        command = [sys.executable, str(script)]
        def rows(self):
            return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    return Ledger()


FAKE_TTS = '''import sys, wave
out = sys.argv[1]
text = sys.stdin.read()
with wave.open(out, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes(b"\\x01\\x00" * (800 + 40 * len(text)))
'''


@pytest.fixture
def fake_tts(tmp_path):
    """A text-to-speech command that writes a short WAV whose length follows the text; one voice per language."""
    script = tmp_path / "tts.py"
    script.write_text(FAKE_TTS)
    return {"command": [sys.executable, str(script), "{out}"], "voices": {"fr": "voice-fr.onnx", "zh": "voice-zh.onnx", "en": "voice-en.onnx"},
            "format": "wav", "workers": 2}


def write_track(root: Path, track: dict, units: list | None = None, words: list | None = None) -> None:
    folder = root / "tracks" / track["track"]
    (folder / "units").mkdir(parents=True, exist_ok=True)
    (folder / "track.json").write_text(json.dumps(track, ensure_ascii=False))
    for number, unit in enumerate(units or [], 1):
        (folder / "units" / f"{number:02d}-{unit['id']}.json").write_text(json.dumps(unit, ensure_ascii=False))
    if words is not None:
        (folder / "words.json").write_text(json.dumps({"words": words}, ensure_ascii=False))
