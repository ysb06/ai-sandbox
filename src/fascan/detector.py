from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import numpy as np
import onnxruntime as ort
from numpy.typing import NDArray
from PIL import Image as PILImage


@dataclass
class FaceDetection:
    score: float
    bbox: tuple[float, float, float, float]


def _preprocess(image: NDArray[np.uint8]) -> NDArray[np.float32]:
    """Convert an RGB image to a contiguous RFB-640 input tensor."""
    with ExitStack() as stack:
        source = PILImage.fromarray(image)
        stack.callback(source.close)
        resized = source.resize((640, 480), PILImage.Resampling.BILINEAR)
        stack.callback(resized.close)
        tensor = np.array(resized, dtype=np.float32, copy=True)

    tensor -= np.float32(127)
    tensor /= np.float32(128)
    return np.ascontiguousarray(tensor.transpose(2, 0, 1)[np.newaxis, ...])


def _nms(
    boxes: NDArray[np.float32],
    scores: NDArray[np.float32],
    iou_threshold: float,
) -> list[int]:
    """Select normalized xyxy boxes by descending score using hard NMS."""
    sizes = np.maximum(boxes[:, 2:] - boxes[:, :2], 0)
    areas = sizes[:, 0] * sizes[:, 1]
    remaining = np.argsort(scores, kind="stable")[::-1]
    selected: list[int] = []

    while remaining.size:
        current = int(remaining[0])
        selected.append(current)
        remaining = remaining[1:]

        top_left = np.maximum(boxes[current, :2], boxes[remaining, :2])
        bottom_right = np.minimum(boxes[current, 2:], boxes[remaining, 2:])
        overlap = np.maximum(bottom_right - top_left, 0)
        intersection = overlap[:, 0] * overlap[:, 1]
        union = areas[current] + areas[remaining] - intersection
        iou = intersection / (union + np.float32(1e-5))
        remaining = remaining[iou <= iou_threshold]

    return selected


class UltraLightFaceDetector:
    """Detect faces in RGB uint8 images using version-RFB-640.onnx on CPU.

    The session is loaded once and reused for each detect() call. Images from
    fascan.pipeline are already rotation-corrected; their dimensions define the
    coordinate system of the returned boxes.
    """

    def __init__(
        self,
        model_path: str | Path,
        *,
        score_threshold: float = 0.4,
        nms_iou_threshold: float = 0.3,
    ) -> None:
        self.score_threshold = score_threshold
        self.nms_iou_threshold = nms_iou_threshold
        self._session = ort.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"]
        )
        self._input_name = self._session.get_inputs()[0].name

    def detect(self, image: NDArray[np.uint8]) -> list[FaceDetection]:
        """Return faces in descending score order, or [] when none are found."""
        height, width = image.shape[:2]
        scores_output, boxes_output = cast(
            list[NDArray[np.float32]],
            self._session.run(
                ["scores", "boxes"], {self._input_name: _preprocess(image)}
            ),
        )

        # The model already outputs face probabilities and normalized xyxy boxes.
        face_scores = scores_output[0, :, 1]
        mask = face_scores > self.score_threshold
        scores = face_scores[mask]
        boxes = boxes_output[0, mask, :]
        selected = _nms(boxes, scores, self.nms_iou_threshold)

        scale = np.array([width, height, width, height], dtype=np.float32)
        pixel_boxes = np.clip(boxes[selected], 0, 1) * scale
        results: list[FaceDetection] = []
        for index, box in zip(selected, pixel_boxes):
            left, top, right, bottom = box
            results.append(
                FaceDetection(
                    score=float(scores[index]),
                    bbox=(float(left), float(top), float(right), float(bottom)),
                )
            )
        return results
