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


@dataclass(frozen=True)
class PipelineConfig:
    """파이프라인 전체 설정 + 런타임 시크릿(API 키)."""

    audio: AudioConfig
    stt: SttConfig
    chunking: ChunkingConfig
    summarize: SummarizeConfig
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
    """파싱된 dict 를 dataclass 트리로 변환한다."""
    audio_raw = raw["audio"]
    stt_raw = raw["stt"]
    gate_raw = stt_raw["confidence_gate"]
    chunk_raw = raw["chunking"]
    sum_raw = raw["summarize"]

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
        api_key=api_key,
    )
