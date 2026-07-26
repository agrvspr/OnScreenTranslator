"""
Model layer.

Everything here is pure logic: capturing a screen region, running OCR,
grouping detected lines into paragraphs, deduping against previously-seen
paragraphs, and calling the translation API. Nothing in this file touches
Tkinter or any UI code, which means all of it is testable by feeding in
a numpy image array or a plain string -- no window needs to be open.
"""

import hashlib
from collections import OrderedDict

import mss
import numpy as np
from PIL import Image
from deep_translator import GoogleTranslator

TARGET_LANG = "en"

# Internal language keys -> (easyocr_code, deep_translator_source_code).
# These are the languages the app can actually OCR + translate.
LANGUAGES = {
    "korean": ("ko", "ko"),
    "chinese_simplified": ("ch_sim", "zh-CN"),
    "chinese_traditional": ("ch_tra", "zh-TW"),
    "japanese": ("ja", "ja"),
}

# What the dropdown shows -> internal key (or "auto" for auto-detect).
# "Auto-detect" is first so it's the default selection.
LANGUAGE_OPTIONS = {
    "Auto-detect": "auto",
    "Korean": "korean",
    "Chinese (Simplified)": "chinese_simplified",
    "Chinese (Traditional)": "chinese_traditional",
    "Japanese": "japanese",
}

# A vertical gap between two OCR lines bigger than (avg line height * this
# multiplier) is treated as a paragraph break rather than a line wrap.
PARAGRAPH_GAP_MULTIPLIER = 1.6

# How many recent paragraph hashes to remember, so scrolling back over
# text you've already seen doesn't re-translate it. Oldest entries are
# evicted once this many new ones have come in.
MAX_SEEN_PARAGRAPHS = 400


def capture_region(region):
    """region: (x1, y1, x2, y2) screen coordinates.
    Returns an RGB numpy array of that region of the screen."""
    x1, y1, x2, y2 = region
    monitor = {"left": x1, "top": y1, "width": x2 - x1, "height": y2 - y1}
    with mss.mss() as sct:
        shot = sct.grab(monitor)
    img = Image.frombytes("RGB", shot.size, shot.rgb)
    return np.array(img)


def detect_language_from_text(text):
    """Guess the source language from a piece of already-OCR'd text by
    counting characters in script ranges that are UNIQUE to each language.

    The trick: Korean and Japanese each have their own alphabets that
    Chinese never uses --
      - Hangul (Korean):    U+AC00-U+D7A3, plus jamo U+1100-U+11FF
      - Hiragana (Japanese): U+3040-U+309F
      - Katakana (Japanese): U+30A0-U+30FF
    Chinese uses only Han/CJK ideographs (U+4E00-U+9FFF), which Korean and
    Japanese *also* borrow -- so Han characters alone can't distinguish
    them, but the presence of Hangul or Kana is a dead giveaway.

    Returns one of: "korean", "japanese", "chinese", or None if the text
    has no CJK content to judge from (e.g. it's all Latin / punctuation).

    Note: returns generic "chinese" -- it can't tell Simplified from
    Traditional from script alone (they share the same Unicode block).
    The caller decides which Chinese variant reader to use; we default to
    Simplified elsewhere, with the dropdown as the manual override.
    """
    hangul = hiragana = katakana = han = 0
    for ch in text:
        cp = ord(ch)
        if 0xAC00 <= cp <= 0xD7A3 or 0x1100 <= cp <= 0x11FF:
            hangul += 1
        elif 0x3040 <= cp <= 0x309F:
            hiragana += 1
        elif 0x30A0 <= cp <= 0x30FF:
            katakana += 1
        elif 0x4E00 <= cp <= 0x9FFF:
            han += 1

    kana = hiragana + katakana

    # Any Hangul at all -> Korean (Korean text is overwhelmingly Hangul).
    if hangul > 0 and hangul >= kana:
        return "korean"
    # Any Kana at all -> Japanese (Chinese never uses Kana; Korean doesn't
    # either). Even a little Kana mixed with Han means Japanese.
    if kana > 0:
        return "japanese"
    # Han characters but no Hangul/Kana -> Chinese.
    if han > 0:
        return "chinese"
    return None


def group_lines_into_paragraphs(ocr_results):
    """ocr_results: list of (bbox, text, confidence) from
    easyocr.Reader.readtext(..., detail=1).

    Sorts detected lines top-to-bottom, then splits them into paragraphs
    wherever the vertical gap between consecutive lines is unusually large,
    which is a reasonable proxy for "this is a new paragraph" on most
    reading UIs (webnovels, e-readers, chat apps, etc).

    Returns a list of paragraph strings, in reading order (top to bottom).
    """
    if not ocr_results:
        return []

    lines = []
    for bbox, text, _conf in ocr_results:
        text = text.strip()
        if not text:
            continue
        ys = [point[1] for point in bbox]
        lines.append({"top": min(ys), "bottom": max(ys), "text": text})

    if not lines:
        return []

    lines.sort(key=lambda l: l["top"])

    heights = [l["bottom"] - l["top"] for l in lines if l["bottom"] > l["top"]]
    avg_height = (sum(heights) / len(heights)) if heights else 20
    gap_threshold = avg_height * PARAGRAPH_GAP_MULTIPLIER

    paragraphs = []
    current_words = [lines[0]["text"]]
    prev_bottom = lines[0]["bottom"]

    for line in lines[1:]:
        gap = line["top"] - prev_bottom
        if gap > gap_threshold:
            paragraphs.append(" ".join(current_words).strip())
            current_words = [line["text"]]
        else:
            current_words.append(line["text"])
        prev_bottom = line["bottom"]

    if current_words:
        paragraphs.append(" ".join(current_words).strip())

    return [p for p in paragraphs if p]


