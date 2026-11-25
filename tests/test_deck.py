from datetime import timedelta

from language_practice import clock, deck as deckmod
from language_practice.store import Learner


def now():
    return clock.now()


def make(tmp_path, error, lang="en"):
    learner = Learner(tmp_path)
    language = learner.language(lang)
    return language, deckmod.build_from_error(lang, error, now())


def test_short_fix_in_a_sentence_becomes_cloze_of_the_changed_part(tmp_path):
    _, card = make(tmp_path, {"category": "grammar.agreement", "wrong": "people who has", "right": "people who have",
                              "corrected": "Many people who have children like it."})
    assert card["kind"] == "cloze"
    assert card["text"] == "Many people who {{c1::have}} children like it."
    assert deckmod.present(card)["prompt"] == "Many people who ____ children like it."


def test_long_or_unplaceable_fix_becomes_correction_pair(tmp_path):
    _, card = make(tmp_path, {"wrong": "recommend this workshop come alive", "right": "recommend that this workshop be selected",
                              "sentence": "I recommend this workshop come alive.", "corrected": "I recommend that this workshop be selected."})
    assert card["kind"] == "correction"
    assert deckmod.present(card)["answer"] == "I recommend that this workshop be selected."
    _, fragment = make(tmp_path, {"wrong": "universal vibe", "right": "broader appeal"})
    assert fragment["kind"] == "correction" and fragment["front"] == "universal vibe"


def test_card_none_and_empty_make_no_card(tmp_path):
    assert make(tmp_path, {"wrong": "x", "right": "y", "card": "none"})[1] is None
    assert make(tmp_path, {"note": "just a remark"})[1] is None


def test_french_accents_and_chinese_make_cards(tmp_path):
    _, fr = make(tmp_path, {"wrong": "je suis allé a Paris", "right": "je suis allé à Paris", "corrected": "Hier, je suis allé à Paris."}, "fr")
    assert fr["kind"] == "cloze" and "{{c1::à}}" in fr["text"]
    _, zh = make(tmp_path, {"wrong": "我昨天去商店", "right": "我昨天去了商店", "corrected": "我昨天去了商店。"}, "zh")
    assert zh["kind"] == "cloze" and zh["text"] == "我昨天去{{c1::了}}商店。"


def test_same_error_again_is_a_recurrence_not_a_second_card(learner_dir):
    from language_practice.store import Learner
    language = Learner(learner_dir).language("en")
    deck = deckmod.Deck(language)
    error = {"wrong": "must has", "right": "must have", "corrected": "Everyone must have a ticket."}
    first, new1 = deck.add(deckmod.build_from_error("en", error, now()), now())
    config = Learner(learner_dir).config
    deck.grade(first["id"], "good", config, now())
    later = now() + timedelta(hours=1)
    again, new2 = deck.add(deckmod.build_from_error("en", error, later), later)
    assert (new1, new2) == (True, False)
    assert len(deck.cards) == 1 and again["occurrences"] == 2
    assert deckmod.due_at(again) <= later  # the card is due again straight away


def test_grading_schedules_in_days_and_logs_the_review(learner_dir):
    from language_practice.store import Learner, read_jsonl
    learner = Learner(learner_dir)
    language = learner.language("en")
    deck = deckmod.Deck(language)
    card, _ = deck.add(deckmod.build_from_error("en", {"wrong": "a", "right": "b"}, now()), now())
    good = deck.grade(card["id"], "good", learner.config, now(), session="s1", answer="b")
    due = clock.parse(good["next_due"])
    assert timedelta(days=1) < due - now() < timedelta(days=4)  # no intra-day learning steps by default
    deck.save()
    log = read_jsonl(language.reviews_path)
    assert log[0]["rating_name"] == "good" and log[0]["session"] == "s1" and log[0]["answer"] == "b"
    assert deck.due(now(), "America/New_York") == []
    assert len(deck.due(now() + timedelta(days=4), "America/New_York")) == 1


def test_due_means_due_by_the_end_of_the_learners_day(learner_dir):
    from language_practice.store import Learner
    deck = deckmod.Deck(Learner(learner_dir).language("en"))
    card = deckmod.build_from_error("en", {"wrong": "a", "right": "b"}, now())
    card["fsrs"]["due"] = clock.iso(clock.parse("2026-11-18T03:00:00+00:00"))  # 22:00 New York, same local day
    deck.cards.append(card)
    assert len(deck.due(now(), "America/New_York")) == 1
    assert deck.due(now(), "America/New_York", include_today=False) == []


def test_bad_rating_is_an_error(learner_dir):
    import pytest
    from language_practice.store import Learner, PracticeError
    learner = Learner(learner_dir)
    deck = deckmod.Deck(learner.language("en"))
    card, _ = deck.add(deckmod.build_from_error("en", {"wrong": "a", "right": "b"}, now()), now())
    with pytest.raises(PracticeError):
        deck.grade(card["id"], "perfect", learner.config, now())
