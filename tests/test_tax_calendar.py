"""Tests for agents/lib/tax_calendar.py — quarterly reminder .ics generator."""
import datetime
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib import tax_calendar as cal


# ---------------------------------------------------------------------------
# 1040-ES events
# ---------------------------------------------------------------------------

def test_events_1040es_returns_four_quarters():
    events = cal.events_1040es(2026)
    assert [e.quarter_label for e in events] == ["Q1", "Q2", "Q3", "Q4"]


def test_events_1040es_uses_statutory_dates():
    events = cal.events_1040es(2026)
    dates = [e.date for e in events]
    assert dates == [
        datetime.date(2026, 4, 15),
        datetime.date(2026, 6, 15),
        datetime.date(2026, 9, 15),
        datetime.date(2027, 1, 15),  # Q4 due Jan 15 of NEXT year
    ]


def test_events_1040es_all_categorized_federal_estimated_tax():
    for e in cal.events_1040es(2026):
        assert e.category == "federal_estimated_tax"


def test_events_1040es_uids_are_stable_and_unique():
    events = cal.events_1040es(2026)
    uids = [e.uid for e in events]
    assert len(set(uids)) == 4
    assert events == cal.events_1040es(2026)  # determinism


# ---------------------------------------------------------------------------
# FBAR events
# ---------------------------------------------------------------------------

def test_events_fbar_returns_apr_and_oct_in_year_after_tax_year():
    # FBAR for TY2026 is reported in 2027.
    events = cal.events_fbar(2026)
    dates = [e.date for e in events]
    assert dates == [datetime.date(2027, 4, 15), datetime.date(2027, 10, 15)]


def test_events_fbar_categorized():
    for e in cal.events_fbar(2026):
        assert e.category == "fbar"


def test_events_fbar_oct_summary_mentions_auto_extension():
    events = cal.events_fbar(2026)
    assert "extension" in events[1].summary.lower() or "extension" in events[1].description.lower()


# ---------------------------------------------------------------------------
# RMD events
# ---------------------------------------------------------------------------

def test_events_rmd_emits_dec_31_for_primary_subject_to_rmd():
    events = cal.events_rmd(2026, primary_subject_to_rmd=True, spouse_subject_to_rmd=False)
    assert len(events) == 1
    assert events[0].date == datetime.date(2026, 12, 31)
    assert events[0].category == "rmd"


def test_events_rmd_emits_for_both_spouses():
    events = cal.events_rmd(2026, primary_subject_to_rmd=True, spouse_subject_to_rmd=True)
    assert len(events) == 2
    # Both Dec 31, but distinct UIDs / descriptions
    assert {e.date for e in events} == {datetime.date(2026, 12, 31)}
    assert len({e.uid for e in events}) == 2


def test_events_rmd_empty_when_neither_age_73():
    assert cal.events_rmd(2026, primary_subject_to_rmd=False, spouse_subject_to_rmd=False) == []


# ---------------------------------------------------------------------------
# State PTET events
# ---------------------------------------------------------------------------

def test_events_ptet_ca_emits_june_prepayment_and_march_election():
    events = cal.events_ptet(2026, state="CA", has_k1_income=True)
    dates = sorted(e.date for e in events)
    # CA PTET: June 15 of tax year (prepayment), March 15 of following year (election + balance)
    assert datetime.date(2026, 6, 15) in dates
    assert datetime.date(2027, 3, 15) in dates


def test_events_ptet_ny_emits_election_plus_three_estimates_plus_return():
    """NY PTET: election + Q1 share March 15; Q2/Q3/Q4 estimates on
    June 15, September 15, December 15 of the tax year (NY-specific —
    distinct from the federal Q1-Q4 schedule); annual return + final
    payment on March 15 of the following year (per NY Tax Department)."""
    events = cal.events_ptet(2026, state="NY", has_k1_income=True)
    dates = sorted(e.date for e in events)
    # L1: §7503 weekend rollover — statutory Mar 15 2026 is a Sunday → Mar 16.
    assert dates == [
        datetime.date(2026, 3, 16),   # election + Q1 (Mar 15 = Sunday)
        datetime.date(2026, 6, 15),   # Q2 estimate
        datetime.date(2026, 9, 15),   # Q3 estimate
        datetime.date(2026, 12, 15),  # Q4 estimate (NY-specific — NOT Jan 15)
        datetime.date(2027, 3, 15),   # annual return + final payment
    ]


def test_events_ptet_nj_emits_four_estimates_plus_election():
    """NJ BAIT PTE-150 quarterly estimates on Apr 15, Jun 15, Sep 15 of
    tax_year + Jan 15 of tax_year+1 (same as federal individual Q1-Q4
    schedule); election deadline March 15 of tax_year+1 with the
    timely-filed PTE-100."""
    events = cal.events_ptet(2026, state="NJ", has_k1_income=True)
    dates = sorted(e.date for e in events)
    assert dates == [
        datetime.date(2026, 4, 15),   # Q1 estimate
        datetime.date(2026, 6, 15),   # Q2 estimate
        datetime.date(2026, 9, 15),   # Q3 estimate
        datetime.date(2027, 1, 15),   # Q4 estimate (in tax_year+1)
        datetime.date(2027, 3, 15),   # election + balance with PTE-100
    ]


def test_events_ptet_ny_estimates_distinct_uids():
    events = cal.events_ptet(2026, state="NY", has_k1_income=True)
    uids = [e.uid for e in events]
    assert len(set(uids)) == 5


def test_events_ptet_nj_estimates_distinct_uids():
    events = cal.events_ptet(2026, state="NJ", has_k1_income=True)
    uids = [e.uid for e in events]
    assert len(set(uids)) == 5


def test_events_ptet_empty_without_k1_income():
    assert cal.events_ptet(2026, state="CA", has_k1_income=False) == []


def test_events_ptet_empty_for_unsupported_state():
    assert cal.events_ptet(2026, state="TX", has_k1_income=True) == []
    assert cal.events_ptet(2026, state="WA", has_k1_income=True) == []


def test_events_ptet_state_case_insensitive():
    assert cal.events_ptet(2026, state="ca", has_k1_income=True) != []
    assert cal.events_ptet(2026, state="Ca", has_k1_income=True) != []


# ---------------------------------------------------------------------------
# build_events: orchestration from taxpayer profile
# ---------------------------------------------------------------------------

def _profile(**overrides):
    base = {
        "tax_year": 2026,
        "filing_status": "Single",
        "address": {"state": "TX"},
        "flags": {
            "has_foreign_accounts": False,
            "has_k1_income": False,
            "subject_to_rmd_primary": False,
            "subject_to_rmd_spouse": False,
        },
    }
    base.update(overrides)
    return base


def test_build_events_always_includes_1040es():
    events = cal.build_events(_profile(), 2026)
    quarters = [e for e in events if e.category == "federal_estimated_tax"]
    assert len(quarters) == 4


def test_build_events_skips_fbar_when_no_foreign_accounts():
    events = cal.build_events(_profile(), 2026)
    assert all(e.category != "fbar" for e in events)


def test_build_events_includes_fbar_when_foreign_accounts():
    p = _profile()
    p["flags"]["has_foreign_accounts"] = True
    events = cal.build_events(p, 2026)
    assert sum(1 for e in events if e.category == "fbar") == 2


def test_build_events_skips_rmd_when_under_age():
    events = cal.build_events(_profile(), 2026)
    assert all(e.category != "rmd" for e in events)


def test_build_events_includes_rmd_when_age_73_primary():
    p = _profile()
    p["flags"]["subject_to_rmd_primary"] = True
    events = cal.build_events(p, 2026)
    assert sum(1 for e in events if e.category == "rmd") == 1


def test_build_events_includes_ptet_only_for_ca_ny_nj_with_k1():
    p = _profile()
    p["flags"]["has_k1_income"] = True
    p["flags"]["k1_states"] = ["CA"]
    events = cal.build_events(p, 2026)
    ptet_events = [e for e in events if e.category == "ptet"]
    assert len(ptet_events) == 2  # CA: June 15 prepayment + March 15 balance


def test_build_events_orders_chronologically():
    p = _profile()
    p["flags"]["has_foreign_accounts"] = True
    p["flags"]["subject_to_rmd_primary"] = True
    p["address"]["state"] = "CA"
    p["flags"]["has_k1_income"] = True
    events = cal.build_events(p, 2026)
    dates = [e.date for e in events]
    assert dates == sorted(dates)


def test_build_events_handles_missing_flags_block():
    # A truncated profile shouldn't crash — just emits 1040-ES.
    minimal = {"tax_year": 2026, "address": {"state": "TX"}}
    events = cal.build_events(minimal, 2026)
    assert len(events) == 4
    assert all(e.category == "federal_estimated_tax" for e in events)


def test_build_events_rejects_tax_year_mismatch():
    p = _profile()
    p["tax_year"] = 2025
    with pytest.raises(cal.CalendarError):
        cal.build_events(p, 2026)


