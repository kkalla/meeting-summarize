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
    # 앱 로그는 stdout 으로 보낸다. logging 기본은 stderr 라, 그대로 두면 launchd 의
    # StandardErrorPath(watcher.err.log)에 INFO 까지 전부 쌓이고 watcher.log 는 빈다.
    # 이렇게 하면 watcher.log = 앱 로그 전부, watcher.err.log = 인터프리터 크래시 등 진짜 비정상.
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    log = logging.getLogger(__name__)

    try:
        config = load_config(args.config)
    except DependencyError as exc:
        log.error("설정 로드 실패: %s", exc)
        return 1
    except Exception:  # noqa: BLE001 - YAMLError 등 비-DependencyError 도 raw traceback 없이 종료
        # 설정 파싱 실패가 그대로 죽으면 restart: unless-stopped 가 즉시 재시작을 반복한다.
        log.exception("설정 로드 중 예상치 못한 오류")
        return 1

    watcher = FolderWatcher(config, args.config)

    def _handle_signal(signum: int, _frame: FrameType | None) -> None:
        log.info("시그널 %s 수신", signal.Signals(signum).name)
        watcher.request_stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        watcher.run()
    except Exception:  # noqa: BLE001 - _ensure_dirs mkdir(볼륨 권한) 등 시작 실패를 깔끔히 종료
        # 그대로 전파시키면 컨테이너가 비정상 종료 → restart 정책상 무한 재시작한다.
        log.exception("watcher 실행 중 치명적 오류")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
