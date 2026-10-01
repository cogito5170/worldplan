# worldplan — 세계 일정 계획 엔진

여러 시간대에 흩어진 사람들의 **여러 일정**을 한 번에 배치하는 엔진. 웹 화면과 MCP 서버로 감쌌다.
표준 라이브러리만 쓴다(tzdata 는 시스템 zoneinfo 가 없는 이미지용).

se_new 의 정책을 그대로 옮겼다:

| se_new 정책 | 여기서 |
|---|---|
| 생성자와 심판을 분리한다. 심판은 LLM 이 아니다 | `solver.py`(생성) 와 `judge.py`(판정) 는 서로 임포트하지 않는다. MCP 에 붙은 LLM 이 낸 계획도 `verify_world_schedule` 로 같은 심판을 지난다 |
| 독립 대조 하나를 붙인다 | 생성자는 **UTC 구간 연산**, 심판은 **현지 벽시계**로 잰다. 비용도 따로 재서 다르면 거절(J7) |
| 기본은 REJECT · 모르는 것은 안 된 것 | 오프셋 없는 시각 · 일정 0개 · 필수 참가자 없는 일정 · 겹치는 체류 · 선후 고리는 추측 없이 거절 |
| 과장하지 않는다 · 무효화 조건 | 최적은 탐색이 끝까지 돈 경우에만(`proven_optimal`). 매 판정에 `invalidated_if` · `not_checked` |
| 판정 원장은 지울 수 없다(G020) | `data/ledger.jsonl` 해시 사슬. 고치거나 지우면 `ledger_status` 가 그 줄을 짚는다 |
| 사람의 말을 남기지 않는다 | 원장에는 입력 해시와 판정만. 이름·시간대·일정 내용은 없다(검사가 붙든다) |
| 검사는 흔적을 남기지 않는다 | 검사는 `WORLDPLAN_LEDGER_ROOT` 를 임시 자리로 세운다 |
| 사람에게 설치를 시키지 마라 | 의존성 없음. Dockerfile 하나 |

## 돌리기

```bash
pip install .                       # 또는 python3 -m worldplan ... (설치 없이)
worldplan serve                     # http://127.0.0.1:8765/  웹 화면 + REST + MCP(POST /mcp)
worldplan mcp                       # MCP stdio
worldplan plan worldplan/static/sample.json     # 종료 코드 0=ACCEPT 1=REJECT
worldplan ledger                    # 원장 사슬 검사
```

Docker:

```bash
docker build -t worldplan .
docker run -p 8765:8765 -e WORLDPLAN_TOKEN=$(openssl rand -hex 16) -v worldplan-data:/data worldplan
```

### MCP 클라이언트에 붙이기

```bash
claude mcp add worldplan -- python3 -m worldplan mcp            # Claude Code, stdio
claude mcp add --transport http worldplan http://HOST:8765/mcp \
  --header "Authorization: Bearer $WORLDPLAN_TOKEN"              # 원격 HTTP
```

Claude Desktop 등 `mcpServers` JSON:

```json
{"mcpServers": {"worldplan": {"command": "python3", "args": ["-m", "worldplan", "mcp"],
                              "env": {"WORLDPLAN_LEDGER_ROOT": "/path/to/data"}}}}
```

도구: `plan_world_schedule` · `verify_world_schedule` · `find_common_slots` · `world_clock` ·
`ledger_status` · `describe_judge`. 프로토콜 2025-06-18 / 2025-03-26 / 2024-11-05.

### 환경 변수

| 변수 | 기본 | |
|---|---|---|
| `WORLDPLAN_HOST` | 127.0.0.1 | 0.0.0.0 으로 열면 **토큰을 세워라**(안 세우면 경고) |
| `WORLDPLAN_PORT` | 8765 | |
| `WORLDPLAN_TOKEN` | 없음 | 세우면 `/api/*` · `/mcp` 에 `Authorization: Bearer` 필요 |
| `WORLDPLAN_ALLOWED_ORIGINS` | 없음 | 브라우저 Origin 허용(같은 호스트는 늘 허용). 다른 Origin 은 403 — DNS 리바인딩 방지 |
| `WORLDPLAN_LEDGER_ROOT` | ./data | 원장 자리 |

## 요청 꼴

`worldplan/static/sample.json` 이 전부 쓴 예시다(서울·베를린·뉴욕·상파울루, 서울 사람의 베를린 출장 포함).