def test_build_events_no_year_in_profile_is_ok():
    p = _profile()
    del p["tax_year"]
    events = cal.build_events(p, 2026)
    assert len(events) == 4


# ---------------------------------------------------------------------------
# ICS serialization (RFC 5545)
# ---------------------------------------------------------------------------

def test_to_ics_has_required_envelope():
    ics = cal.to_ics(cal.events_1040es(2026))
    assert ics.startswith("BEGIN:VCALENDAR")
    assert "VERSION:2.0" in ics
    assert "PRODID:" in ics
    assert ics.rstrip().endswith("END:VCALENDAR")


def test_to_ics_each_event_has_required_fields():
    events = cal.events_1040es(2026)
    ics = cal.to_ics(events)
    # One VEVENT block per event
    assert ics.count("BEGIN:VEVENT") == len(events)
    assert ics.count("END:VEVENT") == len(events)
    for e in events:
        assert f"UID:{e.uid}" in ics
        assert f"SUMMARY:{e.summary}" in ics


def test_to_ics_uses_all_day_value_date_format():
    ics = cal.to_ics(cal.events_1040es(2026))
    # All-day events use DTSTART;VALUE=DATE:YYYYMMDD (no T or Z)
    assert "DTSTART;VALUE=DATE:20260415" in ics
    assert "DTSTART;VALUE=DATE:20260615" in ics
    assert "DTSTART;VALUE=DATE:20260915" in ics
    assert "DTSTART;VALUE=DATE:20270115" in ics  # Q4 in next year


def test_to_ics_includes_valarm_reminder():
    ics = cal.to_ics(cal.events_1040es(2026))
    # Each event gets a 7-day-before reminder
    assert "BEGIN:VALARM" in ics
    assert "TRIGGER:-P7D" in ics


def test_to_ics_escapes_commas_and_semicolons_in_description():
    e = cal.CalendarEvent(
        uid="test-1",
        summary="Test, event",
        date=datetime.date(2026, 4, 15),
        description="Pay this; or else, you owe a penalty",
        category="federal_estimated_tax",
        quarter_label="Q1",
    )
    ics = cal.to_ics([e])
    # Commas and semicolons in TEXT properties must be backslash-escaped per RFC 5545
    assert "Test\\, event" in ics
    assert "Pay this\\; or else\\, you owe a penalty" in ics


def test_to_ics_lines_use_crlf():
    ics = cal.to_ics(cal.events_1040es(2026))
    # RFC 5545 mandates CRLF line endings
    assert "\r\n" in ics
    assert ics.count("\r\n") > ics.count("\n\n")  # not just doubled bare LFs


def test_to_ics_handles_empty_event_list():
    ics = cal.to_ics([])
    assert "BEGIN:VCALENDAR" in ics
    assert "END:VCALENDAR" in ics
    assert "BEGIN:VEVENT" not in ics


def test_to_ics_uids_stable_across_invocations():
    a = cal.to_ics(cal.events_1040es(2026))
    b = cal.to_ics(cal.events_1040es(2026))
    assert a == b


# ---------------------------------------------------------------------------
# CLI / write_calendar
# ---------------------------------------------------------------------------

def test_write_calendar_writes_ics_under_workspace(tmp_path):
    profile_path = tmp_path / "taxpayer_profile.json"
    p = _profile()
    p["flags"]["has_foreign_accounts"] = True
    profile_path.write_text(json.dumps(p))

    out = cal.write_calendar(profile_path, tmp_path, tax_year=2026)
    assert out.exists()
    assert out.parent == tmp_path
    assert out.name == "tax_calendar_2026.ics"
    content = out.read_text()
    assert "BEGIN:VCALENDAR" in content
    # Exact count: 4 ES + 2 FBAR (statutory + extended) = 6 events
    assert content.count("BEGIN:VEVENT") == 6
    assert "CATEGORIES:FBAR" in content


def test_write_calendar_rejects_path_outside_workspace(tmp_path):
    other = tmp_path / "other_dir"
    other.mkdir()
    profile_path = other / "taxpayer_profile.json"
    profile_path.write_text(json.dumps(_profile()))

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(cal.CalendarError):
        cal.write_calendar(profile_path, workspace, tax_year=2026)


def test_write_calendar_sets_owner_only_permissions(tmp_path):
    profile_path = tmp_path / "taxpayer_profile.json"
    profile_path.write_text(json.dumps(_profile()))
    out = cal.write_calendar(profile_path, tmp_path, tax_year=2026)
    mode = out.stat().st_mode & 0o777
    # PII (last4 SSN doesn't appear, but state + flag combo is still sensitive)
    assert mode == 0o600


def test_main_writes_calendar(tmp_path):
    profile_path = tmp_path / "taxpayer_profile.json"
    profile_path.write_text(json.dumps(_profile()))
    rc = cal._main(["--profile", str(profile_path),
                    "--workspace", str(tmp_path), "--year", "2026"])
    assert rc == 0
    assert (tmp_path / "tax_calendar_2026.ics").exists()


def test_main_rejects_invalid_year(tmp_path):
    profile_path = tmp_path / "taxpayer_profile.json"
    profile_path.write_text(json.dumps(_profile()))
    rc = cal._main(["--profile", str(profile_path),
                    "--workspace", str(tmp_path), "--year", "abc"])
    assert rc == 1


# ---------------------------------------------------------------------------
# Code-review fixes (PR #19 review round)
# ---------------------------------------------------------------------------

# --- C1: UTF-8 line folding must preserve multi-byte chars at split boundary ---

def test_fold_line_preserves_multibyte_char_at_75_byte_boundary():
    # Construct a line where a 2-byte UTF-8 char (é = 0xC3 0xA9) straddles
    # the 75-octet split. 74 ASCII chars + é = byte 75 is the leading byte
    # of é; old impl would discard the trailing continuation byte.
    prefix = "A" * 74
    line = prefix + "é" + "B" * 20
    folded = cal._fold_line(line)
    # Reconstructing the logical line: continuation lines start with " "
    # so we drop the "\r\n " join between physical lines.
    reconstructed = folded.replace("\r\n ", "")
    assert reconstructed == line, (
        f"Expected '{line}' preserved across fold; got '{reconstructed}'")


def test_fold_line_preserves_3_byte_char_at_boundary():
    # Em-dash (—) is U+2014, 3 bytes in UTF-8 (0xE2 0x80 0x94).
    prefix = "A" * 73
    line = prefix + "—" + "B" * 20
    folded = cal._fold_line(line)
    reconstructed = folded.replace("\r\n ", "")
    assert reconstructed == line


def test_fold_line_preserves_4_byte_emoji():
    # Pizza slice 🍕 (U+1F355) is 4 bytes. Cover the worst-case width.
    prefix = "A" * 72
    line = prefix + "🍕" + "B" * 20
    folded = cal._fold_line(line)
    reconstructed = folded.replace("\r\n ", "")
    assert reconstructed == line


def test_fold_line_continuation_uses_leading_space():
    long_line = "A" * 200
    folded = cal._fold_line(long_line)
    # Every line after the first must begin with exactly one SPACE per §3.1
    parts = folded.split("\r\n")
    assert len(parts) > 1
    for p in parts[1:]:
        assert p.startswith(" "), f"continuation must start with space: {p!r}"


def test_to_ics_actually_folds_long_lines():
    # The 1040-ES descriptions are >300 chars — the output MUST contain at
    # least one folded line (CRLF followed by space).
    ics = cal.to_ics(cal.events_1040es(2026))
    assert "\r\n " in ics, "expected line folding to occur"


def test_to_ics_unfolds_to_logical_summary():
    e = cal.CalendarEvent(
        uid="test@x",
        summary="A" * 200,
        date=datetime.date(2026, 4, 15),
        description="x",
        category="federal_estimated_tax",
        quarter_label="Q1",
    )
    ics = cal.to_ics([e])
    # RFC 5545 unfolding: strip "\r\n " between physical lines, then SUMMARY
    # value should be exactly the original.
    unfolded = ics.replace("\r\n ", "")
    assert f"SUMMARY:{'A' * 200}" in unfolded


# --- C2: strict bool parsing — reject truthy strings ---

def test_build_events_rejects_string_flag_value():
    # "yes"/"no" must NOT be silently coerced.
    p = _profile()
    p["flags"]["has_foreign_accounts"] = "yes"
    with pytest.raises(cal.CalendarError, match="has_foreign_accounts"):
        cal.build_events(p, 2026)


def test_build_events_rejects_string_age_flag():
    p = _profile()
    p["flags"]["subject_to_rmd_primary"] = "false"
    with pytest.raises(cal.CalendarError, match="subject_to_rmd_primary"):
        cal.build_events(p, 2026)


def test_build_events_rejects_string_k1_flag():
    p = _profile()
    p["flags"]["has_k1_income"] = "no"
    p["address"]["state"] = "CA"
    with pytest.raises(cal.CalendarError, match="has_k1_income"):
        cal.build_events(p, 2026)


