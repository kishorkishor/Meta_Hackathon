# How it works

## 1. Images

For each of the two dates the app searches Sentinel-2 L2A scenes on Earth Search (Element 84, no account)
inside a window around the date. It reads the cloud mask (SCL) of the least cloudy candidates first, keeps
the scenes that add clear pixels, and takes the per pixel median of up to three clear looks. Every scene is
warped onto one shared latitude and longitude grid, so an area can cross tile edges and UTM zones and the
result lines up with the web map.

Clear means SCL class 2 (dark area), 4 (vegetation), 5 (not vegetated), 6 (water) or 7 (unclassified).
Class 2 is kept on purpose: fresh burn scars are often classified as dark area. Cloud, cloud shadow,
cirrus, snow, saturated and missing pixels are dropped. Dark pixels that survive are flagged later.

Grid: 20 m pixels for areas up to 120 km², 40 m above that (limit 400 km²). The app reads the image
overviews that already match the grid, which moves 4 to 16 times fewer bytes than full resolution.

## 2. Seasonal shift

Between two dates a whole landscape can dry out or green up. Before any rule runs, the typical change of each
index is measured on ground that changed only a little (index moved by at most 0.25) and taken off. This is
the dNBR offset used in burn mapping, applied to every index. A real burn or flood covering much of the area
is left out of the measurement, so it cannot explain itself away. With less than 20 percent stable ground no
shift is taken off, and the shift is never more than 0.15. The report states the shift when it is 0.05 or more.

## 3. Change rules

| Change | Rule |
|---|---|
| New water | MNDWI after > 0 (margin 0.05), before < 0 (0.05), rise ≥ 0.2 (0.1) |
| Water gone | the reverse |
| Burned land | dNBR ≥ 0.27 (0.1), NDVI before ≥ 0.3 (0.05), visible mean after ≤ 0.12 (0.02) |
| New building or bare ground | NDBI after > 0 (0.03), rise ≥ 0.1 (0.05), NDVI drop ≥ 0.15 (0.05) |
| Vegetation loss | NDVI before ≥ 0.5 (0.05), drop ≥ 0.2 (0.1), NDVI after ≤ 0.5 (0.05) |

The first rule that holds wins, in that order. Changes (dNBR, drops and rises) are measured after the seasonal
shift is taken off. The number in brackets is each condition's margin: a pixel is "sure" only if every
condition holds by at least its margin.

NBR = (B8A - B12) / (B8A + B12). MNDWI = (B03 - B11) / (B03 + B11). NDVI = (B8A - B04) / (B8A + B04).
NDBI = (B11 - B8A) / (B11 + B8A). B8A stands in for B08 so every index works at 20 m. The burn threshold is
the moderate-low severity cut of Key and Benson (2006). The brightness condition exists because clearing to
soil and building on fields also drop NBR; char and ash are dark, soil and roofs are bright. On the Bandarban
hills, pixels with dNBR ≥ 0.27 had a median visible brightness of 0.10 a few weeks after burning, which set
the 0.12 limit. "NDVI after ≤ 0.5" keeps dry season browning, which leaves green cover, out of vegetation loss.
These are starting points, not universal values.

## 4. How sure

Each changed pixel is "sure" only if it passes its rule by a margin and nothing casts doubt on it:

| Doubt | Test |
|---|---|
| near threshold | some condition holds by less than its margin |
| could be image shift | moving the before image by one pixel in some direction changes the pixel's class |
| near cloud | within 2 pixels of a pixel with no clear look |
| dark or shadow | visible mean below 0.02 in either image |
| only one clear look | one scene behind the median in either window |

One doubt gives "likely". Image shift, darkness, or two doubts together give "unsure", drawn hatched on the map.
Pixels with no clear look in both periods are grey and never counted as change or as no change.

## 5. Regions

Changed pixels of one type are grouped into patches (8-connected, at least 6 pixels, about 0.24 ha at 20 m).
Each patch gets an area, a confidence (sure if most of its pixels are sure, unsure if most are unsure,
otherwise likely), its main doubt (named only when a quarter of the patch has it and the patch is not sure)
and a polygon. Totals count only pixels in patches, so they always match the list.

## 6. Meta models

