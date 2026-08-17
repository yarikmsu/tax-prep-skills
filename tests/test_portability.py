"""Portability doc-sync suite — machine-checks the multi-agent layout invariants.

Every assertion here guards a claim made to users of a NON-Claude agent, where we
have no CI coverage (no vendor CLI licenses in CI, spec section 9). A silently-broken
symlink or a dropped frontmatter field would make the toolkit undiscoverable on that
agent with no other signal — the user just sees zero skills and no error.
"""
import os
import re
from pathlib import Path

from tests.skill_text import CONTRIBUTOR_SKILLS, skill_text

REPO = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO / ".agents" / "skills"
CLAUDE_SKILLS = REPO / ".claude" / "skills"

# The toolkit itself — what a taxpayer clones this repository for.
PRODUCT_SKILLS = {
    "tax-amend", "tax-archive", "tax-calendar", "tax-compute", "tax-extend",
    "tax-fbar", "tax-generate", "tax-harvest", "tax-parse", "tax-parse-crypto",
    "tax-project", "tax-ptet", "tax-retirement", "tax-review", "tax-rollover",
    "tax-roth-convert", "tax-scenario", "tax-setup", "tax-verify", "tax-withholding",
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
        push_at = body.index('git push --no-verify')
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
        push_at = body.index('git push --no-verify')
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
                       "tax_calendar_2026.ics",
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
            result = subprocess.run(
                [sys.executable, f"agents/lib/{name}.py"],
                cwd=str(REPO), capture_output=True, text=True, timeout=120)
            assert result.returncode == 0, (
                f"{name}.py is documented as self-testing when run with no "
                f"arguments, and it exited {result.returncode}: "
                f"{result.stderr[-300:]}")

    def test_both_documents_name_the_same_modules(self):
        agents = (REPO / "AGENTS.md").read_text()
        contributing = (REPO / "CONTRIBUTING.md").read_text()
        for name in self.SELF_TESTING:
            assert f"`{name}`" in agents, f"AGENTS.md §5 does not name {name}"
            assert f"`{name}`" in contributing, f"CONTRIBUTING.md does not name {name}"
