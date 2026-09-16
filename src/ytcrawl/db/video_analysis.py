from typing import Any
from sqlalchemy import ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column

from ytcrawl.db.core import Base

class Video(Base):
    __tablename__ = "video_analysis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ref_id: Mapped[int] = mapped_column(
        ForeignKey("videos.id"),
        nullable=False,
        index=True,
    )
    vtt_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    vtt_options: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    vtt_result: Mapped[str | None] = mapped_column(Text, nullable=True)

# Todo: 다음 기능들을 구현해야 함
# 1. 영상 하나 당 같은 모델의 출력은 하나만 존재해야함. 이것을 판별하는 함수를 구현. 
#    이 함수의 반환값은 video_analysis 테이블에서 중복된 모델 출력의 id 묶음 리스트를 반환해야 함.
# 2. 중복된 모델 출력이 존재할 경우, 가장 최근에 생성된 id를 제외한 나머지 id들을 삭제하는 함수를 구현.
# 3. 특정 모델 이름의 데이터가 없는 영상 리스트를 반환하는 함수를 구현. 
