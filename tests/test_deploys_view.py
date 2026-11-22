"""brain-kit's deploy records as the pages say them (backend/core/deploys_view.py): anything the app does not know is
unknown and never live, a pipeline's link is only ever on the repo's own GitLab, and the eval's 部署 numbers of
several repos add up the way each one is counted."""

import time

import pytest

import signinfix
from appkit import BRAINKIT, mod
from brainfix import make_project, make_service, needs_brainkit

V = mod("deploys_view")
GL = "https://gitlab.example.com"
CFG = {"gitlab": {"base": GL, "project": "shop/pay"}}
LIVE = "sha256:168d16c5a244384d3d434ccd4adc5a8e6ee168b4ce638e7653ed9541f82750e1"
SHA = "24d8188c606e9434a622519515dacc6397947262"


def trace(dep):
    return {"outcome": {"deploy": dep}}


def attempt(**over):
    return dict({"deployment": 844623, "pipeline": 4138161, "job": 34418480, "web_url": GL + "/shop/pay/-/jobs/34418480",
                 "ref": "main", "sha": SHA, "created_at": "2026-11-18T05:13:28Z", "finished_at": "2026-11-18T05:15:35Z",
                 "status": "success", "result": "promoted", "why": None, "digest": LIVE, "stable_digest_after": LIVE,
                 "reason": "every canary step passed", "failed_checks": []}, **over)


def test_nothing_to_show_when_deploys_are_off_not_merged_or_unread_by_an_older_brain_kit():
    assert V.requirement(None, CFG) is None
    assert V.requirement({"outcome": {}}, CFG) is None                     # a brain-kit whose trace has no deploys
    assert V.requirement(trace({"value": None, "environment": None, "attempts": []}), CFG) is None


def test_a_value_or_a_result_the_app_does_not_know_is_unknown_never_live():
    d = V.requirement(trace({"value": "rolled_back", "environment": "staging", "digest": LIVE, "at": "2026-11-18T05:15:35Z",
                             "lead_time_hours": 0.09, "attempts": [attempt(result="rolled_back")], "missing": []}), CFG)
    assert (d["value"], d["digest"], d["at"], d["lead_s"]) == ("unknown", None, None, None)
    assert d["attempts"][0]["result"] == "unknown" and d["why"] == V.UNREAD


def test_a_pipeline_link_is_only_ever_on_the_repos_own_gitlab():
    assert V.requirement(trace({"value": "promoted", "attempts": [attempt()]}), CFG)["attempts"][0]["url"] == \
        GL + "/shop/pay/-/pipelines/4138161"
    by_number = {"gitlab": {"base": GL, "project": 42}}       # a project named by its number: GitLab's own page of the job
    assert V.requirement(trace({"value": "promoted", "attempts": [attempt()]}), by_number)["attempts"][0]["url"] == \
        GL + "/shop/pay/-/jobs/34418480"
    elsewhere = attempt(web_url="https://evil.example.net/jobs/1")
    assert V.requirement(trace({"value": "promoted", "attempts": [elsewhere]}), by_number)["attempts"][0]["url"] is None
    assert V.requirement(trace({"value": "promoted", "attempts": [attempt()]}), {})["attempts"][0]["url"] is None


def test_the_eval_says_its_three_numbers_and_what_they_leave_out():
    assert V.metrics({"environment": None, "src": "off"}) == [] and V.notes({"environment": None}, None) == []
    dp = {"environment": "staging", "frequency": {"per_week": 1.75}, "lead_time_hours": {"median": 0.5, "by_run": {"A-1": 0.5}},
          "change_failure": {"value": [1, 4], "failed_open": ["A-3"]},
          "missing": ["deployment 844613 (5d7ea083a279): job 34417471 has no deploy-evidence.json (never written, or expired)"]}
    assert {m["id"]: m["value"] for m in V.metrics(dp)} == {"deploy_frequency": 1.75, "lead_time": 0.5,
                                                            "change_failure": [1, 4]}
    said = V.notes(dp, ["A-2"])
    assert [n["text"] for n in said] == [
        "staging 有部署读不到结果（部署证据不在了：没写，或者过期了），不算进部署频率，也不算上线",
        "已知变更失败、14 天还没满（满了再算进变更失败率）：A-3",
        "合并满 14 天了，可部署结果读不到，不算进交付正确率：A-2"]
    assert "never written, or expired" in said[0]["raw"] and all(n["todo"] for n in said)


@needs_brainkit
def test_several_repos_numbers_add_up_and_their_lead_time_is_one_median_over_all_their_requirements(tmp_path, home):
    """全部: deploys a week add up, change failures add up, 合并到上线 is the median of every repo's requirements (not a
    sum of medians); a repo whose kit.json reads no deploys hides no other's."""
    repos = [make_project(tmp_path, name, prefix) for name, prefix in (("pay", "PAY"), ("order", "ORD"), ("web", "WEB"))]
    svc = make_service(tmp_path, repos)
    svc.reload_sync()
    D = mod("deliveries")

    def dora(per_week, failed, hours):
        return V.metrics({"environment": "staging", "frequency": {"per_week": per_week},
                          "lead_time_hours": {"median": sorted(hours)[len(hours) // 2], "by_run": {}},
                          "change_failure": {"value": failed}})

    base = [{"id": "autonomy", "label": "自主度", "value": [1, 2], "rule": ""}]
    for rid, metrics, hours in (("r1", base, []), ("r2", base + dora(1.5, [1, 4], [5.0]), [5.0]),
                                ("r3", base + dora(0.5, [0, 3], [0.1, 0.2, 10.0]), [0.1, 0.2, 10.0])):
        repo = svc.repos[rid]
        sha = repo.git.out("rev-parse", "-q", "--verify", repo.main_ref() + "^{commit}")
        view = {"available": True, "error": "", "raw": "", "ref": repo.main_ref(), "metrics": metrics, "queue": [],
                "skipped": [], "notes": [], "note_rows": [], "families": [], "_cto": dict(D.cto_part({}), lead_hours=hours)}
        svc._eval[rid] = (sha, time.monotonic(), view, None)
    merged, _ = svc._eval_view("all", svc._sessions_snapshot())
    got = {m["id"]: m["value"] for m in merged["metrics"]}
    assert got["autonomy"] == [3, 6]
    assert (got["deploy_frequency"], got["change_failure"]) == (2.0, [1, 7])
    assert got["lead_time"] == 2.6, "the median of 0.1, 0.2, 5 and 10 hours"


@needs_brainkit
def test_the_client_kept_for_a_gitlab_takes_a_new_sign_in_without_a_restart(home, monkeypatch):
    """A GitLab behind single sign-on, whose sign-in helper's transports keep the sign-in they were made with: the
    client Clients keeps for it stops working when the session ends. The next one asked for after that is made again,
    so the deploys are read once the person signs in again here, with no restart."""
    bk = mod("bk")
    assert bk.load(str(BRAINKIT))["ok"]
    try:
        monkeypatch.setenv("GITLAB_TOKEN", "fake-person-token")
        sso = signinfix.install(lambda method, url, headers, body: (200, b'{"username": "alice"}'), "new")
        clients = V.Clients()
        assert clients.gitlab(GL).current_user() == {"username": "alice"}
        sso.expire()
        with pytest.raises(bk.m("common").BrainError, match="sign in again"):
            clients.gitlab(GL).current_user()
        sso.sign_in_again()
        assert clients.gitlab(GL).current_user() == {"username": "alice"}
    finally:
        bk.unload()
