from collections.abc import Generator
from contextlib import ExitStack
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import cast

import av
import numpy as np
from numpy.typing import NDArray
from PIL import Image as PILImage


@dataclass
class SampledImage:
    """A sampled frame with an owned, rotation-corrected RGB uint8 array."""

    frame_index: int
    timestamp_sec: float
    image: NDArray[np.uint8]


def _frame_to_rgb_image(frame: av.VideoFrame) -> NDArray[np.uint8]:
    with ExitStack() as stack:
        image: PILImage.Image = frame.to_image()
        stack.callback(image.close)

        rotation = frame.rotation
        if rotation:
            # PyAV and Pillow both use counterclockwise rotation angles.
            image = image.rotate(
                rotation,
                resample=PILImage.Resampling.BICUBIC,
                expand=True,
            )
            stack.callback(image.close)

        # The returned array remains valid after the Pillow images are closed.
        return np.array(image, dtype=np.uint8, copy=True, order="C")


def iter_video_images(
    file_path: str | Path,
    *,
    interval: float = 0.5,
) -> Generator[SampledImage, None, None]:
    sample_interval = Fraction(str(interval))
    next_sample_time = Fraction(0)
    first_timestamp: Fraction | None = None

    with av.open(str(file_path)) as container:
        for frame_index, frame in enumerate(container.decode(video=0)):
            timestamp = cast(int, frame.pts) * cast(Fraction, frame.time_base)
            if first_timestamp is None:
                first_timestamp = timestamp
            elapsed = timestamp - first_timestamp
            if elapsed < next_sample_time:
                continue

            next_sample_time = (elapsed // sample_interval + 1) * sample_interval
            yield SampledImage(
                frame_index=frame_index,
                timestamp_sec=float(elapsed),
                image=_frame_to_rgb_image(frame),
            )
