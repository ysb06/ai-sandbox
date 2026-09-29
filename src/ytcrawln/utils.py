from contextlib import contextmanager
from pathlib import Path
from typing import Generator

import av
from av.container import InputContainer


@contextmanager
def open_video_container(file_path: str | Path) -> Generator[InputContainer, None, None]:
    with av.open(str(file_path)) as container:
        yield container