**SAM 3.1** gets the box of each of the 16 largest patches on the after image. It may drop pixels from a patch,
and it may add a neighbouring pixel (within 3 pixels) only if both periods were clear there and every condition
of the same rule holds at least within its margin. Added pixels are "likely", never "sure". So SAM can sharpen
an edge but cannot add change the satellite did not see. If its outline overlaps the patch by less than 0.3
IoU, the patch keeps the rule based outline and the disagreement is reported. Raw, kept, added, removed and
rejected areas are all stored.

**SAM 3.1 second witness** (built during the hackathon) gets no hint from the rules. It is asked, one concept
per request as SAM requires, to find a thing in the before image and, separately, in the after image:

| change type | concept | SAM's own view of the change |
|---|---|---|
| burned land | burn scar | found after, not before |
| new water | water | found after, not before |
| water gone | water | found before, not after |
| vegetation loss | trees | found before, not after |
| new building or bare ground | building | found after, not before |

Only concepts for change types the rules found are asked (at most 4 concepts, 8 calls, 4 at a time).
Instances scored below 0.3 are ignored, and ground not clear in both periods never counts. Each pixel is then
"both see it", "only the rules" or "only SAM", drawn as the SAM vs rules layer. Each patch gets the share of
it SAM also marks: agrees (at least half), partly (at least a fifth) or does not see it. Areas only SAM saw,
1 ha or more, are listed as places to check by eye. The rules' own confidence is not changed: SAM on 20 m
pixels is a second opinion, and both views stay visible side by side.

**Muse Spark looks at every big patch** (built during the hackathon). Every patch of 2 ha or more, 20 per run,
largest first, goes to Muse Spark as two true colour crops (before and after, contrast stretched, edge drawn
in yellow) with the label the rules gave and its area. It is not told how sure the rules were. It answers in a
fixed JSON shape (structured output): verdict (agrees, disagrees or unclear), its own label (one of the five
change types, no real change, cloud haze or shadow, seasonal change in crops or grass, something else), what
it sees and why. Any value outside those lists becomes "unclear". Four patches are looked at a time; answers
are cached per patch, a failed look is retried next time. Disagreements get a dashed red edge on the map,
unclear ones a dotted grey edge, and both are listed with what was seen. Nothing is removed: the rules' patch,
its confidence and the visual verdict are all shown.

**Field checklist** (built during the hackathon). Candidates are every patch of 1 ha or more and every place of
1 ha or more that only SAM saw. They are put in a first order by area, where a place counts double if Muse
Spark disagrees or could not tell, if SAM does not see it, or if only SAM saw it, because only a visit settles
those. The 25 first go to Muse Spark with their facts (kind, area, confidence, main doubt, both checks, what
Muse Spark saw). It chooses up to 10 stops, may reorder them, and writes what to look for and why (structured
output; the stop id must be one of the candidates). Unknown or repeated ids are dropped; empty text gets the
template text. If it names no real candidate, or the call fails, the plain version is used and the page says so.
Each stop's point is the pixel of its patch farthest from the edge, so it is always inside the patch even when the
patch is C shaped. Distances are straight lines between stops. The plain version is free and is redone on its
own when new checks arrive; a written one is kept and marked out of date. Exports: a printable page (overview
map, one block per stop with before and after pictures and a tick box form), KML and GeoJSON.

**Ask in plain words** (built during the hackathon). Muse Spark on the Responses API, with web search grounding
and three tools (OpenStreetMap place search, a Sentinel-2 scene count with whole tile cloud cover, submit plan),
turns a question into an area, two dates and a reason. The plan must pass the same limits as the map form;
a plan that fails goes back with the reason. At most 10 turns per plan and 40 plans an hour per server.

**Muse Spark** works as the analyst through tool calls: facts, region details, the visual check (chosen
patches, or every big patch as above), SAM outlines, the SAM second witness and NASA FIRMS fires. It may only use numbers its tools returned. The loop stops after 8 steps.
Without a Meta key the app still works and writes a plain automatic summary from the same facts.

**Limits.** Two analyses download at once and up to eight wait in line; each analysis allows 3 Muse Spark
reports, 3 SAM refinements, 3 visual check runs, 2 second witness runs, 3 field checklists and 30 questions,
one at a time.

## 7. What it cannot do

* See through cloud. A monsoon flood under cloud is "not seen", not "no flood".
* See objects smaller than a pixel. At 20 m a single house is below the limit; new building means a block.
* Tell the cause. A burn rule finds dark, lost vegetation; it cannot say who lit it.
* Replace a field check. The output says where to look first.
