"""HTTP API and the map page.

    POST /api/plan                    a question in plain words -> Muse Spark's place, dates and reasons
    POST /api/analyze                 start an analysis (optionally from a plan), returns its id
    GET  /api/analyze/{id}            status, then layers, regions, totals and the report
    GET  /api/analyze/{id}/layer/{n}  PNG overlay: before, after, change, unseen, refined
    GET  /api/analyze/{id}/crop/{r}   before/after crops of one region (PNG, side by side)
    POST /api/analyze/{id}/report     Muse Spark writes the report (checks and SAM outlines on the way)
    POST /api/analyze/{id}/ask        a follow-up question
    POST /api/analyze/{id}/refine     SAM 3.1 outlines only
    POST /api/analyze/{id}/witness    SAM 3.1 concept prompts as a second witness, with an agreement layer
    GET  /api/demos                   prepared places
    GET  /api/health
"""
from __future__ import annotations

import io
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from datetime import date

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field, field_validator

from app import analysis, config as C, firms, meta_client
from app.demos import DEMOS

app = FastAPI(title="What Changed Here")


class AnalyzeIn(BaseModel):
    west: float = Field(ge=-180, le=180)
    south: float = Field(ge=-90, le=90)
    east: float = Field(ge=-180, le=180)
    north: float = Field(ge=-90, le=90)
    before: date
    after: date
    window_days: int = Field(default=C.WINDOW_DAYS, ge=3, le=90)
    plan_id: str | None = Field(default=None, max_length=40)   # set when the area came from a question

    @field_validator("after")
    @classmethod
    def _not_future(cls, v):
        if v > date.today():
            raise ValueError("the after date is in the future")
        return v


class AskIn(BaseModel):
    question: str = Field(min_length=2, max_length=1000)


class PlanIn(BaseModel):
    question: str = Field(min_length=4, max_length=500)


PLANS: OrderedDict[str, dict] = OrderedDict()   # newest last; kept on the server so a run cites the real plan
MAX_PLANS_KEPT = 200


def _get(run_id: str) -> analysis.Analysis:
    a = analysis.RUNS.get(run_id)
    if a is None:
        raise HTTPException(404, "unknown analysis")
    return a


def _done(run_id: str) -> analysis.Analysis:
    a = _get(run_id)
    if a.status != "done":
        raise HTTPException(409, f"analysis is {a.status}")
    return a


def _need_meta():
    if not meta_client.available():
        raise HTTPException(503, "Meta Model API key not set (MODEL_API_KEY)")


@contextmanager
def _hold(a: analysis.Analysis):
    """One Meta step per analysis at a time; a second click gets a clear answer instead of a race."""
    if not a.lock.acquire(blocking=False):
        raise HTTPException(409, "this analysis is busy with another step; try again in a moment")
    try:
        yield
    finally:
        a.lock.release()


@app.get("/api/health")
def health():
    return {"ok": True, "meta": meta_client.available(), "firms": bool(C.FIRMS_MAP_KEY),
            "chat_model": C.META_CHAT_MODEL, "sam_model": C.META_SAM_MODEL, "max_area_km2": C.MAX_AREA_KM2}


@app.get("/api/demos")
def demos():
    return DEMOS


@app.post("/api/plan")
def make_plan(body: PlanIn):
    """Muse Spark reads the question, finds the place and the dates, and checks there are clear images."""
    _need_meta()
    from app import planner
    if not planner.allow():
        raise HTTPException(429, "the demo has answered many questions this hour; try again later or draw an area")
    try:
        p = planner.plan(body.question.strip())
    except planner.PlanError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(502, f"Muse Spark call failed: {type(e).__name__}: {e}")
    pid = uuid.uuid4().hex[:10]
    PLANS[pid] = p
    while len(PLANS) > MAX_PLANS_KEPT:
        PLANS.popitem(last=False)
    return {"id": pid, **p}


