from fastapi import APIRouter

router = APIRouter()


@router.get("/videos/", tags=["videos"])
async def get_videos_list():
    return None


@router.get("/videos/{video_ref_id}", tags=["videos"])
async def get_video_details(video_ref_id: int):
    return None


@router.get("/videos/media/{video_ref_id}", tags=["videos"])
async def get_video_media(video_ref_id: int):
    return None
