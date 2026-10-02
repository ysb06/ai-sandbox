from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.engine import make_url

from ytcrawln.config import get_config
from ytcrawln.db import core
from ytcrawln.utils import is_sftp_source

from .routers import clips, videos

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(
    *,
    db_url: str | None = None,
    media_root: str | Path | None = None,
) -> FastAPI:
    if db_url is None or media_root is None:
        config = get_config()
        selected_db_url = config.db_url if db_url is None else db_url
        selected_media_root = config.media_root if media_root is None else media_root
    else:
        selected_db_url = db_url
        selected_media_root = media_root

    if is_sftp_source(selected_media_root):
        raise ValueError(
            "Review supports local media files only; SFTP MEDIA_ROOT is supported "
            "only by the video analysis commands. Set a local MEDIA_ROOT for Review."
        )
    selected_media_root = Path(selected_media_root).expanduser().resolve()

    url = make_url(selected_db_url)
    if url.get_backend_name() == "sqlite" and url.database not in (None, "", ":memory:"):
        db_path = Path(url.database)
        if not db_path.is_file():
            raise FileNotFoundError(
                f"Review database file not found: {db_path.resolve()}"
            )

    core.configure(selected_db_url)
    app = FastAPI(title="ytcrawln review")
    app.state.media_root = selected_media_root
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(clips.router)
    app.include_router(videos.router)

    @app.get("/", response_class=FileResponse, include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

    return app
