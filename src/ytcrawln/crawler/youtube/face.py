"""
Crawl Step 03: 수집된 영상에서 얼굴을 인식하여 얼굴이 나타난 부분을 기록
"""

from argparse import Namespace
import argparse
from contextlib import closing

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from ytcrawln.config import get_config, DEFAULT_CONFIG_PATH
from ytcrawln.db import core
from ytcrawln.db.tables.face_in_video import FaceInVideo
from ytcrawln.db.tables.videos import Video

from fascan.pipeline import scan_video
from fascan.detector import UltraLightFaceDetector
from fascan.visualizer import show_detections


def run_face_detection(args: Namespace) -> None:
    """얼굴 기록이 없는 영상의 ID와 경로를 조회해 얼굴 검출을 준비한다."""
    config = get_config(args.config)
    core.configure(config.db_url)
    core.create_all()  # Ensure tables exist, especially FaceInVideo
    factory = sessionmaker(bind=core.get_engine(), expire_on_commit=False)
    with factory() as session:
        has_face = (
            select(FaceInVideo.id).where(FaceInVideo.video_ref_id == Video.id).exists()
        )
        videos = session.execute(
            select(Video.id, Video.path).where(~has_face).order_by(Video.id)
        ).all()

    media_root = config.media_root
    for video_id, video_path in videos:
        if not video_path:
            continue

        results = scan_video(
            file_path=(media_root / video_path),
            detector=UltraLightFaceDetector(model_path="models/version-RFB-640.onnx"),
        )

        with closing(results):
            for image, result in results:
                pass


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
