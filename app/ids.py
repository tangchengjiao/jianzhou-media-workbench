"""Collision-resistant identifiers for persisted domain records."""

from __future__ import annotations

import uuid


def new_id(prefix: str) -> str:
    """Return an opaque stable id while keeping the domain prefix readable."""
    normalized = prefix.strip().rstrip("-")
    return f"{normalized}-{uuid.uuid4().hex}"
