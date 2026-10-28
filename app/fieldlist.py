"""Field checklist: where a team on the ground should go first, what to look for there, and why.

The code picks the candidate places and a first order: larger first, and a place where the checks disagree
counts double, because only a visit settles it. Muse Spark then chooses up to ten stops from that list, may
reorder them, and writes for each what a person standing there should look for and why it is on the list. Its
answer is structured output limited to the candidate ids, so it cannot invent a place or a coordinate. Without
a key, or if the call fails, the same list is written from plain templates.

Each stop gets a point inside its patch (the pixel farthest from the edge, so the point never falls in the gap
of a C shaped patch), its size, the straight line distance from the previous stop and links for phones. The
list goes out as JSON for the page, a printable page with a tick box form per stop, KML and GeoJSON.
"""
from __future__ import annotations

import html
import io
import json
import math
from datetime import datetime, timezone
from xml.sax.saxutils import escape

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

from app import meta_client
from app.analysis import Analysis, stretch
from app.imagery import true_colour

MAX_STOPS = 10
MAX_CANDIDATES = 25      # places Muse Spark chooses from
MIN_STOP_HA = 1.0        # smaller patches are not worth a trip on their own
DISPUTED_WEIGHT = 2.0    # a place where the checks disagree counts double in the first order
MAX_TEXT = 400

LOOK_FOR = {
    "burned land": "Black or grey ground, ash, scorched trunks and shrubs, and where green cover starts again.",
    "new water": "Standing water or wet mud, a water line on walls or trees, flattened crops and debris left "
                 "where the water reached.",
    "water gone": "A dry bed, cracked mud or new plants where there was water, and where the water edge is now.",
    "vegetation loss": "Felled or dead trees, stumps, cleared or ploughed ground, and whether it was cut, burned "
                       "or dried out.",
    "new building or bare ground": "New roofs, roads, quarries or cleared soil, and whether people live or work "
                                   "there now.",
}
SAFETY = ("Check that the way is safe before you go. Do not walk into flood water, burning or smoking ground, "
          "or unstable slopes.")
CONFIDENCE_WORDS = {"sure": "high confidence", "likely": "likely", "unsure": "low confidence"}

PROMPT = (
    "You are planning a field visit for a team that will check satellite change on the ground. {context}"
    "Below are candidate places as JSON, already in a first order by a simple rule: larger first, and places "
    "where the checks disagree count double. Choose up to {n} stops the team should visit first, most important "
    "first. Put first the places where a visit matters most: large floods, burns or clearing that may affect "
    "people, and places where the checks disagree, because only a visit settles them. For each stop write "
    "look_for: what a person standing there should look for, in plain words a field worker understands "
    "(physical signs, never index names), and why: one sentence on why it is on the list, using only the facts "
    "given. Also write intro: two short sentences for the team about this trip. Do not add numbers that are not "
    "in the facts. Do not use dashes as punctuation.\n\nCandidates: {facts}")


def _ha(x: float) -> str:
    return f"{x:,.0f} ha" if x >= 10 else f"{x:.1f} ha"


def _text(v) -> str:
    s = " ".join(str(v or "").split()).replace(" — ", ", ").replace("—", ", ").replace("–", " to ")
    return s[:MAX_TEXT]


def _disputes(a: Analysis, p: dict) -> list[str]:
    """Why the checks doubt this patch, in plain words; empty when nothing disagrees."""
    out = []
    c = a.checks.get(p["id"])
    if c and c["verdict"] == "disagrees":
        out.append("Muse Spark disagrees" + (f", it looks like {c['looks_like']}" if c.get("looks_like") else ""))
    elif c and c["verdict"] == "unclear":
        out.append("Muse Spark could not tell from the pictures")
    w = p.get("sam_witness")
    if w and w["verdict"] == "does not see it":
        out.append("SAM 3.1 does not see it")
    return out


