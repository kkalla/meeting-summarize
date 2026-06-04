"""설정 dataclass 검증 + YAML→dataclass 변환 단위 테스트.

frozen dataclass 의 ``__post_init__`` 가 도메인 범위를 벗어난 값을 생성 시점에
거부하는지(게이트 무력화·무한루프 예방), 그리고 YAML 키 누락이 raw 예외가 아닌
:class:`DependencyError` 로 포장되는지 확인한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import (
    CacheConfig,
    ChunkingConfig,
    ConfidenceGate,
    SttConfig,
    SummarizeConfig,
    _build_config,
)
from src.exceptions import DependencyError

# --- CacheConfig ----------------------------------------------------------


def test_cache_config_rejects_non_positive_ttl_when_enabled():
    with pytest.raises(DependencyError):
        CacheConfig(transcripts_dir=Path("/data/transcripts"), ttl_hours=0, enabled=True)


def test_cache_config_allows_any_ttl_when_disabled():
    # disabled 면 ttl 은 쓰이지 않으므로 검증하지 않는다.
    cfg = CacheConfig(transcripts_dir=Path("/data/transcripts"), ttl_hours=0, enabled=False)
    assert cfg.enabled is False


def test_build_config_parses_cache_section():
    raw = _raw()
    raw["cache"] = {"transcripts_dir": "/data/transcripts", "ttl_hours": 168, "enabled": True}
    cfg = _build_config(raw, api_key="k")
    assert cfg.cache.enabled is True
    assert cfg.cache.transcripts_dir == Path("/data/transcripts")
    assert cfg.cache.ttl_hours == 168.0


def test_build_config_missing_cache_section_defaults_disabled():
    raw = _raw()  # cache 키 없음
    cfg = _build_config(raw, api_key="k")
    assert cfg.cache.enabled is False


def test_build_config_parses_stt_prompt_and_strips():
    raw = _raw()
    raw["stt"]["prompt"] = "  비식별화, 밸리데이트  "
    cfg = _build_config(raw, api_key="k")
    assert cfg.stt.prompt == "비식별화, 밸리데이트"  # 앞뒤 공백 제거


def test_build_config_stt_prompt_defaults_empty_when_missing():
    raw = _raw()  # stt.prompt 키 없음(기존 설정 파일 하위호환)
    cfg = _build_config(raw, api_key="k")
    assert cfg.stt.prompt == ""


@pytest.mark.parametrize("bad_prompt", [True, None, ["용어"], 123])
def test_build_config_rejects_non_string_prompt(bad_prompt):
    # 비문자열 YAML(prompt: true/null/리스트)을 str() 로 조용히 "True"/"None" 으로 덮지 않고
    # 명시적으로 거부해야 한다(깨진 용어집이 whisper 에 주입되는 것 방지).
    raw = _raw()
    raw["stt"]["prompt"] = bad_prompt
    with pytest.raises(DependencyError):
        _build_config(raw, api_key="k")


def test_build_config_rejects_overlong_prompt():
    raw = _raw()
    raw["stt"]["prompt"] = "가" * 2001  # MAX_STT_PROMPT_CHARS(2000) 초과
    with pytest.raises(DependencyError):
        _build_config(raw, api_key="k")


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
            "rtf_estimate": 0.2,
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


# --- SttConfig ------------------------------------------------------------


def _stt(**overrides):
    base = {
        "whisper_cli": "whisper-cli",
        "model_path": "model.bin",
        "language": "ko",
        "timeout_sec": 3600,
        "confidence_gate": ConfidenceGate(max_no_speech_prob=0.6, min_avg_logprob=-1.0, min_valid_ratio=0.2),
        "completeness_tolerance_sec": 5.0,
        "rtf_estimate": 0.2,
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize("rtf", [0.0, -0.5])
def test_stt_config_rejects_non_positive_rtf_estimate(rtf):
    with pytest.raises(DependencyError):
        SttConfig(**_stt(rtf_estimate=rtf))


def test_stt_config_strips_prompt_on_direct_construction():
    # 불변식이 로딩 경로가 아니라 타입(__post_init__)에 있으므로, SttConfig 를 직접
    # 생성해도 앞뒤 공백이 제거된다(공백차만 다른 프롬프트가 다른 캐시 지문을 내지 않게).
    cfg = SttConfig(**_stt(prompt="  비식별화  "))
    assert cfg.prompt == "비식별화"


def test_stt_config_rejects_non_string_prompt_on_direct_construction():
    with pytest.raises(DependencyError):
        SttConfig(**_stt(prompt=True))


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
