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

    handler(kwargs) 가 응답 문자열을 반환하거나 예외를 던진다.
    호출 기록은 self.calls 에 쌓인다.
    """

    def __init__(self, handler):
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self._handler = handler

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        content = self._handler(kwargs)  # 예외를 던질 수 있음
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


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


def test_content_none_is_distinguished_and_falls_back_to_next_model():
    # m1 은 본문 없이(content=None) 응답 → 빈 문자열로 뭉개지 않고 다음 모델로 폴백.
    def handler(kwargs):
        return None if kwargs["model"] == "m1" else "## 핵심\n- ok"

    client = FakeClient(handler)
    result = _summarize([_chunk(0, "짧은 회의")], client, config_kwargs={"models": ("m1", "m2")})

    assert result == "## 핵심\n- ok"
    assert [c["model"] for c in client.calls][-1] == "m2"
