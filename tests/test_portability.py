"""Portability doc-sync suite — machine-checks the multi-agent layout invariants.

Every assertion here guards a claim made to users of a NON-Claude agent, where we
have no CI coverage (no vendor CLI licenses in CI, spec section 9). A silently-broken
symlink or a dropped frontmatter field would make the toolkit undiscoverable on that
agent with no other signal — the user just sees zero skills and no error.
"""
import ast
import dataclasses
import datetime
import inspect
import json
import os
import re
import subprocess
import textwrap
import sys
import types
from pathlib import Path

import pytest

from tests.skill_text import CONTRIBUTOR_SKILLS, skill_names, skill_text
from tests.engine_tree import (
    GUARD as ENGINE_GUARD,
    engine_files,
    engine_module_name,
    engine_relpath,
    engine_top_level_names,
)

REPO = Path(__file__).resolve().parent.parent

# Hook input declarations: (priority, path patterns). Lower priority wins.
# Parsed as literals by the selector; this module is never imported by it.
WATCHES = (
    (10, ('.agents/skills', '.agents/skills/*', '.claude/skills', '.claude/skills/*')),
    (30, ('docs/ops/review-subagent-acceptance.md',)),
    (90, ('docs/ops/season-open-runbook.md',)),
    (100, ('docs/ops',)),
    (130, ('scripts/pre-commit-pii-scan.sh',)),
    (140, ('agents/lib/capabilities.py', 'agents/lib/rollover.py')),
    (150, ('agents/lib/tax_law_fetcher.py',)),
    (155, ('agents/lib/tie_out.py',)),
    (160, ('agents/lib/ptet.py',)),
    (165, ('agents/lib/tax_law_export.py',)),
    (170, ('agents/lib/tax_computations', 'agents/lib/tax_computations/*')),
    (190, ('agents/lib/secure_fs.py',)),
    (210, ('agents/lib',)),
    (230, ('agents/lib/*.sh',)),
    (240, ('agents/lib/*',)),
    (250, ('tests/conftest.py',)),
    (280, ('tests/test_host_guard.py',)),
    (310, ('agents/templates/result_template.json',)),
    (320, ('agents/templates/projection_inputs_template.json',)),
    (330, ('tests/skill_text.py',)),
    (340, ('tests/engine_tree.py',)),
    (350, ('docs/QUICKSTART.md', 'docs/contributing/agents-compat.md')),
    (360, ('CONTRIBUTING.md', 'README.md')),
    (370, ('agents/install.sh',)),
    (380, ('CLAUDE.md',)),
    (400, ('AGENTS.md',)),
    (430, ('LICENSE', '.gitignore', '.pii-allowlist', '.vscode/settings.json', 'docs/contributing', 'docs/contributing/*.md', 'docs/ops', 'docs/ops/*.md', 'docs/roadmap', 'docs/roadmap/*.md')),
    (440, ('tests/test_portability.py',)),
    (450, ('tests',)),
)
SKILLS_DIR = REPO / ".agents" / "skills"
CLAUDE_SKILLS = REPO / ".claude" / "skills"

# The toolkit itself — what a taxpayer clones this repository for.
PRODUCT_SKILLS = {
    "tax-amend", "tax-archive", "tax-calendar", "tax-compute", "tax-extend",
    "tax-fbar", "tax-generate", "tax-harvest", "tax-parse", "tax-parse-crypto",
    "tax-project", "tax-ptet", "tax-retirement", "tax-review", "tax-rollover",
    "tax-roth-convert", "tax-safe-harbor", "tax-scenario", "tax-setup", "tax-verify",
    "tax-withholding",
}

# Skills for people working ON the toolkit rather than filing with it. They ship
# in .agents/skills/ deliberately: AGENTS.md invites community state modules, and
# a contributor skill only one vendor's agent can see would contradict the
# portability work that put every skill there in the first place.
#
# The split is only a LABEL. Both sets are held to every rule below — frontmatter,
# the neutralization lint, the line cap, and a .claude/skills symlink each — so
# nothing is relaxed by naming the boundary that the `tax-` prefix already implied.

EXPECTED_SKILLS = PRODUCT_SKILLS | CONTRIBUTOR_SKILLS


def _projection():
    """The engine, imported at CALL time rather than module scope.

    Every other assertion in this file reads the repository as FILES, which is
    what lets the skill-discovery invariants fail one at a time and name
    themselves. An `import agents.lib.projection` up top would hand the whole
    module's collection to the health of a five-module import chain
    (projection -> rollover -> tax_computations -> tax_law_fetcher ->
    secure_fs): one SyntaxError there and every portability test errors at
    COLLECTION reporting an unrelated ImportError, instead of the four named
    subprocess failures a broken engine used to cost. This suite already
    records that shape — a collection failure names the wrong thing — for
    capabilities and agents under the `agents/lib` delete arm.
    """
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from agents.lib import projection
    return projection


def _withholding():
    """/tax-withholding's engine, imported at CALL time for the same reason.

    It carries HALF the payload the two consuming skills are told to read:
    the catch-up notes they surface are fields of `TrueUpReport`/`CaTrueUp`,
    not of anything in `projection`.
    """
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from agents.lib import withholding
    return withholding


