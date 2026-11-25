# Decisions

Each choice with the reason, newest context first where it matters. Dated 2026-11-17 unless noted.

## Build our own, adopt only the scheduler

No open-source tool covers exam-specific practice with an agent as the tutor, local speaking measurements and a
real review scheduler (see [research.md](research.md)). We adopt **py-fsrs** for scheduling, take the "agent is the
tutor, plain files are memory, cards from the learner's own errors, speaking debt" design from
CamiloCortesM/ai-language-tutor (MIT, design source only), and write the rest. The tutor stays the assistant; this
tool is the memory and the arithmetic, so it never judges language itself.

## Plain files, no database

One folder per learner, one subfolder per language; JSON for single records, JSON lines for logs and the deck
([data-layout.md](data-layout.md)). People and any agent can read, grep and diff them, and ordinary file backups
cover them. Sizes stay small (a few hundred cards, tens of attempts a month), so rewriting
`deck.jsonl` atomically (temp file + rename) on each change is cheap. Concurrent commands are serialised with an
`flock` per language folder; the lock is released during the slow speech analysis. SQLite would add a binary
format nobody can read at a glance for no gain at this size.

## Everything is derived from the files, no second copy

Progress and facts are computed from attempts, errors, reviews and sessions on every call; there is no
`progress.jsonl` or cached summary that could disagree with the records. A UI can read the same JSON commands.

## CLI first, JSON stable

Every command takes `--json`; the JSON shapes in [json.md](json.md) are the interface (fields are only added).
Text output is a convenience for terminals. Global options (`--data`, `--learner`, `--lang`, `--json`) work before
or after the subcommand. `--data DIR` is the primary way to name a learner (the caller passes the directory);
`--learner ID` with `$PRACTICE_ROOT` is the shorthand to set once.

## Time

Stored as UTC ISO strings. A learner's day ("today", cards due by the end of the day, session days, streaks) follows
the zone they are in now, taken in this order: `$PRACTICE_TZ` (an IANA name, for a wrapper that knows), else the first
line printed by the learner's `tz_command` (an argument list or string in learner.json, run on each command with a
15 s limit, e.g. a script that reads a travel calendar), else the stored IANA `timezone`.
A source that fails or prints no zone is named on stderr and skipped. The stored `timezone` is never overwritten with
what was found. `practice tz` shows the zone in use and its source. `PRACTICE_NOW` pins the clock for tests and replays.

## FSRS settings

- `fsrs` package (py-fsrs 6.3.3, MIT, one dependency), default weights, `desired_retention` 0.9, `fuzzing` on
  (a card due in 50 days may land on 49 or 51; set `fsrs.fuzzing` false for exact replays).
- **No intra-day learning steps** (`learning_steps_minutes: []`, same for relearning). The default 1 min / 10 min
  steps assume a flashcard app open all day; here a session happens once a day in chat, so a new card graduates
  at its first grade to a day-scale interval and "again" means tomorrow. Configurable.
- **Due means due by the end of the learner's local day** (Anki's day-level rule), so a card scheduled for 18:30
  tomorrow shows up in tomorrow morning's session. `due_now` (strictly past) is also reported.
- Cards are served least-remembered first (retrievability, then due time).
- `maximum_interval_days` 180: exam prep is months, not years.
- Every grade appends a row to `reviews.jsonl` with the FSRS log, so the optimizer can be run later without losing
  history. Parameter optimisation is not built (needs hundreds of reviews and torch).
- **A repeated error is a recurrence**: same wrong/right pair (normalised) finds its existing card, adds one to
  `occurrences`, unsuspends it and makes it due now; its FSRS memory state is not touched (the next grade will
  show how well it is remembered).

## Card formats

- `cloze`: Anki-style markup `{{c1::answer}}` inside the corrected sentence, so the deck exports to Anki later
  without conversion. Front shows `____`; the hint is the assistant's note or the category.
- `correction`: front is the wrong sentence or fragment, back the corrected one.
- `say`: for a sound speech-check flagged as a repeated pattern; front lists the words, back the expected sound.
- `custom` / manual cloze via `cards add`.
- **Only the changed part is blanked**: the shared leading and trailing words (characters for CJK) of wrong and
  right are stripped (`people who has` / `people who have` tests `have`, not the whole phrase). A cloze is used when
  the corrected sentence is given and the answer is at most 4 words (4 characters for CJK are counted per
  character); otherwise a correction pair. The assistant can force `cloze`, `correction` or `none` per error.
- Card ids are `c-` plus the first 10 hex digits of a hash of kind and normalised wrong and right, so re-recording
  the same feedback cannot create duplicates.
