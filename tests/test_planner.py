"""The plain words planner with Muse Spark, OpenStreetMap and Earth Search replaced by recorded shapes."""
import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import api, meta_client, planner
from app.api import app

DADU = [67.70, 26.70, 67.80, 26.79]


def call(name, args, cid):
    return SimpleNamespace(type="function_call", name=name, arguments=json.dumps(args), call_id=cid)


def turn(rid, *items):
    return SimpleNamespace(id=rid, output=list(items))


def good_plan(**change):
    return {"place_name": "Dadu, Sindh", "west": DADU[0], "south": DADU[1], "east": DADU[2], "north": DADU[3],
            "before": "2022-06-20", "after": "2022-09-05", "window_days": 15, "looking_for": ["new water", "x"],
            "why": "The floods peaked in late August 2022.", **change}


@pytest.fixture
def fake_world(monkeypatch):
    """Records every Responses call; geocoder and imagery search answer from fixed data."""
    sent = []
    monkeypatch.setattr(planner, "find_place", lambda q: {"matches": [
        {"name": "Dadu, Sindh, Pakistan", "lat": 26.73, "lon": 67.77, "suggested_box": DADU}]})
    monkeypatch.setattr(planner, "check_imagery", lambda bbox, day, w: {
        "scenes": 4, "clearest": [{"date": str(day), "scene_cloud_percent": 12}]})

    def install(replies):
        it = iter(replies)

        def fake_respond(input, tools=None, instructions=None, previous_response_id=None):
            sent.append({"input": input, "tools": tools, "prev": previous_response_id, "instructions": instructions})
            return next(it)
        monkeypatch.setattr(meta_client, "respond", fake_respond)
        return sent
    return install


def test_planner_searches_looks_up_checks_and_returns_a_plan(fake_world):
    search = SimpleNamespace(type="web_search_call", action=SimpleNamespace(query="2022 Sindh floods peak date"),
                             results=[SimpleNamespace(url="https://example.org/floods", title="Sindh floods 2022")])
    cited = SimpleNamespace(type="message", content=[SimpleNamespace(annotations=[
        SimpleNamespace(type="url_citation", url="https://example.org/peak", title="Peak report")])])
    sent = fake_world([
        turn("r1", search, cited, call("find_place", {"query": "Dadu"}, "c1")),
        turn("r2", call("check_imagery", {"west": 67.7, "south": 26.7, "east": 67.8, "north": 26.79,
                                          "date": "2022-09-05", "window_days": 15}, "c2")),
        turn("r3", call("submit_plan", good_plan(), "c3")),
    ])
    p = planner.plan("What did the 2022 floods do near Dadu?")
    assert p["ok"] and p["place"] == "Dadu, Sindh" and p["bbox"] == DADU
    assert (p["before"], p["after"], p["window_days"]) == ("2022-06-20", "2022-09-05", 15)
    assert p["looking_for"] == ["new water"]                     # unknown kinds are dropped
    assert {s["url"] for s in p["sources"]} == {"https://example.org/floods", "https://example.org/peak"}
    assert [s["step"] for s in p["steps"]] == ["search", "place", "imagery", "plan"]
    # web search on, the conversation threaded by response id, tool results matched to their calls
    assert any(t.get("type") == "web_search" for t in sent[0]["tools"])
    assert [s["prev"] for s in sent] == [None, "r1", "r2"]
    assert sent[1]["input"][0]["call_id"] == "c1" and "Dadu" in sent[1]["input"][0]["output"]
    assert str(date.today()) in sent[0]["instructions"]


@pytest.mark.parametrize("bad, reason", [
    ({"after": (date.today() + timedelta(days=5)).isoformat()}, "future"),
    ({"east": 69.5, "north": 28.5}, "A box that fits"),
    ({"before": "2015-03-01"}, "2017"),
    ({"before": "2022-10-01"}, "later than"),
    ({"after": "late August"}, "not a date"),
])
def test_a_bad_plan_goes_back_to_the_model_with_the_reason(fake_world, bad, reason):
    sent = fake_world([
        turn("r1", call("submit_plan", good_plan(**bad), "c1")),
        turn("r2", call("submit_plan", good_plan(), "c2")),
    ])
    p = planner.plan("floods near Dadu")
    out = json.loads(sent[1]["input"][0]["output"])
    assert out["accepted"] is False and reason in out["problem"]
    assert p["ok"] and p["after"] == "2022-09-05"
    assert p["steps"][0]["step"] == "fix"


