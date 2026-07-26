"""
View layer.

All Tkinter widgets live here. The view knows nothing about OCR or
translation -- it only displays state (status text, translated text feed)
and forwards user actions (button clicks, dropdown changes) to whatever
callback functions the controller has registered on it.
"""

import tkinter as tk
from tkinter import ttk

PANEL_WIDTH = 380


def select_region_on_screen():
    """Blocks until the user drags a rectangle on a full-screen overlay.
    Returns (x1, y1, x2, y2) screen coordinates, or None if cancelled.

    This is a standalone modal utility (not part of the main panel), so it
    stays a free function rather than a TranslatorView method."""
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


class TranslatorView:
    """The docked side panel: language dropdown, region/start controls,
    status line, and a scrolling feed of translated paragraphs.

    The controller sets `on_select_region`, `on_toggle_start`, and
    `on_language_change` to its own handler methods after construction."""

    def __init__(self, root, language_options):
        self.root = root
        self.language_options = language_options

        self.on_select_region = None
        self.on_toggle_start = None
        self.on_language_change = None

        self.root.title("Screen Translator")
        self.root.geometry(self._right_docked_geometry())
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#1e1e1e")

        self._build_ui()

    def _right_docked_geometry(self):
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = screen_w - PANEL_WIDTH
        return f"{PANEL_WIDTH}x{screen_h}+{x}+0"

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        tk.Label(
            self.root, text="Screen Translator", fg="white", bg="#1e1e1e",
            font=("Segoe UI", 13, "bold")
        ).pack(anchor="w", **pad)

        # Language dropdown
        lang_frame = tk.Frame(self.root, bg="#1e1e1e")
        lang_frame.pack(fill="x", **pad)
        tk.Label(
            lang_frame, text="Language:", fg="white", bg="#1e1e1e",
            font=("Segoe UI", 10)
        ).pack(side="left")

        self.language_var = tk.StringVar(value=next(iter(self.language_options)))
        lang_dropdown = ttk.Combobox(
            lang_frame, textvariable=self.language_var,
            values=list(self.language_options.keys()), state="readonly", width=22
        )
        lang_dropdown.pack(side="left", padx=8)
        lang_dropdown.bind(
            "<<ComboboxSelected>>",
            lambda e: self.on_language_change() if self.on_language_change else None
        )

        # Region + Start/Stop buttons
        btn_frame = tk.Frame(self.root, bg="#1e1e1e")
        btn_frame.pack(fill="x", **pad)

        self.region_btn = tk.Button(
            btn_frame, text="Select Region",
            command=lambda: self.on_select_region() if self.on_select_region else None
        )
        self.region_btn.pack(side="left", padx=(0, 6))

        self.start_btn = tk.Button(
            btn_frame, text="Start", state="disabled",
            command=lambda: self.on_toggle_start() if self.on_toggle_start else None
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
    # Public API the controller uses to update what's on screen
    # ------------------------------------------------------------------
    def get_selected_language(self):
        return self.language_var.get()

    def set_status(self, text):
        self.status_var.set(text)

    def append_translation(self, text):
        self.text_widget.insert("end", text + "\n\n")
        self.text_widget.see("end")

    def set_start_button_enabled(self, enabled):
        self.start_btn.config(state="normal" if enabled else "disabled")

    def set_running_ui_state(self, running):
        self.start_btn.config(text="Stop" if running else "Start")
        self.region_btn.config(state="disabled" if running else "normal")

    def hide(self):
        self.root.withdraw()

    def show(self):
        self.root.deiconify()