- Text is NFC-normalised for matching; matching is case-insensitive but accent-sensitive (`a` is not `à`); CJK is
  matched as substrings, other scripts as whole words.

## Errors

Free dotted lowercase tags (`grammar.verb_form`), with a suggested list per language in `data/categories.json`; the
assistant may invent more. An optional `pattern` names the underlying rule ("subject-verb agreement") so different
sentences with the same weakness count together; recurring errors group by `pattern` else by category. Recurrence
needs two different attempts. Speech-check sound patterns are logged as errors with `source: speech-check`.

## Attempts

An attempt is one answer: text, voice, or an objective set (`--correct/--total` for reading and listening, whose
material the assistant supplies). Text kept as `answer.txt`; voice kept as `audio.<ext>` (default on: a chat app's
copy may be deleted soon after and re-analysis needs it; `speech.keep_audio` false turns it off) with
`speech-check.json` and the transcript written to `answer.txt`. The assistant's feedback goes to `feedback.md`; its
errors to the log. `attempt feedback` can be called again with the same errors without duplicating anything.

**speech-check is a subprocess**, `speech-check analyze <clip> --lang L --format json` (command configurable), not an
import: it has heavy dependencies and its own environment. A failure (missing tool, no speech, timeout) is stored on
the attempt (`speech.status: failed` with the reason) and is never fatal; `attempt analyze <id>` retries, and
`--speech-json` imports a result made elsewhere (for example in a background job). Metrics are a flat numeric
dict ([json.md](json.md)) so trends are simple. Only wrong sounds speech-check marks `repeated` become errors and
say-it cards, because the tool itself says single-word flags are mostly false alarms.

## Planner

A pure function of the files. Steps:

1. Review block when cards are due: 30 s per card, at most 40% of the time and `review_cap` cards.
2. Rank skills by `2 x gap + staleness + error pressure + speaking debt`: gap is the distance to the **current
   step** (the first of `steps`, e.g. 7, 8, 9, that some skill has not reached; so with L7 R8 W6 S6 the step is 7
   and Reading is already there); staleness is days since the skill was last practised, capped at 3 weeks; error
   pressure is the number of recurring patterns for the skill; **speaking debt** adds 2 when there is no recorded
   *voice* answer in `speaking_debt_days` (typing a speaking answer does not pay it, as in ai-language-tutor).
3. For each skill in order, the least recently practised task; the full task when it fits the time left, else its
   small variant (`mini`), else the next skill. Never two tasks of the same skill. Stops at `max_items`.
4. If nothing fits but two minutes remain, a "say the corrected sentences aloud" drill.

A `dreaded` language (one the learner finds hard or tiring) plans lighter: the small variant first
(`prefer_mini`), usually 10 minutes and two items, and the plan names `smallest_step`, the cheapest item, which
already counts if it is all that happens. Nothing in the planner depends on points.

An explicit plan (`session start --task ... --review N`) skips the planner when the learner asks for something
specific.

## Points

The rule this implements (from the learner): points for doing, never for scores; about 5 per 15 focused minutes, up to 20 a
session; harder tasks and due-card reviews earn more; the dreaded language earns more with the smallest steps; no
penalties, no streak loss.

`points.compute` takes only **focused minutes, count of voice answers, count of full writing tasks, cards graded**:

