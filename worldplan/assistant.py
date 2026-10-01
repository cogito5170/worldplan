"""자연어 도우미 -- WALP 를 Claude 앞에 얇게 세운다(Control -> Sequencing -> Deliberative).

    Control       WALP 행동 버스(SE 저장소의 walp, 학습된 체계). 인사 · 감사 · 작별 · 자기소개 · 할 수 있는 것 ·
                  쓰는 법이면 여기서 끝낸다(LLM 없음, 10 ms 안팎)
    Sequencing    시각 변환 센서 -- "서울 오후 3시는 뉴욕 몇 시" · "지금 kim 쪽 몇 시" 처럼 **답이 하나로 정해지는**
                  물음만 엔진의 world_clock 으로 답한다. 조금이라도 모자라면(오전/오후 없음 · 장소 하나 · 일정 동사)
                  손대지 않고 위로 보낸다 -- 모르는 것은 위로
    Deliberative  Claude(`claude -p`, worldplan MCP 도구). 계획 · 판정 · 질문 · 그 밖의 전부

앞단은 WORLDPLAN_FRONT 로 고른다 -- 기본은 eval/PREREG_앞단비교*.md 의 결정을 따른다.

    WORLDPLAN_FRONT        off(Claude 만) | clock(시각 센서만) | walp(시각 센서 + WALP 잡담)
                           봉인 v1: walp 는 잡담층이 일정 요청을 가로채(60 중 10) 안전 기준에서 졌다 -- 기본에서 뺐다
    WORLDPLAN_WALP_HOME    SE 저장소 뿌리(walp 패키지가 있는 곳)
    WORLDPLAN_WALP_MODEL   학습된 체계 JSON
    WORLDPLAN_CLAUDE       0 이면 Claude 를 부르지 않는다(앞단만)
    WORLDPLAN_CLAUDE_MODEL 기본: CLI 기본 모형

답마다 토큰(입력 · 캐시 만들기 · 캐시 읽기 · 출력)과 비용 · 시간을 같이 돌려준다 -- 앞단이 답하면 0 이다.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import engine
from .model import zone

SMALL = {"greet", "thanks", "bye", "about_self", "capability", "help"}
REPLY = {
    "greet": "안녕하세요. 여러 시간대에 흩어진 사람들의 회의를 잡아 드립니다. 위 칸에 사람과 일정을 넣고 '계획 세우기' 를 누르거나, 여기에 말로 물어보세요.",
    "thanks": "천만에요. 더 필요한 게 있으면 말씀하세요.",
    "bye": "안녕히 가세요.",
    "about_self": "worldplan 도우미입니다. 짧은 말과 시각 변환은 앞단(WALP)이 바로 답하고, 계획 · 판정이 필요한 말은 Claude 가 worldplan 도구로 답합니다.",
    "capability": "여러 일정을 한 번에 배치하고(계획 세우기), 직접 짠 계획을 심판에 올리고, 공통 빈 시간을 찾고, 도시 사이 시각을 바꿔 드립니다.",
    "help": "'요청 짓기' 에 사람(시간대 · 일하는 시간)과 일정(길이 · 필수/선택 참가자)을 넣고 '계획 세우기' 를 누르세요. 말로 '다음 주에 kim 이랑 sam 30분 잡아줘' 처럼 물어도 됩니다.",
}

SYSTEM = (
    "You are the assistant inside worldplan, a web app that schedules meetings across time zones. "
    "Use the worldplan tools for anything about plans, verification, free slots or time conversion; never compute a "
    "schedule yourself. The judge (verify/plan verdict) is authoritative: report REJECT honestly. "
    "Answer in the user's language, briefly (at most 4 short sentences unless a table is needed). "
    "Today is {today}. The app's current request (people, events, horizon) is:\n{request}"
)


# ---------------- Control: WALP ----------------

class Walp:
    def __init__(self, home: str, model: str):
        if home not in sys.path:
            sys.path.insert(0, home)
        os.environ.setdefault("WALP_LLM", "0")          # WALP 자기 숙고층은 끈다 -- 숙고는 여기서 Claude 가 한다
        from walp import behavior as B                   # noqa: PLC0415 -- 선택 의존
        self.B = B
        self.bus = B.버스짓기(B.from_json(json.loads(Path(model).read_text(encoding="utf-8"))))

    def act(self, text: str) -> "tuple[str | None, bool]":
        r = self.bus.돌기(text)
        return (r.get("행한것") or {}).get("행위"), self.B.모름(r)


def load_walp() -> "Walp | None":
    home, model = os.environ.get("WORLDPLAN_WALP_HOME"), os.environ.get("WORLDPLAN_WALP_MODEL")
    if not home or not model:
        return None
    try:
        return Walp(home, model)
    except Exception as e:                               # noqa: BLE001 -- 앞단이 없어도 도우미는 돈다
        sys.stderr.write(f"WALP 를 못 불렀다({type(e).__name__}: {e}) -- 앞단 없이 돈다\n")
        return None


# ---------------- Sequencing: 시각 변환 센서 ----------------

CITIES = {
    "서울": "Asia/Seoul", "한국": "Asia/Seoul", "seoul": "Asia/Seoul", "korea": "Asia/Seoul", "부산": "Asia/Seoul",
    "도쿄": "Asia/Tokyo", "동경": "Asia/Tokyo", "일본": "Asia/Tokyo", "tokyo": "Asia/Tokyo",
    "베이징": "Asia/Shanghai", "북경": "Asia/Shanghai", "상하이": "Asia/Shanghai", "중국": "Asia/Shanghai",
    "beijing": "Asia/Shanghai", "shanghai": "Asia/Shanghai",
    "홍콩": "Asia/Hong_Kong", "hong kong": "Asia/Hong_Kong", "타이베이": "Asia/Taipei", "대만": "Asia/Taipei",
    "싱가포르": "Asia/Singapore", "singapore": "Asia/Singapore", "방콕": "Asia/Bangkok", "bangkok": "Asia/Bangkok",
    "자카르타": "Asia/Jakarta", "하노이": "Asia/Ho_Chi_Minh", "호치민": "Asia/Ho_Chi_Minh",
    "델리": "Asia/Kolkata", "뉴델리": "Asia/Kolkata", "뭄바이": "Asia/Kolkata", "인도": "Asia/Kolkata",
    "벵갈루루": "Asia/Kolkata", "delhi": "Asia/Kolkata", "mumbai": "Asia/Kolkata", "india": "Asia/Kolkata",
    "두바이": "Asia/Dubai", "dubai": "Asia/Dubai", "모스크바": "Europe/Moscow", "moscow": "Europe/Moscow",
    "이스탄불": "Europe/Istanbul", "istanbul": "Europe/Istanbul",
    "베를린": "Europe/Berlin", "독일": "Europe/Berlin", "berlin": "Europe/Berlin", "germany": "Europe/Berlin",
    "뮌헨": "Europe/Berlin", "프랑크푸르트": "Europe/Berlin",
    "파리": "Europe/Paris", "프랑스": "Europe/Paris", "paris": "Europe/Paris",
    "런던": "Europe/London", "영국": "Europe/London", "london": "Europe/London",
    "마드리드": "Europe/Madrid", "madrid": "Europe/Madrid", "로마": "Europe/Rome", "rome": "Europe/Rome",
    "암스테르담": "Europe/Amsterdam", "amsterdam": "Europe/Amsterdam", "취리히": "Europe/Zurich",
    "스톡홀름": "Europe/Stockholm", "헬싱키": "Europe/Helsinki", "더블린": "Europe/Dublin", "리스본": "Europe/Lisbon",
    "뉴욕": "America/New_York", "new york": "America/New_York", "nyc": "America/New_York", "워싱턴": "America/New_York",
    "보스턴": "America/New_York", "boston": "America/New_York", "토론토": "America/Toronto", "toronto": "America/Toronto",
    "마이애미": "America/New_York", "애틀랜타": "America/New_York",
    "시카고": "America/Chicago", "chicago": "America/Chicago", "댈러스": "America/Chicago", "휴스턴": "America/Chicago",
    "덴버": "America/Denver", "denver": "America/Denver",
    "la": "America/Los_Angeles", "엘에이": "America/Los_Angeles", "로스앤젤레스": "America/Los_Angeles",
    "los angeles": "America/Los_Angeles", "샌프란시스코": "America/Los_Angeles", "san francisco": "America/Los_Angeles",
    "실리콘밸리": "America/Los_Angeles", "시애틀": "America/Los_Angeles", "seattle": "America/Los_Angeles",
    "밴쿠버": "America/Vancouver", "vancouver": "America/Vancouver",
    "멕시코시티": "America/Mexico_City", "상파울루": "America/Sao_Paulo", "상파울로": "America/Sao_Paulo",
    "sao paulo": "America/Sao_Paulo", "são paulo": "America/Sao_Paulo", "브라질": "America/Sao_Paulo",
    "부에노스아이레스": "America/Argentina/Buenos_Aires",
    "시드니": "Australia/Sydney", "sydney": "Australia/Sydney", "멜버른": "Australia/Melbourne",
    "melbourne": "Australia/Melbourne", "오클랜드": "Pacific/Auckland", "auckland": "Pacific/Auckland",
    "호놀룰루": "Pacific/Honolulu", "하와이": "Pacific/Honolulu", "hawaii": "Pacific/Honolulu",
    "utc": "UTC", "gmt": "UTC", "협정세계시": "UTC",
}
# 일정을 바꾸려는 말 -- 이게 있으면 시각 센서는 손대지 않는다(계획은 Claude 몫)
_TASK = re.compile(r"잡아|예약|넣어|옮겨|바꿔|미뤄|당겨|취소|추가|빼|만들어|schedule|book|move|cancel|회의|미팅|일정|"
                   r"계획|가능|비어|빈 시간|되는 시간|어때|괜찮", re.I)
_ASK = re.compile(r"몇\s*시|what time|time in|시간은|시각은|시간이야|시각이야|몇시", re.I)
_NOW = re.compile(r"지금|현재|now|right now", re.I)
_T12 = re.compile(r"(오전|오후|아침|저녁|밤|새벽|낮)\s*(\d{1,2})\s*시\s*(?:(\d{1,2})\s*분|(반))?")
_T24 = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?!\d)\s*(am|pm)?", re.I)
_TEN = re.compile(r"(?<![\d:])(\d{1,2})\s*(am|pm)\b", re.I)
_NOON = re.compile(r"정오|자정|midnight|noon", re.I)
_DATE_ISO = re.compile(r"(20\d\d)-(\d{1,2})-(\d{1,2})")
_DATE_KO = re.compile(r"(?:(20\d\d)\s*년\s*)?(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_DATE_SL = re.compile(r"(?<![\d:/])(\d{1,2})/(\d{1,2})(?![\d/])")
_REL = {"오늘": 0, "내일": 1, "모레": 2, "today": 0, "tomorrow": 1}


def _places(text: str, request: "dict | None") -> "list[tuple[int, str, str]]":
    """(위치, 이름, 시간대) -- 겹치는 이름은 긴 것이 이긴다."""
    low = text.lower()
    found = []
    names = dict(CITIES)
    for p in (request or {}).get("participants") or []:
        if isinstance(p, dict) and isinstance(p.get("id"), str) and isinstance(p.get("tz"), str) and p["id"]:
            names[p["id"].lower()] = "@" + p["id"]           # 사람은 날짜에 따라 체류지가 바뀐다 -- 나중에 푼다
    for name in sorted(names, key=len, reverse=True):
        pat = re.escape(name)
        if re.fullmatch(r"[a-z .]+", name):
            pat = r"(?<![a-z])" + pat + r"(?![a-z])"
        for m in re.finditer(pat, low):
            if any(a <= m.start() < b for a, b, *_ in found):
                continue
            found.append((m.start(), m.end(), text[m.start():m.end()], names[name]))
    found.sort()
    return [(a, n, z) for a, b, n, z in found]


def _person_tz(request: dict, pid: str, day: "dt.date | None") -> "str | None":
    for p in request.get("participants") or []:
        if p.get("id") != pid:
            continue
        for s in p.get("stays") or []:
            try:
                if day and dt.date.fromisoformat(s["from"]) <= day <= dt.date.fromisoformat(s["to"]):
                    return s["tz"]
            except (KeyError, ValueError, TypeError):
                return None                                   # 못 읽는 체류 -- 추측하지 않는다
        return p.get("tz")
    return None


def _time(text: str) -> "tuple[int, int, int] | None":
    """(시, 분, 위치). 오전/오후가 없는 'N시' 는 모른다 -- None."""
    hits = []
    for m in _T12.finditer(text):
        ap, h = m.group(1), int(m.group(2))
        mi = 30 if m.group(4) else int(m.group(3) or 0)
        if not 1 <= h <= 12 or mi > 59:
            return None
        pm = ap in ("오후", "저녁", "밤") or (ap == "낮" and h < 6)
        h = (h % 12) + (12 if pm else 0)
        if ap == "밤" and h < 18:                          # '밤 1시' 는 새벽이다 -- 헷갈리면 모른다
            return None
        hits.append((h, mi, m.start()))
    for m in _T24.finditer(text):
        h, mi, ap = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
        if ap:
            if not 1 <= h <= 12:
                return None
            h = (h % 12) + (12 if ap == "pm" else 0)
        if h > 23 or mi > 59:
            return None
        hits.append((h, mi, m.start()))
    for m in _TEN.finditer(text):
        h = int(m.group(1))
        if not 1 <= h <= 12:
            return None
        hits.append(((h % 12) + (12 if m.group(2).lower() == "pm" else 0), 0, m.start()))
    for m in _NOON.finditer(text):
        hits.append((12 if m.group(0) in ("정오", "noon") else 0, 0, m.start()))
    hits = {h for h in hits}
    if len({(h, mi) for h, mi, _ in hits}) != 1:
        return None                                       # 시각이 없거나 둘 이상 -- 하나로 안 정해진다
    return sorted(hits, key=lambda x: x[2])[0]


def _date(text: str, today: dt.date) -> "tuple[dt.date | None, bool]":
    """(날짜, 적혀 있었나). 못 읽는 날짜면 (None, True)."""
    m = _DATE_ISO.search(text) or _DATE_KO.search(text)
    try:
        if m and m.re is _DATE_ISO:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))), True
        if m:
            return dt.date(int(m.group(1) or today.year), int(m.group(2)), int(m.group(3))), True
        m = _DATE_SL.search(text)
        if m:
            return dt.date(today.year, int(m.group(1)), int(m.group(2))), True
    except ValueError:
        return None, True
    for w, k in _REL.items():
        if w in text.lower():
            return today + dt.timedelta(days=k), True
    return None, False


def clock_answer(text: str, request: "dict | None", now: dt.datetime) -> "dict | None":
    """답이 하나로 정해지면 {answer, result}, 아니면 None(위로 보낸다)."""
    if _TASK.search(text) or not _ASK.search(text):
        return None
    places = _places(text, request)
    if not places:
        return None
    is_now = bool(_NOW.search(text))
    t = _time(text)
    if is_now and t:
        return None                                       # '지금' 과 시각이 같이 -- 뜻이 둘이다
    if not is_now and not t:
        return None
    today = now.date()
    day, written = _date(text, today)
    if written and day is None:
        return None

    def tz_of(name_tz, d):
        n, z = name_tz
        if z.startswith("@"):
            return _person_tz(request or {}, z[1:], d)
        return z

    if is_now:
        zones = [tz_of((n, z), today) for _, n, z in places]
        if None in zones:
            return None
        res = engine.world_clock(now.astimezone(dt.timezone.utc).isoformat(timespec="minutes"), list(dict.fromkeys(zones)))
        if res.get("verdict") == "REJECT":
            return None
        lines = [f"{n}: {_fmt(r)}" for (_, n, _), r in zip(places, [_row(res, z) for z in zones])]
        return {"answer": "지금 " + " · ".join(lines), "result": res}
    if len(places) < 2:
        return None                                       # 어디 기준인지 · 어디로인지 하나가 빠졌다
    h, mi, tpos = t
    if "기준" in text:
        return None                                       # '서울 기준' 은 원천일 수도 목적지일 수도 있다(봉인 v1 #11)
    before = [p for p in places if p[0] < tpos]
    src = before[-1] if before else places[0]
    dsts = [p for p in places if p is not src]
    if not dsts:
        return None
    src_day = day or None
    src_tz = tz_of(src[1:], src_day or today)
    if not src_tz:
        return None
    try:
        local = dt.datetime.combine(src_day or _today_in(now, src_tz), dt.time(h, mi), tzinfo=zone(src_tz))
    except Exception:                                     # noqa: BLE001 -- 모르는 시간대
        return None
    # 없는 시각(봄 DST 틈) · 두 번 있는 시각(가을) 이면 하나로 안 정해진다
    if _ambiguous(local):
        return None
    instant = local.astimezone(dt.timezone.utc)
    zones = [tz_of(p[1:], instant.astimezone(zone(src_tz)).date()) for p in dsts]
    if None in zones:
        return None
    res = engine.world_clock(instant.isoformat(timespec="minutes"), list(dict.fromkeys([src_tz] + zones)))
    if res.get("verdict") == "REJECT":
        return None
    head = f"{src[1]} {local.strftime('%Y-%m-%d %H:%M')}"
    body = " · ".join(f"{p[1]} {_fmt(_row(res, z))}" for p, z in zip(dsts, zones))
    return {"answer": f"{head} 는 {body} 입니다.", "result": res}


def _today_in(now: dt.datetime, tz: str) -> dt.date:
    return now.astimezone(zone(tz)).date()


def _ambiguous(local: dt.datetime) -> bool:
    a = local.replace(fold=0)
    b = local.replace(fold=1)
    if a.utcoffset() != b.utcoffset():
        return True
    back = a.astimezone(dt.timezone.utc).astimezone(local.tzinfo)
    return back.replace(tzinfo=None) != a.replace(tzinfo=None)


def _row(res: dict, tz: str) -> dict:
    return next(r for r in res["zones"] if r["tz"] == tz)


def _fmt(r: dict) -> str:
    d, hm = r["local"][:10], r["local"][11:16]
    off = r["utc_offset_minutes"]
    sign = "+" if off >= 0 else "-"
    return f"{d} {hm} ({r['abbr']}, UTC{sign}{abs(off) // 60:02d}:{abs(off) % 60:02d})"


# ---------------- Deliberative: Claude ----------------

def _zero() -> dict:
    return {"input": 0, "cache_creation": 0, "cache_read": 0, "output": 0, "total": 0}


class ClaudeCLI:
    """`claude -p` 한 번 = 한 숙고. worldplan MCP(stdio) 도구만 쓴다 -- 셸 · 파일 · 웹 도구는 없다."""

    def __init__(self, model: "str | None" = None, timeout: int = 300):
        self.model = model or os.environ.get("WORLDPLAN_CLAUDE_MODEL")
        self.timeout = timeout
        self.dir = Path(tempfile.mkdtemp(prefix="worldplan-claude-"))   # 빈 자리에서 돈다 -- 아무 CLAUDE.md 도 안 읽는다
        env = {"PYTHONPATH": str(Path(__file__).resolve().parent.parent)}
        if os.environ.get("WORLDPLAN_LEDGER_ROOT"):
            env["WORLDPLAN_LEDGER_ROOT"] = os.environ["WORLDPLAN_LEDGER_ROOT"]
        self.mcp = self.dir / "mcp.json"
        self.mcp.write_text(json.dumps({"mcpServers": {"worldplan": {
            "command": sys.executable, "args": ["-m", "worldplan", "mcp"], "env": env}}}), encoding="utf-8")

    def ask(self, text: str, request: "dict | None", today: str, now_utc: "str | None" = None) -> dict:
        sysp = SYSTEM.format(today=today, request=json.dumps(request or {}, ensure_ascii=False, separators=(",", ":")))
        prompt = f"[now: {now_utc}]\n{text}" if now_utc else text
        cmd = ["claude", "-p", prompt, "--output-format", "json", "--system-prompt", sysp,
               "--strict-mcp-config", "--mcp-config", str(self.mcp), "--tools", "",
               "--allowedTools", "mcp__worldplan", "--setting-sources", "", "--no-session-persistence"]
        if self.model:
            cmd += ["--model", self.model]
        try:
            p = subprocess.run(cmd, cwd=self.dir, capture_output=True, text=True, timeout=self.timeout,
                               stdin=subprocess.DEVNULL)
            d = json.loads(p.stdout)
        except subprocess.TimeoutExpired:
            return {"answer": "Claude 가 제때 답하지 못했다.", "error": "timeout", "tokens": _zero()}
        except (json.JSONDecodeError, FileNotFoundError) as e:
            return {"answer": "Claude 를 부르지 못했다.", "error": type(e).__name__, "tokens": _zero()}
        u = d.get("usage") or {}
        tok = {"input": u.get("input_tokens") or 0, "cache_creation": u.get("cache_creation_input_tokens") or 0,
               "cache_read": u.get("cache_read_input_tokens") or 0, "output": u.get("output_tokens") or 0}
        tok["total"] = sum(tok.values())
        return {"answer": d.get("result") or "", "tokens": tok, "cost_usd": d.get("total_cost_usd") or 0.0,
                "api_ms": d.get("duration_api_ms"), "turns": d.get("num_turns"),
                "model": next(iter(d.get("modelUsage") or {}), None),
                **({"error": "is_error"} if d.get("is_error") else {})}


# ---------------- 세 층을 잇는다 ----------------

FRONT = os.environ.get("WORLDPLAN_FRONT", "off")    # off | clock | walp -- eval/PREREG_앞단비교*.md 의 결정을 따른다


class Assistant:
    def __init__(self, walp: "Walp | None | bool" = True, claude: "ClaudeCLI | None | bool" = True,
                 front: "bool | str | None" = None):
        front = FRONT if front is None else front
        front = {True: "walp", False: "off"}.get(front, front)
        if front not in ("off", "clock", "walp"):
            raise ValueError(f"front: off | clock | walp, got {front!r}")
        self.mode = front
        self.front = front != "off"
        self.walp = (load_walp() if walp is True else (walp or None)) if front == "walp" else None
        if claude is True:
            claude = None if os.environ.get("WORLDPLAN_CLAUDE") == "0" else ClaudeCLI()
        self.claude = claude or None

    def ask(self, text: str, request: "dict | None" = None, now: "dt.datetime | None" = None) -> dict:
        t0 = time.perf_counter()
        now = now or dt.datetime.now(dt.timezone.utc)
        out = self._ask(str(text or "").strip(), request, now)
        out.setdefault("tokens", _zero())
        out.setdefault("cost_usd", 0.0)
        out["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return out

    def _ask(self, text, request, now):
        if not text:
            return {"answer": "", "by": "walp", "route": "빈 말"}
        act = None
        if self.front:
            c = clock_answer(text, request, now)                       # 시각 센서가 이기면 아래를 억제한다
            if c:
                return {**c, "by": "walp", "route": "시각 변환"}
            if self.walp:
                act, unknown = self.walp.act(text)
                if act in SMALL and not unknown:
                    return {"answer": REPLY[act], "by": "walp", "route": f"WALP {act}", "act": act}
        if not self.claude:
            return {"answer": "이 말은 Claude 가 답해야 하는데 Claude 가 꺼져 있다.", "by": "none",
                    "route": "Claude 꺼짐", "act": act}
        r = self.claude.ask(text, request, now.date().isoformat(),
                            now.astimezone(dt.timezone.utc).isoformat(timespec="minutes"))
        route = {"off": "Claude 만", "clock": "시각 센서 못 정함 -> Claude",
                 "walp": "WALP 모름 -> Claude" if self.walp else "WALP 없음 -> Claude"}[self.mode]
        return {**r, "by": "claude", "route": route, "act": act}


_DEFAULT: "Assistant | None" = None


def default() -> Assistant:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = Assistant()
    return _DEFAULT
