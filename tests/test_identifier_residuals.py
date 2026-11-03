"""AA105: the spellings left by AA103. Synthetic only."""
import re

import pytest

from agents.lib.secure_fs import mask_runs, mask_ssn, mask_table, redact
from tests.test_identifier_spellings import _cid_pdf, _comb
from tests.test_parse_utils import MASKED, _ssn
from tests.test_typographic_identifiers import _read


@pytest.mark.parametrize("style", ["soft", "dot", "slash"])
def test_remaining_comb_spellings_mask_only_in_identifier_fields(style):
    value = {"soft": "\u00ad".join(_ssn("")), "dot": _comb("."), "slash": _comb("/")}[style]
    for prefix in ("SSN: ", "SSN\n"):
        assert mask_ssn(prefix + value) == prefix + MASKED
    assert mask_table([["SSN", value]]) == [["SSN", MASKED]]
    assert mask_table([["SSN"], [value]]) == [["SSN"], [MASKED]]
    assert mask_runs(value, {_ssn("")}) == MASKED
    assert mask_ssn("Routing " + value) == "Routing " + value


def test_diagnostic_soft_hyphens_can_be_both_marks_and_invisibles():
    value = "\u00ad".join("\u00ad".join(part) for part in ("000", "00", "6789"))
    mixed = "0\u00ad00\u00ad00-6789"
    assert redact(value) == "[REDACTED]"
    assert redact(mixed) == "[REDACTED]"


def test_unicode_16_digits_mask_even_with_the_floor_interpreters_database(monkeypatch):
    from agents.lib import secure_fs as s
    decimal, category = s.unicodedata.decimal, s.unicodedata.category
    added = {chr(base + n) for base in s._ID_DECIMAL_16 for n in range(10)}
    monkeypatch.setattr(s.unicodedata, "decimal", lambda char, default=None: default if char in added else decimal(char, default))
    monkeypatch.setattr(s.unicodedata, "category", lambda char: "Cn" if char in added else category(char))
    for base in s._ID_DECIMAL_16:
        value = "".join(chr(base + int(c)) for c in _ssn(""))
        assert mask_ssn("SSN: " + value) == "SSN: " + MASKED
        assert redact(value) == "[REDACTED]"


@pytest.mark.parametrize("route", ["extract_pdf_text", "extract_pdf_words", "identity_words", "extract_tables"])
@pytest.mark.parametrize("label", ["Your social security number", "Reference"])
@pytest.mark.parametrize("separators", [False, True])
def test_wide_unmapped_identifier_groups_still_refuse(tmp_path, route, label, separators):
    value = _ssn("")
    groups = (value[:3], value[3:5], value[5:])
    items = [(36, 700, label), (36, 680, "Wages"), (280, 680, "850.00")]
    items += [(x, 700, group) for x, group in zip((280, 340, 400), groups)]
    if separators:
        items += [(320, 700, "-"), (380, 700, "-")]
    mapped = set(label + " Wages850.-") - set(value)
    path = _cid_pdf(tmp_path / "wide-unreadable-field.pdf", mapped=mapped, items=items)
    with pytest.raises(ValueError, match="unmapped font") as error:
        _read(route, path, tmp_path)
    assert "(cid:" not in str(error.value)


def _prints(value, text):
    from agents.lib.secure_fs import identifier_text
    digits = r"\D{0,4}".join(value)
    return re.search(digits, identifier_text(text).text) is not None


@pytest.mark.parametrize("route", ["extract_pdf_text", "extract_pdf_words", "identity_words", "extract_tables"])
@pytest.mark.parametrize("layout", ["soft beside comb", "soft beside plain comb", "same cell", "soft value carries"])
def test_a_soft_hyphen_run_cannot_unmask_a_neighbouring_identifier(tmp_path, route, layout):
    """A comb class mixing soft hyphens with spaces read six soft-hyphen digits
    and a spaced SSN's area as one comb, and the rest printed; and a label whose
    soft-hyphen value counted as adjacent stopped governing the row below."""
    value = _ssn("")
    comb = " - ".join(" ".join(part) for part in (value[:3], value[3:5], value[5:]))
    label = "Your social security number"
    if layout == "soft beside comb":
        items = [(36, 700, label), (200, 700, "­".join("123456") + " " + comb)]
    elif layout == "soft beside plain comb":
        items = [(36, 700, label + " " + "\u00ad".join("123456") + " " + " ".join(value))]
    elif layout == "same cell":
        items = [(36, 700, "SSN " + "­".join("123456") + " " + comb)]
    else:
        other = "".join(("000", "11", "2222"))
        wide = "".join(chr(0xFF10 + int(c)) for c in value)
        items = [(36, 700, "Employee's SSN " + "­".join(other)), (36, 676, wide)]
    items += [(36, 640, "Wages"), (200, 640, "850.00")]
    path = _cid_pdf(tmp_path / "soft.pdf", mapped=set("".join(t for _, _, t in items)), items=items)
    out = _read(route, path, tmp_path)
    assert not _prints(value, out), "a soft-hyphen run unmasked the identifier beside it"
    # The page was read, not refused; the wage row lies outside the table grid.
    assert route == "extract_tables" or "850.00" in out


@pytest.mark.parametrize("digits", [_ssn("") + "123456", "123456" + _ssn("")])
def test_a_soft_hyphen_comb_running_into_more_digits_still_masks_under_a_label(digits):
    """Read as invisibles, the soft hyphens join this into one fifteen-digit
    number, which no nine-digit reading takes; the comb reading must."""
    value = "­".join(digits)
    assert not _prints(_ssn(""), mask_ssn("SSN:­" + value + "  "))
    assert not _prints(_ssn(""), "\n".join("".join(row) for row in mask_table([["SSN", value]])))
