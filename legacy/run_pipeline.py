"""CLI 진입점: 녹음 파일을 받아 요약 Markdown 리포트를 생성한다.

**legacy**: 로컬 whisper.cpp 기반 CLI. 현재 주력 사용법은 Slack 봇
(``scripts/run_slack_bot.py``)이며, 이 스크립트는 그게 필요 없는 경우를 위해 보존돼 있다.

사용 예:
    uv run python legacy/run_pipeline.py 회의녹음.m4a --output out.md
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from legacy.pipeline import run_pipeline
from src.exceptions import PipelineError

DEFAULT_OUTPUT = "out.md"
DEFAULT_CONFIG = "configs/pipeline.yaml"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="로컬 STT + OpenRouter 로 회의 녹음을 요약합니다.",
    )
    parser.add_argument("input", type=Path, help="입력 녹음 파일 (.m4a/.wav 등)")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path(DEFAULT_OUTPUT),
        help=f"출력 Markdown 리포트 경로 (기본: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "-c",
        "--config",
        default=DEFAULT_CONFIG,
        help=f"파이프라인 설정 YAML 경로 (기본: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="디버그 로그 출력",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI 실행. 성공 시 0, 실패 시 1 을 반환한다."""
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        output = run_pipeline(args.input, args.output, args.config)
    except (PipelineError, FileNotFoundError) as exc:
        logging.getLogger(__name__).error("실패: %s", exc)
        return 1

    logging.getLogger(__name__).info("요약 리포트 생성 완료: %s", output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
