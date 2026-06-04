"""설정 dataclass 검증 + YAML→dataclass 변환 단위 테스트.

frozen dataclass 의 ``__post_init__`` 가 도메인 범위를 벗어난 값을 생성 시점에
거부하는지(게이트 무력화·무한루프 예방), 그리고 YAML 키 누락이 raw 예외가 아닌
:class:`DependencyError` 로 포장되는지 확인한다.
"""

from __future__ import annotations

import pytest

from src.config import (
    ChunkingConfig,
    ConfidenceGate,
    SummarizeConfig,
    _build_config,
)
from src.exceptions import DependencyError


def _raw() -> dict:
    """검증을 통과하는 최소 정상 YAML dict."""
    return {
        "audio": {"sample_rate": 16000, "timeout_sec": 300},
        "stt": {
            "whisper_cli": "whisper-cli",
            "model_path": "model.bin",
            "language": "ko",
            "timeout_sec": 3600,
            "confidence_gate": {
                "max_no_speech_prob": 0.6,
                "min_avg_logprob": -1.0,
                "min_valid_ratio": 0.2,
            },
            "completeness_tolerance_sec": 5.0,
        },
        "chunking": {"minutes": 15, "overlap_sec": 30, "single_shot_max_chars": 12000},
        "summarize": {
            "models": ["a/b:free"],
            "base_url": "https://example.com",
            "max_retries": 4,
            "backoff_base": 2,
            "request_timeout_sec": 120,
            "max_tokens": 2048,
            "temperature": 0.3,
            "max_chunk_failure_pct": 0,
        },
        "watcher": {
            "inbox_dir": "/data/inbox",
            "processed_dir": "/data/processed",
            "failed_dir": "/data/failed",
            "output_dir": "/data/output",
            "poll_interval_sec": 10,
            "stability_checks": 2,
            "extensions": [".m4a", ".wav"],
        },
    }


# --- ConfidenceGate -------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_no_speech_prob": 1.5},  # 확률 범위 초과
        {"min_avg_logprob": 0.3},  # 로그확률은 <= 0
        {"min_valid_ratio": 0.0},  # (0, 1] 하한 위반
        {"min_valid_ratio": 1.5},  # 상한 위반
    ],
)
def test_confidence_gate_rejects_out_of_range(kwargs):
    base = {"max_no_speech_prob": 0.6, "min_avg_logprob": -1.0, "min_valid_ratio": 0.2}
    base.update(kwargs)
    with pytest.raises(DependencyError):
        ConfidenceGate(**base)


# --- ChunkingConfig -------------------------------------------------------


def test_chunking_config_rejects_zero_minutes_preventing_infinite_loop():
    with pytest.raises(DependencyError):
        ChunkingConfig(minutes=0, overlap_sec=0, single_shot_max_chars=100)


def test_chunking_config_rejects_overlap_ge_chunk_length():
    with pytest.raises(DependencyError):
        ChunkingConfig(minutes=1, overlap_sec=60, single_shot_max_chars=100)


# --- SummarizeConfig ------------------------------------------------------


def _summarize(**overrides):
    base = {
        "models": ("a/b:free",),
        "base_url": "https://example.com",
        "max_retries": 4,
        "backoff_base": 2.0,
        "request_timeout_sec": 120,
        "max_tokens": 2048,
        "temperature": 0.3,
        "max_chunk_failure_pct": 0.0,
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "overrides",
    [
        {"models": ()},  # 빈 모델 → 실행 시점이 아닌 로딩 시점 실패
        {"max_retries": 0},
        {"temperature": 2.5},
        {"max_chunk_failure_pct": 150.0},
    ],
)
def test_summarize_config_rejects_invalid(overrides):
    with pytest.raises(DependencyError):
        SummarizeConfig(**_summarize(**overrides))


# --- _build_config --------------------------------------------------------


def test_build_config_happy_path():
    config = _build_config(_raw(), api_key="key")
    assert config.api_key == "key"
    assert config.chunking.minutes == 15
    assert config.summarize.models == ("a/b:free",)


def test_build_config_missing_section_raises_dependency_error_not_keyerror():
    raw = _raw()
    del raw["chunking"]
    with pytest.raises(DependencyError):
        _build_config(raw, api_key="key")


def test_build_config_wrong_type_raises_dependency_error():
    raw = _raw()
    raw["audio"]["sample_rate"] = "not-a-number"
    with pytest.raises(DependencyError):
        _build_config(raw, api_key="key")