class TranslationModel:
    """Holds OCR readers, translators, and the dedup memory. One instance
    of this per running session; reset_memory() is called whenever the
    user targets a new screen region, since that's a fresh reading context."""

    def __init__(self):
        self.reader_cache = {}
        self.translator_cache = {}
        self.seen_paragraphs = OrderedDict()

        # Auto-detect state: the language key we've locked onto, once
        # detected. Stays locked until reset_memory() is called (e.g. the
        # user switches the dropdown away and back, or selects a new
        # region) -- it does not periodically re-check on its own.
        self._auto_locked_lang = None

    def reset_memory(self):
        self.seen_paragraphs.clear()
        self._auto_locked_lang = None

    def get_reader(self, easyocr_code, on_loading=None):
        """Lazily loads and caches an EasyOCR reader for the given language
        code. on_loading, if given, is called with a status string right
        before a new model starts downloading/loading (first use only)."""
        if easyocr_code not in self.reader_cache:
            if on_loading:
                on_loading(f"Loading {easyocr_code} OCR model...")
            import easyocr  # imported lazily -- slow import, only pay for it once needed
            self.reader_cache[easyocr_code] = easyocr.Reader(
                [easyocr_code, "en"], gpu=False
            )
        return self.reader_cache[easyocr_code]

    def get_translator(self, translate_code):
        if translate_code not in self.translator_cache:
            self.translator_cache[translate_code] = GoogleTranslator(
                source=translate_code, target=TARGET_LANG
            )
        return self.translator_cache[translate_code]

    # ------------------------------------------------------------------
    # Language resolution
    # ------------------------------------------------------------------
    def resolve_language(self, img_np, selected_key, on_loading=None):
        """Decides which concrete language key to use for this frame.

        selected_key is either a real language key (from the dropdown, e.g.
        "korean") or "auto". For a real key we just return it. For "auto",
        detection runs once (on the first frame after activation, or after
        any reset_memory() call) and then stays locked to that result --
        it will NOT keep re-detecting every frame. To force a fresh
        detection, switch the dropdown to a manual language and back to
        Auto-detect (or select a new region), both of which reset the lock.

        Returns (language_key, detected_flag) where detected_flag is True
        if this was resolved by auto-detection (useful for status display).
        """
        if selected_key != "auto":
            return selected_key, False

        # Already locked onto a language from a previous frame -> keep using it.
        if self._auto_locked_lang:
            return self._auto_locked_lang, True

        detected = self._detect_language(img_np, on_loading)
        if detected:
            self._auto_locked_lang = detected
            return detected, True

        # No CJK text found yet (e.g. still loading a page) -- don't lock,
        # so the next frame tries detection again. Meanwhile fall back to
        # Korean as an arbitrary default so the pipeline has *a* reader to
        # use rather than stalling.
        return "korean", True

    def _detect_language(self, img_np, on_loading=None):
        """Run one cheap OCR pass with a lightweight reader, read the
        script, and map it to a concrete language key. Chinese defaults to
        Simplified (script alone can't tell Simplified from Traditional --
        the dropdown is the manual override for Traditional)."""
        # Use whichever reader is already loaded for a cheap first pass;
        # if none loaded yet, load Korean (arbitrary) to bootstrap. Any
        # CJK-capable reader detects Hangul/Kana/Han code points fine even
        # if it's not the "right" language for full recognition.
        if self.reader_cache:
            probe_code = next(iter(self.reader_cache))
        else:
            probe_code = LANGUAGES["korean"][0]
        reader = self.get_reader(probe_code, on_loading)

        results = reader.readtext(img_np, detail=0)
        sample = " ".join(results)
        script = detect_language_from_text(sample)

        if script == "korean":
            return "korean"
        if script == "japanese":
            return "japanese"
        if script == "chinese":
            return "chinese_simplified"
        return None

    # ------------------------------------------------------------------
    # Extraction + translation
    # ------------------------------------------------------------------
    def extract_new_paragraphs(self, img_np, language_key, on_loading=None):
        """Runs OCR on the image with the reader for language_key, groups
        results into paragraphs, and returns only paragraphs whose content
        hasn't been seen before (marking them seen so they don't repeat)."""
        easyocr_code, _translate_code = LANGUAGES[language_key]
        reader = self.get_reader(easyocr_code, on_loading)
        results = reader.readtext(img_np, detail=1)
        paragraphs = group_lines_into_paragraphs(results)

        new_paragraphs = []
        for para in paragraphs:
            para_hash = hashlib.md5(para.encode("utf-8")).hexdigest()
            if para_hash in self.seen_paragraphs:
                continue
            self._remember(para_hash)
            new_paragraphs.append(para)
        return new_paragraphs

    def _remember(self, para_hash):
        self.seen_paragraphs[para_hash] = True
        if len(self.seen_paragraphs) > MAX_SEEN_PARAGRAPHS:
            self.seen_paragraphs.popitem(last=False)  # evict oldest

    def translate(self, text, language_key):
        _easyocr_code, translate_code = LANGUAGES[language_key]
        translator = self.get_translator(translate_code)
        try:
            return translator.translate(text)
        except Exception as e:
            return f"[translation error: {e}]"