def test_build_events_rejects_numeric_age_flag():
    p = _profile()
    p["flags"]["subject_to_rmd_primary"] = 1
    with pytest.raises(cal.CalendarError):
        cal.build_events(p, 2026)


def test_build_events_accepts_null_flag_as_false():
    # Explicit null in JSON is the "field present, not set" case — degrades
    # to the default (False) rather than erroring.
    p = _profile()
    p["flags"]["has_foreign_accounts"] = None
    events = cal.build_events(p, 2026)
    assert all(e.category != "fbar" for e in events)


# --- C3: state validation + non-PTET state visibility ---

def test_events_ptet_rejects_non_string_state():
    with pytest.raises(cal.CalendarError):
        cal.events_ptet(2026, state=6, has_k1_income=True)


def test_events_ptet_strips_whitespace_from_state():
    # " CA " must not silently fall through to "no PTET" — it's a real
    # upstream form glitch and CA PTET prepayment loss is high-cost.
    events = cal.events_ptet(2026, state=" CA ", has_k1_income=True)
    assert len(events) == 2  # CA emits June prepayment + March balance


def test_build_events_includes_warning_when_k1_but_non_ptet_state():
    # When has_k1_income=True and k1_states lists a non-modeled state (TX),
    # the emitted calendar should include a visible reminder that PTET
    # modeling didn't fire — otherwise it's indistinguishable from "no K-1".
    p = _profile()
    p["flags"]["has_k1_income"] = True
    p["flags"]["k1_states"] = ["TX"]
    events = cal.build_events(p, 2026)
    notes = [e for e in events if "PTET" in e.summary or "PTE" in e.summary]
    assert len(notes) == 1, (
        f"expected exactly one PTET-not-modeled note for TX K-1 filer, "
        f"got {[e.summary for e in notes]}"
    )
    assert notes[0].category == "note"
    assert "TX" in notes[0].summary


# --- I1: missed test coverage ---

def test_to_ics_escapes_backslash_before_other_specials():
    e = cal.CalendarEvent(
        uid="x@x",
        summary="path c:\\users",
        date=datetime.date(2026, 4, 15),
        description="raw\\backslash",
        category="fbar",
    )
    ics = cal.to_ics([e])
    # \\ in source -> \\\\ in output (each \ becomes \\)
    assert "raw\\\\backslash" in ics
    assert "path c:\\\\users" in ics


def test_to_ics_escapes_newlines_in_description():
    e = cal.CalendarEvent(
        uid="x@x",
        summary="x",
        date=datetime.date(2026, 4, 15),
        description="line1\nline2",
        category="fbar",
    )
    ics = cal.to_ics([e])
    # Pre-fold check: \n -> \\n (literal backslash + n), CRLF stays as line sep
    unfolded = ics.replace("\r\n ", "")
    assert "line1\\nline2" in unfolded


def test_events_ptet_ca_emits_exactly_two_events():
    events = cal.events_ptet(2026, state="CA", has_k1_income=True)
    dates = sorted(e.date for e in events)
    assert dates == [datetime.date(2026, 6, 15), datetime.date(2027, 3, 15)]


def test_events_ptet_ny_event_count():
    # NY: election+Q1 (Mar 15), Q2/Q3/Q4 estimates + annual return next-year → 5
    events = cal.events_ptet(2026, state="NY", has_k1_income=True)
    assert len(events) == 5


def test_events_ptet_nj_event_count():
    # NJ: 4 quarterly estimates (Apr/Jun/Sep of tax_year, Jan of next)
    # + election (Mar 15 of next year) → 5 total
    events = cal.events_ptet(2026, state="NJ", has_k1_income=True)
    assert len(events) == 5


def test_build_events_handles_null_flags_block():
    # flags: null (the `or {}` fallback the production code relies on)
    p = {"tax_year": 2026, "flags": None, "address": None}
    events = cal.build_events(p, 2026)
    assert len(events) == 4
    assert all(e.category == "federal_estimated_tax" for e in events)


def test_build_events_rejects_non_dict_flags():
    p = _profile()
    p["flags"] = "not a dict"
    with pytest.raises(cal.CalendarError, match="flags"):
        cal.build_events(p, 2026)


def test_build_events_rejects_non_dict_address():
    p = _profile()
    p["address"] = ["CA"]
    with pytest.raises(cal.CalendarError, match="address"):
        cal.build_events(p, 2026)


def test_build_events_uids_unique_across_all_categories():
    p = _profile()
    p["flags"]["has_foreign_accounts"] = True
    p["flags"]["subject_to_rmd_primary"] = True
    p["flags"]["subject_to_rmd_spouse"] = True
    p["flags"]["has_k1_income"] = True
    p["flags"]["filed_extension"] = True  # extension-file vs fbar-extended share Oct 15
    p["address"]["state"] = "CA"
    events = cal.build_events(p, 2026)
    uids = [e.uid for e in events]
    assert len(uids) == len(set(uids)), "UID collisions across categories"


def test_events_rmd_spouse_only_emits_one_event():
    events = cal.events_rmd(2026, primary_subject_to_rmd=False, spouse_subject_to_rmd=True)
    assert len(events) == 1
    assert "spouse" in events[0].uid


# --- I2: category is a Literal (typed) — runtime invariant check ---

def test_calendar_event_rejects_unknown_category():
    with pytest.raises((cal.CalendarError, ValueError, TypeError)):
        cal.CalendarEvent(
            uid="x@x", summary="x", date=datetime.date(2026, 4, 15),
            description="x", category="UNKNOWN_CATEGORY",
        )


def test_calendar_event_quarter_label_only_on_1040es():
    # An RMD event with quarter_label="Q1" is internally inconsistent.
    with pytest.raises((cal.CalendarError, ValueError)):
        cal.CalendarEvent(
            uid="x@x", summary="x", date=datetime.date(2026, 4, 15),
            description="x", category="rmd", quarter_label="Q1",
        )


def test_calendar_event_1040es_requires_quarter_label():
    with pytest.raises((cal.CalendarError, ValueError)):
        cal.CalendarEvent(
            uid="x@x", summary="x", date=datetime.date(2026, 4, 15),
            description="x", category="federal_estimated_tax", quarter_label="",
        )


# --- I3: _main should not silently swallow ValueError from bugs ---

def test_main_does_not_swallow_internal_value_error(tmp_path, monkeypatch):
    # If to_ics raises a ValueError (i.e. a real bug), _main should NOT
    # convert it into a clean exit 1. Such a bug deserves the traceback.
    profile_path = tmp_path / "taxpayer_profile.json"
    profile_path.write_text(json.dumps(_profile()))

    def boom(_events):
        raise ValueError("simulated internal bug")

    monkeypatch.setattr(cal, "to_ics", boom)
    with pytest.raises(ValueError):
        cal._main(["--profile", str(profile_path),
                   "--workspace", str(tmp_path), "--year", "2026"])


# --- I4: atomic write via .tmp + replace ---

# --- Codex round 9: platform-safe CRLF preservation on disk ---

def test_write_calendar_writes_pure_bytes_no_platform_translation(tmp_path):
    """The output file must contain exactly the bytes returned by to_ics().
    Path.write_text(default newline=None) translates \\n -> os.linesep on
    Windows, producing \\r\\r\\n in a file that already had \\r\\n. Assert
    against bytes on disk, which round-trips identically on every
    platform — text-mode reads mask this bug."""
    profile_path = tmp_path / "taxpayer_profile.json"
    profile_path.write_text(json.dumps(_profile()))
    out = cal.write_calendar(profile_path, tmp_path, tax_year=2026)

    on_disk = out.read_bytes()
    expected = cal.to_ics(cal.build_events(_profile(), 2026)).encode("utf-8")
    assert on_disk == expected, (
        f"bytes on disk differ from to_ics() output — likely text-mode "
        f"translation. on_disk length {len(on_disk)}, expected {len(expected)}"
    )


def test_write_calendar_does_not_double_crlf(tmp_path):
    """The strongest direct pin against the Windows \\r\\r\\n bug — read
    bytes, count CR runs, assert no consecutive CR."""
    profile_path = tmp_path / "taxpayer_profile.json"
    profile_path.write_text(json.dumps(_profile()))
    out = cal.write_calendar(profile_path, tmp_path, tax_year=2026)

    on_disk = out.read_bytes()
    assert b"\r\r" not in on_disk, "found doubled CR — text-mode mangled CRLF"
    assert b"\r\r\n" not in on_disk
    # Sanity: the file should still contain genuine CRLF line endings.
    assert b"\r\n" in on_disk


def test_write_calendar_does_not_leave_tmp_files(tmp_path):
    profile_path = tmp_path / "taxpayer_profile.json"
    profile_path.write_text(json.dumps(_profile()))
    cal.write_calendar(profile_path, tmp_path, tax_year=2026)
    tmps = list(tmp_path.glob("*.tmp"))
    assert tmps == [], f"unexpected leftover tmp files: {tmps}"


# --- I5: OSError on read translated to CalendarError ---

