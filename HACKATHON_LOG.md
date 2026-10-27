# Built during the hackathon

Everything before the git tag `pre-hackathon` existed before the event (see the README). This file lists
what was built during the building period, Oct 27 to Nov 4, 2026, newest last.

| Date | What | Commits |
|---|---|---|
| Oct 27 | **Ask in plain words.** New `app/planner.py`: Muse Spark on the Responses API with web search grounding and three tools (OpenStreetMap place search, a Sentinel-2 clear image check, submit plan) turns a question into an area, two dates and a reason. Plans are checked against the map limits and sent back with the reason when wrong. New `POST /api/plan`, runs remember the question, and the report answers it first. Page: question box, plan card with sources and steps, then the comparison and report run on their own. 18 new offline tests (69 in total). | see `git log` for Oct 27 |
