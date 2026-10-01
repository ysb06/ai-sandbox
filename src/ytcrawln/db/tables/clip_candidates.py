from sqlalchemy import Float, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ytcrawln.db.core import Base


class ClipCandidate(Base):
    """A stored review candidate, independent of its source face detections."""

    __tablename__ = "clip_candidates"
    __table_args__ = (
        UniqueConstraint(
            "video_ref_id", "frame", name="uq_clip_candidates_video_ref_id_frame"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_ref_id: Mapped[int] = mapped_column(
        ForeignKey("videos.id"),
        nullable=False,
    )
    frame: Mapped[int] = mapped_column(Integer, nullable=False)
    time_sec: Mapped[float] = mapped_column(Float, nullable=False)
