"""Compatibility repair for text persisted by older decoder versions."""

from __future__ import annotations

from typing import Any


_MOJIBAKE_MARKERS = {chr(0x00C2), chr(0x00C3), chr(0x00E2)}


def repair_legacy_mojibake(value: Any) -> Any:
    """Return legacy UTF-8-as-single-byte text in its intended form.

    This is deliberately a presentation-only compatibility layer. Uploaded
    originals and persisted normalized records remain immutable.
    """
    if isinstance(value, str):
        return _repair_string(value)
    if isinstance(value, list):
        return [repair_legacy_mojibake(item) for item in value]
    if isinstance(value, dict):
        return {
            repair_legacy_mojibake(key): repair_legacy_mojibake(item)
            for key, item in value.items()
        }
    return value


def _repair_string(value: str) -> str:
    if not any(marker in value for marker in _MOJIBAKE_MARKERS):
        return value

    for source_encoding in ("latin-1", "cp1252"):
        try:
            repaired = value.encode(source_encoding).decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            continue
        if repaired != value:
            return repaired
    return value
