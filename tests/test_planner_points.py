import json
from datetime import timedelta

from language_practice import clock, points, planner
from language_practice.store import Learner


def test_step_logic_works_on_the_first_step_not_yet_reached_in_every_skill():
    profile = {"steps": [7, 8, 9], "target": 9, "levels": {"listening": 6, "reading": 8, "writing": 5, "speaking": 5}}
    assert planner.current_step(profile) == 7
    assert planner.skill_gap(profile, "writing") == 2 and planner.skill_gap(profile, "reading") == 0
    profile["levels"] = {"listening": 7, "reading": 8, "writing": 7, "speaking": 7}
    assert planner.current_step(profile) == 8
    profile["levels"] = {k: 9 for k in profile["levels"]}
    assert planner.current_step(profile) == 9 and planner.skill_gap(profile, "writing") == 0


def test_plan_fits_the_time_and_puts_the_speaking_debt_first(learner_dir, run):
    code, plan = run("plan", "--minutes", "12")
    assert code == 0 and plan["minutes_planned"] <= 12
    assert plan["items"][0]["skill"] == "speaking"
    assert any("speaking debt" in r for r in plan["priorities"][0]["reasons"])


def test_plan_has_a_review_block_when_cards_are_due(learner_dir, run, tmp_path):
    errors = tmp_path / "e.json"
    errors.write_text(json.dumps([{"wrong": "a b", "right": "a c", "corrected": "a c d"}] * 1 + [{"wrong": f"x{i}", "right": f"y{i}"} for i in range(9)]))
    run("attempt", "add", "--skill", "writing", "--text", "hello there", "--errors", str(errors))
    _, plan = run("plan", "--minutes", "15")
    review = plan["items"][0]
    assert review["kind"] == "review" and review["cards"] == 10 and review["minutes"] == 5
    assert plan["minutes_planned"] <= 15


def test_light_language_plans_small_steps(tmp_path, run):
    run("init", "--lang", "fr", "--exam", "tef-canada", "--dreaded", "--minutes", "10", "--max-items", "2", "--prefer-mini")
    _, plan = run("plan", "--lang", "fr")
    assert plan["light"] and len(plan["items"]) <= 2 and plan["minutes_planned"] <= 10
    assert all(i["variant"] == "mini" for i in plan["items"] if i["kind"] == "task")
    assert plan["smallest_step"]["minutes"] == min(i["minutes"] for i in plan["items"])


def test_staleness_rotates_skills_and_tasks(learner_dir, run):
    run("attempt", "add", "--task", "celpip.speaking.1", "--text", "typed instead of spoken")
    _, plan = run("plan", "--minutes", "30")
    speaking = next(i for i in plan["items"] if i["skill"] == "speaking")
    assert speaking["task"] != "celpip.speaking.1"


def test_points_follow_minutes_and_kinds_of_work():
    cfg = Learner.__init__.__globals__["DEFAULT_CONFIG"]["points"]
    assert points.compute(cfg, False, 15, 0, 0, 0)["points"] == 5
    assert points.compute(cfg, False, 15, 1, 0, 0)["points"] == 8
    assert points.compute(cfg, False, 15, 0, 0, 10)["points"] == 7
    assert points.compute(cfg, False, 90, 5, 3, 40)["points"] == 20  # session cap
    assert points.compute(cfg, False, 0, 0, 0, 0)["points"] == 0
    assert points.compute(cfg, False, 3, 0, 0, 1)["points"] == 2  # the start counts


def test_dreaded_language_earns_more_for_smaller_steps():
    cfg = Learner.__init__.__globals__["DEFAULT_CONFIG"]["points"]
    normal = points.compute(cfg, False, 10, 1, 0, 0)["points"]
    dreaded = points.compute(cfg, True, 10, 1, 0, 0)["points"]
    assert dreaded > normal
    assert points.compute(cfg, True, 120, 5, 3, 40)["points"] == 30


def test_points_ignore_levels_estimates_and_errors(learner_dir, run, fake_ledger, tmp_path):
    run("config", "set", "rewards", json.dumps({"command": fake_ledger.command, "user": "t"}))
    totals = []
    for estimate, error_count in (("5", 0), ("11-12", 6)):
        _, session = run("session", "start", "--task", "celpip.writing.1")
        errs = tmp_path / "errs.json"
        errs.write_text(json.dumps([{"wrong": f"w{i}{estimate}", "right": "r"} for i in range(error_count)]))
        args = ["attempt", "add", "--task", "celpip.writing.1", "--text", "word " * 160, "--minutes", "15", "--estimate", estimate]
        if error_count:
            args += ["--errors", str(errs)]
        run(*args)
        _, done = run("session", "finish", session["id"], "--minutes", "15")
        totals.append(done["points"]["points"])
    assert totals[0] == totals[1] == 8  # 5 for 15 minutes + 3 for a full essay


def test_essay_bonus_needs_a_real_essay_not_a_stub(learner_dir, run):
    _, session = run("session", "start", "--task", "celpip.writing.1")
    run("attempt", "add", "--task", "celpip.writing.1", "--text", "too short", "--minutes", "15")
    _, done = run("session", "finish", "--minutes", "15")
    assert done["points"]["parts"]["essay"] == 0


def test_rewards_command_may_be_a_string_with_a_home_path(learner_dir, run, tmp_path, fake_ledger):
    command = " ".join(["python3", fake_ledger.command[1]])
    run("config", "set", "rewards", json.dumps({"command": command, "user": "t"}))
    run("session", "start", "--task", "celpip.reading.1")
    run("attempt", "add", "--task", "celpip.reading.1", "--correct", "5", "--total", "10", "--minutes", "10")
    _, done = run("session", "finish", "--minutes", "10")
    assert done["points"]["status"] == "earned"
