from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request

from ytcrawln.review import schemas, service

router = APIRouter()


@router.get("/videos/", tags=["videos"])
async def get_videos_list():
    return None


@router.get(
    "/videos/{video_ref_id}",
    response_model=schemas.VideoDetailResponse,
    tags=["videos"],
)
def get_video_details(
    request: Request,
    video_ref_id: Annotated[int, Path(ge=1)],
) -> schemas.VideoDetailResponse:
    response = service.get_video_detail(
        video_ref_id,
        media_root=request.app.state.media_root,
    )
    if response is None:
        raise HTTPException(status_code=404, detail="Video not found.")
    return response


@router.get("/videos/media/{video_ref_id}", tags=["videos"])
async def get_video_media(video_ref_id: int):
    return None
