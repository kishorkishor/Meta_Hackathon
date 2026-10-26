"""Meta steps with fake Muse Spark and SAM 3.1 responses: SAM parsing, outline refinement, visual checks, agent loop."""
import json
from types import SimpleNamespace

import numpy as np
import pytest
from meta_sam_parser._mask_codec import _encode_raster

from app import agent, analysis, meta_client, refine
from app.detect import BURN, detect
from app.imagery import Composite, Grid
from tests.test_core import BURNED, FOREST, N, paint, scene


# ---------------------------------------------------------------------------------------------------
# a fake SAM 3.1 stream in the documented wire format
# ---------------------------------------------------------------------------------------------------

def sam_line(objects, w, h):
    recs = []
    for oid, (l, t, r, b), raster in objects:
        mh, mw = raster.shape
        payload = _encode_raster(raster.astype(int).ravel().tolist(), mw, mh)
        recs.append(f"{oid}<|box;x1={l};y1={t};x2={r - 1};y2={b - 1};w={w};h={h};c=0.9|>"
                    f"<|mask;x=0;y=0;c=0.9;data={mh},{mw},{payload}|>")
    return "<0f>" + ",".join(recs)


class FakeAsyncClient:
    def __init__(self, text):
        self.text = text
        self.responses = self
        self.calls = []

    async def create(self, **kw):
        self.calls.append(kw)
        text = self.text

        async def events():
            base = {"item_id": "msg_1", "output_index": 0, "content_index": 0}
            yield {"type": "response.output_text.delta", "delta": text, **base}
            yield {"type": "response.output_text.done", "text": text, **base}
            yield {"type": "response.completed", "response": {"status": "completed"}}
        return events()


def test_segment_parses_boxes_and_places_box_local_masks(monkeypatch):
    w, h = 60, 40
    raster = np.zeros((10, 20), bool)
    raster[2:8, 5:15] = True
    fake = FakeAsyncClient(sam_line([(1, (10, 5, 30, 15), raster)], w, h))
    monkeypatch.setattr(meta_client, "_client", lambda asynchronous=False: fake)
    out = meta_client.segment(np.zeros((h, w, 3), np.uint8), boxes=[(10, 5, 30, 15)])
    assert len(out) == 1
    m = out[0]["mask"]
    assert m.shape == (h, w)
    assert m.sum() == raster.sum()
    assert m[5 + 2, 10 + 5] and not m[5 + 1, 10 + 5]
    assert out[0]["confidence"] == pytest.approx(0.9)
    req = fake.calls[0]
    assert req["model"] == "sam-3.1" and req["stream"] is True
    text = req["input"][0]["content"][0]["text"]
    assert text.startswith("<0f>1<|box;x1=10;y1=5;x2=29;y2=14;w=60;h=40|>")
    assert req["input"][0]["content"][1]["image_url"].startswith("data:image/png;base64,")


def test_segment_with_no_matches_returns_empty(monkeypatch):
    monkeypatch.setattr(meta_client, "_client", lambda asynchronous=False: FakeAsyncClient(""))
    assert meta_client.segment(np.zeros((20, 20, 3), np.uint8), text="burn scar") == []


# ---------------------------------------------------------------------------------------------------
# an analysis built from synthetic composites
# ---------------------------------------------------------------------------------------------------

def make_analysis():
    before = scene(FOREST)
    after = paint(scene(FOREST), BURNED, slice(10, 30), slice(10, 30))
    g = Grid(90.0, 23.0, 90.0078, 23.0072, N, N)
    ones = np.full((N, N), 3, np.int16)
    a = analysis.Analysis("test", g.bbox, None, None, 10)
    a.grid = g
    a.comp_a = Composite(g, before, ones, [{"id": "a", "date": "2026-01-10", "clear_share": 1.0}])
    a.comp_b = Composite(g, after, ones, [{"id": "b", "date": "2026-05-10", "clear_share": 1.0}])
    analysis.finish(a, detect(before, after, ones, ones))
    return a


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(analysis.C, "CACHE", tmp_path)


def test_sam_refinement_cannot_grow_outside_the_detected_area(monkeypatch):
    a = make_analysis()
    assert a.regions[0]["properties"]["kind"] == "burned land"

    def fake_segment(img, boxes=None, text=None):
        scale = img.shape[0] // N
        m = np.zeros(img.shape[:2], bool)
        m[11 * scale:29 * scale, 11 * scale:29 * scale] = True   # a slightly tighter outline: kept
        m[0:5 * scale, 0:5 * scale] = True                       # far away: must be rejected
        return [{"id": "1", "box": boxes[0], "mask": m, "confidence": 0.8}]

    monkeypatch.setattr(meta_client, "segment", fake_segment)
    out = refine.refine_with_sam(a)
    row = out["regions"][0]
    assert row["used"] is True
    assert row["sam_kept_ha"] < row["detector_ha"]
    assert row["rejected_ha"] > 0
    assert "refined" in a.layers


