from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import ForeignKey, Integer, JSON, Text, UniqueConstraint, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from ytcrawl.db.core import Base
from ytcrawl.db.videos import Video


@dataclass(frozen=True)
class VideoAnalysisTarget:
    ref_id: int
    video_id: str | None
    path: Path


class VideoAnalysis(Base):
    __tablename__ = "video_vtt_analysis"
    __table_args__ = (
        UniqueConstraint("ref_id", "model", name="uq_video_vtt_analysis_ref_id_model"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ref_id: Mapped[int] = mapped_column(
        ForeignKey("videos.id"),
        nullable=False,
        index=True,
    )
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    options: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    batch_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)


def find_videos_needing_analysis(
    session: Session,
    *,
    model: str,
    media_root: str | Path,
) -> tuple[VideoAnalysisTarget, ...]:
    if not model.strip():
        raise ValueError("Model name must not be empty")

    root = Path(media_root).expanduser().resolve()
    has_analysis = (
        select(VideoAnalysis.id)
        .where(VideoAnalysis.ref_id == Video.id, VideoAnalysis.model == model)
        .exists()
    )
    rows = session.execute(
        select(Video.id, Video.video_id, Video.path)
        .where(Video.path.is_not(None), ~has_analysis)
        .order_by(Video.id)
    )

    targets: list[VideoAnalysisTarget] = []
    for ref_id, video_id, stored_path in rows:
        if not stored_path or not stored_path.strip():
            continue
        try:
            path = (root / Path(stored_path)).resolve()
            if not path.is_file():
                continue
        except (OSError, RuntimeError, ValueError):
            continue
        targets.append(VideoAnalysisTarget(ref_id=ref_id, video_id=video_id, path=path))

    return tuple(targets)


def save_analysis(
    session: Session,
    *,
    ref_id: int,
    model: str,
    options: dict[str, Any],
    batch_description: str,
    summary: str | None = None,
) -> VideoAnalysis:
    if not model.strip():
        raise ValueError("Model name must not be empty")
    if not batch_description.strip():
        raise ValueError("Batch description must not be empty")
    if session.get(Video, ref_id) is None:
        raise ValueError(f"Video not found: {ref_id}")

    statement = insert(VideoAnalysis).values(
        ref_id=ref_id,
        model=model,
        options=options,
        batch_description=batch_description,
        summary=summary,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[VideoAnalysis.ref_id, VideoAnalysis.model],
        set_={
            "options": statement.excluded.options,
            "batch_description": statement.excluded.batch_description,
            "summary": statement.excluded.summary,
        },
    )
    session.execute(statement)
    session.flush()
    return session.scalars(
        select(VideoAnalysis)
        .where(VideoAnalysis.ref_id == ref_id, VideoAnalysis.model == model)
        .execution_options(populate_existing=True)
    ).one()
