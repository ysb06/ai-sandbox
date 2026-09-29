import argparse
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ytcrawln.config import DEFAULT_CONFIG_PATH, get_config
from ytcrawln.db import core
from ytcrawln.db.tables.face_in_video import FaceInVideo


def count_unique_face_frames(session: Session) -> int:
    unique_frames = (
        select(FaceInVideo.video_ref_id, FaceInVideo.frame).distinct().subquery()
    )
    statement = select(func.count()).select_from(unique_frames)
    return session.execute(statement).scalar_one()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Configuration file (default: project config_ytcrawln.yaml).",
    )
    args = parser.parse_args(argv)

    config = get_config(args.config)
    db_path = config.db_path.expanduser().resolve()
    engine = core.create_engine_for_url(f"sqlite:///{db_path.as_posix()}")
    with Session(engine) as session:
        count = count_unique_face_frames(session)
    engine.dispose()

    print(f"Unique frames with faces: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
