from pathlib import Path

from fastapi import FastAPI
from sqlalchemy.engine import make_url

from ytcrawln.config import get_config
from ytcrawln.db import core

from .routers import videos


def create_app(*, db_url: str | None = None) -> FastAPI:
    selected_db_url = get_config().db_url if db_url is None else db_url

    url = make_url(selected_db_url)
    if url.get_backend_name() == "sqlite" and url.database not in (None, "", ":memory:"):
        db_path = Path(url.database)
        if not db_path.is_file():
            raise FileNotFoundError(
                f"Review database file not found: {db_path.resolve()}"
            )

    core.configure(selected_db_url)
    app = FastAPI(title="ytcrawln review")
    app.include_router(videos.router)

    return app
