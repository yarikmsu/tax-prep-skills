"""AA109: digit widths cannot establish an unknown glyph's meaning."""
import pytest

from agents.lib import parse_utils as P
from tests.test_identifier_spellings import _cid_pdf
from tests.test_parse_utils import _ssn
from tests.test_typographic_identifiers import _read
from tests.test_unreadable_identifier_fields import ROUTES


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("layout", ["beside", "below"])
@pytest.mark.parametrize("digit", [0, 4, 8])
@pytest.mark.parametrize("in_forms", [False, True], ids=["page resources", "direct form resources"])
def test_same_named_font_resources_can_give_one_cid_mixed_roles(tmp_path, route, layout, digit, in_forms):
    import pdfplumber

    digits = "".join(("912", "34", "5678"))
    value = "-".join((digits[:3], digits[3:5], digits[5:]))
    x, y = (280, 700) if layout == "beside" else (36, 680)
    items = [(36, 700, "SSN"), *((x + i * 5.4, y, c) for i, c in enumerate(value))]
    alternate = [i for i, (_, _, text) in enumerate(items) if text == "-"]
    path = _cid_pdf(tmp_path / "colliding-fonts.pdf", mapped=set("SSN" + digits) - {digits[digit]},
                    items=items, alternate_items=alternate, alternate_cids={"-": ord(digits[digit])}, in_forms=in_forms)
    with pdfplumber.open(path) as document:
        chars = document.pages[0].chars
        unreadable = [c for c in chars if c["text"].startswith("(cid:")]
        assert len(unreadable) == 3 and len({(c["fontname"], c["text"]) for c in unreadable}) == 1
        assert len(document.pages[0].page_obj.resources["Font"]) == 2
        classified = P._font_resource_chars(document.pages[0], lambda: None)
        assert len({c["_font_resource"] for c in classified}) == (len(items) if in_forms else 2)
    with pytest.raises(ValueError, match="unmapped font") as error:
        _read(route, path, tmp_path)
    assert "(cid:" not in str(error.value) and digits not in str(error.value)


def test_font_resource_classification_copies_keep_source_objects_and_check_the_deadline(tmp_path):
    import pdfplumber

    path = _cid_pdf(tmp_path / "resources.pdf", mapped={"A"}, items=[(36, 700, "A-")])
    with pdfplumber.open(path) as document:
        page = document.pages[0]
        original = [dict(c) for c in page.chars]
        classified = P._font_resource_chars(page, lambda: None)
        assert [{k: v for k, v in c.items() if k != "_font_resource"} for c in classified] == original
        assert all("_font_resource" in c for c in classified)
        assert page.chars == original

        def expired():
            raise P.ParseBudgetError("synthetic deadline")

        with pytest.raises(P.ParseBudgetError, match="synthetic deadline"):
            P._font_resource_chars(page, expired)


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("width", [150, 300, 1000, 1400])
@pytest.mark.parametrize("mark", ["", "-"])
def test_an_unknown_digit_outside_every_readable_digits_width_still_refuses(tmp_path, route, width, mark):
    digits = _ssn("")
    items = [(36, 700, "SSN"), (280, 700, _ssn(mark)), (36, 680, "Wages"), (280, 680, "850.00")]
    mapped = set("".join(t for _, _, t in items)) - {digits[-1]}
    path = _cid_pdf(tmp_path / "proportional.pdf", mapped=mapped, items=items, widths={digits[-1]: width})
    with pytest.raises(ValueError, match="unmapped font"):
        _read(route, path, tmp_path)


