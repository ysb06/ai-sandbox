from __future__ import annotations

import importlib
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)
from fastapi.testclient import TestClient

from ytcrawl.db import core, videos, youtube_search_runs
from ytcrawl.review import service
from ytcrawl.search import youtube as youtube_search


class ReviewMediaPathTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.root = Path(self.tmp_dir.name)
        self.db_url = f"sqlite:///{self.root / 'ytcrawl.sqlite3'}"
        core.configure(self.db_url)
        core.create_all()

    def _create_search_run(self) -> int:
        with core.session_scope() as session:
            run = youtube_search_runs.create_search_run(
                session,
                query="selfie face video",
                published_after=None,
                published_before=None,
                fixed_params=youtube_search.FIXED_SEARCH_PARAMS,
                request_hash="f" * 64,
                page=1,
                response={"items": [], "pageInfo": {}},
            )
            return run.id

    def _create_video(self, *, path: str | None) -> int:
        search_id = self._create_search_run()
        with core.session_scope() as session:
            video = videos.Video(
                search_id=search_id,
                video_id="abc12345678",
                title="Local file",
                path=path,
            )
            session.add(video)
            session.flush()
            return video.id

    def _create_review_app(self, *, media_root: Path):
        with patch.dict(
            "os.environ",
            {
                "YTCRAWL_DB_URL": self.db_url,
                "YTCRAWL_MEDIA_ROOT": str(media_root),
            },
        ):
            app_module = importlib.import_module("ytcrawl.review.app")
        return app_module.create_app(db_url=self.db_url, media_root=media_root)

    def test_detail_uses_video_path_even_when_file_is_outside_media_root(self) -> None:
        video_file = self.root / "downloads" / "video.mp4"
        video_file.parent.mkdir()
        video_file.write_bytes(b"video-bytes")
        video_ref_id = self._create_video(path=str(video_file))
        unrelated_media_root = self.root / "other-media-root"
        unrelated_media_root.mkdir()

        detail = service.get_video_detail(
            media_root=unrelated_media_root,
            video_ref_id=video_ref_id,
        )
        resolved_path = service.resolve_media_path(
            video_ref_id=video_ref_id,
            media_root=unrelated_media_root,
        )

        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertTrue(detail.media.available)
        self.assertEqual(f"/media/videos/{video_ref_id}", detail.media.url)
        self.assertEqual(video_file.resolve(), resolved_path)

    def test_detail_reports_media_unavailable_when_path_file_is_missing(self) -> None:
        missing_path = self.root / "downloads" / "missing.mp4"
        video_ref_id = self._create_video(path=str(missing_path))

        detail = service.get_video_detail(
            media_root=self.root / "other-media-root",
            video_ref_id=video_ref_id,
        )
        resolved_path = service.resolve_media_path(
            video_ref_id=video_ref_id,
            media_root=self.root / "other-media-root",
        )

        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertFalse(detail.media.available)
        self.assertIsNone(detail.media.url)
        self.assertIsNone(resolved_path)

    def test_media_endpoint_serves_video_path_outside_media_root(self) -> None:
        video_file = self.root / "downloads" / "video.mp4"
        video_file.parent.mkdir()
        video_file.write_bytes(b"video-bytes")
        app = self._create_review_app(
            media_root=self.root / "other-media-root",
        )
        video_ref_id = self._create_video(path=str(video_file))

        response = TestClient(app).get(f"/media/videos/{video_ref_id}")

        self.assertEqual(200, response.status_code)
        self.assertEqual(b"video-bytes", response.content)

    def test_media_endpoint_returns_404_when_video_path_file_is_missing(self) -> None:
        app = self._create_review_app(
            media_root=self.root / "other-media-root",
        )
        video_ref_id = self._create_video(path=str(self.root / "missing.mp4"))

        response = TestClient(app).get(f"/media/videos/{video_ref_id}")

        self.assertEqual(404, response.status_code)


if __name__ == "__main__":
    unittest.main()
