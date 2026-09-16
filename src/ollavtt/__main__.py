import argparse
from typing import Sequence
from ollavtt.pipeline import iter_frame_batches, convert_frame_batch_to_image_batch, describe_batch


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ollavtt", description="Convert video to text."
    )
    parser.add_argument("--input", help="Input video file path")

    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()

    file_path = "results/ytcrawl-stage/media/vid_2mLEwpLlwF8.mp4" if args.input is None else args.input
    for batch in iter_frame_batches(file_path):
        image_batch = convert_frame_batch_to_image_batch(batch)
        batch.clear()
        description = describe_batch(image_batch)
        if description is not None:
            print(description)
        else:
            print("WARNING: No description generated for this batch.")
        break  # For demonstration purposes, we only process the first batch
