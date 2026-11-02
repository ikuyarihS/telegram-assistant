"""The one Claude Code playbook for TG Kit (playbook.md).

Three readers get this same text, so they cannot drift (tests/test_guide.py):
`tgkit guide` prints it, skills/telegram/SKILL.md is its frontmatter plus this
text, and the app's Claude mode (claude.py) gets it plus a read-only block.
"""

from importlib import resources


def text() -> str:
    return resources.files(__package__).joinpath("playbook.md").read_text(encoding="utf-8")
