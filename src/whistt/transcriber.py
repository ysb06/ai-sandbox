import json
import os
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Union

import numpy as np
import torch
import whisperx
from numpy.typing import ArrayLike
from tqdm import tqdm
from whisperx.asr import FasterWhisperPipeline
from whisperx.diarize import DiarizationPipeline
from whisperx.schema import AlignedTranscriptionResult, TranscriptionResult
from whisperx.utils import get_writer


class TranscriptionModel(Enum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    LARGE_V3 = "large-v3"


# class AlignmentModel(Enum):
#     SMALL = "small"
#     MEDIUM = "medium"
#     LARGE = "large"


class DiarizationModel(Enum):
    SPEAKER_DIARIZATION_COMMUNITY_V1 = "pyannote/speaker-diarization-community-1"
    SPEAKER_DIARIZATION_V3_1 = "pyannote/speaker-diarization-3.1"

DEFAULT_LANGUAGE = "ko"
DEFAULT_DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
DEFAULT_COMPUTE_TYPE = "float16" if torch.cuda.is_available() else "int8"
HUGGINGFACE_TOKEN = os.environ.get("HF_TOKEN", "").strip()
WRITER_OPTIONS = {
    "max_line_width": None,
    "max_line_count": None,
    "highlight_words": False,
}


class Transcriber:
    def __init__(
        self,
        transcription_model=TranscriptionModel.LARGE_V3,
        diarization_model=DiarizationModel.SPEAKER_DIARIZATION_V3_1,
        enable_diarization: bool = True,
    ):
        self._transcription_model_name = transcription_model.value
        self._transcription_model: Optional[FasterWhisperPipeline] = None
        self.transcription_model = transcription_model

        self._enable_diarization = enable_diarization
        self._diarization_model_name = diarization_model.value
        self._diarization_model: Optional[DiarizationPipeline] = None
        if self._enable_diarization:
            self.diarization_model = diarization_model

        self.progress_bar = tqdm(
            total=100,
            desc="Running pipeline...",
            unit="%",
            leave=True,
            dynamic_ncols=True,
            bar_format="{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt}",
        )

    def _set_progress_state(self, state: str) -> None:
        self.progress_bar.set_description_str(state)
        self.progress_bar.reset(total=100)
        self.progress_bar.refresh()

    @property
    def transcription_model(self):
        return self._transcription_model

    @transcription_model.setter
    def transcription_model(self, model_name: TranscriptionModel):
        self._transcription_model_name = model_name.value
        self._transcription_model = whisperx.load_model(
            model_name.value,
            DEFAULT_DEVICE,
            compute_type=DEFAULT_COMPUTE_TYPE,
        )

    @property
    def diarization_model(self):
        return self._diarization_model

    @diarization_model.setter
    def diarization_model(self, model_name: DiarizationModel):
        self._diarization_model_name = model_name.value
        self._diarization_model = DiarizationPipeline(
            model_name=model_name.value,
            token=HUGGINGFACE_TOKEN,
            device=DEFAULT_DEVICE,
        )

    def run_pipeline(self, input_path: Path, output_dir: Path):
        input_path = input_path.expanduser().resolve()
        output_dir = output_dir.expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        audio: ArrayLike = whisperx.load_audio(str(input_path))
        transcription = self.transcribe(audio)
        alignment = self.align(transcription, audio)
        if self._enable_diarization:
            result = self.diarize(audio, alignment, transcription["language"])
        else:
            result = alignment

        summary = self.save_output(
            result,
            input_path,
            output_dir,
            diarization_enabled=self._enable_diarization,
        )

        self.progress_bar.close()
        return summary


    def transcribe(self, audio: ArrayLike, language: str = DEFAULT_LANGUAGE) -> TranscriptionResult:
        self._set_progress_state("Transcribing...")

        if self._transcription_model is None:
            raise ValueError("Transcription model is not loaded")

        transcription_result: TranscriptionResult = self._transcription_model.transcribe(
            np.array(audio), language=language, progress_callback=self.print_progress
        )

        return transcription_result

    def align(self, transcription: TranscriptionResult, audio: ArrayLike) -> AlignedTranscriptionResult:
        self._set_progress_state("Aligning...")

        language = transcription.get("language")
        if language is None:
            raise ValueError("Language not found in transcription result")
        
        align_model, align_metadata = whisperx.load_align_model(
            language_code=language,
            device=DEFAULT_DEVICE,
        )
        alignment_result = whisperx.align(
            transcription["segments"],
            align_model,
            align_metadata,
            np.array(audio),
            DEFAULT_DEVICE,
            return_char_alignments=False,
            progress_callback=self.print_progress,
        )
        alignment_result["language"] = language

        return alignment_result

    def diarize(self, audio: ArrayLike, alignment: AlignedTranscriptionResult, language: str):
        self._set_progress_state("Diarizing...")

        if self._diarization_model is None:
            raise ValueError("Diarization model is not loaded")

        diarize_segments = self._diarization_model(
            np.array(audio), progress_callback=self.print_progress
        )
        diarization_result = whisperx.assign_word_speakers(diarize_segments, alignment)
        diarization_result["language"] = language

        return diarization_result

    def save_output(
        self,
        result,
        input_path: Path,
        output_dir: Path,
        diarization_enabled: bool,
    ):
        writer = get_writer("all", str(output_dir))
        writer(result, str(input_path), WRITER_OPTIONS)

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
            "model_name": self._transcription_model_name,
            "language": result.get("language", "unknown"),
            "device": DEFAULT_DEVICE,
            "compute_type": DEFAULT_COMPUTE_TYPE,
            "diarization": {
                "enabled": diarization_enabled,
                "model_name": self._diarization_model_name if diarization_enabled else None,
            },
            "output_files": output_files,
        }

        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        return summary

    def print_progress(self, progress: float):
        progress_value = max(0.0, min(100.0, float(progress)))

        if progress_value < float(self.progress_bar.n):
            self.progress_bar.reset(total=100)

        delta = progress_value - float(self.progress_bar.n)
        self.progress_bar.update(delta)
