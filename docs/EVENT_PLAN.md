# What we build during the event (Oct 27 to Nov 4)

The prototype (tag `pre-hackathon`) answers "what changed here" with spectral rules. The event work makes
Meta's models do things the rules cannot: understand the question, find the place and the dates, look at the
pictures, outline objects, and speak the user's language. Each item below is new, and the demo shows only these.

| # | Feature | Meta model | Why judges care |
|---|---|---|---|
| 1 | **Ask in plain words.** "What did the August floods do to farms near Sylhet?" Muse Spark finds the place and the event dates (search grounding), picks the windows, runs the analysis, and answers. | Muse Spark (tools, search grounding) | Agentic: it plans and runs a multi-step job. Best Agentic prize. |
| 2 | **Second witness.** SAM 3.1 segments "flood water", "burn scar", "new building" straight from the after image by text prompt; the map shows where SAM and the index rules agree and disagree. | SAM 3.1 (concept prompts) | Goes beyond a box-prompt integration; agreement becomes part of the confidence. |
| 3 | **Muse Spark looks at every big patch** (before and after, outline drawn) and says agrees, disagrees or unclear; disagreements are flagged on the map. | Muse Spark (vision, structured output) | Visual second opinion, verifiable. |
| 4 | **Field checklist.** Ranked "go here first" list with coordinates, what to look for and why, as a printable page and a KML/GeoJSON file for phones. | Muse Spark (writing) | Turns a map into an action for aid teams. |
| 5 | **Your language.** Report and answers in Bangla, Hindi, Urdu, Spanish, Portuguese, Greek...; voice questions through Muse Voice Transcribe. | Muse Spark, Muse Voice Transcribe | Field workers can use it without English or typing. |
| 6 | **Live online** on Hugging Face Spaces, demo video, X post. | | Demo 25 percent, viral prize. |

## Order

1. Day 1 (Oct 27): live key test of every Meta call already wired in the prototype; fix what breaks. Then #1.
2. Oct 28 to 29: #2 and #3.
3. Oct 30 to 31: #4 and #5.
4. Nov 1 to 2: deploy, polish, more demo places.
5. Nov 3: demo video, README, screenshots. Nov 4: submit early.

Every feature gets its own commits and a line in HACKATHON_LOG.md.
