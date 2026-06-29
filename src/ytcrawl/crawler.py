from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy.orm import Session

from ytcrawl import db
from ytcrawl.downloader.youtube import download as download_youtube
from ytcrawl.search import youtube as youtube_search


def run_crawl_youtube(
    args: argparse.Namespace,
    api_key: str,
    db_url: str,
) -> int:
    search_result = youtube_search.save_search_youtube(args, api_key, db_url)
    if search_result.skipped:
        print(
            "No next page token for latest matching search run "
            f"{search_result.skipped_run_id}; no new search performed."
        )
        return 0

    failures = 0
    successes = 0
    output_dir = str(args.output_dir)
    engine = db.create_engine_for_url(db_url)
    with Session(engine) as session:
        for record in search_result.video_records:
            video_pk = record.id
            video_id = record.video_id
            if not video_id:
                failures += 1
                print(
                    f"Skipping video row {video_pk}: missing video_id.",
                    file=sys.stderr,
                )
                continue

            try:
                downloaded_path = Path(download_youtube(video_id, output_dir)).resolve()
            except Exception as exc:  # noqa: BLE001 - keep crawling remaining videos.
                failures += 1
                print(f"Failed to download {video_id}: {exc}", file=sys.stderr)
                continue

            video = session.get(db.Video, video_pk)
            if video is not None:
                video.path = str(downloaded_path)
                successes += 1
        session.commit()

    print(
        f"Saved {search_result.item_count} videos from search run {search_result.run_id}; "
        f"downloaded {successes}, failed {failures}."
    )
    return 1 if failures else 0
