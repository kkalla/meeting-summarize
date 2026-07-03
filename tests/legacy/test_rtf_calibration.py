"""RTF 자동 보정 단위 테스트: 로드/EMA 누적/원자적 저장/손상 폴백."""

from __future__ import annotations

import json

import pytest

from legacy import rtf_calibration


@pytest.fixture
def state_file(tmp_path):
    return rtf_calibration.state_path(tmp_path)


def test_load_returns_fallback_when_no_state(state_file):
    # Arrange / Act
    rtf = rtf_calibration.load_rtf(state_file, fallback=0.6)

    # Assert
    assert rtf == 0.6


def test_first_update_seeds_with_observed_value(state_file):
    # Arrange / Act
    updated = rtf_calibration.update_rtf(state_file, observed_rtf=2.8, fallback=0.6)

    # Assert: 첫 샘플은 EMA 평활 없이 실측값 그대로 시드된다.
    assert updated == pytest.approx(2.8)
    assert rtf_calibration.load_rtf(state_file, fallback=0.6) == pytest.approx(2.8)


def test_second_update_applies_ema_smoothing(state_file):
    # Arrange
    rtf_calibration.update_rtf(state_file, observed_rtf=2.0, fallback=0.6)

    # Act: 두 번째 실측은 EMA(alpha=0.3)로 섞인다.
    updated = rtf_calibration.update_rtf(state_file, observed_rtf=4.0, fallback=0.6)

    # Assert: 0.3*4.0 + 0.7*2.0 = 2.6
    assert updated == pytest.approx(2.6)


def test_update_persists_sample_count(state_file):
    # Arrange / Act
    rtf_calibration.update_rtf(state_file, observed_rtf=2.0, fallback=0.6)
    rtf_calibration.update_rtf(state_file, observed_rtf=3.0, fallback=0.6)

    # Assert
    raw = json.loads(state_file.read_text(encoding="utf-8"))
    assert raw["samples"] == 2


@pytest.mark.parametrize("observed", [0.0, -1.0])
def test_non_positive_observed_does_not_corrupt_state(state_file, observed):
    # Arrange
    rtf_calibration.update_rtf(state_file, observed_rtf=2.8, fallback=0.6)

    # Act: 비정상 실측값(0/음수)은 무시되고 기존 누적값이 보존된다.
    result = rtf_calibration.update_rtf(state_file, observed_rtf=observed, fallback=0.6)

    # Assert
    assert result == pytest.approx(2.8)
    assert rtf_calibration.load_rtf(state_file, fallback=0.6) == pytest.approx(2.8)


def test_corrupt_state_falls_back(state_file):
    # Arrange: 깨진 JSON.
    state_file.write_text("{not valid json", encoding="utf-8")

    # Act / Assert
    assert rtf_calibration.load_rtf(state_file, fallback=0.6) == 0.6


def test_corrupt_state_reseeds_on_update(state_file):
    # Arrange
    state_file.write_text("garbage", encoding="utf-8")

    # Act: 손상 상태에서 update 하면 폴백 없이 실측값으로 새로 시드한다.
    updated = rtf_calibration.update_rtf(state_file, observed_rtf=3.5, fallback=0.6)

    # Assert
    assert updated == pytest.approx(3.5)


def test_non_positive_stored_rtf_falls_back(state_file):
    # Arrange: 비정상 저장값(<=0).
    state_file.write_text(json.dumps({"rtf": 0.0, "samples": 5}), encoding="utf-8")

    # Act / Assert
    assert rtf_calibration.load_rtf(state_file, fallback=0.6) == 0.6


def test_state_filename_not_json_suffix():
    # 전사 캐시(*.json) purge glob 에 보정 상태가 휩쓸리지 않도록 .json 확장자를 피한다.
    assert not rtf_calibration.STATE_FILENAME.endswith(".json")
