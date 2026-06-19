#!/usr/bin/env python3
"""Collect public Creative Commons YouTube video metadata via YouTube Data API v3.

This program does NOT download video or audio files. It searches public videos with
videoLicense=creativeCommon, resolves full public metadata with videos.list, and
stores results in a resumable SQLite database.

Example:
    export YOUTUBE_API_KEY='...'
    python youtube_cc_collector.py \
        --start 2025-01-01 --end 2026-01-01 \
        --window-hours 24 --regions US \
        --durations short medium long \
        --max-search-calls 95 \
        --db youtube_cc.sqlite \
        --export-jsonl youtube_cc.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

UTC = timezone.utc
SEARCH_PAGE_SIZE = 50
DETAIL_BATCH_SIZE = 50

# Publicly readable parts. Owner-only parts such as fileDetails,
# processingDetails, and suggestions are intentionally excluded.
DETAIL_PARTS = ",".join(
    [
        "snippet",
        "contentDetails",
        "statistics",
        "status",
        "topicDetails",
        "recordingDetails",
        "localizations",
        "liveStreamingDetails",
        "paidProductPlacementDetails",
    ]
)


class QuotaExhausted(RuntimeError):
    """Raised when the API reports that a daily quota bucket is exhausted."""


@dataclass(frozen=True)
class Task:
    id: int
    start_at: str
    end_at: str
    region_code: str
    duration: str
    safe_search: str
    next_page_token: str | None
    pages_done: int


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def utc_now_text() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def parse_datetime(value: str) -> datetime:
    """Parse YYYY-MM-DD or an RFC3339/ISO-8601 timestamp as UTC."""
    value = value.strip()
    if len(value) == 10:
        parsed = datetime.fromisoformat(value).replace(tzinfo=UTC)
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        parsed = parsed.astimezone(UTC)
    return parsed


def rfc3339(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def iter_windows(
    start: datetime, end: datetime, window_size: timedelta
) -> Iterator[tuple[datetime, datetime]]:
    if start >= end:
        raise ValueError("--start must be earlier than --end")
    if window_size.total_seconds() <= 0:
        raise ValueError("window size must be positive")

    cursor = start
    step = window_size
    while cursor < end:
        next_cursor = min(cursor + step, end)
        # YouTube documents publishedAfter/publishedBefore as inclusive.
        # Adjacent windows therefore overlap at one boundary instant. The
        # SQLite primary key deduplicates that harmless overlap and avoids gaps.
        yield cursor, next_cursor
        cursor = next_cursor


def chunks(values: Sequence[str], size: int) -> Iterator[list[str]]:
    for i in range(0, len(values), size):
        yield list(values[i : i + size])


def int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def bool_or_none(value: Any) -> int | None:
    if value is None:
        return None
    return int(bool(value))


def error_reasons(exc: HttpError) -> set[str]:
    try:
        payload = json.loads(exc.content.decode("utf-8"))
        errors = payload.get("error", {}).get("errors", [])
        return {str(item.get("reason")) for item in errors if item.get("reason")}
    except Exception:
        return set()


def execute_request(request: Any, *, attempts: int = 6) -> dict[str, Any]:
    """Execute a Google API request with bounded exponential backoff."""
    transient_reasons = {"rateLimitExceeded", "userRateLimitExceeded", "backendError"}
    quota_reasons = {"dailyLimitExceeded", "quotaExceeded"}

    for attempt in range(attempts):
        try:
            return request.execute(num_retries=0)
        except HttpError as exc:
            status = int(getattr(exc.resp, "status", 0) or 0)
            reasons = error_reasons(exc)

            if reasons & quota_reasons:
                raise QuotaExhausted(f"YouTube API quota exhausted: {sorted(reasons)}") from exc

            retryable = status in {429, 500, 502, 503, 504} or bool(reasons & transient_reasons)
            if not retryable or attempt == attempts - 1:
                raise

            delay = min(60.0, 2.0**attempt)
            time.sleep(delay)

    raise RuntimeError("unreachable")


def connect_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    create_schema(conn)
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            start_at            TEXT NOT NULL,
            end_at              TEXT NOT NULL,
            region_code         TEXT NOT NULL,
            duration            TEXT NOT NULL,
            safe_search         TEXT NOT NULL,
            next_page_token     TEXT,
            done                INTEGER NOT NULL DEFAULT 0,
            pages_done          INTEGER NOT NULL DEFAULT 0,
            approx_total        INTEGER,
            last_error          TEXT,
            updated_at          TEXT NOT NULL,
            UNIQUE(start_at, end_at, region_code, duration, safe_search)
        );

        CREATE TABLE IF NOT EXISTS search_hits (
            video_id            TEXT PRIMARY KEY,
            first_seen_at       TEXT NOT NULL,
            last_seen_at        TEXT NOT NULL,
            search_published_at TEXT,
            search_channel_id   TEXT,
            search_title        TEXT,
            first_task_id       INTEGER,
            FOREIGN KEY(first_task_id) REFERENCES tasks(id)
        );

        CREATE TABLE IF NOT EXISTS videos (
            video_id               TEXT PRIMARY KEY,
            fetched_at             TEXT NOT NULL,
            published_at           TEXT,
            channel_id             TEXT,
            channel_title          TEXT,
            title                  TEXT,
            description            TEXT,
            category_id            TEXT,
            default_language       TEXT,
            default_audio_language TEXT,
            duration               TEXT,
            definition             TEXT,
            dimension              TEXT,
            caption                TEXT,
            licensed_content       INTEGER,
            projection             TEXT,
            privacy_status          TEXT,
            license                 TEXT,
            embeddable              INTEGER,
            public_stats_viewable   INTEGER,
            made_for_kids           INTEGER,
            view_count              INTEGER,
            like_count              INTEGER,
            favorite_count          INTEGER,
            comment_count           INTEGER,
            raw_json                TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_tasks_pending
            ON tasks(done, start_at, id);
        CREATE INDEX IF NOT EXISTS idx_videos_published_at
            ON videos(published_at);
        CREATE INDEX IF NOT EXISTS idx_videos_channel_id
            ON videos(channel_id);
        CREATE INDEX IF NOT EXISTS idx_videos_fetched_at
            ON videos(fetched_at);
        """
    )
    conn.commit()


