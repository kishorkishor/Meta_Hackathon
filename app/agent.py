"""Muse Spark as the analyst: it decides which checks to run with tools, then writes or answers in plain words.

Every number it may use comes from a tool result. The loop is bounded (AGENT_MAX_STEPS) and every tool is cheap
or cached, so a report takes a few calls, not dozens.
"""
from __future__ import annotations

import json

from app import config as C
from app import firms, meta_client
from app.analysis import Analysis
from app.refine import check_regions, refine_with_sam
from app.report import facts
from app.witness import second_witness

SYSTEM = (
    "You are a careful Earth observation analyst. You explain what changed in an area between two dates, "
    "for people who are not experts: aid workers, local officials, journalists. Rules: use only numbers and "
    "facts returned by your tools; never invent places, causes or casualties; say plainly what could not be "
    "checked (cloud, shadow, image shift) and how sure each finding is; when a visual check disagrees with "
    "the detector, say so. Write short plain sentences. Do not use dashes as punctuation. Coordinates are "
    "given as [longitude, latitude]; write them as latitude, longitude.")

TOOLS = [
    {"type": "function", "function": {
        "name": "get_facts",
        "description": "Totals per change type, images used, share of the area that was clear, largest regions.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_region",
        "description": "Full details of one region: area, confidence, reasons for doubt, visual check if done.",
        "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}}},
    {"type": "function", "function": {
        "name": "check_regions_visually",
        "description": "Look at before and after images of up to 6 regions and say if each label looks right.",
        "parameters": {"type": "object", "properties": {
            "ids": {"type": "array", "items": {"type": "integer"}, "maxItems": 6}}, "required": ["ids"]}}},
    {"type": "function", "function": {
        "name": "refine_outlines",
        "description": "Sharpen the outlines of the largest regions with SAM 3.1 and report how areas changed.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "second_witness",
        "description": "Ask SAM 3.1, with no hint from the detector, to find water, burn scars, trees or buildings "
                       "in the before and after images. Returns how much of each change type SAM agrees with, "
                       "per patch agreement, and large areas only SAM saw (worth checking by eye).",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "active_fires",
        "description": "NASA FIRMS fire detections inside the area over the last N days (1 to 10).",
        "parameters": {"type": "object", "properties": {"days": {"type": "integer"}}, "required": ["days"]}}},
]


def _run_tool(a: Analysis, name: str, args: dict):
    if name == "get_facts":
        return facts(a)
    if name == "get_region":
        f = next((f for f in a.regions if f["properties"]["id"] == args.get("id")), None)
        if f is None:
            return {"error": "no such region"}
        return {**f["properties"], "visual_check": a.checks.get(f["properties"]["id"])}
    if name == "check_regions_visually":
        return {str(k): v for k, v in check_regions(a, [int(i) for i in args.get("ids", [])]).items()}
    if name == "refine_outlines":
        return a.refined or refine_with_sam(a)
    if name == "second_witness":
        if a.witness:
            return a.witness
        if a.witnesses_made >= C.MAX_WITNESS_PER_RUN:
            return {"error": "second witness limit for this analysis reached"}
        a.witnesses_made += 1
        return second_witness(a)
    if name == "active_fires":
        return firms.active_fires(a.bbox, args.get("days", 7))
    return {"error": f"unknown tool {name}"}


def _loop(a: Analysis, messages: list) -> str:
    for _ in range(C.AGENT_MAX_STEPS):
        msg = meta_client.chat(messages, tools=TOOLS)
        calls = msg.tool_calls or []
        messages.append({"role": "assistant", "content": msg.content or "",
                         **({"tool_calls": [c.model_dump() for c in calls]} if calls else {})})
        if not calls:
            return (msg.content or "").strip()
        for c in calls:
            try:
                args = json.loads(c.function.arguments or "{}")
                result = _run_tool(a, c.function.name, args)
            except Exception as e:  # a failed tool is information for the analyst, not a crash
                result = {"error": f"{type(e).__name__}: {e}"}
            messages.append({"role": "tool", "tool_call_id": c.id, "content": json.dumps(result, default=str)})
    messages.append({"role": "user", "content": "Stop calling tools and answer now with what you have."})
    return (meta_client.chat(messages).content or "").strip()


REPORT_TASK = (
    "Write the change report for this area. First get the facts. Then check the largest regions visually "
    "(up to 6), sharpen their outlines, and ask SAM for a second witness. Then write: a one sentence headline; "
    "what changed, with hectares and how sure, including where SAM agrees or not; what could not be checked "
    "and why; and the two or three places to look at first on the ground, with coordinates. Keep it under "
    "200 words.")


def report_task(a: Analysis) -> str:
    if not a.question:
        return REPORT_TASK
    plan = a.plan or {}
    why = f" The area and dates were chosen like this: {plan['why']}" if plan.get("why") else ""
    return (f"A person asked: \"{a.question}\".{why} {REPORT_TASK} Make the headline a direct answer to "
            "their question, and if the images cannot answer part of it, say which part and why.")


def write_report(a: Analysis) -> str:
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": report_task(a)}]
    text = _loop(a, messages)
    a.chat = messages[1:]
    a.report, a.report_source = text, "muse-spark"
    return text


def ask(a: Analysis, question: str) -> str:
    history = a.chat[-12:] if a.chat else [{"role": "user", "content": "Context: report for this area."},
                                          {"role": "assistant", "content": a.report}]
    # keep the history self-contained: drop a leading tool reply whose call was trimmed away
    while history and history[0].get("role") == "tool":
        history = history[1:]
    messages = [{"role": "system", "content": SYSTEM}, *history, {"role": "user", "content": question}]
    answer = _loop(a, messages)
    a.chat = messages[1:]
    return answer
