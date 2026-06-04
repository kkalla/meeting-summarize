"""폴더 watcher: inbox 를 폴링해 안정화된 녹음파일을 파이프라인에 흘려보낸다.

podman 컨테이너 안에서 데몬으로 동작한다. 새 파일이 업로드되면(크기 안정화로
완료 판정) 기존 :func:`~src.pipeline.run_pipeline` 을 그대로 호출하고, 결과에 따라
원본을 ``processed/`` 또는 ``failed/`` 로 옮긴다. 한 번에 한 파일씩 순차 처리한다.
"""

from __future__ import annotations

import errno
import logging
import shutil
import time
from datetime import datetime
from pathlib import Path

from src.config import PipelineConfig, WatcherConfig
from src.exceptions import PipelineError
from src.pipeline import run_pipeline

logger = logging.getLogger(__name__)

REPORT_SUFFIX = ".md"
# stop 신호에 빠르게 반응하도록 sleep 을 이 간격(초)으로 잘게 나눈다.
SLEEP_SLICE_SEC = 1


class FolderWatcher:
    """inbox 폴더를 폴링하며 안정화된 녹음파일을 처리하는 watcher.

    Attributes:
        config: 파이프라인 전체 설정(파이프라인 호출에 재사용).
        config_path: ``run_pipeline`` 에 넘길 설정 YAML 경로.

    Example:
        >>> watcher = FolderWatcher(load_config(), "configs/pipeline.yaml")
        >>> watcher.run()  # SIGINT 까지 블로킹
    """

    def __init__(self, config: PipelineConfig, config_path: str) -> None:
        self._config = config
        self._wcfg: WatcherConfig = config.watcher
        self._config_path = config_path
        # 파일별 직전 (size, mtime) 스냅샷과 연속 동일 횟수.
        self._snapshots: dict[Path, tuple[int, float]] = {}
        self._stable_counts: dict[Path, int] = {}
        # 이동이 영구 실패(읽기전용/권한/디스크풀)해 inbox 에 남은 파일. 재처리를 막는다.
        self._quarantined: set[Path] = set()
        self._stop = False

    def request_stop(self) -> None:
        """다음 안전 지점에서 폴링 루프를 멈추도록 요청한다(시그널 핸들러용)."""
        logger.info("종료 신호 수신 — 현재 작업을 마치고 멈춥니다")
        self._stop = True

    def run(self) -> None:
        """폴링 루프를 시작한다. :meth:`request_stop` 전까지 블로킹한다."""
        self._ensure_dirs()
        logger.info(
            "watcher 시작: inbox=%s 폴링=%ds 안정화=%d회",
            self._wcfg.inbox_dir,
            self._wcfg.poll_interval_sec,
            self._wcfg.stability_checks,
        )
        while not self._stop:
            try:
                self._scan_once()
            except OSError as exc:
                logger.error("inbox 스캔 실패: %s", exc)
            self._sleep(self._wcfg.poll_interval_sec)
        logger.info("watcher 종료")

    def _ensure_dirs(self) -> None:
        """watcher 가 쓰는 디렉토리 4종을 미리 만든다."""
        for directory in (
            self._wcfg.inbox_dir,
            self._wcfg.processed_dir,
            self._wcfg.failed_dir,
            self._wcfg.output_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def _scan_once(self) -> None:
        """inbox 를 한 번 스캔해 안정화된 파일을 순차 처리한다."""
        files = self._list_candidates()
        self._forget_vanished(files)
        for path in files:
            if self._stop:
                break
            try:
                if self._is_stable(path):
                    self._process_file(path)
            except OSError as exc:
                # 스캔 도중 파일이 사라지거나 접근 불가 — 다음 파일로 넘어간다.
                logger.warning("파일 접근 실패, 건너뜀: %s (%s)", path.name, exc)

    def _list_candidates(self) -> list[Path]:
        """inbox 안의 대상 확장자 파일을 이름순으로 반환한다."""
        exts = self._wcfg.extensions
        candidates: list[Path] = []
        for path in self._wcfg.inbox_dir.iterdir():
            if not path.is_file():
                continue
            if path.suffix.lower() not in exts:
                logger.debug("대상 아닌 확장자, 무시: %s", path.name)
                continue
            if path in self._quarantined:
                # 이동 영구 실패로 격리된 파일 — 운영자가 치울 때까지 재처리하지 않는다.
                continue
            candidates.append(path)
        return sorted(candidates)

    def _forget_vanished(self, current: list[Path]) -> None:
        """더 이상 inbox 에 없는 파일의 추적 상태를 정리한다(메모리 누수 방지)."""
        present = set(current)
        for tracked in [p for p in self._snapshots if p not in present]:
            self._snapshots.pop(tracked, None)
            self._stable_counts.pop(tracked, None)
        # 격리 파일이 inbox 에서 치워졌으면 격리 목록에서도 잊는다(메모리 누수 방지).
        # 격리 파일은 _list_candidates 에서 제외돼 `present` 에 절대 없으므로, 후보목록이
        # 아니라 실제 파일 존재로 판단해야 한다(아니면 매 스캔 즉시 해제돼 재처리 반복).
        for gone in [p for p in self._quarantined if not p.exists()]:
            self._quarantined.discard(gone)

    def _is_stable(self, path: Path) -> bool:
        """``(size, mtime)`` 가 연속 ``stability_checks`` 회 동일한지 판정한다.

        Args:
            path: 검사할 파일 경로.

        Returns:
            업로드가 끝나 처리해도 되는 상태면 ``True``.
        """
        stat = path.stat()
        current = (stat.st_size, stat.st_mtime)
        previous = self._snapshots.get(path)
        self._snapshots[path] = current
        if previous == current:
            self._stable_counts[path] = self._stable_counts.get(path, 1) + 1
        else:
            self._stable_counts[path] = 1
        return self._stable_counts[path] >= self._wcfg.stability_checks

    def _process_file(self, path: Path) -> None:
        """파일 하나를 파이프라인에 통과시키고 결과에 따라 이동한다."""
        output_path = self._wcfg.output_dir / f"{path.stem}{REPORT_SUFFIX}"
        logger.info("처리 시작: %s", path.name)
        try:
            run_pipeline(path, output_path, self._config_path)
        except (PipelineError, FileNotFoundError) as exc:
            # 예상된 도메인 실패(전사/요약/입력 누락). 파일만 failed 로 옮기고 계속 진행.
            logger.error("처리 실패: %s (%s)", path.name, exc)
            self._relocate(path, self._wcfg.failed_dir, "실패")
        except Exception:  # noqa: BLE001 - 데몬 생존이 우선: 어떤 파일도 루프를 죽이면 안 됨
            # 예상치 못한 예외(SDK 오류 등)도 삼키지 말고 traceback 을 남긴 뒤 failed 로.
            # 그대로 전파시키면 스캔 루프가 죽고 restart 정책상 같은 파일을 무한 재시도한다.
            logger.exception("예상치 못한 오류로 처리 실패: %s", path.name)
            self._relocate(path, self._wcfg.failed_dir, "실패")
        else:
            # run_pipeline 이 조용히 빈/누락 리포트를 남겼을 수 있으므로 산출물을 검증한다.
            # 검증 없이 processed 로 옮기면 원본이 inbox 에서 사라져 복구·재처리가 불가능하다.
            if self._output_ok(output_path):
                self._relocate(path, self._wcfg.processed_dir, "완료", report=output_path)
            else:
                logger.error("출력 리포트가 없거나 비어 있음: %s — failed 로 격리", output_path)
                self._relocate(path, self._wcfg.failed_dir, "실패(빈 출력)")
        finally:
            self._snapshots.pop(path, None)
            self._stable_counts.pop(path, None)

    def _output_ok(self, output_path: Path) -> bool:
        """파이프라인 산출물이 실제로 생성됐고 비어 있지 않은지 검사한다."""
        try:
            return output_path.is_file() and output_path.stat().st_size > 0
        except OSError:
            return False

    def _relocate(self, path: Path, dest_dir: Path, label: str, *, report: Path | None = None) -> None:
        """파일을 옮기고 결과를 로깅한다. 이동이 영구 실패하면 격리한다.

        이동이 OSError 로 실패하면(읽기전용 마운트/권한/디스크풀) 파일은 inbox 에 남는다.
        이때 격리하지 않으면 다음 스캔에서 다시 추적·재처리되어 STT+요약을 무한 반복한다.
        """
        try:
            dest = self._move(path, dest_dir)
        except OSError as exc:
            self._quarantined.add(path)
            logger.error("파일 이동 실패 — 격리(재처리 안 함): %s -> %s (%s)", path.name, dest_dir, exc)
            return
        if report is not None:
            logger.info("처리 완료: %s -> %s (리포트: %s)", path.name, dest, report)
        else:
            logger.info("%s 파일 이동: %s -> %s", label, path.name, dest)

    def _move(self, path: Path, dest_dir: Path) -> Path:
        """``path`` 를 ``dest_dir`` 로 옮긴다. 이름 충돌 시 타임스탬프 suffix 를 붙인다.

        Args:
            path: 옮길 원본 파일.
            dest_dir: 목적지 디렉토리.

        Returns:
            실제로 옮겨진 최종 경로.

        Raises:
            OSError: 이동이 실패할 때(교차 파일시스템 외 사유는 그대로 전파).
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / path.name
        if dest.exists():
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            dest = dest_dir / f"{path.stem}_{timestamp}{path.suffix}"
        # Path.replace 로 같은 파일시스템 내 원자적 이동을 시도하고, 교차 파일시스템
        # (볼륨 마운트 경계, EXDEV)일 때만 복사+삭제로 폴백한다. 그 외 OSError
        # (ENOSPC/EPERM/EROFS 등)는 원인이 로그에 남도록 그대로 전파한다.
        try:
            path.replace(dest)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            shutil.move(str(path), str(dest))
        return dest

    def _sleep(self, seconds: int) -> None:
        """``seconds`` 만큼 자되, stop 신호에 빠르게 반응하도록 잘게 나눈다."""
        for _ in range(max(0, seconds)):
            if self._stop:
                return
            time.sleep(SLEEP_SLICE_SEC)
