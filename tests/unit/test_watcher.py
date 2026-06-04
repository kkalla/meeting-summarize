"""폴더 watcher 단위 테스트: 안정화 판정, 확장자 필터, 처리/실패 이동, 정리."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.config import WatcherConfig, _build_watcher_config
from src.exceptions import DependencyError, PipelineError
from src.watcher import FolderWatcher


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
    monkeypatch.setattr("src.watcher.run_pipeline", lambda inp, out, cfg: calls.append((inp, out, cfg)))
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
