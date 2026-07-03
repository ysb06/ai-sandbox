from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine as sqlalchemy_create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def create_engine_for_url(db_url: str) -> Engine:
    url = make_url(db_url)
    if url.drivername == "sqlite" and url.database not in (None, "", ":memory:"):
        Path(url.database).expanduser().parent.mkdir(parents=True, exist_ok=True)
    return sqlalchemy_create_engine(db_url)


def configure(db_url: str) -> None:
    global _engine, _SessionFactory

    if _engine is not None:
        _engine.dispose()
    _engine = create_engine_for_url(db_url)
    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError(
            "Database is not configured. Call core.configure(db_url) first."
        )
    return _engine


def create_all() -> None:
    from ytcrawl.db import (  # noqa: F401
        video_download_attempts,
        video_reviews,
        videos,
        videos_detail,
        youtube_search_runs,
    )

    engine = get_engine()
    Base.metadata.create_all(engine)
    videos.migrate_schema(engine)
    videos_detail.migrate_schema(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    if _SessionFactory is None:
        raise RuntimeError(
            "Database is not configured. Call core.configure(db_url) first."
        )

    session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
