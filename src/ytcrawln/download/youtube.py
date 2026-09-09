import random
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

DOWNLOAD_FORMAT = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best[ext=mp4]/best"
SLEEP_INTERVAL_REQUESTS = (3.0, 7.0)
RETRY_FUNCTION_INTERVAL = 5.0
YOUTUBE_WATCH_URL = "https://www.youtube.com/watch?v={video_id}"


class YouTubeDownloadError(RuntimeError):
    def __init__(self, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status


def match_filter(info_dict: dict[str, Any], *, _: bool) -> str | None:
    value = info_dict.get("live_status")
    live_status = value.lower() if isinstance(value, str) else None
    if live_status is None and info_dict.get("is_live") is True:
        live_status = "is_live"
    if live_status not in ["is_live", "is_upcoming", "post_live"]:
        return None
    live_status = live_status
    video_id = info_dict.get("id") or "<unknown>"

    # 제외 사유를 출력
    return f"Skipping live video {video_id}: " f"live_status={live_status}"


def download(
    video_id: str,
    output_dir: str | Path,
    overwrite: bool = False,
) -> Path | None:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    options: dict[str, Any] = {
        "format": DOWNLOAD_FORMAT,
        "merge_output_format": "mp4",
        "outtmpl": str(destination / f"vid_{video_id}.%(ext)s"),
        "noplaylist": True,
        "overwrites": overwrite,
        "match_filter": match_filter,
        "sleep_interval_requests": random.uniform(*SLEEP_INTERVAL_REQUESTS),
        "retries": 1,
        "fragment_retries": 0,
        "extractor_retries": 1,
        "retry_sleep_functions": {
            "http": RETRY_FUNCTION_INTERVAL,
            "extractor": RETRY_FUNCTION_INTERVAL,
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
    with YoutubeDL(options) as ydl:
        exit_code = ydl.download([url])

    if exit_code not in (0, None):
        raise YouTubeDownloadError(f"yt-dlp failed with exit code {exit_code}.")

    candidates = sorted(path for path in destination.glob(f"vid_{video_id}.*"))
    result = candidates[0] if candidates else None

    return result
