"""Ask in plain words: Muse Spark turns a question into a place, two dates and what to look for.

"What did the August floods do to farms near Sylhet?" needs three things the map form asks for by hand: an area,
a before date and an after date. Muse Spark finds them with tools. It searches the web for when the event
happened (search grounding), looks the place up in OpenStreetMap, checks that Sentinel-2 actually has clear
looks near both dates, and then submits a plan. The plan is checked in code against the same limits as the map
form; a plan that breaks one goes back to the model with the reason, so it can fix it instead of failing.

Runs on the Responses API, because search grounding is not offered over Chat Completions.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from datetime import date, timedelta

import numpy as np
import requests

from app import analysis, config as C, imagery, languages, meta_client

KINDS = ["burned land", "new water", "water gone", "vegetation loss", "new building or bare ground"]
FIRST_DATE = date(2017, 1, 1)     # Sentinel-2 L2A covers the whole world from about here
DEFAULT_SIDE_KM = 10.0            # a 10 km box keeps the fine 20 m grid and runs in about a minute

SYSTEM = """You plan a satellite change analysis from a question in plain words. Today is {today}.

The analysis compares free Sentinel-2 images (10 to 20 m pixels, every 5 days, blocked by cloud) of one
rectangle at two dates and maps: burned land, new water (floods, new reservoirs), water gone, vegetation loss
(clearing, logging) and new buildings or bare ground. It cannot see people, damage to single houses, crops by
type, or anything under cloud.

Steps:
1. If the question names an event (a flood, a fire, a cyclone), find when it happened. Search the web when you
   are not sure of the dates; never guess a year.
2. Call find_place to get coordinates. Never make up coordinates.
3. Pick the rectangle: each side between 0.3 and 40 km, at most 400 km2. Prefer about 10 km by 10 km around the
   place or the part of it the question is about; go bigger only when the question needs it.
4. Pick the before date shortly before the event and the after date shortly after it (for floods, at or near
   the peak; for fires, after the fire is out). Both must be on or before today and after 2017.
5. Call check_imagery for both dates. If a date has no clear scene, move it or widen the window and check again.
   Monsoon floods are often under cloud: then pick the first clear spell after the peak and say so.
6. Call submit_plan. If the question cannot be answered with this tool, or the place cannot be found, call
   give_up with a short reason in plain words.

