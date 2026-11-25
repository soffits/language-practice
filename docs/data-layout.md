# Data layout

One directory per learner (passed with `--data`, or `$PRACTICE_ROOT/<learner>`). No private data lives in this repo.
Text is UTF-8, JSON is indented, JSON lines are one record per line; times are UTC ISO 8601.

```
<data>/
  learner.json            who, timezone, FSRS, speech, rewards and points settings
  formats/*.json          optional: your own exam task formats (same shape as data/formats in the package)
  tracks/<id>/            optional: your own lesson tracks and word lists (same shape as data/tracks in the package)
  <lang>/                 one folder per language code: en, fr, zh-hans ...
    profile.json          exam(s), target, steps, current levels per skill, daily minutes, dreaded, notes
    deck.jsonl            one card per line, FSRS state inside (rewritten atomically on every change)
    reviews.jsonl         one line per grade, with the FSRS review log (append only)
    errors.jsonl          one line per error found (append only): wrong, right, category, pattern, attempt, card
    lessons.jsonl         one line per foundations lesson marked done: ts, lesson, unit, session, minutes, cards_added (append only)
    readings.jsonl        one line per graded reading recorded: id, ts, topic, domain, title, source, new_words, file (append only)
    readings/<id>.md      the reading text
    audio/<key>.mp3       generated example audio, named by a hash of voice, speed and text (rebuildable cache)
    sessions/<id>.json    plan, status, focused minutes, activity, points outcome
    attempts/<id>/
      attempt.json        skill, task, kind, metrics, estimate, error ids, speech status
      answer.txt          the written answer, or the transcript of a voice answer
      audio.ogg           the clip as received (kept unless speech.keep_audio is false)
      speech-check.json   the full speech-check result
      feedback.md         the assistant's feedback text
```

Ids: sessions `YYYYMMDD-HHMMSS-<lang>` (UTC), attempts `<session id>-a<n>` (or `<timestamp>-<lang>-a<n>` outside a
session), errors `<attempt id>-e<n>` (assistant) or `-s<n>` (speech-check), cards `c-<10 hex>`.

## learner.json

```json
{"schema_version": 1, "learner": "id", "name": "", "timezone": "America/New_York", "tz_command": null,
 "fsrs": {"desired_retention": 0.9, "learning_steps_minutes": [], "relearning_steps_minutes": [],
          "maximum_interval_days": 180, "fuzzing": true},
 "speech": {"command": ["speech-check"], "args": [], "timeout_s": 900, "keep_audio": true},
 "rewards": {"command": ["python3", "/path/rewards.py"], "global_args": [], "user": "id-in-ledger", "kind": "practice"},
 "tts": {"command": ["python", "-m", "piper", "-m", "{voice}", "-f", "{out}", "--length-scale", "{length_scale}"],
         "voices": {"fr": "/path/voice.onnx"}, "format": "mp3", "slow_length_scale": 1.4, "workers": 3, "timeout_s": 120},
 "points": {"per_15_minutes": 5, "cap": 20, "voice_bonus": 3, "voice_bonus_max": 2, "essay_bonus": 3,
            "essay_bonus_max": 1, "review_bonus_per_5_cards": 1, "review_bonus_cap": 3,
            "dreaded_multiplier": 1.5, "dreaded_cap": 30, "minimum": 2}}
```

`timezone` is the learner's home zone; `tz_command` (optional, `practice init --tz-command '...'`) is a command that prints the IANA zone the person is in now, and the day follows it (also `$PRACTICE_TZ`; see `docs/decisions.md`, Time). Missing keys take these defaults. `practice config set <dotted.key> <json>` edits it (`--lang L` edits the profile).

## profile.json

`exam` (list of exam ids), `foundations` (`{tracks: [lesson track ids], words: word-list id, new_per_day, explain: language of the explanations, stage: auto|exam}`),
`reading` (`{words: word-list id, band_size, new_per_reading, interests: [...], domains: [...]}`), `target`, `steps` (e.g. `[7, 8, 9]`), `levels` (`{"writing": 7, ...}`, from real test
results, updated by the assistant), `daily_minutes`, `max_items`, `review_cap`, `prefer_mini`, `dreaded`,
`speaking_debt_days`, `notes`.

## deck.jsonl (one card)

```json
{"id": "c-ef94c342a9", "key": "<sha1>", "kind": "cloze", "lang": "en",
 "text": "Many people who {{c1::have}} children like it.", "category": "grammar.agreement",
 "pattern": "subject-verb agreement", "hint": "plural subject", "wrong": "has",
 "created": "2026-11-17T23:25:30+00:00", "occurrences": 1, "suspended": false,
 "fsrs": {"card_id": 1, "state": 2, "step": null, "stability": 2.3, "difficulty": 5.9,
          "due": "2026-11-19T23:25:49+00:00", "last_review": "2026-11-17T23:25:49+00:00"}}
```

`correction` cards have `front` and `back` instead of `text`; `say` cards have `front` and `back`. `listen` cards (from foundations lessons) have
`mode` (`hear|say|dictate|pick`), `lesson`, `item`, `say` (the text spoken), `show`, `answer`, `choices`, `gloss`, `track`; `word` cards
(from a word list or mined from a text) have `word`, `article`, `pos`, `say`, `gloss`, `example`, `word_id`, `track`, `rank`, `source`. FSRS states:
1 learning (new cards too), 2 review, 3 relearning.

## attempt.json

`id, lang, session, item, skill, task, lesson, lesson_items, variant (full|mini), kind (text|voice|objective), created, minutes, prompt,
word_count, within_word_range, objective {correct,total}, audio_file, answer_file, feedback_file, speech {status:
ok|failed|skipped, error, warnings, tool, reliability, repeat}, metrics {name: number}, errors [ids], estimate {low, high}, note`.

Metric names, all numeric: `words`, `accuracy` (objective), `errors_per_100_words` (assistant errors only), and from
speech-check `duration_s`, `response_time_share` (duration over the task's response time), `speech_rate_wpm`,
`articulation_rate_syll_per_s`, `phonation_time_ratio`, `mean_length_of_run_words`, `pauses_per_min`, `long_pauses`,
`long_pauses_per_min`, `longest_pause_s`, `fillers`, `fillers_per_100_words`, `repetitions`, `self_corrections`,
`asr_confidence_mean`, `words_flagged_likely`, `phone_error_rate`, `repeated_sound_patterns`,
`median_sentence_range_st`, `flat_sentence_share`.