- **참가자**: `tz`(IANA) · `work`(현지 업무창, `end<=start` 면 밤샘) · `core`(선호, 밖이면 비용) ·
  `busy` · `holidays`(현지 날짜) · `stays`(출장: 날짜 구간마다 다른 시간대) · `daily_cap_minutes`
- **일정**: `duration_minutes` · `required`(필수, 하드) · `optional`(선택, 못 오면 비용) ·
  `not_before`/`not_after` · `fixed_start` · `after` + `gap_after_deps_minutes` · `priority`
- **비용** = 선호 밖 분 × 10 + 못 오는 선택 참가자 × 300. 이 비용함수와 `slot_minutes` 격자에 대해서만 최적을 말한다

## 심판이 재는 것 (J0–J7)

J0 전부 한 번씩 · J1 horizon/창/고정 · J2 현지 업무시간·업무일·휴일·체류지 · J3 busy · J4 겹침+buffer ·
J5 선후+gap · J6 하루 상한 · J7 생성자 비용 = 심판 비용.

**재지 않는 것** (매 응답의 `not_checked` 에 나간다): 체류지 사이 이동 시간 · 공휴일 DB(입력에 준 것만 안다) ·
실제 캘린더 동기화 · 회의실 같은 자원 · 격자 밖 시작 시각(생성자 쪽).

## 잰 것 (2026-10-01, 이 컨테이너, tzdata system 2026b)

| 무엇 | 결과 | 어떻게 · 무엇이 이걸 깨나 |
|---|---|---|
| 검사 | 42개 통과 | `python3 -m unittest discover -s tests -t .` |
| 최적성 독립 대조 | 무작위 60문제(가능 26 · 불가능 34)에서 생성자 = 전수탐색 | 전수탐색은 **심판만으로** 가능·비용을 잰다. 사소한 설명 막기: 가능/불가능이 각각 10/5개 미만이면 검사가 실패한다. 비용>0 인 경우 19개 |
| 그 대조가 빨개질 수 있나 | 생성자 비용 가중치를 10→9 로 바꾸면 8/60 불일치, 탐색 노드를 5로 자르면 19/60 불일치 | 돌연변이를 넣어 확인. 작은 문제(2~3명, 1~3개, 120분 격자)에 대해서만이다 |
| 망가뜨린 계획 | 13가지 망가뜨림 전부 심판이 해당 J 로 거절(J0–J7 전부 한 번 이상) | `tests/test_judge_catches.py` |
| DST | 10-26(유럽만 겨울시간) 뉴욕–베를린 3시간 겹침, 11-02 에는 2시간 · 가을 되돌림 밤샘창 | `tests/test_dst.py`. 처음 판의 심판은 되돌림 밤에 비용을 벽시계로 빼서 **60분을 0분으로** 쟀다 -- 고쳤다(실제 분을 걷는다) |
| MCP 호환 | 공식 MCP Python SDK 2.2.0 클라이언트로 stdio·HTTP 둘 다 initialize → tools/list → tools/call 성공(협상 2025-06-18) | SDK 의 최신 판(2026-07-28)은 우리가 지원하지 않는다 -- 클라이언트가 그 판만 고집하면 붙지 않는다 |
| 크기 | 100명·300일정(겹치는 시간대) 8.3초 proven_optimal | **쉬운 사례라서다**: 찾은 비용 = 하한(일정마다 제일 싼 자리)이었다. 빡빡한 사례(20명·120일정·하루 상한 120분)는 20초 한도에 걸려 `unknown` → REJECT |

## 정직한 한계

- **빡빡한 큰 문제에서 약하다.** 분기한정 + MRV 이고, 한도에 걸리면 부분 계획만 참고로 내고 REJECT 한다.
  큰 사례용으로는 CP-SAT 같은 솔버를 생성자 자리에 꽂을 수 있다(심판은 그대로) -- 아직 안 했다.
- **원장은 막지 않고 들킨다.** 파일 권한이 있는 사람은 사슬 전체를 다시 쓸 수 있다. 머리 해시를 밖에 적어 두면 막을 수 있다.
- **입력 파서는 생성자와 심판이 공유한다.** 제약을 재는 코드는 따로지만 입력 해석이 틀리면 둘 다 같이 틀린다.
- HTTP 서버는 표준 라이브러리 `ThreadingHTTPServer` 다. 공개 인터넷 앞에는 TLS 를 끝내는 리버스 프록시를 둬라.
- 선행조사는 `docs/선행조사.md` -- 회의 하나의 공통 시각 찾기는 이미 여러 MCP 서버가 한다.
