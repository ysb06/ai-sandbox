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
| `pdm run python -m ytcrawl.analysis.video_to_text` | 아직 `minicpm-v4.6` 분석 기록이 없는 로컬 영상을 순차 분석하고, 영상마다 결과를 `video_vtt_analysis`에 저장합니다. |
| `pdm run python -m whistt INPUT_PATH OUTPUT_DIR` | 오디오 또는 영상 파일을 WhisperX로 전사·정렬하고 화자를 분리합니다. `--no-diarization`으로 화자 분리를 생략할 수 있습니다. |

## ytcrawln ID 목록 등록

한 줄에 YouTube ID 하나가 있는 UTF-8 파일로 영상과 상세 정보를 함께 등록합니다.
`config_ytcrawln.yaml`의 DB와 테이블을 먼저 준비해야 하며, API 키는 환경변수 또는
`.env`의 `YOUTUBE_API_KEY`를 사용합니다.

```bash
pdm run python -m ytcrawln.crawler.youtube.import_ids \
  --ids-file inputs/youtube_ids.txt
```

`--config`로 다른 설정 파일을 지정할 수 있습니다. 파일 내부 중복은 제거하지만,
입력 ID 중 하나라도 DB에 이미 있으면 API 호출이나 저장 전에 전체 실행을 거부합니다.
최대 50개씩 조회하고, 조회에 성공한 영상과 상세 정보를 같은 트랜잭션으로 저장합니다.
새 영상의 `path`는 `vid_{ID}.mp4` 형식의 상대경로로 저장합니다.
영상 다운로드나 해당 파일의 존재 여부 확인은 수행하지 않습니다.

API 미반환·잘못된 영상은 사유를 출력하고 제외합니다. API 요청 또는 DB 배치가
실패하면 중단하며, 이전에 완료한 배치는 보존합니다. 같은 DB에 대한 등록은 하나씩
실행하고, 일부 완료 후 재시도할 때는 완료된 ID를 제외한 파일을 사용하세요.
종료 코드는 성공 `0`, 일부 실패·미완료 `1`, 입력·설정·DB 준비·기존 ID 충돌 오류 `2`,
사용자 중단 `130`입니다. 마지막에 입력·중복·등록·미반환·실패·미처리 건수를 출력합니다.

## ytcrawln 검수 후보 등록

얼굴 분석을 마친 뒤 프로젝트 루트에서 다음 명령을 실행합니다.

```bash
pdm run python -m ytcrawln.db.importers.clip_candidates
```

기본 설정은 `config_ytcrawln.yaml`이며 `--config`로 다른 설정 파일을 지정할 수
있습니다. 기존 DB의 `face_in_videos` 전체에서 `(video_ref_id, frame)`별로 묶고,
`MIN(time_sec)`를 기준 시각으로 복사하여 `clip_candidates`에 저장합니다.
후보 테이블이 없으면 생성하며, 재실행 시 기존 후보의 ID와 시각은 유지하고
새 후보만 추가합니다. 완료 후 원본 고유 후보 수, 신규 등록 수, 기존 후보 수를 출력합니다.

얼굴 분석과 DB 초기화는 후보를 자동 등록하지 않습니다. Review의 `/clips`는
저장된 후보만 조회하므로, 최초 전환 시 위 명령을 실행한 뒤 Review 서버를
재시작하고 페이지를 새로고침합니다. 이후 분석 결과가 추가되면 같은 명령을
재실행하고 페이지를 새로고침합니다. 분석과 등록 명령은 순서대로 실행합니다.
등록 실패 시 이번 실행의 미커밋 삽입은 롤백되며 같은 명령으로 재시도할 수 있습니다.

등록된 후보는 얼굴 분석 결과를 변경하거나 삭제해도 유지됩니다. 후보가 남아 있는
동안 `videos.id`를 다른 원본 영상에 재사용하지 않아야 합니다. 영상 파일 추출과
Review 판정 저장은 이 명령의 기능에 포함되지 않습니다.

