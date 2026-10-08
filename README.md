# AI Data Sandbox

AI 연구용 영상 데이터를 수집하고 검수하며, 음성 전사와 화자 분리를
실험하기 위한 Python 프로젝트입니다.

## Installation

```bash
pip install -r requirements.txt
```

### Functions

1. 수집된 YouTube 영상에서 얼굴을 검출하고 해당 부분의 클립을 Review 후보로 추출

```bash
python -m ytcrawln.crawler.youtube.face --max-results 5 --show-progress
```

2. 특정 비디오들을 videos 테이블에서 제거

```bash
python -m ytcrawln.db.utils delete-videos --ids-file inputs/delete_ids_1.txt
python -m ytcrawln.db.utils delete-videos --ids-file inputs/delete_ids_1.txt --execute
```

3. videos 테이블 기준으로 media_root 내 참조 없는 영상을 정리

```bash
python -m ytcrawln.db.utils cleanup-media
python -m ytcrawln.db.utils cleanup-media --execute
```