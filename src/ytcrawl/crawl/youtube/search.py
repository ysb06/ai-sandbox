from __future__ import annotations

import argparse
from pathlib import Path

from ytcrawl.crawl import details, download
from ytcrawl.crawl import search as snippet_search
from ytcrawl.db import core, videos


def crawl_youtube(
    args: argparse.Namespace,
    api_key: str,
    db_url: str,
    *,
    output_dir: str | Path | None = None,
) -> int:
    core.configure(db_url)
    core.create_all()

    snippet_result = snippet_search.crawl_youtube_snippet(args, api_key)
    if snippet_result.skipped:
        return 0

    detail_result = details.crawl_youtube_details(
        api_key,
        video_records=snippet_result.video_records,
    )
    embed_successes, embed_failures = details.save_youtube_embed_codes(detail_result)

    always_download = getattr(args, "always_download", False)
    if always_download:
        download_records = snippet_result.video_records
    else:
        with core.session_scope() as session:
            download_records = videos.find_video_records_for_search_needing_download(
                session,
                search_id=snippet_result.run_id,
            )

    selected_output_dir = (
        output_dir
        if output_dir is not None
        else getattr(args, "output_dir", None)
    )
    if download_records and not selected_output_dir:
        raise ValueError("output_dir is required to download videos.")

    download_successes = 0
    download_failures = 0
    download_user_declined = 0
    download_deferred = 0
    download_total_failures = 0
    download_halt_error_type = None
    download_attempted_unique = 0
    download_remaining_unique = 0
    if download_records:
        download_result = download.crawl_youtube_videos(
            selected_output_dir,
            download_records,
            continuation_prompt=download.prompt_for_next_batch,
        )
        download_successes = download_result.successes
        download_failures = download_result.failures
        download_user_declined = download_result.user_declined
        download_deferred = download_result.deferred
        download_total_failures = download_result.total_failures
        download_halt_error_type = download_result.halt_error_type
        download_attempted_unique = download_result.attempted_unique_videos
        download_remaining_unique = download_result.remaining_unique_videos

    print(
        f"Saved {snippet_result.item_count} videos from search run "
        f"{snippet_result.run_id}; "
        f"details saved {detail_result.saved}, "
        f"detail failed {detail_result.failures}; "
        f"embed codes saved {embed_successes}, "
        f"embed code failed {embed_failures}; "
        f"downloaded {download_successes}, "
        f"download failed {download_failures}, "
        f"download user declined {download_user_declined}, "
        f"download deferred {download_deferred}; "
        f"download attempted {download_attempted_unique} unique videos, "
        f"download remaining {download_remaining_unique} unique videos."
    )
    return 1 if (
        detail_result.failures
        or embed_failures
        or download_total_failures
        or download_halt_error_type
    ) else 0
