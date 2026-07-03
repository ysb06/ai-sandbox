from __future__ import annotations

from ytcrawl.crawl.details import (
    DetailCrawlResult,
    crawl_youtube_details,
    group_video_record_ids_by_video_id as _group_video_record_ids_by_video_id,
    map_video_detail_items_by_video_id as _map_video_detail_items_by_video_id,
)
from ytcrawl.crawl.download import (
    BOT_CHECK_ERROR_TYPE,
    DOWNLOAD_SLEEP_SECONDS_RANGE,
    classify_download_error,
    clean_download_error_message,
    create_download_attempts as _create_download_attempts,
    crawl_youtube_videos,
    mark_download_attempts_failed as _mark_download_attempts_failed,
    sleep_before_next_download as _sleep_before_next_download,
)
from ytcrawl.crawl.search import SnippetCrawlResult, crawl_youtube_snippet
from ytcrawl.crawl.youtube import crawl_youtube

run_crawl_youtube = crawl_youtube
_classify_download_error = classify_download_error
_clean_download_error_message = clean_download_error_message

__all__ = [
    "BOT_CHECK_ERROR_TYPE",
    "DOWNLOAD_SLEEP_SECONDS_RANGE",
    "DetailCrawlResult",
    "SnippetCrawlResult",
    "_classify_download_error",
    "_clean_download_error_message",
    "_create_download_attempts",
    "_group_video_record_ids_by_video_id",
    "_map_video_detail_items_by_video_id",
    "_mark_download_attempts_failed",
    "_sleep_before_next_download",
    "classify_download_error",
    "clean_download_error_message",
    "crawl_youtube",
    "crawl_youtube_details",
    "crawl_youtube_snippet",
    "crawl_youtube_videos",
    "run_crawl_youtube",
]