## 보조 CLI

영상 분석은 기본적으로 1초 간격으로 프레임을 선택하고 영상당 앞의 3개 배치를
처리합니다. 분석과 요약에는 모두 `minicpm-v4.6`을 사용합니다. 범위를 바꾸려면
다음과 같이 실행합니다.

```bash
pdm run python -m ytcrawl.analysis.video_to_text \
  --max-batches 5 \
  --interval 0.5
```

영상 한 편이 끝날 때마다 결과를 커밋합니다. 같은 명령을 다시 실행하면 이미
저장된 영상은 건너뛰고, 중단되었거나 실패한 영상부터 다시 처리합니다.

JSON 목록 다운로드는 다음과 같이 실행합니다.

```bash
pdm run python -m ytcrawl.crawl.youtube.custom \
  --json inputs/download_candidates_2500_20260911T065017Z.json
```

입력은 `video_id`가 있는 객체 배열입니다. 전체 입력을 검증한 뒤 중복 ID를
제거하고, 검색/상세 API 호출이나 검색 기록 생성 없이 JSON 메타데이터를 저장합니다.
DB와 영상 경로는 `DATA_ROOT`를 사용하며 API 키는 필요하지 않습니다.
50개마다 확인하지 않고 전체 목록을 순차 처리합니다. 기존 60~120초 대기와
라이브 제외, 403/429/봇 확인 오류 시 중단은 유지됩니다. 일반 다운로드 실패는
기록하고 다음 영상으로 진행합니다.

같은 명령을 재실행하면 완료 파일을 재사용하고 누락/빈 파일을 다운로드합니다.
기존 영상 행과 리뷰는 보존하며 새로운 영상은 검색 연결 없이 Review에 표시됩니다.
기존 DB의 검색 ID 필수 제약은 최초 실행 시 `Backup`에 DB를 백업한 뒤 변환합니다.
원격 Review에도 nullable 검색 ID를 지원하는 코드를 배포해야 합니다.
종료 요약은 고유 영상 기준이며 실패 또는 미처리가 있으면 종료 코드 1,
입력/설정 오류는 2입니다. 빈 목록은 저장 없이 종료합니다.

| 명령 | 설명 |
| --- | --- |
| `pdm run python -m ytcrawl.search --preset PRESET --output search.json` | YouTube `search.list` 원본 응답을 JSON으로 저장합니다. |
| `pdm run python -m ytcrawl.search.youtube_channel --handle @HANDLE` | 핸들 또는 `--name`으로 채널 ID 후보를 조회합니다. |
| `pdm run python -m ytcrawl.search.youtube_detail --video-ids VIDEO_ID --output details.json` | 하나 이상의 영상 상세 응답을 JSON으로 저장합니다. |
| `pdm run python -m ytcrawl.download --video-id VIDEO_ID` | 영상 하나를 설정된 미디어 디렉터리에 다운로드합니다. |
| `pdm run python -m ytcrawl.crawl.recovery --download-missing` | DB에 등록된 영상 중 로컬 파일이 없거나 비정상인 항목을 재다운로드합니다. `--audit` 단독 실행으로 상태만 점검할 수 있습니다. |
| `pdm run python -m ytcrawl.statistics.stats` | DB가 참조하는 완료 영상의 개수, 용량, 재생 시간을 집계합니다. |
| `pdm run python -m ytcrawl.statistics.acceptance --accept-ratio 0.5` | 검수자 승인 비율 기준을 만족한 영상의 통계를 집계합니다. |
| `pdm run python -m ytcrawl.cleanup --reject-ratio 0.5` | Reject 비율 기준을 만족한 로컬 영상 파일을 즉시 삭제합니다. 삭제 없이 대상을 확인하려면 `--dry-run`을 추가합니다. |
| `pdm run python -m ytcrawl.sync push all` | 설정된 SSH 피어와 `push` 또는 `pull` 방향으로 `db`, `media`, `all` 중 하나를 동기화합니다. DB 동기화는 목적지 DB를 교체하므로 주의해야 합니다. |

