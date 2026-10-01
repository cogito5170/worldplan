#!/usr/bin/env bash
# worldplan + worldTrip 을 맥에 깔고, Gemini CLI(와 있으면 Claude CLI)에 MCP 로 붙인다.
#
#   curl -fsSL https://raw.githubusercontent.com/cogito5170/worldplan/main/scripts/setup_mac.sh | bash
#   bash scripts/setup_mac.sh [--source DIR] [--worldtrip-source DIR] [--walp-source DIR] [--no-worldtrip]
#                             [--no-cli-install] [--no-claude-hook]
#
# 하는 일
#   1. ~/.worldplan/venv 에 worldplan · worldtrip 을 깐다(시스템 파이썬은 건드리지 않는다)
#   2. ~/.local/bin 에 worldplan · worldtrip 명령을 건다
#   3. gemini 가 없으면 brew(없으면 npm)로 깐다
#   4. gemini(와 claude)에 MCP 서버 worldplan · worldtrip 을 user 범위로 등록한다(--trust: 도구 확인 없이)
#   5. gentleMonster 에서 화면을 미리 받아 둔다
#   6. Claude Code 에도 WALP 잡담층을 건다(~/.claude/settings.json 의 UserPromptSubmit 훅 -- 잡담은 모형에 안 보낸다)
#   7. 로그인이 되어 있는지 보고, 안 되어 있으면 무엇을 하라고 말한다(로그인은 사람만 할 수 있다)
#
# 다시 돌려도 된다(같은 이름의 MCP 등록은 지우고 다시 건다).
set -euo pipefail

HOME_DIR="${WORLDPLAN_HOME:-$HOME/.worldplan}"
VENV="$HOME_DIR/venv"
BIN_DIR="${WORLDPLAN_BIN_DIR:-$HOME/.local/bin}"
WP_SRC="git+https://github.com/cogito5170/worldplan"
WT_SRC="git+https://github.com/cogito5170/worldTrip"
WALP_SRC="https://github.com/cogito5170/walp/archive/refs/heads/main.tar.gz"
WITH_WT=1
CLI_INSTALL=1
CLAUDE_HOOK=1

while [ $# -gt 0 ]; do
  case "$1" in
    --source) WP_SRC="$2"; shift 2 ;;
    --worldtrip-source) WT_SRC="$2"; shift 2 ;;
    --walp-source) WALP_SRC="$2"; shift 2 ;;
    --no-worldtrip) WITH_WT=0; shift ;;
    --no-claude-hook) CLAUDE_HOOK=0; shift ;;
    --no-cli-install) CLI_INSTALL=0; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "모르는 인자: $1" >&2; exit 2 ;;
  esac
done

say() { printf '\033[1m[worldplan]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[worldplan]\033[0m %s\n' "$*" >&2; }

# ---- 1. 파이썬 3.10+ ----
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$(command -v "$c")"; break
  fi
done
if [ -z "$PY" ]; then
  if command -v brew >/dev/null 2>&1 && [ "$CLI_INSTALL" = 1 ]; then
    say "파이썬 3.10 이상이 없다 -- brew install python@3.12"
    brew install python@3.12
    PY="$(brew --prefix)/bin/python3.12"
  else
    warn "파이썬 3.10 이상이 필요하다(맥 기본 python3 는 3.9 일 수 있다). https://www.python.org 또는 brew install python@3.12"
    exit 1
  fi
fi
say "파이썬: $PY ($("$PY" -V 2>&1))"

# ---- 2. venv + 패키지 ----
mkdir -p "$HOME_DIR" "$BIN_DIR"
if [ ! -x "$VENV/bin/python" ]; then
  "$PY" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install -q --upgrade pip >/dev/null 2>&1 || true
say "worldplan 설치: $WP_SRC"
"$VENV/bin/python" -m pip install -q --upgrade "$WP_SRC"
# walp 는 같은 판 번호로 바뀔 수 있다(main 을 따른다) -- 늘 다시 받는다
"$VENV/bin/python" -m pip install -q --upgrade --force-reinstall --no-deps "$WALP_SRC"
if [ "$WITH_WT" = 1 ]; then
  say "worldtrip 설치: $WT_SRC"
  "$VENV/bin/python" -m pip install -q --upgrade "$WT_SRC"
