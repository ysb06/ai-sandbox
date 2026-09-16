import argparse
from fractions import Fraction
from io import BytesIO
from math import isfinite
from typing import Iterator, Sequence, TypedDict
import av
from dataclasses import dataclass
from PIL import Image as PILImage
from PIL.Image import Image


@dataclass
class SampledFrame:
    frame_id: int
    frame_time: float
    frame: av.VideoFrame


class FrameImage(TypedDict):
    frame_id: int
    frame_time: float
    image_bytes: bytes


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ollavtt", description="Convert video to text."
    )

    parser.add_argument("--input", help="Input video file path")

    return parser.parse_args(argv)


# Todo: 현재는 프레임 배치만 반환하지만 frame_id, frame_time, frame 튜플 배치 반환으로 변경해야 함
def iter_frame_batches(
    file_path: str,
    start_time: float = 0,
    interval: float = 1,
    batch_size: int = 8,
) -> Iterator[list[SampledFrame]]:
    sample_interval = Fraction(str(interval))
    next_sample_time = Fraction(0)

    start_time_f = Fraction(str(start_time))
    frame_batch: list[SampledFrame] = []
    with av.open(file_path) as container:
        for idx, frame in enumerate(container.decode(video=0)):
            if frame.pts is None or frame.time_base is None:
                raise ValueError("Cannot sample a video frame without a timestamp")

            timestamp = frame.pts * frame.time_base
            if start_time_f is None:
                start_time_f = timestamp
            elapsed = timestamp - start_time_f
            if elapsed < next_sample_time:
                continue

            frame_batch.append(
                SampledFrame(
                    frame_id=idx,
                    frame_time=float(elapsed),
                    frame=frame,
                )
            )
            next_sample_time = (elapsed // sample_interval + 1) * sample_interval
            if len(frame_batch) == batch_size:
                yield frame_batch
                frame_batch = []

    if frame_batch:
        yield frame_batch


def convert_frame_batch_to_image_batch(
    frame_batch: list[SampledFrame],
    *,
    max_image_size: int = 448,  # Maximum size must be greater than zero
    jpeg_quality: int = 85,  # JPEG quality should be between 1 and 95
) -> list[FrameImage]:
    result: list[FrameImage] = []
    for frame in frame_batch:
        with frame.frame.to_image() as image:
            image: Image = image
            image.thumbnail(
                (max_image_size, max_image_size), PILImage.Resampling.LANCZOS
            )
            with BytesIO() as buffer:
                image.save(buffer, format="JPEG", quality=jpeg_quality)
                image_bytes = buffer.getvalue()

        result.append(
            {
                "frame_id": frame.frame_id,
                "frame_time": frame.frame_time,
                "image_bytes": image_bytes,
            }
        )
    return result

def describe_batch(batch: list[SampledFrame]) -> str:
    pass
    


if __name__ == "__main__":
    args = parse_args()
