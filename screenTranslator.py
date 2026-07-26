"""
Real-Time Screen Translator - Side Panel Edition
--------------------------------------------------
A docked panel (like a sidebar) sits next to whatever you're reading
(a book app, a game, a website) and shows the live English translation
of Korean or Chinese text as it changes on screen -- similar to having
a translated subtitle track running alongside the original content.

Layout matches: [ Whatever you're viewing ] | [ This app's panel ]
                                              |   - language dropdown
                                              |   - translated text feed

WINDOWS-FRIENDLY (no Win32-specific tricks used here, unlike an earlier
click-through-overlay version -- this should also run on Mac/Linux with
Python + Tkinter, since mss handles screen capture cross-platform).

--------------------------------------------------------------------
SETUP
--------------------------------------------------------------------
    pip install easyocr mss deep-translator pillow numpy

    python screen_translator.py

First run downloads OCR models per language the first time you use them
(~50-100MB each) and installs PyTorch as a dependency of EasyOCR (1-2GB),
so the very first launch will take a few minutes.

--------------------------------------------------------------------
HOW TO USE
--------------------------------------------------------------------
1. Run the script. A panel window opens (defaults to docking on the
   right side of your screen).
2. Pick your source language from the dropdown (Korean / Chinese
   Simplified / Chinese Traditional).
3. Click "Select Region" and drag a rectangle over the area of your
   screen showing the foreign text (e.g. the text area of your e-reader
   or game window).
4. Click "Start". The panel will begin showing translated text as it
   detects new/changed text in that region.
5. Switch languages any time from the dropdown -- it reloads the
   correct OCR model (may take a few seconds the first time each
   language is used).
6. Click "Stop" to pause, "Select Region" again to re-target a
   different area (e.g. you turned the page / moved to a new screen).

--------------------------------------------------------------------
KNOWN LIMITATIONS (starter app, not a polished product)
--------------------------------------------------------------------
- Translates the captured region as one combined block of text, not
  per-line/per-paragraph. See "NEXT STEPS" at the bottom for how to
  extend this to per-line translation.
- Free Google Translate (via deep-translator) can rate-limit under
  heavy polling. REFRESH_INTERVAL=1.5s is a safe default.
- Switching languages mid-session reloads the OCR model, which takes
  a few seconds -- this is a one-time cost per language per run.
"""

import time
import hashlib
import threading
import queue
import tkinter as tk
from tkinter import ttk

import mss
import numpy as np
from PIL import Image
from deep_translator import GoogleTranslator

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
REFRESH_INTERVAL = 1.5   # seconds between screen captures
TARGET_LANG = "en"       # translate target language
PANEL_WIDTH = 380

# Language options: display name -> (easyocr_code, translate_code)
LANGUAGE_OPTIONS = {
    "Korean": ("ko", "ko"),
    "Chinese (Simplified)": ("ch_sim", "zh-CN"),
    "Chinese (Traditional)": ("ch_tra", "zh-TW"),
}


