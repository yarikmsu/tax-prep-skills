"""AA106: unknown identifier digits refuse; ordinary symbols stay readable."""
import pytest

from tests.test_identifier_spellings import _cid_pdf
from tests.test_parse_utils import MASKED, _ssn
from tests.test_typographic_identifiers import _read

ROUTES = ("extract_pdf_text", "extract_pdf_words", "identity_words", "extract_tables")


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("layout", ["overlay", "letters", "one mapped", "one unmapped", "stack", "nearby digit"])
def test_an_unreadable_identifier_field_never_returns_font_codes(tmp_path, route, layout):
    digits = _ssn("")
    items = [(36, 700, "Your social security number"), (36, 640, "Wages"), (280, 640, "850.00")]
    mapped = set("Your social security number Wages850.") - set(digits)
    if layout == "overlay":
        items.append((42, 700, digits))
    elif layout == "letters":
        items.append((280, 700, digits[:3] + "AB" + digits[3:5] + "CD" + digits[5:]))
        mapped.update("ABCD")
    elif layout == "one mapped":
        items.append((280, 700, digits))
        mapped.add(digits[-1])
    elif layout == "one unmapped":
        items.append((280, 700, digits))
        mapped.update(set(digits) - {digits[-1]})
    elif layout == "nearby digit":
        items.append((280, 700, digits[:4] + "1" + digits[4:]))
        mapped.add("1")
    else:
        items.extend((36, 680 - i * 12, c) for i, c in enumerate(digits))
    path = _cid_pdf(tmp_path / "field.pdf", mapped=mapped, items=items)
    with pytest.raises(ValueError, match="unmapped font") as error:
        _read(route, path, tmp_path)
    assert "(cid:" not in str(error.value) and digits not in str(error.value)


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("layout", ["checkbox", "leader", "bullets", "readable identifier", "separator"])
def test_unmapped_symbols_do_not_turn_a_readable_amount_into_an_identifier(tmp_path, route, layout):
    value = _ssn("-")
    amount = "123456.78"
    items = [(36, 700, "Your social security number"), (280, 700, value),
             (36, 680, "Wages"), (280, 680, amount)]
    mapped = set("".join(t for _, _, t in items))
    symbols = [(270, 680, 0x7f)]
    if layout == "leader":
        symbols = [(100 + i * 5, 680, 0x7f) for i in range(32)]
    elif layout == "bullets":
        symbols = [(30, 640 - i * 12, 0x7f) for i in range(13)]
        items.extend((42, 640 - i * 12, str(i + 1)) for i in range(13))
        mapped.update("0123456789")
    elif layout == "readable identifier":
        symbols = [(345, 700, 0x7f)]
    elif layout == "separator":
        mapped.remove("-")
        symbols = []
    path = _cid_pdf(tmp_path / "readable.pdf", mapped=mapped, items=items, symbols=symbols)
    out = _read(route, path, tmp_path)
    assert amount in out and MASKED in out


@pytest.mark.parametrize("route", ROUTES)
def test_a_readable_ligature_in_the_label_does_not_exempt_an_unknown_digit(tmp_path, route):
    items = [(36, 700, "Recipient's identiﬁcation number"), (280, 700, _ssn("")),
             (36, 680, "Wages"), (280, 680, "850.00")]
    mapped = set("".join(t for _, _, t in items)) - {_ssn("")[-1]}
    path = _cid_pdf(tmp_path / "ligature.pdf", mapped=mapped, items=items)
    with pytest.raises(ValueError, match="unmapped font"):
        _read(route, path, tmp_path)


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("label", ["", "SSN"])
def test_a_tight_delimited_field_with_one_unknown_digit_needs_no_position_label(tmp_path, route, label):
    items = [(36, 700, label + _ssn("-")), (36, 680, "Wages"), (280, 680, "850.00")]
    mapped = set("".join(t for _, _, t in items)) - {_ssn("")[-1]}
    path = _cid_pdf(tmp_path / "tight.pdf", mapped=mapped, items=items)
    with pytest.raises(ValueError, match="unmapped font"):
        _read(route, path, tmp_path)


