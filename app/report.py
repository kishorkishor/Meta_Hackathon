"""Facts for the analyst, and a plain report written from them when Muse Spark is not available."""
from __future__ import annotations


def scene_dates(scenes) -> str:
    ds = sorted({s["date"] for s in scenes or []})
    if not ds:
        return "no clear images"
    return ds[0] if len(ds) == 1 else f"{ds[0]} to {ds[-1]}"


def facts(a, top: int = 15) -> dict:
    """Compact, exact numbers. Everything the analyst says must come from here or from its tools."""
    regs = []
    for f in a.regions[:top]:
        p = f["properties"]
        regs.append({k: p[k] for k in ("id", "kind", "area_ha", "confidence", "main_doubt", "center")})
        if p["id"] in a.checks:
            regs[-1]["visual_check"] = a.checks[p["id"]].get("verdict")
        if p.get("sam_witness"):
            regs[-1]["sam_witness"] = p["sam_witness"]["verdict"]
    return {
        "area_bbox_west_south_east_north": list(a.bbox),
        "asked_dates": {"before": str(a.before), "after": str(a.after), "search_window_days": a.window_days},
        "images_used": {"before": scene_dates(a.comp_a.scenes if a.comp_a else None),
                        "after": scene_dates(a.comp_b.scenes if a.comp_b else None)},
        "clear_in_both_share": a.totals.get("seen_share"),
        "seasonal_shift": a.totals.get("seasonal_offsets"),
        "area_ha": a.totals.get("area_ha"),
        "change_by_kind": a.totals.get("by_kind", {}),
        "regions_total": len(a.regions),
        "largest_regions": regs,
        "sam_refinement": {k: v for k, v in a.refined.items() if k != "masks"} or None,
        "sam_second_witness": getattr(a, "witness", None) or None,
        "notes": a.message or None,
    }


def _ha(x: float) -> str:
    return f"{x:,.0f} ha" if x >= 10 else f"{x:.1f} ha"


def fallback_report(a) -> str:
    f = facts(a)
    lines = [f"Images compared: {f['images_used']['before']} (before) and {f['images_used']['after']} (after)."]
    seen = f["clear_in_both_share"] or 0
    if seen >= 0.995:
        lines.append(f"The whole {_ha(f['area_ha'])} area was clear in both periods.")
    else:
        lines.append(f"{seen:.0%} of the {_ha(f['area_ha'])} area was clear in both periods; "
                     "the rest could not be checked.")
    drop = (f.get("seasonal_shift") or {}).get("ndvi_drop", 0)
    if abs(drop) >= 0.05:
        lines.append(f"The whole area {'dried or browned' if drop > 0 else 'greened up'} between the dates "
                     f"(vegetation index {'down' if drop > 0 else 'up'} {abs(drop):.2f} on average). "
                     "That seasonal shift was taken off before looking for change.")
    kinds = f["change_by_kind"]
    if not kinds:
        lines.append("No change above the detection rules was found in the clear part.")
    for name, k in sorted(kinds.items(), key=lambda kv: -kv[1]["area_ha"]):
        n = sum(1 for r in a.regions if r["properties"]["kind"] == name)
        part = f"{name.capitalize()}: {_ha(k['area_ha'])} in {n} patch{'es' if n != 1 else ''}"
        if k["sure_ha"]:
            part += f", {_ha(k['sure_ha'])} of it high confidence"
        if k["unsure_ha"]:
            part += f", {_ha(k['unsure_ha'])} needs checking by hand"
        lines.append(part + ".")
    big = f["largest_regions"][:3]
    if big:
        lines.append("Largest patches: " + "; ".join(
            f"#{r['id']} {r['kind']} {_ha(r['area_ha'])} at {r['center'][1]:.4f}, {r['center'][0]:.4f}"
            f" ({r['confidence']}{', ' + r['main_doubt'] if r['main_doubt'] else ''})" for r in big) + ".")
    if f["notes"]:
        lines.append(f["notes"])
    return "\n".join(lines)
