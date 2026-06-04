"""요약 본문 + 메타데이터를 최종 Markdown 리포트로 렌더하는 순수 로직."""

from __future__ import annotations

from dataclasses import dataclass

SEC_PER_MIN = 60


@dataclass(frozen=True)
class ReportMeta:
    """리포트 헤더에 들어가는 메타데이터.

    Attributes:
        source_file: 원본 녹음 파일명/경로.
        duration_sec: 회의 길이(초).
        segment_count: 전사 세그먼트 수.
        chunk_count: 요약 청크 수.
        generated_at: 생성 시각 문자열(호출자가 주입).
    """

    source_file: str
    duration_sec: float
    segment_count: int
    chunk_count: int
    generated_at: str


def render_report(summary_body: str, meta: ReportMeta) -> str:
    """요약 본문 위에 제목·메타 헤더를 붙여 최종 Markdown 을 만든다.

    Args:
        summary_body: LLM 이 생성한 3섹션(핵심/결정/액션) Markdown 본문.
        meta: 헤더 메타데이터.

    Returns:
        완성된 Markdown 리포트 문자열(끝에 개행 1개).
    """
    header = "\n".join(
        [
            "# 회의 요약",
            "",
            f"- **원본**: {meta.source_file}",
            f"- **길이**: {_format_duration(meta.duration_sec)}",
            f"- **세그먼트**: {meta.segment_count}개 / **청크**: {meta.chunk_count}개",
            f"- **생성**: {meta.generated_at}",
            "",
            "---",
            "",
        ]
    )
    return f"{header}{summary_body.strip()}\n"


def _format_duration(duration_sec: float) -> str:
    """초를 ``MM분 SS초`` 형태로 포맷한다."""
    total = int(round(duration_sec))
    minutes, seconds = divmod(total, SEC_PER_MIN)
    return f"{minutes}분 {seconds}초"
