"""폴더 watcher 단위 테스트: 안정화 판정, 확장자 필터, 처리/실패 이동, 정리."""

from __future__ import annotations

import errno
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.config import WatcherConfig, _build_watcher_config
from src.exceptions import DependencyError, PipelineError
from src.watcher import FolderWatcher


def _write_report(inp: Path, out: Path, cfg: object) -> None:
    """실제 run_pipeline 처럼 비어있지 않은 리포트를 남기는 테스트 더블."""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("# 회의 요약\n", encoding="utf-8")


def _wcfg(tmp_path: Path, stability_checks: int = 2) -> WatcherConfig:
    return WatcherConfig(
        inbox_dir=tmp_path / "inbox",
        processed_dir=tmp_path / "processed",
        failed_dir=tmp_path / "failed",
        output_dir=tmp_path / "output",
        poll_interval_sec=1,
        stability_checks=stability_checks,
        extensions=(".m4a", ".wav"),
    )


@pytest.fixture
def watcher(tmp_path: Path) -> FolderWatcher:
    config = SimpleNamespace(watcher=_wcfg(tmp_path))
    instance = FolderWatcher(config, "configs/pipeline.yaml")
    instance._ensure_dirs()
    return instance


def _touch(path: Path, content: bytes = b"recording") -> Path:
    path.write_bytes(content)
    return path


def test_is_stable_returns_true_after_consecutive_identical_observations(watcher):
    # Arrange
    audio = _touch(watcher._wcfg.inbox_dir / "talk.m4a")

    # Act / Assert: stability_checks=2 -> 첫 관측 False, 둘째 연속 동일 True
    assert watcher._is_stable(audio) is False
    assert watcher._is_stable(audio) is True


def test_stable_file_is_processed_and_moved_to_processed(watcher, monkeypatch):
    # Arrange
    calls: list[tuple] = []

    def _fake_pipeline(inp, out, cfg):
        # 실제 run_pipeline 처럼 리포트를 생성한다(watcher 가 산출물 존재를 검증하므로 필수).
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("# 회의 요약\n", encoding="utf-8")
        calls.append((inp, out, cfg))

    monkeypatch.setattr("src.watcher.run_pipeline", _fake_pipeline)
    audio = _touch(watcher._wcfg.inbox_dir / "meeting.m4a")

    # Act: 첫 스캔은 안정화 미완(skip), 둘째 스캔에서 처리
    watcher._scan_once()
    assert audio.exists()
    watcher._scan_once()

    # Assert
    assert not audio.exists()
    assert (watcher._wcfg.processed_dir / "meeting.m4a").exists()
    assert len(calls) == 1
    assert calls[0][1] == watcher._wcfg.output_dir / "meeting.md"


def test_growing_file_is_not_processed(watcher, monkeypatch):
    # Arrange
    called: list[tuple] = []
    monkeypatch.setattr("src.watcher.run_pipeline", lambda *a: called.append(a))
    audio = _touch(watcher._wcfg.inbox_dir / "uploading.m4a", b"part")

    # Act: 스캔 사이에 크기가 변하면 안정화가 리셋된다
    watcher._scan_once()
    _touch(audio, b"part-plus-more-bytes")
    watcher._scan_once()

    # Assert
    assert called == []
    assert audio.exists()


def test_unsupported_extension_is_ignored(watcher, monkeypatch):
    # Arrange
    called: list[tuple] = []
    monkeypatch.setattr("src.watcher.run_pipeline", lambda *a: called.append(a))
    note = _touch(watcher._wcfg.inbox_dir / "notes.txt")

    # Act
    watcher._scan_once()
    watcher._scan_once()

    # Assert
    assert called == []
    assert note.exists()


def test_pipeline_failure_moves_file_to_failed_and_does_not_raise(watcher, monkeypatch):
    # Arrange
    def boom(*_args):
        raise PipelineError("STT 실패")

    monkeypatch.setattr("src.watcher.run_pipeline", boom)
    audio = _touch(watcher._wcfg.inbox_dir / "bad.m4a")

    # Act
    watcher._scan_once()
    watcher._scan_once()

    # Assert
    assert not audio.exists()
    assert (watcher._wcfg.failed_dir / "bad.m4a").exists()


def test_unexpected_exception_moves_to_failed_and_daemon_survives(watcher, monkeypatch):
    # Arrange: PipelineError 가 아닌 예상치 못한 예외(SDK 버그 등)
    def boom(*_args):
        raise ValueError("예상치 못한 오류")

    monkeypatch.setattr("src.watcher.run_pipeline", boom)
    audio = _touch(watcher._wcfg.inbox_dir / "weird.m4a")

    # Act: 예외가 스캔 루프로 전파되지 않아야 한다(데몬 생존)
    watcher._scan_once()
    watcher._scan_once()

    # Assert
    assert not audio.exists()
    assert (watcher._wcfg.failed_dir / "weird.m4a").exists()


