"""Example audio from a local text-to-speech command, cached by content in the language's folder.

`tts` in learner.json names the command (any local TTS that reads the text on stdin and writes a WAV file) and one voice
per language. A clip lives at `<language>/audio/<key>.<format>` where the key is a hash of voice, speed and text, so
nothing is ever rebuilt twice and an edited sentence simply gets a new clip. Nothing here talks to a network.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable

from .store import Language, PracticeError
from .text import nfc


def settings(language: Language) -> dict[str, Any]:
    return language.learner.config["tts"]


def voice_for(config: dict[str, Any], lang: str) -> str | None:
    voices = config["voices"]
    voice = voices.get(lang) or voices.get(lang.split("-")[0])
    return str(Path(voice).expanduser()) if voice else None


def clip_key(lang: str, voice: str, text: str, slow: bool) -> str:
    return hashlib.sha1(f"{lang}|{Path(voice).name}|{'slow' if slow else 'normal'}|{nfc(text).strip()}".encode()).hexdigest()[:12]


def clip_name(config: dict[str, Any], lang: str, voice: str, text: str, slow: bool) -> str:
    return f"{clip_key(lang, voice, text, slow)}.{config['format']}"


def lookup(language: Language, text: str, slow: bool = False) -> str | None:
    """The cached clip for `text` as a path relative to the language folder, or None when it is not built."""
    config = settings(language)
    voice = voice_for(config, language.code)
    if not voice or not text:
        return None
    name = f"audio/{clip_name(config, language.code, voice, text, slow)}"
    return name if (language.dir / name).exists() else None


def clips(language: Language, text: str) -> dict[str, str | None]:
    return {"file": lookup(language, text), "slow_file": lookup(language, text, True)}


def _synthesize(config: dict[str, Any], voice: str, text: str, slow: bool, target: Path) -> None:
    command = config["command"]
    scale = config["slow_length_scale"] if slow else 1.0
    argv = [Path(part).expanduser().as_posix() if part.startswith(("~", "/")) else part for part in command]
    argv = [part.replace("{voice}", voice).replace("{length_scale}", str(scale)) for part in argv]
    with tempfile.TemporaryDirectory(prefix="tts-") as work:
        wav = Path(work) / "clip.wav"
        argv = [part.replace("{out}", str(wav)) for part in argv]
        try:
            done = subprocess.run(argv, input=text, capture_output=True, text=True, timeout=config["timeout_s"])
        except subprocess.TimeoutExpired as exc:
            raise PracticeError(f"tts timed out on '{text}'") from exc
        if done.returncode != 0 or not wav.exists() or wav.stat().st_size < 100:
            raise PracticeError(f"tts failed on '{text}': {(done.stderr or '').strip()[-200:] or 'no audio written'}")
        if config["format"] == "wav":
            shutil.move(str(wav), target)
            return
        if shutil.which("ffmpeg") is None:
            raise PracticeError("ffmpeg is needed to make mp3/opus clips (or set tts.format to wav)")
        codec = {"mp3": ["-codec:a", "libmp3lame", "-q:a", "5"], "opus": ["-codec:a", "libopus", "-b:a", "32k"],
                 "ogg": ["-codec:a", "libopus", "-b:a", "32k"], "m4a": ["-codec:a", "aac", "-b:a", "48k"]}.get(config["format"])
        if codec is None:
            raise PracticeError(f"tts.format must be wav, mp3, opus, ogg or m4a, not '{config['format']}'")
        converted = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-ac", "1", *codec, "-f",
                                    {"opus": "ogg", "m4a": "ipod"}.get(config["format"], config["format"]), str(target)],
                                   capture_output=True, text=True, timeout=config["timeout_s"])
        if converted.returncode != 0:
            raise PracticeError(f"ffmpeg failed on '{text}': {converted.stderr.strip()[-200:]}")


def ensure(language: Language, wanted: Iterable[tuple[str, bool]]) -> dict[str, Any]:
    """Build the missing clips for (text, slow) pairs. Never raises for a missing TTS: the status says what happened."""
    config = settings(language)
    voice = voice_for(config, language.code)
    pairs = list(dict.fromkeys((nfc(text).strip(), slow) for text, slow in wanted if text and text.strip()))
    if not config["command"] or not voice:
        return {"status": "not_configured", "built": 0, "cached": 0, "failed": [], "total": len(pairs)}
    folder = language.dir / "audio"
    folder.mkdir(parents=True, exist_ok=True)
    todo = []
    for text, slow in pairs:
        target = folder / clip_name(config, language.code, voice, text, slow)
        if not target.exists():
            todo.append((text, slow, target))

    def make(job: tuple[str, bool, Path]) -> str | None:
        text, slow, target = job
        part = target.with_name(f".{target.name}.part")
        try:
            _synthesize(config, voice, text, slow, part)
            part.replace(target)
            return None
        except PracticeError as exc:
            part.unlink(missing_ok=True)
            return str(exc)

    with ThreadPoolExecutor(max_workers=max(1, int(config["workers"]))) as pool:
        errors = [e for e in pool.map(make, todo) if e]
    return {"status": "ok" if not errors else "partial", "built": len(todo) - len(errors), "cached": len(pairs) - len(todo),
            "failed": errors, "total": len(pairs)}


def resolve(language: Language, relative: str) -> Path:
    """A clip path from a view, only ever inside the language's audio folder."""
    path = (language.dir / relative).resolve()
    if path.parent != (language.dir / "audio").resolve() or not path.is_file():
        raise PracticeError(f"no clip '{relative}'")
    return path


