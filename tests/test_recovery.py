from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
import tempfile

from ytcrawl.config import ConfigError
from ytcrawl.crawl import recovery
from ytcrawl.db import core
from ytcrawl.db.video_download_attempts import VideoDownloadAttempt
from ytcrawl.db.videos import Video
from ytcrawl.db.youtube_search_runs import YouTubeSearchRun


class RecoveryDatabaseTestCase(TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_root = Path(self.temp_dir.name)
        self.db_path = self.data_root / "ytcrawl.sqlite3"
        self.db_url = f"sqlite:///{self.db_path.as_posix()}"
        self.media_root = self.data_root / "media"
        self.config = SimpleNamespace(
            db_path=self.db_path,
            db_url=self.db_url,
            media_root=self.media_root,
        )

        core.configure(self.db_url)
        core.create_all()
        with core.session_scope() as session:
            search_run = YouTubeSearchRun(
                request_hash="recovery-test",
                collection_method="channel_uploads",
                query=None,
                channel_id="test-channel",
                playlist_id="test-playlist",
                published_after=None,
                published_before=None,
                part="snippet",
                search_type="video",
                max_results=50,
                region_code=None,
                safe_search=None,
                video_license=None,
                response_kind=None,
                response_etag=None,
                next_page_token=None,
                total_results=None,
                results_per_page=None,
                item_count=0,
                page=1,
            )
            session.add(search_run)
            session.flush()
            self.search_id = search_run.id

    def tearDown(self) -> None:
        # Dispose the file-backed engine before TemporaryDirectory removes it.
        core.configure("sqlite:///:memory:")
        self.temp_dir.cleanup()

    def add_video(
        self,
        video_id: str,
        *,
        path: str | None = None,
        embed_code: str | None = None,
        attempts: tuple[dict[str, object], ...] = (),
    ) -> int:
        with core.session_scope() as session:
            video = Video(
                search_id=self.search_id,
                video_id=video_id,
                path=path,
                embed_code=embed_code,
            )
            session.add(video)
            session.flush()
            video_ref_id = video.id

            for values in attempts:
                session.add(
                    VideoDownloadAttempt(
                        video_ref_id=video_ref_id,
                        downloader="yt-dlp",
                        format_selector="test-format",
                        **values,
                    )
                )
        return video_ref_id

    def failed_attempt(
        self,
        *,
        error_type: str = "incomplete_read",
        finished_at: datetime | None = None,
    ) -> dict[str, object]:
        return {
            "finished_at": finished_at or datetime.now(timezone.utc),
            "error_type": error_type,
            "error_message": "download interrupted",
        }

    def successful_attempt(self) -> dict[str, object]:
        return {
            "finished_at": datetime.now(timezone.utc),
            "file_size_bytes": 123,
            "error_type": None,
            "error_message": None,
        }


class FindFailedVideoRecordsTests(RecoveryDatabaseTestCase):
    def test_selects_only_latest_completed_failures_without_a_path(self) -> None:
        selected_id = self.add_video(
            "selected",
            attempts=(self.failed_attempt(),),
        )
        embed_selected_id = self.add_video(
            "embed-is-not-an-exclusion",
            embed_code="<iframe></iframe>",
            attempts=(self.failed_attempt(),),
        )
        empty_path_selected_id = self.add_video(
            "empty-path",
            path="",
            attempts=(self.failed_attempt(),),
        )
        self.add_video(
            "later-success",
            attempts=(self.failed_attempt(), self.successful_attempt()),
        )
        self.add_video(
            "latest-unfinished",
            attempts=(
                self.failed_attempt(),
                {
                    "finished_at": None,
                    "error_type": "incomplete_read",
                    "error_message": "still running",
                },
            ),
        )
        self.add_video(
            "already-has-path",
            path="vid_already.mp4",
            attempts=(self.failed_attempt(),),
        )
        self.add_video(
            "empty-error-type",
            attempts=(self.failed_attempt(error_type=""),),
        )
        self.add_video("never-attempted")

        with core.session_scope() as session:
            records = recovery.find_failed_video_records(session)

        self.assertEqual(
            [record.id for record in records],
            [selected_id, embed_selected_id, empty_path_selected_id],
        )

    def test_uses_attempt_id_not_timestamp_to_choose_latest_attempt(self) -> None:
        now = datetime.now(timezone.utc)
        selected_id = self.add_video(
            "id-order",
            attempts=(
                self.successful_attempt(),
                self.failed_attempt(finished_at=now - timedelta(days=1)),
            ),
        )

        with core.session_scope() as session:
            records = recovery.find_failed_video_records(session)

        self.assertEqual([record.id for record in records], [selected_id])

    def test_keeps_duplicate_video_ids_as_separate_ordered_records(self) -> None:
        first_id = self.add_video(
            "duplicate-id",
            attempts=(self.failed_attempt(),),
        )
        second_id = self.add_video(
            "duplicate-id",
            embed_code="<iframe></iframe>",
            attempts=(self.failed_attempt(),),
        )

        with core.session_scope() as session:
            records = recovery.find_failed_video_records(session)

        self.assertEqual(
            [(record.id, record.video_id) for record in records],
            [(first_id, "duplicate-id"), (second_id, "duplicate-id")],
        )


class RecoveryCliTests(RecoveryDatabaseTestCase):
    def test_help_does_not_load_config(self) -> None:
        stdout = StringIO()
        with patch.object(recovery, "get_config") as get_config:
            with redirect_stdout(stdout):
                with self.assertRaises(SystemExit) as raised:
                    recovery.main(["--help"])

        self.assertEqual(raised.exception.code, 0)
        self.assertIn("usage:", stdout.getvalue())
        get_config.assert_not_called()

    def test_config_error_returns_two(self) -> None:
        stderr = StringIO()
        with patch.object(
            recovery,
            "get_config",
            side_effect=ConfigError("invalid test config"),
        ):
            with redirect_stderr(stderr):
                exit_code = recovery.main([])

        self.assertEqual(exit_code, 2)
        self.assertIn("Configuration error: invalid test config", stderr.getvalue())

    def test_missing_database_returns_two_without_creating_it(self) -> None:
        missing_path = self.data_root / "missing" / "ytcrawl.sqlite3"
        missing_config = SimpleNamespace(
            db_path=missing_path,
            db_url=f"sqlite:///{missing_path.as_posix()}",
            media_root=self.media_root,
        )
        stderr = StringIO()

        with patch.object(recovery, "get_config", return_value=missing_config):
            with redirect_stderr(stderr):
                exit_code = recovery.main([])

        self.assertEqual(exit_code, 2)
        self.assertFalse(missing_path.exists())
        self.assertIn(str(missing_path), stderr.getvalue())

    def test_no_targets_returns_zero_without_invoking_downloader(self) -> None:
        stdout = StringIO()
        with patch.object(recovery, "get_config", return_value=self.config):
            with patch.object(recovery, "crawl_youtube_videos") as downloader:
                with redirect_stdout(stdout):
                    exit_code = recovery.main([])

        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout.getvalue().strip(), "No failed downloads to recover.")
        downloader.assert_not_called()

    def test_success_passes_all_duplicate_records_to_existing_downloader(self) -> None:
        first_id = self.add_video(
            "same-video",
            attempts=(self.failed_attempt(),),
        )
        second_id = self.add_video(
            "same-video",
            embed_code="<iframe></iframe>",
            attempts=(self.failed_attempt(),),
        )
        stdout = StringIO()

        with patch.object(recovery, "get_config", return_value=self.config):
            with patch.object(
                recovery,
                "crawl_youtube_videos",
                return_value=(2, 0),
            ) as downloader:
                with redirect_stdout(stdout):
                    exit_code = recovery.main([])

        self.assertEqual(exit_code, 0)
        downloader.assert_called_once()
        output_dir, records = downloader.call_args.args
        self.assertEqual(output_dir, self.media_root)
        self.assertEqual([record.id for record in records], [first_id, second_id])
        self.assertEqual(
            stdout.getvalue().strip(),
            "Recovered 2 videos, failed 0.",
        )

    def test_download_failure_returns_one(self) -> None:
        self.add_video("fails-again", attempts=(self.failed_attempt(),))
        stdout = StringIO()

        with patch.object(recovery, "get_config", return_value=self.config):
            with patch.object(
                recovery,
                "crawl_youtube_videos",
                return_value=(0, 1),
            ):
                with redirect_stdout(stdout):
                    exit_code = recovery.main([])

        self.assertEqual(exit_code, 1)
        self.assertEqual(
            stdout.getvalue().strip(),
            "Recovered 0 videos, failed 1.",
        )

    def test_unexpected_recovery_error_returns_one(self) -> None:
        stderr = StringIO()
        with patch.object(recovery, "get_config", return_value=self.config):
            with patch.object(
                recovery.core,
                "configure",
                side_effect=RuntimeError("database unavailable"),
            ):
                with redirect_stderr(stderr):
                    exit_code = recovery.main([])

        self.assertEqual(exit_code, 1)
        self.assertIn("Recovery error: database unavailable", stderr.getvalue())


if __name__ == "__main__":
    import unittest

    unittest.main()