def candidates(a: Analysis) -> list[dict]:
    """Places worth a visit, in the first order (score = area, doubled where the checks disagree)."""
    out = []
    for f in a.regions:
        p = f["properties"]
        if p["area_ha"] < MIN_STOP_HA:
            continue
        d = _disputes(a, p)
        c = a.checks.get(p["id"]) or {}
        out.append({"ref": f"P{p['id']}", "source": "patch", "region_id": p["id"], "kind": p["kind"],
                    "area_ha": p["area_ha"], "confidence": p["confidence"], "main_doubt": p["main_doubt"],
                    "visual_check": c.get("verdict"), "muse_spark_saw": c.get("what_you_see") or None,
                    "sam_witness": (p.get("sam_witness") or {}).get("verdict"), "disputes": d,
                    "center": p["center"], "score": p["area_ha"] * (DISPUTED_WEIGHT if d else 1.0)})
    for i, m in enumerate((a.witness or {}).get("only_sam_places") or [], 1):
        if m["area_ha"] < MIN_STOP_HA:
            continue
        out.append({"ref": f"S{i}", "source": "only SAM", "region_id": None, "kind": m["kind"],
                    "area_ha": m["area_ha"], "confidence": None, "main_doubt": None, "visual_check": None,
                    "muse_spark_saw": None, "sam_witness": None,
                    "disputes": ["only SAM 3.1 saw this, the rules did not"], "center": m["center"],
                    "score": m["area_ha"] * DISPUTED_WEIGHT})
    out.sort(key=lambda c: -c["score"])
    return out[:MAX_CANDIDATES]


def _why(c: dict) -> str:
    if c["source"] == "only SAM":
        return f"Only SAM 3.1 saw {c['kind']} here ({_ha(c['area_ha'])}) and the rules did not; a look settles it."
    if c["disputes"]:
        return "The checks disagree: " + "; ".join(c["disputes"]) + ". A visit settles which is right."
    s = f"{c['kind'].capitalize()}, {_ha(c['area_ha'])}, {CONFIDENCE_WORDS[c['confidence']]} from the satellite"
    if c["visual_check"] == "agrees":
        s += ", and Muse Spark agrees from the pictures"
    if c["sam_witness"] == "agrees":
        s += ", and SAM 3.1 sees it too"
    return s + ". A visit tells how bad it is on the ground."


def _ask_muse_spark(a: Analysis, cands: list[dict], n: int) -> dict:
    keys = ("ref", "kind", "area_ha", "confidence", "main_doubt", "visual_check", "muse_spark_saw",
            "sam_witness", "disputes", "source")
    facts = [{k: c[k] for k in keys if c[k] not in (None, [])} for c in cands]
    context = f"The images compare {a.before} with {a.after}. "
    if a.question:
        context += f"The person asked: {a.question!r}. "
    if a.plan.get("why"):
        context += f"Why these dates: {a.plan['why']} "
    schema = {
        "type": "object",
        "properties": {
            "intro": {"type": "string"},
            "stops": {"type": "array", "maxItems": n, "items": {
                "type": "object",
                "properties": {"ref": {"type": "string", "enum": [c["ref"] for c in cands]},
                               "look_for": {"type": "string"}, "why": {"type": "string"}},
                "required": ["ref", "look_for", "why"]}},
        },
        "required": ["intro", "stops"],
    }
    q = PROMPT.format(context=context, n=n, facts=json.dumps(facts, ensure_ascii=False))
    msg = meta_client.chat([{"role": "user", "content": q}], schema=schema)
    return json.loads(msg.content or "{}")


def _pick(cands: list[dict], ans: dict) -> list[dict]:
    """Muse Spark's choice, kept only where it names a real candidate, once; empty text gets the template."""
    by_ref = {c["ref"]: c for c in cands}
    out, seen = [], set()
    for s in ans.get("stops") or []:
        if not isinstance(s, dict):
            continue
        ref = str(s.get("ref", "")).strip().upper()
        if ref not in by_ref or ref in seen:
            continue
        seen.add(ref)
        c = by_ref[ref]
        out.append({**c, "look_for": _text(s.get("look_for")) or LOOK_FOR[c["kind"]],
                    "why": _text(s.get("why")) or _why(c)})
        if len(out) == MAX_STOPS:
            break
    return out


def inside_point(a: Analysis, rid: int) -> list[float]:
    """The pixel of the patch farthest from its edge, as [lon, lat]: always inside the patch."""
    p = next(f["properties"] for f in a.regions if f["properties"]["id"] == rid)
    if a.region_ids is None or a.grid is None:
        return p["center"]
    x0, y0, x1, y1 = p["pixel_box"]
    m = a.region_ids[y0:y1, x0:x1] == rid
    if not m.any():
        return p["center"]
    d = ndimage.distance_transform_edt(np.pad(m, 1))[1:-1, 1:-1]
    y, x = np.unravel_index(int(np.argmax(d)), d.shape)
    lon, lat = a.grid.transform @ (x0 + x + 0.5, y0 + y + 0.5)
    return [round(float(lon), 5), round(float(lat), 5)]


