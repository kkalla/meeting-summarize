"""Slack DM 오디오 업로드 → OpenRouter STT 전사 → 기존 요약 파이프라인 재사용 → Markdown 리포트.

전사만 OpenRouter STT 로 바꿔치기하고, 요약(``src.summarize.summarize_meeting``)과 리포트
렌더링(``src.report.render_report``)은 로컬 CLI 파이프라인(``src.pipeline``)과 동일한
프롬프트/설정(``configs/pipeline.yaml`` 의 ``summarize`` 섹션)을 그대로 재사용한다 — 요약
로직을 이중으로 유지하지 않기 위함이다.

OpenRouter STT 는 세그먼트 단위 타임스탬프를 주지 않으므로, 구간(오디오 분할 단위)마다
텍스트 하나짜리 청크로 감싼다(``chunk_segments`` 를 거치지 않는다). 짧은 오디오(청크 1개)는
``summarize_meeting`` 이 자동으로 single-shot 분기를 타 Map 단계를 건너뛰고, 긴 오디오는
``src.slack_bot.audio_split`` 이 여러 구간으로 잘라 청크가 여러 개가 되면서 자연히
기존 Map-Reduce 경로를 재사용한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

from src.chunking import Chunk
from src.config import PROJECT_ROOT, load_config
from src.exceptions import DependencyError, SlackBotError
from src.report import ReportMeta, render_report
from src.slack_bot.audio_split import probe_duration_sec, split_audio
from src.slack_bot.config import SlackBotConfig
from src.slack_bot.openrouter_stt import transcribe_audio
from src.summarize import summarize_meeting

logger = logging.getLogger(__name__)

PROMPTS_DIR = PROJECT_ROOT / "prompts"
MAP_PROMPT = PROMPTS_DIR / "map_summary.txt"
REDUCE_PROMPT = PROMPTS_DIR / "reduce_summary.txt"
REPORT_SUFFIX = ".md"
# Slack 다운로드 전용 타임아웃(초). 회의 녹음은 수십~수백 MB 일 수 있어 넉넉히 둔다.
DOWNLOAD_TIMEOUT_SEC = 120
# STT 로 보내기 전 항상 이 포맷으로 재인코딩한다(src.slack_bot.audio_split.split_audio 가
# 담당). OpenRouter STT 문서는 m4a/aac 도 지원한다고 하지만 실제 프로바이더는 m4a 에
# 422 를 반환한다(2026-07-09 재현 확인) — wav 는 문서와 실측이 일치해 이것만 쓴다.
STT_AUDIO_FORMAT = "wav"


@dataclass(frozen=True)
class SlackAudioFile:
    """Slack 이벤트에서 뽑아낸 처리 대상 오디오 파일.

    Attributes:
        url_private: Slack Bot Token 인증이 필요한 비공개 다운로드 URL.
        name: 원본 파일명(리포트 파일명 생성에 사용).
        extension: 소문자, 점 포함 확장자(예: ``.m4a``).
    """

    url_private: str
    name: str
    extension: str


def process_meeting_audio(
    audio_file: SlackAudioFile,
    *,
    config: SlackBotConfig,
    pipeline_config_path: str = "configs/pipeline.yaml",
    http_client: httpx.Client | None = None,
) -> Path:
    """Slack 오디오 파일 하나를 요약 Markdown 리포트로 변환해 로컬에 저장한다.

    Args:
        audio_file: 다운로드할 Slack 파일 정보.
        config: Slack 봇 설정(STT 모델/토큰).
        pipeline_config_path: 요약 단계 재사용을 위한 ``configs/pipeline.yaml`` 경로.
        http_client: 주입용 httpx 클라이언트(테스트).

    Returns:
        생성된 리포트 파일 경로.

    Raises:
        SlackBotError: Slack 파일 다운로드 실패.
        PipelineError: 전사(TranscriptionError) 또는 요약(SummarizationError) 실패.
    """
    pipeline_cfg = load_config(pipeline_config_path)  # 요약 설정/키 선검사 — 다운로드 전 빠르게 실패

    audio_bytes = _download_slack_file(audio_file.url_private, config.slack_bot_token, http_client)
    logger.info("Slack 파일 다운로드 완료: %s (%d bytes)", audio_file.name, len(audio_bytes))

    duration_sec = probe_duration_sec(audio_bytes, audio_file.extension)
    audio_segments = split_audio(
        audio_bytes,
        input_extension=audio_file.extension,
        output_extension=f".{STT_AUDIO_FORMAT}",
        duration_sec=duration_sec,
        segment_sec=config.stt.segment_minutes * 60,
        overlap_sec=config.stt.segment_overlap_sec,
    )

    chunks = []
    for i, segment in enumerate(audio_segments):
        stt_result = transcribe_audio(
            segment.data, audio_format=STT_AUDIO_FORMAT, config=config.stt, api_key=config.openrouter_api_key
        )
        chunks.append(
            Chunk(
                index=i,
                start=segment.start_sec,
                end=segment.start_sec + stt_result.duration_sec,
                text=stt_result.text,
                segments=(),
            )
        )
    logger.info("OpenRouter STT 전사 완료: %d개 구간, %d자", len(chunks), sum(len(c.text) for c in chunks))

    summary = summarize_meeting(
        chunks,
        config=pipeline_cfg.summarize,
        api_key=pipeline_cfg.api_key,
        map_template=_read_prompt(MAP_PROMPT),
        reduce_template=_read_prompt(REDUCE_PROMPT),
        single_shot_max_chars=pipeline_cfg.chunking.single_shot_max_chars,
    )

    meta = ReportMeta(
        source_file=audio_file.name,
        duration_sec=duration_sec,
        segment_count=len(chunks),
        chunk_count=len(chunks),
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        model=summary.model,
    )
    report = render_report(summary.body, meta)

    config.slack.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = config.slack.output_dir / f"{Path(audio_file.name).stem}{REPORT_SUFFIX}"
    output_path.write_text(report, encoding="utf-8")
    logger.info("리포트 생성 완료: %s", output_path)
    return output_path


def _download_slack_file(url_private: str, bot_token: str, http_client: httpx.Client | None) -> bytes:
    """Slack 파일을 Bot Token 인증으로 다운로드한다(``url_private`` 는 비공개 URL).

    Raises:
        SlackBotError: HTTP 오류(권한/네트워크 등).
    """
    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=DOWNLOAD_TIMEOUT_SEC, follow_redirects=True)
    try:
        resp = client.get(url_private, headers={"Authorization": f"Bearer {bot_token}"})
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise SlackBotError(f"Slack 파일 다운로드 실패: {exc}") from exc
    finally:
        if owns_client:
            client.close()
    return resp.content


def _read_prompt(path: Path) -> str:
    """프롬프트 파일을 읽는다(``src.pipeline._read_prompt`` 와 동일 규칙).

    Raises:
        DependencyError: 프롬프트 파일이 없을 때.
    """
    if not path.is_file():
        raise DependencyError(f"프롬프트 파일이 없습니다: {path}")
    return path.read_text(encoding="utf-8")
