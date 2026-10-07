"""Pure YouTube ID validation shared by ingestion and database utilities."""

import re
from collections.abc import Sequence
from pathlib import Path

VIDEO_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{11}")


class VideoIDValidationError(ValueError):
    """An ID file or sequence is empty or contains an invalid YouTube ID."""


def read_video_ids(path: str | Path) -> list[str]:
    """Read all nonblank lines, preserving duplicates for caller-side reporting."""
    lines = Path(path).expanduser().read_text(encoding="utf-8-sig").splitlines()
    ids = []
    for number, line in enumerate(lines, start=1):
        video_id = line.strip()
        if not video_id:
            continue
        if VIDEO_ID_PATTERN.fullmatch(video_id) is None:
            raise VideoIDValidationError(
                f"Invalid YouTube ID at line {number}: {video_id!r}."
            )
        ids.append(video_id)
    if not ids:
        raise VideoIDValidationError("The ID file contains no video IDs.")
    return ids


def unique_video_ids(video_ids: Sequence[str]) -> list[str]:
    """Validate every ID before deduplicating in first-occurrence order."""
    if isinstance(video_ids, (str, bytes)) or not video_ids:
        raise VideoIDValidationError("Expected a nonempty sequence of YouTube IDs.")
    unique: dict[str, None] = {}
    for number, value in enumerate(video_ids, start=1):
        if not isinstance(value, str) or VIDEO_ID_PATTERN.fullmatch(value) is None:
            raise VideoIDValidationError(f"Invalid YouTube ID at position {number}.")
        unique[value] = None
    return list(unique)
