"""Field checklist: ranking, Muse Spark's choice kept to real places, and the exports for phones and print."""
import json
from types import SimpleNamespace
from xml.etree import ElementTree

import numpy as np

from app import analysis, fieldlist, meta_client
from app.detect import detect
from app.imagery import Composite, Grid
from tests.test_api import BODY, client, wait  # noqa: F401  (client is a fixture)
from tests.test_core import BURNED, FOREST, N, paint, scene
from tests.test_meta import make_analysis, tmp_cache  # noqa: F401


def c_shaped():
    """A burn shaped like a C: its centre of mass falls in the gap, outside the patch."""
    before = scene(FOREST)
    after = scene(FOREST)
    for rows, cols in ((slice(10, 30), slice(10, 15)), (slice(10, 15), slice(10, 32)), (slice(25, 30), slice(10, 32))):
        after = paint(after, BURNED, rows, cols)
    g = Grid(90.0, 23.0, 90.0078, 23.0072, N, N)
    ones = np.full((N, N), 3, np.int16)
    a = analysis.Analysis("cshape", g.bbox, "2026-01-10", "2026-05-10", 10)
    a.grid = g
    a.comp_a = Composite(g, before, ones, [{"id": "a", "date": "2026-01-10", "clear_share": 1.0}])
    a.comp_b = Composite(g, after, ones, [{"id": "b", "date": "2026-05-10", "clear_share": 1.0}])
    analysis.finish(a, detect(before, after, ones, ones))
    return a


def pixel_of(a, point):
    col, row = ~a.grid.transform @ tuple(point)
    return int(row), int(col)


def with_second_patch(a, area_ha, rid=2):
    first = a.regions[0]
    a.regions.append({"type": "Feature", "geometry": first["geometry"],
                      "properties": {**first["properties"], "id": rid, "area_ha": area_ha,
                                     "center": [90.006, 23.006]}})
    return a


def test_the_point_is_inside_the_patch_even_when_the_centre_is_not():
    a = c_shaped()
    p = a.regions[0]["properties"]
    assert len(a.regions) == 1 and p["kind"] == "burned land"
    assert a.region_ids[pixel_of(a, p["center"])] != p["id"]       # the centre of mass is in the gap
    fl = fieldlist.make_checklist(a, use_model=False)
    s = fl["stops"][0]
    assert a.region_ids[pixel_of(a, s["point"])] == p["id"]        # the stop is on burned ground
    assert s["size_m"][0] > 300 and s["km_from_previous"] is None
    assert s["links"]["geo"] == f"geo:{s['point'][1]},{s['point'][0]}"


def test_plain_checklist_without_muse_spark():
    a = make_analysis()
    fl = fieldlist.make_checklist(a, use_model=False)
    assert fl["source"] == "template" and fl["intro"].startswith("1 stop,")
    s = fl["stops"][0]
    assert s["look_for"] == fieldlist.LOOK_FOR["burned land"]
    assert "16 ha" in s["why"] and "high confidence" in s["why"]
    assert "Do not walk into flood water" in fl["safety"]


def test_disputed_places_count_double_and_only_sam_places_join():
    a = with_second_patch(make_analysis(), 10.0)                   # smaller than the 16 ha burn...
    a.checks[2] = {"verdict": "disagrees", "looks_like": "cloud, haze or shadow", "what_you_see": "a cloud",
                   "reason": "white"}                                # ...but disputed, so it counts as 20
    a.witness = {"only_sam_places": [{"kind": "new water", "area_ha": 3.0, "center": [90.007, 23.007]},
                                     {"kind": "new water", "area_ha": 0.5, "center": [90.001, 23.001]}]}
    refs = [c["ref"] for c in fieldlist.candidates(a)]
    assert refs == ["P2", "P1", "S1"]                              # the 0.5 ha place is below the floor
    fl = fieldlist.make_checklist(a, use_model=False)
    assert "Muse Spark disagrees, it looks like cloud, haze or shadow" in fl["stops"][0]["why"]
    assert fl["stops"][2]["source"] == "only SAM" and fl["stops"][2]["point"] == [90.007, 23.007]
    assert fl["stops"][1]["km_from_previous"] > 0


def fake_chat(answer, seen=None):
    def chat(messages, tools=None, json_mode=False, schema=None):
        if seen is not None:
            seen.append((messages[0]["content"], schema))
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(content=json.dumps(answer))
    return chat