def test_write_calendar_translates_unicode_decode_error(tmp_path):
    # Binary garbage where the profile should be — read_text raises
    # UnicodeDecodeError, which must surface as CalendarError not raw.
    profile_path = tmp_path / "taxpayer_profile.json"
    profile_path.write_bytes(b"\xff\xfe\x00\x01\x02not json at all")
    with pytest.raises(cal.CalendarError):
        cal.write_calendar(profile_path, tmp_path, tax_year=2026)


def test_write_calendar_translates_is_a_directory_error(tmp_path):
    # User passes a directory where a profile was expected.
    profile_dir = tmp_path / "taxpayer_profile.json"
    profile_dir.mkdir()
    with pytest.raises(cal.CalendarError):
        cal.write_calendar(profile_dir, tmp_path, tax_year=2026)


# --- I6: tax_year validation inside lib (not only CLI) ---

def test_build_events_rejects_string_tax_year():
    p = _profile()
    with pytest.raises(cal.CalendarError):
        cal.build_events(p, "2026")  # type: ignore[arg-type]


def test_build_events_rejects_year_out_of_range():
    p = _profile()
    p["tax_year"] = 1900
    with pytest.raises(cal.CalendarError):
        cal.build_events(p, 1900)


def test_build_events_rejects_bool_as_year():
    p = _profile()
    p["tax_year"] = True
    with pytest.raises(cal.CalendarError):
        cal.build_events(p, True)  # type: ignore[arg-type]


def test_write_calendar_rejects_invalid_year(tmp_path):
    profile_path = tmp_path / "taxpayer_profile.json"
    profile_path.write_text(json.dumps(_profile()))
    with pytest.raises(cal.CalendarError):
        cal.write_calendar(profile_path, tmp_path, tax_year="../etc/passwd")  # type: ignore[arg-type]


# --- Suggestion: build_events sortedness pinned ---

def test_build_events_documented_sortedness():
    # Sort by (date, uid) — a tiebreak test pinning the secondary key.
    events = cal.events_1040es(2026)
    sorted_events = sorted(events, key=lambda e: (e.date, e.uid))
    assert events == sorted_events


# ---------------------------------------------------------------------------
# Codex review round (post-PR #19 first push)
# ---------------------------------------------------------------------------

# --- Codex #1: CA PTET prepayment is the GREATER, not lesser, of the two ---

def test_events_ptet_ca_prepay_description_says_greater():
    """R&TC §19904(a): June 15 prepayment is the greater of $1,000 or 50% of
    prior-year PTE elective tax. The 'lesser of' wording previously shipped
    would understate the required payment in nearly every case."""
    events = cal.events_ptet(2026, state="CA", has_k1_income=True)
    prepay = next(e for e in events if "prepayment" in e.summary.lower())
    desc_low = prepay.description.lower()
    assert "greater" in desc_low, (
        "CA PTET prepayment description must say GREATER of $1k or 50%, "
        f"got: {prepay.description!r}"
    )
    assert "lesser" not in desc_low, (
        "CA PTET prepayment description must NOT say 'lesser of'"
    )


def test_events_ptet_ca_prepay_description_covers_post_2026_regime():
    """For TY2026+, missing the June 15 payment no longer fully forfeits the
    election — it reduces the credit by 12.5% of the underpayment. Description
    should not falsely claim forfeiture is the only consequence."""
    events = cal.events_ptet(2026, state="CA", has_k1_income=True)
    prepay = next(e for e in events if "prepayment" in e.summary.lower())
    # Either mentions the regime change explicitly or uses neutral wording
    # like "reduce" rather than absolute "forfeits".
    desc_low = prepay.description.lower()
    assert "12.5%" in prepay.description or "reduce" in desc_low or "reduction" in desc_low, (
        f"description should reflect the TY2026 regime change, got: {prepay.description!r}"
    )


# --- Codex #2: NJ BAIT election deadline lands in tax_year + 1 ---

def test_events_ptet_nj_election_is_in_following_year():
    """NJ BAIT election is made on the timely-filed PTE-100, due March 15 of
    the year AFTER the tax year (for calendar-year entities). Emitting
    March 15 of the tax year itself is off-by-one and lands in the past for
    typical prior-year setup."""
    events = cal.events_ptet(2026, state="NJ", has_k1_income=True)
    # Find the election event (one of the 5 NJ events, distinguished by UID).
    election = next(e for e in events if "election" in e.uid)
    assert election.date == datetime.date(2027, 3, 15), (
        f"expected March 15, 2027 for TY2026 NJ BAIT election, got {election.date}"
    )


# --- NY regression pin (statutorily different from NJ — don't merge them) ---

def test_build_events_rejects_list_profile():
    # `[]` is valid JSON but the wrong top-level shape; calling `.get` on it
    # would raise AttributeError. Should surface as CalendarError instead.
    with pytest.raises(cal.CalendarError, match="object"):
        cal.build_events([], 2026)


def test_build_events_rejects_string_profile():
    with pytest.raises(cal.CalendarError, match="object"):
        cal.build_events("not a profile", 2026)


def test_build_events_rejects_none_profile():
    with pytest.raises(cal.CalendarError, match="object"):
        cal.build_events(None, 2026)


def test_build_events_rejects_int_profile():
    with pytest.raises(cal.CalendarError, match="object"):
        cal.build_events(42, 2026)


# --- Codex round 4: falsy non-dict flags/address must not silently degrade ---

def test_build_events_rejects_list_flags_block():
    """`flags: []` is falsy AND not a dict — `or {}` short-circuit silently
    substitutes {} BEFORE the isinstance check sees the corruption. Must
    hard-error so a truncated profile cannot silently drop FBAR/RMD/PTET."""
    p = _profile()
    p["flags"] = []
    with pytest.raises(cal.CalendarError, match="flags"):
        cal.build_events(p, 2026)


def test_build_events_rejects_empty_string_flags_block():
    p = _profile()
    p["flags"] = ""
    with pytest.raises(cal.CalendarError, match="flags"):
        cal.build_events(p, 2026)


def test_build_events_rejects_int_flags_block():
    p = _profile()
    p["flags"] = 0
    with pytest.raises(cal.CalendarError, match="flags"):
        cal.build_events(p, 2026)


def test_build_events_rejects_list_address_block():
    p = _profile()
    p["address"] = []
    with pytest.raises(cal.CalendarError, match="address"):
        cal.build_events(p, 2026)


def test_build_events_rejects_empty_string_address_block():
    p = _profile()
    p["address"] = ""
    with pytest.raises(cal.CalendarError, match="address"):
        cal.build_events(p, 2026)


# --- Codex round 5 #1: PTET driven by K-1 entity states, not address.state ---

def test_build_events_ptet_uses_k1_states_not_address():
    """Cross-state K-1 case: TX resident with a CA K-1 entity. Per Codex
    round 5, PTET deadlines belong to the entity's state, not the
    taxpayer's mailing address."""
    p = _profile()
    p["address"]["state"] = "TX"
    p["flags"]["has_k1_income"] = True
    p["flags"]["k1_states"] = ["CA"]
    events = cal.build_events(p, 2026)
    ptet = [e for e in events if e.category == "ptet"]
    # CA PTET emits 2 events (June prepayment + March balance)
    assert len(ptet) == 2
    summaries = " ".join(e.summary for e in ptet)
    assert "CA PTET" in summaries


def test_build_events_does_not_emit_ptet_for_address_when_no_k1():
    """CA resident with K-1s only in non-modeled states (TX) — currently
    we'd silently emit CA PTET because address.state == CA. After the
    fix, no CA PTET should fire."""
    p = _profile()
    p["address"]["state"] = "CA"
    p["flags"]["has_k1_income"] = True
    p["flags"]["k1_states"] = ["TX"]
    events = cal.build_events(p, 2026)
    ptet = [e for e in events if e.category == "ptet"]
    assert ptet == [], f"unexpected PTET events for TX K-1: {[e.summary for e in ptet]}"


def test_build_events_emits_ptet_for_multiple_k1_states():
    """A taxpayer with K-1s in BOTH CA and NY should get reminders for
    both. The pre-fix code only checked address.state, which is a single
    value, so multi-state K-1 holders were under-served."""
    p = _profile()
    p["flags"]["has_k1_income"] = True
    p["flags"]["k1_states"] = ["CA", "NY"]
    events = cal.build_events(p, 2026)
    ptet = [e for e in events if e.category == "ptet"]
    # CA emits 2; NY emits 5 (election+Q1 + Q2/Q3/Q4 + annual return) → 7 total
    assert len(ptet) == 7
    summaries = " ".join(e.summary for e in ptet)
    assert "CA PTET" in summaries
    assert "NY PTET" in summaries


def test_build_events_emits_not_modeled_note_per_unmodeled_k1_state():
    """K-1 in a non-modeled state (TX) — should emit a visible note for
    THAT state, not silently drop."""
    p = _profile()
    p["flags"]["has_k1_income"] = True
    p["flags"]["k1_states"] = ["TX"]
    events = cal.build_events(p, 2026)
    notes = [e for e in events if e.category == "note"]
    assert len(notes) == 1
    assert "TX" in notes[0].summary


