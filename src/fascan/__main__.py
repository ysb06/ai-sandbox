from fascan.detector import UltraLightFaceDetector
from fascan.pipeline import scan_video
from fascan.visualizer import show_detections


def main() -> None:
    results = scan_video(
        file_path="results/ytcrawl-stage/media/vid__hQJGY5NtdI.mp4",
        detector=UltraLightFaceDetector(model_path="models/version-RFB-640.onnx"),
    )
    show_detections(results, delay_ms=0)


if __name__ == "__main__":
    main()