# ---------------------------------------------------------------------------
# Region selector (drag-select overlay, reused each time "Select Region" is clicked)
# ---------------------------------------------------------------------------
def select_region_on_screen():
    """Blocks until the user drags a rectangle on a full-screen overlay.
    Returns (x1, y1, x2, y2) or None if cancelled."""
    coords = {}

    picker = tk.Toplevel()
    picker.attributes("-fullscreen", True)
    picker.attributes("-alpha", 0.25)
    picker.attributes("-topmost", True)
    picker.config(bg="black", cursor="cross")
    picker.grab_set()

    canvas = tk.Canvas(picker, bg="gray", highlightthickness=0)
    canvas.pack(fill="both", expand=True)

    start = {}
    rect_id = [None]

    def on_press(event):
        start["x"], start["y"] = event.x, event.y
        rect_id[0] = canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="red", width=2
        )

    def on_drag(event):
        canvas.coords(rect_id[0], start["x"], start["y"], event.x, event.y)

    def on_release(event):
        x1, y1 = start["x"], start["y"]
        x2, y2 = event.x, event.y
        coords["region"] = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        picker.destroy()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)

    tk.Label(
        picker, text="Drag to select the region to translate. Esc to cancel.",
        fg="white", bg="black", font=("Segoe UI", 12)
    ).place(relx=0.5, rely=0.02, anchor="n")

    picker.bind("<Escape>", lambda e: picker.destroy())
    picker.wait_window()

    return coords.get("region")


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------
class TranslatorPanel:
    def __init__(self, root):
        self.root = root
        self.root.title("Screen Translator")
        self.root.geometry(self._right_docked_geometry())
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#1e1e1e")

        self.region = None
        self.running = False
        self.worker_thread = None
        self.result_queue = queue.Queue()

        # Cache loaded OCR readers and translators per language so switching
        # back doesn't reload a model you've already used this session.
        self.reader_cache = {}
        self.translator_cache = {}

        self._build_ui()
        self.root.after(100, self._poll_queue)

    def _right_docked_geometry(self):
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = screen_w - PANEL_WIDTH
        return f"{PANEL_WIDTH}x{screen_h}+{x}+0"

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        header = tk.Label(
            self.root, text="Screen Translator", fg="white", bg="#1e1e1e",
            font=("Segoe UI", 13, "bold")
        )
        header.pack(anchor="w", **pad)

        # Language dropdown
        lang_frame = tk.Frame(self.root, bg="#1e1e1e")
        lang_frame.pack(fill="x", **pad)
        tk.Label(
            lang_frame, text="Language:", fg="white", bg="#1e1e1e",
            font=("Segoe UI", 10)
        ).pack(side="left")

        self.language_var = tk.StringVar(value="Korean")
        lang_dropdown = ttk.Combobox(
            lang_frame, textvariable=self.language_var,
            values=list(LANGUAGE_OPTIONS.keys()), state="readonly", width=22
        )
        lang_dropdown.pack(side="left", padx=8)
        lang_dropdown.bind("<<ComboboxSelected>>", self._on_language_change)

        # Region + Start/Stop buttons
        btn_frame = tk.Frame(self.root, bg="#1e1e1e")
        btn_frame.pack(fill="x", **pad)

        self.region_btn = tk.Button(
            btn_frame, text="Select Region", command=self._on_select_region
        )
        self.region_btn.pack(side="left", padx=(0, 6))

        self.start_btn = tk.Button(
            btn_frame, text="Start", command=self._on_toggle_start, state="disabled"
        )
        self.start_btn.pack(side="left")

        # Status line
        self.status_var = tk.StringVar(value="Select a region to begin.")
        tk.Label(
            self.root, textvariable=self.status_var, fg="#aaaaaa", bg="#1e1e1e",
            font=("Segoe UI", 9), wraplength=PANEL_WIDTH - 20, justify="left"
        ).pack(anchor="w", **pad)

        # Translated text feed
        tk.Label(
            self.root, text="Translated text:", fg="white", bg="#1e1e1e",
            font=("Segoe UI", 10, "bold")
        ).pack(anchor="w", padx=10, pady=(10, 0))

        text_frame = tk.Frame(self.root, bg="#1e1e1e")
        text_frame.pack(fill="both", expand=True, padx=10, pady=10)

        scrollbar = tk.Scrollbar(text_frame)
        scrollbar.pack(side="right", fill="y")

        self.text_widget = tk.Text(
            text_frame, wrap="word", bg="#2a2a2a", fg="#00e08a",
            font=("Segoe UI", 12), yscrollcommand=scrollbar.set,
            relief="flat", padx=8, pady=8
        )
        self.text_widget.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.text_widget.yview)

    # ------------------------------------------------------------------
    # UI event handlers
    # ------------------------------------------------------------------
    def _on_select_region(self):
        self.root.withdraw()  # hide panel while selecting so it's not in the way
        region = select_region_on_screen()
        self.root.deiconify()

        if region:
            self.region = region
            self.status_var.set(f"Region set: {region}. Ready to start.")
            self.start_btn.config(state="normal")
        else:
            self.status_var.set("Region selection cancelled.")

    def _on_language_change(self, event=None):
        if self.running:
            self.status_var.set(
                f"Switching to {self.language_var.get()}... (loading model if needed)"
            )

    def _on_toggle_start(self):
        if not self.running:
            self._start()
        else:
            self._stop()

    def _start(self):
        if not self.region:
            self.status_var.set("Select a region first.")
            return
        self.running = True
        self.start_btn.config(text="Stop")
        self.region_btn.config(state="disabled")
        self.status_var.set("Running...")
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()

    def _stop(self):
        self.running = False
        self.start_btn.config(text="Start")
        self.region_btn.config(state="normal")
        self.status_var.set("Stopped.")

    # ------------------------------------------------------------------
    # Background worker: capture -> OCR -> translate -> push to queue
    # ------------------------------------------------------------------
    def _get_reader(self, easyocr_code):
        if easyocr_code not in self.reader_cache:
            self.result_queue.put(("status", f"Loading {easyocr_code} OCR model..."))
            import easyocr
            self.reader_cache[easyocr_code] = easyocr.Reader(
                [easyocr_code, "en"], gpu=False
            )
        return self.reader_cache[easyocr_code]

    def _get_translator(self, translate_code):
        if translate_code not in self.translator_cache:
            self.translator_cache[translate_code] = GoogleTranslator(
                source=translate_code, target=TARGET_LANG
            )
        return self.translator_cache[translate_code]

    def _worker_loop(self):
        sct = mss.mss()
        last_hash = None
        last_lang_name = None
        reader = None
        translator = None

        x1, y1, x2, y2 = self.region
        monitor = {"left": x1, "top": y1, "width": x2 - x1, "height": y2 - y1}

        while self.running:
            lang_name = self.language_var.get()
            if lang_name != last_lang_name:
                easyocr_code, translate_code = LANGUAGE_OPTIONS[lang_name]
                reader = self._get_reader(easyocr_code)
                translator = self._get_translator(translate_code)
                last_lang_name = lang_name
                last_hash = None  # force re-translation after a language switch
                self.result_queue.put(("status", f"Running ({lang_name})..."))

            try:
                shot = sct.grab(monitor)
                img = Image.frombytes("RGB", shot.size, shot.rgb)
                img_np = np.array(img)

                results = reader.readtext(img_np, detail=0)
                combined_text = " ".join(results).strip()

                if combined_text:
                    text_hash = hashlib.md5(combined_text.encode("utf-8")).hexdigest()
                    if text_hash != last_hash:
                        last_hash = text_hash
                        try:
                            translated = translator.translate(combined_text)
                        except Exception as e:
                            translated = f"[translation error: {e}]"
                        self.result_queue.put(("text", translated))
            except Exception as e:
                self.result_queue.put(("status", f"Error: {e}"))

            time.sleep(REFRESH_INTERVAL)

    # ------------------------------------------------------------------
    # Poll the queue from the main thread and update the GUI safely
    # ------------------------------------------------------------------
    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.result_queue.get_nowait()
                if kind == "text":
                    self.text_widget.insert("end", payload + "\n\n")
                    self.text_widget.see("end")
                elif kind == "status":
                    self.status_var.set(payload)
        except queue.Empty:
            pass
        self.root.after(150, self._poll_queue)


