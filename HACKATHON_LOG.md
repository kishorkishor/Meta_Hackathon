# Built during the hackathon

Everything before the git tag `pre-hackathon` existed before the event (see the README). This file lists
what was built during the building period, Oct 27 to Nov 4, 2026, newest last.

| Date | What | Commits |
|---|---|---|
| Oct 27 | **Ask in plain words.** New `app/planner.py`: Muse Spark on the Responses API with web search grounding and three tools (OpenStreetMap place search, a Sentinel-2 clear image check, submit plan) turns a question into an area, two dates and a reason. Plans are checked against the map limits and sent back with the reason when wrong. New `POST /api/plan`, runs remember the question, and the report answers it first. Page: question box, plan card with sources and steps, then the comparison and report run on their own. 18 new offline tests (69 in total). | see `git log` for Oct 27 |
| Oct 27 | **SAM 3.1 second witness.** New `app/witness.py`: SAM 3.1 concept prompts ("burn scar", "water", "trees", "building") on the before and after images separately, with no hint from the rules; SAM's own change is compared pixel by pixel with the rules. New SAM vs rules map layer (both, only rules, only SAM), per patch agreement, list of areas only SAM saw, `POST /api/analyze/{id}/witness`, a `second_witness` tool for the Muse Spark analyst, and the result in the report facts. Concept calls run 4 at a time. 9 new offline tests (78 in total). | see `git log` for Oct 27 |