def _tax_computations():
    """`compute_form_2210`'s module, imported at CALL time for the same reason.

    It owns the one §6654 constant `projection` does not: the $1,000
    de-minimis balance, which the filed-return penalty check applies and the
    advisory skill states as a refusal it cannot apply itself.
    """
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from agents.lib import tax_computations
    return tax_computations


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
    # Skills that exceed the spec's 500-line SKILL.md limit and have not yet been
    # restructured. This set may only SHRINK — see the paired assertion below.
    OVERSIZED: set[str] = set()

    MAX_SKILL_LINES = 500
    MAX_NAME_CHARS = 64
    MAX_DESCRIPTION_CHARS = 1024
    NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
    LICENSE = "Apache-2.0"
    REF_LINK_RE = re.compile(r"references/([\w.-]+\.md)")

    def test_all_expected_skills_present(self):
        found = {p.name for p in _skill_dirs()}
        assert found == EXPECTED_SKILLS, (
            f"missing: {sorted(EXPECTED_SKILLS - found)}; "
            f"unexpected: {sorted(found - EXPECTED_SKILLS)}. "
            "This list is deliberately explicit rather than globbed: adding a skill "
            "must also add its .claude/skills symlink, so the update is a prompt, "
            "not busywork")

    def test_the_product_and_contributor_sets_stay_legible(self):
        """The split names a boundary the `tax-` prefix only implied, and it is
        worth nothing if the two sets blur.

        The prefix rule is the whole content of the label: a `tax-` skill is
        something a taxpayer invokes on their own return, and anything else is
        work on the toolkit. Without this, `CONTRIBUTOR_SKILLS` becomes the set
        someone appends to when a name does not fit, and the distinction the
        split was made to express stops being true of the data.
        """
        assert not (PRODUCT_SKILLS & CONTRIBUTOR_SKILLS), "a skill is in both sets"
        mislabelled = sorted(n for n in PRODUCT_SKILLS if not n.startswith("tax-"))
        assert not mislabelled, (
            f"{mislabelled} are in PRODUCT_SKILLS but are not `tax-` skills")
        mislabelled = sorted(n for n in CONTRIBUTOR_SKILLS if n.startswith("tax-"))
        assert not mislabelled, (
            f"{mislabelled} are `tax-` skills sitting in CONTRIBUTOR_SKILLS")

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

    def test_every_skill_declares_the_repository_license(self):
        """The spec's optional license field is the only license signal that
        travels with a skill folder someone copies out of this repo — so the
        VALUE is what matters, not merely that the key is set. A skill declaring
        the wrong license travels exactly as far as one declaring the right one,
        and nothing downstream re-derives it from the LICENSE file.
        """
        for d in _skill_dirs():
            declared = _frontmatter(d / "SKILL.md").get("license")
            assert declared == self.LICENSE, (
                f"{d.name} declares license={declared!r}; the repository is "
                f"{self.LICENSE}")

    def test_the_license_file_is_what_the_skills_declare(self):
        """Pins LICENSE itself, so the constant above cannot drift away from the
        text it names — relicensing the repo without touching 20 frontmatter
        blocks would otherwise leave every skill advertising the old terms."""
        assert (REPO / "LICENSE").is_file(), "no LICENSE file"
        text = (REPO / "LICENSE").read_text()
        assert "Apache License" in text and "Version 2.0" in text, (
            f"LICENSE is not the {self.LICENSE} text every skill declares")

    def test_frontmatter_name_satisfies_the_spec(self):
        for d in _skill_dirs():
            name = _frontmatter(d / "SKILL.md").get("name", "")
            assert len(name) <= self.MAX_NAME_CHARS, f"{d.name}: name too long"
            assert self.NAME_RE.match(name), (
                f"{d.name}: name {name!r} must be lowercase alphanumerics and "
                "single hyphens, with no leading, trailing or doubled hyphen")

    def test_frontmatter_description_within_the_spec_limit(self):
        for d in _skill_dirs():
            desc = _frontmatter(d / "SKILL.md").get("description", "")
            assert 0 < len(desc) <= self.MAX_DESCRIPTION_CHARS, (
                f"{d.name}: description is {len(desc)} chars "
                f"(limit {self.MAX_DESCRIPTION_CHARS})")

    def test_skill_bodies_are_under_the_line_limit(self):
        over = []
        for d in _skill_dirs():
            if d.name in self.OVERSIZED:
                continue
            n = len((d / "SKILL.md").read_text().splitlines())
            if n > self.MAX_SKILL_LINES:
                over.append(f"{d.name} ({n})")
        assert not over, (
            f"SKILL.md over {self.MAX_SKILL_LINES} lines: {over}. Move detail "
            "into references/ and link it one level deep from SKILL.md.")

    def test_the_oversized_allowlist_only_shrinks(self):
        """A skill that has been restructured must leave the allowlist, or the
        list silently readmits regressions."""
        stale = []
        for name in sorted(self.OVERSIZED):
            n = len((SKILLS_DIR / name / "SKILL.md").read_text().splitlines())
            if n <= self.MAX_SKILL_LINES:
                stale.append(f"{name} ({n})")
        assert not stale, (
            f"now under the limit and must be removed from OVERSIZED: {stale}")

    def test_frontmatter_values_have_no_unquoted_colon(self):
        """_frontmatter() splits on the first colon and accepts anything after
        it. Every other agent uses a real YAML parser, where an unquoted scalar
        containing ': ' is a parse error — and a skill that fails to parse
        disappears with no message on exactly the agents this repo has no CI for.

        Scoped to that ONE construct, and named for it. This is a heuristic, not
        a parser: a leading `@`/`[`/`&`, a mid-value ` #`, a tab, or a duplicate
        key would also break real YAML and none is caught here. Adding PyYAML to
        catch them would put a dependency in requirements.txt to serve a test,
        against a pinned-deps posture adopted for supply-chain reasons — so the
        honest move is to narrow the claim rather than widen the check.
        """
        bad = []
        for d in _skill_dirs():
            block = (d / "SKILL.md").read_text().split("---\n", 2)[1]
            for line in block.splitlines():
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                key, _, value = line.partition(":")
                v = value.strip()
                if v and v[0] not in "\"'" and ": " in v:
                    bad.append(f"{d.name}: unquoted colon in {key.strip()!r}")
        assert not bad, f"frontmatter would fail a strict YAML parser: {bad}"

    def test_reference_files_are_one_level_deep(self):
        """Agents partially read files reached through nested references, so
        every reference must link DIRECTLY from SKILL.md — and every such link
        must resolve.

        All three directions are checked, because each fails silently in its own
        way and none implies the others:

        - a **dangling** link is a step the agent is told to read and cannot, so
          the procedure is simply absent at that point in the return;
        - an **unlinked** file is content no agent ever loads;
        - a **nested** file defeats `tests/skill_text.py`, which globs
          `references/*.md` exactly one level down. Text moved into a
          subdirectory drops out of every doc-sync assertion at once while they
          all stay green — the precise vacuity that helper exists to prevent.
        """
        for d in _skill_dirs():
            refs = d / "references"
            body = (d / "SKILL.md").read_text()

            for link in sorted(set(self.REF_LINK_RE.findall(body))):
                assert (refs / link).is_file(), (
                    f"{d.name}/SKILL.md links references/{link}, which does not "
                    "exist — the agent is sent to read a file that is not there")

            if not refs.is_dir():
                continue
            for f in sorted(refs.rglob("*.md")):
                rel = f.relative_to(refs)
                assert len(rel.parts) == 1, (
                    f"{d.name}/references/{rel} is nested. Flatten it: both the "
                    "linkage check here and skill_text() stop one level down")
                assert f"references/{f.name}" in body, (
                    f"{d.name}/references/{f.name} is not linked from SKILL.md")

    def test_every_bash_block_in_every_skill_parses(self):
        """A skill is instructions to run, and an angle-bracket placeholder is a
        REDIRECT.

        `PR=<number>` does not read as "fill this in" to a shell: it tries to
        open a file called `number` and aborts the line. So does
        `--categories <comma-separated ...>`, which had been sitting in
        tax-rollover's apply step — the one command that writes carryforwards
        into the new year's workspace. The failure is at paste time, in front of
        a user, with a syntax error naming a token the document never mentions.

        `bash -n` parses without executing, so this costs nothing and no command
        runs. It is a SYNTAX check only: it cannot tell whether a command does
        the right thing, only that a shell would accept it. Brace and quoted
        placeholders (`{current_year}`, `"{prior year}"`) pass, which is why
        those are the conventions here.
        """
        import subprocess

        offenders = []
        for d in _skill_dirs():
            text = skill_text(d.name)
            for i, block in enumerate(re.findall(r"```bash\n(.*?)```", text, re.S)):
                r = subprocess.run(["bash", "-n"], input=block,
                                   text=True, capture_output=True)
                if r.returncode:
                    last = r.stderr.strip().splitlines()[-1] if r.stderr else "?"
                    offenders.append(f"{d.name} block {i}: {last}")
        assert not offenders, (
            "bash blocks a shell would reject — an angle-bracket placeholder is "
            "a redirect, use `{braces}` or quote it:\n  " + "\n  ".join(offenders))

    def test_codex_loop_resolves_only_threads_handled_this_round(self):
        """The mutation must join this round's REST IDs to GraphQL threads.

        Querying every unresolved thread closes older bot findings and human
        review threads that Step 4 neither displayed nor answered.
        """
        body = skill_text("codex-loop")
        assert "...| .id" not in body
        assert "HANDLED_COMMENT_IDS" in body
        assert "comments(first:1) { nodes { databaseId path } }" in body
        assert "$handled | index($comment_id)" in body
        assert 'review_threads > "$THREADS_BEFORE"' in body
        assert '| .id\' "$THREADS_BEFORE" > "$THREAD_IDS"' in body
        assert 'resolution_failed=0' in body
        assert 'done < "$THREAD_IDS"' in body
        assert 'review_threads > "$THREADS_AFTER"' in body
        assert 'and ($threads | all(.isResolved))' in body
        assert "leave handled threads open" in body

    def test_codex_loop_binds_a_clean_pass_to_its_reviewed_commit(self):
        """A late clean result for commit A must not certify commit B.

        Timestamp filtering proves only that the response arrived after B's
        trigger. The bot's clean-pass body names the reviewed commit, which is
        the missing association.
        """
        body = skill_text("codex-loop")
        assert "Reviewed commit" in body
        assert 'HEAD_AT_TRIGGER" != "$REVIEWED_COMMIT"*' in body
        assert 'CURRENT_HEAD" != "$HEAD_AT_TRIGGER"' in body

    def test_codex_loop_selects_matching_clean_candidate_before_latest(self):
        """A late stale response must not displace a valid current-head pass.

        Two properties, and pinning only the first cost the second. PREFERRING
        head-bound candidates is what stops a late stale reply from displacing a
        valid pass. Filtering the others AWAY also emptied REVIEWED_COMMIT for a
        stale-only set, which made the `elif` stale branch below unreachable and
        left the empty-marker message as the only thing an operator ever saw —
        and its documented remedy, widening the markup class, is the wrong thing
        to go and do about a marker that parsed perfectly. So: prefer a match,
        fall back to the latest candidate, and let the shell classify it.
        """
        body = skill_text("codex-loop")
        start = body.index("REVIEWED_COMMIT=$(")
        gate = body[start:body.index("CURRENT_HEAD=$(", start)]
        preferred = ('map(select(. as \\$c | \\"$HEAD_AT_TRIGGER\\" '
                     '| startswith(\\$c))) | last')
        assert preferred in gate
        assert gate.index(preferred) < gate.index("// last") < gate.index(
            "| tail -1"), (
            "the latest stale SHA is selected before candidates are head-bound")
        assert "// last // empty" in gate, (
            "a stale-only candidate set left nothing to report as stale")

    def test_codex_loop_collects_only_root_bot_findings(self):
        """Human comments and replies must never enter the handled-ID set."""
        body = skill_text("codex-loop")
        start = body.index("# Inline findings (P1/P2)")
        inline = body[start:body.index("```", start)]
        assert ".user.login == $bot" in inline
        assert ".in_reply_to_id == null" in inline
        assert "root findings from `$CODEX_BOT`" in body

    def test_codex_loop_binds_inline_findings_to_the_triggered_head(self):
        """A late review of A must not become B's exact handled-ID batch."""
        body = skill_text("codex-loop")
        start = body.index("# Inline findings (P1/P2)")
        inline = body[start:body.index("```", start)]
        assert '--arg head "$HEAD_AT_TRIGGER"' in inline
        assert ".original_commit_id == $head" in inline
        assert "original_commit_id" in body
        assert "re-anchored `commit_id`" in body

    def test_codex_loop_persists_state_across_fresh_shells(self):
        """Every executable block must reconstruct the round explicitly."""
        body = skill_text("codex-loop")
        blocks = re.findall(r"```bash\n(.*?)```", body, re.S)
        assert blocks
        for block in blocks:
            assert "codex-loop-state.env" in block
            if 'PR="{pull request number}"' in block:
                assert 'chmod 600 "$STATE_FILE"' in block
            else:
                assert '. "$STATE_FILE"' in block
        for name in ("TRIGGER", "HEAD_AT_TRIGGER", "CURRENT_BATCH_IDS_JSON",
                     "HANDLED_IDS_JSON", "PUBLISHED_SHA", "REVIEW_CLEAN"):
            assert f"{name}=%q" in body
        assert '"$HEAD_AT_TRIGGER" "$TRIGGER" \'[]\' \'[]\' \'\' \'0\'' in body

    def test_codex_loop_requires_an_exact_disposition_for_every_batch_id(self):
        """Every current root finding, and no unrelated thread, must mutate."""
        body = skill_text("codex-loop")
        assert "CURRENT_BATCH_IDS_JSON" in body
        for predicate in (".created_at >= $trigger", ".user.login == $bot",
                          ".in_reply_to_id == null"):
            assert predicate in body
        guard = "handled IDs do not exactly match this review batch"
        assert body.count(guard) >= 4
        assert body.count('(($handled | sort) == ($batch | sort))') >= 4
        push_at = body.index('git push -u origin')
        reply_at = body.index('comments/$COMMENT_ID/replies')
        resolve_at = body.index("resolveReviewThread")
        assert body.rfind(guard, 0, push_at) >= 0
        assert body.rfind(guard, 0, reply_at) >= 0
        assert body.rfind(guard, 0, resolve_at) >= 0

    def test_codex_loop_rechecks_published_head_before_thread_mutations(self):
        """A concurrent push must stop replies and resolution independently."""
        body = skill_text("codex-loop")
        reply_at = body.index('comments/$COMMENT_ID/replies')
        resolve_at = body.index("resolveReviewThread")
        head_query = 'CURRENT_HEAD=$(gh pr view "$PR" --json headRefOid'
        head_guard = '[ "$CURRENT_HEAD" = "$PUBLISHED_SHA" ]'
        assert body.rfind(head_query, 0, reply_at) >= 0
        assert body.rfind(head_guard, 0, reply_at) >= 0
        assert body.rfind(head_query, reply_at, resolve_at) >= reply_at
        assert body.rfind(head_guard, reply_at, resolve_at) >= reply_at

    def test_codex_loop_stops_when_trigger_or_timestamp_fails(self):
        """An empty trigger must not widen polling to historical results."""
        body = skill_text("codex-loop")
        assert 'if ! TRIGGER=$(gh api' in body
        assert '[ -z "$TRIGGER" ]' in body
        assert "review trigger failed or returned no timestamp" in body
        assert 'if ! HEAD_AT_TRIGGER=$(gh pr view' in body
        assert '[ -z "$HEAD_AT_TRIGGER" ]' in body

    def test_codex_loop_refutations_include_the_verified_head_marker(self):
        """Reply verification must accept the documented refutation format."""
        body = skill_text("codex-loop")
        assert "Every reply class must include `$PUBLISHED_PREFIX`" in body
        assert "Reviewed at $PUBLISHED_PREFIX — no code change:" in body
        assert "round has\nonly refutations" in body

    def test_codex_loop_rechecks_head_after_ci_finishes(self):
        """A push during CI polling must invalidate the earlier clean review."""
        body = skill_text("codex-loop")
        ci_query = body.index('gh run list --commit "$SHA"')
        final_head = body.index('FINAL_HEAD=$(gh pr view', ci_query)
        final_guard = body.index('[ "$FINAL_HEAD" != "$HEAD_AT_TRIGGER" ]',
                                 final_head)
        assert 'SHA=$HEAD_AT_TRIGGER' in body
        assert ci_query < final_head < final_guard
        assert "HEAD moved while CI was polled" in body
        assert "clean review and green CI both cover current HEAD" in body

    def test_codex_loop_final_success_requires_clean_review_and_green_ci(self):
        """Green checks cannot certify a missing or stale review response."""
        body = skill_text("codex-loop")
        assert "REVIEW_CLEAN=0" in body
        clean_set = body.index("REVIEW_CLEAN=1")
        clean_branch = body.index("clean pass covers current HEAD")
        final_gate = body.index(
            '[ "$REVIEW_CLEAN" -eq 1 ] && [ "$CI_GREEN" -eq 1 ]')
        final_success = body.index(
            "clean review and green CI both cover current HEAD")
        assert clean_set < clean_branch < final_gate < final_success
        assert "could not fetch CI runs" in body

    def test_codex_loop_requires_consent_for_every_remote_write(self):
        """Automatic skill selection must remain locally read/write only.

        Comments were called out originally, but replies, thread resolution and
        pushes mutate remote state just as surely and need the same consent
        boundary on agents that ignore `disable-model-invocation`.
        """
        body = skill_text("codex-loop").lower()
        assert "before **every remote\nwrite**" in body
        for mutation in ("trigger comments", "review replies",
                         "thread\nresolution", "pushes", "pr metadata"):
            assert mutation in body
        assert "automatic skill selection" in body
        assert "request only to inspect or fix\nfindings do not" in body

    def test_codex_loop_publishes_before_replying_or_resolving(self):
        """Remote thread state must never get ahead of the PR's code state."""
        body = skill_text("codex-loop")
        commit_at = body.index('git commit -m "fix: address review findings"')
        push_at = body.index('git push -u origin')
        verify_at = body.index('[ "$PUBLISHED_SHA" = "$LOCAL_SHA" ]')
        reply_at = body.index('comments/$COMMENT_ID/replies')
        resolve_at = body.index('resolveReviewThread')
        assert commit_at < push_at < verify_at < reply_at < resolve_at
        assert "leave every handled thread open" in body

    def test_codex_loop_verifies_every_reply_before_resolution(self):
        """A failed reply must not leave an unanswered thread marked resolved.

        The verification is per-class, not per-SHA. `contains($sha)` was the
        predicate here, and it is the one that lets the wrong class through: a
        "Fixed in <sha>" reply posted on a REFUTED finding names the same sha and
        passes, after which the thread is resolved on a claim that code changed
        when none did. Requiring the reply to start with that comment's own
        recorded opening pins class and published head together, so keeping
        `contains($sha)` alongside it would be pure redundancy — the opening
        already embeds the sha.
        """
        body = skill_text("codex-loop")
        reply_guard = body.index("reply_failed=0")
        reply_verify = body.index("handled review threads are not all answered")
        resolve_at = body.index("resolveReviewThread")
        assert reply_guard < reply_verify < resolve_at
        assert ">/dev/null || reply_failed=1" in body
        assert 'done\nif [ "$reply_failed" -ne 0 ]' in body
        assert 'select(.in_reply_to_id != null)' in body
        assert ".in_reply_to_id == $root" in body
        assert ".value.opening as $opening" in body
        assert "(.body | startswith($opening))" in body

    def test_codex_loop_composes_one_reply_body_per_finding(self):
        """One body per handled root, looked up by comment id.

        The step used to post a single hardcoded `Fixed in $PUBLISHED_PREFIX —
        {summary}` to every thread in the batch. Two failures in one line: a
        round of pure refutations — PR #23 round 17 was exactly that — announced
        fixes that nothing touched, and the brace placeholder nobody substituted
        became the round's public answer to a real finding.

        The negative assertion is the load-bearing half. Without it the old
        hardcoded form can be reintroduced beside the new machinery and every
        other codex-loop assertion still passes.
        """
        body = skill_text("codex-loop")
        assert '-f body="Fixed in $PUBLISHED_PREFIX' not in body, (
            "a single fix-claim body is being broadcast to the whole batch")
        assert '-f body="$BODY"' in body
        assert 'jq -re --arg id "$COMMENT_ID"' in body, (
            "the body must be looked up per comment, and `-re` is what makes a "
            "missing entry exit non-zero instead of posting the literal `null`")

    def test_codex_loop_reply_class_is_recorded_with_the_finding(self):
        """Step 4 records a disposition per comment, not a bare id list.

        Which finding was fixed and which was refuted is known only while the
        findings are open in front of the agent; recomposing it at reply time is
        recollection. The id list is DERIVED from the plan rather than typed
        beside it, because two hand-maintained copies of the same batch are how
        they drift.
        """
        body = skill_text("codex-loop")
        assert "REPLY_PLAN_JSON" in body
        assert """HANDLED_IDS_JSON=$(jq -c 'map(.id)' <<<"$REPLY_PLAN_JSON")""" in body
        assert '(.class == "fix" or .class == "refute")' in body
        assert 'error("unknown reply class for comment' in body, (
            "an unrecognised class must fail the pipeline; the tempting `else` "
            "branch silently produces the fix wording, which is the direction "
            "that lies to the reviewer")

    def test_codex_loop_refuses_to_publish_an_unsubstituted_placeholder(self):
        """Braces are this repository's placeholder convention, so a reply body
        containing one answers nothing — and it is about to be posted and then
        resolved.

        The guard exists on both sides of the publish boundary on purpose. At
        plan time the findings are still open and a bad record costs a
        re-edit; at post time the same defect is one API call from permanent.
        """
        body = skill_text("codex-loop")
        assert body.count('test("[{}]") | not') >= 2, (
            "the brace guard must run at plan time AND immediately before the "
            "bodies are posted")
        publish_at = body.index("comments/$COMMENT_ID/replies")
        assert body.index('test("[{}]") | not') < publish_at
        assert body.rindex('test("[{}]") | not') < publish_at, (
            "the pre-post validation must precede the posting loop, not follow it")
        assert ("a reply record has no class, no reason, or an unsubstituted "
                "placeholder") in body
        assert ("prepared replies do not match the handled roots, or one is "
                "unsubstituted") in body

    # Names the loop persists to its state file. Every one of them is read back
    # by a LATER block in a fresh shell, which is what makes an unpersisted
    # change to any of them invisible rather than merely local.
    PERSISTED_NAMES = ("TRIGGER", "HEAD_AT_TRIGGER", "CURRENT_BATCH_IDS_JSON",
                       "REPLY_PLAN_JSON", "HANDLED_IDS_JSON", "PUBLISHED_SHA",
                       "REVIEW_CLEAN", "CI_GREEN")

    def test_codex_loop_persists_every_verdict_change(self):
        """A verdict downgraded in shell memory alone is worse than one lost.

        The state file is APPEND-only and last-write-wins, and the block that set
        `REVIEW_CLEAN=1` already appended that line. So a retraction that only
        assigns the variable leaves the earlier affirmative as the last line in
        the file: the next fresh shell sources a clean pass for a HEAD the bot
        never reviewed, and the loop ends there. `CI_GREEN` was never persisted
        at all.

        Written as a general rule rather than as an assertion about the CI block,
        because a targeted version goes green the next time a retraction is added
        somewhere else.
        """
        body = skill_text("codex-loop")
        blocks = re.findall(r"```bash\n(.*?)```", body, re.S)
        assert blocks
        unpersisted = []
        for block in blocks:
            for name in self.PERSISTED_NAMES:
                if not re.search(rf"^\s*{name}=", block, re.M):
                    continue
                if f"{name}=%q" not in block or '"$STATE_FILE"' not in block:
                    unpersisted.append(name)
        assert not unpersisted, (
            f"{sorted(set(unpersisted))} are assigned in a block that never "
            "writes them to the state file — the next shell reads the previous "
            "round's value and calls it this round's")

    def test_codex_loop_resets_every_per_round_name_at_trigger(self):
        """A name left out of the per-round reset is not unset — it is silently
        carried forward.

        `REVIEW_CLEAN` and `CI_GREEN` are the dangerous two: carried forward as
        1 they certify a round before it has produced any evidence at all. The
        reset list is compared against what the INITIALIZER writes rather than
        against a hand-kept list here, so adding a name to the state file
        without adding it to the reset fails.
        """
        body = skill_text("codex-loop")
        # Anchored on the `\n` separator or the opening quote: the format
        # strings are single-quoted shell, so an unanchored `(\w+)=%q` reads the
        # escape's own `n` as the first letter of the name after it.
        initialized = set(re.findall(r"(?:\\n|')(\w+)=%q",
                                     body[:body.index("## The loop")]))
        # Derived once from `gh repo view` and true for the whole loop, so the
        # trigger step has nothing to say about them.
        per_round = initialized - {"PR", "REPO", "OWNER", "NAME", "CODEX_BOT"}
        assert per_round >= set(self.PERSISTED_NAMES)

        # Located by the trigger post itself, not by a name in the format
        # string: the initializer also writes `HEAD_AT_TRIGGER=%q`, and matching
        # that first tests the initializer twice and the reset never.
        trigger_block = [b for b in re.findall(r"```bash\n(.*?)```", body, re.S)
                         if '-f body="@codex review"' in b]
        assert len(trigger_block) == 1, "the trigger step has moved or split"
        reset = "".join(re.findall(r"printf '([^']*)'", trigger_block[0]))
        assert reset, "the trigger step no longer resets any per-round state"
        missing = sorted(n for n in per_round if f"{n}=%q" not in reset)
        assert not missing, (
            f"{missing} survive a new trigger, so this round starts holding the "
            "previous round's batch, replies or verdicts")

    def test_codex_loop_clean_pass_match_is_anchored_but_markup_tolerant(self):
        """The gate that ends the loop, driven rather than grepped.

        `startswith("Codex Review: Didn\'t find any major issues")` was the
        previous form and it is brittle in the direction that HANGS: the bot's
        product label is undocumented, so the documented marker on its own, a
        heading, and a bolded label all miss. A miss leaves REVIEWED_COMMIT
        empty, the loop reports no parseable clean pass, and it re-triggers for
        ever on a PR the bot has already passed.

        Widening to `contains` is the tempting repair and it fails the other
        way, which is why the regex is ANCHORED and why `>` is absent from the
        markup class: a blockquote is how a human quotes the bot, and a
        quotation must never end the loop. Both directions are asserted here on
        the REAL expression lifted out of the skill and run through jq, because
        a text assertion about an anchor cannot tell an anchor that works from
        one that was mis-escaped into matching everything.

        The one substitution made to the extracted expression is the shell's:
        the jq program is embedded in a double-quoted shell string, so the
        backtick in the character class is written `\\`` there and arrives at
        jq as a bare backtick. Both spellings mean a literal backtick to
        Oniguruma; normalising keeps the test honest about what jq actually
        receives.
        """
        import shutil
        import subprocess

        if shutil.which("jq") is None:
            pytest.skip("the clean-pass gate is a jq program")
        body = skill_text("codex-loop")
        assert "select(.body | startswith(" not in body, (
            "the brittle prefix match is back in the selector — it can be "
            "reintroduced BESIDE the regex, where every other assertion here "
            "still passes. The form is still NAMED in the prose that explains "
            "why it went, which is why this is anchored on the selector rather "
            "than on the string.")
        program = re.search(r"select\(\.body \| test\((.*?)\)\)", body, re.S)
        assert program, "the clean-pass matcher has moved or changed shape"
        pattern = "".join(re.findall(r'\\"(.*?)\\"', program.group(1)))
        assert pattern.startswith("^"), (
            "the match is unanchored, so a quotation of the marker in a human "
            "comment ends the loop")
        assert ">" not in pattern, (
            "a blockquote marker in the skipped-prefix class lets a quoted "
            "clean pass certify a commit the bot never passed")
        pattern = pattern.replace("\\`", "`")

        def matches(comment_body):
            r = subprocess.run(
                ["jq", "-n", "--arg", "b", comment_body, "--arg", "re", pattern,
                 "$b | test($re)"], capture_output=True, text=True)
            assert r.returncode == 0, r.stderr
            return r.stdout.strip()

        accepted = {
            "the documented marker alone":
                "Didn\'t find any major issues\n\nReviewed commit: abc1234",
            "a heading and the product label":
                "## Codex Review: Didn\'t find any major issues\n\nReviewed "
                "commit: abc1234",
            "a bolded label":
                "**Codex Review:** Didn\'t find any major issues",
        }
        for label, comment in accepted.items():
            assert matches(comment) == "true", f"{label} was not recognised"

        rejected = {
            "a human quoting the bot in a blockquote":
                "> Codex said Didn\'t find any major issues, but I disagree",
            "the marker mid-sentence":
                "I expect it will say Didn\'t find any major issues shortly",
            "the marker quoted on a later line":
                "My take:\n\nDidn\'t find any major issues was last round",
        }
        for label, comment in rejected.items():
            assert matches(comment) == "false", (
                f"{label} would end the loop on a review that never happened")

    def test_codex_loop_documented_reply_openings_match_the_verifier(self):
        """Two hand-maintained copies of one string, and only one was checked.

        The verifier in references/reply-verification.md refuses to post a body
        whose opening is not exactly one of two literals; the prose in Step 6
        tells the agent what to write. They drifted — the prose said `Fixed in
        $PUBLISHED_PREFIX` with no trailing separator, which the verifier
        rejects — so following the documentation produced a run that always
        failed at the last gate.

        Derived from the jq rather than written out twice more here, because a
        third copy in the test is a third thing to drift. The em dash between
        single spaces is part of the string, not typography.
        """
        reference = (SKILLS_DIR / "codex-loop" / "references"
                     / "reply-verification.md").read_text()
        literals = re.findall(r'then "((?:Fixed|Reviewed) [^"]*)"', reference)
        assert len(literals) == 2, (
            f"the verifier's two sanctioned openings have moved: {literals}")
        prose = (SKILLS_DIR / "codex-loop" / "SKILL.md").read_text()
        for literal in literals:
            documented = literal.replace("\\($sha)", "$PUBLISHED_PREFIX")
            assert documented in prose, (
                f"the verifier accepts only {documented!r} and no step "
                "documents that exact opening, trailing separator included — "
                "an agent following the prose composes a body the last gate "
                "refuses, after the finding has already been resolved in "
                "everything but name")

    def test_codex_loop_bootstrap_guards_every_derived_name(self):
        """`REPO=$(gh repo view …)` was the only unguarded command in the file.

        An unauthenticated `gh` exits non-zero AND prints nothing, so the
        failure has to be caught on both. Unguarded it wrote `REPO=''` into the
        state file, every later block built `repos//…/issues//comments`, and a
        404 read through the Step 3 filters yields no comments and no findings
        — indistinguishable from the clean pass that ends the loop. A gate that
        fails toward "everything is fine" is the one shape a review loop must
        never have.

        The two siblings are here because the same failure survives a
        successful `gh`: `${REPO%/*}` on a slashless string strips nothing and
        leaves OWNER === NAME === REPO, and `PR` is the one value typed by hand
        and carries this repository's `{brace}` placeholder convention.

        Generalized over the names the API paths are BUILT from rather than
        over every `gh` assignment, and the boundary is the point. The file's
        other captures — LOCAL_SHA, PR_HEAD, PUBLISHED_SHA, CURRENT_HEAD,
        FINAL_HEAD — are each compared against an expected value on the very
        next line, so an empty capture produces an INEQUALITY and lands on the
        conservative branch: leave the threads open, retract the verdict,
        re-trigger. Requiring a guard there too would be a rule that fires on
        correct code, and a gate that cries wolf is reasoned past. What
        distinguishes the identity names is that nothing ever compares them —
        they are interpolated straight into a URL, where empty is not an error
        but a well-formed path to nothing.
        """
        body = skill_text("codex-loop")
        assert "if ! REPO=$(gh repo view" in body
        assert '[ -z "$REPO" ]' in body
        assert '[ "$OWNER" = "$REPO" ]' in body
        assert "case $PR in" in body

        interpolated = set()
        for path in re.findall(r"repos/[^\s\"']+", body):
            interpolated.update(re.findall(r"\$\{?(\w+)\}?", path))
        assert {"OWNER", "NAME", "PR"} <= interpolated, (
            f"the API paths stopped naming the identity values: {interpolated}")

        unguarded = []
        for block in re.findall(r"```bash\n(.*?)```", body, re.S):
            for line in block.splitlines():
                m = re.match(r"\s*(?:if ! )?(\w+)=\$\(", line)
                if not m or m.group(1) not in interpolated | {"REPO"}:
                    continue
                if not (line.lstrip().startswith("if ! ")
                        or "|| exit 1" in line or "|| return 1" in line):
                    unguarded.append(line.strip())
        assert not unguarded, (
            "these assignments feed an API path without being checked: "
            f"{unguarded}. An empty value here is not an error downstream; it "
            "is a well-formed path to nothing, whose 404 read through the Step "
            "3 filters is indistinguishable from the clean pass that ends the "
            "loop.")

    def test_state_module_limits_inline_tax_figures_to_governed_exceptions(self):
        """A general fixed-surtax escape hatch silently goes stale after amendment."""
        body = (SKILLS_DIR / "state-module" / "SKILL.md").read_text().lower()
        assert "statutory-fixed structural\nconstant" in body
        assert "mental_health_surcharge" in body
        assert "explicitly enumerated in agents.md §2" in body
        assert "fetch a new\nstate's surtax figures even when" in body
        agents = (REPO / "AGENTS.md").read_text().lower()
        assert "narrow enumerated exception" in agents
        assert "r&tc §17043" in agents
        assert "_source_host_authoritative" in body
        assert "floridarevenue.com" in body

    def test_fl_state_sales_tax_rate_is_fetched_and_consumed_from_cache(self):
        """Florida's general rate is a fetched figure, not an inline constant."""
        fetch = (SKILLS_DIR / "tax-setup" / "references" /
                 "tax-law-fetch.md").read_text()
        compute = (SKILLS_DIR / "tax-compute" / "references" /
                   "state-return.md").read_text()
        assert "state.sales_tax.state_rate" in fetch
        assert '_sources["state.FL"]' in fetch
        assert '_sources["state.FL.county_surtax_rate"]' in fetch
        assert "STATE_RATE from the runtime-fetched" in compute
        assert "compute_sales_tax_deduction(TABLE_AMOUNT, STATE_RATE" in compute
        assert "hardcoded `0.06`" not in compute

    def test_state_module_trigger_requires_explicit_implementation_intent(self):
        """An unsupported-state filing question belongs at the capability gate."""
        desc = _frontmatter(SKILLS_DIR / "state-module" / "SKILL.md")[
            "description"
        ].lower()
        assert "explicitly asked to implement" in desc
        assert "do not use for preparing a taxpayer return" in desc

    def test_state_module_does_not_classify_washington_as_type_none(self):
        """WA capital-gains excise requires a distinct modeled regime."""
        body = (SKILLS_DIR / "state-module" / "SKILL.md").read_text()
        type_none_line = next(
            line for line in body.splitlines() if "A `type: \"none\"` state" in line
        )
        assert "WA" not in type_none_line
        assert "Washington's capital-gains excise" in body

    def test_interview_contents_matches_where_the_groups_actually_live(self):
        """A reference file's own table of contents must not claim a section the
        file does not contain.

        `tax-setup` splits its interview into `references/interview.md`, but
        Group 10 stays in `SKILL.md` on purpose: Step 9.5 runs `/tax-rollover`
        between them, and the rollover pre-fills the prior-year overpayment
        Group 10 asks about. Asking it inside the reference would have the
        taxpayer recall from memory a figure the next step supplies.

        That makes the TOC load-bearing. It listed Group 10 with no note, so an
        agent walking the reference in order hit EOF one group short and the
        interview quietly ended early — the estimated-payment questions never
        asked. Neither the atom-survival audit nor the line-level diff could see
        it: the text was not deleted, only moved, so both reported zero loss
        while the navigation was broken. This is the check that sees it.
        """
        skill_dir = SKILLS_DIR / "tax-setup"
        body = (skill_dir / "SKILL.md").read_text()
        ref = (skill_dir / "references" / "interview.md").read_text()

        group_re = re.compile(r"\*\*Group ([\d.]+) —")
        here = set(group_re.findall(ref))
        there = set(group_re.findall(body))

        toc = re.findall(r"^- Group ([\d.]+) —(.*)$", ref, re.M)
        assert toc, "interview.md lost its Contents block"
        for num, rest in toc:
            assert num in here or num in there, (
                f"interview.md lists Group {num}, which exists in neither "
                "interview.md nor SKILL.md")
            if num not in here:
                assert "SKILL.md" in rest, (
                    f"interview.md lists Group {num} but does not contain it, "
                    "and the entry does not say where it lives — an agent "
                    "reading this file in order ends the interview early")

        # And the reference must not simply run out: the reader has to be sent
        # back for whatever comes after it.
        assert "SKILL.md" in ref[-800:], (
            "interview.md ends without pointing the reader back to SKILL.md; "
            "the last group in the file is not the last group in the interview")

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
            # The WHOLE skill tree, not just SKILL.md: a Claude-only tool named
            # in a references/ file is exactly as unfollowable for a Codex or
            # Kimi user, and scoping this to SKILL.md would have quietly dropped
            # coverage of every line progressive disclosure relocated.
            norm = self._normalize(skill_text(d.name))
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
                     "tax-parse", "tax-fbar", "state-module")

    def test_web_dependent_skills_carry_a_stale_data_degrade(self):
        """A cached or missing web result must stop the run, not silently yield
        prior-year figures — the failure mode with no downstream detector."""
        for name in self.WEB_DEPENDENT:
            # The fetch INSTRUCTIONS may legitimately live in references/ — that
            # is ordinary step detail — so look for them across the whole tree.
            low = skill_text(name).lower()
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
            #
            # And it must be in SKILL.md, NOT a references/ file: the whole body
            # of SKILL.md loads when the skill activates, while a reference is
            # read only if the agent chooses to. A gate the agent can decline to
            # load is a gate that silently over-states completeness, so the
            # freshness STOP is deliberately held to the stricter location while
            # the search list above is not.
            always_loaded = (SKILLS_DIR / name / "SKILL.md").read_text().lower()
            sentences = re.split(r"(?<=[.!?])\s+|\n", always_loaded)
            assert any("paste" in s and "stop" in s for s in sentences), (
                f"{name} tells the agent to search the web but no single sentence "
                "tells it to STOP and ask the user to PASTE current figures; a "
                "stale tax figure produces a wrong return that looks entirely "
                "plausible and that nothing downstream detects")

    def test_parallel_skills_document_a_sequential_fallback(self):
        for name in ("tax-parse", "tax-verify", "tax-parse-crypto"):
            text = skill_text(name).lower()
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
            text = skill_text(name).lower()
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

    def _skill_with_references(self, work: Path):
        """A throwaway clone holding one skill that uses progressive disclosure."""
        import shutil
        import subprocess

        (work / "agents").mkdir(parents=True)
        d = work / ".agents" / "skills" / "tax-setup"
        (d / "references").mkdir(parents=True)
        (d / "SKILL.md").write_text(
            "---\nname: tax-setup\ndescription: x\n---\n"
            "Read [references/interview.md](references/interview.md).\n")
        (d / "references" / "interview.md").write_text("THE INTERVIEW PROCEDURE\n")
        shutil.copy(REPO / "agents" / "install.sh", work / "agents")
        subprocess.run(["git", "init", "-q", "."], cwd=work, check=True)

    def test_reference_files_are_installed_alongside_the_body(self, tmp_path):
        """A skill is a DIRECTORY, and installing only SKILL.md truncates it.

        Under progressive disclosure SKILL.md keeps the gates and links the
        procedure out to references/*.md. Installing the body alone leaves the
        agent a stub that names files it cannot open — for tax-compute, 1588 of
        1698 lines. Nothing errors: the agent reads a well-formed skill, finds no
        procedure at Step 1 and improvises the return. Step 12.7's tie-out gate
        does not catch it either, because it compares parse_ledger.json against
        result.json and correctly-parsed documents tie out against an invented
        computation.

        Read through rather than merely stat-ed, so a symlink that resolves
        nowhere fails the same way a missing file does.
        """
        import subprocess

        for mode in ([], ["--copy"]):
            label = mode[0] if mode else "link"
            work = tmp_path / f"clone-{label}"
            self._skill_with_references(work)
            home = tmp_path / f"home-{label}"
            home.mkdir()
            subprocess.run(["bash", "agents/install.sh", *mode], cwd=work,
                           capture_output=True, text=True,
                           env={**os.environ, "HOME": str(home)})
            ref = (home / ".claude" / "skills" / "tax-setup"
                   / "references" / "interview.md")
            assert ref.is_file(), f"{label} mode installed no references/"
            assert "THE INTERVIEW PROCEDURE" in ref.read_text(), (
                f"{label} mode installed a references/ that cannot be read")

    def test_upgrading_a_file_only_install_gains_the_references(self, tmp_path):
        """The migration case: every existing install predates progressive
        disclosure, so ~/.claude/skills/<name>/ is a real directory holding just
        a SKILL.md. Re-running the installer has to fill in references/ there —
        and must not need the destination torn down by hand first."""
        import subprocess

        work = tmp_path / "clone"
        self._skill_with_references(work)
        home = tmp_path / "home"
        dest = home / ".claude" / "skills" / "tax-setup"
        dest.mkdir(parents=True)
        (dest / "SKILL.md").symlink_to(
            work / ".agents" / "skills" / "tax-setup" / "SKILL.md")

        subprocess.run(["bash", "agents/install.sh"], cwd=work,
                       capture_output=True, text=True,
                       env={**os.environ, "HOME": str(home)})
        assert (dest / "references" / "interview.md").is_file(), (
            "upgrading an install made before progressive disclosure left the "
            "skill truncated, with no error and no prompt to re-install")


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
                      "projections/", "withholding/", "archive/"]

    # File-shaped runtime artifacts. The directory list above cannot cover
    # these, and `generate_tax_pdf.py` is the one the PII gate structurally
    # cannot backstop: that gate matches SSN and credential SHAPES plus
    # sensitive filenames, and a taxpayer's NAME matches neither — a generator
    # that hardcodes one in a section header commits cleanly. Held as a CLASS
    # rather than as the two entries that were missing, because the installer's
    # own comment records this list drifting from the tracked .gitignore once
    # already.
    SENSITIVE_FILES = ["generate_tax_pdf.py", ".planning/HANDOFF.json",
                       "taxpayer_profile.json", "result.json",
                       "parse_ledger.json", "holdings.json",
                       # The rest of AGENTS.md §6's runtime file list, added
                       # after `projection_inputs.json` — YTD wages,
                       # withholding and estimated payments — turned out to be
                       # the one entry `git add -A` staged in a workspace whose
                       # .gitignore the installer had written. It was absent
                       # from here for the same reason it was absent there, so
                       # the test that drives both installer paths against this
                       # list could not see it. A list that holds six of a
                       # class is a list that will be short again.
                       "answers.md", "projection_inputs.json",
                       "crypto_transactions.json", "FBAR_2026.md",
                       "tax_calendar_2026.ics", "eval_run.json",
                       # Agent runtime state under `.claude/`. `.archive-files`
                       # is a NUL-separated list of every path beneath archive/
                       # — a taxpayer's scanned documents, named after what they
                       # are — and the PII gate cannot recognise a pathname by
                       # shape. `settings.local.json` accumulates per-machine
                       # allow rules and absolute paths, and hookify's
                       # `*.local.md` rules are generated from conversation
                       # analysis, so they quote the work in progress.
                       ".claude/settings.local.json",
                       ".claude/hookify.block-git-push.local.md",
                       ".claude/.perms-stamp",
                       ".claude/.skill-invariants-stamp",
                       ".claude/.archive-dirs",
                       ".claude/.archive-files",
                       # Holds nothing, but its PRESENCE is the signal that the
                       # manifest pair above is mid-publish. Committing it would
                       # make every fresh clone rebuild both indexes once.
                       ".claude/.archive-index-dirty",
                       # The mktemp siblings, and the entries that matter most.
                       # None of these four files is written in place: each is
                       # built as `<name>.XXXXXX` and moved over the real name,
                       # so a hook killed by its timeout mid-refresh leaves the
                       # sibling holding exactly what the finished file would
                       # have held. The bare names above were already ignored
                       # before this rule existed; the leftovers were not.
                       ".claude/.perms-stamp.Qw7rTt",
                       ".claude/.skill-invariants-stamp.K1k1k1",
                       ".claude/.archive-dirs.Ab3xZ9",
                       ".claude/.archive-files.Ab3xZ9"]

    # The counterweight to the entries above, and it is not optional: git never
    # descends into an excluded directory, so a single `.claude/` line would
    # make all four of these uncommittable and NO negation could undo it.
    COMMITTABLE_CLAUDE_PATHS = [".claude/settings.json",
                                ".claude/skills/tax-setup",
                                ".claude/hooks/harden-runtime-perms.sh",
                                ".claude/agents/bias-direction-reviewer.md"]

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

    def _unignored_files(self, gitignore_text: str, tmp: Path) -> list:
        """Which SENSITIVE_FILES git does not ignore, given this .gitignore."""
        import subprocess

        probe_repo = tmp / "fileprobe"
        probe_repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "."], cwd=probe_repo, check=True)
        (probe_repo / ".gitignore").write_text(gitignore_text)
        unignored = []
        for name in self.SENSITIVE_FILES:
            probe = probe_repo / name
            probe.parent.mkdir(parents=True, exist_ok=True)
            probe.write_text("x")
            if subprocess.run(["git", "check-ignore", "-q", name],
                              cwd=probe_repo).returncode != 0:
                unignored.append(name)
        return unignored

    def test_tracked_gitignore_covers_the_sensitive_runtime_files(self, tmp_path):
        """The repo's own .gitignore, not the installer's copy of it."""
        missing = self._unignored_files(
            (REPO / ".gitignore").read_text(), tmp_path / "tracked")
        assert not missing, f".gitignore does NOT ignore: {missing}"

    def test_installer_also_covers_the_sensitive_runtime_files(self, tmp_path):
        """IGNORE_PATTERNS must not drift from the tracked .gitignore.

        Both installer paths are checked because the append path is the one
        that upgrades a workspace whose .gitignore predates a new entry — the
        exact situation that leaves an existing generator unprotected. The
        directory-level tests above pass while these fail, which is how the
        gap stayed invisible: the two new entries are file-shaped.
        """
        for label, seed in (("create", None), ("append", "# mine\n*.log\n")):
            produced = self._run_installer(tmp_path / label, seed_gitignore=seed)
            missing = self._unignored_files(produced, tmp_path / f"{label}_p")
            assert not missing, f"{label} path leaves these unignored: {missing}"

    def _ignored_paths(self, gitignore_text: str, tmp: Path, names) -> set:
        """Which of `names` git ignores under this .gitignore.

        `git check-ignore` answers about a PATHNAME, so nothing has to exist —
        which is what lets one probe repo cover a mktemp leftover that only
        exists after a crash and a directory that must stay committable.
        """
        import subprocess

        probe_repo = tmp / "claudeprobe"
        probe_repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "."], cwd=probe_repo, check=True)
        (probe_repo / ".gitignore").write_text(gitignore_text)
        return {name for name in names
                if subprocess.run(["git", "check-ignore", "-q", name],
                                  cwd=probe_repo).returncode == 0}

    def test_claude_directory_config_stays_committable(self, tmp_path):
        """`.claude/` is a directory people `git add`, and that is exactly what
        makes the artifact rules load-bearing rather than a directory rule.

        settings.json carries the project's PostToolUse hooks, skills/ holds the
        tracked relative symlinks Claude Code discovers through, and hooks/ and
        agents/ are the scripts and reviewers themselves. Simplifying the six
        artifact rules into one `.claude/` line passes every other assertion in
        this class while making the repository's own configuration
        uncommittable in every workspace the installer touches.

        Inherently one-directional, and worth saying so: this passes unchanged
        against a .gitignore carrying no `.claude/` rules at all, because
        over-blocking is the only failure it can observe. The sensitive-file
        tests above are what pin the rules' existence; this pins their scope.
        """
        for label, text in (("tracked", (REPO / ".gitignore").read_text()),
                            ("create", self._run_installer(tmp_path / "cfg_c")),
                            ("append", self._run_installer(
                                tmp_path / "cfg_a", seed_gitignore="# mine\n"))):
            ignored = self._ignored_paths(text, tmp_path / f"{label}_cfg",
                                          self.COMMITTABLE_CLAUDE_PATHS)
            assert not ignored, (
                f"the {label} .gitignore makes {sorted(ignored)} uncommittable")

    def test_the_installer_and_the_tracked_gitignore_agree_on_claude_state(self):
        """Both copies of the same rule set, compared as SETS rather than
        spot-checked.

        The tracked .gitignore protects this clone; IGNORE_PATTERNS protects a
        taxpayer workspace that has no .gitignore yet, or one predating an
        entry. AGENTS.md §1 is explicit about what hand-mirrored rules cost
        here — four bypasses once shipped to two copies at once — and the
        installer's own comment records this list drifting before.

        Equality, not containment, because both directions are real failures: a
        rule only in the installer leaves this repository staging its own hook
        state, and a rule only here leaves every installed workspace staging
        the archive path index.
        """
        gitignore, installer = self._ignore_rule_sets()
        gitignore = {p for p in gitignore if p.startswith(".claude/")}
        installer = {p for p in installer if p.startswith(".claude/")}
        assert gitignore == installer, (
            f"only in .gitignore: {sorted(gitignore - installer)}; "
            f"only in the installer: {sorted(installer - gitignore)}")

    @staticmethod
    def _ignore_rule_sets():
        """The tracked .gitignore's rules and the installer's, as two sets.

        Comments are stripped from BOTH sides before the quoted names are
        pulled out of the array. Without that step a double-quoted filename
        inside one of the array's explanatory comments becomes a phantom
        installer-only entry — the block already quotes `.claude/settings.json`
        and `.perms-stamp.XXXXXX` in backticks, which is harmless only until
        someone reaches for double quotes.
        """
        gitignore = {ln.strip()
                     for ln in (REPO / ".gitignore").read_text().splitlines()
                     if ln.strip() and not ln.strip().startswith("#")}
        array = re.search(r"IGNORE_PATTERNS=\((.*?)\n\)",
                          (REPO / "agents" / "install.sh").read_text(), re.S)
        assert array, "IGNORE_PATTERNS has moved — the installer writes what?"
        body = "\n".join(ln for ln in array.group(1).splitlines()
                         if not ln.strip().startswith("#"))
        return gitignore, set(re.findall(r'"([^"]+)"', body))

    def test_the_installer_and_the_tracked_gitignore_agree_on_every_rule(self):
        """The same equality over the WHOLE list, which is where it was needed.

        Scoping the comparison to `.claude/` left every other rule unchecked,
        and that is precisely where the drift was: nine entries lived in the
        tracked .gitignore and in neither installer path —
        `projection_inputs.json`, the `.env` variants, `*.bak`, `*.tmp`,
        `Thumbs.db` and the IDE pair. Only one direction was ever
        self-correcting, which is why nothing surfaced it: the installer's
        WORKSPACE_ROOT is the clone root, so running it here APPENDS whatever
        the array has that .gitignore lacks. Nothing at all pushes an entry the
        other way, so that direction drifted silently for as long as it liked.
        """
        gitignore, installer = self._ignore_rule_sets()
        assert gitignore == installer, (
            f"only in .gitignore (an installed workspace stages it): "
            f"{sorted(gitignore - installer)}; "
            f"only in the installer (this clone stages it): "
            f"{sorted(installer - gitignore)}")

    def test_every_negation_follows_the_rule_it_reopens(self):
        """git honours a negation only where it comes AFTER its exclusion, and
        never at all where the exclusion is a directory.

        The create path writes IGNORE_PATTERNS in array order, so the order in
        that array is the order in the file. Both negated paths are files the
        repository must keep committable — the tracked VS Code settings that
        point Copilot at .agents/skills, and the placeholder that makes an
        empty documents/ survive a clone — and a reordering that looks purely
        cosmetic silently makes both uncommittable in every workspace the
        installer has ever touched.
        """
        gitignore_lines = [ln.strip()
                           for ln in (REPO / ".gitignore").read_text().splitlines()
                           if ln.strip() and not ln.strip().startswith("#")]
        array = re.search(r"IGNORE_PATTERNS=\((.*?)\n\)",
                          (REPO / "agents" / "install.sh").read_text(), re.S)
        body = "\n".join(ln for ln in array.group(1).splitlines()
                         if not ln.strip().startswith("#"))
        installer_order = re.findall(r'"([^"]+)"', body)
        for label, order in (("tracked .gitignore", gitignore_lines),
                             ("IGNORE_PATTERNS", installer_order)):
            for negation, exclusion in (("!.vscode/settings.json", ".vscode/*"),
                                        ("!documents/.gitkeep", "documents/*")):
                assert negation in order and exclusion in order, (
                    f"{label} lost {negation} or {exclusion}")
                assert order.index(negation) > order.index(exclusion), (
                    f"{label} places {negation} before {exclusion}, so git "
                    "never reaches the negation and the file stays ignored")

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

    def test_a_dangling_link_is_removed_whatever_the_skill_is_named(self, tmp_path):
        """Ownership is proven by where the link resolves, not by the name.

        The scan used to glob `tax-*`, which was exactly co-extensive with the
        skill set at the time. `.agents/skills/` now also holds contributor
        skills, so a name-based glob would skip them while the installer still
        printed its clean summary — the shape of silent gap this file exists to
        catch. A non-`tax-` name is the case that distinguishes the two rules.
        """
        import shutil
        import subprocess

        work = tmp_path / "clone"
        (work / "agents").mkdir(parents=True)
        for name in ("tax-setup", "codex-loop"):
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
        assert (home / ".claude" / "skills" / "codex-loop" / "SKILL.md").is_symlink()

        shutil.rmtree(work / ".agents" / "skills" / "codex-loop")
        subprocess.run(["bash", "agents/install.sh"], cwd=work,
                       capture_output=True, text=True, env=env)
        assert not (home / ".claude" / "skills" / "codex-loop").exists(), (
            "a dangling link into this clone is ours by construction whatever "
            "it is called, and the agent lists it as a skill it cannot read")

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

    **A cap alone is the wrong control, and Y5 is what that cost.** The previous
    one sat 10 bytes above the file, so a five-line addition for an unrelated
    change had to evict three passages of live content — and which safeguard died
    was decided by whoever was editing, at the end of a task about something else.
    A cap can only force a choice; what makes the choice safe is the rest of this
    class: a stated criterion for what may leave (AGENTS.md §9 — the instruction
    files carry the ALARMS, `docs/contributing/` carries the ANSWERS), a complete
    index so a relocated file stays reachable, and a check that every pointer
    resolves. Relocating has to be the CHEAP move or trimming will keep winning.
    """

    CODEX_CHAIN_LIMIT = 32 * 1024
    AGENTS_MAX_BYTES = 26 * 1024
    CLAUDE_MAX_BYTES = 8 * 1024

    def test_the_agents_cap_is_pinned_at_its_value(self):
        """A RATCHET, and the reason it is a bare equality.

        The first version of this test asserted the cap left an eighth of Codex's
        32 KiB unspent, which reads like a principle and permits a cap of 28 KiB —
        the exact pre-Y5 cap whose ten bytes of headroom this class exists to
        describe. An inequality that sanctions the status quo ante is worse than
        no test: it reads as a guard while licensing the thing it names.

        So the number itself is pinned. The reserve is what CHOSE it — Codex's
        limit covers the whole instruction CHAIN, and a nested AGENTS.md plus the
        user's own global instructions land in the same budget, so a cap set AT
        the limit is spent by content nobody in this repository wrote. Lowering it
        needs no argument. RAISING it is the move this class refuses, and doing so
        means editing this line and saying why in the commit message.
        """
        assert self.AGENTS_MAX_BYTES == 26 * 1024, (
            "the AGENTS.md cap moved. It reserves "
            f"{self.CODEX_CHAIN_LIMIT - self.AGENTS_MAX_BYTES} bytes of Codex's "
            f"{self.CODEX_CHAIN_LIMIT}-byte chain limit for the rest of the chain; "
            "raising it to fit new content is what AGENTS.md §9's criterion exists "
            "to replace")

    @staticmethod
    def _section_weights(text):
        """Bytes per `## ` section, heaviest first — the failure message's payload."""
        weights, name = [], None
        for line in text.splitlines(keepends=True):
            if line.startswith("## "):
                weights.append([line.strip(), 0])
                name = weights[-1]
            if name:
                name[1] += len(line.encode())
        return sorted(weights, key=lambda row: -row[1])

    def test_agents_md_exists_and_under_size_cap(self):
        agents = REPO / "AGENTS.md"
        assert agents.is_file()
        text = agents.read_text()
        size = agents.stat().st_size
        # The message carries the section weights because of WHEN it fires: at
        # the end of an unrelated task, to someone who now has to choose. Naming
        # the criterion without the candidates leaves the cheapest move —
        # deleting whatever they are looking at — still the most available one.
        heaviest = "\n  ".join(f"{n:>6} bytes  {name}"
                                for name, n in self._section_weights(text)[:3])
        assert size <= self.AGENTS_MAX_BYTES, (
            f"AGENTS.md is {size} bytes, over the {self.AGENTS_MAX_BYTES} cap — "
            "Codex silently truncates the combined instruction chain at 32 KiB, so "
            "content past the cap is invisible with no error.\n\nDo NOT trim a rule "
            "to fit and do NOT raise the cap. Apply §9's criterion — a line stays "
            "only if an agent who never looks it up would otherwise do the wrong "
            "thing — and move the ANSWERS into docs/contributing/, linked from §9 "
            f"and from the section they left. Heaviest sections:\n  {heaviest}")

    def test_claude_md_is_thin_in_bytes_as_well_as_lines(self):
        """The line cap alone is gameable, and by the one edit most likely to try.

        Every line here is wrapped prose, so a section can be re-flowed onto
        fewer, longer lines and shed no weight at all while the line count drops.
        Bytes are what the session actually pays for.
        """
        size = (REPO / "CLAUDE.md").stat().st_size
        assert size <= self.CLAUDE_MAX_BYTES, (
            f"CLAUDE.md is {size} bytes, over the {self.CLAUDE_MAX_BYTES} cap. It "
            "loads on top of AGENTS.md in every session; hook and subagent depth "
            "belongs in docs/contributing/claude-automation.md")

    def test_claude_md_imports_agents_md_as_first_content_line(self):
        lines = [ln.strip() for ln in (REPO / "CLAUDE.md").read_text().splitlines()]
        content = [ln for ln in lines if ln and not ln.startswith("#")]
        assert content and content[0] == "@AGENTS.md", (
            "CLAUDE.md's first content line must be the @AGENTS.md import so the "
            "shared project truth loads before anything Claude-specific")

    def test_claude_md_is_thin(self):
        # Kept beside the byte cap rather than replaced by it. At this file's
        # wrap the byte cap always fires first, so what this one still catches is
        # the opposite shape: many short lines — a long bulleted list, or prose
        # broken one clause per line — which is cheap in bytes and expensive to
        # read, and which Copilot CLI truncates on regardless.
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

    # The five that existed before Y5 added an index rule. Kept as a FLOOR under
    # the derived roster below: a regex that stops matching reports a clean pass,
    # and this is what makes that visible.
    RELOCATED_BEFORE_Y5 = ("docs/ops/review-subagent-acceptance.md",
                           "docs/contributing/skill-catalog.md",
                           "docs/contributing/test-invariants.md",
                           "docs/contributing/tax-gotchas.md",
                           "docs/contributing/agents-compat.md",
                           "docs/contributing/state-module-playbook.md")

    # The trailing boundary is load-bearing: without it `pii-gate.md.bak`
    # matches as `pii-gate.md`, and a pointer renamed to a target that does
    # not exist passes every check in this class.
    POINTER = re.compile(r"docs/(?:contributing|ops)/[a-z0-9._-]+\.md(?![\w.-])")

    @classmethod
    def _names(cls, text):
        """The doc paths `text` points at, as whole tokens.

        A bare `in` test cannot do this job: `docs/contributing/pii-gate.md` is a
        SUBSTRING of `docs/contributing/pii-gate.md.bak`, so renaming every
        pointer to a target that does not exist passed the index check, the
        backlink check and the pointer sweep at once — the sweep because its own
        boundary made the broken pointer invisible, and the other two because
        they matched the prefix.
        """
        return set(cls.POINTER.findall(text))

    def _instruction_text(self):
        return "\n".join((REPO / n).read_text() for n in ("AGENTS.md", "CLAUDE.md"))

    def test_every_relocation_pointer_resolves(self):
        """A pointer to a file that is not there is worse than no pointer: the
        agent reads a rule saying the depth exists, goes looking, and concludes
        the rule was the whole of it."""
        found = set(self.POINTER.findall(self._instruction_text()))
        assert found >= set(self.RELOCATED_BEFORE_Y5), (
            "the pointer sweep no longer sees docs it has always named — the "
            "regex stopped matching, not the pointers stopped existing:\n  "
            + "\n  ".join(sorted(set(self.RELOCATED_BEFORE_Y5) - found)))
        missing = sorted(rel for rel in found if not (REPO / rel).is_file())
        assert not missing, (
            "the instruction files point at these, and they are not there:\n  "
            + "\n  ".join(missing))

    def test_the_contributing_index_is_complete(self):
        """The half of a relocation that is easy to lose is REACHABILITY.

        Moving the text out is one edit and linking it is another, and only the
        first one is visible in the diff you were reading. A relocated file
        nothing indexes is not moved, it is deleted with extra steps — so §9
        indexes `docs/contributing/` in FULL and the directory listing, not a
        hand-kept list, is what this test iterates.
        """
        agents_text = (REPO / "AGENTS.md").read_text()
        _, sep, index = agents_text.partition("## 9. Where the deep documentation lives")
        assert sep, "AGENTS.md §9 has been renamed; this test reads it by heading"
        # Scoped to §9 on purpose. Searching the whole file would pass on a doc
        # that some OTHER section happens to link — which is how the first
        # version of this test read a deleted index row as present.
        # ROWS, not the whole tail: §9 is currently last, so a partition at its
        # heading would count an HTML comment — or a future §10 — as the index.
        rows = "\n".join(ln for ln in index.splitlines() if ln.startswith("|"))
        # Spelled as whole literals rather than joined from parts: the hook's
        # watched-set test locates the paths a module reads by scanning for
        # them, and a path joined one segment at a time reads as a bare top
        # directory, which is not a watched root. Both of these are.
        indexed = self._names(rows)
        unindexed = [f"{sub}/{path.name}"
                     for sub in ("docs/contributing", "docs/ops")
                     for path in sorted((REPO / sub).glob("*.md"))
                     if f"{sub}/{path.name}" not in indexed]
        assert not unindexed, (
            "these exist and AGENTS.md §9 never names them, so an agent that does "
            "not already know which section they belong to cannot find them:\n  "
            + "\n  ".join(unindexed))

    # Every doc Y5 relocated INTO, and the instruction file the content left.
    # Declared rather than derived from the provenance lines: `skill-catalog.md`
    # also opens "Relocated ... from CLAUDE.md", from a much older move, and a
    # backlink from CLAUDE.md would be WRONG — that content is agent-neutral and
    # belongs to AGENTS.md, which links it. Provenance records where text came
    # from; it does not say a live section still owes it a pointer.
    PROVENANCE = re.compile(r"[Rr]elocated out of `(AGENTS\.md|CLAUDE\.md)`")

    def test_each_relocation_is_linked_from_the_file_it_left(self):
        """§9's index makes a file reachable from AGENTS.md. That is not the same
        as reachable from the SECTION it left: an agent editing a hook reads the
        Hooks section, not the documentation index.

        So the backlink is required OUTSIDE §9 — passing on the index row alone
        was the first version of this test, and removing §1's own `pii-gate.md`
        pointer left it green.

        The roster is DERIVED from the provenance lines rather than hand-kept,
        for the reason the index test iterates the directory: a relocation nobody
        adds to a list is exactly the one that loses its backlink. `Relocated out
        of` is the phrase that carries it; `skill-catalog.md` opens "Relocated
        verbatim FROM CLAUDE.md", a much older move whose content is now
        agent-neutral and owed a backlink from AGENTS.md, which has one.
        """
        relocations = {}
        for path in sorted((REPO / "docs" / "contributing").glob("*.md")):
            found = self.PROVENANCE.search(path.read_text())
            if found:
                relocations[f"docs/contributing/{path.name}"] = found.group(1)
        assert len(relocations) >= 3, (
            "the provenance sweep found only "
            f"{sorted(relocations)} — the phrase it reads has been reworded, and "
            "a derived roster that stops matching reports a clean pass over "
            "nothing")

        for rel, source in relocations.items():
            body = (REPO / source).read_text()
            outside_index = body.partition("## 9. Where the deep documentation lives")[0]
            assert rel in self._names(outside_index), (
                f"{rel} holds depth cut out of {source}, and the only pointer to "
                f"it in {source} is the §9 index row — an agent reads the section "
                "it edits, not the documentation index")

    RESTATEMENT_WORDS = 12

    @staticmethod
    def _shingles(text, n):
        words = " ".join(re.sub(r"[`*_#|>-]", " ", text).split()).lower().split()
        return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}

    def test_a_relocation_target_does_not_restate_what_the_file_kept(self):
        """AGENTS.md §9 asserts nothing below it is duplicated above. Assert it.

        The first version of this change made that claim and broke it in nine
        places — 18% of CLAUDE.md was verbatim in the file it had just relocated
        into, including the whole reviewer-grant sentence. Every one of those is a
        pair that can now disagree, in a change set whose entire argument is that
        a second copy is how disagreement starts. The claim was prose, so nothing
        caught it.

        A run of twelve consecutive words is the threshold: shared identifiers,
        paths and short technical phrases stay legal, a restated RULE does not.
        The fix when this fires is to say why the rule is what it is, not to say
        the rule again — the instruction file already did that, once.
        """
        offenders = []
        for path in sorted((REPO / "docs" / "contributing").glob("*.md")):
            rel = f"docs/contributing/{path.name}"
            body = path.read_text()
            found = self.PROVENANCE.search(body)
            if not found:
                continue
            shared = (self._shingles(body, self.RESTATEMENT_WORDS)
                      & self._shingles((REPO / found.group(1)).read_text(),
                                       self.RESTATEMENT_WORDS))
            offenders += [f"{rel} restates {found.group(1)}: ...{run}..."
                          for run in sorted(shared)]
        assert not offenders, (
            f"{len(offenders)} shared runs of {self.RESTATEMENT_WORDS}+ words "
            "between an instruction file and the doc it relocated into:\n  "
            + "\n  ".join(offenders[:10]))

    # Rules that may never leave the always-loaded files, whatever the cap says.
    #
    # This is the counterweight to every OTHER test in this class, and without it
    # they are actively harmful: a byte cap makes DELETING A RULE the cheapest way
    # to go green. `test_security_rules_are_front_loaded` does not cover it — it
    # asks for the words "ssn", "chmod 600" and "never" somewhere in the first
    # hundred lines, which survives deleting the full-SSN prohibition outright,
    # because §1 says all three of those words in half a dozen other places.
    #
    # Deliberately SHORT. Every entry is a rule whose loss produces a wrong return
    # or a disclosure, not merely content worth keeping; the criterion in §9 is
    # what governs everything else, and a pin on ordinary prose would make the
    # file unable to be edited at all.
    LOAD_BEARING = (
        ("AGENTS.md", "NEVER store or emit a full SSN"),
        ("AGENTS.md", "Never run a real return on a vendor cloud VM"),
        ("AGENTS.md", "Never paste taxpayer data into a GitHub issue"),
        ("AGENTS.md", "Never hardcode a rate, bracket, cap, or threshold"),
        ("AGENTS.md", "you cannot confirm the figure is current: STOP"),
        ("AGENTS.md", "is the single PII gate"),
        ("AGENTS.md", "OVER-states the number the user acts on is a hard error"),
        ("AGENTS.md", "Never credit an AI assistant as an author"),
        # §9's criterion. This class's docstring credits it as the control that
        # makes the cap safe, and deleting it used to leave every test green.
        ("AGENTS.md", "This file carries the ALARMS; these carry the ANSWERS"),
        ("CLAUDE.md", "Never run `git push` yourself"),
        ("CLAUDE.md", "**or `Bash`**"),
    )

    def test_the_cap_cannot_be_met_by_deleting_a_rule(self):
        """Every check above rewards a SMALLER file. This one does not."""
        # Whitespace-normalised on both sides: these files are wrapped prose, and
        # a pin that a re-flow can break is a pin that gets deleted rather than
        # fixed.
        bodies = {name: " ".join((REPO / name).read_text().split())
                  for name in {n for n, _ in self.LOAD_BEARING}}
        missing = [f"{name}: {rule!r}" for name, rule in self.LOAD_BEARING
                   if " ".join(rule.split()) not in bodies[name]]
        assert not missing, (
            "these rules are gone from the file that must state them:\n  "
            + "\n  ".join(missing)
            + "\n\nIf one was reworded rather than removed, update this list in "
            "the same commit. If one was RELOCATED, put it back: these are the "
            "rules an agent breaks without ever thinking to look them up, which "
            "is the whole of what an instruction file is for.")


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