@pytest.mark.parametrize("route", ROUTES)
def test_a_wide_unknown_before_a_date_can_be_the_first_identifier_digit(tmp_path, route):
    # With no mapping, this could be an em dash before a date or the first
    # digit of an identifier. A TIN label makes returning font codes unsafe.
    items = [(36, 700, "RECIPIENT'S TIN"), (280, 700, "\u2014" + "/".join(("12", "31", "2025"))),
             (36, 680, "Wages"), (280, 680, "850.00")]
    mapped = set("".join(t for _, _, t in items)) - {"\u2014"}
    path = _cid_pdf(tmp_path / "ambiguous.pdf", mapped=mapped, items=items, widths={"\u2014": 1000})
    with pytest.raises(ValueError, match="unmapped font"):
        _read(route, path, tmp_path)


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("scale", [40, 180])
def test_an_unknown_digit_may_have_its_own_horizontal_scaling(tmp_path, route, scale):
    digits = _ssn("")
    items = [(36, 700, "SSN"), *((280 + i * 7, 700, c) for i, c in enumerate(digits)),
             (36, 680, "Wages"), (280, 680, "850.00")]
    mapped = set("".join(t for _, _, t in items)) - {digits[-1]}
    path = _cid_pdf(tmp_path / "scaled.pdf", mapped=mapped, items=items, horizontal_scales={9: scale})
    with pytest.raises(ValueError, match="unmapped font"):
        _read(route, path, tmp_path)


@pytest.mark.parametrize("route", ROUTES)
def test_omb_metadata_keeps_its_meaning_when_its_caption_period_is_unmapped(tmp_path, route):
    value = "-".join(("1545", "0029"))
    items = [(36, 700, "Employee's SSN OMB No."), (280, 700, value)]
    mapped = set("".join(t for _, _, t in items)) - {"-", "."}
    path = _cid_pdf(tmp_path / "metadata.pdf", mapped=mapped, items=items)
    out = _read(route, path, tmp_path)
    assert "1545" in out and "0029" in out


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("gap", [90, 270])
@pytest.mark.parametrize("omb_below", [False, True])
@pytest.mark.parametrize("caption_period", [False, True])
def test_omb_metadata_cannot_claim_a_separately_labelled_unreadable_identifier(
        tmp_path, route, gap, omb_below, caption_period):
    # An invalid synthetic area, with a unique fifth digit to unmap. Its
    # four/unknown/four shape could otherwise be mistaken for OMB metadata.
    value = "".join(("912", "34", "5678"))
    items = [(36 + gap, 720, "SSN"), (36, 700, "OMB No."), (36 + gap, 700, value)]
    if omb_below:
        items.append((36, 680, "-".join(("1545", "0029"))))
    mapped = set("".join(t for _, _, t in items)) - {value[4]}
    if caption_period:
        mapped.discard(".")
    path = _cid_pdf(tmp_path / "separate-fields.pdf", mapped=mapped, items=items)
    with pytest.raises(ValueError, match="unmapped font") as error:
        _read(route, path, tmp_path)
    assert "(cid:" not in str(error.value) and value not in str(error.value)


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("prefix, suffix", [("", "."), ("(", ")"), ("(", ").")])
@pytest.mark.parametrize("layout", ["beside", "below"])
def test_multiple_unknown_separator_kinds_cannot_hide_an_unknown_identifier_digit(
        tmp_path, route, prefix, suffix, layout):
    # Neither the digit reading nor any single-kind separator reading can
    # recover nine digits. Punctuation kinds must also be considered together.
    value = prefix + _ssn("-") + suffix
    items = [(36, 700, "SSN"), (280 if layout == "beside" else 36,
              700 if layout == "beside" else 680, value), (36, 640, "Wages"), (280, 640, "850.00")]
    mapped = set("".join(t for _, _, t in items)) - set("-" + prefix + suffix + _ssn("")[-1])
    path = _cid_pdf(tmp_path / "separators.pdf", mapped=mapped, items=items)
    with pytest.raises(ValueError, match="unmapped font") as error:
        _read(route, path, tmp_path)
    assert "(cid:" not in str(error.value) and _ssn("") not in str(error.value)


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("caption", ["SSN", "Other"])
def test_many_unknown_kinds_refuse_before_enumerating_separator_subsets(tmp_path, monkeypatch, route, caption):
    # Mapped letters interrupt the consecutive-unknown guard, but cannot
    # justify exploring more than a billion punctuation hypotheses.
    value = "".join(chr(0xe000 + i) + "A" for i in range(30))
    items = [(36, 700, caption), (140, 700, value)]
    path = _cid_pdf(tmp_path / "many-kinds.pdf", mapped=set(caption + "A"), items=items)
    combinations = P.combinations
    count = 0

    def bounded(*args):
        nonlocal count
        for choice in combinations(*args):
            count += 1
            assert count <= 256, "separator hypothesis work exceeded its budget"
            yield choice

    monkeypatch.setattr(P, "combinations", bounded)
    with pytest.raises(ValueError, match="unmapped font") as error:
        _read(route, path, tmp_path)
    assert count == 0, "oversized hypothesis sets must be refused before enumeration"
    assert "(cid:" not in str(error.value)


