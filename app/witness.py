"""SAM 3.1 as a second witness, independent of the index rules.

The rules decide change from band arithmetic. SAM 3.1 gets no hint from them: it is shown the before image and
the after image separately and asked, by a plain concept prompt, to find "water", "burn scar", "trees" or
"building" in each. What SAM finds after but not before (or before but not after, for water gone and trees
gone) is SAM's own view of the change. Comparing the two gives three answers per pixel:

    both see it          the rules and SAM agree
    only the rules       the rules saw change that SAM did not find
    only SAM             SAM saw change the rules did not; listed as places to check by eye

Agreement is reported per patch and per change type. It does not overwrite the rules' own confidence: SAM on
20 m pixels is a second opinion, not ground truth, and both views stay visible.
"""
from __future__ import annotations

import numpy as np
from PIL import Image
from scipy import ndimage

from app import imagery, meta_client
from app.analysis import Analysis, _png
from app.detect import BUILT, BURN, CLASS_NAMES, VEG_LOSS, WATER_GONE, WATER_NEW

# change type -> (concept SAM looks for, "new" = found after but not before, "gone" = before but not after)
CONCEPTS = {BURN: ("burn scar", "new"), WATER_NEW: ("water", "new"), WATER_GONE: ("water", "gone"),
            VEG_LOSS: ("trees", "gone"), BUILT: ("building", "new")}
MIN_SAM_CONFIDENCE = 0.3     # SAM instances below this score are ignored
AGREES, PARTLY = 0.5, 0.2    # share of a patch SAM also marks: agrees / partly / does not see it
MIN_MISSED_PX = 25           # 1 ha at 20 m: smaller "only SAM" blobs are not listed
MAX_MISSED = 8
COLOURS = {"both": (46, 204, 113, 210), "rules_only": (241, 196, 15, 190), "sam_only": (155, 89, 182, 200)}


def sam_image(comp: imagery.Composite) -> tuple[np.ndarray, int]:
    """True colour for SAM, doubled when small (SAM sees 20 m pixels better a little larger)."""
    img = imagery.true_colour(comp)[..., :3]
    h, w = img.shape[:2]
    scale = 2 if max(h, w) < 700 else 1
    if scale > 1:
        img = np.asarray(Image.fromarray(img).resize((w * scale, h * scale), Image.BILINEAR))
    return img, scale


def _union(instances: list[dict], shape: tuple[int, int]) -> np.ndarray:
    """All confident instances as one mask on the analysis grid."""
    h, w = shape
    out = None
    for r in instances:
        if r.get("confidence") is not None and r["confidence"] < MIN_SAM_CONFIDENCE:
            continue
        out = r["mask"] if out is None else out | r["mask"]
    if out is None:
        return np.zeros(shape, bool)
    if out.shape != shape:
        out = np.asarray(Image.fromarray(out.astype(np.uint8) * 255).resize((w, h), Image.NEAREST)) > 0
    return out


def _level(share: float) -> str:
    return "agrees" if share >= AGREES else "partly" if share >= PARTLY else "does not see it"


def second_witness(a: Analysis) -> dict:
    codes = sorted({f["properties"]["code"] for f in a.regions})
    if not codes:
        return {"note": "no changed patches to compare"}
    phrases = sorted({CONCEPTS[c][0] for c in codes})
    imgs = {"before": sam_image(a.comp_a)[0], "after": sam_image(a.comp_b)[0]}
    jobs = [(phrase, when) for phrase in phrases for when in ("before", "after")]
    answers = meta_client.segment_many([(imgs[when], phrase) for phrase, when in jobs])
    if all(isinstance(r, Exception) for r in answers):
        raise answers[0]
    shape = a.det.classes.shape
    found, failed = {}, set()
    for (phrase, when), r in zip(jobs, answers):
        if isinstance(r, Exception):
            failed.add(phrase)
        else:
            found[phrase, when] = _union(r, shape)

    seen, rid = a.det.seen, a.region_ids
    in_region = rid > 0
    sizes = np.bincount(rid.ravel(), minlength=len(a.regions) + 1)
    px_ha = a.grid.pixel_area_ha()
    both, rules_only, sam_only = (np.zeros(shape, bool) for _ in range(3))
    share = np.full(len(a.regions) + 1, np.nan)
    by_kind, missed = {}, []
    for code in codes:
        phrase, mode = CONCEPTS[code]
        name = CLASS_NAMES[code]
        if phrase in failed:
            by_kind[name] = {"note": f"SAM call for '{phrase}' failed"}
            continue
        before, after = found[phrase, "before"], found[phrase, "after"]
        sam = (after & ~before if mode == "new" else before & ~after) & seen
        rules = (a.det.classes == code) & in_region
        both |= rules & sam
        rules_only |= rules & ~sam
        extra = sam & ~in_region
        sam_only |= extra
        hits = np.bincount(rid[rules & sam], minlength=len(sizes))
        mine = np.unique(rid[rules])
        share[mine] = hits[mine] / np.maximum(sizes[mine], 1)
        by_kind[name] = {"concept": phrase, "rules_ha": round(float(rules.sum()) * px_ha, 1),
                         "both_ha": round(float((rules & sam).sum()) * px_ha, 1),
                         "only_sam_ha": round(float(extra.sum()) * px_ha, 1)}
        labels, n = ndimage.label(extra, structure=np.ones((3, 3), bool))
        if n:
            counts = np.bincount(labels.ravel())
            for lab in np.argsort(counts[1:])[::-1][:MAX_MISSED] + 1:
                if counts[lab] < MIN_MISSED_PX:
                    break
                ys, xs = np.nonzero(labels == lab)
                lon, lat = a.grid.transform @ (xs.mean() + 0.5, ys.mean() + 0.5)
                missed.append({"kind": name, "area_ha": round(float(counts[lab]) * px_ha, 1),
                               "center": [round(float(lon), 5), round(float(lat), 5)]})

    for f in a.regions:
        p = f["properties"]
        s = share[p["id"]]
        if not np.isnan(s):
            p["sam_witness"] = {"share": round(float(s), 2), "verdict": _level(float(s))}
    rgba = np.zeros((*shape, 4), np.uint8)
    for key, m in (("both", both), ("rules_only", rules_only), ("sam_only", sam_only)):
        rgba[m] = COLOURS[key]
    a.layers["agreement"] = _png(rgba)
    total_rules = float((both | rules_only).sum())
    a.witness = {
        "calls": len(jobs), "failed_concepts": sorted(failed),
        "agree_share": round(float(both.sum()) / total_rules, 2) if total_rules else None,
        "both_ha": round(float(both.sum()) * px_ha, 1), "only_rules_ha": round(float(rules_only.sum()) * px_ha, 1),
        "only_sam_ha": round(float(sam_only.sum()) * px_ha, 1), "by_kind": by_kind,
        "only_sam_places": sorted(missed, key=lambda m: -m["area_ha"])[:MAX_MISSED],
    }
    return a.witness
