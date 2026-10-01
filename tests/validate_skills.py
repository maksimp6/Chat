"""Fail-closed validation for repository Agent Skills."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_skills.registry import SkillRegistry


def main() -> int:
    skills = SkillRegistry().validate_all()
    print(f"Validated {len(skills)} agent skills")
    for skill in skills:
        print(f"- {skill['name']} ({skill['version']})")

    helper = ROOT / "scripts" / "check_agent_skill_policy.py"
    if helper.exists():
        result = subprocess.run(
            [sys.executable, str(helper), "--root", str(ROOT)],
            capture_output=True,
            text=True,
        )
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        if result.returncode != 0:
            return result.returncode

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
