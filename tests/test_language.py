"""Your language: reports, answers, plans and the field checklist in the reader's language, and voice questions
through Muse Voice Transcribe. All offline: the Meta calls are replaced by fakes that record what was sent."""
import io
import json
import wave
from types import SimpleNamespace
from xml.etree import ElementTree

import numpy as np
import pytest

from app import agent, fieldlist, languages, meta_client, planner, voice
from tests.test_api import BODY, client, wait  # noqa: F401  (client is a fixture)
from tests.test_fieldlist import fake_chat
from tests.test_meta import make_analysis, tmp_cache  # noqa: F401


def wav_bytes(seconds=2.0, rate=16000, channels=1, width=2):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        t = np.arange(int(seconds * rate)) / rate
        tone = (np.sin(2 * np.pi * 220 * t) * 8000).astype("<i2")
        w.writeframes(np.repeat(tone, channels).tobytes() if width == 2 else bytes(len(t) * channels * width))
    return buf.getvalue()


def test_languages_and_the_prompt_line():
    assert languages.instruction("en") == ""
    line = languages.instruction("bn")
    assert "Bengali" in line and "বাংলা" in line and "digits 0 to 9" in line
    assert languages.get("UR")["rtl"] and not languages.get("el")["voice"]
    with pytest.raises(KeyError):
        languages.get("xx")
    assert len({lang["code"] for lang in languages.LANGUAGES}) == len(languages.LANGUAGES)


def test_report_and_answers_come_in_the_chosen_language(monkeypatch):
    a = make_analysis()
    seen = []

    def chat(messages, tools=None, json_mode=False, schema=None):
        seen.append(messages[0]["content"])
        return SimpleNamespace(content="প্রায় ১৬ হেক্টর পুড়েছে।", tool_calls=None)
    monkeypatch.setattr(meta_client, "chat", chat)
    agent.write_report(a, "bn")
    assert "Bengali" in seen[-1] and a.report_language == "bn" and a.summary()["report_language"] == "bn"
    agent.ask(a, "Which patch is largest?", "hi")
    assert "Hindi" in seen[-1]
    agent.ask(a, "And now?")
    assert seen[-1] == agent.SYSTEM                                  # English: the rules alone


def test_the_planner_writes_its_reasons_in_the_chosen_language(monkeypatch):
    seen = []

    def respond(input, tools=None, instructions=None, previous_response_id=None):
        seen.append(instructions)
        raise RuntimeError("stop here")
    monkeypatch.setattr(meta_client, "respond", respond)
    for code in ("es", "en"):
        with pytest.raises(RuntimeError):
            planner.plan("¿Qué hicieron las inundaciones en Dadu?", code)
    assert "Spanish" in seen[0] and "OpenStreetMap" in seen[0]
    assert "reader's language" not in seen[1]


def test_a_checklist_in_bangla_translates_the_page_and_keeps_english_where_a_label_is_missing(monkeypatch):
    a = make_analysis()
    seen = []
    monkeypatch.setattr(meta_client, "chat", fake_chat({
        "intro": "প্রথমে বড় পোড়া জায়গায় যান।",
        "stops": [{"ref": "P1", "look_for": "ছাই আর পোড়া গাছ।", "why": "সবচেয়ে বড়।"}],
        "labels": {"title": "মাঠ পরিদর্শনের তালিকা", "look_for": "যা দেখবেন", "kind_burned_land": "পোড়া জমি",
                   "stops_folder": "থামার জায়গা", "as_on_map": "", "safety": "আগে পথ নিরাপদ কিনা দেখুন।"}}, seen))
    fl = fieldlist.make_checklist(a, language="bn")
    prompt, schema = seen[0]
    assert "Bengali" in prompt and '"look_for": "Look for"' in prompt
    assert "labels" in schema["required"] and set(schema["properties"]["labels"]["required"]) == set(fieldlist.LABELS)
    assert fl["language"] == "bn" and fl["safety"] == "আগে পথ নিরাপদ কিনা দেখুন।"
    assert fl["labels"]["as_on_map"] == "as on the map"              # empty translation stays English
    page = fieldlist.page(a)
    assert '<html lang="bn" dir="ltr">' in page and "মাঠ পরিদর্শনের তালিকা" in page and "1. পোড়া জমি, 16 ha" in page
    assert "<b>যা দেখবেন:</b> ছাই আর পোড়া গাছ।" in page and "as on the map" in page
    root = ElementTree.fromstring(fieldlist.kml(a).encode())
    assert root.find(".//{http://www.opengis.net/kml/2.2}Folder/{http://www.opengis.net/kml/2.2}name").text == "থামার জায়গা"
    assert fieldlist.geojson(a)["language"] == "bn"


