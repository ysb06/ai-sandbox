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