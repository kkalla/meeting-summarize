"""시스템 알림(osascript 래퍼) 단위 테스트: 플랫폼 분기·enabled·이스케이프·실패 무해."""

from __future__ import annotations

from src import notify as notify_mod


def _capture_run(monkeypatch) -> list:
    """subprocess.run 을 가로채 호출 인자를 모은다."""
    calls: list = []
    monkeypatch.setattr(notify_mod.subprocess, "run", lambda args, **kw: calls.append(args))
    return calls


def test_disabled_does_nothing(monkeypatch):
    monkeypatch.setattr(notify_mod.sys, "platform", "darwin")
    calls = _capture_run(monkeypatch)

    notify_mod.notify("제목", "본문", enabled=False)

    assert calls == []


def test_non_darwin_is_noop(monkeypatch):
    monkeypatch.setattr(notify_mod.sys, "platform", "linux")
    calls = _capture_run(monkeypatch)

    notify_mod.notify("제목", "본문", enabled=True)

    assert calls == []


def test_darwin_invokes_osascript_with_title_and_message(monkeypatch):
    monkeypatch.setattr(notify_mod.sys, "platform", "darwin")
    calls = _capture_run(monkeypatch)

    notify_mod.notify("제목", "본문", sound="Glass", enabled=True)

    assert len(calls) == 1
    args = calls[0]
    assert args[0] == "osascript"
    assert args[1] == "-e"
    script = args[2]
    assert 'display notification "본문"' in script
    assert 'with title "제목"' in script
    assert 'sound name "Glass"' in script


def test_no_sound_omits_sound_clause(monkeypatch):
    monkeypatch.setattr(notify_mod.sys, "platform", "darwin")
    calls = _capture_run(monkeypatch)

    notify_mod.notify("t", "m", enabled=True)

    assert "sound name" not in calls[0][2]


def test_escapes_quotes_and_newlines(monkeypatch):
    # 큰따옴표는 이스케이프, 개행은 공백으로 — AppleScript 구문 깨짐/주입 방지.
    monkeypatch.setattr(notify_mod.sys, "platform", "darwin")
    calls = _capture_run(monkeypatch)

    notify_mod.notify('제"목', 'a"b\nc', enabled=True)

    script = calls[0][2]
    assert '\\"' in script  # 큰따옴표 이스케이프됨
    assert "\n" not in script  # 개행 제거됨


def test_osascript_failure_is_swallowed(monkeypatch):
    # osascript 부재/권한 거부 등은 예외를 전파하지 않고 삼켜야 한다(부가 기능).
    monkeypatch.setattr(notify_mod.sys, "platform", "darwin")

    def boom(*args, **kwargs):
        raise OSError("osascript 없음")

    monkeypatch.setattr(notify_mod.subprocess, "run", boom)

    notify_mod.notify("t", "m", enabled=True)  # 예외 없이 반환되면 성공


def test_timeout_is_swallowed(monkeypatch):
    import subprocess

    monkeypatch.setattr(notify_mod.sys, "platform", "darwin")

    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="osascript", timeout=5)

    monkeypatch.setattr(notify_mod.subprocess, "run", slow)

    notify_mod.notify("t", "m", enabled=True)  # 예외 없이 반환
