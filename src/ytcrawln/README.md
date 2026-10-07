# ytcrawln

ytcrawl의 새로운 버전으로, 영상 데이터셋 수집 플랫폼.

## Installation

{추후 설치 방법 정리}

설치 시 중요한 점은 --enable-libssh이 적용된 ffmpeg가 설치되어 있어야 함. 

```bash
brew install ffmpeg-full
```

```bash
PKG_CONFIG=/opt/homebrew/bin/pkg-config \
PKG_CONFIG_PATH=/opt/homebrew/opt/ffmpeg-full/lib/pkgconfig \
LDSHARED="clang -bundle -undefined dynamic_lookup" \
LDFLAGS="-L/opt/homebrew/opt/ffmpeg-full/lib" \
pdm run pip install \
    --no-binary=av \
    --no-cache-dir \
    --no-deps \
    --verbose \
    'av==18.1.0'
```

## Review에서 SFTP 영상 사용

Review는 SFTP 영상을 **로컬에 완전히 다운로드한 뒤** 기존 `FileResponse`로
제공합니다. 브라우저의 HTTP Range·탐색·6초 클립 재생 처리는 기존 방식을
그대로 사용합니다. PyAV 디코딩이나 재인코딩은 하지 않습니다.

아래 값은 예시입니다. 실제 `config_ytcrawln.yaml`의 경로와 SSH alias는
운영자가 지정합니다.

```yaml
DATA_ROOT: "/local/path/to/ytcrawln"
MEDIA_ROOT: "sftp://SSH_ALIAS/absolute/server/path/media"
```

- DB와 리뷰 결과는 기존처럼 로컬 `DATA_ROOT`에 저장합니다.
- 원격 영상의 완료 캐시는 `<DATA_ROOT>/media-temp`에 저장합니다. 별도의
  캐시 설정 키는 없으며 `AppConfig.cache_root`로 파생합니다.
- DB의 `videos.path` 상대 파일명을 `MEDIA_ROOT`와 조합합니다. 완전한 SFTP
  URL과 로컬 절대경로도 지원하며 DB 마이그레이션은 필요하지 않습니다.
- 원격 경로의 공백, `%`, `?`, `#`는 그대로 사용합니다. URL 인코딩된 파일명을
  자동으로 디코딩하지 않습니다.
- 로컬 영상은 복사하지 않고 원래 파일을 그대로 제공합니다.

### 최초 다운로드와 캐시 재사용

영상 상세 요청과 영상 GET·HEAD 요청은 모두 로컬 파일 확보 과정을 거칩니다.
캐시가 없으면 **파일 전체 다운로드가 끝날 때까지 요청이 대기**합니다.
같은 영상을 동시에 요청하면 진행 중인 다운로드를 기다리고, 완료된 캐시가
있으면 재사용합니다. macOS·Linux에서는 POSIX `flock`으로 여러 worker나
프로세스 사이에서도 같은 캐시에 대한 다운로드를 조정합니다. 잠금용 `.lock`
파일은 삭제하지 않으며, 캐시를 수동 관리할 때도 그대로 유지해야 합니다.
브라우저에는 기존
`/videos/media/{video_ref_id}` URL과 원본 영상 파일명을 제공합니다.

서버·디렉터리가 다른 동명 파일은 전체 원격 주소를 기준으로 구분합니다.
다운로드 중인 파일과 완료 캐시는 분리하며, 성공한 파일만 완료 캐시로
게시합니다. 일부만 받은 파일을 영상으로 제공하거나 다음 다운로드에 이어
사용하지 않습니다. 자동 재시도도 하지 않습니다.

브라우저에서 화면을 이동하거나 연결을 끊어도 이미 시작한 다운로드는 계속되어
완료 캐시를 남깁니다. 반면 서버의 실행 task 자체가 취소되면 해당 다운로드를
중단하고 그 실행의 부분 파일을 정리합니다. 프로세스 강제 종료 등으로 남은
`.part` 파일은 재개하거나 자동 정리하지 않으므로 운영자가 필요에 따라 확인합니다.
Review 프로세스가 종료되면 진행 중 다운로드의 완료를 보장하지 않습니다.
완료 캐시는 자동 삭제하지 않으므로 **영상 전체 크기만큼 로컬 디스크 공간**이
필요합니다. 원격의 같은 경로 파일은 바뀌지 않는다고 가정하며, 원격 파일을
교체했거나 공간을 확보해야 할 때는 운영자가 해당 완료 캐시를 수동 제거합니다.

