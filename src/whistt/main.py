from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv as _load_dotenv
except ImportError:  # pragma: no cover - exercised only before deps are installed.
    _load_dotenv = None

try:
    from tqdm import tqdm as _tqdm
except ImportError:  # pragma: no cover - exercised only before deps are installed.
    _tqdm = None

DEFAULT_MODEL_NAME = "large-v3"
DEFAULT_DEVICE = "cpu"
DEFAULT_COMPUTE_TYPE = "int8"
DIARIZATION_MODEL_NAME = "pyannote/speaker-diarization-3.1"
WRITER_OPTIONS = {
    "max_line_width": None,
    "max_line_count": None,
    "highlight_words": False,
}


class WhisttError(RuntimeError):
    """Raised when the CLI cannot complete successfully."""


class ProgressReporter:
    """Emit stage logs and progress updates to stderr."""

    def __init__(self, stream: Any | None = None) -> None:
        self.stream = stream if stream is not None else sys.stderr
        self._active_label: str | None = None
        self._bar: Any | None = None

    def stage(self, index: int, total: int, message: str) -> None:
        self.finish()
        text = f"[{index}/{total}] {message}"
        if _tqdm is not None:
            _tqdm.write(text, file=self.stream)
        else:
            print(text, file=self.stream, flush=True)

    def start_progress(self, label: str) -> None:
        self.finish()
        self._active_label = label
        if _tqdm is not None:
            self._bar = _tqdm(
                total=100,
                desc=label,
                file=self.stream,
                unit="%",
                leave=True,
                dynamic_ncols=True,
                mininterval=0.2,
                bar_format="{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt}",
            )
        else:
            print(f"    {label}:   0%", file=self.stream, flush=True)

    def update(self, percent: float) -> None:
        if self._active_label is None:
            return

        percent_value = max(0.0, min(100.0, float(percent)))
        if self._bar is not None:
            delta = percent_value - float(self._bar.n)
            if delta > 0:
                self._bar.update(delta)
            elif percent_value == 100.0 and self._bar.n < 100:
                self._bar.n = 100
                self._bar.refresh()
        else:
            print(
                f"    {self._active_label}: {percent_value:5.1f}%",
                file=self.stream,
                flush=True,
            )

    def finish(self) -> None:
        if self._active_label is None:
            return

        if self._bar is not None:
            if self._bar.n < 100:
                self._bar.update(100 - self._bar.n)
            self._bar.close()
            self._bar = None
        else:
            self.update(100.0)
        self._active_label = None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _strip_optional_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _load_dotenv_file(path: Path) -> bool:
    if not path.is_file():
        return False

    if _load_dotenv is not None:
        _load_dotenv(dotenv_path=path, override=False)
        return True

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = _strip_optional_quotes(value.strip())
    return True


def load_hf_token() -> str:
    token = os.environ.get("HF_TOKEN", "").strip()
    if token:
        return token

    env_candidates = [Path.cwd() / ".env", _repo_root() / ".env"]
    seen: set[Path] = set()
    for candidate in env_candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        _load_dotenv_file(candidate)
        token = os.environ.get("HF_TOKEN", "").strip()
        if token:
            return token

    raise WhisttError(
        "HF_TOKEN is not set. Diarization with "
        f"'{DIARIZATION_MODEL_NAME}' requires a Hugging Face read token in the "
        "environment or a .env file."
    )


def _ensure_ffmpeg_available() -> None:
    if shutil.which("ffmpeg") is None:
        raise WhisttError(
            "ffmpeg was not found in PATH. Install ffmpeg before running whistt."
        )


def _import_runtime() -> tuple[Any, Any, Any]:
    matplotlib_dir = Path(tempfile.gettempdir()) / "whistt-matplotlib"
    matplotlib_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(matplotlib_dir))

    import whisperx
    from whisperx.diarize import DiarizationPipeline
    from whisperx.utils import get_writer

    return whisperx, DiarizationPipeline, get_writer


def _error_with_cause(message: str, exc: Exception) -> str:
    detail = str(exc).strip()
    return f"{message} {detail}" if detail else message


