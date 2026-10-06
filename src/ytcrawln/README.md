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