@pytest.mark.parametrize("rows, protect, distinct", [(1, False, False), (2, False, False), (2, True, False),
                                                     (2, False, True)])
def test_separator_hypothesis_budget_is_shared_across_page_fields(tmp_path, monkeypatch, rows, protect, distinct):
    import pdfplumber

    # Eight kinds admit 255 nonempty subsets. One field fits the page budget.
    # A field repeating the same kinds reuses those hypotheses; one with new
    # kinds must not restart the budget. Already protected fields need no
    # hypotheses and must not consume that budget.
    def value(row):
        base = 0xe000 + (8 * row if distinct else 0)
        return "".join(chr(base + i) + "A" for i in range(8)) + "0" * 9

    path = _cid_pdf(tmp_path / "repeated-kinds.pdf", mapped=set("A0"),
                    items=[(140, 700 - i * 40, value(i)) for i in range(rows)])
    combinations = P.combinations
    count = 0

    def bounded(*args):
        nonlocal count
        for choice in combinations(*args):
            count += 1
            assert count <= 256, "separator hypothesis work exceeded its page budget"
            yield choice

    monkeypatch.setattr(P, "combinations", bounded)
    with pdfplumber.open(path) as document:
        chars = document.pages[0].chars
        unknown = {id(c) for c in chars if c["text"].startswith("(cid:")}
        if distinct:
            with pytest.raises(ValueError, match="unmapped font"):
                P._unreadable_field_candidates(chars, unknown, set(), lambda: None)
        else:
            P._unreadable_field_candidates(chars, unknown, unknown if protect else set(), lambda: None)
    assert count == (0 if protect else 255)


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("layout", ["one item", "two items"])
def test_omb_metadata_cannot_exempt_an_identifier_under_its_shared_ssn_heading(tmp_path, route, layout):
    # Only an IRS control number (agency 1545) is metadata. The shared SSN
    # heading's own value has the same four/unknown/four shape.
    value = "".join(("912", "34", "5678"))
    caption = "Employee's SSN OMB No."
    items = ([(36, 700, f"{caption} {value}")] if layout == "one item"
             else [(36, 700, caption), (280, 700, value)])
    path = _cid_pdf(tmp_path / "shared-heading.pdf", mapped=set(caption + value) - {value[4]}, items=items)
    with pytest.raises(ValueError, match="unmapped font") as error:
        _read(route, path, tmp_path)
    assert "(cid:" not in str(error.value) and value not in str(error.value)


@pytest.mark.parametrize("route", ROUTES)
def test_repeated_amount_rows_with_unmapped_punctuation_stay_readable(tmp_path, route):
    # Forty rows repeat the same three unknown kinds. They share one set of
    # separator hypotheses instead of each spending the page budget again.
    amounts = " ".join(("$1,234.56", "$7,890.12", "$3,456.78"))
    items = [item for i in range(40) for item in ((36, 760 - i * 15, "Interest"), (120, 760 - i * 15, amounts))]
    path = _cid_pdf(tmp_path / "statement.pdf", mapped=set("Interest" + amounts) - set("$,."), items=items)
    assert "Interest" in _read(route, path, tmp_path)
