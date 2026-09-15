import argparse
from typing import Sequence
import av


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ollavtt", description="Convert video to text."
    )

    parser.add_argument("--input", help="Input video file path")

    return parser.parse_args(argv)

def extract_frames(file_path: str, interval: float = 1) -> list:
    frame_batch = []
    with av.open(file_path) as container:
        for frame in container.decode(video=0):
            frame_batch.append(frame)
    
    return frame_batch
    

if __name__ == "__main__":
    args = parse_args()
