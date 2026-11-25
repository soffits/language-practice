"""`practice`: command line for the planner, attempts, error log, review deck, progress and facts."""
from __future__ import annotations

import argparse
import json
import shlex
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import __version__ as VERSION
from . import attempts as attemptsmod, audio as audiomod, clock, deck as deckmod, errors as errmod, formats, foundations, planner
from . import progress as progressmod, render, sessions, tracks as trackmod, words as wordsmod
from .deck import Deck
from .store import (DEFAULT_CONFIG, DEFAULT_PROFILE, Language, Learner, PracticeError, merged, read_jsonl, resolve_root, set_dotted)


def parse_value(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def read_text_arg(inline: str | None, path: str | None) -> str | None:
    if path:
        return Path(path).expanduser().read_text(encoding="utf-8")
    return inline


def open_language(args: argparse.Namespace) -> tuple[Learner, Language]:
    learner = Learner(resolve_root(args.data, args.learner))
    learner.config  # fails with a clear message when nothing was set up
    return learner, learner.resolve_language(args.lang)


# ---- init, config, formats ---------------------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> Any:
    learner = Learner(resolve_root(args.data, args.learner))
    config = learner.config if learner.exists() else merged(DEFAULT_CONFIG, {})
    config["learner"] = config.get("learner") or args.learner or learner.root.name
    if args.name:
        config["name"] = args.name
    if args.tz:
        clock.zone(args.tz)
        config["timezone"] = args.tz
    if args.tz_command:
        config["tz_command"] = shlex.split(args.tz_command)
    if args.rewards_command:
        config["rewards"]["command"] = shlex.split(args.rewards_command)
    if args.rewards_user:
        config["rewards"]["user"] = args.rewards_user
    learner.save_config(config)
    language = learner.language(args.lang)
    exams = formats.load_exams(learner.root)
    profile = language.profile() if language.exists() else merged(DEFAULT_PROFILE, {"lang": language.code})
    exam = args.exam or (profile["exam"] or [{"en": "celpip", "fr": "tef-canada"}.get(language.code.split("-")[0])])
    profile["exam"] = [e for e in ([exam] if isinstance(exam, str) else exam) if e]
    for exam_id in profile["exam"]:
        if exam_id not in exams:
            raise PracticeError(f"unknown exam '{exam_id}'; known: {', '.join(sorted(exams))}")
    if args.target is not None:
        profile["target"] = args.target
    if args.steps:
        profile["steps"] = [float(x) if "." in x else int(x) for x in args.steps.split(",")]
    for pair in args.level or []:
        skill, _, value = pair.partition("=")
        if skill not in formats.SKILLS or not value:
            raise PracticeError("--level takes skill=number, e.g. writing=7")
        profile["levels"][skill] = float(value) if "." in value else int(value)
    if args.minutes:
        profile["daily_minutes"] = args.minutes
    if args.max_items:
        profile["max_items"] = args.max_items
    if args.foundations or args.words or args.new_words is not None or args.explain or args.stage:
        all_tracks = trackmod.load_tracks(learner.root)
        settings = profile.get("foundations") or {"tracks": []}
        for track_id in args.foundations or []:
            trackmod.get(all_tracks, track_id, "lessons")
        if args.foundations:
            settings["tracks"] = args.foundations
        if args.words:
            trackmod.get(all_tracks, args.words, "words")
            settings["words"] = args.words
        if args.new_words is not None:
            settings["new_per_day"] = args.new_words
        if args.explain:
            settings["explain"] = args.explain
        if args.stage:
            settings["stage"] = args.stage
        profile["foundations"] = settings
    if args.dreaded is not None:
        profile["dreaded"] = args.dreaded
    if args.prefer_mini is not None:
        profile["prefer_mini"] = args.prefer_mini
    language.save_profile(profile)
    return {"learner": config["learner"], "root": str(learner.root), "lang": language.code, "profile": profile}


def cmd_config(args: argparse.Namespace) -> Any:
    learner = Learner(resolve_root(args.data, args.learner))
    language = learner.language(args.lang) if args.lang else None
    target = language.profile() if language else learner.config
    if args.action == "set":
        set_dotted(target, args.key, parse_value(args.value))
        (language.save_profile if language else learner.save_config)(target)
    return target


def cmd_formats(args: argparse.Namespace) -> Any:
    exams = formats.load_exams(resolve_root(args.data, args.learner) if (args.data or args.learner) else None)
    if args.exam:
        if args.exam not in exams:
            raise PracticeError(f"unknown exam '{args.exam}'; known: {', '.join(sorted(exams))}")
        return exams[args.exam]
    return {"exams": [{k: e[k] for k in ("exam", "name", "lang", "scale")} | {"tasks": len(e["tasks"])} for e in exams.values()],
            "categories": formats.categories()}


# ---- tracks, lessons, audio, words, readings ----------------------------------------------------------------------

def _tracks(args: argparse.Namespace) -> dict[str, dict[str, Any]]:
    root = resolve_root(args.data, args.learner) if (args.data or args.learner) else None
    return trackmod.load_tracks(root)


def cmd_tracks(args: argparse.Namespace) -> Any:
    all_tracks = _tracks(args)
    if args.track:
        track = trackmod.get(all_tracks, args.track, all_tracks[args.track]["kind"] if args.track in all_tracks else "lessons")
        if track["kind"] == "words":
            return {k: v for k, v in track.items() if k != "words"} | {"words": len(track["words"]), "first": track["words"][:5]}
        return {k: v for k, v in track.items() if k != "units"} | {"units": [
            {"id": u["id"], "title": u.get("title"), "lessons": [{"id": l["id"], "title": l.get("title"), "minutes": l["minutes"],
                                                                   "items": len(l["items"])} for l in u["lessons"]]} for u in track["units"]]}
    return {"tracks": [{"track": t["track"], "name": t["name"], "lang": t["lang"], "kind": t["kind"], "explain": t["explain"],
                        **({"lessons": len(trackmod.lessons_of(t)), "units": len(t["units"])} if t["kind"] == "lessons"
                           else {"words": len(t["words"])})} for t in all_tracks.values()]}


def cmd_lesson(args: argparse.Namespace) -> Any:
    learner, language = open_language(args)
    profile, now = language.profile(), clock.now()
    all_tracks = trackmod.load_tracks(learner.root)
    state = foundations.status(language, profile, all_tracks)
    if args.action == "list":
        done = {row["lesson"] for row in foundations.completions(language)}
        chosen = foundations.lesson_tracks(profile, all_tracks) or [t for t in all_tracks.values()
                                                                    if t["kind"] == "lessons" and t["lang"] == language.code]
        languages = foundations.explain_languages(profile, chosen[0] if chosen else None)
        return {"status": state, "lessons": [{"id": l["id"], "unit": l["unit"], "title": trackmod.pick(l.get("title"), languages),
                                              "minutes": l["minutes"], "done": l["id"] in done}
                                             for t in chosen for l in trackmod.lessons_of(t)]}
    lesson_id = args.lesson or (state or {}).get("next_lesson")
    lesson = trackmod.find_lesson(all_tracks, lesson_id) if lesson_id else None
    if lesson is None:
        raise PracticeError("name a lesson (practice lesson list)" if not lesson_id else f"unknown lesson '{lesson_id}'")
    if args.action == "show":
        return foundations.view(language, lesson, profile, build_audio=not args.no_audio)
    if args.action == "audio":
        built = audiomod.ensure(language, foundations.lesson_texts(lesson))
        if built["status"] != "ok" and built["total"] != built["cached"]:
            raise PracticeError(f"audio is not available ({built['status']}); set tts in learner.json")
        track = audiomod.listen_track(language, lesson["id"], [tuple(trackmod.spoken(item)) for item in lesson["items"]])
        return {"lesson": lesson["id"], **track}
    session = args.session or next((s["id"] for s in reversed(language.sessions()) if s["status"] == "open"), None)
    return foundations.finish_lesson(language, lesson, now, session, args.minutes)


def _wanted_clips(language: Language, args: argparse.Namespace, all_tracks: dict[str, dict[str, Any]]) -> list[tuple[str, bool]]:
    profile = language.profile()
    wanted: list[tuple[str, bool]] = []
    if args.lesson:
        lessons = [trackmod.find_lesson(all_tracks, args.lesson)]
        if lessons[0] is None:
            raise PracticeError(f"unknown lesson '{args.lesson}'")
    else:
        chosen = [trackmod.get(all_tracks, args.track, "lessons")] if args.track and all_tracks.get(args.track, {}).get("kind") == "lessons" \
            else ([] if args.track else foundations.lesson_tracks(profile, all_tracks))
        lessons = [l for t in chosen for l in trackmod.lessons_of(t)]
    for lesson in lessons:
        wanted += foundations.lesson_texts(lesson)
        wanted += [(c["say"], False) for c in foundations.cards_for(lesson, clock.now(), clock.now())]
    word_track = wordsmod.words_track(language, profile, all_tracks, args.track if args.track and all_tracks.get(args.track, {}).get("kind") == "words" else None) \
        if not args.lesson and (args.track is None or all_tracks.get(args.track, {}).get("kind") == "words") else None
    if word_track:
        for word in word_track["words"][: args.limit or None]:
            wanted.append((wordsmod.display(word), False))
            if word.get("example"):
                wanted.append((word["example"]["text"], False))
    return wanted


def cmd_audio(args: argparse.Namespace) -> Any:
    learner, language = open_language(args)
    all_tracks = trackmod.load_tracks(learner.root)
    if args.action == "build":
        return audiomod.ensure(language, _wanted_clips(language, args, all_tracks))
    profile = language.profile()
    lessons = [trackmod.find_lesson(all_tracks, args.lesson)] if args.lesson else [
        l for t in foundations.lesson_tracks(profile, all_tracks) for l in trackmod.lessons_of(t)]
    if not lessons or lessons[0] is None:
        raise PracticeError("name a lesson (--lesson ID) or set up foundations for this language")
    results = []
    for lesson in lessons:
        spoken = [text for item in lesson["items"] for text in trackmod.spoken(item)]
        shown = [item.get("show", item.get("say")) or " ".join(item["pair"]) for item in lesson["items"]]
        results.append({**audiomod.check(language, lesson["id"], spoken, shown, learner.config["speech"]), "items": len(spoken)})
    return {"lang": language.code, "checks": results, "mean_score": round(sum(r["score"] for r in results) / len(results), 3)}


def cmd_words(args: argparse.Namespace) -> Any:
    learner, language = open_language(args)
    profile, now = language.profile(), clock.now()
    track = wordsmod.words_track(language, profile, trackmod.load_tracks(learner.root), args.track)
    if track is None:
        raise PracticeError("no word list is set for this language; run: practice init --words <track>")
    if args.action == "status":
        return wordsmod.status(language, profile, track, now)
    if args.action == "list":
        band = (profile.get("reading") or {}).get("band_size", wordsmod.DEFAULT_BAND)
        chunk = track["words"][(args.band - 1) * band: args.band * band] if args.band else track["words"]
        return {"track": track["track"], "words": [{"id": w["id"], "rank": w["rank"], "word": wordsmod.display(w),
                                                    "gloss": trackmod.pick(w["gloss"], ["zh", "en"])} for w in chunk]}
    cards = wordsmod.introduce(language, track, args.count or wordsmod.cap_per_day(profile), now)
    return {"introduced": [deckmod.present(c, language=language) for c in cards]}


def cmd_reading(args: argparse.Namespace) -> Any:
    learner, language = open_language(args)
    profile, now = language.profile(), clock.now()
    all_tracks = trackmod.load_tracks(learner.root)
    track = wordsmod.words_track(language, profile, all_tracks)
    if args.action == "list":
        return {"readings": wordsmod.read_readings(language)}
    if track is None:
        raise PracticeError("no word list is set for this language; run: practice init --words <track>")
    if args.action == "plan":
        return wordsmod.plan_reading(language, profile, track, now)
    text = read_text_arg(args.text, args.text_file)
    if not text or not args.title:
        raise PracticeError("add needs --title and --text or --text-file")
    with language.lock():
        return wordsmod.add_reading(language, track, now, topic=args.topic, domain=args.domain, title=args.title, text=text,
                                    source=args.source, new_words=[w for w in (args.new_words or "").split(",") if w.strip()])


# ---- planning and sessions ----------------------------------------------------------------------------------------

def cmd_tz(args: argparse.Namespace) -> Any:
    learner = Learner(resolve_root(args.data, args.learner))
    name, source = learner.zone
    return {"timezone": name, "source": source, "stored": learner.config["timezone"]}


def cmd_status(args: argparse.Namespace) -> Any:
    learner, language = open_language(args)
    return status_of(learner, language, clock.now())


def status_of(learner: Learner, language: Language, now: datetime) -> dict[str, Any]:
    deck = Deck(language).stats(now, learner.timezone)
    opened = [s["id"] for s in language.sessions() if s["status"] == "open"]
    progress = progressmod.progress(language, now)
    all_tracks = trackmod.load_tracks(learner.root)
    state = foundations.status(language, language.profile(), all_tracks)
    track = wordsmod.words_track(language, language.profile(), all_tracks) if state else None
    return {"lang": language.code, "now": clock.iso(now), "open_sessions": opened, "cards": deck,
            "foundations": state, "words": wordsmod.status(language, language.profile(), track, now) if track else None,
            "sessions_finished": progress["sessions"]["finished"], "last_finished": progress["sessions"]["last_finished"],
            "speaking_debt": progress["speaking_debt"], "step": progress["step"]}


def cmd_overview(args: argparse.Namespace) -> Any:
    """Where the learner is now, in one answer: their languages, the status of one, and its open session with the
    attempts in it, or today's plan when no session is open."""
    learner = Learner(resolve_root(args.data, args.learner))
    learner.config
    languages = learner.languages()
    if args.lang and args.lang.lower() in languages:
        code = args.lang.lower()
    elif languages:
        code = languages[0]
    else:
        return {"languages": [], "lang": None, "status": None, "session": None, "plan": None, "attempts": []}
    language = learner.language(code)
    now = clock.now()
    with language.lock():
        status = status_of(learner, language, now)
        opened = status["open_sessions"]
        session = sessions.view(language, language.session(opened[-1])) if opened else None
        attempts = [render.attempt_detail(language, language.attempt(i)) for i in session["attempts"]] if session else []
    plan = None if session else planner.build(language, None, now, None)
    return {"languages": languages, "lang": code, "status": status, "session": session, "plan": plan, "attempts": attempts}


def cmd_plan(args: argparse.Namespace) -> Any:
    _, language = open_language(args)
    return planner.build(language, args.minutes, clock.now(), args.focus)


def cmd_session(args: argparse.Namespace) -> Any:
    learner, language = open_language(args)
    now = clock.now()
    with language.lock():
        if args.action == "start":
            return sessions.view(language, sessions.start(language, now, args.minutes, args.focus, args.task, args.review, args.lesson))
        if args.action == "list":
            return {"sessions": [{k: s.get(k) for k in ("id", "status", "started", "finished", "focused_minutes")} | {
                "points": (s.get("points") or {}).get("points")} for s in language.sessions()]}
        if args.session:
            session_id = args.session
        elif args.action in ("show", "points"):
            session_id = _latest(language)
        else:
            session_id = _only_open(language)
        if args.action == "show":
            return sessions.view(language, language.session(session_id))
        if args.action == "finish":
            done = sessions.finish(language, session_id, now, args.minutes, not args.no_points)
            return sessions.view(language, done)
        if args.action == "points":
            return sessions.view(language, sessions.retry_points(language, session_id))
        if args.action == "reopen":
            return sessions.view(language, sessions.reopen(language, session_id))
        if args.action == "abandon":
            return sessions.view(language, sessions.abandon(language, session_id, now))
        if args.action == "prompt":
            if not args.item:
                raise PracticeError("name the plan item with --item")
            text = read_text_arg(args.text, args.text_file) or ""
            return sessions.view(language, sessions.set_prompt(language, session_id, args.item, text))
    raise PracticeError(f"unknown session action {args.action}")


def _latest(language: Language) -> str:
    opened = [s["id"] for s in language.sessions() if s["status"] == "open"]
    everything = [s["id"] for s in language.sessions()]
    if not everything:
        raise PracticeError("no sessions yet")
    return opened[-1] if opened else everything[-1]


def _only_open(language: Language) -> str:
    opened = [s["id"] for s in language.sessions() if s["status"] == "open"]
    if len(opened) != 1:
        raise PracticeError("name the session: " + (", ".join(opened) if opened else "no session is open"))
    return opened[0]


# ---- attempts ------------------------------------------------------------------------------------------------------

def cmd_attempt(args: argparse.Namespace) -> Any:
    learner, language = open_language(args)
    now = clock.now()
    with language.lock():
        if args.action == "list":
            return {"attempts": [{k: a.get(k) for k in ("id", "session", "skill", "task", "kind", "created", "word_count")} | {
                "errors": len(a["errors"]), "speech": (a.get("speech") or {}).get("status")} for a in language.attempts()]}
        if args.action == "show":
            return render.attempt_detail(language, language.attempt(args.attempt))
        if args.action == "analyze":
            attemptsmod.analyze(language, args.attempt, now=now, speech_json=Path(args.speech_json) if args.speech_json else None)
            return render.attempt_detail(language, language.attempt(args.attempt))
        feedback = read_text_arg(args.feedback, args.feedback_file)
        errors = attemptsmod.load_errors(args.errors)
        if args.action == "feedback":
            language.attempt(args.attempt)
            result = attemptsmod.finish_feedback(language, args.attempt, now=now, feedback=feedback, errors=errors)
            return render.attempt_result(language, result["attempt"], result["errors"])
        text = read_text_arg(args.text, args.text_file)
        result = attemptsmod.add(
            language, now=now, session=args.session, item=args.item, skill=args.skill, task_id=args.task, variant=args.variant,
            text=text, audio=Path(args.audio).expanduser() if args.audio else None, correct=args.correct, total=args.total,
            prompt=read_text_arg(args.prompt, args.prompt_file), minutes=args.minutes, reference=args.reference,
            speech_json=Path(args.speech_json).expanduser() if args.speech_json else None, no_speech=args.no_speech,
            feedback=feedback, errors=errors, estimate=args.estimate, note=args.note, lesson=args.lesson,
            lesson_items=[i for i in (args.lesson_item or "").split(",") if i])
        return render.attempt_result(language, result["attempt"], result["errors"])


# ---- review, cards, errors, progress -------------------------------------------------------------------------------

def cmd_review(args: argparse.Namespace) -> Any:
    learner, language = open_language(args)
    config, now = learner.config, clock.now()
    with language.lock():
        deck = Deck(language)
        tz = learner.timezone
        if args.action == "stats":
            return deck.stats(now, tz)
        if args.action == "due":
            due = deck.due(now, tz)
            shown = due[: args.limit] if args.limit else due
            languages = foundations.explain_languages(language.profile()) + ["zh", "en"]
            return {"lang": language.code, "due_total": len(due), "shown": len(shown),
                    "cards": [deckmod.present(c, not args.no_answer, language, languages) for c in shown]}
        session = args.session or next((s["id"] for s in reversed(language.sessions()) if s["status"] == "open"), None)
        results = []
        for pair in args.grades:
            card_id, _, rating = pair.partition("=")
            if not rating:
                raise PracticeError("grades look like <card-id>=<again|hard|good|easy>")
            results.append(deck.grade(card_id, rating, config, now, session, args.answer if len(args.grades) == 1 else None))
        deck.save()
        return {"graded": results, "session": session, "due_remaining": len(deck.due(now, tz))}


def cmd_cards(args: argparse.Namespace) -> Any:
    learner, language = open_language(args)
    now = clock.now()
    with language.lock():
        deck = Deck(language)
        if args.action == "list":
            cards = deck.cards if args.all else deck.due(now, learner.timezone)
            return {"cards": [deckmod.present(c) | {"due": c["fsrs"]["due"], "suspended": c["suspended"]} for c in cards]}
        if args.action == "show":
            return deck.get(args.card)
        if args.action in ("suspend", "resume"):
            deck.get(args.card)["suspended"] = args.action == "suspend"
            deck.save()
            return {"card": args.card, "suspended": args.action == "suspend"}
        if args.action == "add":
            if args.word:
                all_tracks = trackmod.load_tracks(learner.root)
                track = wordsmod.words_track(language, language.profile(), all_tracks)
                example = {"text": args.example, **({"gloss": args.example_gloss} if args.example_gloss else {})} if args.example else None
                card, is_new = wordsmod.add_word_card(language, track, args.word, args.gloss, args.article, example, args.source, now)
                return {"card": deckmod.present(card, language=language), "new": is_new}
            if args.text:
                key = deckmod.card_key("cloze", args.text, "")
                card = deckmod.new_card(language.code, "cloze", key, now, text=args.text, category=args.category, pattern=None, hint=args.hint or "")
            elif args.front and args.back:
                key = deckmod.card_key("custom", args.front, args.back)
                card = deckmod.new_card(language.code, "custom", key, now, front=args.front, back=args.back, category=args.category, pattern=None, hint=args.hint or "")
            else:
                raise PracticeError("add needs --text (a cloze with {{c1::answer}}) or --front and --back")
            stored, is_new = deck.add(card, now)
            deck.save()
            return {"card": deckmod.present(stored), "new": is_new}
    raise PracticeError(f"unknown cards action {args.action}")


def cmd_errors(args: argparse.Namespace) -> Any:
    _, language = open_language(args)
    rows = read_jsonl(language.errors_path)
    if args.skill:
        rows = [r for r in rows if r["skill"] == args.skill]
    return {"lang": language.code, **errmod.summary(rows), "errors": rows[-args.limit:] if args.limit else rows}


def cmd_progress(args: argparse.Namespace) -> Any:
    _, language = open_language(args)
    now = clock.now()
    result = progressmod.progress(language, now)
    if args.recent:
        with language.lock():
            done = language.sessions()[-args.recent:]
            shown = language.attempts()[-args.recent:]
            result["facts"] = progressmod.facts([language], now)["languages"].get(language.code)
            result["recent"] = {
                "sessions": [{k: s.get(k) for k in ("id", "status", "started", "finished", "focused_minutes")}
                             | {"points": (s.get("points") or {}).get("points")} for s in reversed(done)],
                "attempts": [render.attempt_detail(language, language.attempt(a["id"])) for a in reversed(shown)],
            }
    return result


def cmd_facts(args: argparse.Namespace) -> Any:
    learner = Learner(resolve_root(args.data, args.learner))
    learner.config
    codes = [args.lang.lower()] if args.lang else learner.languages()
    return progressmod.facts([learner.language(c) for c in codes], clock.now())


# ---- parser --------------------------------------------------------------------------------------------------------

def common() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--data", help="learner data directory (or $PRACTICE_DATA)", default=argparse.SUPPRESS)
    parent.add_argument("--learner", help="learner id under $PRACTICE_ROOT", default=argparse.SUPPRESS)
    parent.add_argument("--lang", help="language code (optional when only one is set up)", default=argparse.SUPPRESS)
    parent.add_argument("--json", action="store_true", help="print JSON instead of readable text", default=argparse.SUPPRESS)
    return parent


def build_parser() -> argparse.ArgumentParser:
    parent = common()
    parser = argparse.ArgumentParser(prog="practice", parents=[parent], description="Language-test practice with an assistant as the tutor: plan, attempts, error log, spaced review, progress.")
    parser.add_argument("--version", action="version", version=f"practice {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, func: Callable, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, parents=[parent], help=help_text, description=help_text)
        p.set_defaults(func=func)
        return p

    p = add("init", cmd_init, "set up a learner and a language (safe to repeat; only given fields change)")
    p.add_argument("--exam", action="append", help="exam id (celpip, tef-canada, tcf-canada, or your own in <data>/formats)")
    p.add_argument("--target", type=float)
    p.add_argument("--steps", help="comma list of level steps, e.g. 9,10,11")
    p.add_argument("--level", action="append", help="current level per skill, e.g. writing=7 (repeatable)")
    p.add_argument("--minutes", type=int, help="default minutes per session")
    p.add_argument("--max-items", type=int, help="most plan items per session")
    p.add_argument("--foundations", action="append", help="lesson track for a beginner, used until its lessons are done (repeatable, in order)")
    p.add_argument("--words", help="word-list track the learner meets in daily batches")
    p.add_argument("--new-words", type=int, help="most new words a day (default 10, 8 for a dreaded language)")
    p.add_argument("--explain", help="language the lesson explanations are shown in (default: the track's first)")
    p.add_argument("--stage", choices=["auto", "exam"], help="exam: skip the foundations lessons and plan exam tasks")
    p.add_argument("--dreaded", action=argparse.BooleanOptionalAction, default=None, help="a language the learner finds hard or tiring: lighter plans, more points")
    p.add_argument("--prefer-mini", action=argparse.BooleanOptionalAction, default=None, help="plan the small version of each task when it fits")
    p.add_argument("--name"), p.add_argument("--tz", help="IANA time zone of the learner's day")
    p.add_argument("--tz-command", help="command that prints the IANA zone the person is in now (their day follows it), e.g. 'cat ~/.current-zone'")
    p.add_argument("--rewards-command", help="points ledger command, e.g. 'python3 /path/rewards.py'")
    p.add_argument("--rewards-user", help="the learner's id in that ledger")

    p = add("config", cmd_config, "show or set learner config (no --lang) or language profile (--lang)")
    p.add_argument("action", choices=["show", "set"]); p.add_argument("key", nargs="?"); p.add_argument("value", nargs="?")

    p = add("formats", cmd_formats, "list exam task formats, or show one exam's tasks")
    p.add_argument("exam", nargs="?")

    p = add("tracks", cmd_tracks, "list learning tracks (lesson tracks and word lists), or show one")
    p.add_argument("track", nargs="?")

    p = add("lesson", cmd_lesson, "foundations lessons: list | show [id] | audio [id] | done [id] (default: the next lesson)")
    p.add_argument("action", choices=["list", "show", "audio", "done"]); p.add_argument("lesson", nargs="?")
    p.add_argument("--session"); p.add_argument("--minutes", type=float, help="done: minutes the lesson really took")
    p.add_argument("--no-audio", action="store_true", help="show: do not build missing clips")

    p = add("audio", cmd_audio, "example audio from the local text-to-speech: build | check (round trip through speech-check)")
    p.add_argument("action", choices=["build", "check"])
    p.add_argument("--lesson"); p.add_argument("--track"); p.add_argument("--limit", type=int, help="build: only the first N words of a word list")

    p = add("words", cmd_words, "the word list: status | list [--band N] | intro [--count N] (next words become cards)")
    p.add_argument("action", choices=["status", "list", "intro"]); p.add_argument("--track"); p.add_argument("--band", type=int)
    p.add_argument("--count", type=int)

    p = add("reading", cmd_reading, "graded readings: plan (topic and target words) | add | list")
    p.add_argument("action", choices=["plan", "add", "list"])
    p.add_argument("--title"); p.add_argument("--topic"); p.add_argument("--domain"); p.add_argument("--source")
    p.add_argument("--text"); p.add_argument("--text-file"); p.add_argument("--new-words", help="comma list of word ids or spellings the text introduces")

    add("tz", cmd_tz, "the time zone the learner's day follows now, and where it came from")
    add("status", cmd_status, "one-glance state: due cards, open sessions, speaking debt")
    p = add("plan", cmd_plan, "what to do now (does not start a session)")
    p.add_argument("--minutes", type=int); p.add_argument("--focus", choices=formats.SKILLS)

    p = add("session", cmd_session, "start, show, list, finish, reopen or abandon a session, or attach a prompt to a plan item")
    p.add_argument("action", choices=["start", "show", "list", "finish", "points", "reopen", "abandon", "prompt"])
    p.add_argument("session", nargs="?")
    p.add_argument("--minutes", type=float, help="start: time available; finish: focused minutes worked (the points basis)")
    p.add_argument("--focus", choices=formats.SKILLS); p.add_argument("--task", action="append", help="start: plan these task ids instead of the planner's")
    p.add_argument("--review", type=int, help="start: include a review block of N cards")
    p.add_argument("--lesson", action="append", help="start: plan these foundations lessons instead of the planner's")
    p.add_argument("--no-points", action="store_true", help="finish without asking the ledger for points")
    p.add_argument("--item", help="prompt: the plan item (i1...) to attach the prompt to")
    p.add_argument("--text", help="prompt: the prompt text (empty removes it)"); p.add_argument("--text-file", help="prompt: read the text from a file")

    p = add("attempt", cmd_attempt, "record an attempt (text, voice or objective), add feedback and errors, analyse audio")
    p.add_argument("action", choices=["add", "feedback", "analyze", "show", "list"]); p.add_argument("attempt", nargs="?")
    p.add_argument("--session"); p.add_argument("--item", help="plan item id (i1...)"); p.add_argument("--skill", choices=formats.SKILLS)
    p.add_argument("--task"); p.add_argument("--variant", choices=["full", "mini"], default="full")
    p.add_argument("--text"); p.add_argument("--text-file"); p.add_argument("--audio")
    p.add_argument("--correct", type=int); p.add_argument("--total", type=int)
    p.add_argument("--prompt"); p.add_argument("--prompt-file"); p.add_argument("--minutes", type=float, help="focused minutes this attempt took")
    p.add_argument("--lesson", help="voice: repeat-after-me of a foundations lesson (reference text and skill come from it)")
    p.add_argument("--lesson-item", help="voice: comma list of the lesson's item ids that were repeated (default all)")
    p.add_argument("--reference", help="voice: the text the learner was meant to read (read-aloud mode)")
    p.add_argument("--speech-json", help="voice: use this speech-check result instead of running it")
    p.add_argument("--no-speech", action="store_true", help="voice: skip speech analysis")
    p.add_argument("--feedback"); p.add_argument("--feedback-file")
    p.add_argument("--errors", help="JSON file of errors ('-' for stdin): [{category, wrong, right, sentence, corrected, note, pattern}]")
    p.add_argument("--estimate", help="the assistant's level estimate as a range, e.g. 7-8 (information only)")
    p.add_argument("--note")

    p = add("review", cmd_review, "due cards out, grades in: due | grade <card>=<rating>... | stats")
    p.add_argument("action", choices=["due", "grade", "stats"]); p.add_argument("grades", nargs="*")
    p.add_argument("--limit", type=int); p.add_argument("--no-answer", action="store_true")
    p.add_argument("--session"); p.add_argument("--answer", help="grade: what the learner answered (single card)")

    p = add("cards", cmd_cards, "list, show, add, suspend or resume cards")
    p.add_argument("action", choices=["list", "show", "add", "suspend", "resume"]); p.add_argument("card", nargs="?")
    p.add_argument("--all", action="store_true"); p.add_argument("--text"); p.add_argument("--front"); p.add_argument("--back")
    p.add_argument("--word", help="add: a word card (from the word list by spelling or id, or your own with --gloss)")
    p.add_argument("--gloss", help="add --word: meaning, plain text or JSON like {\"zh\": \"...\"}", type=parse_value)
    p.add_argument("--article"); p.add_argument("--example"); p.add_argument("--example-gloss", type=parse_value)
    p.add_argument("--source", default="words", help="add --word: where the word came from (words, reading, error, speaking)")
    p.add_argument("--category", default="manual"); p.add_argument("--hint")

    p = add("errors", cmd_errors, "the error log, with totals and recurring patterns")
    p.add_argument("--skill", choices=formats.SKILLS); p.add_argument("--limit", type=int)
    p = add("progress", cmd_progress, "per skill trends, recurring errors, sounds, speaking debt, cards")
    p.add_argument("--recent", type=int, default=0, help="also the language's facts and the last N sessions and attempts in full")
    add("overview", cmd_overview, "languages, status, and the open session with its attempts or today's plan, in one answer")
    add("facts", cmd_facts, "stable counters and streaks for a points ledger (JSON only)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv, namespace=argparse.Namespace(data=None, learner=None, lang=None, json=False))
    try:
        result = args.func(args)
    except PracticeError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False) if args.json else f"practice: {exc}", file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}) if args.json else f"practice: {exc}", file=sys.stderr)
        return 1
    if args.json or args.command == "facts":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render.text(args.command, getattr(args, "action", None), result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
