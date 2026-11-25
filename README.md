# language-practice

`practice` is a command-line tool for exam-oriented language practice where an assistant is the tutor and the tool is
the memory: it plans a session, records writing and voice attempts (voice goes through
[speech-check](https://github.com/soffits/speech-check) for measurements), keeps the learner's error log, turns errors into review cards
scheduled with [FSRS](https://github.com/open-spaced-repetition/py-fsrs), shows progress over time, and tells a
points ledger what a finished session was worth. State is plain files per learner and language. Every command has
JSON output, so an agent or a page can drive it.

Built-in task formats: CELPIP-General, TEF Canada, TCF Canada (data files, add your own). Any language code works;
text handling covers accented Latin and CJK. The speaking measurements support English and French.

For a learner who cannot use exam tasks yet there is a **foundations stage**, also data: lesson tracks (units of 5 to 10 minute
listen-and-repeat lessons with a tiny explanation, example audio from a local text-to-speech voice, and a few FSRS cards each) and word
lists met in small daily batches. Packaged: `fr-foundations` (sounds and the alphabet, numbers 0 to 100, greetings; explanations in
Chinese) and `fr-core` (524 frequent French words, ranked from Lexique 3.83, CC BY-SA 4.0). The plan switches to exam tasks by itself
once every lesson is done.

## Install

```sh
uv tool install git+https://github.com/soffits/language-practice
practice --help
```

Voice attempts need the separate [speech-check](https://github.com/soffits/speech-check) command on PATH (see its README for
install and model download); everything else works without it. From a clone, `uv sync` and `uv run practice --help` give the same thing.

## Use

```sh
practice --data ~/data/ana init --lang en --exam celpip --steps 7,8,9 --target 9 \
         --level writing=6 --level speaking=6 --level listening=7 --level reading=8 --tz Europe/Paris
practice --data ~/data/ana plan --minutes 20                  # what to do now
practice --data ~/data/ana session start --minutes 20         # plan becomes a session
practice --data ~/data/ana session prompt --item i1 --text-file prompt.txt   # the tutor's prompt, shown with the item
practice --data ~/data/ana attempt add --task celpip.writing.2 --text-file answer.txt --minutes 12 \
         --feedback-file feedback.md --errors errors.json      # errors become review cards
practice --data ~/data/ana attempt add --task celpip.speaking.5 --audio clip.ogg --minutes 5
practice --data ~/data/ana init --lang fr --foundations fr-foundations --words fr-core --explain zh   # beginner stage
practice --data ~/data/ana lesson show                        # the next lesson, audio built on demand
practice --data ~/data/ana lesson done --minutes 6            # its cards join the deck
practice --data ~/data/ana attempt add --lesson fr.sounds.1 --audio clip.ogg   # repeat after me, read-aloud analysis
practice --data ~/data/ana reading plan                       # a topic and target words for the next graded reading
practice --data ~/data/ana review due                         # cards out
practice --data ~/data/ana review grade c-1a2b3c4d5e=good c-9f8e7d6c5b=again
practice --data ~/data/ana session finish --minutes 25        # asks the points ledger once
practice --data ~/data/ana progress                           # trends, recurring errors, speaking signals
practice --data ~/data/ana facts                              # counters and streaks for trophies
```

`--data DIR` (or `$PRACTICE_DATA`, or `--learner ID` with `$PRACTICE_ROOT`) names the learner's folder; `--lang` is
needed when more than one language is set up; add `--json` for JSON.

Points are for doing, not for scores: focused minutes, voice answers, full essays and cards reviewed. Set
`rewards.command` and `rewards.user` with `practice config set` to connect a ledger.

Example audio needs a local text-to-speech command in `learner.json` (`tts`, see [docs/data-layout.md](docs/data-layout.md)); clips are
cached by content next to the learner's data. `practice audio check` runs the clips back through speech-check as an ear proxy.

## Documents

- [docs/decisions.md](docs/decisions.md): what was chosen and why
- [docs/data-layout.md](docs/data-layout.md): files on disk
- [docs/json.md](docs/json.md): the JSON interface
- [docs/facts.md](docs/facts.md): progress facts for a points ledger
- [docs/research.md](docs/research.md): what exists for each piece

## Development

```sh
uv run pytest                                  # unit tests, no models needed
PRACTICE_E2E_VOICES=<dir with en_US-amy-medium.onnx> SPEECH_CHECK_HOME=<models> uv run pytest -m e2e
```

The end-to-end test makes a short Opus clip with a Piper voice and runs the real speech-check on it.

Copyright (C) 2026 Sakina. Licence: AGPL-3.0-or-later, see [LICENSE](LICENSE). The French word list in `src/language_practice/data/tracks/fr-core` is derived from
Lexique and stays under CC BY-SA 4.0, see the NOTICE file in that folder.
