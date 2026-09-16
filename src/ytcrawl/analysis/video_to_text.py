"""Analyze downloaded videos with MiniCPM-V and save results per video."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from ollama import RequestError, ResponseError

from ollavtt.pipeline import analyze_video
from ytcrawl.config import AppConfig, ConfigError, get_config
from ytcrawl.db import core
from ytcrawl.db.video_vtt_analysis import (
    find_videos_needing_analysis,
    save_analysis,
)


MODEL = "minicpm-v4.6"
SUMMARY_MODEL = "minicpm-v4.6"
DEFAULT_MAX_BATCHES = 3
DEFAULT_INTERVAL = 1.0
BATCH_SIZE = 8
MAX_IMAGE_SIZE = 448
JPEG_QUALITY = 85
TEMPERATURE = 0


@dataclass(frozen=True, slots=True)
class AnalysisRunResult:
    targets: int
    saved: int
    failed: int
    missing: int


def run_analysis(
    config: AppConfig,
    *,
    max_batches: int = DEFAULT_MAX_BATCHES,
    interval: float = DEFAULT_INTERVAL,
) -> AnalysisRunResult:
    core.configure(config.db_url)
    core.create_all()

    with core.session_scope() as session:
        targets = find_videos_needing_analysis(
            session,
            model=MODEL,
            media_root=config.media_root,
        )

    options = {
        "summary_model": SUMMARY_MODEL,
        "max_batches": max_batches,
        "interval": interval,
        "batch_size": BATCH_SIZE,
        "max_image_size": MAX_IMAGE_SIZE,
        "jpeg_quality": JPEG_QUALITY,
        "temperature": TEMPERATURE,
    }
    saved = 0
    failed = 0
    missing = 0

    for index, target in enumerate(targets, start=1):
        label = target.video_id or str(target.ref_id)
        try:
            if not target.path.is_file():
                missing += 1
                print(
                    f"[{index}/{len(targets)}] Skipped missing file: "
                    f"video={label}, path={target.path}",
                    file=sys.stderr,
                )
                continue

            print(
                f"[{index}/{len(targets)}] Analyzing video={label}: {target.path}"
            )
            batch_description, summary = analyze_video(
                str(target.path),
                model=MODEL,
                summary_model=SUMMARY_MODEL,
                max_batches=max_batches,
                interval=interval,
                batch_size=BATCH_SIZE,
                max_image_size=MAX_IMAGE_SIZE,
                jpeg_quality=JPEG_QUALITY,
            )
        except (ConnectionError, RequestError, ResponseError):
            raise
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 - continue with the next video.
            failed += 1
            print(
                f"[{index}/{len(targets)}] Analysis failed: "
                f"video={label}, path={target.path}, error={exc}",
                file=sys.stderr,
            )
            continue

        with core.session_scope() as session:
            save_analysis(
                session,
                ref_id=target.ref_id,
                model=MODEL,
                options=options,
                batch_description=batch_description,
                summary=summary,
            )

        saved += 1
        print(f"[{index}/{len(targets)}] Saved analysis: video={label}")

    return AnalysisRunResult(
        targets=len(targets),
        saved=saved,
        failed=failed,
        missing=missing,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze downloaded videos that do not have a MiniCPM-V result and "
            "save each completed result to video_vtt_analysis."
        )
    )
    parser.add_argument(
        "--max-batches",
        type=int,
        default=DEFAULT_MAX_BATCHES,
        help=f"Maximum frame batches per video (default: {DEFAULT_MAX_BATCHES}).",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help=f"Frame sampling interval in seconds (default: {DEFAULT_INTERVAL}).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = get_config()
    except (OSError, ValueError, ConfigError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        result = run_analysis(
            config,
            max_batches=args.max_batches,
            interval=args.interval,
        )
    except KeyboardInterrupt:
        print(
            "Interrupted; completed videos remain saved and the current video will "
            "be retried on the next run.",
            file=sys.stderr,
        )
        return 130
    except Exception as exc:  # noqa: BLE001 - report fatal DB/Ollama failures.
        print(f"Video analysis stopped: {exc}", file=sys.stderr)
        return 1

    print(
        f"Analysis complete: targets={result.targets}, saved={result.saved}, "
        f"failed={result.failed}, missing={result.missing}."
    )
    return int(bool(result.failed or result.missing))


if __name__ == "__main__":
    raise SystemExit(main())
