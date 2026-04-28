"""Runtime loader for repo-root skill documents."""

from __future__ import annotations


from src.config import SKILLS_DIR

SKILLS: dict[str, str] = {}


def available_skills() -> list[str]:
    if not SKILLS_DIR.exists():
        return []
    return sorted(path.stem for path in SKILLS_DIR.glob("*.md"))


def load_skill(skill_name: str) -> str | None:
    cached = SKILLS.get(skill_name)
    if cached is not None:
        return cached

    path = SKILLS_DIR / f"{skill_name}.md"
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None
    SKILLS[skill_name] = content
    return content


def load_section(skill_name: str, section: str) -> str | None:
    content = load_skill(skill_name)
    if not content:
        return None

    heading = f"## {section}"
    if content.startswith(heading):
        start = len(heading)
        body = content[start:]
    else:
        token = f"\n{heading}"
        index = content.find(token)
        if index == -1:
            return None
        body = content[index + len(token) :]

    if body.startswith("\n"):
        body = body[1:]
    next_heading = body.find("\n## ")
    section_body = body if next_heading == -1 else body[:next_heading]
    stripped = section_body.strip()
    return stripped or None
