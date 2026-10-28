"""Muse Spark looking at every big patch, with its answers replaced by fixed JSON."""
import threading

import pytest

from app import agent, meta_client, visual
from app.report import facts
from tests.test_api import BODY, client, wait  # noqa: F401  (client is a fixture)
from tests.test_meta import make_analysis, tmp_cache  # noqa: F401


def two_patches():
    """The synthetic burn plus a copy of it as a second, smaller patch, so ordering and filters show."""
    a = make_analysis()
    first = a.regions[0]
    second = {"type": "Feature", "geometry": first["geometry"],
              "properties": {**first["properties"], "id": 2, "area_ha": 0.5}}
    a.regions.append(second)
    return a


def test_answers_are_cleaned_to_known_values(monkeypatch):
    a = make_analysis()
    seen = []

    def look(q, images, schema=None):
        seen.append((q, [label for label, _ in images], schema))
        return {"verdict": "Disagrees ", "looks_like": "Seasonal change in crops or grass", "what_you_see": "x" * 900,
                "reason": "green fields turned brown"}
    monkeypatch.setattr(meta_client, "look", look)
    out = visual.check_regions(a, [1, 99])
    assert list(out) == [1]
    c = out[1]
    assert c["verdict"] == "disagrees" and c["looks_like"] == "seasonal change in crops or grass"
    assert len(c["what_you_see"]) == 300
    q, labels, schema = seen[0]
    assert labels == ["Before:", "After:"] and "burned land" in q and "ha" in q
    assert "sure" not in q.lower()                       # it is not told how sure the rules were
    assert schema["properties"]["verdict"]["enum"] == visual.VERDICTS


@pytest.mark.parametrize("answer", [{"verdict": "maybe"}, {"raw": "I think it is a fire"}, {}])
def test_an_answer_out_of_shape_becomes_unclear(monkeypatch, answer):
    a = make_analysis()
    monkeypatch.setattr(meta_client, "look", lambda q, images, schema=None: answer)
    c = visual.check_regions(a, [1])[1]
    assert c["verdict"] == "unclear" and c["looks_like"] is None


def test_every_big_patch_is_checked_once_and_summarised(monkeypatch):
    a = two_patches()
    calls = []
    monkeypatch.setattr(meta_client, "look", lambda q, images, schema=None: calls.append(q) or
                        {"verdict": "disagrees", "looks_like": "cloud, haze or shadow", "what_you_see": "a cloud",
                         "reason": "bright white"})
    v = visual.check_every_big_patch(a)
    assert len(calls) == 1                               # the 0.5 ha patch is below the 2 ha floor
    assert v["checked"] == 1 and v["eligible"] == 1 and v["counts"]["disagrees"] == 1
    assert v["ha"]["disagrees"] == round(a.regions[0]["properties"]["area_ha"], 1)
    assert v["flagged"][0]["looks_like"] == "cloud, haze or shadow" and v["flagged"][0]["center"]
    visual.check_every_big_patch(a)
    assert len(calls) == 1                               # cached: asking again costs nothing
    assert facts(a)["largest_regions"][0]["visual_check"] == "disagrees"
    assert facts(a)["visual_check_of_big_patches"]["counts"]["disagrees"] == 1


def test_each_run_goes_further_down_the_list(monkeypatch):
    a = make_analysis()
    for i in range(2, 6):
        a.regions.append({"type": "Feature", "geometry": a.regions[0]["geometry"],
                          "properties": {**a.regions[0]["properties"], "id": i}})
    monkeypatch.setattr(visual, "outline_crop", lambda a, rid, which: None)
    monkeypatch.setattr(visual, "MAX_PATCHES", 2)
    looked = []
    monkeypatch.setattr(meta_client, "look", lambda q, images, schema=None: looked.append(1) or {"verdict": "agrees"})
    v = visual.check_every_big_patch(a)
    assert (v["checked"], v["remaining"], len(looked)) == (2, 3, 2)
    v = visual.check_every_big_patch(a)
    assert (v["checked"], v["remaining"], len(looked)) == (4, 1, 4)
    assert sorted(a.checks) == [1, 2, 3, 4]


