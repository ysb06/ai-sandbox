import logging
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query
from sqlalchemy.exc import SQLAlchemyError

from ytcrawln.review import schemas, service

router = APIRouter()
logger = logging.getLogger(__name__)


@contextmanager
def _handle_clip_errors() -> Generator[None]:
    try:
        yield
    except (
        service.ClipCandidatesNotInitializedError,
        service.ClipReviewsNotInitializedError,
    ) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except service.ClipCandidateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        logger.exception("Clip database operation failed.")
        raise HTTPException(
            status_code=500,
            detail="검수 데이터 처리에 실패했습니다. 잠시 후 다시 시도해 주세요.",
        ) from exc


@router.get("/clips", response_model=schemas.ClipListResponse, tags=["clips"])
def get_clips_list(
    username: Annotated[schemas.ReviewUsername | None, Query()] = None,
) -> schemas.ClipListResponse:
    with _handle_clip_errors():
        return service.list_clips(username=username)


@router.get(
    "/clips/{clip_id}/reviews",
    response_model=schemas.ReviewListResponse,
    tags=["clips"],
)
def get_clip_reviews(
    clip_id: Annotated[int, Path(ge=1)],
) -> schemas.ReviewListResponse:
    with _handle_clip_errors():
        return service.list_clip_reviews(clip_id)


@router.put(
    "/clips/{clip_id}/review",
    response_model=schemas.ReviewItem,
    tags=["clips"],
)
def put_clip_review(
    clip_id: Annotated[int, Path(ge=1)],
    request: schemas.ReviewSaveRequest,
) -> schemas.ReviewItem:
    with _handle_clip_errors():
        return service.save_clip_review(clip_id, request)
