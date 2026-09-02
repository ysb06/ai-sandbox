from __future__ import annotations

from pathlib import Path
import sys

from ytcrawl.crawl import details, download
from ytcrawl.db import core, videos, youtube_search_runs
from ytcrawl.search import youtube_uploads


def crawl_youtube_channel(
    *,
    channel_id: str,
    api_key: str,
    db_url: str,
    output_dir: str | Path,
    published_after: str | None = None,
    published_before: str | None = None,
    always_download: bool = False,
) -> int:
    lower_bound, upper_bound = youtube_uploads.parse_published_range(
        published_after,
        published_before,
    )
    request_hash = youtube_uploads.build_request_hash(
        channel_id=channel_id,
        published_after=published_after,
        published_before=published_before,
    )

    core.configure(db_url)
    core.create_all()

    page = 1
    page_token = None
    playlist_id = None


    with core.session_scope() as session:
        latest_run = youtube_search_runs.find_latest_matching_search_run(
            session,
            request_hash=request_hash,
        )
        if latest_run is not None:
            if latest_run.next_page_token is None:
                print(
                    "No next page token for latest matching channel run "
                    f"{latest_run.id}; no new collection performed."
                )
                return 0
            page = latest_run.page + 1
            page_token = latest_run.next_page_token
            playlist_id = latest_run.playlist_id

    youtube = youtube_uploads.create_uploads_client(api_key)
    if not playlist_id:
        playlist_id = youtube_uploads.fetch_uploads_playlist_id(
            youtube,
            channel_id=channel_id,
        )

    playlist_item_failures = 0
    response = youtube_uploads.fetch_uploads_page(
        youtube,
        playlist_id=playlist_id,
        page_token=page_token,
    )
    selection = youtube_uploads.select_playlist_items(
        response,
        published_after=lower_bound,
        published_before=upper_bound,
    )
    for skipped_item in selection.skipped:
        print(
            "Skipping uploads playlist item "
            f"at page {page}, index {skipped_item.index}: "
            f"{skipped_item.reason}.",
            file=sys.stderr,
        )
    playlist_item_failures += len(selection.skipped)

    with core.session_scope() as session:
        run = youtube_search_runs.create_channel_upload_run(
            session,
            channel_id=channel_id,
            playlist_id=playlist_id,
            published_after=published_after,
            published_before=published_before,
            fixed_params=youtube_uploads.FIXED_UPLOADS_PARAMS,
            request_hash=request_hash,
            page=page,
            response=response,
            item_count=len(selection.items),
        )
        video_records = videos.create_videos_from_playlist_items(
            session,
            search_id=run.id,
            items=selection.items,
        )
        run_id = run.id
        item_count = run.item_count

    detail_result = details.crawl_youtube_details(api_key, video_records)
    embed_successes, embed_failures = details.save_youtube_embed_codes(detail_result)

    if always_download:
        download_records = video_records
    else:
        with core.session_scope() as session:
            download_records = videos.find_video_records_by_ids_needing_download(
                session,
                video_ref_ids=[record.id for record in video_records],
            )

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
            str(output_dir),
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
        f"Saved {item_count} videos from channel upload run {run_id}, page {page}; "
        f"playlist item failed {playlist_item_failures}; "
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
        playlist_item_failures
        or detail_result.failures
        or embed_failures
        or download_total_failures
        or download_halt_error_type
    ) else 0
