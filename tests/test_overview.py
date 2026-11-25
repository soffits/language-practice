"""The one-call views: `overview` answers what separate status, plan and session calls would, and `progress --recent`
adds the facts and the latest sessions and attempts in full."""


def test_overview_without_a_session_is_status_and_plan(learner_dir, run):
    _, overview = run("overview")
    _, status = run("status")
    _, plan = run("plan")
    assert overview["languages"] == ["en"] and overview["lang"] == "en"
    assert overview["status"] == status and overview["plan"] == plan
    assert overview["session"] is None and overview["attempts"] == []


def test_overview_with_an_open_session_shows_it_and_its_attempts(learner_dir, run):
    _, session = run("session", "start", "--task", "celpip.reading.1")
    _, added = run("attempt", "add", "--task", "celpip.reading.1", "--correct", "7", "--total", "10", "--minutes", "12")
    _, overview = run("overview")
    _, shown = run("session", "show", session["id"])
    _, attempt = run("attempt", "show", added["attempt"]["id"])
    assert overview["plan"] is None and overview["session"] == shown
    assert overview["attempts"] == [attempt]


def test_overview_falls_back_to_the_first_language(learner_dir, run):
    run("init", "--lang", "fr")
    _, overview = run("--lang", "de", "overview")
    assert overview["languages"] == ["en", "fr"] and overview["lang"] == "en"
    _, chosen = run("--lang", "fr", "overview")
    assert chosen["lang"] == "fr" and chosen["status"]["lang"] == "fr"


def test_overview_with_no_language_yet(learner_dir, run):
    (learner_dir / "en" / "profile.json").unlink()
    code, overview = run("overview")
    assert code == 0 and overview["languages"] == [] and overview["lang"] is None


def test_progress_recent_adds_facts_sessions_and_attempts(learner_dir, run):
    _, plain = run("progress")
    assert "recent" not in plain and "facts" not in plain
    first = run("session", "start", "--task", "celpip.reading.1")[1]
    one = run("attempt", "add", "--task", "celpip.reading.1", "--correct", "7", "--total", "10")[1]["attempt"]["id"]
    run("session", "finish", first["id"])
    run("session", "start", "--task", "celpip.reading.1")
    two = run("attempt", "add", "--task", "celpip.reading.1", "--correct", "8", "--total", "10")[1]["attempt"]["id"]
    _, progress = run("progress", "--recent", "1")
    _, facts = run("facts", "--lang", "en")
    assert progress["facts"] == facts["languages"]["en"]
    _, listed = run("session", "list")
    assert progress["recent"]["sessions"] == listed["sessions"][-1:]
    _, attempts = run("attempt", "list")
    newest = attempts["attempts"][-1]["id"]
    assert progress["recent"]["attempts"] == [run("attempt", "show", newest)[1]]
    _, both = run("progress", "--recent", "5")
    assert both["recent"]["sessions"] == listed["sessions"][::-1]
    assert {a["id"] for a in both["recent"]["attempts"]} == {one, two}
    assert [a["id"] for a in both["recent"]["attempts"]] == [a["id"] for a in attempts["attempts"]][::-1]