### 의존성과 SSH 준비

Review의 원격 다운로드는 AsyncSSH를 사용합니다. 새 직접 의존성은
`asyncssh>=2.24,<3`이며, 설치가 필요하면 운영자가 갱신된 PDM lock으로
의존성을 동기화합니다. 이 기능은 FFmpeg/PyAV나 가상환경을 자동 재설치하지
않습니다. 위 Installation의 libssh/PyAV 설정은 **영상 분석의 직접 접근**에
필요하며 Review 캐시 다운로드 자체에는 필요하지 않습니다.

```bash
pdm sync
pdm run uvicorn ytcrawln.review.app:create_app --factory --host 127.0.0.1 --port 8000
```

SSH 설정은 브라우저 사용자가 아니라 **Review 백엔드 실행 계정**에 준비합니다.
기본 SSH config, 키 파일, ssh-agent, known_hosts를 사용하며 URL에 사용자나
포트가 명시되어 있으면 해당 값을 적용합니다.

- 비밀번호·keyboard-interactive 인증과 비밀번호가 들어간 URL은 지원하지
  않습니다. 암호화된 개인 키는 ssh-agent에 미리 등록합니다.
- 신뢰할 서버의 호스트 키를 known_hosts에 준비합니다. 코드에서 호스트 키
  검증을 비활성화하지 않습니다.
- 점프 서버와 ProxyCommand는 비대화형으로 동작해야 합니다. AsyncSSH가
  지원하는 SSH config는 OpenSSH의 전체 기능과 동일하지 않을 수 있습니다.
- 브라우저가 재생할 수 있는 코덱의 영상이어야 하며, 이 기능에서 변환하지
  않습니다.

`create_app(db_url=..., media_root=...)`로 두 값을 직접 주입하면 config를
읽지 않습니다. 이때 원격 `media_root`를 지정하려면 `cache_root=Path(...)`도
주입해야 합니다. `cache_root`에는 로컬 파일시스템 경로만 사용할 수 있습니다.
로컬 `media_root`에서는 캐시 없이 기존 사용이 가능하지만,
DB 안의 완전한 SFTP URL을 사용할 경우에도 캐시 경로가 필요합니다.

파일이 없거나 일반 파일이 아니면 상세 응답의 `media.available=false`가
되며 영상 요청은 `404`입니다. 원격 인증·호스트 키·접속·읽기 오류는 `502`,
원격 작업 timeout은 `504`, 로컬 캐시 설정·파일 작업 오류는 `500`으로
반환합니다. 기존 리뷰 화면의 오류 표시를 사용하며 내부 원격 경로나
인증정보는 오류 응답에 포함하지 않습니다.

실제 서버 접속과 브라우저 재생·탐색 확인은 운영자가 별도로 수행합니다.
정적 검사 결과는 원격 접속이나 실제 재생 성공을 의미하지 않습니다.

## YouTube ID 목록으로 DB 데이터 삭제

한 줄에 YouTube ID 하나를 적은 UTF-8 텍스트 파일을 사용합니다. 형식은
`inputs/youtube_ids.txt`와 같지만, 등록용 목록과 혼동하지 않도록 별도 삭제
목록을 권장합니다. BOM·빈 줄·줄 양끝 공백은 허용하고, 중복 ID는 하나로
묶습니다. 잘못된 ID나 빈 입력이 있으면 전체 실행을 거부합니다.

기본 실행은 **읽기 전용 미리보기**입니다.

```bash
python -m ytcrawln.db.utils delete-videos --ids-file inputs/delete_youtube_ids.txt
```