def test_patches_are_looked_at_in_parallel(monkeypatch):
    a = make_analysis()
    for i in range(2, 6):
        a.regions.append({"type": "Feature", "geometry": a.regions[0]["geometry"],
                          "properties": {**a.regions[0]["properties"], "id": i}})
    monkeypatch.setattr(visual, "outline_crop", lambda a, rid, which: None)
    gate = threading.Barrier(4, timeout=5)               # only passes if 4 looks are running at the same time

    def look(q, images, schema=None):
        gate.wait()
        return {"verdict": "agrees"}
    monkeypatch.setattr(meta_client, "look", look)
    monkeypatch.setattr(visual, "AT_ONCE", 4)
    out = visual.check_regions(a, [1, 2, 3, 4])
    assert len(out) == 4 and all(c["verdict"] == "agrees" for c in out.values())


def test_a_failed_look_is_not_cached_and_all_failing_raises(monkeypatch):
    a = two_patches()
    a.regions[1]["properties"]["area_ha"] = 5.0
    answers = iter([{"verdict": "agrees"}, ConnectionError("down")])

    def look(q, images, schema=None):
        r = next(answers)
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(visual, "AT_ONCE", 1)            # one at a time so the order is fixed
    monkeypatch.setattr(meta_client, "look", look)
    v = visual.check_every_big_patch(a)
    assert v["checked"] == 1 and 2 not in a.checks       # the failed one is tried again next time
    monkeypatch.setattr(meta_client, "look", lambda q, images, schema=None: (_ for _ in ()).throw(TimeoutError()))
    with pytest.raises(TimeoutError):
        visual.check_regions(a, [2])


def test_agent_can_check_every_big_patch(monkeypatch):
    a = make_analysis()
    monkeypatch.setattr(meta_client, "look", lambda q, images, schema=None: {"verdict": "agrees"})
    out = agent._run_tool(a, "check_every_big_patch", {})
    assert out["counts"]["agrees"] == 1
    assert agent._run_tool(a, "check_regions_visually", {"ids": [1]})["1"]["verdict"] == "agrees"
    a.looks_made = 99
    assert "limit" in agent._run_tool(a, "check_every_big_patch", {})["note"]


def test_check_endpoint(client, monkeypatch):  # noqa: F811
    rid = client.post("/api/analyze", json=BODY).json()["id"]
    wait(client, rid)
    assert client.post(f"/api/analyze/{rid}/check").status_code == 503
    monkeypatch.setattr(meta_client.C, "META_API_KEY", "x")
    monkeypatch.setattr(meta_client, "look", lambda q, images, schema=None: {
        "verdict": "agrees", "looks_like": "burned land", "what_you_see": "dark scar", "reason": "char"})
    j = client.post(f"/api/analyze/{rid}/check").json()
    assert j["visual"]["counts"]["agrees"] == 1 and j["checks"]["1"]["what_you_see"] == "dark scar"
    client.post(f"/api/analyze/{rid}/check")
    client.post(f"/api/analyze/{rid}/check")
    assert client.post(f"/api/analyze/{rid}/check").status_code == 429


def test_structured_output_is_requested_when_a_schema_is_given(monkeypatch):
    sent = {}

    class Fake:
        def __init__(self):
            self.chat = self
            self.completions = self

        def create(self, **kw):
            sent.update(kw)
            msg = type("M", (), {"content": '{"verdict": "agrees"}', "tool_calls": None})
            return type("R", (), {"choices": [type("C", (), {"message": msg})]})
    monkeypatch.setattr(meta_client, "_client", lambda asynchronous=False: Fake())
    import numpy as np
    out = meta_client.look("q", [("Before:", np.zeros((4, 4, 3), np.uint8))], schema=visual.SCHEMA)
    assert out == {"verdict": "agrees"}
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["schema"] is visual.SCHEMA
