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

# Display name -> (easyocr_code, deep_translator_source_code)
LANGUAGE_OPTIONS = {
    "Korean": ("ko", "ko"),
    "Chinese (Simplified)": ("ch_sim", "zh-CN"),
    "Chinese (Traditional)": ("ch_tra", "zh-TW"),
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

    def reset_memory(self):
        self.seen_paragraphs.clear()

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

    def extract_new_paragraphs(self, img_np, easyocr_code):
        """Runs OCR on the image, groups results into paragraphs, and
        returns only the paragraphs whose content hasn't been seen/returned
        before (marking them as seen so they won't come back again)."""
        reader = self.get_reader(easyocr_code)
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

    def translate(self, text, translate_code):
        translator = self.get_translator(translate_code)
        try:
            return translator.translate(text)
        except Exception as e:
            return f"[translation error: {e}]"