def test_build_events_k1_states_missing_emits_warning_when_has_k1():
    """has_k1_income=True but no k1_states listed — cannot determine
    PTET deadlines. Should emit a guidance note rather than silently
    skip."""
    p = _profile()
    p["flags"]["has_k1_income"] = True
    # k1_states key intentionally omitted
    events = cal.build_events(p, 2026)
    notes = [e for e in events if e.category == "note"]
    assert len(notes) == 1, (
        f"expected exactly one K-1-states-missing note, got {len(notes)}"
    )
    note = notes[0]
    assert note.uid == f"note-k1-states-missing-2026@tax-prep-skills"
    assert "TY2026" in note.summary
    # Sanity: deterministic UID across calls (no UUID or time component).
    events2 = cal.build_events(p, 2026)
    note2 = next(e for e in events2 if e.category == "note")
    assert note2.uid == note.uid


def test_build_events_rejects_non_list_k1_states():
    p = _profile()
    p["flags"]["has_k1_income"] = True
    p["flags"]["k1_states"] = "CA"  # string, not list — wrong type
    with pytest.raises(cal.CalendarError, match="k1_states"):
        cal.build_events(p, 2026)


def test_build_events_rejects_non_string_in_k1_states():
    p = _profile()
    p["flags"]["has_k1_income"] = True
    p["flags"]["k1_states"] = ["CA", 6, "NY"]
    with pytest.raises(cal.CalendarError, match="k1_states"):
        cal.build_events(p, 2026)


# --- Codex round 5 #2: FBAR penalty wording — neutral, statute-cited ---

# --- Codex round 6: shape-validate k1_states + escape CR + reject CRLF in UID ---

# --- Codex round 8: stale k1_states must not block calendar when has_k1=False ---

def test_build_events_tolerates_stale_full_state_name_when_has_k1_false():
    """Symmetric inverse of round 7: a profile with has_k1_income=False
    but a stale `k1_states=["California"]` (full name instead of code)
    must NOT block the 1040-ES calendar. k1_states is irrelevant when
    K-1 is off, so don't validate it then."""
    p = _profile()
    p["flags"]["has_k1_income"] = False
    p["flags"]["k1_states"] = ["California"]
    events = cal.build_events(p, 2026)
    assert len(events) == 4
    assert all(e.category == "federal_estimated_tax" for e in events)


def test_build_events_tolerates_non_list_k1_states_when_has_k1_false():
    """Even a wrong-TYPE k1_states (string instead of list) should be
    inert when has_k1=False — the field isn't consulted."""
    p = _profile()
    p["flags"]["has_k1_income"] = False
    p["flags"]["k1_states"] = "CA"
    events = cal.build_events(p, 2026)
    assert len(events) == 4


def test_build_events_tolerates_crlf_k1_states_when_has_k1_false():
    """Even the round 6 injection payload is inert when has_k1=False —
    the malicious string can't flow into any UID/SUMMARY/DESCRIPTION
    because PTET emission is gated off entirely."""
    p = _profile()
    p["flags"]["has_k1_income"] = False
    p["flags"]["k1_states"] = ["TX\r\nBEGIN:VEVENT\r\nUID:evil"]
    events = cal.build_events(p, 2026)
    assert len(events) == 4


def test_build_events_still_rejects_malformed_k1_states_when_has_k1_true():
    """Regression pin: strict validation MUST still fire when k1_states
    is actually consulted by the emission logic."""
    p = _profile()
    p["flags"]["has_k1_income"] = True
    p["flags"]["k1_states"] = ["California"]
    with pytest.raises(cal.CalendarError, match="2-letter"):
        cal.build_events(p, 2026)


def test_build_events_still_rejects_non_list_k1_states_when_has_k1_true():
    p = _profile()
    p["flags"]["has_k1_income"] = True
    p["flags"]["k1_states"] = "CA"
    with pytest.raises(cal.CalendarError, match="k1_states"):
        cal.build_events(p, 2026)


# --- Codex round 7: has_k1_income=False must suppress PTET regardless of stale k1_states ---

def test_build_events_skips_ptet_when_has_k1_false_with_stale_states():
    """Codex round 7 regression case: has_k1_income=False with non-empty
    k1_states (e.g. stale entries from a prior year after /tax-rollover,
    or a corrected K-1 flag without clearing the state list). The
    calendar must emit ZERO PTET events because the taxpayer reported
    no K-1 income — k1_states alone is not authoritative."""
    p = _profile()
    p["flags"]["has_k1_income"] = False
    p["flags"]["k1_states"] = ["CA"]
    events = cal.build_events(p, 2026)
    ptet = [e for e in events if e.category == "ptet"]
    assert ptet == [], (
        f"unexpected PTET events when has_k1_income=False: "
        f"{[e.summary for e in ptet]}"
    )


def test_build_events_no_missing_note_when_has_k1_false():
    """The k1-states-missing guidance note is also K-1-specific — must not
    fire when has_k1_income is False, even if k1_states happens to be
    empty (the user simply has no K-1, not a misconfiguration)."""
    p = _profile()
    p["flags"]["has_k1_income"] = False
    p["flags"]["k1_states"] = []
    events = cal.build_events(p, 2026)
    notes = [e for e in events if e.category == "note"]
    assert notes == [], (
        f"unexpected guidance note when has_k1_income=False: "
        f"{[e.summary for e in notes]}"
    )


def test_build_events_no_note_when_has_k1_false_with_stale_states():
    """And also no not-modeled note for the stale states — has_k1_income
    is the master gate."""
    p = _profile()
    p["flags"]["has_k1_income"] = False
    p["flags"]["k1_states"] = ["TX"]
    events = cal.build_events(p, 2026)
    notes = [e for e in events if e.category == "note"]
    assert notes == []


def test_build_events_rejects_three_letter_k1_state():
    p = _profile()
    p["flags"]["has_k1_income"] = True
    p["flags"]["k1_states"] = ["CAL"]
    with pytest.raises(cal.CalendarError, match="2-letter"):
        cal.build_events(p, 2026)


def test_build_events_rejects_full_state_name_in_k1_states():
    p = _profile()
    p["flags"]["has_k1_income"] = True
    p["flags"]["k1_states"] = ["California"]
    with pytest.raises(cal.CalendarError, match="2-letter"):
        cal.build_events(p, 2026)


def test_build_events_rejects_one_letter_k1_state():
    p = _profile()
    p["flags"]["has_k1_income"] = True
    p["flags"]["k1_states"] = ["C"]
    with pytest.raises(cal.CalendarError, match="2-letter"):
        cal.build_events(p, 2026)


def test_build_events_rejects_state_with_unicode():
    p = _profile()
    p["flags"]["has_k1_income"] = True
    p["flags"]["k1_states"] = ["CÁ"]  # accented A — not in [A-Z]{2}
    with pytest.raises(cal.CalendarError, match="2-letter"):
        cal.build_events(p, 2026)


def test_build_events_rejects_state_with_digits():
    p = _profile()
    p["flags"]["has_k1_income"] = True
    p["flags"]["k1_states"] = ["C1"]
    with pytest.raises(cal.CalendarError, match="2-letter"):
        cal.build_events(p, 2026)


def test_build_events_rejects_state_with_crlf_injection():
    """The Codex round 6 attack vector: CRLF embedded in a state code
    flows into UID/SUMMARY/DESCRIPTION unescaped and the parser sees
    injected iCal properties. Must be rejected at the input boundary."""
    p = _profile()
    p["flags"]["has_k1_income"] = True
    p["flags"]["k1_states"] = [
        "TX\r\nBEGIN:VEVENT\r\nUID:evil\r\nSUMMARY:phantom\r\nEND:VEVENT"
    ]
    with pytest.raises(cal.CalendarError, match="2-letter"):
        cal.build_events(p, 2026)


# --- Layer 2: TEXT escape must handle \r (defense in depth) ---

def test_escape_text_handles_carriage_return():
    """RFC 5545 §3.3.11 — bare CR in TEXT properties must be encoded as
    `\\n` so calendar parsers can't be tricked by a line break."""
    escaped = cal._escape_text("line1\rline2")
    # CR must not survive into the encoded output; the standard
    # representation is the literal two-character sequence `\n`.
    assert "\r" not in escaped
    assert "\\n" in escaped


def test_escape_text_handles_crlf_sequence():
    escaped = cal._escape_text("line1\r\nline2")
    assert "\r" not in escaped
    assert "\n" not in escaped
    # CRLF should collapse to a single `\n` literal, not duplicated.
    assert escaped == "line1\\nline2"


# --- Layer 3: CalendarEvent rejects control chars in UID (defense in depth) ---

def test_calendar_event_rejects_crlf_in_uid():
    """Per RFC 5545 §3.8.4.7, UID values are line-folded TEXT — embedded
    CR/LF would break the format. Reject at construction."""
    with pytest.raises(cal.CalendarError, match="UID"):
        cal.CalendarEvent(
            uid="evil\r\nBEGIN:VEVENT\r\nUID:injected",
            summary="x", date=datetime.date(2026, 4, 15),
            description="x", category="rmd",
        )


