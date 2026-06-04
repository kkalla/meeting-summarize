"""설정 로딩: ``configs/pipeline.yaml`` 파싱 + ``.env`` 환경변수 로드.

YAML 의 각 섹션을 frozen dataclass 로 정규화해 코드 전반에서 타입 안전하게
참조한다. 매직넘버는 전부 YAML 에서 온다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

from src.exceptions import DependencyError

ENV_API_KEY = "OPENROUTER_API_KEY"
DEFAULT_CONFIG_PATH = "configs/pipeline.yaml"

# 프로젝트 루트(src/ 의 부모). 상대경로 리소스를 CWD 가 아닌 루트 기준으로 푼다.
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _resolve_resource(value: str) -> str:
    """프로젝트 리소스 경로를 해석한다.

    절대경로/PATH 명령(구분자 없음)은 그대로 두고, 상대경로는 프로젝트 루트
    기준으로 절대화한다(예: ``vendor/...`` → ``<root>/vendor/...``).
    """
    if not value or os.path.isabs(value) or (os.sep not in value and "/" not in value):
        return value
    return str(PROJECT_ROOT / value)


def _resolve_dir(value: str) -> Path:
    """watcher 디렉토리 경로를 해석한다.

    절대경로(예: 컨테이너의 ``/data/inbox``)는 그대로 두고, 상대경로는 프로젝트
    루트 기준으로 절대화한다. :func:`_resolve_resource` 는 단일 토큰(명령/파일)
    전용이라 디렉토리에는 이 함수를 쓴다.
    """
    path = Path(value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


@dataclass(frozen=True)
class AudioConfig:
    """오디오 전처리(ffmpeg) 설정."""

    sample_rate: int
    timeout_sec: int


@dataclass(frozen=True)
class ConfidenceGate:
    """무음/환각 판정 임계값."""

    max_no_speech_prob: float
    min_avg_logprob: float
    min_valid_ratio: float

    def __post_init__(self) -> None:
        # 도메인 범위를 벗어난 임계값은 게이트를 조용히 무력화하므로 생성 시점에 막는다.
        if not 0.0 <= self.max_no_speech_prob <= 1.0:
            raise DependencyError(f"max_no_speech_prob 은 [0, 1] 이어야 합니다: {self.max_no_speech_prob}")
        if self.min_avg_logprob > 0.0:
            raise DependencyError(f"min_avg_logprob 은 로그확률이므로 <= 0 이어야 합니다: {self.min_avg_logprob}")
        if not 0.0 < self.min_valid_ratio <= 1.0:
            raise DependencyError(f"min_valid_ratio 는 (0, 1] 이어야 합니다: {self.min_valid_ratio}")


@dataclass(frozen=True)
class SttConfig:
    """whisper.cpp 전사 설정."""

    whisper_cli: str
    model_path: str
    language: str
    timeout_sec: int
    confidence_gate: ConfidenceGate
    completeness_tolerance_sec: float


@dataclass(frozen=True)
class ChunkingConfig:
    """세그먼트 청킹 설정."""

    minutes: int
    overlap_sec: int
    single_shot_max_chars: int

    def __post_init__(self) -> None:
        # minutes<=0 이면 chunk_segments 의 윈도우가 전진하지 않아 무한루프에 빠진다.
        if self.minutes <= 0:
            raise DependencyError(f"chunking.minutes 는 양수여야 합니다: {self.minutes}")
        if self.overlap_sec < 0:
            raise DependencyError(f"chunking.overlap_sec 은 0 이상이어야 합니다: {self.overlap_sec}")
        if self.overlap_sec >= self.minutes * 60:
            raise DependencyError(
                f"chunking.overlap_sec({self.overlap_sec}s) 가 청크 길이({self.minutes * 60}s) 이상이면 "
                "모든 청크가 동일 구간을 중복 요약하게 됩니다."
            )
        if self.single_shot_max_chars < 0:
            raise DependencyError(
                f"chunking.single_shot_max_chars 는 0 이상이어야 합니다: {self.single_shot_max_chars}"
            )


@dataclass(frozen=True)
class SummarizeConfig:
    """OpenRouter Map-Reduce 요약 설정."""

    models: tuple[str, ...]
    base_url: str
    max_retries: int
    backoff_base: float
    request_timeout_sec: int
    max_tokens: int
    temperature: float
    max_chunk_failure_pct: float

    def __post_init__(self) -> None:
        # 잘못된 값은 요약 실행 시점(폴백 루프 한가운데)에서야 터지므로 로딩 때 막는다.
        if not self.models:
            raise DependencyError("summarize.models 는 비어 있을 수 없습니다.")
        if self.max_retries < 1:
            raise DependencyError(f"summarize.max_retries 는 1 이상이어야 합니다: {self.max_retries}")
        if not 0.0 <= self.temperature <= 2.0:
            raise DependencyError(f"summarize.temperature 는 [0, 2] 이어야 합니다: {self.temperature}")
        if not 0.0 <= self.max_chunk_failure_pct <= 100.0:
            raise DependencyError(
                f"summarize.max_chunk_failure_pct 는 [0, 100] 이어야 합니다: {self.max_chunk_failure_pct}"
            )


@dataclass(frozen=True)
class WatcherConfig:
    """폴더 watcher 데몬 설정."""

    inbox_dir: Path
    processed_dir: Path
    failed_dir: Path
    output_dir: Path
    poll_interval_sec: int
    stability_checks: int
    extensions: tuple[str, ...]

    def __post_init__(self) -> None:
        # 다른 config dataclass 와 동일하게 불변식을 타입이 소유한다. 직접 생성(테스트·
        # 미래 호출자)에서도 무효 상태(busy-spin/무동작)가 통과하지 않도록 한다.
        if self.poll_interval_sec < 1:
            raise DependencyError("watcher.poll_interval_sec 는 1 이상이어야 합니다(busy-spin 방지).")
        if self.stability_checks < 1:
            raise DependencyError("watcher.stability_checks 는 1 이상이어야 합니다.")
        if not self.extensions:
            raise DependencyError("watcher.extensions 가 비어 있습니다 — 처리할 확장자를 하나 이상 지정하세요.")


@dataclass(frozen=True)
class PipelineConfig:
    """파이프라인 전체 설정 + 런타임 시크릿(API 키)."""

    audio: AudioConfig
    stt: SttConfig
    chunking: ChunkingConfig
    summarize: SummarizeConfig
    watcher: WatcherConfig
    api_key: str


def load_config(
    config_path: str | os.PathLike[str] = DEFAULT_CONFIG_PATH,
) -> PipelineConfig:
    """YAML 설정과 ``.env`` 를 읽어 :class:`PipelineConfig` 를 만든다.

    Args:
        config_path: pipeline.yaml 경로.

    Returns:
        검증된 설정 객체.

    Raises:
        DependencyError: 설정 파일이 없거나 ``OPENROUTER_API_KEY`` 가 비어 있을 때.
    """
    path = Path(config_path)
    # CWD 기준으로 못 찾고 상대경로면 프로젝트 루트 기준으로 재시도(기본 실행 편의).
    if not path.is_file() and not path.is_absolute():
        path = PROJECT_ROOT / config_path
    if not path.is_file():
        raise DependencyError(f"설정 파일을 찾을 수 없습니다: {path}. " "configs/pipeline.yaml 을 확인하세요.")

    with path.open("r", encoding="utf-8") as fp:
        raw = yaml.safe_load(fp) or {}

    load_dotenv()
    api_key = os.environ.get(ENV_API_KEY, "").strip()
    if not api_key:
        raise DependencyError(
            f"{ENV_API_KEY} 가 설정되지 않았습니다. "
            ".env.example 을 .env 로 복사하고 OpenRouter 키를 채워넣으세요 "
            "(https://openrouter.ai/keys)."
        )

    return _build_config(raw, api_key)


def _build_config(raw: dict, api_key: str) -> PipelineConfig:
    """파싱된 dict 를 dataclass 트리로 변환한다.

    Raises:
        DependencyError: YAML 에 필수 섹션/키가 없거나 값 타입이 잘못됐을 때.
            (CLI 가 PipelineError 만 잡으므로 raw KeyError/TypeError 노출을 막는다.)
    """
    try:
        audio_raw = raw["audio"]
        stt_raw = raw["stt"]
        gate_raw = stt_raw["confidence_gate"]
        chunk_raw = raw["chunking"]
        sum_raw = raw["summarize"]
        watch_raw = raw.get("watcher")
        if watch_raw is None:
            raise DependencyError("설정에 watcher 섹션이 없습니다. configs/pipeline.yaml 의 watcher 섹션을 확인하세요.")

        return PipelineConfig(
            audio=AudioConfig(
                sample_rate=int(audio_raw["sample_rate"]),
                timeout_sec=int(audio_raw["timeout_sec"]),
            ),
            stt=SttConfig(
                whisper_cli=_resolve_resource(str(stt_raw["whisper_cli"])),
                model_path=_resolve_resource(str(stt_raw["model_path"])),
                language=str(stt_raw["language"]),
                timeout_sec=int(stt_raw["timeout_sec"]),
                confidence_gate=ConfidenceGate(
                    max_no_speech_prob=float(gate_raw["max_no_speech_prob"]),
                    min_avg_logprob=float(gate_raw["min_avg_logprob"]),
                    min_valid_ratio=float(gate_raw["min_valid_ratio"]),
                ),
                completeness_tolerance_sec=float(stt_raw["completeness_tolerance_sec"]),
            ),
            chunking=ChunkingConfig(
                minutes=int(chunk_raw["minutes"]),
                overlap_sec=int(chunk_raw["overlap_sec"]),
                single_shot_max_chars=int(chunk_raw["single_shot_max_chars"]),
            ),
            summarize=SummarizeConfig(
                models=tuple(str(m) for m in sum_raw["models"]),
                base_url=str(sum_raw["base_url"]),
                max_retries=int(sum_raw["max_retries"]),
                backoff_base=float(sum_raw["backoff_base"]),
                request_timeout_sec=int(sum_raw["request_timeout_sec"]),
                max_tokens=int(sum_raw["max_tokens"]),
                temperature=float(sum_raw["temperature"]),
                max_chunk_failure_pct=float(sum_raw["max_chunk_failure_pct"]),
            ),
            watcher=_build_watcher_config(watch_raw),
            api_key=api_key,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DependencyError(
            f"pipeline.yaml 설정이 올바르지 않습니다({type(exc).__name__}: {exc}). "
            "configs/pipeline.yaml 의 필수 키와 값 타입을 확인하세요."
        ) from exc


def _build_watcher_config(watch_raw: dict) -> WatcherConfig:
    """watcher 설정 dict 를 :class:`WatcherConfig` 로 변환한다.

    경계값 검증은 :meth:`WatcherConfig.__post_init__` 가 소유한다(다른 config 와 일관).
    이 함수는 dict→타입 변환과 경로 해석만 담당한다.
    """
    return WatcherConfig(
        inbox_dir=_resolve_dir(str(watch_raw["inbox_dir"])),
        processed_dir=_resolve_dir(str(watch_raw["processed_dir"])),
        failed_dir=_resolve_dir(str(watch_raw["failed_dir"])),
        output_dir=_resolve_dir(str(watch_raw["output_dir"])),
        poll_interval_sec=int(watch_raw["poll_interval_sec"]),
        stability_checks=int(watch_raw["stability_checks"]),
        extensions=tuple(str(ext).lower() for ext in watch_raw["extensions"]),
    )
