# Build plan

Hackathon: Meta Global AI Developer Hackathon, Indie track, theme "Push the Frontier".
Build window: Oct 26 to Nov 4, 2026. Submission closes Nov 4, 9 PM ET (Nov 5, 8 AM in Dhaka).

## Status

* Working app: image fetcher, change rules with seasonal correction and doubt flags, regions, map page,
  automatic report, four demo places (Bangladesh hill burns, Greece fire, Pakistan flood, Brazil clearing).
* Muse Spark analyst and SAM 3.1 outlines are wired to the documented Model API and tested against recorded
  response shapes; they still need a live run with an API key.
* The change model trained on SECOND aerial imagery was tested on 60 Sentinel-2 hill cells and did not
  separate burned from unburned ground well (AUROC 0.62), so it is not used. Spectral rules lead.
* Next: live Meta calls, prompt tuning for the visual check, demo video.

## What we are building

Pick any place on Earth and two dates. The app finds clear Sentinel 2 images for both dates, works out
what changed (burned land, flood water, new buildings, cleared forest, moved river banks), outlines each
change with SAM 3.1, and has Muse Spark write a short report that anyone can read. Areas the system is
not sure about are marked "check by hand" instead of guessed. You can then ask follow up questions about
the area.

## What we already have

| Piece | Where it lives now | How we reuse it |
|---|---|---|
| Sentinel 2 search on Earth Search, cloud mask from the SCL layer, fast reads from the first overview | `Desktop/NASA_PROJECT/backend/pipeline/s2check.py` | Base of the image fetcher. Already handles windows, clear pixel share and picking the clearest scenes. |
| Burn scar test (dNBR before vs after) | same file | First change detector for burned land. It is physics, not a guess, so it is a good check on the model. |
| Live fires from NASA FIRMS (last 7 days, no key) | `Desktop/NASA_PROJECT/backend/pipeline/live.py` | "What is burning right now" layer. The public file covers South Asia; other regions have their own files, or use the FIRMS area API with a free key. |
| Harmonized MODIS + VIIRS fire record for Bangladesh, hill district inspection list | `Desktop/NASA_PROJECT/backend/pipeline/harmonize.py`, `history.py`, `watchlist.py` | Background facts for the Bangladesh demo ("this hill has burned in 14 of 24 seasons"). Not needed for other countries. |
| Draw an area on a map, find before and after pairs, export tiles | `Desktop/CVPR/app/` (FastAPI, rasterio, Leaflet) | Map UI and area picker. |
| Change model (CLIP text prompt + segmentation) and its reliability layer | code: `D:/secondcc/results/run1/model4_artifacts/model4_full_run_source.py`; weights: `best_model3_secondcc.pth` (92 MB) in the same folder | Second change detector plus the "I'm not sure" score. Weights stay out of git. |
| Explainer page look (dark and light mode, relief shading) | `Desktop/NASA_SHOWCASE/build/` | Style for the demo page. |

## What is new (the Meta part)

1. **SAM 3.1 outlines.** Turn noisy change pixels into clean shapes: each burn scar, each flooded field,
   each new building block. Also lets the user click a spot and get that object outlined.
2. **Muse Spark as the analyst.** It does not just write text at the end. It gets tools (search scenes,
   run the burn test, run the water test, run the change model, ask SAM for outlines, look up FIRMS fires)
   and decides which to run for the place and question. Then it writes the report and answers questions.
   This is the "beyond a basic integration" part judges score.
3. **Honest uncertainty in the report.** Muse Spark is told which areas were flagged as unsure and must
   say so, for example "2 areas could not be checked because of cloud".

## Biggest risk: the change model may not work on Sentinel 2 (tested: it does not)

The model learned on SECOND, which is sharp aerial imagery. Sentinel 2 pixels are 10 m, so it may do
badly. Plan:

* Day 1: run the model on a few Sentinel 2 pairs where we know the answer (the NASA project's burn
  checks are perfect for this) and look at the results.
* If it is weak, the main detectors become spectral indices that work at 10 m anywhere:
  dNBR for burns, NDWI or MNDWI for water, NDBI plus NDVI drop for building and clearing.
  The model then only runs where it helps, and its reliability score still decides what gets flagged.
* Either way, say what we found in the README. Judges respect honest limits more than big claims.

## Demo plan (three places, three continents, then live)

1. **Chittagong hill tracts, Bangladesh: burning before the monsoon.** Straight from the NASA project.
   We already know which hills burned in 2026 and have before and after images for them.
2. **A monsoon flood in South Asia.** Water appears, fields and roads go under.
3. **Fast city growth or forest clearing** somewhere else, to show it is not only fire.
4. Then a judge picks any place and two dates live.

Record a backup video of every demo step in case the live run is slow.

## Ten day schedule

| Day | Date | Goal |
|---|---|---|
| 1 | Oct 26 | Read the official rules and the Meta Model API docs. Claim credits. Test Muse Spark and SAM 3.1 calls. Run the model on known Sentinel 2 pairs. |
| 2 | Oct 27 | Image fetcher for any area and dates (from s2check.py). Cloud masking. Cache. |
| 3 | Oct 28 | Change detectors: burn, water, built up, vegetation loss. Uncertainty mask. |
| 4 | Oct 29 | SAM 3.1 outlines on the change areas. |
| 5 | Oct 30 | Muse Spark with tools. First full report end to end. |
| 6 | Oct 31 | Map front end: pick area, pick dates, see layers, read report, ask questions. |
| 7 | Nov 1 | Prepare the three demo places. Fix what breaks. |
| 8 | Nov 2 | FIRMS live layer. Speed (caching, smaller reads). |
| 9 | Nov 3 | README, screenshots, demo video. |
| 10 | Nov 4 | Final checks, submit well before 9 PM ET. |

## Things to check on Oct 26 before writing code

* **Can we use code written before the hackathon?** If the rules say only new work counts, we only
  bring in old code as an open source dependency with its license, and write the new parts fresh.
* **NASA project ownership.** It is MIT licensed under "Burn Before the Rain team". MIT allows reuse if
  the copyright notice is kept. Still tell the teammates, since the hackathon licence gives Meta rights
  to whatever is submitted.
* **Keep out of the repo:** API keys (`.env`), model weights, unpublished paper results (TraceID, flood
  leakage, garment ideas).

## How judges score it

Impact 30, use of Meta AI tools 25, demo 25 (live run plus video), technical depth 20.
