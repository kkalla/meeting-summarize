"""STT: whisper-cli subprocess 실행 + JSON 파싱 → ``list[Segment]``.

순수 로직(파싱·완전성 검사·신뢰도 게이트)은 subprocess 와 분리해 단위 테스트
가능하게 둔다. :class:`Segment` 가 STT→요약 사이의 단일 진실 소스다.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

from src.config import ConfidenceGate, SttConfig
from src.exceptions import DependencyError, TranscriptionError

logger = logging.getLogger(__name__)

MS_PER_SEC = 1000.0


@dataclass(frozen=True)
class Segment:
    """전사된 한 세그먼트. start/end 단위는 초.

    Attributes:
        start: 시작 시각(초).
        end: 종료 시각(초).
        text: 전사 텍스트(공백 정리됨).
        no_speech_prob: 무음 확률(있으면). whisper.cpp 빌드에 따라 None 일 수 있음.
        avg_logprob: 평균 로그확률(있으면/추정 가능하면).
    """

    start: float
    end: float
    text: str
    no_speech_prob: float | None = None
    avg_logprob: float | None = None


def parse_segments(data: dict) -> list[Segment]:
    """whisper-cli JSON dict 를 :class:`Segment` 리스트로 변환한다.

    Args:
        data: ``-oj -ojf`` 로 생성된 full JSON(세그먼트별 ``tokens[].p`` 포함)을 파싱한 dict.

    Returns:
        세그먼트 리스트(빈 전사면 빈 리스트).

    Raises:
        TranscriptionError: 예상 키 구조가 깨졌을 때.
    """
    transcription = data.get("transcription")
    if transcription is None:
        raise TranscriptionError("전사 JSON 에 'transcription' 키가 없습니다. 출력이 손상됐을 수 있습니다.")

    segments: list[Segment] = []
    for item in transcription:
        try:
            offsets = item["offsets"]
            start = float(offsets["from"]) / MS_PER_SEC
            end = float(offsets["to"]) / MS_PER_SEC
            text = str(item.get("text", "")).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise TranscriptionError(f"세그먼트 파싱 실패: {item!r}") from exc

        segments.append(
            Segment(
                start=start,
                end=end,
                text=text,
                no_speech_prob=_opt_float(item.get("no_speech_prob")),
                avg_logprob=_segment_avg_logprob(item),
            )
        )
    return segments


def check_completeness(segments: list[Segment], audio_duration_sec: float, tolerance_sec: float) -> None:
    """오디오 길이와 마지막 세그먼트 end 를 비교해 잘린 전사를 탐지한다.

    Raises:
        TranscriptionError: 마지막 세그먼트가 오디오 끝보다 ``tolerance_sec`` 이상
            앞서 끝나 전사가 중도에 잘린 것으로 판단될 때.
    """
    if not segments:
        return
    last_end = segments[-1].end
    gap = audio_duration_sec - last_end
    if gap > tolerance_sec:
        raise TranscriptionError(
            f"전사가 중간에 잘린 것으로 보입니다: 오디오 {audio_duration_sec:.1f}s 중 "
            f"{last_end:.1f}s 에서 종료(차이 {gap:.1f}s > 허용 {tolerance_sec:.1f}s)."
        )


def apply_confidence_gate(segments: list[Segment], gate: ConfidenceGate) -> None:
    """무음/환각 전사를 신뢰도 게이트로 판정한다.

    빈 전사이거나, 유효 음성 세그먼트 비율이 임계 미만이면 중단한다.

    Raises:
        TranscriptionError: 유효 음성이 없다고 판정될 때.
    """
    if not segments:
        raise TranscriptionError("전사 결과가 없습니다(세그먼트 0개). 입력 오디오를 확인하세요.")

    # 신뢰도 데이터(no_speech_prob/avg_logprob)가 둘 다 없으면 게이트가 그 세그먼트를
    # 낙관적으로 통과시킨다. 이런 세그먼트가 많으면 게이트가 사실상 무력화된 것이므로
    # 침묵하지 않고 경고한다(whisper.cpp 빌드가 확률을 출력하지 않는 경우 등).
    ungated = sum(1 for seg in segments if seg.text and not _has_confidence_data(seg))
    if ungated:
        ungated_ratio = ungated / len(segments)
        logger.warning(
            "신뢰도 데이터 없는 세그먼트 %d/%d (%.0f%%) — 해당 세그먼트는 게이트를 우회합니다.",
            ungated,
            len(segments),
            ungated_ratio * 100,
        )

    valid = sum(1 for seg in segments if _is_valid_speech(seg, gate))
    ratio = valid / len(segments)
    if ratio < gate.min_valid_ratio:
        raise TranscriptionError(
            f"유효 음성이 없습니다: 유효 세그먼트 비율 {ratio:.0%} < 임계 {gate.min_valid_ratio:.0%} "
            "(무음 또는 환각 전사로 추정)."
        )
    logger.info("신뢰도 게이트 통과: 유효 세그먼트 %d/%d (%.0f%%)", valid, len(segments), ratio * 100)


def transcribe(wav_path: Path, config: SttConfig) -> list[Segment]:
    """WAV 파일을 whisper-cli 로 전사해 검증된 세그먼트 리스트를 반환한다.

    Args:
        wav_path: 16kHz mono WAV 경로.
        config: STT 설정.

    Returns:
        검증을 통과한 세그먼트 리스트.

    Raises:
        DependencyError: whisper-cli 또는 모델 파일이 없을 때.
        TranscriptionError: 실행/파싱/완전성/신뢰도 검사에 실패할 때.
    """
    _check_dependencies(config)

    with tempfile.TemporaryDirectory() as tmp:
        prefix = Path(tmp) / "out"
        data = _run_whisper(wav_path, prefix, config)

    segments = parse_segments(data)
    duration = _wav_duration_sec(wav_path)
    check_completeness(segments, duration, config.completeness_tolerance_sec)
    apply_confidence_gate(segments, config.confidence_gate)
    return segments


def _check_dependencies(config: SttConfig) -> None:
    """whisper-cli 실행 파일과 모델 파일 존재를 선검사한다."""
    if shutil.which(config.whisper_cli) is None and not Path(config.whisper_cli).is_file():
        raise DependencyError(
            f"whisper-cli 를 찾을 수 없습니다: {config.whisper_cli}. './setup.sh' 를 실행하거나 "
            "configs/pipeline.yaml 의 stt.whisper_cli 경로를 확인하세요."
        )
    if not Path(config.model_path).is_file():
        raise DependencyError(f"whisper 모델이 없습니다: {config.model_path}. './setup.sh' 로 모델을 받으세요.")


def _run_whisper(wav_path: Path, prefix: Path, config: SttConfig) -> dict:
    """whisper-cli 를 실행하고 생성된 JSON 을 파싱해 반환한다."""
    cmd = [
        config.whisper_cli,
        "-m",
        config.model_path,
        "-f",
        str(wav_path),
        "-l",
        config.language,
        "-oj",
        "-ojf",  # full JSON: 세그먼트별 tokens[].p 포함 → avg_logprob 기반 신뢰도 게이트 동작
        "-of",
        str(prefix),
    ]
    logger.info("whisper-cli 전사 시작: %s", wav_path)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=config.timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TranscriptionError(f"전사가 {config.timeout_sec}초 안에 끝나지 않았습니다.") from exc
    except OSError as exc:
        # 존재는 하지만 실행 불가(권한 없음/아키텍처 불일치/ENOEXEC 등) — 원시 OSError 가
        # PipelineError 계층 밖으로 새지 않도록 래핑한다.
        raise TranscriptionError(f"whisper-cli 실행에 실패했습니다(권한/아키텍처 확인): {exc}") from exc

    if result.returncode != 0:
        raise TranscriptionError(f"whisper-cli 실패 (exit {result.returncode}):\n{result.stderr.strip()}")

    json_path = prefix.with_suffix(".json")
    if not json_path.is_file():
        raise TranscriptionError(f"whisper-cli 가 JSON 출력을 만들지 못했습니다: {json_path}")

    try:
        with json_path.open("r", encoding="utf-8") as fp:
            return json.load(fp)
    except json.JSONDecodeError as exc:
        raise TranscriptionError(f"전사 JSON 파싱 실패(잘린 출력 가능): {exc}") from exc


def _wav_duration_sec(wav_path: Path) -> float:
    """WAV 파일의 재생 길이(초)를 계산한다."""
    try:
        with contextlib.closing(wave.open(str(wav_path), "rb")) as wav:
            frames = wav.getnframes()
            rate = wav.getframerate()
    except (wave.Error, EOFError) as exc:
        raise TranscriptionError(f"WAV 길이를 읽을 수 없습니다: {wav_path} ({exc})") from exc
    if rate == 0:
        raise TranscriptionError(f"WAV 샘플레이트가 0 입니다: {wav_path}")
    return frames / float(rate)


def _has_confidence_data(seg: Segment) -> bool:
    """세그먼트에 게이트가 판정에 쓸 신뢰도 데이터가 하나라도 있는지 검사한다."""
    return seg.no_speech_prob is not None or seg.avg_logprob is not None


def _is_valid_speech(seg: Segment, gate: ConfidenceGate) -> bool:
    """세그먼트가 유효 음성으로 판정되는지 검사한다.

    신뢰도 데이터가 둘 다 없으면 판정 근거가 없으므로 낙관적으로 통과시킨다
    (게이트 우회). 이 비율은 :func:`apply_confidence_gate` 가 별도로 경고한다.
    """
    if not seg.text:
        return False
    if seg.no_speech_prob is not None and seg.no_speech_prob > gate.max_no_speech_prob:
        return False
    if seg.avg_logprob is not None and seg.avg_logprob < gate.min_avg_logprob:
        return False
    return True


def _segment_avg_logprob(item: dict) -> float | None:
    """세그먼트 평균 logprob 을 구한다.

    JSON 에 직접 있으면 사용하고, 없으면 토큰 확률(``tokens[].p``)로 근사한다.
    둘 다 없으면 None.
    """
    direct = _opt_float(item.get("avg_logprob"))
    if direct is not None:
        return direct

    tokens = item.get("tokens")
    if not tokens:
        return None
    probs = [_opt_float(tok.get("p")) for tok in tokens if isinstance(tok, dict)]
    valid = [p for p in probs if p is not None and p > 0.0]
    if not valid:
        return None
    return sum(math.log(p) for p in valid) / len(valid)


def _opt_float(value: object) -> float | None:
    """값을 float 로 변환하되 None/변환불가면 None 을 반환한다."""
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
