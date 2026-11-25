"""End to end with the real `speech-check` on a synthetic clip made with a Piper voice.

Run: PRACTICE_E2E_VOICES=<dir holding en_US-amy-medium.onnx> SPEECH_CHECK_HOME=<models> pytest -m e2e
The clip is Opus in an .ogg container, like a phone voice message. Takes a minute or two on CPU.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from language_practice import cli

pytestmark = pytest.mark.e2e
VOICES = Path(os.environ.get("PRACTICE_E2E_VOICES", "/nonexistent"))


def make_clip(path: Path, text: str, voice_file: str) -> None:
    import numpy as np
    import soundfile as sf
    from piper import PiperVoice

    voice = PiperVoice.load(VOICES / voice_file)
    audio = np.concatenate([c.audio_float_array for c in voice.synthesize(text)])
    wav = path.with_suffix(".wav")
    sf.write(wav, audio, voice.config.sample_rate)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-c:a", "libopus", "-b:a", "24k", str(path)], check=True)


@pytest.mark.skipif(not (VOICES / "en_US-amy-medium.onnx").exists() or shutil.which("speech-check") is None,
                    reason="needs speech-check with models and a Piper voice")
def test_voice_answer_through_real_speech_check(tmp_path, capsys):
    data = tmp_path / "learner"
    clip = tmp_path / "answer.ogg"
    make_clip(clip, "I think the best advice is to start early. Very few people finish on time, "
                     "so you should make a plan and follow it every day.", "en_US-amy-medium.onnx")

    def run(*argv):
        assert cli.main(["--data", str(data), *argv, "--json"]) == 0
        return json.loads(capsys.readouterr().out)

    run("init", "--lang", "en", "--tz", "America/New_York", "--level", "speaking=6", "--steps", "7,8,9")
    session = run("session", "start", "--task", "celpip.speaking.1")
    result = run("attempt", "add", "--task", "celpip.speaking.1", "--audio", str(clip), "--minutes", "5")
    assert result["speech"]["status"] == "ok", result["speech"]
    assert "advice" in result["speech"]["transcript"].lower()
    metrics = result["attempt"]["metrics"]
    assert metrics["words"] >= 15 and metrics["articulation_rate_syll_per_s"] > 2
    assert 0 < metrics["response_time_share"] < 1
    run("attempt", "feedback", result["attempt"]["id"], "--feedback", "Clear and well paced.")
    done = run("session", "finish", session["id"], "--minutes", "5")
    assert done["points"]["points"] >= 2
    progress = run("progress")
    assert progress["skills"]["speaking"]["voice_attempts"] == 1
    assert progress["speaking_debt"]["owed"] is False