def _km(p: list[float], q: list[float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (*p, *q))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def _finish(a: Analysis, picked: list[dict]) -> list[dict]:
    res = a.grid.pixel_area_ha() ** 0.5 * 100 if a.grid is not None else 20.0
    by_id = {f["properties"]["id"]: f["properties"] for f in a.regions}
    stops, prev = [], None
    for rank, c in enumerate(picked, 1):
        point = inside_point(a, c["region_id"]) if c["region_id"] else c["center"]
        lon, lat = point
        size = None
        if c["region_id"]:
            x0, y0, x1, y1 = by_id[c["region_id"]]["pixel_box"]
            size = [round((x1 - x0) * res / 10) * 10, round((y1 - y0) * res / 10) * 10]
        stops.append({
            "rank": rank, "ref": c["ref"], "source": c["source"], "region_id": c["region_id"], "kind": c["kind"],
            "area_ha": c["area_ha"], "confidence": c["confidence"], "point": point, "size_m": size,
            "km_from_previous": round(_km(prev, point), 1) if prev else None,
            "look_for": c["look_for"], "why": c["why"], "disputes": c["disputes"],
            "visual_check": c["visual_check"], "sam_witness": c["sam_witness"],
            "links": {"google_maps": f"https://www.google.com/maps/search/?api=1&query={lat},{lon}",
                      "openstreetmap": f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=16/{lat}/{lon}",
                      "geo": f"geo:{lat},{lon}"},
        })
        prev = point
    return stops


def make_checklist(a: Analysis, use_model: bool = True) -> dict:
    """Build the checklist; Muse Spark writes it when use_model is set, else (or if it fails) the templates do."""
    cands = candidates(a)
    n = min(MAX_STOPS, len(cands))
    source, intro, note, picked = "template", "", "", []
    if use_model and cands:
        try:
            ans = _ask_muse_spark(a, cands, n)
            picked = _pick(cands, ans)
            if picked:
                source, intro = "muse-spark", _text(ans.get("intro"))
            else:
                note = "Muse Spark did not name any of the candidate places, so this is the plain version."
        except Exception as e:   # the list is still useful without the model's words
            note = f"Muse Spark could not be reached ({type(e).__name__}), so this is the plain version."
    if not picked:
        picked = [{**c, "look_for": LOOK_FOR[c["kind"]], "why": _why(c)} for c in cands[:n]]
    if not intro and picked:
        intro = (f"{len(picked)} stop{'s' if len(picked) != 1 else ''}, most important first. Distances are straight lines from the previous "
                 "stop; the real way will be longer.")
    a.checklist = {"source": source, "intro": intro, "note": note, "safety": SAFETY, "stops": _finish(a, picked),
               "candidates": len(cands), "made": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
               "based_on": a.evidence_mark()}
    return a.checklist


def current(a: Analysis) -> dict:
    """The checklist to export: a plain one is made (free) when there is none, or redone when it is out of date."""
    if not a.checklist or (a.checklist["source"] == "template" and a.checklist["based_on"] != a.evidence_mark()):
        make_checklist(a, use_model=False)
    return a.checklist


# ---------------------------------------------------------------------------------------------------
# exports
# ---------------------------------------------------------------------------------------------------

def title(s: dict) -> str:
    return f"{s['rank']}. {s['kind'].capitalize()}, {_ha(s['area_ha'])}"


def _checks_line(s: dict) -> str:
    parts = []
    if s["confidence"]:
        parts.append(f"satellite rules: {CONFIDENCE_WORDS[s['confidence']]}")
    else:
        parts.append("satellite rules: not marked")
    if s["visual_check"]:
        parts.append(f"Muse Spark: {s['visual_check']}")
    if s["sam_witness"]:
        parts.append(f"SAM 3.1: {s['sam_witness']}")
    elif s["source"] == "only SAM":
        parts.append("SAM 3.1: saw it")
    return "; ".join(parts)


def _outline(a: Analysis, rid: int) -> dict | None:
    return next((f["geometry"] for f in a.regions if f["properties"]["id"] == rid), None)


def geojson(a: Analysis) -> dict:
    fl = current(a)
    feats = []
    for s in fl["stops"]:
        props = {"rank": s["rank"], "name": title(s), "kind": s["kind"], "area_ha": s["area_ha"],
                 "look_for": s["look_for"], "why": s["why"], "checks": _checks_line(s),
                 "km_from_previous": s["km_from_previous"], "google_maps": s["links"]["google_maps"]}
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": s["point"]},
                      "properties": {"role": "stop", **props}})
    for s in fl["stops"]:
        g = _outline(a, s["region_id"]) if s["region_id"] else None
        if g:
            feats.append({"type": "Feature", "geometry": g,
                          "properties": {"role": "outline", "rank": s["rank"], "name": title(s)}})
    return {"type": "FeatureCollection", "name": "What Changed Here field checklist",
            "dates": {"before": str(a.before), "after": str(a.after)}, "features": feats}