출력된 DB 경로와 테이블별 삭제 예정 건수를 확인한 뒤 실제 삭제할 때만
`--execute`를 지정합니다. 대화형 확인은 따로 하지 않습니다.

```bash
python -m ytcrawln.db.utils delete-videos --ids-file inputs/delete_youtube_ids.txt --execute
```

- `--config`로 다른 설정 파일을 지정할 수 있습니다. 기본값은 프로젝트의
  `config_ytcrawln.yaml`이며 DB는 `DATA_ROOT/ytcrawln.sqlite3`입니다.
- `videos.video_id`가 입력 ID와 일치하는 **모든 등록 행**을 대상으로 합니다.
  DB PK인 `videos.id`를 입력하는 기능이 아닙니다.
- 대상 클립의 `clip_reviews`를 먼저 삭제한 뒤 `clip_candidates`,
  `face_in_videos`, `videos_detail`, `video_reviews`, `videos`를 정리합니다.
  리뷰 작성자나 판정에 따른 예외는 없습니다.
- `videos`는 필수이고, 나머지 관련 테이블은 실제 존재할 때만 처리합니다.
  구버전의 `video_reviews`도 포함합니다. `clip_reviews`만 있고
  `clip_candidates`가 없거나, 필수 연결 컬럼·PK가 다르면 실행을 거부합니다.
- 지원하지 않는 외래 키 참조나 대상 테이블의 trigger가 발견되면 임의로
  연쇄 삭제하지 않고 중단합니다. 테이블 추가 시 명시적 삭제 규칙도 갱신해야
  합니다. 외래 키로 선언되지 않은 외부 테이블의 관계는 자동 추적하지 않습니다.
- DB에 없는 ID는 보고하고 건너뜁니다. 모든 ID가 이미 삭제되었다면 변경 및
  백업 없이 성공 종료합니다. DB 생성·스키마 변경·VACUUM은 하지 않습니다.
- **실제 영상 파일, SFTP 원격 파일, `media-temp` 캐시는 삭제하지 않습니다.**
  다른 호스트의 DB와 자동 동기화하지 않으며 API 키도 필요하지 않습니다.

### 백업과 트랜잭션

실제 삭제 대상이 있을 때 SQLite backup API로 다음 파일을 만들고
`quick_check`를 통과한 경우에만 삭제합니다. 백업은 WAL에 의존하지 않는
독립된 SQLite 파일이며 자동으로 삭제하지 않습니다.

```text
DATA_ROOT/Backup/ytcrawln-before-delete-<UTC timestamp>-<UUID>.sqlite3
```

대상 확인부터 commit까지 `BEGIN IMMEDIATE`로 다른 DB writer를 배제하며,
백업은 별도 읽기 전용 연결에서 생성합니다. 모든 입력 ID의 관련 행 삭제는
**하나의 트랜잭션**입니다. SQL은 작은 묶음으로 실행하지만 중간 commit은
하지 않습니다. 실패·중단 시 미완료 트랜잭션은 rollback하고, 성공한 백업은
보존합니다. 실행된 삭제 건수는 commit 이후에만 출력합니다.

실행 전 crawler·Review·importer 중지는 운영자가 관리합니다. 프로세스를
자동 검사하거나 중지하지 않습니다. 백업은 삭제 전 DB 전체이므로 복원하면
백업 이후의 다른 변경도 되돌아갑니다. 자동 복원은 제공하지 않습니다.

종료 코드는 성공·미리보기·대상 없음 `0`, 입력/config/DB 준비 오류 `2`,
백업·삭제 오류 `1`, 사용자 중단 `130`입니다. `--help`는 config 없이 표시됩니다.

프로그램 호출은 `ytcrawln.db.utils.preview_video_deletion(db_path, video_ids)`와
`delete_videos(db_path, video_ids, backup_root=...)`를 사용합니다. 후자는 실제
삭제 함수이며 항상 먼저 백업합니다. `backup_root`를 생략하면 DB 파일과
같은 디렉터리 아래의 `Backup`을 사용합니다. 두 함수 모두
`VideoDeletionResult`로 ID별 집계 정보와 테이블별 행 수를 반환합니다.
