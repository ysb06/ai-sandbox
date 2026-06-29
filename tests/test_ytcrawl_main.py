import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from ytcrawl import __main__ as ytcrawl_main
from ytcrawl import db
from ytcrawl.search import youtube as youtube_search


def make_search_response(count: int) -> dict:
    return {
        "kind": "youtube#searchListResponse",
        "etag": "response-etag",
        "nextPageToken": "next-token",
        "pageInfo": {"totalResults": 1234, "resultsPerPage": count},
        "items": [
            {
                "kind": "youtube#searchResult",
                "etag": f"item-etag-{index}",
                "id": {"kind": "youtube#video", "videoId": f"video-{index:02d}"},
                "snippet": {
                    "publishedAt": f"2026-06-24T00:{index:02d}:00Z",
                    "channelId": f"channel-{index:02d}",
                    "title": f"Face candidate {index}",
                    "description": f"Search result description {index}",
                    "publishTime": f"2026-06-24T01:{index:02d}:00Z",
                },
            }
            for index in range(count)
        ],
    }


class FakeSearchListRequest:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.num_retries = None

    def execute(self, num_retries: int = 0) -> dict:
        self.num_retries = num_retries
        return self.response


class FakeSearchResource:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.params = None

    def list(self, **params):
        self.params = params
        return FakeSearchListRequest(self.response)


class FakeYouTube:
    def __init__(self, response: dict) -> None:
        self.search_resource = FakeSearchResource(response)

    def search(self):
        return self.search_resource


class YtCrawlArgumentTests(unittest.TestCase):
    def test_parser_accepts_only_plan_options(self):
        args = ytcrawl_main.parse_args(
            [
                "search",
                "--query",
                "interview",
                "--preset",
                "vlog",
                "--published-after",
                "2026-06-24T00:00:00Z",
                "--published-before",
                "2026-06-25T00:00:00Z",
            ]
        )

        self.assertEqual(args.command, "search")
        self.assertEqual(args.query, "interview")
        self.assertEqual(args.preset, "vlog")
        self.assertEqual(args.published_after, "2026-06-24T00:00:00Z")
        self.assertEqual(args.published_before, "2026-06-25T00:00:00Z")

        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit):
                ytcrawl_main.parse_args(["search", "--region-code", "US"])

    def test_query_overrides_preset_and_fixed_search_params_are_applied(self):
        args = ytcrawl_main.parse_args(
            [
                "search",
                "--query",
                "custom face query",
                "--preset",
                "interview",
                "--published-after",
                "2026-06-24T00:00:00Z",
                "--published-before",
                "2026-06-25T00:00:00Z",
            ]
        )

        self.assertEqual(youtube_search.resolve_query(args), "custom face query")
        self.assertIsNone(youtube_search.resolve_preset(args))
        self.assertEqual(
            youtube_search.build_search_params(args),
            {
                "part": "snippet",
                "type": "video",
                "maxResults": 50,
                "regionCode": "KR",
                "safeSearch": "none",
                "videoLicense": "creativeCommon",
                "q": "custom face query",
                "publishedAfter": "2026-06-24T00:00:00Z",
                "publishedBefore": "2026-06-25T00:00:00Z",
            },
        )

    def test_default_preset_resolves_to_talking_head_query(self):
        args = ytcrawl_main.parse_args(["search"])

        self.assertEqual(youtube_search.resolve_preset(args), "talking-head")
        self.assertEqual(youtube_search.resolve_query(args), '"talking head" face person')
        self.assertEqual(youtube_search.build_search_params(args)["q"], '"talking head" face person')