@pytest.mark.parametrize("route", ROUTES)
def test_a_leader_before_a_separate_amount_cannot_borrow_its_readability(tmp_path, route):
    items = [(36, 700, "SSN " + _ssn("-")), (36, 680, "Wages"),
             (100, 680, " ".join(chr(0x7f) for _ in range(18)) + " 850.00")]
    mapped = set("".join(t for _, _, t in items)) - {chr(0x7f)}
    path = _cid_pdf(tmp_path / "leader.pdf", mapped=mapped, items=items)
    out = _read(route, path, tmp_path)
    assert "850.00" in out and MASKED in out


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("mark", ["-", ""])
def test_an_identifier_field_of_one_repeated_unknown_digit_still_refuses(tmp_path, route, mark):
    value = mark.join(("0" * 3, "0" * 2, "0" * 4))
    items = [(36, 700, "SSN"), (280, 700, value), (36, 680, "Wages"), (280, 680, "850.00")]
    mapped = set("".join(t for _, _, t in items)) - {"0"}
    path = _cid_pdf(tmp_path / "repeated.pdf", mapped=mapped, items=items)
    with pytest.raises(ValueError, match="unmapped font"):
        _read(route, path, tmp_path)


@pytest.mark.parametrize("route", ROUTES)
def test_an_unmapped_leader_under_a_header_cannot_borrow_a_caption_digit(tmp_path, route):
    items = [(36, 700, "SSN"), (36, 680, "2 " + " ".join(chr(0x7f) for _ in range(10))),
             (280, 680, "123456.78")]
    mapped = set("".join(t for _, _, t in items)) - {chr(0x7f)}
    path = _cid_pdf(tmp_path / "caption.pdf", mapped=mapped, items=items)
    assert "123456.78" in _read(route, path, tmp_path)


@pytest.mark.parametrize("route", ROUTES)
def test_bullets_and_counts_on_the_same_rows_are_not_one_stacked_identifier(tmp_path, route):
    items = [(36, 700, "SSN"), (280, 680, "123456.78")]
    items.extend((38, 680 - i * 12, str(i + 1)) for i in range(8))
    symbols = [(36, 680 - i * 12, 0x7f) for i in range(8)]
    mapped = set("".join(t for _, _, t in items))
    path = _cid_pdf(tmp_path / "counts.pdf", mapped=mapped, items=items, symbols=symbols)
    assert "123456.78" in _read(route, path, tmp_path)


@pytest.mark.parametrize("route", ROUTES)
def test_a_checkbox_beside_a_readable_comb_does_not_make_the_identifier_unreadable(tmp_path, route):
    items = [(36, 700, "SSN " + chr(0x7f) + " " + " ".join(_ssn(""))),
             (36, 680, "Wages"), (280, 680, "123456.78")]
    mapped = set("".join(t for _, _, t in items)) - {chr(0x7f)}
    path = _cid_pdf(tmp_path / "readable-comb.pdf", mapped=mapped, items=items)
    out = _read(route, path, tmp_path)
    assert MASKED in out and "123456.78" in out and "(cid:127)" in out


@pytest.mark.parametrize("route", ROUTES)
def test_counterfactual_digits_cannot_become_positional_evidence_for_routing_boxes(tmp_path, route):
    items = [(180, 700, "SSN"), (280, 700, "0" * 9), (36, 680, "Routing"),
             (280, 680, chr(0x7f) + " " + " ".join("0" * 8)),
             (380, 680, "Wages"), (430, 680, "123456.78")]
    mapped = set("".join(t for _, _, t in items)) - {chr(0x7f)}
    path = _cid_pdf(tmp_path / "routing.pdf", mapped=mapped, items=items)
    out = _read(route, path, tmp_path)
    assert "123456.78" in out and "(cid:127)" in out


@pytest.mark.parametrize("route", ROUTES)
def test_a_missing_space_mapping_in_a_label_cannot_exempt_an_unknown_digit(tmp_path, route):
    items = [(36, 700, "Your social security number"), (280, 700, _ssn("")),
             (36, 680, "Wages"), (280, 680, "850.00")]
    mapped = set("".join(t for _, _, t in items)) - {" ", _ssn("")[-1]}
    path = _cid_pdf(tmp_path / "label-space.pdf", mapped=mapped, items=items)
    with pytest.raises(ValueError, match="unmapped font"):
        _read(route, path, tmp_path)


@pytest.mark.parametrize("route", ROUTES)
def test_an_unreadable_variant_below_a_filled_label_still_refuses(tmp_path, route):
    items = [(36, 700, "SSN " + "0" * 9), (500, 688, "A"), (36, 676, _ssn("")), (280, 676, "850.00")]
    mapped = set("".join(t for _, _, t in items)) - {_ssn("")[-1]}
    path = _cid_pdf(tmp_path / "filled-label.pdf", mapped=mapped, items=items)
    with pytest.raises(ValueError, match="unmapped font"):
        _read(route, path, tmp_path)
