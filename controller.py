"""
Controller layer.

Wires the view's user actions (button clicks, dropdown changes) to the
model's logic (OCR, translation, dedup), and owns the background worker
thread that continuously captures the selected screen region, extracts
new paragraphs, and translates them -- pushing results back to the main
thread through a queue so Tkinter widgets are only ever touched from the
main thread.
"""

import threading
import queue
import time

from model import TranslationModel, LANGUAGE_OPTIONS, capture_region
from view import TranslatorView, select_region_on_screen

REFRESH_INTERVAL = 1.5  # seconds between screen captures


class TranslatorController:
    def __init__(self, root):
        self.model = TranslationModel()
        self.view = TranslatorView(root, LANGUAGE_OPTIONS)

        self.view.on_select_region = self.handle_select_region
        self.view.on_toggle_start = self.handle_toggle_start
        self.view.on_language_change = self.handle_language_change

        self.region = None
        self.running = False
        self.worker_thread = None
        self.result_queue = queue.Queue()

        root.after(150, self._poll_queue)

    # ------------------------------------------------------------------
    # View event handlers
    # ------------------------------------------------------------------
    def handle_select_region(self):
        self.view.hide()  # get the panel out of the way while selecting
        region = select_region_on_screen()
        self.view.show()

        if region:
            self.region = region
            self.model.reset_memory()  # new region = fresh reading context
            self.view.set_status(f"Region set: {region}. Ready to start.")
            self.view.set_start_button_enabled(True)
        else:
            self.view.set_status("Region selection cancelled.")

    def handle_language_change(self):
        if self.running:
            lang_name = self.view.get_selected_language()
            self.view.set_status(f"Switching to {lang_name}... (loading model if needed)")

    def handle_toggle_start(self):
        if not self.running:
            self._start()
        else:
            self._stop()

    def _start(self):
        if not self.region:
            self.view.set_status("Select a region first.")
            return
        self.running = True
        self.view.set_running_ui_state(True)
        self.view.set_status("Running...")
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()

    def _stop(self):
        self.running = False
        self.view.set_running_ui_state(False)
        self.view.set_status("Stopped.")

    # ------------------------------------------------------------------
    # Background worker: capture -> extract new paragraphs -> translate
    # ------------------------------------------------------------------
    def _worker_loop(self):
        last_lang_name = None
        easyocr_code = translate_code = None

        while self.running:
            lang_name = self.view.get_selected_language()
            if lang_name != last_lang_name:
                easyocr_code, translate_code = LANGUAGE_OPTIONS[lang_name]
                # Loading the reader here (rather than lazily inside the
                # extract call) lets us surface a "loading model" status
                # message the first time a language is used.
                self.model.get_reader(
                    easyocr_code,
                    on_loading=lambda msg: self.result_queue.put(("status", msg))
                )
                last_lang_name = lang_name
                self.result_queue.put(("status", f"Running ({lang_name})..."))

            try:
                img_np = capture_region(self.region)
                new_paragraphs = self.model.extract_new_paragraphs(img_np, easyocr_code)
                for para in new_paragraphs:
                    translated = self.model.translate(para, translate_code)
                    self.result_queue.put(("text", translated))
            except Exception as e:
                self.result_queue.put(("status", f"Error: {e}"))

            time.sleep(REFRESH_INTERVAL)

    # ------------------------------------------------------------------
    # Poll the queue from the main thread and update the view safely
    # ------------------------------------------------------------------
    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.result_queue.get_nowait()
                if kind == "text":
                    self.view.append_translation(payload)
                elif kind == "status":
                    self.view.set_status(payload)
        except queue.Empty:
            pass
        self.view.root.after(150, self._poll_queue)