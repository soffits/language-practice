from language_practice.text import (cloze_answers, cloze_back, cloze_front, count_units, find_span, make_cloze,
                                    minimal_change, normalize)


def test_normalize_keeps_accents_but_not_case_or_edge_punctuation():
    assert normalize("  À la maison. ") == "à la maison"
    assert normalize("a") != normalize("à")
    assert normalize("é") == normalize("é")  # NFC


def test_count_units_words_and_cjk():
    assert count_units("I don't know well-known people.") == 5
    assert count_units("我喜欢学习英语") == 7
    assert count_units("I like 苹果 pie") == 5  # 3 words + 2 characters
    assert count_units("") == 0


def test_cloze_on_whole_words_only():
    assert make_cloze("They have a heave of work.", "have") == "They {{c1::have}} a heave of work."
    assert make_cloze("He is here.", "she") is None
    assert make_cloze("Il est arrivé à l'heure.", "à") == "Il est arrivé {{c1::à}} l'heure."


def test_cloze_cjk_substring():
    assert make_cloze("我昨天去了商店。", "去了") == "我昨天{{c1::去了}}商店。"


def test_cloze_views():
    text = "I look forward to {{c1::joining}} you."
    assert cloze_front(text) == "I look forward to ____ you."
    assert cloze_answers(text) == ["joining"]
    assert cloze_back(text) == "I look forward to joining you."


def test_minimal_change_trims_shared_words_and_characters():
    assert minimal_change("people who has", "people who have") == ("has", "have")
    assert minimal_change("must has", "must have") == ("has", "have")
    assert minimal_change("我昨天去商店", "我昨天去了商店") == ("", "了")
    assert minimal_change("universal vibe", "broader appeal") == ("universal vibe", "broader appeal")
    assert find_span("a b", "") is None
