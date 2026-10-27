"""One analysis run: two composites -> change detection -> regions -> map layers. Kept in memory and on disk."""
from __future__ import annotations

import io
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date

import numpy as np
from PIL import Image
from scipy import ndimage

from app import config as C
from app import imagery
from app.detect import CLASS_COLOURS, LIKELY, SURE, UNSURE, Detection, detect
from app.regions import regions as find_regions, totals as find_totals

MIN_SEEN = 0.30   # below this share of jointly clear pixels the answer is "not enough clear imagery"
MAX_REGIONS_SENT = 400   # largest patches sent to the page; totals still count every patch


@dataclass
class Analysis:
    id: str
    bbox: tuple
    before: date
    after: date
    window_days: int
    status: str = "queued"            # queued, running, done, failed, no_imagery
    message: str = ""
    progress: str = ""
    started: float = field(default_factory=time.time)
    grid: imagery.Grid | None = None
    comp_a: imagery.Composite | None = None
    comp_b: imagery.Composite | None = None
    det: Detection | None = None
    regions: list = field(default_factory=list)
    region_ids: np.ndarray | None = None              # region id per pixel, 0 = none
    totals: dict = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)   # one Meta step at a time
    reports_made: int = 0
    questions_asked: int = 0
    refines_made: int = 0
    layers: dict = field(default_factory=dict)        # name -> PNG bytes
    refined: dict = field(default_factory=dict)       # SAM results
    checks: dict = field(default_factory=dict)        # Muse Spark visual checks per region id
    witness: dict = field(default_factory=dict)       # SAM 3.1 second witness (concept prompts) summary
    witnesses_made: int = 0
    report: str = ""
    report_source: str = ""
    chat: list = field(default_factory=list)
    question: str = ""                                # set when the run came from a question in plain words
    plan: dict = field(default_factory=dict)          # Muse Spark's plan for that question (place, dates, why)

    @property
    def dir(self):
        return C.CACHE / "runs" / self.id

    def summary(self) -> dict:
        """Everything the page and the analyst need, without pixel arrays."""
        out = {"id": self.id, "status": self.status, "message": self.message, "progress": self.progress,
               "bbox": list(self.bbox), "before": str(self.before), "after": str(self.after),
               "window_days": self.window_days, "question": self.question, "plan": self.plan}
        if self.grid is not None:
            out["bounds"] = [[self.grid.lat0, self.grid.lon0], [self.grid.lat1, self.grid.lon1]]
            out["size_px"] = [self.grid.width, self.grid.height]
        for name, comp in (("scenes_before", self.comp_a), ("scenes_after", self.comp_b)):
            if comp is not None:
                out[name] = comp.scenes
        if self.det is not None:
            out["totals"] = self.totals
            out["regions"] = {"type": "FeatureCollection", "features": self.regions[:MAX_REGIONS_SENT]}
            out["regions_total"] = len(self.regions)
            out["layers"] = sorted(self.layers)
            out["refined"] = {k: v for k, v in self.refined.items() if k != "masks"}
            out["checks"] = self.checks
            out["witness"] = self.witness
            out["report"] = self.report
            out["report_source"] = self.report_source
        return out


RUNS: dict[str, Analysis] = {}
_LOCK = threading.Lock()
# A fixed pool: downloads are the bottleneck, so a few run at once and the rest wait in line (no thread each).
_POOL = ThreadPoolExecutor(C.MAX_PARALLEL_RUNS, thread_name_prefix="analysis")
MIN_SIDE_KM = 0.3
MAX_SIDE_KM = 40.0


def validate(bbox, before: date, after: date):
    w, s, e, n = bbox
    if not (w < e and s < n):
        raise ValueError("the area must be given as west, south, east, north")
    if after <= before:
        raise ValueError("the after date must be later than the before date")
    if imagery.area_km2(bbox) > C.MAX_AREA_KM2:
        raise ValueError(f"area is {imagery.area_km2(bbox):.0f} km2; the limit is {C.MAX_AREA_KM2:.0f} km2")
    mid = np.cos(np.radians((s + n) / 2))
    width_km, height_km = (e - w) * 111.32 * mid, (n - s) * 111.32
    if min(width_km, height_km) < MIN_SIDE_KM or max(width_km, height_km) > MAX_SIDE_KM:
        raise ValueError(f"each side must be between {MIN_SIDE_KM} and {MAX_SIDE_KM:.0f} km")


def start(bbox, before: date, after: date, window_days: int = C.WINDOW_DAYS, background: bool = True,
          question: str = "", plan: dict | None = None) -> Analysis:
    bbox = tuple(round(float(v), 5) for v in bbox)
    validate(bbox, before, after)
    a = Analysis(uuid.uuid4().hex[:10], bbox, before, after, window_days, question=question, plan=plan or {})
    with _LOCK:
        waiting = sum(r.status in ("queued", "running") for r in RUNS.values())
        if background and waiting >= C.MAX_QUEUED_RUNS:
            raise Busy(f"{waiting} analyses are already waiting; try again in a minute")
        RUNS[a.id] = a
        _forget_old()
    if background:
        a.progress = "waiting for a free worker"
        _POOL.submit(run, a)
    else:
        run(a)
    return a


class Busy(Exception):
    pass


def _forget_old():
    """Keep the newest runs in memory; their files stay on disk."""
    finished = [r for r in RUNS.values() if r.status not in ("queued", "running")]
    for r in sorted(finished, key=lambda r: r.started)[: max(0, len(RUNS) - C.MAX_RUNS_IN_MEMORY)]:
        RUNS.pop(r.id, None)


