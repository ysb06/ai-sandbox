import argparse
import sys
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path

from av.container import InputContainer
from sqlalchemy import URL, create_engine
from sqlalchemy.orm import Session, sessionmaker

from fascan.detector import UltraLightFaceDetector
from fascan.pipeline import FrameDetectionResult, scan_container
from ytcrawln.config import DEFAULT_CONFIG_PATH, get_config
from ytcrawln.db.tables.face_in_video import FaceInVideo
from ytcrawln.db.tables.videos import get_videos_without_faces
from ytcrawln.utils import open_video_container

DEFAULT_MODEL_PATH = DEFAULT_CONFIG_PATH.parent / "models" / "version-RFB-640.onnx"


def select_face_results(
    container: InputContainer,
    detector: UltraLightFaceDetector,
    max_results: int,
    interval: float = 1.0,
    *,
    show_progress: bool = False,
) -> list[FrameDetectionResult]:
    with closing(
        scan_container(
            container,
            detector=detector,
            interval=interval,
            show_progress=show_progress,
        )
    ) as detections:
        candidates = [result for _, result in detections]

    return _farthest_point_sampling(candidates, max_results)


def analyze_and_save_video(
    video: tuple[int, str | Path],
    max_results: int,
    session: Session,
    detector: UltraLightFaceDetector,
    interval: float = 1.0,
    *,
    show_progress: bool = False,
) -> int:
    video_ref_id, file_path = video

    with open_video_container(file_path) as container:
        results = select_face_results(
            container=container,
            detector=detector,
            max_results=max_results,
            interval=interval,
            show_progress=show_progress,
        )

    added_faces = 0
    for result in results:
        for face in result.faces:
            left, top, right, bottom = face.bbox
            session.add(
                FaceInVideo(
                    video_ref_id=video_ref_id,
                    frame=result.frame_index,
                    time_sec=result.timestamp_sec,
                    image_width=result.image_width,
                    image_height=result.image_height,
                    score=face.score,
                    bbox_left=left,
                    bbox_top=top,
                    bbox_right=right,
                    bbox_bottom=bottom,
                )
            )
            added_faces += 1

    return added_faces


def analyze_pending_videos(
    db_path: str | Path,
    max_results: int,
    *,
    media_root: str | Path,
    model_path: str | Path,
    interval: float = 1.0,
    show_progress: bool = False,
) -> int:
    database_path = Path(db_path).expanduser().resolve()
    media_directory = Path(media_root).expanduser()
    engine = create_engine(URL.create("sqlite", database=str(database_path)))
    try:
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            videos = get_videos_without_faces(session)

        if not videos:
            return 0

        detector = UltraLightFaceDetector(model_path=Path(model_path).expanduser())
        completed_videos = 0
        for video_ref_id, path in videos:
            file_path = Path(path).expanduser()
            if not file_path.is_absolute():
                file_path = media_directory / file_path

            with factory.begin() as session:
                analyze_and_save_video(
                    video=(video_ref_id, file_path),
                    max_results=max_results,
                    session=session,
                    detector=detector,
                    interval=interval,
                    show_progress=show_progress,
                )
                session.flush()
            completed_videos += 1
            print(f"영상 {video_ref_id} 처리 완료: {file_path}")

        return completed_videos
    finally:
        engine.dispose()


def _farthest_point_sampling(
    candidates: list[FrameDetectionResult],
    limit: int,
) -> list[FrameDetectionResult]:
    if limit <= 0:
        return []

    ordered = sorted(
        candidates, key=lambda result: (result.timestamp_sec, result.frame_index)
    )
    if len(ordered) <= limit:
        return ordered

    timestamps = [result.timestamp_sec for result in ordered]
    if limit == 1:
        midpoint = (timestamps[0] + timestamps[-1]) / 2
        index = min(range(len(ordered)), key=lambda i: abs(timestamps[i] - midpoint))
        return [ordered[index]]

    selected = [0]
    distances = [abs(timestamp - timestamps[0]) for timestamp in timestamps]
    distances[0] = -1.0

    while len(selected) < limit:
        # Sorting and max's first-match rule make ties deterministic.
        index = max(range(len(ordered)), key=distances.__getitem__)
        selected.append(index)
        for candidate_index, timestamp in enumerate(timestamps):
            distances[candidate_index] = min(
                distances[candidate_index], abs(timestamp - timestamps[index])
            )
        # Keep selected indices excluded even for duplicate timestamps.
        distances[index] = -1.0

    return [ordered[index] for index in sorted(selected)]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyze local videos and save sampled face detections.",
        epilog=(
            "Run from the project root when using relative paths in the config. "
            "Example: python -m ytcrawln.analyzer.face "
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
        media_root = config.media_root.expanduser().resolve()
        model_path = args.model_path.expanduser().resolve()

        try:
            completed_videos = analyze_pending_videos(
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
        print("사용자 요청으로 중단했습니다. 이미 commit된 영상은 유지됩니다.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
