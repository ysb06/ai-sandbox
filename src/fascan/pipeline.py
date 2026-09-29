from collections.abc import Generator
from contextlib import ExitStack, closing
from dataclasses import dataclass
from fractions import Fraction
from math import isfinite
from pathlib import Path
from typing import cast

import av
import numpy as np
from av.container import InputContainer
from numpy.typing import NDArray
from PIL import Image as PILImage
from tqdm import tqdm

from .detector import FaceDetection, UltraLightFaceDetector


@dataclass
class SampledImage:
    """A sampled frame with an owned, rotation-corrected RGB uint8 array."""

    frame_index: int
    timestamp_sec: float
    image: NDArray[np.uint8]


@dataclass
class FrameDetectionResult:
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


def iter_container_images(
    container: InputContainer,
    *,
    interval: float = 0.5,
) -> Generator[SampledImage, None, None]:
    sample_interval = Fraction(str(interval))
    next_sample_time = Fraction(0)
    first_timestamp: Fraction | None = None

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


def iter_video_images(
    file_path: str | Path,
    *,
    interval: float = 0.5,
) -> Generator[SampledImage, None, None]:
    with av.open(str(file_path)) as container:
        yield from iter_container_images(container, interval=interval)


def scan_container(
    container: InputContainer,
    *,
    detector: UltraLightFaceDetector,
    interval: float = 0.5,
    show_progress: bool = False,
) -> Generator[tuple[NDArray[np.uint8], FrameDetectionResult], None, None]:
    """Detect faces, optionally reporting approximate video-time progress."""
    duration_sec: float | None = None
    if show_progress:
        stream = container.streams.video[0]
        if stream.duration is not None and stream.time_base is not None:
            duration = float(stream.duration * stream.time_base)
            if isfinite(duration) and duration > 0:
                duration_sec = duration

    with (
        closing(iter_container_images(container, interval=interval)) as frames,
        tqdm(
            total=round(duration_sec, 2) if duration_sec is not None else None,
            disable=not show_progress,
            unit="s" if duration_sec is not None else "frame",
        ) as progress,
    ):
        for sampled in frames:
            faces = detector.detect(sampled.image)
            if show_progress:
                if duration_sec is None:
                    progress.update(1)
                else:
                    # Reserve 100% for exhaustion, even with inaccurate metadata.
                    position = min(sampled.timestamp_sec, duration_sec * 0.999)
                    progress.update(round(max(0.0, position - progress.n), 2))

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

        # An exception or explicit generator close bypasses this completion step.
        if show_progress and duration_sec is not None:
            progress.update(round(max(0.0, duration_sec - progress.n), 2))


def scan_video(
    file_path: str | Path,
    *,
    detector: UltraLightFaceDetector,
    interval: float = 0.5,
    show_progress: bool = False,
) -> Generator[tuple[NDArray[np.uint8], FrameDetectionResult], None, None]:
    with av.open(str(file_path)) as container:
        yield from scan_container(
            container,
            detector=detector,
            interval=interval,
            show_progress=show_progress,
        )
