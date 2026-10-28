"""Repository Agent Skill discovery, policy and lazy loading."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agent_skills._vendor.wikifunctions import unique_strings

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
        "3d-print-preflight",
        "a1-mini-print-watch",
        "github-pr-readiness",
        "github-ci-diagnosis",
        "issue-to-pr",
        "runtime-debugging",
        "cloudru-change",
        "security-review",
        "docs-sync",
        "release-readiness",
    },
    "backend-engineer": {
        "3d-print-preflight",
        "issue-to-pr",
        "github-ci-diagnosis",
        "runtime-debugging",
        "security-review",
    },
    "frontend-engineer": {"issue-to-pr", "github-ci-diagnosis", "security-review"},
    "android-engineer": {"issue-to-pr", "github-ci-diagnosis", "security-review"},
    "test-engineer": {"github-ci-diagnosis", "issue-to-pr", "runtime-debugging"},
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
    "operations-observer": {
        "github-pr-readiness",
        "github-ci-diagnosis",
        "security-review",
    },
    "process-governor": {
        "github-ci-diagnosis",
        "issue-to-pr",
        "security-review",
        "docs-sync",
    },
    "logos": {
        "3d-print-preflight",
        "a1-mini-print-watch",
        "github-pr-readiness",
        "github-ci-diagnosis",
        "issue-to-pr",
        "runtime-debugging",
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


# WIKIFUNCTIONS-CANDIDATE WF-02: https://www.wikifunctions.org/wiki/Z10193 + Z10079 + Z10047.
# Собрать нормализатор роли из Python regex/trim/lowercase; сохранить None и str(role),
# шаблон [^a-z0-9]+, отдельную обрезку дефисов по краям и None для пустого результата.
def _normalize_role(role: str | None) -> str | None:
    if role is None:
        return None
    normalized = re.sub(r"[^a-z0-9]+", "-", str(role).strip().lower()).strip("-")
    return normalized or None


def _parse_frontmatter_lines(lines: list[str], source: str) -> dict[str, Any]:
    if not lines or lines[0].strip() != "---":
        raise SkillFormatError(f"{source}: SKILL.md must start with YAML frontmatter")
    closing_index = next(
        (i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None
    )
    if closing_index is None:
        raise SkillFormatError(f"{source}: frontmatter is not closed")
    document = "\n".join(line.rstrip("\r\n") for line in lines[1:closing_index])
    try:
        metadata = yaml.safe_load(document)
    except yaml.YAMLError as exc:
        raise SkillFormatError(f"{source}: invalid YAML frontmatter") from exc
    if not isinstance(metadata, dict) or not all(
        isinstance(key, str) for key in metadata
    ):
        raise SkillFormatError(
            f"{source}: frontmatter must be a mapping with string keys"
        )
    for key in ("name", "description"):
        if not isinstance(metadata.get(key), str) or not metadata[key].strip():
            raise SkillFormatError(
                f"{source}: frontmatter field {key} must be a non-empty string"
            )
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

    def _read_metadata(self, name: str) -> dict[str, Any]:
        path = self._skill_path(name)
        if not path.is_file():
            raise SkillNotFoundError(f"skill not found: {name}")
        lines: list[str] = []
        size = 0
        with path.open("r", encoding="utf-8") as handle:
            for _ in range(MAX_FRONTMATTER_LINES):
                line = handle.readline(MAX_SKILL_BYTES - size + 1)
                if line == "":
                    break
                size += len(line.encode("utf-8"))
                if size > MAX_SKILL_BYTES:
                    raise SkillFormatError(
                        f"{name}: frontmatter exceeds {MAX_SKILL_BYTES} bytes"
                    )
                lines.append(line)
                if len(lines) > 1 and line.strip() == "---":
                    break
        metadata = _parse_frontmatter_lines(lines, str(path))
        if metadata["name"] != name:
            raise SkillFormatError(
                f"{name}: frontmatter name must match directory name"
            )
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

    def _allowed_for_role(self, role: str | None) -> set[str] | None:
        normalized = _normalize_role(role)
        if normalized is None:
            return None
        if normalized not in ROLE_SKILL_ALLOWLIST:
            raise SkillPolicyError(f"unknown role: {role}")
        return ROLE_SKILL_ALLOWLIST[normalized]

    def catalog(self, role: str | None = None) -> list[dict[str, str]]:
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
                        (self.root / name / "SKILL.md").relative_to(
                            self.root.parent.parent
                        )
                    ),
                }
            )
        return result

    def load(self, name: str, role: str | None = None) -> Skill:
        allowed = self._allowed_for_role(role)
        if allowed is not None and name not in allowed:
            raise SkillPolicyError(
                f"role {_normalize_role(role)} cannot use skill {name}"
            )
        metadata = self._read_metadata(name)
        path = self._skill_path(name)
        with path.open("rb") as handle:
            raw = handle.read(MAX_SKILL_BYTES + 1)
        if len(raw) > MAX_SKILL_BYTES:
            raise SkillFormatError(f"{name}: SKILL.md exceeds {MAX_SKILL_BYTES} bytes")
        text = raw.decode("utf-8")
        lines = text.splitlines()
        closing_index = next(
            (i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
            None,
        )
        if closing_index is None:
            raise SkillFormatError(f"{name}: frontmatter is not closed")
        body = "\n".join(lines[closing_index + 1 :]).strip()
        for section in REQUIRED_SECTIONS:
            if not re.search(
                rf"^##\s+{re.escape(section)}\s*$",
                body,
                flags=re.IGNORECASE | re.MULTILINE,
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

    def load_many(self, names: Iterable[str], role: str | None = None) -> list[Skill]:
        unique: list[str] = []
        for name in unique_strings(names):
            if len(unique) >= MAX_SELECTED_SKILLS:
                raise SkillPolicyError(
                    f"at most {MAX_SELECTED_SKILLS} skills may be selected"
                )
            unique.append(name)
        return [self.load(name, role=role) for name in unique]

    def validate_all(self) -> list[dict[str, str]]:
        names = self._names()
        if not names:
            raise SkillFormatError(f"no skills found under {self.root}")
        return [self.load(name).metadata() for name in names]


def compose_skill_instructions(
    skills: Iterable[Skill], *, base_instructions: str | None = None
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
