from __future__ import annotations

import argparse

from ytcrawl.crawl import details, download
from ytcrawl.crawl import search as snippet_search
from ytcrawl.db import core, videos


def crawl_youtube(
    args: argparse.Namespace,
    api_key: str,
    db_url: str,
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
    with core.session_scope() as session:
        download_records = videos.find_video_records_for_search_needing_download(
            session,
            search_id=snippet_result.run_id,
        )
    if download_records and not args.output_dir:
        raise ValueError("--output-dir is required to download videos.")

    download_successes = 0
    download_failures = 0
    if download_records:
        download_successes, download_failures = download.crawl_youtube_videos(
            args.output_dir,
            download_records,
        )

    print(
        f"Saved {snippet_result.item_count} videos from search run "
        f"{snippet_result.run_id}; "
        f"details saved {detail_result.saved}, "
        f"detail failed {detail_result.failures}; "
        f"embed codes saved {embed_successes}, "
        f"embed code failed {embed_failures}; "
        f"downloaded {download_successes}, "
        f"download failed {download_failures}."
    )
    return 1 if (
        detail_result.failures
        or embed_failures
        or download_failures
    ) else 0
