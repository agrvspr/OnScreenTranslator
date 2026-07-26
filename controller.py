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

from model import TranslationModel, LANGUAGE_OPTIONS, LANGUAGES, capture_region
from view import TranslatorView, select_region_on_screen

# Internal language key -> friendly name, for status messages when
# auto-detect resolves a language (reverse of the LANGUAGES keys).
LANGUAGE_KEY_LABELS = {
    "korean": "Korean",
    "chinese_simplified": "Chinese (Simplified)",
    "chinese_traditional": "Chinese (Traditional)",
    "japanese": "Japanese",
}

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
        # Switching the dropdown (including to/from Auto-detect) resets the
        # auto-detect lock so the next frame re-resolves from scratch.
        self.model.reset_memory()
        if self.running:
            lang_name = self.view.get_selected_language()
            self.view.set_status(f"Switched to {lang_name}.")

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
        # Tracks the last language we reported in the status line, so we
        # only push a status update when it actually changes (avoids
        # spamming the status line every frame in auto-detect mode).
        last_reported_key = None
        on_loading = lambda msg: self.result_queue.put(("status", msg))

        while self.running:
            selected_label = self.view.get_selected_language()
            selected_key = LANGUAGE_OPTIONS[selected_label]  # real key or "auto"

            try:
                img_np = capture_region(self.region)

                # Resolve which concrete language to use this frame. In
                # manual mode this is just the dropdown choice; in auto
                # mode it detects the script (cached between rechecks).
                language_key, was_detected = self.model.resolve_language(
                    img_np, selected_key, on_loading=on_loading
                )

                # Report the active language when it changes.
                if language_key != last_reported_key:
                    label = LANGUAGE_KEY_LABELS.get(language_key, language_key)
                    if was_detected:
                        self.result_queue.put(("status", f"Running -- detected {label}."))
                    else:
                        self.result_queue.put(("status", f"Running ({label})."))
                    last_reported_key = language_key

                new_paragraphs = self.model.extract_new_paragraphs(
                    img_np, language_key, on_loading=on_loading
                )
                for para in new_paragraphs:
                    translated = self.model.translate(para, language_key)
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