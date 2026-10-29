"""Rebuild identifier_layouts.json from the blank official PDFs.

    python3 tests/fixtures/prior_return/build_identifier_layouts.py DIR

DIR holds the PDFs named in RECORDS, downloaded from the URLs beside them.
Words are pdfplumber's default `extract_words`, as `parse_utils` reads a
page; fields are the named text fields' rectangles. `fields` are where a
test types an SSN, `typed` what else the filer types; no real return or
identity data is involved.
"""
import json
import sys
from pathlib import Path

import pdfplumber
from pdfminer.pdftypes import resolve1
from pdfminer.utils import decode_text

# (file, URL, zero-based page, SSN fields, {field: typed text})
RECORDS = [
    ("f4506c.pdf", "https://www.irs.gov/pub/irs-pdf/f4506c.pdf", 0,
     ["first_ssn[0]", "second_ssn[0]"], {}),
    ("it213_fill_in.pdf", "https://www.tax.ny.gov/pdf/current_forms/it/it213_fill_in.pdf", 0,
     [f"Page 1 Zone {n}" for n in range(75, 81)], {}),
    ("f1095a--2025.pdf", "https://www.irs.gov/pub/irs-prior/f1095a--2025.pdf", 1,
     ["f1_17[0]", "f1_22[0]", "f1_27[0]", "f1_32[0]", "f1_37[0]"], {}),
    ("f8332.pdf", "https://www.irs.gov/pub/irs-pdf/f8332.pdf", 0,
     ["f1_5[0]", "f1_8[0]", "f1_11[0]"], {}),
    ("f1040sei--2025.pdf", "https://www.irs.gov/pub/irs-prior/f1040sei--2025.pdf", 0,
     ["f1_06[0]", "f1_07[0]", "f1_08[0]"], {}),
    # Part III: the first two covered individuals have no SSN, only a date of
    # birth, so the first SSN prints three rows under "(b) SSN or other TIN".
    ("f1095c--2025.pdf", "https://www.irs.gov/pub/irs-prior/f1095c--2025.pdf", 2,
     ["f3_69[0]", "f3_74[0]"],
     {"f3_56[0]": "ALEX", "f3_58[0]": "DOE", "f3_60[0]": "01/15/2020",
      "f3_61[0]": "SAM", "f3_63[0]": "DOE", "f3_65[0]": "02/20/2022",
      "f3_66[0]": "JO", "f3_68[0]": "DOE", "f3_71[0]": "PAT", "f3_73[0]": "DOE"}),
    # Part I: an escrow company remits, its FEIN typed as the form prints it,
    # so the individual remitter's "SSN or ITIN" row is empty; Part II is the
    # seller.
    ("2025-593.pdf", "https://www.ftb.ca.gov/forms/2025/2025-593.pdf", 0,
     ["593 Form 1024"],
     {"593 Form 1006": "ACME ESCROW INC", "593 Form 1010": "00-0001003", "593 Form 1021": "JANE", "1023": "SELLER"}),
]


def _name(data) -> str:
    for _ in range(8):
        data = resolve1(data)
        if "T" in data:
            value = resolve1(data["T"])
            return decode_text(value) if isinstance(value, bytes) else str(value)
        data = data.get("Parent")
    return ""


def _fields(page) -> dict[str, list]:
    out = {}
    for annot in page.annots or []:
        name = _name(annot.get("data") or {})
        out.setdefault(name, [name, *(round(annot[k], 3) for k in ("x0", "top", "x1", "bottom"))])
    return out


def _line(value) -> str:
    return json.dumps(value)


def main(folder: str) -> None:
    blocks = []
    for file, url, page_no, ssn, typed in RECORDS:
        with pdfplumber.open(Path(folder) / file) as pdf:
            page = pdf.pages[page_no]
            words = [[w["text"], *(round(w[k], 3) for k in ("x0", "top", "x1", "bottom"))] for w in page.extract_words()]
            fields = _fields(page)
        lines = [f'    "source": {_line(url)},', f'    "page": {page_no},', '    "words": [',
                 ",\n".join(f"      {_line(w)}" for w in words), "    ],",
                 f'    "fields": {_line([fields[n] for n in ssn])}']
        if typed:
            lines[-1] += ","
            lines.append(f'    "typed": {_line([[text, *fields[n][1:]] for n, text in typed.items()])}')
        blocks.append("  {\n" + "\n".join(lines) + "\n  }")
    out = Path(__file__).with_name("identifier_layouts.json")
    out.write_text("[\n" + ",\n".join(blocks) + "\n]\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1])