직접 실행한 `yt-dlp`의 쿠키 옵션은 프로젝트 CLI에 자동으로 전달되지 않습니다.
영상 다운로드가 포함된 `ytcrawl`, `ytcrawl.crawl.youtube.channel.main`,
`ytcrawl.download`, `ytcrawl.crawl.youtube.custom`, `ytcrawl.crawl.recovery --download-missing`에서 공통으로
`--cookies-from-browser`를 사용할 수 있습니다. 검색/상세 조회 전용 CLI의
YouTube Data API 요청에는 이 옵션이 적용되지 않습니다.

특정 Chrome 프로필을 쓰려면 해당 프로필 창에서 `chrome://version`을 열어
**프로필 경로(Profile Path)**를 확인합니다. 화면에 표시되는 사용자 이름이 아니라
경로의 마지막 폴더명(예: `Default`, `Profile 1`) 또는 프로필 전체 경로를 사용합니다.
프로필 경로 확인 방법은 [Chromium 공식 문서](https://chromium.googlesource.com/chromium/src/+/master/docs/user_data_dir.md#current-location)를 참고하세요.
진단이 성공한 컴퓨터와 OS 사용자 환경에서 실행하며, 아래 `Profile 1`은 예시입니다.

```bash
pdm run python -m ytcrawl.crawl.recovery --download-missing \
  --cookies-from-browser "chrome:Profile 1"
```

다른 다운로드 CLI에도 같은 옵션을 추가합니다. 예를 들어 검색 수집은 다음과 같습니다.

```bash
pdm run python -m ytcrawl --preset interview \
  --cookies-from-browser "chrome:Profile 1"
```

[yt-dlp 형식](https://github.com/yt-dlp/yt-dlp#filesystem-options)인
`BROWSER[+KEYRING][:PROFILE][::CONTAINER]`를 지원합니다.
`chrome:Default`는 Default 폴더를 명시하고, `chrome`만 지정하면 yt-dlp가
프로필을 자동 선택하므로 특정 프로필 사용이 보장되지 않습니다.
프로필 경로에 공백이 있으면 옵션 값 전체를 따옴표로 감싸세요.
옵션은 실행별로 지정하며, 프로젝트 설정에 저장하거나 쿠키 파일을 내보내지 않습니다.
생략하면 브라우저 쿠키를 읽지 않으며, 봇 확인 오류가 다시 발생하면 기존 안전 중단이 적용됩니다.
`--simulate` 성공은 정보 추출 확인이며 실제 미디어 다운로드 성공을 보장하지는 않습니다.

`ytcrawl.cleanup`은 DB와 저장된 `path`를 변경하지 않으므로 삭제된 파일은
`ytcrawl.crawl.recovery --download-missing`으로 다시 다운로드할 수 있습니다.
Reject 비율은 각 논리 영상에 대한 검수자별 최신 확정 판정만 사용해
`rejected / (accepted + rejected)`로 계산하며, Reject가 한 건 이상인 경우에만
대상이 됩니다. `pending`과 `needs_review`는 계산에서 제외됩니다.
미디어 동기화는 파일 삭제를 전파하지 않으므로 서버 용량도 줄이려면 코드를
배포한 뒤 서버에서 같은 cleanup 명령을 별도로 실행해야 합니다.

`ytcrawl.sync_remote`는 `ytcrawl.sync`가 SSH 피어에서 호출하는 내부 헬퍼이므로
일반적으로 직접 실행하지 않습니다. 각 명령의 상세 옵션은 `--help`로 확인할 수
있습니다. 단, `ytcrawl.review`는 별도 옵션 없이 `config.yaml`을 사용합니다.
