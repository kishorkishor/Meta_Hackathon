"""Settings. Everything can be overridden with environment variables or a .env file in the repo root."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("WCH_CACHE", ROOT / "cache"))
FRONTEND = ROOT / "frontend"


def _load_dotenv():
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

# ---- imagery ----
STAC_URL = os.environ.get("WCH_STAC_URL", "https://earth-search.aws.element84.com/v1/search")
COLLECTION = "sentinel-2-l2a"
RES = 20.0                    # analysis grid in metres (the 20 m SWIR bands set the floor)
MAX_AREA_KM2 = 400.0          # keep requests fast enough for a live demo
FINE_AREA_KM2 = 120.0         # above this the grid switches to 40 m pixels
MAX_PARALLEL_RUNS = 2         # analyses downloading at once; others wait in line
MAX_RUNS_IN_MEMORY = 30
MAX_QUEUED_RUNS = 8           # waiting or running at once; more get "busy, try again"
MAX_REFINES_PER_RUN = 3       # SAM refinement calls per analysis
MAX_REPORTS_PER_RUN = 3       # Muse Spark reports per analysis (each is several paid calls)
MAX_QUESTIONS_PER_RUN = 30
MAX_LOOKS_PER_RUN = 3         # "look at every big patch" runs per analysis (checks are cached per patch)
MAX_WITNESS_PER_RUN = 2      # SAM second witness runs per analysis (each is up to 8 SAM calls)
MAX_SCENE_CLOUD = 70          # STAC pre-filter on whole-scene cloud cover
CANDIDATES = 8                # scenes per window whose cloud mask we look at
MAX_SCENES = 3                # clearest scenes used in the per-pixel median
MIN_CLEAR = 0.05              # a scene must be at least this clear over the area to be used
WINDOW_DAYS = 30              # default search half window around each picked date
# SCL classes kept as clear: 2 dark area pixels (burn scars often land here), 4 vegetation,
# 5 not vegetated, 6 water, 7 unclassified. Dropped: 0 nodata, 1 saturated, 3 cloud shadow,
# 8/9 cloud medium/high probability, 10 thin cirrus, 11 snow.
CLEAR_SCL = (2, 4, 5, 6, 7)

# ---- change thresholds ----
BURN_DNBR = 0.27              # moderate-low severity and up (Key and Benson 2006)
BURN_POST_NBR = 0.10          # burned ground has low NBR after the fire
WATER_MNDWI = 0.0             # MNDWI above this is water (Xu 2006); 0 is the usual default
VEG_NDVI_DROP = 0.25          # NDVI loss that counts as vegetation removed
VEG_PRE_NDVI = 0.5            # ...only where there was real vegetation before
BUILT_NDBI_GAIN = 0.10        # NDBI rise for new built-up or bare ground
MIN_REGION_PIXELS = 6         # 6 px at 20 m = 0.24 ha; smaller blobs are noise
SURE_MARGIN = 0.5             # a pixel is "sure" when its index is past the threshold by this share

# ---- Meta Model API (Muse Spark + SAM 3.1), https://dev.meta.ai/docs/overview ----
META_API_KEY = os.environ.get("MODEL_API_KEY", "")
META_BASE_URL = os.environ.get("META_BASE_URL", "https://api.meta.ai/v1")
META_CHAT_MODEL = os.environ.get("META_CHAT_MODEL", "muse-spark-1.3")
META_SAM_MODEL = os.environ.get("META_SAM_MODEL", "sam-3.1")
META_TIMEOUT = float(os.environ.get("META_TIMEOUT", "90"))
AGENT_MAX_STEPS = 8
PLANNER_MAX_STEPS = 10        # turns the plain words planner may take before it must give a plan
PLANS_PER_HOUR = int(os.environ.get("WCH_PLANS_PER_HOUR", "40"))   # for the whole server; each plan costs calls
WEB_SEARCH = os.environ.get("WCH_WEB_SEARCH", "1") != "0"           # Muse Spark search grounding for event dates
GEOCODER_URL = os.environ.get("WCH_GEOCODER_URL", "https://nominatim.openstreetmap.org/search")
USER_AGENT = "WhatChangedHere/0.2 (github.com/kishorkishor/Meta_Hackathon)"

# ---- NASA FIRMS active fires ----
FIRMS_MAP_KEY = os.environ.get("FIRMS_MAP_KEY", "")