def test_give_up_is_returned_as_a_plain_reason(fake_world):
    fake_world([turn("r1", call("give_up", {"reason": "Satellites cannot count people."}, "c1"))])
    p = planner.plan("how many people live in Dhaka?")
    assert p == {**p, "ok": False, "reason": "Satellites cannot count people."}


def test_a_failing_tool_is_information_not_a_crash(fake_world, monkeypatch):
    def down(q):
        raise ConnectionError("geocoder down")
    monkeypatch.setattr(planner, "find_place", down)
    sent = fake_world([turn("r1", call("find_place", {"query": "Dadu"}, "c1")),
                       turn("r2", call("submit_plan", good_plan(), "c2"))])
    assert planner.plan("floods near Dadu")["ok"]
    assert "geocoder down" in sent[1]["input"][0]["output"]


def test_text_without_a_plan_gets_a_nudge_and_the_loop_is_bounded(fake_world, monkeypatch):
    monkeypatch.setattr(planner.C, "PLANNER_MAX_STEPS", 3)
    talk = SimpleNamespace(type="message", content=[SimpleNamespace(annotations=[])])
    sent = fake_world([turn(f"r{i}", talk) for i in range(5)])
    with pytest.raises(planner.PlanError):
        planner.plan("somewhere sometime")
    assert len(sent) == 4 and "submit_plan" in sent[1]["input"][0]["content"]


def test_without_web_search_on_the_account_it_plans_from_the_model(fake_world, monkeypatch):
    class BadRequestError(Exception):
        pass
    replies = iter([turn("r1", call("submit_plan", good_plan(), "c1"))])
    calls = []

    def respond(input, tools=None, instructions=None, previous_response_id=None):
        calls.append(tools)
        if any(t.get("type") == "web_search" for t in tools):
            raise BadRequestError("web_search not enabled")
        return next(replies)
    monkeypatch.setattr(meta_client, "respond", respond)
    p = planner.plan("floods near Dadu")
    assert p["ok"] and len(calls) == 2 and p["steps"][0]["step"] == "note"


def test_suggested_box_keeps_small_places_and_boxes_big_ones():
    district = (90.40, 23.70, 90.50, 23.80)                        # about 10 km: its own outline is used
    assert planner.suggest_box(90.45, 23.75, district) == list(district)
    station = (90.40, 23.70, 90.41, 23.71)                         # 1 km: too small to show anything around it
    wkm, _ = planner._side_km(planner.suggest_box(90.405, 23.705, station))
    assert abs(wkm - 10) < 0.1
    country = (88.0, 20.6, 92.7, 26.6)
    w, s, e, n = planner.suggest_box(90.4, 23.7, country)
    wkm, hkm = planner._side_km((w, s, e, n))
    assert abs(wkm - 10) < 0.1 and abs(hkm - 10) < 0.1


def test_find_place_parses_nominatim_and_caches(monkeypatch):
    hits = []

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"display_name": "Dadu, Sindh, Pakistan", "lat": "26.73", "lon": "67.77", "category": "place",
                     "type": "town", "boundingbox": ["26.70", "26.79", "67.72", "67.82"]}]

    def get(url, params, headers, timeout):
        hits.append((params, headers))
        return R()
    monkeypatch.setattr(planner.requests, "get", get)
    monkeypatch.setattr(planner, "_GEO_CACHE", {})
    monkeypatch.setattr(planner, "_GEO_LAST", [0.0])
    out = planner.find_place("Dadu")
    m = out["matches"][0]
    assert (m["lat"], m["lon"], m["kind"]) == (26.73, 67.77, "place town")
    assert m["suggested_box"] == [67.72, 26.7, 67.82, 26.79]
    assert planner.find_place(" dadu ") is out and len(hits) == 1
    assert hits[0][0]["accept-language"] == "en"
    assert "WhatChangedHere" in hits[0][1]["User-Agent"]


