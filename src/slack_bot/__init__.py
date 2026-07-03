"""Slack 회의 요약 봇: 로컬 CLI 파이프라인(``src.pipeline``)과 별개인 부가 진입점.

CLAUDE.md 는 CLI 파이프라인 자체를 "서버/REST/웹UI 범위 밖(KISS)"로 규정하지만, 이
서브패키지는 그와 별도로 추가된 스코프다(개인용 Slack DM 봇). 요약 단계(OpenRouter LLM)는
``src.summarize``/``src.report`` 를 그대로 재사용하고, 전사(STT)만 로컬 whisper.cpp 대신
OpenRouter STT API 를 쓴다.
"""
