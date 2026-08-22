#!/bin/bash
# Claude Code on the web 세션에서 테스트가 바로 돌도록 전제 조건을 갖춘다.
#
# 이 저장소는 config/config.yaml 과 tests/frontend/node_modules 를 git 에 담지 않는다.
# 둘 다 없으면 원격 세션의 fresh clone 에서 통합 테스트 50건이 설정 파일 부재로
# 무너지고(웹 컨텍스트 조립 실패), jsdom DOM 회귀 16건은 조용히 skip 된다.
# GitHub Actions 는 ci.yml 의 "Create Mock Config and Data" 스텝으로 같은 준비를 한다.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  # 로컬(개인 PC)은 실제 API 키가 든 config 와 conda py310 환경을 이미 갖고 있다.
  exit 0
fi

REPO_ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
cd "$REPO_ROOT"

echo "[session-start] python 의존성 설치"
python3 -m pip install --quiet --disable-pip-version-check -r requirements.txt

mkdir -p data

if [ ! -f config/config.yaml ]; then
  echo "[session-start] config/config.yaml 생성 (example 복사)"
  # example 은 이미 web: 섹션을 포함하므로 그대로 복사하면 AppConfig 검증을 통과한다.
  # 값은 전부 자리표시자라 실제 API 호출은 불가능하다 — 테스트는 HTTP 를 모킹한다.
  cp config/config.yaml.example config/config.yaml
fi

if [ -d tests/frontend ] && [ ! -d tests/frontend/node_modules/jsdom ]; then
  echo "[session-start] jsdom 설치 (미설치 시 DOM 회귀 16건이 skip 된다)"
  (cd tests/frontend && npm install --silent --no-fund --no-audit)
fi

echo "[session-start] 준비 완료"