def _ring(coords) -> str:
    return " ".join(f"{x:.6f},{y:.6f}" for x, y in coords)


def _kml_polygons(g: dict) -> str:
    polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
    out = []
    for rings in polys:
        inner = "".join(f"<innerBoundaryIs><LinearRing><coordinates>{_ring(r)}</coordinates></LinearRing>"
                        "</innerBoundaryIs>" for r in rings[1:])
        out.append(f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{_ring(rings[0])}</coordinates>"
                   f"</LinearRing></outerBoundaryIs>{inner}</Polygon>")
    return out[0] if len(out) == 1 else "<MultiGeometry>" + "".join(out) + "</MultiGeometry>"


def kml(a: Analysis) -> str:
    fl = current(a)
    marks, outlines = [], []
    for s in fl["stops"]:
        desc = (f"Look for: {s['look_for']}\nWhy: {s['why']}\nChecks: {_checks_line(s)}\n"
                f"Point: {s['point'][1]}, {s['point'][0]} (latitude, longitude)")
        marks.append(f"<Placemark><name>{escape(title(s))}</name><description>{escape(desc)}</description>"
                     f"<styleUrl>#stop</styleUrl><Point><coordinates>{s['point'][0]},{s['point'][1]}</coordinates>"
                     "</Point></Placemark>")
        g = _outline(a, s["region_id"]) if s["region_id"] else None
        if g:
            outlines.append(f"<Placemark><name>{escape(title(s))}</name><styleUrl>#outline</styleUrl>"
                            f"{_kml_polygons(g)}</Placemark>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            f"<name>Field checklist {a.before} to {a.after}</name>"
            f"<description>{escape(fl['intro'] + ' ' + SAFETY)}</description>"
            '<Style id="stop"><IconStyle><color>ff2e8cf0</color><scale>1.2</scale></IconStyle></Style>'
            '<Style id="outline"><LineStyle><color>ff00ffff</color><width>2</width></LineStyle>'
            "<PolyStyle><fill>0</fill></PolyStyle></Style>"
            "<Folder><name>Stops, most important first</name>" + "".join(marks) + "</Folder>"
            "<Folder><name>Patch outlines</name>" + "".join(outlines) + "</Folder></Document></kml>\n")


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:   # Pillow before 10.1 has one fixed size
        return ImageFont.load_default()


