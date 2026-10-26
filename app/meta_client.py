"""Meta Model API: Muse Spark (chat, tools, image understanding) and SAM 3.1 (segmentation).

Model API speaks the OpenAI protocol (base URL https://api.meta.ai/v1, key in MODEL_API_KEY), so the
OpenAI SDK is the client. SAM 3.1 answers over the Responses API as special-token text, which the official
meta_sam_parser package turns into boxes and box-local masks. Docs: https://dev.meta.ai/docs/overview
"""
from __future__ import annotations

import asyncio
import base64
import io
import json

import numpy as np
from PIL import Image

from app import config as C


def available() -> bool:
    return bool(C.META_API_KEY)


def _client(asynchronous=False):
    from openai import AsyncOpenAI, OpenAI
    cls = AsyncOpenAI if asynchronous else OpenAI
    return cls(base_url=C.META_BASE_URL, api_key=C.META_API_KEY, timeout=C.META_TIMEOUT)


def png_data_url(rgb: np.ndarray) -> str:
    buf = io.BytesIO()
    Image.fromarray(rgb[..., :3]).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# ---------------------------------------------------------------------------------------------------
# Muse Spark
# ---------------------------------------------------------------------------------------------------

def chat(messages: list, tools: list | None = None, json_mode: bool = False):
    """One Chat Completions call. Returns the assistant message object."""
    kw = {"model": C.META_CHAT_MODEL, "messages": messages}
    if tools:
        kw["tools"] = tools
    if json_mode:
        kw["response_format"] = {"type": "json_object"}
    return _client().chat.completions.create(**kw).choices[0].message


def look(question: str, images: list[tuple[str, np.ndarray]]) -> dict:
    """Ask Muse Spark about labelled images. Expects a JSON answer; returns {} if it can't parse one."""
    content = [{"type": "text", "text": question}]
    for label, rgb in images:
        content.append({"type": "text", "text": label})
        content.append({"type": "image_url", "image_url": {"url": png_data_url(rgb)}})
    msg = chat([{"role": "user", "content": content}], json_mode=True)
    try:
        return json.loads(msg.content or "{}")
    except json.JSONDecodeError:
        return {"raw": msg.content}


# ---------------------------------------------------------------------------------------------------
# SAM 3.1
# ---------------------------------------------------------------------------------------------------

def segment(rgb: np.ndarray, boxes: list[tuple[int, int, int, int]] | None = None, text: str | None = None) -> list[dict]:
    """Segment one image by box prompts (up to 16, ids 1..n in order) or by a short noun phrase.

    Returns [{"id", "box": (l, t, r, b), "mask": bool array the size of the image, "confidence"}].
    """
    return asyncio.run(_segment(rgb, boxes, text))


async def _segment(rgb, boxes, text):
    from meta_sam_parser import (PromptBox, PromptObject, FramePrompt, build_object_prompt,
                                 decode_mask_to_raster, image_segmentation_format, parse_responses_stream)
    h, w = rgb.shape[:2]
    if boxes:
        prompt = build_object_prompt(size=(w, h), objects=[
            PromptObject(id=i + 1, prompts=[FramePrompt(box=PromptBox(*b))]) for i, b in enumerate(boxes[:16])])
    elif text:
        prompt = text
    else:
        raise ValueError("give boxes or text")
    client = _client(asynchronous=True)
    stream = await client.responses.create(
        model=C.META_SAM_MODEL, stream=True,
        metadata={"mask_encoding": "one_bit", "include_confidence": "true"},
        input=[{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": prompt},
            {"type": "input_image", "image_url": png_data_url(rgb)}]}])
    result = await parse_responses_stream(stream, image_segmentation_format()).final_result()
    boxes_by_id = {r.object_id: r for r in result.records if r.kind == "box"}
    out = []
    for r in result.records:
        if r.kind != "mask":
            continue
        raster = np.frombuffer(decode_mask_to_raster(r.mask), dtype=np.uint8).reshape(r.mask.height, r.mask.width)
        l, t = int(round(r.bounds.left)), int(round(r.bounds.top))
        bw, bh = max(1, int(round(r.bounds.right)) - l), max(1, int(round(r.bounds.bottom)) - t)
        if raster.shape != (bh, bw):  # box-local raster at its own resolution: scale it onto the box
            raster = np.asarray(Image.fromarray(raster * 255).resize((bw, bh), Image.NEAREST)) > 0
        full = np.zeros((h, w), bool)
        y0, x0 = max(t, 0), max(l, 0)
        y1, x1 = min(t + bh, h), min(l + bw, w)
        full[y0:y1, x0:x1] = raster[y0 - t:y1 - t, x0 - l:x1 - l] > 0
        b = boxes_by_id.get(r.object_id)
        out.append({"id": r.object_id, "box": (b.left, b.top, b.right, b.bottom) if b else (l, t, l + bw, t + bh),
                    "mask": full, "confidence": r.confidence})
    return out
