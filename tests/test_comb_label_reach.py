"""AA107 task 2: a filled comb must not hide variant targets below it."""
import pytest
import pdfplumber

from agents.lib import parse_utils as P
from tests.test_identifier_residuals import _prints
from tests.test_identifier_spellings import _cid_pdf, _wide
from tests.test_parse_utils import ROUTING, _ssn
from tests.test_typographic_identifiers import _read


@pytest.mark.parametrize("route", ["extract_pdf_text", "extract_pdf_words", "identity_words", "extract_tables"])
@pytest.mark.parametrize("length", [9, 10, 18])
@pytest.mark.parametrize("spelling", ["wide", "wide comb", "invisible"])
@pytest.mark.parametrize("label", ["Employee's SSN", "SSN or ITIN"])
def test_a_labels_own_comb_cannot_hide_a_variant_two_rows_below(tmp_path, route, length, spelling, label):
    own = ("".join(("000", "11", "2222")) * 2 + "3")[:length]
    target = {"wide": _wide(_ssn("")), "wide comb": " ".join(_wide(_ssn(""))),
              "invisible": "\u200b".join(_ssn(""))}[spelling]
    items = [(36, 700, label + " " + " ".join(own)), (500, 688, "A"),
             (36, 676, target), (280, 676, "850.00")]
    path = _cid_pdf(tmp_path / "column.pdf", mapped=set("".join(t for _, _, t in items)), items=items)
    with pdfplumber.open(path) as document:
        words = document.pages[0].extract_words()
        before = P._governed_reading(words, lambda: None, split=True, measured=True)
        assert before, "fixture must exercise an original positional mask"
        assert before <= P._governed_runs(words), "the added reading withdrew an original mask"
    out = _read(route, path, tmp_path)
    assert not _prints(_ssn(""), out), "the comb on the label row withdrew protection below"
    assert "850.00" in out


@pytest.mark.parametrize("figure", [ROUTING, " - ".join(("125", "50", "2026"))])
@pytest.mark.parametrize("own", [" ".join(_ssn("")), _ssn("-"), _ssn("")])
def test_variant_protection_keeps_a_filled_labels_original_figures(tmp_path, figure, own):
    # The added reading may protect variant targets, but cannot reinterpret
    # an ordinary routing number or an amount under a filled label as an SSN.
    items = [(36, 700, "SSN " + own), (500, 688, "A"),
             (36, 676, figure), (280, 676, "850.00"), (500, 660, _wide("42"))]
    path = _cid_pdf(tmp_path / "figures.pdf", mapped=set("".join(t for _, _, t in items)), items=items)
    for route in ("extract_pdf_text", "extract_pdf_words", "identity_words", "extract_tables"):
        out = _read(route, path, tmp_path)
        assert figure in out and "850.00" in out


@pytest.mark.parametrize("own", [_ssn("-"), _ssn(""), "XXX-XX-1234"])
@pytest.mark.parametrize("spelling", ["wide", "wide comb", "invisible"])
@pytest.mark.parametrize("route", ["extract_pdf_text", "extract_pdf_words", "identity_words", "extract_tables"])
def test_a_plain_or_dashed_value_cannot_close_a_label_to_variant_targets(tmp_path, own, spelling, route):
    target = {"wide": _wide(_ssn("")), "wide comb": " ".join(_wide(_ssn(""))),
              "invisible": "\u200b".join(_ssn(""))}[spelling]
    items = [(36, 700, "SSN " + own), (500, 688, "A"), (36, 676, target), (280, 676, "850.00")]
    path = _cid_pdf(tmp_path / "filled.pdf", mapped=set("".join(t for _, _, t in items)), items=items)
    out = _read(route, path, tmp_path)
    assert not _prints(_ssn(""), out) and "850.00" in out
