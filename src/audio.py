"""오디오 전처리: 임의 녹음 포맷을 whisper.cpp 입력용 16kHz mono WAV 로 변환."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from src.config import AudioConfig
from src.exceptions import DependencyError, PipelineError

logger = logging.getLogger(__name__)

FFMPEG_BIN = "ffmpeg"


def convert_to_wav(input_path: Path, output_path: Path, config: AudioConfig) -> Path:
    """입력 오디오를 16kHz mono PCM WAV 로 변환한다.

    Args:
        input_path: 원본 녹음 파일(.m4a/.wav 등).
        output_path: 생성할 WAV 경로.
        config: 샘플레이트·타임아웃 설정.

    Returns:
        생성된 WAV 파일 경로(``output_path``).

    Raises:
        FileNotFoundError: 입력 파일이 없을 때.
        DependencyError: ffmpeg 가 PATH 에 없을 때.
        PipelineError: ffmpeg 변환이 실패하거나 타임아웃됐을 때.
    """
    if not input_path.is_file():
        raise FileNotFoundError(f"입력 파일을 찾을 수 없습니다: {input_path}")

    if shutil.which(FFMPEG_BIN) is None:
        raise DependencyError(
            "ffmpeg 가 설치되어 있지 않습니다. './setup.sh' 또는 'brew install ffmpeg' 를 실행하세요."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG_BIN,
        "-y",
        "-i",
        str(input_path),
        "-ar",
        str(config.sample_rate),
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    logger.info("ffmpeg 변환 시작: %s -> %s (%dHz mono)", input_path, output_path, config.sample_rate)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=config.timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PipelineError(f"ffmpeg 변환이 {config.timeout_sec}초 안에 끝나지 않았습니다.") from exc

    if result.returncode != 0:
        raise PipelineError(f"ffmpeg 변환 실패 (exit {result.returncode}):\n{result.stderr.strip()}")

    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise PipelineError(f"ffmpeg 가 출력 파일을 만들지 못했습니다: {output_path}")

    logger.info("ffmpeg 변환 완료: %s", output_path)
    return output_path
