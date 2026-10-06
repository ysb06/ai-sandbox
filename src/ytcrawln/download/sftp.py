"""Download one SFTP file without HTTP, cache, or database responsibilities."""

from __future__ import annotations

import errno
import ipaddress
import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio

_CHUNK_SIZE = 256 * 1024
_PREPARE_TIMEOUT = 30
_READ_TIMEOUT = 30
_CLEANUP_TIMEOUT = 5
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SFTPAddress:
    host: str
    path: str
    username: str | None = None
    port: int | None = None


class SFTPDownloadError(Exception):
    """A remote error containing only a safe stage and reason identifier."""

    def __init__(self, stage: str, reason: str) -> None:
        self.stage = stage
        self.reason = reason
        super().__init__(f"SFTP download failed at {stage} ({reason}).")


class SFTPNotFoundError(SFTPDownloadError):
    """The source does not exist or is not a regular file."""


class SFTPTimeoutError(SFTPDownloadError):
    """A remote operation exceeded its time limit."""


def parse_sftp_source(source: str) -> SFTPAddress:
    """Parse the authority while preserving the remote path byte-for-character.

    Spaces, percent signs, '?' and '#' in the path are literal filename
    characters, matching the existing FFmpeg source resolver. Password-bearing
    URLs are not accepted. IPv6 hosts must use square brackets.
    """
    try:
        if not isinstance(source, str) or not source.lower().startswith("sftp://"):
            raise ValueError
        if any(ord(character) < 32 or ord(character) == 127 for character in source):
            raise ValueError
        authority, separator, raw_path = source[7:].partition("/")
        if not authority or not separator:
            raise ValueError
        if any(character.isspace() or character in "?#\\" for character in authority):
            raise ValueError

        username = None
        host_port = authority
        if "@" in authority:
            username, _, host_port = authority.partition("@")
            if not username or ":" in username or "@" in host_port:
                raise ValueError

        port_text = None
        if host_port.startswith("["):
            host, bracket, suffix = host_port[1:].partition("]")
            if not bracket:
                raise ValueError
            ipaddress.IPv6Address(host)
            if suffix:
                if not suffix.startswith(":"):
                    raise ValueError
                port_text = suffix[1:]
        else:
            host, colon, remainder = host_port.partition(":")
            if not host or any(character in "[]:" for character in host):
                raise ValueError
            if colon:
                port_text = remainder

        port = None
        if port_text is not None:
            if not port_text or not port_text.isascii() or not port_text.isdecimal():
                raise ValueError
            # Leading zeros do not require an arbitrarily large int conversion.
            normalized_port = port_text.lstrip("0") or "0"
            if len(normalized_port) > 5:
                raise ValueError
            port = int(normalized_port)
            if not 1 <= port <= 65535:
                raise ValueError
        return SFTPAddress(host, "/" + raw_path, username, port)
    except ValueError:
        raise SFTPDownloadError("parse", "ValueError") from None


def _remote_error(error: Exception, stage: str, ssh: Any) -> SFTPDownloadError:
    if isinstance(error, SFTPDownloadError):
        return error
    if isinstance(error, TimeoutError):
        return SFTPTimeoutError(stage, type(error).__name__)
    if stage in {"stat", "open", "fstat"} and isinstance(
        error,
        (
            ssh.SFTPNoSuchFile,
            ssh.SFTPNoSuchPath,
            ssh.SFTPNotADirectory,
            ssh.SFTPFileIsADirectory,
        ),
    ):
        return SFTPNotFoundError(stage, type(error).__name__)
    return SFTPDownloadError(stage, type(error).__name__)


def _require_regular(attributes: Any, stage: str, ssh: Any) -> None:
    file_type = attributes.type
    if file_type == ssh.FILEXFER_TYPE_REGULAR:
        return
    if file_type in (None, ssh.FILEXFER_TYPE_UNKNOWN):
        permissions = attributes.permissions
        if permissions is None or stat.S_IFMT(permissions) == 0:
            raise SFTPDownloadError(stage, "UnknownFileType")
        if stat.S_ISREG(permissions):
            return
    error_class = SFTPNotFoundError if stage in {"stat", "fstat"} else SFTPDownloadError
    raise error_class(stage, "NotRegularFile")


def _file_size(attributes: Any, stage: str) -> int:
    size = attributes.size
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise SFTPDownloadError(stage, "InvalidFileSize")
    return size


