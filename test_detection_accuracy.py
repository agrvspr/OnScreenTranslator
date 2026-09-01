"""
Measures real accuracy of TranslationModel._detect_language against a
labeled set of test images -- run this to get an actual number for your
resume/portfolio, instead of guessing.

SETUP
-----
1. Create a folder structure like this next to this script:

    test_images/
        korean/
            sample1.png
            sample2.png
            ...
        chinese_simplified/
            sample1.png
            ...
        chinese_traditional/
            sample1.png
            ...
        japanese/
            sample1.png
            ...

2. Each image should be a screenshot crop containing ONLY text in that
   folder's language -- e.g. crop a paragraph from a Korean webnovel and
   save it under korean/, crop something from a Chinese site under
   chinese_simplified/, etc. Aim for at least 10-15 images per language
   for a number that means something; more is better.

   Realistic, varied sources matter more than volume: mix webnovels,
   games, articles, chat apps, etc. rather than 15 screenshots of the
   same page, since that would just be testing "can it read THIS font"
   rather than "does detection generalize."

3. Run:
    python test_detection_accuracy.py
   or, to test a different folder (e.g. a separate harder-cases set):
    python test_detection_accuracy.py test_images_hard

RUNNING A SEPARATE "HARDER CASES" SET
--------------------------------------
Clean webnovel screenshots are close to a best-case scenario for OCR:
large readable text, consistent fonts, high contrast. If you want a more
convincing (and honest) accuracy claim, build a SECOND folder --
e.g. test_images_hard/ with the same korean/chinese_simplified/japanese
subfolder structure -- containing harder examples: small game UI text,
stylized/decorative fonts, low-resolution crops, screenshots mixing
English with the target language, or sparse text (just 1-2 short lines).
Run this script against each folder separately and report both numbers
rather than averaging them together -- "100% on clean webnovel text,
88% on harder cases like small UI text and mixed-language screenshots"
is a more credible, specific claim than a single blended number.

RESULTS
-------
Prints a confusion matrix, per-language accuracy, and overall accuracy,
and saves a detailed CSV (detection_results.csv) with every individual
prediction so you can inspect exactly which images it got wrong and why
-- useful for both debugging and for talking through specific examples
in an interview.
"""

import csv
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

from model import TranslationModel, LANGUAGES

TEST_IMAGES_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("test_images")
RESULTS_CSV = Path(f"detection_results_{TEST_IMAGES_DIR.name}.csv")


def load_test_cases():
    """Returns a list of (image_path, true_label) tuples by scanning
    test_images/<language_key>/*.png (or .jpg)."""
    cases = []
    if not TEST_IMAGES_DIR.exists():
        print(f"ERROR: {TEST_IMAGES_DIR} does not exist. See the setup "
              f"instructions at the top of this file.")
        return cases

    for lang_key in LANGUAGES:
        lang_dir = TEST_IMAGES_DIR / lang_key
        if not lang_dir.exists():
            continue
        for img_path in sorted(lang_dir.glob("*")):
            if img_path.suffix.lower() in (".png", ".jpg", ".jpeg"):
                cases.append((img_path, lang_key))
    return cases


def run_evaluation():
    cases = load_test_cases()
    if not cases:
        print("No test images found -- nothing to evaluate.")
        return

    print(f"Found {len(cases)} test images. Loading OCR models "
          f"(this will load Korean, Chinese, and Japanese readers)...")

    model = TranslationModel()
    results = []  # (image_path, true_label, predicted_label, correct)

    for i, (img_path, true_label) in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {img_path} (expected: {true_label})")
        img = Image.open(img_path).convert("RGB")
        img_np = np.array(img)

        predicted = model._detect_language(img_np, on_loading=lambda msg: None)
        correct = (predicted == true_label)
        results.append((str(img_path), true_label, predicted, correct))
        print(f"    -> predicted: {predicted}  {'OK' if correct else 'WRONG'}")

    _print_report(results)
    _write_csv(results)


def _print_report(results):
    total = len(results)
    correct_count = sum(1 for _, _, _, correct in results if correct)
    overall_accuracy = correct_count / total * 100

    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"Overall accuracy: {correct_count}/{total} ({overall_accuracy:.1f}%)\n")

    # Per-language accuracy
    per_lang_total = defaultdict(int)
    per_lang_correct = defaultdict(int)
    for _path, true_label, _pred, correct in results:
        per_lang_total[true_label] += 1
        if correct:
            per_lang_correct[true_label] += 1

    print("Per-language accuracy:")
    for lang_key in LANGUAGES:
        if per_lang_total[lang_key] == 0:
            continue
        acc = per_lang_correct[lang_key] / per_lang_total[lang_key] * 100
        print(f"  {lang_key:22} {per_lang_correct[lang_key]}/{per_lang_total[lang_key]} ({acc:.1f}%)")

    # Confusion matrix: what did we predict when we got it wrong?
    print("\nMisclassifications (true -> predicted):")
    confusion = defaultdict(int)
    for _path, true_label, pred, correct in results:
        if not correct:
            confusion[(true_label, pred)] += 1
    if not confusion:
        print("  None -- every image was classified correctly.")
    else:
        for (true_label, pred), count in sorted(confusion.items(), key=lambda x: -x[1]):
            print(f"  {true_label} -> {pred}: {count} time(s)")

    print(f"\nFull per-image results written to {RESULTS_CSV}")


def _write_csv(results):
    with open(RESULTS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["image_path", "true_label", "predicted_label", "correct"])
        for row in results:
            writer.writerow(row)


if __name__ == "__main__":
    run_evaluation()