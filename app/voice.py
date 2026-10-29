"""Voice questions: a short recording from the page goes to Muse Voice Transcribe and comes back as text.

The page records with the microphone and turns the sound into the one format the service takes (mono 16 bit PCM
WAV at 16 or 24 kHz). This module checks that before anything is sent, so a bad file costs nothing, and passes
the chosen language as a hint (languageBias) only for the languages the service was evaluated on; for the others
it detects the language itself. The text goes back into the question box, where the person can read and fix it
before asking: a misheard place name should not start an analysis on its own.
"""
from __future__ import annotations

import io
import threading
import time
import wave
from collections import deque

from app import config as C, languages, meta_client

RATES = (16000, 24000)
# words the transcriber should expect in questions about this app
KEYWORDS = ["Sentinel-2", "SAM", "Muse Spark", "hectares", "flood", "burn scar", "deforestation", "cyclone"]
MAX_KEYWORDS = 20


class VoiceError(ValueError):
    pass


def check_wav(data: bytes) -> float:
    """Seconds of audio, if `data` is a WAV the service takes and not longer than a question; else VoiceError."""
    try:
        with wave.open(io.BytesIO(data)) as w:
            channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
    except (wave.Error, EOFError) as e:
        raise VoiceError(f"the recording is not a WAV file ({e})")
    if channels != 1 or width != 2 or rate not in RATES:
        raise VoiceError(f"the recording must be mono 16 bit at 16 or 24 kHz, not {channels} channel, "
                         f"{width * 8} bit, {rate} Hz")
    seconds = frames / rate
    if seconds < 0.3:
        raise VoiceError("the recording is empty; hold the button a little longer")
    if seconds > C.VOICE_MAX_SECONDS + 1:
        raise VoiceError(f"the recording is {seconds:.0f} s; a question can be at most {C.VOICE_MAX_SECONDS} s")
    return seconds


_RECENT: deque = deque()
_RATE_LOCK = threading.Lock()


def allow() -> bool:
    """At most VOICE_PER_HOUR recordings an hour for the whole server: the public demo shares one key."""
    with _RATE_LOCK:
        now = time.time()
        while _RECENT and now - _RECENT[0] > 3600:
            _RECENT.popleft()
        if len(_RECENT) >= C.VOICE_PER_HOUR:
            return False
        _RECENT.append(now)
        return True


def transcribe(data: bytes, language: str = "en", places: list[str] | None = None) -> dict:
    """{"text", "seconds", "language", "hinted"}. `places` (the area's name, say) help with names."""
    seconds = check_wav(data)
    lang = languages.get(language)
    words = KEYWORDS + [p for p in (places or []) if p]
    out = meta_client.transcribe(data, [lang["name"]] if lang["voice"] else None,
                                 list(dict.fromkeys(words))[:MAX_KEYWORDS])
    text = " ".join(str(out.get("transcript") or "").split())
    return {"text": text, "seconds": round(out.get("audioDurationMs", seconds * 1000) / 1000, 1),
            "language": lang["code"], "hinted": lang["voice"]}
