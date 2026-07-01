from __future__ import annotations

import argparse
import random
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ytcrawl.db import (
    core,
    video_download_attempts,
    videos,
    videos_detail,
    youtube_search_runs,
)
from ytcrawl.downloader.youtube import (
    DOWNLOADER_LABEL,
    DOWNLOAD_FORMAT,
    YouTubeDownloadError,
    download as download_youtube,
)
from ytcrawl.search import youtube as youtube_search
from ytcrawl.search import youtube_detail

DOWNLOAD_SLEEP_SECONDS_RANGE = (10.0, 15.0)
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;]*m")
BOT_CHECK_ERROR_TYPE = "bot_check_required"


@dataclass(frozen=True)
class SnippetCrawlResult:
    run_id: int | None
    item_count: int
    video_records: tuple[videos.VideoRecord, ...]
    skipped: bool = False
    skipped_run_id: int | None = None


def _group_video_record_ids_by_video_id(
    video_records: tuple[videos.VideoRecord, ...],
) -> dict[str, list[int]]:
    grouped: dict[str, list[int]] = {}
    for record in video_records:
        if record.video_id:
            grouped.setdefault(record.video_id, []).append(record.id)
    return grouped


def _map_video_detail_items_by_video_id(
    responses: list[dict],
) -> dict[str, dict]:
    detail_items: dict[str, dict] = {}
    for response in responses:
        for item in response.get("items", []):
            video_id = item.get("id")
            if video_id:
                detail_items[video_id] = item
    return detail_items


def _clean_download_error_message(exc: Exception) -> str:
    return ANSI_ESCAPE_PATTERN.sub("", str(exc))


def _classify_download_error(exc: Exception) -> str:
    message = _clean_download_error_message(exc).lower()
    if (
        "sign in to confirm" in message
        and "not a bot" in message
    ) or "--cookies-from-browser" in message or "--cookies" in message:
        return BOT_CHECK_ERROR_TYPE
    if "http error 403" in message or "403: forbidden" in message:
        return "http_403_forbidden"
    if ("bytes read" in message and "more expected" in message) or (
        "content too short" in message
    ):
        return "incomplete_read"
    if isinstance(exc, YouTubeDownloadError):
        return "download_error"
    return "unexpected_error"


def _sleep_before_next_download() -> None:
    time.sleep(random.uniform(*DOWNLOAD_SLEEP_SECONDS_RANGE))


def _create_download_attempts(
    session: Session,
    record_ids: list[int],
) -> list[int]:
    attempt_ids: list[int] = []
    for video_pk in record_ids:
        attempt = video_download_attempts.create_download_attempt(
            session,
            video_ref_id=video_pk,
            downloader=DOWNLOADER_LABEL,
            format_selector=DOWNLOAD_FORMAT,
        )
        attempt_ids.append(attempt.id)
    return attempt_ids


def _mark_download_attempts_failed(
    session: Session,
    attempt_ids: list[int],
    *,
    error_type: str,
    error_message: str,
) -> None:
    for attempt_id in attempt_ids:
        video_download_attempts.mark_download_attempt_failed(
            session,
            id=attempt_id,
            error_type=error_type,
            error_message=error_message,
        )


def crawl_youtube_snippet(
    engine: Engine,
    args: argparse.Namespace,
    api_key: str,
) -> SnippetCrawlResult:
    # 프리셋은 어디까지나 query 생성을 도와주는 형태
    page = 1
    page_token = None

    query = youtube_search.resolve_query(args)
    request_hash = youtube_search.build_request_hash(
        query=query,
        published_after=args.published_after,
        published_before=args.published_before,
        fixed_params=youtube_search.FIXED_SEARCH_PARAMS,
    )

    # 데이터베이스에서 이전 검색 실행을 확인하여 다음 페이지 토큰을 가져옵니다.
    with Session(engine) as session:
        latest_run = youtube_search_runs.find_latest_matching_search_run(
            session,
            request_hash=request_hash,
        )
        if latest_run is not None:
            if latest_run.next_page_token is None:
                print(
                    "No next page token for latest matching search run "
                    f"{latest_run.id}; no new search performed."
                )
                return SnippetCrawlResult(
                    run_id=None,
                    item_count=0,
                    video_records=(),
                    skipped=True,
                    skipped_run_id=latest_run.id,
                )
            page_token = latest_run.next_page_token
            page = latest_run.page + 1

    response = youtube_search.fetch_search_response(
        args, api_key, page_token=page_token
    )

    # YouTube 검색 실행 및 비디오 레코드를 데이터베이스에 저장합니다.
    with Session(engine) as session:
        run = youtube_search_runs.create_search_run(
            session,
            query=query,
            published_after=args.published_after,
            published_before=args.published_before,
            fixed_params=youtube_search.FIXED_SEARCH_PARAMS,
            request_hash=request_hash,
            page=page,
            response=response,
        )
        video_records = videos.create_videos_from_search_response(
            session,
            search_id=run.id,
            response=response,
        )
        run_id = run.id
        item_count = run.item_count
        session.commit()

    return SnippetCrawlResult(
        run_id=run_id,
        item_count=item_count,
        video_records=video_records,
    )


