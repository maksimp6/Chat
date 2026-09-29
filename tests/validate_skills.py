"""Fail-closed validation for repository Agent Skills."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_skills.registry import SkillRegistry


def main() -> int:
    skills = SkillRegistry().validate_all()
    print(f"Validated {len(skills)} agent skills")
    for skill in skills:
        print(f"- {skill['name']} ({skill['version']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