if __name__ == "__main__":
    root = tk.Tk()
    app = TranslatorPanel(root)
    root.mainloop()

# ---------------------------------------------------------------------------
# NEXT STEPS
# ---------------------------------------------------------------------------
# 1. Per-line translation instead of one combined block:
#    reader.readtext(img_np, detail=1) returns (bbox, text, confidence).
#    Push each line as its own queue item instead of joining them, and give
#    each its own row in the text widget (or a small floating label placed
#    at the bbox's on-screen coordinates if you want it to look more like a
#    live overlay rather than a feed).
#
# 2. Auto-detect language instead of manual dropdown:
#    Run both a Korean and a Chinese reader on each frame and keep whichever
#    result has the higher average confidence. Roughly doubles OCR time per
#    frame, so only worth it if you're switching languages constantly.
#
# 3. Better OCR accuracy on stylized fonts (games, book covers):
#    Try PaddleOCR (pip install paddleocr paddlepaddle) as a drop-in
#    replacement for EasyOCR -- generally stronger on CJK scripts.
#
# 4. Better translation quality:
#    - DeepL (pip install deepl) for more natural phrasing generally.
#    - Papago (Naver) API specifically tuned for Korean<->English.
#
# 5. Porting off Windows:
#    This version doesn't use any Win32-specific overlay tricks (unlike the
#    earlier click-through overlay version), so it should run on Mac/Linux
#    with Python + Tkinter as-is. The only Windows assumption is screen
#    coordinate handling in `mss`, which works cross-platform already.
#
# 6. Packaging as a standalone .exe:
#    `pip install pyinstaller` then
#    `pyinstaller --onefile --windowed screen_translator.py`