def test_calendar_event_rejects_nul_in_uid():
    with pytest.raises(cal.CalendarError, match="UID"):
        cal.CalendarEvent(
            uid="evil\x00null",
            summary="x", date=datetime.date(2026, 4, 15),
            description="x", category="rmd",
        )


def test_calendar_event_accepts_valid_uid():
    # Sanity: the standard slug format must continue to work.
    e = cal.CalendarEvent(
        uid="rmd-primary-2026@tax-prep-skills",
        summary="x", date=datetime.date(2026, 4, 15),
        description="x", category="rmd",
    )
    assert e.uid == "rmd-primary-2026@tax-prep-skills"


def test_main_no_injection_via_malformed_state(tmp_path, capsys):
    """End-to-end pin of the Codex attack vector: the malicious payload
    must be rejected at the input boundary and produce a clean ERROR —
    NOT a successfully-written .ics with injected events."""
    profile_path = tmp_path / "taxpayer_profile.json"
    profile_path.write_text(json.dumps({
        "tax_year": 2026,
        "flags": {
            "has_k1_income": True,
            "k1_states": [
                "TX\r\nBEGIN:VEVENT\r\nUID:evil\r\n"
                "SUMMARY:phantom\r\nEND:VEVENT"
            ],
        },
    }))
    rc = cal._main(["--profile", str(profile_path),
                    "--workspace", str(tmp_path), "--year", "2026"])
    assert rc == 1
    err = capsys.readouterr().err
    assert err.startswith("ERROR:")
    assert not (tmp_path / "tax_calendar_2026.ics").exists()


def test_events_fbar_extension_description_avoids_stale_dollar_amounts():
    """FBAR penalty amounts are inflation-adjusted annually AND the
    $10,000 figure was non-willful per-form (Bittner 2023), not willful
    per-account. Avoid committing to specific dollar amounts so the
    description doesn't rot or mislead."""
    events = cal.events_fbar(2026)
    extension = events[1]  # Oct 15 final deadline
    # Should NOT pin a specific dollar amount
    assert "$10,000" not in extension.description
    assert "$10000" not in extension.description


def test_events_fbar_extension_description_cites_statute():
    """Prefer a statute citation (31 U.S.C. §5321) over rotting dollar
    amounts. Also flag the willful/non-willful regime distinction so the
    user knows the lookup is non-trivial."""
    events = cal.events_fbar(2026)
    extension = events[1]
    desc = extension.description
    assert "31 U.S.C." in desc or "31 USC" in desc
    assert "willful" in desc.lower()


def test_main_handles_corrupt_flags_block_cleanly(tmp_path, capsys):
    """Regression pin for the Codex round 4 finding: a profile with
    `"flags": []` must surface as a clean ERROR line, not silently produce
    a 1040-ES-only calendar."""
    profile_path = tmp_path / "taxpayer_profile.json"
    profile_path.write_text(
        '{"tax_year": 2026, "flags": [], "address": {"state": "CA"}}'
    )
    rc = cal._main(["--profile", str(profile_path),
                    "--workspace", str(tmp_path), "--year", "2026"])
    assert rc == 1
    err = capsys.readouterr().err
    assert err.startswith("ERROR:")
    assert "flags" in err
    # And critically, no .ics was written.
    assert not (tmp_path / "tax_calendar_2026.ics").exists()


def test_main_handles_non_object_json_cleanly(tmp_path, capsys):
    # A profile of `[]` is valid JSON but not an object — the CLI should
    # surface a clean `ERROR:` line on stderr and exit 1, not raise.
    profile_path = tmp_path / "taxpayer_profile.json"
    profile_path.write_text("[]")
    rc = cal._main(["--profile", str(profile_path),
                    "--workspace", str(tmp_path), "--year", "2026"])
    assert rc == 1
    err = capsys.readouterr().err
    assert err.startswith("ERROR:"), f"expected clean ERROR line, got: {err!r}"
    assert "Traceback" not in err


def test_events_rmd_uses_subject_to_rmd_flag_not_age():
    """The flag is `subject_to_rmd_*` (user-asserted boolean), not
    `age_73_plus_*`. RMD start age is birth-year-cohort-dependent under
    SECURE 2.0 (1951–1959: age 73; 1960+: age 75), so a pure age flag
    cannot correctly model TY2033+ — the user (advised by their preparer)
    asserts whether RMDs are currently required."""
    events = cal.events_rmd(2026, primary_subject_to_rmd=True,
                            spouse_subject_to_rmd=False)
    assert len(events) == 1
    assert events[0].date == datetime.date(2026, 12, 31)


def test_build_events_uses_subject_to_rmd_flag():
    p = _profile()
    p["flags"]["subject_to_rmd_primary"] = True
    events = cal.build_events(p, 2026)
    assert sum(1 for e in events if e.category == "rmd") == 1


def test_build_events_rejects_string_subject_to_rmd_flag():
    p = _profile()
    p["flags"]["subject_to_rmd_primary"] = "yes"
    with pytest.raises(cal.CalendarError, match="subject_to_rmd_primary"):
        cal.build_events(p, 2026)


def test_events_ptet_ny_election_stays_in_tax_year():
    """NY Tax Law §860: the annual PTET election is made BY March 15 of the
    tax year itself (in-year pre-election), unlike NJ (with-return). Pin this
    so a future NJ/NY-consolidation refactor cannot reintroduce the bug
    Codex caught."""
    events = cal.events_ptet(2026, state="NY", has_k1_income=True)
    election = next(e for e in events if "election" in e.uid)
    # In-year (tax_year, not tax_year+1). L1: statutory Mar 15 2026 is a Sunday,
    # observed Mar 16 — still WITHIN the tax year, which is what this pins.
    assert election.date == datetime.date(2026, 3, 16), (
        f"NY PTET election must be ~March 15 of tax year, got {election.date}"
    )
    assert election.date.year == 2026


# --- Review pass 2: gaps surfaced by 5-agent re-review ---

def test_to_ics_includes_uppercase_categories_line():
    """The `CATEGORIES:` line is the iCal filter key calendar apps use to
    bucket events. Future regression that drops `.upper()` or remaps a
    category would silently break filtering — pin every emitted category
    explicitly."""
    events = (
        cal.events_1040es(2026)
        + cal.events_fbar(2026)
        + cal.events_rmd(2026, primary_subject_to_rmd=True, spouse_subject_to_rmd=False)
        + cal.events_ptet(2026, state="CA", has_k1_income=True)
    )
    ics = cal.to_ics(events)
    assert "CATEGORIES:FEDERAL_ESTIMATED_TAX" in ics
    assert "CATEGORIES:FBAR" in ics
    assert "CATEGORIES:RMD" in ics
    assert "CATEGORIES:PTET" in ics
    # Lowercase forms must NOT appear (catches a future `.upper()` drop).
    assert "CATEGORIES:federal_estimated_tax" not in ics
    assert "CATEGORIES:fbar" not in ics


def test_to_ics_includes_categories_for_note_events():
    """Note events get CATEGORIES:NOTE — separate filter for the info
    events from actual deadlines."""
    p = {
        "tax_year": 2026,
        "flags": {"has_k1_income": True, "k1_states": ["TX"]},
    }
    events = cal.build_events(p, 2026)
    ics = cal.to_ics(events)
    assert "CATEGORIES:NOTE" in ics


def test_write_calendar_translates_json_decode_error(tmp_path):
    """A profile that's valid UTF-8 but invalid JSON must surface as a
    clean CalendarError, not a raw JSONDecodeError. Companion to the
    existing UnicodeDecodeError pin."""
    profile_path = tmp_path / "taxpayer_profile.json"
    profile_path.write_text("{not json")
    with pytest.raises(cal.CalendarError, match="not valid JSON"):
        cal.write_calendar(profile_path, tmp_path, tax_year=2026)


def test_main_rejects_workspace_that_does_not_exist(tmp_path, capsys):
    """`--workspace path/that/does/not/exist` must hard-error with a
    clean ERROR line, not propagate a Python AttributeError or land
    silently in CWD."""
    profile_path = tmp_path / "taxpayer_profile.json"
    profile_path.write_text(json.dumps(_profile()))
    nonexistent = tmp_path / "ghost-dir"
    rc = cal._main(["--profile", str(profile_path),
                    "--workspace", str(nonexistent), "--year", "2026"])
    assert rc == 1
    err = capsys.readouterr().err
    assert err.startswith("ERROR:")
    assert "workspace does not exist" in err


def test_build_events_emits_no_fbar_when_flag_false_exact_count():
    """Round-7 lesson generalized: every gate-style field needs a pin
    that ALL conditional emission is suppressed when the gate is off,
    asserted by exact event count (not by 'no fbar events present')."""
    p = _profile()
    p["flags"]["has_foreign_accounts"] = False
    events = cal.build_events(p, 2026)
    assert len(events) == 4
    assert all(e.category == "federal_estimated_tax" for e in events)


