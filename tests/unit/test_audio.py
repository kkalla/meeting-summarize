"""오디오 변환 단위 테스트: subprocess OSError 의 PipelineError 래핑."""

from __future__ import annotations

import pytest

from src.audio import convert_to_wav
from src.config import AudioConfig
from src.exceptions import PipelineError


def test_convert_to_wav_wraps_oserror_as_pipeline_error(tmp_path, monkeypatch):
    # Arrange: 입력 파일 존재 + ffmpeg 가 PATH 에 있는 것처럼, 단 실행은 OSError
    src_file = tmp_path / "rec.m4a"
    src_file.write_bytes(b"x")
    monkeypatch.setattr("src.audio.shutil.which", lambda _name: "/usr/bin/ffmpeg")

    def boom(*_args, **_kwargs):
        raise OSError("Exec format error")

    monkeypatch.setattr("src.audio.subprocess.run", boom)

    # Act / Assert: 원시 OSError 가 아니라 PipelineError 로 변환되어야 한다
    with pytest.raises(PipelineError):
        convert_to_wav(src_file, tmp_path / "out.wav", AudioConfig(sample_rate=16000, timeout_sec=10))
