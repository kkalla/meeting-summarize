"""Slack 봇 설정 dataclass 검증 + YAML→dataclass 변환 단위 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.exceptions import DependencyError
from src.slack_bot.config import (
    OpenRouterSttConfig,
    SlackConfig,
    _build_config,
)

# --- SlackConfig ------------------------------------------------------------


def test_slack_config_rejects_empty_allowed_extensions():
    with pytest.raises(DependencyError):
        SlackConfig(allowed_extensions=(), output_dir=Path("/data/slack_output"), ack_message="hi")


def test_slack_config_rejects_blank_ack_message():
    with pytest.raises(DependencyError):
        SlackConfig(allowed_extensions=(".m4a",), output_dir=Path("/data/slack_output"), ack_message="   ")


# --- OpenRouterSttConfig -----------------------------------------------------


def _stt(**overrides) -> dict:
    base = {
        "model": "microsoft/mai-transcribe-1.5",
        "base_url": "https://openrouter.ai/api/v1",
        "language": "ko",
        "request_timeout_sec": 300,
        "max_retries": 3,
        "backoff_base": 2.0,
    }
    base.update(overrides)
    return base


def test_stt_config_rejects_blank_model():
    with pytest.raises(DependencyError):
        OpenRouterSttConfig(**_stt(model="  "))


def test_stt_config_rejects_non_positive_max_retries():
    with pytest.raises(DependencyError):
        OpenRouterSttConfig(**_stt(max_retries=0))


def test_stt_config_rejects_non_positive_timeout():
    with pytest.raises(DependencyError):
        OpenRouterSttConfig(**_stt(request_timeout_sec=0))


def test_stt_config_accepts_valid_values():
    cfg = OpenRouterSttConfig(**_stt())
    assert cfg.model == "microsoft/mai-transcribe-1.5"


# --- _build_config -----------------------------------------------------------


def _raw() -> dict:
    """검증을 통과하는 최소 정상 YAML dict."""
    return {
        "slack": {
            "allowed_extensions": [".m4a", ".wav"],
            "output_dir": "data/slack_output",
            "ack_message": "받았습니다",
        },
        "stt": {
            "model": "microsoft/mai-transcribe-1.5",
            "base_url": "https://openrouter.ai/api/v1",
            "language": "ko",
            "request_timeout_sec": 300,
            "max_retries": 3,
            "backoff_base": 2,
        },
    }


def test_build_config_happy_path():
    cfg = _build_config(_raw(), bot_token="xoxb-t", app_token="xapp-t", api_key="key")
    assert cfg.slack_bot_token == "xoxb-t"
    assert cfg.slack_app_token == "xapp-t"
    assert cfg.openrouter_api_key == "key"
    assert cfg.slack.allowed_extensions == (".m4a", ".wav")
    assert cfg.stt.model == "microsoft/mai-transcribe-1.5"


def test_build_config_lowercases_extensions():
    raw = _raw()
    raw["slack"]["allowed_extensions"] = [".M4A", ".WAV"]
    cfg = _build_config(raw, bot_token="t", app_token="t", api_key="k")
    assert cfg.slack.allowed_extensions == (".m4a", ".wav")


def test_build_config_missing_section_raises_dependency_error_not_keyerror():
    raw = _raw()
    del raw["stt"]
    with pytest.raises(DependencyError):
        _build_config(raw, bot_token="t", app_token="t", api_key="k")


def test_build_config_wrong_type_raises_dependency_error():
    raw = _raw()
    raw["stt"]["max_retries"] = "not-a-number"
    with pytest.raises(DependencyError):
        _build_config(raw, bot_token="t", app_token="t", api_key="k")
