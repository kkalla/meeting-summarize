"""요약 단위 테스트: single-shot 분기, 모델 폴백, Map 부분실패 정책."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from openai import AuthenticationError

from src.chunking import Chunk
from src.config import SummarizeConfig
from src.exceptions import SummarizationError
from src.summarize import summarize_meeting


def _auth_error() -> AuthenticationError:
    """openai 인증 오류(401) 인스턴스를 생성한다."""
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(401, request=request)
    return AuthenticationError("invalid api key", response=response, body=None)


NO_SLEEP = lambda _: None  # noqa: E731 - 테스트용 no-op 백오프


class FakeClient:
    """client.chat.completions.create 를 흉내내는 테스트 더블.

    handler(kwargs) 가 응답을 반환하거나 예외를 던진다. 반환값은 두 형태를 지원한다:
      - 문자열/None: 본문. finish_reason 은 기본 "stop".
      - (content, finish_reason) 튜플: 본문과 종료 사유를 함께 지정(잘림 등 케이스용).
    호출 기록은 self.calls 에 쌓인다.
    """

    def __init__(self, handler):
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self._handler = handler

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        result = self._handler(kwargs)  # 예외를 던질 수 있음
        content, finish_reason = result if isinstance(result, tuple) else (result, "stop")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)]
        )


def _chunk(index: int, text: str) -> Chunk:
    return Chunk(index=index, start=0.0, end=1.0, text=text, segments=())


def _config(models=("m1", "m2"), max_retries=2, max_chunk_failure_pct=0.0) -> SummarizeConfig:
    return SummarizeConfig(
        models=tuple(models),
        base_url="https://example/api",
        max_retries=max_retries,
        backoff_base=2,
        request_timeout_sec=10,
        max_tokens=512,
        temperature=0.3,
        max_chunk_failure_pct=max_chunk_failure_pct,
    )


def _summarize(chunks, client, **overrides):
    kwargs = dict(
        config=_config(**overrides.pop("config_kwargs", {})),
        api_key="test",
        map_template="MAP:{chunk_text}",
        reduce_template="REDUCE:{partial_summaries}",
        single_shot_max_chars=overrides.pop("single_shot_max_chars", 5),
        client=client,
        sleep_fn=NO_SLEEP,
    )
    kwargs.update(overrides)
    return summarize_meeting(chunks, **kwargs)


def test_single_chunk_uses_single_shot_reduce_only():
    client = FakeClient(lambda _: "## 핵심 요약\n- ok")

    result = _summarize([_chunk(0, "짧은 회의")], client)

    assert "핵심 요약" in result
    assert len(client.calls) == 1  # Map 생략, 단일 호출
    assert client.calls[0]["messages"][0]["content"].startswith("REDUCE:")


def test_short_total_text_takes_single_shot_even_with_multiple_chunks():
    client = FakeClient(lambda _: "요약")
    chunks = [_chunk(0, "a"), _chunk(1, "b")]  # 총 2자 < 임계 5

    _summarize(chunks, client, single_shot_max_chars=5)

    assert len(client.calls) == 1


def test_map_reduce_path_calls_map_per_chunk_then_reduce():
    client = FakeClient(lambda _: "부분요약")
    chunks = [_chunk(0, "긴구간하나"), _chunk(1, "긴구간둘")]

    _summarize(chunks, client, single_shot_max_chars=1)

    # Map 2회 + Reduce 1회
    assert len(client.calls) == 3
    assert client.calls[0]["messages"][0]["content"].startswith("MAP:")
    assert client.calls[-1]["messages"][0]["content"].startswith("REDUCE:")


def test_falls_back_to_next_model_on_failure():
    def handler(kwargs):
        if kwargs["model"] == "m1":
            raise RuntimeError("429 rate limit")
        return "성공"

    client = FakeClient(handler)

    result = _summarize([_chunk(0, "회의")], client, config_kwargs={"models": ("m1", "m2"), "max_retries": 2})

    assert result == "성공"
    used_models = {call["model"] for call in client.calls}
    assert "m2" in used_models  # 폴백 발생


def test_auth_error_short_circuits_without_retry_or_fallback():
    # 인증 오류는 어떤 모델로 바꿔도 동일 → 재시도/폴백 없이 즉시 중단해야 한다.
    client = FakeClient(lambda _: (_ for _ in ()).throw(_auth_error()))

    with pytest.raises(SummarizationError):
        _summarize([_chunk(0, "회의")], client, config_kwargs={"models": ("m1", "m2"), "max_retries": 3})

    assert len(client.calls) == 1  # 단 한 번만 호출(재시도/폴백 없음)
    assert {call["model"] for call in client.calls} == {"m1"}


def test_empty_choices_falls_back_to_next_model():
    calls: list[dict] = []

    def create(**kwargs):
        calls.append(kwargs)
        if kwargs["model"] == "m1":
            return SimpleNamespace(choices=[])  # 빈 choices → 다음 모델로
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="성공"))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    result = _summarize([_chunk(0, "회의")], client, config_kwargs={"models": ("m1", "m2"), "max_retries": 1})

    assert result == "성공"
    assert {call["model"] for call in calls} == {"m1", "m2"}


def test_raises_when_all_models_fail():
    client = FakeClient(lambda _: (_ for _ in ()).throw(RuntimeError("항상 실패")))

    with pytest.raises(SummarizationError):
        _summarize([_chunk(0, "회의")], client)


def test_map_partial_failure_exceeding_threshold_raises():
    def handler(kwargs):
        content = kwargs["messages"][0]["content"]
        if "MAP:" in content and "FAIL" in content:
            raise RuntimeError("이 청크 실패")
        return "ok"

    client = FakeClient(handler)
    chunks = [_chunk(0, "FAIL구간길게"), _chunk(1, "정상구간길게")]

    # 누락률 50% > 허용 0% → 전체 실패
    with pytest.raises(SummarizationError):
        _summarize(chunks, client, single_shot_max_chars=1, config_kwargs={"max_chunk_failure_pct": 0.0})


def test_map_partial_failure_within_threshold_marks_missing_and_proceeds():
    def handler(kwargs):
        content = kwargs["messages"][0]["content"]
        if "MAP:" in content and "FAIL" in content:
            raise RuntimeError("이 청크 실패")
        return "부분요약"

    client = FakeClient(handler)
    chunks = [_chunk(0, "FAIL구간길게"), _chunk(1, "정상구간길게")]

    # 누락률 50% <= 허용 60% → 누락 표시 후 Reduce 진행
    result = _summarize(
        chunks,
        client,
        single_shot_max_chars=1,
        config_kwargs={"max_chunk_failure_pct": 60.0, "models": ("m1",), "max_retries": 1},
    )

    reduce_call = client.calls[-1]["messages"][0]["content"]
    assert "누락" in reduce_call  # 실패 구간이 Reduce 입력에 명시됨
    assert result == "부분요약"


def test_truncated_length_finish_reason_skips_retry_and_falls_back():
    # reasoning 모델이 사고과정에서 max_tokens 를 소진해 본문이 잘린 경우(finish_reason="length").
    # 잘린 사고과정을 요약으로 저장하면 안 되므로 실패로 보고 다음 모델로 폴백해야 한다.
    # 같은 예산이면 또 잘리는 결정적 실패라 재시도 없이 단 한 번만 호출하고 폴백한다.
    def handler(kwargs):
        if kwargs["model"] == "m1":
            return ("We need to produce final summary in Korean...", "length")
        return ("## 핵심 요약\n- ok", "stop")

    client = FakeClient(handler)

    result = _summarize([_chunk(0, "회의")], client, config_kwargs={"models": ("m1", "m2"), "max_retries": 3})

    assert "핵심 요약" in result
    assert [c["model"] for c in client.calls][-1] == "m2"  # 잘린 m1 버리고 m2 로 폴백
    # 재시도 스킵: m1 은 결정적 잘림이라 max_retries(3) 만큼 재시도하지 않고 단 한 번만 호출.
    assert len([c for c in client.calls if c["model"] == "m1"]) == 1


def test_reasoning_exclude_passed_in_extra_body():
    # reasoning 모델의 사고과정이 본문/토큰예산을 잠식하지 않도록 OpenRouter reasoning 제어를 전달한다.
    client = FakeClient(lambda _: "## 핵심 요약\n- ok")

    _summarize([_chunk(0, "회의")], client)

    extra_body = client.calls[0].get("extra_body", {})
    assert extra_body.get("reasoning", {}).get("exclude") is True


def test_inline_think_block_is_stripped_from_content():
    # 일부 free provider 는 exclude 를 무시하고 content 에 <think> 를 인라인으로 남긴다 — 방어적으로 제거.
    client = FakeClient(lambda _: "<think>영어로 주절주절 사고과정</think>\n## 핵심 요약\n- ok")

    result = _summarize([_chunk(0, "회의")], client)

    assert "<think>" not in result
    assert result.startswith("## 핵심 요약")


def test_unclosed_think_block_does_not_leak_to_summary():
    # 모델이 <think> 를 닫지 않고 finish_reason="stop" 으로 끝낸 누출 변종.
    # 닫는 태그가 없어도 끝까지 제거되어 raw 사고과정이 요약으로 새어나가면 안 된다.
    # 본문이 통째로 사고과정이므로 제거 후 빈 문자열 → 누출로 보고 다음 모델로 폴백.
    def handler(kwargs):
        if kwargs["model"] == "m1":
            return "<think>닫는 태그 없이 영어로 주절주절 사고만 하다 끝남"
        return "## 핵심 요약\n- ok"

    client = FakeClient(handler)
    result = _summarize([_chunk(0, "회의")], client, config_kwargs={"models": ("m1", "m2")})

    assert "<think>" not in result
    assert result.startswith("## 핵심 요약")
    assert [c["model"] for c in client.calls][-1] == "m2"  # 누출난 m1 버리고 m2 로 폴백


def test_unclosed_think_with_trailing_body_drops_body_and_falls_back():
    # 의도된 트레이드오프 명세: <think> 가 닫히지 않은 채 뒤에 본문이 이어지면, 사고/본문 경계
    # 신호가 없으므로 본문도 함께 제거된다 → 빈 결과 → 누출로 보고 다음 모델로 폴백.
    # (복구 불가능한 누출을 조용히 새게 두느니 시끄럽게 폴백시키는 게 낫다.)
    def handler(kwargs):
        if kwargs["model"] == "m1":
            return "<think>닫는 태그 없는 사고과정\n## 핵심 요약\n- 버려질 본문"
        return "## 핵심 요약\n- ok"

    client = FakeClient(handler)
    result = _summarize([_chunk(0, "회의")], client, config_kwargs={"models": ("m1", "m2")})

    assert result == "## 핵심 요약\n- ok"  # m1 의 본문은 살리지 않고 m2 로 폴백
    assert [c["model"] for c in client.calls][-1] == "m2"


def test_nested_think_leak_falls_back_without_leaking_residual():
    # 중첩 <think> 는 비탐욕 매칭이 안쪽 </think> 에서 멈춰, 바깥 </think> 와 그 앞의 잔여
    # 사고과정("잔여사고")이 고아로 남는다. 잔여 사고과정과 본문 경계를 못 가르므로 섣불리
    # 본문을 살리지 않고(=유실 위험) 누출로 보고 다음 모델로 폴백한다.
    def handler(kwargs):
        if kwargs["model"] == "m1":
            return "<think>외부<think>내부</think>잔여사고</think>\n## 핵심 요약\n- 버려질 본문"
        return "## 핵심 요약\n- ok"

    client = FakeClient(handler)
    result = _summarize([_chunk(0, "회의")], client, config_kwargs={"models": ("m1", "m2")})

    assert "</think>" not in result
    assert "잔여사고" not in result  # 잔여 사고과정이 요약으로 새지 않음
    assert result == "## 핵심 요약\n- ok"  # m1 본문은 살리지 않고 m2 로 폴백
    assert [c["model"] for c in client.calls][-1] == "m2"


def test_body_containing_literal_close_tag_is_not_silently_truncated():
    # 회귀 방지: 본문이 </think> 문자열을 포함해도(예: LLM 태그를 논의하는 회의) 그 앞부분이
    # 조용히 잘려나가면 안 된다. 고아 </think> 는 누출 신호로 보고 폴백 → 다음 모델이 깨끗한 요약.
    def handler(kwargs):
        if kwargs["model"] == "m1":
            return "## 핵심 요약\n- 모델이 </think> 태그를 흘리는 버그 논의"
        return "## 핵심 요약\n- 깨끗한 요약"

    client = FakeClient(handler)
    result = _summarize([_chunk(0, "회의")], client, config_kwargs={"models": ("m1", "m2")})

    assert result == "## 핵심 요약\n- 깨끗한 요약"  # 본문 일부만 잘린 결과가 저장되지 않음
    assert [c["model"] for c in client.calls][-1] == "m2"


def test_think_only_content_skips_retry_and_falls_back():
    # provider 가 exclude 를 무시하고 본문 없이 <think>...</think> 만 보낸 경우.
    # 빈 응답으로 뭉개지 않고 ReasoningLeakError 로 분류 → 결정적이라 재시도 없이 바로 다음 모델로.
    def handler(kwargs):
        if kwargs["model"] == "m1":
            return "<think>사고과정만 잔뜩 하고 본문은 없음</think>"
        return "## 핵심 요약\n- ok"

    client = FakeClient(handler)
    result = _summarize([_chunk(0, "회의")], client, config_kwargs={"models": ("m1", "m2"), "max_retries": 3})

    assert result.startswith("## 핵심 요약")
    # 재시도 스킵: m1 은 결정적 누출이라 max_retries(3) 만큼 재시도하지 않고 단 한 번만 호출.
    m1_calls = [c for c in client.calls if c["model"] == "m1"]
    assert len(m1_calls) == 1


def test_think_only_on_all_models_raises_reasoning_leak_error():
    # 모든 모델이 사고과정만 누출하면 ReasoningLeakError(SummarizationError) 로 실패한다.
    client = FakeClient(lambda _: "<think>본문 없이 사고만</think>")

    with pytest.raises(SummarizationError):
        _summarize([_chunk(0, "회의")], client, config_kwargs={"models": ("m1",), "max_retries": 2})

    # 단일 모델·결정적 누출 → 재시도 없이 단 한 번만 호출.
    assert len(client.calls) == 1


def test_truncation_on_all_models_raises_summarization_error():
    # 모든 모델이 잘림(finish_reason="length")이면 TruncatedResponseError(SummarizationError) 로 실패.
    client = FakeClient(lambda _: ("사고과정만 하다 잘림", "length"))

    with pytest.raises(SummarizationError):
        _summarize([_chunk(0, "회의")], client, config_kwargs={"models": ("m1",), "max_retries": 3})

    # 단일 모델·결정적 잘림 → 재시도 없이 단 한 번만 호출.
    assert len(client.calls) == 1


def test_blank_content_without_think_retries_then_falls_back():
    # <think> 없이 공백만 온 응답은 reasoning 누출이 아니라 일시적 빈 응답일 수 있다.
    # ReasoningLeakError(재시도 스킵)로 오분류하지 않고 일반 오류로 던져 재시도 경로를 태운다.
    def handler(kwargs):
        if kwargs["model"] == "m1":
            return "   "  # <think> 없는 공백 응답
        return "## 핵심 요약\n- ok"

    client = FakeClient(handler)
    result = _summarize([_chunk(0, "회의")], client, config_kwargs={"models": ("m1", "m2"), "max_retries": 2})

    assert result == "## 핵심 요약\n- ok"
    # 누출이 아니므로 재시도 스킵하지 않음 → m1 을 max_retries(2) 만큼 호출한 뒤 폴백.
    assert len([c for c in client.calls if c["model"] == "m1"]) == 2


def test_content_none_is_distinguished_and_falls_back_to_next_model():
    # m1 은 본문 없이(content=None) 응답 → 빈 문자열로 뭉개지 않고 다음 모델로 폴백.
    def handler(kwargs):
        return None if kwargs["model"] == "m1" else "## 핵심\n- ok"

    client = FakeClient(handler)
    result = _summarize([_chunk(0, "짧은 회의")], client, config_kwargs={"models": ("m1", "m2")})

    assert result == "## 핵심\n- ok"
    assert [c["model"] for c in client.calls][-1] == "m2"
