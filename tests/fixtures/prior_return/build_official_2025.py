"""Rebuild official_2025.json from the blank TY2025 IRS/FTB PDFs.

    python3 tests/fixtures/prior_return/build_official_2025.py DIR

DIR holds the PDFs named in SOURCES, downloaded from the URLs beside them.
The text is pdfplumber's default extraction, as `parse_utils.extract_pdf_text`
reads it. Each synthetic amount goes where a filled form prints it: after the
row's right-hand line label, inside a pre-printed `( )`, or before FTB's
pre-printed cents. No real return or identity data is involved.
"""
import json
import re
import sys
from pathlib import Path

import pdfplumber

SOURCES = {
    "f1040--2025.pdf": ("https://www.irs.gov/pub/irs-prior/f1040--2025.pdf", (0, 1)),
    "f1040sd--2025.pdf": ("https://www.irs.gov/pub/irs-prior/f1040sd--2025.pdf", (0, 1)),
    "f8606--2025.pdf": ("https://www.irs.gov/pub/irs-prior/f8606--2025.pdf", (0, 1)),
    "f8801--2025.pdf": ("https://www.irs.gov/pub/irs-prior/f8801--2025.pdf", (0, 1)),
    "f8995--2025.pdf": ("https://www.irs.gov/pub/irs-prior/f8995--2025.pdf", (0,)),
    "2025-540.pdf": ("https://www.ftb.ca.gov/forms/2025/2025-540.pdf", (2, 3)),
    "2025-3510.pdf": ("https://www.ftb.ca.gov/forms/2025/2025-3510.pdf", (0,)),
}
# 1040-SR prints Form 1040's lines and right-hand labels; the rest are out of
# import scope. Tests swap them in.
ALTERNATES = {
    "1040-SR": {"f1040s--2025.pdf": ("https://www.irs.gov/pub/irs-prior/f1040s--2025.pdf", (1, 2))},
    "1040-NR": {"f1040nr--2025.pdf": ("https://www.irs.gov/pub/irs-prior/f1040nr--2025.pdf", (0,))},
    "1040-X": {"f1040x.pdf": ("https://www.irs.gov/pub/irs-pdf/f1040x.pdf", (0,))},
    "540NR": {"2025-540nr.pdf": ("https://www.ftb.ca.gov/forms/2025/2025-540nr.pdf", (0,))},
}
# (file, page within that file) -> {line: printed amount}
VALUES = {
    ("f1040--2025.pdf", 0): {"7a": "(3,000)", "9": "50,000", "10": "0", "11a": "50,000"},
    ("f1040--2025.pdf", 1): {"11b": "50,000", "12e": "20,000", "13a": "0", "13b": "0", "14": "20,000",
                             "15": "30,000", "24": "4,000", "34": "1,000", "35a": "600", "36": "400"},
    ("f1040sd--2025.pdf", 0): {"7": "(5,000)", "15": "(7,000)"},
    ("f1040sd--2025.pdf", 1): {"16": "(12,000)", "21": "3,000"},
    ("f8606--2025.pdf", 0): {"1": "2,000", "2": "8,000", "3": "10,000", "13": "1,000", "14": "9,000"},
    ("f8801--2025.pdf", 1): {"21": "8,000", "25": "2,000", "26": "6,000"},
    ("f8995--2025.pdf", 0): {"2": "(2,000)", "3": "1,000", "6": "(400)", "7": "100", "16": "3,000", "17": "500"},
    ("2025-540.pdf", 2): {"97": "700"},
    ("2025-540.pdf", 3): {"98": "200", "99": "500"},
    ("2025-3510.pdf", 0): {"32": "2,000", "38": "300", "39": "1,700"},
    ("f1040s--2025.pdf", 1): {"7a": "(3,000)", "9": "50,000", "10": "0", "11a": "50,000", "11b": "50,000",
                              "12e": "20,000", "13a": "0", "13b": "0", "14": "20,000", "15": "30,000",
                              "24": "4,000"},
    ("f1040s--2025.pdf", 2): {"34": "1,000", "35a": "600", "36": "400"},
}


def fill(text: str, line: str, amount: str) -> str:
    """Put the amount in the one row ending with the line's right-hand label."""
    rows = text.split("\n")
    # A box label follows a dot leader or bullet; prose ending "lines 21" is not one.
    label = re.compile(rf"[.\u2022]\s*{re.escape(line)}(?P<box>\s+\( \))?(?P<cents>\s+\.?00)?$")
    hits = [i for i, row in enumerate(rows) if label.search(row)]
    if len(hits) != 1:
        raise SystemExit(f"line {line}: expected one labelled amount row, found {len(hits)}")
    row = rows[hits[0]]
    match = label.search(row)
    if match["box"]:
        rows[hits[0]] = f"{row[:match.start('box')]} ( {amount} )"
    elif match["cents"]:
        rows[hits[0]] = f"{row[:match.start('cents')]} {amount}{match['cents']}"
    else:
        rows[hits[0]] = f"{row} {amount}"
    return "\n".join(rows)


def read(directory: str, sources: dict) -> tuple[dict, dict]:
    pages, cited = {}, {}
    for name, (url, wanted) in sources.items():
        with pdfplumber.open(Path(directory) / name) as pdf:
            for index in wanted:
                text = pdf.pages[index].extract_text() or ""
                for line, amount in VALUES.get((name, index), {}).items():
                    text = fill(text, line, amount)
                cited[str(len(pages))] = f"{url} page {index + 1}"
                pages[str(len(pages))] = text
    return pages, cited


def main(directory: str) -> None:
    pages, cited = read(directory, SOURCES)
    alternates = {}
    for form, sources in ALTERNATES.items():
        alternates[form], more = read(directory, sources)
        cited[form] = list(more.values())
    out = Path(__file__).with_name("official_2025.json")
    document = {"_sources": cited, "pages": pages, "alternates": alternates}
    out.write_text(json.dumps(document, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1])