async def _read(remote: Any, size: int, offset: int, ssh: Any) -> bytes:
    try:
        with anyio.fail_after(_READ_TIMEOUT):
            data = await remote.read(size, offset=offset)
        if not isinstance(data, bytes):
            raise SFTPDownloadError("read", "InvalidReadType")
        if len(data) > size:
            raise SFTPDownloadError("read", "ExcessData")
        return data
    except Exception as error:
        raise _remote_error(error, "read", ssh) from None


async def _close_remote(remote: Any, sftp: Any, connection: Any) -> None:
    """Best-effort bounded cleanup, never replacing the primary outcome."""
    if connection is None:
        return
    closed = False
    try:
        with anyio.move_on_after(_CLEANUP_TIMEOUT, shield=True):
            if remote is not None:
                try:
                    await remote.close()
                except Exception:
                    pass
            if sftp is not None:
                try:
                    sftp.exit()
                    await sftp.wait_closed()
                except Exception:
                    pass
            try:
                connection.close()
                await connection.wait_closed()
                closed = True
            except Exception:
                pass
    finally:
        if not closed:
            try:
                connection.abort()
            except Exception:
                pass


async def download(source: str, destination: Path) -> None:
    """Download to a caller-owned temporary file, leaving publication to it.

    The caller must reserve and own destination before invoking this low-level
    function. The file is opened with 'wb'; this function never removes it.
    Remote failures are sanitized SFTPDownloadError subclasses. Local file
    open/write/flush/stat/close failures remain their original OSError types.
    Successful return means the local file has been verified and closed.
    """
    address = parse_sftp_source(source)
    try:
        import asyncssh
    except ImportError as error:
        raise SFTPDownloadError("dependency", type(error).__name__) from None

    options: dict[str, Any] = {"password_auth": False, "kbdint_auth": False}
    if address.username is not None:
        options["username"] = address.username
    if address.port is not None:
        options["port"] = address.port
    # Omitting config/client_keys/known_hosts preserves SSH config, agent/key
    # discovery, and host key verification rather than disabling them with None.
    connection = sftp = remote = None
    try:
        stage = "connect"
        try:
            with anyio.fail_after(_PREPARE_TIMEOUT):
                connection = await asyncssh.connect(address.host, **options)
                stage = "sftp"
                sftp = await connection.start_sftp_client()
                stage = "stat"
                attributes = await sftp.stat(address.path)
                _require_regular(attributes, stage, asyncssh)
                stage = "open"
                remote = await sftp.open(address.path, "rb")
                stage = "fstat"
                attributes = await remote.stat()
                _require_regular(attributes, stage, asyncssh)
                expected_size = _file_size(attributes, stage)
        except Exception as error:
            raise _remote_error(error, stage, asyncssh) from None

        local_file = None
        failed = False
        try:
            # Shield acquisition as well as close so cancellation cannot strand
            # an fd returned from the worker thread before it is assigned here.
            with anyio.CancelScope(shield=True):
                local_file = await anyio.open_file(destination, "wb")
            offset = 0
            while offset < expected_size:
                requested = min(_CHUNK_SIZE, expected_size - offset)
                data = await _read(remote, requested, offset, asyncssh)
                if not data:
                    raise SFTPDownloadError("read", "UnexpectedEOF")
                written = await local_file.write(data)
                if written != len(data):
                    raise OSError(errno.EIO, "Short write to local destination.")
                offset += len(data)

            if await _read(remote, 1, offset, asyncssh):
                raise SFTPDownloadError("verify", "ExcessData")
            try:
                with anyio.fail_after(_READ_TIMEOUT):
                    final_attributes = await remote.stat()
                _require_regular(final_attributes, "verify", asyncssh)
                if _file_size(final_attributes, "verify") != expected_size:
                    raise SFTPDownloadError("verify", "SizeChanged")
            except Exception as error:
                raise _remote_error(error, "verify", asyncssh) from None

            await local_file.flush()
            local_stat = await anyio.to_thread.run_sync(
                os.fstat, local_file.wrapped.fileno()
            )
            if offset != expected_size or local_stat.st_size != expected_size:
                raise OSError(errno.EIO, "Local destination size mismatch.")
        except BaseException:
            failed = True
            raise
        finally:
            if local_file is not None:
                # Do not abandon local close in a worker: the cache caller must
                # not remove or publish a file while its writer is still open.
                with anyio.CancelScope(shield=True):
                    try:
                        await local_file.aclose()
                    except Exception as error:
                        if not failed:
                            raise
                        # In particular, never turn task cancellation into an
                        # HTTP cache error when close also fails. The primary
                        # failure already prevents publication of this file.
                        _LOGGER.warning(
                            "SFTP local cleanup failed stage=close reason=%s",
                            type(error).__name__,
                        )
    finally:
        await _close_remote(remote, sftp, connection)
