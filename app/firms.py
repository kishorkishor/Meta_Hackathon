"""NASA FIRMS active fires (VIIRS 375 m) inside an area for the last few days. Needs a free FIRMS MAP_KEY.

We acknowledge the use of data from NASA's Fire Information for Resource Management System (FIRMS),
part of NASA's Earth Science Data and Information System (ESDIS).
"""
from __future__ import annotations

import csv
import io

import requests

from app import config as C

API = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{source}/{w},{s},{e},{n}/{days}"


def active_fires(bbox, days: int = 7, source: str = "VIIRS_SNPP_NRT") -> dict:
    if not C.FIRMS_MAP_KEY:
        return {"available": False, "note": "set FIRMS_MAP_KEY to see active fires"}
    days = max(1, min(int(days), 10))
    w, s, e, n = bbox
    r = requests.get(API.format(key=C.FIRMS_MAP_KEY, source=source, w=w, s=s, e=e, n=n, days=days), timeout=60)
    r.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(r.text)))
    fires = [{"lat": float(x["latitude"]), "lon": float(x["longitude"]), "date": x["acq_date"],
              "time": x["acq_time"], "confidence": x.get("confidence"), "frp": float(x.get("frp") or 0)}
             for x in rows if x.get("latitude")]
    return {"available": True, "days": days, "source": source, "count": len(fires), "fires": fires[:500]}
