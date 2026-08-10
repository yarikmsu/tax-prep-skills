"""Claude Code subagent definitions — `.claude/agents/*.md`.

A subagent definition is a system prompt, and a system prompt full of confident,
wrong field paths is worse than no agent: the auditor looks for
`federal.salt_cap`, finds nothing where it was told to look, and reports what
reads as an absence. Nothing else in this repository imports these files, so
without the assertions below they rot in complete silence — a key renamed in
`create_empty_tax_law` leaves the whole suite green.

Two things are checked here, and both are behavioural rather than textual:

- every dotted schema path an agent names RESOLVES against the real schema
  builders in `agents/lib/tax_law_fetcher.py`;
- the tool grants match what the agents are for. These are review agents; a
  reviewer that can edit can silently "fix" what it found, and the only agent
  with network access must not also be able to read taxpayer files.
"""
import re
from pathlib import Path

from agents.lib.tax_law_fetcher import (
    _AUTHORITATIVE_NONGOV_DOMAINS,
    create_empty_tax_law,
    create_state_template,
)

REPO = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO / ".claude" / "agents"

EXPECTED_AGENTS = {"tax-law-freshness-auditor", "bias-direction-reviewer"}

# Every agent defined here reviews or audits. None of them writes.
#
# `Bash` is in this set, and that is the whole point of the set existing. The
# first version listed only the three obvious editors, so `bias-direction-reviewer`
# held `Bash` — `sed -i`, `echo >>`, `git checkout`, `git commit` — while
# CLAUDE.md said "neither has a write tool" and this assertion agreed. A shell IS
# a write capability; enumerating tools by their NAME rather than by what they
# can do is how a guarantee ends up documented, tested and false.
WRITE_TOOLS = {"Write", "Edit", "NotebookEdit", "Bash"}
NETWORK_TOOLS = {"WebSearch", "WebFetch"}

# Deliberately a closed set. A typo'd tool name grants nothing and reports
# nothing — the agent simply lacks the capability its prompt assumes — so a new
# tool has to be added here on purpose.
KNOWN_TOOLS = {"Read", "Write", "Edit", "NotebookEdit", "Grep", "Glob", "Bash",
               "WebSearch", "WebFetch", "TodoWrite", "AskUserQuestion",
               "BashOutput", "KillShell"}


def _agent_files():
    return sorted(AGENTS_DIR.glob("*.md"))


def _frontmatter(path: Path) -> dict:
    text = path.read_text()
    assert text.startswith("---\n"), f"{path.name} has no frontmatter block"
    _, block, _ = text.split("---\n", 2)
    out = {}
    for line in block.splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _tools(path: Path) -> set[str]:
    raw = _frontmatter(path).get("tools", "")
    return {t.strip() for t in raw.split(",") if t.strip()}


