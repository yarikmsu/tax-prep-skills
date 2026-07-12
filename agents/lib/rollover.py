#!/usr/bin/env python3
"""Rollover prior-year carryforwards, identity, dependents, and expected sources
from archive/{year}/ into current-year workspace files.

Pure functions for diff/merge logic; thin I/O wrappers at edges.
"""

import json
import os
import sys
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
        # Whole schedule_d block: the transform prefers the explicit
        # loss_carryforward_to_next_year written by /tax-compute (Capital Loss
        # Carryover Worksheet) and falls back to deriving from line_16_combined
        # for older archived returns.
        "source_path": "schedule_d",
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
    "ira_basis": {
        "category": "carryforwards",
        "source_path": "form_8606.basis_remaining",  # Form 8606 line 14
        "target_file": "taxpayer_profile.json",
        "target_path": "prior_year.ira_basis",
        "transform": "ira_basis",
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
    """schedule_d block (or a bare line_16_combined number) → carryforward.

    Prefers the explicit loss_carryforward_to_next_year block written by
    /tax-compute via the Capital Loss Carryover Worksheet (ST + LT summed — the
    profile field is a single figure in v1). Falls back to deriving from
    line_16_combined for older archived returns: abs(loss) − $3,000, which
    assumes the full non-MFS cap was used. The fallback is a LOWER bound of the
    true carryforward (the worksheet only ever uses LESS than $3,000 — when
    taxable income is low — never more), so an all-zero explicit block safely
    falls through to the derivation instead of dropping a real carryforward.
    """
    if isinstance(raw, dict):
        cf_block = raw.get("loss_carryforward_to_next_year")
        if isinstance(cf_block, dict):
            total = 0.0
            has_explicit = False
            for key in ("short_term", "long_term"):
                v = cf_block.get(key)
                if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                    total += v
                    has_explicit = True
            if has_explicit:
                return total
        raw = raw.get("line_16_combined")
    if not isinstance(raw, (int, float)) or isinstance(raw, bool) or raw >= 0:
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


def _transform_ira_basis(raw):
    """Form 8606 line 14 basis remaining → next-year line 2. Non-negative; 0 if absent."""
    return raw if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0 else 0


TRANSFORMS = {
    "capital_loss_to_carryforward": _transform_capital_loss,
    "abs": _transform_abs,
    "ftc_unused": _transform_ftc_unused,
    "ira_basis": _transform_ira_basis,
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
            # Filing status always surfaces as ask (never silently overwritten),
            # even when prior and current values match — confirm every year.
            if raw is not None:
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
            warnings.append(
                f"turned {new_age} — no longer CTC-eligible (now qualifies for $500 "
                "Credit for Other Dependents if still a dependent)"
            )
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
    if "dependents" in categories:
        out["dependents"] = _diff_dependents(prior, current, tax_law_new)
    if "expected_sources" in categories:
        out["expected_sources"] = _diff_expected_sources(prior, current)
    return out


# --- apply_changes -----------------------------------------------------------

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

    # Atomic write across two files: snapshot originals in memory before any
    # os.replace, so we can restore the first target if the second replace fails.
    # Without the snapshot, a mid-sequence failure leaves the workspace in a
    # half-applied state — currently latent because every FIELD_MAP target writes
    # to taxpayer_profile.json only, but a real bug the moment a result.json
    # target lands in FIELD_MAP.
    tmp_profile = targets.profile.with_suffix(targets.profile.suffix + ".tmp")
    tmp_result = targets.result.with_suffix(targets.result.suffix + ".tmp")
    original_profile = targets.profile.read_bytes()
    original_result = targets.result.read_bytes()
    try:
        tmp_profile.write_text(json.dumps(profile, indent=2))
        tmp_result.write_text(json.dumps(result, indent=2))
        os.replace(tmp_profile, targets.profile)
        try:
            os.replace(tmp_result, targets.result)
        except OSError:
            targets.profile.write_bytes(original_profile)
            raise
        os.chmod(targets.profile, 0o600)
        os.chmod(targets.result, 0o600)
    except OSError as e:
        # Best-effort restore (no-op if writes already rolled back above)
        if targets.profile.read_bytes() != original_profile:
            targets.profile.write_bytes(original_profile)
        if targets.result.read_bytes() != original_result:
            targets.result.write_bytes(original_result)
        for p in (tmp_profile, tmp_result):
            if p.exists():
                p.unlink()
        raise RolloverError(f"Atomic write failed, no changes applied: {e}") from e

    return report


# --- CLI entry point ----------------------------------------------------------

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
        # CLI's --year is the NEW year; archive should contain prior return (year - 1)
        prior = load_prior(src, year - 1)
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
        print(f"  + {line}")
    for w in report.warnings:
        print(f"  ! {w}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