Write short plain sentences. Do not use dashes as punctuation."""


def _fn(name, description, properties, required):
    return {"type": "function", "name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required}}


_BOX = {k: {"type": "number"} for k in ("west", "south", "east", "north")}
TOOLS = [
    _fn("find_place", "Look a place up in OpenStreetMap. Returns up to 5 matches with coordinates, size and a "
        "suggested rectangle.", {"query": {"type": "string"}}, ["query"]),
    _fn("check_imagery", "Count Sentinel-2 scenes over a rectangle within window_days of a date, with the "
        "clearest dates and their whole scene cloud cover.",
        {**_BOX, "date": {"type": "string", "description": "YYYY-MM-DD"},
         "window_days": {"type": "integer", "minimum": 3, "maximum": 90}},
        ["west", "south", "east", "north", "date", "window_days"]),
    _fn("submit_plan", "Submit the final plan. It is checked; if something is wrong you get the reason back.",
        {"place_name": {"type": "string"}, **_BOX,
         "before": {"type": "string", "description": "YYYY-MM-DD"},
         "after": {"type": "string", "description": "YYYY-MM-DD"},
         "window_days": {"type": "integer", "minimum": 3, "maximum": 90,
                         "description": "days searched on each side of both dates for clear looks"},
         "looking_for": {"type": "array", "items": {"type": "string", "enum": KINDS}},
         "why": {"type": "string", "description": "two or three plain sentences: why this area and these dates"}},
        ["place_name", "west", "south", "east", "north", "before", "after", "window_days", "looking_for", "why"]),
    _fn("give_up", "Say why this question cannot be answered with this tool.",
        {"reason": {"type": "string"}}, ["reason"]),
]


class PlanError(Exception):
    pass


# ---------------------------------------------------------------------------------------------------
# rate limit: each plan is several paid calls and the public demo shares one key
# ---------------------------------------------------------------------------------------------------

_RECENT: deque = deque()
_RATE_LOCK = threading.Lock()


def allow() -> bool:
    with _RATE_LOCK:
        now = time.time()
        while _RECENT and now - _RECENT[0] > 3600:
            _RECENT.popleft()
        if len(_RECENT) >= C.PLANS_PER_HOUR:
            return False
        _RECENT.append(now)
        return True


# ---------------------------------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------------------------------

def box_around(lon: float, lat: float, side_km: float = DEFAULT_SIDE_KM) -> list[float]:
    half_lat = side_km / 2 / 111.32
    half_lon = side_km / 2 / (111.32 * max(float(np.cos(np.radians(lat))), 0.05))
    return [round(lon - half_lon, 5), round(lat - half_lat, 5), round(lon + half_lon, 5), round(lat + half_lat, 5)]


def _side_km(bbox) -> tuple[float, float]:
    w, s, e, n = bbox
    return (e - w) * 111.32 * np.cos(np.radians((s + n) / 2)), (n - s) * 111.32


def suggest_box(lon: float, lat: float, place_bbox=None) -> list[float]:
    """The place's own outline when it is a useful size (a district, a lake), else a 10 km box around its centre.

    A point like a station or a village outline of a few hundred metres would show almost nothing around it.
    """
    if place_bbox is not None:
        wkm, hkm = _side_km(place_bbox)
        if 3 <= min(wkm, hkm) and max(wkm, hkm) <= 20:
            return [round(v, 5) for v in place_bbox]
    return box_around(lon, lat)


_GEO_LOCK = threading.Lock()
_GEO_LAST = [0.0]
_GEO_CACHE: dict = {}


def find_place(query: str) -> dict:
    """OpenStreetMap Nominatim, at most one request a second as its usage policy asks, cached."""
    key = query.strip().lower()
    if key in _GEO_CACHE:
        return _GEO_CACHE[key]
    with _GEO_LOCK:
        wait = 1.1 - (time.time() - _GEO_LAST[0])
        if wait > 0:
            time.sleep(wait)
        r = requests.get(C.GEOCODER_URL, params={"q": query, "format": "jsonv2", "limit": 5, "accept-language": "en"},
                         headers={"User-Agent": C.USER_AGENT}, timeout=20)
        _GEO_LAST[0] = time.time()
    r.raise_for_status()
    matches = []
    for m in r.json():
        lat, lon = float(m["lat"]), float(m["lon"])
        s, n, w, e = (float(v) for v in m["boundingbox"])
        wkm, hkm = _side_km((w, s, e, n))
        matches.append({"name": m.get("display_name", ""), "kind": f'{m.get("category", m.get("class", ""))} '
                        f'{m.get("type", "")}'.strip(), "lat": round(lat, 5), "lon": round(lon, 5),
                        "size_km": [round(float(wkm), 1), round(float(hkm), 1)],
                        "suggested_box": suggest_box(lon, lat, (w, s, e, n))})
    out = {"matches": matches} if matches else {"matches": [], "note": "nothing found; try a simpler name"}
    _GEO_CACHE[key] = out
    return out


def check_imagery(bbox, day: date, window_days: int) -> dict:
    start, end = imagery.window_around(day, window_days)
    end = min(end, date.today())
    best: dict[str, float] = {}
    for item in imagery.search(bbox, start, end):
        d = item["properties"]["datetime"][:10]
        cc = float(item["properties"].get("eo:cloud_cover", 100))
        best[d] = min(cc, best.get(d, 100.0))
    ranked = sorted(best.items(), key=lambda kv: kv[1])
    return {"window": [str(start), str(end)], "scenes": len(best),
            "clearest": [{"date": d, "scene_cloud_percent": round(c)} for d, c in ranked[:6]],
            "note": "scene cloud is for the whole 110 km tile; the analysis measures cloud over the area itself"}


def _date(s) -> date:
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        raise ValueError(f"{s!r} is not a date like 2024-08-25")


def check_plan(args: dict) -> dict:
    """The same limits as the map form. Raises ValueError with a reason the model can act on."""
    bbox = tuple(round(float(args[k]), 5) for k in ("west", "south", "east", "north"))
    before, after = _date(args["before"]), _date(args["after"])
    window = int(args.get("window_days") or C.WINDOW_DAYS)
    if not 3 <= window <= 90:
        raise ValueError("window_days must be between 3 and 90")
    if before < FIRST_DATE:
        raise ValueError("the before date must be in 2017 or later")
    if after > date.today():
        raise ValueError(f"the after date is in the future; today is {date.today()}")
    try:
        analysis.validate(bbox, before, after)
    except ValueError as e:
        if "km" not in str(e):     # a date problem: no box to suggest
            raise
        cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        raise ValueError(f"{e}. A box that fits: {box_around(cx, cy)}")
    kinds = [k for k in args.get("looking_for") or [] if k in KINDS]
    return {"place": str(args.get("place_name", "")).strip()[:200], "bbox": list(bbox), "before": str(before),
            "after": str(after), "window_days": window, "looking_for": kinds,
            "why": str(args.get("why", "")).strip()[:1200]}


# ---------------------------------------------------------------------------------------------------
# the loop
# ---------------------------------------------------------------------------------------------------

def _get(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def _collect(resp, sources: dict, steps: list):
    """Citations from grounded text and the searches the model ran, for the page to show."""
    for item in _get(resp, "output", []) or []:
        kind = _get(item, "type")
        if kind == "web_search_call":
            q = _get(_get(item, "action", {}) or {}, "query")
            steps.append({"step": "search", "text": f"Searched the web: {q}" if q else "Searched the web"})
            for r in _get(item, "results", []) or []:
                url = _get(r, "url")
                if url and url not in sources and len(sources) < 8:
                    sources[url] = _get(r, "title") or url
        elif kind == "message":
            for part in _get(item, "content", []) or []:
                for ann in _get(part, "annotations", []) or []:
                    url = _get(ann, "url")
                    if _get(ann, "type") == "url_citation" and url and len(sources) < 8:
                        sources[url] = _get(ann, "title") or url


def _run_tool(name: str, args: dict, steps: list):
    """Returns (result for the model, final plan or None)."""
    if name == "find_place":
        res = find_place(str(args.get("query", ""))[:200])
        top = res["matches"][0]["name"] if res["matches"] else "nothing"
        steps.append({"step": "place", "text": f"Looked up {args.get('query')!r}: {top}"})
        return res, None
    if name == "check_imagery":
        bbox = tuple(float(args[k]) for k in ("west", "south", "east", "north"))
        day, window = _date(args["date"]), max(3, min(90, int(args.get("window_days") or C.WINDOW_DAYS)))
        res = check_imagery(bbox, day, window)
        best = res["clearest"][0] if res["clearest"] else None
        steps.append({"step": "imagery", "text": f"Images within {window} days of {day}: {res['scenes']} dates" +
                      (f", clearest {best['date']} ({best['scene_cloud_percent']}% cloud)" if best else "")})
        return res, None
    if name == "submit_plan":
        try:
            plan = check_plan(args)
        except (ValueError, KeyError, TypeError) as e:
            steps.append({"step": "fix", "text": f"Plan sent back: {e}"})
            return {"accepted": False, "problem": str(e)}, None
        steps.append({"step": "plan", "text": f"Plan: {plan['place']}, {plan['before']} vs {plan['after']}"})
        return {"accepted": True}, {"ok": True, **plan}
    if name == "give_up":
        return {"ok": True}, {"ok": False, "reason": str(args.get("reason", "")).strip()[:600]}
    return {"error": f"unknown tool {name}"}, None


def plan(question: str, language: str = "en") -> dict:
    """Question in, plan out: {"ok": True, place, bbox, before, after, window_days, looking_for, why, sources,
    steps} or {"ok": False, reason, sources, steps}. Raises PlanError if no plan comes out in time.

    The question may be in any language. With a language other than English, `why` and the give up reason come
    back in it; place lookups still use names OpenStreetMap knows."""
    instructions = SYSTEM.format(today=date.today().isoformat())
    if languages.get(language)["code"] != "en":
        instructions += ("\n\nThe reader's language is {name}. Write why and the give_up reason in {name} "
                         "({native}). Search places with the name OpenStreetMap uses, in Latin letters when the place "
                         "has one, and keep looking_for values exactly as listed.").format(**languages.get(language))
    tools = TOOLS + ([{"type": "web_search", "search_context_size": "low"}] if C.WEB_SEARCH else [])
    sources, steps = {}, []
    try:
        resp = meta_client.respond(question, tools, instructions)
    except Exception as e:
        if not (C.WEB_SEARCH and type(e).__name__ == "BadRequestError"):
            raise
        tools = TOOLS        # search grounding refused on this account: plan from the model's own knowledge
        steps.append({"step": "note", "text": "Web search was not available; dates come from the model"})
        resp = meta_client.respond(question, tools, instructions)
    for _ in range(C.PLANNER_MAX_STEPS):
        _collect(resp, sources, steps)
        calls = [i for i in _get(resp, "output", []) or [] if _get(i, "type") == "function_call"]
        if not calls:
            new_input = [{"role": "user", "content": "Call submit_plan or give_up now."}]
        else:
            new_input = []
            for c in calls:
                try:
                    args = json.loads(_get(c, "arguments") or "{}")
                    result, final = _run_tool(_get(c, "name"), args, steps)
                except Exception as e:   # a failed lookup is information for the model, not a crash
                    result, final = {"error": f"{type(e).__name__}: {e}"}, None
                if final is not None:
                    return {**final, "question": question, "language": languages.get(language)["code"],
                            "steps": steps,
                            "sources": [{"url": u, "title": t} for u, t in sources.items()]}
                new_input.append({"type": "function_call_output", "call_id": _get(c, "call_id"),
                                  "output": json.dumps(result, default=str)})
        resp = meta_client.respond(new_input, tools, instructions, previous_response_id=_get(resp, "id"))
    raise PlanError("Muse Spark could not settle on a place and dates; try naming the place and the month")
