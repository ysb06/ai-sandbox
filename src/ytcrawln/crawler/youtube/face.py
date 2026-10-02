"""CLI for analyzing videos and saving representative face detections."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from ytcrawln.analyzer import face as face_analyzer
from ytcrawln.config import DEFAULT_CONFIG_PATH, get_config
from ytcrawln.utils import is_sftp_source

DEFAULT_MODEL_PATH = DEFAULT_CONFIG_PATH.parent / "models" / "version-RFB-640.onnx"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyze local or SFTP videos and save sampled face detections.",
        epilog=(
            "Run from the project root when using relative paths in the config. "
            "Example: python -m ytcrawln.crawler.youtube.face "
            "--max-results 10 --interval 1.0 --show-progress"
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Configuration file (default: project config_ytcrawln.yaml).",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        required=True,
        help="Maximum detected timestamps per video, not the number of face rows.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Frame sampling interval in seconds (default: 1.0).",
    )
    parser.add_argument(
        "--model-path",
        type=Path,
        default=DEFAULT_MODEL_PATH,
        help="ONNX model (default: project models/version-RFB-640.onnx).",
    )
    parser.add_argument(
        "--show-progress",
        action="store_true",
        help="Show video analysis progress (disabled by default).",
    )

    try:
        args = parser.parse_args(argv)
        config_path = args.config.expanduser().resolve()
        config = get_config(config_path)
        db_path = config.db_path.expanduser().resolve()
        media_root = (
            config.media_root
            if is_sftp_source(config.media_root)
            else Path(config.media_root).expanduser().resolve()
        )
        model_path = args.model_path.expanduser().resolve()

        try:
            completed_videos = face_analyzer.analyze_pending_videos(
                db_path=db_path,
                max_results=args.max_results,
                media_root=media_root,
                model_path=model_path,
                interval=args.interval,
                show_progress=args.show_progress,
            )
        except Exception as exc:
            print(f"분석 실패: {exc}", file=sys.stderr)
            return 1

        print(f"처리 완료 영상 수: {completed_videos} (얼굴 0건 영상 포함)")
        return 0
    except KeyboardInterrupt:
        print(
            "사용자 요청으로 중단했습니다. 이미 commit된 영상은 유지됩니다.",
            file=sys.stderr,
        )
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