class TestSelfTestClaim:
    """`AGENTS.md` §5 and `CONTRIBUTING.md` both tell a contributor which
    `agents/lib/` modules run a self-test when invoked with no arguments.

    Both used to say EVERY module does. Four of twenty-one do; the other
    seventeen argparse-error or print usage. A contributor following that
    sentence runs a module, gets `error: the following arguments are required`,
    and has no way to tell a broken module from a wrong instruction — so the
    list is named in the docs and checked here rather than asserted.

    Only the harmful direction is machine-checked: a module the docs CLAIM
    self-tests must actually do it. The reverse — a module quietly GAINING one,
    leaving the list short — is not, and that is a deliberate trade rather than
    an oversight. The reason for declining it USED to be cost: catching it means
    globbing `agents/lib/`, which would make every edit to any of the modules
    there select this whole suite in the PostToolUse hook. That argument no
    longer holds — the engine-preflight work added an `agents/lib/*.py` arm to
    `select_modules_for` that sets RUN_PORTABILITY for exactly that trigger, so
    the cost is already paid and the reverse check is now free to add. It is
    left undone deliberately rather than reasoned away: the list is short and
    its failure direction is harmless, but do not re-derive the old trade from
    this docstring, because its premise is gone.
    """

    SELF_TESTING = ("tax_computations", "secure_fs", "irs_dates", "crypto_importer")

    def test_the_named_modules_really_do_self_test(self):
        import subprocess
        import sys

        for name in self.SELF_TESTING:
            # `-m`, not a script path. `tax_computations` is a PACKAGE since
            # W2, so `agents/lib/tax_computations.py` names a file that is now
            # a directory — and the module form is what AGENTS.md §8 tells a
            # reader to run, so it is what this should have been checking all
            # along.
            result = subprocess.run(
                [sys.executable, "-m", f"agents.lib.{name}"],
                cwd=str(REPO), capture_output=True, text=True, timeout=120)
            assert result.returncode == 0, (
                f"agents.lib.{name} is documented as self-testing when run "
                f"with no arguments, and it exited {result.returncode}: "
                f"{result.stderr[-300:]}")

    def test_both_documents_name_the_same_modules(self):
        agents = (REPO / "AGENTS.md").read_text()
        contributing = (REPO / "CONTRIBUTING.md").read_text()
        for name in self.SELF_TESTING:
            assert f"`{name}`" in agents, f"AGENTS.md §5 does not name {name}"
            assert f"`{name}`" in contributing, f"CONTRIBUTING.md does not name {name}"


class TestTheAdvisorySkillRestatesTheEnginesLawExactly:
    """`tax-safe-harbor` states §6654's parameters in prose, having no engine.

    That is the deal the skill is built on: it runs where `agents/lib/` is not
    present, so the one rule it needs is restated in its body rather than
    computed. Which makes it the only place in the toolkit where a tax figure
    exists in TWO forms — a constant the engine applies and a sentence a reader
    acts on — and nothing structural keeps them equal. An engine constant
    changed on a statutory amendment leaves the skill quoting the old number to
    a taxpayer who then pays the old amount, and the arithmetic still looks
    self-consistent, because it is: it is consistently wrong.

    So the parity is asserted here, and asserted by DERIVING the expected
    strings from the constants rather than by spelling them out twice more.
    A test that hardcoded "$150,000" would be a third copy with the same drift
    problem, green while both it and the skill disagreed with the engine.

    Lives in this module, not in `test_projection.py`, for a hook reason: the
    tool-time selection map runs portability on BOTH an `agents/lib/*.py` edit
    and a `.agents/skills/*` edit, so drift introduced from either side is
    caught locally. Keyed on `test_projection.py` it would be selected by
    neither.
    """

    SKILL = "tax-safe-harbor"

    def _text(self) -> str:
        """Whitespace-collapsed, because the prose WRAPS.

        `30% / 40% / 0% / 30%` spans a line break in the body, so a raw
        substring search for it fails on correct text — the kind of false red
        that gets an assertion deleted rather than fixed.
        """
        return re.sub(r"\s+", " ", skill_text(self.SKILL))

    @staticmethod
    def _states(text: str, value) -> bool:
        """Is `$value` stated as its own figure, not as a prefix of a bigger one?

        `"$500" in text` is TRUE of a document that only ever says `$500,000`,
        and this class asserts both — so the CA de-minimis bullet could be
        deleted outright while the §19136.3 bullet kept its assertion green.
        That is the substring vacuity this suite already records for sha256
        digests versus nine-digit SSNs, arriving here as a money figure. Bound
        the match on the right: no digit, comma or decimal point may follow.
        """
        return re.search(rf"\${value:,}(?![\d,.])", text) is not None

    def test_the_prior_year_agi_thresholds_match_the_engine(self):
        text = self._text()
        # Read off tax_computations, which OWNS these: `prior_year_prong`
        # there is the one place that applies them, and the de-minimis test
        # below has always reached for them here.
        TC = _tax_computations()
        for value in (TC.PRIOR_YEAR_AGI_THRESHOLD,
                      TC.PRIOR_YEAR_AGI_THRESHOLD_MFS):
            assert self._states(text, value), (
                f"{self.SKILL} does not state ${value:,}, the prior-year AGI "
                f"threshold the engine applies. The skill restates this rule "
                f"instead of computing it, so a change here must be made twice.")

    def test_the_self_employment_filing_trigger_matches_the_engine(self):
        """The §6017 $400 the no-return-year gate states without applying.

        "Did not have to file" is the WHOLE Form 1040 "Who Must File" test, and
        the line of it this skill's own population trips is $400 of net
        self-employment earnings: someone paying estimated tax usually has
        income nobody withholds on, which is the same income that requires the
        return. It fails BOTH of Pub 505's routes to "no tax liability" at once
        — the return was required, and the $400 carried SE tax — so an agent
        that reads the income threshold as the whole test clears a penalty that
        is attaching. Understating what must be paid is the one direction this
        skill promises cannot happen, which is what makes the figure a gate.

        Pinned to `SE_TAX_THRESHOLD` because the engine already owns this
        number: §6017's filing trigger and §1402(b)'s de-minimis floor are the
        same statutory $400, and two unlinked copies of one figure is the drift
        this class exists to stop.
        """
        text = self._text()
        TC = _tax_computations()
        assert self._states(text, TC.SE_TAX_THRESHOLD), (
            f"{self.SKILL} does not state ${TC.SE_TAX_THRESHOLD:,}, the §6017 "
            f"net-self-employment earnings that require a return. Without it "
            f"the no-filing-requirement route reads as the income threshold "
            f"alone, and a taxpayer who owed SE tax is told no penalty can "
            f"attach this year at all")

        # And in the BODY, not only in the reference it links. `skill_text`
        # reads the whole directory, which is right for step detail and wrong
        # for this: the figure is a GATE, and an agent that never opens the
        # link still has to know the income threshold is not the whole test.
        # Reverting the body alone left the assertion above green on the
        # reference's copy, which is the mutation that found this.
        body = re.sub(r"\s+", " ",
                      (SKILLS_DIR / self.SKILL / "SKILL.md").read_text())
        assert self._states(body, TC.SE_TAX_THRESHOLD), (
            f"${TC.SE_TAX_THRESHOLD:,} appears only in a references/ file, so "
            f"SKILL.md alone reads as a complete gate that is missing its "
            f"trigger")

    def test_the_prior_year_factors_match_the_engine(self):
        text = self._text()
        TC = _tax_computations()
        for value in (TC.PRIOR_YEAR_FACTOR_HIGH, TC.PRIOR_YEAR_FACTOR):
            assert f"{value:.0%}" in text, (
                f"{self.SKILL} does not state {value:.0%}, a factor the engine "
                f"applies to the prior year's tax")

    def test_the_de_minimis_floor_matches_the_engine(self):
        """The $1,000 in step 6 is `compute_form_2210`'s own constant.

        It is the one §6654 figure of the four that the skill states WITHOUT
        applying — §6654(e)(1) reads current-year tax reduced by withholding,
        which this skill has no way to compute, so step 6 hands the user the
        rule and the two figures it needs. Stating a threshold is the same
        drift risk as applying one: raise the floor and the prose keeps
        quoting $1,000 to a taxpayer who reads it as a reason not to pay.
        The engine's own no-penalty message interpolates the constant rather
        than retyping it, which is what makes this a two-copy problem and not
        a three-copy one.
        """
        text = self._text()
        TC = _tax_computations()
        assert self._states(text, TC.DE_MINIMIS_BALANCE), (
            f"{self.SKILL} does not state ${TC.DE_MINIMIS_BALANCE:,}, the "
            f"§6654(e)(1) de-minimis balance `compute_form_2210` applies. "
            f"Step 6 refuses the test and hands the user the rule, so the "
            f"figure it quotes has to be the engine's")

    def test_the_ca_installment_weighting_matches_the_engine(self):
        text = self._text()
        PROJ = _projection()
        weights = " / ".join(f"{w:.0%}" for _, _, w in PROJ.CA_ES_SCHEDULE)
        assert weights in text, (
            f"{self.SKILL} must state CA's weighting as {weights!r} — a "
            f"Californian splitting a shortfall into four equal quarters is "
            f"paying on a schedule the state does not use")
        # The zero is the counter-intuitive half, and a reader who takes the
        # weights as decoration still needs it in words.
        q3 = next(w for q, _, w in PROJ.CA_ES_SCHEDULE if q == 3)
        assert q3 == 0.0, "CA_ES_SCHEDULE Q3 is no longer zero — the skill says it is"
        assert "no September instalment" in text or "no September installment" in text

    def test_the_ca_cliffs_match_the_engine(self):
        text = self._text()
        PROJ = _projection()
        for value in (PROJ.CA_HIGH_INCOME_AGI_LIMIT,
                      PROJ.CA_HIGH_INCOME_AGI_LIMIT_MFS,
                      PROJ.CA_ES_DE_MINIMIS, PROJ.CA_ES_DE_MINIMIS_MFS):
            assert self._states(text, value), (
                f"{self.SKILL} does not state ${value:,}, a CA threshold the "
                f"engine applies (R&TC §19136 / §19136.3)")

    def test_the_california_farmer_is_routed_off_the_weighted_schedule(self):
        """FTB 5805F, and the engine already says so where it declines the case.

        `_ca_notes` prints "not modeled: farmers/fishermen (FTB 5805F ... this
        30/40/0/30 schedule does not apply to them)" beside every projected CA
        voucher run. This skill computes the same schedule by hand, so without
        the same branch it applies the weights to the one population California
        exempts from them — reporting April and June shortfalls against a
        calendar holding a single January instalment. That is a manufactured
        underpayment, not a conservative one, and the reader has no engine
        output beside it to contradict the claim.

        The ordering is asserted too: a branch stated after the weights is read
        after the wrong schedule has already been applied.
        """
        text = self._text()
        PROJ = _projection()
        # Every field `_ca_notes` reads, including `de_minimis` — a stub short
        # of one is not a lighter CaSafeHarbor, it is a different object, and
        # this one went red the first time a note keyed on a new field.
        notes = "\n".join(PROJ._ca_notes(types.SimpleNamespace(
            high_income_90_only=False, degraded=False, prior_agi_missing=False,
            prior_tax=None, prior_tax_unnetted=False, de_minimis=False,
            prior_year_floor=False, prior_year_floor_unknown=False,
            current_tax_unnetted=False)))
        assert "5805F" in notes, (
            "the engine no longer excludes farmers/fishermen from the weighted "
            "CA schedule — the skill's branch was derived from that exclusion "
            "and has to be re-derived before this test is relaxed")
        assert "5805F" in text, (
            f"{self.SKILL} applies CA's weighted schedule to farmers and "
            f"fishermen, whom FTB puts on Form 5805F with one January 15 "
            f"instalment — the April and June shortfalls it reports do not exist")
        weights = " / ".join(f"{w:.0%}" for _, _, w in PROJ.CA_ES_SCHEDULE)
        assert text.index("5805F") < text.index(weights), (
            "the 5805F branch is stated after the weighting it replaces, so a "
            "reader working forward has already applied the wrong schedule")

    def test_the_worked_example_reproduces_the_engines_own_answer(self):
        """The strongest form of the parity claim: run the reader's own numbers.

        The assertions above pin the PARAMETERS. This pins the ARITHMETIC — the
        three lines a reader copies and applies to their own return. Prose can
        state every threshold correctly and still show a multiplication whose
        product is wrong, or divide by the wrong denominator, and no substring
        search would notice.

        Figures are PARSED out of the skill rather than restated here, for the
        same reason the parameters are derived: a hardcoded expectation is
        another copy to drift. If the example is reworded past this regex the
        test fails loudly rather than silently stopping covering it.
        """
        text = self._text()
        PROJ = _projection()
        TC = _tax_computations()

        mult = re.search(r"\$([\d,]+) x ([\d.]+) = \$([\d,]+) required for the year", text)
        assert mult, "the worked multiplication is no longer in the skill"
        prior_tax = float(mult.group(1).replace(",", ""))
        factor = float(mult.group(2))
        required_stated = float(mult.group(3).replace(",", ""))
        assert factor == TC.PRIOR_YEAR_FACTOR_HIGH, (
            f"the example applies {factor}, which is not the engine's high factor")

        # `projected_tax` set far above the prior year's, so the 90%-of-current
        # prong cannot be the binding one — the engine then returns exactly the
        # prior-year prong the skill computes by hand, which is the comparison
        # being made. A projected tax near the prior year's would have the engine
        # answer a different question and the test would pass or fail on that.
        engine = PROJ.compute_safe_harbor(
            projected_tax=prior_tax * 10, prior_tax=prior_tax,
            prior_agi=TC.PRIOR_YEAR_AGI_THRESHOLD + 1, filing_status="SINGLE",
            withholding=0, estimates_paid=0)
        assert engine.required_annual == required_stated, (
            f"the skill's example says ${required_stated:,.0f} where the engine "
            f"computes ${engine.required_annual:,.2f} from the same inputs")

        sub = re.search(r"\$([\d,]+) - \$([\d,]+) = \$([\d,]+) still to pay", text)
        assert sub, "the worked subtraction is no longer in the skill"
        assert float(sub.group(1).replace(",", "")) == required_stated, (
            "the subtraction starts from a different number than the "
            "multiplication produced")
        paid = float(sub.group(2).replace(",", ""))
        shortfall_stated = float(sub.group(3).replace(",", ""))
        with_paid = PROJ.compute_safe_harbor(
            projected_tax=prior_tax * 10, prior_tax=prior_tax,
            prior_agi=TC.PRIOR_YEAR_AGI_THRESHOLD + 1, filing_status="SINGLE",
            withholding=paid, estimates_paid=0)
        assert with_paid.shortfall == shortfall_stated, (
            f"the skill's example says ${shortfall_stated:,.0f} still owed where "
            f"the engine computes ${with_paid.shortfall:,.2f}")

        div = re.search(r"\$([\d,]+) / (\d+) = \$([\d,.]+) per paycheck", text)
        assert div, "the worked division is no longer in the skill"
        assert float(div.group(1).replace(",", "")) == shortfall_stated, (
            "the division starts from a different number than the subtraction "
            "produced")
        per = PROJ.compute_w4_4c(shortfall_stated, int(div.group(2))).per_paycheck
        assert per == float(div.group(3).replace(",", "")), (
            f"the skill's per-paycheck figure is not what the engine's W-4 4(c) "
            f"split produces (${per})")


class TestTheAdvisorySkillAllocatesInstalmentsCumulatively:
    """The one place the skill deliberately does NOT follow the engine.

    `compute_estimated_vouchers` spreads the ANNUAL shortfall across whichever
    deadlines are still open, and its docstring says why that is a
    simplification: each instalment is tracked cumulatively, so an even split
    puts less at the earliest open deadline than that deadline requires and
    leaves it underpaid until the next one lands. In a skill whose entire
    licence is "this figure can be too high, never too low", that shape is not
    available — so the body walks the deadlines cumulatively instead, and this
    is the check on the arithmetic it shows for doing so.

    Pinned here and not against the engine precisely BECAUSE the two differ: a
    later reader harmonizing the prose with `compute_estimated_vouchers` would
    be reintroducing the understatement, and the numbers stop satisfying the
    cumulative identity below the moment they do.
    """

    SKILL = "tax-safe-harbor"
    # Bounded on the right, and cents optional: `[\d,.]+` swallows the sentence's
    # closing period and float() then raises where an assertion should have run.
    MONEY = r"[\d,]+(?:\.\d\d)?"

    def test_the_september_figures_satisfy_the_cumulative_identity(self):
        MONEY = self.MONEY
        text = re.sub(r"\s+", " ", skill_text(self.SKILL))

        annual = re.search(r"= \$([\d,]+) required for the year", text)
        gap = re.search(r"\$([\d,]+) - \$([\d,]+) = \$([\d,]+) still to pay", text)
        assert annual and gap, "the worked federal example is no longer in the skill"
        required = float(annual.group(1).replace(",", ""))
        withheld = float(gap.group(2).replace(",", ""))
        shortfall = float(gap.group(3).replace(",", ""))

        sep = re.search(
            r"n = 3, so \$(" + MONEY + r") is required by then against \$("
            + MONEY + r") credited, leaving \*\*\$(" + MONEY
            + r") due on September 15\*\*", text)
        assert sep, (
            "the cumulative September example is no longer in the skill — if it "
            "was replaced by an even split of the annual gap, that is the "
            "understatement this class exists to keep out")
        req_by_q3, credited_q3, due_q3 = (
            float(g.replace(",", "")) for g in sep.groups())

        assert req_by_q3 == round(required * 3 / 4, 2), (
            f"three quarters of ${required:,.2f} is "
            f"${round(required * 3 / 4, 2):,.2f}, not ${req_by_q3:,.2f}")
        assert credited_q3 == round(withheld * 3 / 4, 2), (
            "withholding is credited ratably (§6654(g)(1)), so three quarters of "
            f"${withheld:,.2f} — ${round(withheld * 3 / 4, 2):,.2f} — is what "
            f"stands against the September requirement, not ${credited_q3:,.2f}")
        assert due_q3 == round(req_by_q3 - credited_q3, 2), (
            f"${req_by_q3:,.2f} required less ${credited_q3:,.2f} credited is "
            f"${round(req_by_q3 - credited_q3, 2):,.2f} due at that deadline, "
            f"not the ${due_q3:,.2f} the example shows")

        rest = re.search(r"takes the remaining \$(" + MONEY + r")", text)
        assert rest, "the January remainder is no longer stated"
        assert float(rest.group(1).replace(",", "")) == round(shortfall - due_q3, 2), (
            "the two instalments no longer sum to the annual gap the "
            "subtraction in step 3 produced")

        # The reason the whole block exists, stated as an inequality so it holds
        # for any figures a future rewrite of the example uses: the cumulative
        # amount due at the earliest open deadline EXCEEDS what an even split
        # would put there. If they were equal the example would be showing the
        # even split while claiming to correct it.
        assert due_q3 > round(shortfall / 2, 2), (
            f"${due_q3:,.2f} is not more than an even two-way split of "
            f"${shortfall:,.2f}, so the example does not demonstrate the "
            f"underfunding it is there to warn against")

    def test_california_weights_BOTH_sides_of_its_cumulative_test(self):
        """30/40/0/30 governs the requirement AND the withholding, not just one.

        This assertion has now been wrong in both directions, so it is pinned
        against the form rather than against either reading. FTB 5805
        Worksheet II line 1 enters the required annual payment "multiplied by
        30% for column (a) and column (d) ... -0- in column (c), and 40% for
        column (b)"; line 2 enters "the amount shown on form FTB 5805, Part II,
        line 3" — headed "Withholding taxes" — "multiplied by 30% ... -0- in
        column (c) and 40% for column (b)". Part I question 3 asks whether the
        withholding was NOT withheld in equal installments and is the election
        OUT of that default, available only to a taxpayer who can show the
        actual amounts and dates.

        Reading the two sides as 30/70/70/100 against equal quarters makes
        April 15 need 30% of the requirement against 25% of the withholding and
        reports a shortfall of 5% of the year's withholding for a taxpayer
        whose withholding covers everything — a catch-up payment demanded
        against nothing owed, the over-stating direction. Reading them aligned,
        the test collapses to 30% of what withholding does not already cover,
        and the Short Method on the form itself (Part II lines 7-10, total
        withholding and estimates against the required annual payment, with no
        per-column allocation at all) is only coherent because they ARE
        aligned.
        """
        text = re.sub(r"\s+", " ", skill_text(self.SKILL))
        PROJ = _projection()

        weights = [w for _, _, w in PROJ.CA_ES_SCHEDULE]
        cumulative = [sum(weights[:i + 1]) for i in range(len(weights))]
        required = " / ".join(f"{c:.0%}" for c in cumulative)
        quarters = " / ".join(f"{(i + 1) / len(weights):.0%}"
                              for i in range(len(weights)))

        assert required != quarters, (
            f"CA_ES_SCHEDULE now accumulates to {required}, the same as equal "
            f"quarters — this test distinguishes nothing until they differ again")
        assert required in text, (
            f"the skill must state {required!r} as the CUMULATIVE amount CA "
            f"requires by each deadline")
        assert quarters not in text, (
            f"the skill still names {quarters!r} as a CA fraction. Equal "
            f"quarters are the FEDERAL rule; on the CA side they credit only "
            f"25% of the year's withholding against a 30% requirement and "
            f"invent an April shortfall for a taxpayer who owes nothing")
        assert "BOTH of its sides" in text or "on BOTH of its sides" in text, (
            "the skill states CA's weights without saying they govern the "
            "withholding too — the half a reader supplies from the federal "
            "rule is exactly the half that is different")
        assert "question 3" in text and "election" in text.lower(), (
            "Part I question 3 is no longer identified as the election OUT of "
            "the default allocation; read as the rule rather than the "
            "exception it turns the default upside down")

        # The engine half, derived: with $4,189 of the $5,000 requirement
        # already withheld, April needs 30% of the $811 remainder. The two
        # readings disagree by 5% of the withholding, so this case separates
        # them rather than merely exercising the code.
        vouchers = PROJ.compute_ca_estimated_vouchers(
            5000.0 - 4189.0, datetime.date(2026, 3, 2))
        april = next(v for v in vouchers if v.quarter == 1)
        assert april.amount == round((5000.0 - 4189.0) * weights[0], 2)
        assert PROJ._voucher_catchup_note(
            PROJ._CA_CATCHUP, vouchers,
            types.SimpleNamespace(required_annual=5000.0, withholding=4189.0,
                                  estimates_paid=0.0,
                                  de_minimis=False),
            datetime.date(2026, 3, 2)) is None, (
            "the engine reports the March CA schedule as under-funded, so one "
            "of its two sides is running on quarters again — and the skill's "
            "step 5, which sends the reader back to this same test, would "
            "collect the same payment against nothing owed")

    def test_a_ca_catchup_is_not_held_for_the_next_weighted_date(self):
        """"CA pays on three dates" is true of INSTALMENTS, not of catch-ups.

        The weighting decides what each instalment REQUIRES; §19136 follows
        §6654(b)(2) in running an underpayment until it is PAID. So a
        Californian who missed April or June has principal accruing today, and
        the only sentence in the paragraph about when a CA payment is made
        said it happens on the three weighted dates — which sends that money
        to January 15 and buys months of accrual for nothing. The federal
        timing check produces the same catch-up, and both engine schedules
        carry the same instruction, so the CA paragraph cannot be the one
        place that defers it.
        """
        text = re.sub(r"\s+", " ", skill_text(self.SKILL))
        weighted = text.find("dates that carry weight")
        assert weighted >= 0, (
            "the sentence restricting CA payments to the weighted dates has "
            "been reworded — this test pins what QUALIFIES it")
        window = text[weighted:weighted + 500]
        assert "CATCH-UP is not a scheduled instalment" in window, (
            "the CA paragraph says a payment is made only on the three "
            "weighted dates without exempting a catch-up, so a missed April "
            "or June instalment is held to the next weighted date while it "
            "keeps accruing")
        assert "on the day it is computed" in window, (
            "the CA catch-up is named but not dated to TODAY — an instruction "
            "that does not say when to pay is read as the printed schedule")


class TestTheAdvisorySkillCorrectsTheEngineItHandsOffTo:
    """Step 0 routes a computed liability to the one tool that reads it.

    `/tax-withholding` reads result.json line 24; `/tax-project` instead
    annualizes projection inputs and is a separate forecast. Both engines have
    the current year's law. Both now require an explicitly completed
    covered-year Form 2210 line-8 pre-factor base for the federal prior-year
    prong; their post-deadline voucher simplification can still put too little
    at the earliest open deadline. That remaining correction therefore travels
    with the appropriate handoff or with a separately requested projection.

    Each assertion below pairs "the engine still behaves this way" with "the
    skill still says so", and that pairing is the design. Fixing either engine
    behavior turns the first half red — which is where the warning, now
    describing a bug that no longer exists, has to be deleted. A stale caveat is
    its own defect: it teaches the reader to discount the rest of them.
    """

    SKILL = "tax-safe-harbor"

    def _handoff(self) -> str:
        """Step 0 alone.

        The corrections have to reach the reader who is LEAVING. Stated
        anywhere further down they sit inside the procedure that reader was
        told in the same breath not to run.
        """
        text = re.sub(r"\s+", " ", skill_text(self.SKILL))
        return text[text.index("## Step 0"):text.index("## Step 1")]

    def test_a_computed_return_routes_only_to_the_actual_liability_tool(self):
        handoff = self._handoff()
        assert "hand the actual-liability question to `/tax-withholding`" in handoff
        assert "Form 1040 line 24" in handoff and "computed return" in handoff
        assert "`/tax-project`" in handoff
        assert "does not read `result.json`" in handoff
        assert "annualizes `projection_inputs.json` into a separate forecast" in handoff
        assert "never as a replacement for the known computed liability" in handoff

    def test_special_case_scope_gates_run_before_the_workspace_handoff(self):
        """The delegated engine has no inputs for either special case."""
        handoff = self._handoff()
        scope = handoff.index("Before any workspace handoff")
        route = handoff.index("Then check for a workspace")
        assert scope < route
        window = handoff[scope:route]
        for fact in ("filing status change", "farming or fishing"):
            assert fact in window
        assert "explicit NO" in window
        assert "do not hand off to `/tax-withholding`" in window
        assert "YES or unknown" in window
        assert "Form 2210-F" in window

    def test_the_engine_refuses_to_infer_the_line8_base_from_line24(self):
        PROJ = _projection()
        # Pinned on the SIGNATURE, not on feeding an archive in and watching it
        # be ignored. The archive parameter is gone, so "passing one changes
        # nothing" is no longer expressible — and the old form would now pass
        # for the wrong reason, since an archive object handed to the first
        # positional slot lands on the tax base and raises. Absence of the
        # parameter is the guarantee: nothing here CAN read a prior return.
        params = list(
            inspect.signature(PROJ._prior_tax_agi).parameters)
        assert params == ["completed_line8_tax", "completed_line8_agi",
                          "zero_liability_confirmed",
                          "return_filed_12_months_confirmed"], (
            f"`_prior_tax_agi` takes {params} — an archive-shaped parameter is "
            "back, so archived Form 1040 line 24 can reach the federal "
            "prior-year prong again")

        explicit, _ = PROJ._prior_tax_agi(15400.0)
        assert explicit == 15400.0

        template = json.loads((REPO / "agents" / "templates" /
                               "projection_inputs_template.json").read_text())
        assert "prior_year_form_2210_tax_base" in template
        handoff = self._handoff()
        assert "prior_year_form_2210_tax_base" in handoff
        assert "raw Form 1040 line 24 is never the substitute" in handoff
        assert "leave the prior prong unresolved" in handoff

    def test_the_completed_base_still_enters_the_lower_of_two_prongs(self):
        """An explicit composition is pre-factor; the engine applies min()."""
        PROJ = _projection()
        # $10,000 projected against $18,400 prior at the 110% factor: 90% of
        # the current year is $9,000 and the prior prong $20,240, so the
        # current-year prong controls and netting ANY prior-year credit leaves
        # the required annual payment untouched.
        kw = dict(projected_tax=10000.0, prior_agi=162000.0,
                  filing_status="Single", withholding=0.0, estimates_paid=0.0)
        larger = PROJ.compute_safe_harbor(prior_tax=18400.0, **kw)
        completed = PROJ.compute_safe_harbor(prior_tax=15400.0, **kw)
        assert larger.required_annual == completed.required_annual == \
            larger.pct90_current, (
            "`compute_safe_harbor` no longer takes the LOWER of the two "
            "prongs after applying the factor to the completed base")

        handoff = self._handoff()
        assert "completed pre-factor composition" in handoff
        assert "90%-of-current degraded mode" in handoff

    def test_the_engine_still_underfunds_the_earliest_open_deadline(self):
        PROJ = _projection()
        required, withheld = 20000.0, 16000.0
        # July: April and June have gone, so the engine splits the $4,000 annual
        # gap over the two deadlines left. What September 15 itself requires is
        # three quarters of the year's requirement against three quarters of the
        # year's ratably-credited withholding — a bigger number.
        vouchers = PROJ.compute_estimated_vouchers(
            required - withheld, datetime.date(2025, 7, 1))
        september = next(v for v in vouchers if v.quarter == 3)
        cumulative = round(required * 3 / 4 - withheld * 3 / 4, 2)

        assert september.amount < cumulative, (
            f"`compute_estimated_vouchers` puts ${september.amount:,.2f} at "
            f"September 15 where the cumulative requirement is "
            f"${cumulative:,.2f} — if it now tracks instalments cumulatively, "
            f"step 0's second correction is stale and must be deleted")
        handoff = self._handoff()
        for fn in ("compute_estimated_vouchers", "compute_ca_estimated_vouchers"):
            assert fn in handoff, (
                f"step 0 does not warn that `{fn}` splits the ANNUAL gap across "
                f"the open deadlines, so the referral silently reintroduces the "
                f"underfunding step 4 exists to prevent")

    def test_the_engine_preserves_the_date_attached_to_stored_ytd_figures(self):
        """A valid stored date is provenance, even after the year closes."""
        PROJ = _projection()
        resolved, note = PROJ.resolve_as_of(
            None, "06-30", 2025, datetime.date(2026, 1, 5))
        assert (resolved, note) == ("06-30", None)

        ytd = {"wages": 100000.0, "federal_withholding": 15000.0}
        projected = PROJ.annualize(
            ytd, PROJ.parse_as_of(resolved, 2025), "biweekly", {}, {}, {})
        relabeled = PROJ.annualize(
            ytd, PROJ.parse_as_of("12-31", 2025), "biweekly", {}, {}, {})
        assert projected.federal_withholding > relabeled.federal_withholding

        handoff = self._handoff()
        assert "is provenance for the YTD figures" in handoff
        assert "Do not force `--as-of 12-31`" in handoff
        assert "replace the YTD figures with the year's finals and change " \
               "their stored date together" in handoff


    def test_the_engine_cannot_be_told_that_january_15_has_passed(self):
        """The date that fixes the annualization freezes the calendar with it.

        `--as-of` is MM-DD inside the COVERED year, so 12-31 is not merely the
        right value, it is the largest one there is: no argument reaches
        January 15 of the year AFTER, which is when the fourth instalment
        falls due. Both schedules therefore print that instalment as open and
        funded however late the real date is. A January 20 inquiry about the
        year just closed is rendered a voucher for a deadline five days gone,
        and after April 15 §6654(b)(2) has stopped the clock altogether — the
        penalty is fixed and a 1040-ES payment reduces none of it, while the
        table still reads as an action. Understating timing exposure on the
        path step 0 has just called the proper one is the direction this
        skill's licence rules out, so the handoff carries the calendar too.
        """
        PROJ = _projection()
        year_end = PROJ.parse_as_of("12-31", 2026)
        assert year_end == datetime.date(2026, 12, 31), (
            "`parse_as_of` no longer resolves MM-DD inside the covered year, "
            "so the freeze this correction describes may be gone")
        for label, build in (("federal", PROJ.compute_estimated_vouchers),
                             ("CA", PROJ.compute_ca_estimated_vouchers)):
            funded = [v for v in build(4000.0, year_end) if v.amount > 0]
            assert [v.due_date for v in funded] == ["2027-01-15"], (
                f"the {label} schedule at the latest expressible as-of date no "
                f"longer presents January 15 as the one open instalment — the "
                f"correction is describing a schedule that no longer exists")

        handoff = self._handoff()
        assert "figure 6" in handoff, (
            "the frozen January 15 is not read against today's date, so "
            "nothing in the handoff distinguishes an instalment still open "
            "from one already missed")
        for phrase, missed in (
                ("§6654(b)(2)", "the rule that ends the accrual period"),
                ("April 15", "the day it ends on"),
                ("January 31", "§6654(h)'s escape, still live after the 15th"),
                ("pay NOW", "the instruction for a voucher already late")):
            assert phrase in handoff, (
                f"step 0 hands over a schedule that prints January 15 as open "
                f"without {missed}, so an already-closed instalment is "
                f"presented as a future payment")


