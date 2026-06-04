"""CLI 진입점: 폴더 watcher 데몬을 실행한다.

inbox 폴더를 폴링하다가 녹음파일이 올라오면 요약 파이프라인을 자동 실행한다.
podman 컨테이너의 기본 실행 명령으로 쓰인다.

사용 예:
    uv run python scripts/run_watcher.py
    uv run python scripts/run_watcher.py -c configs/pipeline.yaml -v
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from types import FrameType

from src.config import load_config
from src.exceptions import DependencyError
from src.watcher import FolderWatcher

DEFAULT_CONFIG = "configs/pipeline.yaml"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="inbox 폴더를 감시해 새 녹음파일을 자동 요약합니다.",
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
    """watcher 데몬을 실행한다. 정상 종료 시 0, 설정 오류 시 1 을 반환한다."""
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log = logging.getLogger(__name__)

    try:
        config = load_config(args.config)
    except DependencyError as exc:
        log.error("설정 로드 실패: %s", exc)
        return 1

    watcher = FolderWatcher(config, args.config)

    def _handle_signal(signum: int, _frame: FrameType | None) -> None:
        log.info("시그널 %s 수신", signal.Signals(signum).name)
        watcher.request_stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    watcher.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
