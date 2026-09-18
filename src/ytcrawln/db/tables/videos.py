from dataclasses import dataclass

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ytcrawln.db.core import Base


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str | None] = mapped_column(String(128), nullable=True)
    etag: Mapped[str | None] = mapped_column(String(256), nullable=True)
    video_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    publishTime: Mapped[str | None] = mapped_column(String(32), nullable=True)
    path: Mapped[str | None] = mapped_column(Text, nullable=True)

