from __future__ import annotations

LIVE_VIDEO_EXCLUDED_ERROR_TYPE = "live_video_excluded"


class LiveVideoExcludedError(RuntimeError):
    def __init__(self, video_id: str, live_status: str) -> None:
        self.video_id = video_id
        self.live_status = live_status
        super().__init__(
            f"Live video {video_id} was excluded "
            f"(live_status={live_status}); no media download was started."
        )