def test_urdu_pages_read_right_to_left_and_a_plain_list_says_it_stayed_english(monkeypatch):
    a = make_analysis()
    fl = fieldlist.make_checklist(a, use_model=False, language="ur")
    assert fl["language"] == "en" and fl["asked_language"] == "ur" and "needs Muse Spark" in fl["note"]
    assert '<html lang="en" dir="ltr">' in fieldlist.page(a)
    monkeypatch.setattr(meta_client, "chat", fake_chat({"intro": "سب سے پہلے", "stops": [
        {"ref": "P1", "look_for": "راکھ", "why": "سب سے بڑا"}], "labels": {"title": "فیلڈ فہرست"}}))
    fieldlist.make_checklist(a, language="ur")
    page = fieldlist.page(a)
    assert '<html lang="ur" dir="rtl">' in page and '<div class="where" dir="ltr">' in page


def test_the_recording_is_checked_before_anything_is_sent():
    assert voice.check_wav(wav_bytes(2.0)) == pytest.approx(2.0)
    assert voice.check_wav(wav_bytes(1.0, rate=24000)) == pytest.approx(1.0)
    for bad, why in ((b"RIFF nonsense", "not a WAV"), (wav_bytes(1, channels=2), "mono"),
                     (wav_bytes(1, rate=8000), "16 or 24 kHz"), (wav_bytes(0.1), "empty"),
                     (wav_bytes(62), "at most 60 s")):
        with pytest.raises(voice.VoiceError, match=why):
            voice.check_wav(bad)


def test_the_transcribe_call_sends_the_request_part_and_the_audio(monkeypatch):
    sent = {}

    def post(url, headers=None, files=None, timeout=None):
        sent.update(url=url, headers=headers, files=files)
        return SimpleNamespace(raise_for_status=lambda: None,
                               json=lambda: {"sessionId": "s", "transcript": "  floods near   Dadu ",
                                             "audioDurationMs": 2000, "turns": []})
    monkeypatch.setattr("requests.post", post)
    monkeypatch.setattr(meta_client.C, "META_API_KEY", "k")
    out = voice.transcribe(wav_bytes(2.0), "bn", places=["Dadu, Sindh"])
    assert out == {"text": "floods near Dadu", "seconds": 2.0, "language": "bn", "hinted": True}
    assert sent["url"].endswith("/v1/asr/transcribe") and sent["headers"]["Authorization"] == "Bearer k"
    req = json.loads(sent["files"]["request"][1])
    assert req["model"] == "muse-voice-transcribe-1.0" and req["audioEncoding"] == "WAV"
    assert req["languageBias"] == ["Bengali"] and "Dadu, Sindh" in req["keywords"]
    assert sent["files"]["audio"][2] == "audio/wav"
    voice.transcribe(wav_bytes(2.0), "el")                       # Greek was not evaluated: let it detect
    assert "languageBias" not in json.loads(sent["files"]["request"][1])


def test_language_and_voice_endpoints(client, monkeypatch):  # noqa: F811
    assert [lang["code"] for lang in client.get("/api/languages").json()][:2] == ["en", "bn"]
    assert client.post("/api/transcribe", content=wav_bytes()).status_code == 503       # no key
    rid = client.post("/api/analyze", json=BODY).json()["id"]
    wait(client, rid)
    assert client.post(f"/api/analyze/{rid}/report?language=xx").status_code == 422
    assert client.post(f"/api/analyze/{rid}/field?language=xx").status_code == 422
    assert client.post("/api/plan", json={"question": "floods near Dadu", "language": "xx"}).status_code == 422
    j = client.post(f"/api/analyze/{rid}/field?language=hi").json()                     # plain list, free
    assert j["checklist"]["asked_language"] == "hi" and "Hindi" in j["checklist"]["note"]

    monkeypatch.setattr(meta_client.C, "META_API_KEY", "x")
    monkeypatch.setattr(voice, "_RECENT", __import__("collections").deque())
    monkeypatch.setattr(meta_client, "transcribe",
                        lambda wav, bias=None, keywords=None: {"transcript": "বান্দরবানে কী পুড়েছে?",
                                                               "audioDurationMs": 1500})
    r = client.post(f"/api/transcribe?language=bn&run_id={rid}", content=wav_bytes(1.5),
                    headers={"Content-Type": "audio/wav"})
    assert r.status_code == 200 and r.json()["text"] == "বান্দরবানে কী পুড়েছে?"
    assert client.post("/api/transcribe", content=b"not audio").status_code == 400
    assert client.post("/api/transcribe?language=xx", content=wav_bytes()).status_code == 422
    assert client.post("/api/transcribe", content=b"0" * 3_300_000).status_code == 413
    monkeypatch.setattr(voice.C, "VOICE_PER_HOUR", 1)
    assert client.post("/api/transcribe", content=wav_bytes()).status_code == 429

    def chat(messages, tools=None, json_mode=False, schema=None):   # answers with the language it was asked for
        named = [lang["name"] for lang in languages.LANGUAGES[1:] if lang["name"] in messages[0]["content"]]
        return SimpleNamespace(content="in " + (named[0] if named else "English"), tool_calls=None)
    monkeypatch.setattr(meta_client, "chat", chat)
    j = client.post(f"/api/analyze/{rid}/report?language=pt").json()
    assert j["report_language"] == "pt" and j["report"] == "in Portuguese"
    a = client.post(f"/api/analyze/{rid}/ask", json={"question": "Onde?", "language": "es"}).json()
    assert a == {"answer": "in Spanish", "language": "es"}
