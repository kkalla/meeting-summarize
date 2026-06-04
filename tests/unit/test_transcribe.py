"""전사 순수 로직 단위 테스트: JSON 파싱, 완전성 검사, 신뢰도 게이트."""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from src.config import ConfidenceGate, SttConfig
from src.exceptions import TranscriptionError
from src.transcribe import (
    Segment,
    _run_whisper,
    apply_confidence_gate,
    check_completeness,
    parse_segments,
    transcribe,
)


def _gate(max_no_speech=0.6, min_avg_logprob=-1.0, min_valid_ratio=0.2) -> ConfidenceGate:
    return ConfidenceGate(
        max_no_speech_prob=max_no_speech,
        min_avg_logprob=min_avg_logprob,
        min_valid_ratio=min_valid_ratio,
    )


def _stt_config(rtf_estimate=0.2, prompt="") -> SttConfig:
    return SttConfig(
        whisper_cli="/fake/whisper-cli",
        model_path="/fake/model.bin",
        language="ko",
        timeout_sec=10,
        confidence_gate=_gate(),
        completeness_tolerance_sec=5.0,
        rtf_estimate=rtf_estimate,
        prompt=prompt,
    )


def _whisper_json(tmp_path):
    """정상 transcription JSON 을 써 두고 prefix(Path) 를 반환한다(cmd 검증용 공통 헬퍼)."""
    prefix = tmp_path / "out"
    prefix.with_suffix(".json").write_text(
        '{"transcription": [{"offsets": {"from": 0, "to": 1000}, "text": "안녕"}]}',
        encoding="utf-8",
    )
    return prefix


def test_run_whisper_injects_prompt_flag_when_set(tmp_path, monkeypatch):
    # Arrange: 초기 프롬프트가 설정된 STT 설정
    prefix = _whisper_json(tmp_path)
    captured: dict = {}

    def fake_run(cmd, *_a, **_k):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("src.transcribe.subprocess.run", fake_run)
    config = _stt_config(prompt="비식별화, 밸리데이트")

    # Act
    _run_whisper(tmp_path / "a.wav", prefix, config)

    # Assert: --prompt 와 그 값이 정확히 cmd 에 들어간다
    cmd = captured["cmd"]
    assert "--prompt" in cmd
    assert cmd[cmd.index("--prompt") + 1] == "비식별화, 밸리데이트"


def test_run_whisper_omits_prompt_flag_when_empty(tmp_path, monkeypatch):
    # Arrange: 프롬프트 없음(기본 "")
    prefix = _whisper_json(tmp_path)
    captured: dict = {}

    def fake_run(cmd, *_a, **_k):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("src.transcribe.subprocess.run", fake_run)
    config = _stt_config()

    # Act
    _run_whisper(tmp_path / "a.wav", prefix, config)

    # Assert: 빈 프롬프트면 --prompt 자체가 빠진다(기본 동작 유지)
    assert "--prompt" not in captured["cmd"]


# --- _run_whisper 예외 래핑 ------------------------------------------------


def test_run_whisper_wraps_oserror_as_transcription_error(tmp_path, monkeypatch):
    # Arrange: subprocess 실행이 OSError(권한 없음/아키텍처 불일치) 를 던지는 상황
    def boom(*_args, **_kwargs):
        raise OSError("Permission denied")

    monkeypatch.setattr("src.transcribe.subprocess.run", boom)
    config = _stt_config()

    # Act / Assert: 원시 OSError 가 아니라 TranscriptionError 로 변환되어야 한다
    with pytest.raises(TranscriptionError):
        _run_whisper(tmp_path / "a.wav", tmp_path / "out", config)


def test_run_whisper_tolerates_non_utf8_bytes_in_token_text(tmp_path, monkeypatch):
    # whisper.cpp -ojf 가 한글 토큰을 UTF-8 경계 중간에서 잘라 invalid byte 를 낸 상황 재현.
    # text 는 "안녕"(완전), token text 에는 잘린 "\xeb" 가 섞여 있다.
    prefix = tmp_path / "out"
    raw = (
        b'{"transcription": [{"offsets": {"from": 0, "to": 1000}, '
        b'"text": "\xec\x95\x88\xeb\x85\x95", "tokens": [{"p": 0.9, "text": "\xec\x95\x88\xeb"}]}]}'
    )
    prefix.with_suffix(".json").write_bytes(raw)

    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("src.transcribe.subprocess.run", fake_run)
    config = _stt_config()

    # Act: invalid byte 가 있어도 파싱이 깨지지 않아야 한다
    data = _run_whisper(tmp_path / "a.wav", prefix, config)

    # Assert: 세그먼트 text(완전한 문장)와 토큰 확률은 온전하게 살아남는다
    segments = parse_segments(data)
    assert len(segments) == 1
    assert segments[0].text == "안녕"
    assert segments[0].avg_logprob is not None


