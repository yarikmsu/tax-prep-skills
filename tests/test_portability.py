"""Portability doc-sync suite — machine-checks the multi-agent layout invariants.

Every assertion here guards a claim made to users of a NON-Claude agent, where we
have no CI coverage (no vendor CLI licenses in CI, spec section 9). A silently-broken
symlink or a dropped frontmatter field would make the toolkit undiscoverable on that
agent with no other signal — the user just sees zero skills and no error.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO / ".agents" / "skills"
CLAUDE_SKILLS = REPO / ".claude" / "skills"

EXPECTED_SKILLS = {
    "tax-amend", "tax-archive", "tax-calendar", "tax-compute", "tax-extend",
    "tax-fbar", "tax-generate", "tax-harvest", "tax-parse", "tax-parse-crypto",
    "tax-project", "tax-ptet", "tax-retirement", "tax-review", "tax-rollover",
    "tax-roth-convert", "tax-scenario", "tax-setup", "tax-verify", "tax-withholding",
}


def _skill_dirs():
    return sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir())


def _frontmatter(skill_md: Path) -> dict:
    """Parse the leading `---` YAML block without a yaml dependency.

    Only flat `key: value` pairs are used by any target agent's skill loader, so a
    hand parser is sufficient AND keeps the test dependency-free.
    """
    text = skill_md.read_text()
    assert text.startswith("---\n"), f"{skill_md} has no frontmatter block"
    _, block, _ = text.split("---\n", 2)
    out = {}
    for line in block.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


class TestSkillLayout:
    def test_all_expected_skills_present(self):
        found = {p.name for p in _skill_dirs()}
        assert found == EXPECTED_SKILLS, (
            f"missing: {sorted(EXPECTED_SKILLS - found)}; "
            f"unexpected: {sorted(found - EXPECTED_SKILLS)}. "
            "This list is deliberately explicit rather than globbed: adding a skill "
            "must also add its .claude/skills symlink, so the update is a prompt, "
            "not busywork")

    def test_every_skill_has_skill_md(self):
        for d in _skill_dirs():
            assert (d / "SKILL.md").is_file(), f"{d.name} missing SKILL.md"

    def test_frontmatter_name_matches_directory(self):
        for d in _skill_dirs():
            fm = _frontmatter(d / "SKILL.md")
            assert fm.get("name") == d.name, (
                f"{d.name}/SKILL.md frontmatter name={fm.get('name')!r} must equal "
                "its directory name — all four target agents key discovery on it")

    def test_frontmatter_description_non_empty(self):
        for d in _skill_dirs():
            fm = _frontmatter(d / "SKILL.md")
            assert fm.get("description"), f"{d.name} has empty description"

    def test_old_flat_location_is_gone(self):
        assert not (REPO / "agents" / "skills").exists(), (
            "agents/skills/ must not survive the move — a stale copy would drift "
            "silently from the canonical .agents/skills/ tree")


class TestNeutralizationLint:
    """No skill body may instruct the reader to use a Claude-only tool by name.

    Denylist entries carry an imperative verb on purpose: a bare mention inside a
    per-agent option list ("Codex agents, Cursor subagents, Kimi AgentSwarm, Claude
    Task") is legitimate and must keep working. What breaks a non-Claude user is an
    instruction naming a tool only their agent does not have.
    """

    # Bare tool names that no non-Claude agent has. Matched against emphasis- and
    # whitespace-normalized text, because the skills write `Use **WebSearch** to
    # ...` — a naive substring check passes vacuously against the markdown.
    DENY = (
        "websearch",
        "webfetch",
        "askuserquestion",
        "the task tool",
        "dispatch a task",
    )

    # Phrases that legitimately keep a Claude tool name because they enumerate one
    # option among several agents.
    ALLOW_CONTEXT = (
        "kimi agentswarm, claude task",
        "claude task)",
    )

    @staticmethod
    def _normalize(text: str) -> str:
        """Strip markdown emphasis and collapse whitespace so `**WebSearch**`,
        `_WebSearch_` and `WebSearch` all match the same denylist entry."""
        import re

        return re.sub(r"\s+", " ", re.sub(r"[*_`]", "", text)).lower()

    def test_no_claude_only_invocations(self):
        offenders = []
        for d in _skill_dirs():
            norm = self._normalize((d / "SKILL.md").read_text())
            for pat in self.DENY:
                start = 0
                while (hit := norm.find(pat, start)) != -1:
                    start = hit + 1
                    window = norm[max(0, hit - 40):hit + 40]
                    if any(a in window for a in self.ALLOW_CONTEXT):
                        continue
                    offenders.append(f"{d.name}: {pat!r} in ...{window.strip()}...")
        assert not offenders, (
            "skill bodies still name Claude-only tools — a Codex, Cursor, Kimi or "
            "Copilot user cannot follow these instructions:\n  "
            + "\n  ".join(offenders[:25]))

    # Skills that fetch tax law or exchange rates from the web. Listed explicitly
    # rather than detected by phrase: a phrase-gated check silently STOPS APPLYING
    # the moment someone rewords "search the web", which is the same vacuous-pass
    # trap the emphasis-stripping in _normalize exists to avoid.
    WEB_DEPENDENT = ("tax-setup", "tax-compute", "tax-review", "tax-verify",
                     "tax-parse", "tax-fbar")

    def test_web_dependent_skills_carry_a_stale_data_degrade(self):
        """A cached or missing web result must stop the run, not silently yield
        prior-year figures — the failure mode with no downstream detector."""
        for name in self.WEB_DEPENDENT:
            low = (SKILLS_DIR / name / "SKILL.md").read_text().lower()
            assert "search the web" in low or "searching the web" in low, (
                f"{name} is registered as web-dependent but no longer tells the "
                "agent to search the web — either the fetch moved (update this "
                "list) or the instruction was lost")
            assert "paste" in low and "stop" in low, (
                f"{name} tells the agent to search the web but documents no "
                "fallback for unavailable or cached-only web access; a stale tax "
                "figure produces a wrong return that looks entirely plausible and "
                "that nothing downstream detects")

    def test_parallel_skills_document_a_sequential_fallback(self):
        for name in ("tax-parse", "tax-verify", "tax-parse-crypto"):
            text = (SKILLS_DIR / name / "SKILL.md").read_text().lower()
            assert "sequential" in text, (
                f"{name} relies on parallel dispatch but documents no sequential "
                "fallback — an agent without sub-tasks would stall or improvise")

    # Skills whose questions change the RETURN if guessed, so a headless run must
    # stop rather than assume. tax-review / tax-verify also ask questions, but
    # theirs are advisory context and do not alter computed values.
    INTERVIEW_SKILLS = ("tax-setup", "tax-rollover", "tax-compute", "tax-fbar",
                        "tax-parse")

    def test_interview_skills_document_a_non_interactive_path(self):
        for name in self.INTERVIEW_SKILLS:
            text = (SKILLS_DIR / name / "SKILL.md").read_text().lower()
            assert "answers.md" in text, (
                f"{name} interviews the user but names no non-interactive source; "
                "headless runs (codex exec, Copilot's cloud agent) would guess")
            assert "non-interactive" in text or "headless" in text, (
                f"{name} must state the non-interactive rule explicitly — implying "
                "it leaves a headless run free to invent a filing status")


class TestInstaller:
    """install.sh is the only Claude-specific install path, and the only place the
    PII git hooks get installed for ANY agent."""

    def _script(self) -> str:
        return (REPO / "agents" / "install.sh").read_text()

    def test_sources_skills_from_the_canonical_location(self):
        assert ".agents/skills" in self._script(), (
            "install.sh must read skills from .agents/skills — the old agents/skills "
            "path no longer exists, so a stale reference installs nothing")

    def test_resolves_the_shared_git_dir_for_hooks(self):
        """In a worktree, .git is a FILE, so `$ROOT/.git/hooks` does not exist and
        the PII pre-commit guard would silently never install — precisely where
        agents most often work."""
        script = self._script()
        assert "--git-common-dir" in script, (
            "install.sh must resolve the hooks dir via `git rev-parse "
            "--git-common-dir`; assuming $WORKSPACE_ROOT/.git is a directory skips "
            "hook installation in every git worktree, with only a warning")
        assert "$WORKSPACE_ROOT/.git/hooks" not in script, (
            "the naive hooks path must not come back")

    def test_offers_a_copy_mode_for_symlink_less_clones(self):
        assert "--copy" in self._script(), (
            "Windows clones without developer mode cannot create symlinks and need "
            "a copy install mode")


class TestInstructionFiles:
    """AGENTS.md is the agent-neutral source of truth; CLAUDE.md is additive.

    Size and ordering are load-bearing, not cosmetic: Codex silently truncates the
    combined instruction chain at 32 KiB and Copilot CLI truncates instruction
    files around 160 lines (bug #2111). Content past the cut is invisible with no
    error, so the security rules must come first and the file must stay small.
    """

    AGENTS_MAX_BYTES = 28 * 1024

    def test_agents_md_exists_and_under_size_cap(self):
        agents = REPO / "AGENTS.md"
        assert agents.is_file()
        size = agents.stat().st_size
        assert size <= self.AGENTS_MAX_BYTES, (
            f"AGENTS.md is {size} bytes, over the {self.AGENTS_MAX_BYTES} cap — "
            "Codex silently truncates the combined instruction chain at 32 KiB, so "
            "content past the cap is invisible with no error. Move depth into "
            "docs/contributing/ and link it instead")

    def test_claude_md_imports_agents_md_as_first_content_line(self):
        lines = [ln.strip() for ln in (REPO / "CLAUDE.md").read_text().splitlines()]
        content = [ln for ln in lines if ln and not ln.startswith("#")]
        assert content and content[0] == "@AGENTS.md", (
            "CLAUDE.md's first content line must be the @AGENTS.md import so the "
            "shared project truth loads before anything Claude-specific")

    def test_claude_md_is_thin(self):
        n = len((REPO / "CLAUDE.md").read_text().splitlines())
        assert n <= 150, (
            f"CLAUDE.md is {n} lines — it may hold only Claude-specific content; "
            "agent-neutral project truth belongs in AGENTS.md, where every agent "
            "sees it")

    def test_security_rules_are_front_loaded(self):
        """Truncation must degrade gracefully: safeguards survive, conveniences die."""
        head = "\n".join((REPO / "AGENTS.md").read_text().splitlines()[:100]).lower()
        for needle in ("ssn", "chmod 600", "never"):
            assert needle in head, (
                f"{needle!r} missing from AGENTS.md's first 100 lines — an agent "
                "that truncates the file would lose the rule entirely")

    def test_tax_law_freshness_contract_is_front_loaded(self):
        head = "\n".join((REPO / "AGENTS.md").read_text().splitlines()[:100]).lower()
        assert "web_search" in head and "live" in head, (
            "the tax-law freshness contract must be in the first screenful — "
            "Codex's cached-web-search default silently yields prior-year figures, "
            "which produce a wrong return that looks entirely plausible")

    def test_no_live_doc_sends_readers_to_claude_md_for_relocated_content(self):
        """CLAUDE.md no longer holds the architecture, the test count, or the
        design notes. A contributor doc that still points there sends people to a
        file that cannot answer them — and, for the test-count checklist, leaves
        the real count silently stale.

        Provenance notes ("relocated ... from CLAUDE.md") are fine: they record
        where content came from rather than directing anyone to look there.
        """
        pointer_forms = ("see claude.md", "see `claude.md`", "in claude.md",
                         "in `claude.md`")
        offenders = []
        docs = [REPO / "README.md", REPO / "AGENTS.md"]
        docs += sorted((REPO / "docs" / "contributing").glob("*.md"))
        for doc in docs:
            if doc.name == "agents-compat.md":
                continue  # legitimately describes how agents read CLAUDE.md
            for i, line in enumerate(doc.read_text().splitlines(), 1):
                low = line.lower()
                if any(p in low for p in pointer_forms):
                    offenders.append(f"{doc.relative_to(REPO)}:{i}: {line.strip()[:90]}")
        assert not offenders, (
            "these point readers at CLAUDE.md for content that moved out of it:\n  "
            + "\n  ".join(offenders))

    def test_relocated_reference_docs_exist_and_are_linked(self):
        agents_text = (REPO / "AGENTS.md").read_text()
        for rel in ("docs/contributing/skill-catalog.md",
                    "docs/contributing/test-invariants.md",
                    "docs/contributing/tax-gotchas.md",
                    "docs/contributing/agents-compat.md",
                    "docs/contributing/state-module-playbook.md"):
            assert (REPO / rel).is_file(), f"{rel} is referenced but missing"
            assert rel in agents_text, (
                f"{rel} exists but AGENTS.md never points at it — relocated depth "
                "that nothing links to is lost, not moved")


class TestVSCodeSettings:
    """Copilot in VS Code reads .agents/skills only if the workspace says so.

    The skills documentation and the loader both list the path, but the SETTING's
    default object omits it (verified 2026-08) — so without this checked-in file a
    Copilot user silently sees zero skills and gets no error to debug.
    """

    def test_settings_file_maps_agents_skills(self):
        import json

        settings = REPO / ".vscode" / "settings.json"
        assert settings.is_file(), ".vscode/settings.json must be checked in"
        cfg = json.loads(settings.read_text())
        locations = cfg.get("chat.agentSkillsLocations", {})
        assert locations.get(".agents/skills") is True, (
            "chat.agentSkillsLocations must map '.agents/skills' to true")

    def test_settings_file_is_git_tracked(self):
        import subprocess

        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", ".vscode/settings.json"],
            cwd=REPO, capture_output=True, text=True)
        assert tracked.returncode == 0, (
            ".vscode/settings.json exists but is NOT git-tracked, so it reaches no "
            "one who clones. .gitignore must exclude the directory's CONTENTS "
            "(.vscode/*) rather than the directory (.vscode/) — git cannot "
            "re-include a file whose parent directory is excluded, so a bare "
            "negation silently does nothing")


class TestClaudeSymlinks:
    def test_one_symlink_per_skill(self):
        assert {p.name for p in CLAUDE_SKILLS.iterdir()} == EXPECTED_SKILLS

    def test_symlinks_are_relative_and_resolve_in_repo(self):
        for link in sorted(CLAUDE_SKILLS.iterdir()):
            assert link.is_symlink(), f"{link} must be a symlink"
            target = Path(link.readlink())
            assert not target.is_absolute(), (
                f"{link.name} points at an absolute path ({target}) — that breaks on "
                "every clone but the machine it was created on")
            resolved = link.resolve()
            assert resolved == (SKILLS_DIR / link.name).resolve(), (
                f"{link.name} resolves to {resolved}, not its .agents/skills twin")
            assert (resolved / "SKILL.md").is_file()
