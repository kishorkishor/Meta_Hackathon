"""Run every demo place once so its images are cached and the live demo opens instantly.

    python -m scripts.prepare_demos            # all demos
    python -m scripts.prepare_demos evros-fire # one
"""
import sys
import time
from datetime import date

from app import analysis
from app.demos import DEMOS


def main(ids):
    for d in DEMOS:
        if ids and d["id"] not in ids:
            continue
        t = time.time()
        a = analysis.start(tuple(d["bbox"]), date.fromisoformat(d["before"]), date.fromisoformat(d["after"]),
                           d["window_days"], background=False)
        print(f"\n=== {d['id']}: {a.status} in {time.time() - t:.0f} s ({a.grid.width} x {a.grid.height} px) {a.message}")
        if a.status != "done":
            continue
        print(a.report)
        for f in a.regions[:6]:
            p = f["properties"]
            print(f"  #{p['id']} {p['kind']:28s} {p['area_ha']:8.1f} ha  {p['confidence']:7s} {p['main_doubt'] or ''}")
        print(f"  run folder: {a.dir}")


if __name__ == "__main__":
    main(set(sys.argv[1:]))
