"""Adapter for the `speech-check` command (verbatim transcript, fluency, phone hints, pitch). Not imported as a library."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .store import PracticeError


def run(audio: Path, lang: str, config: dict[str, Any], reference: str | None = None) -> dict[str, Any]:
    command = list(config["command"])
    if shutil.which(command[0]) is None:
        raise PracticeError(f"speech command '{command[0]}' not found; install it or set speech.command")
    argv = [*command, "analyze", str(audio), "--lang", lang, "--format", "json", *config.get("args", [])]
    if reference:
        argv += ["--reference", reference]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=config["timeout_s"])
    except subprocess.TimeoutExpired as exc:
        raise PracticeError(f"speech analysis timed out after {config['timeout_s']} s") from exc
    if done.returncode != 0:
        raise PracticeError(f"speech analysis failed: {done.stderr.strip()[-400:] or 'exit ' + str(done.returncode)}")
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError as exc:
        raise PracticeError("speech analysis did not return JSON") from exc


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def metrics(result: dict[str, Any], response_s: float | None = None) -> dict[str, float]:
    """Numeric signals worth tracking over time, flat and stable. See docs/json.md for the list."""
    out: dict[str, float | None] = {}
    duration = _num(result.get("input", {}).get("duration_s"))
    out["duration_s"] = duration
    if response_s and duration:
        out["response_time_share"] = round(duration / response_s, 3)
    fluency = result.get("fluency") or {}
    if fluency.get("available", True):
        span = _num(fluency.get("speaking_span_s"))
        pauses = fluency.get("pauses") or {}
        out.update(
            words=_num(fluency.get("words")),
            speech_rate_wpm=_num(fluency.get("speech_rate_wpm")),
            articulation_rate_syll_per_s=_num(fluency.get("articulation_rate_syll_per_s")),
            phonation_time_ratio=_num(fluency.get("phonation_time_ratio")),
            mean_length_of_run_words=_num(fluency.get("mean_length_of_run_words")),
            pauses_per_min=_num(pauses.get("per_minute")),
            long_pauses=_num(pauses.get("long_count")),
            longest_pause_s=_num(pauses.get("longest_s")),
            fillers=_num((fluency.get("fillers") or {}).get("count")),
            fillers_per_100_words=_num((fluency.get("fillers") or {}).get("per_100_words")),
            repetitions=_num((fluency.get("repetitions") or {}).get("count")),
            self_corrections=_num((fluency.get("self_corrections") or {}).get("count")),
        )
        long_count = _num(pauses.get("long_count"))
        if long_count is not None and span:
            out["long_pauses_per_min"] = round(long_count / (span / 60), 2)
    confidences = [w["confidence"] for w in (result.get("transcript") or {}).get("words", [])
                   if w.get("kind") == "word" and _num(w.get("confidence")) is not None]
    if confidences:
        out["asr_confidence_mean"] = round(sum(confidences) / len(confidences), 3)
    pron = result.get("pronunciation") or {}
    if pron.get("available", False):
        overall = pron.get("overall") or {}
        out.update(
            words_flagged_likely=_num(overall.get("words_flagged_likely")),
            phone_error_rate=_num(overall.get("phone_error_rate")),
            repeated_sound_patterns=float(sum(1 for p in pron.get("patterns", []) if p.get("repeated"))),
        )
    intonation = result.get("intonation") or {}
    if intonation.get("available", False):
        summary = intonation.get("summary") or {}
        stable = _num(summary.get("sentences_with_stable_pitch"))
        flat = _num(summary.get("flat_sentences"))
        out["median_sentence_range_st"] = _num(summary.get("median_sentence_range_st"))
        if stable:
            out["flat_sentence_share"] = round((flat or 0) / stable, 3)
    return {k: v for k, v in out.items() if v is not None}


def repeated_patterns(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Wrong sounds that came up two or more times: the only phone-level signal trusted enough to act on."""
    pron = result.get("pronunciation") or {}
    patterns = []
    for item in pron.get("patterns", []) if pron.get("available", False) else []:
        if item.get("repeated"):
            patterns.append({"expected": item["expected"], "heard": item["heard"],
                             "occurrences": item.get("occurrences", 2), "words": item.get("words", [])})
    return patterns


def transcript_text(result: dict[str, Any]) -> str:
    return (result.get("transcript") or {}).get("text", "")


CALIBRATED_LANGUAGES = {"en"}
LEVELS = ("very low", "low", "medium", "high")


def reliability(result: dict[str, Any], lang: str) -> str:
    """How far the phone-level feedback can be trusted: `low` for languages speech-check has not calibrated, or when
    it says so itself."""
    level = ((result.get("reliability") or {}).get("pronunciation") or {}).get("level", "low")
    return "low" if lang.split("-")[0] not in CALIBRATED_LANGUAGES or level in ("low", "very low") else level


def repeat_feedback(result: dict[str, Any], lang: str) -> dict[str, Any] | None:
    """Read-aloud result boiled down for a beginner repeating a model: words matched, and per word the sounds that differ."""
    pron = result.get("pronunciation") or {}
    if not pron.get("available", False) or pron.get("mode") != "read_aloud":
        return None
    ref = pron.get("reference") or {}
    flagged = [{"word": w["word"], "expected": w.get("expected"), "heard": w.get("heard"), "flag": w["flag"]}
               for w in pron.get("words", []) if w.get("flag")]
    return {"words_in_reference": ref.get("words_in_reference"), "matched": ref.get("matched"), "word_accuracy": ref.get("word_accuracy"),
            "omitted": ref.get("omitted", []), "substituted": ref.get("substituted", []), "flagged": flagged,
            "reliability": reliability(result, lang),
            "note": "Per-sound feedback for French is uncalibrated: treat it as a hint, never a score."
            if lang.split("-")[0] not in CALIBRATED_LANGUAGES else None}