class YtCrawlPersistenceTests(unittest.TestCase):
    def test_search_runs_table_uses_explicit_columns_not_json_blobs(self):
        engine = create_engine("sqlite:///:memory:")
        db.create_schema(engine)

        column_names = {
            column["name"]
            for column in inspect(engine).get_columns("youtube_search_runs")
        }

        self.assertNotIn("fixed_params_json", column_names)
        self.assertNotIn("response_summary_json", column_names)
        self.assertEqual(
            {
                "id",
                "executed_at",
                "query",
                "preset",
                "published_after",
                "published_before",
                "request_hash",
                "part",
                "search_type",
                "max_results",
                "region_code",
                "safe_search",
                "video_license",
                "response_kind",
                "response_etag",
                "next_page_token",
                "total_results",
                "results_per_page",
                "item_count",
                "page",
            },
            column_names,
        )

    def test_search_response_is_saved_as_one_run_and_fifty_video_rows(self):
        engine = create_engine("sqlite:///:memory:")
        db.create_schema(engine)
        response = make_search_response(50)

        with Session(engine) as session:
            run = db.save_search_response(
                session,
                query="face candidate",
                preset="talking-head",
                published_after="2026-06-24T00:00:00Z",
                published_before="2026-06-25T00:00:00Z",
                fixed_params=youtube_search.FIXED_SEARCH_PARAMS,
                request_hash="hash-1",
                page=1,
                response=response,
            )
            run_id = run.id
            session.commit()

        with Session(engine) as session:
            runs = session.scalars(select(db.YouTubeSearchRun)).all()
            videos = session.scalars(select(db.Video).order_by(db.Video.id)).all()

        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].id, run_id)
        self.assertEqual(runs[0].query, "face candidate")
        self.assertEqual(runs[0].preset, "talking-head")
        self.assertEqual(runs[0].published_after, "2026-06-24T00:00:00Z")
        self.assertEqual(runs[0].published_before, "2026-06-25T00:00:00Z")
        self.assertEqual(runs[0].part, "snippet")
        self.assertEqual(runs[0].search_type, "video")
        self.assertEqual(runs[0].max_results, 50)
        self.assertEqual(runs[0].region_code, "KR")
        self.assertEqual(runs[0].safe_search, "none")
        self.assertEqual(runs[0].video_license, "creativeCommon")
        self.assertEqual(runs[0].response_kind, "youtube#searchListResponse")
        self.assertEqual(runs[0].response_etag, "response-etag")
        self.assertEqual(runs[0].next_page_token, "next-token")
        self.assertEqual(runs[0].total_results, 1234)
        self.assertEqual(runs[0].results_per_page, 50)
        self.assertEqual(runs[0].item_count, 50)

        self.assertEqual(len(videos), 50)
        self.assertEqual(videos[0].search_id, run_id)
        self.assertEqual(videos[0].kind, "youtube#searchResult")
        self.assertEqual(videos[0].etag, "item-etag-0")
        self.assertEqual(videos[0].video_id, "video-00")
        self.assertEqual(videos[0].title, "Face candidate 0")
        self.assertEqual(videos[0].description, "Search result description 0")
        self.assertEqual(videos[0].publishTime, "2026-06-24T01:00:00Z")

    def test_duplicate_video_ids_are_saved_as_distinct_rows_per_search_run(self):
        engine = create_engine("sqlite:///:memory:")
        db.create_schema(engine)
        response = make_search_response(1)

        with Session(engine) as session:
            first_run = db.save_search_response(
                session,
                query="first",
                preset=None,
                published_after=None,
                published_before=None,
                fixed_params=youtube_search.FIXED_SEARCH_PARAMS,
                request_hash="hash-1",
                page=1,
                response=response,
            )
            second_run = db.save_search_response(
                session,
                query="second",
                preset=None,
                published_after=None,
                published_before=None,
                fixed_params=youtube_search.FIXED_SEARCH_PARAMS,
                request_hash="hash-2",
                page=1,
                response=response,
            )
            first_run_id = first_run.id
            second_run_id = second_run.id
            session.commit()

        with Session(engine) as session:
            videos = session.scalars(select(db.Video).order_by(db.Video.id)).all()

        self.assertEqual(first_run_id + 1, second_run_id)
        self.assertEqual(len(videos), 2)
        self.assertEqual(videos[0].video_id, videos[1].video_id)
        self.assertNotEqual(videos[0].id, videos[1].id)
        self.assertNotEqual(videos[0].search_id, videos[1].search_id)

    def test_search_command_persists_results_with_fake_youtube_client(self):
        response = make_search_response(2)
        fake_youtube = FakeYouTube(response)

        with tempfile.TemporaryDirectory() as tmpdir:
            db_url = f"sqlite:///{Path(tmpdir) / 'ytcrawl.sqlite3'}"
            with redirect_stdout(StringIO()):
                exit_code = ytcrawl_main.main(
                    [
                        "search",
                        "--query",
                        "custom face query",
                        "--published-after",
                        "2026-06-24T00:00:00Z",
                        "--published-before",
                        "2026-06-25T00:00:00Z",
                    ],
                    db_url=db_url,
                    env={"YOUTUBE_API_KEY": "fake-key"},
                    youtube_factory=lambda _api_key: fake_youtube,
                )

            engine = db.create_engine_for_url(db_url)
            with Session(engine) as session:
                runs = session.scalars(select(db.YouTubeSearchRun)).all()
                videos = session.scalars(select(db.Video).order_by(db.Video.id)).all()

        self.assertEqual(exit_code, 0)
        self.assertEqual(fake_youtube.search_resource.params["q"], "custom face query")
        self.assertIsNone(runs[0].preset)
        self.assertEqual(runs[0].item_count, 2)
        self.assertEqual(len(videos), 2)

    def test_missing_youtube_api_key_returns_clear_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url = f"sqlite:///{Path(tmpdir) / 'ytcrawl.sqlite3'}"
            with redirect_stderr(StringIO()):
                exit_code = ytcrawl_main.main(
                    ["search", "--query", "face"],
                    db_url=db_url,
                    env={},
                    youtube_factory=lambda _api_key: self.fail("factory should not be called"),
                )

        self.assertEqual(exit_code, 2)

    def test_download_command_does_not_require_youtube_api_key(self):
        calls = []

        def fake_download(video_id, output_dir):
            calls.append((video_id, output_dir))
            return Path(output_dir) / f"vid_{video_id}.mp4"

        with tempfile.TemporaryDirectory() as tmpdir:
            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = ytcrawl_main.main(
                    [
                        "download",
                        "--video-id",
                        "dQw4w9WgXcQ",
                        "--output-dir",
                        tmpdir,
                    ],
                    env={},
                    download_youtube_video=fake_download,
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls, [("dQw4w9WgXcQ", tmpdir)])
        self.assertIn("vid_dQw4w9WgXcQ.mp4", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
