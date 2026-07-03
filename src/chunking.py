"""요약 단위(``Chunk``) 데이터클래스.

과거에는 이 모듈이 whisper.cpp 세그먼트를 시간창+오버랩으로 나누는 ``chunk_segments()``
로직까지 함께 갖고 있었으나, 그 로직은 legacy 로컬 STT 파이프라인(``legacy/chunking.py``)
전용이라 그쪽으로 옮겼다. 여기 남은 ``Chunk`` 는 ``summarize.summarize_meeting()`` 이 실제로
쓰는 최소 계약(순번 + 본문 텍스트)만 담은 core 타입으로, legacy 파이프라인과 Slack 봇
(``src/slack_bot/processor.py``) 양쪽에서 공유한다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    """요약 단위. 하나 이상의 발화를 묶은 텍스트 구간.

    ``start``/``end``/``segments`` 는 legacy 파이프라인처럼 시간 정보가 있는 호출자를
    위한 선택적 메타데이터일 뿐, ``summarize_meeting()`` 자체는 ``index``/``text`` 만 쓴다.
    Slack 봇처럼 세그먼트 타임스탬프가 없는 호출자는 ``segments=()`` 로 비워도 된다.

    Attributes:
        index: 0-기반 청크 순번.
        start: 청크 시작 시각(초). 타임스탬프가 없으면 0.0.
        end: 청크 종료 시각(초). 타임스탬프가 없으면 회의 길이 등으로 채우거나 0.0.
        text: 청크 본문 텍스트.
        segments: 이 청크를 구성한 원본 단위(타입은 호출자가 결정 — legacy 는
            ``legacy.transcribe.Segment``, Slack 봇은 빈 튜플).
    """

    index: int
    start: float
    end: float
    text: str
    segments: tuple[object, ...]