class TestAgentDefinitions:
    def test_the_expected_agents_are_present(self):
        """Explicit, like the skill registry it mirrors. An empty
        `.claude/agents/` would satisfy every other assertion in this module by
        having nothing to check, so the set is named rather than globbed."""
        assert {f.stem for f in _agent_files()} == EXPECTED_AGENTS

    def test_every_agent_declares_an_explicit_tool_allowlist(self):
        """The load-bearing one, and the reason the three grant checks below are
        not vacuous.

        An agent definition with NO `tools:` field inherits every tool — Write
        and Edit included. The grant assertions each work by intersecting the
        declared set with a forbidden set, so a deleted `tools:` line makes them
        all pass against an agent that can now do anything. The permissive
        default has to be forbidden here, or the checks silence themselves at
        exactly the moment the grant becomes dangerous.
        """
        for f in _agent_files():
            assert _tools(f), (
                f"{f.name} declares no `tools:` — which grants ALL tools, and "
                "turns every grant assertion in this file into a no-op")

    def test_frontmatter_values_have_no_unquoted_colon(self):
        """Same construct, same reason, as the skills' check: an unquoted scalar
        containing ': ' is a YAML parse error, and a definition that fails to
        parse disappears with no message."""
        for f in _agent_files():
            block = f.read_text().split("---\n", 2)[1]
            for line in block.splitlines():
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                key, _, value = line.partition(":")
                v = value.strip()
                assert not (v and v[0] not in "\"'" and ": " in v), (
                    f"{f.name}: unquoted colon in {key.strip()!r}")

    def test_frontmatter_name_matches_the_filename(self):
        for f in _agent_files():
            assert _frontmatter(f).get("name") == f.stem, (
                f"{f.name}: frontmatter name must equal the filename stem — "
                "that is what the agent is invoked by")

    def test_every_description_says_when_to_use_the_agent(self):
        """Same reasoning, and the same literal, as the skills' `Use when` rule
        in test_portability.py: the description is the only text loaded before
        selection, so an agent described purely by what it does gives the model
        nothing to trigger on and is never selected. The exact phrase is
        required rather than any wording that conveys a trigger, because a
        phrasing-tolerant check is one reword away from asserting nothing."""
        for f in _agent_files():
            desc = _frontmatter(f).get("description", "")
            assert desc, f"{f.name} has no description"
            assert "use when" in desc.lower(), (
                f"{f.name}: description has no 'Use when ...' trigger clause")

    def test_tool_grants_are_real_tool_names(self):
        for f in _agent_files():
            unknown = _tools(f) - KNOWN_TOOLS
            assert not unknown, (
                f"{f.name} grants {sorted(unknown)}, which are not tool names. "
                "A misspelt grant is silent: the agent simply does not have the "
                "capability its prompt assumes.")

    def test_review_agents_cannot_write(self):
        """A reviewer that can edit can resolve its own findings, and a finding
        that was quietly fixed is a finding nobody reviewed. Both agents here
        report; the human decides."""
        for f in _agent_files():
            granted = _tools(f) & WRITE_TOOLS
            assert not granted, (
                f"{f.name} may write ({sorted(granted)}) — these agents audit "
                "and report, and an auditor that edits its own subject cannot "
                "be trusted about it")

    # The most a networked agent may hold. Stated as a closed set rather than as
    # another forbidden list, so that this stays checkable now that `Bash` is
    # forbidden for every agent here and a no-shell assertion could no longer
    # fail on its own. `TodoWrite`, `AskUserQuestion` and anything added later
    # have to be admitted deliberately.
    NETWORK_AGENT_MAX = {"Read", "Grep", "Glob", "WebSearch", "WebFetch"}

    def test_a_networked_agent_is_held_to_a_minimal_tool_set(self):
        """AGENTS.md §1 keeps network access away from anything touching
        taxpayer data. The freshness auditor is the only agent here that needs
        the web, and its input — `tax_law_<year>.json` — holds published rates
        and no PII. Granting it Bash as well would put an arbitrary shell and
        an outbound connection in the same context inside a workspace holding a
        real return, which is precisely the combination that rule exists to
        prevent. Least privilege is cheap here: it reads one JSON file.

        Named for what it actually enforces. `Read` still reaches every file in
        the workspace, and only the prompt narrows that — so this bounds the
        agent's capabilities rather than making it unable to see taxpayer data,
        and a name claiming the latter would be the stronger guarantee nobody
        checked.
        """
        for f in _agent_files():
            tools = _tools(f)
            if not (tools & NETWORK_TOOLS):
                continue
            excess = sorted(tools - self.NETWORK_AGENT_MAX)
            assert not excess, (
                f"{f.name} has network access and also {excess}; a networked "
                "agent in this workspace is held to reading one JSON file")


