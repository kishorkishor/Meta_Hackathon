# Demo video script (about 2 minutes 30)

Record at 1920 x 1080, dark mode, browser zoom 100 percent. Run `python -m scripts.prepare_demos` first so
every demo opens instantly. Speak slowly; the map does the work.

## 0:00 The problem (20 s)

Screen: the empty map over South Asia.

> After a flood or a fire, the people deciding where to send help often find out from phone calls, days later.
> Free satellites photograph every place on Earth every few days. The hard part is reading the pictures,
> and knowing when the picture is lying to you.

## 0:20 Bandarban hill burning (50 s)

Pick "Hill burning before the monsoon". Press Compare.

> These hills in Bangladesh are burned for farming every spring, right before the monsoon, on the same
> slopes that slide when the rain comes.

Turn layers on one at a time: Before, After, Change.

> Orange is burned land, red is new bare ground. Grey is ground the satellite never saw clearly in both
> periods. We show it instead of pretending it did not change.

Point at the seasonal line in the report.

> The whole area dried out between January and May. We take that out first, so a dry hillside is not
> called a disaster.

Click the largest burned patch. The before and after crop opens.

> Every patch says how sure it is, and why not, if it is not: too close to a cloud, a possible one pixel
> image shift, or only one clear look.

## 1:10 Meta models at work (50 s)

Press "Ask Muse Spark for the report".

> Muse Spark is the analyst. It decides what to check. It looks at the before and after pictures of the
> biggest patches and tells us when it disagrees with our rules. It asks SAM 3.1 to sharpen the outlines,
> but SAM is only allowed to work where the satellite actually saw change.

Turn on "SAM outlines". Read the report headline aloud. Ask one question:
"Which burned patch is closest to a village, and how sure are you?"

## 2:00 Anywhere on Earth (25 s)

Pick the Evros fire, then the Sindh flood: one Compare each, no narration beyond the place name.
Then press Draw area somewhere new, pick two dates, Compare.

> Any place, any two dates, the same honest answer: what changed, how sure we are, and where to look first.

## Checklist before recording

* `/api/health` shows `"meta": true`.
* Each demo has been run once (cached). The Muse Spark report for Bandarban has been generated once and
  looks right; regenerate if it says anything not in the facts.
* Close other tabs; turn off notifications.
