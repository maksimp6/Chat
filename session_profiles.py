"""Built-in reusable AI session profiles.

Profiles are immutable templates. Cloning a profile creates a normal persisted
session with copied configuration, so subsequent session changes cannot mutate
the built-in template or another clone.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, Optional

from session_manager import create_session, get_session
from session_runtime import DEFAULT_SESSIONS, ReadyMadeSession


_PROFILE_MAP = {profile.id: profile for profile in DEFAULT_SESSIONS}


def _serialize(profile: ReadyMadeSession) -> Dict[str, Any]:
    data = asdict(profile)
    data["virtual_server"] = asdict(profile.virtual_server)
    return data


def list_profiles() -> list[Dict[str, Any]]:
    return [_serialize(profile) for profile in _PROFILE_MAP.values()]


def get_profile(profile_id: str) -> Optional[Dict[str, Any]]:
    profile = _PROFILE_MAP.get(profile_id)
    return _serialize(profile) if profile else None


def clone_profile(profile_id: str, name: Optional[str] = None) -> Dict[str, Any]:
    profile = _PROFILE_MAP.get(profile_id)
    if profile is None:
        raise KeyError(profile_id)

    clone = profile.clone(name or f"{profile.name} session")
    persisted = create_session(
        clone.id,
        metadata={
            "profile_id": profile.id,
            "profile": _serialize(clone),
        },
    )
    return {
        "session": persisted,
        "profile": _serialize(clone),
    }


def session_profile(session_id: str) -> Optional[Dict[str, Any]]:
    session = get_session(session_id)
    if not session:
        return None
    profile = session.get("metadata", {}).get("profile")
    return profile if isinstance(profile, dict) else None
