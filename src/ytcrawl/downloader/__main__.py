import argparse
import sys
from collections.abc import Sequence

from ytcrawl.downloader.youtube import download


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ytcrawl.downloader",
        description="Download one YouTube video.",
    )
    parser.add_argument("--video-id", required=True, help="YouTube video ID")
    parser.add_argument("--output-dir", required=True, help="Directory for the downloaded video")
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
) -> int:
    args = parse_args(argv)
    try:
        result_path = download(args.video_id, args.output_dir)
    except Exception as exc:  # noqa: BLE001 - show command-line failure clearly.
        print(f"Failed to download {args.video_id}: {exc}", file=sys.stderr)
        return 1

    print(result_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