def _write_summary(summary_path: Path, summary: dict[str, Any]) -> None:
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run_transcription(input_path: Path, output_dir: Path) -> dict[str, Any]:
    reporter = ProgressReporter()
    total_stages = 10

    input_path = input_path.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()

    reporter.stage(1, total_stages, "Validating input and environment")
    if not input_path.is_file():
        raise WhisttError(f"Input file does not exist: {input_path}")

    _ensure_ffmpeg_available()
    hf_token = load_hf_token()
    output_dir.mkdir(parents=True, exist_ok=True)

    reporter.stage(2, total_stages, "Importing WhisperX runtime")
    whisperx, DiarizationPipeline, get_writer = _import_runtime()

    reporter.stage(3, total_stages, "Loading audio")
    try:
        audio = whisperx.load_audio(str(input_path))
    except Exception as exc:  # pragma: no cover - depends on local ffmpeg/input file.
        raise WhisttError(
            _error_with_cause(
                f"Failed to decode audio from '{input_path}'. Check ffmpeg and the input file.",
                exc,
            )
        ) from exc

    try:
        reporter.stage(4, total_stages, f"Loading WhisperX model ({DEFAULT_MODEL_NAME})")
        model = whisperx.load_model(
            DEFAULT_MODEL_NAME,
            DEFAULT_DEVICE,
            compute_type=DEFAULT_COMPUTE_TYPE,
        )
        reporter.stage(5, total_stages, "Transcribing audio")
        reporter.start_progress("Transcribing")
        transcription_result = model.transcribe(audio, progress_callback=reporter.update)
    except Exception as exc:  # pragma: no cover - depends on local model/runtime.
        raise WhisttError(
            _error_with_cause(
                f"WhisperX transcription failed for '{input_path}'.",
                exc,
            )
        ) from exc
    finally:
        reporter.finish()

    language = transcription_result.get("language")
    if not language:
        raise WhisttError(
            "WhisperX did not return a language code, so alignment could not continue."
        )

    try:
        reporter.stage(6, total_stages, f"Loading alignment model for language '{language}'")
        align_model, align_metadata = whisperx.load_align_model(
            language_code=language,
            device=DEFAULT_DEVICE,
        )
        reporter.stage(7, total_stages, "Aligning timestamps")
        reporter.start_progress("Aligning")
        aligned_result = whisperx.align(
            transcription_result["segments"],
            align_model,
            align_metadata,
            audio,
            DEFAULT_DEVICE,
            return_char_alignments=False,
            progress_callback=reporter.update,
        )
        aligned_result["language"] = language
    except Exception as exc:  # pragma: no cover - depends on local model/runtime.
        raise WhisttError(
            _error_with_cause(
                f"WhisperX alignment failed for detected language '{language}'.",
                exc,
            )
        ) from exc
    finally:
        reporter.finish()

    try:
        reporter.stage(8, total_stages, f"Loading diarization model ({DIARIZATION_MODEL_NAME})")
        diarize_model = DiarizationPipeline(
            model_name=DIARIZATION_MODEL_NAME,
            token=hf_token,
            device=DEFAULT_DEVICE,
        )
        reporter.stage(9, total_stages, "Running speaker diarization")
        reporter.start_progress("Diarizing")
        diarize_segments = diarize_model(audio, progress_callback=reporter.update)
        final_result = whisperx.assign_word_speakers(diarize_segments, aligned_result)
        final_result["language"] = language
    except Exception as exc:  # pragma: no cover - depends on local model/runtime.
        raise WhisttError(
            _error_with_cause(
                "Diarization failed. Verify HF_TOKEN and access approval for "
                f"'{DIARIZATION_MODEL_NAME}'.",
                exc,
            )
        ) from exc
    finally:
        reporter.finish()

    try:
        reporter.stage(10, total_stages, "Writing transcript artifacts")
        writer = get_writer("all", str(output_dir))
        writer(final_result, str(input_path), WRITER_OPTIONS)
    except Exception as exc:  # pragma: no cover - depends on local filesystem/result shape.
        raise WhisttError(
            _error_with_cause(
                f"Failed to write transcript artifacts to '{output_dir}'.",
                exc,
            )
        ) from exc

    stem = input_path.stem
    output_files = {
        "json": str((output_dir / f"{stem}.json").resolve()),
        "txt": str((output_dir / f"{stem}.txt").resolve()),
        "srt": str((output_dir / f"{stem}.srt").resolve()),
        "vtt": str((output_dir / f"{stem}.vtt").resolve()),
        "tsv": str((output_dir / f"{stem}.tsv").resolve()),
    }
    summary_path = output_dir / f"{stem}.summary.json"
    output_files["summary_json"] = str(summary_path.resolve())

    summary = {
        "input_path": str(input_path),
        "output_dir": str(output_dir),
        "model_name": DEFAULT_MODEL_NAME,
        "language": language,
        "device": DEFAULT_DEVICE,
        "compute_type": DEFAULT_COMPUTE_TYPE,
        "diarization": {
            "enabled": True,
            "model_name": DIARIZATION_MODEL_NAME,
        },
        "output_files": output_files,
    }
    _write_summary(summary_path, summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m whistt",
        description=(
            "Run WhisperX transcription, alignment, and speaker diarization on an input file."
        ),
    )
    parser.add_argument("input_path", help="Path to the input audio or video file.")
    parser.add_argument(
        "output_dir",
        help="Directory where JSON/TXT/SRT/VTT/TSV results will be written.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        summary = run_transcription(Path(args.input_path), Path(args.output_dir))
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except WhisttError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote transcript artifacts to {summary['output_dir']}")
    print(f"Summary: {summary['output_files']['summary_json']}")
    return 0


if __name__ == "__main__":
    main()
