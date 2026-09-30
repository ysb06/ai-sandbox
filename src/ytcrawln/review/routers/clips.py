from fastapi import APIRouter, HTTPException

from ytcrawln.review import schemas, service

router = APIRouter()


@router.get("/clips", response_model=schemas.ClipListResponse, tags=["clips"])
def get_clips_list() -> schemas.ClipListResponse:
    try:
        return service.list_clips()
    except service.ClipDataError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
