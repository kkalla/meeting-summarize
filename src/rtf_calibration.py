"""전사 RTF(real-time factor) 자동 보정.

전사할 때마다 실측 RTF 를 EMA(지수이동평균)로 누적해 다음 전사의 ETA 추정에 쓴다.
하드웨어/모델이 바뀌어도 첫 전사 이후부터 "예상 ~N초" 가 실제 소요시간에 수렴한다.

보정값은 시작 로그의 ETA 표시 전용이라 전사 동작/정확성과는 무관하다. 따라서 상태
파일 IO 가 어떤 식으로 실패해도 전사를 막지 않고 설정값(stt.rtf_estimate)으로 폴백한다.
"""

from __future__ import annotations

import contextlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# EMA 평활 계수: 새 실측에 부여하는 가중치. 작을수록 과거값에 둔감(안정적), 클수록 최근
# 하드웨어/부하 변화에 빠르게 반응. 0.3 은 한 번 튀는 값(콜드 캐시, 일시적 CPU 경합)에
# 휘둘리지 않으면서 환경 변화엔 몇 번 안에 수렴하는 절충값.
_EMA_ALPHA = 0.3

# 내용은 JSON 이지만 확장자를 ``.json`` 으로 두지 않는다 — 전사 캐시(``*.json``)와 같은
# 디렉토리를 쓰므로, cache.purge_expired 의 ``*.json`` glob 에 보정 상태가 휩쓸려
# TTL 마다 리셋되는 것을 막는다.
STATE_FILENAME = ".rtf_state"


def state_path(transcripts_dir: Path) -> Path:
    """RTF 보정 상태 파일 경로. 전사 캐시와 같은 디렉토리에 둔다."""
    return transcripts_dir / STATE_FILENAME


def load_rtf(path: Path, fallback: float) -> float:
    """보정된 RTF 를 읽는다. 상태가 없거나 깨졌으면 ``fallback`` 을 쓴다."""
    prev = _read_state(path)
    return prev[0] if prev is not None else fallback


def update_rtf(path: Path, observed_rtf: float, fallback: float) -> float:
    """실측 RTF 를 EMA 로 누적·저장하고 갱신된 값을 반환한다.

    저장 실패는 다음 전사의 ETA 정확도에만 영향을 줄 뿐 전사 자체와 무관하므로,
    예외를 전파하지 않고 경고만 남긴 뒤 계산된 값을 그대로 반환한다.

    Args:
        path: 상태 파일 경로.
        observed_rtf: 이번 전사의 실측 RTF(<=0 이면 무시하고 기존값/폴백 반환).
        fallback: 유효한 누적값이 없을 때 쓸 설정값(파일 없음/손상, 또는
            observed_rtf<=0 인데 기존 상태도 없을 때).
    """
    if observed_rtf <= 0.0:
        return load_rtf(path, fallback)

    prev = _read_state(path)
    if prev is None:
        updated, samples = observed_rtf, 1  # 첫 샘플은 그대로 시드
    else:
        updated = _EMA_ALPHA * observed_rtf + (1.0 - _EMA_ALPHA) * prev[0]
        samples = prev[1] + 1

    _write_state(path, updated, samples)
    return updated


def _read_state(path: Path) -> tuple[float, int] | None:
    """저장된 ``(rtf, samples)`` 를 읽는다. 없거나 손상됐으면 None."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        rtf = float(raw["rtf"])
        samples = int(raw.get("samples", 1))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        logger.warning("RTF 보정 상태가 손상돼 설정값으로 폴백합니다: %s (%s)", path, exc)
        return None
    if rtf <= 0.0:
        logger.warning("RTF 보정 상태값이 비정상(%.3f)이라 설정값으로 폴백합니다: %s", rtf, path)
        return None
    return rtf, samples


def _write_state(path: Path, rtf: float, samples: int) -> None:
    """상태를 원자적으로 저장한다(POSIX 로컬 FS 기준 — 부분 쓰기가 안 보이게 tmp→rename).

    rtf<=0 같은 비정상 값은 저장하지 않는다. 읽기(_read_state)가 거르는 불변식을
    쓰기 측에서도 강제해, 향후 다른 호출자가 잘못된 상태를 영속화하지 못하게 한다.
    """
    if rtf <= 0.0:
        logger.warning("비정상 RTF(%.3f)는 저장하지 않습니다: %s", rtf, path)
        return
    payload = {"rtf": round(rtf, 4), "samples": samples}
    tmp = path.with_name(f"{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        logger.warning("RTF 보정 상태 저장 실패(ETA 정확도에만 영향): %s (%s)", path, exc)
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
