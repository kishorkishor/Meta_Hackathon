"""The languages the page offers for reports, answers, plans and the field checklist.

`name` is the English name, used in prompts and as Muse Voice Transcribe's languageBias. `voice` marks the
languages Muse Voice Transcribe was evaluated on (25 of them, https://dev.meta.ai/docs/speech-to-text); for the
others the page still takes voice, but the transcriber detects the language on its own and may do worse.
"""
from __future__ import annotations

LANGUAGES = [
    {"code": "en", "name": "English", "native": "English", "rtl": False, "voice": True},
    {"code": "bn", "name": "Bengali", "native": "বাংলা", "rtl": False, "voice": True},
    {"code": "hi", "name": "Hindi", "native": "हिन्दी", "rtl": False, "voice": True},
    {"code": "ur", "name": "Urdu", "native": "اردو", "rtl": True, "voice": False},
    {"code": "ta", "name": "Tamil", "native": "தமிழ்", "rtl": False, "voice": True},
    {"code": "mr", "name": "Marathi", "native": "मराठी", "rtl": False, "voice": True},
    {"code": "ar", "name": "Arabic", "native": "العربية", "rtl": True, "voice": True},
    {"code": "es", "name": "Spanish", "native": "Español", "rtl": False, "voice": True},
    {"code": "pt", "name": "Portuguese", "native": "Português", "rtl": False, "voice": True},
    {"code": "fr", "name": "French", "native": "Français", "rtl": False, "voice": True},
    {"code": "el", "name": "Greek", "native": "Ελληνικά", "rtl": False, "voice": False},
    {"code": "tr", "name": "Turkish", "native": "Türkçe", "rtl": False, "voice": True},
    {"code": "id", "name": "Indonesian", "native": "Bahasa Indonesia", "rtl": False, "voice": True},
    {"code": "vi", "name": "Vietnamese", "native": "Tiếng Việt", "rtl": False, "voice": True},
    {"code": "sw", "name": "Swahili", "native": "Kiswahili", "rtl": False, "voice": False},
]
BY_CODE = {lang["code"]: lang for lang in LANGUAGES}
ENGLISH = BY_CODE["en"]


def get(code: str | None) -> dict:
    """The language for a code; raises KeyError for one the page does not offer."""
    return BY_CODE[(code or "en").strip().lower()]


def instruction(code: str | None) -> str:
    """What to add to a prompt so the words come back in that language. Empty for English."""
    lang = get(code)
    if lang["code"] == "en":
        return ""
    return (f"Write every sentence for the reader in {lang['name']} ({lang['native']}), in plain everyday words. "
            "Write numbers with the digits 0 to 9, and keep patch numbers like #12, coordinates, dates and the "
            "names Sentinel-2, SAM 3.1 and Muse Spark exactly as given, so they match the map.")
