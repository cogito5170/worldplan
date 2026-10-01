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

## 세 저장소가 어떻게 얹히나

| 저장소 | 맡는 것 |
|---|---|
| **worldplan**(여기) | 엔진: 생성자 · 심판 · 원장 · REST · MCP · 도우미(앞단 + Claude/Gemini) |
| [gentleMonster](https://github.com/cogito5170/gentleMonster) `gentle_monster/apps/worldplan/` | 화면 — 여기에 복사본을 두지 않는다(두 벌이면 어느 쪽이 진짜인지 모른다). `worldplan app` 이 받아 붙인다 |
| [worldTrip](https://github.com/cogito5170/worldTrip) | 세계여행 엔진. 깔려 있으면 도우미의 숙고층에 `worldtrip` MCP 도구가 같이 붙는다 — 여행과 회의를 한 대화에서 |

## 맥에서 — Gemini CLI 로

```bash
curl -fsSL https://raw.githubusercontent.com/cogito5170/worldplan/main/scripts/setup_mac.sh | bash
```

`~/.worldplan/venv` 에 worldplan · worldtrip 을 깔고(시스템 파이썬은 안 건드린다), `~/.local/bin` 에 명령을 걸고,
gemini 가 없으면 `brew install gemini-cli`(없으면 npm)로 깔고, **gemini 와 claude 에 MCP 서버 worldplan · worldtrip 을
user 범위로 등록**하고, 화면을 받아 둔다. 다시 돌려도 된다. 로그인은 사람만 할 수 있다 — 처음 한 번:

```bash
gemini                      # 'Login with Google' 을 고른다 (또는 export GEMINI_API_KEY=...)
```

쓰는 법:

```bash
worldplan app --llm gemini          # 화면 + 도우미(숙고층 Gemini). 브라우저가 열린다 -> http://127.0.0.1:8765/
worldplan app --llm claude          # 숙고층 Claude
worldplan app                       # auto: gemini 가 깔려 있으면 gemini, 로그인 안 됐으면 claude 로 넘어간다
worldplan ask "서울 오후 3시는 뉴욕 몇 시야?"     # 터미널에서 한 번
gemini                              # Gemini CLI 에서 바로 -- "다음 주 kim 이랑 sam 30분 잡아줘" (worldplan 도구가 붙어 있다)
```

Gemini CLI 를 대화창으로 쓸 때는 처음 연 폴더를 믿을지 묻는다 — 믿어야 MCP 서버가 켜진다(안 믿으면 `Disabled`).

## 돌리기

```bash
pip install .                       # 또는 python3 -m worldplan ... (설치 없이)
worldplan app                       # 화면(gentleMonster)을 받아 붙이고 브라우저를 연다
worldplan serve [--frontend 폴더]   # 엔진만(API + MCP). 화면 폴더를 주면 같이 내준다
worldplan mcp                       # MCP stdio
worldplan plan worldplan/static/sample.json     # 종료 코드 0=ACCEPT 1=REJECT
worldplan ledger                    # 원장 사슬 검사
```

Docker:

```bash
docker build -t worldplan .
docker run -p 8765:8765 -e WORLDPLAN_TOKEN=$(openssl rand -hex 16) -v worldplan-data:/data worldplan
```

### 화면

화면은 gentleMonster 의 `gentle_monster/apps/worldplan/` 이다(토큰 · 화면 검사도 거기). `worldplan app` 이 처음 뜰 때
gentleMonster `main` 에서 받아 `~/.cache/worldplan/frontend/` 에 둔다(GitHub tar.gz → 안 되면 git sparse clone).
다시 받으려면 `--update`, 다른 갈래는 `--ref`, 받아 둔 것이 있으면 `--frontend <gentleMonster>/gentle_monster/apps/worldplan`.
받은 화면에 `index.html · app.js · app.css · tokens.css` 가 다 없으면 붙이지 않고 실패로 끝난다.

폼으로 사람(시간대 · 일하는 시간 · 선호 시간)과 일정(길이 · 필수/선택 · 선후)을 짓고 '계획 세우기' 를 누른다.
폼에 없는 칸(휴일 · 체류 · 바쁜 시간 · 하루 상한)은 '고급' 의 JSON 에서 넣는다. 위에는 사람들의 시간대 시계.

화면 검사(gentleMonster 에서): `python3 -m gentle_monster.apps.check --app worldplan [--url http://127.0.0.1:8765/]` —
계획 ACCEPT · REJECT · 물어보기 × 375 · 1440 px 에서 V 전부 성립(2026-10-01, 고정 응답과 떠 있는 엔진 둘 다).

### 도우미 (물어보기)

말로 묻는다. 앞단이 먼저 보고, 못 정한 말만 숙고층(LLM CLI)이 worldplan · worldtrip MCP 도구로 답한다.

| `WORLDPLAN_LLM` | 숙고층 |
|---|---|
| `auto` (기본) | 깔린 것 — `gemini` 먼저, 그다음 `claude`. 앞이 로그인 · 설치에서 실패하면 뒤로 넘어간다(답의 `route` 에 적힌다) |
| `gemini` · `claude` · `claude,gemini` | 고른 것만 / 그 순서로 |
| `off` | 숙고층 없음 — 앞단이 답하는 것만 |

둘 다 **MCP 도구만** 쓴다: Claude 는 `--tools ""` + `--strict-mcp-config`, Gemini 는 임시 폴더의 `.gemini/settings.json` 에
`tools.core: []`(셸 · 파일 · 웹 내장 도구 없음) + 서버 `trust`. `--yolo` 는 안 쓴다. 시스템 프롬프트는 짧은 앱 전용
(Gemini 는 `GEMINI_SYSTEM_MD`). 토큰은 두 CLI 의 JSON 에서 센다 — Gemini CLI 는 **비용을 내지 않으므로 `cost_usd` 가 비어 있다**(0 이 아니다).

| `WORLDPLAN_FRONT` | 앞단 | 잰 것 (숙고층 Claude sonnet, CLI) |
|---|---|---|
| `clock` (기본) | 시각 센서 — "서울 오후 3시는 뉴욕 몇 시" 처럼 답이 하나로 정해지는 물음만 엔진의 `world_clock` 으로 | 봉인 v2: 토큰 −14.6% · 응답 −10% · 앞단 오답 1/60 (사후에 고침) |
| `walp` | 시각 센서 + WALP 행동 버스(잡담) | 봉인 v1: 토큰 −48% · 응답 −46% 였지만 **잡담층이 일정 요청을 가로챘다(60 중 10)** — 기본에서 뺐다 |
| `off` | 없음 — 전부 숙고층 | 요청당 약 12.8k 토큰 · 6.7 초 · $0.015 |

**절감은 시각 질문의 몫뿐이다.** 시각 질문이 없는 사용이면 절감도 없다. 사전등록 · 봉인 모음 · 결과:
[`eval/PREREG_앞단비교.md`](eval/PREREG_앞단비교.md) · [`eval/PREREG_앞단비교_v2.md`](eval/PREREG_앞단비교_v2.md).
**Gemini 숙고층으로는 아직 재지 않았다** — 이 컨테이너에 Gemini 키가 없다. 확인한 것: 진짜 gemini 0.62 가 우리 설정으로
worldplan · worldtrip MCP 에 `Connected`, 로그인이 없으면 그 오류를 알아보고 Claude 로 넘어간다(`tests/test_llm.py`).

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
| `WORLDPLAN_FRONT` | clock | 도우미 앞단: `off` · `clock` · `walp` (위 표) |
| `WORLDPLAN_LLM` | auto | 도우미 숙고층: `auto` · `gemini` · `claude` · `claude,gemini` · `off` (`--llm` 과 같다) |
| `WORLDPLAN_CLAUDE_MODEL` · `WORLDPLAN_GEMINI_MODEL` | CLI 기본 | 숙고층 모형 |
| `WORLDPLAN_WORLDTRIP` | auto | `0` 이면 worldtrip MCP 를 숙고층에 안 붙인다 |
| `WORLDPLAN_FRONTEND` | 없음 | 화면 폴더(`serve` 의 `--frontend` 와 같다) |
| `WORLDPLAN_WALP_HOME` · `WORLDPLAN_WALP_MODEL` | 없음 | `walp` 앞단일 때: SE 저장소 뿌리 · 학습된 체계 JSON |

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
| 검사 | 64개 통과(진짜 gemini 가 없으면 1개 건너뜀) | `python3 -m unittest discover -s tests -t .` |
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
