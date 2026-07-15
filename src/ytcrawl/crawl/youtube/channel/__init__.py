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

    pages_fetched = 0
    playlist_item_failures = 0
    while True:
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
            videos.create_videos_from_playlist_items(
                session,
                search_id=run.id,
                items=selection.items,
            )

        pages_fetched += 1
        page_token = response.get("nextPageToken")
        if not page_token:
            break
        page += 1

    with core.session_scope() as session:
        run_ids = youtube_search_runs.find_search_run_ids_by_request_hash(
            session,
            request_hash=request_hash,
        )
        video_records = videos.find_video_records_for_searches(
            session,
            search_ids=run_ids,
        )

    detail_result = details.crawl_youtube_details(api_key, video_records)
    embed_successes, embed_failures = details.save_youtube_embed_codes(detail_result)

    with core.session_scope() as session:
        download_records = videos.find_video_records_by_ids_needing_download(
            session,
            video_ref_ids=[record.id for record in video_records],
        )

    download_successes = 0
    download_failures = 0
    if download_records:
        download_successes, download_failures = download.crawl_youtube_videos(
            str(output_dir),
            download_records,
        )

    print(
        f"Collected {len(video_records)} videos across {len(run_ids)} upload pages "
        f"({pages_fetched} fetched now); "
        f"playlist item failed {playlist_item_failures}; "
        f"details saved {detail_result.saved}, "
        f"detail failed {detail_result.failures}; "
        f"embed codes saved {embed_successes}, "
        f"embed code failed {embed_failures}; "
        f"downloaded {download_successes}, "
        f"download failed {download_failures}."
    )
    return 1 if (
        playlist_item_failures
        or detail_result.failures
        or embed_failures
        or download_failures
    ) else 0
