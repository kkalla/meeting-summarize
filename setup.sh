#!/usr/bin/env bash
# 외부 의존성 설치 자동화 (멱등). macOS / Apple Silicon 기준.
#   - ffmpeg (brew)
#   - whisper.cpp 클론 + Metal 가속 빌드 -> whisper-cli
#   - ggml-large-v3 모델 다운로드 (한국어 정확도 우선; turbo 대비 느리지만 인식 품질↑)
# 여러 번 실행해도 안전하도록, 이미 있는 단계는 건너뜁니다.
#
# 사용법: ./setup.sh [--rebuild]
#   --rebuild  whisper-cli 강제 재빌드 (프로젝트 폴더 이동/이름 변경 후 rpath 가 깨졌을 때)
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENDOR_DIR="${ROOT_DIR}/vendor"
WHISPER_DIR="${VENDOR_DIR}/whisper.cpp"
MODEL_NAME="large-v3"
MODEL_FILE="${WHISPER_DIR}/models/ggml-${MODEL_NAME}.bin"
WHISPER_CLI="${WHISPER_DIR}/build/bin/whisper-cli"

log() { printf '\033[1;34m[setup]\033[0m %s\n' "$1"; }
err() { printf '\033[1;31m[setup:error]\033[0m %s\n' "$1" >&2; }

REBUILD=false
for arg in "$@"; do
  case "$arg" in
    --rebuild) REBUILD=true ;;
    *)
      err "알 수 없는 옵션: ${arg} (사용법: ./setup.sh [--rebuild])"
      exit 1
      ;;
  esac
done

# Homebrew 로 패키지 설치(멱등). brew 미설치면 중단.
brew_install() {
  local pkg="$1"
  if command -v "$pkg" >/dev/null 2>&1; then
    log "${pkg} 이미 설치됨 — 건너뜀"
    return
  fi
  if ! command -v brew >/dev/null 2>&1; then
    err "Homebrew가 필요합니다. https://brew.sh 참고 후 'brew install ${pkg}' 실행하세요."
    exit 1
  fi
  log "${pkg} 설치 중..."
  brew install "$pkg"
}

# 1) 빌드/변환 도구 (ffmpeg, cmake) ---------------------------------------
brew_install ffmpeg
brew_install cmake

# 2) whisper.cpp 클론 + 빌드 ---------------------------------------------
mkdir -p "${VENDOR_DIR}"
if [ ! -d "${WHISPER_DIR}/.git" ]; then
  log "whisper.cpp 클론 중..."
  git clone --depth 1 https://github.com/ggml-org/whisper.cpp.git "${WHISPER_DIR}"
else
  log "whisper.cpp 이미 클론됨 — 건너뜀"
fi

if [ "${REBUILD}" = true ] && [ -d "${WHISPER_DIR}/build" ]; then
  log "--rebuild: 기존 빌드 삭제 중..."
  rm -rf "${WHISPER_DIR}/build"
fi

if [ -x "${WHISPER_CLI}" ]; then
  log "whisper-cli 이미 빌드됨 — 건너뜀 (강제 재빌드는 --rebuild)"
else
  log "whisper.cpp 빌드 중 (Metal 가속)..."
  cmake -B "${WHISPER_DIR}/build" -S "${WHISPER_DIR}" -DGGML_METAL=ON >/dev/null
  cmake --build "${WHISPER_DIR}/build" --config Release -j --target whisper-cli
fi

# 3) 모델 다운로드 --------------------------------------------------------
if [ -f "${MODEL_FILE}" ]; then
  log "모델 ${MODEL_NAME} 이미 존재 — 건너뜀"
else
  log "모델 ${MODEL_NAME} 다운로드 중..."
  bash "${WHISPER_DIR}/models/download-ggml-model.sh" "${MODEL_NAME}"
fi

log "완료!"
log "whisper-cli : ${WHISPER_CLI}"
log "model       : ${MODEL_FILE}"
echo
log "configs/pipeline.yaml 의 stt.whisper_cli 를 위 경로로 맞추거나,"
log "  ln -sf '${WHISPER_CLI}' /opt/homebrew/bin/whisper-cli  로 PATH에 등록하세요."
