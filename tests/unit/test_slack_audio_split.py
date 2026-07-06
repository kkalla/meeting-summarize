"""오디오 분할(``src.slack_bot.audio_split``) 단위 테스트.

실제 ffmpeg/ffprobe 를 부르지 않도록 ``subprocess.run``/``shutil.which`` 를 모킹한다
(legacy/audio.py 테스트와 동일한 패턴).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.exceptions import DependencyError
from src.slack_bot.audio_split import probe_duration_sec, split_audio

# --- probe_duration_sec ------------------------------------------------------


def test_probe_duration_sec_raises_when_ffprobe_missing(monkeypatch):
    monkeypatch.setattr("src.slack_bot.audio_split.shutil.which", lambda _name: None)

    with pytest.raises(DependencyError):
        probe_duration_sec(b"fake-audio", ".m4a")


def test_probe_duration_sec_returns_parsed_float(monkeypatch):
    monkeypatch.setattr("src.slack_bot.audio_split.shutil.which", lambda _name: "/usr/bin/ffprobe")
    monkeypatch.setattr(
        "src.slack_bot.audio_split.subprocess.run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout="125.5\n", stderr=""),
    )

    assert probe_duration_sec(b"fake-audio", ".m4a") == 125.5


def test_probe_duration_sec_raises_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr("src.slack_bot.audio_split.shutil.which", lambda _name: "/usr/bin/ffprobe")
    monkeypatch.setattr(
        "src.slack_bot.audio_split.subprocess.run",
        lambda *a, **kw: SimpleNamespace(returncode=1, stdout="", stderr="no such file"),
    )

    with pytest.raises(DependencyError):
        probe_duration_sec(b"fake-audio", ".m4a")


def test_probe_duration_sec_raises_on_unparsable_output(monkeypatch):
    monkeypatch.setattr("src.slack_bot.audio_split.shutil.which", lambda _name: "/usr/bin/ffprobe")
    monkeypatch.setattr(
        "src.slack_bot.audio_split.subprocess.run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout="N/A\n", stderr=""),
    )

    with pytest.raises(DependencyError):
        probe_duration_sec(b"fake-audio", ".m4a")


# --- split_audio --------------------------------------------------------------


def test_split_audio_returns_original_bytes_unsplit_when_short():
    segments = split_audio(
        b"fake-audio",
        input_extension=".m4a",
        output_extension=".m4a",
        duration_sec=300.0,
        segment_sec=900,
        overlap_sec=30,
    )

    assert len(segments) == 1
    assert segments[0].data == b"fake-audio"
    assert segments[0].start_sec == 0.0


def test_split_audio_raises_when_ffmpeg_missing_for_long_audio(monkeypatch):
    monkeypatch.setattr("src.slack_bot.audio_split.shutil.which", lambda _name: None)

    with pytest.raises(DependencyError):
        split_audio(
            b"fake-audio",
            input_extension=".m4a",
            output_extension=".m4a",
            duration_sec=2700.0,
            segment_sec=900,
            overlap_sec=30,
        )


def test_split_audio_writes_one_segment_file_per_window(monkeypatch):
    monkeypatch.setattr("src.slack_bot.audio_split.shutil.which", lambda _name: "/usr/bin/ffmpeg")

    def fake_run(cmd, **kw):
        output_path = cmd[-1]
        with open(output_path, "wb") as fp:
            fp.write(f"data-for-{output_path}".encode())
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("src.slack_bot.audio_split.subprocess.run", fake_run)

    segments = split_audio(
        b"fake-audio",
        input_extension=".m4a",
        output_extension=".m4a",
        duration_sec=2000.0,  # segment_sec(900) 기준 3구간: 0, 900, 1800
        segment_sec=900,
        overlap_sec=30,
    )

    assert [s.start_sec for s in segments] == [0.0, 900.0, 1800.0]
    assert all(s.data for s in segments)


def test_split_audio_raises_when_ffmpeg_exits_nonzero(monkeypatch):
    monkeypatch.setattr("src.slack_bot.audio_split.shutil.which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        "src.slack_bot.audio_split.subprocess.run",
        lambda cmd, **kw: SimpleNamespace(returncode=1, stdout="", stderr="boom"),
    )

    with pytest.raises(DependencyError):
        split_audio(
            b"fake-audio",
            input_extension=".m4a",
            output_extension=".m4a",
            duration_sec=2000.0,
            segment_sec=900,
            overlap_sec=30,
        )