def test_muse_spark_can_only_choose_real_places(monkeypatch):
    a = with_second_patch(make_analysis(), 5.0)
    a.question = "What burned near the hill village?"
    seen = []
    monkeypatch.setattr(meta_client, "chat", fake_chat({
        "intro": "Two stops — start early.",
        "stops": [{"ref": "p2", "look_for": "Ash on the slope.", "why": "Biggest doubt."},
                  {"ref": "X9", "look_for": "invented", "why": "invented"},
                  {"ref": "P2", "look_for": "again", "why": "again"},
                  {"ref": "P1", "look_for": "", "why": "Largest burn."}]}, seen))
    fl = fieldlist.make_checklist(a)
    assert fl["source"] == "muse-spark" and fl["intro"] == "Two stops, start early."
    assert [s["ref"] for s in fl["stops"]] == ["P2", "P1"]          # unknown and repeated refs are dropped
    assert fl["stops"][0]["look_for"] == "Ash on the slope."
    assert fl["stops"][1]["look_for"] == fieldlist.LOOK_FOR["burned land"]   # empty text gets the template
    prompt, schema = seen[0]
    assert "hill village" in prompt and '"ref": "P1"' in prompt
    assert schema["properties"]["stops"]["items"]["properties"]["ref"]["enum"] == ["P1", "P2"]


def test_a_failed_or_empty_answer_falls_back_to_the_plain_list(monkeypatch):
    a = make_analysis()
    monkeypatch.setattr(meta_client, "chat", fake_chat(TimeoutError()))
    fl = fieldlist.make_checklist(a)
    assert fl["source"] == "template" and "TimeoutError" in fl["note"] and len(fl["stops"]) == 1
    monkeypatch.setattr(meta_client, "chat", fake_chat({"intro": "x", "stops": [{"ref": "Z1"}]}))
    fl = fieldlist.make_checklist(a)
    assert fl["source"] == "template" and "did not name" in fl["note"] and fl["stops"][0]["ref"] == "P1"


def test_kml_and_geojson_hold_the_stops_and_outlines():
    a = with_second_patch(make_analysis(), 5.0)
    a.question = "Fires & floods <here>"
    fieldlist.make_checklist(a, use_model=False)
    root = ElementTree.fromstring(fieldlist.kml(a).encode())
    ns = {"k": "http://www.opengis.net/kml/2.2"}
    stops, outlines = root.findall(".//k:Folder", ns)
    names = [p.find("k:name", ns).text for p in stops.findall("k:Placemark", ns)]
    assert names == ["1. Burned land, 16 ha", "2. Burned land, 5.0 ha"]
    lon, lat = map(float, stops.find(".//k:Point/k:coordinates", ns).text.split(","))
    assert [lon, lat] == a.checklist["stops"][0]["point"]
    assert len(outlines.findall(".//k:Polygon", ns)) == 2
    g = fieldlist.geojson(a)
    roles = [f["properties"]["role"] for f in g["features"]]
    assert roles == ["stop", "stop", "outline", "outline"]
    assert g["features"][0]["geometry"] == {"type": "Point", "coordinates": a.checklist["stops"][0]["point"]}


def test_a_plain_list_is_redone_when_new_checks_arrive_but_a_written_one_is_kept(monkeypatch):
    a = make_analysis()
    fieldlist.make_checklist(a, use_model=False)
    a.checks[1] = {"verdict": "disagrees", "looks_like": None, "what_you_see": "", "reason": ""}
    assert a.summary()["checklist_stale"]
    assert "disagree" in fieldlist.current(a)["stops"][0]["why"]   # an export redoes the free plain list
    assert not a.summary()["checklist_stale"]
    monkeypatch.setattr(meta_client, "chat", fake_chat({"intro": "i", "stops": [{"ref": "P1", "look_for": "l",
                                                                                 "why": "w"}]}))
    fieldlist.make_checklist(a)
    a.witnesses_made += 1
    assert a.summary()["checklist_stale"] and fieldlist.current(a)["source"] == "muse-spark"


def test_field_endpoints(client, monkeypatch):  # noqa: F811
    rid = client.post("/api/analyze", json=BODY).json()["id"]
    wait(client, rid)
    j = client.post(f"/api/analyze/{rid}/field").json()          # no key: the plain list, free
    assert j["checklist"]["source"] == "template" and j["checklist"]["stops"]
    r = client.get(f"/api/analyze/{rid}/field.kml")
    assert r.status_code == 200 and "kml" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    g = client.get(f"/api/analyze/{rid}/field.geojson").json()
    assert g["features"][0]["properties"]["rank"] == 1
    assert client.get(f"/api/analyze/{rid}/field/map.png").content[:4] == b"\x89PNG"
    page = client.get(f"/field/{rid}").text
    lon, lat = j["checklist"]["stops"][0]["point"]
    assert f"{lat:.5f}, {lon:.5f}" in page and "could not reach" in page and "Safety." in page
    monkeypatch.setattr(meta_client.C, "META_API_KEY", "x")
    monkeypatch.setattr(meta_client, "chat", fake_chat({"intro": "Go.", "stops": [{"ref": "P1", "look_for": "Ash.",
                                                                                   "why": "Big."}]}))
    for _ in range(3):
        assert client.post(f"/api/analyze/{rid}/field").json()["checklist"]["source"] == "muse-spark"
    assert client.post(f"/api/analyze/{rid}/field").status_code == 429
    assert client.get("/field/nope").status_code == 404
