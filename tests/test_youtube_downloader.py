import tempfile
import unittest
from pathlib import Path

from ytcrawl.downloader import youtube


class FakeYoutubeDL:
    instances = []

    def __init__(self, options):
        self.options = options
        self.downloaded_urls = []
        FakeYoutubeDL.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def download(self, urls):
        self.downloaded_urls.extend(urls)
        outtmpl = self.options["outtmpl"]
        Path(outtmpl.replace("%(ext)s", "mp4")).write_text("video", encoding="utf-8")
        return 0


class YouTubeDownloaderTests(unittest.TestCase):
    def setUp(self):
        FakeYoutubeDL.instances = []

    def test_download_uses_deterministic_video_id_filename(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = youtube.download(
                "dQw4w9WgXcQ",
                tmpdir,
                youtube_dl_cls=FakeYoutubeDL,
            )

        self.assertEqual(result.name, "vid_dQw4w9WgXcQ.mp4")
        instance = FakeYoutubeDL.instances[0]
        self.assertEqual(
            instance.downloaded_urls,
            ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
        )
        self.assertEqual(instance.options["merge_output_format"], "mp4")
        self.assertIn("bv*[ext=mp4]+ba[ext=m4a]", instance.options["format"])

    def test_download_creates_output_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "nested" / "videos"
            result = youtube.download(
                "dQw4w9WgXcQ",
                output_dir,
                youtube_dl_cls=FakeYoutubeDL,
            )

            self.assertTrue(output_dir.is_dir())
            self.assertEqual(result, output_dir / "vid_dQw4w9WgXcQ.mp4")

    def test_download_rejects_invalid_video_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaisesRegex(ValueError, "Invalid YouTube video_id"):
                youtube.download("../not-valid", tmpdir, youtube_dl_cls=FakeYoutubeDL)

        self.assertEqual(FakeYoutubeDL.instances, [])

    def test_download_raises_when_final_file_cannot_be_resolved(self):
        class NoFileYoutubeDL(FakeYoutubeDL):
            def download(self, urls):
                self.downloaded_urls.extend(urls)
                return 0

        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaisesRegex(youtube.YouTubeDownloadError, "Downloaded file not found"):
                youtube.download(
                    "dQw4w9WgXcQ",
                    tmpdir,
                    youtube_dl_cls=NoFileYoutubeDL,
                )


if __name__ == "__main__":
    unittest.main()
