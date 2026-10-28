"""Muse Spark looks at every big patch: a visual second opinion on each label the rules gave.

For each patch Muse Spark gets two true colour crops, before and after, with the patch edge drawn in yellow,
and the label the rules gave it. It answers in a fixed JSON shape (structured output): agrees, disagrees or
unclear, what the patch looks like to it, what it sees, and why. It is not told how sure the rules were, so the
answer is its own. Disagreements are flagged on the map and listed; nothing is deleted. Checks are cached per
patch, so asking again costs nothing for patches already seen.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app import meta_client
from app.analysis import Analysis, outline_crop

VERDICTS = ["agrees", "disagrees", "unclear"]
LOOKS_LIKE = ["burned land", "new water", "water gone", "vegetation loss", "new building or bare ground",
              "no real change", "cloud, haze or shadow", "seasonal change in crops or grass", "something else"]
MAX_PATCHES = 20       # patches looked at per run, largest first (one vision call each)
MIN_PATCH_HA = 2.0     # smaller patches are too few pixels to judge by eye at 20 m
AT_ONCE = 4

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": VERDICTS},
        "looks_like": {"type": "string", "enum": LOOKS_LIKE},
        "what_you_see": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "looks_like", "what_you_see", "reason"],
}

PROMPT = (
    "You are checking a satellite change detector. The two images are Sentinel-2 true colour crops of the same "
    "place, before ({before}) and after ({after}), about {res} m per pixel, contrast stretched. The yellow line "
    "marks a patch of {area_ha} ha that the detector labelled '{kind}'. Look at both images and say whether you "
    "agree with that label. Burned land looks dark brown or black after; new water dark blue or muddy brown "
    "and smooth; vegetation loss green turning to brown or bare soil; new building or bare ground grey, white "
    "or bright soil. Answer in JSON with: verdict (agrees, disagrees or unclear), looks_like (your own label), "
    "what_you_see (one short sentence) and reason (one short sentence). Say unclear if cloud, haze, shadow or "
    "the pixel size make it impossible to tell. Do not guess. Do not use dashes as punctuation.")


def _clean(ans: dict) -> dict:
    """Keep only known values, so a sloppy answer can never show up as a verdict it did not give."""
    verdict = str(ans.get("verdict", "")).strip().lower()
    looks = str(ans.get("looks_like", "")).strip().lower()
    return {"verdict": verdict if verdict in VERDICTS else "unclear",
            "looks_like": looks if looks in LOOKS_LIKE else None,
            "what_you_see": str(ans.get("what_you_see", ""))[:300],
            "reason": str(ans.get("reason", ans.get("raw", "")))[:300]}


def _look(a: Analysis, f: dict) -> dict:
    p = f["properties"]
    res = round(a.grid.pixel_area_ha() ** 0.5 * 100) if a.grid is not None else 20
    q = PROMPT.format(before=a.before, after=a.after, res=res, area_ha=p["area_ha"], kind=p["kind"])
    ans = meta_client.look(q, [("Before:", outline_crop(a, p["id"], "before")),
                               ("After:", outline_crop(a, p["id"], "after"))], schema=SCHEMA)
    return _clean(ans)


def check_regions(a: Analysis, ids: list[int] | None = None, max_n: int | None = None) -> dict:
    """Visual checks of the given patches, a few at a time; cached per patch.

    Without ids: the next max_n big patches not yet looked at, largest first, so each call goes further down
    the list; the answer then covers every big patch looked at so far.
    """
    max_n = max_n or MAX_PATCHES
    by_id = {f["properties"]["id"]: f for f in a.regions}
    if ids is None:
        ids = [f["properties"]["id"] for f in a.regions if f["properties"]["area_ha"] >= MIN_PATCH_HA]
        todo = [i for i in ids if i not in a.checks][:max_n]
    else:
        ids = [i for i in dict.fromkeys(ids) if i in by_id][:max_n]
        todo = [i for i in ids if i not in a.checks]
    if todo:
        with ThreadPoolExecutor(AT_ONCE) as pool:
            answers = list(pool.map(lambda i: _safe_look(a, by_id[i]), todo))
        failed = [e for e in answers if isinstance(e, Exception)]
        if failed and len(failed) == len(todo):
            raise failed[0]
        for i, ans in zip(todo, answers):
            if not isinstance(ans, Exception):   # a failed look is simply not cached; asking again retries it
                a.checks[i] = ans
    return {i: a.checks[i] for i in ids if i in a.checks}


def _safe_look(a, f):
    try:
        return _look(a, f)
    except Exception as e:
        return e


def check_every_big_patch(a: Analysis) -> dict:
    """Check the largest patches and summarise: counts, hectares, and every disagreement with what was seen."""
    done = check_regions(a)
    by_id = {f["properties"]["id"]: f["properties"] for f in a.regions}
    counts = {v: 0 for v in VERDICTS}
    ha = {v: 0.0 for v in VERDICTS}
    flagged = []
    for rid, c in done.items():
        p = by_id[rid]
        counts[c["verdict"]] += 1
        ha[c["verdict"]] += p["area_ha"]
        if c["verdict"] != "agrees":
            flagged.append({"id": rid, "kind": p["kind"], "area_ha": p["area_ha"], "center": p["center"], **c})
    big = [f for f in a.regions if f["properties"]["area_ha"] >= MIN_PATCH_HA]
    a.visual = {"checked": len(done), "eligible": len(big), "remaining": len(big) - len(done), "counts": counts,
                "ha": {k: round(v, 1) for k, v in ha.items()},
                "flagged": sorted(flagged, key=lambda f: (f["verdict"] != "disagrees", -f["area_ha"]))}
    return a.visual
