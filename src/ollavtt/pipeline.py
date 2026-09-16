from contextlib import ExitStack
from fractions import Fraction
from io import BytesIO
from typing import Iterator, TypedDict
import av
from dataclasses import dataclass
from PIL import Image as PILImage
from PIL.Image import Image
from ollama import chat


@dataclass
class SampledFrame:
    frame_id: int
    frame_time: float
    frame: av.VideoFrame


class FrameImage(TypedDict):
    frame_id: int
    frame_time: float
    image_bytes: bytes


def iter_frame_batches(
    file_path: str,
    interval: float = 1.0,  # interval must be greater than zero
    batch_size: int = 8,    # batch_size must be greater than zero
) -> Iterator[list[SampledFrame]]:
    sample_interval = Fraction(str(interval))
    next_sample_time = Fraction(0)

    first_timestamp: Fraction | None = None
    frame_batch: list[SampledFrame] = []
    with av.open(file_path) as container:
        for idx, frame in enumerate(container.decode(video=0)):
            if frame.pts is None or frame.time_base is None:
                raise ValueError("Cannot sample a video frame without a timestamp")

            timestamp = frame.pts * frame.time_base
            if first_timestamp is None:
                first_timestamp = timestamp
            elapsed = timestamp - first_timestamp
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
        with ExitStack() as stack:
            image: Image = frame.frame.to_image()
            stack.callback(image.close)
            rotation = frame.frame.rotation
            if rotation:
                # PyAV and Pillow both use counterclockwise rotation angles.
                image = image.rotate(
                    rotation,
                    resample=PILImage.Resampling.BICUBIC,
                    expand=True,
                )
                stack.callback(image.close)

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


def _get_prompt_for_batch(batch: list[FrameImage]) -> str:
    frame_info = "\n".join(
        f"Frame {item['frame_id']} → {item['frame_time']:.3f} sec" for item in batch
    )

    prompt = f"""The attached images are sampled frames from the same video,
listed in chronological order. Each image corresponds to the
entry at the same position in the list below.

Frame IDs and timestamps:
{frame_info}

Respond in English using the following sections:

1. Scene description
Describe the visible actions and scene changes.
Include the supporting frame IDs and timestamps.
Transcribe important on-screen text only when it is readable.

2. Face visibility
For every provided frame, report:
- Frame ID
- Timestamp
- Human face visibility: visible, not visible, or uncertain

Copy frame IDs and timestamps exactly from the provided list.
If face visibility is unclear, report "uncertain".

Base your answer only on the provided images.
No audio is provided; do not infer speech or sounds.
Do not infer unseen actions between sampled frames.
Do not infer exact event start or end times between frames.
    """

    return prompt


def describe_batch(
    batch: list[FrameImage],
    *,
    model: str = "minicpm-v4.6",
) -> str | None:
    prompt = _get_prompt_for_batch(batch)
    images = [item["image_bytes"] for item in batch]

    response = chat(
        model=model,
        messages=[
            {
                "role": "user",
                "content": prompt,
                "images": images,
            }
        ],
        stream=False,
        options={"temperature": 0},
    )

    return response.message.content
