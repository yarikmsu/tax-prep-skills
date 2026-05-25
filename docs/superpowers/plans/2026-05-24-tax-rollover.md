# `/tax-rollover` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an archive-then-rollover workflow that pulls prior-year carryforwards, identity, dependents (with deterministic age tick), and recurring payer/account list from `archive/{year}/result.json` into a freshly initialized current-year workspace.

**Architecture:** Two new Python modules in `agents/lib/` (deterministic logic, fully tested) wrapped by two thin Markdown skills (orchestration via `AskUserQuestion`). One existing skill (`tax-setup.md`) gets a refusal check and an auto-invoke step. One template (`taxpayer_profile_template.json`) gains a new `expected_sources` section.

**Tech Stack:** Python 3.10+ stdlib (json, pathlib, dataclasses, datetime), pytest, Claude Code skills (Markdown).

**Spec:** `docs/superpowers/specs/2026-05-24-tax-rollover-design.md`
**Branch:** continue on `feat/tax-rollover-spec` (rename to `feat/tax-rollover` after merge, or just keep)
**Working directory:** `/path/to/tax-prep-skills`

---

## File Structure

| Path | Action | Purpose |
|---|---|---|
| `agents/lib/archive.py` | Create | Validated file moves: root → `archive/{year}/` |
| `agents/lib/rollover.py` | Create | `FIELD_MAP`, dataclasses, `load_prior`, `compute_diff`, `apply_changes`, CLI |
| `agents/skills/tax-archive.md` | Create | Thin orchestration: validate year, call archive.py |
| `agents/skills/tax-rollover.md` | Create | Orchestration: call rollover.py diff → AskUserQuestion per category → apply |
| `agents/skills/tax-setup.md` | Modify | Add step 0 (refuse if unarchived prior year) + step 9.5 (auto-invoke rollover) |
| `agents/templates/taxpayer_profile_template.json` | Modify | Add `expected_sources` section |
| `tests/test_archive.py` | Create | ~10 unit tests for archive module |
| `tests/test_rollover.py` | Create | ~25 unit tests + 3 integration tests for rollover module |
| `tests/fixtures/rollover/prior_2024_clean/` | Create | Fixture: synthetic TY2024 MFJ return |
| `tests/fixtures/rollover/prior_2024_drift_forward/` | Create | Fixture: prior + unknown field |
| `tests/fixtures/rollover/prior_2024_drift_backward/` | Create | Fixture: prior missing QBI section |
| `tests/fixtures/rollover/current_2025_empty/` | Create | Fixture: freshly initialized TY2025 |
| `tests/fixtures/rollover/current_2025_partial/` | Create | Fixture: TY2025 with some fields filled |
| `CLAUDE.md` | Modify | Architecture diagram + test count bump (90 → ~125) |

---

## Task 1: Add `expected_sources` to taxpayer_profile template

