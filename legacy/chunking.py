"""세그먼트를 시간 경계 + 오버랩으로 청크 분할하는 순수 로직(legacy 로컬 STT 파이프라인 전용).

발화(세그먼트)를 절대 중간에서 쪼개지 않는다. 청크 경계에 걸친 세그먼트는
오버랩 영역을 통해 인접 청크 양쪽에 포함시켜 액션아이템 누락을 막는다.

``Chunk`` 자체는 core(``src/chunking.py``)로 옮겨 Slack 봇과 공유하고, 여기엔 whisper.cpp
세그먼트(``legacy.transcribe.Segment``)에 의존하는 시간창 분할 로직만 남아 있다.
"""

from __future__ import annotations

from legacy.transcribe import Segment
from src.chunking import Chunk
from src.config import ChunkingConfig

SEC_PER_MIN = 60


def chunk_segments(segments: list[Segment], config: ChunkingConfig) -> list[Chunk]:
    """세그먼트를 ``config.minutes`` 길이 + ``config.overlap_sec`` 오버랩 청크로 나눈다.

    Args:
        segments: 시간순 정렬된 세그먼트(transcribe 출력).
        config: 청킹 설정.

    Returns:
        청크 리스트. 전체가 한 윈도우에 들어가면 길이 1.
    """
    if not segments:
        return []

    # 시간순 정렬을 보장한다(미정렬 입력 시 start/end 경계가 뒤집히는 것 방지).
    segments = sorted(segments, key=lambda seg: (seg.start, seg.end))

    chunk_len = config.minutes * SEC_PER_MIN
    overlap = config.overlap_sec
    total_end = segments[-1].end

    chunks: list[Chunk] = []
    window_start = 0.0
    index = 0
    while window_start < total_end:
        win_lo = max(0.0, window_start - overlap)
        win_hi = window_start + chunk_len
        # 0초 세그먼트가 경계(seg.end == win_lo)에서 탈락하지 않도록 하한은 >= 로 포함.
        members = tuple(seg for seg in segments if seg.start < win_hi and seg.end >= win_lo)
        if members:
            chunks.append(
                Chunk(
                    index=index,
                    start=members[0].start,
                    end=members[-1].end,
                    text="\n".join(seg.text for seg in members if seg.text),
                    segments=members,
                )
            )
            index += 1
        window_start += chunk_len

    return chunks