class TestTheAdvisorySkillsIntakeIsTheForm2210Intake:
    """Figures 1, 4 and 5 are Form 2210's lines 8, 6 and 11 — all of each.

    Every omission moves the answer in the same direction: a refundable credit
    missing from figure 1 raises the harbor the taxpayer is told to fund, and
    excess social security missing from figure 4, or a credit-elected
    overpayment from figure 5, raises the gap they are told to close. Each buys
    a payment that was never owed — and this skill's "too high, never too low"
    licence covers its choice of PRONG, not arithmetic on the wrong intake.

    `tax-compute`'s intake contract states the same three lists for the engine's
    penalty check, so the two are pinned together here. They were apart once
    already: the safe-harbor list was written without the refundable adoption
    credit that Form 2210 line 8 names and the contract already carried.
    """

    SKILL = "tax-safe-harbor"
    CONTRACT = SKILLS_DIR / "tax-compute" / "references" / "tax-and-credits.md"

    # Form 2210 line 8's own enumeration, one tuple per credit: the two
    # documents write these out differently (one spells "additional child tax
    # credit", the other "refundable ACTC"), so a match on ANY spelling counts
    # and neither document is forced into the other's house style.
    REFUNDABLE = (
        ("earned income", "eic"),
        ("additional child tax", "actc"),
        ("american opportunity", "aotc"),
        ("adoption",),
        ("premium tax credit", "ptc"),
        ("fuel",),
        ("1341",),
    )

    def _text(self) -> str:
        return re.sub(r"\s+", " ", skill_text(self.SKILL)).lower()

    def _figure(self, n: int) -> str:
        """The nth intake item, from its marker to the next one."""
        text = self._text()
        start = re.search(rf" {n}\. \*\*", text)
        assert start, f"intake figure {n} is no longer a numbered item"
        end = re.search(rf" {n + 1}\. \*\*", text[start.end():])
        return text[start.start():start.end() + (end.start() if end else len(text))]

    def test_figure_1_asks_which_return_where_the_prior_year_was_amended(self):
        """"Last year's filed return" fetches the latest copy, not the right one.

        IRM 20.1.3.3.1.1: "Preceding taxable year's tax refers to the tax
        shown on the taxpayer's original return, or shown on an amended return
        for the previous year if filed prior to the due date for that year,
        including extensions." The ordinary amendment is later than that — it
        follows a corrected 1099 or a missed deduction by months — and it does
        not replace the figure. Asked for without the question, a downward
        amendment walks into figure 1 and takes the required annual payment
        down with it: a guaranteed payment too low, which is the one failure
        this skill's licence says cannot happen. The cut-off is the RETURN's
        due date including extensions, not April 15, so an amendment filed in
        August under a valid extension still counts.
        """
        figure = self._figure(1)
        for token, missed in (
                ("amend", "the question is never asked"),
                ("original", "the answer — the original return — is never named"),
                ("1040-x", "the form the user will be holding is not named"),
                ("20.1.3.3.1.1", "the rule carries no authority a reader can check"),
                ("extensions", "the cut-off reads as the filing deadline, "
                               "wrongly rejecting a timely extended amendment"),
                ("too low", "the direction is unstated, so a reader treats it "
                            "as a formality rather than as the failure mode")):
            assert token in figure, (
                f"figure 1 asks for last year's filed return and {missed}; a "
                f"return amended downward after its due date is not the "
                f"§6654(d)(1)(B)(ii) baseline and produces a payment that "
                f"does not close the harbor")
        assert "amendment rule" in self._figure(2), (
            "figure 2 takes AGI from 'the same return' without saying which "
            "return that now is, so a reader can pair an original line 24 "
            "with an amended line 11 and pick the factor off neither")

    def test_figure_4_admits_every_credit_form_2210_line_6_admits(self):
        """Line 6 is not withholding alone, and figure 4 claims to be complete.

        The Form 2210 instructions for line 6 add, after the withholding
        lines: "Credit for U.S. tax paid to the USVI. You can take a credit on
        your U.S. return for the U.S. tax allocated to the USVI only if you
        actually paid the tax to the USVI... You can include that credit
        here." Figure 4 already carries the other non-obvious member — excess
        social security under §31(b) — and the intake says in terms that
        nothing else is needed, which is what turns an omission into a wrong
        answer rather than a gap: the credit is money already paid in, so
        leaving it out inflates the gap and buys a payment that was never
        owed. Same direction as the excess-SS omission, same fix.
        """
        figure = self._figure(4)
        # Bounded: `"8689" in figure` is satisfied by "86890" and by any typo
        # that keeps the digits as a prefix — the substring vacuity this suite
        # already records for money figures, arriving as a form number.
        assert re.search(r"\bform 8689\b", figure), (
            "figure 4 does not ask for the Form 8689 credit. Form 2210 line 6 "
            "admits it beside withholding, and figure 4 claims to be the whole "
            "of line 6 — so a USVI filer's payment is counted short and the "
            "recommended payment is too high")
        assert "usvi" in figure, (
            "the form number is asked for without naming what it is, so a "
            "reader with the credit does not recognise the question")

    def test_every_refundable_credit_is_netted_in_both_documents(self):
        skill = self._text()
        contract = re.sub(r"\s+", " ", self.CONTRACT.read_text()).lower()
        for spellings in self.REFUNDABLE:
            assert any(s in contract for s in spellings), (
                f"{self.CONTRACT.name} no longer names {spellings[0]!r} among "
                f"the refundable credits netted out of line 24 — the roster is "
                f"Form 2210 line 8's, and this pairing is what keeps the two "
                f"documents from drifting apart one credit at a time")
            assert any(s in skill for s in spellings), (
                f"{self.SKILL} figure 1 does not net {spellings[0]!r}, which "
                f"Form 2210 line 8 subtracts and the compute contract already "
                f"lists — the harbor it computes is too high by that credit")

    def test_excess_social_security_is_collected_as_withholding_not_as_a_credit(self):
        """Line 6, not line 8 — the one member of the intake that changes sides.

        Excess social security withheld by two employers is a §31(b) credit for
        tax WITHHELD, so Form 2210 counts it with the withholding that spreads
        ratably over the instalments, and `tax-compute`'s contract flags the
        same trap ("excess SS/RRTA is NOT here"). Netted against line 24 instead
        it would shrink the harbor; left out altogether it inflates the gap.
        """
        withholding = self._figure(4)
        assert "schedule 3" in withholding and "line 11" in withholding, (
            f"{self.SKILL} figure 4 does not ask for Schedule 3 line 11 excess "
            f"social security / tier 1 RRTA, which Form 2210 line 6 counts as "
            f"withholding — the gap it computes is too large by that amount")
        assert "social security" not in self._figure(1), (
            "excess social security has moved into the refundable-credit "
            "figure, where it would be netted against the prior year's tax "
            "instead of credited against the current year's requirement")

    def test_figure_one_is_what_line_8_is_computed_from_not_line_8(self):
        """Line 8 already carries the factor step 2 is about to apply.

        Form 2210 Part I line 8 reads "enter 110% of the amount of the tax
        computed earlier" — it is the prior-year tax net of refundable credits
        AFTER the §6654(d)(1)(C) multiplication. Figure 1 is that tax BEFORE it.
        Labelling figure 1 "the Form 2210 line 8 figure" invites a user holding
        an old 2210 to read line 8 off it, and step 2 then multiplies a second
        time: a 10% over-statement, which is exactly the cry-wolf shape the
        skill argues against everywhere else.
        """
        figure = self._figure(1)
        assert "line 8 is computed from" in figure, (
            f"{self.SKILL} figure 1 no longer says it is line 8's INPUT. Line 8 "
            f"is this figure after step 2's factor, so a user who reads line 8 "
            f"off a prior-year 2210 gets the factor applied twice")
        assert not re.search(r"this is the form 2210 line 8 figure", figure), (
            "figure 1 claims to BE Form 2210 line 8. It is the tax line 8 is "
            "computed FROM — the difference is the 100%/110% factor")

    def test_figure_one_refuses_raw_line_24_without_the_line_8_composition(self):
        """Schedule 2 taxes make line 24 an unsafe stand-in for line 8."""
        figure = self._figure(1)
        for phrase in (
                "do not start with form 1040 line 24",
                "schedule 2",
                "household-employment-tax exception",
                "no federal income-tax withholding",
                "section 965",
                "if the user has only line 24",
                "covered-year line-8 instructions",
                "never infer the composition"):
            assert phrase in figure, (
                "figure 1 does not preserve the Form 2210 line-8 safeguard "
                f"{phrase!r}")
        assert "**stop**" in figure

    def test_the_adoption_credit_gate_covers_both_refundable_windows(self):
        """The adoption credit has been refundable TWICE, and both are in range.

        OBBBA made part of it refundable for prior years beginning after 2024,
        and ACA §10909 had already made it FULLY refundable for 2010 and 2011
        (§23 redesignated §36C), reverting for 2012 — the 2012 Form 8839
        instructions open "Credit is nonrefundable". Step 1 admits covered
        years from 2003, so prior years of 2010 and 2011 are both reachable.

        A gate reading "refundable only after 2024, before that wholly
        nonrefundable" is therefore false on two in-range years, and false in
        the direction that does NOT subtract a genuinely refundable credit —
        figure 1 comes out too HIGH, in a skill whose contract is that it may
        overshoot only when it says so. Nothing downstream re-checks it.
        """
        figure = self._figure(1)
        at = figure.index("adoption")
        window = figure[max(0, at - 200):]
        for year in ("2010", "2011", "2012", "2024"):
            assert year in window, (
                f"{self.SKILL} figure 1's adoption-credit gate does not "
                f"mention {year}. Both refundable windows (2010-2011 under ACA "
                f"§10909, and after 2024 under OBBBA) and the 2012 reversion "
                f"have to be there — the covered-year floor is 2003, so a "
                f"prior year of 2010 or 2011 is inside the skill's own range")
        assert "nonrefundable" in window, (
            "the gate does not say what the credit WAS between the two "
            "windows, so a reader with a 2012-2024 return has no way to tell "
            "which side of the line their own Form 8839 falls on")
        # The line NUMBER is year-specific and the skill must not carry the
        # modern one back: in 2010 and 2011 the refundable credit was reported
        # on Form 1040 line 71, and Form 8839 line 13 was a limit line.
        assert "line 71" in window, (
            "the 2010/2011 window cites no line, so a reader follows the "
            "current form's Form 8839 line 13 — which was not the refundable "
            "credit on those returns")
        assert "label" in window, (
            "the gate does not route the reader back to that year's own "
            "line-8 chart, which is the authority the skill defers to "
            "everywhere else a pre-2020 form is involved")

    def test_a_credit_elected_overpayment_is_collected_with_its_date(self):
        payments = self._figure(5)
        assert "overpayment" in payments, (
            f"{self.SKILL} figure 5 asks only for payments the taxpayer MADE. A "
            f"prior-year refund applied to this year's estimated tax is an "
            f"estimated payment with no voucher behind it, and a taxpayer who "
            f"says they made none often has one")
        assert "april 15" in payments, (
            "the applied overpayment is collected without the date Form 2210 "
            "line 11 gives it, and step 4's cumulative test reads dates")

        # Line 11's rule is about when the MONEY went in, not when the return
        # was filed: April 15 where the prior year was fully paid before then,
        # otherwise the date of the payment that created the overpayment. The
        # contract carried a filing-date variant instead, which dates a credit
        # from an extension-filed-but-fully-withheld return to October and
        # overstates every earlier instalment. Both documents are pinned, and
        # the "paid in full" wording is what distinguishes the two rules.
        contract = re.sub(r"\s+", " ", self.CONTRACT.read_text()).lower()
        for name, doc in ((self.SKILL, payments), (self.CONTRACT.name, contract)):
            assert "paid in full" in doc, (
                f"{name} no longer dates a credit-elected overpayment on "
                f"whether the prior year was PAID IN FULL before April 15. If "
                f"it has gone back to keying on the FILING date, an "
                f"extension-filed return whose tax was covered by withholding "
                f"credits in October instead — understating early instalments "
                f"in the skill and overstating them in the engine")

    def test_two_prior_returns_bring_two_agis_and_ONE_combined_factor(self):
        """Two prior returns, two AGIs — and ONE factor, on their sum.

        Treas. Reg. §1.6654-2(e)(3)(i) writes this case out: for a filer joint
        this year who filed separately last year, the tax shown on the prior
        return "shall be the sum of both the tax shown on the return of the
        taxpayer and the tax shown on the return of the taxpayer's spouse".
        The prior year is thereby ONE quantity, and §6654(d)(1)(C)(i) has one
        place to put a factor — it substitutes 110% inside clause (ii) of
        §6654(d)(1)(B), and there is a single clause (ii), not one per spouse.
        The threshold is on the same scale: $150,000 is the joint figure,
        halved to $75,000 by (C)(ii) for a filer SEPARATE in the covered year,
        and this filer is joint in it.

        The skill said per-RETURN once, citing the order of two paragraphs in
        the Form 2210 line-8 instructions. That reading and this one diverge in
        exactly one window — the combined AGI clears $150,000 and neither
        separate AGI does — and there the per-return figure is SHORT by a tenth
        of the whole prior-year tax, which is the one direction this skill
        promises cannot happen. Both AGIs are still collected; they are what
        the sum is made of.
        """
        text = self._text()
        agi = self._figure(2)
        assert "both of them" in agi, (
            f"{self.SKILL} figure 2 asks for one prior-year AGI. Two separate "
            f"prior returns have two, and the combined factor is read off "
            f"their SUM — which needs both")
        assert "per return, not per couple" not in text, (
            f"{self.SKILL} is back on the per-RETURN factor. Two $100,000 AGIs "
            f"then take 100% where the couple's own $200,000 takes 110%, and "
            f"the harbor is short by a tenth of the whole prior-year tax")
        assert "against their **combined** prior-year agi" in text, (
            "the branch does not state the combined-AGI rule, so an agent "
            "holding two AGIs has nothing telling it which to test")
        assert "1.6654-2(e)(3)(i)" in text, (
            "the combined rule is asserted without the regulation that makes "
            "the prior year one quantity — leaving it a preference, and the "
            "next reader free to prefer the shorter one")
        assert "single clause (ii) to substitute in, not one per spouse" in text, (
            "the branch does not say WHY there is only one factor. "
            "§6654(d)(1)(C)(i) modifies (B)(ii); a second factor would need a "
            "second (B)(ii), and the statute has one")
        assert "this table is read once against their combined agi" in text, (
            "step 2 states the combined rule nowhere its own multiplication "
            "can be read, so an agent working the table applies it per return")
        assert "whole joint agi against the $75,000 threshold" in text, (
            "the converse case — joint last year, separate this year — tests a "
            "half share of the joint AGI, where §6654(d)(1)(C) reads the AGI "
            "SHOWN on the prior return against the MFS threshold")

    def test_the_combined_factor_does_not_claim_line_8_settles_it(self):
        """The rule above is chosen, not quoted — and the file has to say so.

        Nothing settles the AGI question textually. Form 2210 line 8 tests "the
        AGI on your 2024 return", singular, and separately says to add the two
        spouses' taxes "as figured earlier"; whether that phrase reaches back
        past the 110% sentence is stated in neither the instructions, Pub 505,
        nor IRM 20.1.3. Citing line 8's paragraph ORDER as the authority is
        what put the per-return rule here, and a replacement that cited it the
        other way would be the same error facing the other direction.

        So the branch has to carry three things a bare rule would not: that
        line 8 does not decide it, the ONE window where the readings differ,
        and the reason the higher is taken there. The last is this skill's
        licence — "too high, never too low" — which is a licence to CHOOSE a
        prong, so a choice made under it has to be visible to the reader who
        pays on it, not silently folded into a number.
        """
        text = self._text()
        assert "does not settle this" in text, (
            "the branch presents the combined factor as what Form 2210 says. "
            "It is not: line 8 never applies its AGI test per return, and it "
            "never combines the AGIs either")
        assert "an ordering is not an instruction" in text, (
            "the branch does not name the reasoning error it exists to undo, "
            "so the next reader re-derives the per-return rule from the same "
            "two paragraphs in the same order")
        assert "not in pub 505, not in irm 20.1.3" in text, (
            "the branch says line 8 is silent without saying the other two "
            "sources are too — leaving a reader to go looking for the answer "
            "in the places already checked")
        assert "differ in exactly one window" in text, (
            "the divergence is stated as general, so a reader applies the "
            "caveat to every separate-to-joint case instead of the one where "
            "the combined AGI clears $150,000 and neither separate AGI does")
        assert "a ten per cent band a preparer may read the other way" in text, (
            "the chosen reading is presented as settled. It is the safe one, "
            "not the certain one, and a report that hides the band cannot be "
            "checked by the preparer who would take the other side")


class TestTheAdvisorySkillIsAnchoredToOneCoveredYear:
    """"This year" names two different years on the day the skill is for.

    The description invites invocation "approaching September 15 or January 15",
    and January 15 is inside the window — January 1 to April 15 — where the
    instalment falling due belongs to the year that ENDED while the calendar
    reads the year that began. Every figure the skill collects is year-keyed:
    the prior return, the filing status, the factor, the deadlines. Pairing one
    year's return with another's status is silently wrong in both.

    The same window has an end. After the covered year's return comes due the
    §6654 period has closed on every instalment, and a voucher sent then buys
    nothing at all — the one recommendation this skill can make that is not
    merely conservative but inert.
    """

    SKILL = "tax-safe-harbor"

    def _text(self) -> str:
        return re.sub(r"\s+", " ", skill_text(self.SKILL))

    def test_the_covered_year_is_fixed_before_the_first_figure_is_asked(self):
        text = self._text()
        first = re.search(r" 1\. \*\*Prior-year total tax", text)
        assert first, "the intake no longer opens on the prior-year tax figure"
        year = text.find("WHICH TAX YEAR")
        assert 0 <= year < first.start(), (
            "the skill asks for last year's return before establishing which "
            "year the payment covers. Invoked in January that pairs the tax "
            "year that just ended with the wrong return and the wrong filing "
            "status, and nothing downstream can detect either")

    def test_the_penalty_period_is_closed_at_the_return_due_date(self):
        text = self._text()
        assert "§6654(b)(2)" in text and "fourth month" in text, (
            "the skill no longer states where the underpayment period ENDS. "
            "§6654(b)(2) runs each instalment to the earlier of payment or the "
            "15th day of the fourth month after the year closes, so past that "
            "date the year's penalty is fixed")
        # CONDITIONAL, and the condition LEADS the imperative. It read "do
        # not send a 1040-ES" flat until round seventeen, which is false for
        # the one taxpayer the §7508A sentence above it exists for, and the
        # engine had already been fixed for exactly that
        # (`test_projection.py::test_a_postponed_instalment_is_not_told_the_
        # schedule_is_spent`). Pinning the condition WITH the imperative is
        # what stops the flat form coming back as a tidy-up.
        assert "where no postponement reached an instalment, do not send one" \
            in text, (
            "a closed year still gets an estimated-tax voucher recommended for "
            "it — money paid into instalments that are all behind it, reducing "
            "a penalty that stopped accruing — or the suppression has gone "
            "categorical again, which costs a postponed taxpayer the one "
            "instalment still open to them")

    def test_a_year_whose_rules_differed_is_refused_rather_than_answered(self):
        """"Statutory and not indexed" is a claim about inflation, not amendment.

        The skill's whole licence is that its two parameters do not drift with
        the cost of living the way a bracket does. They were nonetheless
        AMENDED: §6654(d)(1)(C) ran a schedule of different applicable
        percentages — 112% for a prior year of 2001 — before settling at 110%
        for prior years from 2002, and California's arrived in 2009 (R&TC
        §19136.3's $1,000,000 limitation) and 2010 (§19136.1's weighting, the
        test below). Applying today's numbers to an older year is the same
        silent wrongness the freshness contract exists to prevent, arriving
        through time rather than through a stale cache — and the older-year
        case is real, because /tax-amend and an unfiled-return catch-up both
        reach back.
        """
        text = self._text()
        for floor, what in (("2003", "the federal factor"),
                            ("2010", "California's schedule")):
            assert floor in text, (
                f"the skill states no applicability floor for {what}, so a "
                f"covered year it does not state the law for is answered with "
                f"today's rule instead of refused")
        gate = re.search(r"[Rr]efuse a covered year before", text)
        assert gate, (
            "the year range is described but never turned into a refusal. This "
            "skill's whole answer to a question it cannot reach is step 6, and "
            "a floor with no STOP behind it is a footnote")
        first = re.search(r" 1\. \*\*Prior-year total tax", text)
        assert first and gate.start() < first.start(), (
            "the year is bounded only after the intake has been collected — by "
            "then the figures are already keyed to a year whose rules the "
            "skill does not state")

    def test_the_covered_year_is_bounded_ABOVE_as_well_as_below(self):
        """A floor with no ceiling answers every future year from today's law.

        The lower bound exists because these parameters have been AMENDED, and
        that argument runs forward as readily as back: nothing in a copy of
        this skill can notice §6654(d)(1)(C)'s percentage or either AGI
        threshold moving after it was written, and the skill has no cache to
        go stale visibly and no engine to disagree with. Left open, it applies
        2026's figures to 2031 with the same confidence — which is exactly the
        silent staleness AGENTS.md §2 exists to stop, arriving through the one
        skill that section exempts.

        So the bound is closed at both ends, and the top one degrades the way
        the freshness contract does everywhere else: ask the user to confirm
        the figures against a current source, rather than guessing or
        refusing outright.
        """
        text = self._text()
        top = re.search(r"\*\*Refuse one after (\d{4})\*\*", text)
        assert top, (
            "the covered year has a floor and no ceiling, so a year after the "
            "last one these figures were checked for is answered with them "
            "anyway — the freshness contract's failure mode, in the skill it "
            "exempts")
        floor = re.search(r"before (\d{4}) for California", text)
        assert floor and int(top.group(1)) >= int(floor.group(1)), (
            f"the ceiling ({top.group(1)}) is below the California floor "
            f"({floor.group(1) if floor else '?'}) — the window is empty")
        window = text[top.start():top.start() + 400]
        assert "confirm" in window and "current" in window, (
            "the ceiling refuses without saying what would lift it. AGENTS.md "
            "§2's stop is to ask the user for the current figures and their "
            "source, not to end the conversation")

    def test_a_nonstandard_instalment_calendar_is_refused_not_quartered(self):
        """n/4 is not universal, and neither departure is exotic enough to skip.

        §6654(k) substitutes the corresponding months for a taxable year
        beginning on any date other than January 1, so a fiscal-year filer's
        four instalments fall on the 15th day of the 4th, 6th and 9th months
        of THEIR year and the 1st month after it ends. §6654(j) gives a
        nonresident alien "3 required installments", the first due June 15,
        with 50 percent substituted for 25 percent — the Form 2210
        instructions render it as skip column (a) and enter one-half at
        column (b), and the year's withholding then credits in thirds.

        Step 4 walks four equal quarters against dates it names outright. Run
        on either filer it reports instalments that do not exist, dates that
        have not arrived, and a catch-up sized against the wrong fraction —
        and nothing in the intake would have noticed, because nothing asked.
        """
        text = self._text()
        first = re.search(r" 1\. \*\*Prior-year total tax", text)
        gate = text.find("ordinary CALENDAR year")
        assert 0 <= gate and first and gate < first.start(), (
            "the intake never establishes that the covered year runs on the "
            "ordinary four-quarter calendar, or asks only after the figures "
            "are in — by then they are keyed to deadlines that may not exist")
        for cite, what in (("§6654(k)", "the fiscal-year substitution"),
                           ("§6654(j)", "the nonresident-alien calendar")):
            assert cite in text, (
                f"{what} is not cited, so the refusal reads as a scope "
                f"preference rather than the statute it comes from")
        refusal = text.find("A fiscal year, or a Form 1040-NR")
        assert refusal >= 0, (
            "step 6 does not refuse the two calendars the intake now asks "
            "about — a question with no branch behind it is answered and "
            "then ignored")
        assert "50% at June 15" in text[refusal:refusal + 400], (
            "the 1040-NR branch is named without its shape. §6654(j) drops "
            "April and puts HALF at June 15; a reader who takes 'different' "
            "to mean 'shifted' still runs four quarters")

    def test_the_california_floor_is_the_year_its_weighting_began(self):
        """R&TC §19136.1 ran 30/30/20/20 for TY2009. 30/40/0/30 starts in 2010.

        California's two amendments did NOT arrive together, and the floor has
        to follow the LATER one. §19136.3's $1,000,000 limitation applies to
        taxable years beginning on or after January 1, 2009; §19136.1's
        no-September weighting only to those beginning on or after January 1,
        2010. TY2009 itself required 30% at the 1st and 2nd instalments and
        20% at the 3rd and 4th — a real September payment where step 5's table
        prints zero. A floor of 2009 therefore admits exactly one year the
        table is wrong for, overstating June by 10 points and dropping
        September's 20% altogether: the understating direction, on the one
        deadline the table tells the reader does not exist.
        """
        text = self._text()
        assert "before 2010 for California" in text, (
            "the California floor is not 2010. Every year it admits below "
            "that is answered with §19136.1's post-2010 weighting, which was "
            "not that year's law — and the skill has no other CA schedule")
        assert "30/30/20/20" in text, (
            "the floor no longer says what 2009's weighting actually was, so "
            "it reads as an arbitrary bump rather than the year the table "
            "below stopped being true — the next reader moves it back")

    def test_the_intake_line_numbers_are_bound_to_the_form_that_carried_them(self):
        """On the 2019 Form 1040, line 24 is the estimated-tax PENALTY.

        The floor above accepts a covered year of 2003, so the prior return
        figure 1 is read off can be any Form 1040 back to 2002 — and the
        numbering moved twice in that span. Total tax is line 24 only from the
        2020 form; it was line 16 on 2019, line 15 on 2018, line 63 on 2017.
        Reading 2019's line 24 hands over a penalty figure that is usually
        zero, and step 2 then multiplies it by 1.10: a target a fraction of
        the real one, with every check downstream satisfied. AGI moved too —
        line 8b on 2019, 11a on 2025 — and 2019's line 11b is TAXABLE income,
        smaller than AGI and so able to drop the factor from 110% to 100%.
        Both misreads run in the understating direction.
        """
        text = self._text()
        caveat = text.find("PRIOR year's own Form 1040")
        first = re.search(r" 1\. \*\*Prior-year total tax", text)
        assert caveat >= 0 and first, (
            "the intake names line numbers with nothing saying which Form 1040 "
            "they belong to")
        assert caveat < first.start(), (
            "the form-generation caveat comes after the figures it governs, so "
            "a reader has already taken line 24 off a 2019 return")
        window = text[caveat:first.start()]
        assert "line 24 is the estimated-tax PENALTY" in window, (
            "the caveat does not name what line 24 held before 2020. A "
            "mechanical read of a 2019 return returns the penalty line — near "
            "zero — and the whole answer is built on it")
        assert "2020-and-later" in window, (
            "the line numbers are not bounded to a form generation, so they "
            "read as true of every year the skill accepts")
        assert "match the printed LABEL" in window, (
            "no fallback is given for a prior year before 2020, where the "
            "numbers differ by year and only the printed label is stable")

    def test_the_agi_figure_is_year_keyed_not_only_the_caveat(self):
        """Round fifteen: the caveat above was written, figure 2 was not.

        The test one up pins the PARAGRAPH that says the numbers move. Figure
        2 then asked for "Form 1040, **line 11**" flat, a dozen lines below
        it — and an intake item is what an agent fills in. It reads the
        request, not the essay two paragraphs back, which is the whole reason
        the request exists as a separate numbered thing.

        Line 11 carries AGI only from the 2020 form. On 2019 it is the
        standard-or-itemized-plus-QBI subtotal and AGI is 8b; on 2018 it is
        the TAX and AGI is 7; on 2017 it is alimony received and AGI is 37 —
        each read off that year's own Form 1040. Every one of those is small,
        so every one reads as under $150,000 and selects 100% where 110% is
        owed: the required payment a tenth low, which is the single failure
        "too high, never too low" licence does not cover. It reaches every
        covered year from 2003 to 2020, which is most of the range step 1
        admits.
        """
        text = self._text()
        figure2 = text.find("2. **Prior-year AGI**")
        assert figure2 >= 0, "figure 2 is no longer a numbered intake item"
        end = text.find("3. **Filing status", figure2)
        assert end > figure2, "figure 3 no longer follows figure 2"
        item = text[figure2:end]
        assert re.search(r"line 11[^.]{0,80}2020", item, re.IGNORECASE), (
            "figure 2 names line 11 for AGI without the form generation it "
            "belongs to. A covered year of 2003-2020 has a prior return where "
            "line 11 is not AGI, and the caveat that says so is above the "
            "intake, not in it")
        for year, agi in (("2019", "**8b**"), ("2018", "**7**"),
                          ("2017", "**37**")):
            assert f"AGI is {agi}" in item, (
                f"figure 2 does not say where AGI sits on the {year} form, so "
                "the reader has a rule and no value to apply it to")
        assert "a tenth low" in item, (
            "the direction is unstated, so a wrong AGI line reads as a "
            "precision question rather than the one understatement this "
            "skill promises cannot happen")

    # Every document that tells an agent WHERE prior-year AGI is. This skill
    # asks the taxpayer for it; two planners write it into
    # `prior_year_form_2210_agi`; `/tax-compute` reads it off the archive —
    # and the fifth is the TEMPLATE of the file those planners write into,
    # which carries its own instructions and is the one document reachable
    # without opening a skill at all (AGENTS.md §6 makes it the headless
    # input path). All five feed the SAME §6654(d)(1)(C) threshold test, and
    # all five said "line 11" flat — the fix is only a fix in the one that was
    # reviewed unless it lands in the others too. Round eighteen is the
    # template: the earlier rounds swept the skills and stopped at the
    # directory the skills live in.
    AGI_SITES = (
        (".agents/skills/tax-safe-harbor/SKILL.md",
         "2. **Prior-year AGI**"),
        (".agents/skills/tax-project/SKILL.md",
         "populate `prior_year_form_2210_agi` with"),
        (".agents/skills/tax-withholding/SKILL.md",
         "populate `prior_year_form_2210_agi` with"),
        (".agents/skills/tax-compute/references/tax-and-credits.md",
         "`prior_year_agi` must come from"),
        ("agents/templates/projection_inputs_template.json",
         '"_comment_prior_year_form_2210_agi"'),
    )

    def test_every_document_locating_prior_agi_keys_it_to_a_year(self):
        """The factor test is one rule, written down in five places.

        `/tax-compute` computes a return for any tax year the workspace names,
        and its prior year is the workspace year minus one; the two mid-year
        planners are anchored to a current year but reach the same field, and
        this skill accepts 2003 outright. A bound written into one of them and
        not the others leaves the same understatement reachable by four
        routes, which is how this class's earlier line-number rounds got their
        second and third commits — and the template got the fourth, because a
        sweep spelled `SKILLS_DIR / skill / rel` could not reach a file
        outside `.agents/skills/` however many entries it grew. The paths are
        repository-relative now for exactly that reason.
        """
        for rel, anchor in self.AGI_SITES:
            path = REPO / rel
            flat = re.sub(r"\s+", " ", path.read_text())
            start = flat.find(anchor)
            assert start >= 0, (
                f"{rel} no longer contains {anchor!r}; this sweep "
                "cannot see whether its AGI instruction is year-keyed")
            window = flat[start:start + 700]
            assert re.search(r"line 11[^.]{0,80}2020", window,
                             re.IGNORECASE), (
                f"{rel} states line 11 for prior-year AGI without "
                "bounding it to the 2020-and-later form, so a pre-2020 prior "
                "return is read at a line that holds something else")
            assert "8b" in window, (
                f"{rel} gives the bound with no value for the year "
                "just outside it — 2019, whose line 11 is a deduction "
                "subtotal small enough to pass for AGI under the threshold")
            assert "110%" in window, (
                f"{rel} does not say what the misread costs: the "
                "factor drops to 100% and the required payment comes out a "
                "tenth low, on the filed return")

        catalog = re.sub(r"\s+", " ", (
            REPO / "docs" / "contributing" / "skill-catalog.md").read_text())
        note = "bound in the CAVEAT and nowhere a reader fills in"
        assert note in catalog, (
            "the design note behind this sweep is gone. The next contributor "
            "reads five documents agreeing and has nothing saying they agree "
            "because they were swept, which is what stops the sixth copy")
        template_note = "carries its own `_comment_prior_year_form_2210_agi`"
        assert template_note in catalog, (
            "the note records four skills and not the template, so the entry "
            "added above reads as a copy someone happened to notice rather "
            "than as the one a skills-only sweep could not reach — and the "
            "next reader trims it back to the four the prose names")

    def test_extra_withholding_is_gated_on_the_covered_year_having_payroll(self):
        """Route A is dead in the window the skill most expects to be used in.

        §6654(g)(1) makes withholding the better instrument BECAUSE it is
        credited back across the whole year — but only the year it is withheld
        in. Invoked on January 5 about the January 15 instalment, the covered
        year has ended: a W-4 4(c) change now lands entirely on the year that
        just began and does nothing whatever for the penalty being asked about.
        Route A is presented first and called "not close", so an ungated
        recommendation is the one the reader acts on.
        """
        text = self._text()
        route = text.index("**Route A")
        window = text[route:route + 700]
        assert "COVERED year still has payroll" in window, (
            "Route A recommends extra withholding without first checking the "
            "covered year still has pay periods in it. In January that sends "
            "the money to the wrong tax year and leaves the accruing "
            "instalment untouched")
        assert "January 1 to April 15" in window, (
            "the window where the covered year is closed to withholding but "
            "its penalty is still running is not named, so the gate reads as a "
            "general caution rather than a date test the agent can apply")


class TestTheAdvisorySkillReachesTheAnswersItAlreadyHolds:
    """Two defects of ORDER, not of content — the worst kind to review for.

    Both facts below were already written in the skill. Neither was reachable:
    one sat behind a STOP that fires first, the other behind a schedule that
    defers it. A survival check ("is the rule still stated?") passes on both,
    which is exactly why the assertions here are positional. An agent walks a
    procedure in the order it is written; a correct paragraph three bullets
    past a refusal is a paragraph that never runs.
    """

    SKILL = "tax-safe-harbor"

    def _text(self) -> str:
        return re.sub(r"\s+", " ", skill_text(self.SKILL))

    def _exception(self, text: str) -> int:
        """Where step 1's §6654(e)(2) precondition actually is.

        NOT `text.find("§6654(e)(2)")`. Step 0 names the section too, since
        round fourteen: a NO to its third scope question closes the prong and
        leaves the exception open, so the handoff routes the reader to THIS
        bullet before nulling the base. That mention is a POINTER, and taking
        the file's first occurrence as the anchor pointed all three orderings
        below at it — where "the stop comes first" is simply false, because
        the pointer is four hundred lines above the stop.
        """
        bullet = text.find("If figure 1 comes out at $0")
        assert bullet >= 0, (
            "step 1's no-liability precondition has been reworded, and it is "
            "the anchor these orderings are measured from")
        found = text.find("§6654(e)(2)", bullet)
        assert found >= 0, "that precondition no longer cites §6654(e)(2)"
        return found

    def test_the_zero_liability_exception_is_tested_before_the_filing_refusal(self):
        """§6654(e)(2) has three conditions, and "filed a return" is not one.

        Its text: the preceding year was 12 months, the individual had NO
        liability for it, and was a US citizen or resident throughout. Someone
        under the filing threshold last year meets all three and owed nothing,
        so no penalty can attach this year at all — a complete answer. Behind
        the "a return must have been filed" STOP they get told instead that
        only the 90% prong remains and this skill cannot compute it: a refusal
        where the answer was "you are safe, do nothing". `compute_form_2210`
        gates the same exception on prior tax == 0 alone and has no such
        ordering, so the engine is right and only the prose was wrong.
        """
        text = self._text()
        exception = self._exception(text)
        refusal = text.find("a return must have been filed")
        assert exception >= 0 and refusal >= 0, (
            "one of the two preconditions has been renamed — this test pins "
            "their ORDER, so it cannot find them by anything but their text")
        assert exception < refusal, (
            "the filed-return STOP comes before the §6654(e)(2) no-liability "
            "test, so a taxpayer with no prior-year liability and no return is "
            "refused rather than told no penalty can attach. The exception "
            "does not require a return to have been filed")
        window = text[exception:refusal]
        assert "not** require that a return was filed" in window, (
            "the no-liability bullet does not say that it applies WITHOUT a "
            "filed return, so a reader who takes the two bullets together "
            "still reads the filing requirement into it")

        # And one level up: "Without figure 1, stop" is itself a refusal, and it
        # comes BEFORE the preconditions. A taxpayer who filed nothing has no
        # line 24 to give, so without a forward reference here the exception is
        # unreachable for exactly the population it was written for.
        stop = text.find("**Without figure 1, stop.**")
        assert 0 <= stop < exception, "the missing-figure-1 refusal has moved"
        assert "not a missing figure 1" in text[stop:exception], (
            "the missing-figure-1 STOP does not exempt the taxpayer who owed "
            "nothing last year. They have no line 24 to hand over, so they are "
            "refused here and never reach the §6654(e)(2) bullet that is their "
            "entire answer")

    def test_a_prior_year_that_needed_no_return_is_not_stopped_at_figure_1(self):
        """The population §6654(e)(2) was written for files nothing.

        Round two closed the phrase loophole by requiring figure 1's $0 to be
        COMPUTED from line 24 and the credits — correct against a taxpayer who
        says "I owed nothing" of a refund, and fatal to the one who genuinely
        owed nothing, because they have no line 24 to compute it from. The
        stop then fires first and the exception below is unreachable for
        exactly the case it names as ordinary. Pub 505 chapter 2 supplies the
        second route in terms: "You had no tax liability for 2025 if your
        total tax ... was zero or you didn't have to file an income tax
        return." Not filing a return that WAS required is not that case —
        §6654(d)(1)(B)'s closing text withdraws the prong from a non-filer
        anyway — so the two have to be distinguished rather than merged.
        """
        text = self._text()
        stop = text.find("**Without figure 1, stop.**")
        exception = self._exception(text)
        assert 0 <= stop < exception, "the missing-figure-1 refusal has moved"
        window = text[stop:exception]
        assert "needed no return at all" in window, (
            "the missing-figure-1 STOP has no route for a prior year that "
            "required no return. There is no line 24 to read, so the taxpayer "
            "with no liability at all is refused before reaching the exception "
            "that is their whole answer")
        assert "not having had to file" in window, (
            "the carve-out does not say what establishes the $0 without a "
            "return — Pub 505 makes no filing REQUIREMENT the second route to "
            "\"no tax liability\", and without it the agent is left inferring one")
        assert "required and simply never filed is NOT this case" in window, (
            "the carve-out does not separate a return that was never REQUIRED "
            "from one required and not filed. Merged, it hands the prior-year "
            "prong to a non-filer §6654(d)(1)(B) withdraws it from")

    def test_the_farmer_keeps_a_catch_up_path_when_march_1_is_gone(self):
        """"Skip step 4" took the remedy away with the calendar.

        §6654(i)(1) gives a farmer ONE required installment, due January 15,
        and (D) applies §6654(h) with "March 1" for "January 31" — file and pay
        in full by then and that installment carries no penalty. It is the
        usual answer and it is not the only case: once March 1 has passed, or
        the return cannot be filed by it, the January 15 underpayment has been
        accruing and §6654(b)(2) stops it only on PAYMENT (or the following
        April 15). Telling that taxpayer to skip step 4 outright removed the
        timing check AND the instruction to pay now, leaving them an annual
        figure and no date — the under-stating direction, and the one this
        skill's licence says cannot happen.
        """
        text = self._text()
        at = text.find("Farmers and fishermen are on a different calendar")
        assert at >= 0, "the farmer precondition has been renamed"
        window = text[at:at + 1800]
        assert "skip step 4" not in window, (
            "the farmer branch still skips step 4 wholesale, which drops the "
            "one-installment timing check and with it the only instruction to "
            "pay a missed January 15 before it accrues further")
        assert "ONE deadline" in window, (
            "step 4 is not re-pointed at the single January 15 installment, so "
            "a farmer has no timing check at all")
        assert "pay NOW" in window, (
            "the farmer who cannot use the March 1 escape is given an annual "
            "figure and no instruction to pay the accruing installment")
        assert "§6654(b)(2)" in window, (
            "the accrual rule that makes payment the remedy is not stated "
            "beside it, so the instruction reads as a preference")

    def test_a_california_nonresident_is_warned_not_skipped(self):
        """"Not a CA resident" is not "no California exposure".

        Form 540NR exists for a taxpayer with California-SOURCE income who
        lives elsewhere, and R&TC §19136 reaches their estimated tax the same
        way. The CA branch keys on residence, so that taxpayer collected no CA
        figures and read the every-other-state paragraph about their HOME
        state — with California, where they actually owe, unmentioned.
        Nonresident returns are out of scope for the toolkit (AGENTS.md), and
        out of scope has to be SAID: AGENTS.md's own rule is that a situation
        the engine cannot handle blocks with the form named, never falls
        through quietly.
        """
        text = self._text()
        step5 = text.find("## Step 5")
        step6 = text.find("## Step 6")
        assert 0 <= step5 < step6, "the state step has moved or been renamed"
        section = text[step5:step6]
        assert "540NR" in section, (
            "the state step never names Form 540NR, so a nonresident with "
            "California-source income is told nothing about the CA obligation "
            "they have — only that their home state is not modeled")
        assert "CA-source income exists" in section, (
            "the skill does not ASK about California-source income, so the "
            "540NR case is only reachable for a user who already knew to "
            "raise it")

    def test_an_unmodeled_state_keeps_its_year_specific_referral(self):
        """A generic no-income-tax label cannot establish a historical regime."""
        text = self._text()
        step5 = text.find("## Step 5")
        step6 = text.find("## Step 6")
        assert 0 <= step5 < step6
        section = text[step5:step6]
        for phrase in (
                "covered year's authoritative state instructions",
                "positively establish",
                "no estimated-payment obligation",
                'generic or current "no income tax" label is not enough',
                "state may tax a non-income-tax base"):
            assert phrase in section
        assert "a no-income-tax state has nothing to do here" not in section

    def test_a_missed_instalment_produces_a_payment_now_not_a_bigger_voucher(self):
        """Penalty runs until the money arrives, not until the next due date.

        §6654(b)(2) closes an instalment's underpayment period on the earlier
        of the date it is PAID and the following April 15. A filer checking on
        July 1 having underpaid April and June therefore has principal
        outstanding today; folding it into the September voucher buys ten more
        weeks of accrual on it for nothing. The engine's CA schedule already
        carries this instruction — `_CA_VOUCHER_CATCHUP_NOTE`, "pay catch-up
        amounts NOW rather than at the printed dates" — and the timing check
        that discovers the shortfall is the natural place for its federal twin.
        """
        text = self._text()
        check = text.find("The timing check")
        assert check >= 0, "the timing check has been renamed or removed"
        window = text[check:check + 1200]
        assert "at today's date" in window, (
            "the timing check reports which past deadlines were missed but "
            "never re-runs the cumulative test at TODAY, so the amount "
            "outstanding right now is never computed")
        assert "pay NOW" in window, (
            "the outstanding amount is computed but not directed at an "
            "immediate payment. Rolled into the next scheduled voucher it "
            "keeps accruing until that date arrives")
        PROJ = _projection()
        assert "catch-up amounts NOW" in PROJ._CA_VOUCHER_CATCHUP_NOTE, (
            "the engine's CA note no longer gives the catch-up instruction "
            "this paragraph is the federal twin of — if the engine changed its "
            "mind about the remedy, the two must move together")

    def test_the_timing_check_runs_on_the_withholding_the_year_will_end_with(self):
        """Route A's own rule un-does the shortfall route A was prescribed for.

        §6654(g)(1) deems the year's withholding paid in equal parts on each
        instalment date whenever it was actually taken — the reason this skill
        prefers a W-4 change to a voucher, and the reason a December pay rise
        cures April. So the timing check cannot report April as missed off the
        PRE-adjustment withholding and then call it unfixable: raising
        withholding now raises what April is deemed to have been credited.
        Reporting an accrual that the recommendation two paragraphs up has
        already erased sends a catch-up payment after money that is not owed.
        An estimated payment is the opposite — dated when made — and that
        contrast is what the paragraph has to keep.
        """
        text = self._text()
        check = text.find("The timing check")
        assert check >= 0, "the timing check has been renamed or removed"
        window = text[check:check + 1200]
        assert "route A's addition included" in window, (
            "the timing check runs on the pre-adjustment withholding, so a "
            "spring shortfall that the recommended W-4 change erases is still "
            "reported as accrued and still collected as a catch-up")
        assert "erase a spring shortfall" in window, (
            "the paragraph no longer says WHY the recomputation matters — "
            "that ratable crediting reaches backwards — so a reader takes the "
            "instruction as bookkeeping and skips it")
        assert "never imply a September voucher cures April" in window, (
            "the estimated-payment contrast is gone: with withholding now "
            "described as retroactive, dropping it reads as though a late "
            "voucher cures an earlier instalment too")

    def test_owing_nothing_is_confirmed_against_line_24_before_it_is_a_zero(self):
        """"I owed nothing" is a sentence about the BALANCE DUE, usually.

        The taxpayer whose withholding covered a $15,000 liability says it, and
        so does the taxpayer who got a refund. Reading the phrase as figure 1
        at $0 hands both of them §6654(e)(2) and the answer "no penalty can
        attach this year at all" — for a prior year with a large line 24, which
        is no exception at all and leaves the whole year's instalments unpaid.
        The exception is real and the ordering fix that reaches it stands; what
        cannot stand is inferring its precondition from a colloquialism.
        AGENTS.md: never guess a value that changes a return.
        """
        text = self._text()
        stop = text.find("**Without figure 1, stop.**")
        exception = self._exception(text)
        assert 0 <= stop < exception, "the missing-figure-1 refusal has moved"
        window = text[stop:exception]
        assert "nor is it $0 until CONFIRMED" in window, (
            "the skill takes \"I owed nothing last year\" as figure 1 at $0. "
            "Said of a refund or a zero balance it means withholding COVERED "
            "the tax — line 24 was large, and the no-penalty exception does "
            "not apply")
        assert "withholding COVERED the tax" in window, (
            "the paragraph asks for confirmation without saying what the "
            "phrase usually means, which is the whole reason to confirm it")
        bullet = text.find("If figure 1 comes out at $0")
        assert 0 <= bullet < exception, "the no-liability bullet has been reworded"
        assert "never taken from a phrase" in text[bullet:exception], (
            "the §6654(e)(2) bullet does not say its $0 is the COMPUTED figure "
            "1 — read as the user's own words it is the same inference one "
            "paragraph further down")


