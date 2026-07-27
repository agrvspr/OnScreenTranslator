# Screen Translator

Real-time on-screen OCR translator (Korean / Chinese / Japanese ->
English) with a docked side-panel UI, auto-detect with confirmation,
split into Model / View / Controller layers.

## Setup

**Requires Python 3.12 or older** -- PaddleOCR's underlying framework
(PaddlePaddle) does not yet publish wheels for newer Python versions
(3.13/3.14). If your system Python is newer, create a virtual
environment with an older version first, e.g.:

```
py -3.12 -m venv venv
.\venv\Scripts\Activate.ps1
```

Then, with the venv active:

```
pip install paddleocr paddlepaddle mss deep-translator pillow numpy
python main.py
```

First run downloads OCR models per language the first time you use them.
PaddleOCR's models are considerably smaller than the EasyOCR/PyTorch
combination this project used previously.

## How to use

1. Run `main.py`. A panel opens docked to the right side of your screen.
2. Leave the dropdown on **Auto-detect** (the default) to have it figure
   out Korean / Chinese / Japanese on its own, or pick a specific
   language to force it.
3. Click **Select Region** and drag a rectangle over the foreign text on
   your screen (a webnovel, e-reader, game, etc). This also clears any
   previously translated text in the feed, since a new region means a
   fresh reading context.
4. Click **Start**. If Auto-detect is active, a popup will appear the
   first time it identifies a language ("Detected: Korean -- Is this
   correct?") -- click **Confirm** to proceed, or pick a different
   language from the dropdown in the popup and click **Use this** to
   override it. Translation only begins after this is resolved.
5. Translated paragraphs appear in the feed as new text is detected.
6. Switch languages any time from the dropdown -- switching (including
   back to Auto-detect) resets the lock and will show the confirmation
   popup again next time a language is resolved.
7. Click **Stop** to pause, **Select Region** again to re-target a
   different area.

## How auto-detect works

With the dropdown on **Auto-detect**, the app identifies the language by
looking at *which script* the OCR'd text uses, rather than by running all
three languages' recognizers and comparing them (which would be ~3x
slower per frame). The shortcut works because each language has
characters no other one uses:

- **Hangul** (감, 한) -> Korean
- **Hiragana / Katakana** (あ, ア) -> Japanese
- **Han/CJK ideographs only, no Hangul or Kana** -> Chinese

Once it locks onto a language, it shows a confirmation popup before any
translation happens with that language. After you confirm (or override),
it keeps using that reader until you either switch the dropdown away
from Auto-detect and back (or pick a new language manually), or select a
new region -- both reset the lock and will show the confirmation popup
again next time a language is resolved. It does not periodically
re-check on its own while running, so if the on-screen language actually
changes mid-session (e.g. you switch tabs to a different site), toggle
the dropdown to re-trigger detection and confirmation.

Two limitations to know:
- Script detection can't tell **Simplified from Traditional** Chinese
  (they share the same Unicode block), so auto-detect defaults to
  Simplified. If you're reading Traditional, pick it manually from the
  dropdown.
- Detection needs some CJK text on screen to judge from. On a frame with
  no foreign text it keeps whatever it last detected.

The manual dropdown options are always available as an override when
auto-detect guesses wrong.

## Why paragraphs don't repeat as you scroll

Each screen capture is OCR'd and split into paragraphs by vertical
spacing (see `model.group_lines_into_paragraphs`). The model remembers
the content-hash of every paragraph it has already translated
(`TranslationModel.seen_paragraphs`), so if you scroll and the new
capture overlaps 80% with the last one, only the genuinely new
paragraph(s) get translated and appended -- the feed reads like a
continuous scroll of the source text instead of repeating itself.

## File layout (Model / View / Controller)

```
model.py       Pure logic: screen capture, OCR, paragraph grouping,
               dedup memory, translation calls. No Tkinter imports --
               everything here is testable by feeding in an image array
               or plain string, no window required.

view.py        All Tkinter widgets: the docked panel, the drag-to-select
               region picker. Knows nothing about OCR/translation --
               only displays state and forwards user actions to
               whatever callbacks the controller registered.

controller.py  Wires view events (button clicks, dropdown changes) to
               model methods, and owns the background worker thread that
               continuously captures -> extracts new paragraphs ->
               translates -> pushes results back to the main thread via
               a queue (Tkinter widgets must only be touched from the
               main thread, hence the queue).

main.py        Entry point: creates the Tk root, wires up the
               controller, starts the event loop.
```

### Extending each layer

- **Swapping OCR engines again:** only `model.py` changes -- specifically
  `TranslationModel.get_reader` (what loads/returns) and `_run_ocr`
  (how results get normalized into `(bbox, text, conf)` tuples).
  `view.py` and `controller.py` don't need to know or care which engine
  is behind it.
- **New translation engine (e.g. DeepL, Papago):** only
  `TranslationModel.get_translator` / `translate` change.
- **New UI (e.g. per-paragraph floating overlays instead of a feed):**
  only `view.py` changes, plus a small tweak in `controller.py` to call
  a different view method when pushing translated text (e.g.
  `view.show_overlay_at(bbox, text)` instead of `append_translation`).
- **Auto-detect + confirmation state machine** lives in
  `TranslationModel` (`resolve_language`, `confirm_auto_lock`,
  `override_auto_lock`); the popup itself is
  `view.ask_language_confirmation`; the thread coordination (pausing the
  worker until the dialog resolves) is in `controller._worker_loop` /
  `controller._show_language_confirmation`, using
  `controller.confirmation_event`.

## Known limitations

- **Requires Python 3.12 or older** (see Setup). If PaddleOCR's `predict()`
  result format doesn't match what `model._run_ocr` / `_get_result_field`
  expect (the API has changed across PaddleOCR versions), you may see an
  error mentioning `rec_texts`/`rec_polys`/`rec_scores` -- check the exact
  installed `paddleocr` version's docs and adjust the key names in
  `model._run_ocr` if needed.
- Paragraph grouping is a vertical-gap heuristic
  (`model.PARAGRAPH_GAP_MULTIPLIER`) -- tune it if paragraphs are
  merging together (lower it) or splitting apart (raise it) for a
  particular site's layout.
- Free Google Translate via `deep-translator` can rate-limit under
  heavy polling. `controller.REFRESH_INTERVAL = 1.5` seconds is a safe
  default.
- Dedup is exact-content matching -- if OCR reads the same paragraph
  slightly differently between two captures (a misread character), it
  will be treated as new and translated again. Worth revisiting with
  fuzzy matching if it turns out to be a frequent issue in practice.