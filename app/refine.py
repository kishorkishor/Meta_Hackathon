"""SAM 3.1 outlines on top of the index detector.

Each large region's box is sent to SAM 3.1 on the after image. SAM's mask is kept only inside the detector's
region grown by 3 pixels and only where the ground was clear in both periods, so SAM can sharpen an edge but
cannot invent change where the satellite saw none. Raw areas are kept for comparison.
(The Muse Spark visual check lives in visual.py.)
"""
from __future__ import annotations

import numpy as np
from PIL import Image
from scipy import ndimage

from app import imagery, meta_client
from app.analysis import Analysis, change_rgba, region_mask, _png
from app.detect import LIKELY

GROW = 3
MIN_BOX_PX = 4
MIN_AGREEMENT = 0.3   # SAM masks overlapping the detector less than this are not used


def _iou(a, b):
    u = (a | b).sum()
    return float((a & b).sum() / u) if u else 0.0


def refine_with_sam(a: Analysis, max_regions: int = 16) -> dict:
    img = imagery.true_colour(a.comp_b)[..., :3]
    h, w = img.shape[:2]
    scale = 2 if max(h, w) < 700 else 1
    if scale > 1:
        img = np.asarray(Image.fromarray(img).resize((w * scale, h * scale), Image.BILINEAR))
    regs = [f for f in a.regions
            if min(f["properties"]["pixel_box"][2] - f["properties"]["pixel_box"][0],
                   f["properties"]["pixel_box"][3] - f["properties"]["pixel_box"][1]) >= MIN_BOX_PX][:max_regions]
    if not regs:
        return {"regions": [], "note": "no region large enough for SAM"}
    boxes = [tuple(v * scale for v in f["properties"]["pixel_box"]) for f in regs]
    results = {int(r["id"]): r for r in meta_client.segment(img, boxes=boxes)}

    px_ha = a.grid.pixel_area_ha()
    classes = a.det.classes.copy()
    confidence = a.det.confidence.copy()
    rows = []
    for i, f in enumerate(regs, 1):
        rid, code = f["properties"]["id"], f["properties"]["code"]
        det_mask = region_mask(a, rid)
        row = {"id": rid, "kind": f["properties"]["kind"], "detector_ha": round(float(det_mask.sum()) * px_ha, 2)}
        r = results.get(i)
        if r is None:
            rows.append({**row, "used": False, "why": "SAM returned no mask"})
            continue
        sam = r["mask"]
        if scale > 1:
            sam = np.asarray(Image.fromarray(sam.astype(np.uint8) * 255).resize((w, h), Image.NEAREST)) > 0
        # SAM may keep detected pixels, and may add neighbours only where the same change shows weakly
        # (every condition of the rule holds within its margin) and both periods were clear.
        near = ndimage.binary_dilation(det_mask, iterations=GROW) & a.det.seen
        allowed = det_mask | (near & (a.det.weak == code) & (classes == 0))
        kept = sam & allowed
        added = kept & ~det_mask
        agree = _iou(kept, det_mask)
        row.update(sam_raw_ha=round(float(sam.sum()) * px_ha, 2), sam_kept_ha=round(float(kept.sum()) * px_ha, 2),
                   added_ha=round(float(added.sum()) * px_ha, 2), removed_ha=round(float((det_mask & ~kept).sum()) * px_ha, 2),
                   rejected_ha=round(float((sam & ~allowed).sum()) * px_ha, 2), agreement=round(agree, 2),
                   sam_confidence=r.get("confidence"))
        if agree < MIN_AGREEMENT:
            rows.append({**row, "used": False, "why": "SAM outline disagrees with the detector; kept the detector"})
            continue
        classes[det_mask & ~kept] = 0
        confidence[det_mask & ~kept] = 0
        classes[added] = code
        confidence[added] = LIKELY   # weak evidence plus SAM's edge: never "sure"
        rows.append({**row, "used": True})
    final = sum(r["sam_kept_ha"] if r["used"] else r["detector_ha"] for r in rows)
    a.refined = {"regions": rows, "used": sum(r["used"] for r in rows),
                 "detector_ha": round(sum(r["detector_ha"] for r in rows), 1), "final_ha": round(final, 1)}
    a.layers["refined"] = _png(change_rgba(a.det, classes, confidence))
    return a.refined

