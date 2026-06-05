"""리포트 렌더 단위 테스트."""

from __future__ import annotations

from src.report import ReportMeta, render_report


def _meta() -> ReportMeta:
    return ReportMeta(
        source_file="회의.m4a",
        duration_sec=125.0,
        segment_count=42,
        chunk_count=3,
        generated_at="2026-06-04 10:00:00",
        model="test/model-x",
    )


def test_report_includes_title_and_meta():
    report = render_report("## 핵심 요약\n- 내용", _meta())

    assert report.startswith("# 회의 요약")
    assert "회의.m4a" in report
    assert "2분 5초" in report  # 125초 포맷
    assert "42개" in report
    assert "test/model-x" in report  # 요약 모델명이 헤더에 들어간다
    assert "2026-06-04 10:00:00" in report


def test_report_preserves_summary_body():
    body = "## 핵심 요약\n- A\n\n## 결정 사항\n- B\n\n## 액션 아이템\n- C"

    report = render_report(body, _meta())

    assert "## 핵심 요약" in report
    assert "## 결정 사항" in report
    assert "## 액션 아이템" in report
    assert report.endswith("- C\n")


def test_report_strips_body_whitespace_and_ends_with_newline():
    report = render_report("\n\n본문\n\n", _meta())

    assert report.endswith("본문\n")
    assert not report.endswith("\n\n")
