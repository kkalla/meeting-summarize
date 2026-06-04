"""전체 파이프라인 오케스트레이션: 변환 → 전사 → 청킹 → 요약 → 리포트.

의존성/키 선검사를 가능한 한 앞단에서 수행해, 비싼 전사/요약 전에 빠르게 실패한다.
"""

from __future__ import annotations

import logging
import tempfile
from datetime import datetime
from pathlib import Path

from src import cache
from src.audio import convert_to_wav
from src.chunking import Chunk, chunk_segments
from src.config import PROJECT_ROOT, PipelineConfig, load_config
from src.exceptions import CacheError, DependencyError
from src.report import ReportMeta, render_report
from src.summarize import summarize_meeting
from src.transcribe import Segment, transcribe

logger = logging.getLogger(__name__)

PROMPTS_DIR = PROJECT_ROOT / "prompts"
MAP_PROMPT = PROMPTS_DIR / "map_summary.txt"
REDUCE_PROMPT = PROMPTS_DIR / "reduce_summary.txt"
WAV_SUFFIX = ".wav"


def run_pipeline(
    input_path: Path,
    output_path: Path,
    config_path: str = "configs/pipeline.yaml",
) -> Path:
    """녹음 파일 하나를 요약 Markdown 리포트로 변환한다.

    Args:
        input_path: 입력 녹음 파일(.m4a/.wav 등).
        output_path: 생성할 Markdown 리포트 경로.
        config_path: pipeline.yaml 경로.

    Returns:
        생성된 리포트 경로(``output_path``).

    Raises:
        FileNotFoundError: 입력 파일이 없을 때.
        PipelineError: 단계별 실패(의존성/전사/요약 등).
    """
    config = load_config(config_path)  # 키/설정 선검사 — 전사 전 즉시 실패
    if not input_path.is_file():
        raise FileNotFoundError(f"입력 파일을 찾을 수 없습니다: {input_path}")

    map_template = _read_prompt(MAP_PROMPT)
    reduce_template = _read_prompt(REDUCE_PROMPT)

    with tempfile.TemporaryDirectory() as tmp:
        wav_path = Path(tmp) / f"{input_path.stem}{WAV_SUFFIX}"
        convert_to_wav(input_path, wav_path, config.audio)

        logger.info("전사 시작")
        segments = _transcribe_or_load(input_path, wav_path, config)

    logger.info("전사 완료: 세그먼트 %d개", len(segments))
    chunks = chunk_segments(segments, config.chunking)
    logger.info("청킹 완료: 청크 %d개", len(chunks))

    summary_body = summarize_meeting(
        chunks,
        config=config.summarize,
        api_key=config.api_key,
        map_template=map_template,
        reduce_template=reduce_template,
        single_shot_max_chars=config.chunking.single_shot_max_chars,
    )

    report = render_report(summary_body, _build_meta(input_path, segments, chunks, config))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    logger.info("리포트 생성 완료: %s", output_path)
    return output_path


def _transcribe_or_load(input_path: Path, wav_path: Path, config: PipelineConfig) -> list[Segment]:
    """전사 캐시를 우선 조회하고, 없으면 전사 후 저장한다.

    캐시가 비활성이거나 키 계산이 실패하면 캐시 없이 전사로 폴백한다(캐시는 정확성에
    영향 없는 최적화 — 어떤 캐시 실패도 전사 자체를 막지 않는다).
    """
    cache_cfg = config.cache
    if not cache_cfg.enabled:
        return transcribe(wav_path, config.stt)
    try:
        key = cache.compute_key(input_path)
    except CacheError as exc:
        logger.warning("캐시 키 계산 실패 — 캐시 없이 전사: %s", exc)
        return transcribe(wav_path, config.stt)

    cached = cache.load(cache_cfg.transcripts_dir, key)
    if cached is not None:
        logger.info("전사 캐시 HIT — 전사 스킵 (세그먼트 %d개)", len(cached))
        return cached

    segments = transcribe(wav_path, config.stt)
    cache.store(cache_cfg.transcripts_dir, key, segments, source_name=input_path.name)
    return segments


def _build_meta(
    input_path: Path,
    segments: list[Segment],
    chunks: list[Chunk],
    config: PipelineConfig,
) -> ReportMeta:
    """리포트 메타데이터를 조립한다."""
    duration = segments[-1].end if segments else 0.0
    return ReportMeta(
        source_file=input_path.name,
        duration_sec=duration,
        segment_count=len(segments),
        chunk_count=len(chunks),
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


def _read_prompt(path: Path) -> str:
    """프롬프트 파일을 읽는다.

    Raises:
        DependencyError: 프롬프트 파일이 없을 때.
    """
    if not path.is_file():
        raise DependencyError(f"프롬프트 파일이 없습니다: {path}")
    return path.read_text(encoding="utf-8")
