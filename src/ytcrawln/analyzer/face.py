from contextlib import closing
from pathlib import Path

from av.container import InputContainer
from sqlalchemy import URL, create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from fascan.detector import UltraLightFaceDetector
from fascan.pipeline import FrameDetectionResult, scan_container
from ytcrawln.db.tables.face_in_video import FaceInVideo
from ytcrawln.db.tables.videos import Videos
from ytcrawln.utils import open_video_container, resolve_video_source


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
            file_path = resolve_video_source(media_root, path)

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


def get_videos_without_faces(session: Session) -> list[tuple[int, str]]:
    has_faces = (
        select(FaceInVideo.id).where(FaceInVideo.video_ref_id == Videos.id).exists()
    )
    statement = (
        select(Videos.id, Videos.path)
        .where(~has_faces, Videos.path.is_not(None), Videos.path != "")
        .order_by(Videos.id)
    )
    return [
        (video_id, path)
        for video_id, path in session.execute(statement)
        if path is not None
    ]
