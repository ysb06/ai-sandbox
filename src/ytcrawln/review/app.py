from fastapi import FastAPI

from ytcrawl.config import get_config
from ytcrawl.db import core


def create_app(*, db_url: str | None = None) -> FastAPI:
    if db_url is None:
        config = get_config()
        selected_db_url = config.db_url if db_url is None else db_url
    else:
        selected_db_url = db_url

    core.configure(selected_db_url)
    core.create_all()
    app = FastAPI(title="ytcrawl review")

    return app