fi
ln -sf "$VENV/bin/worldplan" "$BIN_DIR/worldplan"
ln -sf "$VENV/bin/walp-front" "$BIN_DIR/walp-front"     # worldplan 의 의존성으로 깔린 WALP 잡담층 도구
[ "$WITH_WT" = 1 ] && ln -sf "$VENV/bin/worldtrip" "$BIN_DIR/worldtrip"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR 가 PATH 에 없다 -- ~/.zshrc 에 넣어라:  export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac

# ---- 3. Gemini CLI ----
if ! command -v gemini >/dev/null 2>&1 && [ "$CLI_INSTALL" = 1 ]; then
  if command -v brew >/dev/null 2>&1; then
    say "gemini 설치: brew install gemini-cli"
    brew install gemini-cli
  elif command -v npm >/dev/null 2>&1; then
    say "gemini 설치: npm install -g @google/gemini-cli"
    npm install -g @google/gemini-cli
  fi
fi

# ---- 4. MCP 등록 ----
register_gemini() {  # $1 이름  $2 명령
  gemini mcp remove -s user "$1" >/dev/null 2>&1 || true
  gemini mcp add -s user --trust "$1" "$2" mcp >/dev/null
}
register_claude() {
  claude mcp remove -s user "$1" >/dev/null 2>&1 || true
  claude mcp add -s user "$1" -- "$2" mcp >/dev/null
}
GEMINI_OK=0
CLAUDE_OK=0
if command -v gemini >/dev/null 2>&1; then
  register_gemini worldplan "$VENV/bin/worldplan"
  [ "$WITH_WT" = 1 ] && register_gemini worldtrip "$VENV/bin/worldtrip"
  say "gemini 에 MCP 등록: worldplan$([ "$WITH_WT" = 1 ] && echo ' · worldtrip')"
  GEMINI_OK=1
else
  warn "gemini 가 없다 -- brew install gemini-cli 또는 npm install -g @google/gemini-cli 뒤에 이 스크립트를 다시 돌려라"
fi
if command -v claude >/dev/null 2>&1; then
  register_claude worldplan "$VENV/bin/worldplan"
  [ "$WITH_WT" = 1 ] && register_claude worldtrip "$VENV/bin/worldtrip"
  say "claude 에 MCP 등록: worldplan$([ "$WITH_WT" = 1 ] && echo ' · worldtrip')"
  CLAUDE_OK=1
fi

# ---- 5. 화면 받아 두기 ----
if "$VENV/bin/python" -c 'from worldplan import frontend; frontend.fetch()' 2>/dev/null; then
  say "화면(gentleMonster apps/worldplan)을 받아 두었다"
else
  warn "화면을 지금 못 받았다 -- worldplan app 이 처음 뜰 때 다시 받는다"
fi

# ---- 6. Claude Code 훅 ----
if [ "$CLAUDE_HOOK" = 1 ]; then
  "$VENV/bin/walp-front" install-hook >/dev/null
  say "Claude Code 에 WALP 잡담층 훅을 걸었다(~/.claude/settings.json) -- 막혔으면 앞에 // · 떼려면 walp-front uninstall-hook"
fi

# ---- 7. 로그인 ----
if [ "$GEMINI_OK" = 1 ]; then
  if [ -n "${GEMINI_API_KEY:-}" ] || [ -f "$HOME/.gemini/oauth_creds.json" ]; then
    say "gemini 로그인: 됨"
  else
    warn "gemini 로그인이 아직이다 -- 터미널에서 'gemini' 를 한 번 띄워 'Login with Google' 을 고르거나, GEMINI_API_KEY 를 세워라"
  fi
fi

cat <<EOF

다 됐다. 쓰는 법:
  worldplan app --llm gemini        # 화면 + 도우미(Gemini). 브라우저가 열린다 -> http://127.0.0.1:8765/
  worldplan app --llm claude        # 도우미를 Claude 로
  worldplan app                     # auto: gemini 가 있으면 gemini, 로그인이 안 되면 claude 로 넘어간다
  worldplan ask "서울 오후 3시는 뉴욕 몇 시야?"
  walp-front ask "안녕" --llm gemini  # WALP 잡담층을 아무 질문 앞에나(worldplan 밖에서도)
  claude                            # Claude Code -- 잡담은 WALP 가 답한다(토큰 0). 일이 막혔으면 앞에 // 를 붙인다
  gemini                            # Gemini CLI 에서 바로: "다음 주 kim 이랑 sam 30분 잡아줘" (worldplan 도구가 붙어 있다)
  worldtrip app                     # 세계여행 화면(같은 gentleMonster 화면 갈래)
EOF
