# Screen Translator

Real-time on-screen OCR translator (Korean / Chinese -> English) with a
docked side-panel UI, split into Model / View / Controller layers.

## Setup

```
pip install easyocr mss deep-translator pillow numpy
python main.py
```

First run downloads OCR models per language the first time you use them
(~50-100MB each) and installs PyTorch as a dependency of EasyOCR
(1-2GB), so the first launch takes a few minutes.

## How to use

1. Run `main.py`. A panel opens docked to the right side of your screen.
2. Leave the dropdown on **Auto-detect** (the default) to have it figure
   out Korean / Chinese / Japanese on its own, or pick a specific
   language to force it.
3. Click **Select Region** and drag a rectangle over the foreign text on
   your screen (a webnovel, e-reader, game, etc).
4. Click **Start**. Translated paragraphs appear in the feed as new text
   is detected.
5. Switch languages any time from the dropdown -- the model reloads the
   right OCR reader (a few seconds the first time each language is used
   in a session).
6. Click **Stop** to pause, **Select Region** again to re-target a
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

Once it locks onto a language it keeps using that one reader until you
either switch the dropdown away from Auto-detect and back (or pick a
new language manually), or select a new region -- both reset the lock
and trigger a fresh detection on the next frame. It does not
periodically re-check on its own while running, so if the on-screen
language actually changes mid-session (e.g. you switch tabs to a
different site), toggle the dropdown to re-trigger detection.

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

- **New OCR engine (e.g. PaddleOCR):** only `model.py` changes --
  swap what `TranslationModel.get_reader` returns and how
  `extract_new_paragraphs` calls it. `view.py` and `controller.py`
  don't need to know or care.
- **New translation engine (e.g. DeepL, Papago):** only
  `TranslationModel.get_translator` / `translate` change.
- **New UI (e.g. per-paragraph floating overlays instead of a feed):**
  only `view.py` changes, plus a small tweak in `controller.py` to call
  a different view method when pushing translated text (e.g.
  `view.show_overlay_at(bbox, text)` instead of `append_translation`).
- **Auto language detection:** would live mostly in `model.py` (run two
  readers, compare confidence) with a small controller change to stop
  relying on the view's language dropdown as the source of truth.

## Known limitations

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