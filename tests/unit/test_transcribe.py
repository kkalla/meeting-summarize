"""전사 순수 로직 단위 테스트: JSON 파싱, 완전성 검사, 신뢰도 게이트."""

from __future__ import annotations

import math

import pytest

from src.config import ConfidenceGate, SttConfig
from src.exceptions import TranscriptionError
from src.transcribe import (
    Segment,
    _run_whisper,
    apply_confidence_gate,
    check_completeness,
    parse_segments,
)


def _gate(max_no_speech=0.6, min_avg_logprob=-1.0, min_valid_ratio=0.2) -> ConfidenceGate:
    return ConfidenceGate(
        max_no_speech_prob=max_no_speech,
        min_avg_logprob=min_avg_logprob,
        min_valid_ratio=min_valid_ratio,
    )


# --- _run_whisper 예외 래핑 ------------------------------------------------


def test_run_whisper_wraps_oserror_as_transcription_error(tmp_path, monkeypatch):
    # Arrange: subprocess 실행이 OSError(권한 없음/아키텍처 불일치) 를 던지는 상황
    def boom(*_args, **_kwargs):
        raise OSError("Permission denied")

    monkeypatch.setattr("src.transcribe.subprocess.run", boom)
    config = SttConfig(
        whisper_cli="/fake/whisper-cli",
        model_path="/fake/model.bin",
        language="ko",
        timeout_sec=10,
        confidence_gate=_gate(),
        completeness_tolerance_sec=5.0,
    )

    # Act / Assert: 원시 OSError 가 아니라 TranscriptionError 로 변환되어야 한다
    with pytest.raises(TranscriptionError):
        _run_whisper(tmp_path / "a.wav", tmp_path / "out", config)


# --- parse_segments -------------------------------------------------------


def test_parse_segments_converts_offsets_ms_to_sec():
    data = {
        "transcription": [
            {"offsets": {"from": 0, "to": 5000}, "text": " 안녕하세요 "},
            {"offsets": {"from": 5000, "to": 10000}, "text": "회의 시작"},
        ]
    }

    segments = parse_segments(data)

    assert len(segments) == 2
    assert segments[0].start == 0.0
    assert segments[0].end == 5.0
    assert segments[0].text == "안녕하세요"  # strip 됨
    assert segments[1].start == 5.0


def test_parse_segments_approximates_avg_logprob_from_token_probs():
    data = {
        "transcription": [
            {"offsets": {"from": 0, "to": 1000}, "text": "x", "tokens": [{"p": 0.5}, {"p": 0.5}]},
        ]
    }

    segments = parse_segments(data)

    assert segments[0].avg_logprob == pytest.approx(math.log(0.5))


def test_parse_segments_without_tokens_yields_none_confidence():
    # whisper.cpp 의 full JSON(-ojf)이 아닌 경우 tokens/no_speech_prob 가 없을 수 있다.
    # 그럴 땐 신뢰도 필드가 None 이 되고, 게이트는 텍스트 존재 여부로만 작동한다(한계).
    data = {"transcription": [{"offsets": {"from": 0, "to": 1000}, "text": "텍스트만"}]}

    segments = parse_segments(data)

    assert segments[0].avg_logprob is None
    assert segments[0].no_speech_prob is None


def test_parse_segments_raises_when_transcription_key_missing():
    with pytest.raises(TranscriptionError):
        parse_segments({"result": {}})


def test_parse_segments_raises_on_malformed_segment():
    data = {"transcription": [{"text": "오프셋 없음"}]}

    with pytest.raises(TranscriptionError):
        parse_segments(data)


# --- check_completeness ---------------------------------------------------


def test_check_completeness_passes_when_within_tolerance():
    segments = [Segment(0, 100.0, "끝까지")]

    # 오디오 102초, 마지막 end 100초 → 차이 2초 < 허용 5초
    check_completeness(segments, audio_duration_sec=102.0, tolerance_sec=5.0)


def test_check_completeness_raises_on_truncated_transcript():
    segments = [Segment(0, 100.0, "잘림")]

    # 오디오 200초인데 100초에서 끝남 → 잘린 전사
    with pytest.raises(TranscriptionError):
        check_completeness(segments, audio_duration_sec=200.0, tolerance_sec=5.0)


def test_check_completeness_noop_on_empty():
    check_completeness([], audio_duration_sec=100.0, tolerance_sec=5.0)


# --- apply_confidence_gate ------------------------------------------------


def test_gate_raises_on_empty_segments():
    with pytest.raises(TranscriptionError):
        apply_confidence_gate([], _gate())


def test_gate_raises_when_mostly_silence():
    # 모든 세그먼트 no_speech_prob 높음 → 유효 비율 0
    segments = [Segment(0, 5, "환각", no_speech_prob=0.9) for _ in range(5)]

    with pytest.raises(TranscriptionError):
        apply_confidence_gate(segments, _gate())


def test_gate_passes_with_valid_speech():
    segments = [Segment(i, i + 1, "유효 발화", no_speech_prob=0.1, avg_logprob=-0.3) for i in range(5)]

    # 예외 없이 통과해야 함
    apply_confidence_gate(segments, _gate())


def test_gate_flags_low_logprob_as_hallucination():
    segments = [Segment(i, i + 1, "텍스트", avg_logprob=-3.0) for i in range(5)]

    with pytest.raises(TranscriptionError):
        apply_confidence_gate(segments, _gate(min_avg_logprob=-1.0))
