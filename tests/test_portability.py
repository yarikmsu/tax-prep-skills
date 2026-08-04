"""Portability doc-sync suite — machine-checks the multi-agent layout invariants.

Every assertion here guards a claim made to users of a NON-Claude agent, where we
have no CI coverage (no vendor CLI licenses in CI, spec section 9). A silently-broken
symlink or a dropped frontmatter field would make the toolkit undiscoverable on that
agent with no other signal — the user just sees zero skills and no error.
"""
import os
import re
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

    def test_fbar_trigger_covers_signature_authority(self):
        """A description's trigger must not be NARROWER than the skill's own
        prerequisites. tax-fbar runs whenever `has_foreign_accounts` OR
        `signature_authority_foreign_accounts` is true, and the $10,000 aggregate
        test is what the skill PERFORMS — stating it as the trigger is circular,
        because the agent cannot know the aggregate until the skill gathers the
        accounts. A narrow trigger means the skill is never selected, and the
        failure mode is a missing FinCEN 114 rather than a visible error.
        """
        desc = _frontmatter(SKILLS_DIR / "tax-fbar" / "SKILL.md")["description"]
        assert "signature authority" in desc.lower(), (
            "signature authority alone triggers FinCEN 114 Part IV — with no "
            "owned account and nothing on the tax return to hint at it")

    def test_every_description_states_when_to_use_the_skill(self):
        """The Agent Skills spec requires a description to cover what the skill
        does AND when to use it.

        Descriptions are the only skill text loaded at startup, so this is the
        text an agent matches a request against — and 20 skills sharing a `tax-`
        prefix all compete for the same request. A description that says only
        what the skill does gives the agent nothing to discriminate on.

        The literal "Use when" is required rather than any phrasing that conveys
        a trigger. "Use after tax-compute" and "Use mid-year" read perfectly well
        but give this check nothing stable to assert on, and Anthropic's own
        examples all use the "Use when ..." form — so consistency costs nothing
        and buys a check that cannot be silenced by rewording.
        """
        missing = [d.name for d in _skill_dirs()
                   if "use when" not in _frontmatter(d / "SKILL.md")
                                         .get("description", "").lower()]
        assert not missing, (
            f"descriptions with no trigger clause: {missing}. Add 'Use when ...' "
            "naming the situations, file states, and words a user would say.")

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
            # CO-LOCATED, not merely both present somewhere in the file.
            # `"stop" in low` was satisfied by unrelated prose — this file
            # contains six occurrences, including "hard-stop" and "don't stop
            # at" — so the instruction could be INVERTED to "continue with the
            # cached figures; the user can paste corrections later" and the test
            # stayed green. That inversion is precisely the silent stale-figure
            # fallback AGENTS.md §2 classifies as a hard error, and it is the one
            # failure mode with no downstream detector: no test, tie-out or
            # review step sees a plausible wrong bracket. Requiring the two words
            # in ONE sentence is what makes the negation impossible to phrase.
            sentences = re.split(r"(?<=[.!?])\s+|\n", low)
            assert any("paste" in s and "stop" in s for s in sentences), (
                f"{name} tells the agent to search the web but no single sentence "
                "tells it to STOP and ask the user to PASTE current figures; a "
                "stale tax figure produces a wrong return that looks entirely "
                "plausible and that nothing downstream detects")

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

    def _code(self) -> str:
        """The script with comments stripped.

        Every assertion in this class matches a string in install.sh, and this
        file's comments deliberately record the approaches that were tried and
        rejected — so a comment explaining why a flag is WRONG keeps satisfying
        a test that greps for that flag. `test_resolves_the_shared_git_dir_for_hooks`
        was inverted by exactly that: deleting only the comment made it fail on
        correct code, while replacing the real resolution with something that
        breaks every worktree kept it green. Assert against operative lines only.
        """
        return "\n".join(ln for ln in self._script().splitlines()
                         if not ln.lstrip().startswith("#"))

    def test_sources_skills_from_the_canonical_location(self):
        assert ".agents/skills" in self._code(), (
            "install.sh must read skills from .agents/skills — the old agents/skills "
            "path no longer exists, so a stale reference installs nothing")

    def test_resolves_the_hooks_dir_via_git_rather_than_assuming_it(self):
        """`$ROOT/.git/hooks` is wrong twice over: in a worktree `.git` is a FILE,
        and under `core.hooksPath` git reads somewhere else entirely. Either way
        the guard installs where git never looks, and the installer says success.
        """
        code = self._code()
        assert "--git-path hooks" in code, (
            "install.sh must resolve the hooks dir via `git rev-parse --git-path "
            "hooks`, the only form that honours core.hooksPath")
        assert "$WORKSPACE_ROOT/.git/hooks" not in code, (
            "the naive hooks path must not come back")

    def test_offers_a_copy_mode_for_symlink_less_clones(self):
        assert "--copy)" in self._code(), (
            "Windows clones without developer mode cannot create symlinks and need "
            "a copy install mode")

    def test_offers_a_hooks_only_mode(self):
        """Codex, Cursor, Kimi and Copilot users must run this script — it is the
        only way to get the PII git hooks — but they read .agents/skills/ from the
        clone directly, so populating ~/.claude/skills leaves them an inert tree
        and a printed list of slash commands their agent does not have."""
        assert "--hooks-only)" in self._code(), (
            "install.sh must let a non-Claude user install the PII hooks without "
            "a global Claude skill install")

    def test_hooks_only_mode_fails_when_the_hooks_were_not_installed(self):
        """--hooks-only IS the security setup step, so its exit status is what a
        script or a careful user gates on. Printing "Git hooks installed" and
        exiting 0 after warning that they were not would report protection that
        does not exist."""
        import shutil
        import subprocess
        import tempfile

        # The installer resolves its workspace from its OWN location, so the
        # failure path needs the source unpacked outside a git clone — a tarball
        # download rather than `git clone`, which is how it actually happens.
        with tempfile.TemporaryDirectory() as sandbox:
            unpacked = Path(sandbox) / "tax-prep-skills"
            (unpacked / "agents").mkdir(parents=True)
            shutil.copy(REPO / "agents" / "install.sh", unpacked / "agents")
            r = subprocess.run(
                ["bash", str(unpacked / "agents" / "install.sh"), "--hooks-only"],
                cwd=sandbox, capture_output=True, text=True,
                env={**os.environ, "HOME": sandbox})
            assert "NOT installed" in r.stdout, (
                f"expected the no-hooks warning; got:\n{r.stdout}")
            assert r.returncode != 0, (
                "hooks-only reported success without installing any hook")