class TestTheEngineNotesPointAtRealSkillSteps:
    """Two engine notes send the reader to a NUMBERED step of another skill.

    `_ES_VOUCHER_CATCHUP_NOTE` ends "(/tax-safe-harbor step 4)" and its CA twin
    "(/tax-safe-harbor step 5)", and step 0's only carve-out from its own
    hand-off gate keys on the same numbering — "a reader sent here by a voucher
    catch-up note wants step 4's cumulative test — step 5 in California". Renumber
    or insert one step in a 500-line skill and both notes point at the wrong
    procedure while the carve-out still names the old one, bouncing the reader
    back to the very table the note told them to distrust. Every other two-copy
    relation on this branch is pinned by test; this one was not.
    """

    SKILL = SKILLS_DIR / "tax-safe-harbor" / "SKILL.md"
    REF = re.compile(r"/tax-safe-harbor step (\d+)")

    def _headings(self):
        return {int(m.group(1)): m.group(2).strip() for m in
                re.finditer(r"^## Step (\d+) — (.+)$",
                            self.SKILL.read_text(), re.M)}

    def test_every_step_an_engine_module_names_is_a_real_heading(self):
        headings = self._headings()
        assert headings, "the skill's step headings stopped matching '## Step N — ...'"
        found = []
        for src in engine_files():
            rel = engine_relpath(src)
            for n in self.REF.findall(src.read_text()):
                found.append((rel, int(n)))
                assert int(n) in headings, (
                    f"{rel} sends the reader to /tax-safe-harbor step {n}, "
                    f"which is not a heading in the skill (it has "
                    f"{sorted(headings)}) — the note points at the wrong "
                    f"procedure and nothing else would say so")
        assert found, (
            "no engine module references a /tax-safe-harbor step any more; if "
            "the catch-up notes dropped their pointers this test is vacuous")

    def test_the_two_catchup_notes_point_at_the_cumulative_test(self):
        """Resolving to SOME heading is not enough — it must be the right one."""
        headings = self._headings()
        PROJ = _projection()
        fed = int(self.REF.search(PROJ._ES_VOUCHER_CATCHUP_NOTE).group(1))
        ca = int(self.REF.search(PROJ._CA_CATCHUP.text).group(1))
        assert "close it" in headings[fed], (
            f"the federal catch-up note points at step {fed} "
            f"({headings[fed]!r}), which is no longer the step holding the "
            f"cumulative test the note tells the reader to re-check against")
        assert "California" in headings[ca], (
            f"the CA catch-up note points at step {ca} ({headings[ca]!r}), "
            f"which is no longer the California step")
        assert fed != ca, (
            "both notes now point at the same step — the CA reader needs the "
            "weighted schedule, not the federal quarters")

    def test_step_zero_carve_out_names_the_same_two_steps(self):
        """The carve-out lets a note-sent reader skip the hand-off gate. If it
        names different steps than the notes do, that reader is bounced."""
        PROJ = _projection()
        body = self.SKILL.read_text()
        gate = body.split("## Step 1", 1)[0]
        fed = self.REF.search(PROJ._ES_VOUCHER_CATCHUP_NOTE).group(1)
        ca = self.REF.search(PROJ._CA_CATCHUP.text).group(1)
        assert f"step {fed}'s cumulative test" in gate, (
            f"step 0's carve-out does not name step {fed}, the step the "
            f"federal catch-up note sends its reader to")
        assert f"step {ca} in California" in gate, (
            f"step 0's carve-out does not name step {ca}, the step the CA "
            f"catch-up note sends its reader to")


class TestTheSkillsReadWhatTheEngineEmits:
    """A field added to `--json` that no skill reads reaches no reader.

    Both consuming skills summarize from `--json` before they render, which is
    why the catch-up notes were moved into `build_report` at all. The engine
    half shipped and the skill half did not: `de_minimis` made
    `safe_harbor.covered` true against a real balance owed, every voucher
    printed $0.00, and the skills' audit line still read
    "Safe-harbor: {covered/underpaid}" with no instruction to look further.
    """

    CONSUMERS = ("tax-project", "tax-withholding")
    HARBOR_FIELDS = ("de_minimis", "de_minimis_margin",
                     "de_minimis_cliff_more_tax",
                     "de_minimis_cliff_less_withholding",
                     "degraded", "current_tax_unnetted")
    REPORT_FIELDS = ("voucher_catchup_note", "ca_voucher_catchup_note",
                     "voucher_dates_note", "ca_voucher_dates_note",
                     "w4_lever_note")
    # The substring both skills must carry, whatever the CA field is spelled
    # as on each side — /tax-project reads `ca_voucher_dates_note` off the
    # report, /tax-withholding reads `ca.voucher_dates_note` off the nested
    # block, and a check keyed on either spelling passes on one skill and
    # fails on the other for no reason a reader could act on.
    # Which module builds the payload each skill is told to read.
    PAYLOAD_BUILDER = {"tax-project": "projection",
                       "tax-withholding": "withholding"}

    def _body(self, name):
        return (SKILLS_DIR / name / "SKILL.md").read_text()

    # Payload keys that are FIGURES — numbers and structures the agent reads
    # and reports directly. Everything else the builder emits is a DISCLOSURE
    # and must carry its own bullet.
    #
    # Default-DENY, which is the half a suffix rule cannot do. Keying on
    # `_note`/`_warning`/`_mismatch` covered the two note-shaped reasons
    # `state` comes back null and silently missed the flag-shaped third —
    # `state_no_income_tax` / `no_income_tax_state_code`, which is also the
    # only one of the three that means nothing is owed. A key added to either
    # payload now fails this test until it is classified here or documented.
    PAYLOAD_FIGURES = frozenset({
        # Run identity and inputs
        "year", "as_of", "pay_frequency", "liability", "inputs",
        # Covered by the HARBOR_FIELDS and baseline tests, not by a bullet
        # naming the bare key: the skills spell these `safe_harbor.degraded`.
        "baseline_source", "degraded", "has_prior_archive",
        # Computed blocks and the rows inside them
        "proj", "federal", "state", "safe_harbor", "vouchers",
        "w4_penalty", "w4_zero", "ca_safe_harbor", "ca_vouchers", "ca",
        "penalty_target", "zero_target", "vouchers_penalty", "vouchers_zero",
        "ytd_withholding", "projected_full_year_withholding", "estimates_paid",
        "projected_paid", "zero_shortfall", "de4_penalty", "de4_zero",
        # ...and the New York block's own rows. `ny.notes` and
        # `ny_cache_missing` are deliberately NOT here: the first is what the
        # engine SAID rather than assumed and the second is a missing input
        # that must not read as a finding, so both are disclosures and both
        # need a bullet.
        "ny", "nyagi", "required_annual_payment", "options", "shortfall",
        "covered", "it2104_penalty", "it2104_zero",
        "nc",
    })

    def _payload_keys(self, name):
        """Every key the skill's own `_report_to_dict` emits, in order."""
        import importlib
        import inspect as _inspect
        mod = importlib.import_module(
            f"agents.lib.{self.PAYLOAD_BUILDER[name]}")
        src = _inspect.getsource(mod._report_to_dict)
        keys = []
        for key in re.findall(r'"([a-z_0-9]+)":', src):
            if key not in keys:
                keys.append(key)
        assert keys, f"no payload keys found in {name}'s builder"
        return keys

    def _disclosure_fields(self, name):
        """The payload keys that must be documented — everything not a figure."""
        return [k for k in self._payload_keys(name)
                if k not in self.PAYLOAD_FIGURES]

    def _bullet_tokens(self, name):
        """Every `backticked` token in a payload-bullet HEADER.

        Header only, and matched EXACTLY. A mention inside another bullet's
        prose is not documentation — the reader is working down a numbered
        list — and matching on a stripped stem let the two halves of a paired
        field satisfy each other: with `ca_` folded away, deleting either
        `voucher_catchup_note` or `ca_voucher_catchup_note` from its bullet
        left the check green on the survivor.
        """
        tokens = set()
        for line in self._body(name).split("\n"):
            if re.match(r"^\s*- \*\*", line):
                tokens |= set(re.findall(r"`([A-Za-z0-9_.]+)`", line))
        return tokens

    def _top_level_payload_keys(self, name):
        """The keys of the payload's OUTERMOST dict, by AST.

        `_payload_keys` regexes the whole function, so it cannot tell a
        top-level field from one nested inside a state block. This can, and
        that distinction is what keeps the rule below from re-opening the hole
        it was written to close.
        """
        import ast
        import importlib
        import inspect as _inspect
        mod = importlib.import_module(
            f"agents.lib.{self.PAYLOAD_BUILDER[name]}")
        tree = ast.parse(textwrap.dedent(_inspect.getsource(mod._report_to_dict)))
        fn = tree.body[0]
        for node in fn.body:
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
                return {k.value for k in node.value.keys
                        if isinstance(k, ast.Constant)}
        raise AssertionError(f"{name}'s payload builder has no return dict")

    def _spellings(self, name, key):
        """The forms a skill may legitimately use for `key`.

        /tax-project reads `ca_voucher_dates_note` off the report and
        /tax-withholding reads `ca.voucher_dates_note` off a nested block —
        two spellings of one field, and a check keyed on either alone fails
        one skill for no reason a reader could act on. Both are accepted;
        neither is accepted for the FEDERAL half of the pair.

        A key that exists ONLY inside a state block — `ny.notes` is the first —
        is read at its dotted path and nowhere else, so the dotted path is the
        only honest bullet header for it. The gate on "only": accepting a
        dotted spelling for a key that ALSO sits at the top level would let the
        state bullet satisfy the federal field's check, which is exactly the
        collapse the pairing rule above exists to prevent.
        """
        forms = {key}
        if key.startswith("ca_"):
            forms.add("ca." + key[3:])
        if key not in self._top_level_payload_keys(name):
            forms |= {f"{block}.{key}" for block in ("ca", "ny")}
        return forms

    def test_both_skills_name_every_harbor_disclosure_field(self):
        for name in self.CONSUMERS:
            body = self._body(name)
            for f in self.HARBOR_FIELDS:
                assert f in body, (
                    f"/{name} never mentions `{f}`, so an agent summarizing "
                    f"the payload cannot know it is there — which is how a "
                    f"$999 balance came to be reported as 'covered'")

    def test_a_zero_cliff_is_not_attributed_to_the_harbor_test_alone(self):
        """0.00 has two possible reasons and the skills gave one.

        The field says nothing attaches on that path. Both skills explained it
        as "the 90%/prior-year test covers the year on its own" — true until
        R&TC §19136(c)(2)'s prior-year floor became a second, independent
        reason for the same 0.00, at which point an agent following the
        sentence reports a harbor that is met on a run where it is not. Same
        shape as `_CLIFF_NOTHING_ATTACHES` in the engine, whose tail had to be
        split for exactly this: the CONCLUSION stayed true and the REASON
        stopped being.

        Asserted on the bullet that makes the claim, not on the file, so a
        second bullet elsewhere cannot satisfy it for the one that misleads.
        """
        for name in self.CONSUMERS:
            blocks = re.split(r"\n(?=\s*- \*\*)", self._body(name))
            owning = [b for b in blocks if "0.00 means nothing" in b]
            assert len(owning) == 1, (
                f"/{name} no longer has exactly one bullet explaining a 0.00 "
                f"cliff field; this test can no longer find what it pins")
            bullet = owning[0]
            assert "90%/prior-year test covers the year on its own" not in bullet, (
                f"/{name} still gives one reason for a 0.00 cliff. A CA "
                f"prior-year Form 540 total under $500 zeroes these fields "
                f"too, with the required annual payment NOT met on its own")
            assert "separate exemption" in bullet
            assert "PRIOR-year $500 floor" in bullet, (
                f"/{name} names a second reason without naming which one, so "
                f"the agent has nothing to check the payload against")
            assert "status cell" in bullet, (
                f"/{name} leaves the agent to guess which reason applies — "
                f"the cell and the CA notes are where the engine says so")

    def test_both_skills_state_the_resolvers_as_of_and_action_date_contract(self):
        for name in self.CONSUMERS:
            body = self._body(name)
            assert "valid stored `as_of_date` is preserved in every year" in body
            assert "malformed stored value in a closed year is omitted" in body
            assert "real date" in body and "remaining paychecks" in body

    def test_both_skills_name_the_catchup_notes_they_have_to_surface(self):
        for name in self.CONSUMERS:
            body = self._body(name)
            assert "voucher_catchup_note" in body, (
                f"/{name} does not tell the agent to surface the catch-up "
                f"note; it was moved into the payload FOR these two skills")

    def test_every_numbered_step_starts_its_own_block(self):
        """A numbered step needs a blank line above it or it is not a step.

        Under CommonMark an ordered-list item cannot interrupt a paragraph, so
        a `6.` line butted against the previous bullet's last line parses as a
        continuation of that bullet rather than as the next step. A scripted
        insertion into the payload list put the separating blank line on the
        wrong side of the new bullet and did exactly that — invisible in a
        diff, and the kind of thing only a reader rendering the file would
        notice.
        """
        for name in self.CONSUMERS:
            lines = self._body(name).split("\n")
            for i, line in enumerate(lines):
                if i and re.match(r"^\d+\. \*\*", line) and lines[i - 1].strip():
                    raise AssertionError(
                        f"/{name} line {i + 1}: step {line[:40]!r} has no blank "
                        f"line above it, so it renders as part of "
                        f"{lines[i - 1].strip()[:50]!r} instead of as a step")

    def test_both_skills_name_every_note_the_payload_carries(self):
        """The catch-up note is about the AMOUNT on a row; the calendar note
        about whether the DATE beside it is still ahead; the lever note about
        whether the W-4 route exists at all. Independent facts, one field
        each, and a skill that names some of them summarizes a report whose
        other corrections the reader never sees.

        DERIVED from the payload builder, not listed here, and default-DENY:
        every key the builder emits is a disclosure unless `PAYLOAD_FIGURES`
        classifies it as a figure. The hand-kept roster this replaces named
        three substrings and covered five of ten fields; the suffix rule that
        replaced THAT covered the note-shaped fields and missed the
        flag-shaped `state_no_income_tax`. A roster maintained beside the
        thing it checks drifts from it, and a rule keyed on a naming
        convention only sees the names that follow it.

        Each field needs its own BULLET HEADER, matched exactly. The first
        version was satisfied by any occurrence anywhere, so a passing
        reference in another bullet's prose counted — deleting
        `stale_as_of_note`'s entire bullet stayed green on one incidental
        mention. The second folded `ca_` away, so the two halves of a paired
        field satisfied each other and either could be deleted.
        """
        for name in self.CONSUMERS:
            tokens = self._bullet_tokens(name)
            for f in self._disclosure_fields(name):
                assert self._spellings(name, f) & tokens, (
                    f"/{name} has no payload bullet for `{f}`, so an agent "
                    f"summarizing the payload has no instruction to surface "
                    f"it. A mention inside another bullet's prose is not "
                    f"enough — the reader is working down a numbered list. "
                    f"If this key is a figure rather than a disclosure, say "
                    f"so in PAYLOAD_FIGURES.")

    def test_neither_skill_reports_the_harbor_as_a_bare_two_way_verdict(self):
        for name in self.CONSUMERS:
            body = self._body(name)
            assert "{covered/underpaid}" not in body, (
                f"/{name}'s audit line is still a two-way covered/underpaid "
                f"template — under a de minimis 'covered' is true and tax is "
                f"still owed, and that line is the persisted record of the run")
            assert "de minimis" in body, (
                f"/{name} offers no third verdict for the de-minimis case")

    def test_neither_skill_reads_the_de_minimis_flag_as_a_balance(self):
        """The flag is a subtraction, and a subtraction can come out NEGATIVE.

        §6654(e)(1) tests tax MINUS withholding against $1,000, so it is set
        just as readily by withholding that covers the whole liability as by a
        $999 balance. Both skills read it as "penalty excused, tax still owed
        at filing" in every case: $5,000 of tax against $6,000 withheld — a
        refund — was to be reported as $X still due in April, and the audit
        line persisted that reading into `answers.md`. Asking for money the
        taxpayer does not owe is the OVER-stating direction.

        The flag says nothing about the balance. The balance field does, and
        each report has its own: /tax-project projects one, /tax-withholding
        derives the zero-balance target from the computed liability.
        """
        PROJ = _projection()
        sh = PROJ.compute_safe_harbor(5000.0, None, None, "SINGLE",
                                      withholding=6000.0, estimates_paid=0.0)
        assert sh.de_minimis, (
            "the fixture no longer sets the de minimis against withholding "
            "that exceeds the tax, which is the case both skills got wrong")
        assert "balance" in {f.name for f in
                             dataclasses.fields(PROJ.FederalProjection)}, (
            "/tax-project is instructed to read `federal.balance`, which is "
            "no longer a field of FederalProjection")
        assert "zero_target" in {f.name for f in
                                 dataclasses.fields(_withholding().TrueUpReport)}, (
            "/tax-withholding is instructed to read `zero_target`, which is "
            "no longer a field of TrueUpReport")
        for name, decider in (("tax-project", "`federal.balance` decides"),
                              ("tax-withholding", "`zero_target` decides")):
            body = self._body(name)
            assert decider in body, (
                f"/{name} no longer names the field that decides whether tax "
                f"is due, leaving the de-minimis flag to stand in for it")
            assert "still owed at filing" not in body, (
                f"/{name} is back to reading the de-minimis flag as a balance "
                f"owed, which it is not")

    def test_every_field_the_skills_name_still_exists_on_the_engine(self):
        """The other direction: a renamed field leaves both skills instructing
        a read of something the payload no longer carries, silently."""
        PROJ = _projection()
        harbor = {f.name for f in dataclasses.fields(PROJ.SafeHarbor)}
        ca_harbor = {f.name for f in dataclasses.fields(PROJ.CaSafeHarbor)}
        report = {f.name for f in dataclasses.fields(PROJ.ProjectionReport)}
        for f in self.HARBOR_FIELDS:
            assert f in harbor and f in ca_harbor, (
                f"both skills instruct a read of `{f}`, which is no longer a "
                f"field of SafeHarbor/CaSafeHarbor — `vars()` is what builds "
                f"the payload, so the key simply vanishes")
        for f in self.REPORT_FIELDS:
            assert f in report, (
                f"both skills instruct a read of `{f}`, which is no longer a "
                f"field of ProjectionReport")

    def test_the_withholding_half_of_the_payload_is_resolved_too(self):
        """Two skills are named above; only one engine was being checked.

        `voucher_catchup_note` is a field of `TrueUpReport` and `CaTrueUp` as
        well, and /tax-withholding is instructed to read both. Resolving the
        skill-named fields against `projection` alone left that half free to
        be renamed — or dropped from `_report_to_dict` — while this class and
        `test_both_skills_name_the_catchup_notes_they_have_to_surface` stayed
        green and the skill went on naming a key nothing emits.
        """
        WH = _withholding()
        true_up = {f.name for f in dataclasses.fields(WH.TrueUpReport)}
        ca_true_up = {f.name for f in dataclasses.fields(WH.CaTrueUp)}
        assert "voucher_catchup_note" in true_up and (
            "voucher_catchup_note" in ca_true_up), (
            "/tax-withholding instructs a read of `voucher_catchup_note` and "
            "`ca.voucher_catchup_note`, which are no longer fields of "
            "TrueUpReport/CaTrueUp")
        assert "voucher_dates_note" in true_up and (
            "voucher_dates_note" in ca_true_up), (
            "/tax-withholding instructs a read of `voucher_dates_note` and "
            "`ca.voucher_dates_note`, which are no longer fields of "
            "TrueUpReport/CaTrueUp — and that note is the ONLY caveat the "
            "zero-balance column has, the catch-up one being scoped to the "
            "penalty column")
        assert "w4_lever_note" in true_up, (
            "/tax-withholding instructs a read of `w4_lever_note`, which is "
            "no longer a field of TrueUpReport")
        # The harbors reach this payload nested, so the disclosure fields have
        # to survive the report's own hand-built dict, not just `vars()`.
        assert {"safe_harbor", "ca"} <= true_up, (
            "the true-up no longer nests a harbor, so the disclosure fields "
            "this class is about cannot reach /tax-withholding at all")


# Words that turn an amount into a stated legal ceiling. Shared by every skill
# the lint covers, because the question each one is being asked is the same:
# was this figure written down as LAW, or narrated as an illustration?
_LIMIT_WORDS = (r"per child|per person|caps?|capped|limit(?:s|ed|ing|ations?)?|thresholds?|"
                r"maximum|max|minimum|min|floor|ceiling|exclusions?|up to")
# The multiplier is part of the figure. Without the suffix "$200K" tokenised as
# "$200", and a registry holding "$200" let "$200M" through while refusing the
# spelled-out "$200,000" — a registry describing how a figure was abbreviated.
_AMOUNT = r"\$[0-9][0-9,]*(?:\.[0-9]+)?(?:[KkMm]\b)?"
_AMOUNT_SEQUENCE = rf"{_AMOUNT}(?:\s*(?:/|or|and)\s*{_AMOUNT})*"
_LIMIT_PATTERN = re.compile(
    rf"\b(?:{_LIMIT_WORDS})\b[^.]{{0,40}}?({_AMOUNT_SEQUENCE})"
    rf"|({_AMOUNT_SEQUENCE})[^.]{{0,40}}?\b(?:{_LIMIT_WORDS})\b",
    re.I)


def _limit_paragraphs(body: str):
    """Normalize prose wraps without joining Markdown blocks or code tokens.

    Inline delimiters pair only with a run of the same length in one block.
    Code spans without money and CLI flags cannot supply prose limit words.
    Mask them at equal width so they cannot shorten the forty-character window;
    dollar amounts remain readable to the lint, including K/M suffixes.
    """
    body = re.sub(r"(?m)^(?=[ \t]*(?:\||#{1,6} |[-*+] |[0-9]+[.)] ))", "\n", body)
    body = re.sub(r"(?m)^([ \t]*\|.*)$", r"\1\n", body)
    body = re.sub(r"(?m)^([ \t]*#{1,6} .*)$", r"\1\n", body)
    for paragraph in re.split(r"\n[ \t]*\n", body):
        tokens = list(re.finditer(r"`+", paragraph))
        masked = list(paragraph)
        i = 0
        while i < len(tokens):
            opening = tokens[i]
            before = paragraph[:opening.start()]
            if (len(before) - len(before.rstrip("\\"))) % 2:
                # Markdown escapes an opening backtick, but backslashes do
                # not escape the closing delimiter inside a real code span.
                i += 1
                continue
            closing = next((j for j in range(i + 1, len(tokens))
                            if tokens[j].group() == opening.group()), None)
            if closing is None:
                i += 1
                continue
            start, end = opening.end(), tokens[closing].start()
            code = paragraph[start:end]
            if not re.search(_AMOUNT, code) or re.search(r"(?:^|\s)--[A-Za-z]", code):
                money_positions = {k for figure in re.finditer(_AMOUNT, code)
                                   for k in range(start + figure.start(), start + figure.end())}
                for k in range(start, end):
                    if not masked[k].isspace() and k not in money_positions:
                        masked[k] = "x"
            i = closing + 1
        yield (re.sub(r"[ \t\r\n]+", " ", "".join(masked)),
               re.sub(r"[ \t\r\n]+", " ", paragraph))


def _quoted_limits(body: str) -> "set[tuple[str, str]]":
    """Every dollar amount this text presents AS A LIMIT, with its context.

    Arithmetic-adjacency was the first rule tried and is the wrong one: worked
    examples narrate their figures in prose ("put $1,982.50 at EACH of those
    two dates"), and those examples are what make a procedure checkable. The
    limit WORDS are what turn an amount into a stated ceiling.
    """
    out = set()
    for masked, display in _limit_paragraphs(body):
        for match in _LIMIT_PATTERN.finditer(masked):
            # Every amount in a matched clause, including paired exclusions
            # or joint/separate thresholds. finditer alone consumed the second
            # amount while recording only the first one. Keep original prose
            # for diagnostics; masking preserves its normalized offsets.
            context = display[match.start():match.end()].strip()
            for figure in re.finditer(_AMOUNT, context):
                out.add((figure.group().rstrip(","), context[:70]))
    return out


class TestWrappedFigureLint:
    def amounts(self, body):
        return {amount for amount, _ in _quoted_limits(body)}

    def test_wraps_and_formatting_do_not_hide_a_limit(self):
        for body in ("minimum penalty floor is\n  ~$510", "$510\n maximum",
                     "**threshold**:\n `$510`", "up\n to $510", "cap\r\n$510"):
            assert self.amounts(body) == {"$510"}, body

    def test_every_paired_amount_is_checked_with_its_multiplier(self):
        assert self.amounts("$250K/$500K-MFJ exclusion") == {"$250K", "$500K"}
        assert self.amounts("exclusion $250K/\n$500K") == {"$250K", "$500K"}
        assert self.amounts("cap $510 or $600") == {"$510", "$600"}
        assert self.amounts("$150,000/$75,000 threshold") == {"$150,000", "$75,000"}
        assert self.amounts("ceiling $1.25M") == {"$1.25M"}

    def test_schema_identifiers_and_cli_flags_are_not_limit_words(self):
        for body in ("`{min, max, rate}`\n example income $12,345",
                     "`state.max` example $510", "`--min $0` to `--max`",
                     "``literal `max` token`` example $510"):
            assert self.amounts(body) == set(), body

    def test_inline_money_claims_and_unmatched_delimiters_remain_visible(self):
        assert self.amounts("`maximum $510`") == {"$510"}
        assert self.amounts(r"\`cap\` $510") == {"$510"}
        assert self.amounts("threshold\n `$510") == {"$510"}
        assert self.amounts("`min` then §121\n $250K/$500K exclusion") == {"$250K", "$500K"}

    def test_inline_code_can_wrap_and_preserves_the_distance(self):
        assert self.amounts("`min,\n max` example $510") == set()
        assert self.amounts("threshold `" + "x" * 45 + "` $510") == set()

    def test_unrelated_blocks_and_sentences_do_not_share_limit_words(self):
        for body in ("threshold\n\nExample $510", "threshold. Example $510",
                     "- threshold\n- Example $510", "| threshold |\n| Example $510 |",
                     "# threshold\n# Example $510", "# threshold\nExample $510"):
            assert self.amounts(body) == set(), body

    def test_word_boundaries_and_wrapped_distance_are_bounded(self):
        assert self.amounts("capabilities $510") == set()
        assert self.amounts("limit " + "x" * 41 + " $510") == set()
        assert self.amounts("limited\n to $510") == {"$510"}

    def test_corpus_retains_home_sale_exclusions_and_removes_cli_false_alarm(self):
        assert {"$250K", "$500K"} <= self.amounts(skill_text("tax-setup"))
        assert "$0" not in self.amounts(skill_text("tax-roth-convert"))

    def test_registered_skill_gate_rejects_the_original_wrapped_defect(self, monkeypatch):
        monkeypatch.setitem(globals(), "skill_text", lambda _: "minimum penalty floor\n ~$510")
        with pytest.raises(AssertionError, match="\\$510"):
            test_the_taxpayer_facing_skills_quote_no_indexed_tax_figure("tax-extend")

    def test_safe_harbor_gate_uses_the_same_wrapped_detector(self, monkeypatch, tmp_path):
        skill = tmp_path / "tax-safe-harbor"
        skill.mkdir()
        (skill / "SKILL.md").write_text("maximum refundable cap\n $510", encoding="utf-8")
        monkeypatch.setitem(globals(), "SKILLS_DIR", tmp_path)
        with pytest.raises(AssertionError, match="\\$510"):
            test_the_safe_harbor_skill_quotes_no_indexed_tax_figure()


# Per skill, the dollar figures it may state as a limit — every one STATUTORY
# and UNINDEXED, so it cannot go stale the way a fetched figure can.
#
# X5's premise held, and it was worse than X5 said. `tax-review` told the agent
# the SALT cap was $10,000, three times, while `ptet._salt_cap` had been
# returning OBBBA's $40,000 since PR #22 — a skill instructing a reviewer to
# ask a taxpayer about a cap thirty thousand dollars below the real one. The
# other offenders were stale-in-waiting: an ACTC cap dated TY2024/2025, an
# adoption credit at TY2025's figure, a kiddie-tax threshold, an educator
# deduction, a net-investment-income cap "(~$11,600 TY2024)".
#
# NOT tax-compute, and that is a named gap rather than an oversight: it quotes
# eighteen, most of them beside the cache key they are fetched from, and each
# needs its own judgement about whether the figure is instruction or
# illustration. It is the next slice, and until it lands the lint here does not
# cover the skill that computes.
_FIGURE_REGISTRY = {
    # §121's home-sale exclusion. $250,000/$500,000 since the Taxpayer Relief
    # Act of 1997 and never indexed.
    "tax-setup": {"$250K", "$500K", "$250,000", "$500,000"},
    # Nothing. Both thresholds it used to quote were about how many FORMS to
    # expect, and the instruction — ask about platform and gig income whatever
    # arrived — is the same at any figure, so the figures went rather than
    # being registered.
    "tax-parse": set(),
    # §1211(b)'s $3,000 capital-loss cap and §221(b)(1)'s $2,500 student-loan
    # interest cap. Both flat statutory amounts; §221's INDEXING applies to the
    # phase-out range, not to the cap.
    "tax-review": {"$3,000", "$2,500"},
    # The skill that COMPUTES, added in the slice X5's own row named as owed.
    # Eighteen figures were quoted; what survives is flat statutory law:
    #   $2,500  AOTC ceiling, §25A(i) — and §221(b)(1)'s student-loan cap
    #   $1,000  the AOTC's refundable 40%, which is 40% of a flat $2,500
    #   $2,000  Lifetime Learning Credit, §25A(c)
    #   $3,000  capital-loss cap, §1211(b)
    #   $150,000  the §6654(d)(1)(C) prior-year AGI threshold
    #   $1M / $500K  CALIFORNIA's pre-TCJA mortgage limit, halved for MFS
    #   $0  not a figure that can go stale
    #
    # What went: the SALT cap (indexed, and the exact figure `/tax-review`
    # carried wrong), the educator deduction, the adoption exclusion and its
    # OBBBA refundable portion, the senior deduction, and the 25C/25D/30D/25E
    # energy and vehicle caps — those last four replaced by an instruction to
    # confirm AVAILABILITY as well as amount, because OBBBA moved several of
    # their end dates and a credit claimed after termination is disallowed on
    # examination rather than merely mis-sized.
    "tax-compute": {"$0", "$1M", "$500K", "$1,000", "$2,000", "$2,500",
                    "$3,000", "$150,000", "$75,000"},
    # X6 brought the rest in. Every figure below is statutory and unindexed,
    # and the four that are not figures at all — `$0` outcomes — are here
    # because the detector counts them and nothing about a zero can go stale.
    #
    #   $3K       §1211(b)'s cap, as the skill abbreviates it
    #   $150,000  §6654(d)(1)(C)'s prior-year AGI threshold
    #   $200K     §3102(f)'s employer withholding threshold
    "tax-harvest": {"$0", "$3K"},
    # Both §6654(d)(1)(C) statuses and the federal/CA de-minimis floors,
    # already stated in these skills and pinned to the engine by other tests.
    "tax-project": {"$150,000", "$75,000", "$1,000", "$500"},
    "tax-retirement": {"$0"},
    "tax-roth-convert": set(),
    "tax-verify": {"$200K"},
    # Q10, ruled 2026-09-13. §164(b)(7) is a statutory STEP SCHEDULE, not an
    # indexed figure — the statute NAMES $40,000 and $40,400 and gives a formula
    # for the rest, and no inflation index appears in the section. That puts it
    # in the `tax-safe-harbor` class: statutory, so it may be STATED, and pinned
    # to the engine constant by the test below. AGENTS.md §2's third enumerated
    # exception. The figures here are the pre-OBBBA cap and the phase-down MAGI
    # threshold. `$40K` left with the last sentence that abbreviated the cap as
    # a flat figure across TY2025-2029, and an allowance nothing uses is one
    # the next flat figure would pass under.
    "tax-ptet": {"$10,000", "$500,000"},
    "tax-withholding": {"$150,000", "$75,000", "$1,000", "$500"},
    # Empty, and each verified empty rather than assumed. An empty entry is not
    # a formality: it is what puts the skill UNDER the lint, so the first
    # dollar figure any of them grows has to be defended. `tax-extend` is the
    # case that proves it — X6 removed a two-cycles-stale §6651(a) floor from
    # it, and nothing would have stopped the next one going back in.
    "tax-amend": set(),
    "tax-archive": set(),
    "tax-calendar": set(),
    "tax-extend": set(),
    "tax-fbar": set(),
    "tax-generate": set(),
    "tax-parse-crypto": set(),
    "tax-rollover": set(),
    "tax-scenario": set(),
}

