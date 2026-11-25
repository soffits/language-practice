# Facts for a points ledger

`practice facts [--lang L] [--data DIR]` prints JSON (always) for the ledger, a trophy rule or a page to read. Fields
only grow within `schema_version: 1`; nothing here can go down except `speaking_debt_owed`, which is a state, not a
counter. Trophies themselves live in the ledger's config; it reads these numbers and applies its own thresholds.

```json
{"schema_version": 1, "learner": "id", "generated": "2026-11-17T23:25:58+00:00",
 "languages": {"en": {
   "sessions_finished": 12, "sessions_finished_last_7_days": 4, "days_practised": 9, "focused_minutes": 185.0,
   "attempts": {"listening": 1, "reading": 2, "writing": 6, "speaking": 5},
   "voice_attempts": 5, "speaking_sessions_analysed": 5,
   "reviews_total": 140, "cards_total": 38, "cards_mature": 6, "errors_logged": 61,
   "lessons_done": 7, "words_in_deck": 40, "words_known": 12,
   "sounds": [{"sound": "θ>s", "times_flagged": 2, "clean_streak": 3}],
   "max_sound_clean_streak": 3, "speaking_debt_owed": false}}}
```

- `sessions_finished`, `days_practised` (distinct local days with a finished session), `focused_minutes`: what was
  done, never scores.
- `sound` is `expected>heard` in IPA as speech-check reports it. `times_flagged` counts speaking sessions where
  speech-check marked that wrong sound as a repeated pattern; `clean_streak` is how many analysed speaking sessions
  came after the last one that flagged it. A milestone such as "a sound not flagged in 3 consecutive speaking
  sessions" is `max_sound_clean_streak >= 3` (or any entry of `sounds`). Phone-level detection is low trust: it
  is a fact about the tool's flags, not a proof of pronunciation.
- `lessons_done`: distinct foundations lessons marked done. `words_in_deck`: word cards (list words and mined words);
  `words_known`: those in review with stability of at least 4 days.
- `cards_mature`: cards in review with stability of at least 21 days.
