#!/usr/bin/env bash
# 배포 연기 시험: 서버를 띄워 healthz -> /api/plan -> /mcp tools/call 을 끝까지 돌린다.
# 셸 변수는 아스키만 쓴다(se_new CLAUDE.md: 셸 스크립트에 한글 변수명을 쓰지 마라).
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
port="${PORT:-18765}"
ledger_dir="$(mktemp -d)"
log_file="$(mktemp)"
cd "$here"
WORLDPLAN_LEDGER_ROOT="$ledger_dir" WORLDPLAN_PORT="$port" python3 -m worldplan serve >"$log_file" 2>&1 &
server_pid=$!
cleanup() { kill "$server_pid" 2>/dev/null || true; rm -rf "$ledger_dir" "$log_file"; }
trap cleanup EXIT

base="http://127.0.0.1:$port"
for _ in $(seq 1 50); do
  if curl -fsS "$base/healthz" >/dev/null 2>&1; then break; fi
  sleep 0.1
done
curl -fsS "$base/healthz" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["ok"], d; print("healthz ok")'

body="$(python3 -c 'import json; print(json.dumps({"request": json.load(open("worldplan/static/sample.json"))}))')"
verdict="$(curl -fsS -X POST -H 'Content-Type: application/json' -d "$body" "$base/api/plan" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["verdict"])')"
echo "api/plan verdict: $verdict"
test "$verdict" = "ACCEPT"

mcp_body="$(python3 -c 'import json; print(json.dumps({"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"plan_world_schedule","arguments":{"request":json.load(open("worldplan/static/sample.json"))}}}))')"
mcp_verdict="$(curl -fsS -X POST -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d "$mcp_body" "$base/mcp" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["structuredContent"]["verdict"])')"
echo "mcp verdict: $mcp_verdict"
test "$mcp_verdict" = "ACCEPT"

entries="$(curl -fsS "$base/api/ledger" | python3 -c 'import json,sys; c=json.load(sys.stdin)["chain"]; assert c["ok"]; print(c["entries"])')"
echo "ledger entries: $entries"
test "$entries" = "2"
echo "SMOKE OK"
