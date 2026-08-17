"""Read a skill's full instruction text — `SKILL.md` plus the files it links.

A skill is a DIRECTORY, not a file. Progressive disclosure moves step detail out
of `SKILL.md` into `references/*.md`, so a doc-sync assertion that reads
`SKILL.md` alone silently stops covering the text it was written to pin: it goes
green because the content moved, which is indistinguishable from the content
having been deleted — the exact failure those assertions exist to catch.

Reading the whole directory is only sound because
`test_portability.py::TestSkillLayout::test_reference_files_are_one_level_deep`
pins that every reference file is reachable from `SKILL.md` in ONE hop. Content
found here is therefore content the agent can actually load; without that paired
check this helper would happily count text buried where nothing links to it.
"""
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent.parent / ".agents" / "skills"


def skill_text(name: str) -> str:
    """`SKILL.md` and every `references/*.md` for `name`, concatenated."""
    skill_dir = SKILLS_DIR / name
    parts = [(skill_dir / "SKILL.md").read_text()]
    parts += [p.read_text() for p in sorted((skill_dir / "references").glob("*.md"))]
    return "\n".join(parts)


# The two skills that work ON the toolkit rather than on a return. Defined here,
# beside `skill_text()`, because every module that needs it already imports from
# this one — it was duplicated verbatim in test_portability and test_preflight,
# and a third contributor skill registered in one copy would have left the other
# demanding an engine-preflight gate in tooling that is exempt by design.
CONTRIBUTOR_SKILLS = {"codex-loop", "state-module"}
