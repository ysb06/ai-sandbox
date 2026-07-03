from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from yt_dlp import YoutubeDL
from yt_dlp.version import __version__ as YT_DLP_VERSION

VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_WATCH_URL = "https://www.youtube.com/watch?v={video_id}"
DOWNLOAD_FORMAT = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best[ext=mp4]/best"
DOWNLOADER_LABEL = f"yt-dlp={YT_DLP_VERSION}"
TEMPORARY_SUFFIXES = (".part", ".ytdl", ".tmp", ".temp")


class YouTubeDownloadError(RuntimeError):
    pass


def _is_final_download_candidate(path: Path) -> bool:
    if not path.is_file():
        return False
    return not any(path.name.endswith(suffix) for suffix in TEMPORARY_SUFFIXES)


def _find_final_download_candidates(output_dir: Path, video_id: str) -> list[Path]:
    return sorted(
        path
        for path in output_dir.glob(f"vid_{video_id}.*")
        if _is_final_download_candidate(path)
    )


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
    existing_candidates = _find_final_download_candidates(destination, video_id)
    if existing_candidates and not overwrite:
        return _resolve_downloaded_file(destination, video_id)
    if overwrite:
        for candidate in existing_candidates:
            candidate.unlink()

    options: dict[str, Any] = {
        "format": DOWNLOAD_FORMAT,
        "merge_output_format": "mp4",
        "outtmpl": str(destination / f"vid_{video_id}.%(ext)s"),
        "noplaylist": True,
        "js_runtimes": {"node": {}},
    }
    url = YOUTUBE_WATCH_URL.format(video_id=video_id)

    with YoutubeDL(options) as ydl:
        exit_code = ydl.download([url])

    if exit_code not in (0, None):
        raise YouTubeDownloadError(f"yt-dlp failed with exit code {exit_code}.")

    return _resolve_downloaded_file(destination, video_id)
