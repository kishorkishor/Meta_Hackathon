"""SAM 3.1 as a second witness, with SAM replaced by masks drawn on the synthetic burn scene."""
import io

import numpy as np
import pytest
from PIL import Image

from app import agent, meta_client, witness
from app.report import facts
from tests.test_api import BODY, client, wait  # noqa: F401  (client is a fixture)
from tests.test_core import N
from tests.test_meta import FakeAsyncClient, make_analysis, tmp_cache  # noqa: F401


def mask(img, ys, xs):
    """A mask on the (possibly doubled) SAM image, given in grid pixels."""
    s = img.shape[0] // N
    m = np.zeros(img.shape[:2], bool)
    m[ys.start * s:ys.stop * s, xs.start * s:xs.stop * s] = True
    return m


def fake_sam(monkeypatch, draw):
    """draw(phrase, when, img) -> list of instances; `when` is told apart by which composite the image is."""
    asked = []

    def segment_many(jobs):
        out = []
        for i, (img, phrase) in enumerate(jobs):
            when = "before" if i % 2 == 0 else "after"     # jobs come as (phrase, before), (phrase, after)
            asked.append((phrase, when))
            r = draw(phrase, when, img)
            out.append(r)
        return out
    monkeypatch.setattr(meta_client, "segment_many", segment_many)
    return asked


def test_sam_agreement_per_patch_and_places_only_sam_saw(monkeypatch):
    a = make_analysis()
    region = a.regions[0]["properties"]
    assert region["kind"] == "burned land"

    def draw(phrase, when, img):
        if when == "before":
            return []
        return [{"mask": mask(img, slice(10, 30), slice(10, 24)), "confidence": 0.8},   # 70% of the burn
                {"mask": mask(img, slice(32, 38), slice(32, 38)), "confidence": 0.7}]   # a scar the rules missed
    asked = fake_sam(monkeypatch, draw)
    w = witness.second_witness(a)
    assert asked == [("burn scar", "before"), ("burn scar", "after")]    # only concepts for change types found
    assert region["sam_witness"] == {"share": 0.7, "verdict": "agrees"}
    assert w["agree_share"] == 0.7 and w["calls"] == 2
    px_ha = a.grid.pixel_area_ha()
    assert w["both_ha"] == round(280 * px_ha, 1) and w["only_rules_ha"] == round(120 * px_ha, 1)
    assert len(w["only_sam_places"]) == 1 and w["only_sam_places"][0]["kind"] == "burned land"
    rgba = np.asarray(Image.open(io.BytesIO(a.layers["agreement"])))
    assert tuple(rgba[15, 15]) == witness.COLOURS["both"]
    assert tuple(rgba[15, 27]) == witness.COLOURS["rules_only"]
    assert tuple(rgba[35, 35]) == witness.COLOURS["sam_only"]
    assert rgba[2, 2, 3] == 0
    assert facts(a)["largest_regions"][0]["sam_witness"] == "agrees"
    assert facts(a)["sam_second_witness"]["both_ha"] == w["both_ha"]


def test_a_scar_already_there_before_is_not_new_change(monkeypatch):
    a = make_analysis()
    fake_sam(monkeypatch, lambda phrase, when, img: [{"mask": mask(img, slice(10, 30), slice(10, 30)),
                                                      "confidence": 0.9}])
    w = witness.second_witness(a)
    assert a.regions[0]["properties"]["sam_witness"]["verdict"] == "does not see it"
    assert w["both_ha"] == 0 and w["only_sam_ha"] == 0


def test_low_confidence_instances_are_ignored(monkeypatch):
    a = make_analysis()
    fake_sam(monkeypatch, lambda phrase, when, img: [] if when == "before" else
             [{"mask": mask(img, slice(10, 30), slice(10, 30)), "confidence": 0.1}])
    witness.second_witness(a)
    assert a.regions[0]["properties"]["sam_witness"]["share"] == 0.0


def test_unseen_ground_never_counts_for_sam(monkeypatch):
    a = make_analysis()
    a.det.seen[30:40, 30:40] = False
    fake_sam(monkeypatch, lambda phrase, when, img: [] if when == "before" else
             [{"mask": mask(img, slice(32, 38), slice(32, 38)), "confidence": 0.9}])
    assert witness.second_witness(a)["only_sam_ha"] == 0


def test_failed_sam_calls_are_reported_or_raised(monkeypatch):
    a = make_analysis()
    monkeypatch.setattr(meta_client, "segment_many", lambda jobs: [TimeoutError("slow")] * len(jobs))
    with pytest.raises(TimeoutError):
        witness.second_witness(a)
    monkeypatch.setattr(meta_client, "segment_many", lambda jobs: [TimeoutError("slow"), []])
    w = witness.second_witness(a)
    assert w["failed_concepts"] == ["burn scar"] and "failed" in w["by_kind"]["burned land"]["note"]


def test_no_patches_means_no_sam_calls(monkeypatch):
    a = make_analysis()
    a.regions = []
    monkeypatch.setattr(meta_client, "segment_many", lambda jobs: pytest.fail("SAM should not be called"))
    assert "note" in witness.second_witness(a)


def test_segment_many_sends_one_concept_per_request_and_keeps_order(monkeypatch):
    class Picky(FakeAsyncClient):
        async def create(self, **kw):
            if kw["input"][0]["content"][0]["text"] == "trees":
                raise ConnectionError("down")
            return await super().create(**kw)
    fake = Picky("")
    monkeypatch.setattr(meta_client, "_client", lambda asynchronous=False: fake)
    img = np.zeros((20, 20, 3), np.uint8)
    out = meta_client.segment_many([(img, "water"), (img, "trees"), (img, "burn scar")])
    assert out[0] == [] and isinstance(out[1], ConnectionError) and out[2] == []
    assert sorted(c["input"][0]["content"][0]["text"] for c in fake.calls) == ["burn scar", "water"]


def test_agent_tool_reuses_the_result_and_respects_the_limit(monkeypatch):
    a = make_analysis()
    calls = []
    monkeypatch.setattr(agent, "second_witness", lambda run: calls.append(1) or {"both_ha": 1.0})
    assert agent._run_tool(a, "second_witness", {}) == {"both_ha": 1.0}
    a.witness = {"both_ha": 1.0}
    assert agent._run_tool(a, "second_witness", {}) == {"both_ha": 1.0} and len(calls) == 1
    a.witness, a.witnesses_made = {}, 99
    assert "limit" in agent._run_tool(a, "second_witness", {})["error"]


def test_witness_endpoint(client, monkeypatch):  # noqa: F811
    rid = client.post("/api/analyze", json=BODY).json()["id"]
    wait(client, rid)
    assert client.post(f"/api/analyze/{rid}/witness").status_code == 503
    monkeypatch.setattr(meta_client.C, "META_API_KEY", "x")
    fake_sam(monkeypatch, lambda phrase, when, img: [] if when == "before" else
             [{"mask": mask(img, slice(10, 30), slice(10, 30)), "confidence": 0.9}])
    j = client.post(f"/api/analyze/{rid}/witness").json()
    assert "agreement" in j["layers"] and j["witness"]["agree_share"] == 1.0
    assert j["regions"]["features"][0]["properties"]["sam_witness"]["verdict"] == "agrees"
    assert client.get(f"/api/analyze/{rid}/layer/agreement").status_code == 200
    client.post(f"/api/analyze/{rid}/witness")
    assert client.post(f"/api/analyze/{rid}/witness").status_code == 429