class TestFreshnessAuditorMatchesTheSchema:
    """The auditor's checklist is only useful if the fields it names exist."""

    AUDITOR = AGENTS_DIR / "tax-law-freshness-auditor.md"

    # Backticked, fully-qualified paths only. A bare `salt_cap` in prose is
    # ambiguous, so the agent doc spells these out — which is what makes them
    # checkable. Stated as a limit rather than papered over: an unqualified
    # mention is NOT covered here.
    PATH_RE = re.compile(r"`((?:federal|state)\.[A-Za-z0-9_.]+)`")

    def _schema(self) -> dict:
        law = create_empty_tax_law(2026)
        law["state"] = create_state_template("CA", 2026)
        return law

    def _resolve(self, schema: dict, dotted: str):
        node = schema
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return None, False
            node = node[part]
        return node, True

    def test_the_auditor_names_paths_that_exist_in_the_schema(self):
        text = self.AUDITOR.read_text()
        paths = sorted(set(self.PATH_RE.findall(text)))
        assert paths, (
            "no schema paths found in the auditor prompt — either the checklist "
            "is gone or it stopped using backticked fully-qualified paths, and "
            "either way this test has stopped checking anything")
        schema = self._schema()
        missing = [p for p in paths if not self._resolve(schema, p)[1]]
        assert not missing, (
            f"the freshness auditor tells the agent to check {missing}, which "
            "do not exist in create_empty_tax_law / create_state_template. It "
            "would look where it was told, find nothing, and report an absence "
            "that is really a renamed key.")

    def test_the_fields_called_empty_on_arrival_really_are(self):
        """`irmaa.tiers` ships empty and `salt_cap` ships unset — the auditor
        tells the user to confirm they were FETCHED. If a future schema seeded
        them, that instruction would send people chasing a value that is
        already there, and the genuinely-unfetched case would look identical."""
        schema = self._schema()
        tiers, ok = self._resolve(schema, "federal.irmaa.tiers")
        assert ok and all(not v for v in tiers.values()), (
            "federal.irmaa.tiers is no longer empty on arrival")
        salt, ok = self._resolve(schema, "federal.salt_cap")
        assert ok and salt.get("amount") is None, (
            "federal.salt_cap now ships with a value")

    def test_a_clean_report_requires_full_cache_verification(self):
        """The drift-prone shortlist is emphasis, not the audit boundary.

        Ordinary brackets and deductions are just as capable of carrying a
        prior-year value, so checking only the novel indexed fields can certify
        a partially stale cache.
        """
        text = self.AUDITOR.read_text().lower()
        assert "walk the entire cache schema" in text
        assert re.search(r"every\s+populated\s+tax-law\s+figure", text)
        assert "cannot be clean" in text

    def test_filename_year_must_match_payload_year_before_audit(self):
        """A copied cache cannot be certified under its stale embedded year."""
        text = self.AUDITOR.read_text().lower()
        assert "filename/payload year identity" in text
        assert re.search(
            r"tax_law_<year>\.json.{0,120}top-level `tax_year`", text, re.S
        )
        assert "mismatched payload year is **incomplete**" in text
        assert "hard\n   stop" in text

    def test_a_clean_report_rejects_populated_blocks_without_source_pins(self):
        """Live verification cannot replace the cache's chain of custody.

        Checking only the `_sources` entries that exist passes vacuously when a
        populated fetched block has no entry at all.
        """
        text = self.AUDITOR.read_text().lower()
        assert "source-pin completeness" in text
        assert "derive the fetched blocks from the schema" in text
        assert "unpinned" in text
        assert re.search(
            r"populated fetched block unpinned cannot\s+receive a clean\s+report",
            text,
        )
        assert "apply the stop rule" in text

    def test_a_canonical_no_tax_state_still_requires_a_source_pin(self):
        """A template-equal type:none block is still an affirmative law claim."""
        text = self.AUDITOR.read_text().lower()
        assert "single-state block whenever it is populated" in text
        assert '`type: "none"` state block' in text
        assert "even when it equals the `create_state_template` seed" in text

    def test_florida_sales_tax_requires_both_provenance_pins(self):
        """The general-rate pin cannot stand in for the county-rate pin.

        The fetcher treats those values as separately sourced blocks.  An
        auditor that checks only the generic state pin can certify the exact
        legacy cache that check_freshness rejects as INCOMPLETE.
        """
        text = self.AUDITOR.read_text()
        assert '_sources["state.FL"]' in text
        assert '_sources["state.FL.county_surtax_rate"]' in text
        assert re.search(
            r"Florida.{0,300}require(?:s|d)?\s+both.{0,300}separate",
            text,
            re.DOTALL,
        )

    def test_generic_estimate_states_may_omit_the_regime_type(self):
        """The auditor must accept the same generic shape as freshness does."""
        text = self.AUDITOR.read_text().lower()
        assert "modeled typed states" in text
        assert "generic bracket-estimate state blocks" in text
        assert "intentionally omit `state.type`" in text
        assert "must not be rejected" in text
        assert "colorado" in text

    def test_source_authority_matches_the_fetcher_policy(self):
        """The auditor must not reject a source the canonical cache accepts."""
        text = self.AUDITOR.read_text().lower()
        assert "_source_host_authoritative" in text
        for domain in _AUTHORITATIVE_NONGOV_DOMAINS:
            assert domain in text, (
                f"freshness auditor omits authoritative exception {domain}")

    def test_multi_period_fields_are_checked_against_their_effective_periods(self):
        """Not every cache leaf describes the return's filing year.

        Form 2210 crosses into TY+1 and PFIC computations consume rates from
        prior calendar years. Requiring either source to name the filing year
        rejects correct, schema-required data and makes a clean audit
        impossible whenever those optional blocks are populated.
        """
        text = self.AUDITOR.read_text().lower()
        assert "`federal.underpayment_rates`" in text
        assert re.search(
            r"underpayment_rates.{0,220}calendar quarter.{0,100}effective_from",
            text,
            re.DOTALL,
        )
        assert re.search(
            r"coverage through the filing deadline\s+in ty\+1 is expected", text
        )
        assert "`federal.historical_top_rate`" in text
        assert re.search(
            r"historical_top_rate.{0,180}calendar-year\s+key", text, re.DOTALL
        )
        assert re.search(
            r"ca `mental_health_surcharge\.rate` and `?\.threshold`?.{0,220}"
            r"r&tc §17043", text, re.DOTALL
        )
        assert "statutory-fixed, non-indexed constants" in text
        assert "do not require the statute to repeat each future" in text
        assert "these three are the only period exceptions" in text

    def test_bias_reviewer_allows_the_governed_ca_surcharge_constants(self):
        """The reviewer must share AGENTS.md's narrow hardcoded-law exception."""
        text = (AGENTS_DIR / "bias-direction-reviewer.md").read_text().lower()
        assert "ca `mental_health_surcharge` rate and threshold" in text
        assert "r&tc" in text and "§17043" in text