- time: `5 x minutes / 15`
- voice answer: +3 each, at most 2 a session
- full writing task (full variant, at least the task's minimum word count): +3, at most once
- due cards graded: +1 per 5, at most +3
- dreaded language: x1.5, cap 30 instead of 20
- cap 20, minimum 2 for any real activity (starting counts), 0 for none

No level, estimate, error count or accuracy enters, and a test proves two sessions that differ only in those get
the same points. **Focused minutes are what the assistant reports at `session finish --minutes`, else the sum of
the minutes given per attempt, else the planned minutes of finished items**; wall-clock between messages is never
used, since a chat idles. All the numbers are in `learner.json` (`points`) and overridable per learner.

The ledger is an external command (`rewards.command`, `rewards.user`, optional `rewards.global_args` such as
`--data DIR`; the environment passes through). Finishing runs
`<cmd> earn <user> "<what>" --points N --source practice:<session-id> --kind practice --json`, so the ledger's own
once-per-source rule makes a repeat harmless; the outcome (earned, not_recorded, failed, not_configured) and any
trophies the ledger reports are stored on the session and returned. A failed ledger does not fail the session;
`session points <id>` retries. `session reopen <id>` calls `undo --source`. Trophies are the ledger's business: this
tool only exposes facts.

## Facts for trophies

`practice facts` ([facts.md](facts.md)) is a flat, ever-growing JSON per language: sessions finished, attempts per
skill, voice attempts, a **sound clean streak** (how many analysed speaking sessions since a wrong sound was last
flagged as repeated), cards and reviews. Streaks here only record what happened and can't be lost; the ledger
decides what counts as a milestone.

## Task formats are data

`src/language_practice/data/formats/*.json` (CELPIP, TEF Canada, TCF Canada), overridable or extendable per learner
in `<data>/formats/`. Each task has the practice time (`minutes`), a small variant, whether it is a voice, text or
objective answer, prep/response seconds and word limits, so the planner and the speaking time-share metric use
them. CELPIP task names and counts were checked against current sites; the speaking prep and response seconds
were checked against Prometric's Speaking Pro study pack on 2026-11-17 (`docs/research.md`); re-check them when the format changes. When the learner has
not chosen between TEF and TCF, `tef-canada` is a reasonable default (two speaking and two writing sections);
switching is one `config` command.

## Languages

`--lang` is any code (`en`, `fr`, `zh-hans`). Unit counting, matching and cloze work on accented Latin and CJK.
speech-check itself supports English and French; other languages record attempts fine and report the speech step as
failed. Exam files carry their own language.

## Not built, on purpose

Anki export (cards already use Anki cloze markup), FSRS parameter optimisation, generating exam prompts or
listening audio (the assistant supplies them), a Web page inside this tool (the DSH page [soffits/dsh-practice](https://github.com/soffits/dsh-practice) reads the JSON commands), and any
scoring of language (the assistant's estimates are stored as ranges for the record only).


## Foundations: lessons and word lists for a learner who cannot use exam tasks yet (2026-11-17)

Exam task formats are useless at absolute zero (a learner who cannot say the alphabet or a number). A **foundations
stage** comes first, as data, for any learner and language (`data/tracks/`, overridable in `<data>/tracks/`).

**What the research said** (abstracts and review snippets read, full texts not; the choices below are ours):
- Explicit phonetic instruction (what the sound is, how the mouth makes it) plus drills works better for pronunciation
  than exposure alone (Saito & Plonsky's meta-analysis of pronunciation instruction, discussed in *Language Learning*
  2025, doi 10.1111/lang.12716). So every lesson opens with a tiny explanation in the learner's language and ties the
  sound to something the learner can already make (pinyin `ü` for French /y/, `wu` for /u/).
- **Sound-first, listen before reading.** A learner who cannot read the written form of a language needs audio for
  everything: each item has a normal and a slow clip.
- **High-variability phonetic training** (many words and speakers, minimal pairs; SSLA 2025 meta-analysis of 79 studies,
  medium-to-large effect on perception, and a 2024 meta-analysis of 31 studies finding transfer to production) is the model for
  the `pick` drill (hear one of a minimal pair, choose it: rue/roue, tu/tout, banc/bon). Limit: one local voice only; a second
  voice per language is the obvious next improvement (`tts.voices` takes one voice per language now).
- **Shadowing / repeat-after-me** has a growing but mixed evidence base (systematic review 2025); it is cheap, so it is the
  speaking activity of this stage, measured with speech-check's read-aloud mode.
- **What is hard for Mandarin speakers in French** (Mandarin-accented French corpus work, ICPhS 2023; ultrasound work on French /r/,
  ICPhS 2015): front rounded /y/ and /ø/, nasal vowels without a following consonant, the uvular /r/, voiced fricatives /ʒ/, and
  liaison/silent final consonants. These are the lessons of unit 1, in that order (vowels, nasals, consonants, liaison).
- Beginner courses (a typical A1.1 syllabus starts "with the alphabet and numbers", then survival phrases; the FSI French Basic Course,
  public domain, is sound-and-drill first) agree on the order: sounds and letters, numbers 0-20 then to 100, greetings and
  introductions, être/avoir. The tool's French track follows it; everyday nouns come through the word list, not more lessons.

**Open resources and what we borrowed** (licence checked): Lexique 3.83 (CC BY-SA 4.0): the frequency ranking for `fr-core`, with attribution and
share-alike in `data/tracks/fr-core/NOTICE.md`. FSI French Basic Course (US government work, public domain): ordering ideas only, no text copied.
Wikibooks French (CC BY-SA) and Tatoeba (CC BY 2.0 FR, a sentence per author) were considered and not used: attribution per sentence and
share-alike would complicate the files, and examples are short enough to write ourselves. Common Voice (CC0) and Lingua Libre (CC BY-SA) have
real recorded voices, useful later for variability; not needed for the first version. The voice for generated audio is the Piper
`fr_FR-siwis-medium` model (SIWIS dataset, CC BY 4.0), run locally; nothing leaves the machine. All lesson text is written for this tool.

**Tracks are data.** `track.json` plus `units/NN-name.json` (lessons) or `words.json` (ranked words). Text fields are plain
strings or `{ "zh": ..., "en": ... }` objects, so a track can carry any explanation language; `lang` is what is learned and any code works
(tests use Mandarin and French, so CJK and accented text are covered). A lesson has `minutes` (5 to 10), an `explain` text and
`items`: `say` (what is spoken; also shown unless `show` is given), `gloss`, `hint`, or a minimal `pair`, and `drill` for the cards the item
leaves behind (`hear`, `say`, `dictate`, `pick`). Items without `drill` are practice only. Cards are few on purpose (about six a lesson):
a ten-minute session has room for about eight cards, so more would only pile up.

**Audio is generated locally and cached by content.** `tts` in learner.json names any command that reads text on stdin and writes a WAV
(`{voice}`, `{out}`, `{length_scale}` are filled in), and one voice per language. Clips are `<lang>/audio/<sha1(voice, speed, NFC text)[:12]>.mp3`, so
a rebuild is a no-op and an edited sentence gets a new clip. Built in a pool of workers (about a second a clip). `lesson show`, `lesson done`, a
session start with new words and `audio build` fill the cache; with no TTS the views say `audio_status: not_configured` and still carry the text.
`practice audio check` is the ear proxy: it joins a lesson's clips with silences, runs speech-check on the result and compares the transcript with
what the clips should say.

**Cards.** `listen` cards (`hear` front audio then the text and meaning; `say` front the meaning or letter, back the audio; `dictate` hear and write the
digits; `pick` hear and choose between two words) and `word` cards (word with article and audio on the front, meaning and example on the back, one
direction: reading is the goal). A lesson's cards go into the deck when the lesson is marked done and are due from the next local day; they are never
duplicated (the same key is not a recurrence here). They are ordinary FSRS cards.

**Words in daily batches** (`words`, from a words track): at most `foundations.new_per_day` new words a day (default 10, **8 for a dreaded
language**), counted as cards created today; fewer when the due cards already take the review share of the minutes, and none while the due pile
is at `review_cap`. New words become cards when a session starts (so they are due and have audio) and are part of the review item of the plan
(`new`, `new_cards`). The unit of load is the card at 30 s, new or not.

**Stage and levelling up by data.** `profile.foundations = {tracks, words, new_per_day, explain, stage}`. While any lesson of the listed tracks has not been marked
done, `stage` is `foundations` and the plan is `[review, next lesson]`. When every lesson is done the plan is the exam plan again (the word list
carries on inside the review item). `stage: exam` forces the exam plan. Nothing here looks at a score: finishing the lessons is the only condition, and a
learner who stops loses nothing. More units can be added as data and the stage returns until they are done. Points are the existing rules
unchanged (focused minutes, voice answers, cards, dreaded x1.5 and minimum 2 for any real activity); a lesson counts its minutes.

**Speaking at this level is repeat-after-me**: `attempt add --lesson ID [--lesson-item 1,2] --audio clip` fills the reference text from the lesson and uses
speech-check's read-aloud mode (`--reference`). The result is boiled down to words matched and the words whose sounds differ (`speech.repeat`) and labelled
`reliability: low` for any language speech-check has not calibrated (French), however the tool grades itself. Such attempts never create error rows or
say-it cards, so a beginner is not buried in false alarms.

**Readings** (`reading plan|add|list`): the hook for graded readings. The learner's profile holds `reading.interests` (topics chosen by the tutor from what the learner cares about, not hard-coded) and optional `reading.domains` (for English the CELPIP topic areas: housing, health, work...). `plan` returns the interest read
least recently, the domain read least recently, and a target set of unmet words from the first band (100 ranks) of the word list that is not 90 % met, plus a
pool and the list of known words, so the tutor can write or pick a text with mostly known words and a few new ones. `add` records the text and the words it
introduced; those count as met. Tap-to-add is `cards add --word <spelling or id>`. A page with tap-to-add is not built yet. For English no list ships (learners are
usually past it): the deck gets words and collocations mined from their own errors and texts through `cards add --word --gloss ... --source reading|error|speaking`, and a list such as
NGSL/NAWL (CC BY-SA 4.0) can be dropped in `<data>/tracks/` as a words track if wanted.

**Not built**: a second voice, recorded human voices, a page for readings, tap-to-add in the page, automatic generation of the reading texts.