def _tokens(text: str) -> list[str]:
    import re
    import unicodedata
    plain = "".join(c for c in unicodedata.normalize("NFD", nfc(text).casefold()) if unicodedata.category(c) != "Mn")
    return re.findall(r"\w+", plain.replace("-", " ").replace("'", " "))


def check(language: Language, name: str, spoken: list[str], shown: list[str], speech_config: dict[str, Any]) -> dict[str, Any]:
    """Ear proxy for the generated clips: join them with silences, run the learner's speech recogniser on the result and
    compare the transcript with what the clips were meant to say (also with what is shown, since a recogniser writes
    numbers as digits). A score near 1 means the voice is recognisable; the transcript shows where it is not."""
    from difflib import SequenceMatcher

    from . import speech

    config = settings(language)
    voice = voice_for(config, language.code)
    if not voice:
        raise PracticeError("no tts voice for this language")
    with tempfile.TemporaryDirectory(prefix="audio-check-") as work:
        work_dir = Path(work)
        gap = work_dir / "gap.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "1.3", str(gap)], check=True)
        parts, lines = [], []
        for number, text in enumerate(spoken):
            clip = language.dir / "audio" / clip_name(config, language.code, voice, text, False)
            if not clip.exists():
                raise PracticeError(f"clip for '{text}' is not built; run: practice audio build")
            wav = work_dir / f"{number}.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(clip), "-ar", "16000", "-ac", "1", str(wav)], check=True)
            parts += [wav, gap]
        for part in parts:
            lines.append(f"file '{part}'")
        listing = work_dir / "list.txt"
        listing.write_text("\n".join(lines) + "\n")
        joined = work_dir / "joined.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(joined)], check=True)
        result = speech.run(joined, language.code, {**speech_config, "args": ["--no-intonation", "--quiet"]}, " ".join(spoken))
    heard = _tokens(speech.transcript_text(result))
    scores = {label: SequenceMatcher(None, [t for text in words for t in _tokens(text)], heard).ratio()
              for label, words in (("spoken", spoken), ("shown", shown))}
    pron = result.get("pronunciation") or {}
    phones = {"phone_error_rate": (pron.get("overall") or {}).get("phone_error_rate"),
              "word_accuracy": (pron.get("reference") or {}).get("word_accuracy"),
              "flagged": [{k: w.get(k) for k in ("word", "expected", "heard")} for w in pron.get("words", []) if w.get("flag")]} \
        if pron.get("available") else None
    return {"name": name, "score": round(max(scores.values()), 3), "scores": {k: round(v, 3) for k, v in scores.items()},
            "heard": speech.transcript_text(result), "expected": " | ".join(spoken), "phones": phones}


def _pcm(clip: Path, rate: int) -> bytes:
    """A clip as raw 16-bit mono PCM at `rate` Hz (decoded with ffmpeg)."""
    done = subprocess.run(["ffmpeg", "-v", "error", "-i", str(clip), "-ar", str(rate), "-ac", "1", "-f", "s16le", "-"],
                          capture_output=True, timeout=60)
    if done.returncode != 0:
        raise PracticeError(f"ffmpeg could not read {clip.name}: {done.stderr.decode(errors='replace').strip()[-200:]}")
    return done.stdout


def listen_track(language: Language, name: str, texts: list[tuple[str, ...]], rate: int = 22050) -> dict[str, Any]:
    """One file for listen-and-repeat away from the page: each group of texts is played slow, a pause as long as the clip
    plus a second for the learner to repeat, then at normal speed and the same pause. Needs the clips built."""
    config = settings(language)
    voice = voice_for(config, language.code)
    if not voice:
        raise PracticeError("no tts voice for this language")
    folder = language.dir / "audio"
    pieces, used = [], []
    for group in texts:
        for text in group:
            for slow in (True, False):
                clip = folder / clip_name(config, language.code, voice, text, slow)
                if not clip.exists():
                    raise PracticeError(f"clip for '{text}' is not built; run: practice audio build")
                pcm = _pcm(clip, rate)
                used.append(clip.name)
                pieces += [pcm, bytes(2 * int(rate * (len(pcm) / 2 / rate + 1.0)))]
    key = hashlib.sha1("|".join([name, *used]).encode()).hexdigest()[:8]
    target = folder / f"listen-{key}.{config['format']}"
    if not target.exists():
        part = target.with_name(f".{target.name}.part")
        encoder = {"mp3": ["-codec:a", "libmp3lame", "-q:a", "5", "-f", "mp3"], "wav": ["-f", "wav"]}.get(config["format"], ["-codec:a", "libopus", "-b:a", "32k", "-f", "ogg"])
        done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", "-", *encoder, str(part)],
                              input=b"".join(pieces), capture_output=True, timeout=120)
        if done.returncode != 0:
            part.unlink(missing_ok=True)
            raise PracticeError(f"ffmpeg failed: {done.stderr.decode(errors='replace').strip()[-200:]}")
        part.replace(target)
    return {"file": f"audio/{target.name}", "path": str(target), "seconds": round(sum(len(p) for p in pieces) / 2 / rate, 1)}