**Files:**
- Modify: `agents/templates/taxpayer_profile_template.json`
- Test: (validated in Task 4's FIELD_MAP integrity test)

- [ ] **Step 1: Add the new section to the template**

Edit `agents/templates/taxpayer_profile_template.json`. After line 91 (`"foreign_currencies": []`), add:

```json
,
  "expected_sources": {
    "_comment": "Payers/institutions seen in prior year. Informs /tax-parse to flag missing documents. EINs are public business identifiers — full values stored (not last-4-only like individual SSNs).",
    "w2_employers": [],
    "interest_payers": [],
    "dividend_payers": [],
    "brokerages": [],
    "foreign_accounts": []
  }
```

(Make sure the preceding `"foreign_currencies": []` line gains a trailing comma.)

- [ ] **Step 2: Validate JSON parses**

Run: `python3 -c "import json; json.load(open('agents/templates/taxpayer_profile_template.json'))"`
Expected: no output, exit 0.

- [ ] **Step 3: Commit**

```bash
git add agents/templates/taxpayer_profile_template.json
git commit -m "feat(rollover): add expected_sources to taxpayer profile template"
```

---

## Task 2: Create fixture workspaces for rollover tests

**Files:**
- Create: `tests/fixtures/rollover/prior_2024_clean/{result.json, taxpayer_profile.json, tax_law_2024.json}`
- Create: `tests/fixtures/rollover/prior_2024_drift_forward/result.json`
- Create: `tests/fixtures/rollover/prior_2024_drift_backward/result.json`
- Create: `tests/fixtures/rollover/current_2025_empty/{result.json, taxpayer_profile.json, tax_law_2025.json}`
- Create: `tests/fixtures/rollover/current_2025_partial/{result.json, taxpayer_profile.json}`

- [ ] **Step 1: Create the directory structure**

```bash
mkdir -p tests/fixtures/rollover/{prior_2024_clean,prior_2024_drift_forward,prior_2024_drift_backward,current_2025_empty,current_2025_partial}
```

- [ ] **Step 2: Write `prior_2024_clean/result.json`** (synthetic MFJ return; $4K capital loss, two dependents age 16 and 21, one W-2 employer, one foreign account)

```json
{
  "meta": {
    "tax_year": 2024,
    "prepared": "2025-04-01",
    "filing_status": "MFJ",
    "standard_deduction": 29200
  },
  "taxpayers": {
    "primary": {"name": "Test User", "ssn_last4": "1234", "dob": "1980-01-01", "occupation": "Engineer"},
    "spouse": {"name": "Test Spouse", "ssn_last4": "5678", "dob": "1982-05-15", "occupation": "Teacher"},
    "address": "100 Main St, Anytown, CA 94000"
  },
  "form_1040": {"line_34_overpayment": 0, "line_35a_refund": 0},
  "schedule_d": {
    "line_16_combined": -4000
  },
  "form_8995_qbi": {"line_16_qbi_loss_carryforward": -800},
  "form_1116_ftc": {
    "category": "General category income (passive)",
    "foreign_tax_paid_usd": 1200,
    "ftc_limitation": 900,
    "ftc_allowed": 900
  },
  "w2_summary": {"employer": "Acme Corp", "employer_ein": "12-3456789"},
  "interest_detail": {"items": [{"payer": "Chase Bank"}, {"payer": "Foreign Bank EU", "country": "DE"}]},
  "dividend_detail": {"items": [{"payer": "Vanguard"}]},
  "form_8938_fatca": {"accounts": [{"institution": "Foreign Bank EU", "country_code": "DE", "account_last4": "9999"}]},
  "fbar_fincen_114": {"accounts": [{"institution": "Foreign Bank EU", "country_code": "DE", "account_last4": "9999"}]}
}
```

- [ ] **Step 3: Write `prior_2024_clean/taxpayer_profile.json`** (two dependents — ages 16 and 21 at end of TY2024)

```json
{
  "tax_year": 2024,
  "filing_status": "MFJ",
  "primary": {"first_name": "Test", "middle_name": null, "last_name": "User", "ssn_last4": "1234", "dob": "1980-01-01", "occupation": "Engineer"},
  "spouse": {"first_name": "Test", "middle_name": null, "last_name": "Spouse", "ssn_last4": "5678", "dob": "1982-05-15", "occupation": "Teacher"},
  "address": {"street": "100 Main St", "city": "Anytown", "state": "CA", "zip": "94000"},
  "dependents": [
    {"first_name": "Alice", "last_name": "User", "ssn_last4": "1111", "dob": "2008-03-15", "relationship": "Daughter", "months_lived_with_you": 12, "ctc_eligible": true},
    {"first_name": "Bob", "last_name": "User", "ssn_last4": "2222", "dob": "2003-07-01", "relationship": "Son", "months_lived_with_you": 12, "ctc_eligible": false}
  ],
  "flags": {"has_foreign_accounts": true, "has_capital_gains": true, "has_foreign_tax_paid": true},
  "prior_year": {}
}
```

- [ ] **Step 4: Write `prior_2024_clean/tax_law_2024.json`** (minimal — only fields rollover reads)

```json
{
  "year": 2024,
  "dependent_age_thresholds": {"ctc_age": 17, "qualifying_child_general": 19, "qualifying_child_student": 24}
}
```

- [ ] **Step 5: Write `prior_2024_drift_forward/result.json`** (clean + one unknown field)

Copy `prior_2024_clean/result.json` to `prior_2024_drift_forward/result.json`, then add inside the top-level object: `"unknown_future_field": {"some_data": 42}`.

- [ ] **Step 6: Write `prior_2024_drift_backward/result.json`** (clean minus the `form_8995_qbi` section)

Copy `prior_2024_clean/result.json` to `prior_2024_drift_backward/result.json` and delete the `form_8995_qbi` key.

- [ ] **Step 7: Write `current_2025_empty/result.json`** (matches `result_template.json` after `/tax-setup 2025` init)

```json
{
  "meta": {"tax_year": 2025, "filing_status": "MFJ", "standard_deduction": 31500},
  "taxpayers": {"primary": {"name": null, "ssn_last4": null, "dob": null, "occupation": null}, "spouse": {"name": null, "ssn_last4": null, "dob": null, "occupation": null}, "address": null}
}
```

- [ ] **Step 8: Write `current_2025_empty/taxpayer_profile.json`** (empty/null fields)

```json
{
  "tax_year": 2025,
  "filing_status": "MFJ",
  "primary": {"first_name": null, "middle_name": null, "last_name": null, "ssn_last4": null, "dob": null, "occupation": null},
  "spouse": {"first_name": null, "middle_name": null, "last_name": null, "ssn_last4": null, "dob": null, "occupation": null},
  "address": {"street": null, "city": null, "state": null, "zip": null},
  "dependents": [],
  "flags": {},
  "prior_year": {"capital_loss_carryforward": 0, "qbi_loss_carryforward": 0, "unused_ftc_carryforward": 0, "estimated_payments_applied": 0},
  "expected_sources": {"w2_employers": [], "interest_payers": [], "dividend_payers": [], "brokerages": [], "foreign_accounts": []}
}
```

- [ ] **Step 9: Write `current_2025_empty/tax_law_2025.json`**

```json
{"year": 2025, "dependent_age_thresholds": {"ctc_age": 17, "qualifying_child_general": 19, "qualifying_child_student": 24}}
```

- [ ] **Step 10: Write `current_2025_partial/`** (same as empty but with $3K capital loss carryforward already filled — used for conflict testing)

Copy `current_2025_empty/result.json` to `current_2025_partial/result.json` and `current_2025_empty/taxpayer_profile.json` to `current_2025_partial/taxpayer_profile.json`. In the partial profile, change `prior_year.capital_loss_carryforward` from `0` to `3000`.

- [ ] **Step 11: Verify all fixtures parse as JSON**

Run: `python3 -c "import json, pathlib; [json.load(open(p)) for p in pathlib.Path('tests/fixtures/rollover').rglob('*.json')]; print('OK')"`
Expected: `OK`

- [ ] **Step 12: Commit**

```bash
git add tests/fixtures/rollover/
git commit -m "test(rollover): add fixture workspaces for prior/current year scenarios"
```

---

## Task 3: Build `archive.py` — module + tests

**Files:**
- Create: `agents/lib/archive.py`
- Create: `tests/test_archive.py`

- [ ] **Step 1: Write the failing test file**

Create `tests/test_archive.py`:

```python
#!/usr/bin/env python3
"""Tests for agents/lib/archive.py — file moves for /tax-archive."""

import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agents.lib.archive import archive_year, ArchiveError


@pytest.fixture
def workspace(tmp_path):
    """Build a synthetic root workspace with TY2024 files."""
    (tmp_path / "result.json").write_text(json.dumps({"meta": {"tax_year": 2024}}))
    (tmp_path / "taxpayer_profile.json").write_text(json.dumps({"tax_year": 2024}))
    (tmp_path / "answers.md").write_text("# Answers\n")
    (tmp_path / "tax_law_2024.json").write_text(json.dumps({"year": 2024}))
    (tmp_path / "summaries").mkdir()
    (tmp_path / "summaries" / "w2.md").write_text("W-2 summary")
    (tmp_path / "documents").mkdir()
    (tmp_path / "documents" / "w2.pdf").write_bytes(b"%PDF-1.4 fake")
    return tmp_path


def test_happy_path_moves_files(workspace):
    archive_year(2024, root=workspace)
    archive = workspace / "archive" / "2024"
    assert (archive / "result.json").exists()
    assert (archive / "taxpayer_profile.json").exists()
    assert (archive / "answers.md").exists()
    assert (archive / "tax_law_2024.json").exists()
    assert (archive / "summaries" / "w2.md").exists()
    assert (archive / "documents" / "w2.pdf").exists()
    # Root left clean
    assert not (workspace / "result.json").exists()
    assert not (workspace / "summaries").exists()


def test_refuses_if_archive_exists(workspace):
    (workspace / "archive" / "2024").mkdir(parents=True)
    with pytest.raises(ArchiveError, match="already exists"):
        archive_year(2024, root=workspace)
    # Root untouched
    assert (workspace / "result.json").exists()


def test_refuses_if_no_result_json(tmp_path):
    with pytest.raises(ArchiveError, match="No result.json"):
        archive_year(2024, root=tmp_path)


def test_refuses_if_year_mismatch(workspace):
    (workspace / "result.json").write_text(json.dumps({"meta": {"tax_year": 2023}}))
    with pytest.raises(ArchiveError, match="TY2023.*not TY2024"):
        archive_year(2024, root=workspace)
    assert (workspace / "result.json").exists()


def test_partial_file_set_succeeds(tmp_path):
    (tmp_path / "result.json").write_text(json.dumps({"meta": {"tax_year": 2024}}))
    archive_year(2024, root=tmp_path)
    assert (tmp_path / "archive" / "2024" / "result.json").exists()


def test_chmod_600_on_archived_files(workspace):
    archive_year(2024, root=workspace)
    mode = (workspace / "archive" / "2024" / "result.json").stat().st_mode
    assert (mode & 0o777) == 0o600


def test_rejects_non_integer_year(workspace):
    with pytest.raises(ArchiveError, match="4-digit"):
        archive_year("2024; rm -rf /", root=workspace)


def test_rejects_out_of_range_year(workspace):
    with pytest.raises(ArchiveError, match="2000.*2099"):
        archive_year(1999, root=workspace)


def test_year_mismatch_error_names_both_years(workspace):
    (workspace / "result.json").write_text(json.dumps({"meta": {"tax_year": 2023}}))
    with pytest.raises(ArchiveError) as exc_info:
        archive_year(2024, root=workspace)
    assert "2023" in str(exc_info.value)
    assert "2024" in str(exc_info.value)


def test_dry_run_does_not_move(workspace):
    archive_year(2024, root=workspace, dry_run=True)
    assert (workspace / "result.json").exists()
    assert not (workspace / "archive" / "2024").exists()
```

- [ ] **Step 2: Run tests to verify they fail with import error**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_archive.py -v`
Expected: ImportError (module `agents.lib.archive` does not exist).

- [ ] **Step 3: Implement `agents/lib/archive.py`**

```python
#!/usr/bin/env python3
"""Archive current-year tax files into archive/{year}/ before /tax-setup wipes root.

Atomic: any mid-move failure rolls back. Sets chmod 600 on all moved files.
"""

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Union


class ArchiveError(Exception):
    """Raised when /tax-archive cannot safely proceed."""


@dataclass
class ArchiveReport:
    year: int
    moved: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    dry_run: bool = False


# Files that the toolkit creates per year and may want archived
EXPECTED_TOP_LEVEL_FILES = ("result.json", "taxpayer_profile.json", "answers.md")
EXPECTED_TOP_LEVEL_DIRS = ("summaries", "documents")


def _validate_year(year) -> int:
    if not isinstance(year, int):
        raise ArchiveError(f"Year must be a 4-digit integer, got {year!r}")
    if not (2000 <= year <= 2099):
        raise ArchiveError(f"Year {year} out of range (2000-2099)")
    return year


def archive_year(year, root: Path, dry_run: bool = False) -> ArchiveReport:
    """Move TY{year} files from root into root/archive/{year}/.

    Refuses if archive/{year}/ already exists. Refuses if root/result.json
    has meta.tax_year != year. Refuses if root/result.json missing.
    """
    year = _validate_year(year)
    root = Path(root).resolve()
    archive_dir = root / "archive" / str(year)

    if archive_dir.exists():
        raise ArchiveError(f"{archive_dir} already exists. Move or delete it first.")

    result_path = root / "result.json"
    if not result_path.exists():
        raise ArchiveError("No result.json at root — nothing to archive.")

    try:
        meta_year = json.loads(result_path.read_text()).get("meta", {}).get("tax_year")
    except json.JSONDecodeError as e:
        raise ArchiveError(f"result.json is malformed: {e}")
    if meta_year != year:
        raise ArchiveError(
            f"Root result.json is for TY{meta_year}, not TY{year}. Pass the correct year."
        )

    report = ArchiveReport(year=year, dry_run=dry_run)
    if dry_run:
        for name in EXPECTED_TOP_LEVEL_FILES + (f"tax_law_{year}.json",) + EXPECTED_TOP_LEVEL_DIRS:
            (report.moved if (root / name).exists() else report.skipped).append(name)
        return report

    archive_dir.mkdir(parents=True)
    moved_paths: list[tuple[Path, Path]] = []
    try:
        for name in EXPECTED_TOP_LEVEL_FILES + (f"tax_law_{year}.json",):
            src = root / name
            if src.exists():
                dst = archive_dir / name
                shutil.move(str(src), str(dst))
                os.chmod(dst, 0o600)
                moved_paths.append((src, dst))
                report.moved.append(name)
            else:
                report.skipped.append(name)
        for name in EXPECTED_TOP_LEVEL_DIRS:
            src = root / name
            if src.exists():
                dst = archive_dir / name
                shutil.move(str(src), str(dst))
                for p in dst.rglob("*"):
                    if p.is_file():
                        os.chmod(p, 0o600)
                moved_paths.append((src, dst))
                report.moved.append(name)
            else:
                report.skipped.append(name)
    except Exception as e:
        # Roll back any partial moves
        for src, dst in reversed(moved_paths):
            if dst.exists():
                shutil.move(str(dst), str(src))
        if archive_dir.exists() and not any(archive_dir.iterdir()):
            archive_dir.rmdir()
        raise ArchiveError(f"Move failed, rolled back: {e}") from e

    return report


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Usage: python3 -m agents.lib.archive {year}", file=sys.stderr)
        sys.exit(2)
    try:
        year = int(sys.argv[1])
    except ValueError:
        print(f"Year must be a 4-digit integer, got {sys.argv[1]!r}", file=sys.stderr)
        sys.exit(2)
    try:
        report = archive_year(year, root=Path.cwd())
    except ArchiveError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Archived {len(report.moved)} items to archive/{year}/")
    for name in report.moved:
        print(f"  ✓ {name}")
    for name in report.skipped:
        print(f"  - {name} (not present)")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_archive.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add agents/lib/archive.py tests/test_archive.py
git commit -m "feat(rollover): add archive.py for moving year files into archive/"
```

---

## Task 4: Build `rollover.py` — dataclasses, FIELD_MAP, load_prior

**Files:**
- Create: `agents/lib/rollover.py`
- Create: `tests/test_rollover.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rollover.py`:

```python
#!/usr/bin/env python3
"""Tests for agents/lib/rollover.py — diff/merge engine for /tax-rollover."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agents.lib.rollover import (
    FIELD_MAP,
    Change,
    PriorReturn,
    RolloverError,
    load_prior,
)

FIXTURES = Path(__file__).parent / "fixtures" / "rollover"


def test_field_map_entries_have_required_keys():
    for key, entry in FIELD_MAP.items():
        assert "category" in entry, f"FIELD_MAP[{key!r}] missing 'category'"
        assert "target_file" in entry, f"FIELD_MAP[{key!r}] missing 'target_file'"
        assert "target_path" in entry, f"FIELD_MAP[{key!r}] missing 'target_path'"


def test_field_map_categories_are_valid():
    valid = {"carryforwards", "identity", "dependents", "expected_sources"}
    for key, entry in FIELD_MAP.items():
        assert entry["category"] in valid, f"FIELD_MAP[{key!r}] has invalid category"


def test_field_map_no_duplicate_target_paths():
    paths = [(e["target_file"], e["target_path"]) for e in FIELD_MAP.values()]
    assert len(paths) == len(set(paths)), "FIELD_MAP has duplicate target paths"


def test_load_prior_reads_clean_fixture():
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    assert isinstance(prior, PriorReturn)
    assert prior.year == 2024
    assert prior.result["meta"]["tax_year"] == 2024
    assert prior.profile["filing_status"] == "MFJ"


def test_load_prior_refuses_year_mismatch():
    with pytest.raises(RolloverError, match="TY2024.*not TY2025"):
        load_prior(FIXTURES / "prior_2024_clean", year=2025)


def test_load_prior_refuses_missing_archive():
    with pytest.raises(RolloverError, match="does not exist"):
        load_prior(FIXTURES / "nonexistent", year=2024)


def test_load_prior_refuses_malformed_json(tmp_path):
    (tmp_path / "result.json").write_text("not json{{")
    (tmp_path / "taxpayer_profile.json").write_text("{}")
    with pytest.raises(RolloverError, match="malformed"):
        load_prior(tmp_path, year=2024)
```

- [ ] **Step 2: Run tests to verify they fail with ImportError**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_rollover.py -v`
Expected: ImportError (`agents.lib.rollover` does not exist).

- [ ] **Step 3: Implement the skeleton in `agents/lib/rollover.py`**

```python
#!/usr/bin/env python3
"""Rollover prior-year carryforwards, identity, dependents, and expected sources
from archive/{year}/ into current-year workspace files.

Pure functions for diff/merge logic; thin I/O wrappers at edges.
"""

import json
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any


class RolloverError(Exception):
    """Raised when /tax-rollover cannot safely proceed."""


# --- FIELD_MAP -------------------------------------------------------------
# Stable carryforward/identity keys → JSON paths in source and target files.
# Schema renames in newer toolkit versions don't break older archived returns
# because lookup is by stable key, not raw path.

FIELD_MAP: dict[str, dict] = {
    # Category 1: Carryforwards (dollar amounts)
    "capital_loss_carryforward": {
        "category": "carryforwards",
        "source_path": "schedule_d.line_16_combined",  # negative if net loss
        "target_file": "taxpayer_profile.json",
        "target_path": "prior_year.capital_loss_carryforward",
        "transform": "capital_loss_to_carryforward",
    },
    "qbi_loss_carryforward": {
        "category": "carryforwards",
        "source_path": "form_8995_qbi.line_16_qbi_loss_carryforward",
        "target_file": "taxpayer_profile.json",
        "target_path": "prior_year.qbi_loss_carryforward",
        "transform": "abs",
    },
    "unused_ftc_carryforward": {
        "category": "carryforwards",
        "source_path": "form_1116_ftc",
        "target_file": "taxpayer_profile.json",
        "target_path": "prior_year.unused_ftc_carryforward",
        "transform": "ftc_unused",
    },
    # Category 2: Static identity
    "primary_first_name": {
        "category": "identity",
        "source_path": "primary.first_name",
        "target_file": "taxpayer_profile.json",
        "target_path": "primary.first_name",
        "transform": None,
    },
    "primary_middle_name": {
        "category": "identity", "source_path": "primary.middle_name",
        "target_file": "taxpayer_profile.json", "target_path": "primary.middle_name",
        "transform": None,
    },
    "primary_last_name": {
        "category": "identity", "source_path": "primary.last_name",
        "target_file": "taxpayer_profile.json", "target_path": "primary.last_name",
        "transform": None,
    },
    "primary_ssn_last4": {
        "category": "identity", "source_path": "primary.ssn_last4",
        "target_file": "taxpayer_profile.json", "target_path": "primary.ssn_last4",
        "transform": None,
    },
    "primary_dob": {
        "category": "identity", "source_path": "primary.dob",
        "target_file": "taxpayer_profile.json", "target_path": "primary.dob",
        "transform": None,
    },
    "primary_occupation": {
        "category": "identity", "source_path": "primary.occupation",
        "target_file": "taxpayer_profile.json", "target_path": "primary.occupation",
        "transform": None,
    },
    "spouse_block": {
        "category": "identity", "source_path": "spouse",
        "target_file": "taxpayer_profile.json", "target_path": "spouse",
        "transform": "spouse_conditional",
    },
    "address_block": {
        "category": "identity", "source_path": "address",
        "target_file": "taxpayer_profile.json", "target_path": "address",
        "transform": None,
    },
    "filing_status": {
        "category": "identity", "source_path": "filing_status",
        "target_file": "taxpayer_profile.json", "target_path": "filing_status",
        "transform": "filing_status_ask",
    },
}


# --- Dataclasses -----------------------------------------------------------

@dataclass(frozen=True)
class Change:
    category: str
    json_path: str
    target_file: str
    prior_value: Any
    current_value: Any
    action_suggested: str  # "apply" | "ask" | "skip"
    reason: str


@dataclass
class PriorReturn:
    year: int
    result: dict
    profile: dict


# --- I/O ------------------------------------------------------------------

def _read_json(path: Path) -> dict:
    if not path.exists():
        raise RolloverError(f"{path} does not exist")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise RolloverError(f"{path.name} is malformed: {e}")


def load_prior(archive_dir: Path, year: int) -> PriorReturn:
    archive_dir = Path(archive_dir)
    if not archive_dir.exists():
        raise RolloverError(f"archive directory {archive_dir} does not exist")
    result = _read_json(archive_dir / "result.json")
    profile = _read_json(archive_dir / "taxpayer_profile.json")
    meta_year = result.get("meta", {}).get("tax_year")
    if meta_year != year:
        raise RolloverError(
            f"Archive at {archive_dir} is for TY{meta_year}, not TY{year}. "
            "Refusing to roll inconsistent data."
        )
    return PriorReturn(year=year, result=result, profile=profile)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_rollover.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add agents/lib/rollover.py tests/test_rollover.py
git commit -m "feat(rollover): add FIELD_MAP, dataclasses, and load_prior"
```

---

## Task 5: `compute_diff` — carryforwards category

**Files:**
- Modify: `agents/lib/rollover.py`
- Modify: `tests/test_rollover.py`

- [ ] **Step 1: Append failing tests to `tests/test_rollover.py`**

```python
from agents.lib.rollover import compute_diff, CurrentState


def _load_current(fixture_name: str) -> CurrentState:
    base = FIXTURES / fixture_name
    return CurrentState(
        result=json.loads((base / "result.json").read_text()),
        profile=json.loads((base / "taxpayer_profile.json").read_text()),
        tax_law=json.loads((base / "tax_law_2025.json").read_text()) if (base / "tax_law_2025.json").exists() else {},
    )


def test_compute_diff_capital_loss_carryforward():
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"carryforwards"}, tax_law_new=current.tax_law)
    changes = diff["carryforwards"]
    cap_loss = [c for c in changes if c.json_path == "prior_year.capital_loss_carryforward"]
    assert len(cap_loss) == 1
    # Prior schedule_d.line_16_combined = -4000; carryforward = abs(-4000) - 3000 = 1000
    assert cap_loss[0].prior_value == 1000
    assert cap_loss[0].action_suggested == "apply"


def test_compute_diff_qbi_loss_carryforward():
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"carryforwards"}, tax_law_new=current.tax_law)
    qbi = [c for c in diff["carryforwards"] if c.json_path == "prior_year.qbi_loss_carryforward"]
    assert len(qbi) == 1
    assert qbi[0].prior_value == 800  # abs(-800)


def test_compute_diff_ftc_unused():
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"carryforwards"}, tax_law_new=current.tax_law)
    ftc = [c for c in diff["carryforwards"] if c.json_path == "prior_year.unused_ftc_carryforward"]
    assert len(ftc) == 1
    # foreign_tax_paid_usd 1200 - ftc_allowed 900 = 300 unused
    assert ftc[0].prior_value == 300


def test_compute_diff_skips_zero_prior_values():
    """Prior with no capital loss → no diff entry for capital_loss_carryforward."""
    prior_dir = FIXTURES / "prior_2024_drift_backward"
    # Use clean fixture profile + drift_backward result (missing QBI section)
    prior = PriorReturn(
        year=2024,
        result=json.loads((prior_dir / "result.json").read_text()),
        profile=json.loads((FIXTURES / "prior_2024_clean" / "taxpayer_profile.json").read_text()),
    )
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"carryforwards"}, tax_law_new=current.tax_law)
    # form_8995_qbi missing → qbi entry should not appear (or appear as skip)
    qbi = [c for c in diff["carryforwards"] if c.json_path == "prior_year.qbi_loss_carryforward"]
    assert qbi == [] or qbi[0].action_suggested == "skip"


def test_compute_diff_handles_forward_schema_drift():
    """Prior result.json has an unknown extra top-level field — diff should still complete."""
    prior_dir = FIXTURES / "prior_2024_drift_forward"
    prior = PriorReturn(
        year=2024,
        result=json.loads((prior_dir / "result.json").read_text()),
        profile=json.loads((FIXTURES / "prior_2024_clean" / "taxpayer_profile.json").read_text()),
    )
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"carryforwards"}, tax_law_new=current.tax_law)
    # Capital loss still rolls correctly despite the unknown extra field
    cap_loss = [c for c in diff["carryforwards"] if c.json_path == "prior_year.capital_loss_carryforward"]
    assert len(cap_loss) == 1
    assert cap_loss[0].prior_value == 1000


def test_compute_diff_conflict_flags_ask():
    """Re-rollover against current_2025_partial: $3K already filled → action_suggested == 'ask'."""
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    current = _load_current("current_2025_partial")
    diff = compute_diff(prior, current, categories={"carryforwards"}, tax_law_new=current.tax_law)
    cap_loss = [c for c in diff["carryforwards"] if c.json_path == "prior_year.capital_loss_carryforward"]
    assert len(cap_loss) == 1
    assert cap_loss[0].current_value == 3000
    assert cap_loss[0].prior_value == 1000
    assert cap_loss[0].action_suggested == "ask"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_rollover.py -v -k "compute_diff_capital or compute_diff_qbi or compute_diff_ftc or compute_diff_skips or compute_diff_conflict"`
Expected: ImportError on `compute_diff` / `CurrentState`.

- [ ] **Step 3: Add `CurrentState`, `compute_diff` scaffold, and carryforward logic to `agents/lib/rollover.py`**

Append to `agents/lib/rollover.py`:

```python
@dataclass
class CurrentState:
    result: dict
    profile: dict
    tax_law: dict


def _get_path(d: dict, dotted: str, default=None):
    """Read a.b.c from nested dict; return default if any segment missing."""
    cur = d
    for seg in dotted.split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return default
        cur = cur[seg]
    return cur


def _set_path(d: dict, dotted: str, value: Any) -> None:
    """Write value at a.b.c in nested dict, creating intermediate dicts as needed."""
    cur = d
    segs = dotted.split(".")
    for seg in segs[:-1]:
        if seg not in cur or not isinstance(cur[seg], dict):
            cur[seg] = {}
        cur = cur[seg]
    cur[segs[-1]] = value


def _transform_capital_loss(raw):
    """schedule_d.line_16_combined is negative if net loss; cf = abs(loss) - $3K used."""
    if raw is None or raw >= 0:
        return 0
    cf = abs(raw) - 3000
    return max(cf, 0)


def _transform_abs(raw):
    return abs(raw) if isinstance(raw, (int, float)) else 0


def _transform_ftc_unused(raw):
    """raw is the form_1116_ftc block (dict). Unused = paid - allowed."""
    if not isinstance(raw, dict):
        return 0
    paid = raw.get("foreign_tax_paid_usd", 0) or 0
    allowed = raw.get("ftc_allowed", 0) or 0
    return max(paid - allowed, 0)


TRANSFORMS = {
    "capital_loss_to_carryforward": _transform_capital_loss,
    "abs": _transform_abs,
    "ftc_unused": _transform_ftc_unused,
}


def _diff_carryforwards(prior: PriorReturn, current: CurrentState) -> list[Change]:
    out: list[Change] = []
    for key, entry in FIELD_MAP.items():
        if entry["category"] != "carryforwards":
            continue
        raw = _get_path(prior.result, entry["source_path"])
        if raw is None:
            # Schema drift backward: missing source path
            continue
        transform = TRANSFORMS.get(entry.get("transform"))
        prior_value = transform(raw) if transform else raw
        if not prior_value:  # zero or empty → no diff entry
            continue
        target_doc = current.profile if entry["target_file"] == "taxpayer_profile.json" else current.result
        current_value = _get_path(target_doc, entry["target_path"], default=0)
        if current_value and current_value != prior_value:
            action = "ask"
            reason = f"Current value ({current_value}) differs from prior ({prior_value})"
        else:
            action = "apply"
            reason = f"Pulled from {entry['source_path']}"
        out.append(Change(
            category="carryforwards",
            json_path=entry["target_path"],
            target_file=entry["target_file"],
            prior_value=prior_value,
            current_value=current_value,
            action_suggested=action,
            reason=reason,
        ))
    return out


def compute_diff(
    prior: PriorReturn,
    current: CurrentState,
    categories: set[str],
    tax_law_new: dict,
) -> dict[str, list[Change]]:
    out: dict[str, list[Change]] = {}
    if "carryforwards" in categories:
        out["carryforwards"] = _diff_carryforwards(prior, current)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_rollover.py -v`
Expected: 13 passed (7 prior + 6 new).

- [ ] **Step 5: Commit**

```bash
git add agents/lib/rollover.py tests/test_rollover.py
git commit -m "feat(rollover): compute_diff for carryforwards category"
```

---

## Task 6: `compute_diff` — identity category

**Files:**
- Modify: `agents/lib/rollover.py`
- Modify: `tests/test_rollover.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_rollover.py`:

```python
def test_compute_diff_identity_names_roll():
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"identity"}, tax_law_new=current.tax_law)
    first = [c for c in diff["identity"] if c.json_path == "primary.first_name"]
    assert len(first) == 1
    assert first[0].prior_value == "Test"
    assert first[0].action_suggested == "apply"


def test_compute_diff_identity_filing_status_always_asks():
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"identity"}, tax_law_new=current.tax_law)
    fs = [c for c in diff["identity"] if c.json_path == "filing_status"]
    assert len(fs) == 1
    assert fs[0].action_suggested == "ask"


def test_compute_diff_identity_drops_spouse_when_single():
    """If new filing_status is Single, spouse block should not roll forward."""
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    current = _load_current("current_2025_empty")
    current.profile["filing_status"] = "Single"
    diff = compute_diff(prior, current, categories={"identity"}, tax_law_new=current.tax_law)
    spouse = [c for c in diff["identity"] if c.json_path == "spouse"]
    # Either no diff entry, OR entry with action_suggested == "skip" and a warning reason
    if spouse:
        assert spouse[0].action_suggested == "skip"
        assert "filing status" in spouse[0].reason.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_rollover.py -v -k "identity"`
Expected: KeyError on `diff["identity"]` (identity branch not implemented).

- [ ] **Step 3: Implement `_diff_identity` in `agents/lib/rollover.py`**

Add this function above `compute_diff`:

```python
def _diff_identity(prior: PriorReturn, current: CurrentState) -> list[Change]:
    out: list[Change] = []
    new_filing_status = current.profile.get("filing_status")
    spouse_ok_statuses = {"MFJ", "MFS", "QSS"}

    for key, entry in FIELD_MAP.items():
        if entry["category"] != "identity":
            continue
        raw = _get_path(prior.profile, entry["source_path"])
        target_doc = current.profile  # all identity targets live in profile
        current_value = _get_path(target_doc, entry["target_path"])
        transform = entry.get("transform")

        if transform == "spouse_conditional":
            if new_filing_status not in spouse_ok_statuses:
                if isinstance(raw, dict) and any(raw.values()):
                    out.append(Change(
                        category="identity",
                        json_path=entry["target_path"],
                        target_file=entry["target_file"],
                        prior_value=raw,
                        current_value=current_value,
                        action_suggested="skip",
                        reason=f"Spouse block dropped: new filing status {new_filing_status} does not permit spouse",
                    ))
                continue

        if transform == "filing_status_ask":
            # Filing status always surfaces as ask (never silently overwritten)
            if raw is not None and raw != current_value:
                out.append(Change(
                    category="identity",
                    json_path=entry["target_path"],
                    target_file=entry["target_file"],
                    prior_value=raw,
                    current_value=current_value,
                    action_suggested="ask",
                    reason="Filing status changes affect the entire return — confirm",
                ))
            continue

        if raw is None:
            continue
        if current_value == raw:
            continue
        action = "ask" if (current_value not in (None, "", 0)) else "apply"
        out.append(Change(
            category="identity",
            json_path=entry["target_path"],
            target_file=entry["target_file"],
            prior_value=raw,
            current_value=current_value,
            action_suggested=action,
            reason=f"Pulled from prior profile.{entry['source_path']}",
        ))
    return out
```

Then update `compute_diff` to dispatch identity:

```python
def compute_diff(
    prior: PriorReturn,
    current: CurrentState,
    categories: set[str],
    tax_law_new: dict,
) -> dict[str, list[Change]]:
    out: dict[str, list[Change]] = {}
    if "carryforwards" in categories:
        out["carryforwards"] = _diff_carryforwards(prior, current)
    if "identity" in categories:
        out["identity"] = _diff_identity(prior, current)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_rollover.py -v`
Expected: 16 passed.

- [ ] **Step 5: Commit**

```bash
git add agents/lib/rollover.py tests/test_rollover.py
git commit -m "feat(rollover): compute_diff for identity category with filing-status logic"
```

---

## Task 7: `compute_diff` — dependents with age tick

**Files:**
- Modify: `agents/lib/rollover.py`
- Modify: `tests/test_rollover.py`

- [ ] **Step 1: Append failing tests**

```python
def test_age_tick_16_to_17_flips_ctc_eligible():
    """Alice DOB 2008-03-15: age 16 at end of 2024, age 17 at end of 2025."""
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"dependents"}, tax_law_new=current.tax_law)
    alice = [c for c in diff["dependents"] if c.prior_value.get("first_name") == "Alice"][0]
    assert alice.prior_value["ctc_eligible"] is False  # already flipped by age tick
    assert "CTC" in alice.reason or "17" in alice.reason
    assert alice.action_suggested == "ask"  # surfaces because of warning


def test_age_tick_21_to_22_no_change():
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"dependents"}, tax_law_new=current.tax_law)
    bob = [c for c in diff["dependents"] if c.prior_value.get("first_name") == "Bob"][0]
    assert bob.prior_value["ctc_eligible"] is False
    assert bob.action_suggested == "apply"


def test_age_tick_malformed_dob_skips_without_crash():
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    prior.profile["dependents"][0]["dob"] = "not-a-date"
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"dependents"}, tax_law_new=current.tax_law)
    alice = [c for c in diff["dependents"] if c.prior_value.get("first_name") == "Alice"]
    # Either skipped entirely OR included with action_suggested == "skip"
    if alice:
        assert alice[0].action_suggested == "skip"
        assert "DOB" in alice[0].reason or "date" in alice[0].reason.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_rollover.py -v -k "age_tick"`
Expected: KeyError on `diff["dependents"]`.

- [ ] **Step 3: Implement `_diff_dependents` and age-tick math**

Add to `agents/lib/rollover.py`:

```python
def _age_at_end_of_year(dob_str: str, year: int) -> int:
    """Return age at Dec 31 of given year. Raises ValueError on malformed DOB."""
    dob = date.fromisoformat(dob_str)
    end = date(year, 12, 31)
    return end.year - dob.year - ((end.month, end.day) < (dob.month, dob.day))


def _diff_dependents(prior: PriorReturn, current: CurrentState, tax_law_new: dict) -> list[Change]:
    out: list[Change] = []
    prior_deps = prior.profile.get("dependents", []) or []
    new_year = current.profile.get("tax_year") or _get_path(current.result, "meta.tax_year")
    ctc_age = tax_law_new.get("dependent_age_thresholds", {}).get("ctc_age", 17)
    qc_general = tax_law_new.get("dependent_age_thresholds", {}).get("qualifying_child_general", 19)
    qc_student = tax_law_new.get("dependent_age_thresholds", {}).get("qualifying_child_student", 24)

    for idx, dep in enumerate(prior_deps):
        if not isinstance(dep, dict):
            continue
        dob = dep.get("dob")
        if not dob:
            continue
        try:
            new_age = _age_at_end_of_year(dob, new_year)
        except (ValueError, TypeError):
            out.append(Change(
                category="dependents",
                json_path=f"dependents[{idx}]",
                target_file="taxpayer_profile.json",
                prior_value=dep,
                current_value=None,
                action_suggested="skip",
                reason=f"Malformed DOB: {dob!r} — skipping age tick",
            ))
            continue

        new_dep = dict(dep)
        new_dep["ctc_eligible"] = new_age < ctc_age
        warnings = []
        if dep.get("ctc_eligible") and not new_dep["ctc_eligible"]:
            warnings.append(f"turned {new_age} — no longer CTC-eligible (now qualifies for $500 Credit for Other Dependents if still a dependent)")
        if new_age == qc_general:
            warnings.append(f"turned {new_age} — qualifying-child general age cutoff")
        if new_age == qc_student:
            warnings.append(f"turned {new_age} — qualifying-child student age cutoff")

        action = "ask" if warnings else "apply"
        reason = "; ".join(warnings) if warnings else f"Age tick {new_age} — no transitions"
        out.append(Change(
            category="dependents",
            json_path=f"dependents[{idx}]",
            target_file="taxpayer_profile.json",
            prior_value=new_dep,
            current_value=None,
            action_suggested=action,
            reason=reason,
        ))
    return out
```

Update `compute_diff`:

```python
    if "dependents" in categories:
        out["dependents"] = _diff_dependents(prior, current, tax_law_new)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_rollover.py -v`
Expected: 19 passed.

- [ ] **Step 5: Commit**

```bash
git add agents/lib/rollover.py tests/test_rollover.py
git commit -m "feat(rollover): compute_diff for dependents with deterministic age tick"
```

---

## Task 8: `compute_diff` — expected_sources (recurring entities)

**Files:**
- Modify: `agents/lib/rollover.py`
- Modify: `tests/test_rollover.py`

- [ ] **Step 1: Append failing tests**

```python
def test_expected_sources_w2_employer_with_full_ein():
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"expected_sources"}, tax_law_new=current.tax_law)
    w2 = [c for c in diff["expected_sources"] if c.json_path == "expected_sources.w2_employers"][0]
    assert w2.prior_value == [{"name": "Acme Corp", "ein": "12-3456789"}]


def test_expected_sources_foreign_account_dedup():
    """8938 and FBAR fixtures both contain the same Foreign Bank EU account — only one row out."""
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"expected_sources"}, tax_law_new=current.tax_law)
    fa = [c for c in diff["expected_sources"] if c.json_path == "expected_sources.foreign_accounts"][0]
    assert len(fa.prior_value) == 1
    assert fa.prior_value[0]["institution"] == "Foreign Bank EU"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_rollover.py -v -k "expected_sources"`
Expected: KeyError on `diff["expected_sources"]`.

- [ ] **Step 3: Implement `_diff_expected_sources`**

Add to `agents/lib/rollover.py`:

```python
def _diff_expected_sources(prior: PriorReturn, current: CurrentState) -> list[Change]:
    pr = prior.result
    w2_employers = []
    w2 = pr.get("w2_summary") or {}
    if w2.get("employer"):
        w2_employers.append({"name": w2["employer"], "ein": w2.get("employer_ein", "")})

    interest_payers = [
        {"name": item["payer"], "country_code": item.get("country", "US")}
        for item in (pr.get("interest_detail", {}).get("items") or [])
        if item.get("payer")
    ]
    dividend_payers = [
        {"name": item["payer"]}
        for item in (pr.get("dividend_detail", {}).get("items") or [])
        if item.get("payer")
    ]
    # Foreign accounts: dedup by (institution, country_code, account_last4)
    seen: set[tuple] = set()
    foreign_accounts = []
    for src in ("form_8938_fatca", "fbar_fincen_114"):
        for acc in (pr.get(src, {}).get("accounts") or []):
            key = (acc.get("institution"), acc.get("country_code"), acc.get("account_last4"))
            if all(k is not None for k in key) and key not in seen:
                seen.add(key)
                foreign_accounts.append({
                    "institution": acc["institution"],
                    "country_code": acc["country_code"],
                    "account_last4": acc["account_last4"],
                })

    rows = [
        ("expected_sources.w2_employers", w2_employers),
        ("expected_sources.interest_payers", interest_payers),
        ("expected_sources.dividend_payers", dividend_payers),
        ("expected_sources.brokerages", []),
        ("expected_sources.foreign_accounts", foreign_accounts),
    ]
    out: list[Change] = []
    for path, value in rows:
        if not value:
            continue
        current_value = _get_path(current.profile, path, default=[])
        action = "ask" if current_value else "apply"
        out.append(Change(
            category="expected_sources",
            json_path=path,
            target_file="taxpayer_profile.json",
            prior_value=value,
            current_value=current_value,
            action_suggested=action,
            reason="Derived from prior result.json payer/account lists",
        ))
    return out
```

Update `compute_diff`:

```python
    if "expected_sources" in categories:
        out["expected_sources"] = _diff_expected_sources(prior, current)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_rollover.py -v`
Expected: 21 passed.

- [ ] **Step 5: Commit**

```bash
git add agents/lib/rollover.py tests/test_rollover.py
git commit -m "feat(rollover): compute_diff for expected_sources (recurring entities)"
```

---

## Task 9: `apply_changes` — atomic writes

**Files:**
- Modify: `agents/lib/rollover.py`
- Modify: `tests/test_rollover.py`

- [ ] **Step 1: Append failing tests**

```python
from agents.lib.rollover import apply_changes, WritePaths


def test_apply_changes_writes_carryforward(tmp_path):
    # Copy current_2025_empty into tmp
    import shutil
    shutil.copytree(FIXTURES / "current_2025_empty", tmp_path / "ws")
    targets = WritePaths(
        result=tmp_path / "ws" / "result.json",
        profile=tmp_path / "ws" / "taxpayer_profile.json",
    )
    changes = {
        "carryforwards": [Change(
            category="carryforwards",
            json_path="prior_year.capital_loss_carryforward",
            target_file="taxpayer_profile.json",
            prior_value=1000,
            current_value=0,
            action_suggested="apply",
            reason="test",
        )]
    }
    apply_changes(changes, category_decisions={"carryforwards": "apply"}, targets=targets)
    profile = json.loads(targets.profile.read_text())
    assert profile["prior_year"]["capital_loss_carryforward"] == 1000


def test_apply_changes_skipped_category_writes_nothing(tmp_path):
    import shutil
    shutil.copytree(FIXTURES / "current_2025_empty", tmp_path / "ws")
    targets = WritePaths(result=tmp_path / "ws" / "result.json", profile=tmp_path / "ws" / "taxpayer_profile.json")
    original = targets.profile.read_text()
    changes = {"carryforwards": [Change(
        category="carryforwards", json_path="prior_year.capital_loss_carryforward",
        target_file="taxpayer_profile.json", prior_value=1000, current_value=0,
        action_suggested="apply", reason="test")]}
    apply_changes(changes, category_decisions={"carryforwards": "skip"}, targets=targets)
    assert targets.profile.read_text() == original


def test_apply_changes_atomic_rollback_on_failure(tmp_path, monkeypatch):
    import shutil
    shutil.copytree(FIXTURES / "current_2025_empty", tmp_path / "ws")
    targets = WritePaths(result=tmp_path / "ws" / "result.json", profile=tmp_path / "ws" / "taxpayer_profile.json")
    original = targets.profile.read_text()

    real_replace = os.replace
    calls = {"n": 0}
    def flaky_replace(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] >= 1:
            raise OSError("simulated failure")
        return real_replace(*args, **kwargs)
    monkeypatch.setattr("os.replace", flaky_replace)

    changes = {"carryforwards": [Change(
        category="carryforwards", json_path="prior_year.capital_loss_carryforward",
        target_file="taxpayer_profile.json", prior_value=1000, current_value=0,
        action_suggested="apply", reason="test")]}
    with pytest.raises(RolloverError):
        apply_changes(changes, category_decisions={"carryforwards": "apply"}, targets=targets)
    assert targets.profile.read_text() == original
    # No .tmp files left behind
    tmp_files = list(tmp_path.rglob("*.tmp"))
    assert tmp_files == [], f"Found leftover tmp files: {tmp_files}"


def test_apply_changes_chmod_600(tmp_path):
    import shutil
    shutil.copytree(FIXTURES / "current_2025_empty", tmp_path / "ws")
    targets = WritePaths(result=tmp_path / "ws" / "result.json", profile=tmp_path / "ws" / "taxpayer_profile.json")
    changes = {"carryforwards": [Change(
        category="carryforwards", json_path="prior_year.capital_loss_carryforward",
        target_file="taxpayer_profile.json", prior_value=1000, current_value=0,
        action_suggested="apply", reason="test")]}
    apply_changes(changes, category_decisions={"carryforwards": "apply"}, targets=targets)
    mode = targets.profile.stat().st_mode
    assert (mode & 0o777) == 0o600
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_rollover.py -v -k "apply_changes"`
Expected: ImportError on `apply_changes` / `WritePaths`.

- [ ] **Step 3: Implement `apply_changes` and `WritePaths`**

Add to `agents/lib/rollover.py`:

```python
@dataclass
class WritePaths:
    result: Path
    profile: Path


@dataclass
class ApplyReport:
    written: list[str] = field(default_factory=list)
    skipped_categories: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def apply_changes(
    changes: dict[str, list[Change]],
    category_decisions: dict[str, str],  # category -> "apply" | "skip"
    targets: WritePaths,
) -> ApplyReport:
    """Apply selected categories atomically. All-or-nothing across all selected categories."""
    report = ApplyReport()
    profile = json.loads(targets.profile.read_text())
    result = json.loads(targets.result.read_text())

    any_changes = False
    for category, change_list in changes.items():
        if category_decisions.get(category) != "apply":
            report.skipped_categories.append(category)
            continue
        for change in change_list:
            if change.action_suggested == "skip":
                report.warnings.append(f"{category}: skipped {change.json_path} ({change.reason})")
                continue
            target_doc = profile if change.target_file == "taxpayer_profile.json" else result
            if change.json_path.startswith("dependents["):
                # Dependents list: append/replace by index
                idx = int(change.json_path[len("dependents["):-1])
                deps = target_doc.setdefault("dependents", [])
                while len(deps) <= idx:
                    deps.append({})
                deps[idx] = change.prior_value
            else:
                _set_path(target_doc, change.json_path, change.prior_value)
            report.written.append(f"{change.target_file}:{change.json_path}")
            any_changes = True

    if not any_changes:
        return report

    # Atomic write: tmp + os.replace
    tmp_profile = targets.profile.with_suffix(targets.profile.suffix + ".tmp")
    tmp_result = targets.result.with_suffix(targets.result.suffix + ".tmp")
    try:
        tmp_profile.write_text(json.dumps(profile, indent=2))
        tmp_result.write_text(json.dumps(result, indent=2))
        os.replace(tmp_profile, targets.profile)
        os.replace(tmp_result, targets.result)
        os.chmod(targets.profile, 0o600)
        os.chmod(targets.result, 0o600)
    except OSError as e:
        for p in (tmp_profile, tmp_result):
            if p.exists():
                p.unlink()
        raise RolloverError(f"Atomic write failed, no changes applied: {e}") from e

    return report
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_rollover.py -v`
Expected: 25 passed.

- [ ] **Step 5: Commit**

```bash
git add agents/lib/rollover.py tests/test_rollover.py
git commit -m "feat(rollover): apply_changes with atomic write + rollback"
```

---

## Task 10: CLI entry point + integration test

**Files:**
- Modify: `agents/lib/rollover.py`
- Modify: `tests/test_rollover.py`

- [ ] **Step 1: Append failing integration tests**

```python
def test_cli_diff_json_output(tmp_path):
    """Full pipeline: synthetic archive + empty current → run diff via subprocess → parse JSON."""
    import shutil
    shutil.copytree(FIXTURES / "prior_2024_clean", tmp_path / "archive" / "2024")
    shutil.copy(FIXTURES / "current_2025_empty" / "result.json", tmp_path / "result.json")
    shutil.copy(FIXTURES / "current_2025_empty" / "taxpayer_profile.json", tmp_path / "taxpayer_profile.json")
    shutil.copy(FIXTURES / "current_2025_empty" / "tax_law_2025.json", tmp_path / "tax_law_2025.json")

    repo = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        ["python3", "-m", "agents.lib.rollover", "diff",
         "--from", str(tmp_path / "archive" / "2024"),
         "--to", str(tmp_path),
         "--year", "2025",
         "--json"],
        capture_output=True, text=True, cwd=str(repo),
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert "carryforwards" in payload
    assert any(c["json_path"] == "prior_year.capital_loss_carryforward" for c in payload["carryforwards"])


def test_cli_apply_writes_files(tmp_path):
    import shutil
    shutil.copytree(FIXTURES / "prior_2024_clean", tmp_path / "archive" / "2024")
    shutil.copy(FIXTURES / "current_2025_empty" / "result.json", tmp_path / "result.json")
    shutil.copy(FIXTURES / "current_2025_empty" / "taxpayer_profile.json", tmp_path / "taxpayer_profile.json")
    shutil.copy(FIXTURES / "current_2025_empty" / "tax_law_2025.json", tmp_path / "tax_law_2025.json")

    repo = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        ["python3", "-m", "agents.lib.rollover", "apply",
         "--from", str(tmp_path / "archive" / "2024"),
         "--to", str(tmp_path),
         "--year", "2025",
         "--categories", "carryforwards"],
        capture_output=True, text=True, cwd=str(repo),
    )
    assert proc.returncode == 0, proc.stderr
    profile = json.loads((tmp_path / "taxpayer_profile.json").read_text())
    assert profile["prior_year"]["capital_loss_carryforward"] == 1000


def test_cli_rejects_path_traversal(tmp_path):
    repo = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        ["python3", "-m", "agents.lib.rollover", "diff",
         "--from", "../../../etc",
         "--to", str(tmp_path),
         "--year", "2025"],
        capture_output=True, text=True, cwd=str(repo),
    )
    assert proc.returncode != 0
    assert "path" in proc.stderr.lower() or "outside" in proc.stderr.lower() or "does not exist" in proc.stderr.lower()


def test_cli_rejects_invalid_year(tmp_path):
    repo = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        ["python3", "-m", "agents.lib.rollover", "diff",
         "--from", str(tmp_path), "--to", str(tmp_path),
         "--year", "2025; rm -rf /"],
        capture_output=True, text=True, cwd=str(repo),
    )
    assert proc.returncode != 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_rollover.py -v -k "cli"`
Expected: FAIL — no `__main__` block, subprocess returns non-zero with usage error.

- [ ] **Step 3: Add CLI entry point to `agents/lib/rollover.py`**

Append:

```python
def _load_current_state(workspace: Path, year: int) -> CurrentState:
    result = _read_json(workspace / "result.json")
    profile = _read_json(workspace / "taxpayer_profile.json")
    tax_law_path = workspace / f"tax_law_{year}.json"
    tax_law = _read_json(tax_law_path) if tax_law_path.exists() else {}
    return CurrentState(result=result, profile=profile, tax_law=tax_law)


def _validate_subpath(candidate: Path, workspace: Path) -> Path:
    """Ensure candidate is under workspace (no path traversal)."""
    candidate = candidate.resolve()
    workspace = workspace.resolve()
    try:
        candidate.relative_to(workspace)
    except ValueError:
        raise RolloverError(f"Path {candidate} is outside workspace {workspace}")
    return candidate


def _validate_year_arg(value: str) -> int:
    try:
        year = int(value)
    except ValueError:
        raise RolloverError(f"Year must be a 4-digit integer, got {value!r}")
    if not (2000 <= year <= 2099):
        raise RolloverError(f"Year {year} out of range (2000-2099)")
    return year


def _main(argv: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="agents.lib.rollover")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for cmd in ("diff", "apply"):
        p = sub.add_parser(cmd)
        p.add_argument("--from", dest="src", required=True)
        p.add_argument("--to", dest="dst", required=True)
        p.add_argument("--year", required=True)
        if cmd == "diff":
            p.add_argument("--json", action="store_true")
            p.add_argument("--categories", default="carryforwards,identity,dependents,expected_sources")
        else:
            p.add_argument("--categories", required=True)
    args = parser.parse_args(argv)

    try:
        year = _validate_year_arg(args.year)
        workspace = Path(args.dst).resolve()
        src = _validate_subpath(Path(args.src).resolve(), workspace)
        prior = load_prior(src, year - 1)  # archive year is one less than target year
        # But also accept the case where the archive is named after target year - 1; rollover.py CLI
        # treats --year as the *new* year; archive should be for new_year - 1
    except RolloverError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    current = _load_current_state(workspace, year)
    categories = set(args.categories.split(","))
    diff = compute_diff(prior, current, categories=categories, tax_law_new=current.tax_law)

    if args.cmd == "diff":
        if args.json:
            payload = {cat: [_change_to_dict(c) for c in changes] for cat, changes in diff.items()}
            print(json.dumps(payload, indent=2, default=str))
        else:
            for cat, changes in diff.items():
                print(f"\n## {cat}")
                for c in changes:
                    print(f"  [{c.action_suggested}] {c.json_path}: {c.prior_value!r} (currently {c.current_value!r}) — {c.reason}")
        return 0

    # apply
    decisions = {cat: "apply" for cat in categories}
    targets = WritePaths(result=workspace / "result.json", profile=workspace / "taxpayer_profile.json")
    try:
        report = apply_changes(diff, category_decisions=decisions, targets=targets)
    except RolloverError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"Applied {len(report.written)} changes")
    for line in report.written:
        print(f"  ✓ {line}")
    for w in report.warnings:
        print(f"  ⚠ {w}")
    return 0


def _change_to_dict(c: Change) -> dict:
    return {
        "category": c.category,
        "json_path": c.json_path,
        "target_file": c.target_file,
        "prior_value": c.prior_value,
        "current_value": c.current_value,
        "action_suggested": c.action_suggested,
        "reason": c.reason,
    }


if __name__ == "__main__":
    import sys
    sys.exit(_main(sys.argv[1:]))
```

**Note on `--year` semantics:** The CLI takes the **new** year (target). Archive is expected at `--from {archive_dir}` and should contain `result.json` with `meta.tax_year == year - 1`. The skill orchestrates this; the user types the new year.

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_rollover.py -v`
Expected: 29 passed.

- [ ] **Step 5: Commit**

```bash
git add agents/lib/rollover.py tests/test_rollover.py
git commit -m "feat(rollover): CLI entry point with JSON output and path validation"
```

---

## Task 11: Build `agents/skills/tax-archive.md`

**Files:**
- Create: `agents/skills/tax-archive.md`

- [ ] **Step 1: Write the skill markdown**

```markdown
---
name: tax-archive
description: Archive current year's tax files into archive/{year}/ before starting a new year
---

# /tax-archive

Move current-year tax files (`result.json`, `taxpayer_profile.json`, `answers.md`, `tax_law_{year}.json`, `summaries/`, `documents/`) into `archive/{year}/` so the workspace can be initialized for a new tax year.

## Usage
```
/tax-archive <year>
```

## Prerequisites
- `result.json` exists at workspace root with `meta.tax_year == {year}`
- `archive/{year}/` does NOT already exist

## Steps

### 1. Input validation

**SECURITY:** Validate that `{year}` is a 4-digit integer (2000–2099) before using in any shell command. Reject non-numeric input.

### 2. Run the archive script

```bash
python3 -m agents.lib.archive {year}
```

The script:
- Refuses if `archive/{year}/` already exists
- Refuses if root `result.json` is missing or `meta.tax_year` doesn't match
- Moves all listed files atomically (rolls back on failure)
- Sets `chmod 600` on every archived file

### 3. Print summary

Show the user which files were moved and which were skipped (not present). Remind them:

> Run `/tax-setup {year + 1}` next to initialize the workspace for the new year.

## Notes
- This is the **prerequisite** for `/tax-rollover` — without an archive, rollover has nothing to read.
- Archiving is non-destructive: files are moved, not deleted. If anything goes wrong before `/tax-setup`, the user can `mv archive/{year}/* .` to restore.
```

- [ ] **Step 2: Verify the skill markdown parses (frontmatter)**

Run: `head -4 agents/skills/tax-archive.md`
Expected: shows the `---\nname: tax-archive\n...` frontmatter.

- [ ] **Step 3: Commit**

```bash
git add agents/skills/tax-archive.md
git commit -m "feat(rollover): add /tax-archive skill"
```

---

## Task 12: Build `agents/skills/tax-rollover.md`

**Files:**
- Create: `agents/skills/tax-rollover.md`

- [ ] **Step 1: Write the skill markdown**

```markdown
---
name: tax-rollover
description: Pull prior-year carryforwards, identity, dependents (age-ticked), and recurring payers from archive/{year}/ into the current-year workspace
---

# /tax-rollover

Roll forward data from a prior year's archived return into the current-year workspace. Eliminates manual re-typing of carryforward dollar amounts.

## Usage
```
/tax-rollover <prior_year>
```

Example: after `/tax-setup 2025` for TY2025, run `/tax-rollover 2024` to pull from `archive/2024/`.

## Prerequisites
- `archive/{prior_year}/result.json` exists with matching `meta.tax_year`
- Current-year workspace initialized (`result.json` + `taxpayer_profile.json` at root with `meta.tax_year > prior_year`)

## Steps

### 1. Input validation

**SECURITY:** Validate `{prior_year}` is a 4-digit integer (2000–2099). Reject non-numeric.

### 2. Compute the diff

Run via Bash:

```bash
python3 -m agents.lib.rollover diff \
  --from archive/{prior_year} \
  --to . \
  --year {current_year} \
  --json
```

Where `{current_year}` is read from `result.json` `meta.tax_year`. Parse the JSON output.

### 3. Present each category via AskUserQuestion

For each of the four categories (`carryforwards`, `identity`, `dependents`, `expected_sources`) that has at least one change in the diff:

1. Render a brief preview of the changes (3–8 lines): show `json_path`, `prior_value`, `current_value`, `action_suggested`, `reason` for each change.
2. Highlight any change with `action_suggested == "ask"` — these are the ones with warnings (filing-status change, age tick crossings, non-zero current values, etc.).
3. Use **AskUserQuestion** with options: `Apply all`, `Skip category`.

If every change in a category has `action_suggested == "skip"`, do not ask — just note it in the summary.

### 4. Apply selected categories

Run:

```bash
python3 -m agents.lib.rollover apply \
  --from archive/{prior_year} \
  --to . \
  --year {current_year} \
  --categories <comma-separated user-selected categories>
```

If the user selected no categories, skip this step and print *"No changes applied."*

### 5. Append audit block to `answers.md`

```markdown
## Rollover from TY{prior_year} — applied {today}
Categories applied: {comma-separated list}
Fields updated: {count}
Warnings:
- {warning 1}
- {warning 2}
```

Do **not** log specific field values (SSN, dollar amounts) into `answers.md` — only counts and warnings.

### 6. Print final summary

Show:
- Categories applied and skipped
- Number of fields updated
- Warnings (aged-out dependents, deferred carryforward reminders, etc.)
- Reminder for deferred carryforwards (NOL, AMT credit, charitable carryover, Form 8606 basis, prior_overpayment_applied):

  > If you had NOL, AMT credit, 5-year charitable carryover, Form 8606 non-deductible IRA basis, or prior-year overpayment applied to estimates in TY{prior_year}, enter manually in the `prior_year` section of taxpayer_profile.json — the toolkit doesn't track these yet.

## Notes
- Re-runnable safely — diff against existing values surfaces conflicts as `action_suggested: ask`.
- Reads `tax_law_{current_year}.json` to determine CTC age threshold for dependent age tick. If missing, defaults to 17.
```

- [ ] **Step 2: Verify the skill markdown parses**

Run: `head -4 agents/skills/tax-rollover.md`
Expected: frontmatter block.

- [ ] **Step 3: Commit**

```bash
git add agents/skills/tax-rollover.md
git commit -m "feat(rollover): add /tax-rollover skill"
```

---

## Task 13: Modify `agents/skills/tax-setup.md` — step 0 refusal and step 9.5 auto-invoke

**Files:**
- Modify: `agents/skills/tax-setup.md`

- [ ] **Step 1: Read the existing skill**

Run: `cat agents/skills/tax-setup.md | head -40`

Find the location of `### 1. Create Directory Structure` (currently around line 20).

- [ ] **Step 2: Insert step 0 immediately before step 1**

Edit `agents/skills/tax-setup.md`. Before `### 1. Create Directory Structure`, insert:

```markdown
### 0. Refuse if prior-year files exist at root

Before doing anything else, check if there's an existing `result.json` at the workspace root. If yes, read its `meta.tax_year`:

- If `meta.tax_year == {requested year}`: the workspace is already initialized for this year — ask the user whether to overwrite (the existing behavior for re-setup).
- If `meta.tax_year != {requested year}`: refuse with:

  > Prior-year files detected at root (TY{X}). Run `/tax-archive {X}` first, then re-run `/tax-setup {requested year}`.

  Do **not** proceed past this point. This refusal exists to prevent accidentally losing the prior year's data — `/tax-rollover` later depends on archived files being present.

```

- [ ] **Step 3: Append step 9.5 after the existing step 9 (Prior year carryforwards)**

Find `**Group 9 — Prior year carryforwards:**` block (around line 85). After that block ends, before `**Group 10 — Estimated taxes:**`, insert:

```markdown

### 9.5. Auto-invoke /tax-rollover if archive exists

After the carryforward questions in Group 9, check whether `archive/{year - 1}/` exists. If yes:

> Detected prior-year archive at `archive/{year - 1}/`. Running `/tax-rollover {year - 1}` to pre-fill carryforwards, identity, dependents (with age tick), and recurring payer list.

Invoke `/tax-rollover {year - 1}` inline. The rollover skill handles its own AskUserQuestion prompts per category.

After rollover completes, treat the Group 9 carryforward questions as "verify these pre-filled values" rather than "type these from memory."

If `archive/{year - 1}/` does not exist, skip silently — fall back to manual carryforward entry from Group 9.

```

- [ ] **Step 4: Verify the changes**

Run: `grep -n "### 0\.\|### 9\.5" agents/skills/tax-setup.md`
Expected: two matching lines.

- [ ] **Step 5: Commit**

```bash
git add agents/skills/tax-setup.md
git commit -m "feat(rollover): /tax-setup refuses unarchived prior year and auto-invokes /tax-rollover"
```

---

## Task 14: Update `CLAUDE.md` — architecture diagram and test count

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the architecture diagram**

Edit `CLAUDE.md`. Find the architecture block (lines 10–24, beginning with ` ```` ` and `/tax-setup`). Replace with:

```
/tax-archive  → Move prior year's files into archive/{year}/ (run at year-end cutover)
      ↓
/tax-setup    → Interview taxpayer, fetch current tax law, create workspace
      ↓        (auto-invokes /tax-rollover if archive/{year-1}/ exists)
/tax-rollover → Pull prior-year carryforwards, identity, dependents, payers from archive/
      ↓
/tax-parse    → Parse source documents (PDFs, XLSX, CSV) via parallel subagents
      ↓
/tax-compute  → Compute all form values from parsed data
      ↓
/tax-generate → Generate summary PDF from result.json
      ↓
/tax-verify   → Cross-reference against tax software return
      ↓
/tax-review   → EA + IRS officer perspective review
      ↓
/tax-fbar     → Prepare FBAR filing document (if applicable)
```

- [ ] **Step 2: Update the directory structure block**

Find the `agents/` directory tree (around lines 34–53). Update the `skills/` list to include `tax-archive.md` and `tax-rollover.md`, and update the `lib/` list to include `archive.py` and `rollover.py`:

```
agents/
├── skills/           # Skill markdown files (symlinked to ~/.claude/skills/<name>/SKILL.md by install.sh)
│   ├── tax-setup.md
│   ├── tax-archive.md
│   ├── tax-rollover.md
│   ├── tax-parse.md
│   ├── tax-compute.md
│   ├── tax-generate.md
│   ├── tax-verify.md
│   ├── tax-review.md
│   └── tax-fbar.md
├── templates/        # JSON/MD templates for workspace initialization
│   ├── result_template.json
│   ├── answers_template.md
│   └── taxpayer_profile_template.json
├── lib/              # Shared Python utilities
│   ├── parse_utils.py        # PDF/Excel/CSV extraction + document classification
│   ├── tax_law_fetcher.py    # Tax law cache structure + load/save
│   ├── tax_computations.py   # Parameterized tax math (brackets, QDCG, SE, FTC, CTC, etc.)
│   ├── archive.py            # Move year files into archive/{year}/ for /tax-archive
│   └── rollover.py           # Diff/merge engine for /tax-rollover
└── install.sh        # Symlink skills to ~/.claude/skills/<name>/SKILL.md
```

- [ ] **Step 3: Update test count**

Find the line `- 90 tests covering all 8 computation functions + integration pipeline` (line 116).

First, run the test suite to get the exact new count:

```bash
source .venv/bin/activate && python3 -m pytest tests/ --collect-only -q | tail -1
```

Replace `90 tests` with the actual new count (expected ~125). Replace `8 computation functions` with `8 computation functions + archive + rollover modules`.

- [ ] **Step 4: Run the full test suite to confirm everything still passes**

```bash
source .venv/bin/activate && python3 -m pytest tests/ -v
```

Expected: all tests pass, count matches what was put in CLAUDE.md.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for /tax-archive and /tax-rollover"
```

---

## Final verification

- [ ] Run full test suite from a clean state

```bash
source .venv/bin/activate && python3 -m pytest tests/ -v
```

Expected: all ~125 tests pass.

- [ ] Manual smoke test: archive an existing workspace and re-init

If you have a populated workspace (with `result.json`), test the happy path manually:

```bash
# Snapshot current state
git status
# Try archive
python3 -m agents.lib.archive 2024  # use actual current year
# Verify archive/2024/ has the files
ls -la archive/2024/
# Restore (since this is a smoke test, not real archive)
mv archive/2024/* .
rmdir archive/2024
```

- [ ] Print push command for the user

```
git push -u origin feat/tax-rollover-spec
```

After push, open the PR via `gh pr create`.
