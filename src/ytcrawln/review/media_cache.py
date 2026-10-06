import errno
import fcntl
import hashlib
import logging
import os
import posixpath
import stat
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import anyio

from ytcrawln.download.sftp import SFTPNotFoundError, download, parse_sftp_source
from ytcrawln.utils import is_sftp_source

_LOGGER = logging.getLogger(__name__)
_LOCK_POLL_INTERVAL = 0.1


class MediaCacheError(Exception):
    """A local cache failure containing no filesystem paths or credentials."""

    def __init__(self, stage: str, reason: str) -> None:
        self.stage = stage
        self.reason = reason
        super().__init__(f"Media cache {stage} failed ({reason}).")


def media_filename(source: str | Path) -> str:
    """Keep the original name for FileResponse's MIME type and disposition."""
    if is_sftp_source(source):
        return posixpath.basename(parse_sftp_source(str(source)).path)
    return Path(source).name


def _completed_cache(path: Path) -> Path | None:
    try:
        info = path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return None
    if not stat.S_ISREG(info.st_mode):
        raise MediaCacheError("cache-check", "NotRegularFile")
    return path


def _prepare_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _reserve_partial(cache_path: Path) -> Path:
    while True:
        path = cache_path.with_name(f"{cache_path.name}.{uuid4().hex}.part")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            # Even an unlikely UUID collision must not overwrite/remove a file
            # which this request did not create.
            continue
        try:
            os.close(fd)
        except OSError:
            _remove_partial(path)
            raise
        return path


def _remove_partial(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        # Never replace the original failure with a best-effort cleanup error.
        _LOGGER.warning("Media cache cleanup failed reason=%s", type(error).__name__)


@asynccontextmanager
async def _cache_lock(path: Path) -> AsyncIterator[None]:
    # Open a separate descriptor per waiter. flock then covers both independent
    # requests in this process and workers sharing the same local cache root.
    with anyio.CancelScope(shield=True):
        lock_file = await anyio.open_file(path, "a+b")
    try:
        while True:
            try:
                fcntl.flock(lock_file.wrapped.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                if error.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
            await anyio.sleep(_LOCK_POLL_INTERVAL)
        yield
    finally:
        # Closing releases the lock even on cancellation. Do not unlink: a new
        # inode would allow newcomers to bypass waiters on the original file.
        with anyio.CancelScope(shield=True):
            await lock_file.aclose()


async def ensure_local_video(
    source: str | Path | None, *, cache_root: Path | None
) -> Path | None:
    """Return a usable local file, downloading an uncached SFTP source once.

    Completed caches are immutable and reused without a server check. No
    disconnect watcher is installed: a browser navigating away may still leave
    its download running to completion. Actual task cancellation runs cleanup.
    """
    if not source:
        return None
    if not is_sftp_source(source):
        local_path = Path(source)
        try:
            return local_path if await anyio.to_thread.run_sync(local_path.is_file) else None
        except OSError as error:
            raise MediaCacheError("local-check", type(error).__name__) from None
    if cache_root is None:
        raise MediaCacheError("configuration", "MissingCacheRoot")

    filename = media_filename(source)
    key = hashlib.sha256(str(source).encode("utf-8")).hexdigest()
    cache_path = cache_root / (key + Path(filename).suffix)
    lock_path = cache_root / f"{key}.lock"
    stage = "cache-check"
    try:
        existing = await anyio.to_thread.run_sync(_completed_cache, cache_path)
        if existing is not None:
            return existing

        stage = "directory"
        await anyio.to_thread.run_sync(_prepare_directory, cache_root)
        stage = "lock"
        async with _cache_lock(lock_path):
            stage = "cache-check"
            existing = await anyio.to_thread.run_sync(_completed_cache, cache_path)
            if existing is not None:
                return existing

            stage = "partial-create"
            with anyio.CancelScope(shield=True):
                partial_path = await anyio.to_thread.run_sync(_reserve_partial, cache_path)
            try:
                stage = "download-write"
                await download(str(source), partial_path)
                stage = "promotion"
                await anyio.to_thread.run_sync(os.replace, partial_path, cache_path)
                return cache_path
            except SFTPNotFoundError:
                return None
            finally:
                with anyio.CancelScope(shield=True):
                    await anyio.to_thread.run_sync(_remove_partial, partial_path)
    except OSError as error:
        raise MediaCacheError(stage, type(error).__name__) from None
