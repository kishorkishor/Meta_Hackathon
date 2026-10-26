"""Group changed pixels into regions: one GeoJSON polygon per connected patch of one change type."""
from __future__ import annotations

import numpy as np
from rasterio import features
from scipy import ndimage

from app import config as C
from app.detect import CLASS_NAMES, FLAG_NAMES, LIKELY, SURE, UNSURE, Detection
from app.imagery import Grid

EIGHT = np.ones((3, 3), bool)
MAIN_DOUBT_SHARE = 0.25


def regions(det: Detection, grid: Grid, min_pixels: int = C.MIN_REGION_PIXELS) -> tuple[list[dict], np.ndarray]:
    """GeoJSON features, largest first with ids 1..n, and a raster holding each pixel's region id (0 = none)."""
    px_ha = grid.pixel_area_ha()
    out, where = [], []          # where: (class code, its label raster, label) to fill the id raster at the end
    label_maps = {}
    for code, name in CLASS_NAMES.items():
        labels, n = ndimage.label(det.classes == code, structure=EIGHT)
        if n == 0:
            continue
        sizes = np.bincount(labels.ravel(), minlength=n + 1)
        keep = [lab for lab in range(1, n + 1) if sizes[lab] >= min_pixels]
        if not keep:
            continue
        label_maps[code] = labels
        shapes = features.shapes(labels.astype(np.int32), mask=np.isin(labels, keep), transform=grid.transform,
                                 connectivity=8)
        polys = {}
        for geom, value in shapes:
            polys.setdefault(int(value), []).append(geom)
        boxes = ndimage.find_objects(labels)
        for lab in keep:
            sl = boxes[lab - 1]   # work inside the patch's own box, never on the full grid
            m = labels[sl] == lab
            conf = det.confidence[sl][m]
            flags = det.flags[sl][m]
            ys, xs = np.nonzero(m)
            ys, xs = ys + sl[0].start, xs + sl[1].start
            doubts = {FLAG_NAMES[bit]: round(float(((flags & bit) > 0).mean()), 2) for bit in FLAG_NAMES}
            # name a doubt only when it touches a real share of the patch
            main_doubt = max(doubts, key=doubts.get) if max(doubts.values()) >= MAIN_DOUBT_SHARE else None
            sure, likely, unsure = (float((conf == SURE).mean()), float((conf == LIKELY).mean()),
                                    float((conf == UNSURE).mean()))
            level = "sure" if sure >= 0.5 else "unsure" if unsure >= 0.5 else "likely"
            if level == "sure":
                main_doubt = None
            lon, lat = grid.transform @ (xs.mean() + 0.5, ys.mean() + 0.5)
            geoms = polys.get(lab, [])
            geometry = geoms[0] if len(geoms) == 1 else {
                "type": "MultiPolygon", "coordinates": [g["coordinates"] for g in geoms]}
            where.append((code, lab))
            out.append({
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "kind": name, "code": code,
                    "area_ha": round(float(m.sum()) * px_ha, 2),
                    "confidence": level,
                    "share_sure": round(sure, 2), "share_likely": round(likely, 2), "share_unsure": round(unsure, 2),
                    "main_doubt": main_doubt, "doubts": doubts,
                    "center": [round(float(lon), 5), round(float(lat), 5)],
                    "pixel_box": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1],
                },
            })
    order = sorted(range(len(out)), key=lambda i: -out[i]["properties"]["area_ha"])
    lookup = {code: np.zeros(labels.max() + 1, np.int32) for code, labels in label_maps.items()}
    feats = []
    for rid, i in enumerate(order, 1):
        out[i]["properties"]["id"] = rid
        code, lab = where[i]
        lookup[code][lab] = rid
        feats.append(out[i])
    ids = np.zeros(det.classes.shape, np.int32)
    for code, labels in label_maps.items():   # one lookup per change type fills the whole id raster
        m = det.classes == code
        ids[m] = lookup[code][labels[m]]
    return feats, ids


def totals(det: Detection, grid: Grid, region_ids: np.ndarray) -> dict:
    """Areas per change type, counting only pixels that belong to a region, so totals match the region list."""
    px_ha = grid.pixel_area_ha()
    seen = det.seen
    in_region = region_ids > 0
    out = {"area_ha": round(seen.size * px_ha, 1), "seen_share": round(float(seen.mean()), 3),
           "seasonal_offsets": det.offsets, "by_kind": {}}
    for code, name in CLASS_NAMES.items():
        m = (det.classes == code) & in_region
        if not m.any():
            continue
        out["by_kind"][name] = {
            "area_ha": round(float(m.sum()) * px_ha, 1),
            "sure_ha": round(float((m & (det.confidence == SURE)).sum()) * px_ha, 1),
            "unsure_ha": round(float((m & (det.confidence == UNSURE)).sum()) * px_ha, 1),
        }
    return out