class TestRuntimeWorkspaceIgnored:
    """Every runtime workspace directory AGENTS.md calls gitignored must be.

    `archive/` was not, and the gap is not cosmetic: `tax-archive` moves
    `documents/` and `reviews/` BENEATH `archive/{year}/`, where the root-scoped
    `documents/*` rule no longer reaches them. Real taxpayer PDFs then show up as
    untracked files that a broad `git add` stages. The PII scan blocks the path,
    so nothing reaches a commit — but a claim in AGENTS.md that a directory is
    ignored has to be true, and the first line of defence should not be the last.
    """

    WORKSPACE_DIRS = ["documents/", "summaries/", "reviews/", "scenarios/",
                      "projections/", "archive/"]

    def test_gitignore_covers_every_runtime_workspace_directory(self, tmp_path):
        """Driven through `git check-ignore`, not matched as a substring.

        The substring form passed for the wrong reason twice over: the
        explanatory COMMENT above the rules contains the literal strings
        `documents/`, `reviews/` and `archive/`, and `!documents/.gitkeep` is a
        NEGATION that also contains `documents/`. Deleting the real rules while
        keeping the comment left the test green — and this test exists precisely
        because `archive/` was once missing and must never be again.
        """
        import shutil
        import subprocess

        work = tmp_path / "repo"
        work.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "."], cwd=work, check=True)
        shutil.copy(REPO / ".gitignore", work / ".gitignore")

        not_ignored = []
        for d in self.WORKSPACE_DIRS:
            probe = work / d.rstrip("/") / "2025" / "taxpayer.txt"
            probe.parent.mkdir(parents=True, exist_ok=True)
            probe.write_text("x")
            rel = str(probe.relative_to(work))
            r = subprocess.run(["git", "check-ignore", "-q", rel], cwd=work)
            if r.returncode != 0:
                not_ignored.append(rel)
        assert not not_ignored, f"git does NOT ignore: {not_ignored}"

    def test_the_gitkeep_exception_still_works(self):
        """The counterweight: `!documents/.gitkeep` must keep working, since it
        is what makes documents/ exist in a fresh clone."""
        import subprocess

        r = subprocess.run(["git", "check-ignore", "-q", "documents/.gitkeep"],
                           cwd=REPO)
        assert r.returncode != 0, "documents/.gitkeep became un-committable"

    def _run_installer(self, tmp: Path, seed_gitignore: str = None) -> str:
        """Run install.sh in a throwaway workspace and return the .gitignore it
        produced. Driving the script beats grepping it: the two code paths
        (create when absent, append when present) are what must agree, and only
        running both can show that they do."""
        import shutil
        import subprocess

        work = tmp / "tax-prep-skills"
        (work / "agents").mkdir(parents=True)
        (work / ".agents" / "skills" / "tax-setup").mkdir(parents=True)
        (work / ".agents" / "skills" / "tax-setup" / "SKILL.md").write_text(
            "---\nname: tax-setup\ndescription: x\n---\n")
        shutil.copy(REPO / "agents" / "install.sh", work / "agents")
        subprocess.run(["git", "init", "-q", "."], cwd=work, check=True)
        if seed_gitignore is not None:
            (work / ".gitignore").write_text(seed_gitignore)
        home = tmp / "home"
        home.mkdir()
        subprocess.run(["bash", "agents/install.sh"], cwd=work,
                       capture_output=True, text=True,
                       env={**os.environ, "HOME": str(home)})
        return (work / ".gitignore").read_text()

    def _unignored(self, gitignore_text: str, tmp: Path) -> list:
        """Which workspace dirs git does NOT ignore, given this .gitignore.

        Driven through `git check-ignore` rather than substring-matched, for the
        reason the sibling test above documents and these two used to re-commit:
        `"documents/" in text` is satisfied by the NEGATION `!documents/.gitkeep`
        and by the explanatory comment. Deleting the real `documents/*` rule from
        the installer left both of these green while an installed workspace
        stopped ignoring a taxpayer's documents — verified by mutation.
        """
        import subprocess

        probe_repo = tmp / "probe"
        probe_repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "."], cwd=probe_repo, check=True)
        (probe_repo / ".gitignore").write_text(gitignore_text)
        unignored = []
        for d in self.WORKSPACE_DIRS:
            probe = probe_repo / d.rstrip("/") / "2025" / "taxpayer.txt"
            probe.parent.mkdir(parents=True, exist_ok=True)
            probe.write_text("x")
            rel = str(probe.relative_to(probe_repo))
            if subprocess.run(["git", "check-ignore", "-q", rel],
                              cwd=probe_repo).returncode != 0:
                unignored.append(rel)
        return unignored

    def test_installer_protects_a_workspace_that_had_no_gitignore(self, tmp_path):
        produced = self._run_installer(tmp_path / "fresh")
        missing = self._unignored(produced, tmp_path / "fresh_probe")
        assert not missing, f"create path leaves these unignored: {missing}"

    def test_installer_protects_a_workspace_that_already_had_one(self, tmp_path):
        produced = self._run_installer(tmp_path / "existing",
                                       seed_gitignore="# mine\n*.log\n")
        missing = self._unignored(produced, tmp_path / "existing_probe")
        assert not missing, f"append path leaves these unignored: {missing}"
        assert "*.log" in produced, "the user's own entries must survive"

    def test_both_installer_paths_produce_the_same_protections(self, tmp_path):
        """The defect this replaces: two hardcoded lists, and the create path was
        already missing eight patterns the append path had. A grep-the-source
        test passed because the pattern appeared in ONE of them — the same
        hand-mirroring failure this repository shipped between the hook and CI.
        """
        def patterns(text):
            # Compare the RULES, not the prose: only the create path writes the
            # explanatory header, and a comment protects nothing either way.
            return {ln.strip() for ln in text.splitlines()
                    if ln.strip() and not ln.lstrip().startswith("#")}

        fresh = patterns(self._run_installer(tmp_path / "a"))
        existing = patterns(self._run_installer(tmp_path / "b", seed_gitignore=""))
        assert fresh == existing, (
            f"create path and append path disagree: "
            f"only-create={sorted(fresh - existing)} "
            f"only-append={sorted(existing - fresh)}")

    def test_installer_is_idempotent(self, tmp_path):
        """Re-running must not duplicate entries — users re-run it after a pull."""
        import shutil
        import subprocess

        work = tmp_path / "idem" / "tax-prep-skills"
        (work / "agents").mkdir(parents=True)
        (work / ".agents" / "skills" / "tax-setup").mkdir(parents=True)
        (work / ".agents" / "skills" / "tax-setup" / "SKILL.md").write_text(
            "---\nname: tax-setup\ndescription: x\n---\n")
        shutil.copy(REPO / "agents" / "install.sh", work / "agents")
        subprocess.run(["git", "init", "-q", "."], cwd=work, check=True)
        home = tmp_path / "idem" / "home"
        home.mkdir(parents=True)
        env = {**os.environ, "HOME": str(home)}
        for _ in range(2):
            subprocess.run(["bash", "agents/install.sh"], cwd=work,
                           capture_output=True, text=True, env=env)
        lines = [ln for ln in (work / ".gitignore").read_text().splitlines()
                 if ln.strip() and not ln.startswith("#")]
        assert len(lines) == len(set(lines)), (
            f"duplicated after a second run: "
            f"{[x for x in lines if lines.count(x) > 1]}")


