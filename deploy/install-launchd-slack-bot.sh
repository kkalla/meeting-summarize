#!/usr/bin/env bash
# Slack 회의 요약 봇을 launchd LaunchAgent 로 설치/재설치한다(idempotent).
#   ./deploy/install-launchd-slack-bot.sh
# 사전 준비: uv sync --dev, .env 에 SLACK_BOT_TOKEN/SLACK_APP_TOKEN/OPENROUTER_API_KEY.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST="com.max.meeting-slack-bot.plist"
SRC="${PROJECT_DIR}/deploy/${PLIST}"
DEST="${HOME}/Library/LaunchAgents/${PLIST}"

# StandardOutPath/ErrorPath 디렉토리는 launchd 가 자동 생성하지 않는다.
mkdir -p "${PROJECT_DIR}/logs"

# 이미 로드돼 있으면 먼저 내린다(재실행 시 "이미 로드됨" 에러 방지).
launchctl unload "${DEST}" 2>/dev/null || true
cp "${SRC}" "${DEST}"
launchctl load -w "${DEST}"

echo "설치 완료: ${DEST}"
echo "  상태 확인:  launchctl list | grep meeting-slack-bot"
echo "  로그:       tail -f ${PROJECT_DIR}/logs/slack_bot.log"
echo "  중지:       launchctl unload -w ${DEST}"