@app.post("/api/analyze")
def analyze(body: AnalyzeIn):
    question, plan = "", None
    if body.plan_id:
        p = PLANS.get(body.plan_id)
        if p is None or not p.get("ok"):
            raise HTTPException(404, "unknown plan")
        question = p["question"]
        plan = {k: p[k] for k in ("place", "looking_for", "why", "sources")}
    try:
        a = analysis.start((body.west, body.south, body.east, body.north), body.before, body.after, body.window_days,
                           question=question, plan=plan)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except analysis.Busy as e:
        raise HTTPException(429, str(e))
    return {"id": a.id}


@app.get("/api/analyze/{run_id}")
def get_analysis(run_id: str):
    return _get(run_id).summary()


@app.get("/api/analyze/{run_id}/layer/{name}")
def layer(run_id: str, name: str):
    a = _done(run_id)
    data = a.layers.get(name.removesuffix(".png"))
    if data is None:
        raise HTTPException(404, "no such layer")
    return Response(data, media_type="image/png", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/analyze/{run_id}/crop/{rid}")
def crop(run_id: str, rid: int):
    a = _done(run_id)
    if not any(f["properties"]["id"] == rid for f in a.regions):
        raise HTTPException(404, "no such region")
    b, af = analysis.outline_crop(a, rid, "before"), analysis.outline_crop(a, rid, "after")
    gap = np.full((b.shape[0], 8, 3), 255, np.uint8)
    buf = io.BytesIO()
    Image.fromarray(np.hstack([b, gap, af])).save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png")


def _spend(a: analysis.Analysis, counter: str, limit: int, what: str):
    """Count a paid step; called with the run's lock held, so two quick requests cannot both pass."""
    if getattr(a, counter) >= limit:
        raise HTTPException(429, f"{what} limit for this analysis reached ({limit})")
    setattr(a, counter, getattr(a, counter) + 1)


@app.post("/api/analyze/{run_id}/report")
def report(run_id: str):
    a = _done(run_id)
    _need_meta()
    from app.agent import write_report
    with _hold(a):
        _spend(a, "reports_made", C.MAX_REPORTS_PER_RUN, "report")
        try:
            write_report(a)
        except Exception as e:
            raise HTTPException(502, f"Muse Spark call failed: {type(e).__name__}: {e}")
        analysis.save(a)
        return a.summary()


@app.post("/api/analyze/{run_id}/ask")
def ask(run_id: str, body: AskIn):
    a = _done(run_id)
    _need_meta()
    from app.agent import ask as agent_ask
    with _hold(a):
        _spend(a, "questions_asked", C.MAX_QUESTIONS_PER_RUN, "question")
        try:
            return {"answer": agent_ask(a, body.question)}
        except Exception as e:
            raise HTTPException(502, f"Muse Spark call failed: {type(e).__name__}: {e}")


@app.post("/api/analyze/{run_id}/refine")
def refine(run_id: str):
    a = _done(run_id)
    _need_meta()
    from app.refine import refine_with_sam
    with _hold(a):
        _spend(a, "refines_made", C.MAX_REFINES_PER_RUN, "outline")
        try:
            refine_with_sam(a)
        except Exception as e:
            raise HTTPException(502, f"SAM call failed: {type(e).__name__}: {e}")
        analysis.save(a)
        return a.summary()


@app.post("/api/analyze/{run_id}/witness")
def witness(run_id: str):
    a = _done(run_id)
    _need_meta()
    from app.witness import second_witness
    with _hold(a):
        _spend(a, "witnesses_made", C.MAX_WITNESS_PER_RUN, "second witness")
        try:
            second_witness(a)
        except Exception as e:
            raise HTTPException(502, f"SAM call failed: {type(e).__name__}: {e}")
        analysis.save(a)
        return a.summary()


@app.get("/api/analyze/{run_id}/fires")
def fires(run_id: str, days: int = 7):
    return firms.active_fires(_get(run_id).bbox, days)


@app.get("/")
def index():
    return FileResponse(C.FRONTEND / "index.html")


app.mount("/static", StaticFiles(directory=C.FRONTEND), name="static")
