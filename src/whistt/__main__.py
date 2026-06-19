import argparse
from pathlib import Path
import sys
from whistt.transcriber import Transcriber

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m whistt",
        description=(
            "Run WhisperX transcription, alignment, and optional speaker diarization on an input file."
        ),
    )
    parser.add_argument("input_path", help="Path to the input audio or video file.")
    parser.add_argument(
        "output_dir",
        help="Directory where JSON/TXT/SRT/VTT/TSV results will be written.",
    )
    parser.add_argument(
        "--no-diarization",
        action="store_true",
        help="Skip speaker diarization and export transcript-only artifacts.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        transcriber = Transcriber(enable_diarization=not args.no_diarization)
        summary = transcriber.run_pipeline(Path(args.input_path), Path(args.output_dir))
    except KeyboardInterrupt:
        print("Interrupted!", file=sys.stderr)
        return 130

    print(f"Wrote transcript artifacts to {summary['output_dir']}")
    print(f"Summary: {summary['output_files']['summary_json']}")
    return 0


if __name__ == "__main__":
    main()
