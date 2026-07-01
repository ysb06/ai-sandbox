from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine as sqlalchemy_create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def create_engine_for_url(db_url: str) -> Engine:
    url = make_url(db_url)
    if url.drivername == "sqlite" and url.database not in (None, "", ":memory:"):
        Path(url.database).expanduser().parent.mkdir(parents=True, exist_ok=True)
    return sqlalchemy_create_engine(db_url)


def create_all(engine: Engine) -> None:
    from ytcrawl.db import (  # noqa: F401
        video_download_attempts,
        videos,
        videos_detail,
        youtube_search_runs,
    )

    Base.metadata.create_all(engine)
