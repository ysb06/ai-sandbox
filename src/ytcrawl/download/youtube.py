from __future__ import annotations

import random
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError
from yt_dlp.version import __version__ as YT_DLP_VERSION

from ytcrawl.download.errors import LiveVideoExcludedError

VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_WATCH_URL = "https://www.youtube.com/watch?v={video_id}"
DOWNLOAD_FORMAT = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best[ext=mp4]/best"
DOWNLOADER_LABEL = f"yt-dlp={YT_DLP_VERSION}"
TEMPORARY_SUFFIXES = (".part", ".ytdl", ".tmp", ".temp")
THROTTLING_HTTP_STATUSES = frozenset({403, 429})
EXCLUDED_YTDLP_LIVE_STATUSES = frozenset(
    {"is_live", "is_upcoming", "post_live"}
)
HTTP_STATUS_PATTERNS = (
    re.compile(r"\bHTTP(?:\s*Error)?\s+(403|429)\b", re.IGNORECASE),
    re.compile(r"\b(403)\s*:\s*Forbidden\b", re.IGNORECASE),
    re.compile(r"\b(429)\s*:\s*Too\s+Many\s+Requests\b", re.IGNORECASE),
)


class YouTubeDownloadError(RuntimeError):
    def __init__(self, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status


def _ordinary_retry_sleep(n: int) -> float:
    del n
    return 5.0


def _iter_exception_chain(exc: BaseException) -> Iterator[BaseException]:
    pending: list[BaseException] = [exc]
    seen: set[int] = set()

    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current

        linked_exceptions = (
            current.__cause__,
            current.__context__,
            getattr(current, "cause", None),
        )
        for linked_exc in linked_exceptions:
            if isinstance(linked_exc, BaseException):
                pending.append(linked_exc)

        if isinstance(current, DownloadError):
            exc_info = current.exc_info
            if (
                exc_info
                and len(exc_info) > 1
                and isinstance(exc_info[1], BaseException)
            ):
                pending.append(exc_info[1])


def _coerce_throttling_http_status(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        status = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return status if status in THROTTLING_HTTP_STATUSES else None


def _extract_http_status_from_message(message: str) -> int | None:
    for pattern in HTTP_STATUS_PATTERNS:
        if match := pattern.search(message):
            return int(match.group(1))
    return None


def _extract_http_status(exc: BaseException) -> int | None:
    exception_chain = tuple(_iter_exception_chain(exc))

    for current in exception_chain:
        for attribute in ("http_status", "status", "status_code", "code"):
            status = _coerce_throttling_http_status(
                getattr(current, attribute, None)
            )
            if status is not None:
                return status

        response = getattr(current, "response", None)
        if response is not None:
            for attribute in ("status", "status_code"):
                status = _coerce_throttling_http_status(
                    getattr(response, attribute, None)
                )
                if status is not None:
                    return status

    for current in exception_chain:
        status = _extract_http_status_from_message(str(current))
        if status is not None:
            return status

    return None


class _HttpStatusTracker:
    def __init__(self) -> None:
        self.http_status: int | None = None

    def observe(self, message: object) -> None:
        if self.http_status is not None:
            return
        self.http_status = _extract_http_status_from_message(str(message))


class _LiveStatusTracker:
    def __init__(self) -> None:
        self.live_status: str | None = None

    def match_filter(
        self,
        info_dict: dict[str, Any],
        *,
        incomplete: bool,
    ) -> str | None:
        del incomplete
        value = info_dict.get("live_status")
        live_status = value.lower() if isinstance(value, str) else None
        if live_status is None and info_dict.get("is_live") is True:
            live_status = "is_live"
        if live_status not in EXCLUDED_YTDLP_LIVE_STATUSES:
            return None
        self.live_status = live_status
        video_id = info_dict.get("id") or "<unknown>"
        return (
            f"Skipping live video {video_id}: "
            f"live_status={live_status}"
        )


def _track_yt_dlp_output(ydl: YoutubeDL, tracker: _HttpStatusTracker) -> None:
    for method_name in ("to_screen", "to_stderr", "to_stdout"):
        original = getattr(ydl, method_name)

        def tracked_output(
            message: object,
            *args: object,
            _original=original,
            **kwargs: object,
        ) -> Any:
            tracker.observe(message)
            return _original(message, *args, **kwargs)

        setattr(ydl, method_name, tracked_output)


def _is_final_download_candidate(path: Path) -> bool:
    if not path.is_file():
        return False
    if any(path.name.endswith(suffix) for suffix in TEMPORARY_SUFFIXES):
        return False
    return path.stat().st_size > 0


def _find_final_download_candidates(output_dir: Path, video_id: str) -> list[Path]:
    return sorted(
        path
        for path in output_dir.glob(f"vid_{video_id}.*")
        if _is_final_download_candidate(path)
    )


def _remove_empty_final_download_candidates(
    output_dir: Path,
    video_id: str,
) -> None:
    for candidate in output_dir.glob(f"vid_{video_id}.*"):
        if not candidate.is_file() or any(
            candidate.name.endswith(suffix) for suffix in TEMPORARY_SUFFIXES
        ):
            continue
        if candidate.stat().st_size == 0:
            candidate.unlink()


def _resolve_downloaded_file(output_dir: Path, video_id: str) -> Path:
    candidates = _find_final_download_candidates(output_dir, video_id)
    if not candidates:
        raise YouTubeDownloadError(
            f"Downloaded file not found for video_id {video_id}."
        )

    mp4_candidates = [path for path in candidates if path.suffix == ".mp4"]
    if mp4_candidates:
        return sorted(mp4_candidates)[0]
    return sorted(candidates)[0]


def download(
    video_id: str,
    output_dir: str | Path,
    overwrite: bool = False,
) -> Path:
    if not VIDEO_ID_PATTERN.fullmatch(video_id):
        raise ValueError(f"Invalid YouTube video_id: {video_id!r}")

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    _remove_empty_final_download_candidates(destination, video_id)

    live_status_tracker = _LiveStatusTracker()
    options: dict[str, Any] = {
        "format": DOWNLOAD_FORMAT,
        "merge_output_format": "mp4",
        "outtmpl": str(destination / f"vid_{video_id}.%(ext)s"),
        "noplaylist": True,
        "overwrites": overwrite,
        "match_filter": live_status_tracker.match_filter,
        "sleep_interval_requests": random.uniform(3.0, 5.0),
        "retries": 1,
        "fragment_retries": 0,
        "extractor_retries": 1,
        "retry_sleep_functions": {
            "http": _ordinary_retry_sleep,
            "extractor": _ordinary_retry_sleep,
        },
        "skip_unavailable_fragments": False,
        "concurrent_fragment_downloads": 1,
        "js_runtimes": {"node": {}},
        "extractor_args": {
            "youtube": {
                "player_client": ["mweb"],
                "fetch_pot": ["auto"],
            },
        },
    }
    url = YOUTUBE_WATCH_URL.format(video_id=video_id)
    status_tracker = _HttpStatusTracker()

    try:
        with YoutubeDL(options) as ydl:
            _track_yt_dlp_output(ydl, status_tracker)
            exit_code = ydl.download([url])
    except DownloadError as exc:
        if live_status_tracker.live_status is not None:
            raise LiveVideoExcludedError(
                video_id,
                live_status_tracker.live_status,
            ) from exc
        raise YouTubeDownloadError(
            str(exc),
            http_status=_extract_http_status(exc) or status_tracker.http_status,
        ) from exc

    if live_status_tracker.live_status is not None:
        raise LiveVideoExcludedError(video_id, live_status_tracker.live_status)

    if exit_code not in (0, None):
        raise YouTubeDownloadError(
            f"yt-dlp failed with exit code {exit_code}.",
            http_status=status_tracker.http_status,
        )

    try:
        return _resolve_downloaded_file(destination, video_id)
    except YouTubeDownloadError as exc:
        if status_tracker.http_status is None:
            raise
        raise YouTubeDownloadError(
            str(exc),
            http_status=status_tracker.http_status,
        ) from exc
