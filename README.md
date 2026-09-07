# AI Data Sandbox

AI 연구용 영상 데이터를 수집하고 검수하며, 음성 전사와 화자 분리를
실험하기 위한 Python 프로젝트입니다.

## 준비

```bash
pdm install
cp config.example.yaml config.yaml
```

`config.yaml`에서 데이터 저장 경로와 검수 서버, 원격 동기화 설정을 지정합니다.
YouTube API를 사용하는 명령에는 `.env`의 `YOUTUBE_API_KEY`가 필요하며,
WhisperX 화자 분리에는 실행 환경의 `HF_TOKEN`이 필요합니다.

아래 명령은 프로젝트 루트에서 실행합니다.

## 주요 CLI

| 명령 | 설명 |
| --- | --- |
| `pdm run python -m ytcrawl --preset interview` | 프리셋 또는 `--query`로 YouTube를 검색하고 메타데이터와 영상을 저장합니다. Creative Commons 제한과 전체 다운로드가 기본으로 활성화됩니다. |
| `pdm run python -m ytcrawl.crawl.youtube.channel.main --channel-id CHANNEL_ID` | 지정한 채널의 공개 업로드를 수집합니다. `--published-after`와 `--published-before`로 기간을 제한할 수 있습니다. |
| `pdm run python -m ytcrawl.review` | `config.yaml`의 호스트와 포트로 영상 검수 웹 UI를 실행합니다. |
| `pdm run python -m whistt INPUT_PATH OUTPUT_DIR` | 오디오 또는 영상 파일을 WhisperX로 전사·정렬하고 화자를 분리합니다. `--no-diarization`으로 화자 분리를 생략할 수 있습니다. |

## 보조 CLI

| 명령 | 설명 |
| --- | --- |
| `pdm run python -m ytcrawl.search --preset PRESET --output search.json` | YouTube `search.list` 원본 응답을 JSON으로 저장합니다. |
| `pdm run python -m ytcrawl.search.youtube_channel --handle @HANDLE` | 핸들 또는 `--name`으로 채널 ID 후보를 조회합니다. |
| `pdm run python -m ytcrawl.search.youtube_detail --video-ids VIDEO_ID --output details.json` | 하나 이상의 영상 상세 응답을 JSON으로 저장합니다. |
| `pdm run python -m ytcrawl.download --video-id VIDEO_ID` | 영상 하나를 설정된 미디어 디렉터리에 다운로드합니다. |
| `pdm run python -m ytcrawl.statistics.stats` | DB가 참조하는 완료 영상의 개수, 용량, 재생 시간을 집계합니다. |
| `pdm run python -m ytcrawl.statistics.acceptance --accept-ratio 0.5` | 검수자 승인 비율 기준을 만족한 영상의 통계를 집계합니다. |
| `pdm run python -m ytcrawl.sync push all` | 설정된 SSH 피어와 `push` 또는 `pull` 방향으로 `db`, `media`, `all` 중 하나를 동기화합니다. DB 동기화는 목적지 DB를 교체하므로 주의해야 합니다. |

`ytcrawl.sync_remote`는 `ytcrawl.sync`가 SSH 피어에서 호출하는 내부 헬퍼이므로
일반적으로 직접 실행하지 않습니다. 각 명령의 상세 옵션은 `--help`로 확인할 수
있습니다. 단, `ytcrawl.review`는 별도 옵션 없이 `config.yaml`을 사용합니다.