class TestInstallerHooks:
    """The hooks are the ONLY host-side protection, so the installer's report of
    them must be true. Each test here drives install.sh in a throwaway repo and
    then asks git the question that matters: does a full SSN still commit?"""

    def _workspace(self, tmp: Path) -> Path:
        import shutil
        import subprocess

        work = tmp / "tax-prep-skills"
        (work / "agents").mkdir(parents=True)
        (work / "scripts").mkdir(parents=True)
        (work / ".agents" / "skills" / "tax-setup").mkdir(parents=True)
        (work / ".agents" / "skills" / "tax-setup" / "SKILL.md").write_text(
            "---\nname: tax-setup\ndescription: x\n---\n")
        shutil.copy(REPO / "agents" / "install.sh", work / "agents")
        shutil.copy(REPO / "scripts" / "pre-commit-pii-scan.sh", work / "scripts")
        shutil.copy(REPO / ".pii-allowlist", work / ".pii-allowlist")
        subprocess.run(["git", "init", "-q", "-b", "main", "."], cwd=work, check=True)
        subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=work, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=work, check=True)
        return work

    def _install(self, work: Path, home: Path, *args):
        import subprocess

        home.mkdir(parents=True, exist_ok=True)
        return subprocess.run(["bash", "agents/install.sh", *args], cwd=work,
                              capture_output=True, text=True,
                              env={**os.environ, "HOME": str(home)})

    def _try_committing_an_ssn(self, work: Path) -> int:
        """Returns the commit's exit status. Non-zero means a hook stopped it.

        The value is assembled at runtime for the reason given at the top of
        tests/test_pii_content_scan.py — a literal here would trip this repo's
        own scanner on the test file itself.
        """
        import subprocess

        area, group, serial = "123", "45", "6789"
        subprocess.run(["git", "checkout", "-q", "-b", "work"], cwd=work, check=True)
        (work / "notes.txt").write_text(f"taxpayer ssn {area}-{group}-{serial}\n")
        subprocess.run(["git", "add", "notes.txt"], cwd=work, check=True)
        return subprocess.run(["git", "commit", "-m", "leak"], cwd=work,
                              capture_output=True, text=True).returncode

    def test_hooks_are_installed_where_core_hookspath_points(self, tmp_path):
        """`--git-common-dir` ignores core.hooksPath, so hooks landed in
        .git/hooks — a directory git then never consults. husky, lefthook, the
        pre-commit framework and any corporate `core.hooksPath` all set it. The
        installer printed success, --hooks-only exited 0, and a full SSN
        committed cleanly with the user told the gate was armed."""
        import subprocess

        work = self._workspace(tmp_path / "hp")
        subprocess.run(["git", "config", "core.hooksPath", ".husky"],
                       cwd=work, check=True)
        (work / ".husky").mkdir()
        subprocess.run(["git", "add", "-A"], cwd=work, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=work, check=True)

        r = self._install(work, tmp_path / "hp" / "home", "--hooks-only")
        assert r.returncode == 0, f"install failed: {r.stdout}{r.stderr}"
        assert (work / ".husky" / "pre-commit").exists(), (
            "hook was not written to the directory git actually uses")
        assert self._try_committing_an_ssn(work) != 0, (
            "a full SSN committed cleanly — the hook is not where git looks")

    def test_an_existing_foreign_hook_is_backed_up_not_destroyed(self, tmp_path):
        """Another hook manager owning pre-commit is a normal setup. Clobbering
        it silently removes a protection the user chose."""
        import subprocess

        work = self._workspace(tmp_path / "bk")
        hook = work / ".git" / "hooks" / "pre-commit"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\necho MY-EXISTING-HOOK\n")
        hook.chmod(0o755)
        subprocess.run(["git", "add", "-A"], cwd=work, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=work, check=True)

        self._install(work, tmp_path / "bk" / "home", "--hooks-only")
        backup = work / ".git" / "hooks" / "pre-commit.pre-tax-prep-skills.bak"
        assert backup.exists(), "the user's hook was destroyed without a backup"
        assert "MY-EXISTING-HOOK" in backup.read_text()

    def test_a_dangling_hook_symlink_does_not_write_outside_the_hooks_dir(self, tmp_path):
        """`[ -e ]` is FALSE for a dangling symlink, so no backup was taken — and
        `cat >` then FOLLOWED the link, creating the hook at its target, outside
        the hooks directory where git never runs it. The installer reported
        success and nothing guarded commits.
        """
        import subprocess

        work = self._workspace(tmp_path / "dangle")
        outside = tmp_path / "dangle" / "elsewhere" / "target-hook"
        outside.parent.mkdir(parents=True, exist_ok=True)
        hooks = work / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        (hooks / "pre-commit").symlink_to(outside)
        subprocess.run(["git", "add", "-A"], cwd=work, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=work, check=True)

        self._install(work, tmp_path / "dangle" / "home", "--hooks-only")

        assert not outside.exists(), (
            "the hook was written THROUGH the dangling symlink, landing outside "
            "the hooks directory")
        hook = hooks / "pre-commit"
        assert hook.is_file() and not hook.is_symlink(), (
            "the installed hook must be a real file in the hooks directory")
        assert self._try_committing_an_ssn(work) != 0, (
            "a full SSN committed cleanly — the guard is not actually running")

    def test_hooks_are_not_installed_into_a_shared_hooks_directory(self, tmp_path):
        """core.hooksPath can be GLOBAL, resolving to one directory for EVERY
        repository on the machine. Installing there replaced the hook governing
        all of them, and this toolkit's guard then looked for its scanner in each
        other repository's root, did not find it, and refused every commit there.
        Honouring core.hooksPath and owning it are different things.
        """
        import subprocess

        shared = tmp_path / "shared-hooks"
        shared.mkdir(parents=True)
        users_hook = shared / "pre-commit"
        users_hook.write_text("#!/bin/sh\necho ORG-COMPLIANCE-HOOK\n")
        users_hook.chmod(0o755)

        work = self._workspace(tmp_path / "shared")
        subprocess.run(["git", "config", "core.hooksPath", str(shared)],
                       cwd=work, check=True)
        r = self._install(work, tmp_path / "shared" / "home", "--hooks-only")

        assert "ORG-COMPLIANCE-HOOK" in users_hook.read_text(), (
            "replaced a hooks directory shared with other repositories")
        assert not (shared / "pre-commit.pre-tax-prep-skills.bak").exists()
        assert r.returncode != 0, (
            "must not report success when no guard was installed")
        assert "outside this repository" in r.stdout

    def test_reinstalling_does_not_back_up_our_own_hook(self, tmp_path):
        """The counterweight: re-running the installer is routine, and a pile of
        .bak files each time would make the real warning meaningless."""
        import subprocess

        work = self._workspace(tmp_path / "re")
        subprocess.run(["git", "add", "-A"], cwd=work, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=work, check=True)
        home = tmp_path / "re" / "home"
        self._install(work, home, "--hooks-only")
        self._install(work, home, "--hooks-only")
        backups = list((work / ".git" / "hooks").glob("*.bak"))
        assert not backups, f"re-install backed up its own hook: {backups}"

    def test_refuses_to_write_into_an_enclosing_repository(self, tmp_path):
        """Nothing checked that the workspace was itself a repository root, so a
        toolkit unpacked inside another clone replaced THAT project's hooks and
        broke its commits — the hook resolves a scanner path that is not there."""
        import subprocess

        outer = tmp_path / "outer"
        outer.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main", "."], cwd=outer, check=True)
        outer_hook = outer / ".git" / "hooks" / "pre-commit"
        outer_hook.parent.mkdir(parents=True, exist_ok=True)
        outer_hook.write_text("#!/bin/sh\necho OUTER-PROJECT-HOOK\n")
        outer_hook.chmod(0o755)

        inner = outer / "tax-prep-skills"
        (inner / "agents").mkdir(parents=True)
        (inner / ".agents" / "skills" / "tax-setup").mkdir(parents=True)
        (inner / ".agents" / "skills" / "tax-setup" / "SKILL.md").write_text(
            "---\nname: tax-setup\ndescription: x\n---\n")
        import shutil
        shutil.copy(REPO / "agents" / "install.sh", inner / "agents")

        home = tmp_path / "outerhome"
        home.mkdir()
        r = subprocess.run(["bash", "agents/install.sh", "--hooks-only"], cwd=inner,
                           capture_output=True, text=True,
                           env={**os.environ, "HOME": str(home)})
        assert "OUTER-PROJECT-HOOK" in outer_hook.read_text(), (
            "the enclosing repository's hook was overwritten")
        assert r.returncode != 0, "reported success without installing anything"

    def test_every_mode_fails_when_hooks_could_not_be_installed(self, tmp_path):
        """Round 4 hardened only --hooks-only. README's Quick Start, Upgrading
        and Contributing sections all use the bare form, which still printed
        'Installed 20 tax skills' and exited 0 with nothing guarding commits."""
        import shutil
        import subprocess

        for mode in ([], ["--copy"], ["--hooks-only"]):
            sandbox = tmp_path / ("mode" + ("-".join(mode) or "default"))
            unpacked = sandbox / "tax-prep-skills"
            (unpacked / "agents").mkdir(parents=True)
            (unpacked / ".agents" / "skills" / "tax-setup").mkdir(parents=True)
            (unpacked / ".agents" / "skills" / "tax-setup" / "SKILL.md").write_text(
                "---\nname: tax-setup\ndescription: x\n---\n")
            shutil.copy(REPO / "agents" / "install.sh", unpacked / "agents")
            home = sandbox / "home"
            home.mkdir()
            r = subprocess.run(["bash", "agents/install.sh", *mode], cwd=unpacked,
                               capture_output=True, text=True,
                               env={**os.environ, "HOME": str(home)})
            assert r.returncode != 0, (
                f"mode {mode or ['(default)']} reported success with no hooks "
                f"installed:\n{r.stdout}")
            assert "NOT installed" in r.stdout