def test_write_calendar_tmp_cleanup_on_write_failure(tmp_path, monkeypatch):
    """If the write fails mid-way, the `.tmp` sibling must be cleaned
    up so retry doesn't accumulate orphan files. Forces a failure by
    making `os.replace` raise."""
    profile_path = tmp_path / "taxpayer_profile.json"
    profile_path.write_text(json.dumps(_profile()))

    real_replace = cal.os.replace

    def boom(src, dst):
        raise OSError("simulated disk full")

    monkeypatch.setattr(cal.os, "replace", boom)
    with pytest.raises(cal.CalendarError, match="failed to write"):
        cal.write_calendar(profile_path, tmp_path, tax_year=2026)

    # Restore so other test infra isn't broken.
    monkeypatch.setattr(cal.os, "replace", real_replace)

    # Critically: no orphan .tmp files left behind.
    tmps = list(tmp_path.glob("*.tmp"))
    assert tmps == [], f"unexpected tmp orphans: {tmps}"
    # And no final .ics either (atomic guarantee).
    assert not (tmp_path / "tax_calendar_2026.ics").exists()


# --- Review pass 2: code-hygiene behavioral changes ---

def test_to_ics_dtstamp_derived_from_tax_year():
    """DTSTAMP should derive from tax_year so a TY2050 calendar isn't
    stamped with a 2026 date 24 years in the past — RFC 5545 allows
    any valid timestamp but some clients flag stale DTSTAMPs."""
    ics_2026 = cal.to_ics(cal.events_1040es(2026))
    ics_2050 = cal.to_ics(cal.events_1040es(2050))
    assert "DTSTAMP:20260101T000000Z" in ics_2026
    assert "DTSTAMP:20500101T000000Z" in ics_2050


def test_to_ics_dtstamp_byte_stable_within_year():
    """Within a single tax year, byte-stable output is preserved
    (same inputs → same output) — the round-9 invariant survives the
    tax_year derive change."""
    a = cal.to_ics(cal.events_1040es(2026))
    b = cal.to_ics(cal.events_1040es(2026))
    assert a == b


def test_main_rejects_empty_workspace(tmp_path, capsys):
    """--workspace="" must hard-error, not silently fall back to CWD."""
    profile_path = tmp_path / "taxpayer_profile.json"
    profile_path.write_text(json.dumps(_profile()))
    rc = cal._main(["--profile", str(profile_path),
                    "--workspace", "", "--year", "2026"])
    assert rc == 1
    err = capsys.readouterr().err
    assert err.startswith("ERROR:")
    assert "workspace" in err.lower()


def test_validate_tax_year_bool_error_message_says_not_a_bool():
    """The error message for a bool input should explicitly say
    "not a bool" — otherwise the Python quirk (`isinstance(True, int)
    == True`) makes "must be an int, got True" confusing."""
    with pytest.raises(cal.CalendarError, match="not a bool"):
        cal._validate_tax_year(True)
    with pytest.raises(cal.CalendarError, match="not a bool"):
        cal._validate_tax_year(False)


# --- Codex round 13a: year-window gating per state ---

def test_events_ptet_ca_skips_for_pre_2021():
    """CA PTE elective tax was created by AB 150 effective TY2021. For
    pre-2021 tax years, the regime didn't exist — emit a note, not
    actual events."""
    events = cal.events_ptet(2020, state="CA", has_k1_income=True)
    assert all(e.category == "note" for e in events)
    assert len(events) == 1
    assert "CA" in events[0].summary


def test_events_ptet_ca_skips_for_post_2030():
    """CA PTE elective tax sunset under SB 132 after TY2030. For TY2031+,
    emit a note flagging the expiration."""
    events = cal.events_ptet(2031, state="CA", has_k1_income=True)
    assert all(e.category == "note" for e in events)
    assert len(events) == 1


def test_events_ptet_ca_emits_at_first_valid_year_2021():
    events = cal.events_ptet(2021, state="CA", has_k1_income=True)
    assert all(e.category == "ptet" for e in events)
    assert len(events) == 2


def test_events_ptet_ca_emits_at_last_valid_year_2030():
    events = cal.events_ptet(2030, state="CA", has_k1_income=True)
    assert all(e.category == "ptet" for e in events)
    assert len(events) == 2


def test_events_ptet_ny_skips_for_pre_2021():
    """NY PTET created TY2021 by NY Tax Law §860; no sunset to date."""
    events = cal.events_ptet(2020, state="NY", has_k1_income=True)
    assert all(e.category == "note" for e in events)
    assert len(events) == 1


def test_events_ptet_ny_emits_at_first_valid_year_2021():
    events = cal.events_ptet(2021, state="NY", has_k1_income=True)
    assert all(e.category == "ptet" for e in events)


def test_events_ptet_nj_skips_for_pre_2020():
    """NJ BAIT created TY2020 by P.L. 2019 c.320; no sunset to date."""
    events = cal.events_ptet(2019, state="NJ", has_k1_income=True)
    assert all(e.category == "note" for e in events)
    assert len(events) == 1


def test_events_ptet_nj_emits_at_first_valid_year_2020():
    events = cal.events_ptet(2020, state="NJ", has_k1_income=True)
    assert all(e.category == "ptet" for e in events)


# --- Codex round 13b: NY annual return + final payment deadline ---

def test_events_ptet_ny_emits_annual_return_event():
    """Per NY Tax Department: 'the PTET annual return is generally due
    March 15 after the close of the PTET taxable year' and 'any unpaid
    PTET must be paid by March 15 following the close.' NY now emits 5
    events: election+Q1 (Mar 15), Q2 (Jun 15), Q3 (Sep 15), Q4 (Dec 15),
    annual return (Mar 15 next year)."""
    events = cal.events_ptet(2026, state="NY", has_k1_income=True)
    dates = sorted(e.date for e in events)
    # L1: §7503 weekend rollover — statutory Mar 15 2026 is a SUNDAY → observed
    # Mar 16; the other four fall on weekdays and don't move.
    assert dates == [
        datetime.date(2026, 3, 16),   # election + Q1 (Mar 15 = Sunday)
        datetime.date(2026, 6, 15),   # Q2
        datetime.date(2026, 9, 15),   # Q3
        datetime.date(2026, 12, 15),  # Q4
        datetime.date(2027, 3, 15),   # annual return + final payment (Monday)
    ]


def test_events_ptet_ny_annual_return_uid_distinct():
    events = cal.events_ptet(2026, state="NY", has_k1_income=True)
    uids = [e.uid for e in events]
    assert len(set(uids)) == 5
    # The annual-return event should be findable by uid token.
    returns = [e for e in events if "annual-return" in e.uid or "return" in e.uid]
    assert len(returns) == 1
    assert returns[0].date == datetime.date(2027, 3, 15)


# --- Codex round 11: dedupe repeated K-1 state codes ---

def test_strict_state_list_dedupes_duplicates():
    """Multiple K-1s in the same state (e.g. two CA partnerships) must
    not produce duplicate state codes — PTET deadlines are state-level,
    not entity-level. Dedupe at the input boundary keeps build_events
    clean. Order is preserved (first-occurrence wins) so output stays
    deterministic."""
    d = {"k1_states": ["CA", "CA", "NY", "CA"]}
    assert cal._strict_state_list(d, "k1_states") == ["CA", "NY"]


def test_strict_state_list_preserves_order_of_first_occurrence():
    d = {"k1_states": ["NY", "CA", "NJ", "NY", "CA"]}
    assert cal._strict_state_list(d, "k1_states") == ["NY", "CA", "NJ"]


def test_build_events_dedupes_repeated_k1_states():
    """End-to-end: a profile with `k1_states=["CA", "CA"]` (two CA K-1s)
    must emit the CA PTET events once, not twice — the duplicate-UID
    integrity check would otherwise reject the whole calendar."""
    p = _profile()
    p["flags"]["has_k1_income"] = True
    p["flags"]["k1_states"] = ["CA", "CA"]
    events = cal.build_events(p, 2026)
    ptet = [e for e in events if e.category == "ptet"]
    assert len(ptet) == 2  # CA emits 2 events, deduped to one set


def test_build_events_dedupes_mixed_repeated_states():
    p = _profile()
    p["flags"]["has_k1_income"] = True
    p["flags"]["k1_states"] = ["CA", "NY", "CA", "NJ", "NY"]
    events = cal.build_events(p, 2026)
    ptet = [e for e in events if e.category == "ptet"]
    # CA emits 2, NY emits 5, NJ emits 5, no duplicates → 12 total
    assert len(ptet) == 12

# ---------------------------------------------------------------------------
# CA 540-ES events (E7 — 30/40/0/30 weighting, no September installment)
# ---------------------------------------------------------------------------

