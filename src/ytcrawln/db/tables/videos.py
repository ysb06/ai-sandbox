from sqlalchemy import Integer, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from ytcrawln.db.core import Base
from ytcrawln.db.tables.face_in_video import FaceInVideo


class Videos(Base):
    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str | None] = mapped_column(String(128), nullable=True)
    etag: Mapped[str | None] = mapped_column(String(256), nullable=True)
    video_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    publishTime: Mapped[str | None] = mapped_column(String(32), nullable=True)
    path: Mapped[str | None] = mapped_column(Text, nullable=True)


def get_videos_without_faces(session: Session) -> list[tuple[int, str]]:
    """Return ID/path pairs without face records, using the caller's session."""
    has_faces = (
        select(FaceInVideo.id)
        .where(FaceInVideo.video_ref_id == Videos.id)
        .exists()
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