def test_sam_may_add_only_pixels_with_weak_evidence(monkeypatch):
    before = scene(FOREST)
    after = paint(scene(FOREST), BURNED, slice(10, 30), slice(10, 30))
    # lightly burned: dNBR about 0.22, under the 0.27 rule but within its margin; matches no other rule
    light = dict(blue=0.03, green=0.05, red=0.05, nir=0.28, nir08=0.29, swir16=0.16, swir22=0.11)
    paint(after, light, slice(10, 30), slice(30, 32))
    g = Grid(90.0, 23.0, 90.0078, 23.0072, N, N)
    ones = np.full((N, N), 3, np.int16)
    a = analysis.Analysis("weak", g.bbox, None, None, 10)
    a.grid = g
    a.comp_a = Composite(g, before, ones, [])
    a.comp_b = Composite(g, after, ones, [])
    analysis.finish(a, detect(before, after, ones, ones))
    assert (a.det.classes[10:30, 30:32] == 0).all() and (a.det.weak[10:30, 30:32] == BURN).all()

    def fake_segment(img, boxes=None, text=None):
        s = img.shape[0] // N
        m = np.zeros(img.shape[:2], bool)
        m[10 * s:30 * s, 10 * s:33 * s] = True   # covers the burn, the weak strip, and one plain forest column
        return [{"id": "1", "box": boxes[0], "mask": m, "confidence": 0.9}]

    monkeypatch.setattr(meta_client, "segment", fake_segment)
    row = refine.refine_with_sam(a)["regions"][0]
    px = g.pixel_area_ha()
    assert row["added_ha"] == pytest.approx(40 * px, abs=0.01)       # the 2 weak columns, not the forest one
    assert row["rejected_ha"] == pytest.approx(20 * px, abs=0.01)


def test_sam_outline_that_disagrees_is_not_used(monkeypatch):
    a = make_analysis()

    def fake_segment(img, boxes=None, text=None):
        m = np.zeros(img.shape[:2], bool)
        m[0:3, 0:3] = True
        return [{"id": "1", "box": boxes[0], "mask": m, "confidence": 0.5}]

    monkeypatch.setattr(meta_client, "segment", fake_segment)
    row = refine.refine_with_sam(a)["regions"][0]
    assert row["used"] is False and "disagrees" in row["why"]


def test_visual_check_keeps_only_known_verdicts(monkeypatch):
    a = make_analysis()
    answers = iter([{"verdict": "Agrees", "what_you_see": "dark patch", "reason": "char"}])
    monkeypatch.setattr(meta_client, "look", lambda q, imgs: next(answers))
    out = refine.check_regions(a, [1, 99])
    assert out[1]["verdict"] == "agrees"
    assert 99 not in out
    assert refine.check_regions(a, [1])[1]["verdict"] == "agrees"   # cached, no second call


# ---------------------------------------------------------------------------------------------------
# the analyst loop
# ---------------------------------------------------------------------------------------------------

def tool_call(name, args, cid):
    fn = SimpleNamespace(name=name, arguments=json.dumps(args))
    call = SimpleNamespace(id=cid, function=fn)
    call.model_dump = lambda: {"id": cid, "type": "function", "function": {"name": name, "arguments": fn.arguments}}
    return call


def test_agent_calls_tools_then_answers(monkeypatch):
    a = make_analysis()
    seen = []
    replies = iter([
        SimpleNamespace(content="", tool_calls=[tool_call("get_facts", {}, "c1"), tool_call("get_region", {"id": 1}, "c2")]),
        SimpleNamespace(content="Headline: about 1.6 ha burned.", tool_calls=None),
    ])

    def fake_chat(messages, tools=None, json_mode=False):
        seen.append([m["role"] for m in messages])
        return next(replies)

    monkeypatch.setattr(meta_client, "chat", fake_chat)
    text = agent.write_report(a)
    assert text.startswith("Headline")
    assert a.report_source == "muse-spark"
    assert seen[1][-2:] == ["tool", "tool"]
    tool_msgs = [m for m in a.chat if m["role"] == "tool"]
    facts = json.loads(tool_msgs[0]["content"])
    assert facts["change_by_kind"]["burned land"]["area_ha"] > 0


def test_agent_turns_tool_errors_into_information(monkeypatch):
    a = make_analysis()
    replies = iter([
        SimpleNamespace(content="", tool_calls=[tool_call("refine_outlines", {}, "c1")]),
        SimpleNamespace(content="SAM was not reachable, outlines are from the index rules.", tool_calls=None),
    ])
    monkeypatch.setattr(meta_client, "chat", lambda m, tools=None, json_mode=False: next(replies))

    def boom(*a, **k):
        raise ConnectionError("offline")

    monkeypatch.setattr(meta_client, "segment", boom)
    assert "SAM" in agent.write_report(a)
    err = json.loads([m for m in a.chat if m["role"] == "tool"][0]["content"])
    assert "ConnectionError" in err["error"]


def test_agent_stops_after_max_steps(monkeypatch):
    a = make_analysis()
    loop_reply = SimpleNamespace(content="", tool_calls=[tool_call("get_facts", {}, "c")])
    final = SimpleNamespace(content="Done.", tool_calls=None)
    calls = []

    def fake_chat(messages, tools=None, json_mode=False):
        calls.append(tools is not None)
        return loop_reply if tools else final

    monkeypatch.setattr(meta_client, "chat", fake_chat)
    assert agent.write_report(a) == "Done."
    assert calls.count(True) == agent.C.AGENT_MAX_STEPS and calls[-1] is False
