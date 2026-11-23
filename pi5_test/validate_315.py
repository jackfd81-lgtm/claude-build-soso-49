"""Deterministic structural grading for Precision-315. Run with Python 3."""
import argparse
import json
import sys
import unicodedata
from pathlib import Path

def words(text):
    text = unicodedata.normalize("NFC", text)
    # Punctuation alone contributes no word. Internal apostrophes and hyphens
    # stay in the same whitespace-delimited word.
    return sum(any(c.isalpha() or c.isdecimal() for c in chunk) for chunk in text.split())

def grade(fixture, response):
    errors = []
    if not isinstance(response, dict) or response.get("id") != fixture["id"]:
        return ["Wrong or missing challenge ID."]
    rows = response.get("positions")
    if not isinstance(rows, list) or len(rows) != 315:
        return ["Return exactly 315 position objects."]
    for expected, actual in zip(fixture["positions"], rows):
        i = expected["position"]
        if not isinstance(actual, dict) or type(actual.get("position")) is not int or actual["position"] != i:
            errors.append(f"{i}: missing, duplicate or out of order.")
            continue
        text = actual.get("text")
        if not isinstance(text, str) or not 7 <= words(text) <= 15 or len(text.splitlines()) != 1:
            errors.append(f"{i}: expected one line of 7–15 words.")
        if expected["kind"] == "ANCHOR" and text != expected["text"]:
            errors.append(f"{i}: locked text changed.")
        if actual.get("language") != expected["language"]:
            errors.append(f"{i}: wrong or missing language tag.")
    return errors

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("response", type=Path)
    parser.add_argument("--fixture", type=Path, default=Path(__file__).with_name("PRECISION315_BLIND_V2.json"))
    args = parser.parse_args()
    try:
        fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
        response = json.loads(args.response.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        parser.exit(2, f"{exc}\n")
    errors = grade(fixture, response)
    print(json.dumps({"structural_status": "FAIL" if errors else "PASS",
                      "errors": errors, "content_quality": "REVIEW_REQUIRED"}, ensure_ascii=False, indent=2))
    return bool(errors)

if __name__ == "__main__":
    sys.exit(main())