def crawl_youtube_details(
    engine: Engine,
    api_key: str,
    video_records: tuple[videos.VideoRecord, ...],
):
    # YouTube 비디오 별 상세 정보를 가져와서 데이터베이스에 저장합니다.
    detail_successes = 0
    detail_failures = 0
    record_ids_by_video_id = _group_video_record_ids_by_video_id(video_records)
    if record_ids_by_video_id:
        try:
            detail_responses = youtube_detail.fetch_video_detail_responses(
                list(record_ids_by_video_id),
                api_key,
            )
        except Exception as exc:  # noqa: BLE001 - keep downloading remaining videos.
            detail_failures += sum(
                len(record_ids) for record_ids in record_ids_by_video_id.values()
            )
            print(f"Failed to fetch video details: {exc}", file=sys.stderr)
        else:
            detail_items_by_video_id = _map_video_detail_items_by_video_id(
                detail_responses
            )
            for video_id, record_ids in record_ids_by_video_id.items():
                detail_item = detail_items_by_video_id.get(video_id)
                if detail_item is None:
                    detail_failures += len(record_ids)
                    print(
                        f"Missing video detail for video_id {video_id}.",
                        file=sys.stderr,
                    )
                    continue

                for video_pk in record_ids:
                    with Session(engine) as session:
                        try:
                            videos_detail.create_or_update_video_detail(
                                session,
                                video_ref_id=video_pk,
                                item=detail_item,
                            )
                            session.commit()
                            detail_successes += 1
                        except Exception as exc:  # noqa: BLE001 - keep processing rows.
                            session.rollback()
                            detail_failures += 1
                            print(
                                f"Failed to save video detail for video row "
                                f"{video_pk}: {exc}",
                                file=sys.stderr,
                            )

    return (detail_successes, detail_failures)


def crawl_youtube_videos(
    engine: Engine,
    output_dir: str,
    video_records: tuple[videos.VideoRecord, ...],
):
    record_ids_by_video_id = _group_video_record_ids_by_video_id(video_records)
    missing_video_id_count = len(video_records) - sum(
        len(record_ids) for record_ids in record_ids_by_video_id.values()
    )
    for record in video_records:
        if not record.video_id:
            print(
                f"Skipping video row {record.id}: missing video_id.",
                file=sys.stderr,
            )

    # YouTube 비디오를 다운로드하고 데이터베이스에 경로를 업데이트합니다.
    failures = missing_video_id_count
    successes = 0
    video_items = list(record_ids_by_video_id.items())
    for item_index, (video_id, record_ids) in enumerate(video_items):
        if item_index > 0:
            _sleep_before_next_download()

        attempt_ids: list[int] = []
        with Session(engine) as session:
            attempt_ids = _create_download_attempts(session, record_ids)
            session.commit()

        try:
            downloaded_path = Path(
                download_youtube(video_id, output_dir, overwrite=False)
            ).resolve()
        except Exception as exc:  # noqa: BLE001 - keep crawling remaining videos.
            error_type = _classify_download_error(exc)
            error_message = _clean_download_error_message(exc)
            with Session(engine) as session:
                _mark_download_attempts_failed(
                    session,
                    attempt_ids,
                    error_type=error_type,
                    error_message=error_message,
                )
                session.commit()
            failures += len(record_ids)
            print(f"Failed to download {video_id}: {exc}", file=sys.stderr)
            if error_type == BOT_CHECK_ERROR_TYPE:
                remaining_items = video_items[item_index + 1 :]
                skipped_message = (
                    f"Skipped after bot check on {video_id}: {error_message}"
                )
                with Session(engine) as session:
                    for _, skipped_record_ids in remaining_items:
                        skipped_attempt_ids = _create_download_attempts(
                            session,
                            skipped_record_ids,
                        )
                        _mark_download_attempts_failed(
                            session,
                            skipped_attempt_ids,
                            error_type=BOT_CHECK_ERROR_TYPE,
                            error_message=skipped_message,
                        )
                        failures += len(skipped_record_ids)
                    session.commit()
                break
            continue

        file_size_bytes = (
            downloaded_path.stat().st_size if downloaded_path.is_file() else None
        )
        with Session(engine) as session:
            for attempt_id in attempt_ids:
                video_download_attempts.mark_download_attempt_succeeded(
                    session,
                    id=attempt_id,
                    file_size_bytes=file_size_bytes,
                )
            for video_pk in record_ids:
                videos.update_video_path(
                    session, id=video_pk, path=str(downloaded_path)
                )
            session.commit()
        successes += len(record_ids)

    return (successes, failures)


def run_crawl_youtube(
    args: argparse.Namespace,
    api_key: str,
    db_url: str,
) -> int:
    engine = core.create_engine_for_url(db_url)
    core.create_all(engine)

    snippet_result = crawl_youtube_snippet(engine, args, api_key)
    if snippet_result.skipped:
        return 0

    detail_successes, detail_failures = crawl_youtube_details(
        engine,
        api_key,
        video_records=snippet_result.video_records,
    )
    download_successes, download_failures = crawl_youtube_videos(
        engine,
        str(args.output_dir),
        video_records=snippet_result.video_records,
    )

    print(
        f"Saved {snippet_result.item_count} videos from search run "
        f"{snippet_result.run_id}; "
        f"details saved {detail_successes}, "
        f"detail failed {detail_failures}; "
        f"downloaded {download_successes}, "
        f"failed {download_failures}."
    )
    return 1 if detail_failures or download_failures else 0
