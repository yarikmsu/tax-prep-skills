"""Rebuild official_tables_2025.json: TY2025 IRS/FTB forms, filled and flattened.

    python3 tests/fixtures/prior_return/build_official_tables_2025.py DIR

DIR holds the blank PDFs named in SOURCES, downloaded from the URLs beside them.
Each synthetic value is typed into the form's own field, named below as the
form names it, and the page is flattened, the way a viewer or tax software that
fills the official form prints it. The text is pdfplumber's default extraction
and the words its default `extract_words`, as `parse_utils` reads them. Filling
needs pypdf, which the toolkit does not use: install it in a scratch
environment. No real return or identity data is involved.
"""
import io
import json
import sys
from pathlib import Path

import pdfplumber
from pypdf import PdfReader, PdfWriter

SOURCES = {
    "f1040--2025.pdf": "https://www.irs.gov/pub/irs-prior/f1040--2025.pdf",
    "f1116sb.pdf": "https://www.irs.gov/pub/irs-pdf/f1116sb.pdf",
    "2025-540-p.pdf": "https://www.ftb.ca.gov/forms/2025/2025-540-p.pdf",
    "f8995a--2025.pdf": "https://www.irs.gov/pub/irs-prior/f8995a--2025.pdf",
    "f8995ac.pdf": "https://www.irs.gov/pub/irs-pdf/f8995ac.pdf",
}
# Schedule B (Form 1116), passive category, read down each column:
#   (i) 2015: 300 carried in, 300 expires.    (iv) 2018: 200 carried in and used.
#   (x) 2022: 400 carried in, 100 used, 300 out.   (xii) 2024: 600 carried in and out.
#   (xiii) 2025: 700 generated and carried out.    Columns (vii)/(viii) and (xiv) total them.
# Page 1 prints columns (i)-(vii) as f1_10+ (line 1), f1_71+ (line 3), f1_78+ (4), f1_85+ (5);
# page 2 prints (viii)-(xiv) as f2_01+ (line 1), f2_62+ (3), f2_69+ (4), f2_76+ (5), f2_83+ (6)
# and f2_97+ (8). Line 8 column (i) is a pre-printed -0-.
SCHEDULE_B_1 = {
    "f1_01[0]": "25",
    "topmostSubform[0].Page1[0].CheckboxC-D_ReadOrder[0].c1_01[0]": "/3",
    "f1_10[0]": "300", "f1_13[0]": "200", "f1_16[0]": "500",
    "f1_71[0]": "300", "f1_74[0]": "200", "f1_77[0]": "500",
    "f1_81[0]": "-200", "f1_84[0]": "-200",
    "f1_85[0]": "-300", "f1_91[0]": "-300",
    "f1_112[0]": "0",
}
SCHEDULE_B_2 = {
    "f2_01[0]": "500", "f2_03[0]": "400", "f2_05[0]": "600", "f2_07[0]": "1,500",
    "f2_62[0]": "500", "f2_64[0]": "400", "f2_66[0]": "600", "f2_68[0]": "1,500",
    "f2_69[0]": "-200", "f2_71[0]": "-100", "f2_75[0]": "-300",
    "f2_76[0]": "-300", "f2_82[0]": "-300",
    "f2_88[0]": "700", "f2_89[0]": "700",
    "f2_97[0]": "0", "f2_99[0]": "300", "f2_101[0]": "600", "f2_102[0]": "700", "f2_103[0]": "1,600",
}
# Schedule P (540) Part III. Side 3: line 6 = 3001 code, 3003-3006 columns (a)-(d);
# line 10 (code 188) = 3025-3028. Side 4: line 16 = 4001 code, 4003-4006; line 20
# (187, no carryover) = 4025-4027; line 21 (242) = 4028-4031; line 22 = 4032;
# line 23 (180) = 4033-4036. The solar credit's B2 carryover (500) is line 23's (a).
SCHEDULE_P_3 = {
    "540P-3001": "183", "540P-3002": "Research",
    "540P-3003": "1,500", "540P-3004": "1,000", "540P-3005": "4,000", "540P-3006": "500",
    "540P-3025": "2,000", "540P-3026": "300", "540P-3027": "3,700", "540P-3028": "1,700",
}
SCHEDULE_P_4 = {
    "540P-4001": "180", "540P-4002": "Solar Energy",
    "540P-4003": "900", "540P-4004": "400", "540P-4005": "2,000", "540P-4006": "500",
    "540P-4025": "800", "540P-4026": "800", "540P-4027": "1,200",
    "540P-4028": "5,000", "540P-4029": "1,200", "540P-4030": "0", "540P-4031": "3,800",
    "540P-4032": "100",
    "540P-4033": "500", "540P-4034": "100", "540P-4035": "0", "540P-4036": "400",
}
# Schedule C (Form 8995-A): line 1 businesses, then lines 2-6 (f1_15-f1_19).
# Losses -3,000 and the 2,000 carried in net against 1,000 of income: 4,000 out.
SCHEDULE_C = {
    "f1_03[0]": "Business A", "f1_04[0]": "-3,000",
    "f1_07[0]": "Business B", "f1_08[0]": "1,000", "f1_09[0]": "-1,000",
    "f1_15[0]": "2,000", "f1_16[0]": "5,000", "f1_17[0]": "1,000", "f1_18[0]": "1,000",
    "f1_19[0]": "4,000",
}
# Form 1040 page 1: married filing jointly, and official_2025.json's page-1 amounts.
FORM_1040 = {
    "topmostSubform[0].Page1[0].Checkbox_ReadOrder[0].c1_8[1]": "/2",
    "f1_70[0]": "(3,000)", "f1_73[0]": "50,000", "f1_74[0]": "0", "f1_75[0]": "50,000",
}
PAGES = {
    "1040_page1": ("f1040--2025.pdf", 0, FORM_1040),
    "1116B_page1": ("f1116sb.pdf", 0, SCHEDULE_B_1),
    "1116B_page2": ("f1116sb.pdf", 1, SCHEDULE_B_2),
    "P540_side1": ("2025-540-p.pdf", 0, {}),
    "P540_side2": ("2025-540-p.pdf", 1, {}),
    "P540_side3": ("2025-540-p.pdf", 2, SCHEDULE_P_3),
    "P540_side4": ("2025-540-p.pdf", 3, SCHEDULE_P_4),
    "8995A_page1": ("f8995a--2025.pdf", 0, {}),
    "8995C": ("f8995ac.pdf", 0, SCHEDULE_C),
}


def flattened(path: Path, index: int, values: dict) -> bytes:
    writer = PdfWriter()
    writer.append(PdfReader(path), pages=[index])
    if values:
        writer.update_page_form_field_values(writer.pages[0], values, auto_regenerate=False, flatten=True)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def main(directory: str) -> None:
    pages, cited = {}, {}
    for name, (source, index, values) in PAGES.items():
        with pdfplumber.open(io.BytesIO(flattened(Path(directory) / source, index, values))) as pdf:
            page = pdf.pages[0]
            words = [[w["text"], round(w["x0"], 1), round(w["x1"], 1), round(w["top"], 1), round(w["bottom"], 1)]
                     for w in page.extract_words()]
            pages[name] = {"text": page.extract_text() or "", "words": words}
        cited[name] = f"{SOURCES[source]} page {index + 1}"
    out = Path(__file__).with_name("official_tables_2025.json")
    document = {"_sources": cited, "pages": pages}
    out.write_text(json.dumps(document, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1])