class TestInstallerPruning:
    def test_a_withdrawn_skill_is_reported_not_silently_left(self, tmp_path):
        """A renamed or withdrawn skill stays installed and invocable, and under
        --copy is indistinguishable from a live one.

        REPORTED, not deleted: ~/.claude/skills is shared with skills from every
        other source, a `tax-` prefix does not prove we installed it, and no
        skill has ever actually been renamed or withdrawn. Destroying a user's
        files to pre-empt that is the wrong side of the trade.
        """
        import shutil
        import subprocess

        work = tmp_path / "clone"
        (work / "agents").mkdir(parents=True)
        for name in ("tax-setup", "tax-gone"):
            d = work / ".agents" / "skills" / name
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: x\n---\n")
        shutil.copy(REPO / "agents" / "install.sh", work / "agents")
        subprocess.run(["git", "init", "-q", "."], cwd=work, check=True)
        home = tmp_path / "home"
        home.mkdir()
        env = {**os.environ, "HOME": str(home)}

        subprocess.run(["bash", "agents/install.sh", "--copy"], cwd=work,
                       capture_output=True, text=True, env=env)
        assert (home / ".claude" / "skills" / "tax-gone").exists()

        shutil.rmtree(work / ".agents" / "skills" / "tax-gone")
        r = subprocess.run(["bash", "agents/install.sh", "--copy"], cwd=work,
                           capture_output=True, text=True, env=env)
        assert "tax-gone" in r.stdout and "no longer in .agents/skills/" in r.stdout, (
            f"a withdrawn skill was left installed with no warning:\n{r.stdout}")
        assert (home / ".claude" / "skills" / "tax-gone").exists(), (
            "copy-mode install must not delete a file it cannot prove it owns")
        assert (home / ".claude" / "skills" / "tax-setup").exists()

    def test_a_dangling_link_into_this_clone_is_removed(self, tmp_path):
        """The one unambiguous case: a symlink resolving into THIS clone with no
        target is ours by construction and already broken — the agent lists a
        skill whose body cannot be read."""
        import shutil
        import subprocess

        work = tmp_path / "clone"
        (work / "agents").mkdir(parents=True)
        for name in ("tax-setup", "tax-gone"):
            d = work / ".agents" / "skills" / name
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: x\n---\n")
        shutil.copy(REPO / "agents" / "install.sh", work / "agents")
        subprocess.run(["git", "init", "-q", "."], cwd=work, check=True)
        home = tmp_path / "home"
        home.mkdir()
        env = {**os.environ, "HOME": str(home)}

        subprocess.run(["bash", "agents/install.sh"], cwd=work,
                       capture_output=True, text=True, env=env)
        assert (home / ".claude" / "skills" / "tax-gone" / "SKILL.md").is_symlink()

        shutil.rmtree(work / ".agents" / "skills" / "tax-gone")
        subprocess.run(["bash", "agents/install.sh"], cwd=work,
                       capture_output=True, text=True, env=env)
        assert not (home / ".claude" / "skills" / "tax-gone").exists(), (
            "a dangling link into this clone should be cleaned up")

    def test_pruning_never_touches_a_users_own_skills(self, tmp_path):
        """~/.claude/skills is shared with everything else the user installs."""
        import shutil
        import subprocess

        work = tmp_path / "clone"
        (work / "agents").mkdir(parents=True)
        d = work / ".agents" / "skills" / "tax-setup"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("---\nname: tax-setup\ndescription: x\n---\n")
        shutil.copy(REPO / "agents" / "install.sh", work / "agents")
        subprocess.run(["git", "init", "-q", "."], cwd=work, check=True)
        home = tmp_path / "home"
        mine = home / ".claude" / "skills" / "my-own-skill"
        mine.mkdir(parents=True)
        (mine / "SKILL.md").write_text("---\nname: my-own-skill\ndescription: x\n---\n")

        subprocess.run(["bash", "agents/install.sh", "--copy"], cwd=work,
                       capture_output=True, text=True,
                       env={**os.environ, "HOME": str(home)})
        assert mine.exists(), "the installer deleted a skill it did not own"


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


