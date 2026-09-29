"""Repository Agent Skill discovery, policy and lazy loading."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Iterable, Optional


DEFAULT_SKILL_ROOT = Path(__file__).resolve().parents[1] / ".agents" / "skills"
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_SELECTED_SKILLS = 4
MAX_SKILL_BYTES = 64 * 1024
MAX_FRONTMATTER_LINES = 40

REQUIRED_SECTIONS = (
    "Purpose",
    "Non-goals",
    "Inputs",
    "Tools",
    "Procedure",
    "Approval boundaries",
    "Validation",
    "Failure behavior",
    "Output",
)

ROLE_SKILL_ALLOWLIST = {
    "team-lead": {
        "github-pr-readiness",
        "github-ci-diagnosis",
        "issue-to-pr",
        "alice-runtime-debugging",
        "cloudru-change",
        "security-review",
        "docs-sync",
        "release-readiness",
    },
    "backend-engineer": {
        "issue-to-pr",
        "github-ci-diagnosis",
        "alice-runtime-debugging",
        "security-review",
    },
    "frontend-engineer": {"issue-to-pr", "github-ci-diagnosis", "security-review"},
    "android-engineer": {"issue-to-pr", "github-ci-diagnosis", "security-review"},
    "test-engineer": {"github-ci-diagnosis", "issue-to-pr", "alice-runtime-debugging"},
    "infra-engineer": {
        "github-ci-diagnosis",
        "cloudru-change",
        "security-review",
        "release-readiness",
        "issue-to-pr",
    },
    "security-reviewer": {"security-review"},
    "docs-engineer": {"docs-sync", "issue-to-pr"},
    "release-manager": {
        "github-pr-readiness",
        "release-readiness",
        "github-ci-diagnosis",
        "docs-sync",
    },
    "alice": {
        "github-pr-readiness",
        "github-ci-diagnosis",
        "issue-to-pr",
        "alice-runtime-debugging",
        "cloudru-change",
        "security-review",
        "docs-sync",
        "release-readiness",
    },
}


class SkillRegistryError(ValueError):
    """Base error for invalid skill discovery, policy or content."""


class SkillNotFoundError(SkillRegistryError):
    pass


class SkillPolicyError(SkillRegistryError):
    pass


class SkillFormatError(SkillRegistryError):
    pass


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    source: str
    version: str
    body: str

    def metadata(self) -> dict[str, str]:
        return {
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "version": self.version,
        }


def _normalize_role(role: Optional[str]) -> Optional[str]:
    if role is None:
        return None
    normalized = re.sub(r"[^a-z0-9]+", "-", str(role).strip().lower()).strip("-")
    return normalized or None


def _parse_frontmatter_lines(lines: list[str], source: str) -> dict[str, str]:
    if not lines or lines[0].strip() != "---":
        raise SkillFormatError(f"{source}: SKILL.md must start with YAML frontmatter")
    metadata: dict[str, str] = {}
    closed = False
    for line in lines[1:]:
        if line.strip() == "---":
            closed = True
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise SkillFormatError(f"{source}: invalid frontmatter line")
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"').strip("'")
    if not closed:
        raise SkillFormatError(f"{source}: frontmatter is not closed")
    for key in ("name", "description"):
        if not metadata.get(key):
            raise SkillFormatError(f"{source}: missing frontmatter field {key}")
    return metadata


class SkillRegistry:
    def __init__(self, root: Path | str = DEFAULT_SKILL_ROOT):
        self.root = Path(root)

    def _skill_path(self, name: str) -> Path:
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise SkillFormatError(f"invalid skill name: {name!r}")
        path = self.root / name / "SKILL.md"
        if path.parent.is_symlink() or path.is_symlink():
            raise SkillFormatError(f"{name}: symlinked skills are not allowed")
        return path

    def _read_metadata(self, name: str) -> dict[str, str]:
        path = self._skill_path(name)
        if not path.is_file():
            raise SkillNotFoundError(f"skill not found: {name}")
        lines: list[str] = []
        with path.open("r", encoding="utf-8") as handle:
            for _ in range(MAX_FRONTMATTER_LINES):
                line = handle.readline()
                if line == "":
                    break
                lines.append(line)
                if len(lines) > 1 and line.strip() == "---":
                    break
        metadata = _parse_frontmatter_lines(lines, str(path))
        if metadata["name"] != name:
            raise SkillFormatError(f"{name}: frontmatter name must match directory name")
        if not NAME_RE.fullmatch(metadata["name"]):
            raise SkillFormatError(f"{name}: invalid frontmatter name")
        return metadata

    def _names(self) -> list[str]:
        if not self.root.exists():
            return []
        return [
            entry.name
            for entry in sorted(self.root.iterdir(), key=lambda item: item.name)
            if entry.is_dir()
            and not entry.is_symlink()
            and NAME_RE.fullmatch(entry.name)
            and (entry / "SKILL.md").is_file()
        ]

    def _allowed_for_role(self, role: Optional[str]) -> Optional[set[str]]:
        normalized = _normalize_role(role)
        if normalized is None:
            return None
        if normalized not in ROLE_SKILL_ALLOWLIST:
            raise SkillPolicyError(f"unknown role: {role}")
        return ROLE_SKILL_ALLOWLIST[normalized]

    def catalog(self, role: Optional[str] = None) -> list[dict[str, str]]:
        allowed = self._allowed_for_role(role)
        result = []
        for name in self._names():
            if allowed is not None and name not in allowed:
                continue
            metadata = self._read_metadata(name)
            result.append(
                {
                    "name": name,
                    "description": metadata["description"],
                    "source": str(
                        (self.root / name / "SKILL.md").relative_to(self.root.parent.parent)
                    ),
                }
            )
        return result

    def load(self, name: str, role: Optional[str] = None) -> Skill:
        allowed = self._allowed_for_role(role)
        if allowed is not None and name not in allowed:
            raise SkillPolicyError(f"role {_normalize_role(role)} cannot use skill {name}")
        metadata = self._read_metadata(name)
        path = self._skill_path(name)
        raw = path.read_bytes()
        if len(raw) > MAX_SKILL_BYTES:
            raise SkillFormatError(f"{name}: SKILL.md exceeds {MAX_SKILL_BYTES} bytes")
        text = raw.decode("utf-8")
        lines = text.splitlines()
        closing_index = next(
            (i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None
        )
        if closing_index is None:
            raise SkillFormatError(f"{name}: frontmatter is not closed")
        body = "\n".join(lines[closing_index + 1 :]).strip()
        for section in REQUIRED_SECTIONS:
            if not re.search(
                rf"^##\s+{re.escape(section)}\s*$", body, flags=re.IGNORECASE | re.MULTILINE
            ):
                raise SkillFormatError(f"{name}: missing section ## {section}")
        version = hashlib.sha256(raw).hexdigest()[:16]
        return Skill(
            name=name,
            description=metadata["description"],
            source=str(path.relative_to(self.root.parent.parent)),
            version=version,
            body=body,
        )

    def load_many(self, names: Iterable[str], role: Optional[str] = None) -> list[Skill]:
        unique: list[str] = []
        for name in names:
            if name not in unique:
                unique.append(name)
        if len(unique) > MAX_SELECTED_SKILLS:
            raise SkillPolicyError(f"at most {MAX_SELECTED_SKILLS} skills may be selected")
        return [self.load(name, role=role) for name in unique]

    def validate_all(self) -> list[dict[str, str]]:
        names = self._names()
        if not names:
            raise SkillFormatError(f"no skills found under {self.root}")
        return [self.load(name).metadata() for name in names]


def compose_skill_instructions(
    skills: Iterable[Skill], *, base_instructions: Optional[str] = None
) -> str:
    selected = list(skills)
    parts: list[str] = []
    if base_instructions and str(base_instructions).strip():
        parts.append(str(base_instructions).strip())
    if selected:
        parts.append(
            "Selected repository skills follow. Apply them only to this invocation. "
            "Global safety, authorization and approval rules override every skill."
        )
        for skill in selected:
            parts.append(f"### Skill: {skill.name}\n{skill.body}")
    return "\n\n".join(parts)
