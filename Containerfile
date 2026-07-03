# 회의 요약 watcher 이미지 — whisper.cpp(CPU) STT + OpenRouter 요약을 한 컨테이너에 담는다.
# 호스트 setup.sh 는 macOS Metal 빌드(Mach-O)라 linux 컨테이너에서 실행 불가 →
# 여기서 동일 경로에 CPU 빌드를 새로 만든다. 모델(.bin)은 compose 볼륨으로 주입한다.
#
# 주의: 로컬 whisper.cpp 기반 legacy 파이프라인 전용 이미지다. 현재 주력 사용법은
# Slack 봇(scripts/run_slack_bot.py, Socket Mode)이며 이 이미지로 담을 필요가 없다
# (자세한 내용은 legacy/README.md 참고).
FROM python:3.12-slim

# 1) 시스템 의존성: ffmpeg(오디오 변환) + whisper.cpp 빌드 도구.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg git cmake build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 2) whisper.cpp 클론 + CPU 빌드(whisper-cli). 빌드 경로를 호스트 config 의
#    stt.whisper_cli (vendor/whisper.cpp/build/bin/whisper-cli) 와 동일하게 맞춰
#    configs/pipeline.yaml 수정 없이 동작하게 한다.
#    재현 가능한 빌드를 위해 안정 태그에 고정한다(upstream HEAD 자동 유입 방지).
ARG WHISPER_CPP_TAG=v1.7.4
RUN git clone --depth 1 --branch "${WHISPER_CPP_TAG}" https://github.com/ggml-org/whisper.cpp.git vendor/whisper.cpp \
    && cmake -B vendor/whisper.cpp/build -S vendor/whisper.cpp \
        -DGGML_METAL=OFF -DWHISPER_BUILD_TESTS=OFF \
    && cmake --build vendor/whisper.cpp/build --config Release -j --target whisper-cli

# 3) Python 런타임 의존성 (pyproject.toml 의 [project.dependencies] 와 일치).
RUN pip install --no-cache-dir \
        "openai>=1.30.0" "python-dotenv>=1.0.0" "pyyaml>=6.0"

# 4) 애플리케이션 코드. 모델(.bin)과 data/ 는 이미지에 굽지 않고 볼륨으로 주입한다.
#    legacy/ 는 run_watcher.py 가 여기 있으므로 함께 담는다(src/ 의 core 모듈 재사용).
COPY src/ ./src/
COPY legacy/ ./legacy/
COPY configs/ ./configs/
COPY prompts/ ./prompts/

# from src.../from legacy... 임포트가 동작하도록 프로젝트 루트를 모듈 경로에 둔다.
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

CMD ["python", "legacy/run_watcher.py"]