class TestReadmeOnboarding:
    """The README is the only onboarding surface for four of the five agents.

    Every entry below is an explicit registry rather than something derived from
    the README's own text: a check that selects its own scope from the document
    it inspects can always be silenced by editing that document.
    """

    # (agent, a substring that only appears if its invocation syntax is shown)
    # Each token must be unique to the invocation TABLE. Three of the previous
    # five matched unrelated prose — `/tax-setup` is a substring of the path
    # `.agents/skills/tax-setup/SKILL.md`, and the bare words `Cursor` and
    # `VS Code` appear throughout the README — so the whole table could be
    # deleted with the test still green. That is the same substring trap this
    # file's sibling test documents for `/skills`, hit a second time.
    INVOCATION_SYNTAX = [
        ("Claude Code", "`/tax-setup 2025`"),
        ("Codex", "`$tax-setup`"),
        ("Kimi Code", "`/skill:tax-setup 2025`"),
        ("Cursor", "search `tax-setup`"),
        ("VS Code Copilot", "follow the tax-setup skill"),
    ]

    def _invocation_table(self):
        """The table only, so a token cannot be satisfied by prose elsewhere."""
        readme = (REPO / "README.md").read_text()
        rows = [ln for ln in readme.splitlines()
                if ln.startswith("|") and "tax-setup" in ln]
        assert rows, "the per-agent invocation table is gone from README.md"
        return "\n".join(rows)

    def test_readme_documents_per_agent_invocation_syntax(self):
        table = self._invocation_table()
        missing = [label for label, token in self.INVOCATION_SYNTAX
                   if token not in table]
        assert not missing, (
            f"README's invocation table shows no syntax for: {missing}. Only "
            "Claude Code and Cursor use a bare /skill-name — Codex uses "
            "$skill-name and Kimi uses /skill:name. A user of any other agent "
            "gets silence, not an error, which is indistinguishable from a "
            "failed install.")

    def test_readme_states_the_real_git_version_floor(self):
        """The scanner's own failure message used to name git 2.9 while the flag
        it needs arrived in 2.20. A user on 2.17 read "2.9+ required", concluded
        the message was wrong about the cause, and the only other instruction on
        screen was `git commit --no-verify` — a false remedy pointing straight at
        disabling the repository's only PII gate."""
        readme = (REPO / "README.md").read_text()
        scanner = (REPO / "scripts" / "pre-commit-pii-scan.sh").read_text()
        # Anchored to the PREREQUISITES bullet, not to "2.20" appearing anywhere:
        # any future prose carrying that substring (a version, a date, a dollar
        # figure) would otherwise satisfy this with the requirement deleted.
        assert re.search(r"^[-*] \*\*Git 2\.20 or newer\*\*", readme, re.M), (
            "README's prerequisites must state the real git version floor")
        assert "2.9+ required" not in scanner, "scanner names the wrong floor"
        assert "2.20+ required" in scanner

    def test_readme_tells_the_user_how_to_verify_discovery(self):
        readme = (REPO / "README.md").read_text()
        # Match the backticked Codex COMMAND, not a bare "/skills" — the latter
        # is a substring of the `.agents/skills/` path that appears throughout
        # this README, so asserting on it would pass without the step existing.
        assert "`/skills`" in readme, (
            "README must show the command that lists discovered skills")
        lowered = readme.lower()
        assert "no skill and no error" in lowered or "fails silently" in lowered, (
            "README must say that discovery failure is SILENT. Without that, a "
            "user who sees nothing has no reason to suspect the install rather "
            "than the toolkit.")

    def test_quick_start_includes_the_live_web_search_step(self):
        """A cached tax figure is the one error no downstream gate can catch —
        no test, tie-out or review step detects a prior year's bracket — so the
        setting that prevents it belongs in the numbered steps, not in a
        prerequisites bullet a reader skims."""
        readme = (REPO / "README.md").read_text()
        quick_start = readme.split("## Quick Start", 1)[1].split("\n## ", 1)[0]
        assert 'web_search = "live"' in quick_start, (
            "the exact live-search setting must appear inside Quick Start")

    def test_readme_states_that_the_clone_is_the_workspace(self):
        readme = (REPO / "README.md").read_text()
        lowered = readme.lower()
        assert "your tax workspace" in lowered or "is your workspace" in lowered, (
            "README must say the clone directory becomes the taxpayer workspace; "
            "a reader otherwise has no reason to expect result.json and "
            "documents/ to appear inside a git checkout")
        assert "never push this clone" in lowered, (
            "README must warn against pushing a clone that holds taxpayer files")
