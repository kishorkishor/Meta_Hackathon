"""Sentinel-2 L2A composites for any small area from Earth Search (STAC, no account needed).

Every scene is warped onto one shared lat/lon grid (about 20 m pixels), so the area may cross UTM zones and
tile edges, and the output lines up exactly with a web map. For each date window we read the cloud mask
(SCL) of the least cloudy candidate scenes, keep the ones that add clear pixels, and take the per-pixel
median of up to MAX_SCENES clear observations. Pixels with no clear look are NaN.

The scene search, the SCL filter and the reflectance scaling follow Burn Before the Rain
(github.com/kishorkishor/NASA_PROJECT, MIT licence).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import rasterio
import requests
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT

from app import config as C

# B8A (nir08, 20 m) stands in for B08 in NDVI and NDBI: same NIR plateau, and one fewer band to fetch.
BANDS = ("blue", "green", "red", "nir08", "swir16", "swir22")
ENV = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
           GDAL_HTTP_MULTIRANGE="YES", GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES", VSI_CACHE="TRUE",
           GDAL_HTTP_MAX_RETRY="3", GDAL_HTTP_RETRY_DELAY="1")
M_PER_DEG = 111_320.0


@dataclass(frozen=True)
class Grid:
    lon0: float
    lat0: float
    lon1: float
    lat1: float
    width: int
    height: int

    @classmethod
    def for_bbox(cls, bbox, res_m: float = C.RES) -> "Grid":
        lon0, lat0, lon1, lat1 = bbox
        if not (lon0 < lon1 and lat0 < lat1):
            raise ValueError("bbox must be (west, south, east, north)")
        mid = math.radians((lat0 + lat1) / 2)
        dlat = res_m / M_PER_DEG
        dlon = res_m / (M_PER_DEG * math.cos(mid))
        width, height = math.ceil((lon1 - lon0) / dlon), math.ceil((lat1 - lat0) / dlat)
        return cls(lon0, lat0, lon0 + width * dlon, lat0 + height * dlat, width, height)

    @property
    def transform(self):
        return from_origin(self.lon0, self.lat1, (self.lon1 - self.lon0) / self.width,
                           (self.lat1 - self.lat0) / self.height)

    @property
    def bbox(self):
        return (self.lon0, self.lat0, self.lon1, self.lat1)

    def pixel_area_ha(self) -> float:
        mid = math.radians((self.lat0 + self.lat1) / 2)
        dx = (self.lon1 - self.lon0) / self.width * M_PER_DEG * math.cos(mid)
        dy = (self.lat1 - self.lat0) / self.height * M_PER_DEG
        return dx * dy / 10_000.0


def grid_res_m(grid: Grid) -> float:
    return (grid.lat1 - grid.lat0) / grid.height * M_PER_DEG


def res_for_area(bbox) -> float:
    """20 m pixels for areas up to FINE_AREA_KM2, 40 m above that, so a live request stays fast."""
    return C.RES if area_km2(bbox) <= C.FINE_AREA_KM2 else 2 * C.RES


def area_km2(bbox) -> float:
    lon0, lat0, lon1, lat1 = bbox
    mid = math.radians((lat0 + lat1) / 2)
    return (lon1 - lon0) * M_PER_DEG * math.cos(mid) * (lat1 - lat0) * M_PER_DEG / 1e6


@dataclass
class Composite:
    grid: Grid
    bands: dict                     # band -> float32 reflectance, NaN = no clear look
    count: np.ndarray               # clear looks per pixel
    scenes: list = field(default_factory=list)   # [{"id", "date", "clear_share"}]
    window: tuple = ()

    @property
    def valid(self):
        return self.count > 0


def search(bbox, start: date, end: date, limit: int = 50) -> list:
    body = {"collections": [C.COLLECTION], "bbox": list(bbox), "limit": limit,
            "datetime": f"{start.isoformat()}T00:00:00Z/{end.isoformat()}T23:59:59Z",
            "query": {"eo:cloud_cover": {"lt": C.MAX_SCENE_CLOUD}},
            "sortby": [{"field": "properties.eo:cloud_cover", "direction": "asc"}]}
    r = requests.post(C.STAC_URL, json=body, timeout=120)
    r.raise_for_status()
    return r.json().get("features", [])


NATIVE_M = {"blue": 10, "green": 10, "red": 10, "nir": 10, "visual": 10,
            "nir08": 20, "swir16": 20, "swir22": 20, "scl": 20}


def overview_for(key: str, res_m: float):
    """COG overview level whose pixels are closest to res_m without being coarser (None = full res).

    Sentinel-2 COGs have overviews at 2x, 4x, ... the native pixel size, so reading the overview that already
    matches the analysis grid moves 4 to 16 times fewer bytes than warping the full-resolution band."""
    native = NATIVE_M.get(key, 10)
    level, size = None, native
    while size * 2 <= res_m + 1e-6:
        level = 0 if level is None else level + 1
        size *= 2
    return level


def read_asset(item: dict, key: str, grid: Grid, resampling=Resampling.average) -> np.ndarray:
    """One asset warped onto the grid. 0 = nodata (Earth Search COGs use 0 as nodata)."""
    href = item["assets"][key]["href"]
    level = overview_for(key, grid_res_m(grid))
    kw = {} if level is None else {"overview_level": level}
    with rasterio.Env(**ENV), rasterio.open(href, **kw) as src:
        with WarpedVRT(src, crs="EPSG:4326", transform=grid.transform, width=grid.width,
                       height=grid.height, resampling=resampling, src_nodata=0, nodata=0) as vrt:
            return vrt.read(1)


def to_reflectance(item: dict, key: str, dn: np.ndarray) -> np.ndarray:
    out = dn.astype(np.float32)
    nodata = dn == 0
    band = (item["assets"][key].get("raster:bands") or [{}])[0]
    scale = band.get("scale", 0.0001)
    # When 'earthsearch:boa_offset_applied' is true the -1000 DN offset is already in the pixels.
    offset = 0.0 if item["properties"].get("earthsearch:boa_offset_applied") else band.get("offset", 0.0)
    out = out * scale + offset
    out[nodata] = np.nan
    return out


def _clear(item, grid):
    scl = read_asset(item, "scl", grid, Resampling.nearest)
    return np.isin(scl, C.CLEAR_SCL)


_KEY_LOCKS: dict[str, threading.Lock] = {}
_KEY_LOCKS_GUARD = threading.Lock()


def build(bbox, start: date, end: date, res_m: float = C.RES, use_cache: bool = True) -> Composite | None:
    grid = Grid.for_bbox(bbox, res_m)
    key = hashlib.sha1(json.dumps([grid.bbox, grid.width, grid.height, str(start), str(end)]).encode()).hexdigest()[:16]
    path = C.CACHE / "composites" / f"{key}.npz"
    with _KEY_LOCKS_GUARD:
        lock = _KEY_LOCKS.setdefault(key, threading.Lock())
    with lock:  # two requests for the same composite: the second waits and reads the cache
        if use_cache and path.exists():
            try:
                return _load(path, grid)
            except Exception:
                path.unlink(missing_ok=True)  # corrupt or old format: rebuild
        comp = _build(grid, start, end)
        if comp is not None and use_cache:
            _save(path, comp)
        return comp


def _build(grid: Grid, start: date, end: date) -> Composite | None:
    items = search(grid.bbox, start, end)[: C.CANDIDATES]
    if not items:
        return None
    with ThreadPoolExecutor(8) as ex:
        masks = list(ex.map(lambda it: _safe(_clear, it, grid), items))
    looks = sorted([(float(m.mean()), it, m) for it, m in zip(items, masks) if m is not None and m.mean() >= C.MIN_CLEAR],
                   key=lambda t: -t[0])
    if not looks:
        return None

    downloaded: dict[str, dict] = {}   # scene id -> band arrays (unmasked)
    failed: set[str] = set()

    def bands_of(it):
        return {b: to_reflectance(it, b, read_asset(it, b, grid)) for b in BANDS}

    # Plan, download, and if a scene fails, plan again without it so the next candidate takes its place.
    for _ in range(len(looks)):
        plan = _plan([lk for lk in looks if lk[1]["id"] not in failed], grid)
        todo = [it for _, it, _ in plan if it["id"] not in downloaded]
        if not todo:
            break
        with ThreadPoolExecutor(6) as ex:  # the wait is network, not CPU
            for it, got in zip(todo, ex.map(lambda it: _safe(bands_of, it), todo)):
                if got is None:
                    failed.add(it["id"])
                else:
                    downloaded[it["id"]] = got
    plan = _plan([lk for lk in looks if lk[1]["id"] in downloaded], grid)
    if not plan:
        return None

    per_scene = []
    for _, it, admit in plan:
        s = {}
        for b in BANDS:
            arr = downloaded[it["id"]][b].copy()
            arr[~admit] = np.nan
            s[b] = arr
        per_scene.append(s)
    with np.errstate(all="ignore"):
        bands = {b: np.nanmedian(np.stack([s[b] for s in per_scene]), axis=0).astype(np.float32) for b in BANDS}
    bands["nir"] = bands["nir08"]
    count = np.sum([np.isfinite(s["red"]) for s in per_scene], axis=0).astype(np.int16)
    scenes = [{"id": it["id"], "date": it["properties"]["datetime"][:10], "clear_share": round(share, 3)}
              for share, it, _ in plan]
    return Composite(grid, bands, count, scenes, (str(start), str(end)))


def _plan(looks, grid: Grid) -> list:
    """Clearest scenes first; each is used only on pixels that still need a look, so none gets more than MAX_SCENES."""
    count = np.zeros((grid.height, grid.width), np.int16)
    plan = []
    for share, it, ok in looks:
        admit = ok & (count < C.MAX_SCENES)
        if not admit.any():
            continue
        plan.append((share, it, admit))
        count += admit
        if (count >= C.MAX_SCENES).all():
            break
    return plan


def _save(path, comp: Composite):
    """Write to a temporary file and rename, so a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp.npz")
    np.savez_compressed(tmp, count=comp.count, scenes=json.dumps(comp.scenes), window=json.dumps(comp.window),
                        **{f"b_{b}": comp.bands[b] for b in BANDS})
    os.replace(tmp, path)


def _load(path, grid) -> Composite:
    z = np.load(path)
    bands = {b: z[f"b_{b}"] for b in BANDS}
    bands["nir"] = bands["nir08"]
    return Composite(grid, bands, z["count"], json.loads(str(z["scenes"])), tuple(json.loads(str(z["window"]))))


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception:  # one flaky tile read should not kill the whole request
        return None


def window_around(day: date, half_days: int = C.WINDOW_DAYS) -> tuple[date, date]:
    return day - timedelta(days=half_days), day + timedelta(days=half_days)


def true_colour(comp: Composite) -> np.ndarray:
    """RGBA uint8 for display; transparent where there was no clear look."""
    rgb = np.stack([comp.bands[b] for b in ("red", "green", "blue")], axis=-1)
    with np.errstate(invalid="ignore"):
        rgb = np.clip(np.nan_to_num(rgb, nan=0.0) / 0.25, 0, 1) ** (1 / 1.4)
    alpha = np.where(comp.valid, 255, 0).astype(np.uint8)
    return np.dstack([(rgb * 255).astype(np.uint8), alpha])
