from collections.abc import Generator
from contextlib import closing, suppress

import cv2
import numpy as np
from numpy.typing import NDArray

from .pipeline import FrameDetectionResult


def draw_detections(
    image: NDArray[np.uint8],
    result: FrameDetectionResult,
    *,
    color_rgb: tuple[int, int, int] = (0, 255, 0),
    thickness: int = 2,
) -> NDArray[np.uint8]:
    """Return an RGB copy with boxes drawn in original, corrected coordinates.

    The image must be the rotation-corrected RGB frame associated with result.
    OpenCV drawing writes each channel directly, so color_rgb stays in RGB order.
    """
    annotated = image.copy(order="C")
    height, width = annotated.shape[:2]
    for face in result.faces:
        left, top, right, bottom = (round(value) for value in face.bbox)
        left = max(0, min(left, width - 1))
        top = max(0, min(top, height - 1))
        right = max(0, min(right, width - 1))
        bottom = max(0, min(bottom, height - 1))
        cv2.rectangle(
            annotated,
            (left, top),
            (right, bottom),
            color_rgb,
            thickness=thickness,
            lineType=cv2.LINE_AA,
        )
    return annotated


def show_detections(
    results: Generator[tuple[NDArray[np.uint8], FrameDetectionResult], None, None],
    *,
    window_name: str = "fascan",
    delay_ms: int = 500,
) -> None:
    """Consume and display results on the main thread until done, q, or Escape.

    Closing the window also stops playback. This function owns and closes the
    result generator, including on errors. Each wait is in addition to the time
    spent producing the next result; playback is not synchronized to video time.
    """
    window_created = False
    with closing(results):
        try:
            for image, result in results:
                annotated = draw_detections(image, result)
                display_image = cv2.cvtColor(annotated, cv2.COLOR_RGB2BGR)
                if not window_created:
                    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
                    window_created = True

                cv2.imshow(window_name, display_image)
                cv2.setWindowTitle(
                    window_name,
                    f"{window_name} | {result.timestamp_sec:.2f}s"
                    f" | {len(result.faces)} faces",
                )
                key = cv2.waitKey(delay_ms) & 0xFF
                if key in (ord("q"), 27):
                    break

                try:
                    visible = cv2.getWindowProperty(
                        window_name, cv2.WND_PROP_VISIBLE
                    )
                except cv2.error as error:
                    # Some GUI backends remove the window as soon as it closes.
                    if error.code != cv2.Error.StsNullPtr:
                        raise
                    break
                if visible < 1:
                    break
        finally:
            if window_created:
                # An already-closed window must not mask an inference exception.
                with suppress(cv2.error):
                    cv2.destroyWindow(window_name)
