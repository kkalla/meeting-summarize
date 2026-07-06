"""ffmpeg 로 긴 오디오를 겹치는 시간창(chunk)으로 분할한다.

OpenRouter STT 는 업스트림 프로바이더 타임아웃이 약 60초로 알려져 있어, 회의 녹음
전체를 한 번에 던지면 긴 파일(1시간 안팎)에서 타임아웃 위험이 있다. 이 모듈은 오디오를
``segment_sec`` 단위로 잘라(``overlap_sec`` 만큼 겹쳐 경계에서 말이 잘리는 문제를
완화), ``processor.py`` 가 구간별로 전사한 뒤 기존 Map-Reduce 요약(``src.summarize``)에
그대로 먹일 수 있게 한다 — legacy 의 시간창+오버랩 청킹(``legacy/chunking.py``)과 같은
아이디어를 텍스트가 아니라 오디오 자체에 적용한 것이다.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from src.exceptions import DependencyError

logger = logging.getLogger(__name__)

FFMPEG_BIN = "ffmpeg"
FFPROBE_BIN = "ffprobe"


@dataclass(frozen=True)
class AudioSegment:
    """분할된 오디오 조각.

    Attributes:
        data: 오디오 바이트(컨테이너는 호출 시 지정한 ``output_extension`` 기준).
        start_sec: 원본 기준 시작 시각(초) — 리포트 메타데이터의 청크 구간 계산에 쓰인다.
    """

    data: bytes
    start_sec: float


def probe_duration_sec(audio_bytes: bytes, extension: str, *, timeout_sec: int = 30) -> float:
    """ffprobe 로 오디오 전체 길이(초)를 읽는다.

    Args:
        audio_bytes: 원본 오디오 바이트.
        extension: 임시 파일에 쓸 확장자(점 포함, 예: ``.qta``) — 컨테이너 판별에 씀.
        timeout_sec: ffprobe 실행 타임아웃(초).

    Raises:
        DependencyError: ffprobe 미설치, 실행 실패, 또는 길이를 못 읽었을 때.
    """
    if shutil.which(FFPROBE_BIN) is None:
        raise DependencyError(
            f"{FFPROBE_BIN} 가 설치되어 있지 않습니다. './setup.sh' 또는 'brew install ffmpeg' 를 실행하세요."
        )

    with tempfile.NamedTemporaryFile(suffix=extension) as tmp:
        tmp.write(audio_bytes)
        tmp.flush()
        try:
            result = subprocess.run(
                [FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", tmp.name],
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                check=False,
            )
        except OSError as exc:
            raise DependencyError(f"ffprobe 실행에 실패했습니다: {exc}") from exc

    if result.returncode != 0:
        raise DependencyError(
            f"ffprobe 로 오디오 길이를 읽지 못했습니다(exit {result.returncode}): {result.stderr.strip()}"
        )
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise DependencyError(f"ffprobe 출력에서 길이를 파싱하지 못했습니다: {result.stdout!r}") from exc


def split_audio(
    audio_bytes: bytes,
    *,
    input_extension: str,
    output_extension: str,
    duration_sec: float,
    segment_sec: int,
    overlap_sec: int,
    timeout_sec: int = 120,
) -> list[AudioSegment]:
    """오디오를 겹치는 시간창으로 분할한다.

    ``duration_sec`` 이 ``segment_sec`` 이하면 분할하지 않고 원본을 그대로 담아 반환한다
    (짧은 회의는 지금까지처럼 단일 구간 그대로 처리).

    Args:
        audio_bytes: 원본 오디오 바이트.
        input_extension: 원본 확장자(점 포함) — ffmpeg 입력 컨테이너 판별용.
        output_extension: 구간 파일에 쓸 확장자(점 포함, 예: ``.m4a``). OpenRouter STT 가
            인식하는 컨테이너와 맞춰야 한다(``.qta`` 처럼 ffmpeg 가 모르는 확장자는 여기 못 씀
            — 호출부에서 표준 확장자로 매핑해서 넘겨야 한다).
        duration_sec: 원본 오디오 전체 길이(:func:`probe_duration_sec` 결과 재사용, 중복 프로빙 방지).
        segment_sec: 구간 길이(초, 겹침 제외).
        overlap_sec: 구간 간 겹침(초). 경계에서 말이 잘리는 문제 완화.
        timeout_sec: ffmpeg 구간별 실행 타임아웃(초).

    Raises:
        DependencyError: ffmpeg 미설치 또는 구간 분할 실패.
    """
    if duration_sec <= segment_sec:
        return [AudioSegment(data=audio_bytes, start_sec=0.0)]

    if shutil.which(FFMPEG_BIN) is None:
        raise DependencyError(
            f"{FFMPEG_BIN} 가 설치되어 있지 않습니다. './setup.sh' 또는 'brew install ffmpeg' 를 실행하세요."
        )

    segments: list[AudioSegment] = []
    with tempfile.TemporaryDirectory() as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        input_path = tmpdir / f"input{input_extension}"
        input_path.write_bytes(audio_bytes)

        start = 0.0
        index = 0
        while start < duration_sec:
            # ponytail: 고정 길이 시간창으로 자르므로 문장 중간에서 잘릴 수 있음(오버랩으로 완화).
            # 문장/발화 경계 인식이 필요해지면 VAD 기반 분할로 업그레이드.
            window = min(segment_sec + overlap_sec, duration_sec - start)
            output_path = tmpdir / f"segment_{index}{output_extension}"
            _run_ffmpeg_segment(input_path, output_path, start=start, window=window, timeout_sec=timeout_sec)
            segments.append(AudioSegment(data=output_path.read_bytes(), start_sec=start))
            start += segment_sec
            index += 1

    logger.info("오디오 분할 완료: %d개 구간(%d초 단위, %d초 오버랩)", len(segments), segment_sec, overlap_sec)
    return segments


def _run_ffmpeg_segment(input_path: Path, output_path: Path, *, start: float, window: float, timeout_sec: int) -> None:
    """ffmpeg 로 ``[start, start+window)`` 구간을 잘라 ``output_path`` 에 쓴다.

    ``-c copy`` (스트림 그대로 복사) 대신 재인코딩한다: 아이패드 Voice Memos 의 ``qt``
    컨테이너는 특이한 edit list 를 갖고 있어 스트림 복사로 그 잔재가 그대로 남으면
    STT 백엔드가 못 씹을 위험이 있고(46분 파일에서 관찰된 502 원인 후보), 음성 인식엔
    원본 스테레오/48kHz 품질도 필요 없다 — 16kHz mono 64kbps 로 낮춰 페이로드도 크게 줄인다
    (legacy/audio.py 가 whisper.cpp 용으로 16kHz mono 를 쓰는 것과 같은 이유).
    """
    cmd = [
        FFMPEG_BIN,
        "-y",
        "-ss",
        str(start),
        "-i",
        str(input_path),
        "-t",
        str(window),
        "-map",
        "0:a:0",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "aac",
        "-b:a",
        "64k",
        str(output_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec, check=False)
    except OSError as exc:
        raise DependencyError(f"ffmpeg 실행에 실패했습니다(구간 시작 {start}초): {exc}") from exc

    if result.returncode != 0 or not output_path.is_file() or output_path.stat().st_size == 0:
        raise DependencyError(
            f"ffmpeg 오디오 분할 실패(구간 시작 {start}초, exit {result.returncode}): {result.stderr.strip()}"
        )