def run(a: Analysis):
    try:
        a.status = "running"
        res = imagery.res_for_area(a.bbox)
        a.grid = imagery.Grid.for_bbox(a.bbox, res)
        a.progress = "finding clear images before"
        a.comp_a = imagery.build(a.bbox, *imagery.window_around(a.before, a.window_days), res_m=res)
        a.progress = "finding clear images after"
        a.comp_b = imagery.build(a.bbox, *imagery.window_around(a.after, a.window_days), res_m=res)
        if a.comp_a is None or a.comp_b is None:
            a.status, a.message = "no_imagery", (
                "No clear Sentinel-2 image was found for the "
                + ("before" if a.comp_a is None else "after") + " date. Try other dates or a wider window.")
            return
        a.progress = "comparing"
        finish(a, detect(a.comp_a.bands, a.comp_b.bands, a.comp_a.count, a.comp_b.count))
    except Exception as e:  # report, don't crash the server
        a.status, a.message = "failed", f"{type(e).__name__}: {e}"


def finish(a: Analysis, det: Detection):
    a.det = det
    a.regions, a.region_ids = find_regions(det, a.grid)
    a.totals = find_totals(det, a.grid, a.region_ids)
    a.layers = render_layers(a)
    seen = a.totals["seen_share"]
    if seen < MIN_SEEN:
        a.message = (f"Only {seen:.0%} of the area was clear in both periods, so most of it could not be checked. "
                     "Treat the result as partial.")
    from app.report import fallback_report  # local import: report imports this module's types
    a.report, a.report_source = fallback_report(a), "template"
    a.status, a.progress = "done", ""
    save(a)


# ---------------------------------------------------------------------------------------------------
# layers
# ---------------------------------------------------------------------------------------------------

def _png(rgba: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(rgba).save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def change_rgba(det: Detection, classes: np.ndarray | None = None, confidence: np.ndarray | None = None) -> np.ndarray:
    classes = det.classes if classes is None else classes
    confidence = det.confidence if confidence is None else confidence
    h, w = classes.shape
    rgba = np.zeros((h, w, 4), np.uint8)
    for code, colour in CLASS_COLOURS.items():
        m = classes == code
        rgba[m, :3] = colour
    alpha = {SURE: 235, LIKELY: 170, UNSURE: 110}
    for level, val in alpha.items():
        rgba[(classes > 0) & (confidence == level), 3] = val
    # unsure pixels get a light diagonal hatch so they read as "check by hand" at a glance
    yy, xx = np.mgrid[0:h, 0:w]
    hatch = ((xx + yy) % 4 == 0) & (classes > 0) & (confidence == UNSURE)
    rgba[hatch, :3] = 255
    rgba[hatch, 3] = 200
    return rgba


def unseen_rgba(det: Detection) -> np.ndarray:
    h, w = det.seen.shape
    rgba = np.zeros((h, w, 4), np.uint8)
    rgba[~det.seen] = (120, 120, 120, 150)
    return rgba


def render_layers(a: Analysis) -> dict:
    return {"before": _png(imagery.true_colour(a.comp_a)), "after": _png(imagery.true_colour(a.comp_b)),
            "change": _png(change_rgba(a.det)), "unseen": _png(unseen_rgba(a.det))}


def region_mask(a: Analysis, rid: int) -> np.ndarray:
    """Exactly the pixels of one region."""
    return a.region_ids == rid


def outline_crop(a: Analysis, rid: int, which: str = "after") -> np.ndarray:
    """Crop with the region's edge drawn in yellow, for the visual check."""
    f = next(f for f in a.regions if f["properties"]["id"] == rid)
    comp = a.comp_a if which == "before" else a.comp_b
    img = imagery.true_colour(comp)[..., :3].copy()
    m = region_mask(a, rid)
    edge = m & ~ndimage.binary_erosion(m)
    img[edge] = (255, 255, 0)
    return crop_array(img, f["properties"]["pixel_box"], outline=edge)


def stretch(rgb: np.ndarray, keep: np.ndarray | None = None) -> np.ndarray:
    """Per-image 2 to 98 percent contrast stretch, so haze does not hide a scar. `keep` pixels are left as is."""
    x = rgb.astype(np.float32)
    lo, hi = np.percentile(x, 2), np.percentile(x, 98)
    out = np.clip((x - lo) / max(hi - lo, 1.0) * 255, 0, 255).astype(np.uint8)
    if keep is not None:
        out[keep] = rgb[keep]
    return out


def crop_array(img, pixel_box, pad=12, min_size=256, outline=None):
    x0, y0, x1, y1 = pixel_box
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(img.shape[1], x1 + pad), min(img.shape[0], y1 + pad)
    c = stretch(img[y0:y1, x0:x1], None if outline is None else outline[y0:y1, x0:x1])
    scale = max(1, int(np.ceil(min_size / max(c.shape[:2]))))
    return np.asarray(Image.fromarray(c).resize((c.shape[1] * scale, c.shape[0] * scale), Image.NEAREST))


def save(a: Analysis):
    a.dir.mkdir(parents=True, exist_ok=True)
    for name, data in a.layers.items():
        (a.dir / f"{name}.png").write_bytes(data)
    (a.dir / "summary.json").write_text(json.dumps(a.summary(), indent=1, default=str))