def test_move_appends_timestamp_suffix_on_name_collision(watcher):
    # Arrange: processed 에 동일 이름이 이미 존재
    (watcher._wcfg.processed_dir / "dup.m4a").write_bytes(b"old")
    audio = _touch(watcher._wcfg.inbox_dir / "dup.m4a", b"new")

    # Act
    dest = watcher._move(audio, watcher._wcfg.processed_dir)

    # Assert
    assert dest.name != "dup.m4a"
    assert dest.name.startswith("dup_")
    assert dest.suffix == ".m4a"
    assert not audio.exists()
    assert (watcher._wcfg.processed_dir / "dup.m4a").read_bytes() == b"old"


def test_vanished_file_is_dropped_from_tracking(watcher):
    # Arrange
    audio = _touch(watcher._wcfg.inbox_dir / "gone.m4a")
    watcher._is_stable(audio)
    assert audio in watcher._snapshots

    # Act: 파일이 사라진 뒤 스캔
    audio.unlink()
    watcher._scan_once()

    # Assert
    assert audio not in watcher._snapshots
    assert audio not in watcher._stable_counts


def test_sleep_returns_immediately_after_stop_requested(watcher):
    # Arrange
    watcher.request_stop()

    # Act / Assert: stop 상태면 긴 sleep 도 즉시 반환(블로킹 없음)
    watcher._sleep(3600)
    assert watcher._stop is True


def _watch_raw(**overrides) -> dict:
    base = {
        "inbox_dir": "/data/inbox",
        "processed_dir": "/data/processed",
        "failed_dir": "/data/failed",
        "output_dir": "/data/output",
        "poll_interval_sec": 10,
        "stability_checks": 2,
        "extensions": [".m4a"],
    }
    base.update(overrides)
    return base


def test_build_watcher_config_rejects_zero_poll_interval():
    # Act / Assert: poll=0 은 busy-spin 이므로 거부
    with pytest.raises(DependencyError):
        _build_watcher_config(_watch_raw(poll_interval_sec=0))


def test_build_watcher_config_rejects_non_positive_stability_checks():
    with pytest.raises(DependencyError):
        _build_watcher_config(_watch_raw(stability_checks=0))


def test_build_watcher_config_rejects_empty_extensions():
    with pytest.raises(DependencyError):
        _build_watcher_config(_watch_raw(extensions=[]))


def test_empty_output_moves_file_to_failed(watcher, monkeypatch):
    # run_pipeline 이 예외 없이 반환했지만 리포트를 만들지 못한 경우 → processed 가 아닌 failed.
    monkeypatch.setattr("src.watcher.run_pipeline", lambda inp, out, cfg: None)
    audio = _touch(watcher._wcfg.inbox_dir / "silent.m4a")

    watcher._scan_once()
    watcher._scan_once()

    assert not audio.exists()
    assert (watcher._wcfg.failed_dir / "silent.m4a").exists()
    assert not (watcher._wcfg.processed_dir / "silent.m4a").exists()


def test_permanent_move_failure_quarantines_and_stops_reprocessing(watcher, monkeypatch):
    pipeline_calls: list[Path] = []

    def _fake_pipeline(inp, out, cfg):
        _write_report(inp, out, cfg)
        pipeline_calls.append(inp)

    def _raise_rofs(path, dest_dir):
        raise OSError(errno.EROFS, "read-only file system")

    monkeypatch.setattr("src.watcher.run_pipeline", _fake_pipeline)
    monkeypatch.setattr(watcher, "_move", _raise_rofs)
    audio = _touch(watcher._wcfg.inbox_dir / "stuck.m4a")

    # 안정화 후 처리 → 이동 영구 실패 → 격리
    watcher._scan_once()
    watcher._scan_once()
    assert audio.exists()  # 이동 실패로 inbox 에 남음
    assert audio in watcher._quarantined

    # 격리된 파일은 이후 스캔에서 재처리되지 않는다(STT+요약 무한 반복 차단).
    watcher._scan_once()
    watcher._scan_once()
    assert len(pipeline_calls) == 1


def test_move_falls_back_to_shutil_on_exdev(watcher, monkeypatch):
    src = _touch(watcher._wcfg.inbox_dir / "x.m4a")

    def _raise_exdev(self, target):
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(Path, "replace", _raise_exdev)
    dest = watcher._move(src, watcher._wcfg.processed_dir)

    assert dest.exists() and not src.exists()  # shutil.move 폴백 성공


def test_move_reraises_non_exdev_oserror(watcher, monkeypatch):
    src = _touch(watcher._wcfg.inbox_dir / "y.m4a")

    def _raise_eperm(self, target):
        raise OSError(errno.EPERM, "operation not permitted")

    monkeypatch.setattr(Path, "replace", _raise_eperm)

    with pytest.raises(OSError) as exc_info:
        watcher._move(src, watcher._wcfg.processed_dir)
    assert exc_info.value.errno == errno.EPERM  # 원인이 가려지지 않고 그대로 전파


def test_watcher_config_post_init_validates_on_direct_construction(tmp_path):
    # _build_watcher_config 를 거치지 않는 직접 생성도 불변식을 강제한다.
    with pytest.raises(DependencyError):
        WatcherConfig(
            inbox_dir=tmp_path,
            processed_dir=tmp_path,
            failed_dir=tmp_path,
            output_dir=tmp_path,
            poll_interval_sec=0,
            stability_checks=2,
            extensions=(".m4a",),
        )
