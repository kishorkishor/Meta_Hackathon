"""Turn a before and an after composite into change classes, a confidence level and reasons for doubt.

Seasonal shift first: between two dates a whole landscape can green up, dry out or brown. The typical change
of each index over ground that changed only a little is taken off before the rules run. This is the "dNBR
offset" practice of burn mapping (offset from unburned ground), applied to every index.

Rules (each is a set of conditions that must all hold; thresholds in docs/METHOD.md):
  new water       MNDWI after > 0, before < 0, rise >= 0.2
  water gone      the reverse
  burned land     dNBR >= 0.27, NDVI before >= 0.3, visible brightness after <= 0.12 (char and ash are dark;
                  cleared soil, roofs and concrete are brighter)
  new building or bare ground   NDBI after > 0, rise >= 0.1, NDVI drop >= 0.15
  vegetation loss NDVI before >= 0.5, drop >= 0.2, NDVI after <= 0.5 (real loss of cover, not drying)
The first rule that holds wins, in that order.

A detected pixel is "sure" only if every condition holds by its margin and nothing below casts doubt on it:
  near threshold    some condition holds by less than its margin
  could be shift    the class disappears when the before image is moved by one pixel in some direction
  near cloud        within 2 pixels of a pixel with no clear look
  dark              very dark in either image (terrain or cloud shadow)
  one look          only one clear observation in either window
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

from app.indices import all_indices

NONE, BURN, WATER_NEW, WATER_GONE, VEG_LOSS, BUILT = range(6)
CLASS_NAMES = {BURN: "burned land", WATER_NEW: "new water", WATER_GONE: "water gone",
               VEG_LOSS: "vegetation loss", BUILT: "new building or bare ground"}
CLASS_COLOURS = {BURN: (230, 85, 13), WATER_NEW: (33, 113, 181), WATER_GONE: (158, 202, 225),
                 VEG_LOSS: (166, 97, 26), BUILT: (203, 24, 29)}
ORDER = (WATER_NEW, WATER_GONE, BURN, BUILT, VEG_LOSS)

FLAG_NEAR, FLAG_SHIFT, FLAG_CLOUD, FLAG_DARK, FLAG_ONE_LOOK = 1, 2, 4, 8, 16
FLAG_NAMES = {FLAG_NEAR: "near threshold", FLAG_SHIFT: "could be image shift", FLAG_CLOUD: "near cloud",
              FLAG_DARK: "dark or shadow", FLAG_ONE_LOOK: "only one clear look"}
UNSURE, LIKELY, SURE = 1, 2, 3

DARK = 0.02                   # mean visible reflectance below this in either image
CLOUD_BUFFER = 2
MIN_PIXELS_FOR_OFFSET = 500   # below this the scene median is too noisy to trust; no offset
MAX_OFFSET = 0.15             # largest seasonal shift taken off any index
STABLE_MAX = 0.25             # pixels whose index moved more than this are not used to measure the season
MIN_STABLE_SHARE = 0.2        # need at least this share of the clear area as stable ground
SHIFTS = [(dy, dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if (dy, dx) != (0, 0)]


@dataclass
class Detection:
    classes: np.ndarray       # uint8 class per pixel
    confidence: np.ndarray    # uint8 0 none, 1 unsure, 2 likely, 3 sure
    flags: np.ndarray         # uint8 bit mask of reasons for doubt
    seen: np.ndarray          # bool, clear in both windows
    weak: np.ndarray          # uint8 class whose conditions all hold at least within their margins (for SAM)
    before: dict
    after: dict
    offsets: dict = field(default_factory=dict)


def _layers(bands: dict) -> dict:
    return all_indices(bands)


def scene_offsets(a: dict, b: dict, seen: np.ndarray) -> dict:
    """The seasonal shift of each index, measured only on ground that changed a little.

    Pixels whose index moved by more than STABLE_MAX are left out, so a real burn or flood that covers much
    of the area cannot pull the estimate and explain itself away. If too little stable ground is left, the
    shift is not taken off at all (0). The result is capped at MAX_OFFSET."""
    keys = {"nbr_drop": a["nbr"] - b["nbr"], "ndvi_drop": a["ndvi"] - b["ndvi"],
            "ndbi_rise": b["ndbi"] - a["ndbi"], "mndwi_rise": b["mndwi"] - a["mndwi"]}
    out = {}
    with np.errstate(invalid="ignore"):
        # ground that turned from land to water or back is never "stable", however small the index change
        flipped = (a["mndwi"] < 0) != (b["mndwi"] < 0)
        for k, v in keys.items():
            stable = seen & ~flipped & (np.abs(v) <= STABLE_MAX)
            if stable.sum() < MIN_PIXELS_FOR_OFFSET or stable.sum() < MIN_STABLE_SHARE * max(seen.sum(), 1):
                out[k] = 0.0
            else:
                out[k] = round(float(np.clip(np.median(v[stable]), -MAX_OFFSET, MAX_OFFSET)), 4)
    return out


def conditions(a: dict, b: dict, off: dict) -> dict:
    """Per class: list of (value, threshold, margin); a condition holds when value >= threshold."""
    nbr_drop = a["nbr"] - b["nbr"] - off["nbr_drop"]
    ndvi_drop = a["ndvi"] - b["ndvi"] - off["ndvi_drop"]
    ndbi_rise = b["ndbi"] - a["ndbi"] - off["ndbi_rise"]
    mndwi_rise = b["mndwi"] - a["mndwi"] - off["mndwi_rise"]
    return {
        WATER_NEW: [(b["mndwi"], 0.0, 0.05), (-a["mndwi"], 0.0, 0.05), (mndwi_rise, 0.20, 0.10)],
        WATER_GONE: [(a["mndwi"], 0.0, 0.05), (-b["mndwi"], 0.0, 0.05), (-mndwi_rise, 0.20, 0.10)],
        BURN: [(nbr_drop, 0.27, 0.10), (a["ndvi"], 0.30, 0.05), (-b["bright"], -0.12, 0.02)],
        BUILT: [(b["ndbi"], 0.0, 0.03), (ndbi_rise, 0.10, 0.05), (ndvi_drop, 0.15, 0.05)],
        VEG_LOSS: [(a["ndvi"], 0.50, 0.05), (ndvi_drop, 0.20, 0.10), (-b["ndvi"], -0.50, 0.05)],
    }


def classify(a: dict, b: dict, seen: np.ndarray, off: dict):
    """Classes, whether every condition clears its margin, and the weak (within margin) class map."""
    shape = seen.shape
    classes = np.zeros(shape, np.uint8)
    clear_margin = np.zeros(shape, bool)
    weak = np.zeros(shape, np.uint8)
    conds = conditions(a, b, off)
    with np.errstate(invalid="ignore"):
        for code in ORDER:
            holds = seen.copy()
            beyond = seen.copy()
            close = seen.copy()
            for value, th, margin in conds[code]:
                holds &= value >= th
                beyond &= value >= th + margin
                close &= value >= th - margin
            free = classes == NONE
            classes[holds & free] = code
            clear_margin |= holds & free & beyond
            weak[close & (weak == NONE)] = code
    return classes, clear_margin, weak


def _shift(arr: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """Move an image by one pixel, repeating the edge."""
    p = np.pad(arr, 1, mode="edge")
    h, w = arr.shape
    return p[1 + dy:1 + dy + h, 1 + dx:1 + dx + w]


def detect(before_bands: dict, after_bands: dict, count_before=None, count_after=None) -> Detection:
    a, b = _layers(before_bands), _layers(after_bands)
    seen = np.isfinite(a["nbr"]) & np.isfinite(b["nbr"]) & np.isfinite(a["mndwi"]) & np.isfinite(b["mndwi"])
    shape = seen.shape
    off = scene_offsets(a, b, seen)
    classes, clear_margin, weak = classify(a, b, seen, off)
    changed = classes > NONE

    # Misregistration: if moving the before image by one pixel makes the class go away, the "change" may be
    # an edge that the two images place one pixel apart.
    shift = np.zeros(shape, bool)
    if changed.any():
        for dy, dx in SHIFTS:
            a_s = {k: _shift(v, dy, dx) for k, v in a.items()}
            c_s, _, _ = classify(a_s, b, seen, off)
            shift |= changed & (c_s != classes)

    with np.errstate(invalid="ignore"):
        dark = (a["bright"] < DARK) | (b["bright"] < DARK)
    near_cloud = ndimage.binary_dilation(~seen, iterations=CLOUD_BUFFER) & seen
    one_look = np.zeros(shape, bool)
    if count_before is not None and count_after is not None:
        one_look = seen & ((count_before == 1) | (count_after == 1))

    flags = np.zeros(shape, np.uint8)
    for bit, m in ((FLAG_NEAR, ~clear_margin), (FLAG_SHIFT, shift), (FLAG_CLOUD, near_cloud),
                   (FLAG_DARK, dark), (FLAG_ONE_LOOK, one_look)):
        flags[changed & m] |= bit

    serious = FLAG_SHIFT | FLAG_DARK
    two_or_more = np.unpackbits(flags[..., None], axis=-1).sum(-1) >= 2
    confidence = np.zeros(shape, np.uint8)
    confidence[changed] = SURE
    confidence[changed & (flags > 0)] = LIKELY
    confidence[changed & (((flags & serious) > 0) | two_or_more)] = UNSURE
    return Detection(classes, confidence, flags, seen, weak, a, b, off)
