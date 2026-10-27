# What Changed Here

Pick any place on Earth and two dates. Get back a map of what changed and a short report you can read,
from a system that tells you where it is not sure.

Built for the Meta Global AI Developer Hackathon (Oct 26 to Nov 4, 2026) with **Muse Spark** and **SAM 3.1**.

## Before and during the hackathon

The hackathon asks every team to make clear what was built during the event (building period Oct 27 to
Nov 4, 2026). The line in this repository is the git tag **`pre-hackathon`**:

* **Before the event (tag `pre-hackathon`, committed before the Oct 26 kickoff):** a working prototype of the
  Sentinel-2 pipeline, the spectral change rules, the map page and first, untested hooks for Muse Spark and
  SAM 3.1 (no API key existed then, so no Meta model had ever been called).
* **During the event:** every commit after that tag. [HACKATHON_LOG.md](HACKATHON_LOG.md) lists each feature
  built during the event, and the demo video shows only those.

`git diff pre-hackathon..main` shows exactly what was added during the hackathon.

## Why

After a flood, a fire or a season of hill burning, the people who decide where to send help usually find out
from phone calls days later. Free satellite images already show what changed, but reading them takes an
expert, and most change maps colour every pixel with the same confidence even where clouds, shadows or a
slightly shifted image make the answer a guess. A wrong "this village looks fine" is worse than no answer.

## What it does

0. **Ask in plain words** (built during the hackathon). Type a question such as "What did the August 2022
   floods do to the land around Dadu in Sindh?". Muse Spark searches the web for when the event happened
   (search grounding, with the sources shown), looks the place up in OpenStreetMap, checks that Sentinel-2 has
   clear images near both dates, and submits a plan: the area, the two dates and why. The plan is checked in
   code against the same limits as the map form; a plan that breaks one goes back to Muse Spark with the
   reason so it can fix it. The comparison then runs, and the report answers the question first.
1. Finds clear Sentinel-2 images around both dates (Earth Search, no account) and builds a cloud free median.
2. Marks burned land, new water, water gone, vegetation loss, and new buildings or bare ground with
   simple, published spectral rules ([docs/METHOD.md](docs/METHOD.md)).
3. Gives every changed patch a confidence and the reason for any doubt: near threshold, possible image shift,
   next to cloud, dark or shadow, only one clear look. Areas not seen in both periods are shown, not hidden.
4. **SAM 3.1** sharpens the outline of each large patch, but only inside the area the detector saw change.
5. **Muse Spark** acts as the analyst. With tools it reads the facts, looks at before and after crops of the
   largest patches and says whether each label looks right, asks SAM for outlines, checks NASA FIRMS fires,
   and then writes a plain report: what changed, how sure, what could not be checked, where to look first.
   You can then ask it follow up questions about the area.

## Run it

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # on macOS or Linux: .venv/bin/pip
copy env.example .env                               # then add your MODEL_API_KEY
.venv/Scripts/python run.py                         # open http://127.0.0.1:8770
```

Without a key everything except the Muse Spark report and SAM outlines still works, with an automatic summary.
`python -m scripts.prepare_demos` caches the demo places so they open instantly.

Tests (offline, synthetic scenes and recorded API shapes): `.venv/Scripts/python -m pytest`

## Layout

| Path | What |
|---|---|
| `app/imagery.py` | scene search, cloud masks, overview reads, median composite on a lat/lon grid |
| `app/indices.py`, `app/detect.py` | spectral indices, change rules, confidence and doubt flags |
| `app/regions.py` | patches, areas, polygons |
| `app/meta_client.py` | Muse Spark chat and vision, SAM 3.1 over the Responses API |
| `app/refine.py`, `app/agent.py` | SAM outlines, visual checks, the tool calling analyst |
| `app/planner.py` | question in plain words to place, dates and reasons (web search, OpenStreetMap, image check) |
| `app/api.py`, `frontend/` | HTTP API and the map page |
| `docs/METHOD.md` | every rule and threshold, and what the app cannot do |

## Credits and data

* Sentinel-2 L2A: contains modified Copernicus Sentinel data, served by Element 84 Earth Search.
* Active fires: NASA FIRMS (VIIRS 375 m).
* Place search: OpenStreetMap Nominatim (at most one request a second, results cached).
* The scene search, cloud mask choice and reflectance scaling are adapted from
  [Burn Before the Rain](https://github.com/kishorkishor/NASA_PROJECT) (MIT, Burn Before the Rain team),
  which also supplied the Bandarban hill burning demo.
* Basemaps: Esri World Imagery, OpenStreetMap contributors.

MIT licence.
