from __future__ import annotations

from pathlib import Path

from ytcrawl.db import (
    core,
    video_download_attempts,
    video_reviews,
    videos,
    videos_detail,
    youtube_search_runs,
)
from ytcrawl.review import schemas


def list_videos(
    start_id: int,
    rows: int,
    username: str | None = None,
) -> schemas.VideoListResponse:
    with core.session_scope() as session:
        video_rows = videos.find_videos_from_id(
            session,
            start_id=start_id,
            rows=rows + 1,
        )
        visible_rows = video_rows[:rows]
        review_rows = _review_map_for_videos(
            session,
            video_rows=visible_rows,
            username=username,
        )

    has_more = len(video_rows) > rows
    next_start_id = video_rows[rows].id if has_more else None
    return schemas.VideoListResponse(
        items=[
            _video_summary(video, review_rows.get(video.id))
            for video in visible_rows
        ],
        start_id=start_id,
        rows=rows,
        next_start_id=next_start_id,
        has_more=has_more,
    )


def get_video_detail(
    media_root: Path,
    video_ref_id: int,
) -> schemas.VideoDetailResponse | None:
    with core.session_scope() as session:
        video = videos.find_video_by_id(session, video_ref_id=video_ref_id)
        if video is None:
            return None
        detail = videos_detail.find_video_detail_for_video(
            session,
            video_ref_id=video_ref_id,
        )
        latest_attempt = video_download_attempts.find_latest_attempt_for_video(
            session,
            video_ref_id=video_ref_id,
        )
        search_run = youtube_search_runs.find_search_run_by_id(
            session,
            search_id=video.search_id,
        )

        video_info = _video_info(video)
        search_run_info = (
            _search_run_info(search_run) if search_run is not None else None
        )
        detail_info = _detail_info(detail) if detail is not None else None
        attempt_info = (
            _download_attempt_info(latest_attempt)
            if latest_attempt is not None
            else None
        )
        media_info = _media_info(video, media_root)

    return schemas.VideoDetailResponse(
        video=video_info,
        search_run=search_run_info,
        detail=detail_info,
        latest_download_attempt=attempt_info,
        media=media_info,
    )


def get_review(
    video_ref_id: int,
    username: str,
) -> schemas.ReviewResponse | None:
    with core.session_scope() as session:
        video = videos.find_video_by_id(session, video_ref_id=video_ref_id)
        if video is None:
            return None
        review = video_reviews.find_video_review(
            session,
            video_ref_id=video_ref_id,
            username=username,
        )
        if review is None:
            return schemas.ReviewResponse(
                username=username,
                video_ref_id=video_ref_id,
                status=video_reviews.REVIEW_STATUS_PENDING,
                note=None,
                persisted=False,
            )
        return _review_response(review, persisted=True)


def upsert_review(
    video_ref_id: int,
    username: str,
    status: schemas.ReviewStatus,
    note: str | None,
) -> schemas.ReviewResponse | None:
    with core.session_scope() as session:
        video = videos.find_video_by_id(session, video_ref_id=video_ref_id)
        if video is None:
            return None
        review = video_reviews.upsert_video_review(
            session,
            video_ref_id=video_ref_id,
            username=username,
            status=status,
            note=note,
        )
        response = _review_response(review, persisted=True)
        return response


def resolve_media_path(
    video_ref_id: int,
    media_root: Path,
) -> Path | None:
    with core.session_scope() as session:
        video = videos.find_video_by_id(session, video_ref_id=video_ref_id)
        if video is None or not video.path:
            return None
        path = Path(video.path).expanduser().resolve()

    if not path.is_file():
        return None
    try:
        path.relative_to(media_root)
    except ValueError:
        return None
    return path


def _media_info(video: videos.Video, media_root: Path) -> schemas.MediaInfo:
    if not video.path:
        return schemas.MediaInfo(available=False, url=None)
    path = Path(video.path).expanduser().resolve()
    try:
        path.relative_to(media_root)
    except ValueError:
        return schemas.MediaInfo(available=False, url=None)
    if not path.is_file():
        return schemas.MediaInfo(available=False, url=None)
    return schemas.MediaInfo(
        available=True,
        url=f"/media/videos/{video.id}",
    )


def _review_map_for_videos(
    session,
    *,
    video_rows: tuple[videos.Video, ...],
    username: str | None,
) -> dict[int, video_reviews.VideoReview]:
    reviewer = username.strip() if username else ""
    if not reviewer:
        return {}
    return video_reviews.find_video_reviews_for_videos(
        session,
        video_ref_ids=[video.id for video in video_rows],
        username=reviewer,
    )


def _video_summary(
    video: videos.Video,
    review: video_reviews.VideoReview | None = None,
) -> schemas.VideoSummary:
    review_status = review.status if review is not None else None
    return schemas.VideoSummary(
        id=video.id,
        video_id=video.video_id,
        title=video.title,
        publishTime=video.publishTime,
        has_path=bool(video.path),
        review_status=review_status,
        reviewed=review_status in (
            video_reviews.REVIEW_STATUS_ACCEPTED,
            video_reviews.REVIEW_STATUS_REJECTED,
            video_reviews.REVIEW_STATUS_NEEDS_REVIEW,
        ),
    )


def _video_info(video: videos.Video) -> schemas.VideoInfo:
    return schemas.VideoInfo(
        id=video.id,
        search_id=video.search_id,
        kind=video.kind,
        etag=video.etag,
        video_id=video.video_id,
        title=video.title,
        description=video.description,
        publishTime=video.publishTime,
        path=video.path,
    )


def _search_run_info(
    search_run: youtube_search_runs.YouTubeSearchRun,
) -> schemas.SearchRunInfo:
    return schemas.SearchRunInfo(
        id=search_run.id,
        query=search_run.query,
    )


def _detail_info(detail: videos_detail.VideoDetail) -> schemas.VideoDetailInfo:
    return schemas.VideoDetailInfo(
        duration=detail.duration,
        resolution=detail.resolution,
        has_caption=detail.has_caption,
        tags=detail.tags,
        language=detail.language,
        audio_language=detail.audio_language,
        license=detail.license,
        view_count=detail.view_count,
        like_count=detail.like_count,
        comment_count=detail.comment_count,
        recording_date=detail.recording_date,
        location=detail.location,
        rating=detail.rating,
        other_info=detail.other_info,
        is_synthetic_marked=detail.is_synthetic_marked,
    )


def _download_attempt_info(
    attempt: video_download_attempts.VideoDownloadAttempt,
) -> schemas.DownloadAttemptInfo:
    return schemas.DownloadAttemptInfo(
        id=attempt.id,
        started_at=attempt.started_at,
        finished_at=attempt.finished_at,
        file_size_bytes=attempt.file_size_bytes,
        downloader=attempt.downloader,
        format_selector=attempt.format_selector,
        error_type=attempt.error_type,
        error_message=attempt.error_message,
    )


def _review_response(
    review: video_reviews.VideoReview,
    *,
    persisted: bool,
) -> schemas.ReviewResponse:
    return schemas.ReviewResponse(
        username=review.username,
        video_ref_id=review.video_ref_id,
        status=review.status,
        note=review.note,
        persisted=persisted,
    )