# Taxpayer-facing skills the registry deliberately does NOT cover, each with
# the reason. An entry here is a decision; absence from both this and the
# registry is what `test_every_taxpayer_facing_skill_is_triaged` refuses.
_FIGURE_LINT_EXEMPT = {
    # AGENTS.md §2's second and last exception: `tax-safe-harbor` restates
    # §6654 in prose because it has no cache to read. Its figures are held to a
    # CLOSED `allowed` set by the test below this one, which is stricter than
    # this registry, not looser.
    "tax-safe-harbor": "has its own closed `allowed` set (AGENTS.md §2)",
}


@pytest.mark.parametrize("skill", sorted(_FIGURE_REGISTRY))
def test_the_taxpayer_facing_skills_quote_no_indexed_tax_figure(skill):
    """AGENTS.md §2's rule reaches the skills that ASK, not just the one that
    restates §6654 (X5).

    A figure quoted in a skill has no cache behind it and no freshness check
    over it. It goes stale silently while still reading as current — and unlike
    a stale cache, nothing downstream can notice, because the figure never
    becomes a value the engine compares against anything. It is read by an
    agent, spoken to a taxpayer, and gone.
    """
    offenders = {pair for pair in _quoted_limits(skill_text(skill))
                 if pair[0] not in _FIGURE_REGISTRY[skill]}
    assert not offenders, (
        f"{skill} states a dollar amount as a LIMIT that is not one of the "
        f"unindexed statutory figures its registry holds: {sorted(offenders)}. "
        "Name the cache key it is fetched from, or the form whose instructions "
        "carry it — an indexed figure in a skill goes stale while still "
        "reading as current, and a taxpayer hears it as this year's.")


def test_every_taxpayer_facing_skill_is_triaged():
    """The registry's SCOPE, derived rather than listed.

    X6 found two wrong figures in two skills, and the lint could not have caught
    either: it named four skills by hand out of twenty-three, and a skill that
    was simply never added read exactly like a skill with nothing to declare.
    A hand-written scope is the same silent-green as a hand-written arm.

    So every taxpayer-facing skill appears in ONE of the two — the registry, or
    the exemption list with its reason. A new skill fails here until somebody
    looks at its figures, which is the point: absence stops being an answer.

    Contributor tooling is out because it instructs a maintainer running from a
    clone, not a taxpayer, and `CONTRIBUTOR_SKILLS` is where that judgement
    already lives.
    """
    facing = set(skill_names()) - CONTRIBUTOR_SKILLS
    assert facing, "no taxpayer-facing skills found — the derivation broke"
    both = sorted(set(_FIGURE_REGISTRY) & set(_FIGURE_LINT_EXEMPT))
    assert not both, (
        f"{both} are registered AND exempt — an exemption beside a registry "
        "entry is the entry nobody has to keep")
    triaged = set(_FIGURE_REGISTRY) | set(_FIGURE_LINT_EXEMPT)
    missing = sorted(facing - triaged)
    assert not missing, (
        f"{missing} are taxpayer-facing and appear in neither _FIGURE_REGISTRY "
        "nor _FIGURE_LINT_EXEMPT. A skill nothing has looked at reads exactly "
        "like a skill with nothing to declare — which is how /tax-ptet came to "
        "state a SALT cap wrong for the current filing year. Register the "
        "figures it quotes, or exempt it and say why.")
    stale = sorted(triaged - facing)
    assert not stale, (
        f"{stale} are registered or exempted and are not taxpayer-facing "
        "skills any more — an entry that outlives its skill excuses whatever "
        "later takes the name")


# Where an exemption's decision may be recorded. A reason that names none of
# these is an opinion.
_EXEMPTION_POINTERS = frozenset({"AGENTS.md", "runbook", "docs/"})


def test_the_exemptions_each_carry_a_reason():
    """An exemption is a decision, and a decision is recorded somewhere.

    A length check was the first draft and answers the wrong question: thirty
    spaces passed it and a short true reason failed it. What a reason has to
    carry is WHERE the decision lives — the instruction file, the runbook, a
    doc — so a reader can find the ruling rather than take the word for it.
    """
    assert _FIGURE_LINT_EXEMPT, "the exemption list is empty — read it again"
    for skill, reason in sorted(_FIGURE_LINT_EXEMPT.items()):
        assert any(where in reason for where in _EXEMPTION_POINTERS), (
            f"{skill}'s exemption names no place the decision is recorded "
            f"({sorted(_EXEMPTION_POINTERS)}): {reason!r}")
    assert "tax-safe-harbor" in _FIGURE_LINT_EXEMPT, (
        "the one skill AGENTS.md §2 names as restating statute in prose is no "
        "longer exempt — it has a CLOSED allowed set instead, which is "
        "stricter than this registry and must not be replaced by it")


class TestTheSaltPhasedownHandoffs:
    """Where AA16's modified AGI and cap pass from the return to the PTET
    analysis. Each pin is a claim a review found false or a handoff it found
    open, and each is prose or a placeholder that nothing computes against.
    """

    def test_the_result_template_leaves_the_planning_figures_unset(self):
        """The engine writes None for each where it has none, and the planning
        tools read `schedule_a.modified_agi` and `medical_expenses` back. A zero placeholder nobody
        overwrote reads as a real figure: a MAGI of zero, so the PTET block
        reports the phasedown as not biting beside a return that cut the cap,
        or "Salt Cap Applied $0.00" beside a nonzero SALT deduction on the
        summary PDF. Null is absent (`pdf_render._display`)."""
        template = json.loads((REPO / "agents" / "templates" /
                               "result_template.json").read_text())
        for field in ("modified_agi", "salt_cap_applied", "medical_expenses"):
            assert template["schedule_a"][field] is None, (
                f"schedule_a.{field} has a numeric placeholder again; an "
                "unfilled one reads as a real figure")

    def test_the_final_election_is_written_back(self):
        """`/tax-ptet` and the planning caveat read `schedule_a.use_itemized`,
        and the compute step's by-hand §170(p) tilt could change the election
        without it: an itemizer on record who took the standard deduction.
        Since AA23 `compute_schedule_a` weighs §170(p) itself, from
        `non_itemizer_charitable`, so the step must pass it and write the
        function's own election back."""
        flat = re.sub(r"\s+", " ", skill_text("tax-compute"))
        assert ("Record `use_itemized` as `schedule_a.use_itemized`"
                in flat), (
            "the compute step no longer writes the final election back to "
            "schedule_a.use_itemized")
        assert "as `non_itemizer_charitable`" in flat, (
            "the compute step no longer passes §170(p) to compute_schedule_a, "
            "whose election then compares the bare figures")

    def test_magi_adds_back_what_the_irs_worksheets_add(self):
        """Schedule A's line 5e worksheet, Schedule 1-A Part I and Pub 970's
        education-credit worksheets all add back Form 2555 lines 45 AND 50,
        Form 4563 line 15 and excluded Puerto Rico income. The steps said "AGI
        + §911/§931/§933 exclusions", and line 50 is the foreign housing
        DEDUCTION: left out, MAGI reads low and the SALT cap high, which
        over-states a filed return's deduction. They also gave Guam and the
        Northern Marianas a §931 exclusion, which no IRS worksheet has — a
        bona fide resident of either files with the territory (§935)."""
        refs = REPO / ".agents" / "skills" / "tax-compute" / "references"
        agi = re.sub(r"\s+", " ", (refs / "income-and-agi.md").read_text())
        credits = re.sub(r"\s+", " ", (refs / "tax-and-credits.md").read_text())
        # Schedule 1-A's MAGI and Schedule A's, which says it is the same one.
        assert agi.count("lines 45 and 50") >= 2, (
            "a MAGI step in income-and-agi.md no longer adds back Form 2555 "
            "line 50, the foreign housing deduction")
        assert "lines 45 and 50" in credits, (
            "the education credits' MAGI no longer adds back Form 2555 line 50")
        for text in (agi, credits):
            assert "Guam" not in text and "Northern Marianas" not in text, (
                "a MAGI step gives Guam or the Northern Marianas a §931 "
                "exclusion again")

    def test_the_review_prices_ptet_headroom_from_other_salt(self):
        """The review asked the taxpayer whether their SALT was "at the cap"
        and passed `--salt-room`, which overrides the cap the engine prices
        from `--magi`, so the answer, not the return, set the headroom. The
        return's own `salt_before_cap` is no substitute: it holds the state
        tax on the pass-through income when the entity has not elected, which
        shrinks the headroom and over-states the benefit."""
        flat = re.sub(r"\s+", " ", skill_text("tax-review"))
        assert "[--other-salt <amount>]" in flat, (
            "/tax-review's PTET command no longer passes --other-salt")
        assert "[--salt-room" not in flat, (
            "/tax-review's PTET command passes --salt-room again, which "
            "overrides the cap the engine priced")
        assert ("Never pass `schedule_a.salt_before_cap` as `--other-salt`"
                in flat), (
            "the warning against the return's salt_before_cap is gone, and it "
            "is the figure a reader reaches for first")

    def test_tax_ptet_describes_the_no_magi_path_the_code_takes(self):
        """The skill said `--other-salt` with no `--magi` REFUSES; the code
        degrades with a banner (`test_ptet`'s
        `test_deriving_headroom_without_magi_degrades_with_the_direction_named`).
        A reader who believes it refuses takes a clean run as proof the
        phasedown was priced."""
        flat = re.sub(r"\s+", " ", skill_text("tax-ptet"))
        assert "REFUSES rather than pricing" not in flat, (
            "/tax-ptet claims a missing --magi refuses again; it degrades")
        assert "the headroom is a ceiling and the benefit a floor" in flat, (
            "/tax-ptet no longer says what a missing --magi does to the output")


class TestTheTwoSkillsTheFigureRegistryDoesNotCover:
    """X6's re-verification found both of these, and the lint above found
    neither — `tax-extend` and `tax-ptet` are not in `_FIGURE_REGISTRY`, and
    its detector cannot see a figure that markdown wrapped onto the next line.
    Widening it is its own slice (the reasoning and the measurements are in
    `docs/ops/season-open-runbook.md`); until then these two claims are pinned
    by hand, because both were WRONG in the direction a taxpayer acts on.
    """

    def test_tax_extend_states_no_dollar_floor_and_maps_the_rev_procs_right(self):
        """A Revenue Procedure states the §6651(a) floor for the year AFTER its
        own tax year. The note shifted both by one, which sends a TY2025 return
        to the document for TY2026 — and quoted "~$510", the figure for returns
        filed in 2025, one sentence before telling the reader not to reuse a
        prior year's figure."""
        body = skill_text("tax-extend")
        flat = re.sub(r"\s+", " ", body)
        assert "~$510" not in flat and "$510" not in flat, (
            "/tax-extend quotes a §6651(a) floor again. It indexes every year; "
            "the skill's own next sentence says to fetch it, and a figure in "
            "prose has no cache behind it and no freshness check over it")
        assert "2024-40 gives" in flat and "filed in **2026** ($525)" in flat, (
            "the Rev. Proc. 2024-40 -> 2026 mapping is gone or changed. Each "
            "Rev. Proc. names the year AFTER its own tax year, and the note "
            "that shifted both by one is what this pins against")
        assert "2025-32 gives" in flat and "filed in **2027** ($535)" in flat, (
            "the Rev. Proc. 2025-32 -> 2027 mapping is gone or changed")

    def test_tax_ptet_does_not_state_a_flat_salt_cap_for_a_year_RANGE(self):
        """§164(b)(7) steps the cap every year — $40,000 TY2025, $40,400 TY2026,
        then 101% a year through TY2029. A flat figure across the range is
        wrong from TY2026 on, and UNDER-stating the cap makes the election look
        better than it is: what PTET rescues is the state tax the cap blocks.
        """
        flat = re.sub(r"\s+", " ", skill_text("tax-ptet"))
        assert "$40,000 ($20,000 MFS) for TY2025–2029" not in flat, (
            "/tax-ptet states a FLAT SALT cap across TY2025-2029 again. It is "
            "$40,400 in TY2026 and steps every year after; under-stating it "
            "over-states the benefit of electing")
        assert "federal.salt_cap" in flat, (
            "/tax-ptet no longer names the cache key the cap is read from, so "
            "the figure in the prose is all a reader has")
        assert "$40,400 for TY2026" in flat, (
            "the TY2026 figure is the one the flat range got wrong, and it is "
            "the current filing year — name it")

    def test_the_ptet_skill_figures_match_the_engine_constants(self):
        """Q10's actual requirement: the skill may STATE §164(b)(7)'s amounts
        because the statute names them — and a stated figure earns that only by
        being pinned to the one place the engine keeps it.

        This is the `tax-safe-harbor` arrangement. Without it the skill's prose
        and the engine's rule are two copies of the same law, and three audits
        have now found that the copy which drifts is the PROSE, because nothing
        computes against it. The rule moved from `ptet.py` to
        `tax_computations/federal/salt.py` with AA16, when the return's own
        Schedule A began applying it, and the pin moved with it.
        """
        import importlib
        salt = importlib.import_module("agents.lib.tax_computations.federal.salt")
        flat = re.sub(r"\s+", " ", skill_text("tax-ptet"))
        for constant, spelling in (
            (salt.SALT_CAP_2025, "$40,000 for TY2025"),
            (salt.SALT_CAP_2026, "$40,400 for TY2026"),
        ):
            amount = f"${constant:,.0f}"
            assert spelling in flat, (
                f"/tax-ptet no longer states {spelling!r}; the engine holds "
                f"{amount} and a reader has nothing to check it against")
            assert amount in spelling, (
                f"the engine constant {amount} and the skill's {spelling!r} "
                "have diverged — that is the drift this test exists to catch")
        assert f"${salt.SALT_CAP_REVERSION:,.0f}" in flat, (
            "the TY2030 reversion is no longer stated, and it is the arm that "
            "tells a reader the schedule ENDS")
        assert "101%" in flat, (
            "the 101%-a-year step is gone from the skill; without it the two "
            "named years read as the whole schedule")
        # (B)'s phasedown rides the same exception, and since AA16 it decides
        # the filed return: no cache field overrides it, so these constants
        # ARE the figures and the skill's statement of them is pinned here.
        for spelling in (
            f"by {salt.SALT_PHASEDOWN_RATE:.0%} of MAGI over "
            f"${salt.SALT_MAGI_THRESHOLD_2025:,.0f}",
            f"${salt.SALT_MAGI_THRESHOLD_2026:,.0f} for TY2026",
            f"never below ${salt.SALT_PHASEDOWN_FLOOR:,.0f}",
        ):
            assert spelling in flat, (
                f"/tax-ptet no longer states {spelling!r} as the engine "
                "computes it — the phasedown's figures and the rule drifted")

    def test_the_salt_cap_subsection_is_cited_as_164_b_7(self):
        """OBBBA put the cap in §164(b)(7). (b)(6) is the superseded 2018-2025
        limitation, and a reader who follows it finds the OLD $10,000 rule and
        no step-up at all."""
        # The three doc trees that INSTRUCT — contributing, ops and roadmap —
        # and not `docs/changelog`, which is the record of what was done and
        # whose entries are dated statements rather than guidance. That
        # boundary is the hook's too: watching all of `docs` would put a
        # portability run behind every changelog entry, which
        # `test_an_unwatched_source_edit_stays_silent` exists to prevent.
        trees = (REPO / "docs" / "contributing", REPO / "docs" / "ops",
                 REPO / "docs" / "roadmap")
        for tree in trees:
            for path in sorted(tree.rglob("*.md")):
                text = path.read_text(encoding="utf-8")
                if "OBBBA SALT cap" not in text:
                    continue
                assert "164(b)(6)" not in text, (
                    f"{path.relative_to(REPO)} cites §164(b)(6) for the OBBBA "
                    "SALT cap — that subsection is the superseded 2018-2025 "
                    "limitation; the cap is (b)(7)")


def test_the_registry_is_not_a_place_to_put_an_indexed_figure():
    """The counterweight. `_FIGURE_REGISTRY` makes the lint satisfiable by
    adding a line, so the figures in it are named and pinned: every one is
    statutory and unindexed, and this is the sentence that has to be edited to
    claim otherwise."""
    assert _FIGURE_REGISTRY["tax-parse"] == set(), (
        "tax-parse quoted two REPORTING thresholds and needs neither: the "
        "instruction it gives — ask about platform and gig income whatever "
        "arrived — is the same at any figure")
    everything = set().union(*_FIGURE_REGISTRY.values())
    assert everything == {"$0", "$1M", "$3K", "$10,000", "$200K",
                          "$250K", "$250,000", "$500K", "$500,000",
                          "$1,000", "$2,000", "$2,500", "$3,000",
                          "$150,000", "$75,000", "$500"}, (
        "a figure joined the registry without this test being edited. Each one "
        "here is flat statutory law — §121's exclusion, §1211(b)'s capital-loss "
        "cap, §221(b)(1)'s student-loan "
        "cap, §25A's two education ceilings, §6654(d)(1)(C)'s thresholds, "
        "§6654(e)(1)'s floor and the CA estimated-tax floor, "
        "§3102(f)'s never-indexed $200,000 employer withholding threshold "
        "for Additional Medicare Tax, CA's pre-TCJA mortgage limit, and "
        "§164(b)(7)'s SALT schedule — and an "
        "INDEXED figure does not become safe by being listed")

    # §164(b)(7) is the one entry that is a SCHEDULE rather than a single flat
    # figure, and it is here on the strength of the statute NAMING each amount:
    # $40,000 for TY2025, $40,400 for TY2026, 101% of the prior year through
    # TY2029, $10,000 after. No inflation index appears in the section — which
    # is what separates it from a figure a Revenue Procedure publishes each
    # autumn, and is what the owner ruled on in Q10. `$10,000` is the pre-OBBBA
    # limitation the skill contrasts it with.
    assert _FIGURE_REGISTRY["tax-ptet"] == {"$10,000", "$500,000"}, (
        "the /tax-ptet entry changed. Its figures are §164(b)(7)'s, pinned to "
        "the engine constants by "
        "`test_the_ptet_skill_figures_match_the_engine_constants`")
    # X9 newly sees both halves of paired thresholds and the word "floor".
    # Their allowances must remain bound to the figures the engines use.
    TC, PROJ = _tax_computations(), _projection()
    for skill in ("tax-project", "tax-withholding"):
        for value in (TC.prior_year_agi_threshold(True), TC.DE_MINIMIS_BALANCE,
                      PROJ.CA_ES_DE_MINIMIS):
            assert f"${value:,.0f}" in _FIGURE_REGISTRY[skill]


def test_the_lint_would_still_catch_the_defect_it_was_written_for():
    """Non-vacuity, against the real one. `/tax-review` said "$10,000 SALT cap"
    three times while `ptet._salt_cap` returned $40,000 for TY2025-2029 — a
    reviewer told to ask a taxpayer whether a cap thirty thousand dollars below
    the real one was "already exhausted (usually yes)"."""
    caught = _quoted_limits("stranded by the $10,000 SALT cap into a deduction")
    assert caught, "the scanner no longer sees a figure beside the word `cap`"
    assert "$10,000" in {amount for amount, _ in caught}
    assert "$10,000" not in _FIGURE_REGISTRY["tax-review"]
    for name in _FIGURE_REGISTRY:
        body = skill_text(name)
        assert "$10,000 SALT" not in body and "$10K SALT" not in body


def test_the_safe_harbor_skill_quotes_no_indexed_tax_figure():
    """This skill restates law in PROSE and has no cache to go stale against.

AGENTS.md §2 permits that for statutory, UNINDEXED figures, and the
    `allowed` set below is the closed registry it defers to by name. It used to
    enumerate a handful in its own sentence, which read as the whole grant while
    this set had already outgrown it — §19136(c)(2)'s $500/$250 and §6017's $400
    are both here and were never in that sentence, so a reader checking the skill
    against §2 would have flagged two compliant figures and missed the point.
    An INDEXED figure quoted here goes stale silently while still reading as
    current: the refundable adoption-credit cap was written in as "$5,000 per
    child", which OBBBA indexes, and a stale cap mis-states the Form 2210
    line-8 prior-tax base.

    The test looks for a dollar amount presented AS A LIMIT — the words that
    make it law rather than illustration. Arithmetic-adjacency was the first
    rule tried and is the wrong one: worked examples narrate their figures in
    prose ("put $1,982.50 at EACH of those two dates"), and those examples are
    what make the procedure checkable.
    """
    body = (SKILLS_DIR / "tax-safe-harbor" / "SKILL.md").read_text()
    # The unindexed figures AGENTS.md §2 lets this skill state. THIS SET is the
    # registry and §2 now says so in as many words, which is the half that was
    # missing: while §2 carried its own five-figure enumeration, the two
    # documents were a list and a longer list, and the shorter one read as
    # governing. A new figure is added HERE. §2 cannot absorb the list —
    # AGENTS.md sits a few dozen bytes under a hard 28 KiB cap, because Codex
    # truncates the instruction chain silently at 32 KiB, and the pointer that
    # replaced the enumeration is itself pinned by
    # `test_agents_md_defers_the_figure_registry_to_this_file` below.
    #
    # DOLLAR amounts only, deliberately. §6654(i)'s two-thirds qualification
    # and its 66⅔% prong are stated in the skill and are not here: both are
    # unindexed statutory RATIOS (so they cannot go stale the way an indexed
    # cap does), and both belong to a prong the skill says in the same breath
    # it cannot compute — `tax_computations` and `projection` each list
    # farmers/fishermen as NOT MODELED — so neither figure ever reaches an
    # arithmetic a taxpayer acts on, and neither has an engine constant to be
    # pinned against.
    allowed = {"$0", "$150,000", "$75,000", "$1,000", "$1,000,000",
               "$500,000", "$250", "$500",
               # §6017's net-self-employment filing trigger. Fixed by statute
               # and never indexed, and pinned to the engine's own
               # SE_TAX_THRESHOLD by TestTheAdvisorySkillRestatesTheEnginesLawExactly.
               "$400"}
    offenders = {pair for pair in _quoted_limits(body) if pair[0] not in allowed}
    assert not offenders, (
        "tax-safe-harbor states a dollar amount as a LIMIT that is not one of "
        f"the unindexed statutory figures this `allowed` registry holds: "
        f"{sorted(offenders)} — an indexed figure quoted in a skill with no "
        "cache goes stale while still reading as current")

    # The specific regression: the indexed adoption cap.
    assert "$5,000 per child" not in body
    assert "refundable cap is INDEXED" in body, (
        "the reader must be told to take the cap from the covered year's own "
        "Form 8839 instructions, not merely left without a number")


def test_agents_md_defers_the_figure_registry_to_this_file():
    """§2 must POINT at the `allowed` set above, not re-list it.

    A hardcoded threshold in a cache-free skill is a freshness-contract
    exception, and §2 is the governing list of those. It used to grant the
    exception by naming five figures, which made every later addition —
    §19136(c)(2)'s $500/$250, §6017's $400 — look unregistered even though each
    was pinned to an engine constant by test right here. Copying them into §2
    is not the fix and is not available: AGENTS.md is a few dozen bytes under a
    28 KiB cap, and a second list drifts from the first the way this one already
    had. So §2 defers, and this pins the deferral — a pointer nothing checks
    rots into the enumeration it replaced.
    """
    agents = (REPO / "AGENTS.md").read_text()
    bullet = [b for b in agents.split("\n- ") if "`tax-safe-harbor`" in b
              and "2003-2026" in b]
    assert len(bullet) == 1, (
        "AGENTS.md §2 no longer carries exactly one `tax-safe-harbor` "
        "freshness-exception bullet — the deferral below cannot be located")
    text = bullet[0]
    assert "tests/test_portability.py" in text and "allowed" in text, (
        "AGENTS.md §2 grants tax-safe-harbor its hardcoded-figure exception "
        "without naming the closed registry that holds the figures. Point it "
        "at the `allowed` set in tests/test_portability.py; do not re-list "
        "them there, which is how the two came to disagree")


