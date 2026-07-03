"""청킹 로직 단위 테스트: 경계 분할, 오버랩 공유, 빈 입력."""

from __future__ import annotations

from legacy.chunking import chunk_segments
from legacy.transcribe import Segment
from src.config import ChunkingConfig


def _seg(start: float, end: float, text: str = "말") -> Segment:
    return Segment(start=start, end=end, text=text)


def _config(minutes: int = 1, overlap_sec: int = 10, single_shot_max_chars: int = 0) -> ChunkingConfig:
    return ChunkingConfig(minutes=minutes, overlap_sec=overlap_sec, single_shot_max_chars=single_shot_max_chars)


def test_returns_empty_when_no_segments():
    assert chunk_segments([], _config()) == []


def test_all_segments_within_one_window_make_single_chunk():
    # Arrange: 1분 윈도우 안에 모두 들어가는 세그먼트
    segments = [_seg(0, 5), _seg(10, 15), _seg(50, 58)]

    # Act
    chunks = chunk_segments(segments, _config(minutes=1, overlap_sec=10))

    # Assert
    assert len(chunks) == 1
    assert chunks[0].index == 0
    assert chunks[0].start == 0
    assert chunks[0].end == 58
    assert len(chunks[0].segments) == 3


def test_boundary_segment_shared_across_chunks_via_overlap():
    # Arrange: 1분 경계에 걸친 세그먼트가 오버랩(10s)으로 양 청크에 들어가야 함
    boundary = _seg(55, 58)
    segments = [_seg(0, 5), boundary, _seg(65, 70)]

    # Act
    chunks = chunk_segments(segments, _config(minutes=1, overlap_sec=10))

    # Assert: 두 청크 모두 경계 세그먼트를 포함
    assert len(chunks) == 2
    assert boundary in chunks[0].segments
    assert boundary in chunks[1].segments


def test_chunk_text_joins_member_segments():
    segments = [_seg(0, 5, "안녕"), _seg(10, 15, "하세요")]

    chunks = chunk_segments(segments, _config(minutes=1, overlap_sec=0))

    assert chunks[0].text == "안녕\n하세요"


def test_zero_duration_segment_on_boundary_is_not_dropped():
    # 0초 세그먼트(start==end)가 오버랩 경계에 놓여도 어느 청크엔 포함돼야 한다.
    segments = [_seg(0, 5), _seg(60, 60, "경계점"), _seg(65, 70)]

    chunks = chunk_segments(segments, _config(minutes=1, overlap_sec=10))

    all_members = [s for c in chunks for s in c.segments]
    assert any(s.start == 60 and s.end == 60 for s in all_members)


def test_unsorted_input_is_sorted_before_chunking():
    # 미정렬 입력이어도 청크 경계가 뒤집히면 안 된다(start <= end, 첫 청크는 0부터).
    segments = [_seg(100, 105, "나중"), _seg(0, 5, "먼저")]

    chunks = chunk_segments(segments, _config(minutes=15, overlap_sec=30))

    assert chunks[0].start <= chunks[0].end
    assert chunks[0].start == 0


def test_long_meeting_splits_into_multiple_chunks():
    # 15분(900s) 윈도우 — 0s, 800s, 1700s 세그먼트는 서로 다른 윈도우
    segments = [_seg(0, 10), _seg(800, 810), _seg(1700, 1710)]

    chunks = chunk_segments(segments, _config(minutes=15, overlap_sec=30))

    assert len(chunks) >= 2
    assert [c.index for c in chunks] == list(range(len(chunks)))
