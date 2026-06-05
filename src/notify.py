"""macOS 시스템 알림(osascript 래퍼).

watcher 처리 완료/실패를 알림센터로 띄운다. 부가 기능이므로 어떤 실패도(권한 거부,
osascript 부재 등) 파이프라인에 영향을 주지 않게 경고만 남기고 폴백한다.

macOS 전용이라 다른 플랫폼(도커 리눅스 등)에서는 조용히 no-op 한다.
"""

from __future__ import annotations

import logging
import subprocess
import sys

logger = logging.getLogger(__name__)

# osascript 가 멈춰도 watcher 가 매달리지 않도록 짧게 끊는다(알림은 부가 기능).
_NOTIFY_TIMEOUT_SEC = 5


def notify(title: str, message: str, *, sound: str | None = None, enabled: bool = True) -> None:
    """알림센터에 알림을 띄운다.

    Args:
        title: 알림 제목.
        message: 알림 본문.
        sound: 재생할 시스템 사운드 이름(예: "Glass", "Basso"). None 이면 무음.
        enabled: False 면 아무것도 하지 않는다(설정으로 끄기/테스트용).
    """
    if not enabled:
        return
    if sys.platform != "darwin":  # macOS 전용 — 그 외에서는 no-op
        return

    script = f'display notification "{_escape(message)}" with title "{_escape(title)}"'
    if sound:
        script += f' sound name "{_escape(sound)}"'

    try:
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            timeout=_NOTIFY_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("시스템 알림 전송 실패(무시): %s", exc)


def _escape(text: str) -> str:
    """AppleScript 문자열 리터럴용 이스케이프.

    제목/본문에 큰따옴표·백슬래시가 들어가면 AppleScript 구문이 깨지므로 이스케이프하고,
    개행은 공백으로 바꿔 한 줄 알림에 맞춘다(파일명·에러메시지 주입 방어 포함).
    """
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")