def test_check_imagery_keeps_the_clearest_look_per_date(monkeypatch):
    items = [{"properties": {"datetime": "2022-09-03T05:40:00Z", "eo:cloud_cover": 40}},
             {"properties": {"datetime": "2022-09-03T05:40:10Z", "eo:cloud_cover": 8}},
             {"properties": {"datetime": "2022-08-29T05:40:00Z", "eo:cloud_cover": 22}}]
    monkeypatch.setattr(planner.imagery, "search", lambda bbox, start, end: items)
    out = planner.check_imagery(DADU, date(2022, 9, 5), 10)
    assert out["scenes"] == 2
    assert out["clearest"] == [{"date": "2022-09-03", "scene_cloud_percent": 8},
                               {"date": "2022-08-29", "scene_cloud_percent": 22}]


# ---------------------------------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(meta_client.C, "META_API_KEY", "x")
    monkeypatch.setattr(planner, "_RECENT", __import__("collections").deque())
    return TestClient(app)


def test_plan_endpoint_needs_a_key(client, monkeypatch):
    monkeypatch.setattr(meta_client.C, "META_API_KEY", "")
    r = client.post("/api/plan", json={"question": "floods near Dadu"})
    assert r.status_code == 503 and "MODEL_API_KEY" in r.json()["detail"]


def test_plan_endpoint_returns_a_plan_and_the_run_carries_the_question(client, monkeypatch):
    plan = {"ok": True, "place": "Dadu", "bbox": DADU, "before": "2022-06-20", "after": "2022-09-05",
            "window_days": 15, "looking_for": ["new water"], "why": "Peak in late August.", "sources": [],
            "steps": [], "question": "floods near Dadu?"}
    monkeypatch.setattr(planner, "plan", lambda q: plan)
    started = {}

    def fake_start(bbox, before, after, window_days, question="", plan=None):
        started.update(question=question, plan=plan)
        return SimpleNamespace(id="run1")
    monkeypatch.setattr(api.analysis, "start", fake_start)

    j = client.post("/api/plan", json={"question": "floods near Dadu?"}).json()
    assert j["ok"] and j["bbox"] == DADU and j["id"] in api.PLANS
    body = {"west": DADU[0], "south": DADU[1], "east": DADU[2], "north": DADU[3], "before": j["before"],
            "after": j["after"], "window_days": j["window_days"], "plan_id": j["id"]}
    assert client.post("/api/analyze", json=body).json() == {"id": "run1"}
    assert started["question"] == "floods near Dadu?" and started["plan"]["why"] == "Peak in late August."
    assert client.post("/api/analyze", json={**body, "plan_id": "nope"}).status_code == 404


def test_plan_endpoint_is_rate_limited(client, monkeypatch):
    monkeypatch.setattr(planner.C, "PLANS_PER_HOUR", 1)
    monkeypatch.setattr(planner, "plan", lambda q: {"ok": False, "reason": "no", "steps": [], "sources": [],
                                                    "question": q})
    assert client.post("/api/plan", json={"question": "floods near Dadu"}).status_code == 200
    assert client.post("/api/plan", json={"question": "floods near Dadu"}).status_code == 429


def test_plan_errors_are_reported_plainly(client, monkeypatch):
    def stuck(q):
        raise planner.PlanError("could not settle")
    monkeypatch.setattr(planner, "plan", stuck)
    r = client.post("/api/plan", json={"question": "somewhere"})
    assert r.status_code == 422 and "could not settle" in r.json()["detail"]


def test_report_task_answers_the_question_first():
    from app.agent import REPORT_TASK, report_task
    a = SimpleNamespace(question="", plan={})
    assert report_task(a) == REPORT_TASK
    a = SimpleNamespace(question="Did the floods reach the farms?", plan={"why": "Peak in late August."})
    t = report_task(a)
    assert "Did the floods reach the farms?" in t and "Peak in late August." in t and "direct answer" in t
