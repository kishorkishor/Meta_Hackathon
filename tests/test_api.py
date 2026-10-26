"""HTTP API end to end with imagery replaced by synthetic composites (no network)."""
import io
import time
from datetime import date, timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import analysis, imagery, meta_client
from app.api import app
from app.imagery import Composite, Grid
from tests.test_core import BURNED, FOREST, N, paint, scene

BODY = {"west": 90.0, "south": 23.0, "east": 90.0078, "north": 23.0072, "before": "2026-01-10", "after": "2026-05-10"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(analysis.C, "CACHE", tmp_path)
    monkeypatch.setattr(meta_client.C, "META_API_KEY", "")

    def fake_build(bbox, start, end, res_m=20.0, use_cache=True):
        g = Grid(bbox[0], bbox[1], bbox[2], bbox[3], N, N)
        ones = np.full((N, N), 2, np.int16)
        if start < date(2026, 3, 1):
            return Composite(g, scene(FOREST), ones, [{"id": "a", "date": "2026-01-12", "clear_share": 1.0}])
        b = paint(scene(FOREST), BURNED, slice(10, 30), slice(10, 30))
        return Composite(g, b, ones, [{"id": "b", "date": "2026-05-08", "clear_share": 1.0}])

    monkeypatch.setattr(imagery, "build", fake_build)
    monkeypatch.setattr(imagery, "Grid", type("G", (), {"for_bbox": staticmethod(
        lambda bbox, res=20.0: Grid(bbox[0], bbox[1], bbox[2], bbox[3], N, N))}))
    return TestClient(app)


def wait(client, rid):
    for _ in range(100):
        j = client.get(f"/api/analyze/{rid}").json()
        if j["status"] not in ("queued", "running"):
            return j
        time.sleep(0.05)
    raise AssertionError("analysis did not finish")


def test_health_and_demos(client):
    h = client.get("/api/health").json()
    assert h["ok"] is True and h["meta"] is False
    demos = client.get("/api/demos").json()
    assert len(demos) >= 3 and all(len(d["bbox"]) == 4 for d in demos)


def test_full_run_returns_regions_layers_and_a_report(client):
    rid = client.post("/api/analyze", json=BODY).json()["id"]
    j = wait(client, rid)
    assert j["status"] == "done", j
    assert j["regions"]["features"][0]["properties"]["kind"] == "burned land"
    assert j["report_source"] == "template" and "Burned land" in j["report"]
    assert j["scenes_before"][0]["date"] == "2026-01-12"
    for name in ("before", "after", "change", "unseen"):
        r = client.get(f"/api/analyze/{rid}/layer/{name}")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
        assert Image.open(io.BytesIO(r.content)).size == (N, N)
    crop = client.get(f"/api/analyze/{rid}/crop/1")
    assert crop.status_code == 200
    assert client.get(f"/api/analyze/{rid}/crop/999").status_code == 404
    assert client.get(f"/api/analyze/{rid}/layer/nope").status_code == 404


def test_meta_endpoints_say_clearly_when_no_key(client):
    rid = client.post("/api/analyze", json=BODY).json()["id"]
    wait(client, rid)
    for path in ("report", "refine"):
        r = client.post(f"/api/analyze/{rid}/{path}")
        assert r.status_code == 503 and "MODEL_API_KEY" in r.json()["detail"]
    assert client.post(f"/api/analyze/{rid}/ask", json={"question": "where first?"}).status_code == 503


def test_busy_analysis_refuses_a_second_meta_step(client, monkeypatch):
    rid = client.post("/api/analyze", json=BODY).json()["id"]
    wait(client, rid)
    monkeypatch.setattr(meta_client.C, "META_API_KEY", "x")
    a = analysis.RUNS[rid]
    a.lock.acquire()
    try:
        assert client.post(f"/api/analyze/{rid}/report").status_code == 409
    finally:
        a.lock.release()
    a.reports_made = 99
    assert client.post(f"/api/analyze/{rid}/report").status_code == 429
    a.refines_made = 99
    assert client.post(f"/api/analyze/{rid}/refine").status_code == 429


def test_queue_limit_answers_busy(client, monkeypatch):
    monkeypatch.setattr(analysis.C, "MAX_QUEUED_RUNS", 1)
    stuck = analysis.Analysis("stuck", (0, 0, 1, 1), date(2026, 1, 1), date(2026, 2, 1), 10, status="running")
    monkeypatch.setitem(analysis.RUNS, "stuck", stuck)
    r = client.post("/api/analyze", json=BODY)
    assert r.status_code == 429 and "waiting" in r.json()["detail"]


@pytest.mark.parametrize("change, message", [
    ({"after": "2025-01-01"}, "later than"),
    ({"east": 91.0, "north": 24.0}, "limit"),
    ({"east": 179.0, "north": 23.00001}, "each side"),   # tiny area but a 9,000 km long strip
    ({"after": (date.today() + timedelta(days=3)).isoformat()}, "future"),
])
def test_bad_requests_are_rejected_with_a_reason(client, change, message):
    r = client.post("/api/analyze", json={**BODY, **change})
    assert r.status_code == 422
    assert message in str(r.json()["detail"])


def test_unknown_run_is_404(client):
    assert client.get("/api/analyze/nope").status_code == 404


def test_page_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "What Changed Here" in r.text
    assert client.get("/static/app.js").status_code == 200