class TestTheAdvisorySkillStopsInventingWhatItDoesNotHold:
    """Three branches that reached a NUMBER without the inputs a number needs.

    Each is the same failure with a different sign. The CA floor was handed
    over whole when half of it was already proved, so the skill recommended
    paying a harbor no penalty could arise under. The joint-to-separate
    allocation was named without its formula or its inputs, so the only way to
    finish the branch was to invent a split. The April 15 accrual cutoff was
    stated categorically for years the engine itself refuses to state it for.
    """

    SKILL = "tax-safe-harbor"

    def _text(self) -> str:
        return re.sub(r"\s+", " ", skill_text(self.SKILL))

    def test_a_chart_c_tax_line_does_not_settle_figure_one(self):
        """Two compositions, and the file read one off the other.

        §6654(e)(2)'s "no liability" test is the line the RETURN labels total
        tax — the IRS's own estimated-tax penalty guidance says so — and
        refundable credits sit below it. Figure 1 is the Form 2210 line-8 base,
        whose instructions SUBTRACT those same credits by name. So a prior year
        with $500 of SE tax and $2,000 of earned income credit has a total tax
        of $500 and a figure 1 of $0, and the file said of that taxpayer "this
        is not a $0" and routed them to step 6 — 90% of the current year, for
        someone whose required annual payment is $0 under the ordinary prong.

        The §6654(f) reading that would collapse the two is named and NOT acted
        on: (f) defines "tax" for the whole section, netting the part IV credits,
        which would give the exception outright. Published IRS guidance points
        at the total-tax line instead. Acting on the statutory reading where
        they disagree CLEARS a penalty, which is the understating direction this
        skill promises cannot happen — so the file hands it over, and reaches
        the same taxpayer through figure 1, where nothing is in dispute.
        """
        text = self._text()
        assert "not the same composition" in text, (
            "the reference no longer separates the §6654(e)(2) total-tax test "
            "from figure 1, and reading one off the other is the whole bug")
        assert "§6654(f)" in text and "does not act on it" in text, (
            "the statutory reading is either unnamed — leaving an agent to "
            "discover it and act on it — or acted on, which understates")
        for credit in ("earned income credit", "premium tax credit"):
            assert credit in text.lower(), (
                f"line 8's enumerated refundable credits no longer name the "
                f"{credit}; without them 'figure 1 nets the credits' is a "
                f"claim the reader cannot apply")
        assert "and the return WAS filed" in text and \
            "and no return was ever filed" in text, (
            "the tax-named Chart C branch no longer splits on whether the "
            "return was filed — collapsing them routes a filed year with a $0 "
            "figure 1 to step 6, which is the 90% prong it does not need")
        assert "total tax cannot be zero" not in text, (
            "the categorical claim is back: it is true of the total-tax line "
            "and says nothing about figure 1, which is what step 1 needs")

    def test_a_withdrawn_prong_hands_off_instead_of_refusing(self):
        """Step 0's third gate refused an answer the engine produces.

        The gate exists because both tools accept a POSITIVE prior-year base as
        given and never ask whether §6654(d)(1)(B)'s closing text withdrew the
        prong. That is a reason not to POPULATE the field — not a reason to
        refuse the handoff, which leaves 90% of the current year uncomputed and
        sends the user to step 7's "run the full toolkit" from inside the full
        toolkit. California is the real exception: its prong comes from
        `archive/{year-1}/result.json`, not from the field, so there is nothing
        to null and the refusal stands there.

        The engine half is asserted too, not just the prose: a skill that says
        "hand off, the tools degrade" is only right while they do.
        """
        body = re.sub(r"\s+", " ",
                      (SKILLS_DIR / self.SKILL / "SKILL.md").read_text())
        assert "or the third is NO or unknown, do not hand off" not in body, (
            "the third scope question still refuses the handoff outright")
        assert "CONFIRMED that field is null" in body, (
            "the skill hands off without telling the agent to check that "
            "`prior_year_form_2210_tax_base` is empty — a stale positive base "
            "is the understatement the gate was built for")
        assert "**California is the exception**" in body

        PROJ = _projection()
        base, agi = PROJ._prior_tax_agi()
        assert base is None and agi is None, (
            "an absent prior-year base no longer resolves to None, so 'leave "
            "the field null' is no longer the way to withhold the prong")
        harbor = PROJ.compute_safe_harbor(
            projected_tax=40_000.0, prior_tax=None, prior_agi=None,
            filing_status="single", withholding=10_000.0, estimates_paid=0.0)
        assert harbor.degraded is True
        assert harbor.required_annual == harbor.pct90_current, (
            "a null prior base no longer degrades to 90%-of-current, so the "
            "handoff this skill now makes would produce no answer at all")

    def test_the_opening_gate_agrees_with_the_paragraph_that_qualifies_it(self):
        """The fix above left the sentence five lines up still refusing.

        Step 0 opens by naming four scope questions and demanding an explicit
        YES to the last — was the prior year a full 12-month year for which a
        return was actually filed? Three paragraphs later the same step says
        the third is NOT a refusal and to hand off on a NO or unknown with the
        base left null. An agent that reads an opening as a gate, which is what
        an opening is for, never reaches the correction: it continues to step
        6 and refuses the 90%-of-current answer /tax-withholding would have
        produced from the workspace already open.

        A contradiction is worse here than either branch alone would be. Which
        of the two an agent obeys depends on how far it reads, so the same
        workspace gets an answer or a refusal unpredictably, and neither
        outcome carries a reason the user can see. Only the FIRST THREE
        questions may gate the handoff — round sixteen added the calendar one
        to that set — and the fourth decides whether the base stays null.
        """
        body = re.sub(r"\s+", " ",
                      (SKILLS_DIR / self.SKILL / "SKILL.md").read_text())
        opening = body[body.find("Before any workspace handoff"):]
        opening = opening[:opening.find("Then check for a workspace")]
        assert opening, "step 0's handoff gate has moved or been renamed"
        assert "Three of them GATE it; the fourth does not" in opening, (
            "the opening does not say which of the four questions gate the "
            "handoff, so the demand it makes and the correction below it are "
            "two rules for one decision")
        assert "explicit YES" not in opening.split("The fourth —")[0], (
            "the opening still requires an explicit YES before any handoff. "
            "That is the sentence the paragraph below contradicts, and it is "
            "the one an agent acts on")
        # The correction must still be there — a fix that resolved the
        # contradiction by deleting the RIGHT half would pass everything above.
        assert "decides whether the prior-year base is left NULL" in opening, (
            "the fourth question no longer says what it is FOR, so it reads as "
            "a question with no consequence and gets dropped")
        assert "hand off on a NO or unknown" in opening, (
            "the contradiction was resolved by removing the handoff, not the "
            "gate — which is the understating branch of the two")

    def test_the_calendar_question_gates_the_handoff_not_just_step_1(self):
        """Round sixteen: step 6 refused what step 0 handed off first.

        §6654(k)(1) substitutes, for a taxable year beginning on any date
        other than January 1, "the months which correspond thereto"; §6654(j)
        gives a §6072(c) nonresident THREE installments — June 15, September
        15, January 15 — with 50% at the first. `compute_estimated_vouchers`
        models neither: `ES_DUE_DATES` is a fixed April/June/September/January
        calendar and `/tax-withholding` has no input that could move it.

        The calendar check existed, at step 1, and step 6 refuses both filers
        outright. But step 0 hands a workspace user off BEFORE step 1 runs, so
        the two filers the skill promises to refuse were the two who reached
        the engine first and came back actionable dates and amounts from the
        wrong schedule. The NR case is the one that bites hardest: four equal
        instalments where the statute weights the first at 50% UNDERSTATES the
        June payment, which is the direction this skill promises cannot
        happen. Same shape as rounds 13-15 — a rule stated in one place and
        not at the one an agent acts on.

        Asserted on the ENGINE too: the gate is only worth its words while
        that calendar is genuinely fixed. Give the engine a fiscal-year or NR
        mode and this test should be revisited, not deleted.
        """
        body = re.sub(r"\s+", " ",
                      (SKILLS_DIR / self.SKILL / "SKILL.md").read_text())
        opening = body[body.find("Before any workspace handoff"):]
        opening = opening[:opening.find("Then check for a workspace")]
        assert opening, "step 0's handoff gate has moved or been renamed"
        gating = opening[:opening.find("The fourth —")]
        assert "fiscal year" in gating and "1040-NR" in gating, (
            "the calendar question is no longer among the gating ones, so a "
            "fiscal-year or 1040-NR filer is handed off to a tool that prints "
            "the ordinary quarters and step 6 never gets to refuse them")
        assert "§6072(c)" in gating, (
            "the gate refuses every 1040-NR filer, including the ones with "
            "US-withheld wages who are on the ordinary quarters and are "
            "computable here — an over-refusal in place of the old gap")
        assert "§6654(k)" in opening and "§6654(j)" in opening, (
            "the gate states no statutory ground, so the next reader cannot "
            "tell it from an arbitrary scope limit and drops it")
        assert "UNDERSTATING" in opening, (
            "the gate omits WHICH WAY the wrong calendar errs; without it "
            "this reads as a tidiness rule rather than the one failure "
            "direction the skill promises cannot happen")

        PROJ = _projection()
        assert PROJ.ES_DUE_DATES == [(1, (4, 15)), (2, (6, 15)),
                                     (3, (9, 15)), (4, (1, 15))], (
            "the federal voucher calendar is no longer the fixed ordinary "
            "one, so re-read whether the handoff still needs this gate")

        catalog = re.sub(r"\s+", " ", (
            REPO / "docs" / "contributing" / "skill-catalog.md").read_text())
        assert "the calendar gate had to move to where the handoff is" in (
                catalog.lower()), (
            "the design note a contributor rewrites step 0 from no longer "
            "says WHY the check sits at the gate rather than at step 1, so "
            "the next revision tidies it back down into step 1")

    def test_a_no_to_the_12_month_question_still_carries_the_exception(self):
        """Round fourteen: the handoff nulled a field the EXCEPTION also uses.

        §6654(d)(1)(B)'s closing text withdraws the prior-year PRONG when the
        preceding year was short or no return was filed for it. §6654(e)(2) is
        a different rule with different conditions — a 12-month prior year, no
        LIABILITY for it, US citizenship or residence throughout — and filing
        is not among them. The Form 2210 instructions say so in terms: the
        prior year need only be one the return "was (or would have been had
        you been required to file) for a full 12 months". A taxpayer under
        last year's filing thresholds is that rule's ordinary case.

        Both reach the engine through ONE field. The prong is a positive
        `prior_year_form_2210_tax_base`; the exception is that same field at
        $0 beside `prior_year_zero_liability_confirmed`. So step 0's "hand off
        with the base confirmed null, never populate it for a withdrawn prong"
        — a rule written about the FIELD where the statute has one about the
        PRONG — took the exception away with it, and the report then billed
        90% of the current year to someone no penalty can attach to at all.

        Asserted on the ENGINE too, both directions: the skill's instruction
        is only true while that field is what carries the exception, and only
        worth giving while the flag on its own does nothing.
        """
        body = re.sub(r"\s+", " ",
                      (SKILLS_DIR / self.SKILL / "SKILL.md").read_text())
        handoff = body.find("The FOURTH is not a refusal")
        assert handoff >= 0, "step 0's 12-month-question paragraph has moved"
        para = body[handoff:body.find("One arrival is exempt", handoff)]
        assert "§6654(e)(2)" in para, (
            "the handoff names only the withdrawn prong, so the reader nulls "
            "the base and never learns the exception reaches the same field")
        assert "prior_year_zero_liability_confirmed" in para, (
            "the paragraph says the exception survives but not how to "
            "TRANSMIT it, which leaves the null base as its only instruction")
        assert "no-return-year.md" in para, (
            "step 0 resolves the exception without the file that holds the "
            "test — either it restates the three conditions as a third copy, "
            "or the agent answers them from memory")
        assert "tax_year" in para, (
            "answering a prior_year_* field without declaring tax_year is a "
            "file both CLIs REFUSE; an instruction to populate one that omits "
            "it hands the user an error in place of the answer")
        first = para[:para.find("**A NO is not always a NULL.**")]
        assert "unless §6654(e)(2) is still open" in first, (
            "the null-base instruction is categorical again and the exception "
            "reaches the reader only after it. An agent reads an instruction "
            "as an instruction and stops — which is this same step's own "
            "lesson, one test up")
        # The opening is what an agent acts on — the sibling test above is
        # this skill's own lesson about that — so it must not read as though
        # NULL were the 12-month question's only outcome.
        opening = body[body.find("Before any workspace handoff"):handoff]
        assert "§6654(e)(2)'s $0" in opening, (
            "the opening still offers one outcome for that question, so "
            "an agent that stops there nulls the base on every NO")

        PROJ = _projection()
        # The mechanism of the finding: a confirmation without a base is
        # discarded SILENTLY. Nothing warns, and the report degrades to 90%.
        assert PROJ._prior_tax_agi(zero_liability_confirmed=True) == (
            None, None), (
            "a null base no longer swallows the §6654(e)(2) confirmation — if "
            "the engine now honours the flag alone, step 0 has a second route "
            "and the instruction asserted above is no longer the only one")
        # And the route step 0 now takes reaches the answer.
        exempt = PROJ._prior_tax_agi(0, zero_liability_confirmed=True)
        assert exempt == (0.0, None), (
            "a $0 base paired with the confirmation no longer resolves, so "
            "the transmission step 0 now performs produces nothing")
        sh = PROJ.compute_safe_harbor(
            projected_tax=40_000.0, prior_tax=exempt[0], prior_agi=exempt[1],
            filing_status="single", withholding=0.0, estimates_paid=0.0)
        assert sh.required_annual == 0.0 and sh.covered and not sh.degraded, (
            "the transmitted exception no longer zeroes the required annual "
            "payment, which is the whole reason to transmit it")

        catalog = re.sub(r"\s+", " ", (
            REPO / "docs" / "contributing" / "skill-catalog.md").read_text())
        for fragment in (
                "A NO to the 12-month scope question is not a null base",
                "written as a rule about the FIELD rather than about the "
                "prong"):
            assert fragment in catalog, (
                "the design note a contributor rewrites step 0 from has lost "
                f"{fragment!r}, so the next revision states the rule about "
                "the field again and deletes the exception with it")

    def test_the_ca_prior_year_floor_is_proved_not_handed_over(self):
        """Form 540 total tax BOUNDS §19136(c)(2)'s prior-year quantity from
        above, so a total under the floor settles the prong with the figure
        step 1 already collected. Handing that case over makes the skill
        recommend the whole prior-year harbor against a $0 requirement."""
        text = self._text()
        assert "upper bound" in text.lower(), (
            "the CA reference does not say why a Form 540 TOTAL settles a rule "
            "written about tax LESS CREDITS — without the bound the reader has "
            "no reason to act on the figure they have")
        assert "proves the prong outright" in text
        assert "only works downwards" in text.lower(), (
            "the bound is stated without its direction, so a prior-year total "
            "ABOVE the floor reads as the prong failing when it proves nothing")

    def test_the_million_dollar_limitation_does_not_swallow_the_floor(self):
        """The skill hedged where the engine now decides, and both were wrong.

        R&TC §19136.3's whole operative text modifies IRC §6654(d)(1)(B) so
        that "clause (ii) shall not apply" — clause (ii) being the prior-year
        half of the REQUIRED ANNUAL PAYMENT. §19136(c)(2) sits in a different
        section and bars the addition to tax outright; §19136.3 never mentions
        it, and FTB 5805's General Information states the floor flatly while
        conditioning the $1,000,000 rule on the taxpayer being "subject to" the
        penalty in the first place. So the one CA answer this skill can still
        give above the threshold is the one it was sending to a preparer.

        Pinned against the ENGINE's own branch rather than on its own: the two
        are the same rule read by the same taxpayer, and a hedge surviving in
        either place is what tells them to pay a schedule that avoids nothing.
        """
        text = self._text()
        PROJ = _projection()
        high = PROJ.compute_ca_safe_harbor(
            projected_tax=20_000.0, prior_tax=400.0, prior_agi=100_000.0,
            current_agi=2_000_000.0, filing_status="single",
            withholding=2_000.0, estimates_paid=0.0)
        assert high.high_income_90_only and high.prior_year_floor_applied, (
            "the engine no longer applies the prior-year floor at the "
            "§19136.3 limitation — re-derive this branch before relaxing it")
        assert high.shortfall == 0.0
        assert "does not reach that floor" in text.lower(), (
            "the CA reference does not say that §19136.3 leaves §19136(c)(2) "
            "standing, so the reader has no ground to act on the figure the "
            "engine acts on")
        assert "clause (ii)" in text, (
            "the claim is asserted without the text that carries it — "
            "§19136.3 disapplies a CLAUSE of the required-annual-payment rule, "
            "and naming it is what distinguishes the two rules")
        assert "send the user to a CA preparer" not in text, (
            "the skill still hands a taxpayer whose own Form 540 total settles "
            "the prong to a preparer, and prints the 540-ES schedule while it "
            "does — the over-statement AGENTS.md ranks as the hard error")
        assert "Do NOT send this taxpayer to a CA preparer" in text, (
            "the hedge was deleted without a directive in its place; the "
            "handoff is the reflex an agent reaches for at a $1M threshold, "
            "and only naming it stops the reflex")

    def test_the_promise_is_the_estimated_tax_penalty_and_not_the_others(self):
        """"They simply cannot be penalized for having paid it late" was the
        opening claim, and it is broader than anything §6654 provides.

        Two ways it fails, and the skill already acts on both further down
        while promising past them here. §6654(b)(2) runs each instalment's
        underpayment from its OWN due date to the earlier of payment and the
        return's due date, so a harbor topped up in September does not undo
        April; and the harbor is about ESTIMATED tax only, so a balance still
        unpaid when the return falls due draws §6651(a)(2) failure-to-pay and
        interest whatever was paid in during the year. A reader who takes the
        opening at face value stops reading — which is what an opening is for.
        """
        body = (SKILLS_DIR / self.SKILL / "SKILL.md").read_text()
        body = re.sub(r"\s+", " ", body)
        assert "cannot be penalized for having paid it late" not in body, (
            "the unconditional late-payment promise is back")
        assert "§6651(a)(2)" in body, (
            "the opening does not name the penalty the harbor does NOT reach, "
            "so 'no penalty' still reads as covering the balance due")
        assert "§6654(b)(2)" in body and "step 4's timing check" in body, (
            "the estimated-tax promise is stated without the instalment "
            "timing it depends on — the later steps then contradict it")

    def test_the_joint_to_separate_allocation_has_its_formula_and_its_refusal(self):
        """Naming an allocation without its formula leaves the agent to invent
        a split, and every plausible invention is wrong in the forbidden
        direction — half each understates whenever the incomes differed."""
        text = self._text()
        for fragment in ("hypothetical separate tax",
                         "1.6654-2(e)(1)",
                         "whole joint AGI against the $75,000 threshold"):
            assert fragment.lower() in text.lower(), (
                f"the status-change branch does not state {fragment!r}, so the "
                f"allocation cannot be worked from what the skill collects")
        assert "at most the WHOLE joint tax" in text, (
            "the branch refuses without first testing the CEILING it can "
            "already compute. The allocation fraction cannot exceed 1, so "
            "figure 1 is capped by the whole joint tax on EVERY split, and the "
            "factor reads the joint AGI — a taxpayer already withholding above "
            "that ceiling is provably inside the harbor and was being sent "
            "away to have their prior year recomputed twice")
        assert "ONE direction only" in text, (
            "the ceiling is stated without its direction, so falling short of "
            "an UPPER BOUND reads as a shortfall and gets quoted as one")
        assert "Refuse, and route to step 6" in text, (
            "the branch has no exit for the case its own inputs cannot reach, "
            "which is the only case it was written for")

    def test_the_april_cutoff_carries_the_engines_own_7508a_caveat(self):
        """The engine refuses to assert the fixed-penalty claim for a §7508A
        year — "does not silently assert the strongest claim in the report".
        The skill asserted exactly that claim, unconditioned, about the same
        taxpayer. Pinned to the ENGINE's caveat so that resolving it in one
        place cannot leave the other still hedging (or still asserting)."""
        text = self._text()
        assert "§7508A" in text, (
            "the skill states the April 15 accrual cutoff categorically; for a "
            "postponed year the notice disregards that period in computing "
            "additions to tax, and the penalty may not be fixed at all")
        assert "additions to tax" in text.lower()

        PROJ = _projection()
        assert "§7508A" in PROJ._ACCRUAL_POSTPONED_CAVEAT, (
            "the engine no longer hedges the postponed case — if it has been "
            "resolved, the skill's caveat is now stale in the same edit")

    def test_every_categorical_fixed_penalty_claim_reaches_the_caveat(self):
        """Round thirteen: the caveat sat in ONE of the three places the claim
        does, and the other two state it flat.

        Step 4 was fixed in the round that widened the caveat past TY2019 and
        TY2020; step 0's window table and `references/withholding-cure.md`
        were not. Both are read on their own — the window table is what step 0
        gives when a workspace exists, the reference page when route A is the
        question — so an agent reaching either one gets the categorical form
        and never sees step 4. Each may EITHER carry the qualification or send
        the reader to the paragraph that does; neither may state the verdict
        and stop. The engine half of the same class is pinned in
        `test_projection.py::test_the_fixed_penalty_verdict_is_qualified_on_
        every_closed_year`, which asserts on the RENDERED note: the template
        here holds only a `{postponed}` placeholder, and a placeholder proves
        nothing about what fills it.
        """
        claim = re.compile(
            r"penalty is FIXED|penalty is fixed|reduces it by a cent"
            r"|no withholding on any year")
        paths = [SKILLS_DIR / self.SKILL / "SKILL.md"]
        paths += sorted((SKILLS_DIR / self.SKILL / "references").glob("*.md"))
        found = 0
        for path in paths:
            flat = re.sub(r"\s+", " ", path.read_text())
            for match in claim.finditer(flat):
                found += 1
                # SYMMETRIC. Step 4 states its own claim AFTER the caveat
                # that qualifies it ("...before saying the penalty is
                # fixed"), so a forward-only window fails the one site that
                # was already right — and a window shape that reports the
                # correct file is a window shape nobody trusts twice.
                start = match.start()
                window = flat[max(0, start - 600):start + 600]
                assert "§7508A" in window, (
                    f"{path.name} states the fixed-penalty verdict at "
                    f"{match.group(0)!r} without reaching §7508A within the "
                    "paragraph — a disaster declaration covering this "
                    "taxpayer's counties disregards the postponed period in "
                    "computing additions to tax, and §6654's penalty is one. "
                    "Qualify it here, or send the reader to step 4")
        assert found >= 3, (
            f"the sweep matched {found} sites where three were known; the "
            "claim has been reworded out of the pattern, not qualified")

        # And the design note a contributor rewrites that prose from. The
        # sibling class one class down spread by being copied out of exactly
        # this file, so the REASONING is pinned too, not just the sentences:
        # both halves of it are things a later round would otherwise undo as
        # tidying — the table lookup restored as the gate, or the question
        # pushed out to an input whose absence answers it.
        catalog = re.sub(r"\s+", " ", (
            REPO / "docs" / "contributing" / "skill-catalog.md").read_text())
        for fragment in (
                "qualified on EVERY closed year rather than on the two in",
                'a flag reading "no disaster" while unanswered deletes the '
                "hedge"):
            assert fragment in catalog, (
                f"skill-catalog.md no longer records {fragment!r}; the design "
                "note is what the next rewrite of this prose starts from, and "
                "keying the caveat on the nationwide table reads as a "
                "tightening rather than the silent verdict it is")

    def test_a_postponed_instalment_is_still_open_in_the_skill_too(self):
        """Round seventeen: the engine was fixed, and the skill was not.

        Round fifteen split the engine's closed-schedule note in two. The
        FIXED-penalty verdict stays hedged — whether §7508A(a)(2) reaches
        §6654(b)(2)'s accrual endpoint is genuinely unresolved here. The
        INSTALMENT is not hedged at all, because it is not in doubt:
        §7508A(a)(1) disregards the period in determining whether the acts in
        §7508(a)(1) were performed in time, and §7508(a)(1)(B) is "payment of
        any income ... tax or any installment thereof". A covered instalment
        is payable on its postponed date and paying it then is TIMELY.

        That sweep stopped at the engine. Step 4 of this file went on saying
        "do not send a 1040-ES" flat, two sentences after naming the same
        §7508A notice — and this is the copy that runs when the engine is
        gone, which is the whole licence of the skill. Cost of the flat form:
        the §6654 charge on the one instalment still free to fix, forfeited on
        the skill's own advice, in the paragraph making the strongest claim in
        the file.
        """
        flat = re.sub(r"\s+", " ", self._text())

        assert "§7508(a)(1)(B)" in flat, (
            "step 4 hedges the accrual cutoff and nothing else. The "
            "instalment DATE is the half that needs no hedging to be wrong "
            "— §7508A(a)(1) postpones it outright, and the voucher sentence "
            "still says its dates are all behind it")
        assert "still OPEN" in flat and "timely rather than late" in flat, (
            "the doubt is named without the fact it turns on: a postponed "
            "instalment is not late, so the 1040-ES is still the instrument")

        # Leading, not trailing — the engine's own lesson, at
        # `test_projection.py::test_a_postponed_instalment_is_not_told_the_
        # schedule_is_spent`. An agent reads an instruction as an instruction
        # and stops, so a qualification arriving after the imperative is never
        # reached by the reader it was written for.
        instruction = flat.index("do not send one")
        condition = flat.index("where no postponement reached an instalment")
        assert condition < instruction, (
            "the voucher-suppression instruction is categorical again and its "
            f"condition trails it by {instruction - condition} characters")

        # Cross-pinned to the ENGINE's caveat. These are two copies of one
        # rule, and every round of this branch from thirteen on was a rule
        # fixed in one copy and left standing in the other.
        PROJ = _projection()
        for fragment in ("§7508(a)(1)(B)", "still OPEN",
                         "timely rather than late"):
            assert fragment in PROJ._ACCRUAL_POSTPONED_CAVEAT, (
                f"the engine's caveat no longer carries {fragment!r}; if the "
                "instalment half has been resolved or reworded there, this "
                "skill now states it alone and unmirrored")

        catalog = re.sub(r"\s+", " ", (
            REPO / "docs" / "contributing" / "skill-catalog.md").read_text())
        assert "the instalment half never reached the skill" in catalog, (
            "skill-catalog.md no longer records why the flat voucher "
            "suppression was wrong; the design note is what the next rewrite "
            "of this paragraph starts from")


class TestTheRefusalsAndPromisesMatchTheStatutesTheyCite:
    """Round six: three branches that outran the statute they name.

    Each turns a rule with a stated scope into a blanket one, and two of the
    three do it in the direction this skill's licence forbids.

    §6654(e)(2) has three conditions and filing is not among them; the closing
    text of §6654(d)(1)(B) withdraws clause (ii) and nothing else. The skill
    merged them, so a required-but-unfiled prior year lost the EXCEPTION along
    with the prong — a complete "no penalty can attach" answer refused, on the
    strength of a chart line that need not have carried any tax at all.

    §6654(j) applies to "a nonresident alien described in section 6072(c)",
    and §6072(c) covers nonresident aliens "other than those whose wages are
    subject to withholding under chapter 24". The intake gate refused every
    1040-NR while step 6 of the same file refused only the ones with no
    US-withheld wages — one skill contradicting itself, with the wrong half
    reached first.

    And the withholding cure promised the overfunding back as a refund. The
    required annual payment is a PENALTY target, not the tax; where the
    prior-year prong is the lesser one it is anchored on a different year
    entirely, so the actual tax may be well above the figure withheld. That is
    the under-statement — a taxpayer told to expect money back in April who
    owes more instead.
    """

    SKILL = "tax-safe-harbor"

    def _text(self) -> str:
        return re.sub(r"\s+", " ", skill_text(self.SKILL))

    def _body(self) -> str:
        return re.sub(r"\s+", " ",
                      (SKILLS_DIR / self.SKILL / "SKILL.md").read_text())

    def test_an_unfiled_prior_year_loses_the_prong_and_keeps_the_exception(self):
        """The two live in different subsections and only one names filing.

        Asserted on the BODY as well as the reference, because the body is
        where the stop fires: an agent that never opens the link still has to
        know that "required and never filed" is not the end of the procedure.
        """
        for label, text in (("SKILL.md", self._body()),
                            ("the skill", self._text())):
            assert "withdraws the PRONG" in text or "withdraws only the" in text, (
                f"{label} still reads §6654(d)(1)(B)'s closing text as ending "
                f"the whole procedure. It withdraws clause (ii); §6654(e)(2) "
                f"asks about liability and never about filing")
            assert "so the stop holds" not in text, (
                f"{label} keeps the merged consequent — a prior year with a "
                f"required return and $0 of total tax is refused the exception "
                f"that is their entire answer")
        text = self._text()
        assert "does not ask whether a return was filed" in text, (
            "the reference names the two rules without saying which one turns "
            "on filing, leaving the agent to infer the very merge this fixes")
        assert "reconstruct that year's total tax" in text, (
            "nothing tells the agent what to do instead of stopping — the "
            "route is open only if the year's total tax is actually computed")

    def test_chart_c_is_not_read_as_proof_that_a_tax_was_owed(self):
        """The premise the merge rested on. Chart C carries lines that name a
        TAX and lines that force a RECONCILIATION, and "each is a liability"
        was false of the second kind — advance PTC being the clean case, where
        Form 8962 can leave the year at $0 and the return still be required."""
        text = self._text()
        assert "Each is a liability" not in text, (
            "Chart C is still asserted to be uniformly liability-driven, which "
            "is what let a required return stand in for a nonzero total tax")
        assert "Form 8962" in text and "advance premium tax credit" in text.lower(), (
            "the reconciliation case is claimed without its example, so an "
            "agent has no way to tell the two kinds of chart line apart")

    def test_the_intake_refuses_the_same_1040_nr_filers_step_6_does(self):
        """A skill may not refuse at the gate what it accepts at step 6.

        §6654(j) reads on §6072(c)'s nonresident aliens only, and §6072(c)
        excludes the ones whose wages are subject to chapter 24 withholding.
        An NR filer with US-withheld wages is on the ordinary four quarters and
        is computable here; the gate was turning them away.
        """
        text = self._text()
        gate = text.find("ordinary CALENDAR year")
        assert gate >= 0, "the intake gate has moved"
        window = text[gate:gate + 420]
        assert "no US-withheld wages" in window, (
            "the intake still refuses every 1040-NR filer, while step 6 of the "
            "same skill refuses only those with no US-withheld wages — the "
            "gate is reached first, so the narrower branch never runs")
        assert "6072(c)" in window, (
            "the narrowing is stated without the provision it comes from, so "
            "the next reader has nothing to check it against and the blanket "
            "refusal reads like the safer choice")

    def test_overfunding_the_harbor_is_not_promised_back(self):
        """The requirement is a penalty target and the refund depends on the
        tax, which this skill does not compute. Both halves are asserted: that
        the promise is gone, and that the timing point it was carrying — the
        real trade-off between the two figures — survived its removal."""
        text = self._text()
        assert "comes back as refund at filing" not in text, (
            "the withholding cure still promises the excess back. Where the "
            "prior-year prong sets the harbor the actual tax can exceed what "
            "was withheld, and the taxpayer owes MORE in April, not less")
        assert "PENALTY target" in text and "not the tax itself" in text, (
            "the reason is missing — without it the claim reads as a scruple "
            "rather than as the difference between a harbor and a liability")
        assert "out of" in text and "until the return is filed" in text, (
            "the cash-flow trade-off went out with the refund promise. It is "
            "what the whole 'give both figures' section exists to put to the "
            "taxpayer, and it is true whichever way the refund falls")

    def test_the_ca_farmer_still_loses_the_prong_above_the_limitation(self):
        """REFUTED, and pinned so the refutation does not have to be re-derived.

        The finding was that §19136.3 should not reach a farmer, on the federal
        analogy: §6654(i)(1)(C) computes the required annual payment "without
        regard to subparagraph (C) of subsection (d)(1)". True, and subparagraph
        (C) is the 110% STEP-UP. R&TC §19136.3 modifies §6654(d)(1)(B) to
        provide that **clause (ii)** shall not apply at $1,000,000 ($500,000
        MFS) — and clause (ii) IS the farmer's prior-year prong, §6654(i)
        restating no prior-year rule of its own. Verified against leginfo
        (§19136.3 is two subdivisions, no farmer carve-out, no reference to
        §6654(i)) and against the §6654 text.

        So the branch stands, and now says WHY, because the prose that invited
        the finding was the half that named the step-up without naming what
        §19136.3 actually disapplies.
        """
        text = self._text()
        assert "clause (ii) itself" in text, (
            "the CA farmer branch does not say what §19136.3 disapplies, so "
            "the next reader re-derives it from the federal step-up carve-out "
            "and concludes the limitation cannot reach a farmer")
        assert "110% STEP-UP" in text, (
            "the two carve-outs are not distinguished — §6654(i) drops the "
            "step-up, §19136.3 drops the prong, and conflating them is exactly "
            "the reading this pins against")
        farmer = text.find("A California farmer or fisherman")
        assert farmer >= 0
        assert "no CA answer to give" in text[farmer:farmer + 1200], (
            "the farmer branch no longer refuses above the limitation")


class TestTheOvershootIsNotPromisedBackAnywhere:
    """The refund promise was in FOUR documents, and round 6 fixed one of them.

    "Pay the harbor and the excess refunds" is false whenever the prior-year
    prong is the lesser one: the harbor is a PENALTY target, the refund depends
    on the current-year tax, and this skill does not compute that tax. Paying
    110% of a $100,000 prior base against a $130,000 actual tax leaves $20,000
    owing, not a cheque in the post.

    Round 6 removed the claim from `references/withholding-cure.md` and swept
    no further, so the introduction that grants the skill its whole licence, the
    110% default in step 2, README's one-paragraph description and the design
    note in `skill-catalog.md` each kept it — one sentence hand-copied into four
    places, which is why this is a SCAN over every document that carries the
    licence rather than four more per-site pins. A fifth copy fails here.
    """

    # Sentences pairing one of these with an unqualified "refund" are the claim.
    OVERSHOOT = re.compile(r"overpaid|overpay|overshoot|overfund", re.I)
    REFUND = re.compile(r"refund(?!able)", re.I)
    # What makes the same sentence true: the money is a §31/§6402 CREDIT, and
    # comes back only so far as it passes the tax actually due.
    QUALIFIED = re.compile(
        r"insofar|to the extent|not (?:money )?lost|credit", re.I)

    def _documents(self):
        skill = REPO / ".agents" / "skills" / "tax-safe-harbor"
        paths = [skill / "SKILL.md", *sorted((skill / "references").glob("*.md")),
                 REPO / "README.md",
                 REPO / "docs" / "contributing" / "skill-catalog.md"]
        return [(p.relative_to(REPO), p.read_text()) for p in paths]

    def test_no_document_promises_the_overshoot_back(self):
        """A scan, not a pin: the claim spread by being copied, so the check
        has to cover the places it could be copied to next."""
        offenders = []
        for rel, text in self._documents():
            for sentence in re.split(r"(?<=[.;:])\s+", re.sub(r"\s+", " ", text)):
                if (self.OVERSHOOT.search(sentence)
                        and self.REFUND.search(sentence)
                        and not self.QUALIFIED.search(sentence)):
                    offenders.append(f"{rel}: {sentence.strip()}")
        assert not offenders, (
            "a document tells the taxpayer that overshooting the safe harbor "
            "comes back as a refund, without the condition that makes that "
            f"true: {offenders}. The harbor is a penalty target and the refund "
            "turns on the current-year tax, which this skill never computes — "
            "where the prior-year prong is the lesser one the taxpayer can pay "
            "the whole harbor and still owe in April")

    def test_the_licence_still_states_what_the_overshoot_does_do(self):
        """Deleting the sentence would pass the scan above and leave the reader
        with no reason to accept a figure the skill admits is too high. The
        true claim has to be there, in the introduction that grants the licence
        and in both places a reader meets it outside the skill."""
        skill = re.sub(r"\s+", " ", skill_text("tax-safe-harbor"))
        assert "comes back only insofar as it passes it" in skill, (
            "the introduction no longer says what paying above the harbor "
            "actually buys — that it credits against the tax due rather than "
            "being lost — so the overshoot reads as money thrown away")
        for rel, fragment in (
                ("README.md", "credited against the tax actually due, not money lost"),
                ("docs/contributing/skill-catalog.md",
                 "comes back only insofar as it passes it")):
            text = re.sub(r"\s+", " ", (REPO / rel).read_text())
            assert fragment in text, (
                f"{rel} states the bias direction without what the overshoot "
                "becomes; it is the document a contributor rewrites the "
                "skill's own prose from, and the promise came back from here "
                "once already")


class TestTheProvableSafeCeilingScalesWithTheDeadline:
    """The joint-to-separate shortcut refused taxpayers its own figures cleared.

    The ceiling — `factor x whole joint prior-year tax` — bounds the required
    ANNUAL payment. Testing each cumulative payment against the whole of it
    demands January's money in April: with no withholding, a $20,000 ceiling
    and $5,000 paid each quarter, every instalment that could possibly be owed
    is covered on every allocation, and the unscaled test refused until the
    last deadline. Both sides scale by the same `n / 4` — the requirement under
    §6654(d)(1)(A), the withholding under §6654(g)(1)'s ratable rule — which is
    step 4's walk exactly, with the ceiling substituted for the requirement.

    Soundness of the looser test: the true required annual payment R is at most
    the ceiling C, so credited >= C x n/4 >= R x n/4 at every deadline. It stays
    one-directional — falling short still proves nothing.
    """

    def _branch(self) -> str:
        text = (REPO / ".agents" / "skills" / "tax-safe-harbor"
                / "references" / "status-change.md").read_text()
        start = text.find("## Before refusing")
        assert start >= 0, "the provably-safe shortcut is gone from the branch"
        end = text.find("## If the two hypothetical figures cannot be had", start)
        assert end > start
        return re.sub(r"\s+", " ", text[start:end])

    def test_each_deadline_is_tested_against_the_scaled_ceiling(self):
        branch = self._branch()
        assert "ceiling x n / 4" in branch, (
            "the shortcut no longer scales the ceiling to the deadline, so it "
            "asks the last instalment's money at the first and refuses a "
            "taxpayer whose own figures prove them inside the harbor")
        assert "expected full-year withholding x n / 4" in branch, (
            "the credited side is not scaled with it. §6654(g)(1) deems an "
            "equal part of the year's withholding paid on each date; crediting "
            "the whole of it against a scaled ceiling is the OTHER error, and "
            "clears a taxpayer who is short at the early deadlines")
        assert "6654(d)(1)(A)" in branch and "6654(g)(1)" in branch, (
            "the two scalings are asserted without the separate authorities "
            "they come from — they are not one rule, and a reader harmonizing "
            "them will drop whichever they think is the copy")

    def test_the_shortcut_still_works_in_one_direction_only(self):
        """The ceiling is an UPPER bound. Scaling it changes what it clears,
        never what it proves: a shortfall against it is still not a number
        anyone owes, and the refusal below is still the exit."""
        branch = self._branch()
        assert "ONE direction only" in branch
        assert "upper bound, not a number anyone owes" in branch, (
            "a gap to the scaled ceiling can now be quoted as a shortfall")
        assert "step 4" in branch, (
            "the branch restates the deadline walk instead of pointing at the "
            "one place that owns it — a second copy drifts, and this one would "
            "drift against the arithmetic the whole skill is checked on")


class TestTheNewYorkExtensionAndConformityGaps:
    """Slice 13's documentation half: what /tax-extend must tell a New York
    taxpayer, and what `retirement_dashboard` has NOT researched."""

    REPO = Path(__file__).resolve().parents[1]

    def test_tax_extend_says_the_federal_4868_does_not_extend_new_york(self):
        """Form IT-370-I: "Do not submit a copy of the federal extension form.
        We will not accept it in place of Form IT-370 and your due date will
        not be extended." A taxpayer who carries California's automatic rule
        across has filed LATE at 5% per month."""
        body = skill_text("tax-extend")
        assert "does NOT extend the New York return" in body
        assert "Form IT-370" in body
        assert "5%" in body

    def test_tax_extend_says_payment_is_a_condition_of_the_extension(self):
        """Federal Form 4868 is valid with no payment at all; IT-370 is not —
        "you must make full payment of the properly estimated tax balances
        due"."""
        body = skill_text("tax-extend")
        assert "CONDITION of the New York extension" in body
        assert "full payment of the properly estimated" in body

    def test_tax_extend_uses_line_80_not_a_state_only_figure(self):
        body = skill_text("tax-extend")
        assert "line_80_amount_owed" in body
        assert "Line 60, not line 46" in body or "line 60" in body

    def test_tax_extend_names_the_ten_percent_reasonable_cause_test(self):
        """The presumption is measured against the SAME four taxes line 60
        adds up, which is why the estimate uses line 80."""
        body = skill_text("tax-extend")
        assert "10%" in body and "MCTMT" in body

    def test_unknown_state_conformity_never_claims_a_saving(self):
        """X10: replace assumed conformity with a year/source-bound matrix."""
        from agents.lib import retirement_dashboard as rd
        from agents.lib.tax_law_fetcher import create_empty_tax_law
        law = create_empty_tax_law(2025)
        for account in ("employee_401k", "traditional_ira", "hsa"):
            conforming, note = rd._retirement_state_treatment(law, "NY", 2025, account)
            assert conforming is False
            assert "unverified" in note and "$0" in note

    def test_the_matrix_includes_every_state_without_implicit_law(self):
        from agents.lib.tax_law_fetcher import create_retirement_conformity_template
        from agents.lib.capabilities import _USPS_STATES, _TERRITORIES
        matrix = create_retirement_conformity_template()
        assert set(matrix) == _USPS_STATES - _TERRITORIES
        assert len(matrix) == 51
        assert all(value is None for row in matrix.values() for value in row.values())

    def test_the_cache_doc_carries_new_yorks_keying_gotchas(self):
        """The freshness auditor is told to work from the schema, and
        `docs/contributing/tax-law-cache.md` is where AGENTS.md §9 sends a
        reader for the per-block keying. Its DEFINITION is digest-pinned to a
        passed acceptance and cannot be edited without a fresh-session replay,
        so the gotchas go where it already looks."""
        doc = (self.REPO / "docs" / "contributing"
               / "tax-law-cache.md").read_text()
        assert "keyed by RATE SCHEDULE" in doc
        assert "state_brackets_for_status" in doc
        assert "year-windowed" in doc
        assert "TWO ZONES" in doc
        assert "0 on purpose" in doc


class TestACaveatMatchesTheSchemaItDescribes:
    """A caveat naming a simplification the engine no longer has is a LIE to
    the taxpayer, and this repository's own rule is that a lying caveat is
    worse than a missing one.

    `/tax-scenario` and `/tax-roth-convert` both printed "MFS and HOH use the
    single-status capital-gains brackets", one of them adding that "MFS gains
    near the 15%/20% boundary may read low". X1 (#89) made those breakpoints
    four-status a month earlier, and both renderers read them through
    `_rate_kwargs` → `capital_gains_limits`, so MFS had its OWN $300,000
    fifteen-percent breakpoint against single's $533,400 while the output told
    the taxpayer otherwise. Nothing was pinning the claim, so nothing moved.

    The direction was the safe one — it understates the tool rather than the
    refund — which is exactly why it survived: no gate fires on a caveat that
    is merely false. This binds the claim to `create_empty_tax_law`, which is
    the schema both the caveat and the accessor are about.
    """

    SURFACES = ("scenario.py", "roth_ladder.py", "loss_harvest.py", "ptet.py",
                "projection/render.py", "withholding.py")

    def _texts(self):
        lib = REPO / "agents" / "lib"
        out = {name: (lib / name).read_text(encoding="utf-8")
               for name in self.SURFACES}
        for skill in ("tax-scenario", "tax-roth-convert", "tax-harvest"):
            out[skill] = skill_text(skill)
        return out

    def test_nothing_tells_a_taxpayer_mfs_shares_the_single_cg_brackets(self):
        """The claim, against the schema that decides whether it is true."""
        from agents.lib.tax_law_fetcher import create_empty_tax_law
        seeded = create_empty_tax_law(2026)["federal"]["capital_gains_rates"]
        assert set(seeded["zero_pct_limit"]) == {"single", "mfj", "mfs", "hoh"}, (
            "the capital-gains breakpoints are no longer four-status, so the "
            "caveat this forbids may have become true again — read this class "
            "rather than deleting the assertion")
        offenders = sorted(
            name for name, text in self._texts().items()
            if re.search(r"single[- ]status capital[- ]gains", text, re.I))
        assert not offenders, (
            f"{offenders} tell a taxpayer that MFS and HOH fall back to the "
            "single-status capital-gains brackets. They have had their own "
            "since X1 (#89) — MFS's 15% breakpoint is not single's — so this "
            "is a caveat that is simply false, and a false one is worse than "
            "none: it invites a reader to discount a figure that is right.")

    def test_the_half_of_that_caveat_that_IS_true_survived(self):
        """Non-vacuity, and the other direction.

        The sentence carried TWO facts — capital gains AND IRMAA — and only
        one of them moved. Deleting the whole line would have withdrawn a
        disclosure that is still owed: `federal.irmaa.tiers` really is keyed
        `single`/`mfj` alone, so an MFS or HOH ladder really is reading the
        single schedule. One symbol, two concepts: the fix was to split them,
        not to drop the sentence.
        """
        from agents.lib.tax_law_fetcher import create_empty_tax_law
        tiers = create_empty_tax_law(2026)["federal"]["irmaa"]["tiers"]
        assert set(tiers) == {"single", "mfj"}, (
            f"irmaa.tiers is keyed {sorted(tiers)} — if it grew MFS and HOH "
            "the surviving half of the caveat is stale in its turn")
        texts = self._texts()
        assert re.search(r"single-status IRMAA", texts["roth_ladder.py"]), (
            "the IRMAA disclosure went with the capital-gains one; it is the "
            "half that is still true")
        assert re.search(r"single-status IRMAA", texts["tax-roth-convert"]), (
            "the skill dropped the IRMAA disclosure its engine still owes")


