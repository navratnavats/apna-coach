from __future__ import annotations

from typing import Any


def resolve_user_address(living_profile: dict[str, Any]) -> str:
    identity = living_profile.get("identity") or {}
    preferred_title = str(identity.get("preferred_title") or "").strip()
    name = str(identity.get("name") or "").strip()

    if preferred_title:
        normalized = preferred_title.lower()
        if normalized in {"name", "by_name", "name_only"} and name:
            return name
        return preferred_title

    if name:
        return name

    return "Buddy"

