from __future__ import annotations

import posixpath
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Generator

if TYPE_CHECKING:
    from av.container import InputContainer


def is_sftp_source(source: str | Path) -> bool:
    """Return whether the source has an SFTP URL prefix."""
    return isinstance(source, str) and source.lower().startswith("sftp://")


def resolve_video_source(media_root: str | Path, stored_path: str | Path) -> str | Path:
    """Resolve a video reference without treating SFTP URLs as local paths."""
    if is_sftp_source(stored_path):
        return stored_path

    path = Path(stored_path)
    if path.is_absolute():
        return path

    if is_sftp_source(media_root):
        scheme, remote = str(media_root).split("://", 1)
        authority, _, remote_path = remote.partition("/")
        # FFmpeg's SFTP protocol passes the raw path to the server. Keep literal
        # spaces, percent signs, '?' and '#' rather than URL-encoding filenames.
        joined_path = posixpath.join("/", remote_path, path.as_posix())
        return f"{scheme}://{authority}{joined_path}"

    return Path(media_root).expanduser() / path.expanduser()


@contextmanager
def open_video_container(file_path: str | Path) -> Generator[InputContainer, None, None]:
    """Open a local file or SFTP URL, closing it when the caller exits."""
    import av

    with av.open(str(file_path), mode="r") as container:
        yield container