def test_events_ca_es_three_installments_no_september():
    events = cal.events_ca_es(2026)
    assert [e.quarter_label for e in events] == ["Q1", "Q2", "Q4"]
    assert [e.date for e in events] == [
        datetime.date(2026, 4, 15),
        datetime.date(2026, 6, 15),
        datetime.date(2027, 1, 15),  # final 30% due Jan 15 of NEXT year
    ]
    # No event lands on the federal Q3 date — that's the whole point of E7.
    assert datetime.date(2026, 9, 15) not in [e.date for e in events]


def test_events_ca_es_weights_in_summaries():
    events = cal.events_ca_es(2026)
    assert "30%" in events[0].summary
    assert "40%" in events[1].summary
    assert "30%" in events[2].summary


def test_events_ca_es_category_and_uids():
    events = cal.events_ca_es(2026)
    for e in events:
        assert e.category == "state_estimated_tax"
    uids = [e.uid for e in events]
    assert len(set(uids)) == 3
    assert events == cal.events_ca_es(2026)  # determinism


def test_events_ca_es_descriptions_flag_no_september_and_high_income_rule():
    for e in cal.events_ca_es(2026):
        assert "NO September" in e.description
        assert "19136.3" in e.description  # $1M/$500K-MFS 90%-only rule


def test_build_events_ca_resident_gets_ca_es():
    p = _profile(address={"state": "CA"})
    events = cal.build_events(p, 2026)
    ca_es = [e for e in events if e.category == "state_estimated_tax"]
    assert len(ca_es) == 3


def test_build_events_non_ca_resident_no_ca_es():
    events = cal.build_events(_profile(), 2026)  # TX
    assert not [e for e in events if e.category == "state_estimated_tax"]


def test_build_events_missing_address_or_state_skips_ca_es():
    p = _profile()
    del p["address"]
    assert not [e for e in cal.build_events(p, 2026)
                if e.category == "state_estimated_tax"]
    p2 = _profile(address={"state": None})
    assert not [e for e in cal.build_events(p2, 2026)
                if e.category == "state_estimated_tax"]


def test_build_events_ca_state_normalized_case_whitespace():
    p = _profile(address={"state": " ca "})
    events = cal.build_events(p, 2026)
    assert [e for e in events if e.category == "state_estimated_tax"]


def test_build_events_full_state_name_hard_errors():
    # "California" silently producing a federal-only calendar would drop the
    # CA-ES reminders without warning — hard-error at the input boundary.
    with pytest.raises(cal.CalendarError):
        cal.build_events(_profile(address={"state": "California"}), 2026)


def test_build_events_non_string_state_hard_errors():
    with pytest.raises(cal.CalendarError):
        cal.build_events(_profile(address={"state": 42}), 2026)


def test_quarter_label_required_on_state_estimated_tax():
    with pytest.raises(cal.CalendarError):
        cal.CalendarEvent(
            uid="x@test", summary="s", date=datetime.date(2026, 4, 15),
            description="d", category="state_estimated_tax")


def test_ca_es_events_serialize_with_category_and_current_prodid():
    p = _profile(address={"state": "CA"})
    ics = cal.to_ics(cal.build_events(p, 2026))
    assert "CATEGORIES:STATE_ESTIMATED_TAX" in ics
    assert "tax-calendar v4" in ics  # v4: §7503 next-business-day rollover (L1)


def test_build_events_ca_resident_uids_unique_against_federal():
    # CA-ES UIDs (ca540es-*) must never collide with 1040-ES UIDs (1040es-*).
    p = _profile(address={"state": "CA"},
                 flags={"has_foreign_accounts": True, "has_k1_income": True,
                        "k1_states": ["CA"], "subject_to_rmd_primary": True,
                        "subject_to_rmd_spouse": False})
    events = cal.build_events(p, 2026)  # would raise on duplicate UIDs
    uids = [e.uid for e in events]
    assert len(set(uids)) == len(uids)


def test_build_events_empty_state_string_degrades_to_federal_only():
    # "" is scaffolding, not a malformed state — the taxpayer still gets the
    # full federal calendar (losing FBAR/RMD/PTET over "" would be absurd).
    p = _profile(address={"state": ""})
    events = cal.build_events(p, 2026)
    assert not [e for e in events if e.category == "state_estimated_tax"]
    assert [e for e in events if e.category == "federal_estimated_tax"]
    p2 = _profile(address={"state": "   "})
    assert cal.build_events(p2, 2026)


def test_build_events_non_usps_two_letter_code_hard_errors():
    # Two letters alone is not a state: "ZZ" / Canadian "BC" must not
    # silently produce a federal-only calendar (capabilities.py convention).
    for bad in ("ZZ", "BC", "CN"):
        with pytest.raises(cal.CalendarError):
            cal.build_events(_profile(address={"state": bad}), 2026)


# ---------------------------------------------------------------------------
# Extension events (F2 — /tax-extend sets flags.filed_extension)
# ---------------------------------------------------------------------------

def test_events_extension_dates_land_in_filing_year():
    events = cal.events_extension(2026)
    assert [e.date for e in events] == [
        datetime.date(2027, 4, 15),   # 4868 + payment due
        datetime.date(2027, 10, 15),  # extended filing deadline
    ]
    for e in events:
        assert e.category == "extension"


def test_events_extension_pay_by_language_and_ca_3519():
    apr = cal.events_extension(2026)[0]
    assert "NOT the time to pay" in apr.description
    assert "3519" not in apr.description  # non-CA: no FTB nuance
    apr_ca = cal.events_extension(2026, state="CA")[0]
    assert "FTB Form 3519" in apr_ca.description
    assert "AUTOMATIC" in apr_ca.description
    oct_ca = cal.events_extension(2026, state="CA")[1]
    assert "CA Form 540" in oct_ca.description


def test_build_events_extension_gated_on_flag():
    p = _profile()
    assert not [e for e in cal.build_events(p, 2026)
                if e.category == "extension"]
    p2 = _profile(flags={"filed_extension": True})
    ext = [e for e in cal.build_events(p2, 2026) if e.category == "extension"]
    assert len(ext) == 2
    # CA resident's April event carries the FTB 3519 nuance end-to-end.
    p3 = _profile(address={"state": "CA"}, flags={"filed_extension": True})
    ext_ca = [e for e in cal.build_events(p3, 2026)
              if e.category == "extension"]
    assert any("FTB Form 3519" in e.description for e in ext_ca)


def test_build_events_extension_flag_strict_bool():
    with pytest.raises(cal.CalendarError):
        cal.build_events(_profile(flags={"filed_extension": "yes"}), 2026)


def test_extension_events_serialize_with_category():
    p = _profile(flags={"filed_extension": True})
    ics = cal.to_ics(cal.build_events(p, 2026))
    assert "CATEGORIES:EXTENSION" in ics


# ---------------------------------------------------------------------------
# L1 — §7503 next-business-day rollover
# ---------------------------------------------------------------------------

def test_1040es_federal_rolls_past_weekend_and_dc_holiday():
    # Apr 15 2028 is a Saturday → next business day skips the weekend AND the
    # Mon Apr 17 DC Emancipation-Day observance, landing on Tue Apr 18.
    q1 = next(e for e in cal.events_1040es(2028) if e.quarter_label == "Q1")
    assert q1.date == datetime.date(2028, 4, 18)
    assert "next business day" in q1.description


def test_ca_540es_state_rolls_weekend_only_not_dc_holiday():
    # Apr 15 2023 is a Saturday. FEDERAL would roll to Apr 18 (past the Mon
    # Apr 17 DC Emancipation observance), but a CA (state) deadline rolls past
    # the WEEKEND ONLY → Mon Apr 17, because DC holidays don't bind CA.
    q1 = next(e for e in cal.events_ca_es(2023) if e.quarter_label == "Q1")
    assert q1.date == datetime.date(2023, 4, 17)
    assert "State-specific legal holidays are not modeled" in q1.description


def test_rmd_dec31_does_not_roll():
    # Dec 31 2028 is a Sunday, but an RMD is a distribution deadline, not a
    # §7503 file/pay act — it must NOT roll into the next year.
    ev = cal.events_rmd(2028, primary_subject_to_rmd=True,
                        spouse_subject_to_rmd=False)
    assert ev[0].date == datetime.date(2028, 12, 31)
    assert "next business day" not in ev[0].description


def test_no_observed_note_when_date_falls_on_a_weekday():
    # Apr 15 2027 is a Thursday — no roll, no observed-date note.
    q1 = next(e for e in cal.events_1040es(2027) if e.quarter_label == "Q1")
    assert q1.date == datetime.date(2027, 4, 15)
    assert "next business day" not in q1.description


def test_ptet_ny_election_matches_ptet_module_rolled_date():
    # tax_calendar and ptet must print the SAME rolled date for the NY election
    # (L1 requirement: wire both through the rollover together). Mar 15 2026 =
    # Sunday → Mar 16 in both.
    from agents.lib import ptet
    cal_election = next(e for e in cal.events_ptet(2026, state="NY",
                                                   has_k1_income=True)
                        if "election" in e.uid)
    assert cal_election.date == datetime.date(2026, 3, 16)
    ptet_date, _ = ptet._election_deadline("NY", 2026)
    assert ptet_date == "March 16, 2026"
