from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ytcrawln.db.core import Base


class FaceInVideo(Base):
    __tablename__ = "face_in_videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_ref_id: Mapped[int] = mapped_column(
        ForeignKey("videos.id"),
        nullable=False,
        index=True,
    )
    frame: Mapped[int | None] = mapped_column(Integer, nullable=False)
    time_sec: Mapped[float | None] = mapped_column(Float, nullable=False)
    image_width: Mapped[int | None] = mapped_column(Integer, nullable=False)
    image_height: Mapped[int | None] = mapped_column(Integer, nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=False)
    left: Mapped[float | None] = mapped_column(Float, nullable=False)
    top: Mapped[float | None] = mapped_column(Float, nullable=False)
    right: Mapped[float | None] = mapped_column(Float, nullable=False)
    bottom: Mapped[float | None] = mapped_column(Float, nullable=False)
