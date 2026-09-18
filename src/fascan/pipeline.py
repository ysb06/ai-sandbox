from collections.abc import Generator
from contextlib import ExitStack, closing
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import cast

import av
import numpy as np
from numpy.typing import NDArray
from PIL import Image as PILImage

from .detector import FaceDetection, UltraLightFaceDetector


@dataclass
class SampledImage:
    """A sampled frame with an owned, rotation-corrected RGB uint8 array."""

    frame_index: int
    timestamp_sec: float
    image: NDArray[np.uint8]


@dataclass
class FrameDetectionResult:
    """Faces in a sampled frame, without retaining the image array.

    Dimensions and xyxy face boxes use rotation-corrected original image pixels.
    timestamp_sec is relative to the first decoded video frame.
    """

    frame_index: int
    timestamp_sec: float
    image_width: int
    image_height: int
    faces: list[FaceDetection]


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


def scan_video(
    file_path: str | Path,
    *,
    detector: UltraLightFaceDetector,
    interval: float = 0.5,
) -> Generator[tuple[NDArray[np.uint8], FrameDetectionResult], None, None]:
    """Yield frames with detected faces in decoding order using one detector.

    Errors propagate to the caller. Exhausting or explicitly closing this
    generator also closes the underlying frame generator. Callers stopping
    early should close it, for example with contextlib.closing.
    """
    with closing(iter_video_images(file_path, interval=interval)) as frames:
        for sampled in frames:
            faces = detector.detect(sampled.image)
            if not faces:
                continue

            height, width = sampled.image.shape[:2]
            yield sampled.image, FrameDetectionResult(
                frame_index=sampled.frame_index,
                timestamp_sec=sampled.timestamp_sec,
                image_width=width,
                image_height=height,
                faces=faces,
            )