def test_run_whisper_logs_eta_when_duration_given(tmp_path, monkeypatch, caplog):
    # Arrange: 정상 JSON 을 내는 whisper, 오디오 100초 + rtf 0.2 → 예상 ~20초
    prefix = tmp_path / "out"
    prefix.with_suffix(".json").write_text(
        '{"transcription": [{"offsets": {"from": 0, "to": 1000}, "text": "안녕"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "src.transcribe.subprocess.run",
        lambda *a, **k: SimpleNamespace(returncode=0, stderr=""),
    )
    config = _stt_config(rtf_estimate=0.2)

    # Act
    with caplog.at_level("INFO", logger="src.transcribe"):
        _run_whisper(tmp_path / "a.wav", prefix, config, duration_sec=100.0)

    # Assert: 시작 로그에 오디오 길이와 예상 소요시간(100×0.2=20초)이 찍힌다
    start_log = next(r.message for r in caplog.records if "전사 시작" in r.message)
    assert "오디오 100초" in start_log
    assert "예상 ~20초" in start_log


# --- transcribe (오케스트레이션) ------------------------------------------


def _valid_transcription(end_ms: int = 1000) -> dict:
    """신뢰도 게이트를 통과하는 최소 유효 전사(무음확률 낮음 + logprob 높음).

    ``end_ms`` 로 마지막 세그먼트 끝을 오디오 길이에 맞춰 완전성 검사 통과를 보장한다.
    """
    return {
        "transcription": [
            {
                "offsets": {"from": 0, "to": end_ms},
                "text": "안녕",
                "no_speech_prob": 0.1,
                "tokens": [{"p": 0.9}],
            }
        ]
    }


def _patch_transcribe_internals(monkeypatch, *, duration, data):
    # transcribe() 종료 로그/RTF 계산만 보려고 의존성 검사·WAV 읽기·subprocess 를 우회한다.
    monkeypatch.setattr("src.transcribe._check_dependencies", lambda config: None)
    monkeypatch.setattr("src.transcribe._wav_duration_sec", lambda path: duration)
    monkeypatch.setattr("src.transcribe._run_whisper", lambda *a, **k: data)


def test_transcribe_logs_completion_with_actual_rtf(tmp_path, monkeypatch, caplog):
    # Arrange: 오디오 100초, 전사는 mock 으로 즉시 반환(세그먼트 끝=100초 → 완전성 통과)
    _patch_transcribe_internals(monkeypatch, duration=100.0, data=_valid_transcription(end_ms=100_000))

    # Act
    with caplog.at_level("INFO", logger="src.transcribe"):
        segments = transcribe(tmp_path / "a.wav", _stt_config())

    # Assert: 종료 로그에 오디오 길이·실측 RTF 가 찍힌다
    assert len(segments) == 1
    end_log = next(r.message for r in caplog.records if "전사 종료" in r.message)
    assert "오디오 100초" in end_log
    assert "RTF" in end_log


def test_transcribe_zero_duration_does_not_divide_by_zero(tmp_path, monkeypatch, caplog):
    # Arrange: 길이 0 오디오(빈/손상 WAV 경계) — actual_rtf 가드 없으면 ZeroDivisionError
    _patch_transcribe_internals(monkeypatch, duration=0.0, data=_valid_transcription())

    # Act: 예외 없이 통과해야 한다
    with caplog.at_level("INFO", logger="src.transcribe"):
        segments = transcribe(tmp_path / "a.wav", _stt_config())

    # Assert: 0 으로 나누지 않고 RTF 0.00 으로 안전하게 로깅
    assert len(segments) == 1
    end_log = next(r.message for r in caplog.records if "전사 종료" in r.message)
    assert "RTF 0.00" in end_log


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
