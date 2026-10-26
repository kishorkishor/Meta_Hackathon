"""Scene choice and compositing with fake STAC items and fake band reads (no network)."""
import numpy as np
import pytest
from datetime import date

from app import imagery

H = W = 10


def item(i, offset_applied=True):
    bands = {b: {"href": f"s{i}/{b}.tif", "raster:bands": [{"scale": 0.0001, "offset": -0.1}]}
             for b in imagery.BANDS + ("scl",)}
    return {"id": f"S{i}", "assets": bands,
            "properties": {"datetime": f"2026-01-0{i + 1}T00:00:00Z", "earthsearch:boa_offset_applied": offset_applied}}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.setattr(imagery.C, "CACHE", tmp_path)
    monkeypatch.setattr(imagery.Grid, "for_bbox", classmethod(lambda cls, bbox, res=20.0: imagery.Grid(0, 0, 1, 1, W, H)))
    state = {"clear": {}, "value": {}, "fail": set(), "reads": 0}

    def fake_clear(it, grid):
        return state["clear"][it["id"]]

    def fake_read(it, key, grid, resampling=None):
        state["reads"] += 1
        if (it["id"], key) in state["fail"]:
            raise IOError("tile read failed")
        return np.full((H, W), state["value"][it["id"]], np.uint16)

    monkeypatch.setattr(imagery, "_clear", fake_clear)
    monkeypatch.setattr(imagery, "read_asset", fake_read)
    return state


def run(monkeypatch, items, **kw):
    monkeypatch.setattr(imagery, "search", lambda bbox, s, e, limit=50: items)
    return imagery.build((0, 0, 1, 1), date(2026, 1, 1), date(2026, 1, 10), **kw)


def test_no_pixel_gets_more_than_three_looks(fake, monkeypatch):
    left = np.zeros((H, W), bool)
    left[:, :5] = True
    items = [item(i) for i in range(5)]
    for i in range(4):
        fake["clear"][f"S{i}"] = np.ones((H, W), bool)   # four fully clear scenes
        fake["value"][f"S{i}"] = 1000 + 1000 * i         # reflectance 0.1, 0.2, 0.3, 0.4
    fake["clear"]["S4"] = left
    fake["value"]["S4"] = 9000
    comp = run(monkeypatch, items)
    assert comp.count.max() == 3
    assert np.allclose(comp.bands["red"], 0.2)          # median of the first three clear scenes only
    assert len(comp.scenes) == 3


def test_scale_and_offset(fake, monkeypatch):
    fake["clear"]["S0"] = np.ones((H, W), bool)
    fake["value"]["S0"] = 2000
    assert np.allclose(run(monkeypatch, [item(0, offset_applied=False)], use_cache=False).bands["red"], 0.1)
    assert np.allclose(run(monkeypatch, [item(0, offset_applied=True)], use_cache=False).bands["red"], 0.2)


def test_a_scene_whose_band_fails_is_dropped_and_not_counted(fake, monkeypatch):
    for i in range(2):
        fake["clear"][f"S{i}"] = np.ones((H, W), bool)
        fake["value"][f"S{i}"] = 1000 * (i + 1)
    fake["fail"].add(("S1", "swir22"))
    comp = run(monkeypatch, [item(0), item(1)])
    assert comp.count.max() == 1
    assert [s["id"] for s in comp.scenes] == ["S0"]
    assert np.allclose(comp.bands["red"], 0.1)


def test_a_failed_scene_is_replaced_by_the_next_candidate(fake, monkeypatch):
    for i in range(4):
        fake["clear"][f"S{i}"] = np.ones((H, W), bool)
        fake["value"][f"S{i}"] = 1000 * (i + 1)
    fake["fail"].add(("S0", "red"))
    comp = run(monkeypatch, [item(i) for i in range(4)])
    assert comp.count.max() == 3
    assert sorted(s["id"] for s in comp.scenes) == ["S1", "S2", "S3"]
    assert np.allclose(comp.bands["red"], 0.3)


def test_cloudy_everywhere_returns_none(fake, monkeypatch):
    fake["clear"]["S0"] = np.zeros((H, W), bool)
    fake["value"]["S0"] = 1000
    assert run(monkeypatch, [item(0)]) is None


def test_cache_is_used_and_a_corrupt_file_is_rebuilt(fake, monkeypatch):
    fake["clear"]["S0"] = np.ones((H, W), bool)
    fake["value"]["S0"] = 1000
    run(monkeypatch, [item(0)])
    reads = fake["reads"]
    run(monkeypatch, [item(0)])
    assert fake["reads"] == reads                        # second call came from the cache
    files = list((imagery.C.CACHE / "composites").glob("*.npz"))
    assert len(files) == 1 and not list((imagery.C.CACHE / "composites").glob("*.tmp*"))
    files[0].write_bytes(b"not a zip")
    comp = run(monkeypatch, [item(0)])
    assert fake["reads"] > reads and np.allclose(comp.bands["red"], 0.1)