class TestTheBacklogSaysWhatIsOpen:
    """`docs/roadmap/BACKLOG.md` is the artifact every PR is cut from.

    This class used to be 396 lines and nine tests over
    `2026-09-05-staff-agent-review.md`, and every one of them held two hand-kept
    copies of a STATUS against each other: a shipped table against a `— DONE`
    marker on the item entry, against a sequencing step's marker, against a
    not-started list, against a ruling annotation. Five statements of one fact,
    and the tests were the mirror's maintenance cost. They were earned — V11 was
    recorded as shipped in a paragraph, never marked DONE in the list, and was
    very nearly re-implemented four days later — but the answer to a mirror in
    this repository is to have ONE copy (AGENTS.md §1 on the PII gate rules,
    CLAUDE.md on the `.claude/` three-way mirror), not to test the copies against
    each other.

    So the file states status once: an id is in the Open section or in the
    shipped index, never both. Two things are still worth asserting, and the
    later ones are STRONGER than anything the nine did — nothing previously
    checked that a PR a row claims credit for was ever merged, and nothing
    related the Open list to the plan or to the declined items.

    Not every one of the nine was a mirror check: three read a single table
    (non-vacuity, epic ordering, and whether a row named any PR at all). Those
    are kept below rather than dropped, because a single-source file makes a
    STATUS unambiguous and says nothing about a table's contents.
    """

    BACKLOG = REPO / "docs" / "roadmap" / "BACKLOG.md"
    # An open item's entry, and a shipped row. Ids are `V1`-style, plus the
    # `AA1` series the 2026-09-20 review added.
    #
    # THE TRAILING LETTER IS PART OF THE ID, and leaving it off was a real hole
    # in the first draft: `W5a` is an open item, `W5` is a shipped row, and
    # `\d+\b` matched NEITHER on the entry — the boundary fails between `5` and
    # `a`, so the whole open item was invisible here and to both set operations
    # below. Written `[a-z]?` it is matched, and matched as `W5a`, which is a
    # different id from `W5` and so does not collide with the shipped row.
    _ID = r"(?:[VWXYZ]|AA|Q)\d+[a-z]?"
    ENTRY = re.compile(rf"^- \*\*({_ID})\b", re.M)
    ROW = re.compile(
        rf"^\| ({_ID})(?: \([^)]*\))? \| ([^|]*) \| ([^|]*) \|", re.M)
    # EVERY row of the shipped table, whatever its first cell says, because the
    # PR claims are what this file is believed on and not every row is an item:
    # the rulings ship as `Q7`/`Q10`, and four hygiene PRs belong to no backlog
    # id at all. Keyed on `#` in the middle cell rather than on the left one, an
    # id-matched sweep silently skipped six PR numbers, four of them changes
    # to the engine.
    ANY_ROW = re.compile(r"^\| ([^|]+?) \| ([^|]*) \| ([^|]*) \|$", re.M)
    PR = re.compile(r"#(\d+)")

    def _text(self):
        return self.BACKLOG.read_text(encoding="utf-8")

    def _open_section(self):
        """The Open section, by its two boundary literals in ONE place.

        Spelled twice, a reader repairing one after a reword can pick a
        different terminator and leave the two halves disagreeing about what the
        list contains, both green.
        """
        text = self._text()
        return text[text.index("\n## Open\n"):
                    text.index("\n## Blocked on an owner ACTION")]

    def _shipped_section(self):
        text = self._text()
        return text[text.index("\n## Shipped\n"):]

    def test_there_is_exactly_one_roadmap_file(self):
        """The whole point of the 2026-09-20 collapse. Eight files, four of them
        stale reviews still asserting figures that had moved, is what made a
        reader unable to say which was current."""
        # rglob, not glob: `docs/roadmap/archive/2026-09-05-staff-agent-review.md`
        # is the likeliest way a second roadmap file comes back, and one level
        # of globbing cannot see it.
        found = sorted(p.name for p in (REPO / "docs" / "roadmap").rglob("*.md"))
        assert found == ["BACKLOG.md"], (
            f"docs/roadmap/ holds {found} — a second file there is a second "
            "place for an item's status to be stated, which is the mirror this "
            "file was collapsed to remove")

    def test_the_two_sections_parse(self):
        """Non-vacuity. Every test below is a set operation, and an empty set
        agrees with everything — a reword that stops matching would otherwise
        report a clean pass over nothing. The later tests carry their own
        guards too; this one covers the two slices they share."""
        opened = set(self.ENTRY.findall(self._open_section()))
        shipped = set(self.ROW.findall(self._shipped_section()))
        # Completing work shrinks this set. Its size is not evidence that the
        # parser still works; every bold bullet entry must be recognized.
        headings = re.findall(r"^- \*\*[^*]+\*\*", self._open_section(), re.M)
        assert headings and len(opened) == len(headings), (
            f"the Open section parses {len(opened)} ids from {len(headings)} "
            f"headings: {sorted(opened)}")
        assert len(shipped) >= 20, (
            f"the shipped index parses as {sorted(i for i, _, _ in shipped)}")

    def test_an_item_in_both_places_says_which_half_is_open(self):
        """The V11 defect, stated as the property that replaces five markers.

        V11 shipped in #95, was recorded in a status paragraph, was never marked
        done in the item list, and the whole of `parse_utils`'s budget machinery
        was nearly written a second time from that list. The rule is that an id
        lives in the Open section or in the shipped index — with ONE exception,
        because a real item can genuinely be half shipped: W3's selection landed
        in #163-#165 and its stamp state machine has not.

        A blanket exemption for that case re-opens the V11 hole, so the
        exception is not "an id may appear twice" but "an id that appears twice
        must SAY so in the shipped row's third column". A reader scanning the
        index for W3 is told there that the row is not the whole story; the
        first draft of this file had the row and the entry agreeing about
        nothing, which is how the same id reads as two separate pieces of work.
        """
        opened = set(self.ENTRY.findall(self._open_section()))
        rows = {i: done for i, _, done in self.ROW.findall(self._shipped_section())}
        # `OPEN:` with the colon, anchored at the start of the cell. A bare
        # substring test let the word through in any sense: with `"OPEN" not
        # in`, a row reading "the OPEN question of where budgets belong"
        # exempted itself, which is the V11 defect waved through by prose — the
        # repo's own "a gate that reads its own comments" shape, in a gate
        # written to replace five markers.
        silent = sorted(i for i in opened & set(rows)
                        if not rows[i].strip().startswith("OPEN:"))
        assert not silent, (
            f"{silent} are in BOTH the Open section and the shipped index, and "
            "the shipped row does not say which half is still open — a reader "
            "cannot tell which is true, and the one who works down the open "
            "list re-implements what already shipped")

    def test_every_shipped_row_names_a_pull_request_that_really_merged(self):
        """The check the nine it replaces never made.

        A row's PR numbers were previously asserted to EXIST as text. Whether
        any of them named a commit was nobody's question, so a typo, an id from
        another repository, or a PR that was closed rather than merged all read
        as a shipped item. The squash-merge convention puts `(#N)` at the end of
        the subject, which is what makes this answerable from the clone.
        """
        subjects = subprocess.run(
            ["git", "log", "--pretty=%s"], cwd=REPO, capture_output=True,
            text=True, check=True).stdout
        merged = set(re.findall(r"\(#(\d+)\)$", subjects, re.M))
        assert len(merged) >= 100, (
            f"only {len(merged)} merge subjects parsed — this clone is shallow "
            "or the convention changed, and the assertion below would pass on "
            "an empty set")

        rows = [(i, prs) for i, prs, _ in self.ANY_ROW.findall(
            self._shipped_section()) if not set(i) <= set("-| ")
            and i.strip() not in {"Item", "Epic"}]
        assert len(rows) >= 20, f"the shipped table parses as {rows}"

        # A row that names NOTHING passes the set difference below — the empty
        # set has no unmerged member. That half was a separate assertion among
        # the nine and is not subsumed by the stronger check, so it is kept.
        silent = sorted(i for i, prs in rows if not self.PR.search(prs))
        assert not silent, (
            f"{silent} are in the shipped index and name no pull request at "
            "all — the row is the only record of where the work landed")

        missing = {}
        for item, prs in rows:
            unmerged = sorted(set(self.PR.findall(prs)) - merged, key=int)
            if unmerged:
                missing[item] = unmerged
        assert not missing, (
            f"{missing} name pull requests with no merge commit in this "
            "repository — a shipped row is a claim that something landed")

    def test_the_open_list_agrees_with_the_plan_and_the_declined_list(self):
        """Rule 2 says an id is in Open or in the shipped index. The file has
        TWO more places that state a status, and the single-source design does
        not make either impossible — it only removes the DONE marker.

        Both were reproduced as holes before this test existed: a shipped item
        could be re-added to the plan table as work to do (which is what the
        old `test_a_finished_sequencing_step_says_so` covered, the V11 shape
        spread across a table instead of an item), and a DECLINED item could
        sit in Open at the same time (which is what the old
        `test_a_ruled_question_leaves_nothing_reading_as_open` covered — Y3 was
        declined at Q11 and re-adding it to Open stayed green).
        """
        text = self._text()
        opened = set(self.ENTRY.findall(self._open_section()))
        plan = text[text.index("\n## The plan, in order\n"):text.index("\n## Open\n")]
        declined = text[text.index("\n## Declined,"):text.index("\n## Rulings\n")]

        scheduled = set(re.findall(rf"\*\*({self._ID})\*\*", plan))
        assert scheduled and len(scheduled) == len(re.findall(r"\*\*[A-Z]+\d+\*\*", plan)), (
            f"the plan table names {sorted(scheduled)} — it is read by id and "
            "a reword that stops matching checks nothing")
        stale = sorted(scheduled - opened)
        assert not stale, (
            f"{stale} are scheduled in the plan and are not open items — the "
            "plan is what a reader works down, so an id there that has already "
            "shipped is work someone does twice")

        refused = set(re.findall(rf"^- \*\*({self._ID})\b", declined, re.M))
        assert refused, "the declined list parses as empty"
        both = sorted(refused & opened)
        assert not both, (
            f"{both} are DECLINED and OPEN at once — a ruling that leaves its "
            "item reading as available work is the defect the rulings section "
            "exists to prevent")

    def test_the_shipped_index_reads_in_epic_order(self):
        """Kept from the nine. A W2 row was once appended to the bottom of the
        table within a minute of being written, and ordering is not status, so
        nothing about a single-source file makes that impossible. Rulings and
        the hygiene row sit after the items on purpose and are not ordered."""
        letters = [i[0] for i, _, _ in self.ROW.findall(self._shipped_section())
                   if i[0] in "VWXYZ"]
        assert len(letters) >= 20, f"parsed {letters}"
        assert letters == sorted(letters), (
            "the shipped index is out of epic order: "
            f"{''.join(letters)} — a row appended to the bottom reads as the "
            "newest work when it is the oldest")

    def test_every_blocked_item_has_an_open_entry(self):
        """The blocked table is written as a TABLE deliberately: an
        `- **ID — text**` bullet is the shape an entry uses, and repeating an id
        in that shape makes one item read as two pieces of work. The table still
        has to name something real."""
        text = self._text()
        blocked = text[text.index("\n## Blocked on an owner ACTION"):
                       text.index("\n## Declined,")]
        named = set(re.findall(rf"^\| \*\*({self._ID})\*\* \|", blocked, re.M))
        assert named, "the blocked-on-owner table parses as empty"
        opened = set(self.ENTRY.findall(self._open_section()))
        orphans = sorted(named - opened)
        assert not orphans, (
            f"{orphans} are blocked on the owner but have no entry in Open — "
            "the table says an item is waiting and nothing says what it is")


# The shared enumerator's entry points. A function that calls one of these has
# handed the walk to `tests/engine_tree.py`, which excludes `__pycache__`,
# spells paths POSIX-style and knows a package's name is its directory.
_ENGINE_TREE_CALLS = frozenset({
    "engine_files", "engine_packages", "engine_top_level_names",
    "engine_module_name", "engine_relpath",
})


# Functions that walk the engine BY HAND on purpose, keyed (module, function)
# and not by bare name — a name alone would exempt a same-named function in
# another module, which is the scope-by-key-name shape this repo has already
# paid for once. Each of these is a CONTROL: it re-derives what
# `tests/engine_tree.py` claims, so calling the helper would make it assert
# that the helper agrees with itself.
_HAND_WALK_CONTROLS = frozenset({
    ("test_portability.py", "test_the_engine_really_does_hold_a_package"),
})


def handrolled_engine_walks(tree, module_name: str):
    """(function name, line) for every function that walks the ENGINE itself.

    `flat_engine_sweeps` below asks a narrower question — is this glob FLAT —
    and answers "descends" for any `rglob`. That was the whole gap: four
    hand-rolled `rglob` walks sat in the suite through the change that exists
    to end them, each re-deriving the `__pycache__` exclusion and the
    `.as_posix()` spelling, and the flatness lint could not see one of them.
    Being correct today is not the property being asserted. The property is
    that the walk is written ONCE, so the next spelling of it cannot be wrong.

    A function offends when it NAMES the engine directory and calls `glob`,
    `rglob` or `iterdir` without asking `tests/engine_tree.py`. Keyed on the
    function rather than on the receiver because the receiver is routinely a
    local: `package = self.LIB / "projection"` names the engine on one line and
    is walked on the next, and a receiver-only rule reads `package.rglob` as
    some unrelated directory.

    A wider root is NOT this rule's business. `test_secure_fs` sweeps
    `agents/`, deliberately one level above the engine, and narrowing that to
    `engine_files()` would drop a module added beside `lib/` — so a rule that
    fired on it would be asking for the defect. `_NAMES_THE_ENGINE` matches
    `lib`, which `agents/` alone does not.
    """
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if (module_name, node.name) in _HAND_WALK_CONTROLS:
            continue
        walks = []
        delegates = False
        names_engine = False
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call):
                if (isinstance(inner.func, ast.Name)
                        and inner.func.id in _ENGINE_TREE_CALLS):
                    delegates = True
                    continue
                if (isinstance(inner.func, ast.Attribute)
                        and inner.func.attr in ("glob", "rglob", "iterdir")):
                    walks.append(inner.lineno)
            # The engine may be named anywhere in the function — bound to a
            # local, passed as an argument, or written out at the call.
            if isinstance(inner, (ast.Attribute, ast.BinOp, ast.Name,
                                  ast.Constant)):
                try:
                    if _NAMES_THE_ENGINE.search(ast.unparse(inner)):
                        names_engine = True
                except Exception:   # pragma: no cover - unparse is total here
                    pass
        if walks and names_engine and not delegates:
            found += [(node.name, line) for line in walks]
    return found


def flat_engine_sweeps(tree):
    """(function name, line) for every flat `glob("*.py")` over the ENGINE.

    Three judgements, each of which a simpler version got wrong:

    BY AST, not by line. The prose in `TestNoSweepOverTheEngineIsFlat` quotes
    `glob("*.py")` while explaining the defect, and a line scan reported the
    explanation as an offender. `rglob` CONTAINS `glob`, so a substring check
    counts a converted sweep as though it were still flat.

    THE RECEIVER decides whether it is the engine's business. `tests/` is
    swept flat on purpose by the host-guard spawn audit, and that directory
    holds no packages — a rule that fires there is firing on correct code.
    Recognised by the receiver's SOURCE naming the engine directory, which is
    every spelling in use: `self.LIB`, a bare `LIB`, and `REPO / "agents" /
    "lib"` written out.

    ACCOUNTED-FOR is not the same as flat. Two sweeps glob the top level and
    then ADD the package names with `iterdir()` — preflight's roster pin and
    the §5 runnable check — which covers the engine completely and is the right
    shape where the question is about module NAMES rather than files. Asking
    `tests/engine_tree.py` counts for the same reason and is the better answer:
    a function that calls `engine_files`, `engine_packages` or
    `engine_top_level_names` has delegated the walk to the one place that gets
    it right, and pairing that with a flat glob is how a test PROVES the two
    spellings differ.

    So the offence is a flat glob in a function that neither descends, nor
    walks the packages itself, nor asks the shared enumerator.
    """
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        descends = False
        flat = []
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            # The helper is called by BARE NAME, not as an attribute, so it is
            # matched before the `Attribute` narrowing below — a check written
            # only for `x.rglob(...)` cannot see `engine_files()` at all.
            if (isinstance(inner.func, ast.Name)
                    and inner.func.id in _ENGINE_TREE_CALLS):
                descends = True
                continue
            if not isinstance(inner.func, ast.Attribute):
                continue
            if inner.func.attr in ("rglob", "iterdir"):
                descends = True
            elif (inner.func.attr == "glob" and inner.args
                  and isinstance(inner.args[0], ast.Constant)
                  and inner.args[0].value == "*.py"
                  and _NAMES_THE_ENGINE.search(ast.unparse(inner.func.value))):
                flat.append(inner.lineno)
        if flat and not descends:
            found += [(node.name, line) for line in flat]
    return found


# `LIB` as a whole word covers `self.LIB` and the bare constant; the quoted
# `"lib"` covers the path written out. Both spellings are in the suite, and a
# receiver naming neither is some other directory.
_NAMES_THE_ENGINE = re.compile(r'\bLIB\b|["\']lib["\']')


class TestTheEngineIsEnumeratedInOnePlace:
    """`tests/engine_tree.py` answers "how do I walk `agents/lib/`" once.

    Every sweep across the suite asks it, and until W2 made the engine a
    directory of packages each could get away with its own spelling. Three of
    them were then wrong in three different ways within one week — a roster
    excluding `__init__` by STEM dropped the whole federal engine, the
    host-guard exemption named `__init__.py` and covered five files, and a
    dict keyed by `p.name` reported `projection/__init__.py` as a filename
    that names nothing. None of the three went red.

    No count is written down here. The number of sweeps is not the claim, it
    changes with every round, and a figure in prose that nothing reads is how
    this block has gone stale before; the claim is that NONE of them walks the
    engine by hand, and `TestNoSweepWalksTheEngineByHand` is what says so.

    The detectors in the classes below are the backstop; this is the fix.
    """

    def test_it_excludes_compiled_bytecode(self, tmp_path, monkeypatch):
        """`__pycache__` is rewritten by every engine run. A sweep that reads
        it is reading yesterday's source, and on this repo's hook it would put
        a pytest module behind the next tool call."""
        import tests.engine_tree as ET
        monkeypatch.setattr(ET, "LIB", tmp_path)
        (tmp_path / "real.py").write_text("x = 1\n")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "real.cpython-312.py").write_text("x = 2\n")
        assert [p.name for p in ET.engine_files()] == ["real.py"]

    def test_a_path_is_posix_spelled(self):
        """`str(Path)` uses the OS separator, so a comparison against a literal
        written with a forward slash fails on Windows — a red test on a correct
        tree, on the platform whose first CI run found two shipped defects."""
        from pathlib import PureWindowsPath

        from tests.engine_tree import LIB, engine_relpath
        assert engine_relpath(LIB / "projection" / "render.py") == \
            "projection/render.py"
        assert str(PureWindowsPath("projection") / "render.py") != \
            "projection/render.py", (
            "the Windows separator no longer differs from POSIX, so this rule "
            "is about nothing — read it again rather than keeping it")

    def test_a_packages_name_is_its_directory_not_init(self):
        """`__init__` is not a name any roster, CLI list or exemption set
        means. Five files are called `__init__.py` and one of them is the
        engine package itself."""
        from tests.engine_tree import GUARD, LIB, engine_files, engine_module_name
        assert engine_module_name(LIB / "projection" / "__init__.py") == "projection"
        assert engine_module_name(LIB / "tie_out.py") == "tie_out"
        assert engine_module_name(LIB / "tax_computations" / "state" / "ny.py") == \
            "tax_computations.state.ny", (
            "a file inside a package needs its DOTTED path — `ny` is not "
            "importable and a second state with a same-named module would "
            "silently replace the first")
        initialisers = [p for p in engine_files() if p.name == "__init__.py"]
        assert len(initialisers) > 1, (
            "the engine holds one initialiser, so none of this is about "
            "anything yet — read this class again rather than keeping it")
        with pytest.raises(ValueError, match="the engine package"):
            engine_module_name(GUARD)

    def test_the_top_level_roster_counts_packages(self):
        """A flat `glob(\"*.py\")` produces this set and a package silently is
        not in it, which is how the engine's largest module came to be missing
        from a list that read as complete."""
        from tests.engine_tree import engine_packages, engine_top_level_names
        names = engine_top_level_names()
        assert "tax_computations" in names and "projection" in names
        assert "__init__" not in names
        assert engine_packages() <= names
        flat = {p.stem for p in (REPO / "agents" / "lib").glob("*.py")}
        assert engine_packages() - flat, (
            "every package is also a flat `.py`, so the two spellings agree "
            "and this assertion is measuring nothing")

    def test_the_helper_is_actually_CALLED(self):
        """Non-vacuity, and the shape the class below exists for: a helper
        written and never used is the detector's own first draft again.

        A CALL, found by AST — not the string `engine_tree` appearing in a
        file. Under a substring rule this module counts as a caller for
        NAMING the helper in its own prose, and an import left behind by a
        reverted conversion counts too. Neither walks the engine through it,
        which is the only thing that makes the helper load-bearing.
        """
        callers = {}
        for path in sorted((REPO / "tests").glob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            sites = sum(1 for node in ast.walk(tree)
                        if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id in _ENGINE_TREE_CALLS)
            if sites:
                callers[path.name] = sites
        assert len(callers) >= 3, (
            f"only {sorted(callers)} CALL tests/engine_tree.py — the sweeps "
            "went back to re-deriving the walk, and the helper is dead code")


class TestNoSweepOverTheEngineIsFlat:
    """`agents/lib/` holds PACKAGES now, and the sweeps that enumerate it must
    descend.

    This replaces `TestTheEngineDirectoryIsFlat`, which asserted the opposite
    and retired itself the moment W2 landed — which is what it was for. Its
    message named the eleven flat `glob("*.py")` sweeps that would each have
    kept passing while covering strictly less: the host-interpreter roster,
    preflight's module set, the workspace-refusal sweep, secure_fs's roster and
    the state-brackets single-reader sweep. All eleven moved in the same change,
    and this is the invariant that keeps them moved.

    The rule is stronger than "the ones we fixed still descend". A NEW sweep
    written flat is the same defect arriving later, and nothing about the tree
    would stop it — `glob` over a packaged engine is simply quieter than it
    looks.
    """

    LIB = REPO / "agents" / "lib"

    def _flat_engine_sweeps(self):
        """Every function in `tests/` that enumerates the ENGINE flatly."""
        offenders = []
        for path in sorted((REPO / "tests").glob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            offenders += [f"{path.name}:{line} in {func}"
                          for func, line in flat_engine_sweeps(tree)]
        return offenders

    def test_no_sweep_over_the_engine_is_flat(self):
        """The rule itself. It was once written and never called — the class
        held a detector, a non-vacuity probe for the detector, and nothing that
        asked it the question, so the invariant it names was enforced by
        nothing while reading as covered.
        """
        offenders = self._flat_engine_sweeps()
        assert not offenders, (
            f"{offenders} enumerate agents/lib/ with a flat `glob(\"*.py\")` "
            "and account for no packages. `glob` does not descend, so each "
            "covers strictly less than it reads as covering the moment a "
            "module moves into one — and stays GREEN while doing it. Use "
            "`rglob` (excluding `__pycache__`), or pair the flat glob with an "
            "`iterdir()` that adds the package names.")

    def test_the_detector_tells_a_flat_sweep_from_an_accounted_one(self):
        """Non-vacuity for `flat_engine_sweeps`, and the three ways earlier
        versions of it failed.

        It matched `glob` inside `rglob` (a substring check on the letters). It
        read PROSE — this class's own explanation quotes the call it forbids.
        And it ignored the RECEIVER, so it reported a flat sweep of `tests/`,
        a directory that holds no packages and is not the engine.

        The probe calls the SHIPPED predicate rather than restating it. The
        version that restated it was the only copy that ran, which is how the
        rule above came to be enforced by nothing.
        """
        def offends(src):
            return [f for f, _line in flat_engine_sweeps(ast.parse(src))]

        assert offends('def f():\n    return LIB.glob("*.py")\n') == ["f"]
        assert not offends('def f():\n    return LIB.rglob("*.py")\n'), (
            "`rglob` must not read as a flat `glob`")
        assert not offends(
            'def f():\n    a = LIB.glob("*.py")\n    b = LIB.iterdir()\n'
            '    return a, b\n'), (
            "a flat glob PAIRED with an explicit package walk accounts for the "
            "engine and is not the defect")
        assert not offends(
            'def f():\n    return Path(__file__).parent.glob("*.py")\n'), (
            "a flat sweep of a directory that is not the engine is not this "
            "rule's business — tests/ holds no packages")
        assert offends(
            'def f():\n    return (REPO / "agents" / "lib").glob("*.py")\n'
        ) == ["f"], "the engine spelled out as a path must still be seen"
        assert not offends(
            'def f():\n    a = LIB.glob("*.py")\n    b = engine_packages()\n'
            '    return a, b\n'), (
            "a function that asks `tests/engine_tree.py` has handed the walk "
            "to the one place that gets it right; pairing that with a flat "
            "glob is how a test PROVES the two spellings differ")
        assert offends(
            'def f():\n    return LIB.glob("*.py")\n    engine_nonsense()\n'), (
            "any bare call now counts as accounting for packages — the "
            "allowlist stopped being an allowlist")

    def test_the_engine_really_does_hold_a_package(self):
        """If it did not, every assertion above would be about nothing."""
        packages = sorted(d.name for d in self.LIB.iterdir()
                          if d.is_dir() and (d / "__init__.py").is_file())
        assert packages, (
            "agents/lib/ holds no packages, so the sweeps could go back to "
            "`glob` and nothing here would notice — read this class again "
            "rather than keeping it")
        assert "tax_computations" in packages

    def test_the_receipt_still_binds_the_whole_engine(self, tmp_path):
        """`receipt.tree_digest` descends, so the validation receipt's engine
        binding covers a submodule. It was the one mechanism the retired guard
        named as already safe, and now that packages exist it is load-bearing
        rather than hypothetical: an edit to `state/ny.py` must move the digest
        that every gate's verdict is bound to.

        Probed on a tree of its own, NOT by editing the engine and restoring it.
        A test that writes to a tracked source file leaves the repository
        modified on any interrupt, and this one would do it to the file whose
        permissions two hooks re-assert. `engine_digest` is one line of
        delegation, so what is left to check about it is that it still
        delegates — read below, not simulated.
        """
        import ast
        import inspect
        from agents.lib import receipt

        nested = tmp_path / "pkg" / "state"
        nested.mkdir(parents=True)
        (tmp_path / "top.py").write_text("x = 1\n")
        (nested / "ny.py").write_text("y = 1\n")
        before = receipt.tree_digest(tmp_path)
        (nested / "ny.py").write_text("y = 2\n")
        assert receipt.tree_digest(tmp_path) != before, (
            "editing a file inside a package did not move the tree digest — "
            "every gate verdict would survive a change to the module that "
            "produced it")

        # The CODE, not the docstring — which says "digest over
        # `agents/lib/*.py`" and would satisfy any substring check while
        # calling something else entirely.
        tree = ast.parse(textwrap.dedent(inspect.getsource(receipt.engine_digest)))
        called = {node.func.id for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        assert "tree_digest" in called, (
            f"receipt.engine_digest calls {sorted(called)} — it must go through "
            "`tree_digest`, which is what descends; a digest built any other "
            "way narrows to the top level the day the engine is packaged")


class TestNoSweepWalksTheEngineByHand:
    """One spelling of the walk, not sixteen correct ones.

    `TestNoSweepOverTheEngineIsFlat` above asks whether a sweep DESCENDS, and
    answers yes for any `rglob`. That is the narrower question, and the gap it
    left was not hypothetical: four hand-rolled `rglob` walks sat in the suite
    through the change that converted the others, each re-deriving the
    `__pycache__` exclusion and the `.as_posix()` spelling, and the flatness
    lint reported every one of them as fine. Three reviewers found them; the
    lint could not.

    So this is the stronger rule the backlog entry actually asked for — making
    a hand-rolled walk IMPOSSIBLE rather than merely unnecessary. It is the
    shape `state_brackets_for_status` already earned: one accessor, plus a
    structural sweep forbidding anyone else to take the raw value. Correct
    today is not the property; written once is.
    """

    def _hand_walks(self):
        offenders = []
        for path in sorted((REPO / "tests").glob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            offenders += [f"{path.name}:{line} in {func}"
                          for func, line in handrolled_engine_walks(
                              tree, path.name)]
        return offenders

    def test_no_test_module_walks_the_engine_itself(self):
        assert not self._hand_walks(), (
            f"{self._hand_walks()} name agents/lib/ and walk it with "
            "`glob`/`rglob`/`iterdir` instead of asking `tests/engine_tree.py`. "
            "Every such walk re-derives the `__pycache__` exclusion, the POSIX "
            "path spelling and a package's module NAME, and the three of them "
            "that were wrong were wrong in three different ways inside one "
            "week. Call `engine_files()` — it takes a subtree for a question "
            "about one package — or add the function to `_HAND_WALK_CONTROLS` "
            "with the reason it must re-derive the answer.")

    def test_the_detector_sees_a_hand_walk_and_spares_a_delegated_one(self):
        """Cry-wolf validation in both directions, since a rule this broad is
        the kind that gets watered down the first time it fires on correct
        code."""
        def walks(src):
            return [f for f, _ in handrolled_engine_walks(
                ast.parse(textwrap.dedent(src)), "probe.py")]

        assert walks('def f():\n    return LIB.rglob("*.py")\n') == ["f"], (
            "a bare `rglob` over the engine is the exact shape that survived "
            "the conversion — if this passes, so did they")
        assert walks('def f():\n    p = self.LIB / "projection"\n'
                     '    return p.rglob("*.py")\n') == ["f"], (
            "the engine named on one line and walked on the next is how two "
            "of the four hid; a receiver-only rule reads `p` as some other "
            "directory")
        assert not walks('def f():\n    return engine_files()\n'), (
            "delegating to the helper must not offend")
        assert not walks('def f():\n    return engine_files(LIB / "x")\n'), (
            "a SUBTREE question delegates too — `engine_files` takes a root, "
            "and a rule that refused it would push callers back to `rglob`")
        assert not walks(
            'def f():\n    return Path(REPO, "agents").rglob("*.py")\n'), (
            "`agents/` is deliberately WIDER than the engine — test_secure_fs "
            "sweeps it so a module added beside `lib/` is still seen, and "
            "narrowing that to `engine_files()` would be the defect, not the "
            "fix")
        # NOT a real directory name: the hook's own path-literal locator reads
        # `REPO / "<name>"` out of this module's source and would report the
        # probe as a genuine read, asking for an arm that covers a string in a
        # test fixture.
        assert not walks(
            'def f():\n    return (REPO / "elsewhere").glob("*.md")\n'), (
            "some other directory is nobody's business here")

    def test_the_controls_are_named_and_still_exist(self):
        """An exemption for a function that has been renamed or deleted is an
        exemption for nothing, and reads as a considered decision."""
        assert _HAND_WALK_CONTROLS, (
            "no controls are exempt, so the pair-keying above and this test "
            "are asserting nothing — read the class again rather than keeping "
            "it")
        for module, func in sorted(_HAND_WALK_CONTROLS):
            path = REPO / "tests" / module
            assert path.is_file(), f"{module} no longer exists"
            names = {n.name for n in ast.walk(ast.parse(
                path.read_text(encoding="utf-8")))
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            assert func in names, (
                f"{module} holds no `{func}` — the exemption survives its "
                "subject and would silently excuse a future function that "
                "takes the name")


class TestEveryModuleASkillInvokesIsRunnable:
    """AGENTS.md §5's rule, narrowed by owner ruling (2026-09-12) and made TRUE
    in the same change.

    It used to read "Every module exposes a `_main` CLI entry point", and that
    was already FALSE: seven modules have no `def _main` at all — `archive`,
    `crypto_importer`, `irs_dates`, `parse_utils`, `secure_fs`,
    `tax_law_fetcher`, and `tax_computations`, which is the module §5 names
    first. Each does its CLI work inline under `__main__` or through a
    differently-named `main`, and nothing checked the sentence, so it sat there
    describing a shape the engine did not have.

    What the rule is FOR is that a skill can always run what it names. That is
    the version enforced here, derived from the skills rather than listed: a
    module a skill invokes must be runnable, and one nothing invokes needs no
    entry point. W2 — splitting the engine into `federal/` and `state/`
    packages of pure functions — is the change that made the distinction
    matter, and it could not have been made honestly against the old sentence.
    """

    LIB = REPO / "agents" / "lib"
    # `sh agents/lib/run.sh -m agents.lib.X` and `... agents/lib/X.py`, the two
    # forms the skills use. Both spellings, because a skill that switched from
    # one to the other would otherwise drop out of this set silently — the
    # audit `docs/contributing/skill-catalog.md` says a grep cannot do.
    INVOCATION = re.compile(
        r"run\.sh\s+(?:-m\s+agents\.lib\.([a-z_]+)|agents/lib/([a-z_]+)\.py)")

    def _invoked(self) -> "set[str]":
        found = set()
        for directory in sorted(SKILLS_DIR.iterdir()):
            if not directory.is_dir():
                continue
            for match in self.INVOCATION.finditer(skill_text(directory.name)):
                found.add(match.group(1) or match.group(2))
        return found

    def test_the_set_is_derived_and_substantial(self):
        invoked = self._invoked()
        assert len(invoked) >= 20, (
            f"only {sorted(invoked)} parse as skill-invoked — the pattern no "
            "longer matches the skills' own commands, and everything below is "
            "then asserting something about nothing")
        assert "preflight" in invoked and "capabilities" in invoked

    def _unrunnable_reason(self, name: str) -> str:
        """Empty if `run.sh -m agents.lib.<name>` reaches an entry point.

        RUNNABLE, not `_main` by name — what the entry point calls is the
        module's own business. Two shapes answer that with different files. A
        MODULE announces its entry point with `if __name__ == "__main__":`. A
        PACKAGE cannot: `python -m` on one runs its `__main__.py`, which
        carries no guard because nothing imports it for its contents. Asking a
        package's `__init__.py` for the guard reports a runnable package as
        unrunnable, and asking for a `<name>.py` that W2 turned into a
        directory reports it as not a module at all — a refusal of the one
        command AGENTS.md §8 tells a reader to run.
        """
        package = self.LIB / name
        if (package / "__init__.py").is_file():
            if (package / "__main__.py").is_file():
                return ""
            return (f"{name} is a package with no `__main__.py` — a package "
                    "cannot be executed the way a module can")
        module = self.LIB / f"{name}.py"
        if not module.is_file():
            return f"{name} is neither a module nor a package under agents/lib/"
        if '__name__ == "__main__"' not in module.read_text(encoding="utf-8"):
            return f"{name} has no `__main__` block"
        return ""

    def test_every_one_of_them_runs(self):
        missing = [reason for name in sorted(self._invoked())
                   if (reason := self._unrunnable_reason(name))]
        assert not missing, (
            f"{missing} — invoked by a skill and not runnable, so the command "
            "in the skill fails at the shell. A skill must always be able to "
            "run what it names.")

    def test_the_check_reads_a_package_by_its_entry_point(self, tmp_path,
                                                          monkeypatch):
        """Non-vacuity for the PACKAGE branch, which no skill exercises today.

        `tax_computations` is a package and nothing under `.agents/skills/`
        names it — AGENTS.md §8 does, and that is not a skill — so the branch
        W2 added would sit unrun until the first skill invoked it, which is the
        moment being wrong about it costs something. Both directions are
        checked here instead: the shipped package passes, and a package with no
        entry point does not.
        """
        assert self._unrunnable_reason("tax_computations") == "", (
            "the shipped package reads as unrunnable — `run.sh -m "
            "agents.lib.tax_computations` is AGENTS.md §8's own command")
        monkeypatch.setattr(type(self), "LIB", tmp_path)
        (tmp_path / "pure").mkdir()
        (tmp_path / "pure" / "__init__.py").write_text("x = 1\n")
        assert "no `__main__.py`" in self._unrunnable_reason("pure")
        (tmp_path / "pure" / "__main__.py").write_text("print(1)\n")
        assert self._unrunnable_reason("pure") == ""
        (tmp_path / "quiet.py").write_text("def _main():\n    pass\n")
        assert "no `__main__` block" in self._unrunnable_reason("quiet")
        assert "neither a module nor a package" in self._unrunnable_reason("gone")

    def test_a_module_no_skill_invokes_is_OUT_OF_SCOPE_for_the_check(self):
        """The half the ruling added, and the half W2 needs.

        Pinned as the CHECKER'S SCOPE and not as a fact about today's tree: at
        the time of the ruling every uninvoked module still happened to carry a
        `__main__` block, so an assertion that one does not would have been
        asserting the future. W2's `federal/` and `state/` submodules are the
        first case, and what has to be true BEFORE they land is that the
        runnable check does not reach them.
        """
        # Packages too, by DIRECTORY name: `tax_computations/__init__.py` has
        # the stem `__init__`, which names nothing a skill could invoke, so a
        # flat glob leaves the engine's largest module out of the set that
        # `invoked` is compared against. That is what `engine_top_level_names`
        # answers, and asking it is how this stays true of the NEXT package.
        on_disk = engine_top_level_names()
        invoked = self._invoked()
        assert invoked < on_disk, (
            "every module on disk is skill-invoked, so the exemption has no "
            "subject and this test is asserting nothing")
        # The scope is the derived set, not the directory. Read from the
        # check's own source so the two cannot drift: a check widened to the
        # whole directory would re-impose the rule the ruling narrowed.
        import inspect
        body = inspect.getsource(self.test_every_one_of_them_runs)
        assert "self._invoked()" in body and "glob(" not in body, (
            "the runnable check no longer scopes itself to the skill-invoked "
            "set — it would refuse a pure-function submodule for having no "
            "entry point, which is exactly what the ruling exempted")

    def test_the_old_sentence_is_gone_from_AGENTS_md(self):
        """The regression this pins is the sentence coming back, because it
        reads plausibly and was false for seven modules."""
        text = (REPO / "AGENTS.md").read_text(encoding="utf-8")
        assert "Every module exposes a `_main`" not in text
        flat = re.sub(r"\s+", " ", text)
        assert "Every module a SKILL invokes is RUNNABLE through the launcher" in flat

    def test_the_seven_that_disproved_the_old_rule_still_do(self):
        """Named, so the claim above is checkable rather than historical."""
        # By MODULE NAME, which is what §5's sentence is about — and a PACKAGE
        # reads as its directory name, `tax_computations/__init__.py` being
        # `tax_computations` and not the stem `__init__`, which names nothing.
        # W2 made the engine's largest module one, and it is the case this test
        # is about. `__main__.py` is excluded because it IS the entry point the
        # sentence asks for, under a different spelling than `_main`.
        without_main = {
            engine_module_name(p) for p in engine_files()
            if p != ENGINE_GUARD and p.name != "__main__.py"
            and "\ndef _main" not in p.read_text(encoding="utf-8")}
        assert "tax_computations" in without_main, (
            "the module §5 named first is the sharpest case: it has no "
            "`_main`, and the old sentence said every module did")
        assert len(without_main) >= 5