def overview_png(a: Analysis, long_side: int = 1000) -> bytes:
    """The after image with the stops' outlines in yellow and a numbered circle at each stop, for printing."""
    fl = current(a)
    img = stretch(true_colour(a.comp_b)[..., :3])
    if a.det is not None:
        img[~a.det.seen] = (200, 200, 200)   # no clear look in both periods: light grey, as on the page
    edges = np.zeros(img.shape[:2], bool)
    for s in fl["stops"]:
        if s["region_id"]:
            m = a.region_ids == s["region_id"]
            edges |= m & ~ndimage.binary_erosion(m)
    img[edges] = (255, 230, 0)
    k = max(1, math.ceil(long_side / max(img.shape[:2])))
    pic = Image.fromarray(img).resize((img.shape[1] * k, img.shape[0] * k), Image.NEAREST)
    r = max(12, max(pic.size) // 50)   # numbers stay readable when the page shrinks the picture to print
    draw, font = ImageDraw.Draw(pic), _font(int(r * 1.2))
    inv = ~a.grid.transform
    for s in fl["stops"]:
        col, row = inv @ tuple(s["point"])
        x, y = col * k, row * k
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255), outline=(0, 0, 0), width=max(2, r // 8))
        draw.text((x, y), str(s["rank"]), fill=(0, 0, 0), font=font, anchor="mm")
    buf = io.BytesIO()
    pic.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


PAGE_CSS = """
body{font:14px/1.45 system-ui,Segoe UI,Roboto,sans-serif;color:#111;background:#fff;margin:24px auto;max-width:860px;padding:0 16px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:0 0 6px}
.muted{color:#555}.small{font-size:12.5px}
.bar{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0}
.bar a,.bar button{font:inherit;padding:6px 12px;border:1px solid #333;border-radius:6px;background:#fff;color:#111;
  text-decoration:none;cursor:pointer}
.safety{border:2px solid #b42318;border-radius:6px;padding:8px 12px;margin:12px 0}
.overview{width:100%;border:1px solid #999;image-rendering:pixelated}
.stop{border-top:2px solid #111;padding:12px 0;break-inside:avoid;page-break-inside:avoid}
.where{font-family:ui-monospace,Consolas,monospace;font-size:15px}
.crop{max-width:100%;max-height:220px;image-rendering:pixelated;border:1px solid #999;margin:6px 0}
.form{margin-top:8px}.form span{margin-right:18px}.line{border-bottom:1px solid #999;height:22px}
@media print{.bar{display:none}body{margin:0}a{color:#111}}
"""


def page(a: Analysis) -> str:
    """A printable page: overview map, then one block per stop with a small form to fill in on the ground."""
    fl = current(a)
    e = html.escape
    rid = a.id
    blocks = []
    for s in fl["stops"]:
        lon, lat = s["point"]
        size = f"About {s['size_m'][0]} m by {s['size_m'][1]} m. " if s["size_m"] else ""
        dist = f"{s['km_from_previous']} km from stop {s['rank'] - 1} in a straight line." \
            if s["km_from_previous"] is not None else ""
        crop = (f'<img class="crop" src="/api/analyze/{e(rid)}/crop/{s["region_id"]}" alt="Before and after">'
                if s["region_id"] else '<p class="muted small">No picture: only SAM 3.1 marked this place.</p>')
        blocks.append(
            f'<section class="stop"><h2>{e(title(s))}</h2>'
            f'<div class="where">{lat:.5f}, {lon:.5f}</div>'
            f'<div class="small muted">latitude, longitude. {e(size)}{e(dist)} '
            f'<a href="{e(s["links"]["google_maps"])}">Google Maps</a> · '
            f'<a href="{e(s["links"]["openstreetmap"])}">OpenStreetMap</a></div>'
            f'<p><b>Look for:</b> {e(s["look_for"])}</p><p><b>Why:</b> {e(s["why"])}</p>'
            f'<p class="small muted">Checks: {e(_checks_line(s))}</p>{crop}'
            '<div class="form"><b>What you found:</b> <span>☐ as on the map</span><span>☐ something else</span>'
            '<span>☐ could not reach</span></div><div class="line"></div><div class="line"></div></section>')
    asked = f"<p><b>Question:</b> {e(a.question)}</p>" if a.question else ""
    place = f" near {e(a.plan['place'])}" if a.plan.get("place") else ""
    written = "Muse Spark" if fl["source"] == "muse-spark" else "plain templates (no Muse Spark)"
    w, s_, east, n = a.bbox
    empty = "" if fl["stops"] else "<p>No changed patch of 1 ha or more was found, so there is nothing to visit.</p>"
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Field checklist</title><style>{PAGE_CSS}</style></head><body>"
        f"<h1>Field checklist{place}</h1>"
        f'<p class="muted">Satellite images of {a.before} and {a.after}. Area {s_:.4f}, {w:.4f} to {n:.4f}, '
        f"{east:.4f}. Made {e(fl['made'])}, written by {written}.</p>{asked}"
        f"<p>{e(fl['intro'])}</p>"
        + (f'<p class="small muted">{e(fl["note"])}</p>' if fl["note"] else "")
        + '<div class="bar"><button onclick="print()">Print</button>'
        f'<a href="/api/analyze/{e(rid)}/field.kml" download>KML for phones</a>'
        f'<a href="/api/analyze/{e(rid)}/field.geojson" download>GeoJSON</a></div>'
        f'<div class="safety"><b>Safety.</b> {e(SAFETY)}</div>'
        + (f'<img class="overview" src="/api/analyze/{e(rid)}/field/map.png" alt="Map of the stops">'
           '<p class="small muted">After image. Numbers are the stops, yellow lines their outlines, grey had no '
           "clear image in both periods.</p>" if fl["stops"] else "")
        + empty + "".join(blocks)
        + '<p class="small muted">From Sentinel-2 at about 20 m per pixel: a single house is smaller than one pixel, '
          "and an edge can be off by a pixel. Order is by importance, not by road. Made with What Changed Here.</p>"
          "</body></html>")
