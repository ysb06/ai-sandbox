"""
Crawl Step 03: 수집된 영상에서 얼굴을 인식하여 얼굴이 나타난 부분을 기록
"""

from argparse import Namespace
import argparse
from contextlib import closing
import sys

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from tqdm import tqdm

from ytcrawln.config import get_config, DEFAULT_CONFIG_PATH
from ytcrawln.db import core
from ytcrawln.db.tables.face_in_video import FaceInVideo
from ytcrawln.db.tables.videos import Videos

from fascan.pipeline import scan_video
from fascan.detector import UltraLightFaceDetector
from fascan.visualizer import show_detections


def run_face_detection(args: Namespace) -> None:
    """얼굴 기록이 없는 영상을 조회해 얼굴 검출 결과를 저장한다."""
    config = get_config(args.config)
    core.configure(config.db_url)
    core.create_all()  # Ensure tables exist, especially FaceInVideo
    factory = sessionmaker(bind=core.get_engine(), expire_on_commit=False)
    with factory() as session:
        has_face = (
            select(FaceInVideo.id).where(FaceInVideo.video_ref_id == Videos.id).exists()
        )
        videos = session.execute(
            select(Videos.id, Videos.path).where(~has_face).order_by(Videos.id)
        ).all()

    media_root = config.media_root
    total_videos = sum(bool(video_path) for _, video_path in videos)
    with tqdm(total=total_videos, desc="얼굴 검출", unit="video") as progress:
        for video_id, video_path in videos:
            if not video_path:
                tqdm.write(
                    f"영상 {video_id}: 경로가 없어 건너뜁니다.", file=sys.stderr
                )
                continue

            pending_faces = 0
            progress.set_postfix(
                video_id=video_id,
                last_face_frame="-",
                last_face_sec="-",
                pending_faces=pending_faces,
            )
            results = scan_video(
                file_path=(media_root / video_path),
                detector=UltraLightFaceDetector(model_path="models/version-RFB-640.onnx"),
            )

            # 영상 전체의 검출이 끝난 경우에만 커밋하고, 실패하면 롤백한다.
            with factory.begin() as session, closing(results):
                for image, result in results:
                    for face in result.faces:
                        left, top, right, bottom = face.bbox
                        session.add(
                            FaceInVideo(
                                video_ref_id=video_id,
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
                    # 미저장 ORM 객체 누적을 막되 커밋은 영상 단위로 한다.
                    session.flush()
                    pending_faces += len(result.faces)
                    progress.set_postfix(
                        video_id=video_id,
                        last_face_frame=result.frame_index,
                        last_face_sec=f"{result.timestamp_sec:.2f}",
                        pending_faces=pending_faces,
                    )

            # 트랜잭션이 정상적으로 커밋된 영상만 완료 개수에 포함한다.
            progress.set_postfix(video_id=video_id, pending_faces=0, refresh=False)
            progress.update(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Detect faces in videos and record the results in the database."
    )
    parser.add_argument(
        "--config",
        type=str,
        default=DEFAULT_CONFIG_PATH,
        help="Path to the configuration file (default: config_ytcrawln.yaml).",
    )
    args = parser.parse_args()
    run_face_detection(args)
