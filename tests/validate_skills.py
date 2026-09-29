"""Fail-closed validation for repository Agent Skills."""

from skill_registry import SkillRegistry


def main() -> int:
    skills = SkillRegistry().validate_all()
    print(f"Validated {len(skills)} agent skills")
    for skill in skills:
        print(f"- {skill['name']} ({skill['version']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