def initialize_tasks(
    conn: sqlite3.Connection,
    *,
    start: datetime,
    end: datetime,
    window_size: timedelta,
    regions: Sequence[str],
    durations: Sequence[str],
    safe_search: str,
) -> int:
    created = 0
    now = utc_now_text()
    with conn:
        for window_start, window_end in iter_windows(start, end, window_size):
            for region in regions:
                for duration in durations:
                    cursor = conn.execute(
                        """
                        INSERT OR IGNORE INTO tasks(
                            start_at, end_at, region_code, duration,
                            safe_search, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            rfc3339(window_start),
                            rfc3339(window_end),
                            region,
                            duration,
                            safe_search,
                            now,
                        ),
                    )
                    created += max(cursor.rowcount, 0)
    return created


def get_next_task(conn: sqlite3.Connection, *, newest_first: bool) -> Task | None:
    direction = "DESC" if newest_first else "ASC"
    row = conn.execute(
        f"""
        SELECT id, start_at, end_at, region_code, duration,
               safe_search, next_page_token, pages_done
        FROM tasks
        WHERE done = 0
        ORDER BY start_at {direction}, id ASC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    return Task(
        id=int(row["id"]),
        start_at=str(row["start_at"]),
        end_at=str(row["end_at"]),
        region_code=str(row["region_code"]),
        duration=str(row["duration"]),
        safe_search=str(row["safe_search"]),
        next_page_token=row["next_page_token"],
        pages_done=int(row["pages_done"]),
    )


def build_search_request(youtube: Any, task: Task) -> Any:
    params: dict[str, Any] = {
        "part": "snippet",
        "type": "video",
        "videoLicense": "creativeCommon",
        "order": "date",
        "publishedAfter": task.start_at,
        "publishedBefore": task.end_at,
        "maxResults": SEARCH_PAGE_SIZE,
        "safeSearch": task.safe_search,
        "fields": (
            "nextPageToken,pageInfo(totalResults,resultsPerPage),"
            "items(id/videoId,snippet(publishedAt,channelId,title))"
        ),
    }
    if task.region_code:
        params["regionCode"] = task.region_code
    if task.duration != "any":
        params["videoDuration"] = task.duration
    if task.next_page_token:
        params["pageToken"] = task.next_page_token
    return youtube.search().list(**params)


def fetch_video_details(youtube: Any, video_ids: Sequence[str]) -> list[dict[str, Any]]:
    if not video_ids:
        return []

    details: list[dict[str, Any]] = []
    for batch in chunks(list(video_ids), DETAIL_BATCH_SIZE):
        response = execute_request(
            youtube.videos().list(
                part=DETAIL_PARTS,
                id=",".join(batch),
            )
        )
        details.extend(response.get("items", []))
    return details


def upsert_search_hits(
    conn: sqlite3.Connection,
    *,
    task_id: int,
    items: Sequence[dict[str, Any]],
    seen_at: str,
) -> None:
    for item in items:
        video_id = item.get("id", {}).get("videoId")
        if not video_id:
            continue
        snippet = item.get("snippet", {})
        conn.execute(
            """
            INSERT INTO search_hits(
                video_id, first_seen_at, last_seen_at,
                search_published_at, search_channel_id,
                search_title, first_task_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                last_seen_at = excluded.last_seen_at,
                search_published_at = COALESCE(excluded.search_published_at, search_hits.search_published_at),
                search_channel_id = COALESCE(excluded.search_channel_id, search_hits.search_channel_id),
                search_title = COALESCE(excluded.search_title, search_hits.search_title)
            """,
            (
                video_id,
                seen_at,
                seen_at,
                snippet.get("publishedAt"),
                snippet.get("channelId"),
                snippet.get("title"),
                task_id,
            ),
        )


def upsert_video(conn: sqlite3.Connection, item: dict[str, Any], fetched_at: str) -> bool:
    status = item.get("status", {})
    # Defensive revalidation: the search filter can race with license changes.
    if status.get("license") != "creativeCommon":
        return False

    snippet = item.get("snippet", {})
    content = item.get("contentDetails", {})
    stats = item.get("statistics", {})
    video_id = item.get("id")
    if not video_id:
        return False

    conn.execute(
        """
        INSERT INTO videos(
            video_id, fetched_at, published_at, channel_id, channel_title,
            title, description, category_id, default_language,
            default_audio_language, duration, definition, dimension, caption,
            licensed_content, projection, privacy_status, license, embeddable,
            public_stats_viewable, made_for_kids, view_count, like_count,
            favorite_count, comment_count, raw_json
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT(video_id) DO UPDATE SET
            fetched_at = excluded.fetched_at,
            published_at = excluded.published_at,
            channel_id = excluded.channel_id,
            channel_title = excluded.channel_title,
            title = excluded.title,
            description = excluded.description,
            category_id = excluded.category_id,
            default_language = excluded.default_language,
            default_audio_language = excluded.default_audio_language,
            duration = excluded.duration,
            definition = excluded.definition,
            dimension = excluded.dimension,
            caption = excluded.caption,
            licensed_content = excluded.licensed_content,
            projection = excluded.projection,
            privacy_status = excluded.privacy_status,
            license = excluded.license,
            embeddable = excluded.embeddable,
            public_stats_viewable = excluded.public_stats_viewable,
            made_for_kids = excluded.made_for_kids,
            view_count = excluded.view_count,
            like_count = excluded.like_count,
            favorite_count = excluded.favorite_count,
            comment_count = excluded.comment_count,
            raw_json = excluded.raw_json
        """,
        (
            video_id,
            fetched_at,
            snippet.get("publishedAt"),
            snippet.get("channelId"),
            snippet.get("channelTitle"),
            snippet.get("title"),
            snippet.get("description"),
            snippet.get("categoryId"),
            snippet.get("defaultLanguage"),
            snippet.get("defaultAudioLanguage"),
            content.get("duration"),
            content.get("definition"),
            content.get("dimension"),
            content.get("caption"),
            bool_or_none(content.get("licensedContent")),
            content.get("projection"),
            status.get("privacyStatus"),
            status.get("license"),
            bool_or_none(status.get("embeddable")),
            bool_or_none(status.get("publicStatsViewable")),
            bool_or_none(status.get("madeForKids")),
            int_or_none(stats.get("viewCount")),
            int_or_none(stats.get("likeCount")),
            int_or_none(stats.get("favoriteCount")),
            int_or_none(stats.get("commentCount")),
            json.dumps(item, ensure_ascii=False, separators=(",", ":")),
        ),
    )
    return True


def mark_task_error(conn: sqlite3.Connection, task_id: int, message: str) -> None:
    with conn:
        conn.execute(
            "UPDATE tasks SET last_error = ?, updated_at = ? WHERE id = ?",
            (message[:2000], utc_now_text(), task_id),
        )


def process_one_page(conn: sqlite3.Connection, youtube: Any, task: Task) -> tuple[int, int, bool]:
    response = execute_request(build_search_request(youtube, task))
    items = response.get("items", [])
    video_ids = [
        item.get("id", {}).get("videoId")
        for item in items
        if item.get("id", {}).get("videoId")
    ]

    details = fetch_video_details(youtube, video_ids)
    next_token = response.get("nextPageToken")
    approx_total = int_or_none(response.get("pageInfo", {}).get("totalResults"))
    now = utc_now_text()

    verified = 0
    with conn:
        upsert_search_hits(conn, task_id=task.id, items=items, seen_at=now)
        for detail in details:
            verified += int(upsert_video(conn, detail, now))

        conn.execute(
            """
            UPDATE tasks
            SET next_page_token = ?,
                done = ?,
                pages_done = pages_done + 1,
                approx_total = COALESCE(?, approx_total),
                last_error = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (next_token, int(not bool(next_token)), approx_total, now, task.id),
        )

    return len(video_ids), verified, not bool(next_token)


def export_jsonl(conn: sqlite3.Connection, output: Path) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8") as handle:
        for row in conn.execute("SELECT raw_json FROM videos ORDER BY published_at, video_id"):
            handle.write(str(row["raw_json"]) + "\n")
            count += 1
    return count


def print_summary(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM videos) AS videos,
            (SELECT COUNT(*) FROM search_hits) AS hits,
            (SELECT COUNT(*) FROM tasks WHERE done = 1) AS done_tasks,
            (SELECT COUNT(*) FROM tasks WHERE done = 0) AS pending_tasks
        """
    ).fetchone()
    print(
        "Summary: "
        f"verified_cc_videos={row['videos']}, "
        f"unique_search_hits={row['hits']}, "
        f"done_tasks={row['done_tasks']}, "
        f"pending_tasks={row['pending_tasks']}"
    )


def normalize_regions(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        for token in value.split(","):
            token = token.strip().upper()
            if token and token not in result:
                result.append(token)
    if not result:
        result.append("US")
    return result


def normalize_durations(values: Sequence[str]) -> list[str]:
    allowed = {"any", "short", "medium", "long"}
    result: list[str] = []
    for value in values:
        for token in value.split(","):
            token = token.strip().lower()
            if token not in allowed:
                raise ValueError(f"Invalid duration {token!r}; choose from {sorted(allowed)}")
            if token not in result:
                result.append(token)
    return result or ["any"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resumable Creative Commons YouTube metadata collector"
    )
    parser.add_argument("--start", required=True, help="UTC start date/time, e.g. 2025-01-01")
    parser.add_argument("--end", required=True, help="UTC end date/time, e.g. 2026-01-01")
    window_group = parser.add_mutually_exclusive_group()
    window_group.add_argument(
        "--window-days", type=float, default=1.0,
        help="Date-window size in days (default: 1)"
    )
    window_group.add_argument(
        "--window-hours", type=float,
        help="Date-window size in hours; useful for dense periods"
    )
    parser.add_argument(
        "--regions",
        nargs="+",
        default=["US"],
        help="ISO-3166 region codes; comma or space separated (default: US)",
    )
    parser.add_argument(
        "--durations",
        nargs="+",
        default=["any"],
        help="any, short, medium, long; use short medium long for higher-recall partitioning",
    )
    parser.add_argument(
        "--safe-search",
        choices=["none", "moderate", "strict"],
        default="none",
        help="Search filtering (default: none, for maximum recall)",
    )
    parser.add_argument(
        "--max-search-calls",
        type=int,
        default=95,
        help="Stop after this many search.list calls in one run (default: 95)",
    )
    parser.add_argument("--db", type=Path, default=Path("youtube_cc.sqlite"))
    parser.add_argument("--export-jsonl", type=Path, help="Write verified video resources as JSON Lines")
    parser.add_argument(
        "--oldest-first",
        action="store_true",
        help="Process oldest windows first (default: newest first)",
    )
    parser.add_argument(
        "--api-key-env",
        default="YOUTUBE_API_KEY",
        help="Environment variable containing the API key (default: YOUTUBE_API_KEY)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    try:
        start = parse_datetime(args.start)
        end = parse_datetime(args.end)
        regions = normalize_regions(args.regions)
        durations = normalize_durations(args.durations)
        window_size = (
            timedelta(hours=args.window_hours)
            if args.window_hours is not None
            else timedelta(days=args.window_days)
        )
        if window_size.total_seconds() <= 0:
            raise ValueError("window size must be positive")
    except ValueError as exc:
        print(f"Argument error: {exc}", file=sys.stderr)
        return 2

    if args.max_search_calls < 1:
        print("--max-search-calls must be at least 1", file=sys.stderr)
        return 2

    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        print(
            f"Missing API key. Set environment variable {args.api_key_env}.",
            file=sys.stderr,
        )
        return 2

    conn = connect_db(args.db)
    created = initialize_tasks(
        conn,
        start=start,
        end=end,
        window_size=window_size,
        regions=regions,
        durations=durations,
        safe_search=args.safe_search,
    )
    print(f"Initialized {created} new tasks in {args.db}")

    youtube = build("youtube", "v3", developerKey=api_key, cache_discovery=False)

    search_calls = 0
    try:
        while search_calls < args.max_search_calls:
            task = get_next_task(conn, newest_first=not args.oldest_first)
            if task is None:
                print("All queued tasks are complete.")
                break

            search_calls += 1
            try:
                found, verified, task_done = process_one_page(conn, youtube, task)
            except QuotaExhausted:
                raise
            except HttpError as exc:
                mark_task_error(conn, task.id, str(exc))
                print(f"API error on task {task.id}: {exc}", file=sys.stderr)
                return 1
            except Exception as exc:
                mark_task_error(conn, task.id, repr(exc))
                print(f"Error on task {task.id}: {exc}", file=sys.stderr)
                return 1

            print(
                f"search_call={search_calls} task={task.id} "
                f"window={task.start_at}..{task.end_at} "
                f"region={task.region_code} duration={task.duration} "
                f"page={task.pages_done + 1} found={found} "
                f"verified_cc={verified} done={task_done}"
            )

    except QuotaExhausted as exc:
        print(str(exc), file=sys.stderr)

    print_summary(conn)

    if args.export_jsonl:
        count = export_jsonl(conn, args.export_jsonl)
        print(f"Exported {count} records to {args.export_jsonl}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
