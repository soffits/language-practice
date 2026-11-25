# language-practice: notes for maintainers

- The assistant is the tutor; this tool never judges language. It stores, schedules, plans and counts.
- One module per concern: `store` (files, locking), `deck` (cards, FSRS), `errors`, `attempts`, `sessions`, `planner`,
  `points`, `progress` (progress and facts), `speech` (subprocess adapter), `formats` (exam data), `tracks` (lesson and
  word-list data), `foundations` (lessons, stage), `words` (word lists, readings), `audio` (local TTS clips, cached by content), `render`
  (text output), `cli`. Everything derived is recomputed from the files; do not add a cache (the audio folder is the one rebuildable
  store, keyed by content).
- JSON shapes are a public interface (`docs/json.md`, `docs/facts.md`): add fields, never rename or remove within
  `schema_version` 1. Change a shape: update the docs and a test in the same commit.
- Points must never depend on a score, level, estimate, error count or accuracy: `points.compute` takes only minutes
  and counts of things done; `tests/test_planner_points.py` guards it.
- No private data in the repo, tests included (fixtures use made-up learners). Home-specific settings live in the
  learner's data directory, never here.
- Text handling: NFC for matching, accents significant, CJK counted per character (`text.py`); add a CJK and an
  accented case to tests when touching it.
- Dependencies are pinned in `uv.lock`; `uv lock --upgrade` then run unit and e2e tests.

- Foundations content is data under `src/language_practice/data/tracks/`; the licence of what a track is derived from goes in a `NOTICE.md` there (fr-core is CC BY-SA 4.0 via Lexique). Tests for tracks use made-up Mandarin and French tracks.
