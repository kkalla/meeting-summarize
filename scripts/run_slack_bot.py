"""CLI 진입점: Slack 회의 요약 봇을 Socket Mode 로 실행한다.

로컬 CLI 파이프라인(``scripts/run_pipeline.py``)과 별개인 부가 진입점이다. 별도 프로세스로
계속 떠 있으면서 Slack DM 으로 들어오는 오디오 파일을 자동으로 전사+요약한다.

사용 예:
    uv run python scripts/run_slack_bot.py
"""

from __future__ import annotations

import logging
import sys

from src.exceptions import DependencyError
from src.slack_bot.bot import run

if __name__ == "__main__":
    try:
        run()
    except DependencyError as exc:
        logging.getLogger(__name__).error("설정 오류: %s", exc)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(0)
