from contextlib import closing

from av.container import InputContainer

from fascan.detector import UltraLightFaceDetector
from fascan.pipeline import FrameDetectionResult, scan_container


def select_face_results(
    container: InputContainer,
    detector: UltraLightFaceDetector,
    max_results: int,
    interval: float = 1.0,
) -> list[FrameDetectionResult]:
    with closing(
        scan_container(
            container,
            detector=detector,
            interval=interval,
        )
    ) as detections:
        candidates = [result for _, result in detections]

    return _farthest_point_sampling(candidates, max_results)


def _farthest_point_sampling(
    candidates: list[FrameDetectionResult],
    limit: int,
) -> list[FrameDetectionResult]:
    if limit <= 0:
        return []

    ordered = sorted(
        candidates, key=lambda result: (result.timestamp_sec, result.frame_index)
    )
    if len(ordered) <= limit:
        return ordered

    timestamps = [result.timestamp_sec for result in ordered]
    if limit == 1:
        midpoint = (timestamps[0] + timestamps[-1]) / 2
        index = min(range(len(ordered)), key=lambda i: abs(timestamps[i] - midpoint))
        return [ordered[index]]

    selected = [0]
    distances = [abs(timestamp - timestamps[0]) for timestamp in timestamps]
    distances[0] = -1.0

    while len(selected) < limit:
        # Sorting and max's first-match rule make ties deterministic.
        index = max(range(len(ordered)), key=distances.__getitem__)
        selected.append(index)
        for candidate_index, timestamp in enumerate(timestamps):
            distances[candidate_index] = min(
                distances[candidate_index], abs(timestamp - timestamps[index])
            )
        # Keep selected indices excluded even for duplicate timestamps.
        distances[index] = -1.0

    return [ordered[index] for index in sorted(selected)]
